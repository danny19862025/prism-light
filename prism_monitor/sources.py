# SPDX-License-Identifier: GPL-3.0-only
"""Metric sources. Each returns real readings or leaves a metric out.

Unprivileged (in the widget process):
* SystemSource: PDH counters (CPU utility/performance, network, disk) and
  GlobalMemoryStatusEx (RAM).
* AdlGpuSource: AMD's display-driver ADL library, PMLog sensors; the same
  sensor indices LibreHardwareMonitor uses (LibreHardwareMonitorLib
  Interop/AtiAdlxx.cs, ADLPMLogSensors).

Privileged (sensor helper, see host.py): CPU temperature, package power,
core clocks and motherboard fans through LibreHardwareMonitorLib.
"""
import ctypes
import time

from . import winapi
from .model import reading


class SystemSource:
    name = 'system'
    IGNORED_NICS = ('loopback', 'isatap', 'teredo', 'vethernet', 'pseudo', 'wan miniport', 'kernel debug')

    def __init__(self):
        self.pdh = winapi.Pdh()
        self.cpu_name, self.base_mhz = winapi.cpu_name_and_base_mhz()
        counters = {
            'util': r'\Processor Information(_Total)\% Processor Utility',
            'perf': r'\Processor Information(_Total)\% Processor Performance',
            'net_down': r'\Network Interface(*)\Bytes Received/sec',
            'net_up': r'\Network Interface(*)\Bytes Sent/sec',
            'disk_read': r'\PhysicalDisk(_Total)\Disk Read Bytes/sec',
            'disk_write': r'\PhysicalDisk(_Total)\Disk Write Bytes/sec',
            'disk_idle': r'\PhysicalDisk(_Total)\% Idle Time',
        }
        self.missing = [k for k, path in counters.items() if not self.pdh.add(k, path)]
        if 'util' in self.missing:
            # Older builds without "% Processor Utility": classic processor time.
            self.pdh.add('util', r'\Processor(_Total)\% Processor Time')
        self.pdh.collect()  # rate counters need a first sample

    def read(self):
        now = time.monotonic()
        out = {}
        if not self.pdh.collect():
            return out
        util = self.pdh.value('util', nocap=True)
        if util is not None:
            out['cpu_util'] = reading(min(100.0, max(0.0, util)), 'pdh', now=now)
        perf = self.pdh.value('perf', nocap=True)
        if perf is not None and self.base_mhz:
            # Same estimate Task Manager shows as "Speed": base clock × performance %.
            out['cpu_clock'] = reading(self.base_mhz * perf / 100, 'pdh', now=now)
        for key in ('net_down', 'net_up'):
            values = self.pdh.array(key)
            if values is not None:
                total = sum(v for name, v in values.items()
                            if not any(word in name.casefold() for word in self.IGNORED_NICS))
                out[key] = reading(total, 'pdh', now=now)
        for key in ('disk_read', 'disk_write'):
            value = self.pdh.value(key)
            if value is not None:
                out[key] = reading(value, 'pdh', now=now)
        idle = self.pdh.value('disk_idle', nocap=True)
        if idle is not None:
            out['disk_active'] = reading(min(100.0, max(0.0, 100.0 - idle)), 'pdh', now=now)
        memory = winapi.memory_status()
        if memory:
            used, total = memory
            out['ram_used'] = reading(used, 'windows', extra=total, now=now)
        return out

    def close(self):
        self.pdh.close()


class AdlGpuSource:
    """AMD Radeon sensors via the installed driver's atiadlxx.dll (no admin needed)."""
    name = 'adl'
    GFXCLK, FAN_RPM, EDGE, ACTIVITY, ASIC_POWER, HOTSPOT, BOARD_POWER = 1, 14, 8, 19, 23, 27, 73

    class _Info(ctypes.Structure):
        _fields_ = [('iSize', ctypes.c_int), ('iAdapterIndex', ctypes.c_int), ('strUDID', ctypes.c_char * 256),
                    ('iBusNumber', ctypes.c_int), ('iDeviceNumber', ctypes.c_int), ('iFunctionNumber', ctypes.c_int),
                    ('iVendorID', ctypes.c_int), ('strAdapterName', ctypes.c_char * 256),
                    ('strDisplayName', ctypes.c_char * 256), ('iPresent', ctypes.c_int), ('iExist', ctypes.c_int),
                    ('strDriverPath', ctypes.c_char * 256), ('strDriverPathExt', ctypes.c_char * 256),
                    ('strPNPString', ctypes.c_char * 256), ('iOSDisplayIndex', ctypes.c_int)]

    class _Sensor(ctypes.Structure):
        _fields_ = [('supported', ctypes.c_int), ('value', ctypes.c_int)]

    class _Log(ctypes.Structure):
        pass

    _Log._fields_ = [('size', ctypes.c_int), ('sensors', _Sensor * 256)]

    def __init__(self, library='atiadlxx.dll'):
        self.adl = ctypes.CDLL(library)
        self._buffers = []
        self._alloc = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_int)(self._allocate)
        self.ctx = ctypes.c_void_p()
        if self.adl.ADL2_Main_Control_Create(self._alloc, 1, ctypes.byref(self.ctx)):
            raise OSError('The AMD display driver did not start ADL.')
        self.adapter, self.name_ = self._find_adapter()
        self.vram_total = winapi.gpu_memory_bytes(self.name_)
        self.has_vram = hasattr(self.adl, 'ADL2_Adapter_DedicatedVRAMUsage_Get')

    @property
    def device(self):
        return self.name_

    def _allocate(self, size):
        buffer = ctypes.create_string_buffer(size)
        self._buffers.append(buffer)
        return ctypes.addressof(buffer)

    def _query(self, adapter):
        out = self._Log()
        out.size = ctypes.sizeof(self._Log)
        if self.adl.ADL2_New_QueryPMLogData_Get(self.ctx, adapter, ctypes.byref(out)):
            return None
        return {i: s.value for i, s in enumerate(out.sensors) if s.supported}

    def _find_adapter(self):
        count = ctypes.c_int()
        self.adl.ADL2_Adapter_NumberOfAdapters_Get(self.ctx, ctypes.byref(count))
        infos = (self._Info * max(1, count.value))()
        self.adl.ADL2_Adapter_AdapterInfo_Get(self.ctx, infos, ctypes.sizeof(infos))
        candidates, seen = [], set()
        for info in infos[:count.value]:
            if info.iBusNumber in seen:
                continue
            seen.add(info.iBusNumber)
            sensors = self._query(info.iAdapterIndex)
            if sensors and self.EDGE in sensors:
                name = info.strAdapterName.decode(errors='replace').strip()
                # Prefer the discrete card over the Ryzen iGPU "AMD Radeon(TM) Graphics".
                candidates.append(('(TM) Graphics' in name, info.iAdapterIndex, name))
        if not candidates:
            raise OSError('No AMD GPU with ADL sensors found.')
        _integrated, adapter, name = sorted(candidates)[0]
        return adapter, name

    def read(self):
        now = time.monotonic()
        s = self._query(self.adapter)
        if s is None:
            return {}
        out = {}

        def put(key, index, scale=1.0):
            if index in s:
                out[key] = reading(s[index] * scale, 'adl', now=now)
        put('gpu_temp', self.EDGE)
        put('gpu_hotspot', self.HOTSPOT)
        put('gpu_util', self.ACTIVITY)
        put('gpu_clock', self.GFXCLK)
        put('gpu_fan', self.FAN_RPM)
        # RDNA3+ report total board power; older cards report ASIC power.
        put('gpu_power', self.BOARD_POWER if self.BOARD_POWER in s else self.ASIC_POWER)
        if self.has_vram:
            mb = ctypes.c_int()
            if self.adl.ADL2_Adapter_DedicatedVRAMUsage_Get(self.ctx, self.adapter, ctypes.byref(mb)) == 0 and mb.value >= 0:
                out['gpu_vram'] = reading(mb.value * (1 << 20), 'adl', extra=self.vram_total, now=now)
        return {k: v for k, v in out.items() if v is not None}

    def close(self):
        if self.ctx:
            self.adl.ADL2_Main_Control_Destroy(self.ctx)
            self.ctx = None


def map_host_items(items, now=None):
    """LibreHardwareMonitor sensor list → (readings, fans, info).

    LibreHardwareMonitor reports 0 for CPU sensors it could not read (for
    example without PawnIO), so physically impossible zeros are dropped.
    Fan 0 rpm is kept: a stopped fan is a real reading.
    """
    now = time.monotonic() if now is None else now
    readings, fans, info = {}, {}, {}
    cpu_temps = {}
    for item in items:
        hw_type, kind, name, value = item.get('hwType'), item.get('type'), item.get('name', ''), item.get('value')
        if value is None:
            continue
        if hw_type == 'Cpu':
            info.setdefault('cpu_name', item.get('hw'))
            if kind == 'Temperature' and 0 < value < 125:
                cpu_temps[name] = value
            elif kind == 'Power' and name == 'Package' and 0 < value < 1000:
                readings['cpu_power'] = reading(value, 'lhm', now=now)
            elif kind == 'Clock' and name == 'Cores (Average)' and 100 < value < 10000:
                readings['cpu_clock'] = reading(value, 'lhm', now=now)
            elif kind == 'Load' and name == 'CPU Total' and 0 <= value <= 100:
                readings['cpu_util_lhm'] = reading(value, 'lhm', now=now)
        elif hw_type in ('SuperIO', 'Motherboard') and kind == 'Fan' and 0 <= value < 20000:
            fans[item['id']] = (f"{name}", reading(value, 'lhm', now=now))
            info.setdefault('board_chip', item.get('hw'))
        elif hw_type in ('GpuAmd', 'GpuNvidia', 'GpuIntel') and '(TM) Graphics' not in item.get('hw', ''):
            gpu = {('Temperature', 'GPU Core'): 'gpu_temp', ('Temperature', 'GPU Hot Spot'): 'gpu_hotspot',
                   ('Load', 'GPU Core'): 'gpu_util', ('Clock', 'GPU Core'): 'gpu_clock',
                   ('Power', 'GPU Package'): 'gpu_power', ('Fan', 'GPU Fan'): 'gpu_fan'}.get((kind, name))
            if gpu and not (gpu in ('gpu_temp', 'gpu_hotspot') and value <= 0):
                readings[gpu + '_lhm'] = reading(value, 'lhm', now=now)
    for preferred in ('Core (Tctl/Tdie)', 'CPU Package', 'Core (Tdie)', 'Core (Tctl)', 'CCDs Max (Tdie)', 'Package'):
        if preferred in cpu_temps:
            readings['cpu_temp'] = reading(cpu_temps[preferred], 'lhm', now=now)
            info['cpu_temp_sensor'] = preferred
            break
    return {k: v for k, v in readings.items() if v is not None}, fans, info

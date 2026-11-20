# SPDX-License-Identifier: GPL-3.0-only
"""Battery levels of wireless devices: game controllers, Bluetooth keyboards, mice and headsets.

All sources are unprivileged:
- PlayStation controllers (DualSense, DualSense Edge, DualShock 4), read from their HID input report.
- Windows' own Bluetooth battery property: what Settings → Bluetooth & devices shows.
- Xbox controllers through XInput.
- A laptop or UPS battery through GetSystemPowerStatus.

On Linux, UPower lists the same devices: the kernel's hid-playstation, xpad/xpadneo
and Logitech drivers, plus Bluetooth devices through BlueZ.

A background thread scans every SCAN_SECONDS, so a slow device never stalls the widget.
"""
import ctypes
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass

SCAN_SECONDS = 15.0

SONY_MODELS = {0x0CE6: ('DualSense', 'ds'), 0x0DF2: ('DualSense Edge', 'ds'),
               0x05C4: ('DualShock 4', 'ds4'), 0x09CC: ('DualShock 4', 'ds4')}
_SONY_PATH = re.compile(r'vid[_&](?:0002)?054c[_&]pid[_&]([0-9a-f]{4})')
XINPUT_LEVELS = {0: ('Empty', 5.0), 1: ('Low', 25.0), 2: ('Medium', 60.0), 3: ('Full', 100.0)}


@dataclass(frozen=True)
class Battery:
    id: str
    name: str
    percent: float
    state: str = ''          # 'charging' | 'full' | '' (on battery or unknown)
    text: str = ''           # set when the device reports a coarse level instead of a percentage
    source: str = ''


# -- PlayStation report parsing (pure, unit-tested) --------------------------
SIMPLE_MODE = 'simple'   # Bluetooth controller is in its basic mode, which carries no battery level


def parse_sony_report(kind, bluetooth, data):
    """(percent, state) from one input report, SIMPLE_MODE, or None for an unrelated report.

    data starts with the report id. Layouts follow the Linux hid-playstation driver.
    """
    if not data:
        return None
    rid = data[0]
    if kind == 'ds':
        if bluetooth and rid == 0x01:
            return SIMPLE_MODE
        offset = {(False, 0x01): 53, (True, 0x31): 54}.get((bluetooth, rid))
        if offset is None or len(data) <= offset:
            return None
        status = data[offset]
        capacity, charge = status & 0x0F, status >> 4
        if charge == 0x2:
            return 100.0, 'full'
        return min(100.0, capacity * 10.0 + 5), 'charging' if charge == 0x1 else ''
    if kind == 'ds4':
        if bluetooth and rid == 0x01:
            return SIMPLE_MODE
        offset = {(False, 0x01): 30, (True, 0x11): 32}.get((bluetooth, rid))
        if offset is None or len(data) <= offset:
            return None
        status = data[offset]
        capacity, cable = status & 0x0F, bool(status & 0x10)
        if cable:
            return (100.0, 'full') if capacity >= 10 else (capacity * 10.0 + 5, 'charging')
        return min(100.0, capacity * 10.0 + 5), ''
    return None


def sony_model(path):
    """(name, kind, bluetooth) for a PlayStation controller HID interface path, else None."""
    lower = path.lower()
    match = _SONY_PATH.search(lower)
    if not match:
        return None
    model = SONY_MODELS.get(int(match.group(1), 16))
    if not model:
        return None
    return model[0], model[1], '{00001124-' in lower or 'vid&0002054c' in lower


def merge(found):
    """Drop duplicates (the same device seen by two sources) and number identical names."""
    out, seen_ids, seen_names = [], set(), set()
    for b in found:
        key = b.name.casefold()
        if b.id in seen_ids or (b.source == 'windows' and key in seen_names):
            continue
        seen_ids.add(b.id)
        seen_names.add(key)
        out.append(b)
    counts = {}
    for b in out:
        counts[b.name] = counts.get(b.name, 0) + 1
    numbered, index = [], {}
    for b in out:
        if counts[b.name] > 1:
            index[b.name] = index.get(b.name, 0) + 1
            b = Battery(b.id, f'{b.name} {index[b.name]}', b.percent, b.state, b.text, b.source)
        numbered.append(b)
    return numbered


# -- Windows plumbing --------------------------------------------------------
if sys.platform == 'win32':
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD),
                    ('Data4', ctypes.c_ubyte * 8)]

        @classmethod
        def parse(cls, text):
            raw = uuid.UUID(text).bytes_le
            return cls.from_buffer_copy(raw)

    class _DEVPROPKEY(ctypes.Structure):
        _fields_ = [('fmtid', _GUID), ('pid', wintypes.ULONG)]

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_void_p), ('InternalHigh', ctypes.c_void_p),
                    ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]

    class _XInputBattery(ctypes.Structure):
        _fields_ = [('BatteryType', ctypes.c_ubyte), ('BatteryLevel', ctypes.c_ubyte)]

    class _PowerStatus(ctypes.Structure):
        _fields_ = [('ACLineStatus', ctypes.c_ubyte), ('BatteryFlag', ctypes.c_ubyte),
                    ('BatteryLifePercent', ctypes.c_ubyte), ('SystemStatusFlag', ctypes.c_ubyte),
                    ('BatteryLifeTime', wintypes.DWORD), ('BatteryFullLifeTime', wintypes.DWORD)]

    _kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _cfgmgr = ctypes.WinDLL('cfgmgr32')
    _hid = ctypes.WinDLL('hid')
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                   ctypes.POINTER(_OVERLAPPED)]
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED)]
    _kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED),
                                              ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.ResetEvent.argtypes = [wintypes.HANDLE]
    _hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    _hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    _hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _hid.HidP_GetCaps.restype = ctypes.c_long
    _hid.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]

    _GUID_HID = _GUID.parse('4d1e55b2-f16f-11cf-88cb-001111000030')
    _KEY_BATTERY = _DEVPROPKEY(_GUID.parse('104ea319-6ee2-4701-bd47-8ddbf425bbe5'), 2)
    _KEY_FRIENDLY = _DEVPROPKEY(_GUID.parse('a45c254e-df1c-4efd-8020-67d146a850e0'), 14)
    _KEY_DESC = _DEVPROPKEY(_GUID.parse('a45c254e-df1c-4efd-8020-67d146a850e0'), 2)
    _INVALID = wintypes.HANDLE(-1).value
    GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
    FILE_SHARE_RW, OPEN_EXISTING, FILE_FLAG_OVERLAPPED = 0x3, 3, 0x40000000

    def _multi_sz(buffer):
        return [s for s in ctypes.wstring_at(ctypes.addressof(buffer), len(buffer)).split('\0') if s]

    def hid_interfaces():
        size = wintypes.ULONG()
        if _cfgmgr.CM_Get_Device_Interface_List_SizeW(ctypes.byref(size), ctypes.byref(_GUID_HID), None, 0):
            return []
        buffer = ctypes.create_unicode_buffer(size.value + 1)
        if _cfgmgr.CM_Get_Device_Interface_ListW(ctypes.byref(_GUID_HID), None, buffer, size.value, 0):
            return []
        return _multi_sz(buffer)

    def _node_property(devinst, key):
        kind, size = wintypes.ULONG(), wintypes.ULONG(0)
        _cfgmgr.CM_Get_DevNode_PropertyW(devinst, ctypes.byref(key), ctypes.byref(kind), None, ctypes.byref(size), 0)
        if not size.value:
            return None
        buffer = (ctypes.c_ubyte * size.value)()
        if _cfgmgr.CM_Get_DevNode_PropertyW(devinst, ctypes.byref(key), ctypes.byref(kind), buffer,
                                            ctypes.byref(size), 0):
            return None
        if kind.value == 0x12:   # DEVPROP_TYPE_STRING
            return ctypes.wstring_at(ctypes.addressof(buffer)).strip()
        if kind.value == 0x03:   # DEVPROP_TYPE_BYTE
            return buffer[0]
        return None

    def windows_batteries():
        """Devices with Windows' Bluetooth battery property (Settings → Bluetooth & devices)."""
        size = wintypes.ULONG()
        if _cfgmgr.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), None, 0x100):   # FILTER_PRESENT
            return []
        buffer = ctypes.create_unicode_buffer(size.value + 1)
        if _cfgmgr.CM_Get_Device_ID_ListW(None, buffer, size.value, 0x100):
            return []
        found = []
        for instance in _multi_sz(buffer):
            devinst = wintypes.DWORD()
            if _cfgmgr.CM_Locate_DevNodeW(ctypes.byref(devinst), instance, 0):
                continue
            level = _node_property(devinst, _KEY_BATTERY)
            if not isinstance(level, int) or level > 100:
                continue
            name = _node_property(devinst, _KEY_FRIENDLY) or _node_property(devinst, _KEY_DESC) or 'Bluetooth device'
            found.append(Battery('win:' + instance, name, float(level), source='windows'))
        return found

    def _report_lengths(handle):
        data = ctypes.c_void_p()
        if not _hid.HidD_GetPreparsedData(handle, ctypes.byref(data)):
            return 0, 0
        try:
            caps = (ctypes.c_ushort * 32)()
            if _hid.HidP_GetCaps(data, caps) != 0x00110000:
                return 0, 0
            return caps[2], caps[4]   # InputReportByteLength, FeatureReportByteLength
        finally:
            _hid.HidD_FreePreparsedData(data)

    def _read_report(handle, event, length, timeout_ms):
        buffer = (ctypes.c_ubyte * length)()
        ov = _OVERLAPPED(hEvent=event)
        _kernel32.ResetEvent(event)
        got = wintypes.DWORD()
        if not _kernel32.ReadFile(handle, buffer, length, None, ctypes.byref(ov)):
            if ctypes.get_last_error() != 997:   # ERROR_IO_PENDING
                return None
            if _kernel32.WaitForSingleObject(event, timeout_ms) != 0:
                _kernel32.CancelIoEx(handle, ctypes.byref(ov))
                _kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(got), True)
                return None
        if not _kernel32.GetOverlappedResult(handle, ctypes.byref(ov), ctypes.byref(got), False):
            return None
        return bytes(buffer[:got.value])

    # Feature reports carrying the controller's Bluetooth address (bytes 1–6), the same over USB and
    # Bluetooth, so a controller that is plugged in and still paired is listed once.
    _ADDRESS_REPORTS = {('ds', False): 0x09, ('ds', True): 0x09, ('ds4', False): 0x12, ('ds4', True): 0x81}

    def _sony_address(handle, kind, bluetooth, feature_len):
        rid = _ADDRESS_REPORTS[(kind, bluetooth)]
        if not feature_len:
            return None
        feature = (ctypes.c_ubyte * feature_len)(rid)
        if not _hid.HidD_GetFeature(handle, feature, feature_len) or feature[0] != rid:
            return None
        address = bytes(feature[1:7])
        return address.hex() if any(address) else None

    def read_sony(path, kind, bluetooth, wake_bluetooth=True):
        """((percent, state) | SIMPLE_MODE | None, bluetooth address or None)."""
        handle = _kernel32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_RW, None, OPEN_EXISTING,
                                       FILE_FLAG_OVERLAPPED, None)
        if handle in (None, _INVALID):
            handle = _kernel32.CreateFileW(path, GENERIC_READ, FILE_SHARE_RW, None, OPEN_EXISTING,
                                           FILE_FLAG_OVERLAPPED, None)
            if handle in (None, _INVALID):
                return None, None
        event = _kernel32.CreateEventW(None, True, False, None)
        try:
            in_len, feature_len = _report_lengths(handle)
            if not in_len:
                return None, None
            return _read_sony_level(handle, event, kind, bluetooth, wake_bluetooth, in_len, feature_len), \
                _sony_address(handle, kind, bluetooth, feature_len)
        finally:
            if event:
                _kernel32.CloseHandle(event)
            _kernel32.CloseHandle(handle)

    def _read_sony_level(handle, event, kind, bluetooth, wake_bluetooth, in_len, feature_len):
        result, woke = None, False
        for _ in range(6):
            data = _read_report(handle, event, in_len, 250)
            if data is None:
                return result
            result = parse_sony_report(kind, bluetooth, data)
            if result == SIMPLE_MODE and wake_bluetooth and not woke and feature_len:
                # Reading the calibration feature report switches a Bluetooth controller to its
                # full report, which carries the battery level. Steam and Linux do the same.
                feature = (ctypes.c_ubyte * feature_len)(0x05)
                _hid.HidD_GetFeature(handle, feature, feature_len)
                woke = True
                continue
            if result not in (None, SIMPLE_MODE):
                return result
        return result

    def sony_batteries(wake_bluetooth=True):
        found = []
        # USB first: when a controller is plugged in and still paired, the cable reading wins.
        for path in sorted(hid_interfaces(), key=lambda p: bool((sony_model(p) or ('', '', True))[2])):
            model = sony_model(path)
            if not model:
                continue
            name, kind, bluetooth = model
            result, address = read_sony(path, kind, bluetooth, wake_bluetooth)
            if result is None or result == SIMPLE_MODE:
                continue
            percent, state = result
            found.append(Battery('sony:' + (address or path.lower()), name, percent, state,
                                 source='bluetooth' if bluetooth else 'usb'))
        return found

    _xinput = None

    def xinput_batteries():
        global _xinput
        if _xinput is None:
            try:
                _xinput = ctypes.WinDLL('xinput1_4')
            except OSError:
                _xinput = False
        if not _xinput:
            return []
        found = []
        for user in range(4):
            info = _XInputBattery()
            if _xinput.XInputGetBatteryInformation(user, 0, ctypes.byref(info)) != 0:
                continue
            if info.BatteryType not in (2, 3):   # alkaline / NiMH; wired and virtual pads have no battery
                continue
            text, percent = XINPUT_LEVELS.get(info.BatteryLevel, ('', None))
            if percent is not None:
                found.append(Battery(f'xinput:{user}', f'Xbox controller {user + 1}', percent, text=text, source='xinput'))
        return found

    def system_battery():
        status = _PowerStatus()
        if not _kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return []
        if status.BatteryFlag in (128, 255) or status.BatteryLifePercent > 100:
            return []
        charging = bool(status.BatteryFlag & 8)
        full = status.ACLineStatus == 1 and status.BatteryLifePercent >= 100
        return [Battery('system', 'This PC', float(status.BatteryLifePercent),
                        'full' if full else 'charging' if charging else '', source='system')]

    def scan(wake_bluetooth=True):
        """All batteries found, plus a short status line per source."""
        found, status = [], {}
        for key, reader in (('sony', lambda: sony_batteries(wake_bluetooth)), ('windows', windows_batteries),
                            ('xinput', xinput_batteries), ('system', system_battery)):
            try:
                items = reader()
                found.extend(items)
                status[key] = f'OK ({len(items)})'
            except Exception as e:   # one broken source never hides the others
                status[key] = f'Error: {e}'
        return merge(found), status
elif sys.platform.startswith('linux'):
    import subprocess
    from pathlib import Path

    UPOWER_KINDS = {'gaming-input', 'mouse', 'keyboard', 'headset', 'headphones', 'touchpad', 'tablet', 'phone',
                    'media-player', 'pen', 'speakers', 'bluetooth-generic', 'other', 'battery', 'ups', 'remote control'}
    UPOWER_LEVELS = {'critical': ('Critical', 5.0), 'low': ('Low', 25.0), 'normal': ('Medium', 60.0),
                     'high': ('High', 80.0), 'full': ('Full', 100.0)}

    def parse_upower(text):
        """Battery list from `upower --dump` (pure, unit-tested)."""
        found = []
        for block in text.split('\nDevice: ')[0:]:
            lines = block.strip().splitlines()
            if not lines or 'DisplayDevice' in lines[0]:
                continue
            fields, kind = {}, ''
            for line in lines[1:]:
                stripped = line.strip()
                if ':' in stripped:
                    key, _, value = stripped.partition(':')
                    fields[key.strip()] = value.strip()
                elif stripped and line.startswith('  ') and not line.startswith('    '):
                    kind = stripped
            if kind not in UPOWER_KINDS or fields.get('present', 'yes') != 'yes':
                continue
            try:
                percent = float(fields.get('percentage', '').rstrip('%'))
            except ValueError:
                continue
            state = {'charging': 'charging', 'fully-charged': 'full', 'pending-charge': 'charging'}.get(
                fields.get('state', ''), '')
            system = fields.get('power supply') == 'yes'
            if system and kind not in ('battery', 'ups'):
                continue
            text_level = ''
            level = fields.get('battery-level', 'none')
            if level in UPOWER_LEVELS:   # the device reports a coarse level, not a percentage
                text_level, percent = UPOWER_LEVELS[level]
            name = 'This PC' if system else fields.get('model') or fields.get('vendor') or kind.replace('-', ' ').title()
            ident = fields.get('serial') or fields.get('native-path') or lines[0].strip()
            found.append(Battery('system' if system else ident, name, max(0.0, min(100.0, percent)), state,
                                 text_level, 'upower'))
        return found

    def upower_batteries():
        text = subprocess.run(['upower', '--dump'], capture_output=True, text=True, timeout=10).stdout
        return parse_upower(text)

    def sysfs_batteries(root=Path('/sys/class/power_supply')):
        found = []
        for supply in sorted(root.iterdir()) if root.is_dir() else []:
            def get(name):
                try:
                    return (supply / name).read_text().strip()
                except OSError:
                    return ''
            if get('type') != 'Battery' or get('present') == '0':
                continue
            try:
                percent = float(get('capacity'))
            except ValueError:
                continue
            device = get('scope') == 'Device'
            state = {'Charging': 'charging', 'Full': 'full'}.get(get('status'), '')
            name = get('model_name') or supply.name if device else 'This PC'
            found.append(Battery(supply.name if device else 'system', name, percent, state, source='kernel'))
        return found

    def scan(wake_bluetooth=True):
        """All batteries the desktop knows about: UPower (kernel drivers and Bluetooth), else sysfs."""
        try:
            items = upower_batteries()
            return merge(items), {'upower': f'OK ({len(items)})'}
        except (OSError, subprocess.SubprocessError) as e:
            try:
                items = sysfs_batteries()
                return merge(items), {'upower': f'Unavailable: {e}', 'kernel': f'OK ({len(items)})'}
            except OSError as e2:
                return [], {'kernel': f'Error: {e2}'}
else:
    def scan(wake_bluetooth=True):
        return [], {'all': 'Device batteries are read on Windows and Linux only.'}


class BatteryMonitor:
    """Scans on its own thread; the collector picks up the latest result."""

    def __init__(self, interval=SCAN_SECONDS, scanner=None):
        self.interval = interval
        self.scanner = scanner or scan
        self.lock = threading.Lock()
        self.items, self.status, self.scanned_at = [], {}, 0.0
        self.stop_flag = threading.Event()
        self.thread = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return
        self.stop_flag = threading.Event()   # per thread, so a quick stop/start never leaves two scanners
        self.thread = threading.Thread(target=self._run, args=(self.stop_flag,), name='batteries', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        self.thread = None
        with self.lock:
            self.items, self.scanned_at = [], 0.0

    def _run(self, stop_flag):
        while not stop_flag.is_set():
            items, status = self.scanner()
            with self.lock:
                if not stop_flag.is_set():
                    self.items, self.status, self.scanned_at = items, status, time.monotonic()
            stop_flag.wait(self.interval)

    def latest(self):
        with self.lock:
            return list(self.items), dict(self.status), self.scanned_at

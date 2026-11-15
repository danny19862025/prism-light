# SPDX-License-Identifier: GPL-3.0-only
"""Live gaming sensors for the AIO display: CPU/GPU temperature and FPS.

* GPU (AMD Radeon): the installed driver's ADL library, PMLog sensors
  (edge 8, hotspot 27, activity 19), per AMD's MIT-licensed ADL SDK headers.
* CPU (AMD Ryzen, family 17h-1Ah): Tctl from SMN 0x59800 through the signed
  PawnIO AMDFamily17 module, read-only, formula as in Linux k10temp and
  LibreHardwareMonitor. Needs administrator rights.
* FPS: Intel PresentMon (MIT) streaming present events; the rate for the
  foreground window's process. Runs only while something needs FPS.
Every reader degrades to None instead of raising into the UI.
"""
import collections
import ctypes
import csv
import io
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
VENDOR = (Path(sys.executable).parent if getattr(sys, 'frozen', False) else ROOT) / 'vendor'
PCI_MUTEX = 'Global\\Access_PCI'
TCTL_SMN = 0x00059800


def tctl(raw):
    """Ryzen Tctl in °C from the THM_TCON_CUR_TMP register value."""
    offset = (raw & 0x80000) or (raw & 0x30000) == 0x30000
    return (raw >> 21) * 0.125 - (49 if offset else 0)


class AmdGpu:
    EDGE, HOTSPOT, ACTIVITY = 8, 27, 19

    def __init__(self, library='atiadlxx.dll'):
        self.adl = ctypes.CDLL(library)
        self.buffers = []
        malloc = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_int)
        self.alloc = malloc(self._alloc)
        self.ctx = ctypes.c_void_p()
        if self.adl.ADL2_Main_Control_Create(self.alloc, 1, ctypes.byref(self.ctx)):
            raise OSError('AMD display driver did not start.')
        self.adapter, self.name = self.find_adapter()

    def _alloc(self, size):
        buffer = ctypes.create_string_buffer(size)
        self.buffers.append(buffer)
        return ctypes.addressof(buffer)

    class Info(ctypes.Structure):
        _fields_ = [('iSize', ctypes.c_int), ('iAdapterIndex', ctypes.c_int), ('strUDID', ctypes.c_char*256),
                    ('iBusNumber', ctypes.c_int), ('iDeviceNumber', ctypes.c_int), ('iFunctionNumber', ctypes.c_int),
                    ('iVendorID', ctypes.c_int), ('strAdapterName', ctypes.c_char*256), ('strDisplayName', ctypes.c_char*256),
                    ('iPresent', ctypes.c_int), ('iExist', ctypes.c_int), ('strDriverPath', ctypes.c_char*256),
                    ('strDriverPathExt', ctypes.c_char*256), ('strPNPString', ctypes.c_char*256), ('iOSDisplayIndex', ctypes.c_int)]

    class Sensor(ctypes.Structure):
        _fields_ = [('supported', ctypes.c_int), ('value', ctypes.c_int)]

    class Log(ctypes.Structure):
        pass

    Log._fields_ = [('size', ctypes.c_int), ('sensors', Sensor*256)]

    def query(self, adapter):
        out = self.Log()
        out.size = ctypes.sizeof(self.Log)
        if self.adl.ADL2_New_QueryPMLogData_Get(self.ctx, adapter, ctypes.byref(out)):
            return None
        return {i: s.value for i, s in enumerate(out.sensors) if s.supported}

    def find_adapter(self):
        count = ctypes.c_int()
        self.adl.ADL2_Adapter_NumberOfAdapters_Get(self.ctx, ctypes.byref(count))
        infos = (self.Info*max(1, count.value))()
        self.adl.ADL2_Adapter_AdapterInfo_Get(self.ctx, infos, ctypes.sizeof(infos))
        candidates, seen = [], set()
        for info in infos[:count.value]:
            if info.iBusNumber in seen:
                continue
            seen.add(info.iBusNumber)
            sensors = self.query(info.iAdapterIndex)
            if sensors and self.EDGE in sensors:
                name = info.strAdapterName.decode(errors='replace')
                # Prefer a discrete card over integrated "Radeon(TM) Graphics".
                candidates.append(('(TM) Graphics' in name, info.iAdapterIndex, name))
        if not candidates:
            raise OSError('No AMD GPU temperature sensor found.')
        _integrated, adapter, name = sorted(candidates)[0]
        return adapter, name

    def read(self):
        s = self.query(self.adapter) or {}
        return {'gpu_temp': s.get(self.EDGE), 'gpu_hotspot': s.get(self.HOTSPOT), 'gpu_load': s.get(self.ACTIVITY)}

    def close(self):
        if self.ctx:
            self.adl.ADL2_Main_Control_Destroy(self.ctx)
            self.ctx = None


class RyzenTemp:
    def __init__(self, port=None):
        if port is None:
            from .fans import PawnIOPort
            from .hardware import Hardware
            port = PawnIOPort(Hardware().executable.parent, VENDOR/'pawnio-modules'/'AMDFamily17.bin', PCI_MUTEX)
        self.port = port

    def read(self):
        self.port.lock()
        try:
            raw = self.port.execute('ioctl_read_smn', (TCTL_SMN,), 1)
        finally:
            self.port.unlock()
        value = tctl(raw)
        return {'cpu_temp': value if 0 < value < 125 else None}

    def close(self):
        if hasattr(self.port, 'close'):
            self.port.close()


def foreground_pid():
    if sys.platform != 'win32':
        return None
    user32 = ctypes.windll.user32
    pid = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value or None


class FpsMonitor:
    """Parses PresentMon CSV from stdout; FPS of the foreground process."""
    window = 1.0
    ignored = {'dwm.exe', 'explorer.exe', 'searchhost.exe', 'shellexperiencehost.exe', 'textinputhost.exe',
               'startmenuexperiencehost.exe', 'presentmon.exe'}

    def __init__(self, executable=None, launcher=None, foreground=foreground_pid):
        self.executable = Path(executable or VENDOR/'presentmon'/'PresentMon.exe')
        self.launcher = launcher or self.launch
        self.foreground = foreground
        self.lock = threading.Lock()
        self.frames = collections.defaultdict(collections.deque)   # pid -> arrival times
        self.names = {}
        self.process = None
        self.thread = None
        self.error = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def launch(self):
        if not self.executable.is_file():
            raise OSError('PresentMon is missing from vendor/presentmon.')
        argv = [str(self.executable), '--output_stdout', '--no_console_stats', '--v1_metrics',
                '--session_name', 'PrismLightFPS', '--stop_existing_session',
                '--no_track_input', '--no_track_gpu', '--no_track_display']
        return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def start(self):
        if self.running:
            return
        self.error = None
        try:
            self.process = self.launcher()
        except OSError as e:
            self.error = f'FPS unavailable: {e}'
            return
        self.thread = threading.Thread(target=self.read_stream, name='fps', daemon=True)
        self.thread.start()

    def read_stream(self):
        stream = self.process.stdout
        header = None
        for line in stream:
            row = next(csv.reader(io.StringIO(line)), None)
            if not row:
                continue
            if header is None:
                if 'ProcessID' in row:
                    header = {name.casefold(): i for i, name in enumerate(row)}
                continue
            self.add(row, header)
        code = self.process.poll() if self.process else None
        if not self.error:
            self.error = ('FPS needs administrator rights (Run-PrismLight.cmd).' if header is None
                          else f'FPS capture ended ({code}).')

    def add(self, row, header):
        try:
            pid = int(row[header['processid']])
            name = row[header['application']]
        except (KeyError, ValueError, IndexError):
            return
        if name.casefold() in self.ignored:
            return
        now = time.monotonic()
        with self.lock:
            frames = self.frames[pid]
            frames.append(now)
            self.names[pid] = name
            while frames and now - frames[0] > self.window:
                frames.popleft()

    def read(self):
        now = time.monotonic()
        with self.lock:
            for pid in list(self.frames):
                frames = self.frames[pid]
                while frames and now - frames[0] > self.window:
                    frames.popleft()
                if not frames:
                    del self.frames[pid]
            pid = self.foreground()
            if pid not in self.frames:
                pid = max(self.frames, key=lambda p: len(self.frames[p]), default=None)
            if pid is None:
                return {'fps': None, 'fps_app': None}
            return {'fps': round(len(self.frames[pid])/self.window), 'fps_app': self.names.get(pid)}

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        if self.thread:
            self.thread.join(3)
        self.thread = None
        with self.lock:
            self.frames.clear()


class SensorHub:
    """Polls GPU and CPU once a second on a background thread."""
    interval = 1.0

    def __init__(self, gpu=AmdGpu, cpu=RyzenTemp, fps=None):
        self.factories = {'gpu': gpu, 'cpu': cpu}
        self.fps = fps or FpsMonitor()
        self.values = {}
        self.notes = {}
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self.run, name='sensors', daemon=True)
        self.thread.start()

    def run(self):
        readers = {}
        for key, factory in self.factories.items():
            try:
                readers[key] = factory()
            except Exception as e:
                self.notes[key] = str(e)
        try:
            while not self.stop_flag.is_set():
                values = {}
                for key, reader in list(readers.items()):
                    try:
                        values.update(reader.read())
                    except Exception as e:
                        self.notes[key] = str(e)
                with self.lock:
                    self.values = values
                self.stop_flag.wait(self.interval)
        finally:
            for reader in readers.values():
                try:
                    reader.close()
                except Exception:
                    pass

    def want_fps(self, on):
        if on and not self.fps.running and not self.fps.error:
            self.fps.start()
        elif not on and self.fps.running:
            self.fps.stop()

    def read(self):
        with self.lock:
            values = dict(self.values)
        if self.fps.running:
            values.update(self.fps.read())
        return values

    def stop(self):
        self.stop_flag.set()
        if self.thread:
            self.thread.join(3)
        self.thread = None
        self.fps.stop()

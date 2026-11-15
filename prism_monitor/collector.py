# SPDX-License-Identifier: GPL-3.0-only
"""Background polling: merges all sources into one Snapshot per interval.

Runs on its own thread so slow driver calls never block the UI; results are
delivered to the GUI thread through a queued Qt signal.
"""
import threading
import time
import traceback

from PySide6.QtCore import QObject, Signal

from .model import Snapshot
from .sources import AdlGpuSource, SystemSource


class Collector(QObject):
    snapshot = Signal(object)

    def __init__(self, host, tracker, interval=1.0, parent=None):
        super().__init__(parent)
        self.host = host
        self.tracker = tracker
        self.interval = interval
        self.fps_target = ''
        self.fps_note = 'FPS capture is off.'
        self.stop_flag = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.status = {}
        self.info = {}

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._run, name='collector', daemon=True)
        self.thread.start()

    def set_interval(self, seconds):
        self.interval = max(0.25, min(10.0, float(seconds)))
        self.wake.set()

    def _open(self, factory, key):
        try:
            source = factory()
            self.status[key] = 'OK'
            return source
        except Exception as e:  # driver missing, API absent, …
            self.status[key] = f'Unavailable: {e}'
            return None

    def _run(self):
        system = self._open(SystemSource, 'system')
        gpu = self._open(AdlGpuSource, 'adl')
        if system:
            self.info['cpu_name'] = system.cpu_name
        if gpu:
            self.info['gpu_name'] = gpu.device
        try:
            while not self.stop_flag.is_set():
                started = time.monotonic()
                snap = Snapshot(status=dict(self.status))
                snap.info.update(self.info)
                for key, source in (('system', system), ('adl', gpu)):
                    if source is None:
                        continue
                    try:
                        snap.readings.update(source.read())
                        snap.status[key] = 'OK'
                    except Exception as e:
                        snap.status[key] = f'Error: {e}'
                        traceback.print_exc()
                host_readings, fans, host_info = self.host.read()
                # Prefer privileged LibreHardwareMonitor values for CPU metrics; GPU
                # comes from ADL, with LibreHardwareMonitor's copy as a fallback.
                for key, item in host_readings.items():
                    if key.endswith('_lhm'):
                        base = key[:-4]
                        if base not in snap.readings:
                            snap.readings[base] = item
                    else:
                        snap.readings[key] = item
                snap.fans = fans
                for key, value in host_info.items():
                    snap.info.setdefault(key, value)
                fps, app = self.tracker.read(self.fps_target)
                snap.readings.update({k: v for k, v in fps.items() if v is not None})
                snap.info['fps_app'] = app
                snap.status['fps'] = self.fps_note
                snap.status['helper'] = self.host.detail
                self.snapshot.emit(snap)
                elapsed = time.monotonic() - started
                self.wake.wait(max(0.05, self.interval - elapsed))
                self.wake.clear()
        finally:
            for source in (system, gpu):
                if source is not None:
                    try:
                        source.close()
                    except Exception:
                        pass

    def stop(self):
        self.stop_flag.set()
        self.wake.set()
        if self.thread:
            self.thread.join(3)
        self.thread = None

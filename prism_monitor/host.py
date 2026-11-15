# SPDX-License-Identifier: GPL-3.0-only
"""Client for the elevated sensor helper (PrismSensorHost.exe).

The widget always runs as a standard user. CPU temperature, package power,
per-core clocks and motherboard fans need ring-0 access, which
LibreHardwareMonitorLib gets through the PawnIO driver, so they are read in
a separate helper process that the user starts through the Windows
administrator prompt. The helper streams JSON over a per-session named pipe
that only this user can open, and exits as soon as the widget does.
"""
import json
import secrets
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket

from . import winapi
from .sources import map_host_items


def app_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def vendor_dir():
    return app_dir() / 'vendor'


def helper_path():
    return vendor_dir() / 'sensorhost' / 'PrismSensorHost.exe'


class HostClient(QObject):
    state_changed = Signal(str)
    _launched = Signal(object, str)   # (process handle or None, error)

    def __init__(self, tracker=None, parent=None):
        super().__init__(parent)
        self.tracker = tracker
        self.socket = None
        self.buffer = b''
        self.lock = threading.Lock()
        self.latest = ({}, {}, {})
        self.latest_ts = 0.0
        self.state = 'off'
        self.detail = 'Not started.'
        self.handle = None
        self.pipe = None
        self.connect_deadline = 0.0
        self.interval_ms = 1000
        self.want_presentmon = False
        self.hello = {}
        self.retry = QTimer(self, interval=250, timeout=self._try_connect)
        self.watch = QTimer(self, interval=2000, timeout=self._watch)
        self._launched.connect(self._on_launched)

    # -- lifecycle --------------------------------------------------------
    def set_state(self, state, detail):
        self.state, self.detail = state, detail
        self.state_changed.emit(state)

    def start(self):
        if self.state in ('starting', 'connected'):
            return
        exe = helper_path()
        if not winapi.WINDOWS:
            self.set_state('failed', 'The sensor helper is Windows-only.')
            return
        if not exe.is_file():
            self.set_state('failed', f'Sensor helper missing: {exe}')
            return
        if winapi.service_running('PawnIO') is None:
            note = ' PawnIO is not installed, so CPU temperature, CPU power and motherboard fans will stay unavailable.'
        else:
            note = ''
        self.pipe = 'PrismMonitor-' + secrets.token_hex(12)
        try:
            sid = winapi.current_user_sid()
        except OSError as e:
            self.set_state('failed', f'Could not read the current user SID: {e}')
            return
        params = f'--pipe {self.pipe} --sid {sid} --parent {__import__("os").getpid()}'
        self.set_state('starting', 'Waiting for the Windows administrator prompt…' + note)

        def launch():
            try:
                handle = winapi.run_elevated(exe, params, exe.parent)
                self._launched.emit(handle, '')
            except PermissionError as e:
                self._launched.emit(None, str(e))
            except OSError as e:
                self._launched.emit(None, f'Could not start the sensor helper: {e}')
        threading.Thread(target=launch, name='helper-launch', daemon=True).start()

    def _on_launched(self, handle, error):
        if error:
            self.set_state('failed', error)
            return
        self.handle = handle
        self.connect_deadline = time.monotonic() + 30
        self.retry.start()

    def _try_connect(self):
        if self.handle and winapi.process_exited(self.handle):
            self.retry.stop()
            self.set_state('failed', 'The sensor helper exited before connecting.')
            return
        if time.monotonic() > self.connect_deadline:
            self.retry.stop()
            self.set_state('failed', 'The sensor helper did not open its pipe in time.')
            return
        socket = QLocalSocket(self)
        socket.connectToServer(self.pipe)
        if not socket.waitForConnected(100):
            socket.deleteLater()
            return
        self.retry.stop()
        self.socket = socket
        socket.readyRead.connect(self._read)
        socket.disconnected.connect(self._disconnected)
        self.set_state('connected', 'Connected. Reading CPU and motherboard sensors through LibreHardwareMonitor.')
        self.send(f'interval {self.interval_ms}')
        if self.want_presentmon:
            self.send('presentmon on')
        self.watch.start()

    def _watch(self):
        if self.state == 'connected' and time.monotonic() - self.latest_ts > 15 and self.latest_ts:
            self.detail = 'Connected, but no sensor data for 15 s.'
            self.state_changed.emit(self.state)

    def _disconnected(self):
        self.watch.stop()
        if self.state == 'connected':
            self.set_state('failed', 'The sensor helper disconnected.')
        with self.lock:
            self.latest = ({}, {}, {})

    def stop(self):
        self.retry.stop()
        self.watch.stop()
        if self.socket is not None:
            if self.socket.state() == QLocalSocket.ConnectedState:
                self.send('quit')
                self.socket.flush()
                self.socket.waitForBytesWritten(300)
            self.socket.abort()
            self.socket.deleteLater()
            self.socket = None
        winapi.close_handle(self.handle)
        self.handle = None
        with self.lock:
            self.latest = ({}, {}, {})
        if self.state != 'off':
            self.set_state('off', 'Stopped.')

    # -- protocol ---------------------------------------------------------
    def send(self, line):
        if self.socket is not None and self.socket.state() == QLocalSocket.ConnectedState:
            self.socket.write((line + '\n').encode())

    def set_interval(self, ms):
        self.interval_ms = int(ms)
        self.send(f'interval {self.interval_ms}')

    def set_presentmon(self, on):
        self.want_presentmon = on
        self.send('presentmon on' if on else 'presentmon off')

    def _read(self):
        self.buffer += bytes(self.socket.readAll())
        if len(self.buffer) > 8 << 20:   # defensive bound
            self.buffer = b''
        *lines, self.buffer = self.buffer.split(b'\n')
        for raw in lines:
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            kind = message.get('t')
            if kind == 'sensors':
                mapped = map_host_items(message.get('items') or [])
                if not self.hello.get('elevated', True):
                    mapped = ({}, {}, mapped[2])
                with self.lock:
                    self.latest = mapped
                    self.latest_ts = time.monotonic()
            elif kind == 'pm' and self.tracker is not None:
                self.tracker.feed(str(message.get('l', '')))
            elif kind == 'hello':
                self.hello = message
            elif kind in ('pmexit', 'pmerror'):
                self.pm_error = message.get('msg', 'PresentMon exited in the sensor helper.')

    def read(self):
        with self.lock:
            return self.latest

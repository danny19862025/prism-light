# SPDX-License-Identifier: GPL-3.0-only
"""Client for the elevated sensor helper (PrismSensorHost.exe).

The widget always runs as a standard user. CPU temperature, package power,
per-core clocks and motherboard fans need ring-0 access, which
LibreHardwareMonitorLib gets through the PawnIO driver, so they are read in
a separate helper process that the user starts through the Windows
administrator prompt. The helper streams JSON over a per-session named pipe
that only this user can open, and exits as soon as the widget does.
"""
import hashlib
import json
import os
import secrets
import subprocess
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


# One-time "automatic" setup: an admin-protected copy plus an on-demand task.
TASK_NAME = 'Prism Monitor Sensor Helper'


def installed_root():
    return Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Prism Monitor Sensor Helper'


def installed_helper():
    return installed_root() / 'sensorhost' / 'PrismSensorHost.exe'


def _sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def task_status():
    """'missing', 'outdated' or 'ready' for the automatic helper."""
    if not installed_helper().is_file():
        return 'missing'
    if _sha256(installed_helper()) != _sha256(helper_path()):
        return 'outdated'
    return 'ready'


def _no_window():
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class HostClient(QObject):
    state_changed = Signal(str)
    setup_finished = Signal(bool, str)     # (task now installed, message)
    _launched = Signal(object, str)   # (process handle or None, error)
    _setup_done = Signal(str, object, str)   # (action, exit code, error)

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
        self._setup_done.connect(self._on_setup_done)
        self.expected_server = None
        self.mode = None            # 'task' (automatic, restartable) or 'uac'
        self.stopping = False
        self.failures = 0
        self.connected_at = 0.0
        self.restart_timer = QTimer(self, singleShot=True, timeout=lambda: self.start(use_task=True))

    RESTART_DELAYS = (3, 10, 30, 60)
    owner_hwnd = staticmethod(lambda: None)   # set by the app: a window handle that owns UAC prompts

    def _fail(self, detail):
        """Report a failure; in automatic mode, restart the helper silently with backoff."""
        if self.mode == 'task' and not self.stopping and task_status() == 'ready':
            delay = self.RESTART_DELAYS[min(self.failures, len(self.RESTART_DELAYS) - 1)]
            self.failures += 1
            self.restart_timer.start(delay * 1000)
            detail = f'{detail} Restarting automatically in {delay} s.'
        self.set_state('failed', detail)

    # -- lifecycle --------------------------------------------------------
    def set_state(self, state, detail):
        self.state, self.detail = state, detail
        self.state_changed.emit(state)

    def start(self, use_task=False):
        """Start the helper: through the installed task (no prompt) or the UAC prompt."""
        if self.state in ('starting', 'connected', 'setup'):
            return
        self.restart_timer.stop()
        self.stopping = False
        self.mode = 'task' if use_task else 'uac'
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
        try:
            sid = winapi.current_user_sid()
        except OSError as e:
            self.set_state('failed', f'Could not read the current user SID: {e}')
            return
        if use_task:
            self.start_task(sid, note)
            return
        self.expected_server = exe.resolve()
        self.pipe = 'PrismMonitor-' + secrets.token_hex(12)
        params = f'--pipe {self.pipe} --sid {sid} --parent {__import__("os").getpid()}'
        self.set_state('starting', 'Waiting for the Windows administrator prompt…' + note)
        owner = self.owner_hwnd()   # read on the GUI thread

        def launch():
            try:
                handle = winapi.run_elevated(exe, params, exe.parent, owner)
                self._launched.emit(handle, '')
            except PermissionError as e:
                self._launched.emit(None, str(e))
            except OSError as e:
                self._launched.emit(None, f'Could not start the sensor helper: {e}')
        threading.Thread(target=launch, name='helper-launch', daemon=True).start()

    def start_task(self, sid, note=''):
        status = task_status()
        if status != 'ready':
            self.set_state('failed', 'The automatic sensor helper is not set up.' if status == 'missing' else
                           'The automatic sensor helper is from a different Prism Monitor version. '
                           'Run Settings → Sensors → Set up automatic sensor helper again.')
            return
        self.expected_server = installed_helper().resolve()
        self.pipe = 'PrismMonitorHelper-' + sid
        schtasks = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'schtasks.exe'
        try:
            result = subprocess.run([str(schtasks), '/Run', '/TN', TASK_NAME], capture_output=True, text=True,
                                    stdin=subprocess.DEVNULL,  # windowed builds have no stdin handle
                                    timeout=20, creationflags=_no_window())
        except (OSError, subprocess.TimeoutExpired) as e:
            self._fail(f'Could not start the sensor helper task: {e}')
            return
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            self._fail(f'The sensor helper task did not start: {message}')
            return
        self.set_state('starting', 'Starting the automatic sensor helper…' + note)
        self.handle = None
        self.connect_deadline = time.monotonic() + 30
        self.retry.start()

    def setup_task(self):
        """One administrator prompt: copy the helper to Program Files and register the task."""
        self._elevated_action('install')

    def remove_task(self):
        self._elevated_action('uninstall')

    def _elevated_action(self, action):
        exe = helper_path()
        if not exe.is_file():
            self.setup_finished.emit(task_status() != 'missing', f'Sensor helper missing: {exe}')
            return
        try:
            params = '--uninstall' if action == 'uninstall' else f'--install --sid {winapi.current_user_sid()}'
        except OSError as e:
            self.setup_finished.emit(False, f'Could not read the current user SID: {e}')
            return
        if action == 'uninstall' or self.state in ('connected', 'starting'):
            self.stop()   # the installed copy is replaced or removed
        self.set_state('setup', 'Waiting for the Windows administrator prompt (one-time setup)…')
        owner = self.owner_hwnd()

        def run():
            try:
                handle = winapi.run_elevated(exe, params, exe.parent, owner)
            except OSError as e:
                self._setup_done.emit(action, None, str(e))
                return
            try:
                code = winapi.wait_exit_code(handle, 180000)
            finally:
                winapi.close_handle(handle)
            self._setup_done.emit(action, code, '' if code == 0 else f'Setup exited with code {code}.')
        threading.Thread(target=run, name=f'helper-{action}', daemon=True).start()

    def _on_setup_done(self, action, code, error):
        installed = task_status() == 'ready'
        if action == 'install':
            ok = code == 0 and installed
            message = ('Automatic sensor helper set up. CPU temperature, power, clocks and fans now start '
                       'without a prompt.') if ok else f'Setup did not complete. {error}'
        else:
            ok = code == 0 and task_status() == 'missing'
            installed = not ok and installed
            message = 'Automatic sensor helper removed.' if ok else f'Removal did not complete. {error}'
        self.set_state('off', message)
        self.setup_finished.emit(installed, message)

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
            self._fail('The sensor helper exited before connecting.')
            return
        if time.monotonic() > self.connect_deadline:
            self.retry.stop()
            self._fail('The sensor helper did not open its pipe in time.')
            return
        socket = QLocalSocket(self)
        socket.connectToServer(self.pipe)
        if not socket.waitForConnected(100):
            socket.deleteLater()
            return
        self.retry.stop()
        # Only trust a pipe served by the expected helper binary (guards against pipe squatting).
        server = winapi.pipe_server_image(int(socket.socketDescriptor()))
        if self.expected_server and (not server or Path(server).resolve() != self.expected_server):
            socket.abort()
            socket.deleteLater()
            self.set_state('failed', f'Refused a sensor pipe served by an unexpected program: {server or "unknown"}')
            return
        self.socket = socket
        socket.readyRead.connect(self._read)
        socket.disconnected.connect(self._disconnected)
        self.connected_at = time.monotonic()
        self.set_state('connected', 'Connected. Reading CPU and motherboard sensors through LibreHardwareMonitor.')
        self.send(f'interval {self.interval_ms}')
        if self.want_presentmon:
            self.send('presentmon on')
        self.watch.start()

    def _watch(self):
        if self.state == 'connected' and self.failures and time.monotonic() - self.connected_at > 60:
            self.failures = 0   # stable again: next failure restarts quickly
        if self.state == 'connected' and time.monotonic() - self.latest_ts > 15 and self.latest_ts:
            self.detail = 'Connected, but no sensor data for 15 s.'
            self.state_changed.emit(self.state)

    def _disconnected(self):
        self.watch.stop()
        with self.lock:
            self.latest = ({}, {}, {})
        if self.state == 'connected' and not self.stopping:
            if self.socket is not None:
                self.socket.deleteLater()
                self.socket = None
            self._fail('The sensor helper stopped.')

    def stop(self):
        self.stopping = True
        self.mode = None
        self.restart_timer.stop()
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

# SPDX-License-Identifier: GPL-3.0-only
"""Game FPS and frame time from Intel PresentMon (MIT).

PresentMon reads the Windows Event Tracing (ETW) events that DXGI, D3D9,
DirectX kernel and the compositor already emit for every present call. Nothing
is injected into the game and nothing touches anti-cheat, so it works for
Direct3D 9/10/11/12, Vulkan and OpenGL games that present through the Windows
graphics stack. The numbers belong to the selected game process: each CSV row
carries the presenting ProcessID and the time since that process's previous
present (MsBetweenPresents). The widget's own drawing and the monitor refresh
rate are not involved.

Starting an ETW session needs administrator rights or membership of the
built-in "Performance Log Users" group. If neither is available the sensor
helper (already elevated) can run PresentMon and relay its CSV.
"""
import collections
import csv
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import winapi
from .model import reading

SESSION = 'PrismMonitorFPS'
PRESENTMON_ARGS = ['--output_stdout', '--no_console_stats', '--v1_metrics', '--session_name', SESSION,
                   '--stop_existing_session', '--no_track_input', '--no_track_gpu', '--no_track_display',
                   '--exclude', 'dwm.exe']
# Shell/system processes that present but are never the game in automatic mode.
AUTO_IGNORED = {'dwm.exe', 'explorer.exe', 'searchhost.exe', 'shellexperiencehost.exe', 'textinputhost.exe',
                'startmenuexperiencehost.exe', 'presentmon.exe', 'prismmonitor.exe', 'lockapp.exe',
                'applicationframehost.exe', 'systemsettings.exe', 'widgets.exe', 'searchapp.exe'}
WINDOW_S = 1.0       # FPS averaging window
STALE_S = 2.0        # no presents for this long → unavailable
FORGET_S = 10.0


class FrameTracker:
    """Thread-safe PresentMon CSV consumer with per-process frame statistics."""

    def __init__(self, own_pid=None, resolve_name=winapi.process_image_name, foreground=winapi.foreground_pid):
        self.own_pid = own_pid if own_pid is not None else os.getpid()
        self.resolve_name = resolve_name
        self.foreground = foreground
        self.lock = threading.Lock()
        self.header = None
        self.processes = {}          # pid -> {'name', 'chains': {swapchain: deque[(t, ms)]}, 'seen'}
        self.auto_pid = None
        self.auto_since = 0.0
        self.busiest_when_unknown = sys.platform.startswith('linux')
        self.source = 'vklayer' if self.busiest_when_unknown else 'presentmon'

    def reset(self):
        with self.lock:
            self.header = None
            self.processes.clear()

    def feed(self, line, now=None):
        row = next(csv.reader([line]), None)
        if not row:
            return
        if 'ProcessID' in row and 'Application' in row:
            with self.lock:
                # v1 CSV uses lower-case "msBetweenPresents", v2 "MsBetweenPresents".
                self.header = {name.casefold(): i for i, name in enumerate(row)}
            return
        header = self.header
        if header is None:
            return
        try:
            pid = int(row[header['processid']])
            ms = float(row[header['msbetweenpresents']])
            chain = row[header['swapchainaddress']] if 'swapchainaddress' in header else ''
            if 'timeinseconds' in header:
                t = float(row[header['timeinseconds']])
            elif 'timeinms' in header:
                t = float(row[header['timeinms']]) / 1000.0
            else:
                t = None
            name = row[header['application']]
        except (KeyError, ValueError, IndexError):
            return
        self.add(pid, name, chain, t, ms, now)

    def add(self, pid, name, chain, t, ms, now=None):
        """One present of `pid` on swapchain `chain`: t seconds (or None), ms since its previous present."""
        if pid == self.own_pid or pid <= 4:
            return
        now = time.monotonic() if now is None else now
        with self.lock:
            proc = self.processes.get(pid)
            if proc is None:
                if not name or name.startswith('<'):
                    name = self.resolve_name(pid) or f'PID {pid}'
                proc = self.processes[pid] = {'name': name, 'chains': {}, 'seen': now}
            proc['seen'] = now
            frames = proc['chains'].setdefault(chain, collections.deque(maxlen=4000))
            frames.append((t if t is not None else now, ms))

    def _prune(self, now):
        for pid in [p for p, proc in self.processes.items() if now - proc['seen'] > FORGET_S]:
            del self.processes[pid]

    @staticmethod
    def _stats(proc):
        best = None
        chains = [frames for frames in proc['chains'].values() if frames]
        newest = max((frames[-1][0] for frames in chains), default=None)
        for frames in chains:
            latest = frames[-1][0]
            if latest < newest - STALE_S:
                continue   # a swapchain the game replaced (resize, mode change) keeps its last frames forever
            window = [ms for t, ms in frames if t >= latest - WINDOW_S and 0 < ms < 5000]
            if window and (best is None or len(window) > len(best)):
                best = window
        if not best:
            return None
        mean = sum(best) / len(best)
        return 1000.0 / mean, mean, max(best)

    def presenting(self, now=None):
        """[(pid, name, fps)] for processes that presented recently, busiest first."""
        now = time.monotonic() if now is None else now
        out = []
        with self.lock:
            self._prune(now)
            for pid, proc in self.processes.items():
                if now - proc['seen'] <= STALE_S:
                    stats = self._stats(proc)
                    if stats:
                        out.append((pid, proc['name'], stats[0]))
        return sorted(out, key=lambda x: -x[2])

    def _choose(self, target, now):
        live = {pid: p for pid, p in self.processes.items() if now - p['seen'] <= STALE_S}
        if target:
            matches = [pid for pid, p in live.items() if p['name'].casefold() == target.casefold()]
            return max(matches, key=lambda pid: len(self._stats(live[pid]) or ()), default=None)
        fg = self.foreground()
        if fg in live and live[fg]['name'].casefold() not in AUTO_IGNORED:
            self.auto_pid, self.auto_since = fg, now
            return fg
        # Brief grace when focus moves to a non-presenting window (e.g. a notification).
        if self.auto_pid in live and now - self.auto_since < 3.0:
            return self.auto_pid
        if self.busiest_when_unknown:
            # Linux/Wayland: the focused app is not always knowable (X11's active window can be a stale
            # XWayland window while a Wayland app has focus). Follow the busiest presenter.
            best, best_fps = None, 0.0
            for pid, proc in live.items():
                stats = self._stats(proc)
                if stats and stats[0] > best_fps and proc['name'].casefold() not in AUTO_IGNORED:
                    best, best_fps = pid, stats[0]
            return best
        return None

    def read(self, target='', now=None):
        """Readings for the target process name ('' = foreground application)."""
        now = time.monotonic() if now is None else now
        with self.lock:
            self._prune(now)
            pid = self._choose(target, now)
            if pid is None:
                return {}, None
            proc = self.processes[pid]
            stats = self._stats(proc)
            name = proc['name']
        if not stats:
            return {}, name
        fps, frametime, worst = stats
        return {'fps': reading(fps, self.source, now=now),
                'frametime': reading(frametime, self.source, extra=worst, now=now)}, name


class LocalPresentMon:
    """Runs the bundled PresentMon.exe as the current user."""

    def __init__(self, executable, tracker):
        self.executable = Path(executable)
        self.tracker = tracker
        self.process = None
        self.thread = None
        self.error = None
        self.got_header = False
        self.stderr_tail = collections.deque(maxlen=20)

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self):
        if self.running:
            return True
        self.error, self.got_header = None, False
        if not self.executable.is_file():
            self.error = f'PresentMon is missing ({self.executable}).'
            return False
        winapi.stop_trace_session(SESSION)   # clear a session orphaned by a crash
        try:
            self.process = subprocess.Popen([str(self.executable)] + PRESENTMON_ARGS, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
                                            encoding='utf-8', errors='replace', bufsize=1,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError as e:
            self.error = f'PresentMon could not start: {e}'
            return False
        winapi.tie_to_this_process(self.process.pid)
        self.tracker.reset()
        self.thread = threading.Thread(target=self._pump, name='presentmon', daemon=True)
        self.thread.start()
        threading.Thread(target=self._pump_err, name='presentmon-err', daemon=True).start()
        return True

    def _pump(self):
        process = self.process
        for line in process.stdout:
            if not self.got_header and 'ProcessID' in line:
                self.got_header = True
            self.tracker.feed(line.rstrip('\r\n'))
        code = process.wait()
        if not self.got_header:
            detail = ' '.join(self.stderr_tail).strip()
            self.error = ('PresentMon could not start an ETW trace session (needs administrator rights or the '
                          f'"Performance Log Users" group). Exit {code}. {detail[:200]}').strip()
        elif self.process is process:
            self.error = f'PresentMon stopped (exit {code}).'

    def _pump_err(self):
        process = self.process
        try:
            for line in process.stderr:
                self.stderr_tail.append(line.strip())
        except (ValueError, OSError):
            pass

    def stop(self):
        process, self.process = self.process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(3)
            except subprocess.TimeoutExpired:
                process.kill()
        if process:
            winapi.stop_trace_session(SESSION)
        if self.thread:
            self.thread.join(3)
            self.thread = None


class LinuxFrameReceiver:
    """Receives present timestamps from Prism Monitor's Vulkan layer (see monitor/vklayer).

    Same interface as LocalPresentMon. The socket lives in the abstract namespace, so it
    reaches games inside Steam's container runtime too. Each datagram's sender uid and pid
    come from the kernel (SO_PASSCRED); datagrams from other users are dropped.
    """
    PREFIX = 'PFPS1'

    def __init__(self, tracker, name=None):
        self.tracker = tracker
        self.address = '\0' + (name or f'prism-monitor-fps-{os.getuid()}')
        self.sock = None
        self.thread = None
        self.error = None
        self.got_header = False      # True once any game has sent a frame
        self.last = {}               # (pid, chain) -> previous timestamp in seconds
        self.uid = os.getuid()

    @property
    def running(self):
        return self.sock is not None and self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.running:
            return True
        self.error = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            sock.bind(self.address)
        except OSError as e:
            self.error = (f'Could not open the FPS socket: {e}. Is another Prism Monitor running?'
                          if getattr(e, 'errno', None) == 98 else f'Could not open the FPS socket: {e}.')
            return False
        sock.settimeout(0.5)
        self.sock = sock
        self.tracker.reset()
        self.last.clear()
        self.thread = threading.Thread(target=self._pump, args=(sock,), name='fps-layer', daemon=True)
        self.thread.start()
        return True

    def _pump(self, sock):
        creds_size = socket.CMSG_SPACE(struct.calcsize('3i'))
        while self.sock is sock:
            try:
                data, ancillary, _flags, _addr = sock.recvmsg(512, creds_size)
            except socket.timeout:
                continue
            except OSError:
                break
            pid = uid = None
            for level, kind, payload in ancillary:
                if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS and len(payload) >= 12:
                    pid, uid, _gid = struct.unpack('3i', payload[:12])
            if uid != self.uid or not pid:
                continue
            self.handle(pid, data)

    def handle(self, pid, data, now=None):
        parts = data.decode('utf-8', 'replace').split('\t', 3)
        if len(parts) != 4 or parts[0] != self.PREFIX:
            return
        try:
            t = int(parts[2]) / 1e9
        except ValueError:
            return
        chain, name = parts[1], parts[3].strip()
        key = (pid, chain)
        previous = self.last.get(key)
        self.last[key] = t
        if len(self.last) > 512:
            self.last = {key: t}
        if previous is None or t <= previous:
            return
        self.got_header = True
        self.tracker.add(pid, name, chain, t, (t - previous) * 1000.0, now)

    def stop(self):
        sock, self.sock = self.sock, None
        if sock is not None:
            sock.close()
        if self.thread:
            self.thread.join(2)
            self.thread = None

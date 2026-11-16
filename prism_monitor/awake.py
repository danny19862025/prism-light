# SPDX-License-Identifier: GPL-3.0-only
"""Keep awake: a Windows take on macOS `caffeinate`, with job-aware stop conditions.

A session holds the PC awake (optionally with the display on) until its job is done:
a fixed time, a clock time, a process exiting, downloads finishing or the CPU going
idle. When the job is done the hold is released and, if the user asked for it, an
after-action (sleep, hibernate, shut down, lock) follows a cancellable countdown
that the app shows.

Separately, "auto" apps hold the PC awake while any of them runs (for example OBS
or a backup tool), like Amphetamine's app triggers. Auto holds never run an
after-action.

The hold uses SetThreadExecutionState on the GUI thread, so Windows drops it
automatically if Prism Monitor exits or crashes.
"""
import os
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal

from . import winapi

MODES = {
    'indefinite': 'Indefinitely',
    'duration': 'For a while',
    'until_time': 'Until a time',
    'process': 'While an app runs',
    'downloads': 'Until downloads finish',
    'cpu': 'Until the CPU is idle',
}
AFTER = {'none': 'Nothing (allow sleep again)', 'sleep': 'Sleep', 'hibernate': 'Hibernate',
         'shutdown': 'Shut down', 'lock': 'Lock the PC'}

# Files browsers and download tools create while a download is in progress.
PARTIAL_SUFFIXES = ('.crdownload', '.part', '.partial', '.download', '.opdownload', '.!ut', '.!qb', '.aria2',
                    '.tmp.part', '.filepart')


def fmt_duration(seconds):
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def partial_downloads(folder):
    """Names of in-progress download files directly inside `folder` (cheap: one directory listing)."""
    try:
        with os.scandir(folder) as it:
            return [e.name for e in it if e.is_file() and e.name.casefold().endswith(PARTIAL_SUFFIXES)]
    except OSError:
        return []


class WindowsPower:
    """The real system calls; tests substitute a fake."""
    def hold(self, display):
        return winapi.set_execution_state(True, display)

    def release(self):
        return winapi.set_execution_state(False)

    def processes(self):
        return {name.casefold() for name in winapi.running_processes().values()}

    def partials(self, folder):
        return partial_downloads(folder)


@dataclass
class Session:
    mode: str
    started: float
    display: bool = False
    after: str = 'none'
    deadline: float = None            # wall-clock end for duration / until_time
    processes: tuple = ()             # exe names (casefolded) for 'process'
    threshold: float = 0.0            # bytes/s for downloads, % for cpu
    quiet: float = 120.0              # seconds below threshold that count as "done"
    folder: str = ''
    last_busy: float = 0.0
    detail: str = ''
    extra: dict = field(default_factory=dict)


class KeepAwake(QObject):
    changed = Signal()
    finished = Signal(str, str)          # message, after-action

    def __init__(self, settings, power=None, clock=time.time, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.power = power or WindowsPower()
        self.clock = clock
        self.session = None
        self.auto_app = None              # name of the running auto app holding the PC awake
        self.after = 'none'               # chosen per run of the app; never persisted (safety)
        self.net_down = None
        self.cpu_util = None
        self.held = None                  # (display,) currently asserted, or None
        self.proc_cache = (0.0, set())
        self.timer = QTimer(self, interval=1000, timeout=self.tick)
        self.timer.start()

    # -- control --------------------------------------------------------------
    def start(self, mode, minutes=None, until=None, processes=(), display=None, after=None):
        """Start a session. Returns an error message, or '' on success."""
        if mode not in MODES:
            return f'Unknown keep-awake mode {mode!r}.'
        now = self.clock()
        s = self.settings
        session = Session(mode, now, display=s['awake_display'] if display is None else bool(display),
                          after=self.after if after is None else after, last_busy=now)
        if mode == 'duration':
            minutes = float(minutes or s['awake_minutes'])
            if minutes <= 0:
                return 'Choose a duration longer than zero.'
            session.deadline = now + minutes * 60
        elif mode == 'until_time':
            if until is None or until <= now:
                return 'Choose a time in the future.'
            session.deadline = float(until)
        elif mode == 'process':
            names = tuple(n.casefold() for n in processes if n)
            if not names:
                return 'Choose an app to wait for.'
            if not set(names) & self.running(force=True):
                return f"{', '.join(processes)} is not running."
            session.processes = names
            session.extra['labels'] = tuple(processes)
        elif mode == 'downloads':
            session.threshold = s['awake_net_kbps'] * 1024
            session.quiet = s['awake_quiet_min'] * 60
            session.folder = s['awake_folder'] or winapi.downloads_folder()
        elif mode == 'cpu':
            session.threshold = s['awake_cpu_pct']
            session.quiet = s['awake_quiet_min'] * 60
        self.session = session
        self.tick()
        return ''

    def stop(self):
        if self.session is not None:
            self.session = None
            self.tick()

    def set_after(self, action):
        self.after = action if action in AFTER else 'none'
        if self.session is not None:
            self.session.after = self.after
        self.changed.emit()

    def set_display(self, on):
        self.settings['awake_display'] = bool(on)
        if self.session is not None:
            self.session.display = bool(on)
        self.tick()

    def feed(self, snapshot):
        """Latest readings from the collector (network and CPU drive two of the conditions)."""
        net = snapshot.readings.get('net_down')
        cpu = snapshot.readings.get('cpu_util')
        self.net_down = net.value if net else None
        self.cpu_util = cpu.value if cpu else None

    def running(self, force=False):
        now = time.monotonic()
        if force or now - self.proc_cache[0] > 2.0:
            try:
                self.proc_cache = (now, self.power.processes())
            except Exception:
                self.proc_cache = (now, set())
        return self.proc_cache[1]

    # -- evaluation -----------------------------------------------------------
    def evaluate(self, session, now):
        """(done, detail) for a session at time `now`."""
        mode = session.mode
        if mode == 'indefinite':
            return False, 'Until you turn it off'
        if mode in ('duration', 'until_time'):
            left = session.deadline - now
            if left <= 0:
                return True, 'Time is up'
            return False, f'{fmt_duration(left)} left'
        if mode == 'process':
            alive = [label for label, name in zip(session.extra['labels'], session.processes) if name in self.running()]
            if not alive:
                return True, f"{', '.join(session.extra['labels'])} closed"
            return False, f"While {', '.join(alive)} runs"
        if mode == 'downloads':
            partials = self.power.partials(session.folder)
            rate = self.net_down
            busy = bool(partials) or (rate is not None and rate >= session.threshold)
            if busy:
                session.last_busy = now
                from .model import fmt_rate
                parts = []
                if rate is not None:
                    parts.append(f'↓ {fmt_rate(rate)}')
                if partials:
                    parts.append(f"{len(partials)} file{'s' if len(partials) != 1 else ''} downloading")
                return False, ' · '.join(parts) or 'Downloading'
            quiet_for = now - session.last_busy
            if quiet_for >= session.quiet:
                return True, 'Downloads finished'
            return False, f'Quiet, finishing in {fmt_duration(session.quiet - quiet_for)}'
        if mode == 'cpu':
            load = self.cpu_util
            if load is None or load >= session.threshold:
                session.last_busy = now
                return False, f'CPU {load:.0f}% busy' if load is not None else 'Waiting for CPU load'
            quiet_for = now - session.last_busy
            if quiet_for >= session.quiet:
                return True, 'CPU work finished'
            return False, f'CPU {load:.0f}%, finishing in {fmt_duration(session.quiet - quiet_for)}'
        return True, ''

    def tick(self):
        now = self.clock()
        session = self.session
        if session is not None:
            done, session.detail = self.evaluate(session, now)
            if done:
                self.session = None
                message = f'{session.detail}. Keep awake ended after {fmt_duration(now - session.started)}.'
                self.apply()
                self.changed.emit()
                self.finished.emit(message, session.after)
                return
        self.auto_app = None
        if self.session is None and self.settings['awake_auto_apps']:
            running = self.running()
            for name in self.settings['awake_auto_apps']:
                if name.casefold() in running:
                    self.auto_app = name
                    break
        self.apply()
        self.changed.emit()

    def apply(self):
        want = None
        if self.session is not None:
            want = (self.session.display,)
        elif self.auto_app:
            want = (self.settings['awake_display'],)
        if want == self.held:
            return
        try:
            self.power.hold(want[0]) if want else self.power.release()
        except Exception:
            pass
        self.held = want

    def shutdown(self):
        self.timer.stop()
        self.session = None
        self.auto_app = None
        self.apply()

    # -- presentation ---------------------------------------------------------
    @property
    def active(self):
        return self.session is not None or bool(self.auto_app)

    def headline(self):
        """Short status for the widget pill and tray tooltip."""
        if self.session is not None:
            return self.session.detail or MODES[self.session.mode]
        if self.auto_app:
            return f'{self.auto_app} running'
        return 'Off'

    def describe(self):
        if self.session is not None:
            s = self.session
            text = f'Keeping awake: {MODES[s.mode].lower()} — {s.detail}'
            if s.display:
                text += '\nDisplay is kept on.'
            if s.after != 'none':
                text += f'\nWhen finished: {AFTER[s.after].lower()} (after a 60-second countdown you can cancel).'
            return text
        if self.auto_app:
            return f'Keeping awake while {self.auto_app} runs (automatic rule).'
        return 'Keep awake is off: Windows sleeps as usual.'

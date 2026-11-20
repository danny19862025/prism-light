# SPDX-License-Identifier: GPL-3.0-only
"""Linux counterparts of the Windows calls in winapi.py.

Everything here runs as the current user:
* Keep awake: the desktop's standard D-Bus inhibitors (org.freedesktop.PowerManagement
  for sleep, org.freedesktop.ScreenSaver for the display). They are tied to this
  process's D-Bus connection, so the desktop drops them if Prism Monitor exits or
  crashes, like SetThreadExecutionState on Windows. systemd-inhibit is the fallback.
* Processes: /proc.
* Power actions: logind (systemctl / loginctl) and, on KDE, its own logout dialog
  for shutdown, so apps can still ask to save work.
"""
import os
import re
import subprocess
from pathlib import Path

PROC = Path('/proc')
APP = 'Prism Monitor'
REASON = 'Keep awake is on'


def _quiet(argv, wait=True):
    try:
        if wait:
            return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=10).returncode == 0
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# -- keep awake ---------------------------------------------------------------
class _Inhibitor:
    SLEEP = ('org.freedesktop.PowerManagement', '/org/freedesktop/PowerManagement/Inhibit',
             'org.freedesktop.PowerManagement.Inhibit')
    SCREEN = ('org.freedesktop.ScreenSaver', '/ScreenSaver', 'org.freedesktop.ScreenSaver')

    def __init__(self):
        self.cookies = []        # [(service tuple, cookie)]
        self.fallback = None     # systemd-inhibit process when D-Bus is unavailable

    def _bus_inhibit(self, target):
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage
        except ImportError:
            return False
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        iface = QDBusInterface(target[0], target[1], target[2], bus)
        if not iface.isValid():
            return False
        reply = iface.call('Inhibit', APP, REASON)
        if reply.type() != QDBusMessage.ReplyMessage or not reply.arguments():
            return False
        self.cookies.append((target, int(reply.arguments()[0])))
        return True

    def hold(self, display):
        self.release()
        ok = self._bus_inhibit(self.SLEEP)
        if display:
            ok = self._bus_inhibit(self.SCREEN) and ok
        if not ok:
            what = 'sleep:idle' if display else 'sleep'
            try:
                self.fallback = subprocess.Popen(
                    ['systemd-inhibit', f'--what={what}', f'--who={APP}', f'--why={REASON}', '--mode=block',
                     'sleep', 'infinity'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
                ok = True
            except OSError:
                pass
        return ok

    def release(self):
        for (service, path, interface), cookie in self.cookies:
            # UnInhibit takes a uint32, which PySide6 cannot express (Python ints go out as int32 and
            # the call is rejected), so the release is sent with gdbus, or dbus-send as a fallback.
            if not _quiet(['gdbus', 'call', '--session', '--dest', service, '--object-path', path,
                           '--method', f'{interface}.UnInhibit', str(cookie)]):
                _quiet(['dbus-send', '--session', '--type=method_call', f'--dest={service}', path,
                        f'{interface}.UnInhibit', f'uint32:{cookie}'])
        self.cookies = []
        if self.fallback is not None:
            self.fallback.terminate()
            try:
                self.fallback.wait(2)
            except subprocess.TimeoutExpired:
                self.fallback.kill()
            self.fallback = None
        return True


_inhibitor = _Inhibitor()


def set_execution_state(system, display=False):
    return _inhibitor.hold(display) if system else _inhibitor.release()


# -- processes ---------------------------------------------------------------
def process_name(pid, proc=PROC):
    """The program name a user recognises: game.exe for Proton games, else the executable name."""
    base = Path(proc) / str(pid)
    try:
        argv0 = (base / 'cmdline').read_bytes().split(b'\0', 1)[0].decode(errors='replace')
    except OSError:
        argv0 = ''
    if ' --' in argv0:
        argv0 = argv0.split(' --', 1)[0]   # a rewritten process title: "chrome --profile-directory=…"
    name = argv0.replace('\\', '/').rsplit('/', 1)[-1].strip()
    if name and not name.startswith(('wine', 'python')) and len(name) < 120:
        return name
    try:
        comm = (base / 'comm').read_text().strip()
    except OSError:
        return name or None
    return comm or name or None


def running_processes(proc=PROC):
    out = {}
    try:
        entries = list(Path(proc).iterdir())
    except OSError:
        return out
    for entry in entries:
        if entry.name.isdigit():
            name = process_name(entry.name, proc)
            if name:
                out[int(entry.name)] = name
    return out


def _x11_window_pids():
    """PIDs owning a managed X11/XWayland window (games under Proton, Steam, many apps)."""
    try:
        listing = subprocess.run(['xprop', '-root', '_NET_CLIENT_LIST'], capture_output=True, text=True,
                                 timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    pids = set()
    for window in re.findall(r'0x[0-9a-f]+', listing.partition('#')[2]):
        try:
            text = subprocess.run(['xprop', '-id', window, '_NET_WM_PID'], capture_output=True, text=True,
                                  timeout=1).stdout
            pids.add(int(text.rsplit('=', 1)[-1]))
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return pids


def unit_app_id(unit):
    """'app-steam@d17….service' → 'steam'; 'app-com.google.Chrome-3189.scope' → 'chrome'."""
    name = re.sub(r'\.(service|scope)$', '', unit[4:]).split('@', 1)[0]
    name = re.sub(r'-\d+$', '', name)
    name = name.encode().decode('unicode_escape', 'ignore') if '\\x' in name else name
    name = name.rsplit('.', 1)[-1]
    return re.sub(r'\s*\(\d+\)$', '', name).casefold().strip()


def _unit_main(unit, members):
    """The process that is the app itself: named like the unit, else the oldest one."""
    app = unit_app_id(unit)
    named = [pid for pid, name in members if app and name.casefold().removesuffix('.exe') == app]
    if not named:
        named = [pid for pid, name in members if app and (app in name.casefold() or name.casefold() in app)]
    return min(named or [pid for pid, _ in members], default=None)


def windowed_pids(proc=PROC):
    """The apps a user thinks of as "open".

    Wayland gives no list of other apps' windows, so this combines the main process of each
    application the desktop launched (systemd puts each into its own app-… unit; children such
    as a terminal's shells share the unit and are left out) with X11/XWayland window owners.
    """
    uid = os.getuid()
    units = {}   # unit -> [(pid, name)]
    try:
        entries = [e for e in Path(proc).iterdir() if e.name.isdigit()]
    except OSError:
        entries = []
    for entry in entries:
        try:
            if entry.stat().st_uid != uid:
                continue
            unit = (entry / 'cgroup').read_text().strip().rsplit('/', 1)[-1]
        except OSError:
            continue
        if not unit.startswith('app-') or unit.startswith(('app-dbus', 'app-org.kde.kdeconnect')) \
                or 'portal' in unit or 'autostart' in unit:
            continue
        name = process_name(entry.name, proc)
        if name:
            units.setdefault(unit, []).append((int(entry.name), name))
    pids = set()
    for unit, members in units.items():
        pid = _unit_main(unit, members)
        if pid:
            pids.add(pid)
    return pids | _x11_window_pids()


def foreground_pid():
    """The focused X11/XWayland window's process (most games run there), or None."""
    try:
        active = subprocess.run(['xprop', '-root', '_NET_ACTIVE_WINDOW'], capture_output=True, text=True,
                                timeout=1).stdout
        window = active.rsplit(' ', 1)[-1].strip()
        if not window.startswith('0x') or int(window, 16) == 0:
            return None
        pid = subprocess.run(['xprop', '-id', window, '_NET_WM_PID'], capture_output=True, text=True,
                             timeout=1).stdout
        return int(pid.rsplit('=', 1)[-1])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


# -- folders and power actions -------------------------------------------------
def downloads_folder():
    try:
        path = subprocess.run(['xdg-user-dir', 'DOWNLOAD'], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        path = ''
    return path if path and os.path.isdir(path) else os.path.join(os.path.expanduser('~'), 'Downloads')


def sleep_now(hibernate=False):
    return _quiet(['systemctl', 'hibernate' if hibernate else 'suspend'])


def lock_now():
    return _quiet(['loginctl', 'lock-session']) or _quiet(['xdg-screensaver', 'lock'])


def shutdown_now():
    """A normal shutdown: on KDE through its own logout, so apps may still ask to save work."""
    if 'KDE' in os.environ.get('XDG_CURRENT_DESKTOP', '') and \
            _quiet(['qdbus6', 'org.kde.Shutdown', '/Shutdown', 'logoutAndShutdown']):
        return True
    if 'GNOME' in os.environ.get('XDG_CURRENT_DESKTOP', '') and \
            _quiet(['gnome-session-quit', '--power-off', '--no-prompt'], wait=False):
        return True
    return _quiet(['systemctl', 'poweroff'])

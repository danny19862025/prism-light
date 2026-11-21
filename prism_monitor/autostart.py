# SPDX-License-Identifier: GPL-3.0-only
"""Launch at login through the per-user Run key (no administrator rights).

On Linux: an XDG autostart entry in ~/.config/autostart, which KDE, GNOME and
other desktops start when you sign in.

The sensor helper is never started at login silently. If the user enables
"Start the sensor helper on launch", Windows shows its administrator prompt.
"""
import os
import sys
from pathlib import Path

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE = 'PrismMonitor'


def command():
    if getattr(sys, 'frozen', False):
        return f'"{Path(sys.executable).resolve()}"'
    exe = Path(sys.executable).resolve()
    pythonw = exe.with_name('pythonw.exe')
    script = Path(__file__).resolve().parent.parent / 'monitor_main.py'
    return f'"{pythonw if pythonw.exists() else exe}" "{script}"'


def linux_entry():
    base = os.environ.get('XDG_CONFIG_HOME') or os.path.join(os.path.expanduser('~'), '.config')
    return Path(base) / 'autostart' / 'prism-monitor.desktop'


def _exec_arg(text):
    """Quote one argument for a desktop entry's Exec key (double quotes, per the XDG spec)."""
    if text and not any(c in text for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return text
    return '"' + ''.join('\\' + c if c in '"`$\\' else c for c in text) + '"'


def linux_command():
    if getattr(sys, 'frozen', False):
        return _exec_arg(str(Path(sys.executable).resolve()))
    script = Path(__file__).resolve().parent.parent / 'monitor_main.py'
    return f'{_exec_arg(sys.executable)} {_exec_arg(str(script))}'


def enabled():
    if sys.platform.startswith('linux'):
        return linux_entry().is_file()
    if sys.platform != 'win32':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE)
            return True
    except OSError:
        return False


def sync(on):
    """Make the Run entry match the setting. Returns (ok, message)."""
    if sys.platform.startswith('linux'):
        entry = linux_entry()
        try:
            if on:
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.write_text('[Desktop Entry]\nType=Application\nName=Prism Monitor\n'
                                 'Comment=Hardware stats desktop widget\n'
                                 f'Exec={linux_command()}\n'
                                 'Icon=prism-monitor\nTerminal=false\nX-GNOME-Autostart-enabled=true\n',
                                 encoding='utf-8')
            elif entry.exists():
                entry.unlink()
            return True, ''
        except OSError as e:
            return False, f'Could not update launch at login: {e}'
    if sys.platform != 'win32':
        return (not on), 'Launch at login is only implemented on Windows.'
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if on:
                winreg.SetValueEx(key, VALUE, 0, winreg.REG_SZ, command())
            elif enabled():
                winreg.DeleteValue(key, VALUE)
        return True, ''
    except OSError as e:
        return False, f'Could not update launch at login: {e}'

# SPDX-License-Identifier: GPL-3.0-only
"""Launch at login through the per-user Run key (no administrator rights).

The sensor helper is never started at login silently. If the user enables
"Start the sensor helper on launch", Windows shows its administrator prompt.
"""
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


def enabled():
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

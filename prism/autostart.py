# SPDX-License-Identifier: GPL-3.0-only
"""Start Prism Light with Windows, hidden in the tray, with administrator rights.

Hardware access (PawnIO for RAM/motherboard lighting, fans and CPU
temperature) needs elevation, and a Run-key autostart cannot elevate without
a UAC prompt at every login. A per-user Task Scheduler logon task with
"highest privileges" is the standard way to do this. The task is defined in
XML because the schtasks command-line defaults would stop the app after three
days and skip it on battery power.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

TASK = 'Prism Light'
DELAY = 'PT20S'   # let USB devices and drivers settle after login


def command():
    """(program, arguments, working directory) for a background start."""
    if getattr(sys, 'frozen', False):
        exe = Path(sys.executable)
        return str(exe), '--background', str(exe.parent)
    root = Path(__file__).resolve().parent.parent
    python = Path(sys.executable)
    pythonw = python.with_name('pythonw.exe')
    program = pythonw if pythonw.exists() else python
    return str(program), f'"{root / "main.py"}" --background', str(root)


def user():
    domain, name = os.environ.get('USERDOMAIN', ''), os.environ.get('USERNAME', '')
    return f'{domain}\\{name}' if domain else name


def task_xml():
    program, arguments, directory = command()
    who = escape(user())
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Starts Prism Light in the tray at login (lighting, AIO display, fans).</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{who}</UserId><Delay>{DELAY}</Delay></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>{who}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <Enabled>true</Enabled>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(program)}</Command><Arguments>{escape(arguments)}</Arguments><WorkingDirectory>{escape(directory)}</WorkingDirectory></Exec>
  </Actions>
</Task>
'''


def run(args):
    return subprocess.run(['schtasks', *args], capture_output=True, text=True, timeout=20,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def enabled():
    if sys.platform != 'win32':
        return False
    try:
        return run(['/Query', '/TN', TASK]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def enable():
    """Create or replace the logon task. Raises RuntimeError with the reason."""
    if sys.platform != 'win32':
        raise RuntimeError('Start with Windows is available on Windows only.')
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'prism-light-task.xml'
        path.write_text(task_xml(), encoding='utf-16')
        result = run(['/Create', '/F', '/TN', TASK, '/XML', str(path)])
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        if 'denied' in detail.casefold():
            raise RuntimeError('Start with Windows needs administrator rights. Open Prism Light with Run-PrismLight.cmd and try again.')
        raise RuntimeError(f'Could not create the startup task: {detail}')


def disable():
    if sys.platform != 'win32':
        return
    result = run(['/Delete', '/F', '/TN', TASK])
    if result.returncode and enabled():
        raise RuntimeError(f'Could not remove the startup task: {(result.stderr or result.stdout).strip()}')

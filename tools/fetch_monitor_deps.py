#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Prepare Prism Monitor's sensor dependencies under vendor/ (not stored in git).

* LibreHardwareMonitorLib 0.9.6 (.NET Framework 4.7.2 build, MPL-2.0) from the
  official GitHub release, SHA-256 pinned. Only the library and its runtime
  dependencies are extracted; the LibreHardwareMonitor GUI is not used.
* Compiles monitor/sensorhost/PrismSensorHost.cs with the C# compiler that ships
  with Windows' .NET Framework 4.x into vendor/sensorhost/.
* PresentMon is fetched by tools/fetch_sensors.py (shared with Prism Light).
Nothing here installs a driver. LibreHardwareMonitor uses the PawnIO driver
only if the user has installed it.
"""
import hashlib
import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor'
HOST = VENDOR / 'sensorhost'
LHM = ('https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v0.9.6/LibreHardwareMonitor.zip',
       '086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001')
LHM_LICENSE = ('https://raw.githubusercontent.com/LibreHardwareMonitor/LibreHardwareMonitor/v0.9.6/LICENSE',
               '1f256ecad192880510e84ad60474eab7589218784b9a50bc7ceee34c2b91f1d5')
# LibreHardwareMonitorLib.dll and the assemblies it loads at runtime.
LIBRARIES = ('LibreHardwareMonitorLib.dll', 'BlackSharp.Core.dll', 'DiskInfoToolkit.dll', 'HidSharp.dll',
             'RAMSPDToolkit-NDD.dll', 'Microsoft.Bcl.AsyncInterfaces.dll', 'Microsoft.Bcl.HashCode.dll',
             'System.Buffers.dll', 'System.CodeDom.dll', 'System.Collections.Immutable.dll', 'System.Formats.Nrbf.dll',
             'System.IO.Pipelines.dll', 'System.Memory.dll', 'System.Numerics.Vectors.dll',
             'System.Reflection.Metadata.dll', 'System.Resources.Extensions.dll',
             'System.Runtime.CompilerServices.Unsafe.dll', 'System.Security.AccessControl.dll',
             'System.Security.Principal.Windows.dll', 'System.Text.Encodings.Web.dll', 'System.Text.Json.dll',
             'System.Threading.AccessControl.dll', 'System.Threading.Tasks.Extensions.dll')
CSC = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET' / 'Framework64' / 'v4.0.30319' / 'csc.exe'


def download(url, expected):
    with urlopen(Request(url, headers={'User-Agent': 'PrismMonitor-build/1'}), timeout=120) as response:
        data = response.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise SystemExit(f'Checksum mismatch for {url}: {actual}')
    return data


def fetch_lhm():
    marker = HOST / 'lhm-0.9.6.ok'
    if marker.exists():
        return
    HOST.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(download(*LHM))) as z:
        for name in LIBRARIES:
            (HOST / name).write_bytes(z.read(name))
    (HOST / 'LibreHardwareMonitor-LICENSE.txt').write_bytes(download(*LHM_LICENSE))
    marker.write_text(f'{LHM[0]}\nsha256 {LHM[1]}\n', encoding='utf-8')
    print('Fetched LibreHardwareMonitorLib 0.9.6')


def compile_host():
    source = ROOT / 'monitor' / 'sensorhost' / 'PrismSensorHost.cs'
    config = ROOT / 'monitor' / 'sensorhost' / 'PrismSensorHost.exe.config'
    target = HOST / 'PrismSensorHost.exe'
    if target.exists() and target.stat().st_mtime >= max(source.stat().st_mtime, config.stat().st_mtime):
        return
    if not CSC.is_file():
        raise SystemExit(f'.NET Framework C# compiler not found at {CSC}')
    argv = [str(CSC), '/nologo', '/optimize+', '/platform:x64', '/target:exe', f'/out:{target}',
            f'/reference:{HOST / "LibreHardwareMonitorLib.dll"}', '/reference:System.Core.dll', str(source)]
    subprocess.run(argv, check=True)
    (HOST / 'PrismSensorHost.exe.config').write_bytes(config.read_bytes())
    print(f'Compiled {target.relative_to(ROOT)}')


def main():
    if sys.platform != 'win32':
        print('Prism Monitor sensor helper is Windows-only; skipped.')
        return 0
    fetch_lhm()
    compile_host()
    return 0


if __name__ == '__main__':
    sys.exit(main())

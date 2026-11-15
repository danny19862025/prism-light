#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Fetch checksum-pinned sensor helpers into vendor/ (not stored in git).

* Intel PresentMon 2.6.0 console build (MIT) for the FPS readout.
* PawnIO.Modules 0.2.11 AMDFamily17.bin (LGPL-2.1, signed) for Ryzen Tctl.
The app degrades gracefully when these are missing; the build needs them.
"""
import hashlib
import io
import sys
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor'
PRESENTMON = ('https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/PresentMon-2.6.0-x64.exe',
              'b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af')
MODULES = ('https://github.com/namazso/PawnIO.Modules/releases/download/0.2.11/release_0_2_11.zip',
           '43608cb89bc84247fef1368a139013f7d043e17db6d6c8dfc9b46bf0905a81f4')


def download(url, expected):
    with urlopen(Request(url, headers={'User-Agent': 'PrismLight-build/1'}), timeout=90) as response:
        data = response.read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise SystemExit(f'Checksum mismatch for {url}: {actual}')
    return data


def main():
    exe = VENDOR / 'presentmon' / 'PresentMon.exe'
    if not exe.exists() or hashlib.sha256(exe.read_bytes()).hexdigest() != PRESENTMON[1]:
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(download(*PRESENTMON))
        print(f'Fetched {exe.relative_to(ROOT)}')
    module = VENDOR / 'pawnio-modules' / 'AMDFamily17.bin'
    if not module.exists():
        module.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(download(*MODULES))) as z:
            for name in ('AMDFamily17.bin', 'COPYING'):
                (module.parent / name).write_bytes(z.read(name))
        print(f'Fetched {module.relative_to(ROOT)}')
    print('Sensor helpers ready.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

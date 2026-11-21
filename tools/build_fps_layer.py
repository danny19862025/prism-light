#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Build Prism Monitor's Linux frame timing layer into vendor/vklayer/ (not stored in git).

* Vulkan-Headers 1.4.309 (Apache-2.0) from the official Khronos GitHub release,
  SHA-256 pinned, extracted to vendor/vulkan-headers/. Only the headers are used.
* Compiles monitor/vklayer/prism_fps_layer.c with the system C compiler (cc).

The app installs the built layer for the current user when FPS is shown; nothing
here installs anything outside the repository.
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor'
HEADERS = VENDOR / 'vulkan-headers'
OUTPUT = VENDOR / 'vklayer' / 'libVkLayer_prism_fps.so'
SOURCE = ROOT / 'monitor' / 'vklayer' / 'prism_fps_layer.c'
VERSION = '1.4.309'
ARCHIVE = (f'https://github.com/KhronosGroup/Vulkan-Headers/archive/refs/tags/v{VERSION}.tar.gz',
           '437925ada160d86ed763d29dcb9318c1bb0d024d7deaf77bc7c170b8eb6b6f10')


def fetch_headers():
    marker = HEADERS / '.version'
    if marker.is_file() and marker.read_text().strip() == VERSION:
        return
    url, expected = ARCHIVE
    print(f'Downloading Vulkan-Headers {VERSION}…', flush=True)
    with urlopen(Request(url, headers={'User-Agent': 'PrismMonitor-build/1'}), timeout=90) as response:
        data = response.read(50_000_000)
    if hashlib.sha256(data).hexdigest() != expected:
        raise SystemExit('Vulkan-Headers checksum mismatch; refusing to build with it.')
    if HEADERS.exists():
        shutil.rmtree(HEADERS)
    HEADERS.mkdir(parents=True)
    prefix = f'Vulkan-Headers-{VERSION}/'
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for member in archive.getmembers():
            name = member.name
            if not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            if not (rel.startswith('include/') or rel == 'LICENSE.md') or not member.isfile():
                continue
            target = HEADERS / rel
            if '..' in Path(rel).parts:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    marker.write_text(VERSION)


def build():
    if not sys.platform.startswith('linux'):
        print('The frame timing layer is only used on Linux.')
        return 0
    fetch_headers()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    compiler = os.environ.get('CC', 'cc')
    argv = [compiler, '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-fvisibility=hidden', '-std=c11',
            '-I', str(HEADERS / 'include'), '-o', str(OUTPUT), str(SOURCE), '-lpthread',
            '-Wl,--no-undefined', '-Wl,-z,relro,-z,now']
    print(' '.join(argv), flush=True)
    result = subprocess.run(argv)
    if result.returncode:
        return result.returncode
    print(f'Built {OUTPUT}')
    return 0


if __name__ == '__main__':
    raise SystemExit(build())

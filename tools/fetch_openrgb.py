#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Fetch checksum-pinned upstream binaries and matching source for the bundle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import Request,urlopen
import zipfile

VERSION='1.1'
COMMIT='8e16de27858cf2be6af54a33ce003df74cb9fd3e'
BASE='https://codeberg.org/OpenRGB/OpenRGB/releases/download/release_1.1/'
ASSETS={
 'linux':('OpenRGB_1.1_x86_64_8e16de2.AppImage',BASE+'OpenRGB_1.1_x86_64_8e16de2.AppImage','d556c2273b0ef863dbee7ffdb438304b382add5d685c93c60758b42de3b0268d'),
 'win32':('OpenRGB_1.1_Windows_64_8e16de2.zip',BASE+'OpenRGB_1.1_Windows_64_8e16de2.zip','63291726c6fc84eb479ef2ee752e97e1187f6ce9c49fd27eee40ded588a8172a'),
 'source':('OpenRGB-1.1-source.tar.gz','https://codeberg.org/OpenRGB/OpenRGB/archive/'+COMMIT+'.tar.gz','8b919ad446de7f0415e2271a1881f94cd4f14e384a3c59acfcb3f540125f0107'),
}

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def acquire(asset,cache):
    name,url,expected=asset;path=cache/name
    if path.exists() and digest(path)==expected:return path
    cache.mkdir(parents=True,exist_ok=True);temp=path.with_suffix(path.suffix+'.part')
    try:
        with urlopen(Request(url,headers={'User-Agent':'PrismLight-build/1'}),timeout=90) as response,temp.open('wb') as f:
            total=0
            while chunk:=response.read(1024*1024):
                total+=len(chunk)
                if total>400_000_000:raise ValueError('Upstream asset exceeds download limit.')
                f.write(chunk)
        if digest(temp)!=expected:raise ValueError('OpenRGB checksum mismatch; refusing to package it.')
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)
    return path

def prepare(root=None,system=None):
    root=Path(root) if root else Path(__file__).resolve().parent.parent
    system=system or sys.platform
    if system not in ('linux','win32') or platform.machine().lower() not in ('x86_64','amd64'):
        raise RuntimeError('Bundled engine builds currently require x86-64 Windows or Linux.')
    vendor=root/'vendor';cache=vendor/'cache';target=vendor/'openrgb'
    manifest=target/'manifest.json'
    if manifest.exists():
        data=json.loads(manifest.read_text())
        if data.get('platform')==system and data.get('asset_sha256')==ASSETS[system][2] and (target/data['executable']).is_file() and (target/'source'/ASSETS['source'][0]).is_file():
            return target
    archive=acquire(ASSETS[system],cache);source=acquire(ASSETS['source'],cache)
    vendor.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='openrgb-',dir=vendor) as temporary:
        stage=Path(temporary)/'package';stage.mkdir()
        if system=='linux':
            archive.chmod(archive.stat().st_mode|0o111)
            subprocess.run([str(archive.resolve()),'--appimage-extract'],cwd=stage,check=True,stdout=subprocess.DEVNULL)
            executable='squashfs-root/AppRun'
        else:
            runtime=stage/'runtime';runtime.mkdir()
            with zipfile.ZipFile(archive) as z:
                entries=z.infolist()
                if len(entries)>5000 or sum(i.file_size for i in entries)>1_000_000_000:raise ValueError('Unexpected runtime archive size.')
                for i in entries:
                    path=(runtime/i.filename).resolve()
                    if not path.is_relative_to(runtime.resolve()) or stat.S_ISLNK(i.external_attr>>16):raise ValueError('Unsafe runtime archive entry.')
                z.extractall(runtime)
            matches=list(runtime.rglob('OpenRGB.exe'))
            if len(matches)!=1:raise ValueError('Expected one OpenRGB executable.')
            executable=matches[0].relative_to(stage).as_posix()
        sources=stage/'source';sources.mkdir();shutil.copy2(source,sources/source.name)
        with tarfile.open(source) as tar:
            member=tar.extractfile('openrgb/LICENSE')
            if member is None:raise ValueError('Source license missing.')
            (stage/'OpenRGB-LICENSE.txt').write_bytes(member.read())
        data=dict(version=VERSION,commit=COMMIT,platform=system,executable=executable,
                  asset_sha256=ASSETS[system][2],asset_url=ASSETS[system][1],
                  source_sha256=ASSETS['source'][2],source_url=ASSETS['source'][1])
        (stage/'manifest.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
        (stage/'NOTICE.txt').write_text('OpenRGB '+VERSION+' is a separate GPL-licensed hardware engine, bundled with Prism Light.\nUpstream: https://codeberg.org/OpenRGB/OpenRGB\nExact source archive and upstream license accompany this runtime.\nRuntime libraries retain their upstream copyright notices.\n',encoding='utf-8')
        if target.exists():shutil.rmtree(target)
        shutil.move(str(stage),target)
    return target

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--platform',choices=('linux','win32'))
    args=parser.parse_args();print('Bundled hardware prepared:',prepare(system=args.platform))

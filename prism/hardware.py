# SPDX-License-Identifier: GPL-3.0-only
"""Own the bundled OpenRGB child process; no dependency on an installed server."""
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from .presets import default_path

def engine_directory():
    if getattr(sys,'frozen',False):return Path(sys.executable).parent/'hardware'/'openrgb'
    return Path(__file__).resolve().parent.parent/'vendor'/'openrgb'

def child_environment():
    env=dict(os.environ)
    # PyInstaller's Qt/Python libraries belong to Prism, not the hardware child.
    original=env.pop('LD_LIBRARY_PATH_ORIG',None)
    for key in ('LD_LIBRARY_PATH','LD_PRELOAD','QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH',
                'QML2_IMPORT_PATH','QML_IMPORT_PATH','PYTHONHOME','PYTHONPATH'):
        env.pop(key,None)
    if original:env['LD_LIBRARY_PATH']=original
    return env

class Hardware:
    def __init__(self,directory=None,config=None):
        self.directory=Path(directory) if directory else engine_directory()
        self.config=Path(config) if config else default_path().parent/'hardware'
        self.process=None;self.cancel=threading.Event();self.port=None;self.log=None
    @property
    def executable(self):
        manifest=self.directory/'manifest.json'
        if not manifest.exists():
            raise RuntimeError('The bundled hardware engine is missing. Build with tools/fetch_openrgb.py, or reinstall the full Prism Light package.')
        data=json.loads(manifest.read_text(encoding='utf-8'))
        exe=(self.directory/data['executable']).resolve()
        if not exe.is_relative_to(self.directory.resolve()) or not exe.is_file():
            raise RuntimeError('Invalid hardware engine package.')
        return exe
    def connect(self,backend,timeout=45):
        self.close();self.cancel.clear()
        try:
            exe=self.executable;self.config.mkdir(parents=True,exist_ok=True)
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1',0));self.port=reservation.getsockname()[1]
            self.log=(self.config/'engine.log').open('w',encoding='utf-8')
            argv=[str(exe),'--server','--server-host','127.0.0.1','--server-port',str(self.port),
                  '--noautoconnect','--config',str(self.config)]
            # Reset DLL search inheritance around spawn in frozen Windows apps.
            reset=sys.platform=='win32' and getattr(sys,'frozen',False)
            if reset:ctypes.windll.kernel32.SetDllDirectoryW(None)
            try:
                self.process=subprocess.Popen(argv,cwd=exe.parent,env=child_environment(),
                    stdout=self.log,stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            finally:
                if reset:ctypes.windll.kernel32.SetDllDirectoryW(getattr(sys,'_MEIPASS',''))
            deadline=time.monotonic()+timeout;last=None
            while not self.cancel.is_set() and time.monotonic()<deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(f'Hardware engine exited ({self.process.returncode}). Details: {self.config / "engine.log"}')
                try:
                    devices=backend.connect(self.port)
                    if self.cancel.is_set():backend.close();break
                    return devices
                except (OSError,RuntimeError) as e:
                    last=e
                except Exception:
                    # A malformed SDK response is not a readiness issue.
                    raise
                self.cancel.wait(.2)
            if self.cancel.is_set():raise RuntimeError('Hardware discovery cancelled.')
            raise RuntimeError(f'Hardware discovery timed out: {last}. Details: {self.config / "engine.log"}')
        except Exception:
            backend.close();self.close();raise
    def close(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(3)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(3)
            self.process=None
        if self.log:self.log.close();self.log=None
    def diagnostics(self):
        path=self.config/'engine.log'
        try:return path.read_text(encoding='utf-8',errors='replace')[-8000:]
        except OSError:return 'No hardware diagnostics recorded yet.'

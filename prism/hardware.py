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

class StalledScan(Exception):
    pass

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

# Programs that drive the same SMBus/USB lighting controllers. Running one
# alongside the bundled engine causes stalled scans, missing devices and
# lights that revert or flicker. Only the programs that actually drive
# devices are listed: background helpers such as SignalRgbService.exe (an
# auto-start Windows service) do not touch lighting on their own, and warning
# about them told users to quit something that was not interfering.
CONFLICTING_APPS={
    'signalrgb.exe':'SignalRGB','icue.exe':'Corsair iCUE',
    'armourycrate.service.exe':'ASUS Armoury Crate','armourycrateservice.exe':'ASUS Armoury Crate',
    'lightingservice.exe':'ASUS Aura','gcc.exe':'GIGABYTE Control Center','rgbfusion.exe':'GIGABYTE RGB Fusion',
    'mysticlight.exe':'MSI Mystic Light','msi.centralserver.exe':'MSI Center','nzxt cam.exe':'NZXT CAM',
    'openrgb.exe':'another OpenRGB',
}

def conflicting_rgb_apps(exclude_pid=None):
    """Names of running RGB controllers that compete for the same devices."""
    if sys.platform!='win32':return []
    try:
        out=subprocess.run(['tasklist','/FO','CSV','/NH'],capture_output=True,text=True,timeout=5,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout
    except (OSError,subprocess.SubprocessError):return []
    found=[]
    for line in out.splitlines():
        parts=[p.strip('"') for p in line.split('","')]
        if len(parts)<2:continue
        name=CONFLICTING_APPS.get(parts[0].casefold())
        if not name or (exclude_pid and parts[1]==str(exclude_pid)):continue
        if name not in found:found.append(name)
    return found

def conflict_advice(apps):
    if not apps:return ''
    return (f' {", ".join(apps)} is also running and controls the same lighting hardware. '
            'Quit it completely (including its tray icon and background service), then press Scan devices.')

class Hardware:
    # Seconds without new engine log output, before detection completes,
    # that count as a stalled scan (a healthy scan logs every few hundred ms).
    stall_seconds=15
    attempts=2
    def __init__(self,directory=None,config=None):
        self.directory=Path(directory) if directory else engine_directory()
        self.config=Path(config) if config else default_path().parent/'hardware'
        self.process=None;self.cancel=threading.Event();self.port=None;self.log=None;self.conflicts=[]
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
            self.conflicts=conflicting_rgb_apps()
            for attempt in range(1,self.attempts+1):
                try:return self.attempt(backend,timeout)
                except StalledScan as e:
                    if attempt==self.attempts or self.cancel.is_set():
                        raise RuntimeError(str(e)+conflict_advice(self.conflicts)) from None
                    backend.close();self.close()
        except Exception:
            backend.close();self.close();raise
    def attempt(self,backend,timeout):
        exe=self.executable;self.config.mkdir(parents=True,exist_ok=True)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1',0));self.port=reservation.getsockname()[1]
        self.log=(self.config/'engine.log').open('w',encoding='utf-8')
        argv=[str(exe),'--server','--server-host','127.0.0.1','--server-port',str(self.port),
              '--noautoconnect','--verbose','--config',str(self.config)]
        # Reset DLL search inheritance around spawn in frozen Windows apps.
        reset=sys.platform=='win32' and getattr(sys,'frozen',False)
        if reset:ctypes.windll.kernel32.SetDllDirectoryW(None)
        try:
            self.spawned_at=time.time()
            self.process=subprocess.Popen(argv,cwd=exe.parent,env=child_environment(),
                stdout=self.log,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        finally:
            if reset:ctypes.windll.kernel32.SetDllDirectoryW(getattr(sys,'_MEIPASS',''))
        deadline=time.monotonic()+timeout
        # The SDK listener starts before detection finishes. Connecting then
        # can return zero controllers or interleave device-change packets
        # with negotiation. Wait for this owned engine's flushed log marker.
        ready=False;last='waiting for the hardware scan to finish'
        seen=-1;quiet_since=time.monotonic()
        while not self.cancel.is_set() and time.monotonic()<deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f'Hardware engine exited ({self.process.returncode}). Details: {self.config / "engine.log"}'+conflict_advice(self.conflicts))
            if not ready:
                log=self.diagnostics();ready='Detection completed' in log
                if not ready:
                    now=time.monotonic()
                    if len(log)!=seen:seen=len(log);quiet_since=now
                    elif now-quiet_since>self.stall_seconds:
                        step=next((l for l in reversed(log.splitlines()) if l.strip()),'engine start')
                        raise StalledScan(f'Hardware scan stalled at: {step.strip()[:160]}.')
                    self.cancel.wait(.1)
                    continue
            try:
                devices=backend.connect(self.port)
                if self.cancel.is_set():backend.close();break
                return devices
            except (OSError,RuntimeError) as e:
                last=e
            self.cancel.wait(.2)
        if self.cancel.is_set():raise RuntimeError('Hardware discovery cancelled.')
        raise RuntimeError(f'Hardware discovery timed out: {last}. Details: {self.config / "engine.log"}'+conflict_advice(self.conflicts))
    def close(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(3)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(3)
            self.process=None
        if self.log:self.log.close();self.log=None
    def diagnostics(self):
        # OpenRGB writes its own flushed log under <config>/logs. Its stdout
        # (engine.log) can stay empty because a GUI-subsystem process buffers
        # redirected output, so prefer the newest log from this engine run.
        texts=[]
        try:
            since=getattr(self,'spawned_at',0)-2
            logs=[p for p in (self.config/'logs').glob('OpenRGB_*.log') if p.stat().st_mtime>=since]
            if logs:texts.append(max(logs,key=lambda p:p.stat().st_mtime).read_text(encoding='utf-8',errors='replace'))
        except OSError:pass
        try:texts.append((self.config/'engine.log').read_text(encoding='utf-8',errors='replace'))
        except OSError:pass
        text=max(texts,key=len,default='')
        return text[-8000:] if text else 'No hardware diagnostics recorded yet.'

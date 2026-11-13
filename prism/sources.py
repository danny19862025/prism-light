# SPDX-License-Identifier: GPL-3.0-only
"""Explicit audio capture, platform metrics and opt-in local lighting events."""
import array
import ctypes
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading
import time

class Metrics:
    def __init__(self): self.previous=None
    def sample(self):
        result={'cpu':0,'memory':0,'temperatures':[],'fans':[]}
        try:
            if sys.platform.startswith('linux'):
                fields=list(map(int,Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
                idle=fields[3]+fields[4];total=sum(fields)
                mem={line.split(':')[0]:int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()}
                result['memory']=100*(1-mem['MemAvailable']/mem['MemTotal'])
                for hw in Path('/sys/class/hwmon').glob('hwmon*'):
                    name=(hw/'name').read_text().strip() if (hw/'name').exists() else hw.name
                    for p in hw.glob('temp*_input'):
                        try: result['temperatures'].append((name,float(p.read_text())/1000))
                        except (OSError,ValueError): pass
                    for p in hw.glob('fan*_input'):
                        try: result['fans'].append((name,int(p.read_text())))
                        except (OSError,ValueError): pass
            elif sys.platform=='win32':
                idle_v,kernel,user=ctypes.c_ulonglong(),ctypes.c_ulonglong(),ctypes.c_ulonglong()
                if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle_v),ctypes.byref(kernel),ctypes.byref(user)):
                    raise OSError('Windows CPU metrics unavailable')
                idle,total=idle_v.value,kernel.value+user.value
                class Memory(ctypes.Structure):
                    _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ('physical','available','page','available_page','virtual','available_virtual','extended')]
                mem=Memory();mem.length=ctypes.sizeof(mem)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):result['memory']=mem.load
            else: return result
            if self.previous:
                old_total,old_idle=self.previous;delta=total-old_total
                if delta>0:result['cpu']=min(100,max(0,100*(1-(idle-old_idle)/delta)))
            self.previous=total,idle
        except (OSError,ValueError,KeyError) as e:result['error']=str(e)
        return result

def process_names():
    if sys.platform.startswith('linux'):
        names=set()
        for p in Path('/proc').glob('[0-9]*/comm'):
            try:names.add(p.read_text().strip().casefold())
            except OSError:pass
        return names
    if sys.platform=='win32':
        import csv,io
        flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
        data=subprocess.run(['tasklist','/FO','CSV','/NH'],capture_output=True,text=True,timeout=3,creationflags=flags,check=True)
        return {row[0].casefold() for row in csv.reader(io.StringIO(data.stdout)) if row}
    return set()

class Events:
    def __init__(self):self.server=None;self.events=queue.Queue(maxsize=64);self.token=secrets.token_urlsafe(24)
    def start(self,port=0):
        if self.server:return self.server.server_port
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                if self.path!='/event':self.send_error(404);return
                if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+owner.token):self.send_error(401);return
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=4096:raise ValueError('Invalid request size')
                    event=json.loads(self.rfile.read(size))
                    from .effects import color
                    color(event['color'])
                    owner.events.put_nowait({'color':event['color']})
                except (ValueError,KeyError,TypeError,queue.Full):self.send_error(400);return
                self.send_response(204);self.end_headers()
        class Server(ThreadingHTTPServer):
            daemon_threads=True
            def get_request(self):
                conn,address=super().get_request();conn.settimeout(2);return conn,address
        self.server=Server(('127.0.0.1',port),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        return self.server.server_port
    def close(self):
        if self.server:self.server.shutdown();self.server.server_close();self.thread.join(2);self.server=None

class Audio:
    """Qt microphone input, opt-in; short buffers and eight frequency probes."""
    def __init__(self):self.source=None;self.level=0;self.bands=[0]*8;self.error='Audio input is off.'
    def start(self,device=None):
        from PySide6.QtMultimedia import QAudioFormat,QAudioSource,QMediaDevices
        self.stop()
        device=device or QMediaDevices.defaultAudioInput()
        if device.isNull():raise ValueError('No audio input. Choose a microphone or system-monitor input.')
        fmt=device.preferredFormat();self.format=fmt
        self.source=QAudioSource(device,fmt);self.source.setBufferSize(max(4096,fmt.bytesPerFrame()*2048))
        self.stream=self.source.start()
        if self.stream is None:self.stop();raise ValueError('Audio input could not start.')
        self.stream.readyRead.connect(self.read)
        self.error='Listening to '+device.description()
    def read(self):
        from PySide6.QtMultimedia import QAudioFormat
        raw=bytes(self.stream.readAll())
        now=time.monotonic()
        if now-getattr(self,'last_analysis',0)<.03:return
        self.last_analysis=now
        fmt=self.format;kind=fmt.sampleFormat()
        sizes={QAudioFormat.Int16:('h',32768),QAudioFormat.Int32:('i',2147483648),QAudioFormat.Float:('f',1),QAudioFormat.UInt8:('B',128)}
        if kind not in sizes:return
        code,scale=sizes[kind];width=array.array(code).itemsize
        values=array.array(code);values.frombytes(raw[:len(raw)//width*width])
        if sys.byteorder!='little' and width>1:values.byteswap()
        offset=128 if code=='B' else 0
        samples=[(v-offset)/scale for v in values[::max(1,fmt.channelCount())]][-512:]
        if not samples:return
        rms=math.sqrt(sum(v*v for v in samples)/len(samples));self.level=max(rms,self.level*.7)
        n=len(samples);rate=fmt.sampleRate()
        window=[v*(.5-.5*math.cos(math.tau*i/max(1,n-1))) for i,v in enumerate(samples)]
        for j,freq in enumerate((60,120,250,500,1000,2000,4000,8000)):
            re=sum(v*math.cos(math.tau*freq*i/rate) for i,v in enumerate(window))
            im=sum(v*math.sin(math.tau*freq*i/rate) for i,v in enumerate(window))
            self.bands[j]=min(1,math.hypot(re,im)*4/n)
        self.last_sample=time.monotonic()
    def stop(self):
        if self.source:self.source.stop();self.source.deleteLater()
        self.source=None;self.level=0;self.bands=[0]*8


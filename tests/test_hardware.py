# SPDX-License-Identifier: GPL-3.0-only
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from prism.backend import OpenRGB
from prism.hardware import Hardware,child_environment

FAKE='''
import socket,struct,sys,time
port=int(sys.argv[sys.argv.index('--server-port')+1])
listener=socket.socket();listener.bind(('127.0.0.1',port));listener.listen()
# Match OpenRGB: listener exists before enumeration has completed.
time.sleep(.3)
print('[Info] Detection completed',flush=True)
while True:
 conn,_=listener.accept()
 with conn:
  def read(n):
   result=b''
   while len(result)<n:
    part=conn.recv(n-len(result))
    if not part:raise EOFError
    result+=part
   return result
  try:
   while True:
    magic,device,command,size=struct.unpack('<4sIII',read(16));read(size)
    if command==50:continue
    data=struct.pack('<I',3 if command==40 else 0)
    conn.sendall(struct.pack('<4sIII',b'ORGB',device,command,len(data))+data)
  except (EOFError,ConnectionResetError,BrokenPipeError):pass
'''

class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'fake-engine').write_text('placeholder')
        (self.root/'server.py').write_text(FAKE)
        (self.root/'manifest.json').write_text(json.dumps({'executable':'fake-engine'}))
        self.hardware=Hardware(self.root,self.root/'config');self.backend=OpenRGB()
        quiet=patch('prism.hardware.conflicting_rgb_apps',return_value=[]);quiet.start();self.addCleanup(quiet.stop)
    def tearDown(self):self.backend.close();self.hardware.close();self.temp.cleanup()
    def spawn(self,argv,**kwargs):
        return self.original([sys.executable,str(self.root/'server.py'),'--server-port',argv[argv.index('--server-port')+1]],**kwargs)
    def test_owned_engine_negotiation_and_cleanup(self):
        self.original=subprocess.Popen
        with patch('prism.hardware.subprocess.Popen',side_effect=self.spawn):
            devices=self.hardware.connect(self.backend,timeout=5)
        self.assertEqual(devices,[]);self.assertNotEqual(self.hardware.port,6742)
        process=self.hardware.process;self.assertIsNone(process.poll())
        self.backend.health();self.backend.close();self.hardware.close()
        self.assertIsNotNone(process.poll());self.assertIsNone(self.hardware.process)
        self.hardware.close()
    def test_stalled_scan_retries_once_then_names_conflicting_app(self):
        stalled=self.root/'stalled.py';stalled.write_text("import time;print('Start PawnIO: SmbusPIIX4.bin',flush=True);time.sleep(30)")
        self.original=subprocess.Popen;spawned=[]
        def spawn(argv,**kwargs):
            spawned.append(1);return self.original([getattr(sys,'_base_executable',sys.executable),str(stalled)],**kwargs)
        self.hardware.stall_seconds=.5
        with patch('prism.hardware.subprocess.Popen',side_effect=spawn),             patch('prism.hardware.conflicting_rgb_apps',return_value=['SignalRGB']):
            with self.assertRaisesRegex(RuntimeError,'stalled at: Start PawnIO.*SignalRGB is also running'):
                self.hardware.connect(self.backend,timeout=10)
        self.assertEqual(len(spawned),2);self.assertIsNone(self.hardware.process)
    def test_conflicting_apps_are_recognised_from_tasklist(self):
        from prism import hardware
        patch.stopall()
        listing='"SignalRgb.exe","17804","Console","1","507,992 K"\n"SignalRgbService.exe","5252","Services","0","20 K"\n"OpenRGB.exe","99","Console","1","1 K"\n"explorer.exe","1","Console","1","1 K"\n'
        result=subprocess.CompletedProcess([],0,stdout=listing)
        with patch.object(hardware.sys,'platform','win32'),patch('prism.hardware.subprocess.run',return_value=result):
            self.assertEqual(hardware.conflicting_rgb_apps(),['SignalRGB','another OpenRGB'])
            self.assertEqual(hardware.conflicting_rgb_apps(exclude_pid=99),['SignalRGB'])
    def test_missing_and_escaping_engine_fail_without_spawn(self):
        for executable in ('missing.exe','../outside.exe'):
            (self.root/'manifest.json').write_text(json.dumps({'executable':executable}))
            with patch('prism.hardware.subprocess.Popen') as spawn:
                with self.assertRaises(RuntimeError):self.hardware.connect(self.backend)
                spawn.assert_not_called()
        self.assertIsNone(self.backend.sock)
    def test_sdk_is_not_queried_before_detection_completes(self):
        self.original=subprocess.Popen
        original_connect=self.backend.connect
        def connect(port):
            self.assertIn('Detection completed',self.hardware.diagnostics())
            return original_connect(port)
        with patch('prism.hardware.subprocess.Popen',side_effect=self.spawn),patch.object(self.backend,'connect',side_effect=connect):
            self.assertEqual(self.hardware.connect(self.backend,timeout=5),[])
    def test_unfinished_detection_times_out_without_querying_sdk(self):
        (self.root/'server.py').write_text(FAKE.replace("print('[Info] Detection completed',flush=True)", 'time.sleep(10)'))
        self.original=subprocess.Popen
        with patch('prism.hardware.subprocess.Popen',side_effect=self.spawn),patch.object(self.backend,'connect') as connect:
            with self.assertRaisesRegex(RuntimeError,'timed out'):
                self.hardware.connect(self.backend,timeout=.5)
            connect.assert_not_called()
        self.assertIsNone(self.hardware.process)
    def test_cancel_discovery_cleans_up_process(self):
        self.original=subprocess.Popen
        with patch('prism.hardware.subprocess.Popen',side_effect=self.spawn),patch.object(self.backend,'connect',side_effect=ConnectionRefusedError):
            timer=threading.Timer(.15,self.hardware.cancel.set);timer.start()
            try:
                with self.assertRaisesRegex(RuntimeError,'cancelled'):self.hardware.connect(self.backend,timeout=5)
            finally:timer.join()
        self.assertIsNone(self.hardware.process)
    def test_frozen_environment_is_not_inherited(self):
        with patch.dict(os.environ,{'LD_LIBRARY_PATH':'/frozen','LD_LIBRARY_PATH_ORIG':'/original','QT_PLUGIN_PATH':'/frozen/qt','PYTHONHOME':'/frozen/python'}):
            env=child_environment()
            self.assertEqual(env['LD_LIBRARY_PATH'],'/original')
            self.assertNotIn('QT_PLUGIN_PATH',env);self.assertNotIn('PYTHONHOME',env)
            self.assertEqual(os.environ['QT_PLUGIN_PATH'],'/frozen/qt')

if __name__=='__main__':unittest.main()

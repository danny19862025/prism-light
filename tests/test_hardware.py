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
import socket,struct,sys
port=int(sys.argv[sys.argv.index('--server-port')+1])
listener=socket.socket();listener.bind(('127.0.0.1',port));listener.listen()
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
    def test_missing_and_escaping_engine_fail_without_spawn(self):
        for executable in ('missing.exe','../outside.exe'):
            (self.root/'manifest.json').write_text(json.dumps({'executable':executable}))
            with patch('prism.hardware.subprocess.Popen') as spawn:
                with self.assertRaises(RuntimeError):self.hardware.connect(self.backend)
                spawn.assert_not_called()
        self.assertIsNone(self.backend.sock)
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

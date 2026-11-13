# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Prism Light contributors
import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest
from prism.backend import OpenRGB, Demo, SDKError, parse_device, rgb_bytes
from prism.presets import Presets

def string(s):
    b=s.encode()+b'\0'
    return struct.pack('<H',len(b))+b

def fixture(direct=True):
    body=struct.pack('<I',5)+b''.join(string(s) for s in ('Fixture keyboard','Test vendor','Description','1','serial-A','USB-1'))
    body+=struct.pack('<HI',1,0)
    body+=string('Direct' if direct else 'Rainbow')+struct.pack('<12I',0,32 if direct else 0,0,0,0,100,0,0,0,100,0,1)+struct.pack('<H',0)
    body+=struct.pack('<H',1)+string('Main keys')+struct.pack('<4IH',1,1,2,2,0)
    body+=struct.pack('<H',2)+string('A')+struct.pack('<I',0)+string('B')+struct.pack('<I',1)
    body+=struct.pack('<H',2)+bytes(8)
    return struct.pack('<I',len(body)+4)+body

class Server:
    def __init__(self, action='normal', direct=True):
        self.action,self.direct=action,direct
        self.commands=[]
        self.errors=[]
        self.listener=socket.socket()
        self.listener.bind(('127.0.0.1',0))
        self.port=self.listener.getsockname()[1]
        self.listener.listen(1)
        self.thread=threading.Thread(target=self.run,daemon=True)
        self.thread.start()
    def run(self):
        try:
            with self.listener:
                conn,_=self.listener.accept()
                with conn:
                    conn.settimeout(4)
                    def exact(n):
                        data=b''
                        while len(data)<n:
                            part=conn.recv(n-len(data))
                            if not part:
                                raise EOFError
                            data+=part
                        return data
                    while True:
                        magic,dev,cmd,size=struct.unpack('<4sIII',exact(16))
                        payload=exact(size)
                        self.commands.append((dev,cmd,payload))
                        if self.action=='drop':
                            return
                        if self.action=='timeout':
                            try: conn.recv(1)
                            except socket.timeout: pass
                            return
                        if cmd==40:
                            response=struct.pack('<I',2 if self.action=='old' else 6)
                        elif cmd==0:
                            if self.action=='changed' and sum(c[1]==0 for c in self.commands)>1:
                                conn.sendall(struct.pack('<4sIII',b'ORGB',0,100,0))
                            response=struct.pack('<I',1)
                        elif cmd==1:
                            response=fixture(self.direct)
                        elif cmd in (50,1101,1050):
                            continue
                        else:
                            raise AssertionError(cmd)
                        wire=struct.pack('<4sIII',b'BAD!' if self.action=='bad' else b'ORGB',dev,cmd,len(response))+response
                        # Deliberately fragmented TCP framing.
                        conn.sendall(wire[:7])
                        conn.sendall(wire[7:])
        except (EOFError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            self.errors.append(e)
    def finish(self):
        self.thread.join(5)
        if self.errors:
            raise self.errors[0]

class ProtocolTests(unittest.TestCase):
    def test_roundtrip_and_rgb_packet(self):
        server=Server()
        client=OpenRGB()
        try:
            devices=client.connect(server.port)
            self.assertEqual(devices[0].zones,[('Main keys',2)])
            self.assertTrue(devices[0].supported)
            self.assertIn('sent',client.apply([devices[0].key],'#FF8000',50))
        finally:
            client.close(); server.finish()
        led=next(c[2] for c in server.commands if c[1]==1050)
        self.assertEqual(led,struct.pack('<IH',14,2)+bytes([128,64,0,0])*2)
        mode=next(c[2] for c in server.commands if c[1]==1101)
        self.assertEqual(struct.unpack_from('<I',mode)[0],len(mode))
        self.assertEqual(struct.unpack_from('<I',mode,4)[0],0)
        self.assertEqual(server.commands[0][2],struct.pack('<I',3))
    def test_unsupported_device_sends_no_write(self):
        server=Server(direct=False); client=OpenRGB()
        try:
            d=client.connect(server.port)[0]
            self.assertFalse(d.supported)
            self.assertIn('skipped',client.apply([d.key],'#FFFFFF',100))
        finally:
            client.close();server.finish()
        self.assertFalse(any(c[1] in (1050,1101) for c in server.commands))
    def test_failed_connection(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        client=OpenRGB()
        with self.assertRaises(OSError):client.connect(port)
        self.assertIsNone(client.sock)
    def test_drop_malformed_old_and_timeout(self):
        for action in ('drop','bad','old','timeout'):
            with self.subTest(action=action):
                server=Server(action);client=OpenRGB()
                try:
                    with self.assertRaises((SDKError,OSError)):
                        client.connect(server.port)
                    self.assertIsNone(client.sock)
                finally:
                    client.close();server.finish()
    def test_topology_change_blocks_writes(self):
        server=Server('changed');client=OpenRGB()
        try:
            d=client.connect(server.port)[0]
            with self.assertRaisesRegex(SDKError,'list changed'):
                client.apply([d.key],'#FFFFFF',100)
        finally:
            client.close();server.finish()
        self.assertFalse(any(c[1]==1050 for c in server.commands))
    def test_truncated_device(self):
        with self.assertRaises(SDKError):parse_device(0,fixture()[:-1])
    def test_color_validation_and_dimming(self):
        self.assertEqual(rgb_bytes('#FF8040',0),bytes(4))
        self.assertEqual(rgb_bytes('#FF8040',100),bytes([255,128,64,0]))
        for color,level in [('#oops',100),('#GGGGGG',100),('#FFFFFF',101),('#FFFFFF',True)]:
            with self.assertRaises(ValueError):rgb_bytes(color,level)
    def test_demo_does_not_connect_socket(self):
        from unittest.mock import patch
        with patch('socket.create_connection',side_effect=AssertionError('network used')):
            demo=Demo();devices=demo.connect()
            self.assertIn('DEMO',demo.apply([d.key for d in devices],'#FFFFFF',80))
            self.assertFalse(devices[-1].supported)

class PresetTests(unittest.TestCase):
    def test_persistence_overwrite_and_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'presets.json'
            Presets(path).save('Evening','#AABBCC',65,['device-a'],False)
            p=Presets(path).read()['Evening']
            self.assertEqual(p,dict(color='#AABBCC',brightness=65,devices=['device-a'],scope='hardware'))
            Presets(path).save('Evening','#FFFFFF',0,['demo-a'],True)
            self.assertEqual(Presets(path).read()['Evening']['scope'],'demo')
            self.assertEqual(len(list(Path(folder).iterdir())),1)
    def test_corruption_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'presets.json';path.write_text('{broken')
            with self.assertRaisesRegex(ValueError,'File preserved'):
                Presets(path).save('New','#FFFFFF',100,[],True)
            self.assertEqual(path.read_text(),'{broken')
    def test_invalid_preset_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Presets(Path(folder)/'p.json')
            with self.assertRaises(ValueError):store.save(' ','#FFFFFF',100,[],True)
            with self.assertRaises(ValueError):store.save('A','#FFFFFF',-1,[],True)
            self.assertFalse(store.path.exists())

if __name__=='__main__':unittest.main()

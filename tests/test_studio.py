# SPDX-License-Identifier: GPL-3.0-only
import copy
import http.client
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
from prism.backend import OpenRGB,SDKError
from prism.effects import EFFECTS,render,points
from prism.scenes import SceneStore,validate
from prism.catalog import import_catalog
from prism.sources import Events
from test_core import Server

def scene():
    return dict(effect='Rainbow wave',color='#B6A7FF',secondary='#45E0C2',brightness=80,
                speed=50,angle=0,gain=4,devices=['key'],scope='demo',layouts={},paint={})

class FrameTests(unittest.TestCase):
    def test_independent_pixels_and_prepare_once(self):
        server=Server();client=OpenRGB()
        try:
            d=client.connect(server.port)[0]
            self.assertEqual(client.apply_frame({d.key:[(255,0,0),(0,255,64)]}),1)
            client.apply_frame({d.key:[(0,0,255),(15,20,25)]})
        finally:client.close();server.finish()
        modes=[c for c in server.commands if c[1]==1101]
        frames=[c[2] for c in server.commands if c[1]==1050]
        self.assertEqual(len(modes),1)
        self.assertEqual(frames[0],struct.pack('<IH',14,2)+bytes([255,0,0,0,0,255,64,0]))
        self.assertEqual(frames[1][-8:],bytes([0,0,255,0,15,20,25,0]))
    def test_invalid_frame_and_topology_do_not_write(self):
        for action in ('normal','changed'):
            with self.subTest(action=action):
                server=Server(action);client=OpenRGB()
                try:
                    d=client.connect(server.port)[0]
                    with self.assertRaises(ValueError):client.apply_frame({d.key:[(255,0,0)]})
                    with self.assertRaises(ValueError):client.apply_frame({d.key:[(256,0,0),(0,0,0)]})
                    with self.assertRaises(SDKError):client.apply_frame({'missing':[(0,0,0)]})
                    if action=='changed':
                        with self.assertRaises(SDKError):client.apply_frame({d.key:[(0,0,0)]*2})
                finally:client.close();server.finish()
                self.assertFalse(any(c[1] in (1050,1101) for c in server.commands))

class EngineTests(unittest.TestCase):
    def test_all_effects_bounds_blackout_and_inputs(self):
        coords=points(99,dict(x=.1,y=.1,width=.7,height=.2,columns=18,rotation=35))
        self.assertEqual(len(coords),99)
        for effect in EFFECTS:
            for t in (0,1,10000):
                rgb=render(effect,coords,t,scene(),dict(audio=.9,cpu=75,bands=[.2]*8,screen=[[(1,2,3)]],event_time=t,event_color='#FF0000'))
                self.assertEqual(len(rgb),99)
                self.assertTrue(all(type(c) is int and 0<=c<=255 for p in rgb for c in p),effect)
                self.assertEqual(render(effect,coords,t,dict(scene(),brightness=0)),[(0,0,0)]*99)
        self.assertEqual(render('Screen ambience',[(0,0)],0,dict(scene(),brightness=100),{'screen':[[(12,34,56)]]}),[(12,34,56)])
        self.assertEqual(render('Audio pulse',[(0,0)],0,scene()),[(0,0,0)])
    def test_scene_roundtrip_corrupt_preserved_and_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            store=SceneStore(Path(folder)/'studio.json');store.save('Desk',scene())
            self.assertEqual(SceneStore(store.path).read()['scenes']['Desk'],scene())
            bad=dict(scene(),speed=float('nan'))
            with self.assertRaises(ValueError):store.save('Broken',bad)
            self.assertNotIn('Broken',store.read()['scenes'])
            store.path.write_text('{bad')
            with self.assertRaisesRegex(ValueError,'preserved'):store.save('No',scene())
            self.assertEqual(store.path.read_text(),'{bad')
    def test_catalog_treats_code_as_inert(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'plugins.zip'
            with zipfile.ZipFile(path,'w') as z:
                z.writestr('lib/Plugins/Example.js','export function Name(){return "Example";}\nexport function VendorId(){return 0x1234;}\nthrow new Error("DO NOT EXECUTE");')
            rows=import_catalog(path)
            self.assertEqual(rows[0]['name'],'Example');self.assertEqual(rows[0]['vendor'],'0x1234')
    def test_local_events_require_token(self):
        events=Events();port=events.start()
        try:
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
            conn.request('POST','/event',json.dumps({'color':'#FF0000'}),{'Content-Type':'application/json'})
            response=conn.getresponse();self.assertEqual(response.status,401);response.read();conn.close()
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
            conn.request('POST','/event',json.dumps({'color':'#FF0000'}),{'Authorization':'Bearer '+events.token})
            response=conn.getresponse();self.assertEqual(response.status,204);response.read();conn.close()
            self.assertEqual(events.events.get(timeout=1),{'color':'#FF0000'})
        finally:events.close()

class StudioUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        from prism.studio import Window
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.w=Window(preset_path=root/'presets.json',scene_path=root/'studio.json')
        self.w.show();self.w.enter_demo();self.app.processEvents()
    def tearDown(self):self.w.close();self.app.processEvents();self.temp.cleanup()
    def spin(self,duration=.3):
        until=time.monotonic()+duration
        while time.monotonic()<until:self.app.processEvents();time.sleep(.005)
    def test_effect_stream_scene_layout_paint_stop(self):
        w=self.w;w.effect.setCurrentText('Rainbow wave');w.start_effects();self.spin()
        self.assertGreater(w.sent_frames,0);self.assertTrue(w.backend.last_frame)
        frame=next(iter(w.backend.last_frame.values()));self.assertGreater(len(set(frame)),1)
        key=w.devices[0].key;w.stop_effects();self.spin()
        w.paint[key]={'0':'#FF0000'};w.layouts[key]['rotation']=90
        saved=copy.deepcopy(w.scene());w.scene_store.save('Desk',saved)
        w.effect.setCurrentText('Solid');w.apply_scene(saved)
        self.assertEqual(w.effect.currentText(),'Rainbow wave');self.assertEqual(w.layouts[key]['rotation'],90)
        self.spin();self.assertEqual(w.preview_frames[key][0],(204,0,0))
        with self.assertRaisesRegex(ValueError,'scope'):w.apply_scene(dict(saved,scope='hardware'))
        w.blackout();self.spin()
        self.assertTrue(all(p==(0,0,0) for pixels in w.backend.last_frame.values() for p in pixels))
    def test_automation_edge_and_scope(self):
        w=self.w;w.scene_store.save('Auto',w.scene());w.rules=[{'kind':'Application','value':'example.exe','scene':'Auto'}]
        w.names={'example.exe'};w.auto_enabled=True;w.evaluate_rules()
        self.assertTrue(w.streaming);self.assertEqual(w.active_rules,{0})
        w.stop_effects();w.evaluate_rules();self.assertFalse(w.streaming)
        w.names=set();w.evaluate_rules();w.names={'example.exe'};w.evaluate_rules();self.assertTrue(w.streaming)
    def test_all_tabs_and_reference_archive(self):
        self.assertEqual(self.w.tabs.count(),7)
        for i in range(self.w.tabs.count()):self.w.tabs.setCurrentIndex(i);self.app.processEvents()
    def test_gallery_navigation_filter_and_playback(self):
        from prism.shell import LOOKS
        w=self.w;shell=w.shell
        self.assertEqual(len(shell.cards),17)
        shell.set_category('Reactive')
        self.assertEqual(sum(not card.isHidden() for _,card in shell.cards),5)
        shell.search.setText('Screen Flow')
        self.assertEqual(sum(not card.isHidden() for _,card in shell.cards),1)
        shell.choose(LOOKS[2]);self.assertEqual(w.effect.currentText(),'Gradient')
        self.assertEqual(shell.stack.currentIndex(),1);self.assertEqual(w.tabs.currentIndex(),0)
        shell.toggle();self.spin();self.assertTrue(w.streaming)
        shell.toggle();self.assertFalse(w.streaming)
        shell.volume.setValue(35);self.assertEqual(w.brightness.value(),35)
        shell.navigate(None,'Discover');self.assertEqual(shell.stack.currentIndex(),0)
    def test_builtin_hardware_discovery_uses_managed_engine(self):
        from prism.backend import Demo
        w=self.w
        def discover(backend):
            backend.devices=Demo().devices
            return backend.devices
        with patch.object(w.hardware,'connect',side_effect=discover) as connect:
            w.connect_live();self.spin(.3)
            connect.assert_called_once()
            self.assertTrue(w.connected);self.assertFalse(w.backend.demo)
            self.assertEqual(w.connect_btn.text(),'Scan devices')
            self.assertTrue(w.port.isHidden());self.assertIn('Hardware ready',w.status.text())
    def test_blackout_waits_for_inflight_frame(self):
        import threading
        w=self.w;gate=threading.Event();original=w.backend.apply_frame
        def slow(frames):gate.wait(2);return original(frames)
        with patch.object(w.backend,'apply_frame',side_effect=slow):
            w.start_effects();w.tick()
            self.assertIsNotNone(w.future)
            w.blackout();self.assertTrue(w.pending_blackout)
            gate.set();self.spin(.4)
            self.assertFalse(w.pending_blackout);self.assertFalse(w.streaming)
            self.assertTrue(all(p==(0,0,0) for pixels in w.backend.last_frame.values() for p in pixels))

if __name__=='__main__':unittest.main()

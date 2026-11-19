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
from prism.effects import EFFECTS,NEW_EFFECTS,render,points
from prism.scenes import SceneStore,validate
from prism.catalog import import_catalog
from prism.sources import Events
from PySide6.QtWidgets import QSpinBox,QPushButton
from test_core import Server
from qtsafe import QtTestCase

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
        for action in ('normal','grown'):
            with self.subTest(action=action):
                server=Server(action);client=OpenRGB()
                try:
                    d=client.connect(server.port)[0]
                    with self.assertRaises(ValueError):client.apply_frame({d.key:[(255,0,0)]})
                    with self.assertRaises(ValueError):client.apply_frame({d.key:[(256,0,0),(0,0,0)]})
                    with self.assertRaises(SDKError):client.apply_frame({'missing':[(0,0,0)]})
                    if action=='grown':
                        with self.assertRaises(SDKError):client.apply_frame({d.key:[(0,0,0)]*2})
                finally:client.close();server.finish()
                self.assertFalse(any(c[1] in (1050,1101) for c in server.commands))

class EngineTests(unittest.TestCase):
    def test_new_effects_move_and_share_desk_coordinates(self):
        coords=[(x/15,y/7) for y in range(8) for x in range(16)]
        inputs={'audio':.2,'bands':[.1,.2,.08,.18,.05,.14,.22,.04]}
        for effect in NEW_EFFECTS:
            with self.subTest(effect=effect):
                settings=dict(scene(),scale=140,density=75)
                frames=[render(effect,coords,t,settings,inputs) for t in (.2,1.1,2.8)]
                self.assertTrue(any(any(pixel) for frame in frames for pixel in frame))
                self.assertGreater(len(set(frames[1])),1)
                if effect!='Audio bars':self.assertTrue(any(frame!=frames[0] for frame in frames[1:]))
                full=frames[1]
                left=render(effect,coords[:64],1.1,settings,inputs)
                right=render(effect,coords[64:],1.1,settings,inputs)
                self.assertEqual(full,left+right,'Pattern must not restart per device')
        for effect in ('Audio bars','Audio rings'):
            self.assertEqual(render(effect,coords,1,scene()),[(0,0,0)]*len(coords))
    def test_expanded_scene_controls_remain_backward_compatible(self):
        self.assertEqual(validate(scene())['effect'],scene()['effect'])
        for key,value in [('scale',0),('scale',401),('density',0),('density',101)]:
            with self.assertRaises(ValueError):validate(dict(scene(),**{key:value}))
        self.assertEqual(validate(dict(scene(),effect='Rain',scale=150,density=70))['density'],70)
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

class StudioUITests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        from prism.studio import Window
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.w=Window(preset_path=root/'presets.json',scene_path=root/'studio.json')
        self.w.show();self.w.enter_demo();self.app.processEvents()
    def tearDown(self):
        # Destroy the window on the GUI thread now. Left to Python's garbage
        # collector, it can be freed on a background worker thread (AIO
        # sender, sensor poller), which crashes Qt on Linux (segfault in CI).
        import gc
        from PySide6.QtCore import QCoreApplication,QEvent
        w=self.w;w.tray=None;w.close()
        for thread_owner in (w.display,w.fans,w.sensors):thread_owner.stop()
        w.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.app.processEvents();del self.w,w;gc.collect()
        self.temp.cleanup()
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
        self.assertEqual(self.w.tabs.count(),9)
        for i in range(self.w.tabs.count()):self.w.tabs.setCurrentIndex(i);self.app.processEvents()
    def test_gallery_navigation_filter_and_playback(self):
        from prism.shell import LOOKS
        w=self.w;shell=w.shell
        self.assertEqual(len(shell.cards),len(EFFECTS))
        shell.set_category('Reactive')
        self.assertEqual(sum(not card.isHidden() for _,card in shell.cards),sum(look[4]=='Reactive' for look in LOOKS))
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

    def live_session(self):
        """A non-demo session with a fake running engine and fake SDK client."""
        from prism.backend import Demo
        from unittest.mock import MagicMock
        w=self.w;fake=Demo();fake.demo=False;connects=[]
        def reconnect(port):connects.append(port);return fake.devices
        fake.connect=reconnect
        w.backend=fake;w.did_connect(fake.devices)
        w.hardware.process=MagicMock();w.hardware.process.poll.return_value=None;w.hardware.port=50123
        return w,fake,connects
    def test_frame_hiccup_reconnects_and_resumes_effects(self):
        w,fake,connects=self.live_session()
        try:
            calls=[]
            def flaky(frames):
                calls.append(1)
                if len(calls)==1:raise SDKError('OpenRGB response timed out.')
                fake.last_frame=frames;return len(frames)
            fake.apply_frame=flaky
            # The fake reconnect returns the same (fake) backend's devices.
            with patch('prism.studio.OpenRGB',return_value=fake):
                w.start_effects();self.spin(.6)
            self.assertEqual(connects,[50123])
            self.assertTrue(w.connected);self.assertTrue(w.streaming);self.assertFalse(w.recovering)
            self.assertGreater(len(calls),1)
        finally:
            w.hardware.process=None;w.streaming=False
    def test_header_led_count_control_resizes_demo_strip(self):
        w=self.w
        w.enter_demo()
        spin=next(s for s in w.findChildren(QSpinBox) if s.accessibleName()=='LEDs on Ambient strip')
        self.assertEqual((spin.minimum(),spin.maximum(),spin.value()),(0,300,30))
        spin.setValue(48)
        next(b for b in w.findChildren(QPushButton) if b.accessibleName()=='Set LEDs on Ambient strip').click()
        self.spin(.4)
        strip=next(d for d in w.devices if d.name=='Desk light strip')
        self.assertEqual(strip.leds,48);self.assertIn('48 LEDs',w.feedback.text())
        w.tick();self.assertEqual(len(w.preview_frames[strip.key]),48)
    def test_unsized_headers_prompt_once_and_apply_default(self):
        from prism.backend import Demo
        w=self.w;fake=Demo();fake.demo=False
        strip=fake.devices[1];strip.zones=[('Ambient strip',0)];strip.leds=0
        w.backend=fake;w.did_connect(fake.devices);self.spin(.3)
        dialog=w.headers_dialog;self.assertTrue(dialog.isVisible())
        spin=next(s for s in dialog.findChildren(QSpinBox) if s.accessibleName()=='LEDs on Ambient strip')
        self.assertEqual(spin.value(),40)
        next(b for b in dialog.findChildren(QPushButton) if b.text()=='Apply').click();self.spin(.4)
        self.assertEqual(strip.leds,40);self.assertIn('saved',w.feedback.text())
        w.did_connect(fake.devices);self.spin(.2)
        self.assertIs(w.headers_dialog,dialog)  # not prompted twice per session
    def test_aio_display_gallery_select_customise_stream_and_stop(self):
        from test_lcd import FakeScreen
        from prism import aio
        w=self.w;page=w.display_page;screen=FakeScreen();w.display.opener=lambda:screen
        from prism.sensors import SensorHub,FpsMonitor
        class Gpu:
            def read(self):return {'gpu_temp':58,'gpu_load':70}
            def close(self):pass
        def no_cpu():raise OSError('needs administrator rights')
        w.sensors=SensorHub(gpu=Gpu,cpu=no_cpu,fps=FpsMonitor(launcher=lambda:(_ for _ in ()).throw(OSError('x')),foreground=lambda:None))
        w.sensors.interval=.05
        w.shell.navigate(7,'AIO display');self.assertEqual(w.tabs.tabText(7),'AIO display');self.spin(.6)
        self.assertEqual(len(page.cards),len(aio.EFFECTS))
        self.assertFalse(page.cards['Radar'].thumb.pixmap.isNull())     # live thumbnails
        page.filter('Info')
        self.assertTrue(page.cards['Dual gauge'].isVisibleTo(page));self.assertFalse(page.cards['Radar'].isVisibleTo(page))
        page.cards['Sensor bars'].clicked.emit('Sensor bars')
        self.assertEqual(page.title.text(),'Sensor bars');self.assertTrue(page.cards['Sensor bars'].property('selected'))
        self.assertFalse(page.overlay.isEnabled())                       # info blocks have their own readout
        page.select('Plasma flow');self.assertTrue(page.overlay.isEnabled())
        page.overlay.setCurrentText('Custom text');self.assertTrue(page.text.isEnabled());page.text.setText('GG')
        page.set_colour('primary','#FF0000');self.assertEqual(page.colour_btns['primary'].text(),'#FF0000')
        page.start_btn.click();self.spin(.6)
        self.assertTrue(screen.frames);self.assertEqual(screen.frames[-1][:2],bytes([0xFF,0xD8]))
        self.assertFalse(page.preview.pixmap.isNull())
        self.assertEqual(w.display_data(0)['gpu_temp'],58)
        page.stop_btn.click();self.assertTrue(screen.closed);self.assertIn('off',page.status.text())
        saved=aio.load_settings(page.settings_path)
        self.assertEqual((saved['effect'],saved['primary'],saved['text']),('Plasma flow','#FF0000','GG'))
        page.select('Picture / GIF');page.frames=[];page.start_btn.click()
        self.assertIn('Choose a picture',w.feedback.text());self.assertFalse(w.display.running)
    def test_fans_page_controls_header_and_stop_restores(self):
        from test_fans import FakeITE,FakeBus,wait
        from prism.fans import detect,FAN_PWM_CTRL_EXT_REG
        ite=FakeITE();bus=FakeBus({0:ite,1:FakeITE(0x8695,0x0A60)});original=dict(ite.regs)
        w=self.w;w.fans.opener=lambda:detect(bus.connect);w.fans.interval=.05
        w.shell.navigate(8,'Fans');self.assertEqual(w.tabs.tabText(8),'Fans')
        w.fan_start.click();self.spin(.8)
        self.assertEqual(w.fan_chart.header['key'],'8696:0')        # diagram follows the first header
        w.fan_table.selectRow(2);self.spin(.6);self.assertEqual(w.fan_chart.header['key'],'8696:2')
        w.fan_table.selectRow(0);self.spin(.6)
        self.assertEqual(w.fan_table.rowCount(),9);self.assertIn('RPM',w.fan_table.item(0,1).text())
        self.assertEqual(ite.writes,[])  # monitoring alone writes nothing
        mode=w.fan_table.cellWidget(0,2);fixed=w.fan_table.cellWidget(0,3)
        mode.setCurrentText('Fixed');fixed.setValue(75);self.spin(.8)
        self.assertEqual(ite.regs[FAN_PWM_CTRL_EXT_REG[0]],round(75*255/100))
        self.assertIn('Prism Light · 75%',w.fan_table.item(0,5).text())
        w.fan_stop.click();self.assertEqual(ite.regs,original);self.assertFalse(w.fans.running)
    def test_close_hides_to_tray_and_keeps_display_running_until_quit(self):
        from test_lcd import FakeScreen
        w=self.w;screen=FakeScreen();w.display.opener=lambda:screen
        class FakeTray:
            told=0;removed=False
            def hidden(self):self.told+=1
            def remove(self):self.removed=True
        tray=FakeTray();w.tray=tray;w.show()
        w.display_page.start_btn.click();self.spin(.4)
        w.close();self.spin(.5)
        self.assertFalse(w.isVisible());self.assertEqual(tray.told,1)
        sent=len(screen.frames)
        self.spin(1.3)
        self.assertTrue(w.display.running);self.assertFalse(screen.closed)
        self.assertGreater(len(screen.frames),sent)          # still feeding the AIO while hidden
        w.show_from_tray();self.assertTrue(w.isVisible())
        from unittest.mock import patch
        with patch('prism.studio.QApplication.quit') as quit:
            w.quit_app();quit.assert_called_once()
        self.assertTrue(tray.removed);self.assertFalse(w.display.running);self.assertTrue(screen.closed)
    def test_second_launch_reaches_running_copy(self):
        from prism.tray import SingleInstance,notify_running_instance,SERVER_NAME
        from unittest.mock import patch
        shown=[]
        with patch('prism.tray.SERVER_NAME',SERVER_NAME+'-test'):
            self.assertFalse(notify_running_instance(100))
            single=SingleInstance(lambda:shown.append(1))
            self.assertTrue(notify_running_instance())
            self.spin(.4)
            self.assertEqual(shown,[1])
            single.server.close()
    def test_controls_do_not_flicker_while_effects_play(self):
        w=self.w;w.enter_demo();w.fps.setValue(30)
        states=[];texts=set()
        original=w.backend.apply_frame
        def slow(frames):time.sleep(.01);return original(frames)
        w.backend.apply_frame=slow
        w.start_effects()
        end=time.monotonic()+1.6
        while time.monotonic()<end:
            self.app.processEvents();w.tick()
            states.append((w.all_btn.isEnabled(),tuple(c.isEnabled() for c in w.checks.values())))
            texts.add(w.stream_status.text());time.sleep(.01)
        self.assertGreater(w.sent_frames,10)
        self.assertEqual(len(set(states)),1,'controls toggled while frames were sent')
        self.assertTrue(states[0][0])                                   # Select supported stays usable
        self.assertLessEqual(len(texts),4)                              # calm, not one text per frame
        self.assertTrue(any('fps' in t for t in texts))
        w.stop_effects()
    def test_five_devices_get_separate_preview_rows(self):
        from prism.backend import Device
        mode=[('Direct',[0,32,0,0,0,100,0,0,0,100,0,1],b'')]
        w=self.w;w.layouts={}
        w.devices=[Device(i,f'Dev {i}','V',f's{i}','loc',[('z',8)],8,mode) for i in range(5)]
        rows={round(w.layout_for(d)['y'],3) for d in w.devices}
        self.assertEqual(len(rows),5)
    def test_session_is_saved_and_resumed_after_restart(self):
        from test_lcd import FakeScreen
        from test_fans import FakeITE,FakeBus,wait
        from prism.fans import detect,FAN_PWM_CTRL_EXT_REG
        from prism import session
        # --- first run: effects playing, display on, one fan fixed at 70 %
        w,fake,connects=self.live_session()
        screen=FakeScreen();w.display.opener=lambda:screen
        ite=FakeITE();bus=FakeBus({0:ite});w.fans.opener=lambda:detect(bus.connect);w.fans.interval=.05
        w.effect.setCurrentText('Rainbow wave');w.start_effects();w.display_page.start()
        w.fans.start();self.assertTrue(wait(lambda:w.fans.snapshot()['headers']))
        w.fans.configure('8696:0',mode='Fixed',fixed=70);self.spin(.4)
        w.save_session()
        saved=session.load(w.session_path)
        self.assertTrue(saved['effects_playing']);self.assertTrue(saved['display_on']);self.assertTrue(saved['fans_active'])
        self.assertEqual(saved['scene']['effect'],'Rainbow wave')
        self.assertEqual(saved['fan_headers']['8696:0'],{'mode':'Fixed','fixed':70,'pump':False})
        w.streaming=False;w.display.stop();w.fans.stop();w.hardware.process=None
        # --- after restart: a new window with the same settings folder resumes everything
        from prism.studio import Window
        w2=Window(preset_path=w.session_path.parent/'presets.json',scene_path=w.session_path.parent/'studio2.json')
        try:
            screen2=FakeScreen();w2.display.opener=lambda:screen2
            ite2=FakeITE();bus2=FakeBus({0:ite2});w2.fans.opener=lambda:detect(bus2.connect);w2.fans.interval=.05
            w2.effect.setCurrentText('Solid')
            w2.resume_session()
            self.assertTrue(w2.display.running)
            self.assertTrue(wait(lambda:ite2.regs[FAN_PWM_CTRL_EXT_REG[0]]==round(70*255/100)))
            fake2=type(fake)();fake2.demo=False
            w2.backend=fake2;w2.did_connect(fake2.devices)
            end=time.monotonic()+1
            while time.monotonic()<end and not w2.streaming:self.app.processEvents();time.sleep(.02)
            self.assertEqual(w2.effect.currentText(),'Rainbow wave');self.assertTrue(w2.streaming)
            self.assertIn('Welcome back',w2.feedback.text())
        finally:
            from PySide6.QtCore import QCoreApplication,QEvent
            w2.streaming=False;w2.display.stop();w2.fans.stop();w2.sensors.stop();w2.close()
            w2.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
    def test_demo_runs_never_overwrite_the_saved_session(self):
        w=self.w;w.enter_demo();w.save_session()
        self.assertFalse(w.session_path.exists())
    def test_repeated_failures_stop_instead_of_looping(self):
        w,fake,connects=self.live_session()
        try:
            def broken(frames):raise SDKError('OpenRGB response timed out.')
            fake.apply_frame=broken
            w.start_effects();self.spin(1.2)
            self.assertEqual(len(connects),3)
            self.assertFalse(w.connected);self.assertFalse(w.streaming);self.assertFalse(w.recovering)
            self.assertIn('timed out',w.feedback.text())
        finally:
            w.hardware.process=None

if __name__=='__main__':unittest.main()

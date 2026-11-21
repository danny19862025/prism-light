# SPDX-License-Identifier: GPL-3.0-only
import os
import struct
import tempfile
import time
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from prism.lcd import packets,DisplayWorker,PAYLOAD,PACKET
from prism import aio

app=QApplication.instance() or QApplication([])

class FakeScreen:
    name='Fake LCD'
    def __init__(self):self.frames=[];self.features=[];self.closed=False
    def send_jpeg(self,jpeg):self.frames.append(jpeg)
    def set_brightness(self,v):self.features.append(('brightness',v))
    def set_rotation(self,v):self.features.append(('rotation',v))
    def close(self):self.closed=True

def wait(cond,timeout=2):
    end=time.monotonic()+timeout
    while time.monotonic()<end and not cond():time.sleep(.02)
    return cond()

class PacketTests(unittest.TestCase):
    def test_split_flags_only_last_packet(self):
        data=bytes(range(256))*10  # 2560 bytes -> 3 packets
        out=packets(data)
        self.assertEqual([len(p) for p in out],[PACKET]*3)
        self.assertEqual([p[3] for p in out],[0,0,1])
        self.assertEqual([p[4] for p in out],[0,1,2])
        self.assertTrue(all(p[:2]==b'\x02\x05' for p in out))
        self.assertEqual(b''.join(p[8:8+struct.unpack('<H',p[6:8])[0]] for p in out),data)
    def test_exact_multiple_still_marks_final_packet(self):
        out=packets(b'x'*(PAYLOAD*2))
        self.assertEqual([p[3] for p in out],[0,1])
    def test_rejects_empty_and_oversized(self):
        with self.assertRaises(ValueError):packets(b'')
        with self.assertRaises(ValueError):packets(b'x'*(PAYLOAD*257))

class WorkerTests(unittest.TestCase):
    def test_sends_newest_frame_settings_and_restores_on_stop(self):
        screen=FakeScreen();worker=DisplayWorker(lambda:screen);worker.keepalive=.2
        worker.start(brightness=70,rotation=90)
        worker.submit(b'one');worker.submit(b'two')
        self.assertTrue(wait(lambda:screen.frames and screen.frames[-1]==b'two'))
        self.assertTrue(wait(lambda:len(screen.frames)>=2))  # keep-alive resend
        worker.configure(brightness=40)
        self.assertTrue(wait(lambda:('brightness',40) in screen.features))
        self.assertIn(('rotation',90),screen.features)
        worker.stop();self.assertTrue(screen.closed);self.assertFalse(worker.running)
    def test_open_failure_is_reported_not_raised(self):
        def missing():raise OSError('No supported AIO display found.')
        worker=DisplayWorker(missing);worker.start()
        self.assertTrue(wait(lambda:not worker.running))
        self.assertIn('No supported AIO display',worker.status)
    def test_write_failure_stops_and_still_restores(self):
        screen=FakeScreen()
        def broken(jpeg):raise OSError('Display write failed: unplugged')
        screen.send_jpeg=broken
        worker=DisplayWorker(lambda:screen);worker.start();worker.submit(b'frame')
        self.assertTrue(wait(lambda:not worker.running))
        self.assertIn('unplugged',worker.status);self.assertTrue(screen.closed)

class RenderTests(unittest.TestCase):
    DATA={'cpu':42,'memory':63,'temperature':55,'bands':[.1,.5,.9,.3,.2,.6,.4,.8],'effect':'Rainbow wave','settings':{},'inputs':{}}
    def test_every_effect_renders_at_display_and_thumbnail_size(self):
        for name in aio.EFFECTS:
            for size in (aio.SIZE,118):
                image=aio.draw(name,size,2.5,{'text':'Hi','overlay':'CPU load'},self.DATA)
                self.assertEqual((image.width(),image.height()),(size,size),name)
            self.assertEqual(aio.encode(image)[:2],bytes([0xFF,0xD8]))
    def test_effects_survive_missing_sensors(self):
        for name in aio.EFFECTS:
            aio.draw(name,120,1.0,{'overlay':'Temperature'},{})
    def test_animation_changes_between_frames(self):
        for name in ('Plasma flow','Orbit rings','Ring readout','Hex panels'):
            a=aio.draw(name,120,1.0,{},self.DATA);b=aio.draw(name,120,2.0,{},self.DATA)
            self.assertNotEqual(a,b,name)
    def test_info_blocks_show_their_own_readout_without_overlay(self):
        self.assertTrue({'Dual gauge','Sensor bars','Sweep gauge'}<=aio.NO_OVERLAY)
        self.assertNotIn('Plasma flow',aio.NO_OVERLAY)
    def test_picture_and_gif_frames(self):
        from PySide6.QtGui import QImage
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,'p.png');img=QImage(640,360,QImage.Format_RGB32);img.fill(0x336699);img.save(path)
            frames=aio.load_frames(path)
            self.assertEqual(len(frames),1);self.assertEqual(frames[0][0].size().width(),aio.SIZE)
            self.assertEqual(aio.draw('Picture / GIF',100,0,{'frames':frames},{}).pixelColor(50,50).name(),'#336699')
            with self.assertRaises(ValueError):aio.load_frames(os.path.join(d,'missing.png'))
    def test_settings_roundtrip_and_reject_bad_values(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'display.json'
            self.assertEqual(aio.load_settings(path),aio.DEFAULTS)
            aio.save_settings(path,dict(aio.DEFAULTS,effect='Radar',speed=120))
            self.assertEqual(aio.load_settings(path)['effect'],'Radar')
            path.write_text('{"effect":"Nope","overlay":"Bad","speed":"fast"}')
            got=aio.load_settings(path)
            self.assertEqual((got['effect'],got['overlay'],got['speed']),(aio.DEFAULTS['effect'],'None',50))
            path.write_text('not json');self.assertEqual(aio.load_settings(path),aio.DEFAULTS)

if __name__=='__main__':unittest.main()

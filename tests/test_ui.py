# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Prism Light contributors
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import time
from pathlib import Path
import unittest
from PySide6.QtWidgets import QApplication
from prism.ui import Window,STYLE
from test_core import Server

class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion');cls.app.setStyleSheet(STYLE)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.window=Window(preset_path=Path(self.temp.name)/'presets.json')
        self.window.show();self.app.processEvents()
    def tearDown(self):
        self.window.close();self.app.processEvents();self.temp.cleanup()
    def settle(self):
        until=time.monotonic()+6
        while self.window.future and time.monotonic()<until:
            self.app.processEvents();time.sleep(.01)
        self.assertIsNone(self.window.future)
    def test_demo_selection_preset_and_preview(self):
        w=self.window;w.enter_demo();self.app.processEvents()
        self.assertIn('DEMO',w.status.text())
        self.assertEqual(len(w.devices),3)
        unsupported=w.checks[w.devices[-1].key]
        self.assertFalse(unsupported.isEnabled())
        w.set_color('#123456');w.brightness.setValue(45)
        keys=w.selected()
        w.store.save('Night','#ABCDEF',25,keys,True)
        w.reload_presets();w.restore_preset()
        self.assertEqual(w.color,'#ABCDEF');self.assertEqual(w.brightness.value(),25)
        w.apply();self.settle()
        self.assertIn('DEMO',w.feedback.text())
        w.resize(1180,940);self.app.processEvents()
        if os.environ.get('PRISM_SCREENSHOT'):
            w.grab().save(os.environ['PRISM_SCREENSHOT'])
        w.disconnect();self.assertFalse(w.apply_btn.isEnabled())
    def test_live_mock_and_disconnect_error(self):
        w=self.window;server=Server();w.port.setValue(server.port);w.connect_live();self.settle()
        self.assertTrue(w.connected);self.assertIn('Connected',w.status.text())
        w.apply();self.settle();self.assertIn('sent',w.feedback.text())
        w.disconnect();server.finish()
        w.port.setValue(server.port);w.connect_live();self.settle()
        self.assertFalse(w.connected);self.assertIn('SDK Server',w.feedback.text())
    def test_empty_inventory(self):
        self.window.did_connect([])
        self.assertIn('no devices',self.window.feedback.text())
        self.assertFalse(self.window.apply_btn.isEnabled())

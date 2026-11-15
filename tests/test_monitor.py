# SPDX-License-Identifier: GPL-3.0-only
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from prism_monitor.fps import FrameTracker
from prism_monitor.model import Reading, Snapshot, format_value, is_fresh, reading
from prism_monitor.settings import DEFAULTS, Settings, validate
from prism_monitor.sources import map_host_items

V1 = 'Application,ProcessID,SwapChainAddress,Runtime,SyncInterval,PresentFlags,Dropped,TimeInSeconds,msInPresentAPI,msBetweenPresents'
V2 = ('Application,ProcessID,SwapChainAddress,PresentRuntime,SyncInterval,PresentFlags,AllowsTearing,PresentMode,'
      'TimeInMs,MsBetweenSimulationStart,MsBetweenPresents')


def tracker(foreground=None):
    return FrameTracker(own_pid=999, resolve_name=lambda pid: f'resolved{pid}.exe', foreground=lambda: foreground)


def feed_frames(t, name, pid, fps, seconds=1.0, now=100.0, v2=False):
    ms = 1000.0 / fps
    n = int(seconds * fps)
    for i in range(n):
        ts = i * ms / 1000
        if v2:
            t.feed(f'{name},{pid},0xA,DXGI,1,0,0,Flip,{ts * 1000},0,{ms}', now=now)
        else:
            t.feed(f'{name},{pid},0xA,DXGI,1,0,0,{ts},0.1,{ms}', now=now)


class FrameTrackerTests(unittest.TestCase):
    def test_v1_lowercase_columns(self):
        t = tracker()
        t.feed(V1)
        feed_frames(t, 'game.exe', 10, 40)
        values, app = t.read('game.exe', now=100.5)
        self.assertEqual(app, 'game.exe')
        self.assertAlmostEqual(values['fps'].value, 40, delta=0.5)
        self.assertAlmostEqual(values['frametime'].value, 25, delta=0.5)

    def test_v2_columns(self):
        t = tracker()
        t.feed(V2)
        feed_frames(t, 'game.exe', 10, 144, v2=True)
        values, _ = t.read('GAME.EXE', now=100.2)
        self.assertAlmostEqual(values['fps'].value, 144, delta=1)

    def test_selected_process_not_other_presenters(self):
        t = tracker()
        t.feed(V1)
        feed_frames(t, 'browser.exe', 11, 60)
        feed_frames(t, 'game.exe', 12, 30)
        values, _ = t.read('game.exe', now=100.1)
        self.assertAlmostEqual(values['fps'].value, 30, delta=0.5)

    def test_own_process_excluded(self):
        t = tracker(foreground=999)
        t.feed(V1)
        feed_frames(t, 'prismmonitor.exe', 999, 60)
        self.assertEqual(t.read('', now=100.1), ({}, None))
        self.assertEqual(t.presenting(now=100.1), [])

    def test_auto_uses_foreground_and_ignores_shell(self):
        t = tracker(foreground=12)
        t.feed(V1)
        feed_frames(t, 'game.exe', 12, 50)
        feed_frames(t, 'explorer.exe', 13, 60)
        values, app = t.read('', now=100.1)
        self.assertEqual(app, 'game.exe')
        t.foreground = lambda: 13
        t.auto_since = -100
        self.assertEqual(t.read('', now=100.1), ({}, None))

    def test_stale_is_unavailable_not_zero(self):
        t = tracker()
        t.feed(V1)
        feed_frames(t, 'game.exe', 10, 60, now=100.0)
        self.assertEqual(t.read('game.exe', now=103.0), ({}, None))

    def test_unknown_name_resolved(self):
        t = tracker()
        t.feed(V1)
        feed_frames(t, '<unknown>', 77, 30)
        self.assertEqual(t.presenting(now=100.1)[0][1], 'resolved77.exe')

    def test_rows_before_header_ignored(self):
        t = tracker()
        feed_frames(t, 'game.exe', 10, 60)
        self.assertEqual(t.presenting(now=100.1), [])


class HostMappingTests(unittest.TestCase):
    def item(self, hw_type, kind, name, value, hw='AMD Ryzen 9 9900X', id_='/x'):
        return {'hwType': hw_type, 'type': kind, 'name': name, 'value': value, 'hw': hw, 'id': id_}

    def test_unprivileged_zeros_dropped(self):
        readings, fans, _ = map_host_items([
            self.item('Cpu', 'Temperature', 'Core (Tctl/Tdie)', 0),
            self.item('Cpu', 'Power', 'Package', 0),
            self.item('Cpu', 'Clock', 'Cores (Average)', 0)])
        self.assertEqual(readings, {})

    def test_real_values_and_fans(self):
        readings, fans, info = map_host_items([
            self.item('Cpu', 'Temperature', 'CCD1 (Tdie)', 60),
            self.item('Cpu', 'Temperature', 'Core (Tctl/Tdie)', 69.75),
            self.item('Cpu', 'Power', 'Package', 87.6),
            self.item('Cpu', 'Clock', 'Cores (Average)', 5067),
            self.item('SuperIO', 'Fan', 'Fan #1', 864, hw='ITE IT8696E', id_='/lpc/it8696e/0/fan/0'),
            self.item('SuperIO', 'Fan', 'Fan #3', 0, hw='ITE IT8696E', id_='/lpc/it8696e/0/fan/2'),
            self.item('SuperIO', 'Fan', 'Fan #4', None, hw='ITE IT8696E', id_='/lpc/it8696e/0/fan/3')])
        self.assertEqual(readings['cpu_temp'].value, 69.75)
        self.assertEqual(info['cpu_temp_sensor'], 'Core (Tctl/Tdie)')
        self.assertEqual(readings['cpu_power'].value, 87.6)
        self.assertEqual(readings['cpu_clock'].value, 5067)
        self.assertEqual(set(fans), {'/lpc/it8696e/0/fan/0', '/lpc/it8696e/0/fan/2'})   # None skipped, 0 rpm kept

    def test_integrated_gpu_ignored(self):
        readings, _, _ = map_host_items([
            self.item('GpuAmd', 'Temperature', 'GPU Core', 50, hw='AMD Radeon(TM) Graphics')])
        self.assertEqual(readings, {})


class ModelTests(unittest.TestCase):
    def test_reading_rejects_non_numbers(self):
        for value in (None, float('nan'), float('inf'), 'x', True):
            self.assertIsNone(reading(value, 's'))

    def test_freshness(self):
        item = Reading(1.0, 10.0)
        self.assertTrue(is_fresh(item, 12.0, 3.0))
        self.assertFalse(is_fresh(item, 14.0, 3.0))
        self.assertFalse(is_fresh(None, 10.0, 3.0))

    def test_format(self):
        gib = 1 << 30
        self.assertEqual(format_value('ram_used', Reading(16.25 * gib, 0, extra=32 * gib)), '16.2/32 GB')
        self.assertEqual(format_value('cpu_clock', Reading(5067, 0)), '5.07 GHz')
        self.assertEqual(format_value('net_down', Reading(2.5 * (1 << 20), 0)), '2.5 MB/s')


class SettingsTests(unittest.TestCase):
    def test_validate_clamps_and_defaults(self):
        data = validate({'scale': 9, 'opacity': -1, 'theme': 'neon', 'visible': ['cpu_temp', 'bogus'],
                         'position': 'x', 'accent': 'red'})
        self.assertEqual(data['scale'], 2.0)
        self.assertEqual(data['opacity'], 0.2)
        self.assertEqual(data['theme'], 'dark')
        self.assertEqual(data['visible'], ['cpu_temp'])
        self.assertIsNone(data['position'])
        self.assertEqual(data['accent'], DEFAULTS['accent'])
        self.assertFalse(validate({})['launch_at_login'])

    def test_round_trip_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'settings.json'
            s = Settings(path)
            s['scale'] = 1.5
            s.save()
            self.assertEqual(Settings(path)['scale'], 1.5)
            path.write_text('{broken', encoding='utf-8')
            s = Settings(path)
            self.assertIsNotNone(s.error)
            self.assertEqual(s['scale'], 1.0)


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_missing_values_render_as_unavailable(self):
        import time
        from prism_monitor.widget import MonitorWidget
        with tempfile.TemporaryDirectory() as d:
            settings = Settings(Path(d) / 's.json')
            w = MonitorWidget(settings)
            now = time.monotonic()
            snap = Snapshot(readings={'gpu_temp': Reading(42, now, 'adl'), 'cpu_util': Reading(10, now - 60, 'pdh')})
            w.update_snapshot(snap)
            self.assertEqual(w.value_text('cpu_temp', now)[0], 'n/a')
            self.assertEqual(w.value_text('cpu_util', now)[0], 'stale')
            self.assertEqual(w.value_text('gpu_temp', now)[0], '42°')
            self.assertIsNone(w.history['cpu_temp'][-1])   # graph gap, not zero
            w.grab()
            settings['orientation'] = 'vertical'
            w.relayout()
            self.assertGreater(w.height(), w.width())


if __name__ == '__main__':
    unittest.main()

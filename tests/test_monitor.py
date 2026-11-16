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
        self.assertEqual(info['cpu_temps'], {'CCD1 (Tdie)': 60, 'Core (Tctl/Tdie)': 69.75})   # all, for selection
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

    def test_vertical_stand_keeps_keep_awake_button_inside(self):
        from prism_monitor.widget import MonitorWidget
        with tempfile.TemporaryDirectory() as d:
            settings = Settings(Path(d) / 's.json')
            settings['orientation'] = 'vertical'
            w = MonitorWidget(settings)
            for active, headline in ((False, 'Off'), (True, '1:23:45 left'),
                                     (True, '↓ 12.5 MB/s · 3 files downloading')):
                w.set_awake(active, headline, '')
                w.grab()
                self.assertTrue(w.header_rect.contains(w.pill_rect), (active, headline))
                tip = next(text for rect, text in w.hits if rect == w.pill_rect)
                if active:
                    self.assertIn(headline, tip)   # the full status is always in the tooltip
            settings['orientation'] = 'horizontal'
            w.relayout()
            w.set_awake(True, '1:23:45 left', '')
            w.grab()
            self.assertTrue(w.header_rect.contains(w.pill_rect))
            self.assertGreater(w.width(), w.height())

    def test_settings_window_follows_layout_from_menu(self):
        from prism_monitor.settings_window import SettingsWindow
        with tempfile.TemporaryDirectory() as d:
            settings = Settings(Path(d) / 's.json')
            window = SettingsWindow(settings, FrameTracker(own_pid=999))
            settings['orientation'] = 'vertical'   # as the tray menu does
            window.sync_checks()
            self.assertEqual(window.orientation.currentData(), 'vertical')
            self.assertEqual(window.orientation.currentText(), 'Vertical stand')

    def test_automatic_helper_restarts_after_failure_but_not_after_stop(self):
        from unittest import mock
        from prism_monitor import host as host_module
        client = host_module.HostClient()
        with mock.patch.object(host_module, 'task_status', return_value='ready'):
            client.mode = 'task'
            client._fail('The sensor helper stopped.')
            self.assertTrue(client.restart_timer.isActive())
            self.assertEqual(client.restart_timer.interval(), 3000)
            self.assertIn('Restarting automatically', client.detail)
            client._fail('again')
            self.assertEqual(client.restart_timer.interval(), 10000)   # backoff
            client.stop()
            self.assertFalse(client.restart_timer.isActive())
            client.mode = 'uac'   # prompt-based helper is never restarted silently
            client.stopping = False
            client._fail('x')
            self.assertFalse(client.restart_timer.isActive())

    def test_settings_window_refreshes_status(self):
        from prism_monitor.fps import FrameTracker
        from prism_monitor.settings_window import SettingsWindow
        with tempfile.TemporaryDirectory() as d:
            window = SettingsWindow(Settings(Path(d) / 's.json'), FrameTracker(own_pid=999))
            window.update_status(Snapshot(status={'system': 'OK', 'fps': 'OK'}), ('off', 'Not started.'))
            window.show()            # showEvent → refresh_live
            window.refresh_live()
            self.assertIn('Status: OK', window.fps_status.text())
            self.assertTrue(window.auto_label.text().startswith('Automatic helper:'))
            window.close()


class FakePower:
    def __init__(self):
        self.calls = []
        self.procs = set()
        self.files = []

    def hold(self, display):
        self.calls.append(('hold', display))

    def release(self):
        self.calls.append(('release',))

    def processes(self):
        return set(self.procs)

    def partials(self, folder):
        return list(self.files)


class KeepAwakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def make(self, **overrides):
        from prism_monitor.awake import KeepAwake
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = Settings(Path(self.tmp.name) / 's.json')
        settings['awake_folder'] = self.tmp.name
        for k, v in overrides.items():
            settings[k] = v
        self.now = [1000.0]
        self.power = FakePower()
        engine = KeepAwake(settings, power=self.power, clock=lambda: self.now[0])
        engine.timer.stop()
        self.finished = []
        engine.finished.connect(lambda msg, after: self.finished.append((msg, after)))
        return engine

    def snap(self, net=None, cpu=None):
        readings = {}
        if net is not None:
            readings['net_down'] = Reading(net, 0, 'pdh')
        if cpu is not None:
            readings['cpu_util'] = Reading(cpu, 0, 'pdh')
        return Snapshot(readings=readings)

    def test_duration_holds_then_releases(self):
        e = self.make()
        self.assertEqual(e.start('duration', minutes=30, display=True), '')
        self.assertEqual(self.power.calls[-1], ('hold', True))
        self.assertEqual(e.headline(), '30:00 left')
        self.now[0] += 29 * 60
        e.tick()
        self.assertTrue(e.active)
        self.now[0] += 61
        e.tick()
        self.assertFalse(e.active)
        self.assertEqual(self.power.calls[-1], ('release',))
        self.assertEqual(self.finished[0][1], 'none')

    def test_downloads_wait_for_quiet_network_and_partial_files(self):
        e = self.make(awake_net_kbps=100, awake_quiet_min=2.0)
        e.feed(self.snap(net=5 * 1024 * 1024))
        e.start('downloads')
        self.assertIn('MB/s', e.headline())
        e.feed(self.snap(net=1024))                 # network quiet, but a browser file is still partial
        self.power.files = ['movie.mkv.crdownload']
        self.now[0] += 600
        e.tick()
        self.assertTrue(e.active)
        self.assertIn('1 file downloading', e.headline())
        self.power.files = []
        self.now[0] += 60
        e.tick()
        self.assertTrue(e.active)
        self.assertIn('finishing in 1:00', e.headline())
        self.now[0] += 61
        e.tick()
        self.assertFalse(e.active)
        self.assertIn('Downloads finished', self.finished[0][0])

    def test_process_mode_ends_when_app_closes(self):
        e = self.make()
        self.assertIn('not running', e.start('process', processes=('HandBrake.exe',)))
        self.power.procs = {'handbrake.exe'}
        self.assertEqual(e.start('process', processes=('HandBrake.exe',)), '')
        self.power.procs = set()
        e.proc_cache = (0.0, set())
        e.tick()
        self.assertFalse(e.active)
        self.assertIn('HandBrake.exe closed', self.finished[0][0])

    def test_cpu_mode_and_after_action_is_per_session(self):
        e = self.make(awake_cpu_pct=20, awake_quiet_min=1.0)
        e.set_after('shutdown')
        e.feed(self.snap(cpu=90))
        e.start('cpu')
        e.feed(self.snap(cpu=5))
        self.now[0] += 30
        e.tick()
        self.assertTrue(e.active)
        self.now[0] += 31
        e.tick()
        self.assertEqual(self.finished[0][1], 'shutdown')
        self.assertNotIn('awake_after', e.settings.data)   # never persisted

    def test_auto_apps_hold_without_after_action(self):
        e = self.make(awake_auto_apps=['obs64.exe'])
        self.power.procs = {'obs64.exe'}
        e.tick()
        self.assertTrue(e.active)
        self.assertEqual(e.headline(), 'obs64.exe running')
        self.power.procs = set()
        e.proc_cache = (0.0, set())
        e.tick()
        self.assertFalse(e.active)
        self.assertEqual(self.finished, [])
        self.assertEqual(self.power.calls[-1], ('release',))

    def test_countdown_cancel_never_runs_action(self):
        from prism_monitor.awake_ui import CountdownDialog
        ran = []
        d = CountdownDialog('shutdown', 'Downloads finished.', seconds=2, runner=ran.append)
        d.step()
        d.reject()
        d.step()
        self.assertEqual(ran, [])
        d = CountdownDialog('sleep', 'Done.', seconds=2, runner=ran.append)
        d.step()
        d.step()
        self.assertEqual(ran, ['sleep'])

    def test_widget_pill_and_settings_tab(self):
        from prism_monitor.settings_window import SettingsWindow
        from prism_monitor.widget import MonitorWidget
        e = self.make()
        w = MonitorWidget(e.settings)
        w.set_awake(True, '1:00:00 left', 'tip')
        w.grab()
        self.assertIsNotNone(w.pill_rect)
        window = SettingsWindow(e.settings, FrameTracker(own_pid=999), e)
        window.show()
        window.awake_mode.setCurrentIndex(window.awake_mode.findData('duration'))
        window.awake_minutes.setValue(5)
        window.start_awake()
        self.assertTrue(e.active)
        window.update_awake()
        self.assertIn('5:00 left', window.awake_status.text())
        window.close()


if __name__ == '__main__':
    unittest.main()

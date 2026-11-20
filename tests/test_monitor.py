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
from qtsafe import QtTestCase

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
        self.assertEqual(data['visible'], ['cpu_temp', 'battery'])   # new default-on card added once
        self.assertEqual(validate({'visible': ['cpu_temp'], 'known_metrics': data['known_metrics']})['visible'],
                         ['cpu_temp'])                               # and stays off once the user hides it
        self.assertIsNone(data['position'])
        self.assertEqual(data['accent'], DEFAULTS['accent'])
        self.assertFalse(validate({})['launch_at_login'])

    def test_poster_is_the_default_but_saved_settings_keep_card(self):
        self.assertEqual(DEFAULTS['style'], 'poster')
        self.assertEqual(validate({'theme': 'midnight'})['style'], 'card')   # saved before Poster existed
        self.assertEqual(validate({'style': 'poster', 'poster_hero': 'x'})['poster_hero'], 'cpu')
        self.assertEqual(validate({'style': 'bogus'})['style'], 'card')
        self.assertEqual(DEFAULTS['poster_opacity'], 0.0)                     # no panel by default
        self.assertEqual(DEFAULTS['layer'], 'top')
        self.assertEqual(validate({'always_on_top': False})['layer'], 'normal')   # saved before 'layer' existed
        self.assertEqual(validate({'always_on_top': True})['layer'], 'top')
        self.assertEqual(validate({'layer': 'behind'})['layer'], 'behind')
        self.assertEqual(validate({'layer': 'sideways'})['layer'], 'top')
        self.assertEqual(validate({'theme': 'system'})['theme'], 'system')
        from prism_monitor.settings import resolve_theme
        self.assertIn(resolve_theme('system'), ('light', 'dark'))
        self.assertEqual(resolve_theme('midnight'), 'midnight')
        self.assertEqual(validate({'poster_opacity': 7})['poster_opacity'], 1.0)
        self.assertEqual(validate({'poster_text': '#FFB3D1', 'poster_accent': 'red'})['poster_text'], '#ffb3d1')
        self.assertEqual(validate({'poster_accent': 'red'})['poster_accent'], '')

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


class WidgetTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_missing_values_render_as_unavailable(self):
        import time
        from prism_monitor.widget import MonitorWidget
        with tempfile.TemporaryDirectory() as d:
            settings = Settings(Path(d) / 's.json')
            settings['style'] = 'card'
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
            settings['style'] = 'card'
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


class PosterTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def snapshot(self, now, cpu_temp=63, fan=980, fps=None):
        readings = {'cpu_temp': Reading(cpu_temp, now, 'lhm'), 'cpu_util': Reading(35, now, 'pdh'),
                    'gpu_temp': Reading(70, now, 'adl'), 'gpu_util': Reading(92, now, 'adl'),
                    'ram_used': Reading(9.4 * (1 << 30), now, 'windows', 32 * (1 << 30))}
        if fps:
            readings['fps'] = Reading(fps, now, 'presentmon')
            readings['frametime'] = Reading(1000 / fps, now, 'presentmon')
        return Snapshot(readings=readings, fans={'f1': ('CPU fan', Reading(fan, now, 'lhm'))},
                        batteries={'pad': ('DualSense', Reading(35, now, 'usb', 0.0), '')},
                        info={'batteries_scanned': True, 'fps_app': 'Game.exe' if fps else None})

    def widget(self, d):
        from prism_monitor.widget import MonitorWidget
        settings = Settings(Path(d) / 's.json')
        settings['visible'] = settings['visible'] + ['fans']
        settings['fans_visible'] = ['f1']
        return MonitorWidget(settings)

    def test_hero_rows_and_tooltips(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            w = self.widget(d)
            self.assertTrue(w.is_poster())
            now = time.monotonic()
            w.update_snapshot(self.snapshot(now))
            c = w.poster
            self.assertEqual((c.number, c.unit, c.headline, c.caption), ('63', '°C', 'CPU', 'MONITOR'))
            self.assertEqual(c.subline, '35% LOAD')
            self.assertEqual([g.caption for g in c.groups], ['SYSTEM', 'FANS', 'BATTERIES'])
            system = {r.label: r for r in c.groups[0].rows}
            self.assertEqual(system['GPU'].value, '92% · 70°C')
            self.assertIsNotNone(system['GPU'].color)                     # 70°C: amber, as on Linux
            self.assertNotIn('VRAM', system)                               # Linux sections only
            self.assertIsNotNone(c.groups[2].rows[0].color)               # 35% battery: amber
            self.assertNotIn('CPU', system)                                # the hero already shows it
            self.assertAlmostEqual(c.groups[2].rows[0].level, 0.35)
            w.grab()
            self.assertIsNone(w.pill_rect)
            self.assertTrue(any(text.startswith('DualSense') for _, text in w.hits))
            w.settings['poster_hero'] = 'gpu'
            w.relayout()
            self.assertEqual((w.poster.number, w.poster.headline), ('70', 'GPU'))

    def test_fps_takes_the_hero_while_gaming(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            w = self.widget(d)
            w.update_snapshot(self.snapshot(time.monotonic(), fps=142))
            self.assertEqual((w.poster.number, w.poster.headline, w.poster.caption), ('142', 'FPS', 'GAMING'))
            self.assertEqual(w.poster.subline, 'GAME  ·  7.0 MS')
            w.grab()

    def test_width_steady_while_values_change(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            w = self.widget(d)
            now = time.monotonic()
            w.update_snapshot(self.snapshot(now, cpu_temp=63, fan=1411))
            size = w.size()
            for temp, fan in ((41, 980), (88, 1111), (9, 800)):
                w.update_snapshot(self.snapshot(now, cpu_temp=temp, fan=fan))
                self.assertEqual(w.size(), size, (temp, fan))              # never shrinks or jumps
            w.settings['visible'] = [k for k in w.settings['visible'] if k != 'battery']
            w.relayout()
            self.assertLess(w.height(), size.height())                     # rows removed: may shrink

    def test_menu_sections_and_size(self):
        from prism_monitor.app import SECTIONS, SIZES
        self.assertEqual(SIZES['Large'], 1.25)
        self.assertIn('ram_used', SECTIONS['system'])

    def test_background_opacity_draws_a_panel(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            w = self.widget(d)
            w.update_snapshot(self.snapshot(time.monotonic()))
            corner = (3, w.height() // 2)          # left edge, beside the vertical caption
            self.assertLessEqual(w.grab().toImage().pixelColor(*corner).alpha(), 1)
            w.settings['poster_opacity'] = 0.5
            self.assertAlmostEqual(w.grab().toImage().pixelColor(*corner).alpha(), 128, delta=4)

    def test_text_and_number_colours(self):
        from prism_monitor import poster as P
        ink = P.ink_for('dark')
        self.assertEqual((ink.text.name(), ink.accent.name()), ('#ffffff', '#8fd8ff'))
        ink = P.ink_for('dark', '#ffb3d1', '#ff7a7a')
        self.assertEqual((ink.text.name(), ink.accent.name()), ('#ffb3d1', '#ff7a7a'))
        self.assertLess(ink.shadow.lightness(), 128)                       # light text: dark shadow
        self.assertGreater(P.ink_for('dark', '#141821').shadow.lightness(), 128)   # dark text: light halo
        import time
        with tempfile.TemporaryDirectory() as d:
            w = self.widget(d)
            w.update_snapshot(self.snapshot(time.monotonic()))
            w.settings['poster_text'] = '#ffb3d1'
            self.assertEqual(w.poster_ink().text.name(), '#ffb3d1')
            w.grab()

    def test_settings_sidebar_and_about(self):
        from prism_monitor.settings_window import SettingsWindow
        from prism_monitor.ui_kit import DONATE_URL, Glow
        with tempfile.TemporaryDirectory() as d:
            settings = Settings(Path(d) / 's.json')
            w = SettingsWindow(settings, FrameTracker(own_pid=999))
            titles = [w.tabs.tabText(i) for i in range(w.tabs.count())]
            self.assertEqual(titles[-1], 'About')
            w.show_tab('about')
            self.assertIs(w.tabs.currentWidget(), w.about)
            self.assertEqual(DONATE_URL, 'https://buymeacoffee.com/danny1986')
            self.assertGreaterEqual(w.width(), 820)
            self.assertTrue(w.findChildren(Glow))                     # buttons glow on hover
            self.assertIsNone(w.about.coffee.graphicsEffect())        # glow is painted around it: no flicker
            self.assertIsNone(w.about.coffee.findChild(Glow))
            w.grab()

    def test_update_check_picks_newest_monitor_release(self):
        from prism_monitor.updates import compare, latest_release
        releases = [{'tag_name': 'prism-light-v2.0.0', 'html_url': 'light'},
                    {'tag_name': 'prism-monitor-v1.0.10', 'html_url': 'u10'},
                    {'tag_name': 'prism-monitor-v1.0.9', 'html_url': 'u9'},
                    {'tag_name': 'prism-monitor-v2.0.0', 'html_url': 'beta', 'prerelease': True},
                    {'tag_name': 'prism-monitor-v3.0.0', 'html_url': 'draft', 'draft': True}]
        self.assertEqual(latest_release(releases), ('1.0.10', 'u10'))       # numeric, not text, order
        self.assertEqual(compare('1.0.4', releases)[:2], ('newer', '1.0.10'))
        self.assertEqual(compare('1.0.10', releases)[0], 'current')
        self.assertEqual(compare('1.1.0', releases)[0], 'current')          # a build newer than any release
        self.assertEqual(compare('1.0.4', {'message': 'rate limited'})[0], 'none')

    def test_about_update_states_and_font(self):
        import time
        from prism_monitor.settings_window import SettingsWindow
        with tempfile.TemporaryDirectory() as d:
            w = self.widget(d)
            w.update_snapshot(self.snapshot(time.monotonic()))
            w.settings['poster_font'] = 'Consolas'
            w.relayout()
            self.assertEqual(w.poster.family, 'Consolas')
            win = SettingsWindow(w.settings, FrameTracker(own_pid=999))
            about = win.about
            about.update_result('newer', '1.0.9', 'https://example.invalid/r')
            self.assertFalse(about.download_button.isHidden())
            self.assertIn('1.0.9', about.update_status.text())
            about.update_result('current', '1.0.9', '')
            self.assertIn('latest', about.update_status.text())
            about.update_result('error', 'offline', '')
            self.assertIn('offline', about.update_status.text())

    def test_behind_windows_flag(self):
        from PySide6.QtCore import Qt
        with tempfile.TemporaryDirectory() as d:
            w = self.widget(d)
            w.settings['layer'] = 'behind'
            w.apply_flags()
            self.assertTrue(w.windowFlags() & Qt.WindowStaysOnBottomHint)
            self.assertFalse(w.windowFlags() & Qt.WindowStaysOnTopHint)
            w.settings['layer'] = 'top'
            w.apply_flags()
            self.assertTrue(w.windowFlags() & Qt.WindowStaysOnTopHint)

    def test_system_theme_draws_both_looks(self):
        from unittest import mock
        from prism_monitor import widget as widget_module
        with tempfile.TemporaryDirectory() as d:
            w = self.widget(d)
            w.settings['theme'] = 'system'
            for mode in ('light', 'dark'):
                with mock.patch.object(widget_module, 'resolve_theme', return_value=mode):
                    self.assertEqual(w.theme_name(), mode)
                    self.assertEqual(w.poster_ink().text.name(), '#141821' if mode == 'light' else '#ffffff')
                    w.grab()
                    w.settings['style'] = 'card'
                    w.relayout()
                    w.grab()
                    w.settings['style'] = 'poster'
                    w.relayout()

    def test_switching_back_to_card(self):
        with tempfile.TemporaryDirectory() as d:
            w = self.widget(d)
            w.grab()
            w.settings['style'] = 'card'
            w.relayout()
            self.assertIsNone(w.poster)
            self.assertTrue(w.cards)
            w.grab()


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


class KeepAwakeTests(QtTestCase):
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
        e.settings['style'] = 'card'
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



class BatteryTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_dualsense_reports(self):
        from prism_monitor.batteries import SIMPLE_MODE, parse_sony_report
        usb = bytearray(64)
        usb[0], usb[53] = 0x01, 0x13          # charging, capacity 3
        self.assertEqual(parse_sony_report('ds', False, bytes(usb)), (35.0, 'charging'))
        bt = bytearray(78)
        bt[0], bt[54] = 0x31, 0x0A            # on battery, capacity 10
        self.assertEqual(parse_sony_report('ds', True, bytes(bt)), (100.0, ''))
        bt[54] = 0x2A
        self.assertEqual(parse_sony_report('ds', True, bytes(bt)), (100.0, 'full'))
        self.assertEqual(parse_sony_report('ds', True, bytes([0x01] + [0] * 9)), SIMPLE_MODE)
        self.assertIsNone(parse_sony_report('ds', False, bytes([0x05, 1, 2])))   # unrelated report

    def test_dualshock4_reports(self):
        from prism_monitor.batteries import parse_sony_report
        usb = bytearray(64)
        usb[0], usb[30] = 0x01, 0x1B          # cable, capacity 11 = full
        self.assertEqual(parse_sony_report('ds4', False, bytes(usb)), (100.0, 'full'))
        bt = bytearray(78)
        bt[0], bt[32] = 0x11, 0x04
        self.assertEqual(parse_sony_report('ds4', True, bytes(bt)), (45.0, ''))

    def test_controller_paths(self):
        from prism_monitor.batteries import sony_model
        self.assertEqual(sony_model(r'\?\HID#VID_054C&PID_0CE6&MI_03#8&1&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}'),
                         ('DualSense', 'ds', False))
        self.assertEqual(sony_model(r'\?\HID#{00001124-0000-1000-8000-00805f9b34fb}_VID&0002054c_PID&0ce6#b&1#{x}'),
                         ('DualSense', 'ds', True))
        self.assertIsNone(sony_model(r'\?\HID#VID_1532&PID_0099&MI_00#a&1#{x}'))

    def test_merge_prefers_direct_reading_and_numbers_twins(self):
        from prism_monitor.batteries import Battery, merge
        merged = merge([Battery('sony:a', 'DualSense', 40, source='usb'),
                        Battery('win:a', 'dualsense', 50, source='windows'),
                        Battery('sony:b', 'DualSense', 80, source='bluetooth'),
                        Battery('win:k', 'K2 Keyboard', 70, source='windows')])
        self.assertEqual([(b.name, b.percent) for b in merged],
                         [('DualSense 1', 40), ('DualSense 2', 80), ('K2 Keyboard', 70)])

    def test_monitor_restart_runs_one_scanner(self):
        import threading
        import time
        from prism_monitor.batteries import Battery, BatteryMonitor
        calls = []
        running = threading.Semaphore(0)

        def scanner():
            calls.append(threading.get_ident())
            running.release()
            return [Battery('x', 'Pad', 50)], {'fake': 'OK'}
        monitor = BatteryMonitor(interval=0.05, scanner=scanner)
        monitor.start()
        running.acquire(timeout=2)
        monitor.stop()
        monitor.start()
        time.sleep(0.3)
        monitor.stop()
        time.sleep(0.1)
        self.assertLessEqual(len(set(calls)), 2)
        self.assertEqual(monitor.latest()[0], [])          # stopped: nothing stale left behind

    def test_card_rows_colours_and_tooltips(self):
        import time
        from prism_monitor.widget import MonitorWidget, level
        with tempfile.TemporaryDirectory() as d:
            settings = Settings(Path(d) / 's.json')
            self.assertIn('battery', settings['visible'])
            settings['style'] = 'card'
            w = MonitorWidget(settings)
            now = time.monotonic()
            w.update_snapshot(Snapshot(info={'batteries_scanned': False}))
            card = next(c for c in w.cards if c['group'] == 'battery')
            self.assertEqual(card['secondary'], [('battery', 'Devices')])
            self.assertEqual(w.value_text('battery', now)[0], '…')
            snap = Snapshot(info={'batteries_scanned': True}, batteries={
                'pad': ('DualSense', Reading(15, now, 'usb', 1.0), ''),
                'kb': ('Keychron K2 Bluetooth Keyboard', Reading(8, now, 'windows', 0.0), ''),
                'xbox': ('Xbox controller 1', Reading(60, now, 'xinput', 0.0), 'Medium')})
            w.update_snapshot(snap)
            card = next(c for c in w.cards if c['group'] == 'battery')
            self.assertEqual([k for k, _ in card['secondary']], ['bat:pad', 'bat:kb', 'bat:xbox'])
            self.assertEqual(w.value_text('bat:pad', now)[0], '15%')
            self.assertEqual(w.value_text('bat:xbox', now)[0], 'Medium')
            self.assertIsNone(level('bat:pad', snap.batteries['pad'][1]))      # charging: no warning
            self.assertEqual(level('bat:kb', snap.batteries['kb'][1]), 'hot')
            w.grab()
            tips = [text for _, text in w.hits if text.startswith('DualSense')]
            self.assertTrue(tips and 'Charging' in tips[0])
            w.update_snapshot(Snapshot(info={'batteries_scanned': True}))
            self.assertEqual(w.value_text('battery', now)[0], 'none')


if __name__ == '__main__':
    unittest.main()

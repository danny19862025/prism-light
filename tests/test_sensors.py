# SPDX-License-Identifier: GPL-3.0-only
import io
import os
import time
import unittest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from prism.sensors import tctl, FpsMonitor, SensorHub, RyzenTemp, TCTL_SMN

app = QApplication.instance() or QApplication([])

HEADER = 'Application,ProcessID,SwapChainAddress,Runtime,SyncInterval,PresentFlags,Dropped,TimeInSeconds,msBetweenPresents\n'


class FakeProcess:
    def __init__(self, lines):
        self.stdout = io.StringIO(''.join(lines))
        self.code = None
    def poll(self): return self.code
    def terminate(self): self.code = 0
    def wait(self, timeout=None): return 0


def wait(cond, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond(): time.sleep(.02)
    return cond()


class TctlTests(unittest.TestCase):
    def test_formula_and_offsets(self):
        self.assertEqual(tctl((45*8) << 21), 45)
        self.assertEqual(tctl(((94*8) << 21) | 0x80000), 45)     # RANGE_SEL
        self.assertEqual(tctl(((94*8) << 21) | 0x30000), 45)     # TJ_SEL both bits
        self.assertEqual(tctl(((45*8) << 21) | 0x10000), 45)     # one TJ bit is not the flag
    def test_reader_uses_smn_register_under_lock(self):
        class Port:
            locked = 0
            def lock(self): self.locked += 1
            def unlock(self): self.locked -= 1
            def execute(self, name, args, out):
                assert (name, args, out) == ('ioctl_read_smn', (TCTL_SMN,), 1)
                return (52*8) << 21
        port = Port()
        self.assertEqual(RyzenTemp(port).read(), {'cpu_temp': 52})
        self.assertEqual(port.locked, 0)


class FpsTests(unittest.TestCase):
    def test_foreground_game_rate_ignores_desktop_compositor(self):
        lines = [HEADER] + ['game.exe,4242,0x1,DXGI,0,0,0,1.0,6.9\n']*144 + ['dwm.exe,10,0x2,DXGI,1,0,0,1.0,16.6\n']*60
        mon = FpsMonitor(launcher=lambda: FakeProcess(lines), foreground=lambda: 4242)
        mon.start()
        self.assertTrue(wait(lambda: not mon.running))
        self.assertEqual(mon.read(), {'fps': 144, 'fps_app': 'game.exe'})
    def test_falls_back_to_busiest_when_foreground_has_no_frames(self):
        lines = [HEADER] + ['a.exe,1,0,DXGI,0,0,0,1,1\n']*30 + ['b.exe,2,0,DXGI,0,0,0,1,1\n']*90
        mon = FpsMonitor(launcher=lambda: FakeProcess(lines), foreground=lambda: 999)
        mon.start(); wait(lambda: not mon.running)
        self.assertEqual(mon.read()['fps_app'], 'b.exe')
    def test_no_header_means_permission_problem(self):
        mon = FpsMonitor(launcher=lambda: FakeProcess(['error: access denied\n']), foreground=lambda: None)
        mon.start(); self.assertTrue(wait(lambda: not mon.running))
        self.assertIn('administrator', mon.error)
        self.assertEqual(mon.read(), {'fps': None, 'fps_app': None})
    def test_missing_program_is_reported(self):
        mon = FpsMonitor(executable='does-not-exist.exe')
        mon.start(); self.assertFalse(mon.running); self.assertIn('missing', mon.error)


class HubTests(unittest.TestCase):
    def test_merges_readers_and_survives_failures(self):
        class Gpu:
            def read(self): return {'gpu_temp': 61, 'gpu_load': 88}
            def close(self): pass
        def no_cpu(): raise OSError('needs administrator rights')
        hub = SensorHub(gpu=Gpu, cpu=no_cpu, fps=FpsMonitor(launcher=lambda: FakeProcess([HEADER]), foreground=lambda: None))
        hub.interval = .05; hub.start()
        self.assertTrue(wait(lambda: hub.read().get('gpu_temp') == 61))
        self.assertIn('administrator', hub.notes['cpu'])
        hub.stop()
    def test_fps_only_runs_when_wanted(self):
        started = []
        def launch():
            started.append(1)
            proc = FakeProcess([HEADER]); proc.stdout = io.StringIO(HEADER)
            return proc
        hub = SensorHub(gpu=lambda: (_ for _ in ()).throw(OSError('x')), cpu=lambda: (_ for _ in ()).throw(OSError('x')),
                        fps=FpsMonitor(launcher=launch, foreground=lambda: None))
        hub.want_fps(False); self.assertEqual(started, [])
        hub.want_fps(True); self.assertEqual(started, [1])
        hub.stop()


class BlockTests(unittest.TestCase):
    def test_gaming_blocks_render_with_and_without_sensors(self):
        from prism import aio
        full = {'cpu': 30, 'gpu_load': 97, 'cpu_temp': 71.5, 'gpu_temp': 63, 'fps': 165, 'fps_app': 'game.exe'}
        for name in ('Game HUD', 'Dual gauge', 'Sweep gauge', 'Sensor bars', 'Hex panels', 'Bold split'):
            for data in (full, {}, {'temperature': 50}):
                aio.draw(name, 160, 1.0, {}, data)
        for kind in ('CPU temperature', 'GPU temperature', 'FPS'):
            aio.draw('Plasma flow', 160, 1.0, {'overlay': kind}, full)
        self.assertEqual(aio.temp_of({'temperature': 50}, 'cpu_temp'), 50)   # board fallback
        self.assertEqual(aio.temp_of({'temperature': 50, 'cpu_temp': 70}, 'cpu_temp'), 70)


class ChartTests(unittest.TestCase):
    def test_points_follow_mode_floor_and_render(self):
        from prism.fan_chart import FanCurveChart
        chart = FanCurveChart(); chart.resize(700, 280)
        base = dict(key='8696:0', label='fan 1', fixed=10, pump=False, rpm=900, duty=40, target=None, note='', writable=True)
        chart.show_header(dict(base, mode='Auto (BIOS)'), 45); self.assertEqual(chart.points(), [])
        chart.show_header(dict(base, mode='Fixed'), 45); self.assertEqual(chart.points(), [(20, 20), (100, 20)])
        chart.show_header(dict(base, mode='Balanced', pump=True, target=70), 45)
        self.assertTrue(all(d >= 70 for _, d in chart.points()))
        self.assertEqual(chart.points()[-1], (100, 100))
        chart.grab()   # paints without errors
        chart.show_header(None, None); chart.grab()


if __name__ == '__main__':
    unittest.main()

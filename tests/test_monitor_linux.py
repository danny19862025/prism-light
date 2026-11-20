# SPDX-License-Identifier: GPL-3.0-only
"""Linux counterparts of Prism Monitor's Windows features."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from prism_monitor.fps import FrameTracker, LinuxFrameReceiver

LINUX = sys.platform.startswith('linux')

UPOWER_DUMP = '''Device: /org/freedesktop/UPower/devices/gaming_input_ps_controller_battery_a0o5ao9eo2ao3ao1
  native-path:          ps-controller-battery-a0:5a:9e:23:01
  model:                DualSense Wireless Controller
  serial:               a0:5a:9e:23:01
  power supply:         no
  has history:          yes
  gaming-input
    present:             yes
    state:               charging
    percentage:          65%
    icon-name:          'battery-good-charging-symbolic'

Device: /org/freedesktop/UPower/devices/headset_dev_11_22
  native-path:          /org/bluez/hci0/dev_11_22
  model:                WH-1000XM5
  power supply:         no
  headset
    present:             yes
    state:               discharging
    percentage:          40%

Device: /org/freedesktop/UPower/devices/mouse_hidpp_battery_0
  native-path:          hidpp_battery_0
  model:                G502 LIGHTSPEED
  serial:               1234abcd
  power supply:         no
  mouse
    present:             yes
    state:               discharging
    battery-level:       high
    percentage:          55%

Device: /org/freedesktop/UPower/devices/battery_BAT0
  native-path:          BAT0
  power supply:         yes
  battery
    present:             yes
    state:               fully-charged
    percentage:          100%

Device: /org/freedesktop/UPower/devices/line_power_AC
  native-path:          AC
  power supply:         yes
  line-power
    online:              yes

Device: /org/freedesktop/UPower/devices/DisplayDevice
  power supply:         yes
  battery
    present:             yes
    percentage:          100%

Daemon:
  daemon-version:  1.91.5
  on-battery:      no
'''


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux abstract Unix sockets and uids')
class ReceiverTests(unittest.TestCase):
    def test_present_intervals_per_swapchain(self):
        tracker = FrameTracker(own_pid=999, foreground=lambda: None)
        tracker.busiest_when_unknown = True
        receiver = LinuxFrameReceiver(tracker)
        for i in range(61):
            receiver.handle(4321, f'PFPS1\tabc\t{int((50 + i / 60) * 1e9)}\tgame.exe'.encode(), now=100.0)
        receiver.handle(4321, b'garbage', now=100.0)
        receiver.handle(4321, b'PFPS1\tabc\tnot-a-number\tgame.exe', now=100.0)
        values, app = tracker.read('', now=100.0)
        self.assertEqual(app, 'game.exe')
        self.assertAlmostEqual(values['fps'].value, 60.0, places=3)
        self.assertAlmostEqual(values['frametime'].value, 1000 / 60, places=3)
        self.assertEqual(values['fps'].source, 'vklayer')

    @unittest.skipUnless(LINUX, 'abstract Unix sockets are Linux-only')
    def test_socket_round_trip_checks_sender(self):
        import socket
        import time
        tracker = FrameTracker(own_pid=999, foreground=lambda: None)
        tracker.busiest_when_unknown = True
        name = f'prism-monitor-fps-test-{os.getpid()}'
        receiver = LinuxFrameReceiver(tracker, name=name)
        self.assertTrue(receiver.start(), receiver.error)
        try:
            sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            start = time.monotonic()
            for i in range(20):
                sender.sendto(f'PFPS1\t1\t{int((start + i / 100) * 1e9)}\tunit-test'.encode(), '\0' + name)
            deadline = time.monotonic() + 3
            while not receiver.got_header and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(receiver.got_header)
            self.assertEqual([n for _, n, _ in tracker.presenting()], ['unit-test'])
            self.assertEqual(tracker.presenting()[0][0], os.getpid())   # pid from the kernel, not the message
            sender.close()
        finally:
            receiver.stop()
        self.assertFalse(receiver.running)


@unittest.skipUnless(LINUX, 'Linux implementation')
class LinuxPlatformTests(unittest.TestCase):
    def test_upower_parsing(self):
        from prism_monitor.batteries import parse_upower
        found = {b.name: b for b in parse_upower(UPOWER_DUMP)}
        self.assertEqual(set(found), {'DualSense Wireless Controller', 'WH-1000XM5', 'G502 LIGHTSPEED', 'This PC'})
        pad = found['DualSense Wireless Controller']
        self.assertEqual((pad.percent, pad.state, pad.source), (65.0, 'charging', 'upower'))
        self.assertEqual(found['G502 LIGHTSPEED'].text, 'High')       # coarse level, not a percentage
        self.assertEqual((found['This PC'].id, found['This PC'].state), ('system', 'full'))

    def test_autostart_entry(self):
        from prism_monitor import autostart
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {'XDG_CONFIG_HOME': d}):
            self.assertFalse(autostart.enabled())
            self.assertEqual(autostart.sync(True), (True, ''))
            text = (Path(d) / 'autostart' / 'prism-monitor.desktop').read_text()
            self.assertIn('monitor_main.py', text)
            self.assertTrue(autostart.enabled())
            self.assertEqual(autostart.sync(False), (True, ''))
            self.assertFalse(autostart.enabled())
        self.assertEqual(autostart._exec_arg('/a b/"x"'), '"/a b/\\"x\\""')

    @unittest.skipUnless(LINUX, 'XDG autostart is Linux-only')
    def test_launch_at_login_flag(self):
        """The installers' --launch-at-login saves the setting and writes the autostart entry."""
        import subprocess
        root = Path(__file__).resolve().parent.parent
        # A private instance name so the test never reaches a Prism Monitor the user is running.
        code = ('import os, sys; from prism_monitor import app; orig = app.single_instance; '
                'app.single_instance = lambda n, m=b"show": orig(f"{n}-test-{os.getpid()}", m); '
                'sys.exit(app.main(sys.argv[1:]))')
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / 'settings.json'
            env = dict(os.environ, XDG_CONFIG_HOME=d, QT_QPA_PLATFORM='offscreen', PRISM_MONITOR_SMOKE_MS='300')
            env['XDG_RUNTIME_DIR'] = d
            subprocess.run([sys.executable, '-c', code, '--smoke-test', '--launch-at-login', '--native-widget',
                            '--config', str(config)],
                           cwd=root, env=env, check=True, capture_output=True, timeout=60)
            saved = json.loads(config.read_text())
            self.assertTrue(saved['launch_at_login'])
            self.assertEqual((saved['native_widget'], saved['shown']), (True, False))   # tray only
            self.assertIn('monitor_main.py', (Path(d) / 'autostart' / 'prism-monitor.desktop').read_text())
            self.assertFalse((Path(d) / 'prism-monitor' / 'widget.json').exists())     # removed on quit

    @unittest.skipUnless(LINUX, 'the desktop widgets are Linux-only')
    def test_widget_menu_commands(self):
        """The Plasma widget's right-click menu reaches a running monitor through --do."""
        import subprocess, time
        root = Path(__file__).resolve().parent.parent
        name = f'test-{os.getpid()}'
        code = ('import os, sys; from prism_monitor import app; orig = app.single_instance; '
                f'app.single_instance = lambda n, m=b"show": orig(n + "-{name}", m); '
                'sys.exit(app.main(sys.argv[1:]))')
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / 'settings.json'
            env = dict(os.environ, XDG_CONFIG_HOME=d, XDG_RUNTIME_DIR=d, QT_QPA_PLATFORM='offscreen',
                       PRISM_MONITOR_SMOKE_MS='6000')
            first = subprocess.Popen([sys.executable, '-c', code, '--smoke-test', '--config', str(config)],
                                     cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                widget = Path(d) / 'prism-monitor' / 'widget.json'
                for _ in range(100):
                    if widget.exists():
                        break
                    time.sleep(0.05)
                for action in ('section:fans:on', 'hero:gpu', 'section:battery:off'):
                    subprocess.run([sys.executable, '-c', code, '--do', action, '--config', str(config)],
                                   cwd=root, env=env, check=True, timeout=30, capture_output=True)
                for _ in range(60):     # the running monitor applies and publishes the change
                    state = json.loads(widget.read_text()).get('state', {})
                    if state.get('hero') == 'gpu' and not state['sections']['battery']:
                        break
                    time.sleep(0.1)
                self.assertEqual(state['hero'], 'gpu')
                self.assertTrue(state['sections']['fans'])
                self.assertFalse(state['sections']['battery'])
            finally:
                first.terminate()
                first.wait(10)

    def test_widget_document(self):
        """What the Plasma and GNOME widgets read: the Poster's rows, formatted and coloured."""
        from PySide6.QtGui import QColor
        from prism_monitor import poster as P, publish
        content = P.Content(number='63', unit='°C', headline='CPU', subline='35% LOAD', groups=[
            P.Group('SYSTEM', [P.Row('MEMORY', '9.4 / 32 GB', key='ram_used'),
                               P.Row('SWAP', '0.0 / 12 GB', P.WARN, key='swap_used')]),
            P.Group('BATTERIES', [P.Row('PAD', '35%', None, 0.35, key='bat:pad')])])
        doc = publish.document(content, P.Ink(QColor('#ffffff'), QColor('#8fd8ff'), QColor('#000000')), 1.0)
        self.assertEqual((doc['version'], doc['number'], doc['unit'], doc['accent']), (1, '63', '°C', '#8fd8ff'))
        rows = doc['groups'][0]['rows']
        self.assertEqual([r['label'] for r in rows], ['MEMORY', 'SWAP'])
        self.assertEqual((rows[0]['color'], rows[1]['color']), (None, '#ffd166'))
        self.assertEqual(doc['groups'][1]['rows'][0]['level'], 0.35)
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'prism-monitor' / 'widget.json'
            out = publish.Publisher(target)
            out.write(doc)
            self.assertEqual(json.loads(target.read_text())['groups'], doc['groups'])
            self.assertEqual(target.parent.stat().st_mode & 0o777, 0o700)
            out.clear()
            self.assertFalse(target.exists())

    def test_process_names(self):
        from prism_monitor import linuxapi
        with tempfile.TemporaryDirectory() as d:
            for pid, cmdline, comm in (('10', b'Z:\\Games\\Elden\\eldenring.exe\0-x\0', 'eldenring.exe'),
                                       ('11', b'/usr/bin/obs\0', 'obs'),
                                       ('12', b'/usr/bin/python3\0/opt/tool.py\0', 'tool')):
                (Path(d) / pid).mkdir()
                (Path(d) / pid / 'cmdline').write_bytes(cmdline)
                (Path(d) / pid / 'comm').write_text(comm + '\n')
            self.assertEqual(linuxapi.running_processes(d), {10: 'eldenring.exe', 11: 'obs', 12: 'tool'})

    def test_layer_install_and_remove(self):
        from prism_monitor import vklayer
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {'XDG_DATA_HOME': d}):
            source = Path(d) / 'built.so'
            self.assertEqual(vklayer.status(source), 'unbuilt')
            source.write_bytes(b'v1')
            self.assertEqual(vklayer.status(source), 'missing')
            self.assertEqual(vklayer.install(source), (True, ''))
            self.assertEqual(vklayer.status(source), 'installed')
            data = json.loads(vklayer.manifest_path().read_text())
            self.assertEqual(data['layer']['library_path'], str(vklayer.installed_library()))
            self.assertEqual(data['layer']['disable_environment'], {'DISABLE_PRISM_FPS': '1'})
            source.write_bytes(b'v2')
            self.assertEqual(vklayer.status(source), 'outdated')
            vklayer.install(source)
            self.assertEqual(vklayer.installed_library().read_bytes(), b'v2')
            self.assertEqual(vklayer.uninstall(), (True, ''))
            self.assertFalse(vklayer.manifest_path().exists())


if __name__ == '__main__':
    unittest.main()

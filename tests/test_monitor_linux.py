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

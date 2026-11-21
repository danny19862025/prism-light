# SPDX-License-Identifier: GPL-3.0-only
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from prism import autostart, session

NS = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}


class TaskTests(unittest.TestCase):
    def xml(self):
        text = autostart.task_xml()
        return ET.fromstring(text.split('?>', 1)[1])

    def test_task_runs_elevated_hidden_without_time_or_battery_limits(self):
        root = self.xml()
        self.assertEqual(root.find('.//t:RunLevel', NS).text, 'HighestAvailable')
        self.assertEqual(root.find('.//t:ExecutionTimeLimit', NS).text, 'PT0S')        # never stopped after 3 days
        self.assertEqual(root.find('.//t:DisallowStartIfOnBatteries', NS).text, 'false')
        self.assertEqual(root.find('.//t:StopIfGoingOnBatteries', NS).text, 'false')
        self.assertEqual(root.find('.//t:MultipleInstancesPolicy', NS).text, 'IgnoreNew')
        self.assertIsNotNone(root.find('.//t:LogonTrigger', NS))
        self.assertTrue(root.find('.//t:Arguments', NS).text.endswith('--background'))
        self.assertTrue(Path(root.find('.//t:Command', NS).text).name.lower().startswith(('pythonw', 'python', 'prismlight')))

    def test_special_characters_in_paths_are_escaped(self):
        with patch.object(autostart, 'command', return_value=('C:\\A & B\\pythonw.exe', '"C:\\A & B\\main.py" --background', 'C:\\A & B')):
            self.xml()   # parses: '&' was escaped

    def test_enable_disable_and_errors(self):
        calls = []
        def fake(args):
            calls.append(args)
            if args[0] == '/Create':
                self.assertTrue(Path(args[args.index('/XML') + 1]).read_text(encoding='utf-16').startswith('<?xml'))
            return subprocess.CompletedProcess(args, 0, '', '')
        with patch.object(autostart.sys, 'platform', 'win32'), patch.object(autostart, 'run', side_effect=fake):
            autostart.enable()
            self.assertEqual(calls[0][:4], ['/Create', '/F', '/TN', 'Prism Light'])
            self.assertTrue(autostart.enabled())
            autostart.disable()
            self.assertEqual(calls[-1], ['/Delete', '/F', '/TN', 'Prism Light'])
        denied = subprocess.CompletedProcess([], 1, '', 'ERROR: Access is denied.')
        with patch.object(autostart.sys, 'platform', 'win32'), patch.object(autostart, 'run', return_value=denied):
            with self.assertRaisesRegex(RuntimeError, 'administrator'):
                autostart.enable()
            self.assertFalse(autostart.enabled())


class SessionTests(unittest.TestCase):
    def test_roundtrip_and_rejects_bad_values(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'session.json'
            self.assertEqual(session.load(path), session.DEFAULTS)
            data = dict(session.DEFAULTS, effects_playing=True, display_on=True, fans_active=True,
                        scene={'effect': 'Rainbow wave'}, fan_headers={'8696:0': {'mode': 'Quiet', 'fixed': 40, 'pump': False}})
            session.save(path, data)
            self.assertEqual(session.load(path), data)
            path.write_text('{"effects_playing":"yes","scene":[1],"fan_headers":{"x":{"mode":3,"fixed":"9","pump":true}}}')
            got = session.load(path)
            self.assertFalse(got['effects_playing']); self.assertIsNone(got['scene'])
            self.assertEqual(got['fan_headers'], {'x': {'pump': True}})
            path.write_text('not json'); self.assertEqual(session.load(path), session.DEFAULTS)


if __name__ == '__main__':
    unittest.main()

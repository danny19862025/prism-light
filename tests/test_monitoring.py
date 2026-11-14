# SPDX-License-Identifier: GPL-3.0-only
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from PySide6.QtWidgets import QApplication
from prism.monitoring import SystemWidgets


class MonitoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_history_is_bounded_and_summary_uses_visible_samples(self):
        w=SystemWidgets()
        try:
            for i in range(80):w.update_metrics({'cpu':i,'memory':42})
            self.assertEqual(len(w.history.samples),60)
            self.assertEqual(w.history.samples[0],20)
            self.assertEqual(w.cpu.value,79)
            self.assertEqual(w.memory.value,42)
            self.assertIn('peak 79%',w.summary.text())
        finally:w.close()

    def test_failure_does_not_report_zero_or_append_a_fake_sample(self):
        w=SystemWidgets()
        try:
            w.update_metrics({'cpu':60,'memory':35})
            w.update_metrics({'error':'Sensor unavailable','cpu':0,'memory':0})
            self.assertIsNone(w.cpu.value);self.assertIsNone(w.memory.value)
            self.assertEqual(list(w.history.samples),[60])
            self.assertIn('Sensor unavailable',w.summary.text())
            w.update_metrics({'cpu':25,'memory':45})
            self.assertEqual(w.cpu.value,25)
            self.assertNotIn('unavailable',w.summary.text())
        finally:w.close()

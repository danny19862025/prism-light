# SPDX-License-Identifier: GPL-3.0-only
"""Free Qt widgets on the GUI thread after each test.

Closed widgets that sit in Python reference cycles wait for the garbage
collector, which may run on any thread. If it runs on a background worker
(AIO sender, sensor poller, battery monitor), Qt objects are destroyed off
the GUI thread: "QObject::killTimer: Timers cannot be stopped from another
thread" and, on Linux, a segmentation fault. Collect them here instead.
"""
import gc
import unittest


def collect():
    from PySide6.QtCore import QCoreApplication, QEvent
    if QCoreApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
    gc.collect()


class QtTestCase(unittest.TestCase):
    def tearDown(self):
        try:
            super().tearDown()
        finally:
            collect()

# SPDX-License-Identifier: GPL-3.0-only
"""Keep-awake menu (tray and widget button), time picker and the after-action countdown."""
import datetime

from PySide6.QtCore import Qt, QTime, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QMenu, QPushButton,
                               QTimeEdit, QVBoxLayout)

from . import winapi
from .awake import AFTER

DURATIONS = ((15, '15 minutes'), (30, '30 minutes'), (60, '1 hour'), (120, '2 hours'), (240, '4 hours'),
             (480, '8 hours'))
SHELL = {'explorer.exe', 'applicationframehost.exe', 'textinputhost.exe', 'shellexperiencehost.exe',
         'searchhost.exe', 'startmenuexperiencehost.exe', 'systemsettings.exe', 'lockapp.exe'}


def open_apps(own_pid=None):
    """Sorted exe names of apps with a visible window, for "while an app runs"."""
    procs = winapi.running_processes()
    names = set()
    for pid in winapi.windowed_pids():
        name = procs.get(pid)
        if name and pid != own_pid and name.casefold() not in SHELL:
            names.add(name)
    return sorted(names, key=str.casefold)


def run_after_action(action):
    if action == 'sleep':
        return winapi.sleep_now(False)
    if action == 'hibernate':
        return winapi.sleep_now(True)
    if action == 'shutdown':
        return winapi.shutdown_now()
    if action == 'lock':
        return winapi.lock_now()
    return False


class AwakeMenu(QMenu):
    """Rebuilt each time it opens so the status line and app list are current."""
    settings_requested = Signal()
    error = Signal(str)

    def __init__(self, awake, own_pid=None, parent=None):
        super().__init__('Keep awake', parent)
        self.awake = awake
        self.own_pid = own_pid
        self.aboutToShow.connect(self.rebuild)
        self.rebuild()

    def start(self, mode, **kw):
        message = self.awake.start(mode, **kw)
        if message:
            self.error.emit(message)

    def rebuild(self):
        self.clear()
        awake = self.awake
        status = self.addAction(('☕  ' if awake.active else '') + awake.describe().split('\n')[0])
        status.setEnabled(False)
        if awake.session is not None:
            self.addAction('Turn off keep awake', awake.stop)
        self.addSeparator()
        self.addAction('Indefinitely', lambda: self.start('indefinite'))
        timed = self.addMenu('For a while')
        for minutes, label in DURATIONS:
            timed.addAction(label, lambda m=minutes: self.start('duration', minutes=m))
        timed.addSeparator()
        timed.addAction('Custom…', self.custom_duration)
        self.addAction('Until a time…', self.until_time)
        self.addSeparator()
        self.addAction('Until downloads finish', lambda: self.start('downloads'))
        self.addAction('Until CPU work finishes (renders, builds)', lambda: self.start('cpu'))
        apps = self.addMenu('While an app runs')
        names = []
        try:
            names = open_apps(self.own_pid)
        except Exception:
            pass
        for name in names:
            apps.addAction(name, lambda n=name: self.start('process', processes=(n,)))
        if not names:
            none = apps.addAction('No open apps found')
            none.setEnabled(False)
        apps.addSeparator()
        apps.addAction('Other app or background process…', self.settings_requested.emit)
        self.addSeparator()
        display = QAction('Keep the display on too', self, checkable=True)
        display.setChecked(awake.session.display if awake.session else awake.settings['awake_display'])
        display.toggled.connect(awake.set_display)
        self.addAction(display)
        after = self.addMenu('When finished')
        group = QActionGroup(after)
        current = awake.session.after if awake.session else awake.after
        for key, label in AFTER.items():
            action = QAction(label, after, checkable=True)
            action.setChecked(key == current)
            action.triggered.connect(lambda _=False, k=key: awake.set_after(k))
            group.addAction(action)
            after.addAction(action)
        self.addAction('Keep-awake settings…', self.settings_requested.emit)

    def custom_duration(self):
        minutes, ok = QInputDialog.getInt(None, 'Keep awake', 'Keep the PC awake for (minutes):',
                                          self.awake.settings['awake_minutes'], 1, 24 * 60)
        if ok:
            self.awake.settings['awake_minutes'] = minutes
            self.start('duration', minutes=minutes)

    def until_time(self):
        dialog = TimeDialog()
        if dialog.exec() == QDialog.Accepted:
            self.start('until_time', until=dialog.target())


class TimeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Keep awake until')
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Keep the PC awake until (tomorrow if the time has passed today):'))
        self.edit = QTimeEdit(QTime.currentTime().addSecs(3600))
        self.edit.setDisplayFormat('HH:mm')
        layout.addWidget(self.edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def target(self):
        t = self.edit.time()
        now = datetime.datetime.now()
        when = now.replace(hour=t.hour(), minute=t.minute(), second=0, microsecond=0)
        if when <= now:
            when += datetime.timedelta(days=1)
        return when.timestamp()


class CountdownDialog(QDialog):
    """Cancellable countdown before an after-action; the action runs only if nobody cancels."""
    VERBS = {'sleep': 'Sleeping', 'hibernate': 'Hibernating', 'shutdown': 'Shutting down', 'lock': 'Locking'}

    def __init__(self, action, reason, seconds=60, runner=run_after_action, parent=None):
        super().__init__(parent)
        self.action, self.left, self.runner = action, seconds, runner
        self.settled = False
        self.setWindowTitle('Prism Monitor — keep awake finished')
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(reason))
        self.label = QLabel()
        font = self.label.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        font.setBold(True)
        self.label.setFont(font)
        layout.addWidget(self.label)
        row = QHBoxLayout()
        cancel = QPushButton('Cancel')
        cancel.setDefault(True)
        cancel.clicked.connect(self.reject)
        now = QPushButton(f'{AFTER[action]} now')
        now.clicked.connect(self.fire)
        row.addStretch()
        row.addWidget(now)
        row.addWidget(cancel)
        layout.addLayout(row)
        self.timer = QTimer(self, interval=1000, timeout=self.step)
        self.timer.start()
        self.show_left()

    def show_left(self):
        self.label.setText(f'{self.VERBS.get(self.action, AFTER[self.action])} in {self.left} s')

    def step(self):
        if self.settled:
            return
        self.left -= 1
        if self.left <= 0:
            self.fire()
        else:
            self.show_left()

    def fire(self):
        if self.settled:
            return
        self.settled = True
        self.timer.stop()
        self.accept()
        self.runner(self.action)

    def reject(self):
        self.settled = True   # a cancelled countdown can never fire, even from a queued tick
        self.timer.stop()
        super().reject()

# SPDX-License-Identifier: GPL-3.0-only
"""Prism Monitor application: widget, tray icon, settings and sensor plumbing."""
import argparse
import getpass
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import autostart
from .awake import AFTER, KeepAwake
from .awake_ui import AwakeMenu, CountdownDialog
from .batteries import BatteryMonitor, scan as scan_batteries
from .collector import Collector
from .fps import FrameTracker, LocalPresentMon
from .host import HostClient, vendor_dir
from .settings import Settings
from .settings_window import SettingsWindow
from .widget import MonitorWidget

APP_NAME = 'Prism Monitor'


def make_icon(accent, awake=False):
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(20, 23, 32))
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 14, 14)
    p.setBrush(QColor(accent))
    for i, h in enumerate((18, 34, 26, 44)):
        p.drawRoundedRect(QRectF(11 + i * 11, 52 - h, 8, h), 3, 3)
    if awake:   # keep-awake badge
        p.setBrush(QColor('#ffb547'))
        p.setPen(QColor(20, 23, 32))
        p.drawEllipse(QRectF(40, 2, 22, 22))
    p.end()
    return QIcon(pixmap)


class PrismMonitor:
    def __init__(self, app, settings, start_hidden=False):
        self.app = app
        self.settings = settings
        self.tracker = FrameTracker()
        self.host = HostClient(self.tracker)
        self.host.interval_ms = round(settings['interval'] * 1000)
        self.collector = Collector(self.host, self.tracker, settings['interval'])
        self.collector.fps_target = settings['fps_process']
        self.collector.cpu_temp_sensor = settings['cpu_temp_sensor']
        self.batteries = BatteryMonitor(scanner=lambda: scan_batteries(self.settings['battery_wake_bt']))
        self.collector.batteries = self.batteries
        self.presentmon = LocalPresentMon(vendor_dir() / 'presentmon' / 'PresentMon.exe', self.tracker)
        self.fps_mode = 'local'
        self.fps_retry_at = 0.0
        self.widget = MonitorWidget(settings)
        self.awake = KeepAwake(settings)
        self.countdown = None
        self.settings_window = SettingsWindow(settings, self.tracker, self.awake)
        self.save_timer = QTimer(singleShot=True, interval=600, timeout=self.save)
        self.screen_timer = QTimer(singleShot=True, interval=700, timeout=self.ensure_visible)
        self.fps_timer = QTimer(interval=2000, timeout=self.supervise_fps)
        self.last_snapshot = None
        self.closing = False

        self.widget.settings_requested.connect(self.show_settings)
        self.widget.context_requested.connect(lambda pos: self.menu.popup(pos))
        self.widget.moved.connect(self.remember_position)
        self.awake.changed.connect(self.on_awake_changed)
        self.awake.finished.connect(self.on_awake_finished)
        self.settings_window.changed.connect(self.setting_changed)
        self.settings_window.helper_start.connect(self.start_helper)
        self.settings_window.helper_stop.connect(self.host.stop)
        self.settings_window.helper_setup.connect(self.host.setup_task)
        self.settings_window.helper_remove.connect(self.host.remove_task)
        self.host.setup_finished.connect(self.on_setup_finished)
        self.collector.snapshot.connect(self.on_snapshot)
        self.host.state_changed.connect(self.on_helper_state)
        self.host.owner_hwnd = lambda: int(self.widget.winId()) if self.widget.isVisible() else None

        self.build_tray()
        for screen in app.screens():
            self.watch_screen(screen)
        app.screenAdded.connect(self.watch_screen)
        app.screenAdded.connect(lambda *_: self.screen_timer.start())
        app.screenRemoved.connect(lambda *_: self.screen_timer.start())
        app.primaryScreenChanged.connect(lambda *_: self.screen_timer.start())
        app.aboutToQuit.connect(self.shutdown)

        self.place_initially()
        if settings['shown'] and not start_hidden:
            self.widget.show()
        self.collector.start()
        self.supervise_batteries()
        self.supervise_fps()
        self.fps_timer.start()
        self.hint_pending = False
        if settings['helper_task']:
            QTimer.singleShot(800, self.start_automatic_helper)   # installed task: no prompt
        elif settings['helper_autostart']:
            QTimer.singleShot(1500, self.start_helper)
        elif not settings['setup_hint_shown']:
            QTimer.singleShot(4000, self.show_setup_hint)
        if settings.error:
            self.tray.showMessage(APP_NAME, settings.error, QSystemTrayIcon.Warning, 6000)
        autostart.sync(settings['launch_at_login'])

    # -- tray ---------------------------------------------------------------
    def build_tray(self):
        self.tray = QSystemTrayIcon(make_icon(self.settings['accent']))
        self.tray.setToolTip(APP_NAME)
        menu = self.menu = QMenu()
        self.act_show = menu.addAction('Hide widget', self.toggle_widget)
        menu.addAction('Settings…', self.show_settings)
        menu.addSeparator()
        self.awake_menu = AwakeMenu(self.awake, os.getpid(), menu)
        self.awake_menu.settings_requested.connect(lambda: self.show_settings('awake'))
        self.awake_menu.error.connect(lambda m: self.tray.showMessage('Keep awake', m, QSystemTrayIcon.Warning, 5000))
        self.widget.awake_requested.connect(lambda pos: self.awake_menu.popup(pos))
        menu.addMenu(self.awake_menu)
        menu.addSeparator()
        self.act_lock = self.check_action('Lock position', 'locked')
        self.act_click = self.check_action('Click-through', 'click_through')
        self.act_top = self.check_action('Always on top', 'always_on_top')
        look = menu.addMenu('Look')
        look_group = QActionGroup(look)
        self.act_style = {}
        for value, text in (('poster', 'Poster'), ('card', 'Card')):
            action = QAction(text, look, checkable=True)
            action.setChecked(self.settings['style'] == value)
            action.triggered.connect(lambda _=False, v=value: self.set_layout(v, 'style'))
            look_group.addAction(action)
            look.addAction(action)
            self.act_style[value] = action
        layout = self.layout_menu = menu.addMenu('Layout')
        group = QActionGroup(layout)
        self.act_layout = {}
        for value, text in (('horizontal', 'Horizontal strip'), ('vertical', 'Vertical stand')):
            action = QAction(text, layout, checkable=True)
            action.setChecked(self.settings['orientation'] == value)
            action.triggered.connect(lambda _=False, v=value: self.set_layout(v))
            group.addAction(action)
            layout.addAction(action)
            self.act_layout[value] = action
        menu.addSeparator()
        self.act_helper = menu.addAction('Start sensor helper (administrator)…', self.toggle_helper)
        self.act_setup = menu.addAction('Set up automatic CPU sensors (one-time)…', self.host.setup_task)
        menu.addAction('Reset widget position', self.reset_position)
        menu.addSeparator()
        menu.addAction('Quit Prism Monitor', self.app.quit)
        menu.aboutToShow.connect(self.sync_menu)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.messageClicked.connect(self.tray_message_clicked)
        self.tray.show()

    def check_action(self, text, key):
        action = QAction(text, self.menu, checkable=True)
        action.setChecked(self.settings[key])
        action.toggled.connect(lambda on, k=key: self.set_and_apply(k, on))
        self.menu.addAction(action)
        return action

    def sync_menu(self):
        self.act_show.setText('Hide widget' if self.widget.isVisible() else 'Show widget')
        for action, key in ((self.act_lock, 'locked'), (self.act_click, 'click_through'), (self.act_top, 'always_on_top')):
            action.blockSignals(True)
            action.setChecked(self.settings[key])
            action.blockSignals(False)
        for value, action in self.act_layout.items():
            action.setChecked(self.settings['orientation'] == value)
        for value, action in self.act_style.items():
            action.setChecked(self.settings['style'] == value)
        self.layout_menu.setEnabled(self.settings['style'] == 'card')
        connected = self.host.state in ('connected', 'starting')
        automatic = self.settings['helper_task']
        self.act_helper.setText('Stop sensor helper' if connected else
                                'Start sensor helper' if automatic else 'Start sensor helper (administrator)…')
        from .host import task_status
        status = task_status()
        self.act_setup.setText('Set up automatic CPU sensors (one-time)…' if status == 'missing'
                               else 'Update CPU sensor helper…')
        self.act_setup.setVisible(not automatic or status != 'ready')
        self.act_setup.setEnabled(self.host.state != 'setup')

    def set_layout(self, value, key='orientation'):
        """Switch between the horizontal strip and the vertical stand, or between the
        Poster and Card looks (from the menu)."""
        self.set_and_apply(key, value)

    def keep_anchor(self, old):
        """After a layout switch, keep the widget flush with the screen side it was on."""
        screen = QGuiApplication.screenAt(old.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = old.right() - self.widget.width() + 1 if old.center().x() > area.center().x() else old.left()
        y = old.bottom() - self.widget.height() + 1 if old.center().y() > area.center().y() else old.top()
        self.widget.move(x, y)
        self.ensure_visible()
        self.remember_position()

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_widget()
        elif reason == QSystemTrayIcon.DoubleClick:
            self.show_settings()

    def toggle_widget(self):
        if self.widget.isVisible():
            self.widget.hide()
        else:
            self.widget.show()
            self.ensure_visible()
        self.settings['shown'] = self.widget.isVisible()
        self.save_timer.start()

    def toggle_helper(self):
        if self.host.state in ('connected', 'starting'):
            self.host.stop()
        else:
            self.start_helper()

    def start_helper(self):
        self.host.start(use_task=self.settings['helper_task'])

    def start_automatic_helper(self):
        """Start the installed helper; after an app update, refresh its protected copy first (one prompt)."""
        from .host import task_status
        status = task_status()
        if status == 'ready':
            self.start_helper()
        elif status == 'outdated':
            # Prompting from the background only flashes the taskbar and can time out; a click on the
            # notification lets Windows show the prompt in front.
            self.hint_pending = True
            self.tray.showMessage('Update the CPU sensor helper',
                                  'Prism Monitor was updated. Click here and approve the Windows prompt once to '
                                  'show CPU temperature again.', QSystemTrayIcon.Information, 30000)
            self.host.set_state('failed', 'The sensor helper needs a one-time update: click the tray notification '
                                'or choose "Update CPU sensor helper" in the tray menu.')
        else:
            self.settings['helper_task'] = False
            self.save()
            self.show_setup_hint()

    def setup_or_start(self):
        """--setup-helper: set up the automatic helper unless it is already current."""
        from .host import task_status
        if task_status() == 'ready':
            if not self.settings['helper_task']:
                self.settings['helper_task'] = True
                self.save()
            self.start_helper()
        else:
            self.host.setup_task()

    def show_setup_hint(self):
        if self.settings['helper_task'] or self.host.state in ('connected', 'starting', 'setup'):
            return
        self.hint_pending = True
        self.settings['setup_hint_shown'] = True
        self.save_timer.start()
        self.tray.showMessage('CPU temperature needs a one-time setup',
                              'Click here to set up the sensor helper. Windows asks for administrator approval once; '
                              'after that CPU temperature, power and fans show automatically.',
                              QSystemTrayIcon.Information, 15000)

    def tray_message_clicked(self):
        if self.hint_pending:
            self.hint_pending = False
            self.host.setup_task()

    def on_setup_finished(self, installed, message):
        from .host import task_status
        # Keep automatic mode while a protected copy exists (e.g. a declined update prompt): the next
        # launch asks again instead of silently dropping back to "not set up".
        self.settings['helper_task'] = installed or task_status() != 'missing'
        self.save()
        self.settings_window.update_status(self.last_snapshot, (self.host.state, message))
        self.settings_window.refresh_live()
        ok = installed or 'removed' in message
        self.tray.showMessage(APP_NAME, message, QSystemTrayIcon.Information if ok else QSystemTrayIcon.Warning, 6000)
        if installed:
            self.start_helper()

    def show_settings(self, tab=None):
        w = self.settings_window
        w.sync_checks()
        w.fill_processes()
        if tab:
            w.show_tab(tab)
        w.show()
        w.raise_()
        w.activateWindow()

    # -- settings -----------------------------------------------------------
    def set_and_apply(self, key, value):
        if self.settings[key] != value:
            self.settings[key] = value
            self.setting_changed(key)
        self.settings_window.sync_checks()

    def setting_changed(self, key):
        s = self.settings
        old = QRect(self.widget.pos(), self.widget.size())
        if key in ('always_on_top', 'click_through'):
            self.widget.apply_flags()
            if key == 'click_through' and s['click_through']:
                self.tray.showMessage(APP_NAME, 'Click-through is on. Use the tray icon menu to turn it off.',
                                      QSystemTrayIcon.Information, 4000)
        elif key == 'interval':
            self.collector.set_interval(s['interval'])
            self.host.set_interval(round(s['interval'] * 1000))
        elif key == 'launch_at_login':
            ok, message = autostart.sync(s['launch_at_login'])
            if not ok:
                QMessageBox.warning(self.settings_window, APP_NAME, message)
        elif key == 'cpu_temp_sensor':
            self.collector.cpu_temp_sensor = s['cpu_temp_sensor']
            self.widget.reset_history()
        elif key == 'fps_process':
            self.collector.fps_target = s['fps_process']
        elif key == 'accent':
            self.tray.setIcon(make_icon(s['accent'], self.awake.active))
        elif key == 'history':
            self.widget.signature = None
        elif key == 'visible':
            self.supervise_fps()
            self.supervise_batteries()
        self.widget.signature = None
        self.widget.relayout()
        if key in ('orientation', 'style'):
            self.keep_anchor(old)
        self.widget.update()
        self.save_timer.start()

    def save(self):
        try:
            self.settings.save()
        except OSError as e:
            self.tray.showMessage(APP_NAME, f'Could not save settings: {e}', QSystemTrayIcon.Warning, 5000)

    # -- position / screens -------------------------------------------------
    def watch_screen(self, screen):
        screen.availableGeometryChanged.connect(lambda *_: self.screen_timer.start())
        screen.geometryChanged.connect(lambda *_: self.screen_timer.start())

    def default_position(self):
        area = QGuiApplication.primaryScreen().availableGeometry()
        return QPoint(area.right() - self.widget.width() - 24, area.top() + 24)

    def place_initially(self):
        pos = self.settings['position']
        self.widget.move(QPoint(*pos) if pos else self.default_position())
        self.ensure_visible()

    def ensure_visible(self):
        """Move the widget back on screen if no monitor shows enough of it."""
        frame = QRect(self.widget.pos(), self.widget.size())
        need_w, need_h = min(60, frame.width()), min(40, frame.height())
        for screen in QGuiApplication.screens():
            overlap = screen.availableGeometry().intersected(frame)
            if overlap.width() >= need_w and overlap.height() >= need_h:
                return
        self.widget.move(self.default_position())
        self.remember_position()

    def reset_position(self):
        self.widget.move(self.default_position())
        self.widget.show()
        self.remember_position()

    def remember_position(self):
        pos = self.widget.pos()
        self.settings['position'] = [pos.x(), pos.y()]
        self.save_timer.start()

    # -- data ---------------------------------------------------------------
    def on_snapshot(self, snap):
        self.last_snapshot = snap
        self.awake.feed(snap)
        self.widget.update_snapshot(snap)
        self.settings_window.update_status(snap, (self.host.state, self.host.detail))
        fps = snap.readings.get('fps')
        tip = [APP_NAME]
        for key, label, fmt in (('cpu_temp', 'CPU', '{:.0f} °C'), ('gpu_temp', 'GPU', '{:.0f} °C')):
            item = snap.readings.get(key)
            if item:
                tip.append(f'{label} {fmt.format(item.value)}')
        if fps:
            tip.append(f'{fps.value:.0f} fps')
        if self.awake.active:
            tip.append(f'Awake: {self.awake.headline()}')
        self.tray.setToolTip(' · '.join(tip)[:127])

    # -- keep awake ---------------------------------------------------------
    def on_awake_changed(self):
        active = self.awake.active
        self.widget.set_awake(active, self.awake.headline(), self.awake.describe())
        if active != getattr(self, 'awake_icon', None):
            self.awake_icon = active
            self.tray.setIcon(make_icon(self.settings['accent'], active))
        self.settings_window.update_awake()

    def on_awake_finished(self, message, action):
        if action == 'none':
            self.tray.showMessage('Keep awake finished', message + ' Windows can sleep again.',
                                  QSystemTrayIcon.Information, 8000)
            return
        if self.countdown is not None:
            self.countdown.reject()
        self.countdown = CountdownDialog(action, message)
        self.countdown.finished.connect(lambda *_: setattr(self, 'countdown', None))
        self.countdown.show()
        self.countdown.raise_()
        self.tray.showMessage('Keep awake finished', f'{message} {AFTER[action]} in 60 s unless you cancel.',
                              QSystemTrayIcon.Information, 8000)

    def on_helper_state(self, state):
        self.settings_window.update_status(self.last_snapshot, (state, self.host.detail))
        if state == 'failed':
            self.tray.showMessage(APP_NAME, self.host.detail, QSystemTrayIcon.Warning, 5000)
        self.supervise_fps()

    def supervise_fps(self):
        if self.closing:
            return
        visible = set(self.settings['visible'])
        if not visible & {'fps', 'frametime'}:
            self.presentmon.stop()
            self.host.set_presentmon(False) if self.host.want_presentmon else None
            self.collector.fps_note = 'FPS capture is off (FPS and frame time are hidden).'
            return
        if os.environ.get('PRISM_MONITOR_FPS_VIA_HELPER') == '1' and self.host.state == 'connected':
            self.presentmon.stop()   # test hook: exercise the helper relay path
            self.fps_mode = 'host'
        if self.fps_mode == 'host':
            if self.host.state == 'connected':
                if not self.host.want_presentmon:
                    self.tracker.reset()
                    self.host.set_presentmon(True)
                self.collector.fps_note = 'OK (PresentMon relayed by the sensor helper)'
                return
            self.host.want_presentmon = False
            self.fps_mode = 'local'
        if self.presentmon.running:
            self.collector.fps_note = 'OK' if self.presentmon.got_header else 'Starting PresentMon…'
            return
        error = self.presentmon.error
        if error and 'ETW' in error:
            if self.host.state == 'connected':
                self.fps_mode = 'host'
                self.supervise_fps()
                return
            self.collector.fps_note = error + ' Start the sensor helper to relay FPS.'
            if time.monotonic() < self.fps_retry_at:
                return
            self.fps_retry_at = time.monotonic() + 30
        elif error and time.monotonic() < self.fps_retry_at:
            self.collector.fps_note = error
            return
        self.fps_retry_at = time.monotonic() + 5
        if not self.presentmon.start():
            self.collector.fps_note = self.presentmon.error

    def supervise_batteries(self):
        """Scan device batteries only while the Batteries card is shown."""
        if 'battery' in self.settings['visible']:
            self.batteries.start()
        else:
            self.batteries.stop()

    def shutdown(self):
        self.closing = True
        self.awake.shutdown()
        self.fps_timer.stop()
        self.batteries.stop()
        self.collector.stop()
        self.presentmon.stop()
        self.host.stop()
        self.settings['shown'] = self.widget.isVisible()
        self.save()
        self.tray.hide()


def single_instance(name, message=b'show'):
    """A server if this is the first instance; otherwise forwards `message` to the first and returns None."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(300):
        socket.write(message + b'\n')
        socket.waitForBytesWritten(300)
        return None
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    server.listen(name)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(prog='PrismMonitor')
    parser.add_argument('--hidden', action='store_true', help='start with the widget hidden (tray only)')
    parser.add_argument('--config', help='settings file path (for testing)')
    parser.add_argument('--smoke-test', action='store_true', help='start, collect a few snapshots, then exit')
    parser.add_argument('--grab', help='with --smoke-test: save a PNG of the widget (and settings window) here')
    parser.add_argument('--start-helper', action='store_true', help='start the sensor helper immediately')
    parser.add_argument('--setup-helper', action='store_true',
                        help='run the one-time automatic sensor helper setup (one administrator prompt)')
    args, _ = parser.parse_known_args(argv)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv[:1])
    app.setApplicationName('PrismMonitor')
    app.setApplicationDisplayName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    server = single_instance(f'PrismMonitor-{getpass.getuser()}', b'setup' if args.setup_helper else b'show')
    if server is None:
        return 0
    settings = Settings(args.config)
    monitor = PrismMonitor(app, settings, start_hidden=args.hidden)
    app.setWindowIcon(make_icon(settings['accent']))

    def incoming():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(lambda: handle(bytes(conn.readAll())))

    def handle(data):
        if b'setup' in data:
            monitor.setup_or_start()
        else:
            monitor.widget.show()
            monitor.show_settings()
    server.newConnection.connect(incoming)
    if args.start_helper:
        QTimer.singleShot(200, monitor.start_helper)
    if args.setup_helper:
        QTimer.singleShot(500, monitor.setup_or_start)
    if args.smoke_test:
        def report():
            if args.grab:
                monitor.widget.grab().save(args.grab)
                monitor.show_settings()
                monitor.settings_window.refresh_live()
                for i in range(monitor.settings_window.findChild(__import__('PySide6.QtWidgets', fromlist=['QTabWidget']).QTabWidget).count()):
                    tabs = monitor.settings_window.findChild(__import__('PySide6.QtWidgets', fromlist=['QTabWidget']).QTabWidget)
                    tabs.setCurrentIndex(i)
                    app.processEvents()
                    monitor.settings_window.grab().save(args.grab.replace('.png', f'-settings{i}.png'))
            snap = monitor.last_snapshot
            lines = ['no snapshot'] if snap is None else (
                [f'{key:12s} {item.value:14.2f} {item.source}' for key, item in sorted(snap.readings.items())]
                + [f'fans {[(label, round(item.value)) for label, item in snap.fans.values()]}',
                   f'fps_app {snap.info.get("fps_app")}', f'status {snap.status}'])
            text = '\n'.join(lines)
            if sys.stdout is not None:   # windowed builds have no console
                print(text)
            if args.grab:
                Path(args.grab).with_suffix('.txt').write_text(text, encoding='utf-8')
            app.quit()
        QTimer.singleShot(int(os.environ.get('PRISM_MONITOR_SMOKE_MS', '4000')), report)
    return app.exec()

# SPDX-License-Identifier: GPL-3.0-only
"""Keep Prism Light running in the system tray, and allow only one copy.

Closing the window hides it: the AIO display, fan control and lighting keep
running until the user picks Quit from the tray icon. A second launch (for
example Run-PrismLight.cmd again) asks the running copy to show its window
and exits, so two copies never fight over the same fans and devices.
"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

SERVER_NAME = 'PrismLightStudio-single-instance'


def app_icon():
    """An original prism mark, drawn at several sizes."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        s = size
        prism = QPainterPath(QPointF(s*.5, s*.08))
        prism.lineTo(s*.94, s*.86)
        prism.lineTo(s*.06, s*.86)
        prism.closeSubpath()
        g = QLinearGradient(0, 0, s, s)
        g.setColorAt(0, QColor('#c3b6ff'))
        g.setColorAt(.55, QColor('#8f7bff'))
        g.setColorAt(1, QColor('#45e0c2'))
        p.fillPath(prism, g)
        p.setPen(Qt.NoPen)
        for i, colour in enumerate(('#ff7ab3', '#ffd36b', '#45e0c2')):
            p.setBrush(QColor(colour))
            p.drawRect(QRectF(s*(.36 + i*.1), s*.62, s*.08, s*.16))
        p.end()
        icon.addPixmap(pix)
    return icon


def notify_running_instance(timeout=500):
    """True if another copy is running (and was asked to show itself)."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(timeout):
        return False
    socket.write(b'show')
    socket.waitForBytesWritten(timeout)
    socket.disconnectFromServer()
    return True


class SingleInstance:
    def __init__(self, on_show):
        self.on_show = on_show
        self.server = QLocalServer()
        QLocalServer.removeServer(SERVER_NAME)   # stale name after a crash
        self.server.listen(SERVER_NAME)
        self.server.newConnection.connect(self.accept)

    def accept(self):
        # Any connection is a second launch asking to show the window. Act on
        # the connection itself: its message may already be buffered before
        # a readyRead handler could be attached.
        shown = False
        while self.server.hasPendingConnections():
            connection = self.server.nextPendingConnection()
            connection.disconnected.connect(connection.deleteLater)
            shown = True
        if shown:
            self.on_show()


class Tray:
    def __init__(self, window):
        self.window = window
        self.icon = QSystemTrayIcon(app_icon(), window)
        self.icon.setToolTip('Prism Light · running')
        menu = QMenu()
        open_action = QAction('Open Prism Light', menu)
        open_action.triggered.connect(window.show_from_tray)
        self.autostart_action = QAction('Start with Windows', menu)
        self.autostart_action.setCheckable(True)
        self.autostart_action.toggled.connect(window.set_autostart)
        quit_action = QAction('Quit Prism Light', menu)
        quit_action.triggered.connect(window.quit_app)
        menu.addAction(open_action)
        menu.addAction(self.autostart_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.menu = menu
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self.activated)
        self.icon.show()
        self.told = False

    def activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.window.show_from_tray()

    def hidden(self):
        if not self.told:
            self.told = True
            self.icon.showMessage('Prism Light is still running',
                                  'Your AIO display, fans and lighting keep going. '
                                  'Right-click this icon and choose Quit to stop everything.',
                                  QSystemTrayIcon.Information, 6000)

    def set_autostart(self, on):
        self.autostart_action.blockSignals(True)
        self.autostart_action.setChecked(on)
        self.autostart_action.blockSignals(False)

    def remove(self):
        self.icon.hide()

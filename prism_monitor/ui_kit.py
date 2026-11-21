# SPDX-License-Identifier: GPL-3.0-only
"""Settings window building blocks: a vertical sidebar with animated page changes,
glowing buttons, and the animated About page."""
import math
import time

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
                            QUrl, Signal)
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

DONATE_URL = 'https://buymeacoffee.com/danny1986'
REPO_URL = 'https://github.com/danny19862025/prism-light'


class SideTabs(QWidget):
    """A vertical sidebar of pages, with the parts of the QTabWidget API the app uses.
    Switching pages fades and slides the new page in."""
    currentChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('sidetabs')
        self.nav = QListWidget()
        self.nav.setObjectName('sidebar')
        self.nav.setFixedWidth(196)
        self.nav.setFocusPolicy(Qt.NoFocus)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stack = QStackedWidget()
        self.stack.setObjectName('pages')
        self.title = QLabel()
        self.title.setObjectName('pagetitle')
        right = QVBoxLayout()
        right.setContentsMargins(22, 16, 22, 18)
        right.setSpacing(10)
        right.addWidget(self.title)
        right.addWidget(self.stack, 1)
        page = QWidget()
        page.setObjectName('pagearea')
        page.setLayout(right)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.nav)
        row.addWidget(page, 1)
        self.anim = None
        self.nav.currentRowChanged.connect(self._switch)

    # QTabWidget-compatible API ---------------------------------------------------
    def addTab(self, widget, title):
        scroll = QScrollArea()   # tall pages (Metrics, Keep awake) scroll instead of squashing
        scroll.setObjectName('pagescroll')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(widget)
        self.stack.addWidget(scroll)
        item = QListWidgetItem(title)
        item.setSizeHint(QSize(180, 42))
        self.nav.addItem(item)
        if self.nav.count() == 1:
            self.nav.setCurrentRow(0)
        return self.nav.count() - 1

    def count(self):
        return self.nav.count()

    def tabText(self, index):
        item = self.nav.item(index)
        return item.text() if item else ''

    def currentIndex(self):
        return self.nav.currentRow()

    def setCurrentIndex(self, index):
        self.nav.setCurrentRow(index)

    def widget(self, index):
        scroll = self.stack.widget(index)
        return scroll.widget() if scroll else None

    def currentWidget(self):
        return self.widget(self.currentIndex())

    # animation --------------------------------------------------------------------
    def _switch(self, index):
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        self.title.setText(self.tabText(index))
        page = self.stack.currentWidget()
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b'opacity', self)
        fade.setDuration(220)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        # The opacity effect renders the page offscreen; drop it once the fade is done.
        fade.finished.connect(lambda p=page: p.setGraphicsEffect(None))
        if self.anim is not None:
            self.anim.stop()
        self.anim = fade
        fade.start()
        self.currentChanged.emit(index)


class Glow(QObject):
    """A soft accent glow that grows while the mouse is over a button."""

    def __init__(self, button, color, rest=0.0, hover=22.0):
        super().__init__(button)
        self.button, self.rest, self.hover = button, rest, hover
        self.effect = QGraphicsDropShadowEffect(button)
        self.effect.setOffset(0, 0)
        self.effect.setBlurRadius(rest)
        self.set_color(color)
        button.setGraphicsEffect(self.effect)
        self.anim = QPropertyAnimation(self.effect, b'blurRadius', self)
        self.anim.setDuration(180)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        button.installEventFilter(self)

    def set_color(self, color):
        c = QColor(color)
        c.setAlpha(190)
        self.effect.setColor(c)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Enter, QEvent.Leave):
            self.anim.stop()
            self.anim.setStartValue(self.effect.blurRadius())
            self.anim.setEndValue(self.hover if event.type() == QEvent.Enter and obj.isEnabled() else self.rest)
            self.anim.start()
        return False


def add_glow(root, accent):
    """Give every push button under root a hover glow (or recolour existing ones)."""
    for button in root.findChildren(QPushButton):
        glow = button.findChild(Glow)
        if button.property('glow') == 'none':
            continue
        if glow is not None:
            glow.set_color(button.property('glow') or accent)
        elif not isinstance(button.graphicsEffect(), QGraphicsDropShadowEffect):
            Glow(button, button.property('glow') or accent)


class Logo(QWidget):
    """The four tray-icon bars, rising and falling like a live meter while visible."""

    def __init__(self, accent, parent=None):
        super().__init__(parent)
        self.accent = QColor(accent)
        self.start = time.monotonic()
        self.timer = QTimer(self, interval=33, timeout=self.update)
        self.setFixedSize(132, 132)

    def showEvent(self, event):
        self.start = time.monotonic()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()   # no animation, and no CPU use, while the page is hidden
        super().hideEvent(event)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = time.monotonic() - self.start
        r = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        # breathing halo
        glow = QColor(self.accent)
        for i in range(6, 0, -1):
            glow.setAlphaF(0.035 * (0.6 + 0.4 * math.sin(t * 2.0)))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(r.center(), r.width() / 2 * (0.62 + i * 0.065), r.height() / 2 * (0.62 + i * 0.065))
        tile = r.adjusted(18, 18, -18, -18)
        grad = QLinearGradient(tile.topLeft(), tile.bottomRight())
        grad.setColorAt(0, QColor(32, 36, 52))
        grad.setColorAt(1, QColor(14, 16, 24))
        p.setBrush(grad)
        p.setPen(QPen(QColor(self.accent.red(), self.accent.green(), self.accent.blue(), 90), 1.2))
        p.drawRoundedRect(tile, 18, 18)
        rest = (0.42, 0.78, 0.6, 1.0)
        bar_w, gap = tile.width() * 0.11, tile.width() * 0.07
        left = tile.center().x() - (4 * bar_w + 3 * gap) / 2
        base = tile.bottom() - tile.height() * 0.2
        full = tile.height() * 0.56
        intro = min(1.0, t / 0.7)
        ease = 1 - (1 - intro) ** 3
        for i, h in enumerate(rest):
            live = h * (0.82 + 0.18 * math.sin(t * 3.1 + i * 1.3))
            height = full * live * ease
            bar = QRectF(left + i * (bar_w + gap), base - height, bar_w, height)
            g = QLinearGradient(bar.topLeft(), bar.bottomLeft())
            g.setColorAt(0, self.accent.lighter(135))
            g.setColorAt(1, self.accent)
            p.setBrush(g)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(bar, bar_w / 2.4, bar_w / 2.4)


def cup_icon(size=48, ink='#0d0c22'):
    """A steaming coffee cup, drawn so it needs no emoji font."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 24.0
    p.setPen(QPen(QColor(ink), 1.9 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    cup = QPainterPath(QPointF(4 * s, 10 * s))
    cup.lineTo(17 * s, 10 * s)
    cup.lineTo(15.5 * s, 20 * s)
    cup.lineTo(5.5 * s, 20 * s)
    cup.closeSubpath()
    p.drawPath(cup)
    p.drawArc(QRectF(15 * s, 11.5 * s, 6 * s, 5.5 * s), -90 * 16, 180 * 16)
    for x in (8 * s, 12.5 * s):
        steam = QPainterPath(QPointF(x, 7.5 * s))
        steam.cubicTo(x - 1.6 * s, 5.8 * s, x + 1.6 * s, 4.2 * s, x, 2.5 * s)
        p.drawPath(steam)
    p.end()
    return QIcon(pix)


class CoffeeButton(QPushButton):
    """Buy me a coffee: the familiar yellow button with a cup. Its glow is drawn by
    CoffeeGlow around it, not by a graphics effect on the button itself."""

    def __init__(self, parent=None):
        super().__init__('Buy me a coffee', parent)
        self.setIcon(cup_icon())
        self.setIconSize(QSize(24, 24))
        self.setObjectName('coffee')
        self.setProperty('glow', 'none')        # add_glow leaves it alone
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(46)
        self.setToolTip(DONATE_URL)
        self.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DONATE_URL)))


class CoffeeGlow(QWidget):
    """Holds the coffee button and paints a softly breathing yellow halo around it.

    Painting the halo here (a few translucent rounded rectangles) instead of animating a
    QGraphicsDropShadowEffect keeps the button itself still: the effect re-rendered and
    re-blurred the button on every frame and changed its own size, which flickered."""
    MARGIN = 16

    def __init__(self, button, parent=None):
        super().__init__(parent)
        self.button = button
        self.start = time.monotonic()
        self.hover = 0.0
        row = QHBoxLayout(self)
        row.setContentsMargins(self.MARGIN, self.MARGIN, self.MARGIN, self.MARGIN)
        row.addWidget(button)
        self.timer = QTimer(self, interval=40, timeout=self.update)
        button.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Enter, QEvent.Leave):
            self.hover = 1.0 if event.type() == QEvent.Enter else 0.0
            self.update()
        return False

    def showEvent(self, event):
        self.start = time.monotonic()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()   # no animation, and no CPU use, while the page is hidden
        super().hideEvent(event)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        breathe = 0.5 + 0.5 * math.sin((time.monotonic() - self.start) * 2 * math.pi / 2.2)
        strength = 0.55 + 0.45 * breathe + 0.4 * self.hover
        core = QRectF(self.button.geometry())
        steps = 8
        for i in range(steps, 0, -1):
            grow = self.MARGIN * i / steps
            c = QColor(255, 214, 0)
            c.setAlphaF(min(1.0, 0.045 * strength * (steps - i + 1) / steps * 2.2))
            p.setBrush(c)
            p.drawRoundedRect(core.adjusted(-grow, -grow, grow, grow), 12 + grow, 12 + grow)


class AboutPage(QWidget):
    """Logo, name, version and links, sliding in each time the page is opened."""

    def __init__(self, accent, version, parent=None):
        super().__init__(parent)
        self.logo = Logo(accent)
        name = QLabel('Prism Monitor')
        name.setObjectName('abouttitle')
        name.setAlignment(Qt.AlignCenter)
        ver = QLabel(f'Version {version}')
        ver.setObjectName('hint')
        ver.setAlignment(Qt.AlignCenter)
        # update check: a button, a status line with animated dots, and Download when newer
        from .updates import UpdateChecker
        self.checker = UpdateChecker(version, self)
        self.checker.finished.connect(self.update_result)
        self.update_button = QPushButton('Check for updates')
        self.update_button.setObjectName('ghost')
        self.update_button.setCursor(Qt.PointingHandCursor)
        self.update_button.clicked.connect(self.check_updates)
        self.download_button = QPushButton('Download the update')
        self.download_button.setObjectName('primary')
        self.download_button.setCursor(Qt.PointingHandCursor)
        self.download_button.hide()
        self.download_url = ''
        self.download_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.download_url)))
        self.update_status = QLabel('')
        self.update_status.setObjectName('hint')
        self.update_status.setAlignment(Qt.AlignCenter)
        self.dots = 0
        self.dots_timer = QTimer(self, interval=350, timeout=self.tick_dots)
        update_row = QHBoxLayout()
        update_row.setSpacing(8)
        update_row.addStretch(1)
        update_row.addWidget(self.update_button)
        update_row.addWidget(self.download_button)
        update_row.addStretch(1)
        blurb = QLabel('A quiet desktop widget for CPU and GPU temperature, game FPS, fans and battery levels. '
                       'Free and open source, with no tracking and no ads.')
        blurb.setWordWrap(True)
        blurb.setAlignment(Qt.AlignCenter)
        blurb.setFixedWidth(460)
        thanks = QLabel('If Prism Monitor is useful to you, you can support its development:')
        thanks.setObjectName('hint')
        thanks.setAlignment(Qt.AlignCenter)
        thanks.setWordWrap(True)
        thanks.setFixedWidth(460)
        self.coffee = CoffeeButton()
        self.coffee.setFixedWidth(260)
        source = QPushButton('Source code on GitHub')
        source.setObjectName('ghost')
        source.setCursor(Qt.PointingHandCursor)
        source.setFixedWidth(260)
        source.setToolTip(REPO_URL)
        source.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))
        licence = QLabel('GPL-3.0 licence · Barlow font under the SIL Open Font License · '
                         'Sensors by LibreHardwareMonitor, FPS by Intel PresentMon')
        licence.setObjectName('hint')
        licence.setWordWrap(True)
        licence.setAlignment(Qt.AlignCenter)
        licence.setFixedWidth(460)

        self.body = QWidget()
        col = QVBoxLayout(self.body)
        col.setSpacing(10)
        col.addWidget(self.logo, 0, Qt.AlignHCenter)
        col.addWidget(name)
        col.addWidget(ver)
        col.addLayout(update_row)
        col.addWidget(self.update_status)
        col.addSpacing(6)
        col.addWidget(blurb, 0, Qt.AlignHCenter)
        col.addSpacing(14)
        col.addWidget(thanks, 0, Qt.AlignHCenter)
        self.coffee_glow = CoffeeGlow(self.coffee)
        col.addWidget(self.coffee_glow, 0, Qt.AlignHCenter)
        col.addWidget(source, 0, Qt.AlignHCenter)
        col.addSpacing(14)
        col.addWidget(licence, 0, Qt.AlignHCenter)
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(self.body)
        outer.addStretch(2)
        self.slide = None

    # updates ----------------------------------------------------------------------
    def check_updates(self):
        self.update_button.setEnabled(False)
        self.download_button.hide()
        self.dots = 0
        self.tick_dots()
        self.dots_timer.start()
        self.checker.check()

    def tick_dots(self):
        self.dots = self.dots % 3 + 1
        self.update_status.setText('Checking GitHub for a newer version' + '.' * self.dots)

    def update_result(self, state, version, url):
        self.dots_timer.stop()
        self.update_button.setEnabled(True)
        self.update_button.setText('Check again')
        current = self.checker.current
        if state == 'newer':
            self.download_url = url
            self.download_button.setText(f'Download {version}')
            self.download_button.show()
            self.update_status.setText(f'Version {version} is available. You have {current}.')
        elif state == 'current':
            self.update_status.setText(f'You have the latest version ({current}).')
        elif state == 'none':
            self.update_status.setText('No Prism Monitor release was found on GitHub.')
        else:
            self.update_status.setText(f'Could not check for updates: {version}')
        # the new status fades in
        effect = QGraphicsOpacityEffect(self.update_status)
        self.update_status.setGraphicsEffect(effect)
        self.fade = QPropertyAnimation(effect, b'opacity', self)
        self.fade.setDuration(300)
        self.fade.setStartValue(0.0)
        self.fade.setEndValue(1.0)
        self.fade.finished.connect(lambda: self.update_status.setGraphicsEffect(None))
        self.fade.start()

    def set_accent(self, accent):
        self.logo.accent = QColor(accent)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.slide_in)   # after the layout has placed the content

    def slide_in(self):
        end = self.body.pos()
        self.slide = QPropertyAnimation(self.body, b'pos', self)
        self.slide.setDuration(420)
        self.slide.setStartValue(end + QPointF(0, 24).toPoint())
        self.slide.setEndValue(end)
        self.slide.setEasingCurve(QEasingCurve.OutCubic)
        self.slide.start()

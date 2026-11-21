# SPDX-License-Identifier: GPL-3.0-only
"""AIO display page: effect gallery with live previews and per-effect settings."""
import time
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QColorDialog, QComboBox, QFileDialog, QFormLayout, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget)
from . import aio
from .lcd import ROTATIONS

CATEGORIES = ('All', 'Info', 'Sync', 'Ambient', 'Motion', 'System', 'Audio', 'Clock', 'Custom')
PAGE_STYLE = '''
QFrame#displayCard { background:#111928; border:1px solid #2a3851; border-radius:12px; }
QFrame#displayCard[selected="true"] { border:2px solid #a89bff; background:#181f36; }
QFrame#displayCard QLabel { background:transparent; border:0; }
QFrame#displaySide { background:#0e1622; border:1px solid #29364a; border-radius:12px; }
QFrame#displaySide QLabel { background:transparent; }
'''


class Round(QWidget):
    """Shows an image clipped to the round screen, with a bezel."""
    def __init__(self, size, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.pixmap = QPixmap()
    def set_image(self, image):
        self.pixmap = QPixmap.fromImage(image)
        self.update()
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(3, 3, self.width() - 6, self.height() - 6)
        clip = QPainterPath()
        clip.addEllipse(r)
        p.fillPath(clip, QColor('#000000'))
        if not self.pixmap.isNull():
            p.setClipPath(clip)
            p.drawPixmap(r.toRect(), self.pixmap)
            p.setClipping(False)
        p.setPen(QPen(QColor('#3a465f'), 3))
        p.drawEllipse(r)


class Card(QFrame):
    clicked = Signal(str)
    def __init__(self, name, category, parent=None):
        super().__init__(parent)
        self.name, self.category = name, category
        self.setObjectName('displayCard')
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(f'Display effect {name}')
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 12, 12, 10)
        box.setSpacing(8)
        self.thumb = Round(118)
        box.addWidget(self.thumb, 0, Qt.AlignHCenter)
        title = QLabel(name)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet('font-weight:600;')
        box.addWidget(title)
        tag = QLabel(category.upper())
        tag.setAlignment(Qt.AlignCenter)
        tag.setStyleSheet('color:#8d9cb3; font-size:10px; letter-spacing:2px;')
        box.addWidget(tag)
    def set_selected(self, on):
        self.setProperty('selected', on)
        self.style().unpolish(self)
        self.style().polish(self)
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.name)


class DisplayPage(QWidget):
    preview_size = 300
    thumb_interval = .4

    def __init__(self, studio, worker, settings_path):
        super().__init__()
        self.studio, self.worker, self.settings_path = studio, worker, settings_path
        self.settings = aio.load_settings(settings_path)
        self.frames = []
        if self.settings['picture']:
            try:
                self.frames = aio.load_frames(self.settings['picture'])
            except ValueError:
                self.settings['picture'] = ''
        self.started = time.monotonic()
        self.last_frame = 0
        self.last_thumbs = 0
        self.setStyleSheet(PAGE_STYLE)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 10, 0, 0)
        root.setSpacing(18)
        root.addWidget(self.build_side())
        root.addWidget(self.build_gallery(), 1)
        self.select(self.settings['effect'], save=False)
        self.sync()

    # ----------------------------------------------------------- layout
    def build_side(self):
        side = QFrame()
        side.setObjectName('displaySide')
        side.setFixedWidth(360)
        box = QVBoxLayout(side)
        box.setContentsMargins(22, 20, 22, 20)
        box.setSpacing(12)
        eyebrow = QLabel('AIO DISPLAY')
        eyebrow.setStyleSheet('color:#8d9cb3; letter-spacing:3px; font-size:11px;')
        box.addWidget(eyebrow)
        self.preview = Round(self.preview_size)
        self.preview.setAccessibleName('Display preview')
        box.addWidget(self.preview, 0, Qt.AlignHCenter)
        self.title = QLabel()
        self.title.setStyleSheet('font-size:22px; font-weight:700;')
        box.addWidget(self.title)
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setStyleSheet('color:#96a5bc;')
        box.addWidget(self.description)
        row = QHBoxLayout()
        self.start_btn = QPushButton('Show on AIO')
        self.start_btn.setObjectName('primary')
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = QPushButton('Stop')
        self.stop_btn.clicked.connect(self.stop)
        row.addWidget(self.start_btn, 1)
        row.addWidget(self.stop_btn)
        box.addLayout(row)
        form = QFormLayout()
        form.setSpacing(10)
        self.colour_btns = {}
        for key, caption in (('primary', 'Colour 1'), ('secondary', 'Colour 2')):
            b = QPushButton()
            b.setAccessibleName(caption)
            b.clicked.connect(lambda checked=False, k=key: self.choose_colour(k))
            self.colour_btns[key] = b
            form.addRow(caption, b)
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(5, 200)
        self.speed.setAccessibleName('Effect speed')
        self.speed.valueChanged.connect(lambda v: self.change(speed=v))
        form.addRow('Speed', self.speed)
        self.overlay = QComboBox()
        self.overlay.addItems(aio.OVERLAYS)
        self.overlay.setAccessibleName('Centre overlay')
        self.overlay.currentTextChanged.connect(lambda v: self.change(overlay=v))
        form.addRow('Centre', self.overlay)
        self.text = QLineEdit()
        self.text.setMaxLength(40)
        self.text.setPlaceholderText('Your text')
        self.text.setAccessibleName('Custom text')
        self.text.textChanged.connect(lambda v: self.change(text=v))
        form.addRow('Text', self.text)
        self.picture_btn = QPushButton('Choose picture or GIF…')
        self.picture_btn.clicked.connect(self.choose_picture)
        form.addRow('Picture', self.picture_btn)
        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.setAccessibleName('Display brightness')
        self.brightness.valueChanged.connect(self.change_brightness)
        form.addRow('Brightness', self.brightness)
        self.rotation = QComboBox()
        self.rotation.addItems([f'{r}°' for r in ROTATIONS])
        self.rotation.setAccessibleName('Display rotation')
        self.rotation.currentIndexChanged.connect(self.change_rotation)
        form.addRow('Rotation', self.rotation)
        box.addLayout(form)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet('color:#96a5bc;')
        box.addWidget(self.status)
        box.addStretch()
        # Initial values without triggering saves.
        for w in (self.speed, self.overlay, self.text, self.brightness, self.rotation):
            w.blockSignals(True)
        self.speed.setValue(self.settings['speed'])
        self.overlay.setCurrentText(self.settings['overlay'])
        self.text.setText(self.settings['text'])
        self.brightness.setValue(self.settings['brightness'])
        self.rotation.setCurrentIndex(ROTATIONS.index(self.settings['rotation']) if self.settings['rotation'] in ROTATIONS else 0)
        for w in (self.speed, self.overlay, self.text, self.brightness, self.rotation):
            w.blockSignals(False)
        self.paint_colour_buttons()
        return side

    def build_gallery(self):
        wrap = QWidget()
        box = QVBoxLayout(wrap)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(14)
        top = QHBoxLayout()
        title = QLabel('Display effects')
        title.setStyleSheet('font-size:24px; font-weight:750;')
        top.addWidget(title)
        top.addStretch()
        count = QLabel(f'{len(aio.EFFECTS)} effects')
        count.setStyleSheet('color:#8d9cb3;')
        top.addWidget(count)
        box.addLayout(top)
        # Category chips get their own row so they never push past the window edge.
        head = QHBoxLayout()
        head.setSpacing(8)
        group = QButtonGroup(wrap)
        group.setExclusive(True)
        self.category = 'All'
        for name in CATEGORIES:
            b = QPushButton(name)
            b.setObjectName('chip')
            b.setCheckable(True)
            b.setChecked(name == 'All')
            b.clicked.connect(lambda checked=False, n=name: self.filter(n))
            group.addButton(b)
            head.addWidget(b)
        head.addStretch()
        box.addLayout(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self.grid = QGridLayout(body)
        self.grid.setSpacing(14)
        self.grid.setContentsMargins(0, 0, 8, 0)
        self.cards = {}
        for name, (_fn, category) in aio.EFFECTS.items():
            card = Card(name, category)
            card.clicked.connect(self.select)
            self.cards[name] = card
        self.layout_cards()
        scroll.setWidget(body)
        box.addWidget(scroll, 1)
        return wrap

    def layout_cards(self):
        while self.grid.count():
            self.grid.takeAt(0)
        visible = [c for c in self.cards.values() if self.category in ('All', c.category)]
        for card in self.cards.values():
            card.setVisible(card in visible)
        for i, card in enumerate(visible):
            self.grid.addWidget(card, i//4, i % 4)
        self.grid.setRowStretch(len(visible)//4 + 1, 1)

    # ----------------------------------------------------------- actions
    def filter(self, category):
        self.category = category
        self.layout_cards()
        self.last_thumbs = 0

    def select(self, name, save=True):
        if name not in aio.EFFECTS:
            return
        for card_name, card in self.cards.items():
            card.set_selected(card_name == name)
        self.settings['effect'] = name
        self.title.setText(name)
        self.description.setText(aio.EFFECTS[name][0].__doc__.strip())
        custom = name not in aio.NO_OVERLAY
        self.overlay.setEnabled(custom)
        self.text.setEnabled(name == 'Text marquee' or self.settings['overlay'] == 'Custom text')
        self.picture_btn.setEnabled(name == 'Picture / GIF')
        self.last_frame = 0
        if save:
            aio.save_settings(self.settings_path, self.settings)

    def change(self, **values):
        self.settings.update(values)
        self.text.setEnabled(self.settings['effect'] == 'Text marquee' or self.settings['overlay'] == 'Custom text')
        self.last_frame = 0
        aio.save_settings(self.settings_path, self.settings)

    def change_brightness(self, value):
        self.change(brightness=value)
        self.worker.configure(brightness=value)

    def change_rotation(self, index):
        self.change(rotation=ROTATIONS[index])
        self.worker.configure(rotation=ROTATIONS[index])

    def choose_colour(self, key):
        colour = QColorDialog.getColor(QColor(self.settings[key]), self, 'Display colour')
        if colour.isValid():
            self.set_colour(key, colour.name().upper())

    def set_colour(self, key, value):
        self.change(**{key: value})
        self.paint_colour_buttons()

    def paint_colour_buttons(self):
        for key, b in self.colour_btns.items():
            value = self.settings[key]
            text = '#111' if QColor(value).lightness() > 140 else '#fff'
            b.setText(value)
            b.setStyleSheet(f'background:{value}; color:{text}; border:1px solid #4a5874;')

    def choose_picture(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Picture for the AIO display', '',
                                              'Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)')
        if path:
            self.set_picture(path)

    def set_picture(self, path):
        try:
            self.frames = aio.load_frames(path)
        except ValueError as e:
            self.studio.report(str(e), True)
            return
        self.change(picture=path)
        self.select('Picture / GIF')

    def start(self):
        if self.settings['effect'] == 'Picture / GIF' and not self.frames:
            self.studio.report('Choose a picture or GIF first.', True)
            return
        self.worker.start(self.settings['brightness'], self.settings['rotation'])
        self.last_frame = 0
        self.sync()

    def stop(self):
        self.worker.stop()
        self.sync()

    def sync(self):
        running = self.worker.running
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.status.setText(self.worker.status)

    # ----------------------------------------------------------- frames
    def params(self):
        prm = dict(self.settings)
        prm['frames'] = self.frames
        return prm

    def tick(self, now, data):
        """Called by the studio timer with live inputs."""
        self.sync()
        running = self.worker.running
        visible = self.isVisible()
        if not running and not visible:
            return
        data = dict(data, real_elapsed=now - self.started, now=time.localtime())
        t = now - self.started
        if now - self.last_frame >= .125:
            self.last_frame = now
            try:
                if running:
                    image = aio.draw(self.settings['effect'], aio.SIZE, t, self.params(), data)
                    self.worker.submit(aio.encode(image))
                else:
                    image = aio.draw(self.settings['effect'], self.preview_size, t, self.params(), data)
                if visible:
                    self.preview.set_image(image)
            except Exception as e:
                self.status.setText(f'Display: {e}')
        if visible and now - self.last_thumbs >= self.thumb_interval:
            self.last_thumbs = now
            for card in self.cards.values():
                if card.isVisible():
                    card.thumb.set_image(aio.draw(card.name, 118, t, self.params(), data))

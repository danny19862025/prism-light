# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Prism Light contributors
import argparse
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import sys
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QScrollArea, QCheckBox, QLineEdit, QSlider,
    QColorDialog, QComboBox, QSpinBox, QMessageBox, QInputDialog, QLayout)
from .backend import OpenRGB, Demo, rgb_bytes
from .presets import Presets

STYLE = '''
QWidget { background:#10131d; color:#e9edf8; font-family:"Segoe UI",sans-serif; font-size:14px; }
QLabel#eyebrow { color:#a8adca; font-size:12px; font-weight:600; }
QLabel#title { font-size:32px; font-weight:700; }
QLabel#sub { color:#a8adca; }
QFrame#panel { background:#191e2d; border:1px solid #30374c; border-radius:14px; }
QFrame#panel QLabel, QFrame#panel QCheckBox { background:transparent; }
QPushButton { background:#282f45; border:1px solid #3e4763; border-radius:8px; padding:10px 16px; }
QPushButton:hover { background:#36405b; border-color:#9d95ff; }
QPushButton#primary { background:#b6a7ff; color:#17122d; border:0; font-weight:700; }
QPushButton:disabled { background:#222737; color:#697188; border-color:#30374c; }
QLineEdit, QComboBox, QSpinBox { background:#111622; border:1px solid #414b67; border-radius:7px; padding:8px; }
QCheckBox::indicator { width:19px; height:19px; }
QSlider::groove:horizontal { background:#39415b; height:7px; border-radius:3px; }
QSlider::sub-page:horizontal { background:#b6a7ff; border-radius:3px; }
QSlider::handle:horizontal { background:#efebff; width:19px; margin:-6px 0; border-radius:9px; }
QScrollArea { border:0; }
'''

def label(text, kind=None):
    w = QLabel(text)
    w.setWordWrap(True)
    w.setTextFormat(Qt.PlainText)
    if kind:
        w.setObjectName(kind)
    return w

def button(text, callback, primary=False):
    w = QPushButton(text)
    w.clicked.connect(callback)
    if primary:
        w.setObjectName('primary')
    return w

def panel():
    w = QFrame()
    w.setObjectName('panel')
    layout = QVBoxLayout(w)
    layout.setContentsMargins(22,22,22,22)
    layout.setSpacing(14)
    return w, layout

class Window(QMainWindow):
    def __init__(self, demo=False, preset_path=None):
        super().__init__()
        self.setWindowTitle('Prism Light')
        self.resize(1180,940)
        self.setMinimumSize(960,740)
        self.backend = OpenRGB()
        self.connected = False
        self.devices = []
        self.checks = {}
        self.future = None
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.store = Presets(preset_path)
        self.color = '#B6A7FF'
        root = QWidget()
        self.setCentralWidget(root)
        page = QVBoxLayout(root)
        page.setContentsMargins(30,25,30,25)
        page.setSpacing(16)
        page.addWidget(label('PRISM LIGHT    /    YOUR SPACE, YOUR COLOUR', 'eyebrow'))
        page.addWidget(label('A little light. All yours.', 'title'))
        page.addWidget(label('Local lighting control • Free & open source • No account required', 'sub'))
        connection, bar = panel()
        row = QHBoxLayout()
        self.status = label('○  Disconnected')
        row.addWidget(self.status,1)
        self.port = QSpinBox()
        self.port.setRange(1,65535)
        self.port.setValue(6742)
        self.port.setAccessibleName('Local OpenRGB SDK port')
        row.addWidget(label('127.0.0.1 :'))
        row.addWidget(self.port)
        self.connect_btn = button('Connect OpenRGB', self.connect_live)
        self.demo_btn = button('Enter demo mode', self.enter_demo)
        self.disconnect_btn = button('Disconnect', self.disconnect)
        for w in (self.connect_btn,self.demo_btn,self.disconnect_btn):
            row.addWidget(w)
        bar.addLayout(row)
        self.mode_note = label('Start OpenRGB’s SDK server, then connect. Hardware support is provided by OpenRGB.', 'sub')
        bar.addWidget(self.mode_note)
        page.addWidget(connection)
        columns = QHBoxLayout()
        left, listing = panel()
        heading = QHBoxLayout()
        self.device_title = label('Your devices  ·  0', 'eyebrow')
        heading.addWidget(self.device_title,1)
        self.all_btn = button('Select supported', self.select_all)
        heading.addWidget(self.all_btn)
        listing.addLayout(heading)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        cards = QWidget()
        self.cards = QVBoxLayout(cards)
        self.cards.setContentsMargins(0,0,0,0)
        self.cards.addStretch()
        scroll.setWidget(cards)
        listing.addWidget(scroll)
        columns.addWidget(left,3)
        right, editor = panel()
        right.setMinimumWidth(310)
        editor.setSpacing(8)
        editor.setContentsMargins(18,18,18,18)
        editor.addWidget(label('MAKE IT YOURS', 'eyebrow'))
        editor_title = label('Solid colour', 'title')
        editor_title.setStyleSheet('font-size:26px; font-weight:700;')
        editor.addWidget(editor_title)
        self.preview = QLabel()
        self.preview.setMinimumHeight(86)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedHeight(64)
        editor.addWidget(self.preview)
        self.color_field = QLineEdit(self.color)
        self.color_field.setAccessibleName('Hex colour')
        self.color_field.editingFinished.connect(self.edit_color)
        editor.addWidget(self.color_field)
        self.pick_btn = button('Choose colour…', self.pick_color)
        editor.addWidget(self.pick_btn)
        swatches = QHBoxLayout()
        for hexcode in ['#B6A7FF','#59DEC8','#FFB778','#F26DA9','#FFFFFF']:
            b = button('●', lambda checked=False,c=hexcode:self.set_color(c))
            b.setAccessibleName('Use '+hexcode)
            b.setStyleSheet(f'color:{hexcode}; padding:6px; font-size:22px;')
            swatches.addWidget(b)
        editor.addLayout(swatches)
        self.level = label('Brightness  ·  80%')
        editor.addWidget(self.level)
        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0,100)
        self.brightness.setValue(80)
        self.brightness.setAccessibleName('Software brightness percent')
        self.brightness.valueChanged.connect(self.update_preview)
        editor.addWidget(self.brightness)
        editor.addWidget(label('Software brightness in Direct mode. Applies to whole devices; zones are listed for reference.', 'sub'))
        self.apply_btn = button('Apply to selected devices', self.apply, True)
        editor.addWidget(self.apply_btn)
        editor.addStretch()
        editor.addWidget(label('LIGHTING PRESETS', 'eyebrow'))
        self.presets = QComboBox()
        self.presets.setAccessibleName('Saved lighting preset')
        editor.addWidget(self.presets)
        preset_row = QHBoxLayout()
        self.save_btn = button('Save as…', self.save_preset)
        self.restore_btn = button('Restore', self.restore_preset)
        preset_row.addWidget(self.save_btn)
        preset_row.addWidget(self.restore_btn)
        editor.addLayout(preset_row)
        editor.addWidget(label('Presets stay on this computer. Demo and hardware presets are kept separate.', 'sub'))
        editor.setSizeConstraint(QLayout.SetMinimumSize)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setMinimumWidth(410)
        right_scroll.setWidget(right)
        columns.addWidget(right_scroll,2)
        page.addLayout(columns,1)
        self.feedback = label('Ready. Connect OpenRGB or try the labelled demo.', 'sub')
        self.feedback.setMinimumHeight(42)
        self.feedback.setTextInteractionFlags(Qt.TextSelectableByMouse)
        page.addWidget(self.feedback)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.health_tick = 0
        self.update_preview()
        self.reload_presets()
        self.refresh_controls()
        if demo:
            QTimer.singleShot(0,self.enter_demo)
    def report(self, text, error=False):
        self.feedback.setText(text)
        self.feedback.setStyleSheet('color:#ffaeae;' if error else 'color:#b5c6dd;')
    def refresh_controls(self):
        busy = self.future is not None
        for w in (self.connect_btn,self.demo_btn,self.port):
            w.setEnabled(not busy)
        self.disconnect_btn.setEnabled(self.connected and not busy)
        selected = any(c.isChecked() for c in self.checks.values())
        for w in (self.apply_btn,self.save_btn):
            w.setEnabled(self.connected and selected and not busy)
        self.restore_btn.setEnabled(self.connected and self.presets.count()>0 and not busy)
        self.all_btn.setEnabled(self.connected and not busy)
        for key, check in self.checks.items():
            check.setEnabled(self.connected and not busy and check.property('supported'))
    def job(self, fn, done):
        self.future = self.pool.submit(fn)
        self.done = done
        self.refresh_controls()
    def tick(self):
        if self.future and self.future.done():
            future, done = self.future, self.done
            self.future = None
            try:
                done(future.result())
            except Exception as e:
                self.backend.close()
                self.connected = False
                self.status.setText('○  Disconnected / error')
                self.report(f'{e} Check OpenRGB → SDK Server → Start Server, port {self.port.value()}, then reconnect.',True)
            self.refresh_controls()
        self.health_tick += 1
        if self.health_tick >= 40:
            self.health_tick = 0
            if self.connected and not self.backend.demo and not self.future:
                self.job(self.backend.health, lambda result:None)
    def disconnect(self):
        self.backend.close()
        self.connected = False
        self.devices = []
        self.render_devices()
        self.status.setText('○  Disconnected')
        self.mode_note.setText('Start OpenRGB’s SDK server, then connect.')
        self.report('Disconnected. Last lighting may remain on hardware.')
        self.refresh_controls()
    def connect_live(self):
        self.disconnect()
        self.backend = OpenRGB()
        self.status.setText('◌  Connecting to OpenRGB…')
        port = self.port.value()
        self.job(lambda:self.backend.connect(port),self.did_connect)
    def enter_demo(self):
        self.disconnect()
        self.backend = Demo()
        self.did_connect(self.backend.connect())
    def did_connect(self, devices):
        self.devices = devices
        self.connected = True
        self.status.setText('●  DEMO MODE · simulated devices' if self.backend.demo else '●  Connected · local OpenRGB / SDK v3')
        self.mode_note.setText('DEMO MODE — all devices are simulated. No hardware is accessed.' if self.backend.demo else 'Local server connected. Commands are sent without hardware acknowledgement in SDK v3.')
        self.render_devices()
        self.report('Select devices, choose a colour, and apply.' if devices else 'Connected, but OpenRGB reports no devices. Check detection in OpenRGB and reconnect.')
        self.reload_presets()
        self.refresh_controls()
    def render_devices(self):
        self.checks = {}
        while self.cards.count():
            item = self.cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        counts = Counter(d.key for d in self.devices)
        for d in self.devices:
            card, box = panel()
            enabled = d.supported and counts[d.key] == 1
            check = QCheckBox(d.name)
            check.setChecked(enabled)
            check.setProperty('supported', enabled)
            check.toggled.connect(self.refresh_controls)
            box.addWidget(check)
            box.addWidget(label(f'{d.vendor or "Unknown vendor"}  ·  {d.leds} LEDs', 'sub'))
            box.addWidget(label('Direct colour · software brightness' if enabled else 'Unavailable: ambiguous identity' if counts[d.key]>1 else 'Unavailable: no Direct per-LED colour mode or no LEDs', 'sub'))
            for zone, count in d.zones:
                box.addWidget(label(f'◦  {zone}   /   {count} LEDs', 'sub'))
            self.cards.addWidget(card)
            self.checks[d.key] = check
        if not self.devices:
            self.cards.addWidget(label('Your lighting will appear here.\nConnect the local SDK server or enter demo mode.', 'sub'))
        self.cards.addStretch()
        self.device_title.setText(f'YOUR DEVICES  ·  {len(self.devices)}')
    def select_all(self):
        for c in self.checks.values():
            c.setChecked(bool(c.property('supported')))
    def selected(self):
        return [k for k,c in self.checks.items() if c.isChecked() and c.property('supported')]
    def set_color(self, color):
        self.color = color.upper()
        self.color_field.setText(self.color)
        self.update_preview()
    def edit_color(self):
        try:
            rgb_bytes(self.color_field.text(),self.brightness.value())
            self.set_color(self.color_field.text())
        except ValueError as e:
            self.report(str(e),True)
            self.color_field.setText(self.color)
    def pick_color(self):
        color = QColorDialog.getColor(QColor(self.color), self, 'Choose your lighting colour')
        if color.isValid():
            self.set_color(color.name())
    def update_preview(self):
        level = self.brightness.value()
        rgb = rgb_bytes(self.color,level)
        color = '#'+rgb[:3].hex()
        self.preview.setStyleSheet(f'background:{color}; border:1px solid #6b6783; border-radius:12px; color:{"#131521" if sum(rgb[:3])>400 else "#ffffff"}; font-size:18px; font-weight:600;')
        self.preview.setText(f'{self.color}   /   {level}%')
        self.level.setText(f'Brightness  ·  {level}%')
    def apply(self):
        self.edit_color()
        keys, color, brightness = self.selected(),self.color,self.brightness.value()
        self.job(lambda:self.backend.apply(keys,color,brightness),self.report)
    def reload_presets(self):
        self.presets.clear()
        try:
            scope = 'demo' if self.backend.demo else 'hardware'
            for name,p in sorted(self.store.read().items()):
                if p['scope'] == scope:
                    self.presets.addItem(name)
        except Exception as e:
            self.report(str(e),True)
    def save_preset(self):
        name,ok = QInputDialog.getText(self,'Save lighting preset','Name (saves current colour, brightness and selection):')
        if not ok:
            return
        try:
            if name.strip() in self.store.read():
                if QMessageBox.question(self,'Replace preset?',f'Replace “{name.strip()}”?') != QMessageBox.Yes:
                    return
            self.store.save(name,self.color,self.brightness.value(),self.selected(),self.backend.demo)
            self.reload_presets()
            self.presets.setCurrentText(name.strip())
            self.report(f'Saved “{name.strip()}” locally.')
            self.refresh_controls()
        except Exception as e:
            self.report(str(e),True)
    def restore_preset(self):
        try:
            p = self.store.read()[self.presets.currentText()]
            self.set_color(p['color'])
            self.brightness.setValue(p['brightness'])
            for key,c in self.checks.items():
                c.setChecked(key in p['devices'] and bool(c.property('supported')))
            missing = len(set(p['devices'])-set(self.selected()))
            self.report(f'Preset restored. {missing} missing or unsupported device(s) skipped. Click Apply to send lighting.')
            self.refresh_controls()
        except Exception as e:
            self.report(str(e),True)
    def closeEvent(self,event):
        self.timer.stop()
        # No simultaneous socket close during an active bounded network operation.
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.backend.close()
        event.accept()

def main():
    parser = argparse.ArgumentParser(description='Prism Light: local OpenRGB lighting')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--smoke-test', action='store_true', help='Verify demo UI startup, then exit')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    window = Window(demo=args.demo or args.smoke_test)
    window.show()
    if args.smoke_test:
        def smoke():
            valid = window.connected and window.backend.demo and len(window.devices) == 3
            window.close()
            app.exit(0 if valid else 1)
        QTimer.singleShot(300,smoke)
    return app.exec()

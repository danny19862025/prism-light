# SPDX-License-Identifier: GPL-3.0-only
"""Separate settings window; every change applies immediately."""
import sys
import datetime

from PySide6.QtCore import Qt, QTime, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QFontComboBox,
                               QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QSlider, QSpinBox, QStackedWidget, QTimeEdit, QVBoxLayout, QWidget)

from . import __version__
from .awake import AFTER, MODES
from .model import GROUPS, METRICS
from .settings import resolve_theme
from .ui_kit import AboutPage, SideTabs, add_glow

STYLE = {
    'dark': ('#12141c', '#1b1e29', '#262a38', '#eef1f8', '#8e97ab'),
    'midnight': ('#090e20', '#111a36', '#1f2b52', '#e8edff', '#8c9ad0'),
    'light': ('#f4f5f9', '#ffffff', '#d9dce5', '#141821', '#5d6576'),
}


def stylesheet(theme, accent):
    bg, panel, line, text, dim = STYLE.get(theme, STYLE['dark'])
    acc = QColor(accent)
    acc_hi, acc_lo = acc.lighter(118).name(), acc.darker(112).name()
    soft = f'rgba({acc.red()}, {acc.green()}, {acc.blue()}, 38)'
    rim = f'rgba({acc.red()}, {acc.green()}, {acc.blue()}, 120)'
    side = QColor(bg).darker(118).name() if theme != 'light' else '#e9ebf2'
    return f"""
    QWidget {{ background: {bg}; color: {text}; font-family: "Segoe UI Variable Text", "Segoe UI", "Noto Sans", sans-serif; font-size: 10.5pt; }}
    QListWidget#sidebar {{ background: {side}; border: none; border-right: 1px solid {line}; padding: 14px 10px;
                           outline: none; font-size: 11pt; }}
    QListWidget#sidebar::item {{ color: {dim}; padding: 0 14px; margin: 2px 0; border-radius: 9px;
                                 border-left: 3px solid transparent; }}
    QListWidget#sidebar::item:hover {{ background: {line}; color: {text}; }}
    QListWidget#sidebar::item:selected {{ background: {soft}; color: {text}; border-left: 3px solid {accent};
                                          font-weight: 600; }}
    QWidget#pagearea, QStackedWidget#pages {{ background: {bg}; }}
    QScrollArea#pagescroll, QScrollArea#pagescroll > QWidget > QWidget {{ background: {bg}; }}
    QLabel#pagetitle {{ font-size: 17pt; font-weight: 600; padding-bottom: 4px; }}
    QLabel#abouttitle {{ font-size: 22pt; font-weight: 700; color: {text}; }}
    QGroupBox {{ background: {panel}; border: 1px solid {line}; border-radius: 10px; margin-top: 16px; padding: 10px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; color: {accent}; font-weight: 600; }}
    QLabel, QStackedWidget {{ background: transparent; }}
    QLabel#hint {{ color: {dim}; }}
    QCheckBox, QRadioButton {{ background: transparent; spacing: 8px; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {line}; background: {panel}; }}
    QCheckBox::indicator:hover {{ border-color: {accent}; }}
    QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}
    QPushButton {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {panel}, stop:1 {line});
                   border: 1px solid {line}; border-radius: 9px; padding: 7px 16px; }}
    QPushButton:hover {{ border-color: {rim}; color: {text}; }}
    QPushButton:pressed {{ background: {line}; }}
    QPushButton:disabled {{ color: {dim}; border-color: {line}; }}
    QPushButton#primary {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {acc_hi}, stop:1 {acc_lo});
                           color: {bg}; border: 1px solid {accent}; font-weight: 600; }}
    QPushButton#ghost {{ background: transparent; border: 1px solid {line}; color: {text}; }}
    QPushButton#ghost:hover {{ border-color: {rim}; }}
    QPushButton#coffee {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFE24D, stop:1 #FFD000);
                          color: #0d0c22; border: none; border-radius: 12px; font-weight: 700; font-size: 11.5pt;
                          padding: 10px 22px; }}
    QPushButton#coffee:hover {{ background: #FFE766; }}
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QTimeEdit, QListWidget {{
        background: {panel}; border: 1px solid {line}; border-radius: 8px; padding: 5px 8px;
        selection-background-color: {accent}; selection-color: {bg}; }}
    QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover, QTimeEdit:hover {{ border-color: {rim}; }}
    QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus, QTimeEdit:focus {{ border-color: {accent}; }}
    QComboBox QAbstractItemView {{ background: {panel}; border: 1px solid {line}; }}
    QSlider::groove:horizontal {{ height: 6px; background: {line}; border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {acc_lo}, stop:1 {acc_hi});
                                    border-radius: 3px; }}
    QSlider::handle:horizontal {{ background: {text}; width: 16px; margin: -6px 0; border-radius: 8px;
                                  border: 2px solid {accent}; }}
    QLabel#status {{ background: {panel}; border: 1px solid {line}; border-left: 3px solid {accent};
                     border-radius: 8px; padding: 10px; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; }}
    QScrollBar::handle:vertical {{ background: {line}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QToolTip {{ background: {panel}; color: {text}; border: 1px solid {line}; padding: 4px; }}
    """

FPS_NOTES = (
    '<b>How FPS is measured.</b> Intel PresentMon reads the present events Windows already logs (Event Tracing for '
    'Windows). The widget shows the presented frame rate and average frame time of the <i>selected game process</i> '
    'over the last second; its own drawing and your monitor\'s refresh rate are not counted. Nothing is injected '
    'into games and anti-cheat is not touched.<br><br>'
    '<b>Compatibility.</b> Works with Direct3D 9–12, Vulkan and OpenGL games that present through the Windows '
    'graphics stack, in windowed, borderless and fullscreen modes. Not measured: driver-generated frames '
    '(AMD Fluid Motion Frames), frames from games streamed from another PC or cloud, and video players or '
    'apps that do not present through DXGI/D3D. In-game frame generation (FSR 3/DLSS FG) frames are presented by '
    'the game and are counted. Capture needs administrator rights or membership of the Windows "Performance Log '
    'Users" group; otherwise start the sensor helper and FPS is relayed from it.'
)
LINUX = sys.platform.startswith('linux')
if LINUX:
    FPS_NOTES = (
        '<b>How FPS is measured.</b> A small Vulkan layer that Prism Monitor installs for your account '
        '(<code>~/.local/share/vulkan/implicit_layer.d</code>) notes the time of each frame a game presents and '
        'sends it to the widget over a private local socket. The widget shows the presented frame rate and average '
        'frame time of the <i>selected game process</i> over the last second; its own drawing and your monitor\'s '
        'refresh rate are not counted. The layer does not draw anything or change frames.<br><br>'
        '<b>Compatibility.</b> Works with Vulkan games, which includes Windows games running through Proton/DXVK/'
        'VKD3D on Steam, in windowed, borderless and fullscreen modes. Not measured: native OpenGL games, Flatpak '
        'apps (their sandbox hides the layer) and games streamed from another PC. Start a game after turning FPS on; '
        'games already running are not counted. To leave a game out, start it with <code>DISABLE_PRISM_FPS=1</code>.'
    )


class SettingsWindow(QWidget):
    changed = Signal(str)          # settings key that changed
    helper_start = Signal()
    helper_stop = Signal()
    helper_setup = Signal()
    helper_remove = Signal()

    def __init__(self, settings, tracker, awake=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.tracker = tracker
        self.awake = awake
        self.setWindowTitle('Prism Monitor settings')
        self.setMinimumSize(820, 600)
        self.resize(960, 700)
        self.tabs = tabs = SideTabs()
        if awake is not None:
            tabs.addTab(self.awake_tab(), 'Keep awake')
        tabs.addTab(self.appearance_tab(), 'Appearance')
        tabs.addTab(self.metrics_tab(), 'Metrics')
        tabs.addTab(self.behaviour_tab(), 'Behaviour')
        tabs.addTab(self.fps_tab(), 'Game FPS')
        tabs.addTab(self.sensors_tab(), 'Sensors')
        self.about = AboutPage(settings['accent'], __version__)
        tabs.addTab(self.about, 'About')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(tabs)
        self.restyle()
        self.refresh = QTimer(self, interval=1500, timeout=self.refresh_live)

    def restyle(self):
        self.setStyleSheet(stylesheet(resolve_theme(self.settings['theme']), self.settings['accent']))
        add_glow(self, self.settings['accent'])
        if hasattr(self, 'about'):
            self.about.set_accent(self.settings['accent'])

    def show_tab(self, name):
        titles = {'awake': 'Keep awake', 'fps': 'Game FPS', 'sensors': 'Sensors', 'about': 'About'}
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == titles.get(name, name):
                self.tabs.setCurrentIndex(i)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_live()
        self.refresh.start()

    def hideEvent(self, event):
        self.refresh.stop()
        super().hideEvent(event)

    def set(self, key, value):
        if self.settings[key] != value:
            self.settings[key] = value
            self.changed.emit(key)
            if key in ('theme', 'accent'):
                self.restyle()

    # -- tabs ----------------------------------------------------------------
    def appearance_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        theme = QComboBox()
        theme.addItem('Dark', 'dark')
        theme.addItem('Midnight', 'midnight')
        theme.addItem('Light', 'light')
        theme.addItem('System (follow the system light/dark mode)' if LINUX else 'System (follow Windows light/dark mode)', 'system')
        theme.setCurrentIndex(max(0, theme.findData(self.settings['theme'])))
        theme.currentIndexChanged.connect(lambda: self.set('theme', theme.currentData()))
        form.addRow('Theme', theme)
        self.accent_button = QPushButton()
        self.accent_button.clicked.connect(self.pick_accent)
        self.paint_accent()
        form.addRow('Accent colour', self.accent_button)
        self.style_combo = style = QComboBox()
        style.addItem('Poster (no panel, like the Still clock)', 'poster')
        style.addItem('Card (dark panel with graphs)', 'card')
        style.setToolTip('Also in the widget and tray right-click menu: Look.')
        style.setCurrentIndex(max(0, style.findData(self.settings['style'])))
        style.currentIndexChanged.connect(lambda: (self.set('style', style.currentData()), self.sync_style()))
        form.addRow('Look', style)
        self.hero_combo = hero = QComboBox()
        hero.addItem('CPU temperature', 'cpu')
        hero.addItem('GPU temperature', 'gpu')
        hero.setToolTip('The big number in the Poster look. While a game is running it shows FPS instead.')
        hero.setCurrentIndex(max(0, hero.findData(self.settings['poster_hero'])))
        hero.currentIndexChanged.connect(lambda: self.set('poster_hero', hero.currentData()))
        form.addRow('Big number', hero)
        self.card_only = []
        self.poster_only = []
        self.orientation = orientation = QComboBox()
        orientation.addItem('Horizontal strip', 'horizontal')
        orientation.addItem('Vertical stand', 'vertical')
        orientation.setToolTip('Also in the widget and tray right-click menu: Layout.')
        orientation.setCurrentIndex(0 if self.settings['orientation'] == 'horizontal' else 1)
        orientation.currentIndexChanged.connect(lambda: self.set('orientation', orientation.currentData()))
        form.addRow('Layout', orientation)
        font = QSpinBox(minimum=7, maximum=20, suffix=' pt', value=self.settings['font_size'])
        font.valueChanged.connect(lambda v: self.set('font_size', v))
        form.addRow('Font size', font)
        opacity = self.slider(20, 100, round(self.settings['opacity'] * 100), '%', lambda v: self.set('opacity', v / 100))
        form.addRow('Background opacity', opacity)
        backdrop = self.slider(0, 100, round(self.settings['poster_opacity'] * 100), '%',
                               lambda v: self.set('poster_opacity', v / 100))
        backdrop.setToolTip('A dark panel behind the Poster lettering. 0% is no panel, like the Still clock.')
        self.backdrop_slider = backdrop.findChild(QSlider)   # also set by the menu
        form.addRow('Poster background', backdrop)
        font_row = QWidget()
        line = QHBoxLayout(font_row)
        line.setContentsMargins(0, 0, 0, 0)
        self.font_combo = QFontComboBox()
        self.font_combo.setEditable(False)
        self.font_combo.setToolTip('The typeface of the Poster look. Barlow is the bundled font of the Still clock.')
        self.font_combo.currentFontChanged.connect(
            lambda f: self.set('poster_font', '' if f.family() == 'Barlow' else f.family()))
        barlow = QPushButton('Barlow')
        barlow.setToolTip('Back to the bundled Barlow font.')
        barlow.clicked.connect(lambda: (self.set('poster_font', ''), self.sync_font()))
        line.addWidget(self.font_combo, 1)
        line.addWidget(barlow)
        self.poster_only.append(font_row)
        form.addRow('Poster font', font_row)
        self.sync_font()
        self.colour_buttons = {}
        for key, label in (('poster_text', 'Poster text colour'), ('poster_accent', 'Poster number colour')):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            button = QPushButton()
            button.clicked.connect(lambda _=False, k=key, t=label: self.pick_poster_colour(k, t))
            auto = QPushButton('Automatic')
            auto.setToolTip('White lettering and a light-blue number (dark on the Light theme).')
            auto.clicked.connect(lambda _=False, k=key: (self.set(k, ''), self.paint_colours()))
            line.addWidget(button, 1)
            line.addWidget(auto)
            self.colour_buttons[key] = button
            self.poster_only.append(row)
            form.addRow(label, row)
        self.paint_colours()
        scale = self.slider(60, 200, round(self.settings['scale'] * 100), '%', lambda v: self.set('scale', v / 100))
        self.scale_slider = scale.findChild(QSlider)   # also set by the menu's Size
        form.addRow('Widget scale', scale)
        self.card_only += [orientation, opacity]
        for key, text in (('header', 'Show the title bar (clock and keep-awake button)'),
                          ('color_levels', 'Colour hot temperatures and near-full memory amber/red')):
            check = QCheckBox(text)
            check.setChecked(self.settings[key])
            check.toggled.connect(lambda v, k=key: self.set(k, v))
            form.addRow(check)
            if key == 'header':
                self.card_only.append(check)
        graphs = QCheckBox('Show history graphs')
        graphs.setChecked(self.settings['graphs'])
        graphs.toggled.connect(lambda v: self.set('graphs', v))
        form.addRow(graphs)
        history = QSpinBox(minimum=10, maximum=300, suffix=' samples', value=self.settings['history'])
        history.valueChanged.connect(lambda v: self.set('history', v))
        form.addRow('Graph length', history)
        self.card_only += [graphs, history]
        self.sync_style()
        return w

    def sync_style(self):
        """Card-only options are greyed out in the Poster look; the big-number choice is Poster-only."""
        poster = self.settings['style'] == 'poster'
        for widget in self.card_only:
            widget.setEnabled(not poster)
        self.hero_combo.setEnabled(poster)
        self.backdrop_slider.parentWidget().setEnabled(poster)
        for widget in self.poster_only:
            widget.setEnabled(poster)

    def slider(self, low, high, value, suffix, apply):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        slider = QSlider(Qt.Horizontal, minimum=low, maximum=high, value=value)
        label = QLabel(f'{value}{suffix}')
        label.setMinimumWidth(44)
        slider.valueChanged.connect(lambda v: (label.setText(f'{v}{suffix}'), apply(v)))
        row.addWidget(slider)
        row.addWidget(label)
        return box

    def paint_accent(self):
        color = self.settings['accent']
        self.accent_button.setText(color.upper())
        text = '#000' if QColor(color).lightness() > 140 else '#fff'
        self.accent_button.setStyleSheet(f'background:{color};color:{text};padding:4px 12px;border-radius:4px')

    def sync_font(self):
        from .poster import load_fonts
        load_fonts()
        self.font_combo.blockSignals(True)
        self.font_combo.setCurrentFont(QFont(self.settings['poster_font'] or 'Barlow'))
        self.font_combo.blockSignals(False)

    def paint_colours(self):
        for key, button in self.colour_buttons.items():
            value = self.settings[key]
            if not value:
                button.setText('Automatic')
                button.setStyleSheet('')
                continue
            text = '#000' if QColor(value).lightness() > 140 else '#fff'
            button.setText(value.upper())
            button.setStyleSheet(f'background:{value};color:{text};padding:4px 12px;border-radius:4px')

    def pick_poster_colour(self, key, title):
        start = QColor(self.settings[key] or ('#ffffff' if key == 'poster_text' else '#8fd8ff'))
        color = QColorDialog.getColor(start, self, title)
        if color.isValid():
            self.set(key, color.name())
            self.paint_colours()

    def pick_accent(self):
        color = QColorDialog.getColor(QColor(self.settings['accent']), self, 'Accent colour')
        if color.isValid():
            self.set('accent', color.name())
            self.paint_accent()

    def metrics_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        self.metric_boxes = {}
        titles = dict(GROUPS)
        for group, title in GROUPS:
            box = QGroupBox(titles[group])
            row = QHBoxLayout(box)
            for metric in METRICS:
                if metric.group != group:
                    continue
                check = QCheckBox(metric.label)
                check.setChecked(metric.key in self.settings['visible'])
                check.toggled.connect(self.update_visible)
                self.metric_boxes[metric.key] = check
                row.addWidget(check)
            row.addStretch()
            layout.addWidget(box)
        cpu_row = QHBoxLayout()
        cpu_row.addWidget(QLabel('CPU temperature sensor'))
        self.cpu_sensor = QComboBox()
        self.cpu_sensor.setToolTip(
            'Tctl/Tdie is AMD\'s control temperature: the value Ryzen Master, HWiNFO and the BIOS show as CPU '
            'temperature, and what the cooler responds to. CCD sensors are the individual core dies; an idle die '
            'reads much cooler than Tctl.')
        self.cpu_sensor.addItem('Automatic: Core (Tctl/Tdie)', '')
        current = self.settings['cpu_temp_sensor']
        if current:
            self.cpu_sensor.addItem(current, current)
            self.cpu_sensor.setCurrentIndex(1)
        self.cpu_sensor.currentIndexChanged.connect(
            lambda: self.set('cpu_temp_sensor', self.cpu_sensor.currentData() or ''))
        cpu_row.addWidget(self.cpu_sensor, 1)
        layout.addLayout(cpu_row)
        layout.addWidget(QLabel('Motherboard fans to show (read by the kernel\'s hwmon driver):' if LINUX
                                 else 'Motherboard fans to show (needs the sensor helper):'))
        self.fan_list = QListWidget()
        self.fan_list.setMaximumHeight(110)
        self.fan_list.itemChanged.connect(self.update_fans)
        layout.addWidget(self.fan_list)
        return w

    def update_visible(self):
        visible = [m.key for m in METRICS if self.metric_boxes[m.key].isChecked()]
        self.set('visible', visible)

    def update_fans(self):
        chosen = [self.fan_list.item(i).data(Qt.UserRole) for i in range(self.fan_list.count())
                  if self.fan_list.item(i).checkState() == Qt.Checked]
        self.set('fans_visible', chosen)

    def behaviour_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        interval = QDoubleSpinBox(minimum=0.25, maximum=10, singleStep=0.25, decimals=2, suffix=' s',
                                  value=self.settings['interval'])
        interval.valueChanged.connect(lambda v: self.set('interval', v))
        form.addRow('Polling interval', interval)
        self.checks = {}
        self.layer_combo = layer = QComboBox()
        for value, text in (('top', 'On top of windows (see it over games)'), ('normal', 'Normal window'),
                            ('behind', 'Behind windows (on the desktop, like a clock)')):
            layer.addItem(text, value)
        layer.setToolTip('Behind windows: every window, including newly opened ones, covers the widget. '
                         'Also in the right-click menu: Widget position.')
        layer.setCurrentIndex(max(0, layer.findData(self.settings['layer'])))
        layer.currentIndexChanged.connect(lambda: self.set('layer', layer.currentData()))
        form.addRow('Widget position', layer)
        for key, text in (('locked', 'Lock position'),
                          ('click_through', 'Click-through (turn off from the tray icon)'),
                          ('launch_at_login', 'Launch at login'),
                          ('helper_autostart', 'Start the sensor helper on launch (shows the administrator prompt)')):
            if LINUX and key == 'helper_autostart':
                continue   # no helper on Linux: the kernel's sensors need no administrator rights
            check = QCheckBox(text)
            check.setChecked(self.settings[key])
            check.toggled.connect(lambda v, k=key: self.set(k, v))
            self.checks[key] = check
            form.addRow(check)
        note = QLabel('Drag the widget to move it. Double-click it to open these settings, right-click for the menu. '
                      'If a monitor is disconnected, the widget moves back onto a visible screen.')
        note.setWordWrap(True)
        form.addRow(note)
        return w

    def sync_checks(self):
        for key, check in self.checks.items():
            check.blockSignals(True)
            check.setChecked(self.settings[key])
            check.blockSignals(False)
        self.orientation.blockSignals(True)
        self.orientation.setCurrentIndex(self.orientation.findData(self.settings['orientation']))
        self.orientation.blockSignals(False)
        for key, check in self.metric_boxes.items():   # sections can be switched from the menu
            check.blockSignals(True)
            check.setChecked(key in self.settings['visible'])
            check.blockSignals(False)
        self.hero_combo.blockSignals(True)
        self.hero_combo.setCurrentIndex(self.hero_combo.findData(self.settings['poster_hero']))
        self.hero_combo.blockSignals(False)
        self.backdrop_slider.setValue(round(self.settings['poster_opacity'] * 100))
        self.paint_colours()
        self.sync_font()
        self.scale_slider.setValue(round(self.settings['scale'] * 100))   # same value: set() is a no-op
        self.layer_combo.blockSignals(True)
        self.layer_combo.setCurrentIndex(self.layer_combo.findData(self.settings['layer']))
        self.layer_combo.blockSignals(False)
        self.style_combo.blockSignals(True)
        self.style_combo.setCurrentIndex(self.style_combo.findData(self.settings['style']))
        self.style_combo.blockSignals(False)
        self.sync_style()

    def fps_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel('Game process'))
        self.process_combo = QComboBox()
        self.process_combo.setEditable(True)
        self.process_combo.setInsertPolicy(QComboBox.NoInsert)
        self.process_combo.lineEdit().setPlaceholderText('Automatic: the application in focus')
        self.process_combo.activated.connect(self.choose_process)
        self.process_combo.lineEdit().editingFinished.connect(self.choose_process)
        row.addWidget(self.process_combo, 1)
        refresh = QPushButton('Refresh')
        refresh.clicked.connect(self.fill_processes)
        row.addWidget(refresh)
        layout.addLayout(row)
        self.presenting = QLabel()
        self.presenting.setWordWrap(True)
        layout.addWidget(self.presenting)
        self.fps_status = QLabel()
        self.fps_status.setWordWrap(True)
        layout.addWidget(self.fps_status)
        notes = QLabel(FPS_NOTES)
        notes.setWordWrap(True)
        notes.setTextFormat(Qt.RichText)
        layout.addWidget(notes)
        layout.addStretch()
        self.fill_processes()
        return w

    def fill_processes(self):
        combo = self.process_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem('Automatic: the application in focus', '')
        names = []
        for pid, name, fps in self.tracker.presenting():
            if name not in names:
                names.append(name)
                combo.addItem(f'{name}  ({fps:.0f} fps now)', name)
        current = self.settings['fps_process']
        if current and current not in names:
            combo.addItem(current, current)
        index = combo.findData(current)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def choose_process(self, *_):
        combo = self.process_combo
        text = combo.currentText().strip()
        data = combo.currentData()
        if combo.currentIndex() >= 0 and combo.itemText(combo.currentIndex()) == combo.currentText():
            value = data or ''
        else:
            value = '' if not text or text.startswith('Automatic') else text
        if value and not value.lower().endswith('.exe'):
            value += '.exe'
        self.set('fps_process', value)

    def sensors_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.RichText)
        layout.addWidget(self.status_label)
        row = QHBoxLayout()
        start = QPushButton('Start sensor helper…')
        start.setToolTip('Starts PrismSensorHost.exe through the Windows administrator prompt.')
        start.clicked.connect(self.helper_start.emit)
        stop = QPushButton('Stop sensor helper')
        stop.clicked.connect(self.helper_stop.emit)
        row.addWidget(start)
        row.addWidget(stop)
        row.addStretch()
        layout.addLayout(row)
        self.helper_controls = [start, stop]
        auto = QHBoxLayout()
        self.setup_button = QPushButton('Set up automatic sensor helper…')
        self.setup_button.setToolTip('One administrator prompt: copies the helper into Program Files and registers an '
                                     'on-demand Task Scheduler task, so it starts without a prompt from then on.')
        self.setup_button.clicked.connect(self.helper_setup.emit)
        self.remove_button = QPushButton('Remove automatic helper…')
        self.remove_button.clicked.connect(self.helper_remove.emit)
        auto.addWidget(self.setup_button)
        auto.addWidget(self.remove_button)
        auto.addStretch()
        layout.addLayout(auto)
        self.auto_label = QLabel()
        self.auto_label.setWordWrap(True)
        layout.addWidget(self.auto_label)
        self.helper_controls += [self.setup_button, self.remove_button, self.auto_label]
        if LINUX:
            for control in self.helper_controls:
                control.hide()
            info = QLabel(
                'The widget runs as your user and needs no administrator rights or helper on Linux. CPU load, clock, '
                'temperature (k10temp/coretemp) and package power (RAPL), RAM, network and disk come from the '
                'kernel (<code>/proc</code> and <code>/sys</code>); the AMD GPU from the amdgpu driver; batteries '
                'from UPower. Motherboard fans show when the kernel has a driver for the board\'s fan chip, for '
                'example <code>nct6775</code> or <code>it87</code>; check with <code>sensors</code>.')
            info.setWordWrap(True)
            layout.addWidget(info)
            layout.addStretch()
            return w
        info = QLabel(
            'The widget runs as a standard user. GPU (AMD driver), CPU load and clock estimate, RAM, network '
            'and disk need no special rights. CPU temperature, CPU package power, measured core clocks and '
            'motherboard fans need ring-0 access: they are read by a small separate helper that uses '
            'LibreHardwareMonitorLib with the PawnIO driver. The helper only reads sensors, accepts '
            'connections only from your account, and exits with the widget. Prism Monitor never installs drivers; '
            'if PawnIO is missing, install it yourself from <a href="https://pawnio.eu/">pawnio.eu</a>.')
        info.setWordWrap(True)
        info.setOpenExternalLinks(True)
        layout.addWidget(info)
        layout.addStretch()
        return w

    def awake_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        self.awake_status = QLabel(objectName='status')
        self.awake_status.setWordWrap(True)
        layout.addWidget(self.awake_status)

        job = QGroupBox('Keep the PC awake')
        form = QFormLayout(job)
        self.awake_mode = QComboBox()
        for key, label in MODES.items():
            self.awake_mode.addItem(label, key)
        self.awake_mode.setCurrentIndex(self.awake_mode.findData('downloads'))
        form.addRow('Job', self.awake_mode)
        self.awake_params = QStackedWidget()
        pages = {}
        for key in MODES:
            pages[key] = QLabel()
            pages[key].setWordWrap(True)
            pages[key].setAlignment(Qt.AlignLeft | Qt.AlignTop)
            pages[key].setObjectName('hint')
        pages['indefinite'].setText('Until you turn it off, like caffeinate with no options.')
        self.awake_minutes = QSpinBox(minimum=1, maximum=24 * 60, suffix=' min', value=self.settings['awake_minutes'])
        pages['duration'] = self.awake_minutes
        self.awake_until = QTimeEdit(QTime.currentTime().addSecs(3600))
        self.awake_until.setDisplayFormat('HH:mm')
        pages['until_time'] = self.awake_until
        self.awake_app = QComboBox(editable=True)
        self.awake_app.lineEdit().setPlaceholderText('App name, e.g. HandBrake.exe')
        pages['process'] = self.awake_app
        pages['downloads'].setText('Stays awake while files are downloading in the folder below or the network '
                                   'is busy, then ends once both have been quiet for the set time. '
                                   'Works for browsers, Steam, game launchers and torrent apps.')
        pages['cpu'].setText('Stays awake while the CPU is busy (video exports, renders, builds) and ends once '
                             'it has stayed below the threshold below for the set time.')
        self.awake_pages = {}
        for key in MODES:
            self.awake_pages[key] = self.awake_params.addWidget(pages[key])
        self.awake_params.setMaximumHeight(self.fontMetrics().lineSpacing() * 4 + 8)   # fits the 4-line hints at any DPI
        form.addRow('', self.awake_params)
        self.awake_mode.currentIndexChanged.connect(self.awake_mode_changed)
        self.awake_display = QCheckBox('Keep the display on too')
        self.awake_display.setChecked(self.settings['awake_display'])
        self.awake_display.toggled.connect(self.awake.set_display)
        form.addRow(self.awake_display)
        self.awake_after = QComboBox()
        for key, label in AFTER.items():
            self.awake_after.addItem(label, key)
        self.awake_after.setToolTip('Runs after a 60-second countdown you can cancel. Resets to "Nothing" each time '
                                    'Prism Monitor starts, so the PC never shuts down by surprise.')
        self.awake_after.currentIndexChanged.connect(lambda: self.awake.set_after(self.awake_after.currentData()))
        form.addRow('When finished', self.awake_after)
        row = QHBoxLayout()
        self.awake_start = QPushButton('Start', objectName='primary')
        self.awake_start.clicked.connect(self.start_awake)
        self.awake_stop = QPushButton('Turn off')
        self.awake_stop.clicked.connect(self.awake.stop)
        self.awake_error = QLabel()
        self.awake_error.setStyleSheet('color:#ff6b6b')
        row.addWidget(self.awake_start)
        row.addWidget(self.awake_stop)
        row.addWidget(self.awake_error, 1)
        form.addRow(row)
        layout.addWidget(job)

        tuning = QGroupBox('Job detection')
        form = QFormLayout(tuning)
        net = QSpinBox(minimum=1, maximum=100000, suffix=' KB/s', value=self.settings['awake_net_kbps'])
        net.setToolTip('Downloads count as active while the download rate is at or above this.')
        net.valueChanged.connect(lambda v: self.set('awake_net_kbps', v))
        form.addRow('Busy network above', net)
        cpu = QSpinBox(minimum=1, maximum=99, suffix=' %', value=self.settings['awake_cpu_pct'])
        cpu.valueChanged.connect(lambda v: self.set('awake_cpu_pct', v))
        form.addRow('Busy CPU above', cpu)
        quiet = QDoubleSpinBox(minimum=0.25, maximum=60, singleStep=0.5, decimals=2, suffix=' min',
                               value=self.settings['awake_quiet_min'])
        quiet.valueChanged.connect(lambda v: self.set('awake_quiet_min', v))
        form.addRow('Finished after quiet for', quiet)
        folder_row = QHBoxLayout()
        from . import winapi
        self.awake_folder = QLineEdit(self.settings['awake_folder'])
        self.awake_folder.setPlaceholderText(winapi.downloads_folder())
        self.awake_folder.editingFinished.connect(lambda: self.set('awake_folder', self.awake_folder.text().strip()))
        browse = QPushButton('Browse…')
        browse.clicked.connect(self.pick_folder)
        folder_row.addWidget(self.awake_folder, 1)
        folder_row.addWidget(browse)
        form.addRow('Download folder', folder_row)
        layout.addWidget(tuning)

        auto = QGroupBox('Always keep awake while these apps run')
        box = QVBoxLayout(auto)
        self.auto_list = QListWidget()
        self.auto_list.setMaximumHeight(90)
        for name in self.settings['awake_auto_apps']:
            self.auto_list.addItem(name)
        box.addWidget(self.auto_list)
        add_row = QHBoxLayout()
        self.auto_add = QComboBox(editable=True)
        self.auto_add.lineEdit().setPlaceholderText('e.g. obs64.exe, a backup or render app')
        add = QPushButton('Add')
        add.clicked.connect(self.add_auto_app)
        remove = QPushButton('Remove')
        remove.clicked.connect(self.remove_auto_app)
        add_row.addWidget(self.auto_add, 1)
        add_row.addWidget(add)
        add_row.addWidget(remove)
        box.addLayout(add_row)
        layout.addWidget(auto)
        layout.addStretch()
        self.awake_mode_changed()
        return w

    def pick_folder(self):
        from . import winapi
        folder = QFileDialog.getExistingDirectory(self, 'Download folder',
                                                  self.settings['awake_folder'] or winapi.downloads_folder())
        if folder:
            self.awake_folder.setText(folder)
            self.set('awake_folder', folder)

    def fill_open_apps(self, combo):
        from .awake_ui import open_apps
        import os
        text = combo.currentText()
        combo.clear()
        try:
            combo.addItems(open_apps(os.getpid()))
        except Exception:
            pass
        combo.setCurrentText(text)

    def awake_mode_changed(self):
        mode = self.awake_mode.currentData()
        self.awake_params.setCurrentIndex(self.awake_pages[mode])
        if mode == 'process':
            self.fill_open_apps(self.awake_app)

    def start_awake(self):
        mode = self.awake_mode.currentData()
        kw = {}
        if mode == 'duration':
            self.set('awake_minutes', self.awake_minutes.value())
            kw['minutes'] = self.awake_minutes.value()
        elif mode == 'until_time':
            t = self.awake_until.time()
            now = datetime.datetime.now()
            when = now.replace(hour=t.hour(), minute=t.minute(), second=0, microsecond=0)
            if when <= now:
                when += datetime.timedelta(days=1)
            kw['until'] = when.timestamp()
        elif mode == 'process':
            name = self.awake_app.currentText().strip()
            if name and not name.lower().endswith('.exe'):
                name += '.exe'
            kw['processes'] = (name,) if name else ()
        self.awake_error.setText(self.awake.start(mode, **kw))

    def add_auto_app(self):
        name = self.auto_add.currentText().strip()
        if not name:
            return
        if not name.lower().endswith('.exe'):
            name += '.exe'
        apps = list(self.settings['awake_auto_apps'])
        if name.casefold() not in {a.casefold() for a in apps}:
            apps.append(name)
            self.auto_list.addItem(name)
            self.set('awake_auto_apps', apps)
        self.auto_add.setCurrentText('')

    def remove_auto_app(self):
        row = self.auto_list.currentRow()
        if row < 0:
            return
        self.auto_list.takeItem(row)
        self.set('awake_auto_apps', [self.auto_list.item(i).text() for i in range(self.auto_list.count())])

    def update_awake(self):
        if self.awake is None or not self.isVisible():
            return
        self.awake_status.setText(self.awake.describe().replace('\n', '<br>'))
        self.awake_stop.setEnabled(self.awake.session is not None)
        self.awake_start.setText('Restart' if self.awake.session is not None else 'Start')
        current = self.awake.session.after if self.awake.session else self.awake.after
        self.awake_after.blockSignals(True)
        self.awake_after.setCurrentIndex(self.awake_after.findData(current))
        self.awake_after.blockSignals(False)
        display = self.awake.session.display if self.awake.session else self.settings['awake_display']
        self.awake_display.blockSignals(True)
        self.awake_display.setChecked(display)
        self.awake_display.blockSignals(False)
        if self.awake.session is not None:
            self.awake_error.setText('')

    # -- live state ----------------------------------------------------------
    def update_status(self, snapshot, helper_state):
        self.last = (snapshot, helper_state)

    def refresh_live(self):
        self.update_awake()
        if self.awake is not None and not self.auto_add.count():
            self.fill_open_apps(self.auto_add)
        snapshot, helper_state = getattr(self, 'last', (None, ('off', '')))
        status = snapshot.status if snapshot else {}
        names = {'system': 'Windows counters (CPU load/clock, RAM, network, disk)', 'adl': 'AMD GPU driver (ADL)',
                 'fps': 'PresentMon FPS'}
        if LINUX:
            names = {'system': 'Linux kernel sensors (CPU, RAM, network, disk, fans)', 'adl': 'AMD GPU driver (amdgpu)',
                     'fps': 'Vulkan FPS layer'}
        lines = [f'<b>{names[k]}:</b> {status.get(k, "starting…")}' for k in names]
        if LINUX:
            if status.get('batteries'):
                lines.append(f"<b>Batteries:</b> {status['batteries']}")
        else:
            state, detail = helper_state
            lines.append(f'<b>Sensor helper:</b> {state} — {detail}')
            from .host import installed_root, task_status
            auto_status = task_status()
            self.auto_label.setText('Automatic helper: ' + {
                'ready': f'set up ({installed_root()}); starts without a prompt.',
                'outdated': 'set up, but from a different version; run the repair/update button.',
                'missing': 'not set up; the helper needs an administrator prompt each time.'}[auto_status])
            self.setup_button.setText('Set up automatic sensor helper…' if auto_status == 'missing'
                                      else 'Repair / update automatic helper…')
            self.remove_button.setEnabled(auto_status != 'missing')
        if snapshot and snapshot.info.get('cpu_temp_sensor'):
            lines.append(f"<b>CPU temperature sensor:</b> {snapshot.info['cpu_temp_sensor']}")
        self.status_label.setText('<br>'.join(lines))
        self.fps_status.setText('Status: ' + status.get('fps', 'starting…'))
        live = self.tracker.presenting()
        self.presenting.setText('Presenting now: ' + (', '.join(f'{n} ({f:.0f} fps)' for _, n, f in live[:6])
                                                      if live else 'nothing yet'))
        if snapshot and snapshot.info.get('cpu_temps'):
            self.fill_cpu_sensors(snapshot.info['cpu_temps'])
        if snapshot and snapshot.fans:
            self.fill_fans(snapshot)

    def fill_cpu_sensors(self, temps):
        combo = self.cpu_sensor
        names = [combo.itemData(i) for i in range(1, combo.count())]
        combo.blockSignals(True)
        if names != list(temps):
            current = self.settings['cpu_temp_sensor']
            while combo.count() > 1:
                combo.removeItem(1)
            for name in temps:
                combo.addItem(name, name)
            if current and current not in temps:
                combo.addItem(f'{current} (not reported)', current)
            combo.setCurrentIndex(max(0, combo.findData(current)) if current else 0)
        for i in range(1, combo.count()):
            name = combo.itemData(i)
            if name in temps:
                combo.setItemText(i, f'{name} — {temps[name]:.0f} °C')
        tctl = temps.get('Core (Tctl/Tdie)')
        combo.setItemText(0, 'Automatic: Core (Tctl/Tdie)' + (f' — {tctl:.0f} °C' if tctl is not None else ''))
        combo.blockSignals(False)

    def fill_fans(self, snapshot):
        chosen = self.settings['fans_visible']
        ids = [self.fan_list.item(i).data(Qt.UserRole) for i in range(self.fan_list.count())]
        fans = sorted(snapshot.fans.items(), key=lambda kv: kv[1][0])
        if ids == [fid for fid, _ in fans]:
            for i, (fid, (label, item)) in enumerate(fans):
                self.fan_list.item(i).setText(f'{label} — {item.value:.0f} rpm')
            return
        self.fan_list.blockSignals(True)
        self.fan_list.clear()
        for fid, (label, item) in fans:
            entry = QListWidgetItem(f'{label} — {item.value:.0f} rpm')
            entry.setData(Qt.UserRole, fid)
            entry.setFlags(entry.flags() | Qt.ItemIsUserCheckable)
            on = item.value > 0 if chosen is None else fid in chosen
            entry.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.fan_list.addItem(entry)
        self.fan_list.blockSignals(False)

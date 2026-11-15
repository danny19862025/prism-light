# SPDX-License-Identifier: GPL-3.0-only
"""Separate settings window; every change applies immediately."""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSlider, QSpinBox,
                               QTabWidget, QVBoxLayout, QWidget)

from .model import GROUPS, METRICS

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


class SettingsWindow(QWidget):
    changed = Signal(str)          # settings key that changed
    helper_start = Signal()
    helper_stop = Signal()
    helper_setup = Signal()
    helper_remove = Signal()

    def __init__(self, settings, tracker, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.tracker = tracker
        self.setWindowTitle('Prism Monitor settings')
        self.setMinimumWidth(460)
        tabs = QTabWidget()
        tabs.addTab(self.appearance_tab(), 'Appearance')
        tabs.addTab(self.metrics_tab(), 'Metrics')
        tabs.addTab(self.behaviour_tab(), 'Behaviour')
        tabs.addTab(self.fps_tab(), 'Game FPS')
        tabs.addTab(self.sensors_tab(), 'Sensors')
        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        self.refresh = QTimer(self, interval=1500, timeout=self.refresh_live)

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

    # -- tabs ----------------------------------------------------------------
    def appearance_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        theme = QComboBox()
        theme.addItem('Dark', 'dark')
        theme.addItem('Light', 'light')
        theme.setCurrentIndex(0 if self.settings['theme'] == 'dark' else 1)
        theme.currentIndexChanged.connect(lambda: self.set('theme', theme.currentData()))
        form.addRow('Theme', theme)
        self.accent_button = QPushButton()
        self.accent_button.clicked.connect(self.pick_accent)
        self.paint_accent()
        form.addRow('Accent colour', self.accent_button)
        orientation = QComboBox()
        orientation.addItem('Horizontal strip', 'horizontal')
        orientation.addItem('Vertical column', 'vertical')
        orientation.setCurrentIndex(0 if self.settings['orientation'] == 'horizontal' else 1)
        orientation.currentIndexChanged.connect(lambda: self.set('orientation', orientation.currentData()))
        form.addRow('Layout', orientation)
        font = QSpinBox(minimum=7, maximum=20, suffix=' pt', value=self.settings['font_size'])
        font.valueChanged.connect(lambda v: self.set('font_size', v))
        form.addRow('Font size', font)
        form.addRow('Background opacity', self.slider(20, 100, round(self.settings['opacity'] * 100), '%',
                                                     lambda v: self.set('opacity', v / 100)))
        form.addRow('Widget scale', self.slider(60, 200, round(self.settings['scale'] * 100), '%',
                                                lambda v: self.set('scale', v / 100)))
        graphs = QCheckBox('Show history graphs')
        graphs.setChecked(self.settings['graphs'])
        graphs.toggled.connect(lambda v: self.set('graphs', v))
        form.addRow(graphs)
        history = QSpinBox(minimum=10, maximum=300, suffix=' samples', value=self.settings['history'])
        history.valueChanged.connect(lambda v: self.set('history', v))
        form.addRow('Graph length', history)
        return w

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
        layout.addWidget(QLabel('Motherboard fans to show (needs the sensor helper):'))
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
        for key, text in (('always_on_top', 'Always on top'), ('locked', 'Lock position'),
                          ('click_through', 'Click-through (turn off from the tray icon)'),
                          ('launch_at_login', 'Launch at login'),
                          ('helper_autostart', 'Start the sensor helper on launch (shows the administrator prompt)')):
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

    # -- live state ----------------------------------------------------------
    def update_status(self, snapshot, helper_state):
        self.last = (snapshot, helper_state)

    def refresh_live(self):
        snapshot, helper_state = getattr(self, 'last', (None, ('off', '')))
        status = snapshot.status if snapshot else {}
        names = {'system': 'Windows counters (CPU load/clock, RAM, network, disk)', 'adl': 'AMD GPU driver (ADL)',
                 'fps': 'PresentMon FPS'}
        lines = [f'<b>{names[k]}:</b> {status.get(k, "starting…")}' for k in names]
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

# SPDX-License-Identifier: GPL-3.0-only
"""Cross-platform lighting studio on the existing serial OpenRGB transport."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import math
from pathlib import Path
import queue
import sys
import time
from PySide6.QtCore import Qt,QTimer,QRectF,QPointF
from PySide6.QtGui import QColor,QPainter,QPen,QLinearGradient
from PySide6.QtWidgets import (QApplication,QWidget,QVBoxLayout,QHBoxLayout,QLabel,
    QPushButton,QComboBox,QSlider,QSpinBox,QDoubleSpinBox,QTabWidget,QFormLayout,
    QFileDialog,QInputDialog,QMessageBox,QTableWidget,QTableWidgetItem,QHeaderView,
    QLineEdit,QCheckBox,QAbstractItemView,QScrollArea,QLayout,QDialog,QPlainTextEdit)
from .ui import Window as BaseWindow,STYLE,label,button,panel
from .hardware import Hardware
from .backend import OpenRGB
from .effects import EFFECTS,render,points,color
from .scenes import SceneStore,validate
from .sources import Metrics,Audio,Events,process_names
from .catalog import import_catalog

class DeskCanvas(QWidget):
    def __init__(self,studio,editable=False):
        super().__init__();self.studio=studio;self.editable=editable
        self.setMinimumSize(420,150 if not editable else 340)
        self.setMouseTracking(True);self.drag=None;self.hit=[]
        self.setAccessibleName('Desk layout and live LED preview')
    def area(self):return QRectF(28,30,self.width()-56,self.height()-65)
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(),QColor('#111622'));area=self.area()
        p.setPen(QPen(QColor('#202a3b'),1))
        for i in range(21):
            x=area.left()+area.width()*i/20;p.drawLine(QPointF(x,area.top()),QPointF(x,area.bottom()))
        for i in range(11):
            y=area.top()+area.height()*i/10;p.drawLine(QPointF(area.left(),y),QPointF(area.right(),y))
        self.hit=[]
        for d in self.studio.devices:
            if not d.supported:continue
            layout=self.studio.layout_for(d);coords=points(d.leds,layout)
            pixels=self.studio.preview_frames.get(d.key,[(60,65,85)]*d.leds)
            selected=d.key in self.studio.selected()
            transformed=[QPointF(area.left()+x*area.width(),area.top()+y*area.height()) for x,y in coords]
            if transformed:
                bounds=QRectF(min(v.x() for v in transformed)-9,min(v.y() for v in transformed)-9,
                              max(v.x() for v in transformed)-min(v.x() for v in transformed)+18,
                              max(v.y() for v in transformed)-min(v.y() for v in transformed)+18)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor('#a99afa' if selected else '#39415b'),1));p.drawRoundedRect(bounds,7,7)
                p.setPen(QColor('#b9c2d8'));p.drawText(QPointF(bounds.left(),bounds.top()-5),d.name[:42])
                self.hit.append((d.key,None,bounds))
            # Render the full frame, with a bounded preview for very large installations.
            step=max(1,math.ceil(d.leds/1200))
            for i in range(0,d.leds,step):
                pos=transformed[i];r=4 if self.editable else 3
                rgb=pixels[i] if i<len(pixels) else (30,30,30)
                p.setPen(Qt.NoPen);p.setBrush(QColor(*rgb));p.drawEllipse(pos,r,r)
                if self.editable:self.hit.append((d.key,i,QRectF(pos.x()-7,pos.y()-7,14,14)))
        p.setPen(QColor('#8f9db9'))
        text='Click an LED to paint • drag a device to arrange' if self.editable else 'Live spatial preview • hardware writes require Start effects'
        if not self.studio.devices:text='Discovering devices…' if self.studio.future else 'No devices detected. Check device access or try demo mode.'
        p.drawText(20,self.height()-12,text)
    def mousePressEvent(self,event):
        if not self.editable:return
        pos=event.position()
        for key,index,bounds in reversed(self.hit):
            if bounds.contains(pos):
                if index is not None and self.studio.paint_mode.isChecked():
                    pixels=self.studio.paint.setdefault(key,{})
                    if event.button()==Qt.RightButton:pixels.pop(str(index),None)
                    else:pixels[str(index)]=self.studio.color
                    self.studio.report('LED override updated. Save a scene to keep it.');self.update();return
                layout=self.studio.layouts[key]
                self.drag=(key,pos,layout['x'],layout['y']);return
    def mouseMoveEvent(self,event):
        if not self.drag:return
        key,start,x,y=self.drag;area=self.area();layout=self.studio.layouts[key]
        layout['x']=min(2,max(-2,x+(event.position().x()-start.x())/area.width()))
        layout['y']=min(2,max(-2,y+(event.position().y()-start.y())/area.height()))
        self.studio.sync_layout_controls();self.update()
    def mouseReleaseEvent(self,event):self.drag=None

class Window(BaseWindow):
    def __init__(self,demo=False,preset_path=None,scene_path=None,auto_hardware=False,hardware=None):
        self.hardware=hardware or Hardware()
        self.layouts={};self.paint={};self.preview_frames={};self.streaming=False
        self.started=time.monotonic();self.sent_frames=0;self.last_send=0;self.last_sample=0
        self.inputs={};self.metrics=Metrics();self.audio=Audio();self.events=Events()
        self.monitor_pool=ThreadPoolExecutor(max_workers=1);self.monitor_future=None
        self.names=set();self.active_rules=set();self.auto_enabled=False;self.rules=[]
        self.screen_enabled=False;self.screen_samples=None;self.last_screen=0
        self.pending_blackout=False
        self.scene_store=SceneStore(scene_path);self.catalog_rows=[];self.loading_layout=False
        super().__init__(demo=demo,preset_path=preset_path)
        self.setWindowTitle('Prism Light · Studio');self.resize(1320,1020)
        page=self.centralWidget().layout()
        page.itemAt(0).widget().setText('PRISM LIGHT    /    STUDIO')
        page.itemAt(1).widget().setText('One desk. Every colour.')
        page.itemAt(2).widget().setText('Spatial effects · OpenRGB devices · Local scenes & automation')
        original=page.takeAt(4)
        self.tabs=QTabWidget();page.insertWidget(4,self.tabs,1)
        lighting=QWidget();lighting_box=QVBoxLayout(lighting);lighting_box.setContentsMargins(0,10,0,0)
        self.live_canvas=DeskCanvas(self);self.live_canvas.setMinimumHeight(205);self.live_canvas.setMaximumHeight(220)
        lighting_box.addWidget(self.live_canvas);lighting_box.addLayout(original.layout(),1)
        self.tabs.addTab(lighting,'Lighting')
        editor=self.preview.parentWidget().layout()
        editor.itemAt(1).widget().setText('Spatial lighting')
        for item in self.preview.parentWidget().findChildren(QLabel):
            if item.text().startswith('Software brightness in Direct mode.'):
                item.setText('Software brightness · Direct per-LED control. Arrange your lights in Desk & LEDs.')
        self.effect=QComboBox();self.effect.addItems(EFFECTS);self.effect.setAccessibleName('Spatial lighting effect')
        editor.insertWidget(2,self.effect)
        self.secondary='#45E0C2'
        self.second_btn=button('Second colour · '+self.secondary,self.choose_secondary);editor.insertWidget(3,self.second_btn)
        controls=QFormLayout()
        self.speed=QSlider(Qt.Horizontal);self.speed.setRange(1,200);self.speed.setValue(50)
        self.angle=QSpinBox();self.angle.setRange(0,360);self.angle.setSuffix('°')
        self.gain=QSpinBox();self.gain.setRange(1,20);self.gain.setValue(4)
        self.fps=QSpinBox();self.fps.setRange(1,30);self.fps.setValue(20)
        for name,w in [('Speed',self.speed),('Direction',self.angle),('Audio gain',self.gain),('Max FPS',self.fps)]:
            controls.addRow(name,w);w.setAccessibleName(name)
        editor.insertLayout(4,controls)
        stream_row=QHBoxLayout();self.start_btn=button('Start effects',self.start_effects,True)
        self.stop_btn=button('Stop',self.stop_effects);self.blackout_btn=button('Blackout',self.blackout)
        for w in (self.start_btn,self.stop_btn,self.blackout_btn):stream_row.addWidget(w)
        editor.insertLayout(5,stream_row)
        self.stream_status=label('Stopped · preview only','sub');editor.insertWidget(6,self.stream_status)
        self.effect.currentTextChanged.connect(self.effect_changed)
        self.build_layout_tab();self.build_sources_tab();self.build_scenes_tab()
        self.build_automation_tab();self.build_catalog_tab();self.build_status_tab()
        self.timer.setInterval(30)
        self.reload_scenes();self.refresh_controls()
        from .shell import Shell
        self.shell=Shell(self)
        self.connect_btn.setText('Scan devices')
        self.disconnect_btn.setText('Release devices')
        self.port.hide()
        for widget in self.shell.connection.findChildren(QLabel):
            if widget.text()=='127.0.0.1 :':widget.hide()
        self.mode_note.setText('Built-in hardware engine · discovery runs automatically at startup.')
        self.apply_btn.setText('Apply solid colour once')
        self.shell.connection.layout().addWidget(button('Hardware diagnostics',self.show_hardware_diagnostics))
        if auto_hardware and not demo:QTimer.singleShot(0,self.connect_live)
    def layout_for(self,d):
        if d.key not in self.layouts:
            n=next((i for i,v in enumerate(self.devices) if v.key==d.key),0)
            keyboard='keyboard' in d.name.casefold()
            rows=max(2,min(4,sum(v.supported for v in self.devices)))
            self.layouts[d.key]={'x':.08,'y':.16+(n%rows)*.8/rows,'width':.8,'height':.48/rows if keyboard else .04,
                                 'rotation':0,'columns':min(18,d.leds) if keyboard else max(1,d.leds)}
        return self.layouts[d.key]
    def settings(self):
        return dict(color=self.color,secondary=self.secondary,brightness=self.brightness.value(),
                    speed=self.speed.value(),angle=self.angle.value(),gain=self.gain.value())
    def scene(self):
        return dict(self.settings(),effect=self.effect.currentText(),devices=self.selected(),layouts=self.layouts,
                    paint=self.paint,scope='demo' if self.backend.demo else 'hardware')
    def build_layout_tab(self):
        w=QWidget();box=QVBoxLayout(w)
        box.addWidget(label('Arrange your desk','title'))
        box.addWidget(label('Drag devices in the canvas. Paint mode adds persistent LED overrides; right-click an LED to erase.','sub'))
        self.layout_canvas=DeskCanvas(self,True);box.addWidget(self.layout_canvas,1)
        row=QHBoxLayout();self.layout_device=QComboBox();row.addWidget(self.layout_device,1)
        self.paint_mode=QCheckBox('Paint LEDs');row.addWidget(self.paint_mode)
        row.addWidget(button('Clear device paint',self.clear_paint));row.addWidget(button('Reset arrangement',self.reset_layout))
        box.addLayout(row);form=QHBoxLayout();self.layout_controls={}
        for name,title,lo,hi in [('x','X',-2,2),('y','Y',-2,2),('width','Width',.01,3),('height','Height',.01,3),('rotation','Rotation',0,360),('columns','Columns',1,65535)]:
            widget=QSpinBox() if name=='columns' else QDoubleSpinBox()
            widget.setRange(lo,hi)
            if name!='columns':widget.setDecimals(2);widget.setSingleStep(.01 if name!='rotation' else 5)
            widget.setAccessibleName('Layout '+title);form.addWidget(label(title));form.addWidget(widget)
            widget.valueChanged.connect(self.change_layout);self.layout_controls[name]=widget
        box.addLayout(form);self.layout_device.currentIndexChanged.connect(self.sync_layout_controls)
        self.tabs.addTab(w,'Desk && LEDs')
    def build_sources_tab(self):
        w=QWidget();box=QVBoxLayout(w)
        box.addWidget(label('Bring the room to life','title'))
        audio,ab=panel();ab.addWidget(label('AUDIO REACTIVITY','eyebrow'))
        ab.addWidget(label('Choose a microphone or an OS-provided monitor/loopback input. Capture starts only when enabled.','sub'))
        self.audio_devices=QComboBox();self.audio_ids=[]
        try:
            from PySide6.QtMultimedia import QMediaDevices
            self.audio_ids=QMediaDevices.audioInputs()
            for d in self.audio_ids:self.audio_devices.addItem(d.description())
        except ImportError:pass
        ab.addWidget(self.audio_devices);ar=QHBoxLayout()
        ar.addWidget(button('Enable audio input',self.start_audio));ar.addWidget(button('Stop audio input',self.stop_audio));ab.addLayout(ar)
        self.audio_note=label('Audio input is off.','sub');ab.addWidget(self.audio_note);box.addWidget(audio)
        screen_panel,sb=panel();sb.addWidget(label('SCREEN AMBIENCE','eyebrow'))
        self.screen_list=QComboBox()
        for screen in QApplication.screens():self.screen_list.addItem(screen.name())
        sb.addWidget(self.screen_list);sr=QHBoxLayout()
        sr.addWidget(button('Enable screen capture',self.start_screen));sr.addWidget(button('Stop screen capture',self.stop_screen));sb.addLayout(sr)
        self.screen_note=label('Capture is off. Windows and X11 use Qt capture. Wayland may require a portal and can return no image.','sub')
        sb.addWidget(self.screen_note);box.addWidget(screen_panel)
        event,eb=panel();eb.addWidget(label('GAME / APP LIGHTING EVENTS','eyebrow'))
        eb.addWidget(label('Opt-in loopback HTTP input for your game mods and scripts. Select the Game event effect to show received colors.','sub'))
        er=QHBoxLayout();er.addWidget(button('Start local event input',self.start_events));er.addWidget(button('Stop local input',self.stop_events));eb.addLayout(er)
        self.event_note=label('Local event input is off.','sub');self.event_note.setTextInteractionFlags(Qt.TextSelectableByMouse);eb.addWidget(self.event_note)
        box.addWidget(event);box.addStretch();box.setSizeConstraint(QLayout.SetMinimumSize)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(w)
        self.tabs.addTab(scroll,'Audio, screen && games')
    def build_scenes_tab(self):
        w=QWidget();box=QVBoxLayout(w);box.addWidget(label('Your lighting library','title'))
        box.addWidget(label('Scenes include the effect, colors, brightness, speed, device selection, layout and painted LEDs. Demo scenes stay separate from hardware scenes.','sub'))
        self.scene_list=QComboBox();box.addWidget(self.scene_list);row=QHBoxLayout()
        for title,fn in [('Save scene…',self.save_scene),('Load scene',self.load_scene),('Delete scene',self.delete_scene),('Export…',self.export_scene),('Import…',self.import_scene)]:row.addWidget(button(title,fn))
        box.addLayout(row);box.addWidget(label('Built-in starters','eyebrow'))
        for title,effect,a,b in [('Aurora desk','Plasma','#B6A7FF','#45E0C2'),('Late night','Breathing','#334D90','#9D6FD9'),('Ember','Fire','#FF6728','#FFC466'),('Focus','Solid','#FFDEA5','#FFFFFF'),('Ocean drift','Gradient','#1167CF','#35E5D0')]:
            box.addWidget(button(title,lambda checked=False,e=effect,c=a,s=b:self.starter(e,c,s)))
        box.addStretch();self.tabs.addTab(w,'Scenes')
    def build_automation_tab(self):
        w=QWidget();box=QVBoxLayout(w);box.addWidget(label('Let lighting follow your day','title'))
        box.addWidget(label('Rules switch saved scenes while this app is open. Application rules match a running executable name, not the foreground window. Time rules run once per matching minute; missed times are not replayed.','sub'))
        self.auto_check=QCheckBox('Enable lighting automation for this session');self.auto_check.toggled.connect(self.enable_automation);box.addWidget(self.auto_check)
        row=QHBoxLayout();self.rule_kind=QComboBox();self.rule_kind.addItems(['Time','Application'])
        self.rule_value=QLineEdit();self.rule_value.setPlaceholderText('HH:MM or executable name (game.exe / game)')
        self.rule_scene=QComboBox()
        for item in (self.rule_kind,self.rule_value,self.rule_scene):row.addWidget(item)
        row.addWidget(button('Add rule',self.add_rule));box.addLayout(row)
        self.rule_table=QTableWidget(0,3);self.rule_table.setHorizontalHeaderLabels(['Trigger','Match','Scene'])
        self.rule_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.rule_table.setSelectionBehavior(QAbstractItemView.SelectRows);self.rule_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        box.addWidget(self.rule_table);box.addWidget(button('Remove selected rule',self.remove_rule))
        box.addWidget(label('A rule loads its scene and starts effects only for currently connected, uniquely identified, supported devices. Capture sources must be enabled separately.','sub'))
        self.tabs.addTab(w,'Automation')
    def build_catalog_tab(self):
        w=QWidget();box=QVBoxLayout(w);box.addWidget(label('Device protocol reference library','title'))
        box.addWidget(label('Import the recovered readable-files ZIP to index plugin names and USB IDs. This reads metadata only. These scripts require SignalRGB host APIs and are not drivers loaded by Prism Light. Hardware control comes from OpenRGB.','sub'))
        row=QHBoxLayout();row.addWidget(button('Import recovered plugins…',self.import_plugins))
        self.catalog_filter=QLineEdit();self.catalog_filter.setPlaceholderText('Filter device name or USB ID');self.catalog_filter.textChanged.connect(self.filter_catalog);row.addWidget(self.catalog_filter,1);box.addLayout(row)
        self.catalog_table=QTableWidget(0,4);self.catalog_table.setHorizontalHeaderLabels(['Device','Vendor ID','Product ID','Compatibility'])
        self.catalog_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch);self.catalog_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        box.addWidget(self.catalog_table);self.catalog_note=label('No reference catalog imported.','sub');box.addWidget(self.catalog_note)
        self.tabs.addTab(w,'Plugin references')
    def build_status_tab(self):
        w=QWidget();box=QVBoxLayout(w);box.addWidget(label('System & feature coverage','title'))
        self.system_note=label('Collecting read-only CPU, memory and available OS sensors…');box.addWidget(self.system_note)
        rows=[('Device control','OpenRGB SDK v3, per-LED direct mode'),('Lighting','17 spatial, reactive and input-driven effects'),
              ('Layouts','Drag, rotate, grid/strip mapping and LED paint'),('Scenes & automation','Local scene library, import/export, time and running-app rules'),
              ('Audio','Explicit selected input; RMS and eight frequency probes'),('Screen ambience','Explicit Qt screen capture; portal-only desktops may be unavailable'),
              ('Game integrations','Authenticated local color-event API; game-specific adapters must send events'),
              ('SignalRGB plugins','Metadata index only; host API / device-driver ports not implemented'),
              ('Cooling','Read-only OS fan RPM and temperatures where available; no fan/pump writes'),
              ('LCD devices','Display-driver integration not implemented'),('Cloud content & remote control','Not implemented; no SignalRGB account, marketplace or paid-content access')]
        table=QTableWidget(len(rows),2);table.setHorizontalHeaderLabels(['Feature','Implementation / limitation'])
        for i,row in enumerate(rows):
            for j,text in enumerate(row):table.setItem(i,j,QTableWidgetItem(text))
        table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents);table.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch)
        table.verticalHeader().setDefaultSectionSize(44);table.setWordWrap(True);table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        box.addWidget(table);box.addWidget(label('Physical LEDs and Windows hardware need validation on the target PC. Preview and protocol tests do not establish device compatibility.','sub'))
        self.tabs.addTab(w,'System && coverage')
    def refresh_controls(self):
        super().refresh_controls()
        if not hasattr(self,'start_btn'):return
        self.start_btn.setEnabled(self.connected and bool(self.selected()) and not self.streaming and self.future is None)
        self.stop_btn.setEnabled(self.streaming)
        self.blackout_btn.setEnabled(self.connected and bool(self.selected()))
        # Source/connection switching during an in-flight write stays serialized.
        if self.streaming:
            for w in (self.connect_btn,self.demo_btn,self.disconnect_btn,self.apply_btn):w.setEnabled(False)
    def render_devices(self):
        super().render_devices()
        if not self.devices:
            for widget in self.centralWidget().findChildren(QLabel):
                if widget.text().startswith('Your lighting will appear here.'):
                    widget.setText('Your devices appear here after automatic discovery.\nUse Scan devices to retry, or enter demo mode.')
        if hasattr(self,'layout_device'):
            self.layout_device.clear()
            for d in self.devices:
                if d.supported:self.layout_for(d);self.layout_device.addItem(d.name,d.key)
            self.sync_layout_controls();self.reload_scenes()
    def did_connect(self,devices):
        super().did_connect(devices)
        if hasattr(self,'stream_status'):self.stream_status.setText('Stopped · preview only')
        if not self.backend.demo:
            self.status.setText(f'●  Hardware ready · {len(devices)} device(s)')
            self.mode_note.setText('Bundled OpenRGB engine · managed by Prism Light')
            self.report('Hardware ready. Select an effect and press Play.' if devices else
                'No devices detected. Check USB access permissions and supported hardware in Devices.')
    def connect_live(self):
        if self.future:return
        self.disconnect();self.backend=OpenRGB()
        self.status.setText('◌  Discovering your devices…')
        self.mode_note.setText('Starting the built-in hardware engine…')
        self.job(lambda:self.hardware.connect(self.backend),self.did_connect)
    def connection_error(self,error):
        self.report(f'{error} Open Devices to retry discovery or view hardware diagnostics.',True)
    def show_hardware_diagnostics(self):
        dialog=QDialog(self);dialog.setWindowTitle('Hardware diagnostics');dialog.resize(780,520)
        layout=QVBoxLayout(dialog)
        layout.addWidget(label('The hardware engine is included in Prism Light. Device access still depends on your operating system and supported hardware.'))
        output=QPlainTextEdit();output.setReadOnly(True);output.setPlainText(self.hardware.diagnostics());layout.addWidget(output)
        if sys.platform.startswith('linux'):
            layout.addWidget(label('If the log reports missing udev rules, install device-access rules once, reconnect USB devices, then scan again.'))
            def copy_setup():
                import shlex
                try:
                    exe=shlex.quote(str(self.hardware.executable))
                    command='mkdir -p "$HOME/.config/PrismLight" && '+exe+' --generate-udev-rules "$HOME/.config/PrismLight/60-openrgb.rules" && sudo install -D -m 644 "$HOME/.config/PrismLight/60-openrgb.rules" /etc/udev/rules.d/60-openrgb.rules && sudo udevadm control --reload-rules && sudo udevadm trigger'
                    QApplication.clipboard().setText(command);self.report('Linux device-access command copied. Run it in your terminal, then scan again.')
                except Exception as e:self.report(str(e),True)
            layout.addWidget(button('Copy Linux device-access setup command',copy_setup))
        else:layout.addWidget(label('Some Windows motherboard/RAM controllers require the upstream PawnIO driver and administrator setup. Bundling the engine does not install a kernel driver.'))
        layout.addWidget(button('Close',dialog.accept));dialog.exec()
    def sync_layout_controls(self,*args):
        key=self.layout_device.currentData()
        if not key or key not in self.layouts:return
        self.loading_layout=True
        for name,w in self.layout_controls.items():w.setValue(self.layouts[key][name])
        self.loading_layout=False
    def change_layout(self,*args):
        if self.loading_layout:return
        key=self.layout_device.currentData()
        if key:self.layouts[key]={name:w.value() for name,w in self.layout_controls.items()}
    def clear_paint(self):self.paint.pop(self.layout_device.currentData(),None)
    def reset_layout(self):
        self.layouts={}
        for d in self.devices:self.layout_for(d)
        self.sync_layout_controls()
    def choose_secondary(self):
        from PySide6.QtWidgets import QColorDialog
        c=QColorDialog.getColor(QColor(self.secondary),self,'Second effect colour')
        if c.isValid():self.secondary=c.name().upper();self.second_btn.setText('Second colour · '+self.secondary)
    def effect_changed(self,*args):
        name=self.effect.currentText()
        if name.startswith('Audio') and not self.audio.source:self.report('Enable audio input in Audio, screen & games for this effect.')
        elif name=='Screen ambience' and not self.screen_enabled:self.report('Enable screen capture in Audio, screen & games for this effect.')
        elif name=='Game event' and not self.events.server:self.report('Start local event input to receive colors from your own game adapter.')
    def start_effects(self):
        if not self.connected or not self.selected():self.report('Connect and select a supported device first.',True);return
        if self.future:self.report('Wait for the current device operation to finish.');return
        self.streaming=True;self.sent_frames=0;self.refresh_controls();self.report('Effects started. Stop leaves the last frame; Blackout sends black.')
    def stop_effects(self):
        self.streaming=False;self.stream_status.setText('Stopped · last lighting frame may remain');self.refresh_controls()
    def blackout(self):
        self.stop_effects()
        if self.future:
            self.pending_blackout=True
            self.report('Blackout queued after the current frame.');return
        self.pending_blackout=False
        frames={d.key:[(0,0,0)]*d.leds for d in self.devices if d.key in self.selected()}
        self.job(lambda:self.backend.apply_frame(frames),lambda n:self.report(f'Black frame sent to {n} device(s).'))
    def disconnect(self):
        self.streaming=False
        self.pending_blackout=False
        super().disconnect()
        self.hardware.close()
        self.mode_note.setText('Built-in hardware engine · press Scan devices to discover hardware.')
        if hasattr(self,'stream_status'):self.stream_status.setText('Stopped · disconnected')
    def tick(self):
        super().tick()
        if not hasattr(self,'effect'):return
        now=time.monotonic();elapsed=now-self.started
        if not self.connected and self.streaming:self.stop_effects()
        if self.pending_blackout and self.connected and not self.future:self.blackout()
        if self.monitor_future and self.monitor_future.done():
            try:
                metrics,self.names=self.monitor_future.result();self.inputs['cpu']=metrics['cpu']
                sensors=' · '.join(f'{n}: {v:.0f}°C' for n,v in metrics['temperatures'][:5])
                fans=' · '.join(f'{n}: {v} RPM' for n,v in metrics['fans'][:5])
                self.system_note.setText(f"CPU {metrics['cpu']:.0f}% · Memory {metrics['memory']:.0f}%\n{sensors or 'No OS temperature sensors available.'}\n{fans or 'No OS fan sensors available.'}")
            except Exception as e:self.system_note.setText('System metrics unavailable: '+str(e))
            self.monitor_future=None
        if now-self.last_sample>2 and self.monitor_future is None:
            self.last_sample=now;auto=self.auto_enabled
            self.monitor_future=self.monitor_pool.submit(lambda:(self.metrics.sample(),process_names() if auto else set()))
        if self.audio.source:
            if now-getattr(self.audio,'last_sample',now)>1:self.audio.level=0;self.audio.bands=[0]*8
            self.inputs.update(audio=self.audio.level,bands=list(self.audio.bands))
            self.audio_note.setText(f'{self.audio.error} · input level {self.audio.level:.3f}')
        else:self.inputs.update(audio=0,bands=[0]*8)
        if self.screen_enabled and now-self.last_screen>.15:self.capture_screen();self.last_screen=now
        self.inputs['screen']=self.screen_samples
        try:
            while True:
                event=self.events.events.get_nowait();self.inputs.update(event_color=event['color'],event_time=elapsed)
        except queue.Empty:pass
        self.evaluate_rules()
        settings=self.settings();self.preview_frames={}
        for d in self.devices:
            if not d.supported:continue
            pixels=render(self.effect.currentText(),points(d.leds,self.layout_for(d)),elapsed,settings,self.inputs)
            for index,value in self.paint.get(d.key,{}).items():
                i=int(index)
                if i<len(pixels):pixels[i]=tuple(round(v*settings['brightness']/100) for v in color(value))
            self.preview_frames[d.key]=pixels
        self.live_canvas.update();self.layout_canvas.update()
        if self.streaming and not self.future and now-self.last_send>=1/self.fps.value():
            frames={k:v for k,v in self.preview_frames.items() if k in self.selected()}
            if not frames:self.stop_effects();self.report('No supported devices selected. Effects stopped.');return
            self.last_send=now
            self.job(lambda:self.backend.apply_frame(frames),self.did_frame)
    def did_frame(self,n):
        self.sent_frames+=1
        scope='DEMO · simulated' if self.backend.demo else 'OpenRGB · sent, unacknowledged'
        self.stream_status.setText(f'{scope} · {self.sent_frames} frames · {n} device(s)')
    def start_audio(self):
        try:
            if not self.audio_ids:raise ValueError('No audio input devices found.')
            self.audio.start(self.audio_ids[self.audio_devices.currentIndex()]);self.audio_note.setText(self.audio.error)
        except Exception as e:self.audio_note.setText(str(e));self.report(str(e),True)
    def stop_audio(self):self.audio.stop();self.audio_note.setText('Audio input is off.')
    def start_screen(self):
        self.screen_enabled=True;self.capture_screen()
    def stop_screen(self):self.screen_enabled=False;self.screen_samples=None;self.screen_note.setText('Screen capture is off.')
    def capture_screen(self):
        screens=QApplication.screens();i=self.screen_list.currentIndex()
        if not 0<=i<len(screens):self.stop_screen();return
        image=screens[i].grabWindow(0).toImage()
        if image.isNull():
            self.screen_samples=None;self.screen_enabled=False
            self.screen_note.setText('Screen capture unavailable on this desktop. On Wayland a portal-backed capture adapter is needed.');return
        image=image.scaled(24,14,Qt.IgnoreAspectRatio,Qt.SmoothTransformation)
        self.screen_samples=[[image.pixelColor(x,y).getRgb()[:3] for x in range(24)] for y in range(14)]
        self.screen_note.setText('Capturing '+screens[i].name()+' · 24 × 14 color grid · stop capture here when finished.')
    def start_events(self):
        try:
            port=self.events.start()
            self.event_note.setText(f'POST http://127.0.0.1:{port}/event\nAuthorization: Bearer {self.events.token}\nJSON body: {{"color":"#FF6F91"}}')
        except OSError as e:self.report(str(e),True)
    def stop_events(self):self.events.close();self.event_note.setText('Local event input is off.')
    def reload_scenes(self):
        if not hasattr(self,'scene_list'):return
        current=self.scene_list.currentText();self.scene_list.clear();self.rule_scene.clear()
        try:
            data=self.scene_store.read();scope='demo' if self.backend.demo else 'hardware'
            for name,scene in sorted(data['scenes'].items()):
                if scene['scope']==scope:self.scene_list.addItem(name);self.rule_scene.addItem(name)
            self.scene_list.setCurrentText(current);self.rules=data['rules'];self.render_rules()
        except Exception as e:self.report(str(e),True)
    def save_scene(self):
        name,ok=QInputDialog.getText(self,'Save scene','Scene name')
        if not ok:return
        try:
            if name.strip() in self.scene_store.read()['scenes'] and QMessageBox.question(self,'Replace scene?',f'Replace {name.strip()}?')!=QMessageBox.Yes:return
            self.scene_store.save(name,self.scene());self.reload_scenes();self.scene_list.setCurrentText(name.strip());self.report('Scene saved locally.')
        except Exception as e:self.report(str(e),True)
    def apply_scene(self,s):
        validate(s)
        if s['scope']!=('demo' if self.backend.demo else 'hardware'):raise ValueError('This scene belongs to a different demo/hardware scope.')
        self.set_color(s['color']);self.secondary=s['secondary'];self.second_btn.setText('Second colour · '+self.secondary)
        self.effect.setCurrentText(s['effect']);self.brightness.setValue(s['brightness'])
        self.speed.setValue(s['speed']);self.angle.setValue(s['angle']);self.gain.setValue(s['gain'])
        self.layouts=json.loads(json.dumps(s.get('layouts',{})));self.paint=json.loads(json.dumps(s.get('paint',{})))
        for key,check in self.checks.items():check.setChecked(key in s['devices'] and bool(check.property('supported')))
        for d in self.devices:self.layout_for(d)
        self.sync_layout_controls()
    def load_scene(self):
        try:self.apply_scene(self.scene_store.read()['scenes'][self.scene_list.currentText()]);self.report('Scene loaded. Start effects to send it to selected devices.')
        except Exception as e:self.report(str(e),True)
    def delete_scene(self):
        name=self.scene_list.currentText()
        if not name or QMessageBox.question(self,'Delete scene?',f'Delete {name}?')!=QMessageBox.Yes:return
        try:
            data=self.scene_store.read();data['scenes'].pop(name,None);data['rules']=[r for r in data['rules'] if r['scene']!=name];self.scene_store.write(data);self.reload_scenes()
        except Exception as e:self.report(str(e),True)
    def export_scene(self):
        path,_=QFileDialog.getSaveFileName(self,'Export current scene','prism-scene.json','JSON (*.json)')
        if path:
            try:Path(path).write_text(json.dumps({'version':1,'scene':self.scene()},indent=2),encoding='utf-8');self.report('Scene exported.')
            except OSError as e:self.report(str(e),True)
    def import_scene(self):
        path,_=QFileDialog.getOpenFileName(self,'Import scene','','JSON (*.json)')
        if not path:return
        try:
            if Path(path).stat().st_size>4_000_000:raise ValueError('Scene file too large.')
            data=json.loads(Path(path).read_text(encoding='utf-8'))
            if data.get('version')!=1:raise ValueError('Unsupported scene version.')
            self.apply_scene(data['scene']);self.report('Imported to editor. Save scene to add it to your library.')
        except Exception as e:self.report(str(e),True)
    def starter(self,effect,a,b):
        self.effect.setCurrentText(effect);self.set_color(a);self.secondary=b;self.second_btn.setText('Second colour · '+b);self.tabs.setCurrentIndex(0)
    def enable_automation(self,on):self.auto_enabled=on;self.active_rules=set()
    def add_rule(self):
        kind=self.rule_kind.currentText();value=self.rule_value.text().strip();scene=self.rule_scene.currentText()
        try:
            if not scene:raise ValueError('Save a scene for this connection scope first.')
            if kind=='Time':
                parsed=datetime.strptime(value,'%H:%M');value=parsed.strftime('%H:%M')
            elif not value or '/' in value or '\\' in value:raise ValueError('Use an executable name, not a path.')
            data=self.scene_store.read()
            if len(data['rules'])>=100:raise ValueError('Maximum 100 rules.')
            data['rules'].append({'kind':kind,'value':value,'scene':scene});self.scene_store.write(data);self.reload_scenes()
        except Exception as e:self.report(str(e),True)
    def render_rules(self):
        if not hasattr(self,'rule_table'):return
        self.rule_table.setRowCount(len(self.rules))
        for i,r in enumerate(self.rules):
            for j,k in enumerate(('kind','value','scene')):self.rule_table.setItem(i,j,QTableWidgetItem(r[k]))
    def remove_rule(self):
        index=self.rule_table.currentRow()
        if index<0:return
        try:
            data=self.scene_store.read();data['rules'].pop(index);self.scene_store.write(data);self.reload_scenes();self.active_rules=set()
        except Exception as e:self.report(str(e),True)
    def evaluate_rules(self):
        if not self.auto_enabled or not self.connected or self.future:return
        current=datetime.now().strftime('%H:%M');active=set()
        for i,r in enumerate(self.rules):
            match=r['value']==current if r['kind']=='Time' else r['value'].casefold() in self.names
            if not match:continue
            active.add(i)
            if i in self.active_rules:continue
            try:
                s=self.scene_store.read()['scenes'][r['scene']]
                if s['scope']!=('demo' if self.backend.demo else 'hardware'):continue
                self.apply_scene(s);self.start_effects();self.report('Automation applied scene: '+r['scene'])
            except Exception as e:self.report('Automation: '+str(e),True)
        self.active_rules=active
    def import_plugins(self):
        path,_=QFileDialog.getOpenFileName(self,'Read plugin references','','Recovered archive (*.zip);;JavaScript (*.js)')
        if not path:return
        try:self.catalog_rows=import_catalog(path);self.filter_catalog();self.catalog_note.setText(f'{len(self.catalog_rows)} references indexed; none executed. OpenRGB hardware support is unchanged.')
        except Exception as e:self.report(str(e),True)
    def filter_catalog(self,*args):
        needle=self.catalog_filter.text().casefold()
        rows=[r for r in self.catalog_rows if needle in ' '.join(r.values()).casefold()]
        self.catalog_table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            for j,key in enumerate(('name','vendor','product','status')):self.catalog_table.setItem(i,j,QTableWidgetItem(r[key]))
    def closeEvent(self,event):
        self.streaming=False;self.audio.stop();self.events.close()
        self.hardware.cancel.set()
        self.monitor_pool.shutdown(wait=True,cancel_futures=True)
        super().closeEvent(event)
        self.hardware.close()

def main():
    parser=argparse.ArgumentParser(description='Prism Light Studio: local spatial RGB control')
    parser.add_argument('--demo',action='store_true');parser.add_argument('--smoke-test',action='store_true')
    parser.add_argument('--screenshot',help='Save a demo screenshot and exit')
    parser.add_argument('--hardware-smoke-test',action='store_true',help='Verify bundled engine discovery and cleanup without lighting writes')
    parser.add_argument('--hardware-config',help='Override the hardware settings directory')
    args=parser.parse_args();app=QApplication(sys.argv[:1]);app.setStyle('Fusion');app.setStyleSheet(STYLE+'''\nQTabWidget::pane { border:0; } QTabBar::tab { background:#191e2d; color:#a8b5d0; padding:12px 14px; border-bottom:2px solid transparent; } QTabBar::tab:selected { color:#d9ccff; border-bottom:2px solid #b6a7ff; } QTableWidget { background:#151b29; gridline-color:#30374c; } QHeaderView::section { background:#252d42; padding:10px; border:0; }''')
    window=Window(demo=args.demo or args.smoke_test or bool(args.screenshot),auto_hardware=not(args.demo or args.smoke_test or args.screenshot),hardware=Hardware(config=args.hardware_config));window.show()
    if args.hardware_smoke_test:
        def hardware_check():
            if window.future:return
            valid=window.connected and not window.backend.demo and window.hardware.process is not None
            window.close();app.exit(0 if valid else 1)
        check_timer=QTimer(window);check_timer.timeout.connect(hardware_check);check_timer.start(100)
        QTimer.singleShot(50000,lambda:(window.close(),app.exit(1)))
    if args.smoke_test or args.screenshot:
        def smoke():
            valid=window.connected and window.backend.demo and len(window.devices)==3
            if args.screenshot:
                window.effect.setCurrentText('Rainbow wave');window.tick();window.grab().save(args.screenshot)
            window.close();app.exit(0 if valid else 1)
        QTimer.singleShot(800,smoke)
    return app.exec()

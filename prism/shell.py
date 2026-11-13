# SPDX-License-Identifier: GPL-3.0-only
"""Gallery-first desktop shell. Artwork is painted locally from original code."""
import math
from PySide6.QtCore import Qt,QRectF,QPointF
from PySide6.QtGui import QColor,QPainter,QPixmap,QLinearGradient,QRadialGradient,QPen
from PySide6.QtWidgets import (QWidget,QFrame,QLabel,QPushButton,QVBoxLayout,QHBoxLayout,
    QGridLayout,QScrollArea,QStackedWidget,QLineEdit,QButtonGroup,QSlider,QSizePolicy)
from .effects import EFFECTS

THEME='''
QWidget { background:#090e16; color:#e8eaf3; font-family:"Inter","Segoe UI",sans-serif; font-size:13px; }
QFrame#sidebar { background:#101a25; border-right:1px solid #273444; }
QFrame#sidebar QLabel { background:transparent; }
QLabel#brand { color:#edf0ff; font-size:20px; font-weight:800; letter-spacing:2px; }
QLabel#brandSub { color:#62748b; font-size:10px; letter-spacing:3px; }
QPushButton#nav { text-align:left; padding:12px 15px; background:transparent; color:#b9c6d8; border:0; border-radius:7px; font-weight:600; }
QPushButton#nav:hover { background:#1a2939; color:white; }
QPushButton#nav:checked { background:#253543; color:#f1f4ff; }
QFrame#topbar { background:#0c131e; border-bottom:1px solid #253146; }
QFrame#player { background:#0b121b; border-top:1px solid #2b3648; }
QFrame#player QLabel { background:transparent; }
QLabel#notice { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #6a3f73,stop:.5 #635381,stop:1 #354a7e); padding:9px; color:#eee9ff; font-size:12px; }
QLabel#pageTitle { font-size:24px; font-weight:750; }
QLabel#sectionTitle { font-size:20px; font-weight:700; background:transparent; }
QLabel#muted { color:#8d9cb3; background:transparent; }
QPushButton { background:#141d2c; border:1px solid #34405b; border-radius:7px; padding:9px 15px; }
QPushButton:hover { background:#222e45; border-color:#8f9eff; }
QPushButton#primary { background:#a89bff; border:0; color:#17132b; font-weight:700; }
QPushButton#chip { border-radius:18px; padding:8px 16px; background:transparent; border:1px solid #2e3c58; color:#aab8d0; }
QPushButton#chip:checked { background:#242b48; border-color:#8996ff; color:#d9ddff; }
QPushButton#transport { background:transparent; border:0; font-size:24px; padding:5px 14px; color:#e1e8fa; }
QPushButton#transport:hover { background:#1e2a3c; }
QPushButton:disabled { color:#536075; background:#111927; border-color:#233044; }
QFrame#effectCard { background:#111928; border:1px solid #2a3851; border-radius:10px; }
QFrame#effectCard QLabel { background:transparent; border:0; }
QLabel#badge { color:#eaa8d0; border:1px solid #9b5882; border-radius:4px; padding:3px 6px; font-size:10px; }
QFrame#panel { background:#111a28; border:1px solid #29364a; border-radius:10px; }
QFrame#panel QLabel,QFrame#panel QCheckBox { background:transparent; }
QLabel#title { font-size:27px; font-weight:700; }
QLabel#sub,QLabel#eyebrow { color:#96a5bc; }
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox { background:#0d1523; color:#dce2f1; border:1px solid #30405b; border-radius:6px; padding:8px; }
QScrollArea { border:0; background:transparent; }
QScrollBar:vertical { background:#0c131f; width:7px; margin:2px; }
QScrollBar::handle:vertical { background:#35465d; border-radius:3px; min-height:28px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QSlider::groove:horizontal { background:#2a354b; height:5px; border-radius:2px; }
QSlider::sub-page:horizontal { background:#a69bff; border-radius:2px; }
QSlider::handle:horizontal { background:#ddd6ff; width:13px; margin:-4px 0; border-radius:6px; }
QTabWidget::pane { border:0; }
QTableWidget { background:#101927; gridline-color:#243249; }
QHeaderView::section { background:#1b293d; border:0; padding:10px; }
QCheckBox::indicator { width:18px; height:18px; }
'''

LOOKS=[
    ('Aurora Waves','Plasma','#B6A7FF','#45E0C2','Ambient','Soft ribbons of color, flowing across your whole setup.'),
    ('Ember Glow','Fire','#FF6728','#FFC466','Ambient','Warm, flickering light for the quieter hours.'),
    ('Pastel Drift','Gradient','#E9A7DC','#85E7EB','Ambient','A soft blend of mint, pink and lilac.'),
    ('Neon Current','Rainbow wave','#956BFF','#36E8DD','Patterns','A spectrum that travels through your desk.'),
    ('Bubblegum','Ripple','#ED79B4','#853B84','Patterns','Playful rings of rose and violet.'),
    ('Visor','Scanner','#F00083','#10176C','Patterns','A sharp sweep of light, back and forth.'),
    ('After Hours','Breathing','#7074DE','#241A50','Ambient','A slow breath of indigo.'),
    ('Sound Bloom','Audio pulse','#7A6AFF','#FF7FB3','Reactive','Let your audio input set the pulse.'),
    ('Spectrum','Audio spectrum','#43DDCD','#9775FF','Reactive','Eight frequency probes, painted in color.'),
    ('Screen Flow','Screen ambience','#50C9D1','#7065D7','Reactive','Bring your display colors into the room.'),
    ('Starlight','Twinkle','#C4D9FF','#AA87F7','Ambient','Small sparks in a quiet night sky.'),
    ('Orbit','Comet','#68C8ED','#815AE2','Patterns','One bright trail across your lighting.'),
    ('Chromatic','Color cycle','#E978B3','#52DBD0','Ambient','A steady journey around the color wheel.'),
    ('Checkmate','Checkerboard','#937BF0','#45CFC4','Patterns','A moving grid with a little contrast.'),
    ('Focus','Solid','#FFDEA5','#FFFFFF','Ambient','A single warm color. Less distraction.'),
    ('System Pulse','CPU meter','#4EC8C0','#F38084','Reactive','A live view of your CPU load.'),
    ('Game Signal','Game event','#FF6F91','#7462DC','Reactive','Color events from your own game adapter.'),
]

def text(value,name=None):
    w=QLabel(value);w.setWordWrap(True);w.setTextFormat(Qt.PlainText)
    if name:w.setObjectName(name)
    return w

def artwork(effect,width=600,height=240):
    pix=QPixmap(width,height);pix.fill(QColor('#101624'))
    p=QPainter(pix);p.setRenderHint(QPainter.Antialiasing)
    palette={'Fire':['#210d23','#c83642','#ffb23b'], 'Gradient':['#84dde4','#c6abe9','#f6aaca'],
             'Ripple':['#381b47','#b75296','#ed99bf'], 'Scanner':['#11134c','#720b66','#fa0087'],
             'Audio pulse':['#101e42','#6339cb','#f77dd1'], 'Solid':['#2d2443','#e0ac85','#ffdfa8']}
    colors=palette.get(effect,['#111b43','#6c35b9','#13bfa0'])
    bg=QLinearGradient(0,height,width,0)
    for at,c in zip([0,.5,1],colors):bg.setColorAt(at,QColor(c))
    p.fillRect(pix.rect(),bg)
    if effect=='Scanner':
        p.fillRect(QRectF(width*.36,0,width*.18,height),QColor('#ff008c'))
        for i in range(24):
            p.setPen(QPen(QColor(170,90,200,30),1));p.drawLine(int(i*width/24),0,int(i*width/24-width*.12),height)
    elif effect in ('Ripple','Twinkle'):
        for i in range(38):
            x=(math.sin(i*17.1)+1)*width/2;y=(math.cos(i*6.3)+1)*height/2;r=8+(i%6)*7
            g=QRadialGradient(x,y,r);g.setColorAt(0,QColor(255,200,233,130));g.setColorAt(1,QColor(255,200,233,0))
            p.setBrush(g);p.setPen(Qt.NoPen);p.drawEllipse(QPointF(x,y),r,r)
    else:
        for i in range(15):
            x=(math.sin(i*1.2)+1)*width/2;y=(math.cos(i*2.1)+1)*height/2;r=height*(.35+(i%4)*.15)
            c=QColor(colors[i%3]);c.setAlpha(100)
            g=QRadialGradient(x,y,r);g.setColorAt(0,c);c=QColor(c);c.setAlpha(0);g.setColorAt(1,c)
            p.setBrush(g);p.setPen(Qt.NoPen);p.drawEllipse(QPointF(x,y),r,r)
        for line in range(8):
            c=QColor('#d4d0ff');c.setAlpha(22);p.setPen(QPen(c,1))
            previous=None
            for x in range(0,width+4,4):
                y=height*.5+math.sin(x/width*5+line*.22)*height*.3+line*3
                point=QPointF(x,y)
                if previous:p.drawLine(previous,point)
                previous=point
    shade=QLinearGradient(0,0,0,height);shade.setColorAt(0,QColor(3,8,18,0));shade.setColorAt(1,QColor(3,8,18,100));p.fillRect(pix.rect(),shade)
    p.end();return pix

class Art(QWidget):
    def __init__(self,effect,parent=None):
        super().__init__(parent);self.effect=effect;self.image=artwork(effect)
        self.setMinimumHeight(145);self.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Expanding)
    def paintEvent(self,event):
        p=QPainter(self);p.drawPixmap(self.rect(),self.image)

class Hero(Art):
    def __init__(self,look,callback):
        super().__init__(look[1]);self.setMinimumHeight(205)
        box=QVBoxLayout(self);box.setContentsMargins(24,20,24,20)
        row=QHBoxLayout();row.addStretch();badge=text(' INCLUDED ');badge.setStyleSheet('color:#93e8e1;background:#143c49;border:1px solid #35717e;border-radius:4px;padding:4px;font-size:10px;');row.addWidget(badge);box.addLayout(row);box.addStretch()
        title=text(look[0]);title.setStyleSheet('font-size:27px;font-weight:750;background:transparent;');box.addWidget(title)
        subtitle=text(look[5]);subtitle.setStyleSheet('background:transparent;color:#c0cbd8;');box.addWidget(subtitle)
        row=QHBoxLayout();b=QPushButton('View effect  →');b.clicked.connect(callback);row.addWidget(b);row.addStretch();box.addLayout(row)

class Shell:
    def __init__(self,w):
        self.w=w;self.category='All effects';self.cards=[];self.active_name='Solid';self.nav_buttons=[];self.nav_map={}
        # Retain the existing editors and their signals; replace their outer shell.
        old=w.takeCentralWidget();old_layout=old.layout()
        self.connection=old_layout.itemAt(3).widget();old_layout.removeWidget(self.connection)
        old_layout.removeWidget(w.tabs);old_layout.removeWidget(w.feedback)
        self.tabs=w.tabs;self.tabs.tabBar().hide()
        root=QWidget();root.setObjectName('desktopRoot');w.setCentralWidget(root)
        rootbox=QHBoxLayout(root);rootbox.setSpacing(0);rootbox.setContentsMargins(0,0,0,0)
        side=QFrame();side.setObjectName('sidebar');side.setFixedWidth(200);nav=QVBoxLayout(side);nav.setContentsMargins(12,24,12,18);nav.setSpacing(6)
        nav.addWidget(text('PRISM LIGHT','brand'));nav.addWidget(text('LIGHTING STUDIO','brandSub'));nav.addSpacing(27)
        self.group=QButtonGroup(root);self.group.setExclusive(True)
        items=[('◈','Discover',None),('▦','Library',3),('✧','Lighting',0),('▤','Devices',0),('⌘','Layouts',1),('⌁','Macros',4),('⌇','Monitoring',6),('◉','Audio & screen',2),('◇','Plugin references',5)]
        for icon,name,target in items:
            b=QPushButton(f'{icon}   {name.replace("&","&&")}');b.setObjectName('nav');b.setCheckable(True)
            b.clicked.connect(lambda checked=False,t=target,n=name:self.navigate(t,n));self.group.addButton(b);nav.addWidget(b);self.nav_buttons.append(b);self.nav_map[name]=b
        self.nav_buttons[0].setChecked(True);nav.addStretch()
        self.side_status=text('○  Offline','muted');nav.addWidget(self.side_status)
        b=QPushButton('⚙   Connection');b.setObjectName('nav');b.clicked.connect(self.show_connection);nav.addWidget(b)
        version=text('LOCAL FIRST','brandSub');nav.addWidget(version);rootbox.addWidget(side)
        right=QWidget();rightbox=QVBoxLayout(right);rightbox.setContentsMargins(0,0,0,0);rightbox.setSpacing(0);rootbox.addWidget(right,1)
        top=QFrame();top.setObjectName('topbar');topbox=QHBoxLayout(top);topbox.setContentsMargins(24,9,24,9)
        self.breadcrumb=text('Discover','muted');topbox.addWidget(self.breadcrumb);topbox.addStretch()
        self.connection_button=QPushButton('○  Connect devices');self.connection_button.clicked.connect(self.show_connection);topbox.addWidget(self.connection_button)
        rightbox.addWidget(top)
        notice=text('Your space, in sync.  All effects included.  Everything stays on this computer.','notice');notice.setAlignment(Qt.AlignCenter);rightbox.addWidget(notice)
        self.connection.setVisible(False);rightbox.addWidget(self.connection)
        self.stack=QStackedWidget();rightbox.addWidget(self.stack,1)
        self.discover=self.build_discover();self.stack.addWidget(self.discover);self.stack.addWidget(self.tabs)
        w.feedback.setContentsMargins(20,4,20,4);w.feedback.setMinimumHeight(28);rightbox.addWidget(w.feedback)
        self.build_player(rightbox)
        old.deleteLater();w.resize(1500,960);w.setMinimumSize(1080,760)
        w.setStyleSheet(THEME)
        w.effect.currentTextChanged.connect(self.effect_selected);w.brightness.valueChanged.connect(self.volume.setValue)
        w.timer.timeout.connect(self.refresh)
    def navigate(self,index,name):
        self.breadcrumb.setText(name)
        if name in self.nav_map:self.nav_map[name].setChecked(True)
        if index is None:self.stack.setCurrentIndex(0)
        else:self.tabs.setCurrentIndex(index);self.stack.setCurrentIndex(1)
        if name=='Devices':self.connection.setVisible(True)
    def show_connection(self):
        self.connection.setVisible(not self.connection.isVisible())
    def build_discover(self):
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        body=QWidget();box=QVBoxLayout(body);box.setContentsMargins(26,22,26,28);box.setSpacing(22)
        filters=QHBoxLayout();filters.addWidget(text('Discover','pageTitle'));filters.addSpacing(9)
        group=QButtonGroup(body);group.setExclusive(True)
        for name in ['All effects','Ambient','Reactive','Patterns']:
            b=QPushButton(name);b.setObjectName('chip');b.setCheckable(True);b.setChecked(name=='All effects')
            b.clicked.connect(lambda checked=False,n=name:self.set_category(n));group.addButton(b);filters.addWidget(b)
        filters.addStretch();self.search=QLineEdit();self.search.setPlaceholderText('Search effects');self.search.setMaximumWidth(240);self.search.textChanged.connect(self.filter_cards);filters.addWidget(self.search)
        box.addLayout(filters);box.addWidget(text('Spotlight','sectionTitle'))
        self.heroes=QHBoxLayout();self.heroes.setSpacing(16);box.addLayout(self.heroes)
        dots=QHBoxLayout();dots.addStretch();self.pagers=[]
        for index in range(5):
            b=QPushButton('●');b.setFixedSize(22,18);b.setAccessibleName(f'Spotlight page {index+1}')
            b.clicked.connect(lambda checked=False,i=index:self.spotlight(i));dots.addWidget(b);self.pagers.append(b)
        dots.addStretch();box.addLayout(dots);self.spotlight(0)
        row=QHBoxLayout();row.addWidget(text('Included RGB effects','sectionTitle'));row.addStretch();row.addWidget(text('17 looks  ·  ready to customize','muted'));box.addLayout(row)
        self.grid=QGridLayout();self.grid.setSpacing(16);box.addLayout(self.grid)
        for look in LOOKS:
            card=QFrame();card.setObjectName('effectCard');card.setMinimumWidth(190)
            cb=QVBoxLayout(card);cb.setContentsMargins(0,0,0,12);cb.setSpacing(10)
            art=Art(look[1]);art.setFixedHeight(160);cb.addWidget(art)
            row=QHBoxLayout();row.setContentsMargins(13,0,13,0);row.addWidget(text(look[0]));row.addStretch();row.addWidget(text('INCLUDED','badge'));cb.addLayout(row)
            row=QHBoxLayout();row.setContentsMargins(13,0,13,0);row.addWidget(text(look[4],'muted'));row.addStretch()
            b=QPushButton('Use effect  +');b.setAccessibleName('Use '+look[0]);b.clicked.connect(lambda checked=False,l=look:self.choose(l));row.addWidget(b);cb.addLayout(row)
            self.cards.append((look,card))
        self.empty=text('No effects match your search.','muted');box.addWidget(self.empty);self.empty.hide()
        box.addStretch();scroll.setWidget(body);self.filter_cards();return scroll
    def spotlight(self,index):
        while self.heroes.count():
            widget=self.heroes.takeAt(0).widget()
            if widget:widget.deleteLater()
        for offset in (0,1):
            look=LOOKS[(index*2+offset)%len(LOOKS)]
            self.heroes.addWidget(Hero(look,lambda checked=False,l=look:self.choose(l)))
        for i,b in enumerate(self.pagers):b.setStyleSheet(f'background:transparent;border:0;padding:0;color:{"#c9c2ff" if i==index else "#485981"};font-size:10px;')
    def set_category(self,name):self.category=name;self.filter_cards()
    def filter_cards(self,*args):
        while self.grid.count():self.grid.takeAt(0)
        count=0;query=self.search.text().casefold()
        for look,card in self.cards:
            visible=(self.category=='All effects' or self.category==look[4]) and query in (' '.join(look)).casefold()
            card.setVisible(visible)
            if visible:self.grid.addWidget(card,count//3,count%3);count+=1
        self.empty.setVisible(count==0)
    def choose(self,look):
        self.active_name=look[0];self.w.starter(look[1],look[2],look[3]);self.nav_buttons[2].setChecked(True);self.navigate(0,'Lighting')
        self.w.report(look[0]+' selected. Customize it, then press Play to send it to your lights.')
        self.effect_selected(look[1])
    def build_player(self,box):
        player=QFrame();player.setObjectName('player');row=QHBoxLayout(player);row.setContentsMargins(24,12,24,12);row.setSpacing(18)
        self.album=QLabel();self.album.setFixedSize(54,54);row.addWidget(self.album)
        info=QVBoxLayout();self.track=text('Solid');self.track.setStyleSheet('font-weight:700;');self.track_info=text('Prism Light  ·  local effect','muted');self.track.setWordWrap(False);self.track_info.setWordWrap(False);info.addWidget(self.track);info.addWidget(self.track_info);row.addLayout(info);row.addStretch()
        for symbol,tip,fn in [('‹','Previous effect',lambda:self.step(-1)),('▶','Play / pause',self.toggle),('›','Next effect',lambda:self.step(1)),('□','Blackout',self.w.blackout)]:
            b=QPushButton(symbol);b.setObjectName('transport');b.setToolTip(tip);b.setAccessibleName(tip);b.clicked.connect(fn);row.addWidget(b)
            if symbol=='▶':self.play=b
        row.addStretch();row.addWidget(text('Brightness','muted'))
        self.volume=QSlider(Qt.Horizontal);self.volume.setRange(0,100);self.volume.setValue(self.w.brightness.value());self.volume.setFixedWidth(120);self.volume.setAccessibleName('Playback brightness');self.volume.valueChanged.connect(self.w.brightness.setValue);row.addWidget(self.volume)
        layout_button=QPushButton('▦');layout_button.setToolTip('Desk layout');layout_button.clicked.connect(lambda:self.navigate(1,'Layouts'));row.addWidget(layout_button);box.addWidget(player)
        self.effect_selected(self.w.effect.currentText())
    def effect_selected(self,effect):
        self.track.setText(next((l[0] for l in LOOKS if l[1]==effect),effect))
        self.album.setPixmap(artwork(effect,100,100).scaled(54,54,Qt.KeepAspectRatioByExpanding,Qt.SmoothTransformation))
    def step(self,delta):
        index=EFFECTS.index(self.w.effect.currentText());self.w.effect.setCurrentText(EFFECTS[(index+delta)%len(EFFECTS)])
    def toggle(self):
        if self.w.streaming:self.w.stop_effects()
        elif not self.w.connected:self.connection.show();self.w.report('Connect OpenRGB or enter demo mode before starting playback.')
        else:self.w.start_effects()
    def refresh(self):
        self.play.setText('Ⅱ' if self.w.streaming else '▶')
        if self.w.connected:
            message='●  Demo devices' if self.w.backend.demo else f'●  {len(self.w.devices)} devices'
            self.side_status.setText(message);self.connection_button.setText(message)
        else:self.side_status.setText('○  Offline');self.connection_button.setText('○  Connect devices')
        self.track_info.setText('DEMO · simulated lighting' if self.w.backend.demo and self.w.connected else 'Prism Light · local effect')

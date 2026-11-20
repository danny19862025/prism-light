# SPDX-License-Identifier: GPL-3.0-only
"""The desktop widget: a frameless, translucent, custom-painted panel.

It repaints only when a new snapshot arrives (once per polling interval) and
has no animations, so it costs almost nothing while idle.
"""
import collections
import time
from datetime import datetime

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from . import poster as P
from .model import BY_KEY, GROUPS, METRICS, format_value, is_fresh
from .settings import resolve_theme

THEMES = {
    'dark': {'bg': QColor(16, 18, 26), 'card': QColor(255, 255, 255, 10), 'border': QColor(255, 255, 255, 22),
             'text': QColor('#eef1f8'), 'dim': QColor('#8e97ab'), 'na': QColor('#6b7386'), 'track': QColor(255, 255, 255, 9),
             'warn': QColor('#ffb547'), 'hot': QColor('#ff6b6b'), 'on_accent': QColor('#0d1020')},
    'midnight': {'bg': QColor(9, 14, 32), 'card': QColor(120, 150, 255, 16), 'border': QColor(130, 160, 255, 40),
                 'text': QColor('#e8edff'), 'dim': QColor('#8c9ad0'), 'na': QColor('#5f6a96'), 'track': QColor(130, 160, 255, 14),
                 'warn': QColor('#ffc060'), 'hot': QColor('#ff7a85'), 'on_accent': QColor('#081028')},
    'light': {'bg': QColor(247, 248, 252), 'card': QColor(0, 0, 0, 9), 'border': QColor(0, 0, 0, 22),
              'text': QColor('#141821'), 'dim': QColor('#5d6576'), 'na': QColor('#8b92a1'), 'track': QColor(0, 0, 0, 8),
              'warn': QColor('#b86e00'), 'hot': QColor('#d12f2f'), 'on_accent': QColor('#ffffff')},
}

# (warn, hot) thresholds for colouring values by level.
LEVELS = {'cpu_temp': (75, 88), 'gpu_temp': (75, 85), 'gpu_hotspot': (90, 100), 'cpu_util': (85, 97),
          'gpu_util': (90, 99), 'ram_used': (80, 92), 'gpu_vram': (85, 95), 'disk_active': (85, 98)}


def level(key, item):
    """'warn', 'hot' or None for a fresh reading."""
    if item is not None and key.startswith('bat:'):
        if item.extra:   # charging or full: never a warning
            return None
        return 'hot' if item.value <= 10 else 'warn' if item.value <= 20 else None
    if item is None or key not in LEVELS:
        return None
    value = item.value
    if key in ('ram_used', 'gpu_vram'):
        if not item.extra:
            return None
        value = 100 * item.value / item.extra
    warn, hot = LEVELS[key]
    return 'hot' if value >= hot else 'warn' if value >= warn else None

HINTS = {
    'cpu_temp': 'CPU temperature needs the sensor helper and the PawnIO driver. Right-click the widget or tray '
                'icon → "Set up automatic CPU sensors" (one administrator prompt, then it starts automatically).',
    'cpu_power': 'CPU package power needs the sensor helper (administrator) and the PawnIO driver.',
    'fans': 'Motherboard fan speeds need the sensor helper (administrator) and the PawnIO driver.',
    'fps': 'No presenting game found. Focus the game, or pick its process in Settings → Game FPS.',
    'frametime': 'Frame time comes from the same PresentMon capture as FPS.',
    'battery': 'No device is reporting a battery level right now. Shown automatically: PlayStation controllers '
               '(USB or Bluetooth), Xbox controllers, a laptop battery, and Bluetooth keyboards, mice and headsets '
               'that report their battery to Windows. Keyboards plugged in by cable, and devices on their own '
               'wireless dongle, usually do not report one.',
}

BATTERY_SOURCES = {'usb': 'the controller (USB)', 'bluetooth': 'the controller (Bluetooth)',
                   'windows': 'Windows Bluetooth', 'xinput': 'XInput', 'system': 'Windows power status'}


SENSOR_SHORT = {'Core (Tctl/Tdie)': 'Tctl', 'CCDs Max (Tdie)': 'CCD max', 'CCDs Average (Tdie)': 'CCD avg',
                'CCD1 (Tdie)': 'CCD1', 'CCD2 (Tdie)': 'CCD2', 'Core (Tdie)': 'Tdie', 'Core (Tctl)': 'Tctl',
                'CPU Package': 'Package'}


def short_sensor(name):
    return SENSOR_SHORT.get(name, name.replace(' (Tdie)', ''))


def short_device(name):
    if not name:
        return ''
    for token in ('AMD ', 'Intel(R) ', 'Intel ', 'NVIDIA ', 'GeForce ', '(TM)', '(R)', ' Processor',
                  '12-Core', '16-Core', '8-Core', '6-Core'):
        name = name.replace(token, '')
    return ' '.join(name.split())


class MonitorWidget(QWidget):
    settings_requested = Signal()
    context_requested = Signal(object)   # global QPoint
    awake_requested = Signal(object)     # global QPoint under the keep-awake button
    moved = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.snapshot = None
        self.history = collections.defaultdict(lambda: collections.deque(maxlen=self.settings['history']))
        self.cards = []
        self.hits = []
        self.signature = None
        self.header_rect = None
        self.pill_rect = None
        self.awake = (False, 'Off', '')     # active, headline, tooltip
        self.poster = None                  # P.Content while the Poster look is on
        self.poster_floor = None            # Poster size never shrinks until the rows change
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.setWindowTitle('Prism Monitor')
        self.setAccessibleName('Prism Monitor hardware statistics')
        self.move_timer = QTimer(self, singleShot=True, interval=400, timeout=self.moved.emit)
        self.apply_flags()
        self.relayout()

    # -- window behaviour --------------------------------------------------
    def apply_flags(self):
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint
        if self.settings['layer'] == 'top':
            flags |= Qt.WindowStaysOnTopHint
        elif self.settings['layer'] == 'behind':
            flags |= Qt.WindowStaysOnBottomHint
        if self.settings['click_through']:
            flags |= Qt.WindowTransparentForInput
        visible = self.isVisible()
        pos = self.pos()
        self.setWindowFlags(flags)
        if visible:
            self.move(pos)
            self.show()

    def nativeEvent(self, event_type, message):
        """Behind windows: keep the widget at the bottom of the window stack even when it is
        clicked or dragged, so every window, including newly opened ones, stays above it."""
        if self.settings['layer'] == 'behind' and event_type == b'windows_generic_MSG':
            from . import winapi
            winapi.keep_at_bottom(message)
        return super().nativeEvent(event_type, message)

    def theme_name(self):
        """dark, midnight or light; System follows the Windows app mode."""
        return resolve_theme(self.settings['theme'])

    def set_awake(self, active, headline, tooltip):
        state = (bool(active), headline, tooltip)
        if state != self.awake:
            self.awake = state
            self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.pill_rect is not None and self.pill_rect.contains(event.position()):
            r = self.pill_rect
            self.awake_requested.emit(self.mapToGlobal(QPoint(round(r.left()), round(r.bottom()) + 2)))
            event.accept()
            return
        if event.button() == Qt.LeftButton and not self.settings['locked']:
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if self.pill_rect is not None and self.pill_rect.contains(event.position()):
            return
        if event.button() == Qt.LeftButton:
            self.settings_requested.emit()

    def contextMenuEvent(self, event):
        self.context_requested.emit(event.globalPos())

    def moveEvent(self, event):
        super().moveEvent(event)
        self.move_timer.start()

    def mouseMoveEvent(self, event):
        pos = event.position()
        over_pill = self.pill_rect is not None and self.pill_rect.contains(pos)
        self.setCursor(Qt.PointingHandCursor if over_pill else Qt.ArrowCursor)
        for rect, text in self.hits:
            if rect.contains(pos):
                QToolTip.showText(event.globalPosition().toPoint(), text, self)
                return
        QToolTip.hideText()

    # -- data --------------------------------------------------------------
    def stale_after(self):
        return max(3.0, 2.5 * self.settings['interval'])

    def visible_fans(self):
        if not self.snapshot or 'fans' not in self.settings['visible']:
            return []
        chosen = self.settings['fans_visible']
        fans = sorted(self.snapshot.fans.items(), key=lambda kv: kv[1][0])
        if chosen is None:
            return [(fid, label) for fid, (label, item) in fans if item.value > 0]
        return [(fid, label) for fid, (label, item) in fans if fid in chosen]

    def visible_batteries(self):
        if not self.snapshot or 'battery' not in self.settings['visible']:
            return []
        return sorted(((bid, name) for bid, (name, _, _) in self.snapshot.batteries.items()),
                      key=lambda pair: (pair[1].casefold(), pair[0]))

    def update_snapshot(self, snap):
        self.snapshot = snap
        for key, item in snap.readings.items():
            if key in BY_KEY and BY_KEY[key].primary:
                # Used/total metrics graph as a percentage so the scale is meaningful.
                value = 100 * item.value / item.extra if key == 'ram_used' and item.extra else item.value
                self.history[key].append(value)
        visible = set(self.settings['visible'])
        for metric in METRICS:
            if metric.primary and metric.key in visible and metric.key not in snap.readings:
                self.history[metric.key].append(None)   # gap in the graph, not a zero
        self.relayout()
        self.update()

    def reset_history(self):
        self.history.clear()
        self.update()

    # -- layout ------------------------------------------------------------
    def metrics(self):
        s = self.settings['scale']
        px = self.settings['font_size'] * 4 / 3 * s
        return s, px

    def mkfont(self, px, weight=QFont.Normal, family=None):
        font = QFont(family or 'Segoe UI Variable Text')
        font.setPixelSize(max(6, round(px)))
        font.setWeight(weight)
        font.setHintingPreference(QFont.PreferNoHinting)
        return font

    def card_specs(self):
        visible = self.settings['visible']
        specs = []
        for group, title in GROUPS:
            keys = [m.key for m in METRICS if m.group == group and m.key in visible]
            if not keys:
                continue
            if group == 'fans':
                fans = self.visible_fans()
                specs.append((group, title, [], [('fan:' + fid, label) for fid, label in fans] or [('fans', 'Fans')]))
                continue
            if group == 'battery':
                rows = [('bat:' + bid, name) for bid, name in self.visible_batteries()]
                specs.append((group, title, [], rows or [('battery', 'Devices')]))
                continue
            if group == 'clock':
                specs.append((group, title, ['clock'], []))
                continue
            primary = [k for k in keys if BY_KEY[k].primary]
            secondary = [(k, BY_KEY[k].label) for k in keys if not BY_KEY[k].primary]
            specs.append((group, title, primary, secondary))
        return specs

    def is_poster(self):
        return self.settings['style'] == 'poster'

    def relayout(self):
        if self.is_poster():
            self.relayout_poster()
            return
        self.poster = None
        specs = self.card_specs()
        signature = ('card', tuple((g, tuple(p), tuple(sec)) for g, _, p, sec in specs),
                     self.settings['orientation'], self.settings['scale'], self.settings['font_size'],
                     self.settings['graphs'], self.settings['history'], self.settings['header'])
        if signature == self.signature:
            return
        self.signature = signature
        for key in list(self.history):
            self.history[key] = collections.deque(self.history[key], maxlen=self.settings['history'])
        s, px = self.metrics()
        pad, gap = 10 * s, 8 * s
        card_w = max(150 * s, px * 13.5)
        title_h = px * 1.45
        hero_h = px * 2.25 + px * 1.1 + ((22 * s + 4 * s) if self.settings['graphs'] else 0)
        row_h = px * 1.4
        self.cards = []
        horizontal = self.settings['orientation'] == 'horizontal'
        header_h = px * 2.0 if self.settings['header'] else 0
        top = pad + (header_h + gap * 0.75 if header_h else 0)
        x, y = pad, top
        tallest = 0
        for group, title, primary, secondary in specs:
            rows = len(secondary)
            extra = px * 1.35 if group == 'fps' else 0
            h = pad * 1.4 + title_h + (hero_h if primary else 0) + rows * row_h + extra
            if group == 'clock':
                h = pad * 1.4 + title_h + px * 2.6
            self.cards.append({'group': group, 'title': title, 'primary': primary, 'secondary': secondary,
                               'rect': QRectF(x, y, card_w, h)})
            if horizontal:
                x += card_w + gap
                tallest = max(tallest, h)
            else:
                y += h + gap
        if horizontal:
            for card in self.cards:   # equal heights read as one strip
                card['rect'].setHeight(tallest)
            width, height = x - gap + pad, top + tallest + pad
        else:
            width, height = card_w + 2 * pad, y - gap + pad
        if not self.cards:
            width, height = max(card_w + 2 * pad, 260 * s), top + 44 * s
        self.header_rect = QRectF(pad, pad, width - 2 * pad, header_h) if header_h else None
        self.setFixedSize(round(width), round(height))

    # -- poster look -------------------------------------------------------
    def poster_scale(self):
        return self.settings['scale'] * self.settings['font_size'] / 10

    def relayout_poster(self):
        content = self.poster_content(time.monotonic())
        rows = tuple((g.caption, tuple(r.key for r in g.rows)) for g in content.groups)
        signature = ('poster', rows, content.caption, content.headline, content.framed, content.family,
                     self.settings['scale'],
                     self.settings['font_size'], self.theme_name())
        if signature != self.signature:
            self.signature = signature
            self.poster_floor = None     # rows changed: allowed to shrink once
            self.cards, self.header_rect, self.pill_rect = [], None, None
        self.poster = content
        ink = self.poster_ink()
        size, _ = P.paint(None, content, self.poster_scale(), ink, self.poster_floor)
        self.poster_floor = size
        if size != self.size():
            self.setFixedSize(size)

    def poster_ink(self):
        return P.ink_for(self.theme_name(), self.settings['poster_text'], self.settings['poster_accent'])

    def poster_tone(self, color):
        """A Linux poster warning colour, darkened on the Light theme; None when levels are off."""
        if color is None or not self.settings['color_levels']:
            return None
        if self.theme_name() == 'light':
            return THEMES['light']['warn' if color == P.WARN else 'hot']
        return color

    def poster_content(self, now):
        """The Poster's hero number and rows, kept to the Linux app's sections:
        System (CPU, GPU, memory), Fans and Batteries."""
        P.load_fonts()
        visible = set(self.settings['visible'])
        info = self.snapshot.info if self.snapshot else {}

        def fresh(key):
            if key not in visible and ':' not in key:
                return None, None
            text, ok, item = self.value_text(key, now)
            return (text, item) if ok else (None, None)

        def chip(prefix):
            """'35% · 63°C', coloured by temperature like the Linux app."""
            load, _ = fresh(f'{prefix}_util')
            temp, item = fresh(f'{prefix}_temp')
            parts = [t for t in (load, temp and temp + 'C') if t]
            return ' · '.join(parts), P.temp_color(item.value if item else None)

        c = P.Content(framed=self.settings['poster_opacity'] > 0, family=self.settings['poster_font'])
        fps_text, _ = fresh('fps')
        hero_chip = None
        if fps_text:
            frame, _ = fresh('frametime')
            app = (info.get('fps_app') or 'Game').removesuffix('.exe').upper()
            c.number, c.headline, c.caption, c.hero_key = fps_text, 'FPS', 'GAMING', 'fps'
            c.subline = f'{app[:18]}  ·  {frame.upper()}' if frame else app[:24]
        else:
            c.caption = 'MONITOR'
            gpu_shown = bool(fresh('gpu_temp')[0] or fresh('gpu_util')[0])
            hero_chip = 'gpu' if self.settings['poster_hero'] == 'gpu' and gpu_shown else 'cpu'
            c.headline = hero_chip.upper()
            temp, temp_item = fresh(f'{hero_chip}_temp')
            load, _ = fresh(f'{hero_chip}_util')
            if temp:
                c.number, c.unit, c.hero_key = f'{temp_item.value:.0f}', '°C', f'{hero_chip}_temp'
                c.subline = f'{load} LOAD' if load else ''
            else:
                c.number, c.unit, c.subline, c.hero_key = (load or '0%').rstrip('%'), '%', 'LOAD', f'{hero_chip}_util'
        system = []
        for prefix, label in (('cpu', 'CPU'), ('gpu', 'GPU')):
            if prefix != hero_chip:
                text, color = chip(prefix)
                if text:
                    system.append(P.Row(label, text, self.poster_tone(color), key=f'{prefix}_temp'))
        text, item = fresh('ram_used')
        if text:
            full = item.extra and item.value / item.extra >= 0.9
            system.append(P.Row('MEMORY', text.replace('/', ' / '), self.poster_tone(P.WARN if full else None),
                                key='ram_used'))
        if system:
            c.groups.append(P.Group('SYSTEM', system))
        fans = []
        for fid, label in self.visible_fans():
            text, _ = fresh('fan:' + fid)
            if text:
                fans.append(P.Row(label.upper(), text.upper(), key='fan:' + fid))
        if fans:
            c.groups.append(P.Group('FANS', fans))
        batteries = []
        for bid, name in self.visible_batteries():
            text, item = fresh('bat:' + bid)
            if text:
                percent = item.value if text.endswith('%') else None
                color = None if item.extra else P.battery_color(percent)   # charging or full: no warning
                batteries.append(P.Row(name.upper(), text.upper(), self.poster_tone(color),
                                       None if percent is None else percent / 100, key='bat:' + bid))
        if batteries:
            c.groups.append(P.Group('BATTERIES', batteries))
        return c

    def paint_poster(self, p):
        # Fully transparent pixels do not take clicks on Windows, so a 1/255 fill keeps
        # the whole widget draggable and right-clickable without showing a panel.
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))
        backdrop = self.settings['poster_opacity']
        if backdrop > 0:   # optional dark panel behind the lettering, as in the Linux Card look
            s = self.poster_scale()
            light = self.theme_name() == 'light'
            fill = QColor(247, 248, 252) if light else QColor(12, 16, 22)
            fill.setAlphaF(backdrop)
            edge = QColor(0, 0, 0, 22) if light else QColor(124, 199, 255, 70)
            edge.setAlphaF(edge.alphaF() * min(1.0, backdrop * 1.5))
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(edge, 1))
            p.setBrush(fill)
            p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14 * s, 14 * s)
        ink = self.poster_ink()
        _, hits = P.paint(p, self.poster, self.poster_scale(), ink, self.size())
        now = time.monotonic()
        self.hits = []
        snap = self.snapshot
        for rect, key in hits:
            if not key or key == 'clock':
                continue
            text, fresh, item = self.value_text(key, now)
            label = BY_KEY[key].label if key in BY_KEY else key
            if key.startswith('fan:') and snap and key[4:] in snap.fans:
                label = snap.fans[key[4:]][0]
            if key.startswith('bat:') and snap and key[4:] in snap.batteries:
                label = snap.batteries[key[4:]][0]
            self.hits.append((rect, f'{label}: {text}\n' + self.hint(key, item, fresh)))

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event):
        if self.is_poster():
            if self.poster is None:
                self.relayout()
            painter = QPainter(self)
            self.paint_poster(painter)
            return
        theme = THEMES[self.theme_name()]
        accent = QColor(self.settings['accent'])
        s, px = self.metrics()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        bg = QColor(theme['bg'])
        bg.setAlphaF(self.settings['opacity'])
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme['border'], 1))
        p.setBrush(bg)
        p.drawRoundedRect(outer, 14 * s, 14 * s)
        self.hits = []
        self.pill_rect = None
        if self.header_rect is not None:
            self.paint_header(p, theme, accent, s, px)
        if not self.cards:
            p.setPen(theme['dim'])
            p.setFont(self.mkfont(px))
            body = outer.adjusted(0, self.header_rect.bottom() if self.header_rect else 0, 0, 0)
            p.drawText(body, Qt.AlignCenter, 'No metrics selected — double-click for settings')
            return
        now = time.monotonic()
        for card in self.cards:
            self.paint_card(p, card, theme, accent, s, px, now)

    def paint_header(self, p, theme, accent, s, px):
        r = self.header_rect
        # Brand mark: the four bars from the tray icon.
        bar_w, unit = 3.2 * s, r.height() * 0.62
        base = r.center().y() + unit / 2
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        for i, h in enumerate((0.42, 0.78, 0.6, 1.0)):
            p.drawRoundedRect(QRectF(r.left() + 2 * s + i * (bar_w + 2 * s), base - unit * h, bar_w, unit * h),
                              1.2 * s, 1.2 * s)
        x = r.left() + 2 * s + 4 * (bar_w + 2 * s) + 6 * s
        brand = self.mkfont(px * 0.8, QFont.Bold)
        brand.setLetterSpacing(QFont.PercentageSpacing, 118)
        clock_font = self.mkfont(px * 0.85)
        clock = datetime.now().strftime('%H:%M')
        brand_w = QFontMetricsF(brand).horizontalAdvance('PRISM') + 10 * s
        clock_w = QFontMetricsF(clock_font).horizontalAdvance(clock)
        # Keep-awake pill, right aligned. In a narrow (vertical) widget it shortens step by
        # step instead of being cut off: full text, short text, then the cup icon alone.
        active, headline, tip = self.awake
        candidates = [f'Awake · {headline}', headline] if active else ['Keep awake', 'Awake']
        font = self.mkfont(px * 0.8, QFont.DemiBold)
        fm = QFontMetricsF(font)
        icon_w = px * 0.95

        def width_of(label):
            return icon_w + 16 * s + (fm.horizontalAdvance(label) + 4 * s if label else 0)
        text, show_brand = None, True
        for show_brand in (True, False):
            room = r.right() - (x + (brand_w if show_brand else 0) + clock_w) - 10 * s
            text = next((c for c in candidates if width_of(c) <= room), None)
            if text is None and active and room - width_of('') > px * 3:
                text = fm.elidedText(headline, Qt.ElideRight, room - width_of('') - 4 * s)
            if text is not None:
                break
        if text is None:
            text, show_brand = '', False
        if show_brand:
            p.setFont(brand)
            p.setPen(theme['text'])
            p.drawText(QRectF(x, r.top(), r.width(), r.height()), Qt.AlignLeft | Qt.AlignVCenter, 'PRISM')
            x += brand_w
        p.setFont(clock_font)
        p.setPen(theme['dim'])
        p.drawText(QRectF(x, r.top(), r.width(), r.height()), Qt.AlignLeft | Qt.AlignVCenter, clock)
        pill_w = width_of(text)
        pill_h = r.height() * 0.86
        pill = QRectF(r.right() - pill_w, r.center().y() - pill_h / 2, pill_w, pill_h)
        if active:
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            fg = theme['on_accent']
        else:
            p.setPen(QPen(theme['border'], 1))
            p.setBrush(theme['card'])
            fg = theme['dim']
        p.drawRoundedRect(pill, pill_h / 2, pill_h / 2)
        self.paint_cup(p, QRectF(pill.left() + 8 * s, pill.top(), icon_w, pill_h), fg, s, active)
        p.setFont(font)
        p.setPen(fg)
        if text:
            p.drawText(QRectF(pill.left() + 8 * s + icon_w + 4 * s, pill.top(), pill_w, pill_h),
                       Qt.AlignLeft | Qt.AlignVCenter, text)
        self.pill_rect = pill
        self.hits.append((pill, (f'Awake · {headline}\n' if active and text != candidates[0] else '')
                          + (tip or 'Keep awake is off.')
                          + '\nClick to keep the PC awake for a job, like caffeinate on a Mac.'))

    def paint_cup(self, p, box, color, s, steaming):
        """A small coffee cup, drawn so it matches the theme (no emoji font needed)."""
        w = box.width()
        cup = QRectF(box.left(), box.center().y() - w * 0.18, w * 0.72, w * 0.56)
        p.save()
        p.setPen(QPen(color, max(1.0, 1.25 * s), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(color if steaming else Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(cup.topLeft())
        path.lineTo(cup.topRight())
        path.lineTo(cup.right() - cup.width() * 0.12, cup.bottom())
        path.lineTo(cup.left() + cup.width() * 0.12, cup.bottom())
        path.closeSubpath()
        p.drawPath(path)
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(cup.right() - w * 0.1, cup.top() + w * 0.06, w * 0.3, w * 0.28), -90 * 16, 180 * 16)
        if steaming:
            for i in (0.28, 0.56):
                sx = cup.left() + cup.width() * i
                steam = QPainterPath(QPointF(sx, cup.top() - w * 0.08))
                steam.cubicTo(sx - w * 0.08, cup.top() - w * 0.2, sx + w * 0.08, cup.top() - w * 0.28,
                              sx, cup.top() - w * 0.4)
                p.drawPath(steam)
        p.restore()

    def value_color(self, key, item, fresh, theme):
        if not fresh:
            return theme['na']
        if self.settings['color_levels']:
            lvl = level(key, item)
            if lvl:
                return theme[lvl]
        return theme['text']

    def value_text(self, key, now):
        """(text, fresh, item)."""
        snap = self.snapshot
        item = snap.readings.get(key) if snap else None
        if key.startswith('fan:') and snap:
            fan = snap.fans.get(key[4:])
            item = fan[1] if fan else None
        if key.startswith('bat:') and snap:
            battery = snap.batteries.get(key[4:])
            item = battery[1] if battery else None
            if battery and battery[2] and is_fresh(item, now, self.stale_after()):
                return battery[2], True, item   # coarse level, e.g. Xbox "Medium"
        if key == 'battery':
            return ('none' if snap and snap.info.get('batteries_scanned') else '…'), False, None
        if item is None:
            return 'n/a', False, None
        if not is_fresh(item, now, self.stale_after()):
            return 'stale', False, item
        return format_value(key, item), True, item

    def hint(self, key, item, fresh):
        snap = self.snapshot
        if fresh and key.startswith('bat:'):
            state = {1.0: 'Charging', 2.0: 'Fully charged'}.get(item.extra, 'On battery')
            return f'{state}\nSource: {BATTERY_SOURCES.get(item.source, item.source)}\nUpdates every 15 seconds.'
        if key == 'battery' and not (snap and snap.info.get('batteries_scanned')):
            return 'Looking for devices…'
        if fresh:
            src = {'lhm': 'LibreHardwareMonitor (sensor helper)', 'adl': 'AMD driver (ADL)', 'pdh': 'Windows performance counters',
                   'windows': 'Windows', 'presentmon': 'PresentMon'}.get(item.source, item.source)
            extra = ''
            if key == 'frametime' and item.extra:
                extra = f'\nWorst frame in the last second: {item.extra:.1f} ms'
            if key == 'cpu_temp' and snap and snap.info.get('cpu_temps'):
                chosen = snap.info.get('cpu_temp_sensor')
                extra += '\nAll CPU temperature sensors:' + ''.join(
                    f"\n  {'▸ ' if name == chosen else '   '}{name}: {value:.1f} °C"
                    for name, value in snap.info['cpu_temps'].items())
                extra += '\nChoose the sensor in Settings → Metrics.'
            if key == 'cpu_clock' and item.source == 'pdh':
                extra = '\nEstimated like Task Manager (base clock × processor performance). Start the sensor helper for measured core clocks.'
            if key in ('fps', 'frametime') and snap and snap.info.get('fps_app'):
                extra += f"\nProcess: {snap.info['fps_app']}"
            return f'Source: {src}{extra}'
        if item is not None:
            return 'This reading has not updated recently, so it is not shown.'
        base = 'fans' if key.startswith('fan:') else key
        text = HINTS.get(base, 'This sensor is not available on this system.')
        if base in ('fps', 'frametime') and snap and snap.status.get('fps') and snap.status['fps'] != 'OK':
            text = snap.status['fps']
        return text

    def paint_card(self, p, card, theme, accent, s, px, now):
        rect = card['rect']
        p.setPen(Qt.NoPen)
        p.setBrush(theme['card'])
        p.drawRoundedRect(rect, 10 * s, 10 * s)
        pad = 10 * s
        inner = rect.adjusted(pad, pad * 0.8, -pad, -pad * 0.6)
        title_font = self.mkfont(px * 0.82, QFont.DemiBold)
        title_font.setLetterSpacing(QFont.PercentageSpacing, 108)
        p.setFont(title_font)
        dot = px * 0.38
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawEllipse(QRectF(inner.left(), inner.top() + px * 0.65 - dot / 2, dot, dot))
        p.setPen(accent)
        title_rect = QRectF(inner.left() + dot + 5 * s, inner.top(), inner.width(), px * 1.3)
        p.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, card['title'].upper())
        device = ''
        snap = self.snapshot
        if snap:
            device = {'cpu': short_device(snap.info.get('cpu_name')), 'gpu': short_device(snap.info.get('gpu_name')),
                      'fans': snap.info.get('board_chip', '')}.get(card['group'], '')
        if card['group'] == 'clock':
            device = datetime.now().strftime('%a %d %b')
        if device:
            p.setFont(self.mkfont(px * 0.78))
            p.setPen(theme['dim'])
            title_w = QFontMetricsF(title_font).horizontalAdvance(card['title'].upper()) + dot + 13 * s
            dev_rect = QRectF(inner.left() + title_w, inner.top(), inner.width() - title_w, px * 1.3)
            p.drawText(dev_rect, Qt.AlignRight | Qt.AlignVCenter,
                       QFontMetricsF(p.font()).elidedText(device, Qt.ElideRight, dev_rect.width()))
        y = inner.top() + px * 1.45
        if card['group'] == 'clock':
            p.setFont(self.mkfont(px * 2.3, QFont.DemiBold))
            p.setPen(theme['text'])
            p.drawText(QRectF(inner.left(), y, inner.width(), px * 2.6), Qt.AlignLeft | Qt.AlignVCenter,
                       datetime.now().strftime('%H:%M'))
            return
        primary = card['primary']
        if primary:
            col_w = inner.width() / len(primary) if len(primary) <= 2 else inner.width() / 2
            for i, key in enumerate(primary[:2]):
                cell = QRectF(inner.left() + i * col_w, y, col_w - (6 * s if i == 0 and len(primary) > 1 else 0), 0)
                self.paint_primary(p, key, cell, theme, accent, s, px, now)
            y += px * 2.25 + px * 1.1 + ((22 * s + 4 * s) if self.settings['graphs'] else 0)
        secondary = card['secondary']
        for i, (key, label) in enumerate(secondary):
            cell = QRectF(inner.left(), y + i * px * 1.4, inner.width(), px * 1.4)
            text, fresh, item = self.value_text(key, now)
            p.setFont(self.mkfont(px * 0.82))
            p.setPen(theme['dim'])
            shown = label
            if key.startswith('bat:'):   # device names can be long; keep room for the gauge and value
                shown = QFontMetricsF(p.font()).elidedText(label, Qt.ElideRight, cell.width() * 0.5)
            label_w = QFontMetricsF(p.font()).horizontalAdvance(shown) + 6 * s
            p.drawText(cell, Qt.AlignLeft | Qt.AlignVCenter, shown)
            p.setFont(self.mkfont(px * 0.95, QFont.DemiBold if fresh else QFont.Normal))
            p.setPen(self.value_color(key, item, fresh, theme))
            if not fresh:
                f = p.font()
                f.setItalic(True)
                p.setFont(f)
            value_rect = QRectF(cell.left() + label_w, cell.top(), cell.width() - label_w, cell.height())
            if key.startswith('bat:') and fresh:
                # Battery gauge between the name and the value.
                glyph_w = px * 1.55
                text_w = min(QFontMetricsF(p.font()).horizontalAdvance(text), value_rect.width() - glyph_w - 4 * s)
                glyph = QRectF(value_rect.right() - text_w - 5 * s - glyph_w, cell.center().y() - px * 0.34,
                               glyph_w, px * 0.68)
                if glyph.left() > value_rect.left():
                    lvl = level(key, item) if self.settings['color_levels'] else None
                    self.paint_battery(p, glyph, item, theme[lvl] if lvl else accent, theme, s)
                    value_rect.setLeft(glyph.right() + 4 * s)
                p.setPen(self.value_color(key, item, fresh, theme))
            p.drawText(value_rect, Qt.AlignRight | Qt.AlignVCenter,
                       QFontMetricsF(p.font()).elidedText(text, Qt.ElideLeft, value_rect.width()))
            self.hits.append((cell, f'{label}: {text}\n' + self.hint(key, item, fresh)))
        if card['group'] == 'fps':
            cy = y + len(secondary) * px * 1.4 + px * 0.1
            app = snap.info.get('fps_app') if snap else None
            target = self.settings['fps_process']
            if app:
                line = app
            elif target:
                line = f'Waiting for {target}'
            else:
                line = 'No game in focus'
            p.setFont(self.mkfont(px * 0.78))
            p.setPen(theme['dim'])
            r = QRectF(inner.left(), cy, inner.width(), px * 1.3)
            p.drawText(r, Qt.AlignLeft | Qt.AlignVCenter, QFontMetricsF(p.font()).elidedText(line, Qt.ElideMiddle, r.width()))
            self.hits.append((r, 'FPS source process. Choose a specific game in Settings → Game FPS.'))

    def paint_battery(self, p, rect, item, color, theme, s):
        """A small battery: outline, fill to the charge level, and a bolt while charging."""
        p.save()
        body = rect.adjusted(0, 0, -2.2 * s, 0)
        p.setPen(QPen(theme['dim'], max(1.0, 1.1 * s)))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(body, 2.2 * s, 2.2 * s)
        p.setPen(Qt.NoPen)
        p.setBrush(theme['dim'])
        p.drawRoundedRect(QRectF(body.right() + 0.6 * s, rect.center().y() - rect.height() * 0.2,
                                 1.6 * s, rect.height() * 0.4), 0.8 * s, 0.8 * s)
        inner = body.adjusted(2 * s, 2 * s, -2 * s, -2 * s)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(inner.left(), inner.top(), max(1.5 * s, inner.width() * min(1.0, item.value / 100)),
                                 inner.height()), 1.2 * s, 1.2 * s)
        if item.extra == 1.0:   # charging bolt
            c, w, h = body.center(), body.height() * 0.42, body.height() * 0.72
            bolt = QPainterPath(QPointF(c.x() + w * 0.25, c.y() - h / 2))
            bolt.lineTo(c.x() - w * 0.45, c.y() + h * 0.08)
            bolt.lineTo(c.x() - w * 0.02, c.y() + h * 0.08)
            bolt.lineTo(c.x() - w * 0.25, c.y() + h / 2)
            bolt.lineTo(c.x() + w * 0.45, c.y() - h * 0.08)
            bolt.lineTo(c.x() + w * 0.02, c.y() - h * 0.08)
            bolt.closeSubpath()
            p.setPen(QPen(theme['bg'], max(1.0, 1.2 * s)))
            p.setBrush(theme['text'])
            p.drawPath(bolt)
        p.restore()

    def paint_primary(self, p, key, cell, theme, accent, s, px, now):
        metric = BY_KEY[key]
        text, fresh, item = self.value_text(key, now)
        label = metric.label
        if key == 'cpu_temp' and self.snapshot and self.snapshot.info.get('cpu_temp_sensor'):
            label = f"{label} · {short_sensor(self.snapshot.info['cpu_temp_sensor'])}"
        if key in ('ram_used', 'gpu_vram') and fresh and item.extra:
            label = f'{label} · {100 * item.value / item.extra:.0f}%'
        p.setFont(self.mkfont(px * 0.82))
        p.setPen(theme['dim'])
        label_rect = QRectF(cell.left(), cell.top(), cell.width(), px * 1.1)
        p.drawText(label_rect, Qt.AlignLeft | Qt.AlignVCenter, label)
        value_rect = QRectF(cell.left(), cell.top() + px * 1.0, cell.width(), px * 2.25)
        size = px * 1.9 if fresh else px * 1.2
        font = self.mkfont(size, QFont.DemiBold if fresh else QFont.Normal)
        font.setItalic(not fresh)
        while size > px * 0.95 and QFontMetricsF(font).horizontalAdvance(text) > cell.width():
            size *= 0.92   # shrink long values (e.g. "12.4 MB/s") to fit instead of truncating
            font.setPixelSize(max(6, round(size)))
        p.setFont(font)
        p.setPen(self.value_color(key, item, fresh, theme))
        p.drawText(value_rect, Qt.AlignLeft | Qt.AlignVCenter, QFontMetricsF(font).elidedText(text, Qt.ElideRight, cell.width()))
        bottom = value_rect.bottom()
        if self.settings['graphs']:
            graph = QRectF(cell.left(), bottom + 4 * s, cell.width(), 22 * s)
            lvl = level(key, item) if fresh and self.settings['color_levels'] else None
            self.paint_graph(p, self.history.get(key, ()), graph, metric.graph_max, theme,
                             theme[lvl] if lvl else accent, s)
            bottom = graph.bottom()
        self.hits.append((QRectF(cell.left(), cell.top(), cell.width(), bottom - cell.top()),
                          f'{label}: {text}\n' + self.hint(key, item, fresh)))

    def paint_graph(self, p, samples, rect, fixed_max, theme, accent, s):
        p.setPen(Qt.NoPen)
        p.setBrush(theme['track'])
        p.drawRoundedRect(rect, 4 * s, 4 * s)
        samples = list(samples)
        values = [v for v in samples if v is not None]
        if len(values) < 2:
            return
        top = fixed_max or max(values) * 1.15 or 1.0
        capacity = self.settings['history']
        step = rect.width() / max(1, capacity - 1)
        start = rect.right() - (len(samples) - 1) * step
        paths, path, fill = [], None, None
        for i, v in enumerate(samples):
            if v is None:
                path = None
                continue
            pt = QPointF(start + i * step, rect.bottom() - 1 - (rect.height() - 2) * min(1.0, max(0.0, v / top)))
            if path is None:
                path = QPainterPath(pt)
                paths.append(path)
            else:
                path.lineTo(pt)
        line = QColor(accent)
        area = QLinearGradient(0, rect.top(), 0, rect.bottom())
        top_c, bottom_c = QColor(accent), QColor(accent)
        top_c.setAlpha(96)
        bottom_c.setAlpha(6)
        area.setColorAt(0, top_c)
        area.setColorAt(1, bottom_c)
        p.save()
        p.setClipRect(rect)
        for path in paths:
            if path.elementCount() < 2:
                continue
            first, last = path.elementAt(0), path.elementAt(path.elementCount() - 1)
            fill = QPainterPath(path)
            fill.lineTo(last.x, rect.bottom())
            fill.lineTo(first.x, rect.bottom())
            fill.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(area)
            p.drawPath(fill)
            p.setPen(QPen(line, 1.4 * s))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        p.restore()

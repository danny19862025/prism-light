# SPDX-License-Identifier: GPL-3.0-only
"""The desktop widget: a frameless, translucent, custom-painted panel.

It repaints only when a new snapshot arrives (once per polling interval) and
has no animations, so it costs almost nothing while idle.
"""
import collections
import time
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from .model import BY_KEY, GROUPS, METRICS, format_value, is_fresh

THEMES = {
    'dark': {'bg': QColor(16, 18, 26), 'card': QColor(255, 255, 255, 10), 'border': QColor(255, 255, 255, 22),
             'text': QColor('#eef1f8'), 'dim': QColor('#8e97ab'), 'na': QColor('#6b7386'), 'track': QColor(255, 255, 255, 9)},
    'light': {'bg': QColor(247, 248, 252), 'card': QColor(0, 0, 0, 9), 'border': QColor(0, 0, 0, 22),
              'text': QColor('#141821'), 'dim': QColor('#5d6576'), 'na': QColor('#8b92a1'), 'track': QColor(0, 0, 0, 8)},
}

HINTS = {
    'cpu_temp': 'CPU temperature needs the sensor helper (administrator) and the PawnIO driver. '
                'Start it from the tray menu or Settings → Sensors.',
    'cpu_power': 'CPU package power needs the sensor helper (administrator) and the PawnIO driver.',
    'fans': 'Motherboard fan speeds need the sensor helper (administrator) and the PawnIO driver.',
    'fps': 'No presenting game found. Focus the game, or pick its process in Settings → Game FPS.',
    'frametime': 'Frame time comes from the same PresentMon capture as FPS.',
}


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
    moved = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.snapshot = None
        self.history = collections.defaultdict(lambda: collections.deque(maxlen=self.settings['history']))
        self.cards = []
        self.hits = []
        self.signature = None
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
        if self.settings['always_on_top']:
            flags |= Qt.WindowStaysOnTopHint
        if self.settings['click_through']:
            flags |= Qt.WindowTransparentForInput
        visible = self.isVisible()
        pos = self.pos()
        self.setWindowFlags(flags)
        if visible:
            self.move(pos)
            self.show()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.settings['locked']:
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.settings_requested.emit()

    def contextMenuEvent(self, event):
        self.context_requested.emit(event.globalPos())

    def moveEvent(self, event):
        super().moveEvent(event)
        self.move_timer.start()

    def mouseMoveEvent(self, event):
        pos = event.position()
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
            if group == 'clock':
                specs.append((group, title, ['clock'], []))
                continue
            primary = [k for k in keys if BY_KEY[k].primary]
            secondary = [(k, BY_KEY[k].label) for k in keys if not BY_KEY[k].primary]
            specs.append((group, title, primary, secondary))
        return specs

    def relayout(self):
        specs = self.card_specs()
        signature = (tuple((g, tuple(p), tuple(sec)) for g, _, p, sec in specs),
                     self.settings['orientation'], self.settings['scale'], self.settings['font_size'],
                     self.settings['graphs'], self.settings['history'])
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
        x = y = pad
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
            width, height = x - gap + pad, tallest + 2 * pad
        else:
            width, height = card_w + 2 * pad, y - gap + pad
        if not self.cards:
            width, height = 220 * s, 60 * s
        self.setFixedSize(round(width), round(height))

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event):
        theme = THEMES[self.settings['theme']]
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
        if not self.cards:
            p.setPen(theme['dim'])
            p.setFont(self.mkfont(px))
            p.drawText(outer, Qt.AlignCenter, 'No metrics selected — double-click for settings')
            return
        now = time.monotonic()
        for card in self.cards:
            self.paint_card(p, card, theme, accent, s, px, now)

    def value_text(self, key, now):
        """(text, fresh, item)."""
        snap = self.snapshot
        item = snap.readings.get(key) if snap else None
        if key.startswith('fan:') and snap:
            fan = snap.fans.get(key[4:])
            item = fan[1] if fan else None
        if item is None:
            return 'n/a', False, None
        if not is_fresh(item, now, self.stale_after()):
            return 'stale', False, item
        return format_value(key, item), True, item

    def hint(self, key, item, fresh):
        snap = self.snapshot
        if fresh:
            src = {'lhm': 'LibreHardwareMonitor (sensor helper)', 'adl': 'AMD driver (ADL)', 'pdh': 'Windows performance counters',
                   'windows': 'Windows', 'presentmon': 'PresentMon'}.get(item.source, item.source)
            extra = ''
            if key == 'frametime' and item.extra:
                extra = f'\nWorst frame in the last second: {item.extra:.1f} ms'
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
        p.setPen(accent)
        title_rect = QRectF(inner.left(), inner.top(), inner.width(), px * 1.3)
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
            title_w = QFontMetricsF(title_font).horizontalAdvance(card['title'].upper()) + 8 * s
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
            label_w = QFontMetricsF(p.font()).horizontalAdvance(label) + 6 * s
            p.drawText(cell, Qt.AlignLeft | Qt.AlignVCenter, label)
            p.setFont(self.mkfont(px * 0.95, QFont.DemiBold if fresh else QFont.Normal))
            p.setPen(theme['text'] if fresh else theme['na'])
            if not fresh:
                f = p.font()
                f.setItalic(True)
                p.setFont(f)
            value_rect = QRectF(cell.left() + label_w, cell.top(), cell.width() - label_w, cell.height())
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

    def paint_primary(self, p, key, cell, theme, accent, s, px, now):
        metric = BY_KEY[key]
        text, fresh, item = self.value_text(key, now)
        label = metric.label
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
        p.setPen(theme['text'] if fresh else theme['na'])
        p.drawText(value_rect, Qt.AlignLeft | Qt.AlignVCenter, QFontMetricsF(font).elidedText(text, Qt.ElideRight, cell.width()))
        bottom = value_rect.bottom()
        if self.settings['graphs']:
            graph = QRectF(cell.left(), bottom + 4 * s, cell.width(), 22 * s)
            self.paint_graph(p, self.history.get(key, ()), graph, metric.graph_max, theme, accent, s)
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
        area = QColor(accent)
        area.setAlpha(46)
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

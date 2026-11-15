# SPDX-License-Identifier: GPL-3.0-only
"""Original effects for the round AIO pump display.

Every effect draws into a square of any size, so the same code renders the
480x480 frames sent to the cooler and the small animated gallery previews.
Effects receive:
  t       seconds since start, already scaled by the speed setting
  params  primary/secondary colours, overlay, custom text, picture frames
  data    live inputs: cpu %, memory %, hottest temperature, audio bands,
          the current lighting effect and its settings
"""
import json
import math
import random
import time
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QImage, QImageReader, QLinearGradient, QPainter,
                           QPainterPath, QPen, QRadialGradient)
from .effects import render
from .lcd import SIZE

OVERLAYS = ('None', 'Time', 'CPU load', 'CPU temperature', 'GPU temperature', 'FPS', 'Custom text')
# Effects that need the PresentMon framerate feed.
FPS_EFFECTS = {'Game HUD', 'Sweep gauge'}
DEFAULTS = {'effect': 'Dual gauge', 'primary': '#A89BFF', 'secondary': '#45E0C2', 'speed': 50,
            'overlay': 'None', 'text': 'PRISM', 'picture': '', 'brightness': 100, 'rotation': 0}


def qcolor(value, alpha=255):
    c = QColor(value)
    c.setAlpha(alpha)
    return c


def mix(a, b, f):
    a, b = QColor(a), QColor(b)
    f = max(0.0, min(1.0, f))
    return QColor(round(a.red() + (b.red() - a.red()) * f), round(a.green() + (b.green() - a.green()) * f),
                  round(a.blue() + (b.blue() - a.blue()) * f))


def font(px, bold=True):
    f = QFont()
    f.setPixelSize(max(1, round(px)))
    f.setBold(bold)
    return f


def level(data, key, fallback=0):
    value = data.get(key)
    return fallback if value is None else max(0.0, min(100.0, float(value)))


# --------------------------------------------------------------- effects
def lighting_sync(p, s, t, prm, data):
    """The lighting effect currently playing on your devices."""
    grid = 24 if s >= 200 else 12
    points = [((x + .5)/grid, (y + .5)/grid) for y in range(grid) for x in range(grid)]
    settings = dict(data.get('settings') or {})
    settings.setdefault('color', prm['primary'])
    settings.setdefault('secondary', prm['secondary'])
    pixels = render(data.get('effect') or 'Rainbow wave', points, data.get('elapsed', t), settings, data.get('inputs') or {})
    small = QImage(grid, grid, QImage.Format_RGB32)
    for i, (r, g, b) in enumerate(pixels):
        small.setPixelColor(i % grid, i // grid, QColor(r, g, b))
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(QRectF(0, 0, s, s), small)


def plasma(p, s, t, prm, data):
    """Two-colour plasma with slowly drifting interference bands."""
    grid = 40 if s >= 200 else 16
    img = QImage(grid, grid, QImage.Format_RGB32)
    a, b = QColor(prm['primary']), QColor(prm['secondary'])
    for y in range(grid):
        for x in range(grid):
            u, v = x/grid*6, y/grid*6
            f = (math.sin(u + t) + math.sin(v*1.3 - t*.8) + math.sin((u + v)*.7 + t*.6)
                 + math.sin(math.hypot(u - 3, v - 3)*1.4 - t*1.2))/8 + .5
            img.setPixelColor(x, y, mix(a, b, f))
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(QRectF(0, 0, s, s), img)


def liquid(p, s, t, prm, data):
    """Liquid that fills to your CPU load, with a rolling surface."""
    fill = level(data, 'cpu', 35)/100
    surface = s*(1 - (.12 + .76*fill))
    p.fillRect(QRectF(0, 0, s, s), mix(prm['secondary'], '#000000', .82))
    for layer, alpha in ((0, 140), (1, 255)):
        path = QPainterPath(QPointF(0, s))
        for i in range(41):
            x = s*i/40
            y = surface + math.sin(i/40*math.tau*1.5 + t*(1.6 + layer*.7) + layer)*s*.025
            path.lineTo(x, y)
        path.lineTo(s, s)
        g = QLinearGradient(0, surface, 0, s)
        g.setColorAt(0, qcolor(prm['primary'], alpha))
        g.setColorAt(1, qcolor(mix(prm['primary'], '#000000', .55).name(), alpha))
        p.fillPath(path, g)
    rng = random.Random(7)
    p.setPen(Qt.NoPen)
    for i in range(14):
        x = rng.random()*s
        rise = (t*.12*(1 + rng.random()) + rng.random()) % 1
        y = s - rise*(s - surface)
        p.setBrush(qcolor('#ffffff', 70))
        r = s*(.006 + rng.random()*.01)
        p.drawEllipse(QPointF(x, y), r, r)


def orbit(p, s, t, prm, data):
    """Concentric rings with arcs orbiting at different speeds."""
    c = QPointF(s/2, s/2)
    for i in range(6):
        r = s*(.12 + i*.065)
        p.setPen(QPen(qcolor('#ffffff', 18), max(1, s*.004)))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, r, r)
        colour = mix(prm['primary'], prm['secondary'], i/5)
        p.setPen(QPen(colour, s*.018, Qt.SolidLine, Qt.RoundCap))
        start = (t*(30 + i*14)*(1 if i % 2 else -1)) % 360
        p.drawArc(QRectF(c.x() - r, c.y() - r, 2*r, 2*r), int(start*16), int((60 + i*12)*16))
    glow = QRadialGradient(c, s*.11)
    glow.setColorAt(0, qcolor(prm['primary'], 220))
    glow.setColorAt(1, qcolor(prm['primary'], 0))
    p.setPen(Qt.NoPen)
    p.setBrush(glow)
    p.drawEllipse(c, s*.11, s*.11)


def pulse(p, s, t, prm, data):
    """Rings that bloom outward from the centre."""
    c = QPointF(s/2, s/2)
    p.setBrush(Qt.NoBrush)
    for i in range(5):
        phase = (t*.35 + i/5) % 1
        r = phase*s*.55
        colour = mix(prm['primary'], prm['secondary'], phase)
        p.setPen(QPen(qcolor(colour.name(), round(255*(1 - phase))), s*(.04*(1 - phase) + .004)))
        p.drawEllipse(c, r, r)


def starfield(p, s, t, prm, data):
    """Stars streaming past, as if flying forward."""
    c = QPointF(s/2, s/2)
    rng = random.Random(3)
    for i in range(140):
        angle = rng.random()*math.tau
        depth = (rng.random() + t*.25) % 1
        r1 = depth**2*s*.75
        r0 = max(0, depth - .04)**2*s*.75
        colour = mix(prm['secondary'], prm['primary'], rng.random())
        p.setPen(QPen(qcolor(colour.name(), round(60 + 195*depth)), max(1, s*.006*depth + .5)))
        p.drawLine(QPointF(c.x() + math.cos(angle)*r0, c.y() + math.sin(angle)*r0),
                   QPointF(c.x() + math.cos(angle)*r1, c.y() + math.sin(angle)*r1))


def radar(p, s, t, prm, data):
    """A sweeping radar with fading blips."""
    c = QPointF(s/2, s/2)
    p.fillRect(QRectF(0, 0, s, s), mix(prm['primary'], '#000000', .9))
    p.setBrush(Qt.NoBrush)
    for i in range(1, 5):
        p.setPen(QPen(qcolor(prm['primary'], 70), max(1, s*.003)))
        p.drawEllipse(c, s*.11*i, s*.11*i)
    p.drawLine(QPointF(s*.06, s/2), QPointF(s*.94, s/2))
    p.drawLine(QPointF(s/2, s*.06), QPointF(s/2, s*.94))
    angle = (t*70) % 360
    sweep = QConicalGradient(c, -angle)
    sweep.setColorAt(0, qcolor(prm['primary'], 200))
    sweep.setColorAt(.18, qcolor(prm['primary'], 0))
    sweep.setColorAt(1, qcolor(prm['primary'], 0))
    p.setPen(Qt.NoPen)
    p.setBrush(sweep)
    p.drawEllipse(c, s*.44, s*.44)
    rng = random.Random(11)
    for _ in range(7):
        a, r = rng.random()*360, rng.uniform(.1, .42)*s
        age = ((angle - a) % 360)/360
        p.setBrush(qcolor(prm['secondary'], round(255*(1 - age))))
        point = QPointF(c.x() + math.cos(math.radians(a))*r, c.y() - math.sin(math.radians(a))*r)
        p.drawEllipse(point, s*.014, s*.014)


def spectrum(p, s, t, prm, data):
    """Audio bands as a ring of bars (gentle motion without audio)."""
    bands = list(data.get('bands') or [])
    c = QPointF(s/2, s/2)
    count = 48
    for i in range(count):
        if any(bands):
            value = bands[int(i/count*len(bands)) % len(bands)]
        else:
            value = .35 + .3*math.sin(t*2 + i*.45)*math.sin(t*.7 + i*.13)
        value = max(.04, min(1, value))
        a = math.tau*i/count - math.pi/2
        r0, r1 = s*.2, s*(.22 + .24*value)
        p.setPen(QPen(mix(prm['primary'], prm['secondary'], value), s*.016, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(c.x() + math.cos(a)*r0, c.y() + math.sin(a)*r0),
                   QPointF(c.x() + math.cos(a)*r1, c.y() + math.sin(a)*r1))


def bubbles(p, s, t, prm, data):
    """Soft glowing orbs floating upward."""
    p.fillRect(QRectF(0, 0, s, s), mix(prm['secondary'], '#000000', .88))
    rng = random.Random(5)
    p.setPen(Qt.NoPen)
    for i in range(22):
        speed = .05 + rng.random()*.08
        y = s*(1.15 - ((t*speed + rng.random()) % 1)*1.3)
        x = s*rng.random() + math.sin(t*.8 + i)*s*.03
        r = s*(.03 + rng.random()*.07)
        g = QRadialGradient(QPointF(x, y), r)
        colour = mix(prm['primary'], prm['secondary'], rng.random())
        g.setColorAt(0, qcolor(colour.name(), 200))
        g.setColorAt(1, qcolor(colour.name(), 0))
        p.setBrush(g)
        p.drawEllipse(QPointF(x, y), r, r)


def gradient_spin(p, s, t, prm, data):
    """A smooth colour wheel turning slowly."""
    g = QConicalGradient(QPointF(s/2, s/2), (t*40) % 360)
    for stop, colour in ((0, prm['primary']), (.5, prm['secondary']), (1, prm['primary'])):
        g.setColorAt(stop, QColor(colour))
    p.fillRect(QRectF(0, 0, s, s), g)
    shade = QRadialGradient(QPointF(s/2, s/2), s*.5)
    shade.setColorAt(0, qcolor('#000000', 150))
    shade.setColorAt(1, qcolor('#000000', 0))
    p.fillRect(QRectF(0, 0, s, s), shade)


def vortex(p, s, t, prm, data):
    """Spiral arms twisting into the centre."""
    c = QPointF(s/2, s/2)
    for arm in range(6):
        path = QPainterPath()
        for i in range(60):
            f = i/59
            a = arm*math.tau/6 + f*4.5 - t*1.2
            r = f*s*.48
            point = QPointF(c.x() + math.cos(a)*r, c.y() + math.sin(a)*r)
            path.moveTo(point) if i == 0 else path.lineTo(point)
        p.setPen(QPen(mix(prm['primary'], prm['secondary'], arm/5), s*.02, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


def dashboard(p, s, t, prm, data):
    """CPU load, memory and temperature gauges."""
    cpu, mem, temp = data.get('cpu'), data.get('memory'), data.get('temperature')
    def ring(value, colour, inset, width):
        rect = QRectF(inset, inset, s - 2*inset, s - 2*inset)
        p.setPen(QPen(qcolor('#ffffff', 25), width, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, 225*16, -270*16)
        if value is not None:
            p.setPen(QPen(QColor(colour), width, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, 225*16, int(-270*16*max(0, min(100, value))/100))
    p.setBrush(Qt.NoBrush)
    ring(cpu, prm['primary'], s*.08, s*.045)
    ring(mem, prm['secondary'], s*.18, s*.033)
    p.setPen(QColor('#ffffff'))
    p.setFont(font(s*.17))
    p.drawText(QRectF(0, s*.3, s, s*.24), Qt.AlignCenter, '--' if cpu is None else f'{cpu:.0f}%')
    p.setPen(QColor('#b5c6dd'))
    p.setFont(font(s*.055, False))
    p.drawText(QRectF(0, s*.52, s, s*.08), Qt.AlignCenter, 'CPU')
    detail = 'RAM --' if mem is None else f'RAM {mem:.0f}%'
    if temp is not None:
        detail += f'  ·  {temp:.0f}°C'
    p.drawText(QRectF(0, s*.61, s, s*.08), Qt.AlignCenter, detail)


def analog_clock(p, s, t, prm, data):
    """An analog clock with a sweeping seconds hand."""
    now = data.get('now') or time.localtime()
    c = QPointF(s/2, s/2)
    for i in range(60):
        a = math.tau*i/60
        r0 = s*(.40 if i % 5 else .36)
        p.setPen(QPen(qcolor('#ffffff', 200 if i % 5 == 0 else 70), s*(.012 if i % 5 == 0 else .005)))
        p.drawLine(QPointF(c.x() + math.cos(a)*r0, c.y() + math.sin(a)*r0),
                   QPointF(c.x() + math.cos(a)*s*.44, c.y() + math.sin(a)*s*.44))
    secs = now.tm_sec + (time.time() % 1 if 'now' not in data else 0)
    hands = ((now.tm_hour % 12 + now.tm_min/60)/12, s*.22, s*.03, '#ffffff'),\
            ((now.tm_min + secs/60)/60, s*.33, s*.02, prm['primary']),\
            (secs/60, s*.38, s*.008, prm['secondary'])
    for frac, length, width, colour in hands:
        a = math.tau*frac - math.pi/2
        p.setPen(QPen(QColor(colour), width, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(c, QPointF(c.x() + math.cos(a)*length, c.y() + math.sin(a)*length))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(prm['secondary']))
    p.drawEllipse(c, s*.02, s*.02)


def digital_clock(p, s, t, prm, data):
    """Large time and date with a seconds ring."""
    now = data.get('now') or time.localtime()
    rect = QRectF(s*.06, s*.06, s*.88, s*.88)
    p.setPen(QPen(qcolor('#ffffff', 25), s*.02))
    p.drawEllipse(rect)
    p.setPen(QPen(QColor(prm['primary']), s*.02, Qt.SolidLine, Qt.RoundCap))
    p.drawArc(rect, 90*16, int(-360*16*now.tm_sec/60))
    p.setPen(QColor('#ffffff'))
    p.setFont(font(s*.23))
    p.drawText(QRectF(0, s*.29, s, s*.3), Qt.AlignCenter, time.strftime('%H:%M', now))
    p.setPen(QColor(prm['secondary']))
    p.setFont(font(s*.065, False))
    p.drawText(QRectF(0, s*.6, s, s*.1), Qt.AlignCenter, time.strftime('%a %d %b', now))


def marquee(p, s, t, prm, data):
    """Your own text scrolling across the screen."""
    g = QLinearGradient(0, 0, s, s)
    g.setColorAt(0, mix(prm['primary'], '#000000', .75))
    g.setColorAt(1, mix(prm['secondary'], '#000000', .75))
    p.fillRect(QRectF(0, 0, s, s), g)
    text = (prm.get('text') or 'PRISM').strip() or 'PRISM'
    p.setFont(font(s*.2))
    width = p.fontMetrics().horizontalAdvance(text) + s*.3
    x = s - (t*s*.35) % (width + s)
    p.setPen(QColor('#ffffff'))
    p.drawText(QRectF(x, 0, width, s), Qt.AlignVCenter | Qt.AlignLeft, text)


def picture(p, s, t, prm, data):
    """A picture or animated GIF you choose."""
    frames = prm.get('frames') or []
    if not frames:
        p.setPen(QColor('#8d9cb3'))
        p.setFont(font(s*.06, False))
        p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, 'Choose a picture')
        return
    total = sum(d for _, d in frames)
    position = (data.get('real_elapsed', t)*1000) % total if total else 0
    for image, delay in frames:
        if position < delay:
            break
        position -= delay
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.drawImage(QRectF(0, 0, s, s), image)


# ------------------------------------------------------------ info blocks
def value_text(value, suffix):
    return '--' if value is None else f'{value:.0f}{suffix}'


def arc(p, rect, start, span, value, colour, width, track=25):
    """Gauge arc: start/span in degrees (Qt: counter-clockwise positive)."""
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(qcolor('#ffffff', track), width, Qt.SolidLine, Qt.RoundCap))
    p.drawArc(rect, int(start*16), int(span*16))
    if value is not None:
        p.setPen(QPen(QBrush(colour), width, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, int(start*16), int(span*16*max(0, min(100, value))/100))


def label_at(p, s, y, h, text, px, colour='#ffffff', bold=True, x=0, w=None):
    p.setPen(QColor(colour))
    p.setFont(font(s*px, bold))
    p.drawText(QRectF(x, s*y, w if w is not None else s, s*h), Qt.AlignCenter, text)


def temp_of(data, key):
    """CPU falls back to the hottest board sensor when Tctl is unavailable."""
    value = data.get(key)
    if value is None and key == 'cpu_temp':
        value = data.get('temperature')
    return value


def dual_gauge(p, s, t, prm, data):
    """Two arcs: CPU temperature outside, GPU temperature inside."""
    cpu, gpu = temp_of(data, 'cpu_temp'), data.get('gpu_temp')
    arc(p, QRectF(s*.07, s*.07, s*.86, s*.86), 225, -270, cpu, QColor(prm['primary']), s*.05)
    arc(p, QRectF(s*.17, s*.17, s*.66, s*.66), 225, -270, gpu, QColor(prm['secondary']), s*.04)
    label_at(p, s, .30, .07, 'CPU TEMP', .045, prm['primary'], False)
    label_at(p, s, .36, .14, value_text(cpu, '°C'), .11)
    p.setPen(QPen(qcolor('#ffffff', 60), max(1, s*.004)))
    p.drawLine(QPointF(s*.33, s*.51), QPointF(s*.67, s*.51))
    label_at(p, s, .52, .14, value_text(gpu, '°C'), .11)
    label_at(p, s, .65, .07, 'GPU TEMP', .045, prm['secondary'], False)


def single_gauge(p, s, t, prm, data):
    """One wide arc with a big CPU temperature."""
    temp = temp_of(data, 'cpu_temp')
    g = QConicalGradient(QPointF(s/2, s/2), 225)
    g.setColorAt(0, QColor(prm['secondary']))
    g.setColorAt(.75, QColor(prm['primary']))
    g.setColorAt(1, QColor(prm['secondary']))
    arc(p, QRectF(s*.1, s*.1, s*.8, s*.8), 225, -270, temp, g, s*.07)
    label_at(p, s, .33, .26, value_text(temp, '°'), .22)
    label_at(p, s, .68, .08, 'CPU TEMP', .06, '#d6dcf0', False)


def sensor_bars(p, s, t, prm, data):
    """Rows of live readings with progress bars."""
    rows = [('CPU LOAD', data.get('cpu'), '%'), ('GPU LOAD', data.get('gpu_load'), '%'),
            ('CPU TEMP', temp_of(data, 'cpu_temp'), '°C'), ('GPU TEMP', data.get('gpu_temp'), '°C')]
    top, step = s*.2, s*.155
    for i, (name, value, suffix) in enumerate(rows):
        y = top + i*step
        p.setPen(QColor('#d6dcf0'))
        p.setFont(font(s*.048, False))
        p.drawText(QRectF(s*.14, y, s*.5, s*.08), Qt.AlignLeft | Qt.AlignVCenter, name)
        p.setPen(QColor('#ffffff'))
        p.setFont(font(s*.052))
        p.drawText(QRectF(s*.5, y, s*.36, s*.08), Qt.AlignRight | Qt.AlignVCenter, value_text(value, suffix))
        bar = QRectF(s*.14, y + s*.09, s*.72, s*.022)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor('#ffffff', 30))
        p.drawRoundedRect(bar, bar.height()/2, bar.height()/2)
        if value is not None:
            fill = QRectF(bar.x(), bar.y(), bar.width()*max(0, min(100, value))/100, bar.height())
            p.setBrush(mix(prm['primary'], prm['secondary'], i/3))
            p.drawRoundedRect(fill, bar.height()/2, bar.height()/2)


def ring_readout(p, s, t, prm, data):
    """Thin rotating rings around one large CPU temperature."""
    temp = temp_of(data, 'cpu_temp')
    c = QPointF(s/2, s/2)
    p.setBrush(Qt.NoBrush)
    for i in range(7):
        r = s*(.31 + i*.022)
        p.setPen(QPen(qcolor('#ffffff', 60 + i*22), max(1, s*.004)))
        start = (t*(18 + i*9)*(1 if i % 2 else -1)) % 360
        p.drawArc(QRectF(c.x() - r, c.y() - r, 2*r, 2*r), int(start*16), int((250 - i*15)*16))
    label_at(p, s, .3, .08, 'CPU TEMP', .055, prm['primary'])
    label_at(p, s, .37, .25, value_text(temp, ''), .24)
    p.setPen(QPen(QColor('#ffffff'), max(1, s*.004)))
    p.drawRect(QRectF(s*.45, s*.63, s*.1, s*.07))
    label_at(p, s, .63, .07, '°C', .04)


def hexagon(p, centre, r, colour, progress):
    path = QPainterPath()
    for i in range(7):
        a = math.radians(60*i - 30)
        point = QPointF(centre.x() + math.cos(a)*r, centre.y() + math.sin(a)*r)
        path.moveTo(point) if i == 0 else path.lineTo(point)
    p.setBrush(qcolor('#000000', 140))
    p.setPen(QPen(qcolor(colour.name(), 70), max(1, r*.04)))
    p.drawPath(path)
    if progress is not None:
        length = path.length()*max(0, min(100, progress))/100
        part = QPainterPath()
        for i in range(1, 101):
            point = path.pointAtPercent(path.percentAtLength(min(length, path.length())*i/100))
            part.moveTo(point) if i == 1 else part.lineTo(point)
        p.setPen(QPen(colour, r*.07, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawPath(part)


def hex_panels(p, s, t, prm, data):
    """Four glowing hexagon tiles: CPU and GPU load and temperature."""
    rng = random.Random(9)
    p.setPen(Qt.NoPen)
    for _ in range(70):
        x = (rng.random()*s + t*s*.02*(1 + rng.random())) % s
        p.setBrush(qcolor(prm['primary'], rng.randint(40, 160)))
        p.drawEllipse(QPointF(x, rng.random()*s), s*.004, s*.004)
    tiles = [('CPU LOAD', data.get('cpu'), '%'), ('GPU LOAD', data.get('gpu_load'), '%'),
             ('CPU TEMP', temp_of(data, 'cpu_temp'), '°C'), ('GPU TEMP', data.get('gpu_temp'), '°C')]
    colour = QColor(prm['primary'])
    for i, (name, value, suffix) in enumerate(tiles):
        cx = s*(.3 if i % 2 == 0 else .7)
        cy = s*(.3 if i < 2 else .7)
        hexagon(p, QPointF(cx, cy), s*.15, colour, value)
        label_at(p, s, cy/s - .06, .05, name, .032, '#cfd8e8', False, cx - s*.15, s*.3)
        label_at(p, s, cy/s - .015, .07, value_text(value, suffix), .055, prm['primary'], True, cx - s*.15, s*.3)
    custom = (prm.get('text') or '').strip()[:12]
    if custom:
        label_at(p, s, .465, .07, custom, .06, prm['secondary'])


def sweep_gauge(p, s, t, prm, data):
    """Bold gradient arc: GPU temperature headline, CPU temperature and FPS."""
    gpu, cpu, fps = data.get('gpu_temp'), temp_of(data, 'cpu_temp'), data.get('fps')
    rect = QRectF(s*.06, s*.06, s*.88, s*.88)
    g = QConicalGradient(QPointF(s/2, s/2), 270)
    g.setColorAt(0, QColor(prm['secondary']))
    g.setColorAt(.5, mix(prm['primary'], prm['secondary'], .5))
    g.setColorAt(1, QColor(prm['primary']))
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(g, s*.06, Qt.SolidLine, Qt.RoundCap))
    p.drawArc(rect, int(245*16), int(-310*16))
    a = math.radians(90 - (t*25) % 360)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(prm['primary']))
    p.drawEllipse(QPointF(s/2 + math.cos(a)*s*.44, s/2 - math.sin(a)*s*.44), s*.035, s*.035)
    custom = (prm.get('text') or '').strip()[:14]
    if custom:
        label_at(p, s, .2, .08, custom.upper(), .06)
    label_at(p, s, .33, .22, value_text(gpu, '°'), .2, x=s*.08, w=s*.46)
    label_at(p, s, .55, .08, 'GPU', .06, x=s*.08, w=s*.46)
    p.setPen(QPen(qcolor('#ffffff', 120), max(1, s*.005)))
    p.drawLine(QPointF(s*.56, s*.36), QPointF(s*.56, s*.66))
    label_at(p, s, .34, .1, value_text(cpu, '°'), .08, x=s*.58, w=s*.3)
    label_at(p, s, .43, .06, 'CPU', .04, x=s*.58, w=s*.3)
    label_at(p, s, .5, .1, value_text(fps, ''), .08, x=s*.58, w=s*.3)
    label_at(p, s, .59, .06, 'FPS', .04, x=s*.58, w=s*.3)


def game_hud(p, s, t, prm, data):
    """Big live framerate with CPU and GPU temperatures underneath."""
    fps, app = data.get('fps'), data.get('fps_app')
    arc(p, QRectF(s*.07, s*.07, s*.86, s*.86), 225, -270, None if fps is None else fps*100/144,
        QColor(prm['primary']), s*.045)
    label_at(p, s, .2, .07, 'FPS', .055, prm['primary'], False)
    label_at(p, s, .26, .24, value_text(fps, ''), .22)
    name = (app or 'no game detected').rsplit('.', 1)[0][:16]
    label_at(p, s, .49, .06, name, .04, '#8d9cb3', False)
    for x, caption, value, colour in ((.18, 'CPU', temp_of(data, 'cpu_temp'), prm['primary']),
                                      (.52, 'GPU', data.get('gpu_temp'), prm['secondary'])):
        label_at(p, s, .58, .1, value_text(value, '°C'), .075, x=s*x, w=s*.3)
        label_at(p, s, .67, .06, caption, .04, colour, False, s*x, s*.3)


def dot_ring_clock(p, s, t, prm, data):
    """Digital time and CPU load inside a ring of dots; a comet lights them."""
    now = data.get('now') or time.localtime()
    c = QPointF(s/2, s/2)
    count = 60
    head = (data.get('real_elapsed', t)*10) % count
    p.setPen(Qt.NoPen)
    for i in range(count):
        a = math.tau*i/count - math.pi/2
        behind = (head - i) % count
        r = s*(.012 if behind > 8 else .012 + .012*(1 - behind/8))
        p.setBrush(QColor(prm['secondary']) if behind < 8 else qcolor('#ffffff', 200))
        p.drawEllipse(QPointF(c.x() + math.cos(a)*s*.43, c.y() + math.sin(a)*s*.43), r, r)
    label_at(p, s, .3, .16, time.strftime('%H:%M', now), .15, prm['primary'])
    label_at(p, s, .47, .07, 'CPU LOAD', .055, prm['primary'], False)
    label_at(p, s, .55, .14, value_text(data.get('cpu'), '%'), .12, prm['primary'])


def bold_split(p, s, t, prm, data):
    """Solid colour background, CPU and GPU temperatures, turning ornament."""
    p.fillRect(QRectF(0, 0, s, s), QColor(prm['primary']))
    c = QPointF(s/2, s/2)
    p.setBrush(Qt.NoBrush)
    for i in range(24):
        a = math.radians(i*15 + t*12)
        p.setPen(QPen(qcolor('#ffffff', 150), s*.012, Qt.SolidLine, Qt.RoundCap))
        r0, r1 = s*.38, s*(.44 + .03*math.sin(t*2 + i))
        p.drawLine(QPointF(c.x() + math.cos(a)*r0, c.y() + math.sin(a)*r0),
                   QPointF(c.x() + math.cos(a)*r1, c.y() + math.sin(a)*r1))
    label_at(p, s, .24, .07, 'CPU TEMP', .045, '#ffffff', False)
    label_at(p, s, .3, .15, value_text(temp_of(data, 'cpu_temp'), '°C'), .13)
    p.setPen(QPen(qcolor('#ffffff', 200), max(1, s*.005)))
    p.drawLine(QPointF(s*.28, s*.48), QPointF(s*.72, s*.48))
    label_at(p, s, .5, .07, 'GPU TEMP', .045, '#ffffff', False)
    label_at(p, s, .56, .15, value_text(data.get('gpu_temp'), '°C'), .13)


def minimal_clock(p, s, t, prm, data):
    """A clean analog clock with long hour marks and a thin seconds hand."""
    now = data.get('now') or time.localtime()
    c = QPointF(s/2, s/2)
    for i in range(60):
        a = math.tau*i/60
        major = i % 5 == 0
        r0 = s*(.36 if major else .41)
        p.setPen(QPen(qcolor('#ffffff', 230 if major else 90), s*(.016 if major else .005), Qt.SolidLine, Qt.FlatCap))
        p.drawLine(QPointF(c.x() + math.cos(a)*r0, c.y() + math.sin(a)*r0),
                   QPointF(c.x() + math.cos(a)*s*.45, c.y() + math.sin(a)*s*.45))
    secs = now.tm_sec + (time.time() % 1 if 'now' not in data else 0)
    for frac, length, width, colour in (((now.tm_hour % 12 + now.tm_min/60)/12, .24, .022, '#f4f0dc'),
                                        ((now.tm_min + secs/60)/60, .36, .016, '#f4f0dc'),
                                        (secs/60, .4, .005, prm['secondary'])):
        a = math.tau*frac - math.pi/2
        p.setPen(QPen(QColor(colour), s*width, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(c, QPointF(c.x() + math.cos(a)*s*length, c.y() + math.sin(a)*s*length))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(prm['secondary']))
    p.drawEllipse(c, s*.018, s*.018)


# name: (function, category)
EFFECTS = {
    'Game HUD': (game_hud, 'Info'),
    'Dual gauge': (dual_gauge, 'Info'),
    'Single gauge': (single_gauge, 'Info'),
    'Sensor bars': (sensor_bars, 'Info'),
    'Ring readout': (ring_readout, 'Info'),
    'Hex panels': (hex_panels, 'Info'),
    'Sweep gauge': (sweep_gauge, 'Info'),
    'Dot ring clock': (dot_ring_clock, 'Info'),
    'Bold split': (bold_split, 'Info'),
    'Minimal clock': (minimal_clock, 'Clock'),
    'Lighting sync': (lighting_sync, 'Sync'),
    'Plasma flow': (plasma, 'Ambient'),
    'Liquid fill': (liquid, 'System'),
    'Orbit rings': (orbit, 'Motion'),
    'Pulse': (pulse, 'Motion'),
    'Starfield': (starfield, 'Motion'),
    'Radar': (radar, 'Motion'),
    'Spectrum ring': (spectrum, 'Audio'),
    'Bubbles': (bubbles, 'Ambient'),
    'Colour wheel': (gradient_spin, 'Ambient'),
    'Vortex': (vortex, 'Motion'),
    'Dashboard': (dashboard, 'System'),
    'Analog clock': (analog_clock, 'Clock'),
    'Digital clock': (digital_clock, 'Clock'),
    'Text marquee': (marquee, 'Custom'),
    'Picture / GIF': (picture, 'Custom'),
}
# Effects that already show data in the centre skip the overlay.
NO_OVERLAY = ({'Dashboard', 'Analog clock', 'Digital clock', 'Text marquee', 'Picture / GIF', 'Minimal clock'}
              | {name for name, (_fn, category) in EFFECTS.items() if category == 'Info'})


def overlay(p, s, prm, data):
    kind = prm.get('overlay', 'None')
    if kind == 'None':
        return
    if kind == 'Time':
        big, small = time.strftime('%H:%M', data.get('now') or time.localtime()), ''
    elif kind == 'CPU load':
        cpu = data.get('cpu')
        big, small = ('--' if cpu is None else f'{cpu:.0f}%'), 'CPU'
    elif kind == 'CPU temperature':
        big, small = value_text(temp_of(data, 'cpu_temp'), '°'), 'CPU'
    elif kind == 'GPU temperature':
        big, small = value_text(data.get('gpu_temp'), '°'), 'GPU'
    elif kind == 'FPS':
        big, small = value_text(data.get('fps'), ''), 'FPS'
    else:
        big, small = (prm.get('text') or '').strip()[:18], ''
    if not big:
        return
    shade = QRadialGradient(QPointF(s/2, s/2), s*.32)
    shade.setColorAt(0, qcolor('#000000', 150))
    shade.setColorAt(1, qcolor('#000000', 0))
    p.setPen(Qt.NoPen)
    p.setBrush(shade)
    p.drawEllipse(QPointF(s/2, s/2), s*.32, s*.32)
    size = s*(.2 if len(big) <= 5 else max(.07, .9/len(big)))
    p.setPen(QColor('#ffffff'))
    p.setFont(font(size))
    p.drawText(QRectF(0, s*.36, s, s*.24), Qt.AlignCenter, big)
    if small:
        p.setPen(QColor('#d6dcf0'))
        p.setFont(font(s*.05, False))
        p.drawText(QRectF(0, s*.58, s, s*.08), Qt.AlignCenter, small)


def draw(name, size, t, params, data):
    """Render one effect frame into a new square QImage."""
    function = EFFECTS[name][0]
    prm = dict(DEFAULTS)
    prm.update(params or {})
    image = QImage(size, size, QImage.Format_RGB32)
    image.fill(Qt.black)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    scaled = t*prm.get('speed', 50)/50
    function(p, size, scaled, prm, data or {})
    if name not in NO_OVERLAY:
        overlay(p, size, prm, data or {})
    p.end()
    return image


def encode(image, quality=85):
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    if not image.save(buffer, 'JPEG', quality):
        raise RuntimeError('Could not encode the display image.')
    return bytes(data)


def load_frames(path, size=SIZE, limit=240):
    """Read a picture or animated GIF as centre-cropped square frames."""
    reader = QImageReader(path)
    if not reader.canRead():
        raise ValueError('That file is not a readable image.')
    frames = []
    while len(frames) < limit:
        image = reader.read()
        if image.isNull():
            break
        side = min(image.width(), image.height())
        square = image.copy((image.width() - side)//2, (image.height() - side)//2, side, side)
        delay = reader.nextImageDelay() if reader.supportsAnimation() else 0
        frames.append((square.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation), max(40, delay or 1000)))
        if not reader.supportsAnimation() or not reader.canRead():
            break
    if not frames:
        raise ValueError('That file is not a readable image.')
    return frames


def load_settings(path):
    settings = dict(DEFAULTS)
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
        for key, default in DEFAULTS.items():
            if isinstance(saved.get(key), type(default)):
                settings[key] = saved[key]
    except (OSError, ValueError, AttributeError):
        pass
    if settings['effect'] not in EFFECTS:
        settings['effect'] = DEFAULTS['effect']
    if settings['overlay'] not in OVERLAYS:
        settings['overlay'] = 'None'
    return settings


def save_settings(path, settings):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps({k: settings[k] for k in DEFAULTS}, indent=1), encoding='utf-8')
        tmp.replace(path)
    except OSError:
        pass

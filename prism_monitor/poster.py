# SPDX-License-Identifier: GPL-3.0-only
"""The 'Poster' look, matching the Still desktop clock: no panel, bold uppercase Barlow,
a huge faded accent number with a white headline across it, a vertical caption, a thin
rule, then compact uppercase rows.

Ported from the standalone prism-monitor app, with its colours and soft shadow. On
Windows the shadow is blurred by scaling a small copy of the lettering instead of a
QGraphicsDropShadowEffect, which re-blurred the whole layered window on every update; and
values are measured with their digits replaced by '0', so the widget does not change width
each time a number changes.
"""
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QLinearGradient, QPainter, QPen

# The Linux app's palette, matching the Still clock.
TEXT = QColor('#FFFFFF')
ACCENT = QColor('#8FD8FF')
WARN, BAD = QColor('#FFD166'), QColor('#FF7A7A')

FAMILY = 'Barlow'
FALLBACK = 'Segoe UI Variable Display'
_loaded = False
_DIGITS = re.compile(r'\d')


def load_fonts():
    """Register the bundled Barlow faces once. Falls back to Segoe UI if they are missing."""
    global _loaded, FAMILY
    if _loaded:
        return
    _loaded = True
    families = set()
    for file in sorted((Path(__file__).resolve().parent / 'fonts').glob('*.ttf')):
        font_id = QFontDatabase.addApplicationFont(str(file))
        if font_id >= 0:
            families.update(QFontDatabase.applicationFontFamilies(font_id))
    if 'Barlow' not in families:
        FAMILY = FALLBACK


def font(px, weight=QFont.Bold, spacing=0.0, family=''):
    f = QFont(family or FAMILY)
    f.setPixelSize(max(1, round(px)))
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


def alpha(color, a):
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def steady(text):
    """Text used for measuring: every digit becomes '0', so 980 and 1411 rpm reserve the
    same width per digit and the window keeps its size while values change."""
    return _DIGITS.sub('0', text)


@dataclass
class Row:
    label: str
    value: str
    color: QColor = None           # None = the text colour
    level: float = None            # 0..1 draws a thin gauge under the value (batteries)
    key: str = ''                  # metric key, for tooltips


@dataclass
class Group:
    caption: str
    rows: list


@dataclass
class Content:
    number: str = '--'
    unit: str = ''
    headline: str = ''
    subline: str = ''
    caption: str = 'MONITOR'
    hero_key: str = ''
    groups: list = field(default_factory=list)
    family: str = ''               # '' = Barlow
    framed: bool = False           # a background panel is drawn: leave a wider margin


@dataclass
class Ink:
    text: QColor
    accent: QColor
    shadow: QColor


def ink_for(theme_name, text='', accent=''):
    """White lettering and light-blue accent with a soft dark shadow, as on Linux; on the
    Light theme, dark lettering. text/accent override the lettering and the big number
    ('' = automatic). Light lettering gets a dark shadow, dark lettering a light halo,
    so any colour stays readable."""
    light = theme_name == 'light'
    ink_text = QColor(text) if text else QColor('#141821') if light else QColor(TEXT)
    ink_accent = QColor(accent) if accent else QColor('#2f7fb5') if light else QColor(ACCENT)
    shadow = QColor(0, 0, 0, 170) if ink_text.lightness() >= 128 else QColor(255, 255, 255, 200)
    return Ink(ink_text, ink_accent, shadow)


# Font choices offered in the menu: (name, family). '' = the bundled Barlow. The others
# ship with Windows 10/11.
FONTS = (('Barlow (like the Still clock)', ''), ('Bahnschrift', 'Bahnschrift'),
         ('Segoe UI Variable', 'Segoe UI Variable Display'), ('Arial', 'Arial'), ('Consolas', 'Consolas'),
         ('Georgia', 'Georgia'), ('Impact', 'Impact'))
if sys.platform.startswith('linux'):
    # Common Linux families; the menu leaves out any that are not installed.
    FALLBACK = 'Noto Sans'
    FONTS = (('Barlow (like the Still clock)', ''), ('Noto Sans', 'Noto Sans'), ('Inter', 'Inter'),
             ('Cantarell', 'Cantarell'), ('DejaVu Sans', 'DejaVu Sans'), ('Liberation Sans', 'Liberation Sans'),
             ('Ubuntu', 'Ubuntu'), ('Hack', 'Hack'), ('Noto Sans Mono', 'Noto Sans Mono'),
             ('Noto Serif', 'Noto Serif'))


def installed(family):
    return not family or family in QFontDatabase.families()

# Colour choices offered in the menu: (name, hex). '' = automatic for the theme.
TEXT_COLOURS = (('Automatic', ''), ('White', '#ffffff'), ('Warm white', '#fff1dc'), ('Light blue', '#8fd8ff'),
                ('Mint', '#a8f0c6'), ('Pink', '#ffb3d1'), ('Gold', '#ffd98a'), ('Black', '#141821'))
NUMBER_COLOURS = (('Automatic', ''), ('Light blue', '#8fd8ff'), ('White', '#ffffff'), ('Mint', '#7ce3a1'),
                  ('Violet', '#b9a6ff'), ('Pink', '#ff8fc4'), ('Orange', '#ffb36b'), ('Red', '#ff7a7a'))


def temp_color(temp):
    return None if temp is None or temp < 70 else WARN if temp < 85 else BAD


def battery_color(percent):
    return None if percent is None or percent > 40 else WARN if percent > 20 else BAD


# The shadow is drawn small and scaled up smoothly, a cheap blur: a wide soft glow, then a
# tighter one that keeps the lettering readable over bright wallpaper.
SHADOW_PASSES = ((4, 1.0), (2, 0.8))


def paint(p, content, scale, ink, min_size=None):
    """Draw (p not None) or only measure (p None). Returns (size, hits) where hits is a
    list of (QRectF, key) for tooltips. min_size keeps the widget from shrinking."""
    if p is None:
        return _layout(None, content, scale, ink, None, min_size)
    size, hits = _layout(None, content, scale, ink, None, min_size)
    p.save()
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    for step, strength in SHADOW_PASSES:
        small = QImage(max(1, size.width() // step), max(1, size.height() // step), QImage.Format_ARGB32_Premultiplied)
        small.fill(0)
        sp = QPainter(small)
        sp.scale(1 / step, 1 / step)
        sp.translate(0, 1.2 * scale)
        _layout(sp, content, scale, ink, alpha(ink.shadow, ink.shadow.alphaF() * strength), size)
        sp.end()
        p.drawImage(QRectF(0, 0, small.width() * step, small.height() * step), small)
    p.restore()
    return _layout(p, content, scale, ink, None, size)


def _layout(p, c, scale, ink, shadow, min_size):
    """One routine both measures and draws. With shadow set, every mark is drawn in that colour."""
    def pen(color, a=1.0):
        if shadow is not None:
            return alpha(shadow, shadow.alphaF() * QColor(color).alphaF() * a)
        return alpha(color, QColor(color).alphaF() * a)

    def font_(px, weight=QFont.Bold, spacing=0.0):
        return font(px, weight, spacing, c.family)

    m = 46.0 * scale                     # Still's "main size"
    pad = (18 if c.framed else 10) * scale
    floor_w = min_size.width() if min_size else 0

    num_font = font_(m * 2.2, QFont.Bold)
    num_fm = QFontMetricsF(num_font)
    ink_rect = num_fm.tightBoundingRect('8')
    num_top, num_h = ink_rect.top(), ink_rect.height()
    num_w = max(num_fm.horizontalAdvance('88'), num_fm.horizontalAdvance(steady(c.number)))

    cap_size = m * .2
    cap_font = font_(cap_size, QFont.DemiBold, cap_size * .85)
    cap_fm = QFontMetricsF(cap_font)
    cap_len = cap_fm.horizontalAdvance(c.caption)
    cap_col = cap_fm.height() + m * .22

    y = pad
    head_font = font_(m * .72, QFont.Bold)
    head_fm = QFontMetricsF(head_font)
    head_ink = head_fm.tightBoundingRect('M')
    head_x = pad + cap_col + m * .3
    # behind the end of the headline: Barlow's spacing, pushed right for wider fonts
    num_x = max(pad + cap_col + m * 1.7, head_x + head_fm.horizontalAdvance(c.headline) + m * .14)
    head_cap = y + num_h * .38
    head_base = head_cap - head_ink.top()

    sub_font = font_(max(11.0, m * .3), QFont.Bold, 1.2 * scale)
    sub_fm = QFontMetricsF(sub_font)
    sub_ink = sub_fm.tightBoundingRect('8')
    sub_cap = head_cap + head_ink.height() + m * .3
    sub_base = sub_cap - sub_ink.top()
    line_y = sub_cap + sub_ink.height() + m * .2
    line_w = max(m * 1.1, sub_fm.horizontalAdvance(steady(c.subline)) * .9)

    unit_font = font_(m * .62, QFont.Bold)
    unit_fm = QFontMetricsF(unit_font)
    unit_w = unit_fm.horizontalAdvance(c.unit) + m * .06 if c.unit else 0
    width = max(num_x + num_w + unit_w, head_x + head_fm.horizontalAdvance(c.headline),
                head_x + sub_fm.horizontalAdvance(steady(c.subline)), head_x + line_w)
    hero_bottom = max(y + num_h, y + cap_len, line_y + 2 * scale)
    hits = [(QRectF(head_x, y, num_x + num_w + unit_w - head_x, hero_bottom - y), c.hero_key)]

    if p is not None:
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        # big number, fading towards its base like Still's poster day
        p.setFont(num_font)
        if shadow is None:
            grad = QLinearGradient(0, y, 0, y + num_h)
            grad.setColorAt(0, alpha(ink.accent, 1))
            grad.setColorAt(.45, alpha(ink.accent, 225 / 255))
            grad.setColorAt(1, alpha(ink.accent, 28 / 255))
            p.setPen(QPen(grad, 0))
        else:
            p.setPen(pen(ink.shadow, .35))   # the number is faded, so only a faint shadow
        number_w = num_fm.horizontalAdvance(c.number)
        p.drawText(QPointF(num_x + (num_w - number_w) / 2, y - num_top), c.number)
        if c.unit:                        # top right of the number, like a superscript
            p.setFont(unit_font)
            p.setPen(pen(ink.accent, .95))
            p.drawText(QPointF(num_x + (num_w + number_w) / 2 + m * .06, y - unit_fm.tightBoundingRect('C').top()), c.unit)
        # vertical caption, reading bottom to top
        p.save()
        p.translate(pad + cap_fm.ascent(), y + max(num_h, cap_len))
        p.rotate(-90)
        p.setFont(cap_font)
        p.setPen(pen(ink.text, .9))
        p.drawText(QPointF(0, 0), c.caption)
        p.restore()
        p.setFont(head_font)
        p.setPen(pen(ink.text))
        p.drawText(QPointF(head_x, head_base), c.headline)
        p.setFont(sub_font)
        p.drawText(QPointF(head_x, sub_base), sub_fm.elidedText(c.subline, Qt.ElideRight, max(width, floor_w) - head_x))
        p.fillRect(QRectF(head_x, line_y, line_w, max(1.0, 1.6 * scale)), pen(ink.text, .85))

    # rows ---------------------------------------------------------------------
    label_font = font_(m * .3, QFont.DemiBold, 1.4 * scale)
    value_font = font_(m * .34, QFont.Bold, .6 * scale)
    group_font = font_(m * .22, QFont.Bold, m * .22 * .6)
    lf, vf, gf = QFontMetricsF(label_font), QFontMetricsF(value_font), QFontMetricsF(group_font)
    row_h = max(lf.height(), vf.height()) + 3 * scale
    left = head_x
    widest = 0.0
    for g in c.groups:
        for r in g.rows:
            widest = max(widest, min(lf.horizontalAdvance(r.label), 13 * m * .3)
                         + 22 * scale + vf.horizontalAdvance(steady(r.value)))
        widest = max(widest, gf.horizontalAdvance(g.caption))
    width = max(width, left + widest, floor_w - pad - 2)
    right = width

    cy = hero_bottom + m * .35
    for g in c.groups:
        if p is not None:
            p.setFont(group_font)
            p.setPen(pen(ink.accent))
            p.drawText(QPointF(left, cy + gf.ascent()), g.caption)
        cy += gf.height() + 4 * scale
        for r in g.rows:
            base = cy + max(lf.ascent(), vf.ascent())
            vw = vf.horizontalAdvance(r.value)
            if p is not None:
                p.setFont(label_font)
                p.setPen(pen(ink.text, .82))
                p.drawText(QPointF(left, base), lf.elidedText(r.label, Qt.ElideRight, right - left - vw - 16 * scale))
                p.setFont(value_font)
                p.setPen(pen(r.color or ink.text))
                p.drawText(QPointF(right - vw, base), r.value)
                if r.level is not None:
                    bar_y = base + 3 * scale
                    p.fillRect(QRectF(right - vw, bar_y, vw, 1.4 * scale), pen(ink.text, .2))
                    p.fillRect(QRectF(right - vw, bar_y, vw * max(0.0, min(1.0, r.level)), 1.4 * scale),
                               pen(r.color or ink.text))
            step = row_h + (4 * scale if r.level is not None else 0)
            hits.append((QRectF(left, cy, right - left, step), r.key))
            cy += step
        cy += m * .22
    size = QSize(int(right + pad + 2), int(max(hero_bottom, cy) + pad))
    if min_size is not None:
        size = size.expandedTo(min_size)
    return size, hits

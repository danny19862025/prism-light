# SPDX-License-Identifier: GPL-3.0-only
"""Fan curve diagram: temperature (x) against fan speed (y) for one header."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget
from .fans import CURVES, FAILSAFE_C, FAN_FLOOR, PUMP_FLOOR, curve_duty

T_MIN, T_MAX = 20, 100
ACCENT = '#a89bff'


class FanCurveChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.setAccessibleName('Fan curve diagram')
        self.header = None        # snapshot dict of the selected header
        self.temperature = None   # hottest board sensor

    def show_header(self, header, temperature):
        self.header, self.temperature = header, temperature
        self.update()

    def points(self):
        """(temperature, duty) points of the line to draw, or [] for BIOS."""
        h = self.header
        if not h or h['mode'] == 'Auto (BIOS)':
            return []
        floor = PUMP_FLOOR if h['pump'] else FAN_FLOOR
        if h['mode'] == 'Fixed':
            duty = max(floor, min(100, h['fixed']))
            return [(T_MIN, duty), (T_MAX, duty)]
        return [(t, max(floor, curve_duty(h['mode'], t))) for t in range(T_MIN, T_MAX + 1)]

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor('#0e1622'))
        plot = QRectF(58, 34, max(10, self.width() - 84), max(10, self.height() - 76))
        x = lambda t: plot.left() + (t - T_MIN)/(T_MAX - T_MIN)*plot.width()
        y = lambda d: plot.bottom() - d/100*plot.height()
        small = QFont()
        small.setPixelSize(11)
        p.setFont(small)
        # grid and axis labels
        for d in range(0, 101, 20):
            p.setPen(QPen(QColor('#22304a'), 1))
            p.drawLine(QPointF(plot.left(), y(d)), QPointF(plot.right(), y(d)))
            p.setPen(QColor('#8d9cb3'))
            p.drawText(QRectF(0, y(d) - 8, plot.left() - 8, 16), Qt.AlignRight | Qt.AlignVCenter, f'{d}%')
        for t in range(T_MIN, T_MAX + 1, 10):
            p.setPen(QPen(QColor('#22304a'), 1))
            p.drawLine(QPointF(x(t), plot.top()), QPointF(x(t), plot.bottom()))
            p.setPen(QColor('#8d9cb3'))
            p.drawText(QRectF(x(t) - 20, plot.bottom() + 6, 40, 16), Qt.AlignCenter, f'{t}°')
        p.drawText(QRectF(plot.left(), plot.bottom() + 22, plot.width(), 16), Qt.AlignCenter, 'Hottest motherboard sensor (°C)')
        h = self.header
        title = QFont()
        title.setPixelSize(14)
        title.setBold(True)
        p.setFont(title)
        p.setPen(QColor('#e8eaf3'))
        p.drawText(QRectF(plot.left(), 6, plot.width(), 22), Qt.AlignLeft | Qt.AlignVCenter,
                   'Select a fan header in the table' if not h else f"{h['label']}  ·  {h['mode']}")
        p.setFont(small)
        # failsafe line
        p.setPen(QPen(QColor('#ff7a8a'), 1.5, Qt.DashLine))
        p.drawLine(QPointF(x(FAILSAFE_C), plot.top()), QPointF(x(FAILSAFE_C), plot.bottom()))
        p.drawText(QRectF(x(FAILSAFE_C) - 70, plot.top() + 2, 66, 16), Qt.AlignRight, 'failsafe')
        if not h:
            return
        floor = PUMP_FLOOR if h['pump'] else FAN_FLOOR
        # floor band
        p.fillRect(QRectF(plot.left(), y(floor), plot.width(), plot.bottom() - y(floor)), QColor(255, 122, 138, 28))
        p.setPen(QColor('#c48a96'))
        p.drawText(QRectF(plot.left() + 6, y(floor) + 2, 200, 16), Qt.AlignLeft, f'minimum {floor}%')
        points = self.points()
        if not points:
            p.setPen(QColor('#8d9cb3'))
            p.drawText(plot, Qt.AlignCenter, 'The BIOS controls this fan.\nPick Fixed or a curve to draw its line.')
        else:
            path = QPainterPath(QPointF(x(points[0][0]), y(points[0][1])))
            for t, d in points[1:]:
                path.lineTo(x(t), y(d))
            fill = QPainterPath(path)
            fill.lineTo(x(points[-1][0]), plot.bottom())
            fill.lineTo(x(points[0][0]), plot.bottom())
            fill.closeSubpath()
            p.fillPath(fill, QColor(168, 155, 255, 40))
            p.setPen(QPen(QColor(ACCENT), 3))
            p.drawPath(path)
            if h['mode'] in CURVES:
                p.setBrush(QColor('#0e1622'))
                p.setPen(QPen(QColor(ACCENT), 2))
                for t, d in CURVES[h['mode']]:
                    p.drawEllipse(QPointF(x(t), y(max(floor, d))), 4, 4)
        # live operating point
        if self.temperature is not None:
            t = max(T_MIN, min(T_MAX, self.temperature))
            duty = h['target'] if h['target'] is not None else h['duty']
            p.setPen(QPen(QColor('#45e0c2'), 1, Qt.DotLine))
            p.drawLine(QPointF(x(t), plot.top()), QPointF(x(t), plot.bottom()))
            if duty is not None:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor('#45e0c2'))
                p.drawEllipse(QPointF(x(t), y(duty)), 7, 7)
                rpm = '' if h['rpm'] is None else f"  ·  {h['rpm']} RPM"
                text = f'now {self.temperature:.0f}°C → {duty}%{rpm}'
                width = p.fontMetrics().horizontalAdvance(text) + 14
                left = x(t) + 12 if x(t) + 12 + width < plot.right() else x(t) - 12 - width
                box = QRectF(left, max(plot.top(), y(duty) - 34), width, 22)
                p.setPen(QPen(QColor('#45e0c2'), 1))
                p.setBrush(QColor('#0b121b'))
                p.drawRoundedRect(box, 5, 5)
                p.setPen(QColor('#e8eaf3'))
                p.drawText(box, Qt.AlignCenter, text)

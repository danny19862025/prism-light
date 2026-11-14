# SPDX-License-Identifier: GPL-3.0-only
"""Read-only system display widgets, independent of RGB device discovery."""
from collections import deque
import math
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel


class UsageGauge(QWidget):
    def __init__(self, title, accent, parent=None):
        super().__init__(parent)
        self.title, self.accent, self.value = title, QColor(accent), None
        self.setMinimumSize(180, 170)
        self.setAccessibleName(title)

    def set_value(self, value):
        self.value = float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None
        if self.value is not None:self.value = min(100, max(0, self.value))
        self.setAccessibleDescription('Unavailable' if self.value is None else f'{self.value:.1f} percent')
        self.update()

    def paintEvent(self, event):
        p = QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen);p.setBrush(QColor('#151d2b'));p.drawRoundedRect(QRectF(self.rect()).adjusted(1,1,-1,-1),12,12)
        p.setPen(QColor('#a8b5d0'));p.drawText(QRectF(16,12,self.width()-32,24),Qt.AlignCenter,self.title)
        size = min(self.width()-44,self.height()-66,120)
        ring = QRectF((self.width()-size)/2,48,size,size)
        p.setPen(QPen(QColor('#303a50'),9,Qt.SolidLine,Qt.RoundCap));p.setBrush(Qt.NoBrush)
        p.drawArc(ring,225*16,-270*16)
        if self.value is not None:
            p.setPen(QPen(self.accent,9,Qt.SolidLine,Qt.RoundCap));p.drawArc(ring,225*16,round(-270*16*self.value/100))
        font = p.font();font.setPixelSize(26);font.setBold(True);p.setFont(font);p.setPen(QColor('#eef1ff'))
        p.drawText(ring,Qt.AlignCenter,'—' if self.value is None else f'{self.value:.0f}%')


class UsageHistory(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent);self.samples = deque(maxlen=60)
        self.setMinimumHeight(180);self.setAccessibleName('CPU usage history, last 60 readings')

    def append(self, value):
        self.samples.append(value);self.update()

    def paintEvent(self, event):
        p = QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        area = QRectF(38,18,max(1,self.width()-54),max(1,self.height()-48))
        p.setPen(QPen(QColor('#29344a'),1))
        for value in (0,25,50,75,100):
            y=area.bottom()-area.height()*value/100
            p.drawLine(QPointF(area.left(),y),QPointF(area.right(),y))
        p.setPen(QColor('#9baac2'));p.drawText(2,22,'100%');p.drawText(12,round(area.bottom()),'0%')
        p.drawText(QRectF(area.left(),area.bottom()+8,area.width(),24),Qt.AlignRight,'Latest reading · updates every 2 seconds')
        if not self.samples:
            p.drawText(area,Qt.AlignCenter,'Waiting for CPU readings…');return
        path = QPainterPath()
        for index, value in enumerate(self.samples):
            point=QPointF(area.right()-(len(self.samples)-1-index)*area.width()/59,area.bottom()-value*area.height()/100)
            if index:path.lineTo(point)
            else:path.moveTo(point)
        p.setPen(QPen(QColor('#b6a7ff'),2));p.drawPath(path)
        if len(self.samples)==1:p.drawEllipse(point,3,3)


class SystemWidgets(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent);box=QVBoxLayout(self);box.setContentsMargins(0,0,0,0)
        row=QHBoxLayout();self.cpu=UsageGauge('CPU usage','#b6a7ff');self.memory=UsageGauge('Memory usage','#45e0c2')
        row.addWidget(self.cpu);row.addWidget(self.memory);box.addLayout(row)
        self.summary=QLabel('Waiting for system readings…');self.summary.setWordWrap(True);box.addWidget(self.summary)
        self.history=UsageHistory();box.addWidget(self.history)
        self.setAccessibleName('Live CPU and memory monitor')

    def update_metrics(self, metrics):
        if metrics.get('error'):
            self.cpu.set_value(None);self.memory.set_value(None)
            self.summary.setText('System readings unavailable: '+str(metrics['error']));return
        self.cpu.set_value(metrics.get('cpu'));self.memory.set_value(metrics.get('memory'))
        if self.cpu.value is not None:self.history.append(self.cpu.value)
        values=list(self.history.samples)
        self.summary.setText(f'CPU · average {sum(values)/len(values):.0f}% · peak {max(values):.0f}% · last {len(values)} readings' if values else 'Waiting for CPU readings…')

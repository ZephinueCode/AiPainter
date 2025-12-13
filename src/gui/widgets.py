# src/gui/widgets.py

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout)
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import (QPainter, QColor, QConicalGradient, QBrush, QPen, 
                         QLinearGradient, QPainterPath, QMouseEvent)
import math

class ProcreateColorPicker(QWidget):
    colorChanged = pyqtSignal(list) # [r, g, b]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(220, 220)
        
        # Color State
        self.hue = 0.0        # 0.0 - 1.0
        self.sat = 1.0        # 0.0 - 1.0
        self.val = 1.0        # 0.0 - 1.0
        self.current_color = QColor.fromHsvF(self.hue, self.sat, self.val)

        # UI Geometry
        self.ring_width = 25
        self.margin = 10
        
        # Interaction State
        self.dragging_ring = False
        self.dragging_box = False

    def set_color_rgb(self, r, g, b):
        """外部设置颜色"""
        c = QColor.fromRgbF(r, g, b)
        self.hue = max(0.0, min(1.0, c.hsvHueF()))
        # hsvHueF return -1 for grayscale, handle it
        if self.hue < 0: self.hue = 0.0
        self.sat = c.hsvSaturationF()
        self.val = c.valueF()
        self.current_color = c
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w, h = self.width(), self.height()
        center = QPointF(w/2, h/2)
        outer_radius = min(w, h)/2 - self.margin
        inner_radius = outer_radius - self.ring_width
        
        # 1. Draw Hue Ring
        self._draw_hue_ring(painter, center, inner_radius, outer_radius)
        
        # 2. Draw SV Box
        box_half_size = (inner_radius - 10) / math.sqrt(2) * 0.9 
        box_rect = QRectF(center.x() - box_half_size, center.y() - box_half_size,
                          box_half_size*2, box_half_size*2)
        self._draw_sv_box(painter, box_rect)
        
        # 3. Draw Indicators
        self._draw_hue_indicator(painter, center, inner_radius, outer_radius)
        self._draw_sv_indicator(painter, box_rect)

    def _draw_hue_ring(self, painter, center, r_in, r_out):
        gradient = QConicalGradient(center, 90)
        for i in range(0, 361, 10):
            hue_val = i % 360
            gradient.setColorAt(i/360.0, QColor.fromHsv(hue_val, 255, 255))
            
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(gradient))
        
        path = QPainterPath()
        path.addEllipse(center, r_out, r_out)
        path_inner = QPainterPath()
        path_inner.addEllipse(center, r_in, r_in)
        path = path.subtracted(path_inner)
        
        painter.drawPath(path)

    def _draw_sv_box(self, painter, rect):
        hue_color = QColor.fromHsvF(self.hue, 1.0, 1.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(hue_color)
        painter.drawRect(rect)
        
        g_sat = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.top())
        g_sat.setColorAt(0, QColor(255, 255, 255, 255))
        g_sat.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setBrush(g_sat)
        painter.drawRect(rect)
        
        g_val = QLinearGradient(rect.left(), rect.top(), rect.left(), rect.bottom())
        g_val.setColorAt(0, QColor(0, 0, 0, 0))
        g_val.setColorAt(1, QColor(0, 0, 0, 255))
        painter.setBrush(g_val)
        painter.drawRect(rect)
        
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(100,100,100), 1))
        painter.drawRect(rect)
        self.box_rect = rect

    def _draw_hue_indicator(self, painter, center, r_in, r_out):
        angle = -90 - (self.hue * 360) 
        rad = math.radians(angle)
        mid_r = (r_in + r_out) / 2
        ix = center.x() + mid_r * math.cos(rad)
        iy = center.y() + mid_r * math.sin(rad)
        painter.setPen(QPen(Qt.GlobalColor.black, 2))
        painter.setBrush(Qt.GlobalColor.white)
        painter.drawEllipse(QPointF(ix, iy), 6, 6)

    def _draw_sv_indicator(self, painter, rect):
        ix = rect.left() + self.sat * rect.width()
        iy = rect.top() + (1.0 - self.val) * rect.height()
        painter.setPen(QPen(Qt.GlobalColor.white, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(ix, iy), 6, 6)
        painter.setPen(QPen(Qt.GlobalColor.black, 1))
        painter.drawEllipse(QPointF(ix, iy), 5, 5)

    def mousePressEvent(self, event):
        pos = event.position()
        center = QPointF(self.width()/2, self.height()/2)
        dist = math.sqrt((pos.x()-center.x())**2 + (pos.y()-center.y())**2)
        outer_radius = min(self.width(), self.height())/2 - self.margin
        inner_radius = outer_radius - self.ring_width
        
        if hasattr(self, 'box_rect') and self.box_rect.contains(pos):
            self.dragging_box = True
            self._update_sv_from_pos(pos)
        elif inner_radius <= dist <= outer_radius:
            self.dragging_ring = True
            self._update_hue_from_pos(pos)

    def mouseMoveEvent(self, event):
        if self.dragging_box:
            self._update_sv_from_pos(event.position())
        elif self.dragging_ring:
            self._update_hue_from_pos(event.position())

    def mouseReleaseEvent(self, event):
        self.dragging_ring = False
        self.dragging_box = False

    def _update_hue_from_pos(self, pos):
        center = QPointF(self.width()/2, self.height()/2)
        dx = pos.x() - center.x()
        dy = pos.y() - center.y()
        deg = math.degrees(math.atan2(dy, dx))
        angle_from_top = deg + 90
        if angle_from_top < 0: angle_from_top += 360
        self.hue = 1.0 - (angle_from_top / 360.0)
        self.hue = max(0.0, min(1.0, self.hue))
        self._emit_color()
        self.update()

    def _update_sv_from_pos(self, pos):
        r = self.box_rect
        x = max(r.left(), min(r.right(), pos.x()))
        y = max(r.top(), min(r.bottom(), pos.y()))
        self.sat = (x - r.left()) / r.width()
        self.val = 1.0 - (y - r.top()) / r.height()
        self._emit_color()
        self.update()

    def _emit_color(self):
        self.current_color = QColor.fromHsvF(self.hue, self.sat, self.val)
        self.colorChanged.emit([self.current_color.redF(), 
                                self.current_color.greenF(), 
                                self.current_color.blueF()])

class ColorPickerWidget(QWidget):
    colorChanged = pyqtSignal(list)
    def __init__(self):
        super().__init__()
        l = QVBoxLayout(self)
        self.picker = ProcreateColorPicker()
        self.picker.colorChanged.connect(self.colorChanged)
        l.addWidget(self.picker, 0, Qt.AlignmentFlag.AlignCenter)

    def set_color(self, rgb):
        # rgb is list [r, g, b] 0.0-1.0
        self.picker.set_color_rgb(rgb[0], rgb[1], rgb[2])
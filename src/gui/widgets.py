# src/gui/widgets.py

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar, QFrame, QSlider, QGridLayout)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QPointF, QRectF
from PyQt6.QtGui import (QPainter, QColor, QConicalGradient, QBrush, QPainterPath, QLinearGradient, QPen, QPixmap, QImage, QMouseEvent)
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
        self.update()

class PaletteButton(QPushButton):
    """Simple color block button"""
    colorSelected = pyqtSignal(list) # [r, g, b] (float)
    colorSaved = pyqtSignal(int) # index

    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.color = [0.8, 0.8, 0.8] # Default gray
        self.setFixedSize(30, 30)
        self.update_style()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_rgb(self, rgb):
        self.color = rgb
        self.update_style()

    def update_style(self):
        r, g, b = [int(c*255) for c in self.color]
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: rgb({r}, {g}, {b});
                border: 1px solid #888;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                border: 2px solid #fff;
            }}
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.colorSelected.emit(self.color)
        elif event.button() == Qt.MouseButton.RightButton:
            self.colorSaved.emit(self.index)

class ColorPickerWidget(QWidget):
    colorChanged = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setSpacing(5)
        
        # 1. Wheel
        self.wheel = ProcreateColorPicker()
        self.wheel.colorChanged.connect(self.colorChanged) # Proxy signal directly
        layout.addWidget(self.wheel, 0, Qt.AlignmentFlag.AlignCenter)
        
        # 2. Palette Grid (2x5) instead of sliders
        palette_frame = QFrame()
        p_layout = QGridLayout(palette_frame)
        p_layout.setContentsMargins(0, 5, 0, 0)
        p_layout.setSpacing(4)
        
        self.palette_buttons = []
        for i in range(10):
            btn = PaletteButton(i)
            btn.colorSelected.connect(self.load_from_palette)
            btn.colorSaved.connect(self.save_to_palette)
            self.palette_buttons.append(btn)
            row = 0 if i < 5 else 1
            col = i % 5
            p_layout.addWidget(btn, row, col)
            
        layout.addWidget(palette_frame)
        self.current_rgb = [0, 0, 0] # Internal state tracking

    def set_color(self, rgb):
        self.current_rgb = rgb
        self.wheel.set_color_rgb(rgb[0], rgb[1], rgb[2])

    def load_from_palette(self, rgb):
        self.set_color(rgb)
        self.colorChanged.emit(rgb)

    def save_to_palette(self, index):
        # Save current color to the button
        self.palette_buttons[index].set_rgb(self.current_rgb)

class GradientSlider(QWidget):
    gradientChanged = pyqtSignal(list) # List of (pos, (r,g,b))
    stopSelected = pyqtSignal(list) # current color of selected stop

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(60)
        self.setMouseTracking(True)
        # List of [pos (0-1), [r,g,b] (0-255)]
        self.stops = [
            [0.0, [0, 0, 0]],
            [1.0, [255, 255, 255]]
        ]
        self.selected_index = -1
        self.dragging_index = -1
        self.hover_index = -1
        
        self.margin_x = 10
        self.bar_height = 20
        self.handle_size = 12

    def set_current_stop_color(self, rgb_0_1):
        if self.selected_index != -1:
            self.stops[self.selected_index][1] = [int(c*255) for c in rgb_0_1]
            self.update()
            self.gradientChanged.emit(self.stops)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w, h = self.width(), self.height()
        bar_y = (h - self.bar_height) / 2
        bar_rect = QRectF(self.margin_x, bar_y, w - 2*self.margin_x, self.bar_height)
        
        # Draw Gradient Bar
        grad = QLinearGradient(bar_rect.left(), 0, bar_rect.right(), 0)
        sorted_stops = sorted(self.stops, key=lambda x: x[0])
        for pos, col in sorted_stops:
            grad.setColorAt(pos, QColor(col[0], col[1], col[2]))
            
        painter.setBrush(grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(bar_rect, 4, 4)
        
        # Draw Handles
        for i, (pos, col) in enumerate(self.stops):
            cx = self.margin_x + pos * bar_rect.width()
            cy = bar_y + self.bar_height + 8
            
            # Triangle shape
            path = QPainterPath()
            path.moveTo(cx, bar_y + self.bar_height)
            path.lineTo(cx - 6, cy + 6)
            path.lineTo(cx + 6, cy + 6)
            path.closeSubpath()
            
            # Color indicator box
            indicator_rect = QRectF(cx - 5, cy + 8, 10, 10)
            
            # Selection Highlight
            if i == self.selected_index:
                painter.setPen(QPen(Qt.GlobalColor.blue, 2))
            elif i == self.hover_index:
                painter.setPen(QPen(Qt.GlobalColor.gray, 2))
            else:
                painter.setPen(QPen(Qt.GlobalColor.black, 1))
            
            painter.setBrush(Qt.GlobalColor.white)
            painter.drawPath(path)
            
            painter.setBrush(QColor(col[0], col[1], col[2]))
            painter.drawRect(indicator_rect)

    def mousePressEvent(self, event):
        pos = event.position()
        w = self.width() - 2*self.margin_x
        bar_rect = QRectF(self.margin_x, (self.height()-self.bar_height)/2, w, self.bar_height)
        
        # Check handles
        clicked_handle = -1
        for i, (p, c) in enumerate(self.stops):
            cx = self.margin_x + p * w
            cy = bar_rect.bottom() + 8
            # Hit area around handle
            if abs(pos.x() - cx) < 10 and abs(pos.y() - cy) < 20:
                clicked_handle = i
                break
        
        if clicked_handle != -1:
            self.selected_index = clicked_handle
            self.dragging_index = clicked_handle
            col = self.stops[self.selected_index][1]
            self.stopSelected.emit([c/255.0 for c in col])
        elif bar_rect.contains(pos):
            # Add stop
            rel_x = (pos.x() - self.margin_x) / w
            rel_x = max(0.0, min(1.0, rel_x))
            
            # Interpolate color roughly
            new_col = [128, 128, 128] # Default
            # Simple find neighbor
            sorted_s = sorted(self.stops, key=lambda x: x[0])
            for k in range(len(sorted_s)-1):
                if sorted_s[k][0] <= rel_x <= sorted_s[k+1][0]:
                    ratio = (rel_x - sorted_s[k][0]) / (sorted_s[k+1][0] - sorted_s[k][0])
                    c1 = sorted_s[k][1]
                    c2 = sorted_s[k+1][1]
                    new_col = [int(c1[j]*(1-ratio) + c2[j]*ratio) for j in range(3)]
                    break
            
            self.stops.append([rel_x, new_col])
            self.selected_index = len(self.stops) - 1
            self.dragging_index = self.selected_index
            self.stopSelected.emit([c/255.0 for c in new_col])
            self.gradientChanged.emit(self.stops)
            
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position()
        w = self.width() - 2*self.margin_x
        
        if self.dragging_index != -1:
            rel_x = (pos.x() - self.margin_x) / w
            rel_x = max(0.0, min(1.0, rel_x))
            self.stops[self.dragging_index][0] = rel_x
            self.gradientChanged.emit(self.stops)
            self.update()
            return

        # Hover check
        self.hover_index = -1
        bar_rect = QRectF(self.margin_x, (self.height()-self.bar_height)/2, w, self.bar_height)
        for i, (p, c) in enumerate(self.stops):
            cx = self.margin_x + p * w
            cy = bar_rect.bottom() + 8
            if abs(pos.x() - cx) < 10 and abs(pos.y() - cy) < 20:
                self.hover_index = i
                break
        self.update()

    def mouseReleaseEvent(self, event):
        self.dragging_index = -1

    def mouseDoubleClickEvent(self, event):
        # Remove stop if clicked, but keep at least 2
        if len(self.stops) <= 2: return
        
        pos = event.position()
        w = self.width() - 2*self.margin_x
        bar_rect = QRectF(self.margin_x, (self.height()-self.bar_height)/2, w, self.bar_height)
        
        for i, (p, c) in enumerate(self.stops):
            cx = self.margin_x + p * w
            cy = bar_rect.bottom() + 8
            if abs(pos.x() - cx) < 10 and abs(pos.y() - cy) < 20:
                self.stops.pop(i)
                self.selected_index = -1
                self.gradientChanged.emit(self.stops)
                self.update()
                break

# === Generator Status Widget ===
class GeneratorStatusWidget(QFrame):
    copyRequested = pyqtSignal(QImage)
    addLayerRequested = pyqtSignal(QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setStyleSheet("""
            GeneratorStatusWidget {
                background-color: #ffffff; 
                border: 1px solid #c0c0c0; 
                border-radius: 8px;
            }
            QLabel { color: #333; }
        """)
        self.setFixedWidth(300)
        self.setMinimumHeight(300) # Increased min height
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(10)
        
        # Header
        header_layout = QHBoxLayout()
        self.lbl_title = QLabel("AI Generator")
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(self.lbl_title)
        header_layout.addStretch()
        
        self.btn_close = QPushButton("×")
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet("border: none; font-weight: bold; font-size: 16px; color: #888;")
        self.btn_close.clicked.connect(self.hide)
        header_layout.addWidget(self.btn_close)
        self.layout.addLayout(header_layout)
        
        # Preview Image
        self.lbl_preview = QLabel()
        self.lbl_preview.setFixedSize(230, 230)
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setStyleSheet("background: #f0f0f0; border-radius: 4px; border: 1px dashed #ddd;")
        self.lbl_preview.setText("Generating...")
        self.layout.addWidget(self.lbl_preview, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # Status Text
        self.lbl_info = QLabel("Processing...")
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_info.setStyleSheet("font-size: 11px; color: #666;")
        self.layout.addWidget(self.lbl_info)
        
        # Action Buttons
        self.btn_container = QWidget()
        btn_layout = QHBoxLayout(self.btn_container)
        btn_layout.setContentsMargins(0,0,0,0)
        btn_layout.setSpacing(10)
        
        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.setStyleSheet("""
            QPushButton { background-color: #f0f0f0; border: 1px solid #ccc; border-radius: 4px; padding: 6px; }
            QPushButton:hover { background-color: #e0e0e0; }
        """)
        self.btn_copy.clicked.connect(self._on_copy)
        
        self.btn_add = QPushButton("Add Layer")
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.setStyleSheet("""
            QPushButton { background-color: #f0f0f0; border: 1px solid #ccc; border-radius: 4px; padding: 6px; }
            QPushButton:hover { background-color: #e0e0e0; }
        """)
        self.btn_add.clicked.connect(self._on_add)
        
        btn_layout.addWidget(self.btn_copy)
        btn_layout.addWidget(self.btn_add)
        self.layout.addWidget(self.btn_container)
        
        self.current_image = None
        self.reset_state()

    def start_loading(self):
        self.show()
        self.raise_()
        self.lbl_title.setText("Generating...")
        self.lbl_preview.clear()
        self.lbl_preview.setText("Generating...")
        self.lbl_info.setText("Please wait...")
        self.btn_container.hide()
        self.current_image = None
        self.adjustSize()

    def finish_loading(self, image):
        self.current_image = image
        self.lbl_title.setText("Result Ready")
        self.lbl_info.setText("Generation successful.")
        
        pix = QPixmap.fromImage(image)
        scaled = pix.scaled(self.lbl_preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.lbl_preview.setPixmap(scaled)
        
        self.btn_container.show()
        self.adjustSize()

    def show_error(self, msg):
        self.lbl_title.setText("Error")
        self.lbl_preview.clear()
        self.lbl_preview.setText("Failed")
        self.lbl_info.setText(msg)
        self.btn_container.hide()
        self.adjustSize()

    def reset_state(self):
        self.hide()

    def _on_copy(self):
        if self.current_image:
            self.copyRequested.emit(self.current_image)
            self.hide()

    def _on_add(self):
        if self.current_image:
            self.addLayerRequested.emit(self.current_image)
            self.hide()
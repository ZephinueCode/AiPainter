# src/gui/canvas.py

import numpy as np
from PIL import Image
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import QWidget, QScrollBar, QGridLayout, QMenu, QApplication, QMessageBox
from PyQt6.QtCore import Qt, pyqtSignal, QPointF
from PyQt6.QtGui import QPainter, QColor, QPainterPath
from OpenGL.GL import *
from src.core.brush_manager import BrushConfig
from src.core.logic import Node, GroupLayer, PaintLayer, PaintCommand, UndoStack, ProjectLogic, TextLayer
from src.core.tools import RectSelectTool, LassoTool, BucketTool, PickerTool, SmudgeTool, TextTool, ClipboardUtils
from src.core.processor import ImageProcessor
from src.gui.dialogs import GradientMapDialog, AdjustmentDialog

class GLCanvas(QOpenGLWidget):
    layer_structure_changed = pyqtSignal()
    view_changed = pyqtSignal()
    brush_color_changed = pyqtSignal(list)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc_width = 1920
        self.doc_height = 1080
        self.zoom = 1.0
        self.offset = QPointF(0, 0)
        
        self.root = GroupLayer("Root")
        self.active_layer = None
        
        self.current_brush = None
        self._brush_color = [0,0,0]
        self.brush_texture_id = None
        self.last_pos = None

        self.is_panning = False
        self.last_pan_pos = QPointF(0, 0)

        self.undo_stack = UndoStack()
        self._stroke_start_image = None
        
        self.selection_path = QPainterPath()
        
        self.active_tool = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    @property
    def brush_color(self):
        return self._brush_color
    
    @brush_color.setter
    def brush_color(self, val):
        self._brush_color = val
        self.brush_color_changed.emit(val if isinstance(val, list) else list(val))

    def set_tool(self, tool_name):
        if self.active_tool:
            self.active_tool.deactivate()
            self.active_tool = None
        
        self.setCursor(Qt.CursorShape.ArrowCursor)
        
        if tool_name == "Rect Select": self.active_tool = RectSelectTool(self)
        elif tool_name == "Lasso": self.active_tool = LassoTool(self)
        elif tool_name == "Fill Select": self.active_tool = BucketTool(self)
        elif tool_name == "Picker": self.active_tool = PickerTool(self)
        elif tool_name == "Smudge": self.active_tool = SmudgeTool(self)
        elif tool_name == "Text": self.active_tool = TextTool(self)
        
        if self.active_tool:
            self.active_tool.activate()
        
        self.update()

    def initializeGL(self):
        glEnable(GL_BLEND)
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        
        if not self.root.children:
            bg = PaintLayer(self.doc_width, self.doc_height, "Background")
            self._fill_layer(bg, [1,1,1])
            self.root.add_child(bg)
            self.active_layer = bg
            
        self.layer_structure_changed.emit()

    def resize_canvas_smart(self, new_w, new_h, anchor=(0.5, 0.5)):
        self.makeCurrent()
        diff_w = new_w - self.doc_width
        diff_h = new_h - self.doc_height
        off_x = int(diff_w * anchor[0])
        off_y = int(diff_h * anchor[1])

        def resize_node_recursive(node):
            if isinstance(node, PaintLayer):
                old_img = node.get_image()
                node.width = new_w
                node.height = new_h
                node.cleanup()
                node.setup()
                new_img = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
                new_img.paste(old_img, (off_x, off_y))
                node.load_from_image(new_img)
            elif isinstance(node, GroupLayer):
                for child in node.children:
                    resize_node_recursive(child)
        resize_node_recursive(self.root)
        self.doc_width = new_w
        self.doc_height = new_h
        self.update()
        self.view_changed.emit()

    def _fill_layer(self, layer, color):
        self.makeCurrent()
        glBindFramebuffer(GL_FRAMEBUFFER, layer.fbo)
        glViewport(0, 0, layer.width, layer.height)
        glClearColor(color[0], color[1], color[2], 1.0)
        glClear(GL_COLOR_BUFFER_BIT)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)

    def paintGL(self):
        self.makeCurrent()
        
        dpr = self.devicePixelRatio()
        glViewport(0, 0, int(self.width() * dpr), int(self.height() * dpr))
        
        glEnable(GL_BLEND)
        glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        
        glClearColor(0.15, 0.15, 0.15, 1.0)
        glClear(GL_COLOR_BUFFER_BIT)

        glMatrixMode(GL_PROJECTION); glLoadIdentity()
        glOrtho(0, self.width(), self.height(), 0, -1, 1)
        glMatrixMode(GL_MODELVIEW); glLoadIdentity()
        glTranslatef(self.offset.x(), self.offset.y(), 0)
        glScalef(self.zoom, self.zoom, 1)

        glColor3f(0.5, 0.5, 0.5)
        glRectf(0, 0, self.doc_width, self.doc_height)
        
        glEnable(GL_TEXTURE_2D)
        glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        self._render_node(self.root)
        
        glDisable(GL_TEXTURE_2D)
        glColor3f(0,0,0)
        glLineWidth(2)
        glBegin(GL_LINE_LOOP)
        glVertex2f(0, 0)
        glVertex2f(0, self.doc_height)
        glVertex2f(self.doc_width, self.doc_height)
        glVertex2f(self.doc_width, 0)
        glEnd()

    def paintEvent(self, event):
        super().paintEvent(event)
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        if self.active_tool:
            if hasattr(self.active_tool, 'draw_overlay'):
                painter.save()
                painter.translate(self.offset)
                painter.scale(self.zoom, self.zoom)
                self.active_tool.draw_overlay(painter)
                painter.restore()
        else:
            painter.translate(self.offset)
            painter.scale(self.zoom, self.zoom)
            if not self.selection_path.isEmpty():
                from PyQt6.QtGui import QPen
                pen = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(self.selection_path)
                pen.setColor(Qt.GlobalColor.black); pen.setDashOffset(5)
                painter.setPen(pen); painter.drawPath(self.selection_path)
            
        painter.end()

    def _render_node(self, node):
        if not node.visible: return
        if isinstance(node, GroupLayer):
            for child in node.children:
                self._render_node(child)
        elif isinstance(node, PaintLayer):
            glBindTexture(GL_TEXTURE_2D, node.texture)
            op = node.opacity * self._get_parent_opacity(node)
            glColor4f(op, op, op, op) 
            
            glBegin(GL_QUADS)
            glTexCoord2f(0, 1); glVertex2f(0, 0)
            glTexCoord2f(0, 0); glVertex2f(0, node.height)
            glTexCoord2f(1, 0); glVertex2f(node.width, node.height)
            glTexCoord2f(1, 1); glVertex2f(node.width, 0)
            glEnd()

    def _get_parent_opacity(self, node):
        op = 1.0
        p = node.parent
        while p and p != self.root:
            if not p.visible: return 0.0 # Stop recursion if parent hidden
            op *= p.opacity
            p = p.parent
        return op

    def mousePressEvent(self, event):
        self.setFocus()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.is_panning = True
            self.last_pan_pos = event.position()
            return
        
        pos = (event.position() - self.offset) / self.zoom
        
        if self.active_tool:
            self.active_tool.mouse_press(event, event.position(), pos)
        else:
            if event.button() == Qt.MouseButton.RightButton:
                self.show_default_context_menu(event)
            elif event.button() == Qt.MouseButton.LeftButton:
                if isinstance(self.active_layer, GroupLayer):
                    QMessageBox.warning(self, "Group Selected", "Cannot paint on a Group Layer.\nPlease select a Paint Layer.")
                    return

                self.last_pos = pos 
                if self.active_layer and isinstance(self.active_layer, PaintLayer) and self.active_layer.visible and self.current_brush:
                    self.makeCurrent()
                    self._stroke_start_image = self.active_layer.get_image()
                    # Ensure texture is loaded
                    self._update_brush_texture()
                    self._paint_stroke(pos)

    def mouseMoveEvent(self, event):
        if self.is_panning:
            delta = event.position() - self.last_pan_pos
            self.offset += delta
            self.last_pan_pos = event.position()
            self.update()
            self.view_changed.emit()
            return

        pos = (event.position() - self.offset) / self.zoom

        if self.active_tool:
            self.active_tool.mouse_move(event, event.position(), pos)
        elif self.last_pos:
            self._paint_stroke(pos)
            self.last_pos = pos

    def mouseReleaseEvent(self, event):
        if self.is_panning:
            self.is_panning = False
            return
            
        if self.active_tool:
            self.active_tool.mouse_release(event, event.position(), (event.position() - self.offset) / self.zoom)
        else:
            if self._stroke_start_image and self.active_layer:
                self.makeCurrent()
                end_image = self.active_layer.get_image()
                cmd = PaintCommand(self.active_layer, self._stroke_start_image, end_image)
                self.undo_stack.push(cmd)
                self._stroke_start_image = None
            self.last_pos = None

    def keyPressEvent(self, event):
        ctrl = event.modifiers() & Qt.KeyboardModifier.ControlModifier
        
        if event.key() == Qt.Key.Key_Delete or event.key() == Qt.Key.Key_Backspace:
            if not self.selection_path.isEmpty():
                if self.active_layer and isinstance(self.active_layer, PaintLayer):
                    mask = ClipboardUtils.get_selection_mask(self)
                    if mask:
                        old_img = self.active_layer.get_image()
                        transparent = Image.new("RGBA", old_img.size, (0,0,0,0))
                        new_img = Image.composite(transparent, old_img, mask)
                        
                        cmd = PaintCommand(self.active_layer, old_img, new_img)
                        self.undo_stack.push(cmd)
                        self.active_layer.load_from_image(new_img)
                        self.update()
            return

        if event.key() == Qt.Key.Key_Escape:
            if self.active_tool and hasattr(self.active_tool, 'deactivate'):
                self.active_tool.deactivate()
            if not self.active_tool:
                self.selection_path = QPainterPath()
            self.update()
            
        elif ctrl and event.key() == Qt.Key.Key_Z:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier: self.undo_stack.redo()
            else: self.undo_stack.undo()
            self.update()
        elif ctrl and event.key() == Qt.Key.Key_Y:
            self.undo_stack.redo()
            self.update()
            
        elif ctrl and event.key() == Qt.Key.Key_C:
            ClipboardUtils.copy(self)
        elif ctrl and event.key() == Qt.Key.Key_X:
            ClipboardUtils.cut(self)
        elif ctrl and event.key() == Qt.Key.Key_V:
            ClipboardUtils.paste(self)
        else:
            super().keyPressEvent(event)

    def show_default_context_menu(self, event):
        menu = QMenu(self)
        clipboard = QApplication.clipboard()
        can_paste = clipboard.mimeData().hasImage()
        act_paste = menu.addAction("Paste", lambda: ClipboardUtils.paste(self))
        act_paste.setEnabled(can_paste)
        
        menu.addSeparator()
        menu.addAction("HSL Adjustment", lambda: self.open_adjustment("HSL"))
        menu.addAction("Contrast", lambda: self.open_adjustment("Contrast"))
        menu.addAction("Exposure", lambda: self.open_adjustment("Exposure"))
        menu.addAction("Gaussian Blur", lambda: self.open_adjustment("Blur"))
        menu.addAction("Gradient Map...", self.open_gradient_map)
        
        menu.exec(event.globalPosition().toPoint())

    def open_adjustment(self, type):
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer): return
        
        params = []
        func = None
        
        if type == "HSL":
            params = [
                {"name": "Hue", "min": -180, "max": 180, "default": 0, "scale": 1.0},
                {"name": "Saturation (x100%)", "min": 0, "max": 200, "default": 100, "scale": 0.01},
                {"name": "Lightness", "min": -100, "max": 100, "default": 0, "scale": 1.0}
            ]
            func = ImageProcessor.adjust_hsl
        elif type == "Contrast":
            params = [{"name": "Factor (x100%)", "min": 0, "max": 300, "default": 100, "scale": 0.01}]
            func = ImageProcessor.adjust_contrast
        elif type == "Exposure":
            params = [{"name": "Factor (x100%)", "min": 0, "max": 300, "default": 100, "scale": 0.01}]
            func = ImageProcessor.adjust_exposure
        elif type == "Blur":
            params = [{"name": "Radius", "min": 0, "max": 50, "default": 0, "scale": 1.0}]
            func = ImageProcessor.apply_blur

        if func:
            dlg = AdjustmentDialog(self, type, func, params)
            if dlg.exec():
                cmd = PaintCommand(self.active_layer, dlg.original_img, self.active_layer.get_image())
                self.undo_stack.push(cmd)

    def open_gradient_map(self):
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer):
             QMessageBox.warning(self, "Invalid Layer", "Please select a Paint Layer.")
             return
        
        layer = self.active_layer
        self.makeCurrent()
        old_img = layer.get_image().copy()
        
        dlg = GradientMapDialog(self)
        if dlg.exec():
            self.makeCurrent()
            new_img = layer.get_image().copy()
            cmd = PaintCommand(layer, old_img, new_img)
            self.undo_stack.push(cmd)
            self.update()
        else:
            # Revert logic handled by dialog reject, but ensure update
            pass

    def load_project(self, path):
        try:
            self.makeCurrent()
            width, height, root = ProjectLogic.load_project(path)
            self.doc_width = width; self.doc_height = height; self.root = root
            self.undo_stack = UndoStack()
            self.active_layer = None
            def find_first(node):
                if isinstance(node, PaintLayer): return node
                for c in node.children:
                    res = find_first(c)
                    if res: return res
                return None
            if self.root.children: self.active_layer = find_first(self.root)
            self.layer_structure_changed.emit(); self.update(); self.view_changed.emit()
        except Exception as e: print(e)

    def save_project(self, path):
        self.makeCurrent()
        ProjectLogic.save_project(self.root, self.doc_width, self.doc_height, path)

    def import_psd(self, path):
        try:
            self.makeCurrent()
            width, height, root = ProjectLogic.import_psd(path, self.doc_width, self.doc_height)
            self.doc_width = width; self.doc_height = height; self.root = root
            self.layer_structure_changed.emit(); self.update(); self.view_changed.emit()
        except Exception as e: print(e)

    def export_image(self, path):
        self.makeCurrent()
        fbo = glGenFramebuffers(1); tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, tex)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, self.doc_width, self.doc_height, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
        glBindFramebuffer(GL_FRAMEBUFFER, fbo); glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0)
        glViewport(0, 0, self.doc_width, self.doc_height); glClearColor(0,0,0,0); glClear(GL_COLOR_BUFFER_BIT)
        glMatrixMode(GL_PROJECTION); glLoadIdentity(); glOrtho(0, self.doc_width, self.doc_height, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW); glLoadIdentity()
        glEnable(GL_TEXTURE_2D); glBlendFunc(GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
        self._render_node(self.root)
        data = glReadPixels(0, 0, self.doc_width, self.doc_height, GL_RGBA, GL_UNSIGNED_BYTE)
        Image.frombytes("RGBA", (self.doc_width, self.doc_height), data).transpose(Image.FLIP_TOP_BOTTOM).save(path)
        glDeleteFramebuffers(1, [fbo]); glDeleteTextures([tex]); glBindFramebuffer(GL_FRAMEBUFFER, 0)

    def set_brush(self, config):
        self.current_brush = config
        self._update_brush_texture()
        if self.active_tool:
            self.active_tool.deactivate()
            self.active_tool = None
            self.update()

    def _update_brush_texture(self):
        if not self.current_brush: return
        
        self.makeCurrent()
        if self.brush_texture_id: glDeleteTextures([self.brush_texture_id])
        self.brush_texture_id = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self.brush_texture_id)
        
        if self.current_brush.texture:
            gray_img = self.current_brush.texture
            if gray_img.mode != 'L':
                gray_img = gray_img.convert('L')
            
            # Create white RGB image
            white = Image.new("RGB", gray_img.size, (255, 255, 255))
            # Put grayscale as alpha
            rgba_img = white.copy()
            rgba_img.putalpha(gray_img)
            
            width, height = rgba_img.size
            data = rgba_img.transpose(Image.FLIP_TOP_BOTTOM).tobytes("raw", "RGBA")
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, data)
            
        else:
            size = 128; img = np.zeros((size, size, 4), dtype=np.uint8)
            center = size/2; y, x = np.ogrid[:size, :size]; dist = np.sqrt((x-center)**2 + (y-center)**2)
            norm_dist = dist / (size/2); alpha = np.clip((1.0 - norm_dist) / (1.0 - self.current_brush.hardness + 0.001), 0, 1)
            val = (alpha * 255).astype(np.uint8)
            img[..., 0] = 255; img[..., 1] = 255; img[..., 2] = 255; img[..., 3] = val
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, size, size, 0, GL_RGBA, GL_UNSIGNED_BYTE, img)
            
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)

    def _paint_stroke(self, current_pos):
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer): return
        if not self.active_layer.visible or not self.current_brush: return
        if not self.last_pos: self.last_pos = current_pos
        self.makeCurrent(); glBindFramebuffer(GL_FRAMEBUFFER, self.active_layer.fbo); glViewport(0, 0, self.active_layer.width, self.active_layer.height)
        glMatrixMode(GL_PROJECTION); glLoadIdentity(); glOrtho(0, self.active_layer.width, self.active_layer.height, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW); glLoadIdentity()
        
        glEnable(GL_BLEND)
        glBlendEquation(GL_FUNC_ADD)
        
        if self.current_brush.blend_mode == "Eraser": 
            glBlendFunc(GL_ZERO, GL_ONE_MINUS_SRC_ALPHA)
            glColor4f(0, 0, 0, self.current_brush.opacity)
        else: 
            # Fix: Use Separate Blending for correct alpha accumulation
            # RGB: Standard Source Over (Premultiplied output from glColor)
            # Alpha: Standard accumulation (Src + Dst*(1-Src))
            glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
            
            alpha = self.current_brush.flow * self.current_brush.opacity
            
            # === Robust Color Parsing ===
            r, g, b = 0.0, 0.0, 0.0
            
            c = self.brush_color
            if isinstance(c, QColor):
                r, g, b = c.redF(), c.greenF(), c.blueF()
            elif isinstance(c, (list, tuple)) and len(c) >= 3:
                r, g, b = c[0], c[1], c[2]
                # Heuristic: If any component > 1.0, assume 0-255 range and normalize
                if r > 1.0 or g > 1.0 or b > 1.0:
                    r /= 255.0
                    g /= 255.0
                    b /= 255.0
            
            # Standard OpenGL Color
            glColor4f(r, g, b, alpha)

        dist = np.sqrt((current_pos.x() - self.last_pos.x())**2 + (current_pos.y() - self.last_pos.y())**2)
        step = max(1.0, self.current_brush.size * self.current_brush.spacing)
        steps = int(dist / step) + 1; dx = (current_pos.x() - self.last_pos.x()) / steps; dy = (current_pos.y() - self.last_pos.y()) / steps
        glEnable(GL_TEXTURE_2D); glBindTexture(GL_TEXTURE_2D, self.brush_texture_id)
        
        glBegin(GL_QUADS)
        for i in range(steps):
            cx = self.last_pos.x() + dx * i; cy = self.last_pos.y() + dy * i; hs = self.current_brush.size / 2
            glTexCoord2f(0,0); glVertex2f(cx-hs, cy-hs); glTexCoord2f(0,1); glVertex2f(cx-hs, cy+hs); glTexCoord2f(1,1); glVertex2f(cx+hs, cy+hs); glTexCoord2f(1,0); glVertex2f(cx+hs, cy-hs)
        glEnd(); glBindFramebuffer(GL_FRAMEBUFFER, 0); self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        zoom_factor = 1.1 if delta > 0 else 0.9
        old_zoom = self.zoom; new_zoom = max(0.1, min(old_zoom * zoom_factor, 50.0))
        mouse_pos = event.position(); world_pos = (mouse_pos - self.offset) / old_zoom
        self.zoom = new_zoom; self.offset = mouse_pos - world_pos * new_zoom
        self.update(); self.view_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.view_changed.emit()

class CanvasWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.gl_canvas = GLCanvas()
        self.v_bar = QScrollBar(Qt.Orientation.Vertical); self.h_bar = QScrollBar(Qt.Orientation.Horizontal)
        layout = QGridLayout(self); layout.setSpacing(0); layout.setContentsMargins(0,0,0,0)
        layout.addWidget(self.gl_canvas, 0, 0); layout.addWidget(self.v_bar, 0, 1); layout.addWidget(self.h_bar, 1, 0)
        layout.setColumnStretch(0, 1); layout.setRowStretch(0, 1)
        self.v_bar.valueChanged.connect(self.on_scroll); self.h_bar.valueChanged.connect(self.on_scroll)
        self.gl_canvas.view_changed.connect(self.update_scrollbars); self.update_scrollbars()

    # Proxies
    @property
    def brush_color_changed(self): return self.gl_canvas.brush_color_changed
    @property
    def layer_structure_changed(self): return self.gl_canvas.layer_structure_changed
    @property
    def doc_width(self): return self.gl_canvas.doc_width
    @doc_width.setter
    def doc_width(self, v): self.gl_canvas.doc_width = v
    @property
    def doc_height(self): return self.gl_canvas.doc_height
    @doc_height.setter
    def doc_height(self, v): self.gl_canvas.doc_height = v
    @property
    def root(self): return self.gl_canvas.root
    @property
    def active_layer(self): return self.gl_canvas.active_layer
    @active_layer.setter
    def active_layer(self, v): self.gl_canvas.active_layer = v
    @property
    def current_brush(self): return self.gl_canvas.current_brush
    @current_brush.setter
    def current_brush(self, v): self.gl_canvas.current_brush = v
    @property
    def brush_color(self): return self.gl_canvas.brush_color
    @brush_color.setter
    def brush_color(self, v): self.gl_canvas.brush_color = v
    def initializeGL(self): self.gl_canvas.initializeGL()
    def update(self): super().update(); self.gl_canvas.update()
    def import_psd(self, path): self.gl_canvas.import_psd(path)
    def save_project(self, path): self.gl_canvas.save_project(path)
    def export_image(self, path): self.gl_canvas.export_image(path)
    def set_brush(self, config): self.gl_canvas.set_brush(config)
    def resize_canvas_smart(self, w, h, anchor): self.gl_canvas.resize_canvas_smart(w, h, anchor)
    def load_project(self, path): self.gl_canvas.load_project(path)
    def set_tool(self, name): self.gl_canvas.set_tool(name)
    def make_current(self): self.gl_canvas.makeCurrent()
    def update_scrollbars(self):
        self.h_bar.blockSignals(True); self.v_bar.blockSignals(True)
        vw = self.gl_canvas.width(); vh = self.gl_canvas.height()
        cw = self.gl_canvas.doc_width * self.gl_canvas.zoom; ch = self.gl_canvas.doc_height * self.gl_canvas.zoom
        if cw > vw: self.h_bar.setRange(0, int(cw - vw)); self.h_bar.setPageStep(vw); self.h_bar.setValue(int(-self.gl_canvas.offset.x())); self.h_bar.show()
        else: self.h_bar.hide()
        if ch > vh: self.v_bar.setRange(0, int(ch - vh)); self.v_bar.setPageStep(vh); self.v_bar.setValue(int(-self.gl_canvas.offset.y())); self.v_bar.show()
        else: self.v_bar.hide()
        self.h_bar.blockSignals(False); self.v_bar.blockSignals(False)
    def on_scroll(self):
        if not self.h_bar.isHidden(): self.gl_canvas.offset.setX(float(-self.h_bar.value()))
        if not self.v_bar.isHidden(): self.gl_canvas.offset.setY(float(-self.v_bar.value()))
        self.gl_canvas.update()
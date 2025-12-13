# src/core/tools.py

from PyQt6.QtCore import Qt, QPoint, QRect, QPointF, QBuffer, QIODevice, QRectF
from PyQt6.QtGui import QPainter, QPen, QColor, QPainterPath, QCursor, QFont, QImage, QTransform, QPolygonF
from PyQt6.QtWidgets import QMenu, QInputDialog, QDialog, QVBoxLayout, QSlider, QLabel, QDialogButtonBox, QApplication, QMessageBox
from src.core.logic import PaintLayer, TextLayer, PaintCommand, GroupLayer
from src.core.processor import ImageProcessor
from PIL import Image, ImageDraw
import numpy as np
from OpenGL.GL import glReadPixels, GL_RGB, GL_FLOAT
import io
import math

# === Clipboard Utils ===
class ClipboardUtils:
    @staticmethod
    def get_selection_mask(canvas):
        if not hasattr(canvas, 'selection_path') or canvas.selection_path.isEmpty():
            return None
        
        w, h = canvas.doc_width, canvas.doc_height
        qimg = QImage(w, h, QImage.Format.Format_Grayscale8)
        qimg.fill(0)
        
        painter = QPainter(qimg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawPath(canvas.selection_path)
        painter.end()
        
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.ReadWrite)
        qimg.save(buffer, "PNG")
        try:
            mask = Image.open(io.BytesIO(bytes(buffer.data()))).convert("L")
            return mask
        except: return None

    @staticmethod
    def copy(canvas):
        if not canvas.active_layer: return False
        try:
            img = canvas.active_layer.get_image()
        except: return False
        
        mask = ClipboardUtils.get_selection_mask(canvas)
        if mask:
            res = Image.new("RGBA", img.size, (0,0,0,0))
            res.paste(img, (0,0), mask)
            bbox = mask.getbbox()
            if bbox: res = res.crop(bbox)
            img = res
            
        try:
            if img.mode != "RGBA": img = img.convert("RGBA")
            bio = io.BytesIO()
            img.save(bio, "PNG")
            qimg = QImage.fromData(bio.getvalue())
            QApplication.clipboard().setImage(qimg)
            return True
        except: return False

    @staticmethod
    def cut(canvas):
        # Relaxed check for PaintLayer to allow cutting from active paint layer
        if not canvas.active_layer or canvas.active_layer.__class__.__name__ == 'GroupLayer':
            return ClipboardUtils.copy(canvas)
            
        ClipboardUtils.copy(canvas)
        mask = ClipboardUtils.get_selection_mask(canvas)
        old_img = canvas.active_layer.get_image()
        
        if mask:
            transparent = Image.new("RGBA", old_img.size, (0,0,0,0))
            new_img = Image.composite(transparent, old_img, mask)
        else:
            new_img = Image.new("RGBA", old_img.size, (0,0,0,0))
        
        cmd = PaintCommand(canvas.active_layer, old_img, new_img)
        canvas.undo_stack.push(cmd)
        canvas.active_layer.load_from_image(new_img)
        
        if hasattr(canvas, 'selection_path'):
            canvas.selection_path = QPainterPath()
        canvas.update()
        return True

    @staticmethod
    def paste(canvas):
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        if not mime_data.hasImage(): return
        
        qimg = clipboard.image()
        if qimg.isNull(): return
        
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.ReadWrite)
        qimg.save(buffer, "PNG")
        pil_img = Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")
        width, height = pil_img.size
        
        cursor_pos = canvas.mapFromGlobal(QCursor.pos())
        
        if not canvas.rect().contains(cursor_pos):
            QMessageBox.warning(canvas, "Paste Error", "Cannot paste outside the canvas area.\nPlease move mouse over canvas.")
            return

        paste_x = int((cursor_pos.x() - canvas.offset.x()) / canvas.zoom - width / 2)
        paste_y = int((cursor_pos.y() - canvas.offset.y()) / canvas.zoom - height / 2)

        if canvas.active_layer and canvas.active_layer.__class__.__name__ == 'PaintLayer':
            target_layer = canvas.active_layer
            old_img = target_layer.get_image()
            new_img = old_img.copy()
            new_img.paste(pil_img, (paste_x, paste_y), pil_img)
            
            cmd = PaintCommand(target_layer, old_img, new_img)
            canvas.undo_stack.push(cmd)
            target_layer.load_from_image(new_img)
        else:
            new_layer = PaintLayer(canvas.doc_width, canvas.doc_height, name="Pasted Layer")
            full_img = Image.new("RGBA", (canvas.doc_width, canvas.doc_height), (0,0,0,0))
            full_img.paste(pil_img, (paste_x, paste_y))
            new_layer.load_from_image(full_img)
            
            target_parent = canvas.root
            if canvas.active_layer:
                p = canvas.active_layer
                while p.parent and p.parent.__class__.__name__ != 'GroupLayer' and p.parent != canvas.root:
                    p = p.parent
                target_parent = p.parent if p.parent else canvas.root
            target_parent.add_child(new_layer)
            canvas.active_layer = new_layer
            canvas.layer_structure_changed.emit()

        canvas.update()

# === Base Tool ===
class Tool:
    def __init__(self, canvas):
        self.canvas = canvas
        self.cursor = Qt.CursorShape.ArrowCursor
        if not hasattr(self.canvas, 'selection_path'):
            self.canvas.selection_path = QPainterPath()

    def mouse_press(self, event, pos, layer_pos): pass
    def mouse_move(self, event, pos, layer_pos): pass
    def mouse_release(self, event, pos, layer_pos): pass
    
    def draw_overlay(self, painter):
        if hasattr(self.canvas, 'selection_path') and not self.canvas.selection_path.isEmpty():
            self._draw_selection_border(painter)

    def _draw_selection_border(self, painter):
        pen = QPen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self.canvas.selection_path)
        pen.setColor(Qt.GlobalColor.black)
        pen.setDashOffset(5)
        painter.setPen(pen)
        painter.drawPath(self.canvas.selection_path)

    def activate(self): self.canvas.setCursor(self.cursor)
    def deactivate(self): pass
    
    @property
    def selection_path(self): return self.canvas.selection_path
    @selection_path.setter
    def selection_path(self, path): self.canvas.selection_path = path

# === Selection Tool (Multi-Layer Support) ===
class SelectionTool(Tool):
    STATE_IDLE = 0
    STATE_CREATING = 1
    STATE_TRANSFORMING = 2

    def __init__(self, canvas):
        super().__init__(canvas)
        self.cursor = Qt.CursorShape.CrossCursor
        self.state = self.STATE_IDLE
        self.transform_mode = None
        self.start_mouse_pos = None
        self.handle_size = 14
        
        # Floating items list: each is {'layer', 'qimg', 'snapshot'}
        self.floating_items = []
        
        self.tf_pos = QPointF(0,0)
        self.tf_rotation = 0.0
        self.tf_scale = QPointF(1.0, 1.0)
        self.base_path = QPainterPath() 

        self.cache_tf_pos = QPointF(0,0)
        self.cache_tf_scale = QPointF(1,1)
        self.cache_tf_rot = 0.0

    def has_selection(self):
        return not self.selection_path.isEmpty()
    
    def clear_selection(self):
        self.commit_transform()
        self.selection_path = QPainterPath()
        self.state = self.STATE_IDLE
        self.canvas.update()

    def _collect_layers(self, node, layers):
        """Recursively collect PaintLayers from a Group using name check for robustness"""
        cname = node.__class__.__name__
        if cname == 'GroupLayer':
            for child in node.children:
                self._collect_layers(child, layers)
        elif cname == 'PaintLayer' or cname == 'TextLayer':
            layers.append(node)

    def _lift_selection(self):
        if self.floating_items: return
        if not self.canvas.active_layer: return
        
        # Ensure context for GL operations
        self.canvas.makeCurrent()
        
        mask = ClipboardUtils.get_selection_mask(self.canvas)
        
        bbox = self.selection_path.boundingRect()
        
        self.tf_pos = bbox.topLeft()
        self.tf_rotation = 0.0
        self.tf_scale = QPointF(1.0, 1.0)
        
        t = QTransform()
        t.translate(-bbox.left(), -bbox.top())
        self.base_path = t.map(self.selection_path)
        
        targets = []
        # Use name check to avoid instance mismatch across modules
        if self.canvas.active_layer.__class__.__name__ == 'GroupLayer':
            self._collect_layers(self.canvas.active_layer, targets)
        else:
            targets.append(self.canvas.active_layer)
            
        if not targets or not mask: return

        crop = (int(bbox.left()), int(bbox.top()), int(bbox.right()), int(bbox.bottom()))
        self.floating_items = []
        
        for layer in targets:
            try:
                img = layer.get_image()
                snapshot = img.copy() 
                
                temp = Image.new("RGBA", img.size, (0,0,0,0))
                temp.paste(img, (0,0), mask)
                floating = temp.crop(crop)
                
                if floating.getbbox() is None: continue

                bio = io.BytesIO()
                floating.save(bio, "PNG")
                qimg = QImage.fromData(bio.getvalue())
                
                clear_temp = Image.new("RGBA", img.size, (0,0,0,0))
                cleared = Image.composite(clear_temp, img, mask)
                layer.load_from_image(cleared)
                
                self.floating_items.append({
                    'layer': layer,
                    'qimg': qimg,
                    'snapshot': snapshot
                })
            except Exception as e:
                print(f"Error lifting layer {layer.name}: {e}")

    def commit_transform(self):
        if not self.floating_items: 
            self.tf_rotation = 0.0
            self.tf_scale = QPointF(1.0, 1.0)
            return
        
        w, h = self.canvas.doc_width, self.canvas.doc_height
        
        t = QTransform()
        t.translate(self.tf_pos.x(), self.tf_pos.y())
        
        bbox = self.base_path.boundingRect()
        cx = bbox.center().x()
        cy = bbox.center().y()
        
        t.translate(cx, cy)
        t.rotate(self.tf_rotation)
        t.scale(self.tf_scale.x(), self.tf_scale.y())
        t.translate(-cx, -cy)
        
        for item in self.floating_items:
            res = QImage(w, h, QImage.Format.Format_RGBA8888)
            res.fill(0)
            
            p = QPainter(res)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.setTransform(t)
            p.drawImage(0, 0, item['qimg'])
            p.end()
            
            ptr = res.bits()
            ptr.setsize(res.sizeInBytes())
            pasted = Image.frombuffer("RGBA", (w, h), ptr, "raw", "RGBA", 0, 1).copy()
            
            curr = item['layer'].get_image()
            curr.alpha_composite(pasted)
            
            cmd = PaintCommand(item['layer'], item['snapshot'], curr)
            self.canvas.undo_stack.push(cmd)
            
            item['layer'].load_from_image(curr)
        
        self.floating_items = []
        self.canvas.update()

    def _get_current_transform(self):
        bbox = self.base_path.boundingRect()
        cx = bbox.center().x()
        cy = bbox.center().y()
        
        t = QTransform()
        t.translate(self.tf_pos.x(), self.tf_pos.y())
        t.translate(cx, cy)
        t.rotate(self.tf_rotation)
        t.scale(self.tf_scale.x(), self.tf_scale.y())
        t.translate(-cx, -cy)
        return t

    def _get_handles(self):
        if not self.has_selection(): return {}
        if self.state == self.STATE_CREATING: return {}
        
        t = self._get_current_transform()
        path = t.map(self.base_path) if self.floating_items or self.transform_mode else self.selection_path
            
        bbox = path.boundingRect()
        s = self.handle_size / self.canvas.zoom
        
        tl = bbox.topLeft(); tr = bbox.topRight()
        bl = bbox.bottomLeft(); br = bbox.bottomRight()
        rot = QPointF(bbox.center().x(), bbox.top() - 30/self.canvas.zoom)
        
        def r(pt): return QRectF(pt.x()-s/2, pt.y()-s/2, s, s)
        
        return {'tl': r(tl), 'tr': r(tr), 'bl': r(bl), 'br': r(br), 'rot': r(rot), 'move': bbox}

    def _hit_test(self, pos):
        if not self.has_selection(): return None
        handles = self._get_handles()
        if not handles: return None
        
        for k in ['tl','tr','bl','br','rot']:
            if handles.get(k) and handles[k].contains(pos): return k
        
        if handles.get('move') and handles['move'].contains(pos): return 'move'
        return None

    def start_creating(self, pos):
        self.commit_transform()
        self.selection_path = QPainterPath()
        self.state = self.STATE_CREATING
        self.start_mouse_pos = pos

    def update_creating(self, pos):
        raise NotImplementedError

    def finish_creating(self):
        if self.selection_path.isEmpty(): self.state = self.STATE_IDLE
        else: self.state = self.STATE_IDLE

    # --- Interaction ---
    def mouse_press(self, event, pos, layer_pos):
        if event.button() == Qt.MouseButton.RightButton:
            self.show_context_menu(event.globalPosition().toPoint())
            return True

        if event.button() == Qt.MouseButton.LeftButton:
            if self.has_selection():
                hit = self._hit_test(layer_pos)
                if hit:
                    self._lift_selection()
                    # Check floating items only if we had targets to lift
                    # If empty targets (empty group), just transform box
                    self.state = self.STATE_TRANSFORMING
                    self.transform_mode = hit
                    self.start_mouse_pos = layer_pos
                    self.cache_tf_pos = QPointF(self.tf_pos)
                    self.cache_tf_scale = QPointF(self.tf_scale)
                    self.cache_tf_rot = self.tf_rotation
                    return True
                
                self.clear_selection()
            
            self.start_creating(layer_pos)
            return True
        return False

    def mouse_move(self, event, pos, layer_pos):
        if self.state == self.STATE_IDLE and self.has_selection():
            hit = self._hit_test(layer_pos)
            if hit == 'move': self.canvas.setCursor(Qt.CursorShape.OpenHandCursor)
            elif hit == 'rot': self.canvas.setCursor(Qt.CursorShape.PointingHandCursor)
            elif hit: self.canvas.setCursor(Qt.CursorShape.SizeFDiagCursor)
            else: self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        elif self.state == self.STATE_CREATING:
            self.update_creating(layer_pos)
        elif self.state == self.STATE_TRANSFORMING:
            self.update_transform(layer_pos)

    def mouse_release(self, event, pos, layer_pos):
        if self.state == self.STATE_CREATING:
            self.finish_creating()
        elif self.state == self.STATE_TRANSFORMING:
            self.state = self.STATE_IDLE
            self.transform_mode = None

    def update_transform(self, pos):
        delta = pos - self.start_mouse_pos
        
        if self.transform_mode == 'move':
            self.tf_pos = self.cache_tf_pos + delta
            
        elif self.transform_mode == 'rot':
            bbox = self.base_path.boundingRect()
            center = self.cache_tf_pos + QPointF(bbox.width()/2, bbox.height()/2)
            
            vec_s = self.start_mouse_pos - center
            vec_c = pos - center
            angle = math.degrees(math.atan2(vec_c.y(), vec_c.x()) - math.atan2(vec_s.y(), vec_s.x()))
            self.tf_rotation = self.cache_tf_rot + angle
            
        elif self.transform_mode in ['tl','tr','bl','br']:
            center = self.selection_path.boundingRect().center()
            dist_s = (self.start_mouse_pos - center).manhattanLength() or 1
            dist_c = (pos - center).manhattanLength()
            ratio = dist_c / dist_s
            self.tf_scale = self.cache_tf_scale * ratio

        t = self._get_current_transform()
        self.selection_path = t.map(self.base_path)
        self.canvas.update()

    def draw_overlay(self, painter):
        if self.floating_items:
            painter.save()
            painter.setTransform(self._get_current_transform(), combine=True)
            for item in self.floating_items:
                painter.drawImage(0, 0, item['qimg'])
            painter.restore()
            
        if self.has_selection():
            self._draw_selection_border(painter)
            if self.state == self.STATE_IDLE:
                handles = self._get_handles()
                for k, r in handles.items():
                    if k == 'move': continue
                    if k == 'rot':
                        painter.setBrush(QColor(100, 200, 255))
                        painter.drawEllipse(r)
                    else:
                        painter.setBrush(QColor(255, 255, 255))
                        painter.drawRect(r)

    def deactivate(self):
        self.commit_transform()
        self.state = self.STATE_IDLE
        self.canvas.update()

    def show_context_menu(self, global_pos):
        menu = QMenu(self.canvas)
        menu.addAction("Cut", lambda: ClipboardUtils.cut(self.canvas)).setEnabled(self.has_selection())
        menu.addAction("Copy", lambda: ClipboardUtils.copy(self.canvas)).setEnabled(self.has_selection())
        menu.addAction("Paste", lambda: ClipboardUtils.paste(self.canvas))
        menu.addSeparator()
        menu.addAction("Rotate Left 90", lambda: self._menu_tf(-90)).setEnabled(self.has_selection())
        menu.addAction("Rotate Right 90", lambda: self._menu_tf(90)).setEnabled(self.has_selection())
        menu.exec(global_pos)

    def _menu_tf(self, angle):
        self._lift_selection()
        # Allows rotating empty box
        self.tf_rotation += angle
        t = self._get_current_transform()
        self.selection_path = t.map(self.base_path)
        self.commit_transform()

# === Concrete Selection Tools ===
class RectSelectTool(SelectionTool):
    def update_creating(self, pos):
        path = QPainterPath()
        path.addRect(QRectF(self.start_mouse_pos, pos).normalized())
        self.selection_path = path
        self.canvas.update()

class LassoTool(SelectionTool):
    def __init__(self, canvas):
        super().__init__(canvas)
        self.points = []
    
    def start_creating(self, pos):
        super().start_creating(pos)
        self.points = [pos]
        
    def update_creating(self, pos):
        self.points.append(pos)
        path = QPainterPath()
        if self.points:
            path.moveTo(self.points[0])
            for p in self.points[1:]: path.lineTo(p)
        self.selection_path = path
        self.canvas.update()
        
    def finish_creating(self):
        if len(self.points) > 2: self.selection_path.closeSubpath()
        super().finish_creating()
        self.points = []

# === Bucket ===
class BucketTool(Tool):
    def __init__(self, canvas):
        super().__init__(canvas)
        self.cursor = Qt.CursorShape.PointingHandCursor
    def mouse_press(self, event, pos, layer_pos):
        if event.button() == Qt.MouseButton.LeftButton and self.canvas.active_layer:
            # Use name check for robustness
            if self.canvas.active_layer.__class__.__name__ == 'GroupLayer': return
            target = self.canvas.active_layer
            old = target.get_image()
            color = tuple([int(c*255) for c in self.canvas.brush_color] + [255])
            fill = Image.new("RGBA", (target.width, target.height), color)
            mask = ClipboardUtils.get_selection_mask(self.canvas)
            final = Image.composite(fill, old, mask) if mask else fill
            self.canvas.undo_stack.push(PaintCommand(target, old, final))
            target.load_from_image(final)
            self.canvas.update()

# === Picker (Fixed) ===
class PickerTool(Tool):
    def __init__(self, canvas):
        super().__init__(canvas)
        self.cursor = Qt.CursorShape.CrossCursor
        self.picked_color = None
    def mouse_press(self, event, pos, layer_pos):
        if event.button() == Qt.MouseButton.LeftButton: self.pick(event.position())
    def mouse_move(self, event, pos, layer_pos):
        self.pick(event.position(), False); self.canvas.update()
    def pick(self, wpos, apply=True):
        dpr = self.canvas.devicePixelRatio()
        gx, gy = wpos.x()*dpr, (self.canvas.height()-wpos.y())*dpr
        self.canvas.makeCurrent()
        try:
            d = glReadPixels(int(gx), int(gy), 1, 1, GL_RGB, GL_FLOAT)
            if isinstance(d, np.ndarray):
                vals = d.flatten()
                r, g, b = vals[0], vals[1], vals[2]
            else:
                r, g, b = d[0][0], d[0][1], d[0][2]
            self.picked_color = (r, g, b)
            if apply: self.canvas.brush_color = [r, g, b]
        except Exception as e:
            print(f"Picker Error: {e}")

    def draw_overlay(self, p):
        if self.picked_color:
            w, h = self.canvas.width(), self.canvas.height()
            r = QRect(w-80, h-80, 60, 60)
            p.resetTransform()
            c = QColor.fromRgbF(*self.picked_color)
            pen_white = QPen(Qt.GlobalColor.white, 3)
            p.setPen(pen_white)
            p.setBrush(c)
            p.drawEllipse(r)
            pen_black = QPen(Qt.GlobalColor.black, 1)
            p.setPen(pen_black)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r)
            p.setPen(Qt.GlobalColor.white)
            p.setFont(QFont("Arial", 8))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"{int(c.red()*255)},{int(c.green()*255)},{int(c.blue()*255)}")

# === Text Tool (Fixed) ===
class TextTool(Tool):
    def __init__(self, canvas):
        super().__init__(canvas); self.cursor = Qt.CursorShape.IBeamCursor
    def mouse_press(self, event, pos, layer_pos):
        if event.button() == Qt.MouseButton.LeftButton:
            t, ok = QInputDialog.getText(self.canvas, "Text", "Content:")
            if ok and t:
                l = TextLayer(self.canvas.doc_width, self.canvas.doc_height, text=t, x=int(layer_pos.x()), y=int(layer_pos.y()))
                tgt = self.canvas.active_layer.parent if self.canvas.active_layer and self.canvas.active_layer.parent else self.canvas.root
                tgt.add_child(l); self.canvas.active_layer = l; self.canvas.layer_structure_changed.emit(); self.canvas.update()
        elif event.button() == Qt.MouseButton.RightButton and isinstance(self.canvas.active_layer, TextLayer):
            s, ok = QInputDialog.getInt(self.canvas, "Size", "Size:", value=self.canvas.active_layer.font_size)
            if ok: 
                self.canvas.active_layer.font_size = s
                self.canvas.active_layer.update_texture()
                self.canvas.update()

# === Smudge Tool ===
class SmudgeTool(Tool):
    def __init__(self, canvas):
        super().__init__(canvas)
        self.cursor = Qt.CursorShape.PointingHandCursor
        self.is_smudging = False
        self.last_pos = None
        self.smudge_strength = 0.5

    def mouse_press(self, event, pos, layer_pos):
        if event.button() == Qt.MouseButton.LeftButton and self.canvas.active_layer and self.canvas.active_layer.__class__.__name__ == 'PaintLayer':
            self.is_smudging = True
            self.last_pos = layer_pos

    def mouse_move(self, event, pos, layer_pos):
        if self.is_smudging and self.last_pos:
            self._apply_smudge(self.last_pos, layer_pos)
            self.last_pos = layer_pos

    def mouse_release(self, event, pos, layer_pos):
        self.is_smudging = False
        self.last_pos = None

    def _apply_smudge(self, p1, p2):
        radius = self.canvas.current_brush.size
        x_min = int(min(p1.x(), p2.x()) - radius)
        y_min = int(min(p1.y(), p2.y()) - radius)
        x_max = int(max(p1.x(), p2.x()) + radius)
        y_max = int(max(p1.y(), p2.y()) + radius)
        w, h = self.canvas.doc_width, self.canvas.doc_height
        
        layer_img = self.canvas.active_layer.get_image()
        src_box = (int(p1.x()-radius), int(p1.y()-radius), int(p1.x()+radius), int(p1.y()+radius))
        try: src_patch = layer_img.crop(src_box)
        except: return

        src_patch = src_patch.filter(ImageFilter.GaussianBlur(1))
        
        mask = Image.new("L", src_patch.size, 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0,0, src_patch.width, src_patch.height), fill=int(255 * self.smudge_strength))
        
        dest_x = int(p2.x() - radius)
        dest_y = int(p2.y() - radius)
        
        layer_img.paste(src_patch, (dest_x, dest_y), mask)
        self.canvas.active_layer.load_from_image(layer_img)
        self.canvas.update()

# === Adjustment Dialog ===
class AdjustmentDialog(QDialog):
    def __init__(self, parent, title, func, params):
        super().__init__(parent); self.setWindowTitle(title); self.func = func; self.params = params; self.sliders = []
        self.preview_layer = parent.active_layer; self.original_img = self.preview_layer.get_image()
        l = QVBoxLayout(self)
        for p in params:
            l.addWidget(QLabel(p["name"])); s = QSlider(Qt.Orientation.Horizontal); s.setRange(p["min"], p["max"]); s.setValue(p["default"])
            s.valueChanged.connect(self.upd); l.addWidget(s); self.sliders.append({"w": s, "s": p["scale"]})
        b = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        b.accepted.connect(self.accept); b.rejected.connect(self.reject); l.addWidget(b)
    def upd(self):
        args = [s["w"].value()*s["s"] for s in self.sliders]
        self.preview_layer.load_from_image(self.func(self.original_img, *args)); self.parent().update()
    def reject(self): self.preview_layer.load_from_image(self.original_img); self.parent().update(); super().reject()
# src/core/tools.py

from PyQt6.QtCore import Qt, QPoint, QRect, QPointF, QBuffer, QIODevice, QRectF
from PyQt6.QtGui import QPainter, QPen, QColor, QPainterPath, QCursor, QFont, QImage, QTransform, QPolygonF, QBrush
from PyQt6.QtWidgets import QMenu, QInputDialog, QDialog, QVBoxLayout, QSlider, QLabel, QDialogButtonBox, QApplication, QMessageBox
from src.core.logic import PaintLayer, TextLayer, PaintCommand, GroupLayer
from src.core.processor import ImageProcessor
from PIL import Image, ImageDraw, ImageFilter  # Added ImageFilter
import numpy as np
from OpenGL.GL import glReadPixels, GL_RGB, GL_FLOAT
import io
import os
import math

# === Clipboard Utils ===
class ClipboardUtils:
    @staticmethod
    def get_selection_mask(canvas):
        if not hasattr(canvas, 'selection_path') or canvas.selection_path.isEmpty():
            return None
        
        # If a feathered gradient mask is stored, use it directly
        if hasattr(canvas, 'selection_feather_mask') and canvas.selection_feather_mask is not None:
            mask_arr = canvas.selection_feather_mask
            return Image.fromarray(mask_arr, mode="L")

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
            # Alpha-weighted extraction for feathered masks
            mask_arr = np.array(mask, dtype=np.float32) / 255.0
            img_arr = np.array(img, dtype=np.float32)
            res_arr = img_arr.copy()
            res_arr[..., 3] *= mask_arr
            res = Image.fromarray(res_arr.clip(0, 255).astype(np.uint8), "RGBA")
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
            # Use alpha-weighted removal so feathered masks blend correctly
            mask_arr = np.array(mask, dtype=np.float32) / 255.0
            img_arr = np.array(old_img, dtype=np.float32)
            img_arr[..., 3] *= (1.0 - mask_arr)
            new_img = Image.fromarray(img_arr.clip(0, 255).astype(np.uint8), "RGBA")
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
        """Base tool: do NOT draw selection border/handles.
        Only SelectionTool subclasses should draw selection visuals."""
        pass

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
        if hasattr(self.canvas, 'selection_feather_mask'):
            self.canvas.selection_feather_mask = None
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
        
        # Convert mask to numpy float for precise alpha-weighted separation
        mask_arr = np.array(mask, dtype=np.float32) / 255.0  # 0.0 ~ 1.0
        
        for layer in targets:
            try:
                img = layer.get_image()  # PIL RGBA
                snapshot = img.copy()
                
                img_arr = np.array(img, dtype=np.float32)  # H x W x 4
                
                # Floating = original * mask_alpha (the lifted portion)
                float_arr = img_arr.copy()
                float_arr[..., 3] *= mask_arr  # modulate alpha by mask
                
                # Remaining = original * (1 - mask_alpha) (stays on the layer)
                remain_arr = img_arr.copy()
                remain_arr[..., 3] *= (1.0 - mask_arr)
                
                floating_img = Image.fromarray(float_arr.clip(0, 255).astype(np.uint8), "RGBA")
                remaining_img = Image.fromarray(remain_arr.clip(0, 255).astype(np.uint8), "RGBA")
                
                floating = floating_img.crop(crop)
                
                if floating.getbbox() is None: continue

                bio = io.BytesIO()
                floating.save(bio, "PNG")
                qimg = QImage.fromData(bio.getvalue())
                
                layer.load_from_image(remaining_img)
                
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
        
        s = self.handle_size / self.canvas.zoom

        # When we have a floating transform, compute the actual rotated corners
        if self.floating_items or self.transform_mode:
            t = self._get_current_transform()
            bbox = self.base_path.boundingRect()
            # Map the original (unrotated) corners through the transform
            tl = t.map(bbox.topLeft())
            tr = t.map(bbox.topRight())
            bl = t.map(bbox.bottomLeft())
            br = t.map(bbox.bottomRight())
            center_top = t.map(QPointF(bbox.center().x(), bbox.top()))
            rot_pt = center_top + (center_top - t.map(QPointF(bbox.center().x(), bbox.center().y())))
            # Normalise rotation handle distance
            diff = center_top - t.map(bbox.center())
            length = math.sqrt(diff.x()**2 + diff.y()**2)
            if length > 0:
                rot_pt = center_top + diff * (30 / self.canvas.zoom / length)
            else:
                rot_pt = QPointF(center_top.x(), center_top.y() - 30/self.canvas.zoom)
        else:
            # No transform yet – use the selection_path's bounding rect
            bbox = self.selection_path.boundingRect()
            tl = bbox.topLeft()
            tr = bbox.topRight()
            bl = bbox.bottomLeft()
            br = bbox.bottomRight()
            rot_pt = QPointF(bbox.center().x(), bbox.top() - 30/self.canvas.zoom)

        def r(pt): return QRectF(pt.x()-s/2, pt.y()-s/2, s, s)
        
        return {'tl': r(tl), 'tr': r(tr), 'bl': r(bl), 'br': r(br), 'rot': r(rot_pt), 'move': self.selection_path.boundingRect()}

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
        if hasattr(self.canvas, 'selection_feather_mask'):
            self.canvas.selection_feather_mask = None
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
            if hit == 'move':
                self.canvas.setCursor(Qt.CursorShape.OpenHandCursor)
            elif hit == 'rot':
                self.canvas.setCursor(Qt.CursorShape.PointingHandCursor)
            elif hit in ('tl', 'br'):
                self.canvas.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif hit in ('tr', 'bl'):
                self.canvas.setCursor(Qt.CursorShape.SizeBDiagCursor)
            else:
                self.canvas.setCursor(Qt.CursorShape.CrossCursor)
        elif self.state == self.STATE_CREATING:
            self.update_creating(layer_pos)
        elif self.state == self.STATE_TRANSFORMING:
            self.update_transform(layer_pos)

    def mouse_release(self, event, pos, layer_pos):
        if self.state == self.STATE_CREATING:
            self.finish_creating()
            self.canvas.update()  # repaint to show handles immediately
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
            # Always show handles when a selection exists (except while creating)
            if self.state != self.STATE_CREATING:
                handles = self._get_handles()
                handle_pen = QPen(QColor(0, 0, 0), max(1.0, 1.0 / self.canvas.zoom))
                handle_pen.setCosmetic(False)
                for k, r in handles.items():
                    if k == 'move': continue
                    painter.setPen(handle_pen)
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

        # Fixed: ImageFilter was missing. Now imported.
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


# === AI Magic Wand Tool (MobileSAM) ===
class MagicWandTool(Tool):
    """
    AI Magic Wand tool – powered by MobileSAM.
    Generates a selection mask in real-time via multi-point input.
    
    Interaction:
      - Left click : add positive point (include region)
      - Right click: add negative point (exclude region)
      - Ctrl+Z     : undo last point
      - Enter      : apply current mask as selection
      - Escape     : cancel
    """

    def __init__(self, canvas):
        super().__init__(canvas)
        self.cursor = Qt.CursorShape.CrossCursor

        # Point data – stored in layer pixel coordinates
        self._points = []       # [[x, y], ...]
        self._labels = []       # [1, 0, ...]  1=positive 0=negative
        self._point_mode = 1    # Current default mode: 1=positive

        # Feather radius (set by the panel's slider)
        self.feather = 0

        # Mask
        self._current_mask = None   # numpy H×W uint8 (0 or 255)

        # Inference
        self._inference_thread = None
        self._is_inferring = False

        # Temp image file (passed to MobileSAM)
        self._temp_image_path = None

        # Service reference
        from src.agent.mobile_sam_service import MobileSAMService
        self._sam = MobileSAMService.instance()

        # Status callback (set by the panel)
        self._status_callback = None

    # ── Lifecycle ────────────────────────────────────

    def activate(self):
        super().activate()
        self._clear_all()

        # Ensure model is loaded
        if not self._sam.is_loaded:
            self._sam.model_loading_msg.connect(self._on_model_msg)
            self._sam.model_load_finished.connect(self._on_model_loaded)
            self._sam.load_model_async()

    def deactivate(self):
        self._cleanup_temp()
        self._disconnect_sam_signals()
        super().deactivate()

    def _disconnect_sam_signals(self):
        try: self._sam.model_loading_msg.disconnect(self._on_model_msg)
        except TypeError: pass
        try: self._sam.model_load_finished.disconnect(self._on_model_loaded)
        except TypeError: pass

    # ── Model Load Callbacks ────────────────────────────

    def _on_model_msg(self, msg):
        if self._status_callback:
            self._status_callback(msg)

    def _on_model_loaded(self, success, msg):
        if self._status_callback:
            self._status_callback(msg if success else f"⚠️ {msg}")

    # ── Mouse Events ───────────────────────────────────

    def mouse_press(self, event, pos, layer_pos):
        if not self._sam.is_loaded or self._is_inferring:
            return

        layer = self.canvas.active_layer
        if not layer or layer.__class__.__name__ == 'GroupLayer':
            return

        # layer_pos is already in canvas (= layer) coordinates (no layer offset in this project)
        lx, ly = int(layer_pos.x()), int(layer_pos.y())

        # Bounds check
        if lx < 0 or ly < 0 or lx >= layer.width or ly >= layer.height:
            return

        # Left click → current mode; Right click → always negative
        if event.button() == Qt.MouseButton.LeftButton:
            label = self._point_mode
        elif event.button() == Qt.MouseButton.RightButton:
            label = 0
        else:
            return

        self._points.append([lx, ly])
        self._labels.append(label)

        # Ensure temp image exists
        self._ensure_temp_image(layer)

        # Update panel info
        self._notify_panel()

        # Run inference
        self._run_inference()

        self.canvas.update()

    def mouse_move(self, event, pos, layer_pos):
        pass  # Not needed for now

    def mouse_release(self, event, pos, layer_pos):
        pass

    # ── Keyboard Events (forwarded by GLCanvas.keyPressEvent) ──

    # GLCanvas does not forward keyPress to tools generically, so
    # keyboard shortcuts (Enter, Ctrl+Z) are handled directly in
    # canvas.keyPressEvent via isinstance check. Escape is handled
    # natively by the canvas (calls tool.deactivate).

    # ── Inference ────────────────────────────────────────

    def _ensure_temp_image(self, layer):
        """Export the active layer's current pixels to a temp PNG.
        Recreates when starting a new point session (first point) or when
        the cached file is missing, to ensure it reflects the latest layer state.
        """
        # Reuse existing temp if we already have points in this session
        if self._temp_image_path and os.path.exists(self._temp_image_path) and len(self._points) > 1:
            return

        # Clean up previous temp file if any
        self._cleanup_temp()

        self.canvas.makeCurrent()
        pil_img = layer.get_image()  # PIL RGBA – reads current FBO state

        # Flatten to RGB on white background for better SAM results
        if pil_img.mode == 'RGBA':
            bg = Image.new("RGB", pil_img.size, (255, 255, 255))
            bg.paste(pil_img, mask=pil_img.split()[3])
            pil_img = bg

        from src.agent.mobile_sam_service import MobileSAMService
        self._temp_image_path = MobileSAMService.pil_to_temp_png(pil_img)

    def _cleanup_temp(self):
        if self._temp_image_path and os.path.exists(self._temp_image_path):
            try: os.remove(self._temp_image_path)
            except: pass
        self._temp_image_path = None

    def _run_inference(self):
        if not self._points or self._is_inferring or not self._temp_image_path:
            return

        self._is_inferring = True
        if self._status_callback:
            self._status_callback("Generating mask...")

        thread = self._sam.create_inference_thread(
            self._temp_image_path,
            self._points,
            self._labels
        )
        if thread is None:
            self._is_inferring = False
            return

        self._inference_thread = thread
        thread.result_ready.connect(self._on_inference_done)
        thread.error_occurred.connect(self._on_inference_error)
        thread.start()

    def _on_inference_done(self, mask):
        self._is_inferring = False
        if mask is not None:
            self._current_mask = mask
        if self._status_callback:
            self._status_callback("Mask updated — Enter to apply / Esc to cancel")
        self.canvas.update()

    def _on_inference_error(self, msg):
        self._is_inferring = False
        if self._status_callback:
            self._status_callback(f"⚠️ Inference failed: {msg}")

    # ── Public Operations (called by the panel) ────────

    def set_point_mode(self, mode):
        """Switch point mode: 1=positive, 0=negative."""
        self._point_mode = mode

    def undo_last_point(self):
        """Undo the last point."""
        if not self._points:
            return
        self._points.pop()
        self._labels.pop()
        self._notify_panel()
        if self._points:
            self._run_inference()
        else:
            self._current_mask = None
            self.canvas.update()

    def clear_all_points(self):
        """Clear all points."""
        self._clear_all()
        self.canvas.update()

    def apply_as_selection(self, feather=0):
        """Convert the current mask to a selection path and apply it.
        
        Args:
            feather: Blur/erode radius. Positive = expand/soften edges,
                     negative = shrink/erode edges. 0 = sharp.
        """
        if self._current_mask is None:
            return

        import cv2

        # Use the feather parameter (from panel) or fall back to self.feather
        old_feather = self.feather
        self.feather = feather
        mask = self._get_feathered_mask()
        self.feather = old_feather

        if mask is None or not (mask > 1).any():
            return

        # Use a low threshold so the contour captures the full feathered extent.
        # Any pixel with value > 1 is considered part of the selection boundary.
        _, binary = cv2.threshold(mask, 1, 255, cv2.THRESH_BINARY)

        # Find contours → build a smooth QPainterPath (no ugly QRegion rectangles)
        contours, hierarchy = cv2.findContours(
            binary, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
        )

        if not contours:
            return

        path = QPainterPath()
        # hierarchy[0][i] = [next, prev, first_child, parent]
        # Outer contours (parent == -1) are added, holes (parent >= 0) are subtracted.
        for i, cnt in enumerate(contours):
            if len(cnt) < 3:
                continue
            sub = QPainterPath()
            pts = cnt.squeeze()
            if pts.ndim != 2:
                continue
            sub.moveTo(float(pts[0][0]), float(pts[0][1]))
            for px, py in pts[1:]:
                sub.lineTo(float(px), float(py))
            sub.closeSubpath()

            if hierarchy is not None and hierarchy[0][i][3] == -1:
                path = path.united(sub)   # outer contour
            else:
                path = path.subtracted(sub)  # hole

        if path.isEmpty():
            return

        # Store the feathered gradient mask on the canvas for operations (copy/cut/lift)
        if hasattr(self.canvas, 'selection_feather_mask'):
            self.canvas.selection_feather_mask = mask if feather != 0 else None

        # Set as canvas selection
        self.canvas.selection_path = path

        # Clean up tool state
        self._clear_all()

        # Auto-switch to Rect Select so the user can transform immediately
        self.canvas.set_tool("Rect Select")
        # Notify any connected panel about the tool change
        if hasattr(self.canvas, '_tool_switched_callback') and self.canvas._tool_switched_callback:
            self.canvas._tool_switched_callback("Rect Select")
        self.canvas.update()

    # ── Internal Methods ────────────────────────────────

    def _clear_all(self):
        self._points.clear()
        self._labels.clear()
        self._current_mask = None
        self._cleanup_temp()
        self._notify_panel()

    def _notify_panel(self):
        """Notify the panel to update point counts (panel calls get_point_counts)."""
        pass

    def get_point_counts(self):
        pos = self._labels.count(1)
        neg = self._labels.count(0)
        return pos, neg

    def _get_feathered_mask(self):
        """Apply current feather value to the raw mask and return the result.
        
        Positive feather = expand outward with soft edge.
        Negative feather = shrink inward with soft edge.
        Returns None if no mask available.
        """
        if self._current_mask is None:
            return None
        import cv2
        raw = self._current_mask  # H x W uint8 (0 or 255)
        f = self.feather
        if f == 0:
            return raw.copy()

        af = abs(f)
        ksize = af * 2 + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ksize, ksize))

        if f > 0:
            # Expand outward: dilate to get new outer boundary, then blur for softness
            dilated = cv2.dilate(raw, kernel, iterations=1)
            blurred = cv2.GaussianBlur(dilated, (ksize, ksize), 0)
            # Keep the original interior at full 255, soft-blend only the expanded zone
            result = np.maximum(raw, blurred)
        else:
            # Shrink inward: erode to get new inner boundary, then blur for softness
            eroded = cv2.erode(raw, kernel, iterations=1)
            blurred = cv2.GaussianBlur(eroded, (ksize, ksize), 0)
            # Clamp to original mask so we don't expand beyond original boundary
            result = np.minimum(raw, blurred)

        return result

    # ── Overlay Drawing ───────────────────────────────

    def draw_overlay(self, painter):
        """Draw mask preview and point markers on the canvas."""
        # Draw default selection dashes first (if any)
        super().draw_overlay(painter)

        layer = self.canvas.active_layer
        if not layer:
            return

        # ── Draw semi-transparent mask overlay (with feather applied) ──
        display_mask = self._get_feathered_mask()
        if display_mask is not None:
            h, w = display_mask.shape
            # Build RGBA overlay – alpha proportional to mask intensity
            rgba = np.zeros((h, w, 4), dtype=np.uint8)
            rgba[..., 0] = 60    # R
            rgba[..., 1] = 140   # G
            rgba[..., 2] = 255   # B
            # Scale alpha by mask value (0-255) → visible feather gradient
            rgba[..., 3] = (display_mask.astype(np.float32) * (90.0 / 255.0)).astype(np.uint8)

            overlay_img = QImage(rgba.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
            painter.save()
            painter.setOpacity(0.8)
            painter.drawImage(0, 0, overlay_img)
            painter.setOpacity(1.0)
            painter.restore()

        # ── Draw point markers ──
        zoom = self.canvas.zoom
        for i, (pt, lbl) in enumerate(zip(self._points, self._labels)):
            x, y = pt
            if lbl == 1:
                outer = QColor(0, 200, 0, 200)
                inner = QColor(0, 255, 0, 255)
            else:
                outer = QColor(200, 0, 0, 200)
                inner = QColor(255, 0, 0, 255)

            r_outer = max(5.0, 6.0 / zoom)
            r_inner = max(2.5, 3.0 / zoom)

            pen_w = max(1.0, 1.5 / zoom)
            painter.setPen(QPen(Qt.GlobalColor.white, pen_w))
            painter.setBrush(QBrush(outer))
            painter.drawEllipse(QPointF(x, y), r_outer, r_outer)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(inner))
            painter.drawEllipse(QPointF(x, y), r_inner, r_inner)

        # ── Inference-in-progress indicator ──
        if self._is_inferring:
            painter.resetTransform()
            painter.setPen(QColor(255, 200, 50))
            painter.setFont(QFont("Arial", 11))
            painter.drawText(20, 30, "Generating AI mask...")
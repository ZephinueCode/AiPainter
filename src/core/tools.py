# src/core/tools.py

from PyQt6.QtCore import Qt, QPoint, QRect, QPointF, QBuffer, QIODevice, QRectF
from PyQt6.QtGui import QPainter, QPen, QColor, QPainterPath, QCursor, QFont, QImage, QTransform, QPolygonF, QBrush
from PyQt6.QtWidgets import QMenu, QInputDialog, QDialog, QVBoxLayout, QSlider, QLabel, QDialogButtonBox, QApplication, QMessageBox
from src.core.logic import PaintLayer, TextLayer, PaintCommand, GroupLayer
from src.core.processor import ImageProcessor
from PIL import Image, ImageDraw, ImageFilter  # Added ImageFilter
import numpy as np
from OpenGL.GL import *
import io
import os
import math

# === Clipboard Utils ===
class ClipboardUtils:
    """Internal clipboard for selection-aware copy/cut/paste.

    Stores data on the *canvas* object so the SelectionTool can create
    floating overlays on paste.

    Canvas attributes used:
        _clip_image       – PIL RGBA cropped image
        _clip_offset      – (x, y) original top-left of the crop in doc coords
        _clip_path        – QPainterPath of the selection at copy time
        _clip_feather     – numpy H×W uint8 feather mask or None
        _clip_was_cut     – bool  (True = cut, False = copy)
    """

    @staticmethod
    def get_selection_mask(canvas):
        if not hasattr(canvas, 'selection_path') or canvas.selection_path.isEmpty():
            return None

        if hasattr(canvas, 'selection_feather_mask') and canvas.selection_feather_mask is not None:
            return Image.fromarray(canvas.selection_feather_mask, mode="L")

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
            return Image.open(io.BytesIO(bytes(buffer.data()))).convert("L")
        except:
            return None

    @staticmethod
    def _rasterize_mask_from_path(path, w, h):
        """Rasterize a QPainterPath into a PIL 'L' mask."""
        qimg = QImage(w, h, QImage.Format.Format_Grayscale8)
        qimg.fill(0)
        painter = QPainter(qimg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawPath(path)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.ReadWrite)
        qimg.save(buffer, "PNG")
        try:
            return Image.open(io.BytesIO(bytes(buffer.data()))).convert("L")
        except:
            return None

    # ------------------------------------------------------------------ copy
    @staticmethod
    def copy(canvas, record_history=True):
        if not canvas.active_layer:
            return False
        before_state = canvas.begin_history_action() if record_history else None

        # Check if there is a floating selection on the active tool.
        # If so, copy the floating content instead of the layer pixels.
        tool = canvas.active_tool
        floating = (tool and hasattr(tool, 'floating_items') and tool.floating_items)

        if floating:
            # Composite all floating items into one PIL image
            item = tool.floating_items[0]
            qimg = item['qimg']
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.ReadWrite)
            qimg.save(buf, "PNG")
            res = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")
            # The offset is the current tf_pos (may have been dragged)
            offset = (int(tool.tf_pos.x()), int(tool.tf_pos.y()))
        else:
            try:
                img = canvas.active_layer.get_image()
            except:
                return False

            mask = ClipboardUtils.get_selection_mask(canvas)
            if mask:
                mask_arr = np.array(mask, dtype=np.float32) / 255.0
                img_arr = np.array(img, dtype=np.float32)
                res_arr = img_arr.copy()
                res_arr[..., 3] *= mask_arr
                res = Image.fromarray(res_arr.clip(0, 255).astype(np.uint8), "RGBA")
                bbox = mask.getbbox()
                if bbox:
                    res = res.crop(bbox)
                    offset = (bbox[0], bbox[1])
                else:
                    offset = (0, 0)
            else:
                res = img.copy()
                offset = (0, 0)

        # Store on canvas
        canvas._clip_image = res
        canvas._clip_offset = offset
        canvas._clip_path = QPainterPath(canvas.selection_path)
        canvas._clip_feather = (
            canvas.selection_feather_mask.copy()
            if getattr(canvas, 'selection_feather_mask', None) is not None
            else None
        )
        canvas._clip_was_cut = False

        # Clear visible selection after copy
        canvas.selection_path = QPainterPath()
        canvas.selection_feather_mask = None

        # If there are floating items, discard them without committing
        # (the content was already captured above)
        if floating:
            for item in tool.floating_items:
                item['layer'].load_from_image(item['snapshot'])
            tool.floating_items = []

        # Also put on system clipboard for cross-app paste
        try:
            if res.mode != "RGBA":
                res = res.convert("RGBA")
            bio = io.BytesIO()
            res.save(bio, "PNG")
            qimg = QImage.fromData(bio.getvalue())
            QApplication.clipboard().setImage(qimg)
        except:
            pass

        canvas.update()
        if record_history:
            canvas.end_history_action(before_state, "Copy")
        return True

    # ------------------------------------------------------------------- cut
    @staticmethod
    def cut(canvas, record_history=True):
        before_state = canvas.begin_history_action() if record_history else None
        if not canvas.active_layer or canvas.active_layer.__class__.__name__ == 'GroupLayer':
            ok = ClipboardUtils.copy(canvas, record_history=False)
            if record_history and ok:
                canvas.end_history_action(before_state, "Cut")
            return ok

        tool = canvas.active_tool
        floating = (tool and hasattr(tool, 'floating_items') and tool.floating_items)

        if floating:
            # Floating overlay exists — copy its content then discard it.
            # copy() already discards floating items and restores snapshots.
            ClipboardUtils.copy(canvas, record_history=False)
            canvas._clip_was_cut = True
            canvas.update()
            if record_history:
                canvas.end_history_action(before_state, "Cut")
            return True

        # Normal path: no floating items — grab mask before copy clears it
        mask = ClipboardUtils.get_selection_mask(canvas)

        ClipboardUtils.copy(canvas, record_history=False)
        canvas._clip_was_cut = True

        # Erase selected area from layer
        if mask is None:
            path = canvas._clip_path
            if path and not path.isEmpty():
                mask = ClipboardUtils._rasterize_mask_from_path(
                    path, canvas.doc_width, canvas.doc_height)

        old_img = canvas.active_layer.get_image()
        if mask:
            mask_arr = np.array(mask, dtype=np.float32) / 255.0
            img_arr = np.array(old_img, dtype=np.float32)
            img_arr[..., 3] *= (1.0 - mask_arr)
            new_img = Image.fromarray(img_arr.clip(0, 255).astype(np.uint8), "RGBA")
        else:
            new_img = Image.new("RGBA", old_img.size, (0, 0, 0, 0))

        canvas.active_layer.load_from_image(new_img)
        canvas.update()
        if record_history:
            canvas.end_history_action(before_state, "Cut")
        return True

    # ----------------------------------------------------------------- paste
    @staticmethod
    def paste(canvas, at_position=None, record_history=True):
        """Paste as a floating selection on the active SelectionTool.

        Parameters
        ----------
        at_position : QPointF | None
            If given, place the top-left of the pasted image at this
            *document-space* coordinate.  Otherwise use the original offset
            (cut) or a slight duplicate-offset (copy).
        """
        before_state = canvas.begin_history_action() if record_history else None
        clip_img = getattr(canvas, '_clip_image', None)

        # Fallback: try system clipboard (e.g. paste from external app)
        if clip_img is None:
            clipboard = QApplication.clipboard()
            if not clipboard.mimeData().hasImage():
                return
            qimg = clipboard.image()
            if qimg.isNull():
                return
            buffer = QBuffer()
            buffer.open(QIODevice.OpenModeFlag.ReadWrite)
            qimg.save(buffer, "PNG")
            clip_img = Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")
            canvas._clip_image = clip_img
            canvas._clip_offset = (0, 0)
            canvas._clip_path = QPainterPath()
            canvas._clip_feather = None
            canvas._clip_was_cut = False

        was_cut = getattr(canvas, '_clip_was_cut', False)
        orig_offset = getattr(canvas, '_clip_offset', (0, 0))

        # Determine paste position
        if at_position is not None:
            px = int(at_position.x())
            py = int(at_position.y())
        elif was_cut:
            # Keyboard cut-paste → exact original position
            px, py = orig_offset
        else:
            # Keyboard copy-paste → nudge 10 px right-down
            px, py = orig_offset[0] + 10, orig_offset[1] + 10

        # Build a selection path (rect) around the pasted region
        cw, ch = clip_img.size
        sel_path = QPainterPath()
        sel_path.addRect(QRectF(px, py, cw, ch))

        # Convert pasted image to QImage for floating overlay
        bio = io.BytesIO()
        clip_img.save(bio, "PNG")
        qimg_float = QImage.fromData(bio.getvalue())

        # If the active tool is a SelectionTool, inject the floating overlay
        tool = canvas.active_tool
        if tool and hasattr(tool, 'floating_items'):
            # Commit any previous floating transform first
            tool.commit_transform(record_history=False)

            tool.tf_pos = QPointF(px, py)
            tool.tf_rotation = 0.0
            tool.tf_scale = QPointF(1.0, 1.0)
            tool.base_path = QPainterPath()
            tool.base_path.addRect(QRectF(0, 0, cw, ch))

            canvas.selection_path = sel_path
            canvas.selection_feather_mask = None

            layer = canvas.active_layer
            if layer and layer.__class__.__name__ == 'PaintLayer':
                tool.floating_items = [{
                    'layer': layer,
                    'qimg': qimg_float,
                    'snapshot': layer.get_image().copy(),
                }]
            tool.state = tool.STATE_IDLE
        else:
            # No selection tool active → merge directly (legacy path)
            if canvas.active_layer and canvas.active_layer.__class__.__name__ == 'PaintLayer':
                target = canvas.active_layer
                old_img = target.get_image()
                new_img = old_img.copy()
                new_img.paste(clip_img, (px, py), clip_img)
                target.load_from_image(new_img)

        canvas.update()
        if record_history:
            canvas.end_history_action(before_state, "Paste")

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
        self._create_before_state = None
        self._transform_before_state = None

    def has_selection(self):
        return not self.selection_path.isEmpty()
    
    def clear_selection(self):
        if self.selection_path.isEmpty() and not self.floating_items:
            return
        before_state = self.canvas.begin_history_action()
        self.commit_transform(record_history=False)
        self.selection_path = QPainterPath()
        if hasattr(self.canvas, 'selection_feather_mask'):
            self.canvas.selection_feather_mask = None
        self.state = self.STATE_IDLE
        self.canvas.update()
        self.canvas.end_history_action(before_state, "Clear Selection")

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

    def commit_transform(self, record_history=True):
        if not self.floating_items: 
            self.tf_rotation = 0.0
            self.tf_scale = QPointF(1.0, 1.0)
            return
        before_state = self.canvas.begin_history_action() if record_history else None
        
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
            item['layer'].load_from_image(curr)
        
        self.floating_items = []
        self.canvas.update()
        if record_history:
            self.canvas.end_history_action(before_state, "Commit Selection Transform")

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
        self.commit_transform(record_history=False)
        self._create_before_state = self.canvas.begin_history_action()
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
        self.canvas.end_history_action(self._create_before_state, "Create Selection")
        self._create_before_state = None

    # --- Interaction ---
    def mouse_press(self, event, pos, layer_pos):
        if event.button() == Qt.MouseButton.RightButton:
            self.show_context_menu(event.globalPosition().toPoint(), doc_pos=layer_pos)
            return True

        if event.button() == Qt.MouseButton.LeftButton:
            if self.has_selection():
                hit = self._hit_test(layer_pos)
                if hit:
                    self._transform_before_state = self.canvas.begin_history_action()
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
            self.canvas.end_history_action(self._transform_before_state, "Transform Selection")
            self._transform_before_state = None

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

    def show_context_menu(self, global_pos, doc_pos=None):
        menu = QMenu(self.canvas)
        menu.addAction("Cut", lambda: ClipboardUtils.cut(self.canvas)).setEnabled(self.has_selection())
        menu.addAction("Copy", lambda: ClipboardUtils.copy(self.canvas)).setEnabled(self.has_selection())
        paste_pos = QPointF(doc_pos.x(), doc_pos.y()) if doc_pos else None
        can_paste = QApplication.clipboard().mimeData().hasImage() or getattr(self.canvas, '_clip_image', None) is not None
        act_paste = menu.addAction("Paste", lambda: ClipboardUtils.paste(self.canvas, at_position=paste_pos))
        act_paste.setEnabled(can_paste)
        menu.addSeparator()
        act_del_rest = menu.addAction("Delete Rest", lambda: self._delete_rest())
        act_del_rest.setEnabled(self.has_selection() and self.canvas.active_layer is not None)
        menu.addSeparator()
        menu.addAction("Rotate Left 90", lambda: self._menu_tf(-90)).setEnabled(self.has_selection())
        menu.addAction("Rotate Right 90", lambda: self._menu_tf(90)).setEnabled(self.has_selection())
        menu.addSeparator()
        has_layer = self.canvas.active_layer is not None
        if self.has_selection():
            act_wanx = menu.addAction("Edit Selected Area", lambda: self._trigger_wanx_inpaint())
            act_wanx.setEnabled(has_layer)
        else:
            act_qwen = menu.addAction("Edit Layer", lambda: self._trigger_qwen_edit())
            act_qwen.setEnabled(has_layer)
        menu.exec(global_pos)

    def _trigger_wanx_inpaint(self):
        """Delegate to canvas for Wanx inpaint (with mask)."""
        if hasattr(self.canvas, 'start_wanx_inpaint'):
            self.canvas.start_wanx_inpaint()

    def _trigger_qwen_edit(self):
        """Delegate to canvas for Qwen image edit (no mask)."""
        if hasattr(self.canvas, 'start_qwen_edit'):
            self.canvas.start_qwen_edit()

    def _delete_rest(self):
        """Delete everything OUTSIDE the selection (make unselected area transparent).
        
        Respects feathered masks: feathered pixels get proportionally transparent.
        After applying, the feather mask is cleared so that subsequent lift/drag
        operations use the hard-edge selection path and don't leave ghost outlines.
        """
        if not self.has_selection():
            return
        layer = self.canvas.active_layer
        if not layer or layer.__class__.__name__ != 'PaintLayer':
            return

        before_state = self.canvas.begin_history_action()
        self.canvas.makeCurrent()
        mask = ClipboardUtils.get_selection_mask(self.canvas)
        if mask is None:
            return

        old_img = layer.get_image()
        mask_arr = np.array(mask, dtype=np.float32) / 255.0  # 0..1
        img_arr = np.array(old_img, dtype=np.float32)

        # Keep only the selected area: multiply alpha by the mask
        img_arr[..., 3] *= mask_arr
        new_img = Image.fromarray(img_arr.clip(0, 255).astype(np.uint8), "RGBA")

        layer.load_from_image(new_img)

        # Clear the feather gradient mask so the next _lift_selection uses
        # the hard-edge QPainterPath mask (binary 0/255).  This prevents
        # double-application of the feather producing ghost outlines.
        if hasattr(self.canvas, 'selection_feather_mask'):
            self.canvas.selection_feather_mask = None

        self.canvas.update()
        self.canvas.end_history_action(before_state, "Delete Rest")

    def _menu_tf(self, angle):
        before_state = self.canvas.begin_history_action()
        self._lift_selection()
        # Allows rotating empty box
        self.tf_rotation += angle
        t = self._get_current_transform()
        self.selection_path = t.map(self.base_path)
        self.commit_transform(record_history=False)
        self.canvas.end_history_action(before_state, "Rotate Selection")

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


class LiquifyTool(Tool):
    MODE_PUSH = 0
    MODE_BLOAT = 1
    MODE_PUCKER = 2
    MODE_RESTORE = 3

    def __init__(self, canvas):
        super().__init__(canvas)
        self.cursor = Qt.CursorShape.CrossCursor
        self.mode = self.MODE_PUSH
        self.radius = 50.0
        self.strength = 0.5
        self.grid_density = 20
        self.grid_points = None
        self.orig_grid_points = None
        self.uv_coords = None
        self.grid_rows = 0
        self.grid_cols = 0
        self.is_active = False
        self.texture_id = None
        self.preview_img = None
        self._target_layer = None
        self._has_changes = False
        self._applied = False
        self.last_mouse_pos = None
        self.hover_pos = None

    def activate(self):
        super().activate()
        layer = self.canvas.active_layer
        if not layer or not isinstance(layer, PaintLayer):
            return
        self.is_active = True
        self._target_layer = layer
        self._has_changes = False
        self._applied = False
        self._init_grid()
        layer.visible = False
        self.canvas.update()

    def deactivate(self):
        if self.is_active:
            if self._target_layer:
                self._target_layer.visible = True
            # Explicit Apply/Cancel UX: deactivate does not auto-commit.
            # If user did not apply, preview is discarded.
        self._cleanup()
        self.canvas.update()

    def set_mode(self, mode):
        try:
            mode = int(mode)
        except Exception:
            return
        if mode in (self.MODE_PUSH, self.MODE_BLOAT, self.MODE_PUCKER, self.MODE_RESTORE):
            self.mode = mode

    def set_radius(self, radius):
        try:
            self.radius = max(1.0, float(radius))
        except Exception:
            pass

    def set_strength(self, strength):
        try:
            self.strength = max(0.01, min(1.0, float(strength)))
        except Exception:
            pass

    def _cleanup(self):
        if self.texture_id:
            try:
                self.canvas.makeCurrent()
                glDeleteTextures([self.texture_id])
            except Exception:
                pass
            self.texture_id = None
        self.is_active = False
        self.grid_points = None
        self.orig_grid_points = None
        self.uv_coords = None
        self.grid_rows = 0
        self.grid_cols = 0
        self.last_mouse_pos = None
        self.hover_pos = None
        self.preview_img = None
        self._target_layer = None
        self._has_changes = False
        self._applied = False

    def commit(self):
        if not self.is_active or self.grid_points is None or self._target_layer is None:
            return False
        if not self._has_changes:
            return False

        try:
            self.canvas.makeCurrent()
            w, h = self.canvas.doc_width, self.canvas.doc_height
            fbo = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, fbo)
            tex = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0)
            glViewport(0, 0, w, h)
            glClearColor(0, 0, 0, 0)
            glClear(GL_COLOR_BUFFER_BIT)
            glMatrixMode(GL_PROJECTION)
            glLoadIdentity()
            glOrtho(0, w, h, 0, -1, 1)
            glMatrixMode(GL_MODELVIEW)
            glLoadIdentity()

            self._render_mesh_gl()
            data = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
            res_img = Image.frombytes("RGBA", (w, h), data).transpose(Image.FLIP_TOP_BOTTOM)

            glDeleteFramebuffers(1, [fbo])
            glDeleteTextures([tex])
            glBindFramebuffer(GL_FRAMEBUFFER, 0)

            old_img = self.preview_img.copy() if self.preview_img is not None else self._target_layer.get_image()
            if old_img.tobytes() == res_img.tobytes():
                return False
            cmd = PaintCommand(self._target_layer, old_img, res_img.copy())
            self.canvas.undo_stack.push(cmd)
            self._target_layer.load_from_image(res_img)
            self._applied = True
            return True
        except Exception as e:
            print(f"Liquify Commit Error: {e}")
            return False

    def apply_and_finish(self):
        """Commit liquify changes and close the tool session."""
        ok = self.commit()
        if self._target_layer:
            self._target_layer.visible = True
        self._cleanup()
        self.canvas.update()
        return ok

    def cancel_and_finish(self):
        """Discard liquify preview and close the tool session."""
        if self._target_layer:
            self._target_layer.visible = True
        self._cleanup()
        self.canvas.update()

    def _init_grid(self):
        img = self._target_layer.get_image()
        self.preview_img = img.copy()

        w, h = self.canvas.doc_width, self.canvas.doc_height
        xs = np.arange(0, w + self.grid_density, self.grid_density)
        ys = np.arange(0, h + self.grid_density, self.grid_density)
        self.grid_cols = len(xs)
        self.grid_rows = len(ys)
        xv, yv = np.meshgrid(xs, ys)
        self.grid_points = np.stack([xv, yv], axis=-1).astype(np.float32)
        self.orig_grid_points = self.grid_points.copy()
        self.uv_coords = np.zeros_like(self.grid_points)
        self.uv_coords[..., 0] = self.grid_points[..., 0] / w
        self.uv_coords[..., 1] = self.grid_points[..., 1] / h

        self.canvas.makeCurrent()
        if self.texture_id:
            glDeleteTextures([self.texture_id])
        self.texture_id = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self.texture_id)
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        data = img.tobytes("raw", "RGBA")
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, img.width, img.height, 0, GL_RGBA, GL_UNSIGNED_BYTE, data)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

    def mouse_press(self, event, pos, world_pos):
        if not self.is_active:
            return
        self.last_mouse_pos = world_pos
        self.hover_pos = world_pos
        self._apply_deformation(world_pos)
        self.canvas.update()

    def mouse_move(self, event, pos, world_pos):
        if not self.is_active:
            return
        self.hover_pos = world_pos
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._apply_deformation(world_pos)
            self.last_mouse_pos = world_pos
        self.canvas.update()

    def mouse_release(self, event, pos, world_pos):
        self.last_mouse_pos = None
        self.hover_pos = world_pos

    def _apply_deformation(self, curr_pos):
        if self.grid_points is None:
            return
        center = np.array([curr_pos.x(), curr_pos.y()])
        radius_sq = self.radius ** 2
        diff = self.grid_points - center
        dist_sq = np.sum(diff**2, axis=-1)
        mask = dist_sq < radius_sq
        if not np.any(mask):
            return
        dist_sq_norm = dist_sq[mask] / radius_sq
        factor = (1.0 - dist_sq_norm) ** 2 * self.strength

        if self.mode == self.MODE_PUSH:
            if self.last_mouse_pos is None:
                return
            move_vec = np.array([curr_pos.x() - self.last_mouse_pos.x(), curr_pos.y() - self.last_mouse_pos.y()])
            if np.linalg.norm(move_vec) < 0.1:
                return
            self.grid_points[mask] += move_vec[np.newaxis, :] * factor[:, np.newaxis]
            self._has_changes = True
        elif self.mode == self.MODE_BLOAT:
            self.grid_points[mask] += diff[mask] * (factor[:, np.newaxis] * 0.1)
            self._has_changes = True
        elif self.mode == self.MODE_PUCKER:
            self.grid_points[mask] -= diff[mask] * (factor[:, np.newaxis] * 0.1)
            self._has_changes = True
        elif self.mode == self.MODE_RESTORE:
            current = self.grid_points[mask]
            target = self.orig_grid_points[mask]
            delta = (target - current) * (factor[:, np.newaxis] * 0.5)
            if np.any(np.abs(delta) > 1e-3):
                self.grid_points[mask] += delta
                self._has_changes = True

    def draw_overlay(self, painter):
        if not self.is_active or not self.hover_pos:
            return
        painter.setPen(QColor(255, 255, 255, 200))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(self.hover_pos, self.radius, self.radius)

    def draw_gl(self):
        if not self.is_active or self.grid_points is None:
            return
        self._render_mesh_gl()

    def _render_mesh_gl(self):
        if self.texture_id is None:
            return

        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, self.texture_id)
        glColor4f(1, 1, 1, 1)
        rows, cols = self.grid_rows, self.grid_cols
        verts = self.grid_points.reshape(-1, 2)
        texs = self.uv_coords.reshape(-1, 2)

        if not hasattr(self, "_gl_indices"):
            inds = []
            for i in range(rows - 1):
                for j in range(cols - 1):
                    tl = i * cols + j
                    tr = tl + 1
                    bl = (i + 1) * cols + j
                    br = bl + 1
                    inds.extend([tl, tr, br, bl])
            self._gl_indices = np.array(inds, dtype=np.uint32)

        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_TEXTURE_COORD_ARRAY)
        glVertexPointer(2, GL_FLOAT, 0, verts.tobytes())
        glTexCoordPointer(2, GL_FLOAT, 0, texs.tobytes())
        glDrawElements(GL_QUADS, len(self._gl_indices), GL_UNSIGNED_INT, self._gl_indices.tobytes())
        glDisableClientState(GL_VERTEX_ARRAY)
        glDisableClientState(GL_TEXTURE_COORD_ARRAY)
        glDisable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, 0)

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
        self._redo_points = []  # redo stack for points
        self._redo_labels = []  # redo stack for labels
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
            self._status_callback(msg if success else f"{msg}")

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
        # New point invalidates redo stack.
        self._redo_points.clear()
        self._redo_labels.clear()

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
            self._status_callback(f"Inference failed: {msg}")

    # ── Public Operations (called by the panel) ────────

    def set_point_mode(self, mode):
        """Switch point mode: 1=positive, 0=negative."""
        self._point_mode = mode

    def undo_last_point(self):
        """Undo the last point."""
        if not self._points:
            return
        p = self._points.pop()
        l = self._labels.pop()
        self._redo_points.append(p)
        self._redo_labels.append(l)
        self._notify_panel()
        if self._points:
            self._run_inference()
        else:
            self._current_mask = None
            self.canvas.update()

    def redo_last_point(self):
        """Redo the last undone point."""
        if not self._redo_points:
            return
        p = self._redo_points.pop()
        l = self._redo_labels.pop()
        self._points.append(p)
        self._labels.append(l)
        self._notify_panel()
        self._run_inference()

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
        self._redo_points.clear()
        self._redo_labels.clear()
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

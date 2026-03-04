# src/gui/canvas.py

import numpy as np
from PIL import Image
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import QWidget, QScrollBar, QGridLayout, QMenu, QApplication, QMessageBox
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF, QBuffer, QIODevice, QEvent
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QPen, QImage
from OpenGL.GL import *
from src.core.brush_manager import BrushConfig
from src.core.logic import Node, GroupLayer, PaintLayer, PaintCommand, UndoStack, ProjectLogic, TextLayer
from src.core.history import CanvasStateCommand
from src.core.psd_utils import collect_paint_layers_for_export, write_psd
from src.core.tools import RectSelectTool, LassoTool, BucketTool, PickerTool, SmudgeTool, TextTool, ClipboardUtils, MagicWandTool, LiquifyTool
from src.core.brush_functions import StrokeStabilizer
from src.core.processor import ImageProcessor
from src.gui.dialogs import GradientMapDialog, AdjustmentDialog
from src.agent.agent_manager import AIAgentManager
import os
import uuid
import io

import sys


class GLCanvas(QOpenGLWidget):
    layer_structure_changed = pyqtSignal()
    view_changed = pyqtSignal()
    brush_color_changed = pyqtSignal(list)
    _AUTO_SKETCH_PROMPT = (
        "Detect rough or unfinished line art in the current layer and refine it into clean, "
        "high-quality line art. Preserve original composition and character identity, fix broken "
        "strokes, improve contour consistency, and avoid adding unrelated objects."
    )
    _AUTO_COLOR_PROMPT = (
        "Analyze the current line art and generate a high-quality colored version while preserving "
        "the existing structure. Apply harmonious palette, clean rendering, and readable shading."
    )
    _AUTO_OPTIMIZE_PROMPT = (
        "Auto-optimize this drawing while preserving intent. Improve structure correctness, "
        "proportions, silhouette readability, composition balance, and overall visual quality."
    )
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc_width = 1920
        self.doc_height = 1080
        self.zoom = 1.0
        self.offset = QPointF(0, 0)
        
        self.root = GroupLayer("Root")
        self._active_layer = None
        
        self.current_brush = None
        self._brush_color = [0,0,0]
        self.brush_texture_id = None
        self.last_pos = None
        self.stabilizer = StrokeStabilizer(smoothing_factor=0.0)

        self.is_panning = False
        self.last_pan_pos = QPointF(0, 0)

        self.undo_stack = UndoStack(owner_canvas=self)
        self._stroke_start_image = None
        self._history_restoring = False
        self.active_tool_name = None
        
        self.selection_path = QPainterPath()
        self.selection_feather_mask = None  # numpy HxW uint8 gradient mask (for feathered selections)
        
        self.active_tool = None
        self._tool_switched_callback = None  # callback for tool auto-switch notification
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

    @property
    def brush_color(self):
        return self._brush_color
    
    @brush_color.setter
    def brush_color(self, val):
        self._brush_color = val
        self.brush_color_changed.emit(val if isinstance(val, list) else list(val))

    @property
    def active_layer(self):
        return self._active_layer

    @active_layer.setter
    def active_layer(self, value):
        if value is not self._active_layer:
            # Layer changed: clear selection (it belongs to the old layer).
            self.selection_path = QPainterPath()
            self.selection_feather_mask = None
            # Commit any in-progress floating transform
            if self.active_tool and hasattr(self.active_tool, 'commit_transform'):
                self.active_tool.commit_transform()
                if hasattr(self.active_tool, 'floating_items'):
                    self.active_tool.floating_items = []
        self._active_layer = value

    def set_tool(self, tool_name):
        if self._history_restoring:
            return
        if self.active_tool:
            self.active_tool.deactivate()
            self.active_tool = None
        
        self.setCursor(Qt.CursorShape.ArrowCursor)
        
        # Clear selection when switching away from selection tools
        # (selection is only kept when switching between Rect Select / Lasso)
        selection_tools = {"Rect Select", "Lasso", "Magic Wand"}
        if tool_name not in selection_tools:
            self.selection_path = QPainterPath()
            self.selection_feather_mask = None
        
        if tool_name == "Rect Select": self.active_tool = RectSelectTool(self)
        elif tool_name == "Lasso": self.active_tool = LassoTool(self)
        elif tool_name == "Fill Select": self.active_tool = BucketTool(self)
        elif tool_name == "Picker": self.active_tool = PickerTool(self)
        elif tool_name == "Smudge": self.active_tool = SmudgeTool(self)
        elif tool_name == "Text": self.active_tool = TextTool(self)
        elif tool_name == "Magic Wand": self.active_tool = MagicWandTool(self)
        elif tool_name == "Liquify": self.active_tool = LiquifyTool(self)
        
        if self.active_tool:
            self.active_tool.activate()
            self.active_tool_name = tool_name
        else:
            self.active_tool_name = None
        
        self.update()

    def perform_undo(self):
        if self.undo_stack.undo():
            self.layer_structure_changed.emit()
            self.view_changed.emit()
            self.update()

    def perform_redo(self):
        if self.undo_stack.redo():
            self.layer_structure_changed.emit()
            self.view_changed.emit()
            self.update()

    def begin_history_action(self):
        if self._history_restoring:
            return None
        return self.capture_history_state()

    def end_history_action(self, before_state, label=""):
        if self._history_restoring or before_state is None:
            return
        after_state = self.capture_history_state()
        self.undo_stack.push(CanvasStateCommand(self, before_state, after_state, label))

    def _iter_paint_layers(self, node):
        if isinstance(node, PaintLayer):
            yield node
            return
        if hasattr(node, "children"):
            for child in node.children:
                yield from self._iter_paint_layers(child)

    def find_layer_by_uuid(self, layer_uuid):
        if not layer_uuid:
            return None

        def walk(node):
            if isinstance(node, PaintLayer) and getattr(node, "uuid", None) == layer_uuid:
                return node
            if hasattr(node, "children"):
                for child in node.children:
                    res = walk(child)
                    if res is not None:
                        return res
            return None

        return walk(self.root)

    def _cleanup_node_resources(self, node):
        if isinstance(node, PaintLayer):
            try:
                node.cleanup()
            except Exception:
                pass
            return
        if hasattr(node, "children"):
            for child in node.children:
                self._cleanup_node_resources(child)

    def _node_path(self, target):
        if target is None:
            return None

        def walk(node, prefix):
            if node is target:
                return list(prefix)
            if not hasattr(node, "children"):
                return None
            for idx, child in enumerate(node.children):
                res = walk(child, prefix + [idx])
                if res is not None:
                    return res
            return None

        return walk(self.root, [])

    def _node_from_path(self, path):
        if path is None:
            return None
        node = self.root
        for idx in path:
            if not hasattr(node, "children") or idx < 0 or idx >= len(node.children):
                return None
            node = node.children[idx]
        return node

    def _snapshot_layer_data(self, layer):
        img = self._read_layer_rgba(layer).copy()
        if not getattr(layer, "uuid", None):
            layer.uuid = str(uuid.uuid4())
        data = {
            "type": "TextLayer" if isinstance(layer, TextLayer) else "PaintLayer",
            "name": layer.name,
            "visible": bool(layer.visible),
            "opacity": float(layer.opacity),
            "uuid": getattr(layer, "uuid", None),
            "image": img,
        }
        if isinstance(layer, TextLayer):
            data.update({
                "text_content": layer.text_content,
                "font_size": layer.font_size,
                "text_color": layer.text_color,
                "pos_x": layer.pos_x,
                "pos_y": layer.pos_y,
            })
        return data

    def _snapshot_node(self, node):
        if isinstance(node, GroupLayer):
            return {
                "type": "GroupLayer",
                "name": node.name,
                "visible": bool(node.visible),
                "opacity": float(node.opacity),
                "children": [self._snapshot_node(child) for child in node.children],
            }
        if isinstance(node, PaintLayer):
            return self._snapshot_layer_data(node)
        return {
            "type": "Node",
            "name": getattr(node, "name", "Node"),
            "visible": bool(getattr(node, "visible", True)),
            "opacity": float(getattr(node, "opacity", 1.0)),
            "children": [self._snapshot_node(child) for child in getattr(node, "children", [])],
        }

    def _restore_node(self, data):
        typ = data.get("type")
        if typ == "GroupLayer":
            grp = GroupLayer(data.get("name", "Group"))
            grp.visible = bool(data.get("visible", True))
            grp.opacity = float(data.get("opacity", 1.0))
            for child_data in data.get("children", []):
                grp.add_child(self._restore_node(child_data))
            return grp

        if typ == "TextLayer":
            layer = TextLayer(
                self.doc_width,
                self.doc_height,
                text=data.get("text_content", "Text"),
                font_size=data.get("font_size", 50),
                color=data.get("text_color", (0, 0, 0, 255)),
                x=data.get("pos_x", 0),
                y=data.get("pos_y", 0),
                name=data.get("name", "Text Layer"),
            )
        else:
            layer = PaintLayer(self.doc_width, self.doc_height, data.get("name", "Layer"))

        layer.visible = bool(data.get("visible", True))
        layer.opacity = float(data.get("opacity", 1.0))
        layer.uuid = data.get("uuid") or str(uuid.uuid4())
        img = data.get("image")
        if img is not None:
            layer.load_from_image(img)
        return layer

    def _capture_floating_state(self):
        tool = self.active_tool
        if not tool or not hasattr(tool, "floating_items") or not tool.floating_items:
            return None

        floating_items = []
        for item in tool.floating_items:
            layer_path = self._node_path(item.get("layer"))
            if layer_path is None:
                continue

            qimg = item.get("qimg")
            qimg_bytes = b""
            if isinstance(qimg, QImage):
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.ReadWrite)
                qimg.save(buf, "PNG")
                qimg_bytes = bytes(buf.data())

            snapshot = item.get("snapshot")
            floating_items.append({
                "layer_path": layer_path,
                "qimg_bytes": qimg_bytes,
                "snapshot": snapshot.copy() if snapshot is not None else None,
            })

        if not floating_items:
            return None

        return {
            "tf_pos": (float(tool.tf_pos.x()), float(tool.tf_pos.y())),
            "tf_rotation": float(tool.tf_rotation),
            "tf_scale": (float(tool.tf_scale.x()), float(tool.tf_scale.y())),
            "base_path": QPainterPath(tool.base_path),
            "items": floating_items,
        }

    def _restore_floating_state(self, floating_state):
        if not floating_state:
            return
        tool = self.active_tool
        if not tool or not hasattr(tool, "floating_items"):
            return

        tool.floating_items = []
        for item_state in floating_state.get("items", []):
            layer = self._node_from_path(item_state.get("layer_path"))
            if layer is None or not isinstance(layer, PaintLayer):
                continue
            qimg = QImage.fromData(item_state.get("qimg_bytes", b""))
            if qimg.isNull():
                continue
            snapshot = item_state.get("snapshot")
            tool.floating_items.append({
                "layer": layer,
                "qimg": qimg,
                "snapshot": snapshot.copy() if snapshot is not None else layer.get_image().copy(),
            })

        tf_pos = floating_state.get("tf_pos", (0.0, 0.0))
        tf_scale = floating_state.get("tf_scale", (1.0, 1.0))
        tool.tf_pos = QPointF(tf_pos[0], tf_pos[1])
        tool.tf_rotation = float(floating_state.get("tf_rotation", 0.0))
        tool.tf_scale = QPointF(tf_scale[0], tf_scale[1])
        tool.base_path = QPainterPath(floating_state.get("base_path", QPainterPath()))
        if hasattr(tool, "STATE_IDLE"):
            tool.state = tool.STATE_IDLE

    def _activate_tool_from_name(self, tool_name):
        self.active_tool = None
        self.active_tool_name = None
        if not tool_name:
            return
        tool_map = {
            "Rect Select": RectSelectTool,
            "Lasso": LassoTool,
            "Fill Select": BucketTool,
            "Picker": PickerTool,
            "Smudge": SmudgeTool,
            "Text": TextTool,
            "Magic Wand": MagicWandTool,
            "Liquify": LiquifyTool,
        }
        cls = tool_map.get(tool_name)
        if cls is None:
            return
        self.active_tool = cls(self)
        self.active_tool.activate()
        self.active_tool_name = tool_name

    def capture_history_state(self):
        self.makeCurrent()
        return {
            "doc_width": int(self.doc_width),
            "doc_height": int(self.doc_height),
            "root": self._snapshot_node(self.root),
            "active_layer_path": self._node_path(self._active_layer),
            "selection_path": QPainterPath(self.selection_path),
            "selection_feather_mask": (
                self.selection_feather_mask.copy()
                if self.selection_feather_mask is not None else None
            ),
            "tool_name": self.active_tool_name,
            "floating_state": self._capture_floating_state(),
        }

    def apply_history_state(self, state):
        if state is None:
            return
        self._history_restoring = True
        try:
            self.makeCurrent()
            self._cleanup_node_resources(self.root)

            self.doc_width = int(state.get("doc_width", self.doc_width))
            self.doc_height = int(state.get("doc_height", self.doc_height))
            self.root = self._restore_node(state.get("root", {"type": "GroupLayer", "name": "Root", "children": []}))

            self._active_layer = self._node_from_path(state.get("active_layer_path"))
            self.selection_path = QPainterPath(state.get("selection_path", QPainterPath()))
            feather = state.get("selection_feather_mask")
            self.selection_feather_mask = feather.copy() if feather is not None else None

            self._activate_tool_from_name(state.get("tool_name"))
            self._restore_floating_state(state.get("floating_state"))

            self.layer_structure_changed.emit()
            self.view_changed.emit()
            self.update()
        finally:
            self._history_restoring = False

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
        before_state = self.begin_history_action()
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
        self.end_history_action(before_state, "Resize Canvas")

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
        if self.active_tool and hasattr(self.active_tool, "draw_gl"):
            self.active_tool.draw_gl()
        
        glDisable(GL_TEXTURE_2D)
            
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
            painter.save()
            painter.translate(self.offset)
            painter.scale(self.zoom, self.zoom)
            if not self.selection_path.isEmpty():
                from PyQt6.QtGui import QPen as _Pen
                pen = _Pen(Qt.GlobalColor.white, 1, Qt.PenStyle.DashLine)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(self.selection_path)
                pen.setColor(Qt.GlobalColor.black); pen.setDashOffset(5)
                painter.setPen(pen); painter.drawPath(self.selection_path)
            painter.restore()

        # Draw document border. Use dashed style when a selection exists.
        painter.save()
        painter.translate(self.offset)
        painter.scale(self.zoom, self.zoom)
        doc_rect = QRectF(0, 0, self.doc_width, self.doc_height)
        if not self.selection_path.isEmpty():
            # Selection exists: draw dashed border to indicate active selection mode.
            pen = QPen(QColor(100, 100, 100, 200), 2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
        else:
            # No selection: draw normal solid document border.
            pen = QPen(QColor(0, 0, 0), 2, Qt.PenStyle.SolidLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(doc_rect)
        painter.restore()
            
        painter.end()

    def _render_node(self, node):
        if not node.visible: return
        glPushMatrix()
        if isinstance(node, GroupLayer):
            for child in node.children:
                self._render_node(child)
        elif isinstance(node, PaintLayer):
            glEnable(GL_TEXTURE_2D)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glBindTexture(GL_TEXTURE_2D, node.texture)
            op = node.opacity * self._get_parent_opacity(node)
            glColor4f(1.0, 1.0, 1.0, op) 
            
            glBegin(GL_QUADS)
            glTexCoord2f(0, 1); glVertex2f(0, 0)
            glTexCoord2f(0, 0); glVertex2f(0, node.height)
            glTexCoord2f(1, 0); glVertex2f(node.width, node.height)
            glTexCoord2f(1, 1); glVertex2f(node.width, 0)
            glEnd()
            glDisable(GL_TEXTURE_2D)
        glPopMatrix()

    def _get_parent_opacity(self, node):
        op = 1.0
        p = node.parent
        while p and p != self.root:
            if not p.visible: return 0.0 # Stop recursion if parent hidden
            op *= p.opacity
            p = p.parent
        return op

    def _screen_to_doc(self, screen_pt):
        """Convert widget (screen) coordinates to document coordinates."""
        return (screen_pt - self.offset) / self.zoom

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
                    if self.stabilizer.smoothing_factor > 0:
                        self.stabilizer.reset()
                        self.last_pos = self.stabilizer.update(pos)
                    # Ensure texture is loaded
                    self._update_brush_texture()
                    self._paint_stroke(self.last_pos, pressure=1.0)

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
            if self.stabilizer.smoothing_factor > 0:
                pos = self.stabilizer.update(pos)
            self._paint_stroke(pos, pressure=1.0)
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

    def tabletEvent(self, event):
        if self.active_tool:
            pos = (event.position() - self.offset) / self.zoom
            if event.type() == QEvent.Type.TabletPress:
                self.active_tool.mouse_press(event, event.position(), pos)
            elif event.type() == QEvent.Type.TabletMove:
                self.active_tool.mouse_move(event, event.position(), pos)
            elif event.type() == QEvent.Type.TabletRelease:
                self.active_tool.mouse_release(event, event.position(), pos)
            event.accept()
            return

        pos = (event.position() - self.offset) / self.zoom
        pressure = max(0.0, min(1.0, float(event.pressure())))

        if event.type() == QEvent.Type.TabletPress:
            if isinstance(self.active_layer, GroupLayer):
                event.accept()
                return
            self.last_pos = pos
            if self.active_layer and isinstance(self.active_layer, PaintLayer) and self.active_layer.visible and self.current_brush:
                self.makeCurrent()
                self._stroke_start_image = self.active_layer.get_image()
                if self.stabilizer.smoothing_factor > 0:
                    self.stabilizer.reset()
                    self.last_pos = self.stabilizer.update(pos)
                self._update_brush_texture()
                self._paint_stroke(self.last_pos, pressure=pressure)
        elif event.type() == QEvent.Type.TabletMove:
            if self.last_pos:
                if self.stabilizer.smoothing_factor > 0:
                    pos = self.stabilizer.update(pos)
                self._paint_stroke(pos, pressure=pressure)
                self.last_pos = pos
        elif event.type() == QEvent.Type.TabletRelease:
            if self._stroke_start_image and self.active_layer:
                self.makeCurrent()
                end_image = self.active_layer.get_image()
                cmd = PaintCommand(self.active_layer, self._stroke_start_image, end_image)
                self.undo_stack.push(cmd)
                self._stroke_start_image = None
            self.last_pos = None
        event.accept()

    def keyPressEvent(self, event):
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)

        # Helper: auto-commit Magic Wand result if it has not been applied yet.
        def _ensure_magic_wand_committed():
            """If the active tool is MagicWandTool and selection_path is still
            empty, automatically apply the wand result as a selection so that
            copy/cut/delete can work immediately after clicking."""
            if (self.active_tool
                    and isinstance(self.active_tool, MagicWandTool)
                    and self.selection_path.isEmpty()):
                self.active_tool.apply_as_selection(
                    feather=self.active_tool.feather
                )

        # Magic Wand specific keys (Enter to confirm, Ctrl+Z undo point, Ctrl+Shift+Z/Ctrl+Y redo point).
        if self.active_tool and isinstance(self.active_tool, MagicWandTool):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.active_tool.apply_as_selection(feather=self.active_tool.feather)
                self.update()
                return
            if ctrl and event.key() == Qt.Key.Key_Z:
                if shift:
                    self.active_tool.redo_last_point()
                else:
                    self.active_tool.undo_last_point()
                self.update()
                return
            if ctrl and event.key() == Qt.Key.Key_Y:
                self.active_tool.redo_last_point()
                self.update()
                return

        # Delete / Backspace
        if event.key() == Qt.Key.Key_Delete or event.key() == Qt.Key.Key_Backspace:
            # If there's a floating selection, discard it (restore layer snapshot)
            tool = self.active_tool
            if tool and hasattr(tool, 'floating_items') and tool.floating_items:
                for item in tool.floating_items:
                    item['layer'].load_from_image(item['snapshot'])
                tool.floating_items = []
                self.selection_path = QPainterPath()
                self.selection_feather_mask = None
                self.update()
                return

            # Auto-commit Magic Wand before checking selection
            _ensure_magic_wand_committed()

            if not self.selection_path.isEmpty():
                if self.active_layer and isinstance(self.active_layer, PaintLayer):
                    mask = ClipboardUtils.get_selection_mask(self)
                    if mask:
                        old_img = self.active_layer.get_image()
                        mask_arr = np.array(mask, dtype=np.float32) / 255.0
                        img_arr = np.array(old_img, dtype=np.float32)
                        img_arr[..., 3] *= (1.0 - mask_arr)
                        new_img = Image.fromarray(img_arr.clip(0, 255).astype(np.uint8), "RGBA")

                        cmd = PaintCommand(self.active_layer, old_img, new_img)
                        self.undo_stack.push(cmd)
                        self.active_layer.load_from_image(new_img)
                        self.update()
            return

        # Escape
        if event.key() == Qt.Key.Key_Escape:
            if self.active_tool and isinstance(self.active_tool, LiquifyTool):
                self.active_tool.cancel_and_finish()
                self.set_tool("Rect Select")
                if self._tool_switched_callback:
                    self._tool_switched_callback("Rect Select")
                self.update()
                return
            if self.active_tool and hasattr(self.active_tool, 'deactivate'):
                self.active_tool.deactivate()
            if not self.active_tool:
                self.selection_path = QPainterPath()
                self.selection_feather_mask = None
            self.update()
            return

        # Undo / Redo
        if ctrl and event.key() == Qt.Key.Key_Z:
            if shift:
                self.perform_redo()
            else:
                self.perform_undo()
            return
        if ctrl and event.key() == Qt.Key.Key_Y:
            self.perform_redo()
            return

        # Copy (Ctrl+C)
        if ctrl and event.key() == Qt.Key.Key_C:
            _ensure_magic_wand_committed()
            ClipboardUtils.copy(self)
            return

        # Cut (Ctrl+X)
        if ctrl and event.key() == Qt.Key.Key_X:
            _ensure_magic_wand_committed()
            ClipboardUtils.cut(self)
            return

        # Paste (Ctrl+V)
        if ctrl and event.key() == Qt.Key.Key_V:
            ClipboardUtils.paste(self)
            return

        super().keyPressEvent(event)

    def show_default_context_menu(self, event):
        menu = QMenu(self)
        clipboard = QApplication.clipboard()
        can_paste = clipboard.mimeData().hasImage() or getattr(self, '_clip_image', None) is not None
        doc_pos = self._screen_to_doc(event.position())
        act_paste = menu.addAction("Paste", lambda: ClipboardUtils.paste(self, at_position=doc_pos))
        act_paste.setEnabled(can_paste)
        
        menu.addSeparator()
        menu.addAction("(AI) Multi-Layer Gen (Experimental)", self.test_trigger_ai) #for test
        act_qwen = menu.addAction("(AI) Edit Layer", self.start_qwen_edit)
        act_qwen.setEnabled(self.active_layer is not None and isinstance(self.active_layer, PaintLayer))
        menu.addSeparator()
        menu.addAction("HSL Adjustment", lambda: self.open_adjustment("HSL"))
        menu.addAction("Contrast", lambda: self.open_adjustment("Contrast"))
        menu.addAction("Exposure", lambda: self.open_adjustment("Exposure"))
        menu.addAction("Gaussian Blur", lambda: self.open_adjustment("Blur"))
        menu.addAction("Gradient Map...", self.open_gradient_map)
        
        menu.exec(event.globalPosition().toPoint())
    
    def test_trigger_ai(self):
        """Debug entry for layered AI generation from the active layer."""
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer):
            QMessageBox.warning(self, "AI Tip", "Select a layer first.")
            return

        # 1) Ask user for prompt (empty prompt allows auto partition behavior).
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Qwen Layered AI", "Input prompt (or leave blank to partition)......")
        if not ok: return

        # 2) Read current layer image as model input.
        self.makeCurrent() # Ensure valid GL context before reading texture-backed layer.
        input_pil = self.active_layer.to_pil()

        # 3) Start layered generation thread.
        from src.agent.generate import ImageGenerator
        self.current_generator = ImageGenerator()
        # Results are handled in handle_layered_generation.
        self.current_generator.layered_generation_finished.connect(self.handle_layered_generation)

        # 4) Trigger generation.
        print("AI Parsing...")
        self.current_generator.generate_layered(prompt=text, input_image=input_pil, num_layers=4)

    def handle_layered_generation(self, images, names, error_msg):
        """
        Insert generated layers into the project and refresh the UI state. Keep GL context valid.
        """
        if error_msg:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Generation Failed", error_msg)
            return

        if not images:
            return

        print(f"Received {len(images)} layers, inserting......")

        # Ensure GL context before creating and uploading new layer textures.
        before_state = self.begin_history_action()
        
        # 1) create_group_from_images builds GPU-backed PaintLayers from generated PIL images.
        #    It internally uploads textures through load_from_image.
        self.makeCurrent()

        try:
            # 2) Build an AI group from returned images + names.
            #    Generated items are converted into project PaintLayer nodes.
            ai_group = ProjectLogic.create_group_from_images(
                images, 
                names, 
                self.doc_width, 
                self.doc_height
            )

            # 3) Insert the generated group into the root layer tree.
            self.root.add_child(ai_group)

            # 4) Make the top generated layer active for immediate editing.
            if ai_group.children:
                self.active_layer = ai_group.children[-1]

            # 5) Refresh UI state after insertion.
            self.layer_structure_changed.emit() # Refresh layer tree panel.
            self.view_changed.emit()            # Refresh viewport-dependent UI state.
            self.update()                       # Trigger canvas repaint.
            self.end_history_action(before_state, "AI Layered Generation")
            
            print("Layered Image Inserted.")

        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Insert Failure", f"Error Processing AI Layers: {str(e)}")

    # Inpaint / Image-Edit

    @staticmethod
    def _layer_has_transparency(pil_rgba):
        """Check whether the layer uses transparency (has any alpha < 255)."""
        if pil_rgba.mode != "RGBA":
            return False
        alpha = np.array(pil_rgba.split()[3])
        return bool(np.any(alpha < 255))

    def _prompt_with_rmwhite(self, title, label, show_checkbox):
        """Show a prompt dialog that optionally includes a 'Remove White BG' checkbox.

        Returns (prompt_str, remove_white_bg_bool, accepted_bool).
        """
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel as _L,
                                      QLineEdit, QCheckBox, QDialogButtonBox)
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        layout = QVBoxLayout(dlg)
        layout.addWidget(_L(label))
        edit = QLineEdit()
        layout.addWidget(edit)

        cb = None
        if show_checkbox:
            cb = QCheckBox("Auto Remove White Background on result")
            cb.setChecked(True)
            layout.addWidget(cb)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        ok = dlg.exec() == QDialog.DialogCode.Accepted
        remove_bg = cb.isChecked() if cb else False
        return edit.text(), remove_bg, ok

    def start_wanx_inpaint(self):
        """Launch Wanx inpaint with current selection mask and text prompt."""
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer):
            QMessageBox.warning(self, "No Layer", "Please select a Paint Layer.")
            return
        if self.selection_path.isEmpty():
            QMessageBox.warning(self, "No Selection", "Please create a selection first.")
            return

        self.makeCurrent()
        base_img = self.active_layer.get_image()  # PIL RGBA
        has_transp = self._layer_has_transparency(base_img)

        prompt, remove_bg, ok = self._prompt_with_rmwhite(
            "Wanx Inpaint",
            "Describe what to generate in the selected area:\n(Leave empty for automatic)",
            show_checkbox=has_transp,
        )
        if not ok:
            return
        # Wanx API requires a non-empty prompt; use a space as placeholder
        prompt_to_send = prompt.strip() if prompt.strip() else " "

        # Build binary mask (white = edit area) at layer dimensions
        mask = self._build_inpaint_mask()
        if mask is None:
            QMessageBox.warning(self, "Mask Error", "Failed to build selection mask.")
            return

        # Convert RGBA to RGB for the API (white background).
        if base_img.mode == "RGBA":
            bg = Image.new("RGB", base_img.size, (255, 255, 255))
            bg.paste(base_img, mask=base_img.split()[3])
            base_rgb = bg
        else:
            base_rgb = base_img.convert("RGB")

        # Convert mask to RGB (API expects image)
        mask_rgb = mask.convert("RGB")

        from src.agent.inpaint_service import WanxInpaintThread
        self._inpaint_old_img = base_img.copy()  # for undo
        self._inpaint_remove_white_bg = remove_bg
        self._inpaint_apply_mode = "replace"
        self._inpaint_new_layer_name = ""
        self._inpaint_target_layer = self.active_layer
        self._inpaint_thread = WanxInpaintThread(base_rgb, mask_rgb, prompt_to_send)
        self._inpaint_thread.progress.connect(self._on_inpaint_progress)
        self._inpaint_thread.finished.connect(self._on_wanx_inpaint_finished)
        self._show_inpaint_progress("Wanx Inpaint", prompt_to_send, base_rgb, mask)
        self._inpaint_thread.start()

    def start_qwen_edit(self):
        """Launch Qwen image edit: prompt-only whole-image edit.

        Always sends the full canvas-sized image (doc_width x doc_height).
        The QwenEditThread handles resize if the canvas dimensions are outside
        the API's [512, 2048] range, and scales the result back afterwards.
        """
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer):
            QMessageBox.warning(self, "No Layer", "Please select a Paint Layer.")
            return

        self.makeCurrent()
        base_img = self.active_layer.get_image()  # PIL RGBA, doc-sized

        if base_img.getbbox() is None:
            QMessageBox.warning(self, "Empty Layer", "The layer has no visible content.")
            return

        has_transp = self._layer_has_transparency(base_img)

        prompt, remove_bg, ok = self._prompt_with_rmwhite(
            "Qwen Image Edit",
            "Describe the edit you want to make:",
            show_checkbox=has_transp,
        )
        if not ok or not prompt.strip():
            return

        # Run prompt-based edit using shared Qwen workflow
        self._run_qwen_edit(
            title="Qwen Image Edit",
            prompt=prompt.strip(),
            remove_bg=remove_bg,
            apply_mode="replace",
            new_layer_name="",
        )

    def start_auto_sketch(self):
        """AI shortcut: refine unfinished line art and output to a new layer."""
        self._run_qwen_edit(
            title="Auto Sketch",
            prompt=self._AUTO_SKETCH_PROMPT,
            remove_bg=True,
            apply_mode="new_layer",
            new_layer_name="AI Auto Sketch",
        )

    def _build_auto_color_prompt(self, color_pref="", effect_pref="", material_pref=""):
        prompt = self._AUTO_COLOR_PROMPT
        extras = []
        if color_pref:
            extras.append(f"Preferred color style: {color_pref}.")
        if effect_pref:
            extras.append(f"Preferred rendering effect: {effect_pref}.")
        if material_pref:
            extras.append(f"Preferred material/texture: {material_pref}.")
        if extras:
            prompt = f"{prompt} User preferences: {' '.join(extras)}"
        return prompt

    def _has_active_selection(self):
        if hasattr(self, "selection_feather_mask") and self.selection_feather_mask is not None:
            try:
                return bool(np.any(self.selection_feather_mask > 0))
            except Exception:
                return True
        return not self.selection_path.isEmpty()

    def _run_auto_color_inpaint(self, prompt):
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer):
            QMessageBox.warning(self, "No Layer", "Please select a Paint Layer.")
            return

        mask = self._build_inpaint_mask()
        if mask is None:
            QMessageBox.warning(self, "No Selection", "Please create a valid selection first.")
            return

        self.makeCurrent()
        base_img = self.active_layer.get_image()
        if base_img.mode == "RGBA":
            bg = Image.new("RGB", base_img.size, (255, 255, 255))
            bg.paste(base_img, mask=base_img.split()[3])
            base_rgb = bg
        else:
            base_rgb = base_img.convert("RGB")

        mask_rgb = mask.convert("RGB")
        from src.agent.inpaint_service import WanxInpaintThread
        self._inpaint_old_img = base_img.copy()
        self._inpaint_remove_white_bg = False
        self._inpaint_apply_mode = "new_layer"
        self._inpaint_new_layer_name = "AI Auto Color (Local)"
        self._inpaint_target_layer = self.active_layer
        self._inpaint_thread = WanxInpaintThread(base_rgb, mask_rgb, prompt.strip() or " ")
        self._inpaint_thread.progress.connect(self._on_inpaint_progress)
        self._inpaint_thread.finished.connect(self._on_wanx_inpaint_finished)
        self._show_inpaint_progress("Auto Color (Local Inpaint)", prompt, base_rgb, mask)
        self._inpaint_thread.start()

    def start_auto_color(self, color_pref="", effect_pref="", material_pref=""):
        """AI shortcut: colorize line art.

        If selection exists -> local inpaint auto color.
        Otherwise -> whole-layer image edit to a new layer.
        """
        final_prompt = self._build_auto_color_prompt(color_pref, effect_pref, material_pref)

        if self._has_active_selection():
            self._run_auto_color_inpaint(final_prompt)
            return

        self._run_qwen_edit(
            title="Auto Color",
            prompt=final_prompt,
            remove_bg=True,
            apply_mode="new_layer",
            new_layer_name="AI Auto Color",
        )

    def start_auto_optimize(self):
        """AI shortcut: optimize structure and aesthetics to a new layer."""
        self._run_qwen_edit(
            title="Auto Optimize",
            prompt=self._AUTO_OPTIMIZE_PROMPT,
            remove_bg=True,
            apply_mode="new_layer",
            new_layer_name="AI Auto Optimize",
        )

    def start_auto_resolution(self):
        """Local super-resolution shortcut. Result is always added as a new layer."""
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer):
            QMessageBox.warning(self, "No Layer", "Please select a Paint Layer.")
            return

        self.makeCurrent()
        base_img = self.active_layer.get_image()
        bbox = base_img.getbbox()
        if bbox is None:
            QMessageBox.warning(self, "Empty Layer", "The layer has no visible content.")
            return

        from PyQt6.QtWidgets import QInputDialog
        style, ok = QInputDialog.getItem(
            self,
            "Auto Resolution",
            "Choose super-resolution style:",
            ["General", "Illustration"],
            1,
            False,
        )
        if not ok or not style:
            return

        cropped = base_img.crop(bbox).convert("RGBA")
        manager = AIAgentManager()
        from src.agent.superres_service import LocalSuperResolutionThread

        self._superres_target_layer = self.active_layer
        self._superres_thread = LocalSuperResolutionThread(
            base_image=cropped,
            target_size=(self.doc_width, self.doc_height),
            style=style.lower(),
            general_model_path=manager.superres_general_model_path,
            illustration_model_path=manager.superres_illustration_model_path,
        )
        self._superres_thread.progress.connect(self._on_inpaint_progress)
        self._superres_thread.finished.connect(self._on_auto_resolution_finished)
        self._show_inpaint_progress(
            "Auto Resolution",
            f"Style: {style} | Source content: {bbox[2]-bbox[0]}x{bbox[3]-bbox[1]}",
            cropped,
            None,
        )
        self._superres_thread.start()

    def _run_qwen_edit(self, title, prompt, remove_bg, apply_mode, new_layer_name):
        """Common runner for prompt-based Qwen edits and shortcuts."""
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer):
            QMessageBox.warning(self, "No Layer", "Please select a Paint Layer.")
            return

        self.makeCurrent()
        base_img = self.active_layer.get_image()

        if base_img.getbbox() is None:
            QMessageBox.warning(self, "Empty Layer", "The layer has no visible content.")
            return

        if base_img.mode == "RGBA":
            bg = Image.new("RGB", base_img.size, (255, 255, 255))
            bg.paste(base_img, mask=base_img.split()[3])
            send_rgb = bg
        else:
            send_rgb = base_img.convert("RGB")

        from src.agent.inpaint_service import QwenEditThread
        self._inpaint_old_img = base_img.copy()
        self._inpaint_remove_white_bg = bool(remove_bg)
        self._qwen_apply_mode = apply_mode
        self._qwen_new_layer_name = new_layer_name
        self._qwen_target_layer = self.active_layer
        self._inpaint_thread = QwenEditThread(send_rgb, prompt)
        self._inpaint_thread.progress.connect(self._on_inpaint_progress)
        self._inpaint_thread.finished.connect(self._on_qwen_edit_finished)
        self._show_inpaint_progress(title, prompt, send_rgb, None)
        self._inpaint_thread.start()

    def _build_inpaint_mask(self):
        """Build a binary/feathered L-mode mask from the current selection.
        
        Returns a PIL Image (mode 'L') of size (doc_width, doc_height).
        White (255) = area to edit, Black (0) = keep.
        For feathered selections the gradient is preserved so the mask
        boundary reflects the feather extent.
        """
        # Use the stored feather mask if available (from Magic Wand with feather)
        if hasattr(self, 'selection_feather_mask') and self.selection_feather_mask is not None:
            mask_arr = self.selection_feather_mask
            # Threshold to binary: any feathered pixel > 0 becomes part of the mask.
            # This expands the mask to cover the full feather zone.
            import cv2
            # Use threshold at 1 to include the entire feathered gradient
            _, binary = cv2.threshold(mask_arr, 1, 255, cv2.THRESH_BINARY)
            return Image.fromarray(binary, mode="L")

        # Otherwise rasterize from QPainterPath
        if self.selection_path.isEmpty():
            return None

        from PyQt6.QtGui import QImage as _QImage, QPainter as _QPainter
        from PyQt6.QtCore import QIODevice as _QIODevice
        from PyQt6.QtCore import QBuffer as _QBuffer

        w, h = self.doc_width, self.doc_height
        qimg = _QImage(w, h, _QImage.Format.Format_Grayscale8)
        qimg.fill(0)

        painter = _QPainter(qimg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawPath(self.selection_path)
        painter.end()

        buf = _QBuffer()
        buf.open(_QIODevice.OpenModeFlag.ReadWrite)
        qimg.save(buf, "PNG")
        try:
            mask = Image.open(io.BytesIO(bytes(buf.data()))).convert("L")
            return mask
        except Exception:
            return None

    def _show_inpaint_progress(self, title, prompt, base_pil, mask_pil=None):
        """Show a rich progress dialog with prompt, image preview and optional mask."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar
        from PyQt6.QtGui import QPixmap
        from PyQt6.QtCore import QByteArray, QBuffer as _Buf, QIODevice as _IO

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(480)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        layout = QVBoxLayout(dlg)

        # Prompt
        prompt_label = QLabel(f"<b>Prompt:</b> {prompt}")
        prompt_label.setWordWrap(True)
        layout.addWidget(prompt_label)

        # Preview images
        preview_row = QHBoxLayout()

        def _pil_to_pixmap(pil_img, max_w=200, max_h=150):
            buf = io.BytesIO()
            pil_img.save(buf, "PNG")
            pm = QPixmap()
            pm.loadFromData(buf.getvalue())
            return pm.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)

        # Base image preview
        img_box = QVBoxLayout()
        img_box.addWidget(QLabel("<b>Input Image</b>"))
        img_lbl = QLabel()
        img_lbl.setPixmap(_pil_to_pixmap(base_pil))
        img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_box.addWidget(img_lbl)
        preview_row.addLayout(img_box)

        # Mask preview (if any)
        if mask_pil is not None:
            mask_box = QVBoxLayout()
            mask_box.addWidget(QLabel("<b>Mask</b>"))
            mask_lbl = QLabel()
            mask_lbl.setPixmap(_pil_to_pixmap(mask_pil.convert("RGB")))
            mask_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mask_box.addWidget(mask_lbl)
            preview_row.addLayout(mask_box)

        layout.addLayout(preview_row)

        # Progress bar (indeterminate)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)  # indeterminate
        layout.addWidget(self._progress_bar)

        # Status label
        self._progress_status = QLabel("Preparing...")
        self._progress_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._progress_status)

        dlg.setLayout(layout)
        dlg.show()
        self._progress_dlg = dlg

    def _on_inpaint_progress(self, msg):
        if hasattr(self, '_progress_status') and self._progress_status:
            self._progress_status.setText(msg)

    def _on_wanx_inpaint_finished(self, result_img, error):
        if hasattr(self, '_progress_dlg') and self._progress_dlg:
            self._progress_dlg.close()
            self._progress_dlg = None

        if error:
            QMessageBox.warning(self, "Wanx Inpaint Failed", error)
            return

        if result_img is None:
            return

        apply_mode = getattr(self, "_inpaint_apply_mode", "replace")
        if apply_mode == "new_layer":
            layer_name = getattr(self, "_inpaint_new_layer_name", "AI Inpaint")
            ref_layer = getattr(self, "_inpaint_target_layer", self.active_layer)
            self._insert_ai_result_layer(
                result_img,
                layer_name,
                ref_layer,
                remove_bg=getattr(self, "_inpaint_remove_white_bg", False),
            )
            self._inpaint_old_img = None
            self._inpaint_remove_white_bg = False
            self._inpaint_apply_mode = "replace"
            self._inpaint_new_layer_name = ""
            self._inpaint_target_layer = None
            return

        self._apply_inpaint_result(result_img)

    def _on_qwen_edit_finished(self, result_img, error):
        if hasattr(self, '_progress_dlg') and self._progress_dlg:
            self._progress_dlg.close()
            self._progress_dlg = None

        if error:
            QMessageBox.warning(self, "Qwen Edit Failed", error)
            return

        if result_img is None:
            return

        apply_mode = getattr(self, "_qwen_apply_mode", "replace")
        if apply_mode == "new_layer":
            layer_name = getattr(self, "_qwen_new_layer_name", "AI Qwen Edit")
            ref_layer = getattr(self, "_qwen_target_layer", self.active_layer)
            self._insert_ai_result_layer(
                result_img,
                layer_name,
                ref_layer,
                remove_bg=getattr(self, "_inpaint_remove_white_bg", False),
            )
            self._inpaint_old_img = None
            self._inpaint_remove_white_bg = False
            self._qwen_apply_mode = "replace"
            self._qwen_new_layer_name = ""
            self._qwen_target_layer = None
            return

        self._apply_qwen_result(result_img)

    def _apply_qwen_result(self, result_img):
        """Apply Qwen-edited image back to the active layer (full canvas size)."""
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer):
            return

        self.makeCurrent()

        old_img = getattr(self, '_inpaint_old_img', None)
        if old_img is None:
            old_img = self.active_layer.get_image()

        if result_img.mode != "RGBA":
            result_img = result_img.convert("RGBA")

        # The thread already returns an image at canvas size; safety resize
        lw, lh = self.active_layer.width, self.active_layer.height
        if result_img.size != (lw, lh):
            result_img = result_img.resize((lw, lh), Image.LANCZOS)

        # Auto-remove white background if the user opted in
        if getattr(self, '_inpaint_remove_white_bg', False):
            from src.core.processor import remove_white_background
            result_img = remove_white_background(result_img)

        cmd = PaintCommand(self.active_layer, old_img, result_img)
        self.undo_stack.push(cmd)

        self.active_layer.load_from_image(result_img)
        self.update()
        self._inpaint_old_img = None
        self._inpaint_remove_white_bg = False
        self._inpaint_apply_mode = "replace"
        self._inpaint_new_layer_name = ""
        self._inpaint_target_layer = None
        self._qwen_apply_mode = "replace"
        self._qwen_new_layer_name = ""
        self._qwen_target_layer = None

    def _on_auto_resolution_finished(self, result_img, error):
        if hasattr(self, '_progress_dlg') and self._progress_dlg:
            self._progress_dlg.close()
            self._progress_dlg = None

        if error:
            QMessageBox.warning(self, "Auto Resolution Failed", error)
            return
        if result_img is None:
            return

        ref_layer = getattr(self, "_superres_target_layer", self.active_layer)
        self._insert_ai_result_layer(result_img, "AI Auto Resolution", ref_layer, remove_bg=False)
        self._superres_target_layer = None

    def _insert_ai_result_layer(self, result_img, layer_name, ref_layer, remove_bg=False):
        """Insert AI result as a new paint layer near the reference layer."""
        before_state = self.begin_history_action()
        if result_img.mode != "RGBA":
            result_img = result_img.convert("RGBA")

        tw, th = self.doc_width, self.doc_height
        if result_img.size != (tw, th):
            result_img = result_img.resize((tw, th), Image.LANCZOS)

        if remove_bg:
            from src.core.processor import remove_white_background
            result_img = remove_white_background(result_img)

        self.makeCurrent()
        new_layer = PaintLayer(tw, th, layer_name)
        new_layer.load_from_image(result_img)

        parent = self.root
        insert_idx = len(parent.children)
        if ref_layer and isinstance(ref_layer, PaintLayer) and ref_layer.parent:
            parent = ref_layer.parent
            if ref_layer in parent.children:
                insert_idx = parent.children.index(ref_layer) + 1

        parent.children.insert(insert_idx, new_layer)
        new_layer.parent = parent

        self.active_layer = new_layer
        self.layer_structure_changed.emit()
        self.update()
        self.end_history_action(before_state, f"Insert {layer_name}")

    def _apply_inpaint_result(self, result_img):
        """Apply the AI-edited image back to the current layer with undo support."""
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer):
            return

        self.makeCurrent()

        # Resize result to match layer dimensions if needed
        lw, lh = self.active_layer.width, self.active_layer.height
        if result_img.size != (lw, lh):
            result_img = result_img.resize((lw, lh), Image.LANCZOS)

        if result_img.mode != "RGBA":
            result_img = result_img.convert("RGBA")

        # Auto-remove white background if the user opted in
        if getattr(self, '_inpaint_remove_white_bg', False):
            from src.core.processor import remove_white_background
            result_img = remove_white_background(result_img)

        old_img = getattr(self, '_inpaint_old_img', None)
        if old_img is None:
            old_img = self.active_layer.get_image()

        cmd = PaintCommand(self.active_layer, old_img, result_img)
        self.undo_stack.push(cmd)

        self.active_layer.load_from_image(result_img)
        self.update()
        self._inpaint_old_img = None
        self._inpaint_remove_white_bg = False

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
            self.undo_stack = UndoStack(owner_canvas=self)
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

    def open_img(self, path):
        try:
            before_state = self.begin_history_action()
            self.makeCurrent()
            
            # Load image via PIL
            img = Image.open(path)
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            w, h = img.size

            # Scale to fit the canvas while preserving aspect ratio
            ratio = min(self.doc_width / w, self.doc_height / h, 1.0)
            display_w = int(w * ratio)
            display_h = int(h * ratio)
            if ratio < 1.0:
                img = img.resize((display_w, display_h), Image.LANCZOS)

            # Always create a document-sized layer so that all tools
            # (selection, clipboard, etc.) work with consistent dimensions.
            canvas_img = Image.new("RGBA", (self.doc_width, self.doc_height), (0, 0, 0, 0))
            paste_x = (self.doc_width - display_w) // 2
            paste_y = (self.doc_height - display_h) // 2
            canvas_img.paste(img, (paste_x, paste_y))

            new_layer = PaintLayer(self.doc_width, self.doc_height, os.path.basename(path))
            new_layer.uuid = str(uuid.uuid4())
            new_layer.load_from_image(canvas_img)

            # Add to layer tree
            self.root.add_child(new_layer)
            self.active_layer = new_layer
            self.layer_structure_changed.emit()
            self.update()
            self.end_history_action(before_state, "Import Image")
        except Exception as e:
            print(f"Error importing image: {e}")

    def import_psd(self, path):
        try:
            before_state = self.begin_history_action()
            self.makeCurrent()
            width, height, root = ProjectLogic.import_psd(path, self.doc_width, self.doc_height)
            self.doc_width = width; self.doc_height = height; self.root = root
            self.layer_structure_changed.emit(); self.update(); self.view_changed.emit()
            self.end_history_action(before_state, "Import PSD")
        except Exception as e: print(e)

    def export_image(self, path):
        self.makeCurrent()
        fbo = glGenFramebuffers(1)
        tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, tex)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, self.doc_width, self.doc_height, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
        glBindFramebuffer(GL_FRAMEBUFFER, fbo)
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0)
        glViewport(0, 0, self.doc_width, self.doc_height)
        glClearColor(1,0,0,1)
        glClear(GL_COLOR_BUFFER_BIT)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(0, self.doc_width, self.doc_height, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        glEnable(GL_BLEND) 
        glEnable(GL_TEXTURE_2D)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        print("Rendering...")
        def debug_nodes(node):
            if isinstance(node, PaintLayer):
                print(f"Rendering: {node.name}, Texture ID: {node.texture}, Visibility: {node.visible}")
            if hasattr(node, 'children'):
                for c in node.children: debug_nodes(c)
        debug_nodes(self.root)
        self._render_node(self.root)
        data = glReadPixels(0, 0, self.doc_width, self.doc_height, GL_RGBA, GL_UNSIGNED_BYTE)
        Image.frombytes("RGBA", (self.doc_width, self.doc_height), data).transpose(Image.FLIP_TOP_BOTTOM).save(path)
        glDeleteFramebuffers(1, [fbo]); glDeleteTextures([tex]); glBindFramebuffer(GL_FRAMEBUFFER, 0)

    def capture_visible_image(self):
        """Render currently visible layers to a PIL RGBA image."""
        try:
            self.makeCurrent()
            fbo = glGenFramebuffers(1)
            tex = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tex)
            glTexImage2D(
                GL_TEXTURE_2D,
                0,
                GL_RGBA,
                self.doc_width,
                self.doc_height,
                0,
                GL_RGBA,
                GL_UNSIGNED_BYTE,
                None,
            )
            glBindFramebuffer(GL_FRAMEBUFFER, fbo)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0)
            glViewport(0, 0, self.doc_width, self.doc_height)
            glClearColor(0, 0, 0, 0)
            glClear(GL_COLOR_BUFFER_BIT)
            glMatrixMode(GL_PROJECTION)
            glLoadIdentity()
            glOrtho(0, self.doc_width, self.doc_height, 0, -1, 1)
            glMatrixMode(GL_MODELVIEW)
            glLoadIdentity()
            glEnable(GL_BLEND)
            glEnable(GL_TEXTURE_2D)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            self._render_node(self.root)
            data = glReadPixels(0, 0, self.doc_width, self.doc_height, GL_RGBA, GL_UNSIGNED_BYTE)
            img = Image.frombytes("RGBA", (self.doc_width, self.doc_height), data).transpose(Image.FLIP_TOP_BOTTOM)
            glDeleteFramebuffers(1, [fbo])
            glDeleteTextures([tex])
            glBindFramebuffer(GL_FRAMEBUFFER, 0)
            return img
        except Exception as e:
            print(f"Capture visible image failed: {e}")
            return None

    def _read_layer_rgba(self, node):
        """
        Robustly read a PaintLayer RGBA image from GPU.
        Priority: texture read -> FBO read fallback.
        Returns a PIL.Image in RGBA, top-left origin.
        """
        expected = node.width * node.height * 4
        raw = None

        self.makeCurrent()
        glPixelStorei(GL_PACK_ALIGNMENT, 1)
        glFinish()  # ensure all pending draws completed

        # --- Try texture read first ---
        try:
            glBindTexture(GL_TEXTURE_2D, node.texture)
            # Keep to base level only (avoid mip confusion on some drivers)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_BASE_LEVEL, 0)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAX_LEVEL, 0)

            raw = glGetTexImage(GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE)
            if raw is not None and len(raw) != expected:
                raw = None
        except Exception:
            raw = None

        # --- Fallback: read from layer FBO ---
        if raw is None:
            try:
                glBindFramebuffer(GL_FRAMEBUFFER, node.fbo)
                raw = glReadPixels(0, 0, node.width, node.height, GL_RGBA, GL_UNSIGNED_BYTE)
                glBindFramebuffer(GL_FRAMEBUFFER, 0)
                if raw is not None and len(raw) != expected:
                    raw = None
            except Exception:
                raw = None
                glBindFramebuffer(GL_FRAMEBUFFER, 0)

        # --- Last fallback: transparent ---
        if raw is None:
            raw = b'\x00' * expected

        img = Image.frombytes("RGBA", (node.width, node.height), raw)
        # OpenGL origin is bottom-left; convert to top-left
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
        return img


    def export_to_psd(self, path):
        """Export layer tree to Photoshop-compatible PSD (paint layers only)."""
        self.makeCurrent()
        glPixelStorei(GL_PACK_ALIGNMENT, 1)

        doc_w, doc_h = self.doc_width, self.doc_height
        try:
            layers_bottom_to_top = collect_paint_layers_for_export(
                self.root,
                doc_w,
                doc_h,
                self._read_layer_rgba,
            )
            if not layers_bottom_to_top:
                print("No layers to export.")
                return
            write_psd(path, doc_w, doc_h, layers_bottom_to_top)
            print(f"PSD exported successfully: {path}")
        except Exception as e:
            print(f"PSD export failed: {e}")
            import traceback
            traceback.print_exc()

    def set_brush(self, config):
        self.current_brush = config
        self._update_brush_texture()
        if self.active_tool:
            self.active_tool.deactivate()
            self.active_tool = None
            self.active_tool_name = None
        # Switching to a brush means the user wants to paint, so clear selection.
        self.selection_path = QPainterPath()
        self.selection_feather_mask = None
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

    def _interpolate_pressure_curve(self, points, pressure):
        x_val = max(0.0, min(1.0, float(pressure)))
        if not points:
            return x_val

        pairs = []
        for p in points:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                px = max(0.0, min(255.0, float(p[0])))
                py = max(0.0, min(255.0, float(p[1])))
                pairs.append((px, py))
        if len(pairs) < 2:
            return x_val
        pairs.sort(key=lambda t: t[0])

        x255 = x_val * 255.0
        if x255 <= pairs[0][0]:
            return pairs[0][1] / 255.0
        if x255 >= pairs[-1][0]:
            return pairs[-1][1] / 255.0

        for i in range(len(pairs) - 1):
            x0, y0 = pairs[i]
            x1, y1 = pairs[i + 1]
            if x0 <= x255 <= x1:
                if abs(x1 - x0) < 1e-6:
                    return y1 / 255.0
                t = (x255 - x0) / (x1 - x0)
                y = y0 * (1.0 - t) + y1 * t
                return max(0.0, min(1.0, y / 255.0))
        return x_val

    def _paint_stroke(self, current_pos, pressure=1.0):
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer): return
        if not self.active_layer.visible or not self.current_brush: return
        if not self.last_pos: self.last_pos = current_pos
        self.makeCurrent(); glBindFramebuffer(GL_FRAMEBUFFER, self.active_layer.fbo); glViewport(0, 0, self.active_layer.width, self.active_layer.height)
        glMatrixMode(GL_PROJECTION); glLoadIdentity(); glOrtho(0, self.active_layer.width, self.active_layer.height, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW); glLoadIdentity()
        
        glEnable(GL_BLEND)
        glBlendEquation(GL_FUNC_ADD)

        p = max(0.0, min(1.0, float(pressure)))
        size_factor = self._interpolate_pressure_curve(getattr(self.current_brush, "pressure_size_curve", None), p)
        opacity_factor = self._interpolate_pressure_curve(getattr(self.current_brush, "pressure_opacity_curve", None), p)
        current_size = max(1.0, float(self.current_brush.size) * size_factor)
        current_opacity = max(0.0, min(1.0, float(self.current_brush.opacity) * opacity_factor))
        
        if self.current_brush.blend_mode == "Eraser": 
            glBlendFunc(GL_ZERO, GL_ONE_MINUS_SRC_ALPHA)
            glColor4f(0, 0, 0, current_opacity)
        else: 
            # Fix: Use Separate Blending for correct alpha accumulation
            # RGB: Standard Source Over (Premultiplied output from glColor)
            # Alpha: Standard accumulation (Src + Dst*(1-Src))
            glBlendFuncSeparate(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_ONE, GL_ONE_MINUS_SRC_ALPHA)
            
            alpha = self.current_brush.flow * current_opacity
            
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
        step = max(1.0, current_size * self.current_brush.spacing)
        steps = int(dist / step) + 1; dx = (current_pos.x() - self.last_pos.x()) / steps; dy = (current_pos.y() - self.last_pos.y()) / steps
        glEnable(GL_TEXTURE_2D); glBindTexture(GL_TEXTURE_2D, self.brush_texture_id)
        
        glBegin(GL_QUADS)
        for i in range(steps):
            cx = self.last_pos.x() + dx * i; cy = self.last_pos.y() + dy * i; hs = current_size / 2
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
    @property
    def stabilizer(self): return self.gl_canvas.stabilizer
    def initializeGL(self): self.gl_canvas.initializeGL()
    def update(self): super().update(); self.gl_canvas.update()
    def import_psd(self, path): self.gl_canvas.import_psd(path)
    def open_img(self,path): self.gl_canvas.open_img(path)
    def save_project(self, path): self.gl_canvas.save_project(path)
    def export_image(self, path): self.gl_canvas.export_image(path)
    def export_psd(self, path): self.gl_canvas.export_to_psd(path)
    def capture_visible_image(self): return self.gl_canvas.capture_visible_image()
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


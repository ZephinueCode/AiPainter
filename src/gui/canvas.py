# src/gui/canvas.py

import numpy as np
from PIL import Image
from PyQt6.QtOpenGLWidgets import QOpenGLWidget
from PyQt6.QtWidgets import QWidget, QScrollBar, QGridLayout, QMenu, QApplication, QMessageBox
from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QPen
from OpenGL.GL import *
from src.core.brush_manager import BrushConfig
from src.core.logic import Node, GroupLayer, PaintLayer, PaintCommand, UndoStack, ProjectLogic, TextLayer
from src.core.tools import RectSelectTool, LassoTool, BucketTool, PickerTool, SmudgeTool, TextTool, ClipboardUtils, MagicWandTool
from src.core.processor import ImageProcessor
from src.gui.dialogs import GradientMapDialog, AdjustmentDialog
import os
import uuid
import io
from pytoshop.user import nested_layers
from pytoshop import enums
import pytoshop
import packbits
import pytoshop.core
import pytoshop.layers
pytoshop.core.packbits = packbits
pytoshop.layers.packbits = packbits


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
        self._active_layer = None
        
        self.current_brush = None
        self._brush_color = [0,0,0]
        self.brush_texture_id = None
        self.last_pos = None

        self.is_panning = False
        self.last_pan_pos = QPointF(0, 0)

        self.undo_stack = UndoStack()
        self._stroke_start_image = None
        
        self.selection_path = QPainterPath()
        self.selection_feather_mask = None  # numpy H×W uint8 gradient mask (for feathered selections)
        
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
            # Layer changed → clear selection (it belongs to the old layer)
            self.selection_path = QPainterPath()
            self.selection_feather_mask = None
            # Commit any in-progress floating transform
            if self.active_tool and hasattr(self.active_tool, 'commit_transform'):
                self.active_tool.commit_transform()
                if hasattr(self.active_tool, 'floating_items'):
                    self.active_tool.floating_items = []
        self._active_layer = value

    def set_tool(self, tool_name):
        if self.active_tool:
            self.active_tool.deactivate()
            self.active_tool = None
        
        self.setCursor(Qt.CursorShape.ArrowCursor)
        
        # Clear selection when switching away from selection tools
        # (selection is only kept when switching between Rect Select / Lasso)
        selection_tools = {"Rect Select", "Lasso"}
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
            # No active tool (e.g. brush mode) – draw only basic dashed border, no handles
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
            if hasattr(self, 'active_layer') and node == self.active_layer:
                glDisable(GL_TEXTURE_2D)
                glColor4f(0.0, 0.6, 1.0, 1.0) # 亮蓝色
                glLineWidth(20.0)
                glBegin(GL_LINE_LOOP)
                glVertex2f(0, 0)
                glVertex2f(node.width, 0)
                glVertex2f(node.width, node.height)
                glVertex2f(0, node.height)
                glEnd()
                glEnable(GL_TEXTURE_2D)
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

            if not self.selection_path.isEmpty():
                if self.active_layer and isinstance(self.active_layer, PaintLayer):
                    mask = ClipboardUtils.get_selection_mask(self)
                    if mask:
                        old_img = self.active_layer.get_image()
                        # Alpha-weighted removal for feathered masks
                        mask_arr = np.array(mask, dtype=np.float32) / 255.0
                        img_arr = np.array(old_img, dtype=np.float32)
                        img_arr[..., 3] *= (1.0 - mask_arr)
                        new_img = Image.fromarray(img_arr.clip(0, 255).astype(np.uint8), "RGBA")
                        
                        cmd = PaintCommand(self.active_layer, old_img, new_img)
                        self.undo_stack.push(cmd)
                        self.active_layer.load_from_image(new_img)
                        self.update()
            return

        # Magic Wand: Enter applies selection, Ctrl+Z undoes last point
        if self.active_tool and isinstance(self.active_tool, MagicWandTool):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.active_tool.apply_as_selection(feather=self.active_tool.feather)
                self.update()
                return
            elif ctrl and event.key() == Qt.Key.Key_Z:
                self.active_tool.undo_last_point()
                self.update()
                return

        if event.key() == Qt.Key.Key_Escape:
            if self.active_tool and hasattr(self.active_tool, 'deactivate'):
                self.active_tool.deactivate()
            if not self.active_tool:
                self.selection_path = QPainterPath()
                self.selection_feather_mask = None
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
        can_paste = clipboard.mimeData().hasImage() or getattr(self, '_clip_image', None) is not None
        doc_pos = self._screen_to_doc(event.position())
        act_paste = menu.addAction("Paste", lambda: ClipboardUtils.paste(self, at_position=doc_pos))
        act_paste.setEnabled(can_paste)
        
        menu.addSeparator()
        menu.addAction("✨ AI Generate Layered (Test)", self.test_trigger_ai)#for test
        act_qwen = menu.addAction("✨ Edit With Qwen-Imageedit", self.start_qwen_edit)
        act_qwen.setEnabled(self.active_layer is not None and isinstance(self.active_layer, PaintLayer))
        menu.addSeparator()
        menu.addAction("HSL Adjustment", lambda: self.open_adjustment("HSL"))
        menu.addAction("Contrast", lambda: self.open_adjustment("Contrast"))
        menu.addAction("Exposure", lambda: self.open_adjustment("Exposure"))
        menu.addAction("Gaussian Blur", lambda: self.open_adjustment("Blur"))
        menu.addAction("Gradient Map...", self.open_gradient_map)
        
        menu.exec(event.globalPosition().toPoint())
    
    def test_trigger_ai(self):
        """一键拆分当前图层工作流"""
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer):
            QMessageBox.warning(self, "AI 提示", "请先选中一个绘图图层。")
            return

        # 1. 弹窗询问 (可选)
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Qwen Layered AI", "输入描述 (或直接点击确定进行拆分):")
        if not ok: return

        # 2. 提取当前图层作为 AI 输入
        self.makeCurrent() # 必须激活上下文才能读纹理
        input_pil = self.active_layer.to_pil()

        # 3. 准备生成器
        from src.agent.generate import ImageGenerator
        self.current_generator = ImageGenerator()
        # 连接信号到你之前定义的 handle_layered_generation
        self.current_generator.layered_generation_finished.connect(self.handle_layered_generation)

        # 4. 开始推理
        print("AI 正在解析图层，请稍候...")
        self.current_generator.generate_layered(prompt=text, input_image=input_pil, num_layers=4)

    def handle_layered_generation(self, images, names, error_msg):
        """
        回调槽函数：在主线程中执行，处理生成的图片并转为 OpenGL 图层
        """
        if error_msg:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "生成失败", error_msg)
            return

        if not images:
            return

        print(f"收到 {len(images)} 个图层，正在导入工作流...")

        # --- 核心步骤 ---
        
        # 1. 激活当前 OpenGL 上下文 (非常重要！)
        # 因为 ProjectLogic.create_group_from_images 内部会调用 load_from_image (含 glGenTextures)
        self.makeCurrent()

        try:
            # 2. 调用你在 logic.py 中定义的静态方法
            # 这会把 PIL 序列转换成包含多个 PaintLayer 的 GroupLayer
            ai_group = ProjectLogic.create_group_from_images(
                images, 
                names, 
                self.doc_width, 
                self.doc_height
            )

            # 3. 插入工作流：将其挂载到图层树的根节点
            self.root.add_child(ai_group)

            # 4. 交互优化：激活新生成的组中最后一个图层（最顶层）
            if ai_group.children:
                self.active_layer = ai_group.children[-1]

            # 5. 通知 UI 系统更新
            self.layer_structure_changed.emit() # 通知图层面板刷新列表
            self.view_changed.emit()            # 触发画布重绘
            self.update()                       # 强制 QOpenGLWidget 刷新
            
            print("分层图像已成功插入工作流。")

        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "导入错误", f"处理 AI 图层时出错: {str(e)}")

    # ── Inpaint / Image-Edit ─────────────────────────────

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
        """Launch Wanx inpaint: selection mask + prompt → local repaint."""
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

        # Convert RGBA → RGB for the API (white background)
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
        self._inpaint_thread = WanxInpaintThread(base_rgb, mask_rgb, prompt_to_send)
        self._inpaint_thread.progress.connect(self._on_inpaint_progress)
        self._inpaint_thread.finished.connect(self._on_wanx_inpaint_finished)
        self._show_inpaint_progress("Wanx Inpaint", prompt_to_send, base_rgb, mask)
        self._inpaint_thread.start()

    def start_qwen_edit(self):
        """Launch Qwen image edit: prompt-only whole-image edit.

        Always sends the full canvas-sized image (doc_width × doc_height).
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

        # Convert full canvas RGBA → RGB (white background) for the API
        if base_img.mode == "RGBA":
            bg = Image.new("RGB", base_img.size, (255, 255, 255))
            bg.paste(base_img, mask=base_img.split()[3])
            send_rgb = bg
        else:
            send_rgb = base_img.convert("RGB")

        from src.agent.inpaint_service import QwenEditThread
        self._inpaint_old_img = base_img.copy()
        self._inpaint_remove_white_bg = remove_bg
        self._inpaint_thread = QwenEditThread(send_rgb, prompt.strip())
        self._inpaint_thread.progress.connect(self._on_inpaint_progress)
        self._inpaint_thread.finished.connect(self._on_qwen_edit_finished)
        self._show_inpaint_progress("Qwen Image Edit", prompt.strip(), send_rgb, None)
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
            # Threshold to binary – any feathered pixel > 0 becomes part of the mask.
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
        self._progress_status = QLabel("Preparing…")
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

        self._apply_qwen_result(result_img)

    def _apply_qwen_result(self, result_img):
        """Apply Qwen-edited image back to the active layer (full canvas size)."""
        if not self.active_layer or not isinstance(self.active_layer, PaintLayer):
            return

        self.makeCurrent()

        old_img = getattr(self, '_inpaint_old_img', self.active_layer.get_image())

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

        old_img = getattr(self, '_inpaint_old_img', self.active_layer.get_image())

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

    def open_img(self, path):
        try:
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
        except Exception as e:
            print(f"Error importing image: {e}")

    def import_psd(self, path):
        try:
            self.makeCurrent()
            width, height, root = ProjectLogic.import_psd(path, self.doc_width, self.doc_height)
            self.doc_width = width; self.doc_height = height; self.root = root
            self.layer_structure_changed.emit(); self.update(); self.view_changed.emit()
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
        print("开始渲染导出...")
        def debug_nodes(node):
            if isinstance(node, PaintLayer):
                print(f"正在渲染层: {node.name}, 纹理ID: {node.texture}, 可见性: {node.visible}")
            if hasattr(node, 'children'):
                for c in node.children: debug_nodes(c)
        debug_nodes(self.root)
        self._render_node(self.root)
        data = glReadPixels(0, 0, self.doc_width, self.doc_height, GL_RGBA, GL_UNSIGNED_BYTE)
        Image.frombytes("RGBA", (self.doc_width, self.doc_height), data).transpose(Image.FLIP_TOP_BOTTOM).save(path)
        glDeleteFramebuffers(1, [fbo]); glDeleteTextures([tex]); glBindFramebuffer(GL_FRAMEBUFFER, 0)

    def export_to_psd(self, path):
        self.makeCurrent()
        glPixelStorei(GL_PACK_ALIGNMENT, 1)

        def process_node(node):
            # 调试：看看遍历到了什么
            print(f"正在处理节点: {node.name}, 类型: {type(node)}")
            type_name = type(node).__name__

            if "GroupLayer" in type_name:
                sub_layers = []
                for child in node.children:
                    res = process_node(child)
                    if res: sub_layers.append(res)
                
                group = nested_layers.Group()
                group.name = node.name
                group.layers = sub_layers
                group.closed = False
                group.opacity = int(node.opacity * 255)
                group.visible = node.visible
                return group

            elif isinstance(node, PaintLayer):
                if not node.texture: return None
                
                glBindTexture(GL_TEXTURE_2D, node.texture)
                raw_data = glGetTexImage(GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (node.width, node.height), raw_data)
                img = img.transpose(Image.FLIP_TOP_BOTTOM)
                
                bbox = img.getbbox()
                if not bbox:
                    # 遇到完全空白的图层，直接跳过，防止 pytoshop 崩溃
                    return None

                img = img.crop(bbox)
                left, top, right, bottom = bbox[0], bbox[1], bbox[2], bbox[3]
                img_np = np.array(img)
                
                channels = {
                    0: img_np[:, :, 0], 
                    1: img_np[:, :, 1], 
                    2: img_np[:, :, 2], 
                    -1: img_np[:, :, 3]  
                }
                layer = nested_layers.Image(
                    name=node.name,
                    top=top,
                    left=left,
                    bottom=bottom,
                    right=right,
                    channels=channels,
                    opacity=int(node.opacity * 255),
                    visible=node.visible,
                    color_mode=enums.ColorMode.rgb
                )
                layer.mask = None
                return layer
            return None
        all_layers = []
        for child in reversed(self.root.children):
            layer_obj = process_node(child)
            if layer_obj:
                all_layers.append(layer_obj)

        if not all_layers:
            print("拦截：画布完全为空，没有像素可以导出。")
            return
        try:
            psd = nested_layers.nested_layers_to_psd(all_layers, color_mode=enums.ColorMode.rgb)
            
            with open(path, 'wb') as f:
                psd.write(f)
            print(f"PSD 成功导出！包含图层组结构：{path}")
        except Exception as e:
            print(f"pytoshop 最终合并失败: {e}")

    def set_brush(self, config):
        self.current_brush = config
        self._update_brush_texture()
        if self.active_tool:
            self.active_tool.deactivate()
            self.active_tool = None
        # Switching to a brush means the user wants to paint — clear selection entirely
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
    def open_img(self,path): self.gl_canvas.open_img(path)
    def save_project(self, path): self.gl_canvas.save_project(path)
    def export_image(self, path): self.gl_canvas.export_image(path)
    def export_psd(self, path): self.gl_canvas.export_to_psd(path)
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
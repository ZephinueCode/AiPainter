# src/__main__.py

import sys
import os

# Pre-import torch before any PyQt6/OpenGL imports.
# On Windows, Qt's OpenGL initialization changes the DLL loader state,
# which prevents torch's c10.dll from initializing (WinError 1114).
# Importing torch first ensures c10.dll is loaded while the DLL state is clean.
try:
    import torch  # noqa: F401
except ImportError:
    pass

from PyQt6.QtWidgets import (QApplication, QMainWindow, QDockWidget, QFileDialog, QMessageBox)
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QAction, QPalette, QColor, QFont, QImage, QKeySequence

from src.core.brush_manager import BrushManager
from src.gui.canvas import CanvasWidget
from src.gui.panels import LeftSidebar, LayerPanel, PropertyPanel
from src.gui.dialogs import SettingsDialog, CanvasSizeDialog, AIGenerateDialog
from src.gui.widgets import GeneratorStatusWidget, ChatPanelWidget
from src.agent.agent_manager import AIAgentManager 
from src.agent.generate import ImageGenerator
from src.agent.chat_service import ChatRequestThread
from src.core.logic import PaintLayer # For adding new layer
from src.core.tools import ClipboardUtils, MagicWandTool
from PIL import Image
import io

# High DPI Scaling
if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

#Qwen-Image-Layered API
os.environ["REPLICATE_API_TOKEN"] = "r8_HKGBCxZipa0jGm7Ey3nQIaZ5xBs0kNN0Cf55i"

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AiPainter V0.1")
        self.resize(1600, 900)
        self.gen_status = None
        
        self.agent_manager = AIAgentManager()
        self.ui_scale = 1.0
        
        # Apply theme first
        set_light_theme(QApplication.instance(), self.ui_scale)
        
        self.brush_manager = BrushManager()
        self.canvas = CanvasWidget()
        self.setCentralWidget(self.canvas)
        
        self.create_actions()
        self.create_menubar()
        self.create_docks()
        
        # AI Generator Backend
        self.generator = ImageGenerator()
        self.generator.generation_finished.connect(self.on_generation_finished)
        
        # AI Status Widget (Floating in bottom left)
        # Create as child of MainWindow so it floats above central widget
        self.gen_status = GeneratorStatusWidget(self)
        self.gen_status.copyRequested.connect(self.copy_generated_image)
        self.gen_status.addLayerRequested.connect(self.add_generated_layer)
        self.gen_status.hide()
        self._chat_thread = None
        self._chat_history = []
        
        self.statusBar().showMessage("Ready")

    def resizeEvent(self, event):
        if event is not None:
            super().resizeEvent(event)
        if self.gen_status is None or not hasattr(self, "dock_left"):
            return
        # Position status widget at bottom left, above status bar
        # Adjust as needed based on layout
        margin = 20
        # Calculate X based on left dock width if visible
        x = margin
        if self.dock_left.isVisible():
            x += self.dock_left.width()
            
        y = self.height() - self.gen_status.height() - margin - 30 # 30 for status bar
        self.gen_status.move(x, y)

    def on_open_generator_dialog(self):
        dlg = AIGenerateDialog(self)
        # Connect generation request
        dlg.generationRequested.connect(self.start_generation)
        dlg.exec()

    def on_auto_sketch(self):
        self.canvas.gl_canvas.start_auto_sketch()

    def on_auto_color(self):
        opts = self.left_sidebar.ai_panel.get_auto_color_options()
        self.canvas.gl_canvas.start_auto_color(
            color_pref=opts.get("color", ""),
            effect_pref=opts.get("effect", ""),
            material_pref=opts.get("material", ""),
        )

    def on_auto_optimize(self):
        self.canvas.gl_canvas.start_auto_optimize()

    def on_auto_resolution(self):
        self.canvas.gl_canvas.start_auto_resolution()

    def _layer_panel_has_focus(self):
        fw = QApplication.focusWidget()
        if fw is None:
            return False
        return (
            fw is self.layer_panel.tree
            or self.layer_panel.tree.isAncestorOf(fw)
            or self.layer_panel.isAncestorOf(fw)
        )

    def _forward_text_shortcut(self, method_name):
        fw = QApplication.focusWidget()
        if fw is None:
            return False
        method = getattr(fw, method_name, None)
        if callable(method):
            try:
                method()
                return True
            except Exception:
                return False
        return False

    def on_copy(self):
        if self._layer_panel_has_focus():
            self.layer_panel.copy_selected_node()
        elif self._forward_text_shortcut("copy"):
            return
        else:
            ClipboardUtils.copy(self.canvas.gl_canvas)

    def on_cut(self):
        if self._layer_panel_has_focus():
            self.layer_panel.cut_selected_node()
        elif self._forward_text_shortcut("cut"):
            return
        else:
            ClipboardUtils.cut(self.canvas.gl_canvas)

    def on_paste(self):
        if self._layer_panel_has_focus():
            self.layer_panel.paste_node()
        elif self._forward_text_shortcut("paste"):
            return
        else:
            ClipboardUtils.paste(self.canvas.gl_canvas)

    def on_undo(self):
        if self._forward_text_shortcut("undo"):
            return
        if self._dispatch_magic_wand_history(redo=False):
            return
        self.canvas.gl_canvas.perform_undo()

    def on_redo(self):
        if self._forward_text_shortcut("redo"):
            return
        if self._dispatch_magic_wand_history(redo=True):
            return
        self.canvas.gl_canvas.perform_redo()

    def _dispatch_magic_wand_history(self, redo=False):
        """When Magic Wand is active, undo/redo should operate on wand points first."""
        gl = getattr(self.canvas, "gl_canvas", None)
        if gl is None:
            return False
        tool = getattr(gl, "active_tool", None)
        if not isinstance(tool, MagicWandTool):
            return False
        if redo:
            if hasattr(tool, "redo_last_point"):
                tool.redo_last_point()
                gl.update()
                return True
            return False
        if hasattr(tool, "undo_last_point"):
            tool.undo_last_point()
            gl.update()
            return True
        return False

    def _clear_chat_history(self):
        self._chat_history = []

    def _on_chat_progress(self, msg):
        if hasattr(self, "chat_panel") and self.chat_panel:
            self.chat_panel.set_busy(True, msg)

    def _on_chat_finished(self, assistant_text, error):
        if hasattr(self, "chat_panel") and self.chat_panel:
            self.chat_panel.set_busy(False, "")

        if error:
            QMessageBox.warning(self, "Chat Error", error)
            return

        if assistant_text:
            self._chat_history.append({"role": "assistant", "text": assistant_text})
            self.chat_panel.append_assistant(assistant_text)
            self.statusBar().showMessage("Chat response received.", 3000)
            if len(self._chat_history) > 40:
                self._chat_history = self._chat_history[-40:]

    def on_chat_send(self, text, include_visible_image):
        text = (text or "").strip()
        if not text:
            return
        if self._chat_thread and self._chat_thread.isRunning():
            self.statusBar().showMessage("Chat request is still running...", 3000)
            return

        visible_img = None
        if include_visible_image:
            visible_img = self.canvas.capture_visible_image()
            if visible_img is None:
                QMessageBox.warning(self, "Chat", "Failed to capture visible image.")
                return

        self.chat_panel.append_user(text, with_image=include_visible_image)
        self.chat_panel.clear_input()
        self.chat_panel.set_busy(True, "Preparing chat request...")
        self._chat_history.append({"role": "user", "text": text})
        if len(self._chat_history) > 40:
            self._chat_history = self._chat_history[-40:]

        self._chat_thread = ChatRequestThread(
            user_text=text,
            history=self._chat_history,
            include_image=include_visible_image,
            visible_image=visible_img,
            parent=self,
        )
        self._chat_thread.progress.connect(self._on_chat_progress)
        self._chat_thread.finished.connect(self._on_chat_finished)
        self._chat_thread.start()
        
    def start_generation(self, prompt, neg, size):
        self.gen_status.start_loading()
        # Force re-layout to ensure size is correct before moving
        self.gen_status.adjustSize()
        self.resizeEvent(None) 
        
        self.generator.generate(prompt, neg, size)
        
    def on_generation_finished(self, qimage, error_msg):
        if error_msg:
            self.gen_status.show_error(error_msg)
        else:
            self.gen_status.finish_loading(qimage)
        # Re-position after size change
        self.resizeEvent(None)

    def copy_generated_image(self, qimage):
        QApplication.clipboard().setImage(qimage)
        self.statusBar().showMessage("Image copied to clipboard.")

    def add_generated_layer(self, qimage):
        before_state = self.canvas.gl_canvas.begin_history_action()
        # Convert QImage to PIL
        from PyQt6.QtCore import QBuffer, QIODevice
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.ReadWrite)
        qimage.save(buffer, "PNG")
        pil_img = Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")
        
        # Create new layer
        w, h = self.canvas.doc_width, self.canvas.doc_height
        new_layer = PaintLayer(w, h, "AI Generated")
        
        # Center image
        img_w, img_h = pil_img.size
        cx = (w - img_w) // 2
        cy = (h - img_h) // 2
        
        full_img = Image.new("RGBA", (w, h), (0,0,0,0))
        full_img.paste(pil_img, (cx, cy))
        new_layer.load_from_image(full_img)
        
        # Add to root (simple)
        self.canvas.root.add_child(new_layer)
        self.canvas.active_layer = new_layer
        self.canvas.layer_structure_changed.emit()
        self.canvas.update()
        self.canvas.gl_canvas.end_history_action(before_state, "Insert AI Generated Layer")
        self.statusBar().showMessage("Image added as new layer.")

    def apply_ui_scale(self):
        font = QApplication.font()
        font.setPointSize(int(10 * self.ui_scale))
        QApplication.setFont(font)

    def create_actions(self):
        self.act_new = QAction("New Project...", self)
        self.act_new.triggered.connect(self.on_new_project)
        
        self.act_save_proj = QAction("Save Project (.glp)...", self)
        self.act_save_proj.triggered.connect(self.on_save_project)
        
        self.act_open_proj = QAction("Open Project (.glp)...", self)
        self.act_open_proj.triggered.connect(self.on_open_project)

        self.act_open_img = QAction("Open Image ...",self)
        self.act_open_img.triggered.connect(self.on_open_image)
        
        self.act_import = QAction("Import PSD...", self)
        self.act_import.triggered.connect(self.on_import_psd)
        
        self.act_export_flat = QAction("Export Flat Image...", self)
        self.act_export_flat.triggered.connect(self.on_export_flat)

        self.act_export_psd = QAction("Export PSD...", self)
        self.act_export_psd.triggered.connect(self.on_export_psd)
        
        self.act_settings = QAction("Settings...", self)
        self.act_settings.triggered.connect(self.on_settings)
        
        # Edit Actions
        self.act_undo = QAction("Undo", self)
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_undo.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.act_undo.triggered.connect(self.on_undo)

        self.act_redo = QAction("Redo", self)
        self.act_redo.setShortcuts([
            QKeySequence("Ctrl+Shift+Z"),
            QKeySequence("Ctrl+Y"),
        ])
        self.act_redo.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.act_redo.triggered.connect(self.on_redo)

        self.act_copy = QAction("Copy", self)
        self.act_copy.setShortcut(QKeySequence.StandardKey.Copy)
        self.act_copy.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.act_copy.triggered.connect(self.on_copy)

        self.act_cut = QAction("Cut", self)
        self.act_cut.setShortcut(QKeySequence.StandardKey.Cut)
        self.act_cut.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.act_cut.triggered.connect(self.on_cut)

        self.act_paste = QAction("Paste", self)
        self.act_paste.setShortcut(QKeySequence.StandardKey.Paste)
        self.act_paste.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
        self.act_paste.triggered.connect(self.on_paste)

        self.act_hsl = QAction("HSL Adjustment...", self)
        self.act_hsl.triggered.connect(lambda: self.canvas.gl_canvas.open_adjustment("HSL"))
        
        self.act_contrast = QAction("Contrast...", self)
        self.act_contrast.triggered.connect(lambda: self.canvas.gl_canvas.open_adjustment("Contrast"))
        
        self.act_exposure = QAction("Exposure...", self)
        self.act_exposure.triggered.connect(lambda: self.canvas.gl_canvas.open_adjustment("Exposure"))
        
        self.act_blur = QAction("Gaussian Blur...", self)
        self.act_blur.triggered.connect(lambda: self.canvas.gl_canvas.open_adjustment("Blur"))

        # === Add Gradient Map Action ===
        self.act_grad_map = QAction("Gradient Map...", self)
        self.act_grad_map.triggered.connect(lambda: self.canvas.gl_canvas.open_gradient_map())

    def create_menubar(self):
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        file_menu.addAction(self.act_new)
        file_menu.addAction(self.act_open_proj)
        file_menu.addSeparator()
        file_menu.addAction(self.act_save_proj)
        file_menu.addSeparator()
        file_menu.addAction(self.act_import)
        file_menu.addAction(self.act_open_img) # jpg & png
        file_menu.addAction(self.act_export_flat)
        file_menu.addAction(self.act_export_psd)
        file_menu.addSeparator()
        file_menu.addAction(self.act_settings)
        
        edit_menu = bar.addMenu("&Edit")
        edit_menu.addAction(self.act_undo)
        edit_menu.addAction(self.act_redo)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_copy)
        edit_menu.addAction(self.act_cut)
        edit_menu.addAction(self.act_paste)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_hsl)
        edit_menu.addAction(self.act_contrast)
        edit_menu.addAction(self.act_exposure)
        edit_menu.addAction(self.act_blur)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_grad_map) # Added here

    def create_docks(self):
        self.dock_left = QDockWidget("Tools", self)
        self.dock_left.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        
        self.left_sidebar = LeftSidebar(
            self.brush_manager, 
            self.canvas.set_brush, 
            self.canvas.set_tool,
            self.canvas
        )
        
        # Hook up the AI button manually
        self.left_sidebar.ai_panel.btn_generate.disconnect() 
        self.left_sidebar.ai_panel.btn_generate.clicked.connect(self.on_open_generator_dialog)
        self.left_sidebar.ai_panel.btn_auto_sketch.clicked.connect(self.on_auto_sketch)
        self.left_sidebar.ai_panel.btn_auto_color.clicked.connect(self.on_auto_color)
        self.left_sidebar.ai_panel.btn_auto_optimize.clicked.connect(self.on_auto_optimize)
        self.left_sidebar.ai_panel.btn_auto_resolution.clicked.connect(self.on_auto_resolution)

        self.dock_left.setWidget(self.left_sidebar)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_left)
        
        self.dock_layer = QDockWidget("Layers", self)
        self.layer_panel = LayerPanel(self.canvas)
        self.dock_layer.setWidget(self.layer_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_layer)
        
        self.dock_prop = QDockWidget("Properties", self)
        self.prop_panel = PropertyPanel(self.canvas)
        self.dock_prop.setWidget(self.prop_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_prop)

        self.dock_chat = QDockWidget("Chat", self)
        self.dock_chat.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.chat_panel = ChatPanelWidget(self)
        self.chat_panel.sendRequested.connect(self.on_chat_send)
        self.chat_panel.btn_clear.clicked.connect(self._clear_chat_history)
        self.dock_chat.setWidget(self.chat_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_chat)
        self.dock_chat.setFloating(True)
        self.dock_chat.hide()
        self.dock_chat.toggleViewAction().setText("Chat Page")

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.dock_left.toggleViewAction())
        view_menu.addAction(self.dock_layer.toggleViewAction())
        view_menu.addAction(self.dock_prop.toggleViewAction())
        view_menu.addAction(self.dock_chat.toggleViewAction())

    def on_new_project(self):
        dlg = CanvasSizeDialog(self)
        if dlg.exec():
            vals = dlg.get_values()
            self.canvas.doc_width = vals['width']
            self.canvas.doc_height = vals['height']
            self.canvas.root.children = []
            self.canvas.initializeGL()
            self.canvas.update()

    def on_save_project(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Project", "", "GL Project (*.glp)")
        if path:
            if not path.endswith(".glp"):
                path += ".glp"
            self.canvas.save_project(path)
            self.statusBar().showMessage(f"Project saved: {path}")

    def on_open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", "GL Project (*.glp)")
        if path:
            self.canvas.load_project(path)
            self.statusBar().showMessage(f"Project loaded: {path}")

    def on_open_image(self):
        filters = "Image Files (*.jpg *.jpeg *.png);;PNG Files (*.png);;JPEG Files (*.jpg *.jpeg);;All Files (*)"
        paths, _ = QFileDialog.getOpenFileNames(self, "Import Image", "", filters)
        for path in paths:
            self.canvas.open_img(path)


    def on_import_psd(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import PSD", "", "PSD Files (*.psd);;All Files (*)")
        if path:
            self.canvas.import_psd(path)

    def on_export_flat(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Flat Image", "", "PNG Files (*.png);;JPEG Files (*.jpg)")
        if path:
            if not path.endswith(".png") and not path.endswith(".jpg"):
                path += ".png"
            self.canvas.export_image(path)
            self.statusBar().showMessage(f"Exported to {path}")
    
    def on_export_psd(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export PSD", "", "PSD Files (*.psd);;All Files (*)")
        if path:
            self.canvas.export_psd(path)
            self.statusBar().showMessage(f"Exported to {path}")

    def on_settings(self):
        dlg = SettingsDialog(self, 
                             self.canvas.doc_width, 
                             self.canvas.doc_height,
                             self.ui_scale)
        if dlg.exec():
            vals = dlg.get_values()
            
            if vals['width'] != self.canvas.doc_width or vals['height'] != self.canvas.doc_height:
                self.canvas.resize_canvas_smart(vals['width'], vals['height'], vals['anchor'])
            
            if vals['ui_scale'] != self.ui_scale:
                self.ui_scale = vals['ui_scale']
                set_light_theme(QApplication.instance(), self.ui_scale)
                self.statusBar().showMessage(f"Settings applied. Scale: {self.ui_scale}x")

def set_light_theme(app, scale=1.0):
    # Base font size 
    base_size = int(10 * scale)
    base_padding = int(5 * scale)
    base_radius = int(3 * scale)
    
    font = QFont("Segoe UI", base_size)
    app.setFont(font)

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(245, 245, 245))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Text, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(0, 0, 0))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
    palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)
    
    # Dynamic Stylesheet based on scale
    app.setStyleSheet(f"""
        QMainWindow, QDialog, QDockWidget {{
            background-color: #f0f0f0;
            color: #000000;
        }}
        QWidget {{
            color: #000000;
            font-size: {base_size}pt;
        }}
        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
            background-color: #ffffff;
            color: #000000;
            border: 1px solid #c0c0c0;
            selection-background-color: #2a82da;
            selection-color: #ffffff;
            padding: {base_padding}px;
        }}
        QListWidget, QTreeWidget, QTableWidget {{
            background-color: #ffffff;
            color: #000000;
            border: 1px solid #c0c0c0;
            selection-background-color: #2a82da;
            selection-color: #ffffff;
        }}
        QPushButton {{
            background-color: #e0e0e0;
            border: 1px solid #c0c0c0;
            padding: {base_padding}px;
            border-radius: {base_radius}px;
        }}
        QPushButton:hover {{
            background-color: #d0d0d0;
        }}
        QPushButton:pressed {{
            background-color: #c0c0c0;
        }}
        QMenuBar, QMenu {{
            background-color: #f0f0f0;
            color: #000000;
        }}
        QMenuBar::item:selected, QMenu::item:selected {{
            background-color: #2a82da;
            color: #ffffff;
        }}
        QLabel {{
            color: #000000;
        }}
        QGroupBox {{
            border: 1px solid #c0c0c0;
            margin-top: {base_padding * 2}px;
            padding-top: {base_padding}px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 {base_padding}px;
        }}
        QScrollBar:vertical {{
            background: #f0f0f0;
            width: {int(12 * scale)}px;
        }}
        QScrollBar::handle:vertical {{
            background: #cdcdcd;
            min-height: {int(20 * scale)}px;
        }}
        QScrollBar:horizontal {{
            background: #f0f0f0;
            height: {int(12 * scale)}px;
        }}
        QScrollBar::handle:horizontal {{
            background: #cdcdcd;
            min-width: {int(20 * scale)}px;
        }}
    """)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow() # Theme set inside ctor now
    window.show()
    sys.exit(app.exec())

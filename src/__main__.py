# src/__main__.py

import sys
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QDockWidget, QFileDialog, QMessageBox)
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QAction, QPalette, QColor, QFont, QImage

from src.core.brush_manager import BrushManager
from src.gui.canvas import CanvasWidget
from src.gui.panels import LeftSidebar, LayerPanel, PropertyPanel
from src.gui.dialogs import SettingsDialog, CanvasSizeDialog, AIGenerateDialog
from src.gui.widgets import GeneratorStatusWidget
from src.agent.agent_manager import AIAgentManager 
from src.agent.generate import ImageGenerator
from src.core.logic import PaintLayer # For adding new layer
from PIL import Image
import io

# High DPI Scaling
if hasattr(Qt.ApplicationAttribute, 'AA_EnableHighDpiScaling'):
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
if hasattr(Qt.ApplicationAttribute, 'AA_UseHighDpiPixmaps'):
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GL Paint Pro")
        self.resize(1600, 900)
        
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
        
        self.statusBar().showMessage("Ready")

    def resizeEvent(self, event):
        super().resizeEvent(event)
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
        
        self.act_import = QAction("Import PSD...", self)
        self.act_import.triggered.connect(self.on_import_psd)
        
        self.act_export_flat = QAction("Export Flat Image...", self)
        self.act_export_flat.triggered.connect(self.on_export_flat)
        
        self.act_settings = QAction("Settings...", self)
        self.act_settings.triggered.connect(self.on_settings)
        
        # Edit Actions
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
        file_menu.addAction(self.act_export_flat)
        file_menu.addSeparator()
        file_menu.addAction(self.act_settings)
        
        edit_menu = bar.addMenu("&Edit")
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
            self.canvas.set_tool
        )
        
        # Hook up the AI button manually
        self.left_sidebar.ai_panel.btn_generate.disconnect() 
        self.left_sidebar.ai_panel.btn_generate.clicked.connect(self.on_open_generator_dialog)

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

        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.dock_left.toggleViewAction())
        view_menu.addAction(self.dock_layer.toggleViewAction())
        view_menu.addAction(self.dock_prop.toggleViewAction())

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
            self.canvas.save_project(path)
            self.statusBar().showMessage(f"Project saved: {path}")

    def on_open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", "GL Project (*.glp)")
        if path:
            self.canvas.load_project(path)
            self.statusBar().showMessage(f"Project loaded: {path}")

    def on_import_psd(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import PSD", "", "PSD Files (*.psd);;All Files (*)")
        if path:
            self.canvas.import_psd(path)

    def on_export_flat(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Flat Image", "", "PNG Files (*.png);;JPEG Files (*.jpg)")
        if path:
            self.canvas.export_image(path)
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
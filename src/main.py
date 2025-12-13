# src/main.py

import sys
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QDockWidget, QFileDialog, QMessageBox)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QPalette, QColor

from src.core.brush_manager import BrushManager
from src.gui.canvas import CanvasWidget
from src.gui.panels import LeftSidebar, LayerPanel, PropertyPanel
from src.gui.dialogs import SettingsDialog, CanvasSizeDialog
from src.agent.agent_manager import AIAgentManager # Initialize AI manager early

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
        
        # Init Manager
        self.agent_manager = AIAgentManager()
        
        # Set default UI scale to 1.5
        self.ui_scale = 1.5
        self.apply_ui_scale()
        
        self.brush_manager = BrushManager()
        self.canvas = CanvasWidget()
        self.setCentralWidget(self.canvas)
        
        self.create_actions()
        self.create_menubar()
        self.create_docks()
        
        self.statusBar().showMessage("Ready")

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

    def create_docks(self):
        self.dock_left = QDockWidget("Tools", self)
        self.dock_left.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.left_sidebar = LeftSidebar(
            self.brush_manager, 
            self.canvas.set_brush, 
            self.canvas.set_tool
        )
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
                self.apply_ui_scale()
                self.statusBar().showMessage(f"Settings applied. Scale: {self.ui_scale}x")

def set_light_theme(app):
    # Force light theme palette
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
    
    # Additionally set stylesheet to ensure widgets conform
    app.setStyleSheet("""
        QMainWindow, QDialog, QDockWidget {
            background-color: #f0f0f0;
            color: #000000;
        }
        QWidget {
            color: #000000;
        }
        QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {
            background-color: #ffffff;
            color: #000000;
            border: 1px solid #c0c0c0;
            selection-background-color: #2a82da;
            selection-color: #ffffff;
        }
        QListWidget, QTreeWidget, QTableWidget {
            background-color: #ffffff;
            color: #000000;
            border: 1px solid #c0c0c0;
            selection-background-color: #2a82da;
            selection-color: #ffffff;
        }
        QPushButton {
            background-color: #e0e0e0;
            border: 1px solid #c0c0c0;
            padding: 5px;
            border-radius: 3px;
        }
        QPushButton:hover {
            background-color: #d0d0d0;
        }
        QPushButton:pressed {
            background-color: #c0c0c0;
        }
        QMenuBar, QMenu {
            background-color: #f0f0f0;
            color: #000000;
        }
        QMenuBar::item:selected, QMenu::item:selected {
            background-color: #2a82da;
            color: #ffffff;
        }
        QLabel {
            color: #000000;
        }
        QGroupBox {
            border: 1px solid #c0c0c0;
            margin-top: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 3px;
        }
        QScrollBar:vertical {
            background: #f0f0f0;
            width: 12px;
        }
        QScrollBar::handle:vertical {
            background: #cdcdcd;
            min-height: 20px;
        }
        QScrollBar:horizontal {
            background: #f0f0f0;
            height: 12px;
        }
        QScrollBar::handle:horizontal {
            background: #cdcdcd;
            min-width: 20px;
        }
    """)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Apply Light Theme
    set_light_theme(app)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
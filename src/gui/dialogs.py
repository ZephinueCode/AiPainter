# src/gui/dialogs.py

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QSpinBox, 
                             QDialogButtonBox, QTabWidget, QWidget, QDoubleSpinBox, 
                             QLabel, QGridLayout, QPushButton, QButtonGroup, 
                             QLineEdit, QMessageBox, QTextEdit, QHBoxLayout, QApplication)
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage
import json
import os
from src.agent.agent_manager import AIAgentManager
from src.gui.widgets import GradientSlider, ColorPickerWidget
from src.core.processor import ImageProcessor

# === Gradient Map Dialog ===
class GradientMapDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Gradient Map")
        self.resize(300, 500)
        
        self.preview_layer = parent.active_layer
        self.original_img = self.preview_layer.get_image()
        
        layout = QVBoxLayout(self)
        
        # Color Picker Area
        self.color_picker = ColorPickerWidget()
        layout.addWidget(self.color_picker)
        
        # Gradient Slider
        self.slider = GradientSlider()
        layout.addWidget(self.slider)
        
        # Connections
        self.slider.stopSelected.connect(self.color_picker.set_color)
        self.slider.gradientChanged.connect(self.on_gradient_changed)
        self.color_picker.colorChanged.connect(self.slider.set_current_stop_color)
        
        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        
        # Initial apply
        self.on_gradient_changed(self.slider.stops)

    def on_gradient_changed(self, stops):
        # stops: [[pos, [r,g,b]], ...]
        # Convert to tuple format for processor
        proc_stops = []
        for s in stops:
            proc_stops.append((s[0], (s[1][0], s[1][1], s[1][2])))
            
        new_img = ImageProcessor.apply_gradient_map(self.original_img, proc_stops)
        self.preview_layer.load_from_image(new_img)
        self.parent().update()

    def reject(self):
        self.preview_layer.load_from_image(self.original_img)
        self.parent().update()
        super().reject()

# === Anchor Selection Widget (for Canvas Resize) ===
class AnchorWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QGridLayout(self)
        self.layout.setSpacing(2)
        self.btn_group = QButtonGroup(self)
        self.buttons = []
        
        self.anchor_val = (0.5, 0.5) # Default Center

        positions = [(r, c) for r in range(3) for c in range(3)]
        
        for r, c in positions:
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setFixedSize(30, 30)
            btn.setStyleSheet("""
                QPushButton { background-color: #ddd; border: 1px solid #999; }
                QPushButton:checked { background-color: #444; border: 1px solid #000; }
            """)
            self.layout.addWidget(btn, r, c)
            self.btn_group.addButton(btn, r * 3 + c)
            
            # Center button checked by default
            if r == 1 and c == 1:
                btn.setChecked(True)

        self.btn_group.idClicked.connect(self._on_click)

    def _on_click(self, id):
        r, c = divmod(id, 3)
        # Map 0,1,2 to 0.0, 0.5, 1.0
        y = r / 2.0
        x = c / 2.0
        self.anchor_val = (x, y)

    def get_anchor(self):
        return self.anchor_val

# === Global Settings Dialog ===
class SettingsDialog(QDialog):
    def __init__(self, parent=None, current_width=1920, current_height=1080, current_scale=1.5):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(500, 450) 
        
        self.agent_manager = AIAgentManager()
        
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        
        # --- Tab 1: Image (Canvas) ---
        self.tab_image = QWidget()
        layout_img = QVBoxLayout(self.tab_image)
        
        form_img = QFormLayout()
        self.spin_w = QSpinBox(); self.spin_w.setRange(1, 16384); self.spin_w.setValue(current_width)
        self.spin_h = QSpinBox(); self.spin_h.setRange(1, 16384); self.spin_h.setValue(current_height)
        form_img.addRow("Width (px):", self.spin_w)
        form_img.addRow("Height (px):", self.spin_h)
        layout_img.addLayout(form_img)
        
        layout_img.addWidget(QLabel("Anchor Point (Resize Direction):"))
        self.anchor_widget = AnchorWidget()
        layout_img.addWidget(self.anchor_widget, alignment=Qt.AlignmentFlag.AlignCenter)
        layout_img.addStretch()
        
        self.tabs.addTab(self.tab_image, "Canvas")

        # --- Tab 2: Interface ---
        self.tab_ui = QWidget()
        form_ui = QFormLayout(self.tab_ui)
        
        self.spin_ui_scale = QDoubleSpinBox()
        self.spin_ui_scale.setRange(0.5, 2.0)
        self.spin_ui_scale.setSingleStep(0.1)
        self.spin_ui_scale.setValue(current_scale)
        self.spin_ui_scale.setSuffix("x")
        
        form_ui.addRow("UI Scale (0.5 - 2.0):", self.spin_ui_scale)
        form_ui.addRow(QLabel("Note: Interface scaling may require \na restart or window resize to fully apply."))
        self.tab_ui.setLayout(form_ui)
        
        self.tabs.addTab(self.tab_ui, "Interface")
        
        # --- Tab 3: AI Settings ---
        self.tab_ai = QWidget()
        layout_ai = QVBoxLayout(self.tab_ai)
        form_ai = QFormLayout()
        
        self.txt_base_url = QLineEdit(self.agent_manager.base_url)
        self.txt_api_key = QLineEdit(self.agent_manager.api_key)
        self.txt_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_model = QLineEdit(self.agent_manager.model)
        self.txt_proxy = QLineEdit(self.agent_manager.proxy)
        self.txt_proxy.setPlaceholderText("e.g. http://127.0.0.1:7890")
        
        form_ai.addRow("Base URL:", self.txt_base_url)
        form_ai.addRow("API Key:", self.txt_api_key)
        form_ai.addRow("Model:", self.txt_model)
        form_ai.addRow("Proxy (Optional):", self.txt_proxy)
        layout_ai.addLayout(form_ai)
        
        btn_test = QPushButton("Test Connection")
        btn_test.clicked.connect(self.test_ai_connection)
        layout_ai.addWidget(btn_test)
        layout_ai.addStretch()
        
        self.tabs.addTab(self.tab_ai, "AI Settings")

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def test_ai_connection(self):
        # Update manager temp with current UI values for testing
        self.agent_manager.base_url = self.txt_base_url.text()
        self.agent_manager.api_key = self.txt_api_key.text()
        self.agent_manager.proxy = self.txt_proxy.text()
        self.agent_manager._init_client()
        
        success, msg = self.agent_manager.test_connection()
        if success:
            QMessageBox.information(self, "AI Connection", msg)
        else:
            QMessageBox.warning(self, "AI Connection Failed", msg)

    def accept(self):
        # Save AI settings on OK
        self.agent_manager.save_config(
            self.txt_base_url.text(),
            self.txt_api_key.text(),
            self.txt_model.text(),
            self.txt_proxy.text()
        )
        super().accept()

    def get_values(self):
        return {
            "width": self.spin_w.value(),
            "height": self.spin_h.value(),
            "anchor": self.anchor_widget.get_anchor(),
            "ui_scale": self.spin_ui_scale.value()
        }

# === New Project Dialog ===
class CanvasSizeDialog(QDialog):
    def __init__(self, parent=None, width=1920, height=1080):
        super().__init__(parent)
        self.setWindowTitle("New Project")
        layout = QVBoxLayout(self)
        
        form = QFormLayout()
        self.spin_w = QSpinBox(); self.spin_w.setRange(1, 16384); self.spin_w.setValue(width)
        self.spin_h = QSpinBox(); self.spin_h.setRange(1, 16384); self.spin_h.setValue(height)
        form.addRow("Width:", self.spin_w)
        form.addRow("Height:", self.spin_h)
        layout.addLayout(form)
        
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_values(self):
        return { "width": self.spin_w.value(), "height": self.spin_h.value() }

# === Custom Size Cycler Widget (Editable + Vertical Arrows) ===
class SizeCyclerWidget(QWidget):
    def __init__(self, label_text, parent=None):
        super().__init__(parent)
        # DashScope Supported Resolutions (Common ones)
        self.options = [512, 768, 1024, 1328, 1472, 1664]
        self.current_idx = 2 # Default 1024
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(5)
        
        layout.addWidget(QLabel(label_text))
        
        # Value Input (Editable)
        self.spin_val = QSpinBox()
        self.spin_val.setRange(64, 4096) 
        self.spin_val.setSingleStep(32)
        self.spin_val.setValue(self.options[self.current_idx])
        self.spin_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spin_val.setFixedWidth(120) 
        self.spin_val.setStyleSheet("""
            QSpinBox {
                background-color: white; 
                border: 1px solid #ccc; 
                border-radius: 3px; 
                padding: 3px;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.spin_val)
        
        # Vertical Buttons Container
        btn_container = QWidget()
        v_layout = QVBoxLayout(btn_container)
        v_layout.setContentsMargins(0,0,0,0)
        v_layout.setSpacing(0)
        
        layout.addWidget(btn_container)
        layout.addStretch()

    def next_val(self):
        curr = self.spin_val.value()
        next_opt = self.options[0]
        for opt in self.options:
            if opt > curr:
                next_opt = opt
                break
        self.spin_val.setValue(next_opt)

    def prev_val(self):
        curr = self.spin_val.value()
        prev_opt = self.options[-1]
        for opt in reversed(self.options):
            if opt < curr:
                prev_opt = opt
                break
        self.spin_val.setValue(prev_opt)

    def get_value(self):
        return self.spin_val.value()

# === AI Generation Dialog (Streamlined) ===
class AIGenerateDialog(QDialog):
    # Signals to Main Window
    generationRequested = pyqtSignal(str, str, str) # prompt, neg, size_str

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Image Generator")
        self.resize(400, 300) 
        
        layout = QVBoxLayout(self)
        
        # Prompt
        layout.addWidget(QLabel("Prompt:"))
        self.txt_prompt = QTextEdit()
        self.txt_prompt.setPlaceholderText("Describe the image you want to generate...")
        self.txt_prompt.setMaximumHeight(80)
        layout.addWidget(self.txt_prompt)
        
        # Negative Prompt
        layout.addWidget(QLabel("Negative Prompt:"))
        self.txt_negative = QTextEdit()
        self.txt_negative.setPlaceholderText("Things to avoid...")
        self.txt_negative.setMaximumHeight(60)
        layout.addWidget(self.txt_negative)
        
        # Size Selection (Custom Editable Cyclers)
        size_label = QLabel("Size Settings (WxH):")
        size_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(size_label)
        
        size_layout = QHBoxLayout()
        self.cycler_w = SizeCyclerWidget("W:")
        self.cycler_h = SizeCyclerWidget("H:")
        size_layout.addWidget(self.cycler_w)
        size_layout.addWidget(self.cycler_h)
        layout.addLayout(size_layout)
        
        layout.addStretch()
        
        # Actions
        btn_layout = QHBoxLayout()
        self.btn_generate = QPushButton("Start Generation")
        self.btn_generate.setStyleSheet("font-weight: bold; padding: 8px; background-color: #e0e0e0;")
        self.btn_generate.clicked.connect(self.on_start)
        
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        
        btn_layout.addWidget(self.btn_generate)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

    def on_start(self):
        prompt = self.txt_prompt.toPlainText().strip()
        negative = self.txt_negative.toPlainText().strip()
        
        w = self.cycler_w.get_value()
        h = self.cycler_h.get_value()
        size_str = f"{w}*{h}"
        
        if not prompt:
            QMessageBox.warning(self, "Error", "Please enter a prompt.")
            return
            
        # Emit signal to main window controller
        self.generationRequested.emit(prompt, negative, size_str)
        
        # Close dialog immediately
        self.accept()
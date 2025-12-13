# src/gui/dialogs.py

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QSpinBox, 
                             QDialogButtonBox, QTabWidget, QWidget, QDoubleSpinBox, 
                             QLabel, QGridLayout, QPushButton, QButtonGroup)
from PyQt6.QtCore import Qt

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
            
            if r == 1 and c == 1:
                btn.setChecked(True)

        self.btn_group.idClicked.connect(self._on_click)

    def _on_click(self, id):
        r, c = divmod(id, 3)
        y = r / 2.0
        x = c / 2.0
        self.anchor_val = (x, y)

    def get_anchor(self):
        return self.anchor_val

class SettingsDialog(QDialog):
    def __init__(self, parent=None, current_width=1920, current_height=1080, current_scale=1.5):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(400, 300)
        
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
        
        self.tabs.addTab(self.tab_ui, "Interface")

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_values(self):
        return {
            "width": self.spin_w.value(),
            "height": self.spin_h.value(),
            "anchor": self.anchor_widget.get_anchor(),
            "ui_scale": self.spin_ui_scale.value()
        }

class CanvasSizeDialog(SettingsDialog):
    pass
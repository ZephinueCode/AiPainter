# src/gui/dialogs.py

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QSpinBox, 
                             QDialogButtonBox, QTabWidget, QWidget, QDoubleSpinBox, 
                             QLabel, QGridLayout, QToolButton, QPushButton, QButtonGroup, 
                             QLineEdit, QMessageBox, QTextEdit, QHBoxLayout, QApplication, QSlider)
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage
import json
import os
from src.agent.agent_manager import AIAgentManager
from src.gui.widgets import GradientSlider, ColorPickerWidget
from src.core.processor import ImageProcessor

# === Adjustment Dialog (HSL, Contrast, etc.) ===
class AdjustmentDialog(QDialog):
    def __init__(self, parent, title, func, params):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(300, 150 + len(params) * 30)
        
        self.func = func
        self.params = params
        self.inputs = []
        
        # Capture original state for preview/cancel
        self.preview_layer = parent.active_layer
        self.original_img = self.preview_layer.get_image().copy()
        
        layout = QVBoxLayout(self)
        
        for p in params:
            row = QHBoxLayout()
            row.addWidget(QLabel(p['name']))
            
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(p['min'], p['max'])
            slider.setValue(p['default'])
            slider.valueChanged.connect(self.on_change)
            
            row.addWidget(slider)
            layout.addLayout(row)
            
            self.inputs.append({"widget": slider, "scale": p['scale']})
            
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def on_change(self):
        # Collect args from sliders
        args = []
        for item in self.inputs:
            args.append(item["widget"].value() * item["scale"])
            
        # Apply processor function
        # func(image, arg1, arg2, ...)
        new_img = self.func(self.original_img, *args)
        
        # Update Canvas
        self.preview_layer.load_from_image(new_img)
        self.parent().update()

    def reject(self):
        # Revert changes
        self.preview_layer.load_from_image(self.original_img)
        self.parent().update()
        super().reject()

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
        
        # ── Connection Settings ──
        conn_label = QLabel("Connection")
        conn_label.setStyleSheet("font-weight: bold; font-size: 13px; margin-top: 5px;")
        layout_ai.addWidget(conn_label)
        
        form_conn = QFormLayout()
        self.txt_base_url = QLineEdit(self.agent_manager.base_url)
        self.txt_api_key = QLineEdit(self.agent_manager.api_key)
        self.txt_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_proxy = QLineEdit(self.agent_manager.proxy)
        self.txt_proxy.setPlaceholderText("e.g. http://127.0.0.1:7890")
        
        form_conn.addRow("Base URL:", self.txt_base_url)
        form_conn.addRow("API Key:", self.txt_api_key)
        form_conn.addRow("Proxy (Optional):", self.txt_proxy)
        layout_ai.addLayout(form_conn)
        
        btn_test = QPushButton("Test Connection")
        btn_test.clicked.connect(self.test_ai_connection)
        layout_ai.addWidget(btn_test)

        # ── Model Configuration ──
        model_label = QLabel("Model Configuration")
        model_label.setStyleSheet("font-weight: bold; font-size: 13px; margin-top: 15px;")
        layout_ai.addWidget(model_label)

        model_desc = QLabel("Configure the model used for each AI feature.\nLeave default if unsure.")
        model_desc.setStyleSheet("color: #888; font-size: 10px;")
        model_desc.setWordWrap(True)
        layout_ai.addWidget(model_desc)

        form_models = QFormLayout()

        self.txt_generate_model = QLineEdit(self.agent_manager.generate_model)
        self.txt_generate_model.setPlaceholderText("e.g. qwen-vl-max, wanx-v1")
        form_models.addRow("Generate (Text→Image):", self.txt_generate_model)

        self.txt_edit_model = QLineEdit(self.agent_manager.edit_model)
        self.txt_edit_model.setPlaceholderText("e.g. qwen-image-2.0")
        form_models.addRow("Edit (Image→Image):", self.txt_edit_model)

        self.txt_inpaint_model = QLineEdit(self.agent_manager.inpaint_model)
        self.txt_inpaint_model.setPlaceholderText("e.g. wanx2.1-imageedit")
        form_models.addRow("Inpaint (Mask Edit):", self.txt_inpaint_model)

        self.txt_layered_model = QLineEdit(self.agent_manager.layered_model)
        self.txt_layered_model.setPlaceholderText("e.g. qwen/qwen-image-layered")
        form_models.addRow("Layered (Replicate):", self.txt_layered_model)
        
        self.txt_replicate_key = QLineEdit(self.agent_manager.replicate_api_key)
        self.txt_replicate_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_replicate_key.setPlaceholderText("Replicate API Token (r8_...)")
        form_models.addRow("Replicate API Key:", self.txt_replicate_key)

        layout_ai.addLayout(form_models)

        btn_reset_models = QPushButton("Reset to Defaults")
        btn_reset_models.setStyleSheet("color: #888;")
        btn_reset_models.clicked.connect(self._reset_model_defaults)
        layout_ai.addWidget(btn_reset_models)

        layout_ai.addStretch()
        self.tabs.addTab(self.tab_ai, "AI Settings")

        # --- Tab 4: MobileSAM Model Management ---
        self.tab_sam = QWidget()
        layout_sam = QVBoxLayout(self.tab_sam)

        sam_title = QLabel("MobileSAM Model Management")
        sam_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout_sam.addWidget(sam_title)

        sam_desc = QLabel(
            "MobileSAM is a lightweight image segmentation model\n"
            "used by the AI Magic Wand tool.\n"
            "It will be downloaded automatically on first use (~10 MB)."
        )
        sam_desc.setWordWrap(True)
        sam_desc.setStyleSheet("color: #666;")
        layout_sam.addWidget(sam_desc)

        # Status info
        form_sam = QFormLayout()
        self._sam_status_label = QLabel("Checking...")
        self._sam_size_label = QLabel("—")
        form_sam.addRow("Status:", self._sam_status_label)
        form_sam.addRow("File Size:", self._sam_size_label)
        layout_sam.addLayout(form_sam)

        # Action buttons
        sam_btn_layout = QHBoxLayout()
        self._sam_download_btn = QPushButton("Download / Load Model")
        self._sam_download_btn.clicked.connect(self._on_sam_download)
        sam_btn_layout.addWidget(self._sam_download_btn)

        self._sam_delete_btn = QPushButton("Delete Model")
        self._sam_delete_btn.setStyleSheet("QPushButton { color: #c00; }")
        self._sam_delete_btn.clicked.connect(self._on_sam_delete)
        sam_btn_layout.addWidget(self._sam_delete_btn)
        layout_sam.addLayout(sam_btn_layout)

        # Progress
        self._sam_progress_label = QLabel("")
        self._sam_progress_label.setWordWrap(True)
        self._sam_progress_label.setStyleSheet("color: #c08000;")
        self._sam_progress_label.setVisible(False)
        layout_sam.addWidget(self._sam_progress_label)

        layout_sam.addStretch()
        self.tabs.addTab(self.tab_sam, "MobileSAM")

        # Refresh SAM status
        self._refresh_sam_status()

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
        self.agent_manager.save_config(
            base_url=self.txt_base_url.text(),
            api_key=self.txt_api_key.text(),
            model=self.txt_generate_model.text(),
            proxy=self.txt_proxy.text(),
            edit_model=self.txt_edit_model.text(),
            inpaint_model=self.txt_inpaint_model.text(),
            layered_model=self.txt_layered_model.text(),
            replicate_api_key=self.txt_replicate_key.text(),
        )
        super().accept()

    # --- MobileSAM Management ---
    def _refresh_sam_status(self):
        try:
            from src.agent.mobile_sam_service import MobileSAMService
            sam = MobileSAMService.instance()
            if sam.is_loaded:
                self._sam_status_label.setText("Loaded (ready in memory)")
                self._sam_status_label.setStyleSheet("color: green; font-weight: bold;")
            elif sam.is_model_downloaded():
                self._sam_status_label.setText("Downloaded (not loaded)")
                self._sam_status_label.setStyleSheet("color: #0066cc;")
            else:
                self._sam_status_label.setText("Not downloaded")
                self._sam_status_label.setStyleSheet("color: #cc0000;")
            self._sam_size_label.setText(sam.get_model_size_str())
        except ImportError:
            self._sam_status_label.setText("ultralytics not installed")
            self._sam_status_label.setStyleSheet("color: #cc0000;")
            self._sam_size_label.setText("—")

    def _on_sam_download(self):
        try:
            from src.agent.mobile_sam_service import MobileSAMService
            sam = MobileSAMService.instance()
            if sam.is_loaded:
                QMessageBox.information(self, "MobileSAM", "Model is already loaded.")
                return

            self._sam_progress_label.setText("Downloading / loading model, please wait...")
            self._sam_progress_label.setVisible(True)
            self._sam_download_btn.setEnabled(False)

            sam.model_loading_msg.connect(self._on_sam_progress)
            sam.model_load_finished.connect(self._on_sam_load_done)
            sam.load_model_async()
        except ImportError:
            QMessageBox.warning(self, "MobileSAM", "ultralytics is not installed.\nRun: pip install ultralytics")

    def _on_sam_progress(self, msg):
        self._sam_progress_label.setText(msg)

    def _on_sam_load_done(self, success, msg):
        self._sam_download_btn.setEnabled(True)
        self._sam_progress_label.setVisible(False)
        self._refresh_sam_status()
        
        # Disconnect signals
        try:
            from src.agent.mobile_sam_service import MobileSAMService
            sam = MobileSAMService.instance()
            sam.model_loading_msg.disconnect(self._on_sam_progress)
            sam.model_load_finished.disconnect(self._on_sam_load_done)
        except (TypeError, ImportError):
            pass

        if success:
            QMessageBox.information(self, "MobileSAM", "Model loaded successfully!")
        else:
            QMessageBox.warning(self, "MobileSAM", f"Load failed: {msg}")

    def _on_sam_delete(self):
        try:
            from src.agent.mobile_sam_service import MobileSAMService
            sam = MobileSAMService.instance()
            
            reply = QMessageBox.question(
                self, "Delete Model", 
                "Are you sure you want to delete the MobileSAM model?\nIt will need to be re-downloaded next time.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                ok, msg = sam.delete_model()
                self._refresh_sam_status()
                if ok:
                    QMessageBox.information(self, "MobileSAM", msg)
                else:
                    QMessageBox.warning(self, "MobileSAM", msg)
        except ImportError:
            QMessageBox.warning(self, "MobileSAM", "ultralytics is not installed.")

    def _reset_model_defaults(self):
        from src.agent.agent_manager import _DEFAULT_MODELS
        self.txt_generate_model.setText(_DEFAULT_MODELS["generate_model"])
        self.txt_edit_model.setText(_DEFAULT_MODELS["edit_model"])
        self.txt_inpaint_model.setText(_DEFAULT_MODELS["inpaint_model"])
        self.txt_layered_model.setText(_DEFAULT_MODELS["layered_model"])

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
        self.spin_val.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.spin_val.setFixedWidth(90)  # ← 缩短总宽度，箭头按钮占比更合理
        self.spin_val.setStyleSheet("""
            QSpinBox {
                background-color: white;
                border: 1px solid #ccc;
                border-radius: 3px;
                padding: 2px;
                font-weight: bold;
            }
            QSpinBox::up-button {
                width: 20px;
            }
            QSpinBox::down-button {
                width: 20px;
            }
        """)
        layout.addWidget(self.spin_val)
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
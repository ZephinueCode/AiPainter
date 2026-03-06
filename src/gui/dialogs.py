# src/gui/dialogs.py

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QSpinBox, 
                             QDialogButtonBox, QTabWidget, QWidget, QDoubleSpinBox, 
                             QLabel, QGridLayout, QToolButton, QPushButton, QButtonGroup, 
                             QLineEdit, QMessageBox, QTextEdit, QHBoxLayout, QApplication,
                             QSlider, QCheckBox, QRadioButton, QScrollArea, QFrame,
                             QSizePolicy)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QPoint, QRect, QRectF
from PyQt6.QtGui import QPixmap, QImage, QPainter, QColor, QPen, QBrush, QCursor, QPainterPath
import json
import os
import numpy as np
from PIL import Image
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
        self.resize(520, 520) 
        
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
        self.txt_base_url.setPlaceholderText("https://dashscope.aliyuncs.com/api/v1")
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
        self.txt_generate_model.setPlaceholderText("e.g. qwen-image-2.0, wanx2.1-t2i-plus")
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

        self.txt_chat_model = QLineEdit(self.agent_manager.chat_model)
        self.txt_chat_model.setPlaceholderText("e.g. qwen3.5-plus")
        form_models.addRow("Chat (Assistant):", self.txt_chat_model)

        self.txt_replicate_key = QLineEdit(self.agent_manager.replicate_api_key)
        self.txt_replicate_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_replicate_key.setPlaceholderText("Replicate API Token (r8_...)")
        form_models.addRow("Replicate API Key:", self.txt_replicate_key)

        layout_ai.addLayout(form_models)

        chat_prompt_label = QLabel("Chat System Prompt")
        chat_prompt_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout_ai.addWidget(chat_prompt_label)

        self.txt_chat_system_prompt = QTextEdit()
        self.txt_chat_system_prompt.setPlaceholderText("Default behavior prompt for the chat assistant.")
        self.txt_chat_system_prompt.setMaximumHeight(120)
        self.txt_chat_system_prompt.setPlainText(self.agent_manager.chat_system_prompt)
        layout_ai.addWidget(self.txt_chat_system_prompt)

        btn_reset_models = QPushButton("Reset to Defaults")
        btn_reset_models.setStyleSheet("color: #888;")
        btn_reset_models.clicked.connect(self._reset_model_defaults)
        layout_ai.addWidget(btn_reset_models)

        layout_ai.addStretch()
        self.tabs.addTab(self.tab_ai, "AI Settings")

        # --- Tab 4: AI Prompts ---
        self.tab_prompts = QWidget()
        layout_prompts = QVBoxLayout(self.tab_prompts)

        prompts_title = QLabel("AI Preset Prompts")
        prompts_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout_prompts.addWidget(prompts_title)

        prompts_desc = QLabel("Customize the preset prompts used by Auto Sketch, Auto Color,\nand Auto Optimize. Leave empty to use defaults.")
        prompts_desc.setWordWrap(True)
        prompts_desc.setStyleSheet("color: #888; font-size: 10px;")
        layout_prompts.addWidget(prompts_desc)

        layout_prompts.addWidget(QLabel("Auto Sketch Prompt:"))
        self.txt_auto_sketch_prompt = QTextEdit()
        self.txt_auto_sketch_prompt.setPlaceholderText("Prompt for Auto Sketch...")
        self.txt_auto_sketch_prompt.setMaximumHeight(80)
        self.txt_auto_sketch_prompt.setPlainText(self.agent_manager.auto_sketch_prompt)
        layout_prompts.addWidget(self.txt_auto_sketch_prompt)

        layout_prompts.addWidget(QLabel("Auto Color Prompt:"))
        self.txt_auto_color_prompt = QTextEdit()
        self.txt_auto_color_prompt.setPlaceholderText("Prompt for Auto Color...")
        self.txt_auto_color_prompt.setMaximumHeight(80)
        self.txt_auto_color_prompt.setPlainText(self.agent_manager.auto_color_prompt)
        layout_prompts.addWidget(self.txt_auto_color_prompt)

        layout_prompts.addWidget(QLabel("Auto Optimize Prompt:"))
        self.txt_auto_optimize_prompt = QTextEdit()
        self.txt_auto_optimize_prompt.setPlaceholderText("Prompt for Auto Optimize...")
        self.txt_auto_optimize_prompt.setMaximumHeight(80)
        self.txt_auto_optimize_prompt.setPlainText(self.agent_manager.auto_optimize_prompt)
        layout_prompts.addWidget(self.txt_auto_optimize_prompt)

        self.cb_auto_remove_white_bg = QCheckBox("Auto Remove White Background on result")
        self.cb_auto_remove_white_bg.setChecked(self.agent_manager.auto_remove_white_bg)
        self.cb_auto_remove_white_bg.setToolTip(
            "When enabled, Auto Sketch / Color / Optimize will automatically\n"
            "remove the white background from the AI result."
        )
        layout_prompts.addWidget(self.cb_auto_remove_white_bg)

        btn_reset_prompts = QPushButton("Reset Prompts to Defaults")
        btn_reset_prompts.setStyleSheet("color: #888;")
        btn_reset_prompts.clicked.connect(self._reset_prompt_defaults)
        layout_prompts.addWidget(btn_reset_prompts)

        layout_prompts.addStretch()
        self.tabs.addTab(self.tab_prompts, "AI Prompts")

        # --- Tab 5: Local Models ---
        self.tab_sam = QWidget()
        layout_sam = QVBoxLayout(self.tab_sam)

        sam_title = QLabel("Local Models")
        sam_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout_sam.addWidget(sam_title)

        sam_desc = QLabel(
            "MobileSAM is a lightweight image segmentation model\n"
            "used by the AI Magic Wand tool.\n"
            "It will be downloaded automatically on first use (~10 MB).\n\n"
            "Super-resolution model paths are also configured here."
        )
        sam_desc.setWordWrap(True)
        sam_desc.setStyleSheet("color: #666;")
        layout_sam.addWidget(sam_desc)

        sr_label = QLabel("Super-Resolution Weights")
        sr_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout_sam.addWidget(sr_label)
        form_sr = QFormLayout()
        self.txt_sr_general_model_path = QLineEdit(self.agent_manager.superres_general_model_path)
        self.txt_sr_general_model_path.setPlaceholderText("e.g. models/RealESRGAN_x4plus.pth")
        form_sr.addRow("General Model:", self.txt_sr_general_model_path)

        self.txt_sr_illustration_model_path = QLineEdit(self.agent_manager.superres_illustration_model_path)
        self.txt_sr_illustration_model_path.setPlaceholderText("e.g. models/realesr-animevideov3.pth")
        form_sr.addRow("Illustration Model:", self.txt_sr_illustration_model_path)
        layout_sam.addLayout(form_sr)

        # Status info
        form_sam = QFormLayout()
        self._sam_status_label = QLabel("Checking...")
        self._sam_size_label = QLabel("—")
        form_sam.addRow("Status:", self._sam_status_label)
        form_sam.addRow("File Size:", self._sam_size_label)
        layout_sam.addLayout(form_sam)

        # Action buttons
        sam_btn_layout = QHBoxLayout()
        self._sam_download_btn = QPushButton("Download / Load MobileSAM")
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
        self.tabs.addTab(self.tab_sam, "Local Models")

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
            chat_model=self.txt_chat_model.text(),
            chat_system_prompt=self.txt_chat_system_prompt.toPlainText(),
            replicate_api_key=self.txt_replicate_key.text(),
            superres_general_model_path=self.txt_sr_general_model_path.text(),
            superres_illustration_model_path=self.txt_sr_illustration_model_path.text(),
            auto_sketch_prompt=self.txt_auto_sketch_prompt.toPlainText(),
            auto_color_prompt=self.txt_auto_color_prompt.toPlainText(),
            auto_optimize_prompt=self.txt_auto_optimize_prompt.toPlainText(),
            auto_remove_white_bg=self.cb_auto_remove_white_bg.isChecked(),
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
        self.txt_chat_model.setText(_DEFAULT_MODELS["chat_model"])
        self.txt_chat_system_prompt.setPlainText(_DEFAULT_MODELS["chat_system_prompt"])
        self.txt_sr_general_model_path.setText(_DEFAULT_MODELS["superres_general_model_path"])
        self.txt_sr_illustration_model_path.setText(_DEFAULT_MODELS["superres_illustration_model_path"])

    def _reset_prompt_defaults(self):
        from src.agent.agent_manager import _DEFAULT_PROMPTS
        self.txt_auto_sketch_prompt.setPlainText(_DEFAULT_PROMPTS["auto_sketch_prompt"])
        self.txt_auto_color_prompt.setPlainText(_DEFAULT_PROMPTS["auto_color_prompt"])
        self.txt_auto_optimize_prompt.setPlainText(_DEFAULT_PROMPTS["auto_optimize_prompt"])
        self.cb_auto_remove_white_bg.setChecked(_DEFAULT_PROMPTS["auto_remove_white_bg"])

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

    def __init__(self, parent=None, canvas_width=1920, canvas_height=1080):
        super().__init__(parent)
        self.setWindowTitle("AI Image Generator")
        self.resize(400, 300) 
        self._canvas_w = canvas_width
        self._canvas_h = canvas_height
        
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

        self.btn_fit_canvas = QPushButton("Fit to Canvas")
        self.btn_fit_canvas.setToolTip(f"Set size to current canvas dimensions ({canvas_width}×{canvas_height})")
        self.btn_fit_canvas.clicked.connect(self._on_fit_canvas)
        size_layout.addWidget(self.btn_fit_canvas)

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

    def _on_fit_canvas(self):
        self.cycler_w.spin_val.setValue(self._canvas_w)
        self.cycler_h.spin_val.setValue(self._canvas_h)

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


# === Chroma Key Preview Canvas ===
class _ChromaKeyCanvas(QWidget):
    """Internal preview widget for the Chroma Key dialog.

    Supports:
    - Pan (middle-click / Ctrl+drag) and zoom (scroll wheel)
    - Eyedropper mode: click to pick the key color
    - Safety-brush mode: paint green overlay to mark protected areas
    """

    colorPicked = pyqtSignal(int, int, int)  # r, g, b (0-255)
    safetyMaskChanged = pyqtSignal(bool)    # True = has painted area

    MODE_EYEDROPPER = 0
    MODE_SAFETY = 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._src_img: Image.Image | None = None   # original RGBA
        self._result_img: Image.Image | None = None # after chroma key
        self._safety_mask: np.ndarray | None = None # H×W uint8, 255=protected
        self._show_result = True

        self._zoom = 1.0
        self._offset = QPoint(0, 0)
        self._pan_start = None

        self.mode = self.MODE_EYEDROPPER
        self.brush_size = 20
        self._painting = False
        self._last_safety_pt = None          # for interpolation
        self._safety_overlay_dirty = True    # rebuild overlay pixmap
        self._safety_overlay_pm = None       # cached QPixmap
        self._safety_overlay_buf = None      # bytes buffer kept alive for QImage

        # Checkerboard tile for transparency preview
        self._checker = self._make_checker(16)

    # ── public API ──────────────────────────────────
    def set_images(self, src: Image.Image, result: Image.Image | None):
        self._src_img = src.copy()
        if self._safety_mask is None or self._safety_mask.shape != (src.height, src.width):
            self._safety_mask = np.zeros((src.height, src.width), dtype=np.uint8)
        self._result_img = result
        self._fit_view()
        self.update()

    def set_result(self, result: Image.Image | None):
        self._result_img = result
        self.update()

    def get_safety_mask(self) -> np.ndarray | None:
        return self._safety_mask

    def set_show_result(self, show: bool):
        self._show_result = show
        self.update()

    def clear_safety_mask(self):
        if self._safety_mask is not None:
            self._safety_mask[:] = 0
            self._safety_overlay_dirty = True
            self._safety_overlay_pm = None
            self._safety_overlay_buf = None
            self.safetyMaskChanged.emit(False)
            self.update()

    # ── internal helpers ────────────────────────────
    @staticmethod
    def _make_checker(tile_size):
        """Build a small checkerboard QPixmap for transparency bg."""
        pm = QPixmap(tile_size * 2, tile_size * 2)
        pm.fill(QColor(204, 204, 204))
        p = QPainter(pm)
        p.fillRect(0, 0, tile_size, tile_size, QColor(255, 255, 255))
        p.fillRect(tile_size, tile_size, tile_size, tile_size, QColor(255, 255, 255))
        p.end()
        return pm

    def _fit_view(self):
        if self._src_img is None:
            return
        iw, ih = self._src_img.size
        vw, vh = self.width(), self.height()
        if iw == 0 or ih == 0:
            return
        scale = min(vw / iw, vh / ih) * 0.95
        self._zoom = scale
        self._offset = QPoint(
            int((vw - iw * scale) / 2),
            int((vh - ih * scale) / 2),
        )

    def _widget_to_img(self, pos) -> QPoint | None:
        """Convert widget coordinates → image pixel coordinates."""
        if self._src_img is None:
            return None
        ix = (pos.x() - self._offset.x()) / self._zoom
        iy = (pos.y() - self._offset.y()) / self._zoom
        iw, ih = self._src_img.size
        if 0 <= ix < iw and 0 <= iy < ih:
            return QPoint(int(ix), int(iy))
        return None

    @staticmethod
    def _pil_to_qpixmap(pil_img: Image.Image) -> QPixmap:
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        qimg = QImage(data, pil_img.width, pil_img.height,
                       pil_img.width * 4, QImage.Format.Format_RGBA8888)
        # .copy() MUST be called while `data` is still alive to
        # produce a QImage that owns its own pixel buffer.
        return QPixmap.fromImage(qimg.copy())

    # ── painting events ─────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Background
        p.fillRect(self.rect(), QColor(60, 60, 60))

        img = self._result_img if (self._show_result and self._result_img) else self._src_img
        if img is None:
            p.end()
            return

        iw, ih = img.size
        dest = QRectF(self._offset.x(), self._offset.y(), iw * self._zoom, ih * self._zoom)

        # Checkerboard behind the image (shows transparency)
        p.save()
        p.setClipRect(dest)
        p.drawTiledPixmap(dest.toAlignedRect(), self._checker)
        p.restore()

        # Draw the image itself
        pm = self._pil_to_qpixmap(img)
        p.drawPixmap(dest.toAlignedRect(), pm)

        # Draw safety mask overlay (semi-transparent green) — cached
        if self._safety_mask is not None and np.any(self._safety_mask):
            if self._safety_overlay_dirty or self._safety_overlay_pm is None:
                h, w = self._safety_mask.shape
                green = np.zeros((h, w, 4), dtype=np.uint8)
                green[self._safety_mask > 0] = [0, 200, 80, 100]
                # QImage owns its own copy of the pixel data
                qimg = QImage(green.data, w, h, w * 4,
                              QImage.Format.Format_RGBA8888).copy()
                self._safety_overlay_pm = QPixmap.fromImage(qimg)
                self._safety_overlay_dirty = False
            p.drawPixmap(dest.toAlignedRect(), self._safety_overlay_pm)

        # Draw brush cursor in safety mode
        if self.mode == self.MODE_SAFETY and self.underMouse():
            cursor_pos = self.mapFromGlobal(QCursor.pos())
            radius = self.brush_size * self._zoom / 2
            p.setPen(QPen(QColor(0, 255, 100, 180), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(cursor_pos, int(radius), int(radius))

        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._pan_start = event.pos() - self._offset
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            if self.mode == self.MODE_EYEDROPPER:
                self._pick_color(event.pos())
            elif self.mode == self.MODE_SAFETY:
                self._painting = True
                self._last_safety_pt = None
                self._paint_safety(event.pos())

    def mouseMoveEvent(self, event):
        if self._pan_start is not None:
            self._offset = event.pos() - self._pan_start
            self.update()
            return
        if self._painting and self.mode == self.MODE_SAFETY:
            self._paint_safety(event.pos())
        if self.mode == self.MODE_SAFETY:
            self.update()  # repaint cursor

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton and self._pan_start is not None
        ):
            self._pan_start = None
            self._update_cursor()
            return
        was_painting = self._painting
        self._painting = False
        self._last_safety_pt = None
        if was_painting and self.mode == self.MODE_SAFETY:
            # Emit once more so the dialog runs _update_preview now that painting stopped
            has = self._safety_mask is not None and bool(np.any(self._safety_mask))
            self.safetyMaskChanged.emit(has)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        mouse = event.position()
        old_img_x = (mouse.x() - self._offset.x()) / self._zoom
        old_img_y = (mouse.y() - self._offset.y()) / self._zoom
        self._zoom = max(0.05, min(self._zoom * factor, 30.0))
        self._offset = QPoint(
            int(mouse.x() - old_img_x * self._zoom),
            int(mouse.y() - old_img_y * self._zoom),
        )
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._src_img:
            self._fit_view()

    # ── tool actions ────────────────────────────────
    def _pick_color(self, widget_pos):
        pt = self._widget_to_img(widget_pos)
        if pt is None or self._src_img is None:
            return
        r, g, b = self._src_img.getpixel((pt.x(), pt.y()))[:3]
        self.colorPicked.emit(r, g, b)

    def _paint_safety(self, widget_pos):
        pt = self._widget_to_img(widget_pos)
        if pt is None or self._safety_mask is None:
            return
        import cv2
        radius = max(1, int(self.brush_size / 2))
        cur = (pt.x(), pt.y())
        if self._last_safety_pt is not None:
            cv2.line(self._safety_mask, self._last_safety_pt, cur, 255, radius * 2)
        cv2.circle(self._safety_mask, cur, radius, 255, -1)
        self._last_safety_pt = cur
        self._safety_overlay_dirty = True
        self.safetyMaskChanged.emit(True)
        self.update()

    def _update_cursor(self):
        if self.mode == self.MODE_EYEDROPPER:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_mode(self, mode):
        self.mode = mode
        self._update_cursor()


# === Chroma Key Dialog ===
class ChromaKeyDialog(QDialog):
    """Chroma Key (Color Key) dialog for removing a specific color from a layer.

    Features:
    - Eyedropper to pick the key color from the image
    - Tolerance slider to control how close a color must be to be removed
    - Edge softness slider for smooth transitions at the boundary
    - Safety-zone brush: paint areas that should never be removed
    - Real-time before/after preview with checkerboard transparency background
    """

    def __init__(self, parent, layer_image: Image.Image):
        """
        Parameters
        ----------
        parent : QWidget
        layer_image : PIL.Image.Image (RGBA)
            The current image of the layer to process.
        """
        super().__init__(parent)
        self.setWindowTitle("Chroma Key")
        self.resize(750, 600)

        self._src_img = layer_image.convert("RGBA")
        self._result_img = None

        # Key color (default: white)
        self._key_r, self._key_g, self._key_b = 255, 255, 255
        self._tolerance = 30
        self._softness = 5

        self._build_ui()
        self._canvas.set_images(self._src_img, None)
        self._update_preview()

    # ── UI construction ─────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)

        # ── Preview canvas ──
        self._canvas = _ChromaKeyCanvas()
        self._canvas.colorPicked.connect(self._on_color_picked)
        self._canvas.safetyMaskChanged.connect(self._on_safety_mask_changed)
        root.addWidget(self._canvas, 1)

        # ── Toolbar row ──
        tool_row = QHBoxLayout()

        self._rb_eyedropper = QRadioButton("Eyedropper")
        self._rb_eyedropper.setChecked(True)
        self._rb_eyedropper.setToolTip("Click on the image to pick the key color")
        self._rb_eyedropper.toggled.connect(self._on_mode_changed)
        tool_row.addWidget(self._rb_eyedropper)

        self._rb_safety = QRadioButton("Safety Brush")
        self._rb_safety.setToolTip("Paint areas that should NOT be removed")
        tool_row.addWidget(self._rb_safety)

        tool_row.addSpacing(15)

        tool_row.addWidget(QLabel("Brush Size:"))
        self._sl_brush_size = QSlider(Qt.Orientation.Horizontal)
        self._sl_brush_size.setRange(2, 100)
        self._sl_brush_size.setValue(20)
        self._sl_brush_size.setFixedWidth(100)
        self._sl_brush_size.valueChanged.connect(lambda v: setattr(self._canvas, 'brush_size', v))
        tool_row.addWidget(self._sl_brush_size)

        self._btn_clear_safety = QPushButton("Clear Safety Zone")
        self._btn_clear_safety.setEnabled(False)
        self._btn_clear_safety.clicked.connect(self._on_clear_safety)
        tool_row.addWidget(self._btn_clear_safety)

        tool_row.addStretch()

        # Toggle before / after
        self._btn_toggle = QPushButton("Show Original")
        self._btn_toggle.setCheckable(True)
        self._btn_toggle.setToolTip("Toggle between original and result")
        self._btn_toggle.toggled.connect(self._on_toggle_preview)
        tool_row.addWidget(self._btn_toggle)

        root.addLayout(tool_row)

        # ── Parameter row ──
        param_form = QFormLayout()

        # Color swatch + label
        color_row = QHBoxLayout()
        self._lbl_swatch = QLabel()
        self._lbl_swatch.setFixedSize(24, 24)
        self._lbl_swatch.setStyleSheet("border: 1px solid #666;")
        color_row.addWidget(self._lbl_swatch)
        self._lbl_color = QLabel("R:255  G:255  B:255")
        color_row.addWidget(self._lbl_color)
        color_row.addStretch()
        param_form.addRow("Key Color:", color_row)

        # Tolerance
        tol_row = QHBoxLayout()
        self._sl_tolerance = QSlider(Qt.Orientation.Horizontal)
        self._sl_tolerance.setRange(0, 255)
        self._sl_tolerance.setValue(self._tolerance)
        self._sl_tolerance.valueChanged.connect(self._on_param_changed)
        tol_row.addWidget(self._sl_tolerance)
        self._lbl_tolerance = QLabel(str(self._tolerance))
        self._lbl_tolerance.setFixedWidth(30)
        tol_row.addWidget(self._lbl_tolerance)
        param_form.addRow("Tolerance:", tol_row)

        # Edge softness
        soft_row = QHBoxLayout()
        self._sl_softness = QSlider(Qt.Orientation.Horizontal)
        self._sl_softness.setRange(0, 50)
        self._sl_softness.setValue(self._softness)
        self._sl_softness.valueChanged.connect(self._on_param_changed)
        soft_row.addWidget(self._sl_softness)
        self._lbl_softness = QLabel(str(self._softness))
        self._lbl_softness.setFixedWidth(30)
        soft_row.addWidget(self._lbl_softness)
        param_form.addRow("Edge Softness:", soft_row)

        root.addLayout(param_form)

        # ── Buttons ──
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        # Initial swatch
        self._refresh_swatch()

    # ── slots ───────────────────────────────────────
    def _on_mode_changed(self):
        if self._rb_eyedropper.isChecked():
            self._canvas.set_mode(_ChromaKeyCanvas.MODE_EYEDROPPER)
        else:
            self._canvas.set_mode(_ChromaKeyCanvas.MODE_SAFETY)

    def _on_color_picked(self, r, g, b):
        self._key_r, self._key_g, self._key_b = r, g, b
        self._refresh_swatch()
        self._update_preview()

    def _on_param_changed(self):
        self._tolerance = self._sl_tolerance.value()
        self._softness = self._sl_softness.value()
        self._lbl_tolerance.setText(str(self._tolerance))
        self._lbl_softness.setText(str(self._softness))
        self._update_preview()

    def _on_toggle_preview(self, checked):
        self._btn_toggle.setText("Show Result" if checked else "Show Original")
        self._canvas.set_show_result(not checked)

    def _on_clear_safety(self):
        self._canvas.clear_safety_mask()
        self._btn_clear_safety.setEnabled(False)
        self._update_preview()

    def _on_safety_mask_changed(self, has_content):
        self._btn_clear_safety.setEnabled(has_content)
        # Don't run expensive _update_preview during active painting;
        # it will be called on mouse release instead.
        if not self._canvas._painting:
            self._update_preview()

    def _refresh_swatch(self):
        self._lbl_swatch.setStyleSheet(
            f"background-color: rgb({self._key_r},{self._key_g},{self._key_b}); "
            f"border: 1px solid #666;"
        )
        self._lbl_color.setText(f"R:{self._key_r}  G:{self._key_g}  B:{self._key_b}")

    # ── core processing ─────────────────────────────
    def _update_preview(self):
        self._result_img = self._apply_chroma_key(
            self._src_img,
            (self._key_r, self._key_g, self._key_b),
            self._tolerance,
            self._softness,
            self._canvas.get_safety_mask(),
        )
        self._canvas.set_result(self._result_img)

    @staticmethod
    def _apply_chroma_key(
        src: Image.Image,
        key_color: tuple,
        tolerance: int,
        softness: int,
        safety_mask: np.ndarray | None,
    ) -> Image.Image:
        """Remove pixels close to *key_color* and return a new RGBA image.

        Parameters
        ----------
        src : PIL RGBA image
        key_color : (r, g, b) 0-255
        tolerance : int  0-255 – max Euclidean distance in RGB space to be fully removed
        softness : int  0-50 – additional distance band for soft (partial) transparency
        safety_mask : H×W uint8 array, 255 = protected pixel (never removed)
        """
        arr = np.array(src, dtype=np.float32)  # H×W×4
        kr, kg, kb = float(key_color[0]), float(key_color[1]), float(key_color[2])

        # Euclidean distance from key color in RGB space
        diff = np.sqrt(
            (arr[:, :, 0] - kr) ** 2
            + (arr[:, :, 1] - kg) ** 2
            + (arr[:, :, 2] - kb) ** 2
        )  # H×W

        tol = float(max(tolerance, 0))
        soft = float(max(softness, 0))

        # Build removal factor: 0.0 = fully remove, 1.0 = fully keep
        if soft > 0:
            # Smooth ramp: 0 at dist<=tol, 1 at dist>=tol+soft
            factor = np.clip((diff - tol) / soft, 0.0, 1.0)
        else:
            factor = np.where(diff <= tol, 0.0, 1.0)

        # Apply safety mask: protected pixels → factor = 1.0
        if safety_mask is not None:
            safe = safety_mask.astype(np.float32) / 255.0
            factor = np.maximum(factor, safe)

        # Modulate existing alpha by the factor
        arr[:, :, 3] *= factor
        result = arr.clip(0, 255).astype(np.uint8)
        return Image.fromarray(result, "RGBA")

    # ── public result ───────────────────────────────
    def get_result(self) -> Image.Image | None:
        """Return the processed image (available after accept)."""
        return self._result_img

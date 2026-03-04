# src/gui/panels.py

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QTreeWidget, QTreeWidgetItem, QListWidget, QListWidgetItem, 
                             QGroupBox, QLabel, QSlider, QInputDialog, QFrame, QGridLayout,
                             QAbstractItemView, QMenu, QMessageBox, QSplitter, QScrollArea,
                             QComboBox)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QColor, QIcon, QPixmap, QImage
from src.gui.widgets import ColorPickerWidget, PressureCurveEditor
from src.core.brush_manager import BrushConfig
from src.core.logic import GroupLayer, PaintLayer, PaintCommand, TextLayer
from src.gui.dialogs import AIGenerateDialog
from src.core.processor import ImageProcessor
import io

class BrushPanel(QWidget):
    def __init__(self, brush_manager, on_brush_selected):
        super().__init__()
        self.brush_manager = brush_manager
        self.on_brush_selected = on_brush_selected
        layout = QVBoxLayout(self); layout.setContentsMargins(5,5,5,5)
        
        lbl = QLabel("Brushes")
        lbl.setStyleSheet("font-weight: bold; color: #666;")
        layout.addWidget(lbl)

        # REPLACED: QListWidget -> QTreeWidget for collapsible categories
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(15)
        self.tree.setIconSize(QSize(24, 24)) # Size for brush tips
        self.tree.itemClicked.connect(self._item_clicked)
        layout.addWidget(self.tree)
        
        self.refresh_list()

    def refresh_list(self):
        self.tree.clear()
        
        for cat in self.brush_manager.categories:
            brushes = self.brush_manager.brushes.get(cat, [])
            if not brushes:
                continue

            # Category Item
            cat_item = QTreeWidgetItem(self.tree)
            cat_item.setText(0, cat)
            cat_item.setBackground(0, QColor("#dcdcdc"))
            cat_item.setExpanded(True) # Default expanded
            # Disable selection logic for category headers if desired, 
            # but QTreeWidget selection is handled in click event anyway.

            for brush in brushes:
                item = QTreeWidgetItem(cat_item)
                item.setText(0, brush.name)
                item.setData(0, Qt.ItemDataRole.UserRole, brush)
                
                # Create Icon from Texture
                if brush.texture:
                    try:
                        # Resize for icon
                        thumb = brush.texture.resize((24, 24))
                        # Convert L (Grayscale) to QImage
                        data = thumb.tobytes("raw", "L")
                        qimg = QImage(data, 24, 24, QImage.Format.Format_Grayscale8)
                        item.setIcon(0, QIcon(QPixmap.fromImage(qimg)))
                    except Exception:
                        pass # Fail silently for icon

    def _item_clicked(self, item, col):
        brush = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(brush, BrushConfig):
            self.on_brush_selected(brush)
            # Notify the sibling tools panel to clear its pressed state
            if hasattr(self, '_on_brush_activated_cb') and self._on_brush_activated_cb:
                self._on_brush_activated_cb()

    def clear_selection(self):
        """Deselect all items in the brush tree (called when a tool is selected)."""
        self.tree.clearSelection()

class ToolsPanel(QWidget):
    def __init__(self, on_tool_selected):
        super().__init__()
        self.on_tool_selected = on_tool_selected
        self._active_tool_name = None
        self._tool_buttons = {}  # name -> QPushButton
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        lbl = QLabel("Tools")
        lbl.setStyleSheet("font-weight: bold; color: #666;")
        layout.addWidget(lbl)

        # Compact 2-column tool buttons
        tools = [
            "Rect Select",
            "Lasso",
            "Magic Wand",
            "Fill Select",
            "Picker",
            "Smudge",
            "Text",
            "Liquify",
        ]
        
        _normal_style = (
            "QPushButton { text-align: center; padding: 4px 6px; font-size: 12px; }"
            "QPushButton:hover { background-color: #e0e0e0; }"
            "QPushButton:checked { background-color: #c0d8f0; border: 1px solid #6aa0d0; }"
        )

        tool_grid = QGridLayout()
        tool_grid.setContentsMargins(0, 0, 0, 0)
        tool_grid.setHorizontalSpacing(6)
        tool_grid.setVerticalSpacing(6)
        for idx, name in enumerate(tools):
            row = idx // 2
            col = idx % 2
            btn = QPushButton(name)
            btn.setMinimumHeight(32)
            btn.setStyleSheet(_normal_style)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, n=name: self._on_tool_btn_clicked(n))
            tool_grid.addWidget(btn, row, col)
            self._tool_buttons[name] = btn
        layout.addLayout(tool_grid)

        # === Magic Wand Options Panel (hidden by default) ===
        self.wand_panel = QGroupBox("AI Wand Options")
        self.wand_panel.setVisible(False)
        wand_layout = QVBoxLayout(self.wand_panel)
        wand_layout.setContentsMargins(8, 8, 8, 8)
        wand_layout.setSpacing(4)

        # Description
        desc = QLabel("Left-click to add positive points,\nright-click for negative.\nEnter to apply / Esc to cancel")
        desc.setStyleSheet("color: #888; font-size: 10px;")
        desc.setWordWrap(True)
        wand_layout.addWidget(desc)

        # Positive / Negative mode
        mode_layout = QHBoxLayout()
        from PyQt6.QtWidgets import QRadioButton, QButtonGroup
        self._wand_mode_group = QButtonGroup(self)
        self._wand_positive = QRadioButton("Positive")
        self._wand_positive.setChecked(True)
        self._wand_negative = QRadioButton("Negative")
        self._wand_mode_group.addButton(self._wand_positive, 1)
        self._wand_mode_group.addButton(self._wand_negative, 0)
        mode_layout.addWidget(self._wand_positive)
        mode_layout.addWidget(self._wand_negative)
        wand_layout.addLayout(mode_layout)
        self._wand_mode_group.idToggled.connect(self._on_wand_mode_changed)

        # Point info
        self._wand_info_label = QLabel("Pos: 0  Neg: 0")
        self._wand_info_label.setStyleSheet("color: #666; font-size: 10px;")
        wand_layout.addWidget(self._wand_info_label)

        # Status info
        self._wand_status_label = QLabel("")
        self._wand_status_label.setStyleSheet("color: #c08000; font-size: 10px;")
        self._wand_status_label.setWordWrap(True)
        self._wand_status_label.setVisible(False)
        wand_layout.addWidget(self._wand_status_label)

        # Buttons
        btn_row1 = QHBoxLayout()
        self._wand_undo_btn = QPushButton("Undo")
        self._wand_undo_btn.clicked.connect(self._on_wand_undo)
        btn_row1.addWidget(self._wand_undo_btn)
        self._wand_clear_btn = QPushButton("Clear")
        self._wand_clear_btn.clicked.connect(self._on_wand_clear)
        btn_row1.addWidget(self._wand_clear_btn)
        wand_layout.addLayout(btn_row1)

        self._wand_apply_btn = QPushButton("Apply Selection")
        self._wand_apply_btn.setStyleSheet(
            "QPushButton { background-color: #4a9eff; color: white; font-weight: bold; padding: 6px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #3a8eef; }"
        )
        self._wand_apply_btn.clicked.connect(self._on_wand_apply)
        wand_layout.addWidget(self._wand_apply_btn)

        # Feather / Edge Blur slider (negative = shrink, positive = expand/blur)
        feather_layout = QHBoxLayout()
        feather_layout.addWidget(QLabel("Feather:"))
        self._wand_feather_slider = QSlider(Qt.Orientation.Horizontal)
        self._wand_feather_slider.setRange(-40, 40)
        self._wand_feather_slider.setValue(0)
        self._wand_feather_slider.setToolTip("Edge blur radius (negative = shrink, 0 = sharp, positive = expand)")
        feather_layout.addWidget(self._wand_feather_slider)
        self._wand_feather_label = QLabel("0")
        self._wand_feather_label.setFixedWidth(30)
        self._wand_feather_slider.valueChanged.connect(
            lambda v: self._wand_feather_label.setText(str(v))
        )
        self._wand_feather_slider.valueChanged.connect(self._on_feather_changed)
        feather_layout.addWidget(self._wand_feather_label)
        wand_layout.addLayout(feather_layout)

        layout.addWidget(self.wand_panel)
        # ====================================================

        # === Liquify Options Panel (hidden by default) ===
        self.liquify_panel = QGroupBox("Liquify Options")
        self.liquify_panel.setVisible(False)
        liquify_layout = QVBoxLayout(self.liquify_panel)
        liquify_layout.setContentsMargins(8, 8, 8, 8)
        liquify_layout.setSpacing(4)

        ldesc = QLabel("Drag on canvas to deform.\nAdjust params below, then Apply or Cancel.")
        ldesc.setStyleSheet("color: #888; font-size: 10px;")
        ldesc.setWordWrap(True)
        liquify_layout.addWidget(ldesc)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self._liquify_mode_combo = QComboBox()
        self._liquify_mode_combo.addItems(["Push", "Bloat", "Pucker", "Restore"])
        self._liquify_mode_combo.currentIndexChanged.connect(self._on_liquify_mode_changed)
        mode_row.addWidget(self._liquify_mode_combo, 1)
        liquify_layout.addLayout(mode_row)

        radius_row = QHBoxLayout()
        radius_row.addWidget(QLabel("Radius:"))
        self._liquify_radius_slider = QSlider(Qt.Orientation.Horizontal)
        self._liquify_radius_slider.setRange(5, 300)
        self._liquify_radius_slider.setValue(50)
        self._liquify_radius_slider.valueChanged.connect(self._on_liquify_radius_changed)
        radius_row.addWidget(self._liquify_radius_slider, 1)
        self._liquify_radius_label = QLabel("50")
        self._liquify_radius_label.setFixedWidth(34)
        radius_row.addWidget(self._liquify_radius_label)
        liquify_layout.addLayout(radius_row)

        strength_row = QHBoxLayout()
        strength_row.addWidget(QLabel("Strength:"))
        self._liquify_strength_slider = QSlider(Qt.Orientation.Horizontal)
        self._liquify_strength_slider.setRange(1, 100)
        self._liquify_strength_slider.setValue(50)
        self._liquify_strength_slider.valueChanged.connect(self._on_liquify_strength_changed)
        strength_row.addWidget(self._liquify_strength_slider, 1)
        self._liquify_strength_label = QLabel("50%")
        self._liquify_strength_label.setFixedWidth(34)
        strength_row.addWidget(self._liquify_strength_label)
        liquify_layout.addLayout(strength_row)

        lbtn_row = QHBoxLayout()
        self._liquify_cancel_btn = QPushButton("Cancel")
        self._liquify_cancel_btn.clicked.connect(self._on_liquify_cancel)
        lbtn_row.addWidget(self._liquify_cancel_btn)
        self._liquify_apply_btn = QPushButton("Apply")
        self._liquify_apply_btn.setStyleSheet(
            "QPushButton { background-color: #4a9eff; color: white; font-weight: bold; padding: 6px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #3a8eef; }"
        )
        self._liquify_apply_btn.clicked.connect(self._on_liquify_apply)
        lbtn_row.addWidget(self._liquify_apply_btn)
        liquify_layout.addLayout(lbtn_row)

        layout.addWidget(self.liquify_panel)
        # ================================================

        layout.addStretch()

        # Magic wand tool reference (set by LeftSidebar)
        self._magic_wand_tool = None
        self._liquify_tool = None

    def set_magic_wand_tool_ref(self, tool):
        """Set the magic wand tool reference for panel interaction."""
        self._magic_wand_tool = tool
        if tool:
            tool._status_callback = self._update_wand_status
            # Sync feather slider value to the tool
            tool.feather = self._wand_feather_slider.value()

    def set_liquify_tool_ref(self, tool):
        """Set liquify tool reference for panel interaction and sync panel values."""
        self._liquify_tool = tool
        enabled = tool is not None and getattr(tool, "is_active", False)
        self._liquify_mode_combo.setEnabled(enabled)
        self._liquify_radius_slider.setEnabled(enabled)
        self._liquify_strength_slider.setEnabled(enabled)
        self._liquify_apply_btn.setEnabled(enabled)
        self._liquify_cancel_btn.setEnabled(enabled)
        if not enabled:
            return

        self._liquify_mode_combo.blockSignals(True)
        self._liquify_radius_slider.blockSignals(True)
        self._liquify_strength_slider.blockSignals(True)
        try:
            self._liquify_mode_combo.setCurrentIndex(int(getattr(tool, "mode", 0)))
            radius_val = int(round(float(getattr(tool, "radius", 50.0))))
            strength_val = int(round(float(getattr(tool, "strength", 0.5)) * 100))
            self._liquify_radius_slider.setValue(max(5, min(300, radius_val)))
            self._liquify_strength_slider.setValue(max(1, min(100, strength_val)))
            self._liquify_radius_label.setText(str(self._liquify_radius_slider.value()))
            self._liquify_strength_label.setText(f"{self._liquify_strength_slider.value()}%")
        finally:
            self._liquify_mode_combo.blockSignals(False)
            self._liquify_radius_slider.blockSignals(False)
            self._liquify_strength_slider.blockSignals(False)

    def _on_tool_btn_clicked(self, name):
        self._active_tool_name = name
        self._set_active_button(name)
        self.wand_panel.setVisible(name == "Magic Wand")
        self.liquify_panel.setVisible(name == "Liquify")
        # Notify sibling brush panel to clear its selection
        if hasattr(self, '_on_tool_activated_cb') and self._on_tool_activated_cb:
            self._on_tool_activated_cb()
        self.on_tool_selected(name)

    def _set_active_button(self, name):
        """Highlight only the given tool button (uncheck all others)."""
        for btn_name, btn in self._tool_buttons.items():
            btn.setChecked(btn_name == name)

    def clear_active_button(self):
        """Uncheck all tool buttons (called when a brush is selected)."""
        self._active_tool_name = None
        for btn in self._tool_buttons.values():
            btn.setChecked(False)
        self.wand_panel.setVisible(False)
        self.liquify_panel.setVisible(False)

    def _on_wand_mode_changed(self, id, checked):
        if checked and self._magic_wand_tool:
            self._magic_wand_tool.set_point_mode(id)

    def _on_wand_undo(self):
        if self._magic_wand_tool:
            self._magic_wand_tool.undo_last_point()
            self._refresh_wand_info()

    def _on_wand_clear(self):
        if self._magic_wand_tool:
            self._magic_wand_tool.clear_all_points()
            self._refresh_wand_info()

    def _on_wand_apply(self):
        if self._magic_wand_tool:
            feather = self._wand_feather_slider.value()
            self._magic_wand_tool.apply_as_selection(feather=feather)
            self._refresh_wand_info()
            # Hide wand panel since tool auto-switched to Rect Select
            self.wand_panel.setVisible(False)
            self._active_tool_name = "Rect Select"
            self._set_active_button("Rect Select")

    def _on_feather_changed(self, value):
        """Live-update the feather value on the tool and refresh the mask overlay."""
        if self._magic_wand_tool:
            self._magic_wand_tool.feather = value
            self._magic_wand_tool.canvas.update()  # trigger overlay repaint

    def _refresh_wand_info(self):
        if self._magic_wand_tool:
            pos, neg = self._magic_wand_tool.get_point_counts()
            self._wand_info_label.setText(f"Pos: {pos}  Neg: {neg}")

    def _update_wand_status(self, msg):
        self._wand_status_label.setText(msg)
        self._wand_status_label.setVisible(bool(msg))
        self._refresh_wand_info()

    def _on_liquify_mode_changed(self, index):
        if self._liquify_tool and hasattr(self._liquify_tool, "set_mode"):
            self._liquify_tool.set_mode(index)

    def _on_liquify_radius_changed(self, value):
        self._liquify_radius_label.setText(str(value))
        if self._liquify_tool and hasattr(self._liquify_tool, "set_radius"):
            self._liquify_tool.set_radius(value)

    def _on_liquify_strength_changed(self, value):
        self._liquify_strength_label.setText(f"{value}%")
        if self._liquify_tool and hasattr(self._liquify_tool, "set_strength"):
            self._liquify_tool.set_strength(value / 100.0)

    def _finish_liquify_session(self, apply):
        tool = self._liquify_tool
        if tool is None:
            return

        if apply and hasattr(tool, "apply_and_finish"):
            tool.apply_and_finish()
        elif hasattr(tool, "cancel_and_finish"):
            tool.cancel_and_finish()

        canvas = getattr(tool, "canvas", None)
        if canvas is not None and hasattr(canvas, "set_tool"):
            canvas.set_tool("Rect Select")
            if hasattr(canvas, "_tool_switched_callback") and canvas._tool_switched_callback:
                canvas._tool_switched_callback("Rect Select")

    def _on_liquify_apply(self):
        self._finish_liquify_session(apply=True)

    def _on_liquify_cancel(self):
        self._finish_liquify_session(apply=False)

class AIPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5,5,5,5)
        
        lbl = QLabel("AI Features")
        lbl.setStyleSheet("font-weight: bold; color: #666;")
        layout.addWidget(lbl)
        
        btn_style = (
            "QPushButton { text-align: center; padding: 4px 6px; font-size: 12px; }"
            "QPushButton:hover { background-color: #e0e0e0; }"
        )

        self.btn_generate = QPushButton("Auto Generate")
        self.btn_generate.setMinimumHeight(32)
        self.btn_generate.setStyleSheet(btn_style)
        self.btn_generate.clicked.connect(self.open_generator)
        self.btn_auto_sketch = QPushButton("Auto Sketch")
        self.btn_auto_sketch.setMinimumHeight(32)
        self.btn_auto_sketch.setStyleSheet(btn_style)

        self.btn_auto_color = QPushButton("Auto Color")
        self.btn_auto_color.setMinimumHeight(32)
        self.btn_auto_color.setStyleSheet(btn_style)

        self.btn_auto_optimize = QPushButton("Auto Optimize")
        self.btn_auto_optimize.setMinimumHeight(32)
        self.btn_auto_optimize.setStyleSheet(btn_style)

        self.btn_auto_resolution = QPushButton("Auto Resolution")
        self.btn_auto_resolution.setMinimumHeight(32)
        self.btn_auto_resolution.setStyleSheet(btn_style)

        ai_grid = QGridLayout()
        ai_grid.setContentsMargins(0, 0, 0, 0)
        ai_grid.setHorizontalSpacing(6)
        ai_grid.setVerticalSpacing(6)
        ai_grid.addWidget(self.btn_generate, 0, 0)
        ai_grid.addWidget(self.btn_auto_sketch, 0, 1)
        ai_grid.addWidget(self.btn_auto_color, 1, 0)
        ai_grid.addWidget(self.btn_auto_optimize, 1, 1)
        ai_grid.addWidget(self.btn_auto_resolution, 2, 0, 1, 2)
        layout.addLayout(ai_grid)
        layout.addStretch()

    def open_generator(self):
        dlg = AIGenerateDialog(self)
        dlg.exec()

class LeftSidebar(QWidget):
    def __init__(self, brush_manager, on_brush_selected, on_tool_selected, canvas=None):
        super().__init__()
        self._canvas = canvas
        self._original_tool_cb = on_tool_selected
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.brush_panel = BrushPanel(brush_manager, on_brush_selected)
        self.tools_panel = ToolsPanel(self._on_tool_selected_wrapper)
        self.ai_panel = AIPanel()

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self.splitter.addWidget(self._wrap_scroll_panel(self.brush_panel))
        self.splitter.addWidget(self._wrap_scroll_panel(self.tools_panel))
        self.splitter.addWidget(self._wrap_scroll_panel(self.ai_panel))
        self.splitter.setSizes([320, 320, 220])
        layout.addWidget(self.splitter, 1)

        # Cross-wire brush -> tool pressed-state exclusion
        self.brush_panel._on_brush_activated_cb = self._on_brush_activated
        self.tools_panel._on_tool_activated_cb = self._on_tool_activated

    def _wrap_scroll_panel(self, panel):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        area.setWidget(panel)
        return area

    def _on_tool_selected_wrapper(self, tool_name):
        """Intercept tool selection to wire up the magic wand tool reference."""
        self._original_tool_cb(tool_name)

        if not self._canvas:
            self.tools_panel.set_magic_wand_tool_ref(None)
            self.tools_panel.set_liquify_tool_ref(None)
            return

        gl = self._canvas.gl_canvas if hasattr(self._canvas, 'gl_canvas') else self._canvas
        gl._tool_switched_callback = self._on_tool_auto_switched

        if tool_name == "Magic Wand" and gl.active_tool and hasattr(gl.active_tool, 'set_point_mode'):
            self.tools_panel.set_magic_wand_tool_ref(gl.active_tool)
        else:
            self.tools_panel.set_magic_wand_tool_ref(None)

        if tool_name == "Liquify" and gl.active_tool and hasattr(gl.active_tool, 'apply_and_finish'):
            self.tools_panel.set_liquify_tool_ref(gl.active_tool)
        else:
            self.tools_panel.set_liquify_tool_ref(None)

    def _on_brush_activated(self):
        """Called when a brush is clicked 鈥?clear tool pressed state."""
        self.tools_panel.clear_active_button()
        self.tools_panel.wand_panel.setVisible(False)
        self.tools_panel.liquify_panel.setVisible(False)

    def _on_tool_activated(self):
        """Called when a tool is clicked 鈥?clear brush tree selection."""
        self.brush_panel.clear_selection()

    def _on_tool_auto_switched(self, tool_name):
        """Called when a tool (e.g. MagicWand) auto-switches to another tool."""
        self.tools_panel._active_tool_name = tool_name
        self.tools_panel._set_active_button(tool_name)
        self.tools_panel.wand_panel.setVisible(tool_name == "Magic Wand")
        self.tools_panel.liquify_panel.setVisible(tool_name == "Liquify")
        self.tools_panel.set_magic_wand_tool_ref(None)
        self.tools_panel.set_liquify_tool_ref(None)

    def _add_divider(self, layout):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        line.setStyleSheet("background-color: #ccc;")
        layout.addWidget(line)

class LayerPanel(QWidget):
    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.canvas.layer_structure_changed.connect(self.refresh)
        self._node_clipboard = None
        self._node_clipboard_was_cut = False
        self._opacity_before_state = None
        
        layout = QVBoxLayout(self); layout.setContentsMargins(5,5,5,5)
        
        # --- Opacity Control ---
        op_layout = QHBoxLayout()
        op_layout.addWidget(QLabel("Opacity:"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.valueChanged.connect(self._on_opacity_change)
        self.opacity_slider.sliderPressed.connect(self._on_opacity_begin)
        self.opacity_slider.sliderReleased.connect(self._on_opacity_end)
        op_layout.addWidget(self.opacity_slider)
        self.lbl_opacity_val = QLabel("100%")
        self.lbl_opacity_val.setFixedWidth(35)
        op_layout.addWidget(self.lbl_opacity_val)
        layout.addLayout(op_layout)

        # --- Layer Tree ---
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(15)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setIconSize(QSize(32, 32)) # Set icon size for thumbnails
        
        # Context Menu
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        
        # --- CRITICAL FIX: Override dropEvent to sync logic ---
        original_drop_event = self.tree.dropEvent
        
        def new_drop_event(event):
            before_state = self._gl().begin_history_action()
            # 1. Perform UI Drop
            original_drop_event(event)
            # 2. Sync Logic
            self._sync_logical_structure()
            self._gl().end_history_action(before_state, "Reorder Layers")
             
        self.tree.dropEvent = new_drop_event
        # -----------------------------------------------------
        original_tree_keypress = self.tree.keyPressEvent

        def new_tree_keypress(event):
            if self._handle_tree_shortcut(event):
                return
            original_tree_keypress(event)

        self.tree.keyPressEvent = new_tree_keypress

        self.tree.itemDoubleClicked.connect(self._rename_item)
        self.tree.currentItemChanged.connect(self._on_select)
        self.tree.itemChanged.connect(self._on_data_change)
        
        layout.addWidget(self.tree)
        
        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("Layer")
        self.btn_group = QPushButton("Group")
        self.btn_merge = QPushButton("Merge")
        self.btn_del = QPushButton("Del")
        self.btn_add.clicked.connect(self._add_layer)
        self.btn_group.clicked.connect(self._add_group)
        self.btn_merge.clicked.connect(self._merge_layers)
        self.btn_del.clicked.connect(self._del_node)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_group)
        btn_layout.addWidget(self.btn_merge)
        btn_layout.addWidget(self.btn_del)
        layout.addLayout(btn_layout)

    def _gl(self):
        return self.canvas.gl_canvas if hasattr(self.canvas, 'gl_canvas') else self.canvas

    def _handle_tree_shortcut(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._del_node()
            event.accept()
            return True
        return False

    def _snapshot_node(self, node):
        if isinstance(node, GroupLayer):
            return {
                "type": "GroupLayer",
                "name": node.name,
                "visible": bool(node.visible),
                "opacity": float(node.opacity),
                "children": [self._snapshot_node(child) for child in node.children],
            }

        if isinstance(node, TextLayer):
            return {
                "type": "TextLayer",
                "name": node.name,
                "visible": bool(node.visible),
                "opacity": float(node.opacity),
                "uuid": getattr(node, "uuid", None),
                "text_content": node.text_content,
                "font_size": node.font_size,
                "text_color": node.text_color,
                "pos_x": node.pos_x,
                "pos_y": node.pos_y,
                "image": node.get_image().copy(),
            }

        return {
            "type": "PaintLayer",
            "name": node.name,
            "visible": bool(node.visible),
            "opacity": float(node.opacity),
            "uuid": getattr(node, "uuid", None),
            "image": node.get_image().copy(),
        }

    def _restore_node(self, data):
        typ = data.get("type")
        if typ == "GroupLayer":
            grp = GroupLayer(data.get("name", "Group"))
            grp.visible = bool(data.get("visible", True))
            grp.opacity = float(data.get("opacity", 1.0))
            for child in data.get("children", []):
                grp.add_child(self._restore_node(child))
            return grp

        if typ == "TextLayer":
            node = TextLayer(
                self.canvas.doc_width,
                self.canvas.doc_height,
                text=data.get("text_content", "Text"),
                font_size=data.get("font_size", 50),
                color=data.get("text_color", (0, 0, 0, 255)),
                x=data.get("pos_x", 0),
                y=data.get("pos_y", 0),
                name=data.get("name", "Text Layer"),
            )
        else:
            node = PaintLayer(self.canvas.doc_width, self.canvas.doc_height, data.get("name", "Layer"))

        node.visible = bool(data.get("visible", True))
        node.opacity = float(data.get("opacity", 1.0))
        restored_uuid = data.get("uuid")
        if restored_uuid:
            node.uuid = restored_uuid
        img = data.get("image")
        if img is not None:
            node.load_from_image(img.copy())
        return node

    def copy_selected_node(self):
        curr = self.tree.currentItem()
        if not curr:
            return False
        node = curr.data(0, Qt.ItemDataRole.UserRole)
        if not node or node == self.canvas.root:
            return False
        gl = self._gl()
        gl.makeCurrent()
        self._node_clipboard = self._snapshot_node(node)
        self._node_clipboard_was_cut = False
        return True

    def cut_selected_node(self):
        curr = self.tree.currentItem()
        if not curr:
            return False
        node = curr.data(0, Qt.ItemDataRole.UserRole)
        if not node or node == self.canvas.root or node.parent is None:
            return False
        if not self.copy_selected_node():
            return False
        self._node_clipboard_was_cut = True
        before_state = self._gl().begin_history_action()
        node.parent.remove_child(node)
        self.canvas.active_layer = None
        self.canvas.layer_structure_changed.emit()
        self.canvas.update()
        self._gl().end_history_action(before_state, "Cut Layer Node")
        return True

    def paste_node(self):
        if self._node_clipboard is None:
            return False

        gl = self._gl()
        gl.makeCurrent()
        before_state = gl.begin_history_action()

        curr = self.tree.currentItem()
        target = curr.data(0, Qt.ItemDataRole.UserRole) if curr else self.canvas.root

        if isinstance(target, GroupLayer):
            parent = target
            insert_idx = len(parent.children)
        elif target and target.parent:
            parent = target.parent
            insert_idx = parent.children.index(target) + 1
        else:
            parent = self.canvas.root
            insert_idx = len(parent.children)

        pasted = self._restore_node(self._node_clipboard)
        parent.children.insert(insert_idx, pasted)
        pasted.parent = parent

        self.canvas.active_layer = pasted
        self.canvas.layer_structure_changed.emit()
        self.canvas.update()
        gl.end_history_action(before_state, "Paste Layer Node")

        if self._node_clipboard_was_cut:
            self._node_clipboard = None
            self._node_clipboard_was_cut = False
        return True

    def _update_item_thumbnail(self, item):
        """Helper to update a single item's thumbnail."""
        node = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(node, PaintLayer):
            return

        # Context safety
        if hasattr(self.canvas, 'make_current'):
            self.canvas.make_current()
        elif hasattr(self.canvas, 'gl_canvas'):
            self.canvas.gl_canvas.makeCurrent()

        try:
            # Get PIL Image from layer (This calls glReadPixels)
            pil_img = node.get_image()
            
            # Generate thumbnail efficiently
            pil_img.thumbnail((32, 32))
            if pil_img.mode != "RGBA":
                pil_img = pil_img.convert("RGBA")
            
            # Direct buffer access
            data = pil_img.tobytes("raw", "RGBA")
            qimg = QImage(data, pil_img.width, pil_img.height, QImage.Format.Format_RGBA8888).copy()
            
            pixmap = QPixmap.fromImage(qimg)
            item.setIcon(0, QIcon(pixmap))
        except Exception as e:
            print(f"Thumbnail error: {e}")

    def refresh(self):
        """Refreshes the layer tree, including generating thumbnails."""
        self.tree.blockSignals(True)
        self.tree.clear()
        
        # Ensure context is current for reading pixels for thumbnails
        if hasattr(self.canvas, 'make_current'):
            self.canvas.make_current()
        elif hasattr(self.canvas, 'gl_canvas'):
            self.canvas.gl_canvas.makeCurrent()
        
        def build_tree(ui_parent, node_parent):
            # Reverse for UI (Top layer in logic should be top in UI)
            for node in reversed(node_parent.children):
                item = QTreeWidgetItem(ui_parent)
                item.setText(0, node.name)
                item.setData(0, Qt.ItemDataRole.UserRole, node)
                item.setCheckState(0, Qt.CheckState.Checked if node.visible else Qt.CheckState.Unchecked)
                
                # --- Thumbnail Generation ---
                if isinstance(node, PaintLayer):
                    self._update_item_thumbnail(item)
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
                elif isinstance(node, GroupLayer):
                    # Folder Icon
                    item.setText(0, f"> {node.name}")
                    item.setExpanded(True)
                    item.setBackground(0, QColor("#eaeaea"))
                    build_tree(item, node)
                # -----------------------------
                
                if node == self.canvas.active_layer:
                    self.tree.setCurrentItem(item)
                    # Sync Opacity Slider
                    self.opacity_slider.blockSignals(True)
                    self.opacity_slider.setValue(int(node.opacity * 100))
                    self.lbl_opacity_val.setText(f"{int(node.opacity * 100)}%")
                    self.opacity_slider.blockSignals(False)

        build_tree(self.tree, self.canvas.root)
        self.tree.blockSignals(False)

    def _show_context_menu(self, position):
        item = self.tree.itemAt(position)
        if not item: return
        
        node = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu()
        
        # Add actions based on layer type
        if isinstance(node, PaintLayer):
            action_gradient = menu.addAction("Gradient Map...")
            action_gradient.triggered.connect(lambda: self._open_gradient_map(node))

            action_rm_white = menu.addAction("Auto Remove White Background")
            action_rm_white.triggered.connect(lambda: self._remove_white_bg(node))

            menu.addSeparator()
            action_merge_down = menu.addAction("Merge Down")
            action_merge_down.triggered.connect(lambda: self._merge_down(node))
            # Disable if this is the bottom-most layer
            parent = node.parent if node.parent else self.canvas.root
            idx = parent.children.index(node) if node in parent.children else -1
            action_merge_down.setEnabled(idx > 0)

        elif isinstance(node, GroupLayer) and node != self.canvas.root:
            action_flatten_group = menu.addAction("Flatten Group")
            action_flatten_group.triggered.connect(lambda: self._flatten_group(node))

        menu.addSeparator()
        action_merge_visible = menu.addAction("Merge All Visible")
        action_merge_visible.triggered.connect(self._merge_all_visible)
            
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _open_gradient_map(self, layer):
        # Set active layer if needed
        if self.canvas.active_layer != layer:
            self.canvas.active_layer = layer
            
        # Delegate to Central Canvas Logic
        if hasattr(self.canvas, 'gl_canvas'):
            self.canvas.gl_canvas.open_gradient_map()

    def _remove_white_bg(self, layer):
        """Remove white background from a layer using edge flood-fill."""
        from src.core.processor import remove_white_background

        gl = self._gl()
        before_state = gl.begin_history_action()
        gl.makeCurrent()

        new_img = remove_white_background(layer.get_image())
        layer.load_from_image(new_img)
        self.canvas.update()
        self.refresh()  # update thumbnail
        gl.end_history_action(before_state, "Remove White Background")

    def _on_opacity_begin(self):
        self._opacity_before_state = self._gl().begin_history_action()

    def _on_opacity_change(self, value):
        opacity = value / 100.0
        self.lbl_opacity_val.setText(f"{value}%")
        
        if self.canvas.active_layer:
            self.canvas.active_layer.opacity = opacity
            self.canvas.update()

    def _on_opacity_end(self):
        self._gl().end_history_action(self._opacity_before_state, "Change Opacity")
        self._opacity_before_state = None

    def _sync_logical_structure(self):
        """Rebuilds the Canvas logical tree based on the current UI TreeWidget structure."""
        def rebuild_node(tree_item):
            logical_node = tree_item.data(0, Qt.ItemDataRole.UserRole)
            logical_node.children = [] # Reset logic children
            
            count = tree_item.childCount()
            # Iterate Reverse (UI Top -> Logic Last)
            for i in range(count - 1, -1, -1):
                child_item = tree_item.child(i)
                child_node = rebuild_node(child_item)
                logical_node.add_child(child_node)
            return logical_node

        self.canvas.root.children = []
        count = self.tree.topLevelItemCount()
        for i in range(count - 1, -1, -1):
            item = self.tree.topLevelItem(i)
            node = rebuild_node(item)
            self.canvas.root.add_child(node)
            
        self.canvas.update()

    def _on_select(self, current, prev):
        if not current: return
        node = current.data(0, Qt.ItemDataRole.UserRole)
        self.canvas.active_layer = node
        
        # --- Update Thumbnails on Selection Change ---
        # Update previous (it might have changed since we left it)
        if prev:
            prev_node = prev.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(prev_node, PaintLayer):
                self._update_item_thumbnail(prev)
        
        # Update current (ensure it's fresh)
        if isinstance(node, PaintLayer):
            self._update_item_thumbnail(current)
        # ---------------------------------------------
        
        # Sync Opacity Slider
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(int(node.opacity * 100))
        self.lbl_opacity_val.setText(f"{int(node.opacity * 100)}%")
        self.opacity_slider.blockSignals(False)

    def _on_data_change(self, item, col):
        node = item.data(0, Qt.ItemDataRole.UserRole)
        if node:
            before_state = self._gl().begin_history_action()
            is_checked = (item.checkState(0) == Qt.CheckState.Checked)
            node.visible = is_checked
            if isinstance(node, GroupLayer):
                self.tree.blockSignals(True)
                self._set_node_visibility_recursive(item, is_checked)
                self.tree.blockSignals(False)
            self.canvas.update()
            self._gl().end_history_action(before_state, "Toggle Visibility")

    def _set_node_visibility_recursive(self, parent_item, visible):
        for i in range(parent_item.childCount()):
            child_item = parent_item.child(i)
            child_node = child_item.data(0, Qt.ItemDataRole.UserRole)
            
            child_item.setCheckState(0, Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
            if child_node:
                child_node.visible = visible
                
            if isinstance(child_node, GroupLayer):
                self._set_node_visibility_recursive(child_item, visible)

    def _rename_item(self, item, col):
        node = item.data(0, Qt.ItemDataRole.UserRole)
        text, ok = QInputDialog.getText(self, "Rename", "Name:", text=node.name)
        if ok and text:
            before_state = self._gl().begin_history_action()
            node.name = text
            item.setText(0, text)
            if isinstance(node, GroupLayer):
                item.setText(0, f"> {text}")
            self._gl().end_history_action(before_state, "Rename Layer")

    def _add_layer(self):
        before_state = self._gl().begin_history_action()
        curr = self.tree.currentItem()
        target = curr.data(0, Qt.ItemDataRole.UserRole) if curr else self.canvas.root
        
        if isinstance(target, PaintLayer):
            parent = target.parent if target.parent else self.canvas.root
        else:
            parent = target
            
        new_l = PaintLayer(self.canvas.doc_width, self.canvas.doc_height, "New Layer")
        parent.add_child(new_l)
        self.canvas.active_layer = new_l
        self.canvas.layer_structure_changed.emit()
        self.canvas.update()
        self._gl().end_history_action(before_state, "Add Layer")

    def _add_group(self):
        before_state = self._gl().begin_history_action()
        grp = GroupLayer("New Group")
        self.canvas.root.add_child(grp)
        self.canvas.layer_structure_changed.emit()
        self.canvas.active_layer = grp
        self.canvas.update()
        self._gl().end_history_action(before_state, "Add Group")

    def _del_node(self):
        curr = self.tree.currentItem()
        if not curr: return
        node = curr.data(0, Qt.ItemDataRole.UserRole)
        if node and node.parent:
            before_state = self._gl().begin_history_action()
            node.parent.remove_child(node)
            self.canvas.active_layer = None
            self.canvas.layer_structure_changed.emit()
            self.canvas.update()
            self._gl().end_history_action(before_state, "Delete Node")

    def _merge_layers(self):
        """Button handler: smart merge based on what's selected."""
        curr = self.tree.currentItem()
        if not curr:
            QMessageBox.warning(self, "Merge", "Please select a layer or group.")
            return

        node = curr.data(0, Qt.ItemDataRole.UserRole)

        if isinstance(node, GroupLayer) and node != self.canvas.root:
            self._flatten_group(node)
        elif isinstance(node, PaintLayer):
            self._merge_down(node)
        else:
            QMessageBox.information(self, "Merge", "Select a Paint Layer (merge down)\nor a Group (flatten group).")

    def _merge_down(self, layer):
        """Merge the given PaintLayer with the layer directly below it."""
        from src.core.logic import ProjectLogic
        before_state = self._gl().begin_history_action()

        parent = layer.parent if layer.parent else self.canvas.root
        if layer not in parent.children:
            return

        idx = parent.children.index(layer)
        if idx <= 0:
            QMessageBox.warning(self, "Merge Down", "No layer below to merge with.")
            return

        below = parent.children[idx - 1]
        if not isinstance(below, PaintLayer):
            QMessageBox.warning(self, "Merge Down", "The layer below is not a Paint Layer.\nUse 'Flatten Group' instead.")
            return

        gl = self._gl()
        gl.makeCurrent()

        # Merge: below (bottom) + layer (top)
        merged = ProjectLogic.merge_layers(
            [below, layer],
            self.canvas.doc_width,
            self.canvas.doc_height,
            name=layer.name
        )

        # Replace in tree: remove both, insert merged at same position
        parent.remove_child(layer)
        parent.remove_child(below)
        # Insert at the position where 'below' was
        parent.children.insert(min(idx - 1, len(parent.children)), merged)
        merged.parent = parent

        self.canvas.active_layer = merged
        self.canvas.layer_structure_changed.emit()
        self.canvas.update()
        gl.end_history_action(before_state, "Merge Down")

    def _flatten_group(self, group):
        """Flatten a GroupLayer into a single PaintLayer."""
        from src.core.logic import ProjectLogic
        before_state = self._gl().begin_history_action()

        gl = self._gl()
        gl.makeCurrent()

        merged = ProjectLogic.merge_group(
            group,
            self.canvas.doc_width,
            self.canvas.doc_height
        )

        parent = group.parent if group.parent else self.canvas.root
        if group in parent.children:
            idx = parent.children.index(group)
            parent.remove_child(group)
            parent.children.insert(idx, merged)
            merged.parent = parent
        else:
            parent.add_child(merged)

        self.canvas.active_layer = merged
        self.canvas.layer_structure_changed.emit()
        self.canvas.update()
        gl.end_history_action(before_state, "Flatten Group")

    def _merge_all_visible(self):
        """Merge all visible layers into a single new layer."""
        from src.core.logic import ProjectLogic
        before_state = self._gl().begin_history_action()

        gl = self._gl()
        gl.makeCurrent()

        # Collect all visible PaintLayers in render order
        visible_layers = []

        def collect_visible(node):
            if not node.visible:
                return
            if isinstance(node, PaintLayer):
                visible_layers.append(node)
            elif isinstance(node, GroupLayer):
                for child in node.children:
                    collect_visible(child)

        collect_visible(self.canvas.root)

        if not visible_layers:
            QMessageBox.warning(self, "Merge Visible", "No visible layers found.")
            return

        if len(visible_layers) < 2:
            QMessageBox.information(self, "Merge Visible", "Need at least 2 visible layers to merge.")
            return

        merged = ProjectLogic.merge_layers(
            visible_layers,
            self.canvas.doc_width,
            self.canvas.doc_height,
            name="Merged Visible"
        )

        # Remove all original visible layers from their parents
        for layer in visible_layers:
            if layer.parent:
                layer.parent.remove_child(layer)

        # Add merged layer to root
        self.canvas.root.add_child(merged)
        self.canvas.active_layer = merged
        self.canvas.layer_structure_changed.emit()
        self.canvas.update()
        gl.end_history_action(before_state, "Merge Visible")

class PropertyPanel(QWidget):
    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer_layout.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)
        
        group_color = QGroupBox("Color")
        l_color = QVBoxLayout(group_color)
        self.color_picker = ColorPickerWidget()
        
        self.color_picker.colorChanged.connect(lambda rgb: setattr(self.canvas, 'brush_color', rgb))
        self.canvas.brush_color_changed.connect(self.color_picker.set_color)
        
        l_color.addWidget(self.color_picker)
        layout.addWidget(group_color)
        
        group_brush = QGroupBox("Brush Settings")
        l_brush = QVBoxLayout(group_brush)
        def mk_sl(label, minv, maxv, init, func):
            h = QHBoxLayout(); h.addWidget(QLabel(label))
            s = QSlider(Qt.Orientation.Horizontal); s.setRange(minv, maxv); s.setValue(init)
            s.valueChanged.connect(func); h.addWidget(s)
            return h
        
        l_brush.addLayout(mk_sl("Size", 1, 300, 10, lambda v: setattr(self.canvas.current_brush, 'size', v) if self.canvas.current_brush else None))
        l_brush.addLayout(mk_sl("Opacity", 0, 100, 100, lambda v: setattr(self.canvas.current_brush, 'opacity', v/100) if self.canvas.current_brush else None))
        l_brush.addLayout(mk_sl("Flow", 0, 100, 100, lambda v: setattr(self.canvas.current_brush, 'flow', v/100) if self.canvas.current_brush else None))
        smoothing_init = int(getattr(getattr(self.canvas, "stabilizer", None), "smoothing_factor", 0.0) * 100)
        l_brush.addLayout(mk_sl("Stabilize", 0, 95, smoothing_init, self._on_smoothing_change))

        self.btn_pressure_size = QPushButton("Pressure Size Curve")
        self.btn_pressure_size.clicked.connect(lambda: self._toggle_pressure_curve('size'))
        l_brush.addWidget(self.btn_pressure_size)
        self.pressure_size_editor = PressureCurveEditor()
        self.pressure_size_editor.hide()
        self.pressure_size_editor.curveChanged.connect(lambda: self._on_pressure_curve_change('size'))
        l_brush.addWidget(self.pressure_size_editor)

        self.btn_pressure_opacity = QPushButton("Pressure Opacity Curve")
        self.btn_pressure_opacity.clicked.connect(lambda: self._toggle_pressure_curve('opacity'))
        l_brush.addWidget(self.btn_pressure_opacity)
        self.pressure_opacity_editor = PressureCurveEditor()
        self.pressure_opacity_editor.hide()
        self.pressure_opacity_editor.curveChanged.connect(lambda: self._on_pressure_curve_change('opacity'))
        l_brush.addWidget(self.pressure_opacity_editor)
        layout.addWidget(group_brush)
        
        # --- Adjustments Group ---
        group_adj = QGroupBox("Adjustments")
        l_adj = QVBoxLayout(group_adj)
        
        self.btn_grad_map = QPushButton("Gradient Map")
        self.btn_grad_map.clicked.connect(self._open_gradient_map)
        l_adj.addWidget(self.btn_grad_map)
        
        layout.addWidget(group_adj)
        # -------------------------
        
        layout.addStretch()

    def _open_gradient_map(self):
        # Delegate to Central Canvas Logic
        if hasattr(self.canvas, 'gl_canvas'):
            self.canvas.gl_canvas.open_gradient_map()

    def _toggle_pressure_curve(self, curve_type):
        if curve_type == 'size':
            editor = self.pressure_size_editor
            if editor.isVisible():
                editor.hide()
            else:
                editor.show()
                if self.canvas.current_brush:
                    editor.set_curve_points(self.canvas.current_brush.pressure_size_curve)
        else:
            editor = self.pressure_opacity_editor
            if editor.isVisible():
                editor.hide()
            else:
                editor.show()
                if self.canvas.current_brush:
                    editor.set_curve_points(self.canvas.current_brush.pressure_opacity_curve)

    def _on_pressure_curve_change(self, curve_type):
        if not self.canvas.current_brush:
            return
        if curve_type == 'size':
            self.canvas.current_brush.pressure_size_curve = self.pressure_size_editor.get_curve_points()
        else:
            self.canvas.current_brush.pressure_opacity_curve = self.pressure_opacity_editor.get_curve_points()

    def _on_smoothing_change(self, value):
        try:
            if hasattr(self.canvas, "stabilizer") and self.canvas.stabilizer:
                self.canvas.stabilizer.smoothing_factor = max(0.0, min(0.98, value / 100.0))
        except Exception:
            pass


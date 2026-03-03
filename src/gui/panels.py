# src/gui/panels.py

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QTreeWidget, QTreeWidgetItem, QListWidget, QListWidgetItem, 
                             QGroupBox, QLabel, QSlider, QInputDialog, QFrame, QGridLayout,
                             QAbstractItemView, QMenu, QMessageBox)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont, QColor, QIcon, QPixmap, QImage
from src.gui.widgets import ColorPickerWidget
from src.core.brush_manager import BrushConfig
from src.core.logic import GroupLayer, PaintLayer, PaintCommand
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

        # ASCII Icons & Single Column
        tools = [
            ("Rect Select", "[ ]"), 
            ("Lasso", " @ "),
            ("Magic Wand", " ✦ "),
            ("Fill Select", "\\_/"),
            ("Picker", " + "),
            ("Smudge", " ~ "),
            ("Text", " T ")
        ]
        
        _normal_style = (
            "QPushButton { text-align: left; padding-left: 20px; font-family: monospace; font-size: 13px; }"
            "QPushButton:hover { background-color: #e0e0e0; }"
            "QPushButton:checked { background-color: #c0d8f0; border: 1px solid #6aa0d0; }"
        )
        
        for name, icon in tools:
            btn = QPushButton(f"{icon}  {name}")
            btn.setMinimumHeight(45)
            btn.setStyleSheet(_normal_style)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, n=name: self._on_tool_btn_clicked(n))
            layout.addWidget(btn)
            self._tool_buttons[name] = btn

        # === Magic Wand Options Panel (hidden by default) ===
        self.wand_panel = QGroupBox("🪄 AI Wand Options")
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
        self._wand_positive = QRadioButton("✅ Positive")
        self._wand_positive.setChecked(True)
        self._wand_negative = QRadioButton("❌ Negative")
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
        self._wand_undo_btn = QPushButton("↩ Undo")
        self._wand_undo_btn.clicked.connect(self._on_wand_undo)
        btn_row1.addWidget(self._wand_undo_btn)
        self._wand_clear_btn = QPushButton("🗑 Clear")
        self._wand_clear_btn.clicked.connect(self._on_wand_clear)
        btn_row1.addWidget(self._wand_clear_btn)
        wand_layout.addLayout(btn_row1)

        self._wand_apply_btn = QPushButton("✔ Apply Selection")
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

        layout.addStretch()

        # Magic wand tool reference (set by LeftSidebar)
        self._magic_wand_tool = None

    def set_magic_wand_tool_ref(self, tool):
        """Set the magic wand tool reference for panel interaction."""
        self._magic_wand_tool = tool
        if tool:
            tool._status_callback = self._update_wand_status
            # Sync feather slider value to the tool
            tool.feather = self._wand_feather_slider.value()

    def _on_tool_btn_clicked(self, name):
        self._active_tool_name = name
        self._set_active_button(name)
        self.wand_panel.setVisible(name == "Magic Wand")
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

class AIPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5,5,5,5)
        
        lbl = QLabel("AI Features")
        lbl.setStyleSheet("font-weight: bold; color: #666;")
        layout.addWidget(lbl)
        
        self.btn_generate = QPushButton("▲ Auto Generate")
        self.btn_generate.setMinimumHeight(45)
        self.btn_generate.setStyleSheet("""
            QPushButton { text-align: left; padding-left: 20px; font-family: monospace; font-size: 13px; }
            QPushButton:hover { background-color: #e0e0e0; }
        """)
        self.btn_generate.clicked.connect(self.open_generator)
        
        layout.addWidget(self.btn_generate)
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
        layout.addWidget(self.brush_panel, 1)
        
        self._add_divider(layout)

        self.tools_panel = ToolsPanel(self._on_tool_selected_wrapper)
        layout.addWidget(self.tools_panel, 1)

        # Cross-wire brush ↔ tool pressed-state exclusion
        self.brush_panel._on_brush_activated_cb = self._on_brush_activated
        self.tools_panel._on_tool_activated_cb = self._on_tool_activated

        self._add_divider(layout)

        self.ai_panel = AIPanel()
        layout.addWidget(self.ai_panel, 1)

    def _on_tool_selected_wrapper(self, tool_name):
        """Intercept tool selection to wire up the magic wand tool reference."""
        self._original_tool_cb(tool_name)
        
        # Connect magic wand tool reference
        if tool_name == "Magic Wand" and self._canvas:
            gl = self._canvas.gl_canvas if hasattr(self._canvas, 'gl_canvas') else self._canvas
            if gl.active_tool and hasattr(gl.active_tool, 'set_point_mode'):
                self.tools_panel.set_magic_wand_tool_ref(gl.active_tool)
            # Register auto-switch callback so panel updates when tool changes itself
            gl._tool_switched_callback = self._on_tool_auto_switched
        else:
            self.tools_panel.set_magic_wand_tool_ref(None)

    def _on_brush_activated(self):
        """Called when a brush is clicked — clear tool pressed state."""
        self.tools_panel.clear_active_button()
        self.tools_panel.wand_panel.setVisible(False)

    def _on_tool_activated(self):
        """Called when a tool is clicked — clear brush tree selection."""
        self.brush_panel.clear_selection()

    def _on_tool_auto_switched(self, tool_name):
        """Called when a tool (e.g. MagicWand) auto-switches to another tool."""
        self.tools_panel._active_tool_name = tool_name
        self.tools_panel._set_active_button(tool_name)
        self.tools_panel.wand_panel.setVisible(tool_name == "Magic Wand")
        self.tools_panel.set_magic_wand_tool_ref(None)

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
        
        layout = QVBoxLayout(self); layout.setContentsMargins(5,5,5,5)
        
        # --- Opacity Control ---
        op_layout = QHBoxLayout()
        op_layout.addWidget(QLabel("Opacity:"))
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.valueChanged.connect(self._on_opacity_change)
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
            # 1. Perform UI Drop
            original_drop_event(event)
            # 2. Sync Logic
            self._sync_logical_structure()
            
        self.tree.dropEvent = new_drop_event
        # -----------------------------------------------------

        self.tree.itemDoubleClicked.connect(self._rename_item)
        self.tree.currentItemChanged.connect(self._on_select)
        self.tree.itemChanged.connect(self._on_data_change)
        
        layout.addWidget(self.tree)
        
        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("Layer")
        self.btn_group = QPushButton("Group")
        self.btn_del = QPushButton("Del")
        self.btn_add.clicked.connect(self._add_layer)
        self.btn_group.clicked.connect(self._add_group)
        self.btn_del.clicked.connect(self._del_node)
        btn_layout.addWidget(self.btn_add); btn_layout.addWidget(self.btn_group); btn_layout.addWidget(self.btn_del)
        layout.addLayout(btn_layout)

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
                    item.setText(0, f"📁 {node.name}")
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

        gl = self.canvas.gl_canvas if hasattr(self.canvas, 'gl_canvas') else self.canvas
        gl.makeCurrent()

        old_img = layer.get_image()
        new_img = remove_white_background(old_img)

        cmd = PaintCommand(layer, old_img, new_img)
        gl.undo_stack.push(cmd)
        layer.load_from_image(new_img)
        self.canvas.update()
        self.refresh()  # update thumbnail

    def _on_opacity_change(self, value):
        opacity = value / 100.0
        self.lbl_opacity_val.setText(f"{value}%")
        
        if self.canvas.active_layer:
            self.canvas.active_layer.opacity = opacity
            self.canvas.update()

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
            is_checked = (item.checkState(0) == Qt.CheckState.Checked)
            node.visible = is_checked
            if isinstance(node, GroupLayer):
                self.tree.blockSignals(True)
                self._set_node_visibility_recursive(item, is_checked)
                self.tree.blockSignals(False)
            self.canvas.update()

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
            node.name = text
            item.setText(0, text)
            if isinstance(node, GroupLayer):
                item.setText(0, f"📁 {text}")

    def _add_layer(self):
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

    def _add_group(self):
        grp = GroupLayer("New Group")
        self.canvas.root.add_child(grp)
        self.canvas.layer_structure_changed.emit()

    def _del_node(self):
        curr = self.tree.currentItem()
        if not curr: return
        node = curr.data(0, Qt.ItemDataRole.UserRole)
        if node and node.parent:
            node.parent.remove_child(node)
            self.canvas.active_layer = None
            self.canvas.layer_structure_changed.emit()
            self.canvas.update()

class PropertyPanel(QWidget):
    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        layout = QVBoxLayout(self)
        
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
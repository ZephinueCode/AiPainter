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

class ToolsPanel(QWidget):
    def __init__(self, on_tool_selected):
        super().__init__()
        self.on_tool_selected = on_tool_selected
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        lbl = QLabel("Tools")
        lbl.setStyleSheet("font-weight: bold; color: #666;")
        layout.addWidget(lbl)

        # ASCII Icons & Single Column
        tools = [
            ("Rect Select", "[ ]"), 
            ("Lasso", " @ "),
            ("Fill Select", "\\_/"),
            ("Picker", " + "),
            ("Smudge", " ~ "),
            ("Text", " T ")
        ]
        
        for name, icon in tools:
            btn = QPushButton(f"{icon}  {name}")
            btn.setMinimumHeight(45)
            btn.setStyleSheet("""
                QPushButton { text-align: left; padding-left: 20px; font-family: monospace; font-size: 13px; }
                QPushButton:hover { background-color: #e0e0e0; }
            """)
            btn.clicked.connect(lambda checked, n=name: self.on_tool_selected(n))
            layout.addWidget(btn)
        
        layout.addStretch()

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
    def __init__(self, brush_manager, on_brush_selected, on_tool_selected):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.brush_panel = BrushPanel(brush_manager, on_brush_selected)
        layout.addWidget(self.brush_panel, 1)
        
        self._add_divider(layout)

        self.tools_panel = ToolsPanel(on_tool_selected)
        layout.addWidget(self.tools_panel, 1)

        self._add_divider(layout)

        self.ai_panel = AIPanel()
        layout.addWidget(self.ai_panel, 1)

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
            
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _open_gradient_map(self, layer):
        # Set active layer if needed
        if self.canvas.active_layer != layer:
            self.canvas.active_layer = layer
            
        # Delegate to Central Canvas Logic
        if hasattr(self.canvas, 'gl_canvas'):
            self.canvas.gl_canvas.open_gradient_map()

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
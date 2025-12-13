# src/gui/panels.py

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QTreeWidget, QTreeWidgetItem, QListWidget, QListWidgetItem, 
                             QGroupBox, QLabel, QSlider, QInputDialog, QFrame, QGridLayout,
                             QAbstractItemView)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor
from src.gui.widgets import ColorPickerWidget
from src.core.brush_manager import BrushConfig
from src.core.logic import GroupLayer, PaintLayer

class BrushPanel(QWidget):
    def __init__(self, brush_manager, on_brush_selected):
        super().__init__()
        self.brush_manager = brush_manager
        self.on_brush_selected = on_brush_selected
        layout = QVBoxLayout(self); layout.setContentsMargins(5,5,5,5)
        
        lbl = QLabel("Brushes")
        lbl.setStyleSheet("font-weight: bold; color: #666;")
        layout.addWidget(lbl)

        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self._item_changed)
        layout.addWidget(self.list_widget)
        self.refresh_list()

    def refresh_list(self):
        self.list_widget.clear()
        for cat in self.brush_manager.categories:
            if cat in self.brush_manager.brushes:
                cat_item = QListWidgetItem(f"▼ {cat}")
                cat_item.setBackground(QColor("#dcdcdc"))
                cat_item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list_widget.addItem(cat_item)
                for brush in self.brush_manager.brushes[cat]:
                    item = QListWidgetItem(f"   {brush.name}")
                    item.setData(Qt.ItemDataRole.UserRole, brush)
                    self.list_widget.addItem(item)

    def _item_changed(self, current, prev):
        if not current: return
        brush = current.data(Qt.ItemDataRole.UserRole)
        if isinstance(brush, BrushConfig): self.on_brush_selected(brush)

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
        
        content = QLabel("Coming Soon...\n(Reserved for AI)")
        content.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.setStyleSheet("background-color: #eee; border-radius: 5px; color: #999;")
        layout.addWidget(content, 1)

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

# Custom Tree Widget to handle drop events
class LayersTreeWidget(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setIndentation(15)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    def dropEvent(self, event):
        # Default implementation handles the move
        super().dropEvent(event)
        # Notify parent to sync logic
        if self.parent():
             # Find the LayerPanel instance (which is the parent widget usually)
             # But here we can just assume usage context or use signal
             pass

class LayerPanel(QWidget):
    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.canvas.layer_structure_changed.connect(self.refresh)
        
        layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        
        # Use standard QTreeWidget but override dropEvent method on the instance
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(15)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        
        # Override dropEvent to trigger sync
        original_drop_event = self.tree.dropEvent
        def new_drop_event(event):
            original_drop_event(event)
            self._sync_logical_structure()
        self.tree.dropEvent = new_drop_event
        
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

    def refresh(self):
        # Block signals to prevent loops
        self.tree.blockSignals(True)
        self.tree.clear()
        
        def build_tree(ui_parent, node_parent):
            # Render order is Bottom->Top, UI is Top->Bottom, so reverse for UI
            for node in reversed(node_parent.children):
                item = QTreeWidgetItem(ui_parent)
                item.setText(0, node.name)
                item.setData(0, Qt.ItemDataRole.UserRole, node)
                item.setCheckState(0, Qt.CheckState.Checked if node.visible else Qt.CheckState.Unchecked)
                
                # Prevent dragging ONTO PaintLayers (Leaf nodes)
                if isinstance(node, PaintLayer):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
                
                if isinstance(node, GroupLayer):
                    item.setExpanded(True)
                    item.setBackground(0, QColor("#eaeaea"))
                    build_tree(item, node)
                
                if node == self.canvas.active_layer:
                    self.tree.setCurrentItem(item)

        build_tree(self.tree, self.canvas.root)
        self.tree.blockSignals(False)

    def _sync_logical_structure(self):
        """Rebuilds the Canvas logical tree based on the current UI TreeWidget structure."""

        def rebuild_node(tree_item):
            logical_node = tree_item.data(0, Qt.ItemDataRole.UserRole)
            # Important: Clear existing children to rebuild the list
            logical_node.children = [] 
            
            # Tree is Top->Bottom (visual order). 
            # Logic rendering is Bottom->Top (painter's algorithm).
            # So the visual TOP item should be the LAST child in the logical list.
            
            count = tree_item.childCount()
            # Iterate visual children in REVERSE order
            for i in range(count - 1, -1, -1):
                child_item = tree_item.child(i)
                child_node = rebuild_node(child_item)
                # Re-establish parent-child relationship
                logical_node.add_child(child_node)
                
            return logical_node

        # Root handling
        # self.canvas.root is the invisible root
        self.canvas.root.children = []
        count = self.tree.topLevelItemCount()
        
        # Iterate visual top level items in REVERSE
        for i in range(count - 1, -1, -1):
            item = self.tree.topLevelItem(i)
            node = rebuild_node(item)
            self.canvas.root.add_child(node)

        self.canvas.update()

    def _on_select(self, current, prev):
        if not current: return
        node = current.data(0, Qt.ItemDataRole.UserRole)
        self.canvas.active_layer = node

    def _on_data_change(self, item, col):
        node = item.data(0, Qt.ItemDataRole.UserRole)
        if node:
            is_checked = (item.checkState(0) == Qt.CheckState.Checked)
            node.visible = is_checked
            
            # Recursive check update for groups
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

    def _add_layer(self):
        curr = self.tree.currentItem()
        target = curr.data(0, Qt.ItemDataRole.UserRole) if curr else self.canvas.root
        
        # Insert Logic: If PaintLayer, add to parent. If Group, add to Group.
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
        layout.addStretch()
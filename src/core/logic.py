# src/core/logic.py

import os
import json
import uuid
import zipfile
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from psd_tools import PSDImage
from OpenGL.GL import *

# === 数据结构 (Model) ===

class Node:
    """图层树的基础节点"""
    def __init__(self, name="Node", visible=True):
        self.name = name
        self.visible = visible
        self.opacity = 1.0
        self.parent = None
        self.children = []

    def add_child(self, node):
        node.parent = self
        self.children.append(node)

    def remove_child(self, node):
        if node in self.children:
            self.children.remove(node)
            node.parent = None
    
    def to_dict(self):
        return {
            "type": "Node",
            "name": self.name,
            "visible": self.visible,
            "opacity": self.opacity,
            "children": [c.to_dict() for c in self.children]
        }

class GroupLayer(Node):
    def to_dict(self):
        d = super().to_dict()
        d["type"] = "GroupLayer"
        return d

class PaintLayer(Node):
    """具体的绘画图层"""
    def __init__(self, width, height, name="Layer"):
        super().__init__(name)
        self.width = width
        self.height = height
        self.texture = None
        self.fbo = None
        self.uuid = None
        self.setup()

    def setup(self):
        self.texture = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self.texture)
        data = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, self.width, self.height, 0, GL_RGBA, GL_UNSIGNED_BYTE, data)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)

        self.fbo = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, self.fbo)
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, self.texture, 0)
        glClearColor(0,0,0,0); glClear(GL_COLOR_BUFFER_BIT)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
    
    def add_child(self, node):
        # PaintLayer cannot have children
        print("Error: Cannot add child to PaintLayer")
        pass

    def to_dict(self):
        d = super().to_dict()
        d["type"] = "PaintLayer"
        d["width"] = self.width
        d["height"] = self.height
        if not self.uuid:
            self.uuid = str(uuid.uuid4())
        d["uuid"] = self.uuid
        return d

    def load_from_image(self, pil_image):
        if self.width != pil_image.width or self.height != pil_image.height:
            self.width, self.height = pil_image.size
            glBindTexture(GL_TEXTURE_2D, self.texture)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, self.width, self.height, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
            
        glBindTexture(GL_TEXTURE_2D, self.texture)
        if pil_image.mode != 'RGBA':
            pil_image = pil_image.convert('RGBA')
        img_data = pil_image.transpose(Image.FLIP_TOP_BOTTOM).tobytes()
        glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, self.width, self.height, GL_RGBA, GL_UNSIGNED_BYTE, img_data)

    def get_image(self):
        glBindFramebuffer(GL_FRAMEBUFFER, self.fbo)
        data = glReadPixels(0, 0, self.width, self.height, GL_RGBA, GL_UNSIGNED_BYTE)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        img = Image.frombytes("RGBA", (self.width, self.height), data)
        return img.transpose(Image.FLIP_TOP_BOTTOM)

    def cleanup(self):
        if self.texture: glDeleteTextures([self.texture])
        if self.fbo: glDeleteFramebuffers(1, [self.fbo])

class TextLayer(PaintLayer):
    """文字图层，继承自PaintLayer但包含文字属性"""
    def __init__(self, width, height, text="Text", font_size=50, color=(0,0,0,255), x=100, y=100, name="Text Layer"):
        super().__init__(width, height, name)
        self.text_content = text
        self.font_size = font_size
        self.text_color = color
        self.pos_x = x
        self.pos_y = y
        self.update_texture()

    def update_texture(self):
        # 创建透明底图
        img = Image.new("RGBA", (self.width, self.height), (0,0,0,0))
        draw = ImageDraw.Draw(img)
        try:
            # 尝试加载默认字体，实际应用应支持字体选择
            font = ImageFont.truetype("arial.ttf", self.font_size)
        except:
            font = ImageFont.load_default()
        
        draw.text((self.pos_x, self.pos_y), self.text_content, font=font, fill=self.text_color)
        self.load_from_image(img)

    def to_dict(self):
        d = super().to_dict()
        d["type"] = "TextLayer"
        d["text_content"] = self.text_content
        d["font_size"] = self.font_size
        d["text_color"] = self.text_color
        d["pos_x"] = self.pos_x
        d["pos_y"] = self.pos_y
        return d

# === 撤销/重做逻辑 ===
class PaintCommand:
    def __init__(self, layer, old_img, new_img):
        self.layer = layer
        self.old_img = old_img
        self.new_img = new_img
        
    def undo(self):
        if self.layer:
            self.layer.load_from_image(self.old_img)
            
    def redo(self):
        if self.layer:
            self.layer.load_from_image(self.new_img)

class UndoStack:
    def __init__(self, limit=30):
        self.undo_list = []
        self.redo_list = []
        self.limit = limit
        
    def push(self, cmd):
        self.undo_list.append(cmd)
        self.redo_list.clear()
        if len(self.undo_list) > self.limit:
            self.undo_list.pop(0)
    
    def undo(self):
        if not self.undo_list: return False
        cmd = self.undo_list.pop()
        cmd.undo()
        self.redo_list.append(cmd)
        return True
        
    def redo(self):
        if not self.redo_list: return False
        cmd = self.redo_list.pop()
        cmd.redo()
        self.undo_list.append(cmd)
        return True

# === 项目管理逻辑 ===
class ProjectLogic:
    @staticmethod
    def save_project(root_node, width, height, path):
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            project_data = {
                "width": width,
                "height": height,
                "root": root_node.to_dict()
            }
            with open(os.path.join(temp_dir, "project.json"), "w") as f:
                json.dump(project_data, f, indent=2)
            
            def save_layer_images(node):
                if isinstance(node, PaintLayer):
                    img = node.get_image()
                    img.save(os.path.join(temp_dir, f"{node.uuid}.png"))
                if hasattr(node, 'children'):
                    for child in node.children:
                        save_layer_images(child)
            
            save_layer_images(root_node)
            
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(temp_dir):
                    for file in files:
                        zipf.write(os.path.join(root, file), file)

    @staticmethod
    def load_project(path):
        import tempfile
        # 这里简化处理，实际需要更严谨的资源管理
        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(path, 'r') as zipf:
                zipf.extractall(temp_dir)
            
            with open(os.path.join(temp_dir, "project.json"), "r") as f:
                data = json.load(f)
            
            width = data["width"]
            height = data["height"]
            root = GroupLayer("Root")
            
            def build_tree(node_data, parent):
                if node_data["type"] == "GroupLayer":
                    grp = GroupLayer(node_data["name"])
                    grp.visible = node_data["visible"]
                    grp.opacity = node_data["opacity"]
                    parent.add_child(grp)
                    for child_data in node_data["children"]:
                        build_tree(child_data, grp)
                elif node_data["type"] == "PaintLayer" or node_data["type"] == "TextLayer":
                    if node_data["type"] == "TextLayer":
                        l = TextLayer(width, height, 
                                      text=node_data.get("text_content", "Text"),
                                      font_size=node_data.get("font_size", 50),
                                      color=node_data.get("text_color", (0,0,0,255)),
                                      x=node_data.get("pos_x", 0),
                                      y=node_data.get("pos_y", 0),
                                      name=node_data["name"])
                    else:
                        l = PaintLayer(width, height, node_data["name"])
                    
                    l.visible = node_data["visible"]
                    l.opacity = node_data["opacity"]
                    l.uuid = node_data.get("uuid")
                    
                    img_path = os.path.join(temp_dir, f"{l.uuid}.png")
                    if os.path.exists(img_path):
                        pil_img = Image.open(img_path)
                        l.load_from_image(pil_img)
                    parent.add_child(l)

            for child_data in data["root"]["children"]:
                build_tree(child_data, root)
                
            return width, height, root

    @staticmethod
    def import_psd(path, current_width, current_height):
        psd = PSDImage.open(path)
        width = psd.width
        height = psd.height
        root = GroupLayer("Root")

        def process_psd_layer(psd_layer, parent_node):
            if psd_layer.is_group():
                grp = GroupLayer(psd_layer.name)
                grp.visible = psd_layer.visible
                grp.opacity = psd_layer.opacity / 255.0
                parent_node.add_child(grp)
                for child in psd_layer:
                    process_psd_layer(child, grp)
            else:
                pil_img = psd_layer.composite(viewport=(0,0, width, height))
                l = PaintLayer(width, height, psd_layer.name)
                l.visible = psd_layer.visible
                l.opacity = psd_layer.opacity / 255.0
                l.load_from_image(pil_img)
                parent_node.add_child(l)

        for layer in reversed(list(psd)):
            process_psd_layer(layer, root)
            
        return width, height, root
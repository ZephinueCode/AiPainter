# src/core/logic.py

import os
import json
import uuid
import zipfile
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from psd_tools import PSDImage
from OpenGL.GL import *
import sys

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
    
    def to_pil(self):
        #将当前图层的 OpenGL 纹理读回并转换为 PIL Image (RGBA)
        glBindTexture(GL_TEXTURE_2D, self.texture)
        # 获取纹理数据
        data = glGetTexImage(GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE)
        
        # 转换为 numpy 数组
        img_np = np.frombuffer(data, dtype=np.uint8).reshape((self.height, self.width, 4))
        
        # OpenGL 坐标系 y 轴是反的，需要垂直翻转
        img_pil = Image.fromarray(img_np).transpose(Image.FLIP_TOP_BOTTOM)
        return img_pil

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
    """文字图层"""
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
        
        # 尝试加载字体
        font = None
        # 常见的系统字体路径
        possible_fonts = [
            "arial.ttf", 
            "Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "C:\\Windows\\Fonts\\arial.ttf",
            "/System/Library/Fonts/Helvetica.ttc"
        ]
        
        for f in possible_fonts:
            try:
                font = ImageFont.truetype(f, self.font_size)
                break
            except:
                continue
                
        if font is None:
            # Fallback to default (size is ignored here usually)
            print("Warning: Could not load TTF font, using default bitmap font (size adjustment won't work)")
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

# ... (Rest of logic.py remains unchanged) ...
class PaintCommand:
    def __init__(self, layer, old_img, new_img):
        self.layer = layer
        self.old_img = old_img
        self.new_img = new_img
    def undo(self):
        if self.layer: self.layer.load_from_image(self.old_img)
    def redo(self):
        if self.layer: self.layer.load_from_image(self.new_img)

class UndoStack:
    def __init__(self, limit=30):
        self.undo_list = []
        self.redo_list = []
        self.limit = limit
    def push(self, cmd):
        self.undo_list.append(cmd); self.redo_list.clear()
        if len(self.undo_list) > self.limit: self.undo_list.pop(0)
    def undo(self):
        if not self.undo_list: return False
        cmd = self.undo_list.pop(); cmd.undo(); self.redo_list.append(cmd); return True
    def redo(self):
        if not self.redo_list: return False
        cmd = self.redo_list.pop(); cmd.redo(); self.undo_list.append(cmd); return True

class ProjectLogic:
    @staticmethod
    def save_project(root_node, width, height, path):
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            project_data = { "width": width, "height": height, "root": root_node.to_dict() }
            with open(os.path.join(temp_dir, "project.json"), "w") as f: json.dump(project_data, f, indent=2)
            def save_layer_images(node):
                if isinstance(node, PaintLayer):
                    img = node.get_image()
                    # --- 调试代码 ---
                    save_path = os.path.join(temp_dir, f"{node.uuid}.png")
                    print(f"正在保存图层: {node.name}, UUID: {node.uuid}, 路径: {save_path}")
                    # ----------------
                    img.save(os.path.join(temp_dir, f"{node.uuid}.png"))
                if hasattr(node, 'children'):
                    for child in node.children: save_layer_images(child)
            save_layer_images(root_node)
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(temp_dir):
                    for file in files: zipf.write(os.path.join(root, file), file)

    @staticmethod
    def load_project(path):
        import tempfile
        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(path, 'r') as zipf: zipf.extractall(temp_dir)
            with open(os.path.join(temp_dir, "project.json"), "r") as f: data = json.load(f)
            width = data["width"]; height = data["height"]; root = GroupLayer("Root")
            def build_tree(node_data, parent):
                if node_data["type"] == "GroupLayer":
                    grp = GroupLayer(node_data["name"]); grp.visible = node_data["visible"]; grp.opacity = node_data["opacity"]
                    parent.add_child(grp)
                    for child_data in node_data["children"]: build_tree(child_data, grp)
                elif node_data["type"] == "PaintLayer" or node_data["type"] == "TextLayer":
                    if node_data["type"] == "TextLayer":
                        l = TextLayer(width, height, text=node_data.get("text_content", "Text"), font_size=node_data.get("font_size", 50), color=node_data.get("text_color", (0,0,0,255)), x=node_data.get("pos_x", 0), y=node_data.get("pos_y", 0), name=node_data["name"])
                    else: l = PaintLayer(width, height, node_data["name"])
                    l.visible = node_data["visible"]; l.opacity = node_data["opacity"]; l.uuid = node_data.get("uuid")
                    img_path = os.path.join(temp_dir, f"{l.uuid}.png")
                    if os.path.exists(img_path): pil_img = Image.open(img_path); l.load_from_image(pil_img)
                    parent.add_child(l)
            for child_data in data["root"]["children"]: build_tree(child_data, root)
            return width, height, root
        
    @staticmethod
    def open_img(path):
        try:
            img = Image.open(path)
            if img.mode != "RGBA":
                img = img.convert("RGBA")

            img_data = img.tobytes("raw", "RGBA", 0, -1)
            width, height = img.size

            #生成 OpenGL 纹理
            texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, texture_id)

            
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

            
            glTexImage2D(
                GL_TEXTURE_2D, 0, GL_RGBA, width, height, 
                0, GL_RGBA, GL_UNSIGNED_BYTE, img_data
            )

            glBindTexture(GL_TEXTURE_2D, 0)
            return texture_id, width, height

        except Exception as e:
            print(f"无法加载图片 {path}: {e}")
            return None, 0, 0

    @staticmethod
    def import_psd(path, current_width, current_height):
        psd = PSDImage.open(path); width = psd.width; height = psd.height; root = GroupLayer("Root")
        def process_psd_layer(psd_layer, parent_node):
            if psd_layer.is_group():
                grp = GroupLayer(psd_layer.name); grp.visible = psd_layer.visible; grp.opacity = psd_layer.opacity / 255.0
                parent_node.add_child(grp)
                for child in psd_layer: process_psd_layer(child, grp)
            else:
                pil_img = psd_layer.composite(viewport=(0,0, width, height))
                l = PaintLayer(width, height, psd_layer.name); l.visible = psd_layer.visible; l.opacity = psd_layer.opacity / 255.0
                l.load_from_image(pil_img); parent_node.add_child(l)
        for layer in reversed(list(psd)): process_psd_layer(layer, root)
        return width, height, root
    
    @staticmethod
    def create_group_from_images(images, names, doc_width, doc_height):
        """
        将 PIL.Image 列表转换为一个 GroupLayer
        """
        # 创建一个组，名字随机防止冲突
        group_name = f"AI_Gen_{uuid.uuid4().hex[:4]}"
        group = GroupLayer(group_name)
        
        # 倒序遍历：因为在图层树中，列表第一个通常是背景(最底层)
        # 但在 add_child 时如果直接 append，第一个加进去的在列表最前
        # 你的渲染顺序 logic 决定了 background 应该在 list 的哪里
        # 假设: canvas渲染是按 list 顺序渲染，则 list[0] 是背景
        
        for img, name in zip(images, names):
            # 1. 创建图层
            layer = PaintLayer(doc_width, doc_height, name)
            layer.uuid = str(uuid.uuid4())
            
            # 2. 智能缩放 (如果 AI 生成的是 1024x1024，而画布是 1920x1080)
            # 这里选择保持比例居中，或者拉伸，取决于需求。这里演示拉伸填满：
            if img.size != (doc_width, doc_height):
                img = img.resize((doc_width, doc_height), Image.Resampling.LANCZOS)
            
            # 3. 载入纹理 (这一步需要 OpenGL 上下文，所以必须在主线程调用)
            layer.load_from_image(img)
            
            # 4. 加入组
            group.add_child(layer)
            
        return group
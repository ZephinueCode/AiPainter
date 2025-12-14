# src/core/brush_manager.py

import json
import os
import shutil
import math
from dataclasses import dataclass, field
from PIL import Image, ImageDraw

@dataclass
class BrushConfig:
    name: str
    category: str  # Pencil, Ink, Paint, Airbrush, Other
    size: int
    opacity: float # 0.0 - 1.0
    flow: float    # 0.0 - 1.0
    spacing: float # 0.0 - 1.0
    hardness: float # 0.0 - 1.0
    blend_mode: str # "Normal", "Eraser"
    texture: Image.Image = field(default=None, repr=False) # Runtime PIL Image

class BrushManager:
    def __init__(self, brush_dir="brushes"):
        self.brushes = {}
        self.categories = ["Pencil", "Ink", "Paint", "Airbrush", "Other"]
        self.brush_dir = brush_dir
        self.load_brushes()

    def load_brushes(self):
        """Loads brushes from folders. Structure: brushes/MyBrush/config.json & texture.png"""
        if not os.path.exists(self.brush_dir):
            os.makedirs(self.brush_dir)
            self._create_defaults()

        self.brushes = {}
        
        # Scan subdirectories
        for entry in os.scandir(self.brush_dir):
            if entry.is_dir():
                self._load_brush_from_dir(entry.path)
            elif entry.name.endswith(".json"):
                # Legacy support (optional)
                pass

    def _load_brush_from_dir(self, path):
        config_path = os.path.join(path, "config.json")
        texture_path = os.path.join(path, "texture.png")
        
        if not os.path.exists(config_path):
            return

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Filter compatible keys
            valid_keys = BrushConfig.__annotations__.keys()
            filtered_data = {k: v for k, v in data.items() if k in valid_keys and k != 'texture'}
            
            brush = BrushConfig(**filtered_data)
            
            # Load Texture
            if os.path.exists(texture_path):
                brush.texture = Image.open(texture_path).convert("L")
            else:
                # Synthesize default if missing (default to round)
                brush.texture = self._generate_default_texture(brush.size, brush.hardness, shape="round")
            
            if brush.category not in self.brushes:
                self.brushes[brush.category] = []
            self.brushes[brush.category].append(brush)
            
        except Exception as e:
            print(f"Error loading brush from {path}: {e}")

    def _generate_default_texture(self, size, hardness, shape="round"):
        """Synthesizes a grayscale brush tip (round or square)."""
        res = 128
        img = Image.new("L", (res, res), 0)
        
        center = res / 2
        max_dist = res / 2 * 0.95
        
        # Hardness 1.0 -> Crisp edge
        # Hardness 0.0 -> Soft falloff
        
        for y in range(res):
            for x in range(res):
                if shape == "square":
                    # Chebyshev distance for square (max of dx, dy)
                    dx = abs(x - center)
                    dy = abs(y - center)
                    dist = max(dx, dy)
                else:
                    # Euclidean distance for circle
                    dx = x - center
                    dy = y - center
                    dist = math.sqrt(dx*dx + dy*dy)

                norm_dist = dist / max_dist
                
                if norm_dist > 1.0:
                    val = 0
                elif norm_dist < hardness:
                    val = 255
                else:
                    # Fade from hardness edge to 1.0
                    if hardness >= 1.0:
                        val = 255
                    else:
                        span = 1.0 - hardness
                        factor = (norm_dist - hardness) / span
                        val = int(255 * (1.0 - factor))
                
                if val > 0:
                    img.putpixel((x, y), val)
                    
        return img

    def _create_defaults(self):
        """Creates a set of default brushes with Round and Square variations for testing."""
        base_configs = [
            {"name": "2B Pencil", "category": "Pencil", "size": 4, "opacity": 0.9, "flow": 1.0, "spacing": 0.15, "hardness": 0.8, "blend_mode": "Normal"},
            {"name": "G-Pen", "category": "Ink", "size": 6, "opacity": 1.0, "flow": 1.0, "spacing": 0.05, "hardness": 1.0, "blend_mode": "Normal"},
            {"name": "Thick Oil", "category": "Paint", "size": 50, "opacity": 1.0, "flow": 0.15, "spacing": 0.05, "hardness": 0.6, "blend_mode": "Normal"},
            {"name": "Airbrush Soft", "category": "Airbrush", "size": 80, "opacity": 0.5, "flow": 0.4, "spacing": 0.1, "hardness": 0.0, "blend_mode": "Normal"},
            {"name": "Hard Eraser", "category": "Other", "size": 30, "opacity": 1.0, "flow": 1.0, "spacing": 0.1, "hardness": 1.0, "blend_mode": "Eraser"},
        ]
        
        shapes = ["Round", "Square"]
        
        for base in base_configs:
            for shape in shapes:
                # Create variation config
                d = base.copy()
                d['name'] = f"{base['name']} {shape}"
                
                safe_name = d['name'].replace(" ", "_").lower()
                folder_path = os.path.join(self.brush_dir, safe_name)
                os.makedirs(folder_path, exist_ok=True)
                
                # Save Config
                with open(os.path.join(folder_path, "config.json"), 'w') as f:
                    json.dump(d, f, indent=4)
                
                # Generate and Save Texture
                cfg = BrushConfig(**d)
                # Pass shape to generator (lowercase)
                tex = self._generate_default_texture(cfg.size, cfg.hardness, shape=shape.lower())
                tex.save(os.path.join(folder_path, "texture.png"))
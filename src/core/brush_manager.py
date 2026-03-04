# src/core/brush_manager.py

import json
import os
import shutil
import math
import zlib
from dataclasses import dataclass, field
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

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
    texture_shape: str = "round"  # round, square, triangle, leaf, splatter
    texture_grain: float = 0.0    # 0.0 - 1.0
    # Pressure curves are stored as control points in 0-255 space:
    # [(x0, y0), (x1, y1), ...]. None means linear mapping.
    pressure_size_curve: list = None
    pressure_opacity_curve: list = None
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
        # Always ensure built-in brushes exist, including new additions.
        # Existing user brushes are preserved (no overwrite).
        self._create_defaults()

        self.brushes = {}
        seen = set()
        
        # Scan subdirectories
        for entry in sorted(os.scandir(self.brush_dir), key=lambda e: e.name.lower()):
            if entry.is_dir():
                self._load_brush_from_dir(entry.path, seen)
            elif entry.name.endswith(".json"):
                # Legacy support (optional)
                pass

    def _load_brush_from_dir(self, path, seen=None):
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
            key = (brush.category.strip().lower(), brush.name.strip().lower())
            if seen is not None and key in seen:
                return
            
            # Load Texture
            if os.path.exists(texture_path):
                brush.texture = Image.open(texture_path).convert("L")
            else:
                # Synthesize default texture if missing from config.
                brush.texture = self._generate_default_texture(
                    brush.size,
                    brush.hardness,
                    shape=brush.texture_shape or "round",
                    grain=float(getattr(brush, "texture_grain", 0.0) or 0.0),
                    seed=self._stable_seed(brush.name),
                )
            
            if brush.category not in self.brushes:
                self.brushes[brush.category] = []
            self.brushes[brush.category].append(brush)
            if seen is not None:
                seen.add(key)
            
        except Exception as e:
            print(f"Error loading brush from {path}: {e}")

    def _stable_seed(self, text):
        return int(zlib.crc32((text or "").encode("utf-8")) & 0xFFFFFFFF)

    def _generate_default_texture(self, size, hardness, shape="round", grain=0.0, seed=0):
        """Synthesize a grayscale brush tip with selectable texture shapes."""
        res = 128
        margin = 8
        shape = (shape or "round").lower()
        img = Image.new("L", (res, res), 0)
        draw = ImageDraw.Draw(img)

        if shape == "square":
            draw.rectangle((margin, margin, res - margin, res - margin), fill=255)
        elif shape == "triangle":
            draw.polygon(
                [
                    (res * 0.5, margin),
                    (res - margin, res - margin),
                    (margin, res - margin),
                ],
                fill=255,
            )
        elif shape == "leaf":
            points = []
            cx = cy = (res - 1) / 2.0
            rx = res * 0.42
            ry = res * 0.28
            for i in range(0, 361, 8):
                t = math.radians(i)
                x = math.cos(t)
                y = math.sin(t) * (0.65 + 0.35 * (1.0 - abs(math.cos(t))))
                points.append((cx + x * rx, cy + y * ry))
            draw.polygon(points, fill=255)
        elif shape == "splatter":
            rng = np.random.default_rng(int(seed) if seed else 12345)
            drops = int(24 + max(0, min(16, size // 8)))
            min_r = max(2, res // 36)
            max_r = max(min_r + 1, res // 7)
            for _ in range(drops):
                r = int(rng.integers(min_r, max_r))
                x = int(rng.integers(margin, res - margin))
                y = int(rng.integers(margin, res - margin))
                alpha = int(rng.integers(110, 255))
                draw.ellipse((x - r, y - r, x + r, y + r), fill=alpha)
            img = img.filter(ImageFilter.GaussianBlur(1.2))
        else:
            draw.ellipse((margin, margin, res - margin, res - margin), fill=255)

        # Hardness: higher = sharper edge, lower = softer edge.
        if shape != "splatter":
            softness = max(0.0, min(1.0, 1.0 - float(hardness)))
            blur_radius = softness * 7.0
            if blur_radius > 0.05:
                img = img.filter(ImageFilter.GaussianBlur(blur_radius))

        # Grain: useful for pencils/charcoal.
        arr = np.asarray(img, dtype=np.float32) / 255.0
        g = max(0.0, min(1.0, float(grain)))
        if g > 0.0:
            rng = np.random.default_rng((int(seed) + 97) if seed else 97)
            noise = rng.uniform(0.45, 1.0, size=arr.shape).astype(np.float32)
            arr *= (1.0 - g) + g * noise
            # Emphasize grain near stroke edge.
            edge_band = np.clip(1.0 - np.abs(arr - 0.5) * 2.0, 0.0, 1.0)
            arr *= 1.0 - 0.35 * g * edge_band

        arr = np.clip(arr, 0.0, 1.0)
        img = Image.fromarray((arr * 255).astype(np.uint8), mode="L")
        return img

    def _create_defaults(self):
        """Create/refresh built-in brushes with practical non-duplicated presets."""
        base_configs = [
            # Pencil: soft edge + visible grain.
            {"name": "2B Pencil (Grain)", "category": "Pencil", "size": 4, "opacity": 0.9, "flow": 1.0, "spacing": 0.14, "hardness": 0.45, "blend_mode": "Normal", "texture_shape": "round", "texture_grain": 0.58},
            {"name": "Mechanical Pencil", "category": "Pencil", "size": 2, "opacity": 0.85, "flow": 1.0, "spacing": 0.08, "hardness": 0.62, "blend_mode": "Normal", "texture_shape": "round", "texture_grain": 0.42},
            {"name": "Charcoal Stick", "category": "Pencil", "size": 12, "opacity": 0.72, "flow": 0.72, "spacing": 0.18, "hardness": 0.25, "blend_mode": "Normal", "texture_shape": "splatter", "texture_grain": 0.70},
            # Inking: hard edge + precise.
            {"name": "G-Pen Line", "category": "Ink", "size": 6, "opacity": 1.0, "flow": 1.0, "spacing": 0.04, "hardness": 1.0, "blend_mode": "Normal", "texture_shape": "triangle", "texture_grain": 0.0},
            {"name": "Fine Liner", "category": "Ink", "size": 3, "opacity": 1.0, "flow": 1.0, "spacing": 0.03, "hardness": 1.0, "blend_mode": "Normal", "texture_shape": "triangle", "texture_grain": 0.0},
            {"name": "Brush Pen", "category": "Ink", "size": 10, "opacity": 0.95, "flow": 0.9, "spacing": 0.06, "hardness": 0.9, "blend_mode": "Normal", "texture_shape": "leaf", "texture_grain": 0.08},
            {"name": "Marker Broad", "category": "Ink", "size": 18, "opacity": 0.85, "flow": 0.65, "spacing": 0.08, "hardness": 0.85, "blend_mode": "Normal", "texture_shape": "leaf", "texture_grain": 0.12},
            {"name": "Thick Oil", "category": "Paint", "size": 50, "opacity": 1.0, "flow": 0.15, "spacing": 0.05, "hardness": 0.6, "blend_mode": "Normal", "texture_shape": "round", "texture_grain": 0.18},
            {"name": "Watercolor Wash", "category": "Paint", "size": 42, "opacity": 0.55, "flow": 0.35, "spacing": 0.12, "hardness": 0.2, "blend_mode": "Normal", "texture_shape": "splatter", "texture_grain": 0.35},
            {"name": "Flat Brush", "category": "Paint", "size": 28, "opacity": 0.9, "flow": 0.6, "spacing": 0.06, "hardness": 0.85, "blend_mode": "Normal", "texture_shape": "leaf", "texture_grain": 0.08},
            {"name": "Airbrush Soft", "category": "Airbrush", "size": 80, "opacity": 0.5, "flow": 0.4, "spacing": 0.1, "hardness": 0.0, "blend_mode": "Normal", "texture_shape": "round", "texture_grain": 0.12},
            {"name": "Airbrush Detail", "category": "Airbrush", "size": 22, "opacity": 0.45, "flow": 0.45, "spacing": 0.07, "hardness": 0.2, "blend_mode": "Normal", "texture_shape": "splatter", "texture_grain": 0.16},
            {"name": "Hard Eraser", "category": "Other", "size": 30, "opacity": 1.0, "flow": 1.0, "spacing": 0.1, "hardness": 1.0, "blend_mode": "Eraser", "texture_shape": "round", "texture_grain": 0.0},
            {"name": "Soft Eraser", "category": "Other", "size": 45, "opacity": 0.7, "flow": 0.8, "spacing": 0.09, "hardness": 0.2, "blend_mode": "Eraser", "texture_shape": "leaf", "texture_grain": 0.0},
        ]

        # Clean legacy duplicated presets created by old Round/Square generator.
        legacy_names = [
            "2B Pencil",
            "Mechanical Pencil",
            "Charcoal",
            "G-Pen",
            "Fine Liner",
            "Marker",
            "Thick Oil",
            "Watercolor Soft",
            "Flat Brush",
            "Airbrush Soft",
            "Airbrush Detail",
            "Hard Eraser",
            "Soft Eraser",
        ]
        for legacy in legacy_names:
            legacy_safe = legacy.replace(" ", "_").lower()
            for suffix in ("_round", "_square"):
                p = os.path.join(self.brush_dir, legacy_safe + suffix)
                if os.path.isdir(p):
                    try:
                        shutil.rmtree(p)
                    except Exception:
                        pass

        for d in base_configs:
            safe_name = d["name"].replace(" ", "_").lower()
            folder_path = os.path.join(self.brush_dir, safe_name)
            os.makedirs(folder_path, exist_ok=True)
            config_path = os.path.join(folder_path, "config.json")
            texture_path = os.path.join(folder_path, "texture.png")

            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=4)

            cfg = BrushConfig(**d)
            tex = self._generate_default_texture(
                cfg.size,
                cfg.hardness,
                shape=cfg.texture_shape,
                grain=cfg.texture_grain,
                seed=self._stable_seed(cfg.name),
            )
            tex.save(texture_path)

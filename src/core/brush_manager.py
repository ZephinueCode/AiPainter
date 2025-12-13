# src/utils/brush_manager.py

import json
import os
from dataclasses import dataclass

@dataclass
class BrushConfig:
    name: str
    category: str  # Pencil, Ink, Paint, Airbrush, Other
    size: int
    opacity: float # 0.0 - 1.0 (透明度)
    flow: float    # 0.0 - 1.0 (流量，油画堆积感)
    spacing: float # 0.0 - 1.0 (步长)
    hardness: float # 0.0 - 1.0 (边缘硬度)
    blend_mode: str # "Normal", "Eraser"

class BrushManager:
    def __init__(self, brush_dir="brushes"):
        self.brushes = {}
        self.categories = ["Pencil", "Ink", "Paint", "Airbrush", "Other"]
        self.load_brushes(brush_dir)

    def load_brushes(self, brush_dir):
        if not os.path.exists(brush_dir):
            os.makedirs(brush_dir)
            self._create_defaults(brush_dir)

        self.brushes = {}
        for f in os.listdir(brush_dir):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(brush_dir, f), 'r', encoding='utf-8') as file:
                        data = json.load(file)
                        cfg = BrushConfig(**data)
                        if cfg.category not in self.brushes:
                            self.brushes[cfg.category] = []
                        self.brushes[cfg.category].append(cfg)
                except Exception as e:
                    print(f"Error loading {f}: {e}")

    def _create_defaults(self, path):
        defaults = [
            {"name": "2B Pencil", "category": "Pencil", "size": 4, "opacity": 0.9, "flow": 1.0, "spacing": 0.15, "hardness": 0.8, "blend_mode": "Normal"},
            {"name": "G-Pen", "category": "Ink", "size": 6, "opacity": 1.0, "flow": 1.0, "spacing": 0.05, "hardness": 1.0, "blend_mode": "Normal"},
            {"name": "Thick Oil", "category": "Paint", "size": 50, "opacity": 1.0, "flow": 0.15, "spacing": 0.05, "hardness": 0.6, "blend_mode": "Normal"},
            {"name": "Airbrush Soft", "category": "Airbrush", "size": 80, "opacity": 0.5, "flow": 0.4, "spacing": 0.1, "hardness": 0.0, "blend_mode": "Normal"},
            {"name": "Hard Eraser", "category": "Other", "size": 30, "opacity": 1.0, "flow": 1.0, "spacing": 0.1, "hardness": 1.0, "blend_mode": "Eraser"},
        ]
        for d in defaults:
            fname = d['name'].lower().replace(" ", "_") + ".json"
            with open(os.path.join(path, fname), 'w') as f:
                json.dump(d, f, indent=4)
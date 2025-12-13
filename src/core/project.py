# src/core/project.py

from dataclasses import dataclass

@dataclass
class ProjectConfig:
    width: int = 1920
    height: int = 1080
    bg_color: list = None
    name: str = "Untitled"

    def __post_init__(self):
        if self.bg_color is None:
            self.bg_color = [1.0, 1.0, 1.0, 1.0]
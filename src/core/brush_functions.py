import numpy as np
from PyQt6.QtCore import QPointF


class StrokeStabilizer:
    """Simple EMA-based stroke stabilizer."""

    def __init__(self, smoothing_factor=0.0):
        # 0.0 = no smoothing, 1.0 ~= heavy lag (internally clamped)
        self.smoothing_factor = float(smoothing_factor)
        self.reset()

    def reset(self):
        self.current_pos = None

    def update(self, input_pos):
        x, y = float(input_pos.x()), float(input_pos.y())
        target = np.array([x, y], dtype=np.float32)

        if self.current_pos is None:
            self.current_pos = target
            return QPointF(x, y)

        alpha = max(0.0, min(0.98, float(self.smoothing_factor)))
        if alpha < 0.01:
            self.current_pos = target
            return QPointF(x, y)

        self.current_pos = self.current_pos * alpha + target * (1.0 - alpha)
        return QPointF(float(self.current_pos[0]), float(self.current_pos[1]))

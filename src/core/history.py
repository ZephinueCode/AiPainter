"""History command types for global undo/redo."""


class CanvasStateCommand:
    """Undo/redo command backed by full canvas state snapshots."""

    def __init__(self, canvas, before_state, after_state, label=""):
        self.canvas = canvas
        self.before_state = before_state
        self.after_state = after_state
        self.label = label

    def undo(self):
        self.canvas.apply_history_state(self.before_state)

    def redo(self):
        self.canvas.apply_history_state(self.after_state)

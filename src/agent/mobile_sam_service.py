# src/agent/mobile_sam_service.py

"""
MobileSAM service module.
Handles model downloading, loading and inference via a singleton accessor.
"""

import os
import sys
import io
import tempfile
import numpy as np
from pathlib import Path
from PyQt6.QtCore import QObject, pyqtSignal, QThread
from PIL import Image


class ModelLoadThread(QThread):
    """Background thread for downloading / loading the MobileSAM model."""
    progress_msg = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str, object)  # (success?, message, model_or_None)

    def __init__(self, model_name='models/mobile_sam.pt'):
        super().__init__()
        self.model_name = model_name

    def run(self):
        try:
            self.progress_msg.emit("Loading MobileSAM model (first run will download ~10 MB)...")
            Path(self.model_name).parent.mkdir(parents=True, exist_ok=True)

            from ultralytics import SAM
            model = SAM(self.model_name)

            self.progress_msg.emit("MobileSAM model loaded successfully.")
            self.finished_signal.emit(True, "Model loaded", model)
        except ImportError:
            self.finished_signal.emit(
                False,
                "ultralytics is not installed. Run: pip install ultralytics",
                None,
            )
        except OSError as e:
            # Catch DLL / c10.dll load errors specifically
            self.finished_signal.emit(
                False,
                f"OS/DLL error while loading model: {e}\n"
                "Try reinstalling PyTorch with: pip install torch --force-reinstall",
                None,
            )
        except Exception as e:
            self.finished_signal.emit(False, f"Failed to load model: {e}", None)


class SAMInferenceThread(QThread):
    """Background thread for SAM inference to avoid blocking the UI."""
    result_ready = pyqtSignal(object)   # numpy array (H x W, 0/255) or None
    error_occurred = pyqtSignal(str)

    def __init__(self, model, image_path, points, labels):
        super().__init__()
        self.model = model
        self.image_path = image_path
        self.points = points    # [[x,y], ...]
        self.labels = labels    # [1, 0, ...]

    def run(self):
        try:
            # ultralytics SAM multi-point format:
            #   points=[[[x1,y1],[x2,y2],...]]   (triple nested)
            #   labels=[[l1,l2,...]]              (double nested)
            results = self.model.predict(
                self.image_path,
                points=[self.points],
                labels=[self.labels]
            )

            if results and len(results) > 0:
                result = results[0]
                if result.masks is not None and len(result.masks.data) > 0:
                    mask = result.masks.data[0].cpu().numpy()  # float 0~1
                    mask_uint8 = (mask * 255).astype(np.uint8)
                    self.result_ready.emit(mask_uint8)
                    return

            self.result_ready.emit(None)
        except Exception as e:
            self.error_occurred.emit(str(e))


class MobileSAMService(QObject):
    """
    MobileSAM service – singleton.
    Manages the model lifecycle (download / load / unload) and inference.
    """

    # Signals
    model_loading_msg = pyqtSignal(str)         # progress text
    model_load_finished = pyqtSignal(bool, str)  # (success?, message)

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        if MobileSAMService._instance is not None:
            return
        super().__init__()
        self._model = None
        self._loading = False
        self._load_thread = None
        self._model_name = str(Path("models") / "mobile_sam.pt")
        self._legacy_model_name = "mobile_sam.pt"

    # ── Properties ─────────────────────────────────────────

    @property
    def is_loaded(self):
        return self._model is not None

    @property
    def is_loading(self):
        return self._loading

    # ── Model File Management ────────────────────────────

    def get_model_path(self):
        """Try to locate the downloaded model file."""
        possible = [
            Path(self._model_name),
            Path(self._legacy_model_name),
            Path.home() / '.ultralytics' / Path(self._model_name).name,
        ]
        # Windows AppData
        if sys.platform == 'win32':
            possible.append(Path(os.environ.get('APPDATA', '')) / 'ultralytics' / Path(self._model_name).name)
            possible.append(Path(os.environ.get('USERPROFILE', '')) / Path(self._model_name).name)

        for p in possible:
            if p.exists():
                return str(p)
        return None

    def is_model_downloaded(self):
        return self.get_model_path() is not None

    def get_model_size_str(self):
        path = self.get_model_path()
        if path and os.path.exists(path):
            size_mb = os.path.getsize(path) / (1024 * 1024)
            return f"{size_mb:.1f} MB"
        return "Not downloaded"

    def delete_model(self):
        """Delete the downloaded model and release memory."""
        self._model = None
        path = self.get_model_path()
        if path and os.path.exists(path):
            try:
                os.remove(path)
                return True, "Model file deleted"
            except Exception as e:
                return False, f"Delete failed: {e}"
        return False, "Model file not found"

    # ── Async Loading ────────────────────────────────────

    def load_model_async(self):
        """Load the model asynchronously (auto-downloads on first run)."""
        if self._model is not None:
            self.model_load_finished.emit(True, "Model already loaded")
            return
        if self._loading:
            return

        self._loading = True
        self._load_thread = ModelLoadThread(self._model_name)
        self._load_thread.progress_msg.connect(self._on_progress)
        self._load_thread.finished_signal.connect(self._on_load_done)
        self._load_thread.start()

    def _on_progress(self, msg):
        self.model_loading_msg.emit(msg)

    def _on_load_done(self, success, msg, model):
        self._loading = False
        if success and model is not None:
            self._model = model
        self.model_load_finished.emit(success, msg)
        self._load_thread = None

    # ── Inference ─────────────────────────────────────────

    def create_inference_thread(self, image_path, points, labels):
        """
        Create an inference thread (caller is responsible for connecting
        signals and starting it).

        Args:
            image_path: Path to a temporary PNG file.
            points:     [[x,y], ...] pixel coordinate list.
            labels:     [1, 0, ...] label list (1=positive, 0=negative).

        Returns:
            SAMInferenceThread, or None if the model is not loaded.
        """
        if self._model is None:
            return None
        return SAMInferenceThread(self._model, image_path, list(points), list(labels))

    @staticmethod
    def pil_to_temp_png(pil_image):
        """Save a PIL Image to a temporary PNG and return its path."""
        fd, path = tempfile.mkstemp(suffix='.png')
        os.close(fd)
        if pil_image.mode != 'RGBA':
            pil_image = pil_image.convert('RGBA')
        pil_image.save(path, "PNG")
        return path

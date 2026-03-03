# src/agent/inpaint_service.py

"""
Inpaint / Image-Edit service for AiPainter.

Two modes:
  1. Wanx Inpaint  – mask-based local repaint (wanx2.1-imageedit)
  2. Qwen Edit     – prompt-only whole-image edit (qwen-image-edit-plus)

Both run in a background QThread and emit signals for progress / result.
"""

import io
import os
import tempfile
import requests
import base64
import mimetypes

from PyQt6.QtCore import QThread, pyqtSignal
from PIL import Image

try:
    from dashscope import ImageSynthesis, MultiModalConversation
    import dashscope
except ImportError:
    ImageSynthesis = None
    MultiModalConversation = None

from src.agent.agent_manager import AIAgentManager


def _pil_to_temp_file(pil_img: Image.Image, suffix=".png") -> str:
    """Save a PIL image to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    pil_img.save(path, "PNG")
    return path


def _pil_to_base64_url(pil_img: Image.Image) -> str:
    """Convert a PIL image to a data URI (base64-encoded PNG)."""
    buf = io.BytesIO()
    pil_img.save(buf, "PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


# ── Wanx Inpaint Thread ─────────────────────────────────

class WanxInpaintThread(QThread):
    """
    Calls wanx2.1-imageedit / description_edit_with_mask.

    Signals:
        progress(str)       – status text updates
        finished(Image, str) – (result PIL Image or None, error message)
    """
    progress = pyqtSignal(str)
    finished = pyqtSignal(object, str)  # (PIL.Image | None, error_msg)

    def __init__(self, base_image: Image.Image, mask_image: Image.Image, prompt: str, parent=None):
        super().__init__(parent)
        self._base = base_image.copy()
        self._mask = mask_image.copy()
        self._prompt = prompt

    def run(self):
        manager = AIAgentManager()
        if not manager.api_key:
            self.finished.emit(None, "API Key not configured. Please set it in Settings.")
            return

        if ImageSynthesis is None:
            self.finished.emit(None, "dashscope package is not installed.")
            return

        # Ensure dashscope key is set
        dashscope.api_key = manager.api_key

        base_path = None
        mask_path = None
        try:
            self.progress.emit("Preparing images…")
            base_path = _pil_to_temp_file(self._base)
            mask_path = _pil_to_temp_file(self._mask)

            base_url = f"file://{base_path}"
            mask_url = f"file://{mask_path}"

            self.progress.emit("Sending to Wanx Imageedit…")

            rsp = ImageSynthesis.call(
                api_key=manager.api_key,
                model="wanx2.1-imageedit",
                function="description_edit_with_mask",
                prompt=self._prompt,
                mask_image_url=mask_url,
                base_image_url=base_url,
                n=1,
            )

            if rsp.status_code == 200:
                results = rsp.output.results
                if not results:
                    self.finished.emit(None, "API returned success but no images.")
                    return

                image_url = results[0].url
                self.progress.emit("Downloading result…")

                img_data = requests.get(image_url, timeout=60).content
                result_img = Image.open(io.BytesIO(img_data)).convert("RGBA")
                self.finished.emit(result_img, "")
            else:
                self.finished.emit(None, f"Wanx API Error {rsp.code}: {rsp.message}")

        except Exception as e:
            self.finished.emit(None, f"Wanx Inpaint Error: {e}")
        finally:
            # Cleanup temp files
            for p in (base_path, mask_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass


# ── Qwen Image Edit Thread ──────────────────────────────

class QwenEditThread(QThread):
    """
    Calls qwen-image-edit-plus (MultiModalConversation).

    Signals:
        progress(str)        – status text updates
        finished(Image, str) – (result PIL Image or None, error message)
    """
    progress = pyqtSignal(str)
    finished = pyqtSignal(object, str)

    # API constraint: each dimension must be in [512, 2048]
    _MIN_DIM = 512
    _MAX_DIM = 2048

    def __init__(self, base_image: Image.Image, prompt: str, parent=None):
        super().__init__(parent)
        self._base = base_image.copy()
        self._prompt = prompt

    @staticmethod
    def _compute_api_size(w, h):
        """Compute the API-compatible size closest to (w, h).
        
        Each dimension must be in [512, 2048].  If the input already fits,
        the original size is returned unchanged.  Otherwise the image is
        scaled (preserving aspect ratio) so that both dimensions land inside
        the allowed range.

        Returns (api_w, api_h) as ints.
        """
        if w <= 0 or h <= 0:
            return (1024, 1024)

        cw, ch = float(w), float(h)

        # Scale down if either dimension exceeds MAX
        if cw > QwenEditThread._MAX_DIM or ch > QwenEditThread._MAX_DIM:
            r = min(QwenEditThread._MAX_DIM / cw, QwenEditThread._MAX_DIM / ch)
            cw *= r
            ch *= r

        # Scale up if either dimension is below MIN
        if cw < QwenEditThread._MIN_DIM or ch < QwenEditThread._MIN_DIM:
            r = max(QwenEditThread._MIN_DIM / cw, QwenEditThread._MIN_DIM / ch)
            cw *= r
            ch *= r

        cw = max(QwenEditThread._MIN_DIM, min(int(round(cw)), QwenEditThread._MAX_DIM))
        ch = max(QwenEditThread._MIN_DIM, min(int(round(ch)), QwenEditThread._MAX_DIM))
        return (cw, ch)

    def run(self):
        manager = AIAgentManager()
        if not manager.api_key:
            self.finished.emit(None, "API Key not configured. Please set it in Settings.")
            return

        if MultiModalConversation is None:
            self.finished.emit(None, "dashscope package is not installed.")
            return

        dashscope.api_key = manager.api_key

        base_path = None
        try:
            self.progress.emit("Preparing image…")

            orig_w, orig_h = self._base.size
            api_w, api_h = self._compute_api_size(orig_w, orig_h)
            needs_resize = (api_w != orig_w or api_h != orig_h)

            # If the original image is outside API limits, resize before sending
            send_img = self._base
            if needs_resize:
                send_img = self._base.resize((api_w, api_h), Image.LANCZOS)

            base_path = _pil_to_temp_file(send_img)
            image_url = f"file://{base_path}"

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"image": image_url},
                        {"text": self._prompt},
                    ],
                }
            ]

            self.progress.emit("Sending to Qwen Image Edit…")

            # Request output at the exact sent resolution
            api_size = f"{api_w}*{api_h}"

            response = MultiModalConversation.call(
                api_key=manager.api_key,
                model="qwen-image-edit-plus",
                messages=messages,
                stream=False,
                n=1,
                watermark=False,
                negative_prompt=" ",
                prompt_extend=True,
                size=api_size,
            )

            if response.status_code == 200:
                # Find image in response content
                image_url_result = None
                for content in response.output.choices[0].message.content:
                    if "image" in content:
                        image_url_result = content["image"]
                        break

                if not image_url_result:
                    self.finished.emit(None, "Qwen API returned no image.")
                    return

                self.progress.emit("Downloading result…")
                img_data = requests.get(image_url_result, timeout=60).content
                result_img = Image.open(io.BytesIO(img_data)).convert("RGBA")

                # If we had to resize for the API, scale the result back to
                # the original content dimensions so it can be pasted in place.
                if needs_resize and result_img.size != (orig_w, orig_h):
                    result_img = result_img.resize((orig_w, orig_h), Image.LANCZOS)

                self.finished.emit(result_img, "")
            else:
                self.finished.emit(None, f"Qwen API Error {response.code}: {response.message}")

        except Exception as e:
            self.finished.emit(None, f"Qwen Edit Error: {e}")
        finally:
            if base_path and os.path.exists(base_path):
                try:
                    os.remove(base_path)
                except OSError:
                    pass

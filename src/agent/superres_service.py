"""Local super-resolution service using Real-ESRGAN models."""

from pathlib import Path
import sys

import numpy as np
import requests
from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal


class LocalSuperResolutionThread(QThread):
    """Run local super-resolution in background to keep UI responsive."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(object, str)  # (PIL.Image | None, error message)
    _KNOWN_MODEL_URLS = {
        "RealESRGAN_x4plus.pth": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
        "realesr-animevideov3.pth": "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth",
    }

    def __init__(
        self,
        base_image: Image.Image,
        target_size: tuple[int, int],
        style: str,
        general_model_path: str,
        illustration_model_path: str,
        parent=None,
    ):
        super().__init__(parent)
        self._base = base_image.copy()
        self._target_size = target_size
        self._style = (style or "").lower().strip()
        self._general_model_path = general_model_path
        self._illustration_model_path = illustration_model_path

    @staticmethod
    def _resolve_path(path_str: str) -> Path:
        p = Path(path_str).expanduser()
        if p.is_absolute():
            return p
        return (Path.cwd() / p).resolve()

    @staticmethod
    def _cover_resize(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
        sw, sh = img.size
        if sw <= 0 or sh <= 0:
            return Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))

        scale = max(float(target_w) / float(sw), float(target_h) / float(sh))
        nw = max(1, int(round(sw * scale)))
        nh = max(1, int(round(sh * scale)))
        resized = img.resize((nw, nh), Image.LANCZOS)

        left = max(0, (nw - target_w) // 2)
        top = max(0, (nh - target_h) // 2)
        return resized.crop((left, top, left + target_w, top + target_h))

    def _select_model_path(self) -> Path:
        if self._style == "general":
            return self._resolve_path(self._general_model_path)
        return self._resolve_path(self._illustration_model_path)

    @staticmethod
    def _patch_torchvision_compat():
        """Provide backward-compatible alias expected by some basicsr versions."""
        if "torchvision.transforms.functional_tensor" in sys.modules:
            return
        try:
            from torchvision.transforms import functional as tv_functional
            sys.modules["torchvision.transforms.functional_tensor"] = tv_functional
        except Exception:
            # Keep original import error path if torchvision itself is unavailable.
            pass

    def _ensure_model_exists(self, model_path: Path) -> tuple[bool, str]:
        if model_path.exists():
            return True, ""

        url = self._KNOWN_MODEL_URLS.get(model_path.name)
        if not url:
            return (
                False,
                "Super-resolution model is missing and no auto-download source is known for this filename:\n"
                f"{model_path}\n"
                "Please place the model file manually or use a known filename.",
            )

        model_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = model_path.with_suffix(model_path.suffix + ".download")

        try:
            self.progress.emit(f"Model missing. Downloading {model_path.name} ...")
            with requests.get(url, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", "0") or "0")
                written = 0
                with open(tmp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        f.write(chunk)
                        written += len(chunk)
                        if total > 0:
                            pct = int((written * 100) / total)
                            self.progress.emit(f"Downloading model... {pct}%")
            tmp_path.replace(model_path)
            self.progress.emit(f"Model downloaded: {model_path.name}")
            return True, ""
        except Exception as e:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            return False, f"Failed to download model {model_path.name}: {e}"

    def run(self):
        try:
            self.progress.emit("Loading local super-resolution model...")

            model_path = self._select_model_path()
            ok, msg = self._ensure_model_exists(model_path)
            if not ok:
                self.finished.emit(None, msg)
                return

            try:
                self._patch_torchvision_compat()
                import torch
                from basicsr.archs.rrdbnet_arch import RRDBNet
                from realesrgan import RealESRGANer
                from realesrgan.archs.srvgg_arch import SRVGGNetCompact
            except Exception as e:
                self.finished.emit(
                    None,
                    "Missing local super-resolution dependencies. "
                    "Install: pip install realesrgan basicsr\n"
                    f"Detail: {e}",
                )
                return

            style_is_general = self._style == "general"
            if style_is_general:
                net = RRDBNet(
                    num_in_ch=3,
                    num_out_ch=3,
                    num_feat=64,
                    num_block=23,
                    num_grow_ch=32,
                    scale=4,
                )
            else:
                net = SRVGGNetCompact(
                    num_in_ch=3,
                    num_out_ch=3,
                    num_feat=64,
                    num_conv=16,
                    upscale=4,
                    act_type="prelu",
                )

            use_cuda = bool(torch.cuda.is_available())
            upsampler = RealESRGANer(
                scale=4,
                model_path=str(model_path),
                model=net,
                tile=0,
                tile_pad=10,
                pre_pad=0,
                half=use_cuda,
                gpu_id=0 if use_cuda else None,
            )

            self.progress.emit("Running super-resolution...")
            src_rgba = self._base.convert("RGBA")
            src_rgb_bgr = np.array(src_rgba.convert("RGB"))[:, :, ::-1]
            sr_bgr, _ = upsampler.enhance(src_rgb_bgr, outscale=4)
            sr_rgb = Image.fromarray(sr_bgr[:, :, ::-1]).convert("RGB")

            alpha = src_rgba.split()[3].resize(sr_rgb.size, Image.LANCZOS)
            sr_rgba = sr_rgb.convert("RGBA")
            sr_rgba.putalpha(alpha)

            tw, th = self._target_size
            self.progress.emit("Fitting result to canvas...")
            final_img = self._cover_resize(sr_rgba, tw, th)
            self.finished.emit(final_img, "")
        except Exception as e:
            self.finished.emit(None, f"Local super-resolution failed: {e}")

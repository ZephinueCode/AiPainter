import os
import tempfile
from typing import List, Dict

from PyQt6.QtCore import QThread, pyqtSignal

from src.agent.agent_manager import AIAgentManager
try:
    from dashscope import MultiModalConversation
except ImportError:
    MultiModalConversation = None


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _load_help_prompt_segment() -> str:
    help_dir = os.path.join(_repo_root(), "help")
    parts = []
    for name in ("functions.md", "change_log.md"):
        path = os.path.join(help_dir, name)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    parts.append(f"## {name}\n{f.read().strip()}")
            except Exception:
                parts.append(f"## {name}\n(Unable to read)")
        else:
            parts.append(f"## {name}\n(Not found)")
    return "\n\n".join(parts).strip()


def build_chat_system_prompt(base_prompt: str) -> str:
    base = (base_prompt or "").strip()
    help_segment = _load_help_prompt_segment()
    if not base:
        return help_segment
    return f"{base}\n\n---\nReference Docs:\n{help_segment}"


class ChatRequestThread(QThread):
    progress = pyqtSignal(str)
    finished = pyqtSignal(str, str)  # (assistant_text, error)

    def __init__(
        self,
        user_text: str,
        history: List[Dict[str, str]],
        include_image: bool = False,
        visible_image=None,
        parent=None,
    ):
        super().__init__(parent)
        self.user_text = (user_text or "").strip()
        self.history = history or []
        self.include_image = bool(include_image)
        self.visible_image = visible_image  # PIL image or None

    @staticmethod
    def _pil_to_temp_file(pil_img) -> str:
        fd, path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        pil_img.save(path, "PNG")
        return path

    @staticmethod
    def _normalize_response_text(resp) -> str:
        try:
            content = resp.output.choices[0].message.content
        except Exception:
            return ""
        if isinstance(content, list):
            chunks = []
            for item in content:
                if isinstance(item, dict):
                    txt = item.get("text")
                    if txt:
                        chunks.append(str(txt))
            return "\n".join(chunks).strip()
        return str(content).strip()

    def run(self):
        mgr = AIAgentManager()
        if not mgr.api_key:
            self.finished.emit("", "API Key not configured. Please set it in Settings -> AI Settings.")
            return
        if MultiModalConversation is None:
            self.finished.emit("", "dashscope package is not installed.")
            return
        mgr._init_client()

        model_name = (mgr.chat_model or "qwen3.5-plus").strip()
        if not model_name:
            model_name = "qwen3.5-plus"

        sys_prompt = build_chat_system_prompt(mgr.chat_system_prompt)

        temp_image_path = None
        try:
            self.progress.emit("Preparing chat request...")
            messages = []
            if sys_prompt:
                messages.append({"role": "system", "content": [{"text": sys_prompt}]})

            # Keep a short rolling context to control token usage.
            trimmed_history = self.history[-12:]
            for item in trimmed_history:
                role = item.get("role", "")
                text = (item.get("text", "") or "").strip()
                if role in ("user", "assistant") and text:
                    messages.append({"role": role, "content": [{"text": text}]})

            if self.include_image and self.visible_image is not None:
                temp_image_path = self._pil_to_temp_file(self.visible_image)
                image_url = f"file://{temp_image_path}"
                user_payload = [{"image": image_url}, {"text": self.user_text}]
                messages.append({"role": "user", "content": user_payload})
            else:
                messages.append({"role": "user", "content": [{"text": self.user_text}]})

            self.progress.emit(f"Calling model: {model_name}")
            resp = MultiModalConversation.call(
                api_key=mgr.api_key,
                model=model_name,
                messages=messages,
                stream=False,
            )
            if getattr(resp, "status_code", None) != 200:
                code = getattr(resp, "code", "")
                msg = getattr(resp, "message", "")
                self.finished.emit("", f"DashScope chat error {code}: {msg}")
                return

            text = self._normalize_response_text(resp)
            if not text:
                self.finished.emit("", "Model returned an empty response.")
                return
            self.finished.emit(text, "")
        except Exception as e:
            self.finished.emit("", f"Chat request failed: {e}")
        finally:
            if temp_image_path and os.path.exists(temp_image_path):
                try:
                    os.remove(temp_image_path)
                except OSError:
                    pass

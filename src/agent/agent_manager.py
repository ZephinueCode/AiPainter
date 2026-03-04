# src/agent/agent_manager.py

import os
import json

try:
    import dashscope
    from dashscope import MultiModalConversation
except ImportError:
    dashscope = None
    MultiModalConversation = None

_DEFAULT_MODELS = {
    "generate_model": "qwen-image-2.0",
    "edit_model": "qwen-image-2.0",
    "inpaint_model": "wanx2.1-imageedit",
    "layered_model": "qwen/qwen-image-layered",
    "chat_model": "qwen3.5-plus",
    "chat_system_prompt": (
        "You are AiPainter Assistant, an expert digital art copilot integrated into a painting app. "
        "Be practical, concise, and actionable. Focus on composition, values, color harmony, edge control, "
        "anatomy/perspective correctness, and workflow improvements. "
        "If an image is provided, prioritize concrete visual critique and next brush-level steps. "
        "Do not fabricate hidden details; state uncertainty explicitly."
    ),
    "superres_general_model_path": "models/RealESRGAN_x4plus.pth",
    "superres_illustration_model_path": "models/realesr-animevideov3.pth",
}

_DEFAULT_REPLICATE_API_KEY = ""

class AIAgentManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AIAgentManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.config_path = "config/settings.json"
        self.client = None
        self.base_url = ""
        self.api_key = ""
        self.replicate_api_key = _DEFAULT_REPLICATE_API_KEY
        self.model = ""
        self.proxy = ""
        self.provider = "dashscope"
        # Model-specific settings
        self.generate_model = _DEFAULT_MODELS["generate_model"]
        self.edit_model = _DEFAULT_MODELS["edit_model"]
        self.inpaint_model = _DEFAULT_MODELS["inpaint_model"]
        self.layered_model = _DEFAULT_MODELS["layered_model"]
        self.chat_model = _DEFAULT_MODELS["chat_model"]
        self.chat_system_prompt = _DEFAULT_MODELS["chat_system_prompt"]
        self.superres_general_model_path = _DEFAULT_MODELS["superres_general_model_path"]
        self.superres_illustration_model_path = _DEFAULT_MODELS["superres_illustration_model_path"]
        self.load_config()
        self._initialized = True

    @staticmethod
    def _normalize_dashscope_base_url(url: str) -> str:
        val = (url or "").strip()
        if not val:
            return "https://dashscope.aliyuncs.com/api/v1"
        if "compatible-mode" in val:
            val = val.replace("/compatible-mode/v1", "/api/v1")
            val = val.replace("compatible-mode/v1", "api/v1")
        return val

    def load_config(self):
        if not os.path.exists("config"):
            os.makedirs("config")
        
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    data = json.load(f)
                    ai_conf = data.get("ai", {})
                    self.base_url = self._normalize_dashscope_base_url(
                        ai_conf.get("base_url", "https://dashscope.aliyuncs.com/api/v1")
                    )
                    self.api_key = ai_conf.get("api_key", "")
                    self.model = ai_conf.get("model", _DEFAULT_MODELS["generate_model"])
                    self.proxy = ai_conf.get("proxy", "")
                    
                    # Load model-specific settings
                    self.generate_model = ai_conf.get("generate_model", _DEFAULT_MODELS["generate_model"])
                    self.edit_model = ai_conf.get("edit_model", _DEFAULT_MODELS["edit_model"])
                    self.inpaint_model = ai_conf.get("inpaint_model", _DEFAULT_MODELS["inpaint_model"])
                    self.layered_model = ai_conf.get("layered_model", _DEFAULT_MODELS["layered_model"])
                    self.chat_model = ai_conf.get("chat_model", _DEFAULT_MODELS["chat_model"])
                    self.chat_system_prompt = ai_conf.get(
                        "chat_system_prompt", _DEFAULT_MODELS["chat_system_prompt"]
                    )
                    legacy_superres = ai_conf.get("superres_model_path", "")
                    self.superres_general_model_path = ai_conf.get(
                        "superres_general_model_path",
                        _DEFAULT_MODELS["superres_general_model_path"],
                    )
                    self.superres_illustration_model_path = ai_conf.get(
                        "superres_illustration_model_path",
                        legacy_superres if legacy_superres else _DEFAULT_MODELS["superres_illustration_model_path"],
                    )
                    self.replicate_api_key = ai_conf.get("replicate_api_key", "")
                    
                    self.provider = "dashscope"
                    self._init_client()
            except Exception as e:
                print(f"Error loading settings: {e}")
        else:
            self.base_url = "https://dashscope.aliyuncs.com/api/v1"
            self.api_key = ""
            self.model = _DEFAULT_MODELS["generate_model"]
            self.proxy = ""
            self.provider = "dashscope"
            self.generate_model = _DEFAULT_MODELS["generate_model"]
            self.edit_model = _DEFAULT_MODELS["edit_model"]
            self.inpaint_model = _DEFAULT_MODELS["inpaint_model"]
            self.layered_model = _DEFAULT_MODELS["layered_model"]
            self.chat_model = _DEFAULT_MODELS["chat_model"]
            self.chat_system_prompt = _DEFAULT_MODELS["chat_system_prompt"]
            self.superres_general_model_path = _DEFAULT_MODELS["superres_general_model_path"]
            self.superres_illustration_model_path = _DEFAULT_MODELS["superres_illustration_model_path"]
            self.replicate_api_key = _DEFAULT_REPLICATE_API_KEY

    def save_config(self, base_url, api_key, model, proxy="",
                        edit_model=None, inpaint_model=None, layered_model=None,
                        chat_model=None, chat_system_prompt=None,
                        replicate_api_key=None, superres_general_model_path=None,
                        superres_illustration_model_path=None):
        self.base_url = self._normalize_dashscope_base_url(base_url)
        self.api_key = api_key
        self.model = model
        self.generate_model = model
        self.proxy = proxy
        
        if edit_model is not None:
            self.edit_model = edit_model
        if inpaint_model is not None:
            self.inpaint_model = inpaint_model
        if layered_model is not None:
            self.layered_model = layered_model
        if chat_model is not None:
            self.chat_model = chat_model
        if chat_system_prompt is not None:
            self.chat_system_prompt = chat_system_prompt
        if replicate_api_key is not None:
            self.replicate_api_key = replicate_api_key
        if superres_general_model_path is not None:
            self.superres_general_model_path = superres_general_model_path
        if superres_illustration_model_path is not None:
            self.superres_illustration_model_path = superres_illustration_model_path
        
        self.provider = "dashscope"
        
        data = {}
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    data = json.load(f)
            except:
                pass
            
        data["ai"] = {
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "proxy": self.proxy,
            "generate_model": self.generate_model,
            "edit_model": self.edit_model,
            "inpaint_model": self.inpaint_model,
            "layered_model": self.layered_model,
            "chat_model": self.chat_model,
            "chat_system_prompt": self.chat_system_prompt,
            "replicate_api_key": self.replicate_api_key,
            "superres_general_model_path": self.superres_general_model_path,
            "superres_illustration_model_path": self.superres_illustration_model_path,
        }
        
        try:
            with open(self.config_path, 'w') as f:
                json.dump(data, f, indent=4)
            self._init_client()
            return True
        except Exception as e:
            print(f"Error saving settings: {e}")
            return False

    def _init_client(self):
        self.client = None
        if not self.api_key or dashscope is None:
            return
        try:
            dashscope.api_key = self.api_key
            if self.base_url:
                dashscope.base_http_api_url = self.base_url
            if self.proxy:
                os.environ["HTTPS_PROXY"] = self.proxy
                os.environ["HTTP_PROXY"] = self.proxy
        except Exception as e:
            print(f"Failed to init DashScope client config: {e}")

    def test_connection(self):
        if not self.api_key:
            return False, "API Key missing."
        if dashscope is None or MultiModalConversation is None:
            return False, "dashscope package not installed."

        try:
            self._init_client()
            probe_model = (self.chat_model or "qwen3.5-plus").strip() or "qwen3.5-plus"
            resp = MultiModalConversation.call(
                api_key=self.api_key,
                model=probe_model,
                messages=[{"role": "user", "content": [{"text": "ping"}]}],
                stream=False,
            )
            if resp.status_code == 200:
                return True, "DashScope connection successful."
            return False, f"DashScope Error {getattr(resp, 'code', '')}: {getattr(resp, 'message', '')}"
        except Exception as e:
            return False, f"DashScope connection failed: {str(e)}"

# src/agent/agent_manager.py

import os
import json
import openai
from openai import OpenAI
import httpx

try:
    import dashscope
except ImportError:
    dashscope = None

_DEFAULT_MODELS = {
    "generate_model": "qwen-image-2.0",
    "edit_model": "qwen-image-2.0",
    "inpaint_model": "wanx2.1-imageedit",
    "layered_model": "qwen/qwen-image-layered",
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
        self.provider = "openai"
        # Model-specific settings
        self.generate_model = _DEFAULT_MODELS["generate_model"]
        self.edit_model = _DEFAULT_MODELS["edit_model"]
        self.inpaint_model = _DEFAULT_MODELS["inpaint_model"]
        self.layered_model = _DEFAULT_MODELS["layered_model"]
        self.load_config()
        self._initialized = True

    def load_config(self):
        if not os.path.exists("config"):
            os.makedirs("config")
        
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    data = json.load(f)
                    ai_conf = data.get("ai", {})
                    self.base_url = ai_conf.get("base_url", "https://dashscope.aliyuncs.com/api/v1")
                    self.api_key = ai_conf.get("api_key", "")
                    self.model = ai_conf.get("model", "qwen-vl-max")
                    self.proxy = ai_conf.get("proxy", "")
                    
                    # Load model-specific settings
                    self.generate_model = ai_conf.get("generate_model", self.model)
                    self.edit_model = ai_conf.get("edit_model", _DEFAULT_MODELS["edit_model"])
                    self.inpaint_model = ai_conf.get("inpaint_model", _DEFAULT_MODELS["inpaint_model"])
                    self.layered_model = ai_conf.get("layered_model", _DEFAULT_MODELS["layered_model"])
                    self.replicate_api_key = ai_conf.get("replicate_api_key", "")
                    
                    if "dashscope" in self.base_url or "aliyuncs" in self.base_url:
                        self.provider = "dashscope"
                    else:
                        self.provider = "openai"
                        
                    self._init_client()
            except Exception as e:
                print(f"Error loading settings: {e}")
        else:
            self.base_url = "https://dashscope.aliyuncs.com/api/v1"
            self.api_key = ""
            self.model = "qwen-vl-max"
            self.proxy = ""
            self.provider = "dashscope"
            self.generate_model = _DEFAULT_MODELS["generate_model"]
            self.edit_model = _DEFAULT_MODELS["edit_model"]
            self.inpaint_model = _DEFAULT_MODELS["inpaint_model"]
            self.layered_model = _DEFAULT_MODELS["layered_model"]
            self.replicate_api_key = _DEFAULT_REPLICATE_API_KEY

    def save_config(self, base_url, api_key, model, proxy="",
                        edit_model=None, inpaint_model=None, layered_model=None,
                        replicate_api_key=None):
        self.base_url = base_url
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
        if replicate_api_key is not None:
            self.replicate_api_key = replicate_api_key
        
        if "dashscope" in self.base_url or "aliyuncs" in self.base_url:
            self.provider = "dashscope"
        else:
            self.provider = "openai"
        
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
            "replicate_api_key": self.replicate_api_key,
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
        if self.api_key:
            try:
                http_client = None
                if self.proxy:
                    http_client = httpx.Client(proxies=self.proxy)
                else:
                    http_client = httpx.Client()
                
                self.client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    http_client=http_client
                )
                
                if dashscope:
                    dashscope.api_key = self.api_key
                    if "compatible-mode" not in self.base_url:
                         dashscope.base_http_api_url = self.base_url
                    
            except Exception as e:
                print(f"Failed to init AI client: {e}")
                self.client = None

    def test_connection(self):
        if not self.api_key:
            return False, "API Key missing."

        if self.provider == "dashscope" and dashscope:
            try:
                from dashscope import Generation
                dashscope.api_key = self.api_key
                resp = Generation.call(model='qwen-turbo', prompt='Hi')
                if resp.status_code == 200:
                    return True, "DashScope Connection Successful!"
                else:
                    return False, f"DashScope Error: {resp.message}"
            except Exception as e:
                pass
                 
        if not self.client:
            return False, "Client not initialized."
        
        try:
            self.client.models.list()
            return True, "Connection Successful (OpenAI Compatible)!"
        except Exception as e:
            return False, f"Connection Failed: {str(e)}"
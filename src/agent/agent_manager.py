# src/agent/agent_manager.py

import os
import json
import openai
from openai import OpenAI
import httpx

# 尝试导入 dashscope
try:
    import dashscope
except ImportError:
    dashscope = None

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
        self.model = ""
        self.proxy = ""
        self.provider = "openai" 
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
                    # 默认为 qwen-vl-max 或 qwen-image-plus，用于图像生成
                    self.model = ai_conf.get("model", "qwen-vl-max") 
                    self.proxy = ai_conf.get("proxy", "")
                    
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

    def save_config(self, base_url, api_key, model, proxy=""):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.proxy = proxy
        
        if "dashscope" in self.base_url or "aliyuncs" in self.base_url:
            self.provider = "dashscope"
        else:
            self.provider = "openai"
        
        data = {}
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    data = json.load(f)
            except: pass
            
        data["ai"] = {
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "proxy": self.proxy
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
                    # 不要覆盖 dashscope.base_http_api_url 除非用户真的改了非默认值
                    # dashscope 默认已经是 https://dashscope.aliyuncs.com/api/v1
                    # 如果用户在界面填的是兼容 URL (带 /compatible-mode/v1)，我们不应该赋给 dashscope SDK
                    if "compatible-mode" not in self.base_url:
                         dashscope.base_http_api_url = self.base_url
                    
            except Exception as e:
                print(f"Failed to init AI client: {e}")
                self.client = None

    def test_connection(self):
        if not self.api_key: return False, "API Key missing."

        # DashScope Test
        if self.provider == "dashscope" and dashscope:
            try:
                # Use a lightweight text model for connection test
                from dashscope import Generation
                # Ensure key is set on the library level
                dashscope.api_key = self.api_key
                resp = Generation.call(model='qwen-turbo', prompt='Hi')
                if resp.status_code == 200:
                    return True, "DashScope Connection Successful!"
                else:
                    return False, f"DashScope Error: {resp.message}"
            except Exception as e:
                 pass # Fall through to OpenAI test if this fails (maybe they used OpenAI client for DashScope)
                 
        if not self.client: return False, "Client not initialized."
        
        try:
            self.client.models.list()
            return True, "Connection Successful (OpenAI Compatible)!"
        except Exception as e:
            return False, f"Connection Failed: {str(e)}"
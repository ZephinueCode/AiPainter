# src/agent/agent_manager.py

import os
import json
import openai
from openai import OpenAI
import httpx

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
        self.proxy = "" # 新增代理字段
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
                    self.base_url = ai_conf.get("base_url", "https://api.openai.com/v1")
                    self.api_key = ai_conf.get("api_key", "")
                    self.model = ai_conf.get("model", "gpt-3.5-turbo")
                    self.proxy = ai_conf.get("proxy", "") # 加载代理
                    self._init_client()
            except Exception as e:
                print(f"Error loading settings: {e}")
        else:
            # Default values
            self.base_url = "https://api.openai.com/v1"
            self.api_key = ""
            self.model = "gpt-3.5-turbo"
            self.proxy = ""

    def save_config(self, base_url, api_key, model, proxy=""):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.proxy = proxy
        
        # Load existing config to preserve other settings (e.g. canvas size)
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
                # 配置 httpx 代理
                http_client = None
                if self.proxy:
                    # 如果用户输入例如 "http://127.0.0.1:7890"
                    http_client = httpx.Client(proxy=self.proxy)
                else:
                    http_client = httpx.Client() # Default client without proxy
                
                self.client = OpenAI(
                    api_key=self.api_key,
                    base_url=self.base_url,
                    http_client=http_client
                )
            except Exception as e:
                print(f"Failed to init OpenAI client: {e}")
                self.client = None

    def test_connection(self):
        if not self.client:
            return False, "Client not initialized (Check API Key)"
        
        try:
            # Simple call to list models or chat
            # Using a cheap call to verify auth
            response = self.client.models.list()
            # If we get here, connection is likely okay
            return True, "Connection Successful!"
        except Exception as e:
            return False, f"Connection Failed: {str(e)}"
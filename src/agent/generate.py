# src/agent/generate.py

import threading
import requests
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage
from src.agent.agent_manager import AIAgentManager
import io
import json
import os

try:
    import dashscope
    from dashscope import MultiModalConversation
except ImportError:
    dashscope = None
    MultiModalConversation = None

class ImageGenerator(QObject):
    generation_finished = pyqtSignal(object, str) # (QImage or None, error_message)

    def __init__(self):
        super().__init__()
        self.manager = AIAgentManager()

    def generate(self, prompt, negative_prompt="", size="1024*1024"):
        # Run in thread
        thread = threading.Thread(target=self._run_generate, args=(prompt, negative_prompt, size))
        thread.start()

    def _run_generate(self, prompt, negative_prompt, size):
        if not self.manager.api_key:
            self.generation_finished.emit(None, "API Key not configured. Please set it in Settings.")
            return

        # DashScope Logic
        if dashscope and self.manager.provider == "dashscope":
            # Don't override base_url with the OpenAI compatible one (which ends in /v1)
            # DashScope SDK expects its own base_url structure or default.
            # Only set if it looks like a custom DashScope URL, otherwise trust SDK default.
            # self.manager.base_url usually is 'https://dashscope.aliyuncs.com/api/v1' for OpenAI compat,
            # but native SDK uses similar default. Let's just reset/ensure api key.
            dashscope.api_key = self.manager.api_key
            
            # Construct messages for Wanx/Qwen-VL
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"text": prompt}
                    ]
                }
            ]

            try:
                # Call API
                response = MultiModalConversation.call(
                    api_key=self.manager.api_key,
                    model=self.manager.model, # e.g. "qwen-image-plus" or "wanx-v1"
                    messages=messages,
                    result_format='message',
                    stream=False,
                    watermark=False,
                    prompt_extend=True, # Optional: let model enhance prompt
                    negative_prompt=negative_prompt,
                    size=size 
                )

                if response.status_code == 200:
                    try:
                        # Parse DashScope response structure
                        # Usually: output -> choices -> message -> content -> list of items
                        # Find item with 'image' key
                        content_list = response.output.choices[0].message.content
                        image_url = None
                        for item in content_list:
                            if 'image' in item:
                                image_url = item['image']
                                break
                        
                        if not image_url:
                             # Fallback: check if it's in results (older api)
                             if hasattr(response.output, 'results') and response.output.results:
                                 image_url = response.output.results[0].url
                             else:
                                 print(json.dumps(response, ensure_ascii=False)) # Debug print
                                 self.generation_finished.emit(None, "No image found in DashScope response.")
                                 return

                        # Download
                        img_data = requests.get(image_url).content
                        qimg = QImage()
                        qimg.loadFromData(img_data)
                        
                        if qimg.isNull():
                            self.generation_finished.emit(None, "Downloaded data is not a valid image.")
                        else:
                            self.generation_finished.emit(qimg, "")

                    except Exception as e:
                        self.generation_finished.emit(None, f"Parse Error: {str(e)}")
                else:
                    self.generation_finished.emit(None, f"DashScope API Error {response.code}: {response.message}")
            
            except Exception as e:
                self.generation_finished.emit(None, f"System Error: {str(e)}")
                
        else:
            # Fallback to OpenAI Client (e.g. for local SD or DALL-E)
            if not self.manager.client:
                self.manager._init_client()
                if not self.manager.client:
                     self.generation_finished.emit(None, "AI Client init failed.")
                     return

            try:
                final_prompt = prompt
                if negative_prompt:
                    final_prompt = f"{prompt} --no {negative_prompt}"
                
                # OpenAI standard size format 1024x1024
                openai_size = size.replace("*", "x")
                
                response = self.manager.client.images.generate(
                    model=self.manager.model,
                    prompt=final_prompt,
                    size=openai_size,
                    quality="standard",
                    n=1,
                )
                
                image_url = response.data[0].url
                img_data = requests.get(image_url).content
                qimg = QImage()
                qimg.loadFromData(img_data)
                
                if qimg.isNull():
                     self.generation_finished.emit(None, "Invalid image data.")
                else:
                     self.generation_finished.emit(qimg, "")

            except Exception as e:
                self.generation_finished.emit(None, f"OpenAI Error: {str(e)}")
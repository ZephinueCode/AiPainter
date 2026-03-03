# src/agent/generate.py

import threading
import requests
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QImage
from PIL import Image
from src.agent.agent_manager import AIAgentManager
import io
import json
import os
import replicate

try:
    import dashscope
    from dashscope import MultiModalConversation
except ImportError:
    dashscope = None
    MultiModalConversation = None

class ImageGenerator(QObject):
    generation_finished = pyqtSignal(object, str) # (QImage or None, error_message)
    # Multi Layered
    layered_generation_finished = pyqtSignal(list, list, str)

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
                print(response)

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
                    model=self.manager.generate_model,
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
    
    def generate_layered(self, prompt, input_image=None, num_layers=4):
        """
        统一的外部调用入口。
        负责开启异步线程，确保不阻塞主界面。
        """
        if input_image is None:
            self.layered_generation_finished.emit([], [], "Error: No Input Provided.")
            return

        # 开启线程执行真正的 API 逻辑
        thread = threading.Thread(
            target=self._execute_replicate_task, 
            args=(prompt, input_image, num_layers),
            daemon=True # 设置为守护线程，程序退出时自动结束
        )
        thread.start()

    def _execute_replicate_task(self, prompt, input_image, num_layers):
        """
        私有方法：负责 Replicate API 的具体交互流程。
        """
        try:
            # 0. 设置 Replicate API Key
            rep_key = self.manager.replicate_api_key
            if not rep_key:
                self.layered_generation_finished.emit([], [], "Replicate API Key not configured.\nPlease set it in Settings -> AI Settings.")
                return
            # Strip any non-ASCII / invisible characters (e.g. zero-width spaces from copy-paste)
            rep_key_clean = rep_key.strip().encode('ascii', errors='ignore').decode('ascii')
            if not rep_key_clean:
                self.layered_generation_finished.emit([], [], "Replicate API Key contains invalid characters.\nPlease re-enter it in Settings.")
                return
            os.environ["REPLICATE_API_TOKEN"] = rep_key_clean

            # 1. 准备图片数据
            clean_img = Image.new("RGBA", input_image.size)
            clean_img.putdata(list(input_image.getdata()))
            img_byte_arr = io.BytesIO()
            clean_img.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)

            # 2. 调用模型 (使用最新的稳定版 ID)
            # 也可以把模型 ID 抽离成类常量
            MODEL_ID = self.manager.layered_model

            print(f"AI Processing...")
            output = replicate.run(
                MODEL_ID,
                input={
                    "image": img_byte_arr,
                    "num_layers": num_layers,
                    "go_fast": True,
                }
            )

            # 3. 处理输出结果
            # Replicate 1.0+ 版本的返回对象支持直接 read() 或访问 url
            pil_images = []
            layer_names = []
            
            for i, layer_file in enumerate(output):
                # 直接通过 URL 读取数据，减少本地文件落地的中间环节
                response = requests.get(layer_file.url, timeout=10)
                if response.status_code == 200:
                    img = Image.open(io.BytesIO(response.content)).convert("RGBA")
                    pil_images.append(img)
                    
                    # 命名逻辑：0是背景，其余是物体
                    name = "AI_Background" if i == 0 else f"AI_Object_{i}"
                    layer_names.append(name)

            # 4. 成功后发射信号
            self.layered_generation_finished.emit(pil_images, layer_names, "")

        except Exception as e:
            # 捕获所有可能的网络或 API 错误并返回
            import traceback
            traceback.print_exc()  # Print full stack trace to console
            self.layered_generation_finished.emit([], [], f"AI Service Error: {str(e)}")
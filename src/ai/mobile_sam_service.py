# src/ai/mobile_sam_service.py

"""
MobileSAM 服务模块
负责模型的下载、加载和推理，提供单例访问
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
    """模型加载/下载线程（首次运行会自动从网上下载）"""
    progress_msg = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str, object)  # (成功?, 消息, model_or_None)

    def __init__(self, model_name='mobile_sam.pt'):
        super().__init__()
        self.model_name = model_name

    def run(self):
        try:
            self.progress_msg.emit("正在加载 MobileSAM 模型，首次运行需要下载（约 10MB）...")

            # 重定向 stdout 以捕获 ultralytics 的下载进度输出
            from ultralytics import SAM
            model = SAM(self.model_name)

            self.progress_msg.emit("MobileSAM 模型加载完成！")
            self.finished_signal.emit(True, "模型加载成功", model)
        except ImportError:
            self.finished_signal.emit(False, "未安装 ultralytics 库，请运行: pip install ultralytics", None)
        except Exception as e:
            self.finished_signal.emit(False, f"模型加载失败: {str(e)}", None)


class SAMInferenceThread(QThread):
    """SAM 推理线程 - 避免阻塞 UI"""
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
            # ultralytics SAM 多点输入格式：
            #   points=[[[x1,y1],[x2,y2],...]]   (三层嵌套)
            #   labels=[[l1,l2,...]]              (两层嵌套)
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
    MobileSAM 服务 - 单例模式
    负责模型的生命周期管理和推理调用
    """

    # 信号
    model_loading_msg = pyqtSignal(str)         # 加载进度文本
    model_load_finished = pyqtSignal(bool, str)  # (成功?, 消息)

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
        self._model_name = 'mobile_sam.pt'

    # ── 属性 ──────────────────────────────────────────────

    @property
    def is_loaded(self):
        return self._model is not None

    @property
    def is_loading(self):
        return self._loading

    # ── 模型文件管理 ──────────────────────────────────────

    def get_model_path(self):
        """尝试定位已下载的模型文件"""
        possible = [
            Path(self._model_name),
            Path.home() / '.ultralytics' / self._model_name,
        ]
        # Windows AppData
        if sys.platform == 'win32':
            possible.append(Path(os.environ.get('APPDATA', '')) / 'ultralytics' / self._model_name)
            possible.append(Path(os.environ.get('USERPROFILE', '')) / self._model_name)

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
        return "未下载"

    def delete_model(self):
        """删除已下载的模型并释放内存"""
        self._model = None
        path = self.get_model_path()
        if path and os.path.exists(path):
            try:
                os.remove(path)
                return True, "模型文件已删除"
            except Exception as e:
                return False, f"删除失败: {e}"
        return False, "未找到模型文件"

    # ── 异步加载 ─────────────────────────────────────────

    def load_model_async(self):
        """异步加载模型（首次会自动下载）"""
        if self._model is not None:
            self.model_load_finished.emit(True, "模型已就绪")
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

    # ── 推理 ─────────────────────────────────────────────

    def create_inference_thread(self, image_path, points, labels):
        """
        创建推理线程（由调用者负责连接信号和启动）

        Args:
            image_path: 临时 PNG 文件路径
            points:     [[x,y], ...] 像素坐标列表
            labels:     [1, 0, ...] 标签列表 (1=正向 0=负向)

        Returns:
            SAMInferenceThread 或 None（模型未加载时）
        """
        if self._model is None:
            return None
        return SAMInferenceThread(self._model, image_path, list(points), list(labels))

    @staticmethod
    def pil_to_temp_png(pil_image):
        """将 PIL Image 保存为临时 PNG 并返回路径"""
        fd, path = tempfile.mkstemp(suffix='.png')
        os.close(fd)
        if pil_image.mode != 'RGBA':
            pil_image = pil_image.convert('RGBA')
        pil_image.save(path, "PNG")
        return path

# src/core/processor.py

from PIL import Image, ImageEnhance, ImageFilter
import colorsys
import numpy as np

class ImageProcessor:
    @staticmethod
    def adjust_hsl(image, hue_delta, sat_factor, light_delta):
        """
        hue_delta: -180 to 180
        sat_factor: 0.0 to 2.0 (1.0 is original)
        light_delta: -255 to 255
        """
        if image.mode != 'RGBA':
            image = image.convert('RGBA')
        
        # 拆分通道
        r, g, b, a = image.split()
        
        # 转换为 HSV 调整
        # 这里使用 numpy 加速可能更好，但为了保持依赖简单，使用 point 操作或遍历
        # 实际上 python 循环太慢，我们尝试使用 PIL 的矩阵运算或简单方案
        # 简易方案：先转 HSV
        img_hsv = image.convert('HSV')
        h, s, v = img_hsv.split()
        
        # 调整 H
        if hue_delta != 0:
            # Hue in PIL is 0-255
            delta = int(hue_delta / 360.0 * 255)
            h = h.point(lambda i: (i + delta) % 256)
        
        # 调整 S
        if sat_factor != 1.0:
            s = s.point(lambda i: int(min(255, max(0, i * sat_factor))))
            
        # 调整 L (在 HSV 模式下其实是 V，亮度)
        if light_delta != 0:
            v = v.point(lambda i: int(min(255, max(0, i + light_delta))))
            
        img_hsv_new = Image.merge('HSV', (h, s, v))
        img_rgb = img_hsv_new.convert('RGB')
        
        # 合并回 Alpha
        r, g, b = img_rgb.split()
        return Image.merge('RGBA', (r, g, b, a))

    @staticmethod
    def adjust_contrast(image, factor):
        """factor: 1.0 is original"""
        enhancer = ImageEnhance.Contrast(image)
        return enhancer.enhance(factor)

    @staticmethod
    def adjust_exposure(image, factor):
        """factor: 1.0 is original, >1 brighter"""
        enhancer = ImageEnhance.Brightness(image)
        return enhancer.enhance(factor)

    @staticmethod
    def apply_blur(image, radius):
        return image.filter(ImageFilter.GaussianBlur(radius))

    @staticmethod
    def rotate(image, angle):
        return image.rotate(angle, expand=False) # 保持原画布大小

    @staticmethod
    def flip_horizontal(image):
        return image.transpose(Image.FLIP_LEFT_RIGHT)

    @staticmethod
    def flip_vertical(image):
        return image.transpose(Image.FLIP_TOP_BOTTOM)
# src/core/processor.py

from PIL import Image, ImageEnhance, ImageFilter, ImageDraw
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

    @staticmethod
    def apply_gradient_map(image, stops):
        """
        stops: list of (position, (r,g,b))
        position: 0.0 to 1.0
        """
        if image.mode != 'RGBA':
            image = image.convert('RGBA')
        
        # 1. Create Gradient LUT (256x1)
        lut_img = Image.new("RGB", (256, 1))
        draw = ImageDraw.Draw(lut_img)
        
        # Sort stops by position
        stops = sorted(stops, key=lambda x: x[0])
        
        # Ensure start and end
        if stops[0][0] > 0.0: stops.insert(0, (0.0, stops[0][1]))
        if stops[-1][0] < 1.0: stops.append(1.0, stops[-1][1])
        
        for i in range(len(stops) - 1):
            pos1, col1 = stops[i]
            pos2, col2 = stops[i+1]
            
            x1 = int(pos1 * 255)
            x2 = int(pos2 * 255)
            width = x2 - x1
            
            if width > 0:
                for x in range(width):
                    ratio = x / width
                    r = int(col1[0] * (1-ratio) + col2[0] * ratio)
                    g = int(col1[1] * (1-ratio) + col2[1] * ratio)
                    b = int(col1[2] * (1-ratio) + col2[2] * ratio)
                    draw.point((x1 + x, 0), fill=(r, g, b))
        
        # Fill last pixel just in case
        draw.point((255, 0), fill=stops[-1][1])
        
        # 2. Extract LUT arrays
        lut_data = list(lut_img.getdata()) # list of (r,g,b)
        r_lut = [p[0] for p in lut_data]
        g_lut = [p[1] for p in lut_data]
        b_lut = [p[2] for p in lut_data]
        
        # 3. Convert Source to Grayscale
        gray = image.convert("L")
        
        # 4. Map
        r_ch = gray.point(r_lut)
        g_ch = gray.point(g_lut)
        b_ch = gray.point(b_lut)
        
        # 5. Merge with original Alpha
        _, _, _, a = image.split()
        return Image.merge("RGBA", (r_ch, g_ch, b_ch, a))
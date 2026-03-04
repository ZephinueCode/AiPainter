"""PSD export utilities with Photoshop-friendly binary layout.

This module intentionally avoids pytoshop and writes a conservative PSD:
- RGB color mode, 8-bit channels
- Layer records for paint layers (no group folder markers)
- Composite image data included as raw RGBA channels

Layer order contract:
- Input layers must be in render order (bottom -> top).
- PSD layer records are written in top -> bottom order.
"""

from __future__ import annotations

import struct
from typing import Callable, List

import numpy as np
from PIL import Image

from src.core.logic import GroupLayer, PaintLayer


class ExportLayer:
    __slots__ = ("name", "image", "opacity", "visible")

    def __init__(self, name: str, image: Image.Image, opacity: int, visible: bool):
        self.name = name
        self.image = image
        self.opacity = int(max(0, min(255, opacity)))
        self.visible = bool(visible)


def _pack_pascal_name(name: str, padding: int = 4) -> bytes:
    """PSD layer name as padded Pascal string (ASCII-safe)."""
    b = (name or "Layer").encode("ascii", errors="replace")[:255]
    out = struct.pack("B", len(b)) + b
    while len(out) % padding != 0:
        out += b"\x00"
    return out


def _packbits_encode(row: bytes) -> bytes:
    """Encode one row using PackBits RLE."""
    result = bytearray()
    i = 0
    n = len(row)
    while i < n:
        run_start = i
        if i + 1 < n and row[i] == row[i + 1]:
            j = i + 2
            while j < n and (j - run_start) < 128 and row[j] == row[run_start]:
                j += 1
            run_len = j - run_start
            result.append((256 - (run_len - 1)) & 0xFF)
            result.append(row[run_start])
            i = j
        else:
            j = i + 1
            while j < n and (j - run_start) < 128:
                if j + 1 < n and row[j] == row[j + 1]:
                    break
                j += 1
            lit_len = j - run_start
            result.append(lit_len - 1)
            result.extend(row[run_start:j])
            i = j
    return bytes(result)


def _encode_rle_channel(channel_hw: np.ndarray) -> bytes:
    """Return PSD channel payload: [compression=1][row byte counts][compressed rows]."""
    h, w = channel_hw.shape
    row_blobs: List[bytes] = []
    row_sizes: List[int] = []
    for y in range(h):
        blob = _packbits_encode(channel_hw[y].tobytes())
        if len(blob) > 0xFFFF:
            raise ValueError(f"RLE row too large for PSD ({len(blob)} bytes, width={w}).")
        row_blobs.append(blob)
        row_sizes.append(len(blob))

    out = bytearray()
    out += struct.pack(">H", 1)  # 1 = RLE
    for sz in row_sizes:
        out += struct.pack(">H", sz)
    for blob in row_blobs:
        out += blob
    return bytes(out)


def collect_paint_layers_for_export(
    root: GroupLayer,
    doc_width: int,
    doc_height: int,
    read_layer_rgba: Callable[[PaintLayer], Image.Image],
) -> List[ExportLayer]:
    """Collect paint layers in render order (bottom -> top), folding group opacity/visibility."""

    out: List[ExportLayer] = []

    def walk(node, parent_visible: bool, parent_opacity: float):
        visible = parent_visible and bool(getattr(node, "visible", True))
        opacity = parent_opacity * float(getattr(node, "opacity", 1.0))

        if isinstance(node, PaintLayer):
            img = read_layer_rgba(node)
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            if img.size != (doc_width, doc_height):
                canvas = Image.new("RGBA", (doc_width, doc_height), (0, 0, 0, 0))
                canvas.paste(img, (0, 0))
                img = canvas
            out.append(
                ExportLayer(
                    name=(node.name or "Layer"),
                    image=img,
                    opacity=int(max(0.0, min(1.0, opacity)) * 255),
                    visible=visible,
                )
            )
            return

        if isinstance(node, GroupLayer):
            for child in node.children:
                walk(child, visible, opacity)
            return

        if hasattr(node, "children"):
            for child in node.children:
                walk(child, visible, opacity)

    walk(root, True, 1.0)
    return out


def write_psd(path: str, width: int, height: int, layers_bottom_to_top: List[ExportLayer]) -> None:
    """Write a Photoshop-compatible PSD from paint layers (bottom -> top input)."""
    if width <= 0 or height <= 0:
        raise ValueError("Invalid PSD size.")

    # PSD layer records are stored top -> bottom.
    layers = list(reversed(layers_bottom_to_top))
    layer_count = len(layers)

    # --- Header ---
    header = b"8BPS"
    header += struct.pack(">H", 1)        # version
    header += b"\x00" * 6                 # reserved
    header += struct.pack(">H", 4)        # channels (RGBA composite)
    header += struct.pack(">I", height)
    header += struct.pack(">I", width)
    header += struct.pack(">H", 8)        # depth
    header += struct.pack(">H", 3)        # color mode: RGB

    color_mode_data = struct.pack(">I", 0)
    image_resources = struct.pack(">I", 0)

    # --- Layer and Mask Info ---
    channel_payloads_per_layer = []
    channel_ids = [0, 1, 2, -1]  # R, G, B, A

    for layer in layers:
        arr = np.array(layer.image.convert("RGBA"), dtype=np.uint8)
        payloads = {
            0: _encode_rle_channel(arr[:, :, 0]),
            1: _encode_rle_channel(arr[:, :, 1]),
            2: _encode_rle_channel(arr[:, :, 2]),
            -1: _encode_rle_channel(arr[:, :, 3]),
        }
        channel_payloads_per_layer.append(payloads)

    layer_info = bytearray()
    layer_info += struct.pack(">h", layer_count)

    # Layer records
    for i, layer in enumerate(layers):
        layer_info += struct.pack(">i", 0)       # top
        layer_info += struct.pack(">i", 0)       # left
        layer_info += struct.pack(">i", height)  # bottom
        layer_info += struct.pack(">i", width)   # right
        layer_info += struct.pack(">H", 4)       # number of channels

        payloads = channel_payloads_per_layer[i]
        for ch_id in channel_ids:
            blob = payloads[ch_id]
            layer_info += struct.pack(">h", ch_id)
            layer_info += struct.pack(">I", len(blob))

        layer_info += b"8BIM"
        layer_info += b"norm"
        layer_info += struct.pack("B", layer.opacity)
        layer_info += struct.pack("B", 0)  # clipping
        flags = 0x00 if layer.visible else 0x02
        layer_info += struct.pack("B", flags)
        layer_info += b"\x00"  # filler

        extra = bytearray()
        extra += struct.pack(">I", 0)               # layer mask data len
        extra += struct.pack(">I", 0)               # blending ranges len
        extra += _pack_pascal_name(layer.name, 4)   # layer name
        layer_info += struct.pack(">I", len(extra))
        layer_info += extra

    # Channel image data
    for payloads in channel_payloads_per_layer:
        for ch_id in channel_ids:
            layer_info += payloads[ch_id]

    if len(layer_info) % 2:
        layer_info += b"\x00"

    layer_info_block = struct.pack(">I", len(layer_info)) + layer_info
    global_layer_mask = struct.pack(">I", 0)
    section4_content = layer_info_block + global_layer_mask
    section4 = struct.pack(">I", len(section4_content)) + section4_content

    # --- Composite Image Data ---
    comp = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for layer in layers_bottom_to_top:
        if not layer.visible:
            continue
        img = layer.image.convert("RGBA")
        if layer.opacity < 255:
            arr = np.array(img, dtype=np.float32)
            arr[:, :, 3] = arr[:, :, 3] * (layer.opacity / 255.0)
            img = Image.fromarray(arr.clip(0, 255).astype(np.uint8), "RGBA")
        comp = Image.alpha_composite(comp, img)

    comp_arr = np.array(comp, dtype=np.uint8)
    section5 = bytearray()
    section5 += struct.pack(">H", 0)  # compression: raw
    section5 += comp_arr[:, :, 0].tobytes()
    section5 += comp_arr[:, :, 1].tobytes()
    section5 += comp_arr[:, :, 2].tobytes()
    section5 += comp_arr[:, :, 3].tobytes()

    with open(path, "wb") as f:
        f.write(header)
        f.write(color_mode_data)
        f.write(image_resources)
        f.write(section4)
        f.write(section5)

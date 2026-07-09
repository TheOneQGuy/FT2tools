
#!/usr/bin/env python3
"""
Font atlas converter for a custom DDS+JSON format.

Features:
- TTF -> custom atlas:
  * Renders glyphs to a transparent atlas
  * Writes a DXT5 DDS with no mipmaps
  * Writes JSON entries with char/x/y/width
  * Optional outline in pixels
  * Adjustable global height


Dependencies:
  pip install pillow

Notes:
- The custom JSON reader ignores extra keys on input.
"""

from __future__ import annotations

import json
import math
import struct
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# DDS (DXT5) helpers
# ---------------------------------------------------------------------------

DDS_MAGIC = b"DDS "
DDS_HEADER_SIZE = 124
DDS_PIXELFORMAT_SIZE = 32
DDS_DXT5_FOURCC = b"DXT5"


def _clamp_u8(v: int) -> int:
    return 0 if v < 0 else 255 if v > 255 else int(v)


def _pack_565(r: int, g: int, b: int) -> int:
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | ((b & 0xF8) >> 3)


def _unpack_565(c: int) -> tuple[int, int, int]:
    r5 = (c >> 11) & 0x1F
    g6 = (c >> 5) & 0x3F
    b5 = c & 0x1F
    r = (r5 * 255 + 15) // 31
    g = (g6 * 255 + 31) // 63
    b = (b5 * 255 + 15) // 31
    return r, g, b


def _alpha_palette(a0: int, a1: int) -> list[int]:
    if a0 > a1:
        return [
            a0,
            a1,
            (6 * a0 + 1 * a1) // 7,
            (5 * a0 + 2 * a1) // 7,
            (4 * a0 + 3 * a1) // 7,
            (3 * a0 + 4 * a1) // 7,
            (2 * a0 + 5 * a1) // 7,
            (1 * a0 + 6 * a1) // 7,
        ]
    return [
        a0,
        a1,
        (4 * a0 + 1 * a1) // 5,
        (3 * a0 + 2 * a1) // 5,
        (2 * a0 + 3 * a1) // 5,
        (1 * a0 + 4 * a1) // 5,
        0,
        255,
    ]


def _color_palette(c0: int, c1: int) -> list[tuple[int, int, int]]:
    r0, g0, b0 = _unpack_565(c0)
    r1, g1, b1 = _unpack_565(c1)
    if c0 > c1:
        return [
            (r0, g0, b0),
            (r1, g1, b1),
            ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3),
            ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3),
        ]
    return [
        (r0, g0, b0),
        (r1, g1, b1),
        ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2),
        (0, 0, 0),
    ]


def _choose_nearest_index(values: Sequence[int], target: int) -> int:
    best_i = 0
    best_d = 10**9
    for i, v in enumerate(values):
        d = abs(int(v) - int(target))
        if d < best_d:
            best_d = d
            best_i = i
    return best_i



def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip()
    if value.startswith("#"):
        value = value[1:]
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise ValueError(f"Invalid color value: {value!r}")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def encode_dxt5_dds(
    image: Image.Image,
    main_rgb: tuple[int, int, int] = (255, 255, 255),
    outline_rgb: tuple[int, int, int] = (0, 0, 0),
    background_rgb: tuple[int, int, int] = (255, 255, 255),
) -> bytes:
    """Encode an RGBA image to a legacy DDS file using DXT5 and no mipmaps."""
    img = image.convert("RGBA")
    width, height = img.size
    raw = img.tobytes()

    def px(x: int, y: int) -> tuple[int, int, int, int]:
        off = (y * width + x) * 4
        return raw[off], raw[off + 1], raw[off + 2], raw[off + 3]

    def nearest_idx(colors: list[tuple[int, int, int]], rgb: tuple[int, int, int]) -> int:
        r, g, b = rgb
        best_i = 0
        best_d = 10**18
        for i, (cr, cg, cb) in enumerate(colors):
            d = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
            if d < best_d:
                best_d = d
                best_i = i
        return best_i

    blocks = bytearray()

    c0 = _pack_565(*main_rgb)
    c1 = _pack_565(*outline_rgb)
    if c0 <= c1:
        c0, c1 = c1, c0

    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            block_pixels = [
                px(min(bx + x, width - 1), min(by + y, height - 1))
                for y in range(4)
                for x in range(4)
            ]

            a0, a1 = 255, 0
            ap = _alpha_palette(a0, a1)
            alpha_index_bits = 0
            for i, (_, _, _, a) in enumerate(block_pixels):
                idx = _choose_nearest_index(ap, a)
                alpha_index_bits |= (idx & 0x7) << (3 * i)

            colors = _color_palette(c0, c1)
            color_index_bits = 0
            for i, (r, g, b, a) in enumerate(block_pixels):
                if a == 0:
                    r, g, b = background_rgb
                idx = nearest_idx(colors, (r, g, b))
                color_index_bits |= (idx & 0x3) << (2 * i)

            blocks.append(a0)
            blocks.append(a1)
            blocks.extend(int(alpha_index_bits).to_bytes(6, "little"))
            blocks.extend(struct.pack("<HHI", c0, c1, color_index_bits))

    linear_size = len(blocks)

    flags = 0x00081007  # CAPS | HEIGHT | WIDTH | PIXELFORMAT | LINEARSIZE
    caps = 0x00001000   # DDSCAPS_TEXTURE

    header = struct.pack("<I", DDS_HEADER_SIZE)
    header += struct.pack("<I", flags)
    header += struct.pack("<I", height)
    header += struct.pack("<I", width)
    header += struct.pack("<I", linear_size)
    header += struct.pack("<I", 0)  # depth
    header += struct.pack("<I", 0)  # mipmap count
    header += b"\x00" * 44         # reserved1[11]

    pf_flags = 0x00000004  # DDPF_FOURCC
    header += struct.pack("<I", DDS_PIXELFORMAT_SIZE)
    header += struct.pack("<I", pf_flags)
    header += DDS_DXT5_FOURCC
    header += struct.pack("<I", 0)  # rgbBitCount
    header += struct.pack("<I", 0)  # rMask
    header += struct.pack("<I", 0)  # gMask
    header += struct.pack("<I", 0)  # bMask
    header += struct.pack("<I", 0)  # aMask

    header += struct.pack("<I", caps)  # caps1
    header += struct.pack("<I", 0)      # caps2
    header += struct.pack("<I", 0)      # caps3
    header += struct.pack("<I", 0)      # caps4
    header += struct.pack("<I", 0)      # reserved2

    assert len(header) == 124, len(header)
    return DDS_MAGIC + header + bytes(blocks)


def decode_dxt5_dds(data: bytes) -> Image.Image:
    """Decode a legacy DDS file with a DXT5 texture into RGBA."""
    if len(data) < 128 or data[:4] != DDS_MAGIC:
        raise ValueError("Not a DDS file.")

    header = data[4:128]
    size, flags, height, width, pitch_or_linear, depth, mipmaps = struct.unpack("<7I", header[:28])
    if size != DDS_HEADER_SIZE:
        raise ValueError("Unsupported DDS header size.")
    pf_off = 72
    pf_size, pf_flags = struct.unpack("<II", header[pf_off:pf_off + 8])
    fourcc = header[pf_off + 8:pf_off + 12]
    if pf_size != DDS_PIXELFORMAT_SIZE or fourcc != DDS_DXT5_FOURCC:
        raise ValueError("Only DXT5 DDS files are supported.")

    block_data = data[128:]
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    blocks_x = (width + 3) // 4
    blocks_y = (height + 3) // 4
    idx = 0

    for by in range(blocks_y):
        for bx in range(blocks_x):
            chunk = block_data[idx:idx + 16]
            if len(chunk) < 16:
                raise ValueError("DDS data is truncated.")
            idx += 16

            a0 = chunk[0]
            a1 = chunk[1]
            alpha_bits = int.from_bytes(chunk[2:8], "little")
            alphas = _alpha_palette(a0, a1)

            c0, c1, color_bits = struct.unpack("<HHI", chunk[8:16])
            colors = _color_palette(c0, c1)

            for py in range(4):
                for px in range(4):
                    x = bx * 4 + px
                    y = by * 4 + py
                    if x >= width or y >= height:
                        continue
                    i = py * 4 + px
                    a_idx = (alpha_bits >> (3 * i)) & 0x7
                    c_idx = (color_bits >> (2 * i)) & 0x3
                    r, g, b = colors[c_idx]
                    a = alphas[a_idx]
                    img.putpixel((x, y), (r, g, b, a))

    return img


def save_dds_dxt5(
    image: Image.Image,
    path: Path,
    main_rgb: tuple[int, int, int] = (255, 255, 255),
    outline_rgb: tuple[int, int, int] = (0, 0, 0),
    background_rgb: tuple[int, int, int] = (255, 255, 255),
) -> None:
    path.write_bytes(
        encode_dxt5_dds(
            image,
            main_rgb=main_rgb,
            outline_rgb=outline_rgb,
            background_rgb=background_rgb,
        )
    )
def decode_dxt5_dds(data: bytes) -> Image.Image:
    """Decode a legacy DDS file with a DXT5 texture into RGBA."""
    if len(data) < 128 or data[:4] != DDS_MAGIC:
        raise ValueError("Not a DDS file.")

    header = data[4:128]
    size, flags, height, width, pitch_or_linear, depth, mipmaps = struct.unpack("<7I", header[:28])
    if size != DDS_HEADER_SIZE:
        raise ValueError("Unsupported DDS header size.")
    pf_off = 72
    pf_size, pf_flags = struct.unpack("<II", header[pf_off:pf_off + 8])
    fourcc = header[pf_off + 8:pf_off + 12]
    if pf_size != DDS_PIXELFORMAT_SIZE or fourcc != DDS_DXT5_FOURCC:
        raise ValueError("Only DXT5 DDS files are supported.")

    block_data = data[128:]
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    blocks_x = (width + 3) // 4
    blocks_y = (height + 3) // 4
    idx = 0

    for by in range(blocks_y):
        for bx in range(blocks_x):
            chunk = block_data[idx:idx + 16]
            if len(chunk) < 16:
                raise ValueError("DDS data is truncated.")
            idx += 16

            a0 = chunk[0]
            a1 = chunk[1]
            alpha_bits = int.from_bytes(chunk[2:8], "little")
            alphas = _alpha_palette(a0, a1)

            c0, c1, color_bits = struct.unpack("<HHI", chunk[8:16])
            colors = _color_palette(c0, c1)

            for py in range(4):
                for px in range(4):
                    x = bx * 4 + px
                    y = by * 4 + py
                    if x >= width or y >= height:
                        continue
                    i = py * 4 + px
                    a_idx = (alpha_bits >> (3 * i)) & 0x7
                    c_idx = (color_bits >> (2 * i)) & 0x3
                    r, g, b = colors[c_idx]
                    a = alphas[a_idx]
                    img.putpixel((x, y), (r, g, b, a))

    return img


def save_dds_dxt5(
    image: Image.Image,
    path: Path,
    main_rgb: tuple[int, int, int] = (255, 255, 255),
    outline_rgb: tuple[int, int, int] = (0, 0, 0),
    background_rgb: tuple[int, int, int] = (255, 255, 255),
) -> None:
    path.write_bytes(
        encode_dxt5_dds(
            image,
            main_rgb=main_rgb,
            outline_rgb=outline_rgb,
            background_rgb=background_rgb,
        )
    )


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def normalize_chars(text: str) -> list[str]:
    seen = set()
    chars = []
    for ch in text:
        if ch in "\r\n\t":
            continue
        if ch not in seen:
            seen.add(ch)
            chars.append(ch)
    return chars


def glyph_name_for_char(ch: str) -> str:
    cp = ord(ch)
    if cp <= 0xFFFF:
        return f"uni{cp:04X}"
    return f"u{cp:06X}"


# ---------------------------------------------------------------------------
# TTF -> custom atlas
# ---------------------------------------------------------------------------

@dataclass
class AtlasGlyph:
    char: str
    width: int
    left_overlap: int
    right_overlap: int
    cell: Image.Image


def _union_bbox(font, chars: Sequence[str], outline_px: int) -> tuple[int, int, int, int]:
    min_x = 10**9
    min_y = 10**9
    max_x = -10**9
    max_y = -10**9

    for ch in chars:
        bbox = font.getbbox(ch, stroke_width=outline_px, anchor="ls")
        if bbox is None:
            continue
        x0, y0, x1, y1 = bbox

        min_x = min(min_x, x0)
        min_y = min(min_y, y0)
        max_x = max(max_x, x1)
        max_y = max(max_y, y1)

    if min_x == 10**9:
        return (0, 0, 0, 0)

    return (min_x, min_y, max_x, max_y)


def _center_fit_rgba(cell: Image.Image, target_h: int, background_rgba: tuple[int, int, int, int]) -> Image.Image:
    """Center-crop or center-pad an RGBA cell to exactly target_h pixels tall."""
    if cell.height == target_h:
        return cell

    if cell.height < target_h:
        out = Image.new("RGBA", (cell.width, target_h), background_rgba)
        y = (target_h - cell.height) // 2
        out.alpha_composite(cell, (0, y))
        return out

    top = (cell.height - target_h) // 2
    return cell.crop((0, top, cell.width, top + target_h))


def _shift_fit_rgba(
    cell: Image.Image,
    target_h: int,
    y_offset: int,
    background_rgba: tuple[int, int, int, int],
) -> Image.Image:
    """Shift a fixed-height RGBA cell vertically, clipping anything outside the window."""
    if cell.height != target_h:
        cell = _center_fit_rgba(cell, target_h, background_rgba)

    y_offset = int(y_offset)
    if y_offset == 0:
        return cell

    out = Image.new("RGBA", (cell.width, target_h), background_rgba)

    src_top = max(0, -y_offset)
    dst_top = max(0, y_offset)
    copy_h = min(cell.height - src_top, target_h - dst_top)

    if copy_h > 0:
        region = cell.crop((0, src_top, cell.width, src_top + copy_h))
        out.alpha_composite(region, (0, dst_top))

    return out



def render_ttf_glyphs(
    font_path: Path,
    chars: Sequence[str],
    height: int,
    outline_px: int,
    scale_pct: int = 100,
    kerning: int = 0,
    y_offset: int = 0,
    main_rgb: tuple[int, int, int] = (255, 255, 255),
    outline_rgb: tuple[int, int, int] = (0, 0, 0),
) -> list[AtlasGlyph]:
    target_h = max(1, int(height))
    outline_px = max(0, int(outline_px))
    scale_pct = max(1, min(400, int(scale_pct)))
    kerning = max(0, int(kerning))
    y_offset = int(y_offset)

    background_rgb = main_rgb if outline_px == 0 else outline_rgb
    background_rgba = (*background_rgb, 0)

    pad = max(2, outline_px + 2)
    y_margin = abs(y_offset)

    # Fit the base font size using only the characters the user chose to export.
    lo = 1
    hi = max(2, target_h * 8)
    base_font = None
    base_bbox = None

    while lo <= hi:
        mid = (lo + hi) // 2
        font = ImageFont.truetype(str(font_path), size=mid)
        bbox = _union_bbox(font, chars, outline_px)
        bbox_h = bbox[3] - bbox[1]

        if bbox_h + pad * 2 <= target_h:
            base_font = font
            base_bbox = bbox
            lo = mid + 1
        else:
            hi = mid - 1

    if base_font is None or base_bbox is None:
        raise ValueError("Could not fit the font into the requested height.")

    base_font_size = base_font.size if hasattr(base_font, "size") else target_h
    render_font_size = max(1, round(base_font_size * scale_pct / 100))
    font = ImageFont.truetype(str(font_path), size=render_font_size)

    # Compute the bbox again at the scaled vector size so we can center/crop
    # after mapping to bitmap, without resizing the bitmap itself.
    render_bbox = _union_bbox(font, chars, outline_px)
    render_h = max(target_h, (render_bbox[3] - render_bbox[1]) + pad * 2)

    # Give the vector draw enough room to move up/down before rasterization.
    # The later center-fit trims back to the fixed global height.
    draw_h = render_h + (y_margin * 2)
    baseline_y = pad - render_bbox[1] + y_margin + y_offset

    glyphs: list[AtlasGlyph] = []

    for ch in chars:
        bbox = font.getbbox(ch, stroke_width=outline_px, anchor="ls")
        if bbox is None:
            bbox = (0, 0, 0, 0)

        x0, y0, x1, y1 = bbox
        left_overlap = max(0, -x0)
        right_overlap = max(0, int(math.ceil(x1 - font.getlength(ch))))
        vis_w = max(1, int(math.ceil(x1 - x0))) + pad * 2

        cell = Image.new("RGBA", (vis_w, draw_h), background_rgba)
        draw = ImageDraw.Draw(cell)

        draw.text(
            (pad - x0, baseline_y),
            ch,
            font=font,
            fill=(*main_rgb, 255),
            stroke_width=outline_px,
            stroke_fill=(*outline_rgb, 255),
            anchor="ls",
        )

        bb = cell.getbbox()
        if bb is not None:
            # Keep the full vertical extent here; the centering/cropping step below
            # is what keeps the final bitmap at the requested global height.
            cell = cell.crop((bb[0], 0, bb[2], draw_h))

        cell = _center_fit_rgba(cell, target_h, background_rgba)

        if kerning > 0:
            left_target = kerning // 2
            right_target = kerning - left_target

            left_pad = max(0, left_target - left_overlap)
            right_pad = max(0, right_target - right_overlap)

            if left_pad or right_pad:
                padded = Image.new(
                    "RGBA",
                    (cell.width + left_pad + right_pad, target_h),
                    background_rgba,
                )
                padded.alpha_composite(cell, (left_pad, 0))
                cell = padded

        glyphs.append(AtlasGlyph(ch, cell.width, left_overlap, right_overlap, cell))

    return glyphs


def next_pow2(n: int) -> int:
    return 1 if n <= 1 else 1 << (n - 1).bit_length()
def next_pow2(n: int) -> int:
    return 1 if n <= 1 else 1 << (n - 1).bit_length()


def pack_atlas(
    glyphs: Sequence[AtlasGlyph],
    atlas_width: int,
    row_gap: int = 1,
    force_pow2_height: bool = True,
    background_rgba: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> tuple[Image.Image, list[dict]]:
    if not glyphs:
        raise ValueError("No glyphs to pack.")

    max_w = max(g.width for g in glyphs)
    atlas_width = next_pow2(max(int(atlas_width), max_w + 1))

    cell_h = glyphs[0].cell.height
    for g in glyphs:
        if g.cell.height != cell_h:
            raise ValueError("All glyph cells must have the same height.")

    x = 0
    y = 0
    placements = []

    rows = []
    current_row_h = cell_h
    current_row = []

    for g in glyphs:
        if x > 0 and x + g.width > atlas_width:
            rows.append((current_row, current_row_h))
            y += current_row_h
            x = 0
            current_row_h = cell_h
            current_row = []

        current_row.append((g, x, y))
        placements.append({"char": g.char, "x": x, "y": y, "width": g.width})
        x += g.width + row_gap
        current_row_h = max(current_row_h, cell_h)

    rows.append((current_row, current_row_h))

    atlas_h = y + current_row_h
    if force_pow2_height:
        atlas_h = next_pow2(atlas_h)

    atlas = Image.new("RGBA", (atlas_width, atlas_h), background_rgba)

    for row, _h in rows:
        for g, px, py in row:
            atlas.alpha_composite(g.cell, (px, py))

    return atlas, placements


def convert_ttf_to_custom(
    font_path: Path,
    charset_text: str,
    height: int,
    outline_px: int,
    scale_pct: int,
    kerning: int,
    y_offset: int,
    atlas_width: int,
    force_pow2_height: bool,
    main_rgb: tuple[int, int, int],
    outline_rgb: tuple[int, int, int],
    background_rgb: tuple[int, int, int],
    json_out: Path,
    dds_out: Path,
) -> None:
    chars = normalize_chars(charset_text)
    if not chars:
        raise ValueError("No characters provided.")

    glyphs = render_ttf_glyphs(
        font_path=font_path,
        chars=chars,
        height=height,
        outline_px=outline_px,
        scale_pct=scale_pct,
        kerning=kerning,
        y_offset=y_offset,
        main_rgb=main_rgb,
        outline_rgb=outline_rgb,
    )

    atlas, entries = pack_atlas(
        glyphs,
        atlas_width=atlas_width,
        row_gap=1,
        force_pow2_height=force_pow2_height,
        background_rgba=(*background_rgb, 0),
    )

    payload = {
        "global_height": int(height),
        "chars": entries,
    }

    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    save_dds_dxt5(
        atlas,
        dds_out,
        main_rgb=main_rgb,
        outline_rgb=outline_rgb,
        background_rgb=background_rgb,
    )

# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ConverterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("TTF to FT2")
        self.geometry("920x760")

        self.ttf_path = tk.StringVar()
        self.out_json_path = tk.StringVar()
        self.out_dds_path = tk.StringVar()
        self.height = tk.IntVar(value=60)
        self.outline = tk.IntVar(value=0)
        self.scale = tk.IntVar(value=100)
        self.kerning = tk.IntVar(value=0)
        self.y_offset = tk.IntVar(value=0)
        self.force_pow2_height = tk.BooleanVar(value=True)
        self.atlas_width = tk.IntVar(value=1024)
        self.main_color = tk.StringVar(value="#ffffff")
        self.outline_color = tk.StringVar(value="#000000")

        self._build_ui()

        self.main_color.trace_add("write", self._sync_color_widgets)
        self.outline_color.trace_add("write", self._sync_color_widgets)

    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}

        cfg = ttk.LabelFrame(self, text="Common settings")
        cfg.pack(fill="x", **pad)

        self._add_spin(cfg, "Global height", self.height, 1, 4096, 0)
        self._add_spin(cfg, "Outline px", self.outline, 0, 256, 1)
        self._add_spin(cfg, "Atlas width", self.atlas_width, 32, 8192, 2)
        self._add_spin(cfg, "Kerning", self.kerning, 0, 64, 0, 1)
        self._add_spin(cfg, "Scale %", self.scale, 10, 400, 1, 1)

        ttk.Checkbutton(cfg, text="Force power-of-two atlas height", variable=self.force_pow2_height).grid(
            row=1, column=4, columnspan=4, sticky="w", padx=6, pady=4
        )

        ttk.Label(cfg, text="Main color").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.main_color_swatch = tk.Label(cfg, width=10, bg=self.main_color.get(), relief="sunken", cursor="hand2")
        self.main_color_swatch.grid(row=2, column=1, sticky="w", padx=6, pady=4)
        self.main_color_swatch.bind("<Button-1>", lambda _e: self._pick_main_color())

        ttk.Label(cfg, text="Outline color").grid(row=2, column=2, sticky="w", padx=6, pady=4)
        self.outline_color_swatch = tk.Label(cfg, width=10, bg=self.outline_color.get(), relief="sunken", cursor="hand2")
        self.outline_color_swatch.grid(row=2, column=3, sticky="w", padx=6, pady=4)
        self.outline_color_swatch.bind("<Button-1>", lambda _e: self._pick_outline_color())

        self._add_spin(cfg, "Y offset", self.y_offset, -2048, 2048, 2, 2)

        cfg.columnconfigure(1, weight=1)
        cfg.columnconfigure(3, weight=1)

        self.ttf_frame = ttk.LabelFrame(self, text="TTF -> custom")
        self.ttf_frame.pack(fill="x", **pad)

        self._add_path_row(self.ttf_frame, "TTF input", self.ttf_path, self._browse_ttf, 0)
        self._add_path_row(self.ttf_frame, "JSON output", self.out_json_path, self._browse_json_out, 1)
        self._add_path_row(self.ttf_frame, "DDS output", self.out_dds_path, self._browse_dds_out, 2)

        ttk.Label(self.ttf_frame, text="Characters to export").grid(row=3, column=0, sticky="nw", padx=6, pady=4)
        self.char_text = tk.Text(self.ttf_frame, height=10, wrap="word")
        self.char_text.grid(row=3, column=1, columnspan=2, sticky="nsew", padx=6, pady=4)
        self.char_text.insert("1.0", "".join(chr(i) for i in range(32, 127)))
        self.ttf_frame.columnconfigure(1, weight=1)
        self.ttf_frame.columnconfigure(2, weight=1)
        self.ttf_frame.rowconfigure(3, weight=1)

        btns = ttk.Frame(self)
        btns.pack(fill="x", **pad)
        ttk.Button(btns, text="Convert", command=self.convert).pack(side="left")
        ttk.Button(btns, text="Clear log", command=lambda: self.log.delete("1.0", "end")).pack(side="left", padx=6)

        log_box = ttk.LabelFrame(self, text="Log")
        log_box.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(log_box, height=12, wrap="word")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    def _add_spin(self, parent: ttk.LabelFrame, label: str, var: tk.IntVar, mn: int, mx: int, col: int, row: int = 0) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=col * 2, sticky="w", padx=6, pady=4)
        spin = ttk.Spinbox(parent, from_=mn, to=mx, textvariable=var, width=8)
        spin.grid(row=row, column=col * 2 + 1, sticky="w", padx=6, pady=4)

    def _add_path_row(self, parent: ttk.LabelFrame, label: str, var: tk.StringVar, browse_cb, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(parent, text="Browse", command=browse_cb).grid(row=row, column=2, sticky="e", padx=6, pady=4)

    def _sync_color_widgets(self, *_args) -> None:
        for var, swatch in ((self.main_color, self.main_color_swatch), (self.outline_color, self.outline_color_swatch)):
            value = var.get().strip()
            try:
                rgb = _hex_to_rgb(value)
            except Exception:
                continue
            normalized = _rgb_to_hex(rgb)
            if value != normalized:
                # avoid infinite trace loops by only setting when needed
                var.set(normalized)
                return
            swatch.config(bg=normalized)

    def _pick_main_color(self) -> None:
        _, hex_value = colorchooser.askcolor(color=self.main_color.get(), parent=self)
        if hex_value:
            self.main_color.set(_rgb_to_hex(tuple(int(round(v)) for v in _hex_to_rgb(hex_value))))

    def _pick_outline_color(self) -> None:
        _, hex_value = colorchooser.askcolor(color=self.outline_color.get(), parent=self)
        if hex_value:
            self.outline_color.set(_rgb_to_hex(tuple(int(round(v)) for v in _hex_to_rgb(hex_value))))

    def _browse_ttf(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("TrueType font", "*.ttf *.otf"), ("All files", "*.*")])
        if p:
            self.ttf_path.set(p)

    def _browse_json_out(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if p:
            self.out_json_path.set(p)
            if not self.out_dds_path.get().strip():
                self.out_dds_path.set(str(Path(p).with_suffix(".dds")))

    def _browse_dds_out(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".dds", filetypes=[("DDS", "*.dds"), ("All files", "*.*")])
        if p:
            self.out_dds_path.set(p)
            if not self.out_json_path.get().strip():
                self.out_json_path.set(str(Path(p).with_suffix(".json")))

    def log_line(self, text: str) -> None:
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.update_idletasks()

    def convert(self) -> None:
        try:
            font_path = Path(self.ttf_path.get().strip())
            json_out = Path(self.out_json_path.get().strip())
            dds_out = Path(self.out_dds_path.get().strip())

            if not font_path.is_file():
                raise FileNotFoundError("Choose a valid TTF/OTF input file.")
            if not self.char_text.get("1.0", "end").strip():
                raise ValueError("Enter or paste the characters to export.")
            if not self.out_json_path.get().strip():
                raise FileNotFoundError("Choose a JSON output file.")
            if not self.out_dds_path.get().strip():
                raise FileNotFoundError("Choose a DDS output file.")

            main_rgb = _hex_to_rgb(self.main_color.get())
            outline_rgb = _hex_to_rgb(self.outline_color.get())
            background_rgb = main_rgb if int(self.outline.get()) == 0 else outline_rgb

            self.log_line("Rendering glyphs...")
            convert_ttf_to_custom(
                font_path=font_path,
                charset_text=self.char_text.get("1.0", "end"),
                height=int(self.height.get()),
                outline_px=int(self.outline.get()),
                scale_pct=int(self.scale.get()),
                kerning=int(self.kerning.get()),
                y_offset=int(self.y_offset.get()),
                atlas_width=int(self.atlas_width.get()),
                force_pow2_height=bool(self.force_pow2_height.get()),
                main_rgb=main_rgb,
                outline_rgb=outline_rgb,
                background_rgb=background_rgb,
                json_out=json_out,
                dds_out=dds_out,
            )
            self.log_line(f"Saved JSON: {json_out}")
            self.log_line(f"Saved DDS:  {dds_out}")
            messagebox.showinfo("Done", "Conversion finished successfully.")
        except Exception as exc:
            self.log_line("ERROR:")
            self.log_line(str(exc))
            self.log_line(traceback.format_exc())
            messagebox.showerror("Conversion failed", str(exc))
def main() -> None:
    app = ConverterApp()
    app.mainloop()


if __name__ == "__main__":
    main()

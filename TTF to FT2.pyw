
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
from fontTools.ttLib import TTFont

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


def _nearest_palette_index(colors: list[tuple[int, int, int]], rgb: tuple[int, int, int]) -> int:
    r, g, b = rgb
    best_i = 0
    best_d = 10**18
    for i, (cr, cg, cb) in enumerate(colors):
        d = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


def _generate_mipmap_levels(image: Image.Image, generate_mipmaps: bool) -> list[Image.Image]:
    levels = [image.convert("RGBA")]
    if not generate_mipmaps:
        return levels

    current = levels[0]
    while current.width > 1 or current.height > 1:
        new_w = max(1, current.width // 2)
        new_h = max(1, current.height // 2)
        current = current.resize((new_w, new_h), Image.Resampling.LANCZOS)
        levels.append(current)

    return levels


def _encode_dxt5_level(image: Image.Image) -> bytes:
    img = image.convert("RGBA")
    width, height = img.size
    raw = img.tobytes()

    def px(x: int, y: int) -> tuple[int, int, int, int]:
        off = (y * width + x) * 4
        return raw[off], raw[off + 1], raw[off + 2], raw[off + 3]

    # Use a broad black/white color range for the two-color font atlas.
    c0 = _pack_565(255, 255, 255)
    c1 = _pack_565(0, 0, 0)
    if c0 <= c1:
        c0, c1 = c1, c0
    colors = _color_palette(c0, c1)
    blocks = bytearray()

    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            block_pixels = [
                px(min(bx + x, width - 1), min(by + y, height - 1))
                for y in range(4)
                for x in range(4)
            ]

            # DXT5 alpha block. 255/0 gives the full 8-entry interpolation range.
            a0, a1 = 255, 0
            ap = _alpha_palette(a0, a1)
            alpha_bits = 0
            for i, (_, _, _, a) in enumerate(block_pixels):
                ai = min(range(8), key=lambda j: abs(ap[j] - a))
                alpha_bits |= (ai & 0x7) << (3 * i)

            color_bits = 0
            for i, (r, g, b, a) in enumerate(block_pixels):
                ci = _nearest_palette_index(colors, (r, g, b))
                color_bits |= (ci & 0x3) << (2 * i)

            blocks.extend(bytes((a0, a1)))
            blocks.extend(alpha_bits.to_bytes(6, "little"))
            blocks.extend(struct.pack("<HHI", c0, c1, color_bits))

    return bytes(blocks)


def _encode_uncompressed_level(image: Image.Image) -> bytes:
    # Standard 32-bit A8R8G8B8 DDS payload, little-endian BGRA bytes.
    rgba = image.convert("RGBA")
    raw = bytearray(rgba.tobytes())
    for i in range(0, len(raw), 4):
        raw[i], raw[i + 2] = raw[i + 2], raw[i]
    return bytes(raw)


def _dds_header(
    width: int,
    height: int,
    top_level_size: int,
    compression: str,
    mip_count: int,
) -> bytes:
    is_dxt5 = compression == "DXT5"
    flags = 0x00001007 | (0x00080000 if is_dxt5 else 0x00000008)
    if mip_count > 1:
        flags |= 0x00020000

    caps = 0x00001000
    if mip_count > 1:
        caps |= 0x00000008 | 0x00400000

    header = struct.pack("<I", DDS_HEADER_SIZE)
    header += struct.pack("<I", flags)
    header += struct.pack("<I", height)
    header += struct.pack("<I", width)
    header += struct.pack("<I", top_level_size)
    header += struct.pack("<I", 0)
    header += struct.pack("<I", mip_count if mip_count > 1 else 0)
    header += b"\x00" * 44

    if is_dxt5:
        header += struct.pack("<I", DDS_PIXELFORMAT_SIZE)
        header += struct.pack("<I", 0x00000004)
        header += DDS_DXT5_FOURCC
        header += struct.pack("<I", 0)
        header += struct.pack("<I", 0)
        header += struct.pack("<I", 0)
        header += struct.pack("<I", 0)
        header += struct.pack("<I", 0)
    else:
        header += struct.pack("<I", DDS_PIXELFORMAT_SIZE)
        header += struct.pack("<I", 0x00000041)  # RGB | ALPHAPIXELS
        header += struct.pack("<I", 0)
        header += struct.pack("<I", 32)
        header += struct.pack("<I", 0x00FF0000)
        header += struct.pack("<I", 0x0000FF00)
        header += struct.pack("<I", 0x000000FF)
        header += struct.pack("<I", 0xFF000000)

    header += struct.pack("<I", caps)
    header += struct.pack("<I", 0)
    header += struct.pack("<I", 0)
    header += struct.pack("<I", 0)
    header += struct.pack("<I", 0)

    assert len(header) == DDS_HEADER_SIZE
    return header


def encode_dds(
    image: Image.Image,
    compression: str = "DXT5",
    generate_mipmaps: bool = False,
) -> bytes:
    compression = compression.lower()
    if compression not in {"dxt5", "uncompressed"}:
        raise ValueError("DDS compression must be DXT5 or uncompressed.")
    compression_name = "DXT5" if compression == "dxt5" else "uncompressed"

    levels = _generate_mipmap_levels(image, generate_mipmaps)
    payloads = []
    for level in levels:
        if compression_name == "DXT5":
            payloads.append(_encode_dxt5_level(level))
        else:
            payloads.append(_encode_uncompressed_level(level))

    top_size = len(payloads[0]) if compression_name == "DXT5" else levels[0].width * 4
    header = _dds_header(
        levels[0].width,
        levels[0].height,
        top_size,
        compression_name,
        len(levels),
    )
    return DDS_MAGIC + header + b"".join(payloads)


def encode_dxt5_dds(
    image: Image.Image,
    main_rgb: tuple[int, int, int] = (255, 255, 255),
    outline_rgb: tuple[int, int, int] = (0, 0, 0),
    background_rgb: tuple[int, int, int] = (255, 255, 255),
    generate_mipmaps: bool = False,
) -> bytes:
    return encode_dds(image, "DXT5", generate_mipmaps)


def decode_dds(data: bytes) -> Image.Image:
    if len(data) < 128 or data[:4] != DDS_MAGIC:
        raise ValueError("Not a DDS file.")

    header = data[4:128]
    size, flags, height, width, pitch_or_linear, depth, mipmaps = struct.unpack("<7I", header[:28])
    if size != DDS_HEADER_SIZE:
        raise ValueError("Unsupported DDS header size.")

    pf_off = 72
    pf_size, pf_flags = struct.unpack("<II", header[pf_off:pf_off + 8])
    fourcc = header[pf_off + 8:pf_off + 12]
    if pf_size != DDS_PIXELFORMAT_SIZE:
        raise ValueError("Unsupported DDS pixel format.")

    mip_count = mipmaps if mipmaps else 1
    block_data = data[128:]

    if fourcc == DDS_DXT5_FOURCC:
        blocks_x = (width + 3) // 4
        blocks_y = (height + 3) // 4
        level_size = blocks_x * blocks_y * 16
        block_data = block_data[:level_size]
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
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
                        img.putpixel((x, y), (r, g, b, alphas[a_idx]))
        return img

    # Uncompressed 32-bit A8R8G8B8
    if fourcc != b"\x00\x00\x00\x00" or (pf_flags & 0x40) == 0:
        raise ValueError("Unsupported DDS format. Expected DXT5 or 32-bit uncompressed DDS.")

    pitch = pitch_or_linear
    expected = height * pitch
    raw = block_data[:expected]
    if len(raw) < expected:
        raise ValueError("DDS data is truncated.")

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for y in range(height):
        row = raw[y * pitch:(y + 1) * pitch]
        for x in range(width):
            off = x * 4
            b, g, r, a = row[off:off + 4]
            img.putpixel((x, y), (r, g, b, a))
    return img


def decode_dxt5_dds(data: bytes) -> Image.Image:
    return decode_dds(data)


def save_dds_dxt5(
    image: Image.Image,
    path: Path,
    main_rgb: tuple[int, int, int] = (255, 255, 255),
    outline_rgb: tuple[int, int, int] = (0, 0, 0),
    background_rgb: tuple[int, int, int] = (255, 255, 255),
    compression: str = "DXT5",
    generate_mipmaps: bool = False,
) -> None:
    path.write_bytes(encode_dds(image, compression, generate_mipmaps))


def save_dds(
    image: Image.Image,
    path: Path,
    compression: str = "DXT5",
    generate_mipmaps: bool = False,
) -> None:
    path.write_bytes(encode_dds(image, compression, generate_mipmaps))

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


def unsupported_chars_for_font(font_path: Path, chars: Sequence[str]) -> list[str]:
    with TTFont(str(font_path), lazy=True) as font:
        cmap = font.getBestCmap() or {}
    unsupported = [ch for ch in chars if ord(ch) not in cmap]
    return unsupported


def chars_from_custom_json_file(path: Path) -> list[str]:
    root = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(root, dict):
        raise ValueError("Expected a JSON object with a 'chars' list.")
    chars_section = root.get("chars")
    if not isinstance(chars_section, list):
        raise ValueError("Could not find a 'chars' list in the JSON file.")

    chars: list[str] = []
    for entry in chars_section:
        if not isinstance(entry, dict):
            continue
        ch = entry.get("char")
        if isinstance(ch, str) and ch:
            chars.append(ch)

    chars = normalize_chars("".join(chars))
    if not chars:
        raise ValueError("No characters were found inside chars[*].char.")
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
    advance: float | None = None
    draw_offset: int | None = None


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



def _get_gpos_kern_lookup_indices(font) -> set[int]:
    """Return GPOS lookup indices associated with the 'kern' feature."""
    result: set[int] = set()
    try:
        gpos = font["GPOS"].table
        feature_list = gpos.FeatureList
        if feature_list is None:
            return result
        lookup_indices = gpos.LookupList.Lookup
        for feature_record in feature_list.FeatureRecord:
            if feature_record.FeatureTag == "kern":
                result.update(feature_record.Feature.LookupListIndex)
    except Exception:
        pass
    return result


def _resolve_pairpos_subtable(subtable):
    """Yield PairPos subtables, including PairPos wrapped in ExtensionPos."""
    try:
        if getattr(subtable, "LookupType", None) == 2:
            yield subtable
        elif getattr(subtable, "ExtensionLookupType", None) == 2:
            ext = getattr(subtable, "ExtSubTable", None)
            if ext is not None:
                yield ext
    except Exception:
        return


def _gpos_pair_value_units(font, left_gid: int, right_gid: int) -> int:
    """Return accumulated GPOS xAdvance adjustment for a glyph pair in font units."""
    total = 0
    try:
        gpos = font["GPOS"].table
        lookup_list = gpos.LookupList.Lookup
        kern_indices = _get_gpos_kern_lookup_indices(font)

        for lookup_index, lookup in enumerate(lookup_list):
            if kern_indices and lookup_index not in kern_indices:
                continue
            if lookup.LookupType not in (2, 9):
                continue

            first_name = font.getGlyphName(left_gid)
            second_name = font.getGlyphName(right_gid)

            for raw_subtable in lookup.SubTable:
                for subtable in _resolve_pairpos_subtable(raw_subtable):
                    fmt = getattr(subtable, "Format", None)

                    if fmt == 1:
                        coverage = getattr(subtable, "Coverage", None)
                        if coverage is None or first_name not in coverage.glyphs:
                            continue
                        cov_index = coverage.glyphs.index(first_name)
                        pair_sets = getattr(subtable, "PairSet", [])
                        if cov_index >= len(pair_sets):
                            continue
                        for record in pair_sets[cov_index].PairValueRecord:
                            if record.SecondGlyph == second_name:
                                value1 = getattr(record, "Value1", None)
                                if value1 is not None:
                                    total += int(getattr(value1, "XAdvance", 0) or 0)
                                break

                    elif fmt == 2:
                        coverage = getattr(subtable, "Coverage", None)
                        if coverage is None or first_name not in coverage.glyphs:
                            continue

                        class1_def = getattr(subtable, "ClassDef1", None)
                        class2_def = getattr(subtable, "ClassDef2", None)
                        class1 = 0 if class1_def is None else int(class1_def.classDefs.get(first_name, 0))
                        class2 = 0 if class2_def is None else int(class2_def.classDefs.get(second_name, 0))

                        class1_records = getattr(subtable, "Class1Record", [])
                        if class1 >= len(class1_records):
                            continue
                        class2_records = getattr(class1_records[class1], "Class2Record", [])
                        if class2 >= len(class2_records):
                            continue
                        value1 = getattr(class2_records[class2], "Value1", None)
                        if value1 is not None:
                            total += int(getattr(value1, "XAdvance", 0) or 0)
    except Exception:
        return total
    return total


def _kern_table_pair_units(font, left_gid: int, right_gid: int) -> int:
    total = 0
    try:
        kern = font.get("kern")
        if kern is not None:
            left_name = font.getGlyphName(left_gid)
            right_name = font.getGlyphName(right_gid)
            for table in kern.kernTables:
                if getattr(table, "version", 0) == 0 and hasattr(table, "kernTable"):
                    total += int(table.kernTable.get((left_name, right_name), 0))
    except Exception:
        pass
    return total


def _pillow_pair_kerning_units(font, left: str, right: str) -> float:
    """Measure the effective pair-spacing adjustment in bitmap pixels.

    Measuring the rendered pair directly catches both negative and positive
    GPOS kerning, including fonts that express a positive adjustment in a way
    that is not exposed by the simple Value1.XAdvance table walk.

    Ligature/contextual substitutions are disabled so this value represents
    pair spacing rather than a ligature width change.
    """
    features = ["-liga", "-clig", "-calt"]
    try:
        pair_advance = float(font.getlength(left + right, features=features))
        left_advance = float(font.getlength(left, features=features))
        right_advance = float(font.getlength(right, features=features))
        return pair_advance - left_advance - right_advance
    except Exception:
        return 0.0


def calculate_font_kerning(
    font_path: Path,
    pixel_size: int,
    chars: Sequence[str],
) -> list[list[object]]:
    """Return every non-zero positive/negative pair adjustment in bitmap pixels.

    The primary measurement is the actual rendered pair advance from Pillow,
    with ligatures/contextual substitutions disabled.  This preserves positive
    kerning as well as negative kerning.  The OpenType GPOS/legacy-table logic
    remains as a fallback for fonts whose renderer does not expose the pair
    adjustment directly.
    """
    kerning_pairs: list[list[object]] = []

    font = ImageFont.truetype(str(font_path), size=max(1, int(pixel_size)))

    # Keep the fallback available for fonts Pillow cannot expose through
    # getlength(), but use the rendered-pair result whenever it is non-zero.
    fallback_by_pair: dict[str, float] = {}
    try:
        with TTFont(str(font_path), lazy=False) as ttfont:
            units_per_em = int(ttfont["head"].unitsPerEm)
            cmap = ttfont.getBestCmap() or {}
            for left in chars:
                left_gid = ttfont.getGlyphID(cmap.get(ord(left), ".notdef"))
                for right in chars:
                    right_gid = ttfont.getGlyphID(cmap.get(ord(right), ".notdef"))
                    units = _gpos_pair_value_units(ttfont, left_gid, right_gid)
                    if units == 0:
                        units = _kern_table_pair_units(ttfont, left_gid, right_gid)
                    if units:
                        fallback_by_pair[left + right] = (
                            float(units) * float(pixel_size) / float(units_per_em)
                        )
    except Exception:
        pass

    for left in chars:
        for right in chars:
            pair = left + right
            pixels = _pillow_pair_kerning_units(font, left, right)
            if abs(pixels) <= 1e-6:
                pixels = fallback_by_pair.get(pair, 0.0)

            if abs(pixels) > 1e-6:
                kerning_pairs.append([pair, round(pixels, 4)])

    return kerning_pairs

def render_ttf_glyphs(
    font_path: Path,
    chars: Sequence[str],
    height: int,
    outline_px: int,
    bold_px: int = 0,
    scale_pct: int = 100,
    tracking: int = 0,
    y_offset: int = 0,
    main_rgb: tuple[int, int, int] = (255, 255, 255),
    outline_rgb: tuple[int, int, int] = (0, 0, 0),
) -> tuple[list[AtlasGlyph], list[list[object]]]:
    target_h = max(1, int(height))
    outline_px = max(0, int(outline_px))
    # Bold is the inner, main-color stroke. Keep it inside the outer outline.
    bold_px = max(0, min(int(bold_px), outline_px))
    scale_pct = max(1, min(400, int(scale_pct)))
    tracking = max(0, int(tracking))
    y_offset = int(y_offset)

    background_rgb = main_rgb if outline_px == 0 else outline_rgb
    background_rgba = (*background_rgb, 0)

    pad = max(2, outline_px + 2)
    y_margin = abs(y_offset)

    # Fit the base vector size using ONLY the characters selected for export.
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

    base_font_size = base_font.size
    render_font_size = max(1, round(base_font_size * scale_pct / 100))
    font = ImageFont.truetype(str(font_path), size=render_font_size)

    # Kerning pairs remain pair-specific JSON values. Tracking is the physical
    # per-glyph spacing that is baked directly into each atlas cell.
    kerning_pairs = calculate_font_kerning(font_path, render_font_size, chars)

    render_bbox = _union_bbox(font, chars, outline_px)
    render_h = max(target_h, (render_bbox[3] - render_bbox[1]) + pad * 2)
    draw_h = render_h + (y_margin * 2)
    baseline_y = pad - render_bbox[1] + y_margin + y_offset

    glyphs: list[AtlasGlyph] = []

    for ch in chars:
        advance_px = float(font.getlength(ch))

        if ch == " ":
            # Space has no bitmap. Its logical width includes the full tracking
            # because the tracking is physical spacing baked into the atlas.
            space_width = max(1, int(round(advance_px + tracking)))
            glyphs.append(
                AtlasGlyph(
                    ch,
                    space_width,
                    0,
                    0,
                    Image.new("RGBA", (1, target_h), background_rgba),
                    None,
                    None,
                )
            )
            continue

        # IMPORTANT: advance and draw_offset metrics are measured from the
        # UNSTROKED font. Outline and Bold are visual-only and never change
        # these values.
        metric_bbox = font.getbbox(ch, stroke_width=0, anchor="ls")
        if metric_bbox is None:
            metric_bbox = (0, 0, 0, 0)

        mx0, my0, mx1, my1 = metric_bbox
        mx0 = int(math.floor(mx0))
        mx1 = int(math.ceil(mx1))

        left_overlap = max(0, -mx0)
        right_overlap = max(0, int(math.ceil(mx1 - advance_px)))

        # The raw draw offset is exactly the font's unstroked left bearing.
        # Positive values mean "add space on the left"; negative values mean
        # "remove space from the left".
        raw_draw_offset = mx0

        # Render into a temporary padded surface only so the visual stroke can
        # be rasterized safely. Then crop ALL transparent horizontal margin.
        # The atlas cell must not contain arbitrary padding around the glyph:
        # tracking and explicitly baked offsets are the only intentional blank
        # space.
        render_pad = max(2, outline_px + bold_px + 2)
        content_w = max(1, mx1 - mx0)
        temp_w = content_w + render_pad * 2
        draw_x = render_pad - mx0
        cell = Image.new("RGBA", (temp_w, draw_h), background_rgba)
        draw = ImageDraw.Draw(cell)

        # Draw the outer outline first, then the optional inner main-color bold
        # stroke. Neither stroke affects the metric values above.
        draw.text(
            (draw_x, baseline_y),
            ch,
            font=font,
            fill=(*main_rgb, 255),
            stroke_width=outline_px,
            stroke_fill=(*outline_rgb, 255),
            anchor="ls",
        )
        if bold_px > 0:
            draw.text(
                (draw_x, baseline_y),
                ch,
                font=font,
                fill=(*main_rgb, 255),
                stroke_width=bold_px,
                stroke_fill=(*main_rgb, 255),
                anchor="ls",
            )

        cell = _center_fit_rgba(cell, target_h, background_rgba)

        # Remove every horizontal transparent pixel introduced only for
        # rendering. This is what prevents the old pad from becoming a fake
        # negative advance. Outline/bold pixels remain; only empty pixels are
        # cropped.
        alpha_bbox = cell.getbbox()
        if alpha_bbox is not None:
            crop_left = max(0, alpha_bbox[0])
            crop_right = min(cell.width, alpha_bbox[2])
            if crop_right > crop_left:
                cell = cell.crop((crop_left, 0, crop_right, target_h))
        else:
            cell = Image.new("RGBA", (1, target_h), background_rgba)

        # ---------------------------------------------------------------
        # 1) Initial tracking is added first. This is the only blank space
        #    that negative draw/advance offsets may consume.
        # ---------------------------------------------------------------
        left_track = tracking // 2
        right_track = tracking - left_track

        if left_track or right_track:
            padded = Image.new(
                "RGBA",
                (cell.width + tracking, target_h),
                background_rgba,
            )
            padded.alpha_composite(cell, (left_track, 0))
            cell = padded

        remaining_left_track = left_track
        remaining_right_track = right_track

        # ---------------------------------------------------------------
        # 2) draw_offset operates ONLY on the LEFT side.
        #    Positive => add left whitespace.
        #    Negative => consume left tracking; anything left becomes JSON.
        # ---------------------------------------------------------------
        remaining_draw_offset: int | None = None
        if raw_draw_offset > 0:
            expanded = Image.new(
                "RGBA",
                (cell.width + raw_draw_offset, target_h),
                background_rgba,
            )
            expanded.alpha_composite(cell, (raw_draw_offset, 0))
            cell = expanded
        elif raw_draw_offset < 0:
            need = -raw_draw_offset
            consume = min(need, remaining_left_track)
            if consume:
                cell = cell.crop((consume, 0, cell.width, cell.height))
                remaining_left_track -= consume
                need -= consume
            if need:
                remaining_draw_offset = -need

        # ---------------------------------------------------------------
        # 3) advance offset operates ONLY on the RIGHT side. The metric
        #    width is the UNSTROKED glyph width, so outline/bold cannot create
        #    an advance offset. Positive => add right whitespace. Negative
        #    => consume right tracking; anything left becomes JSON.
        # ---------------------------------------------------------------
        metric_width = max(1, mx1 - mx0)
        raw_advance_offset = float(advance_px) - float(metric_width)
        remaining_advance_offset: float | None = None

        if raw_advance_offset > 1e-6:
            add = int(math.ceil(raw_advance_offset - 1e-6))
            if add > 0:
                expanded = Image.new(
                    "RGBA",
                    (cell.width + add, target_h),
                    background_rgba,
                )
                expanded.alpha_composite(cell, (0, 0))
                cell = expanded
        elif raw_advance_offset < -1e-6:
            need = int(math.ceil(-raw_advance_offset - 1e-6))
            consume = min(need, remaining_right_track)
            if consume:
                cell = cell.crop((0, 0, cell.width - consume, cell.height))
                remaining_right_track -= consume
                need -= consume
            if need:
                remaining_advance_offset = -float(need)

        if remaining_draw_offset is not None and remaining_draw_offset == 0:
            remaining_draw_offset = None

        if remaining_advance_offset is not None:
            remaining_advance_offset = round(remaining_advance_offset, 4)
            if abs(remaining_advance_offset) < 1e-6:
                remaining_advance_offset = None

        advance_override = remaining_advance_offset

        glyphs.append(
            AtlasGlyph(
                ch,
                cell.width,
                left_overlap,
                right_overlap,
                cell,
                advance_override,
                remaining_draw_offset,
            )
        )

    return glyphs, kerning_pairs

def next_pow2(n: int) -> int:
    return 1 if n <= 1 else 1 << (n - 1).bit_length()


def pack_atlas(
    glyphs: Sequence[AtlasGlyph],
    atlas_width: int,
    glyph_gap: int = 4,
    force_pow2_height: bool = True,
    background_rgba: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> tuple[Image.Image, list[dict]]:
    if not glyphs:
        raise ValueError("No glyphs to pack.")

    glyph_gap = max(0, int(glyph_gap))
    non_space = [g for g in glyphs if g.char != " "]
    max_w = max((g.width for g in non_space), default=1)
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
        if g.char == " ":
            # Space is logical only. Its baked width is not atlas spacing.
            space_entry = {
                "char": " ",
                "width": g.width,
                "special": "space",
            }
            if g.advance is not None:
                space_entry["advance"] = g.advance
            if g.draw_offset is not None:
                space_entry["draw_offset"] = g.draw_offset
            placements.append(space_entry)
            continue

        if x > 0 and x + g.width > atlas_width:
            rows.append((current_row, current_row_h))
            y += current_row_h
            x = 0
            current_row_h = cell_h
            current_row = []

        current_row.append((g, x, y))
        entry = {"char": g.char, "x": x, "y": y, "width": g.width}
        if g.advance is not None:
            entry["advance"] = g.advance
        if g.draw_offset is not None:
            entry["draw_offset"] = g.draw_offset
        placements.append(entry)
        x += g.width + glyph_gap
        current_row_h = max(current_row_h, cell_h)

    if current_row:
        rows.append((current_row, current_row_h))

    atlas_h = y + current_row_h if rows else cell_h
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
    bold_px: int,
    scale_pct: int,
    tracking: int,
    y_offset: int,
    atlas_width: int,
    glyph_gap: int,
    force_pow2_height: bool,
    main_rgb: tuple[int, int, int],
    outline_rgb: tuple[int, int, int],
    background_rgb: tuple[int, int, int],
    dds_compression: str,
    generate_mipmaps: bool,
    json_out: Path,
    dds_out: Path,
) -> None:
    chars = normalize_chars(charset_text)
    if not chars:
        raise ValueError("No characters provided.")

    glyphs, kerning_pairs = render_ttf_glyphs(
        font_path=font_path,
        chars=chars,
        height=height,
        outline_px=outline_px,
        bold_px=bold_px,
        scale_pct=scale_pct,
        tracking=tracking,
        y_offset=y_offset,
        main_rgb=main_rgb,
        outline_rgb=outline_rgb,
    )

    atlas, entries = pack_atlas(
        glyphs,
        atlas_width=atlas_width,
        glyph_gap=glyph_gap,
        force_pow2_height=force_pow2_height,
        background_rgba=(*background_rgb, 0),
    )

    payload = {
        "global_height": int(height),
        "chars": entries,
        "kerning": kerning_pairs,
    }

    json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    save_dds(
        atlas,
        dds_out,
        compression=dds_compression,
        generate_mipmaps=generate_mipmaps,
    )

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
        self.height = tk.IntVar(value=60)
        self.outline = tk.IntVar(value=0)
        self.bold = tk.IntVar(value=0)
        self.scale = tk.IntVar(value=100)
        self.tracking = tk.IntVar(value=0)
        self.y_offset = tk.IntVar(value=0)
        self.force_pow2_height = tk.BooleanVar(value=True)
        self.atlas_width = tk.IntVar(value=1024)
        self.glyph_gap = tk.IntVar(value=4)
        self.dds_compression = tk.StringVar(value="DXT5")
        self.generate_mipmaps = tk.BooleanVar(value=False)
        self.main_color = tk.StringVar(value="#ffffff")
        self.outline_color = tk.StringVar(value="#000000")

        self._build_ui()

        self.main_color.trace_add("write", self._sync_color_widgets)
        self.outline_color.trace_add("write", self._sync_color_widgets)

    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}

        cfg = ttk.LabelFrame(self, text="Settings")
        cfg.pack(fill="x", **pad)

        # [glyphs]
        glyphs_frame = ttk.LabelFrame(cfg, text="Glyphs")
        glyphs_frame.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        self._add_spin(glyphs_frame, "Height", self.height, 1, 4096, 0, 0)
        self._add_spin(glyphs_frame, "Scale %", self.scale, 10, 400, 1, 0)
        self._add_spin(glyphs_frame, "Y offset", self.y_offset, -2048, 2048, 2, 0)
        self._add_spin(glyphs_frame, "Outline px", self.outline, 0, 256, 0, 1)
        self._add_spin(glyphs_frame, "Bold px", self.bold, 0, 256, 1, 1)
        ttk.Label(glyphs_frame, text="Main color").grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.main_color_swatch = tk.Label(
            glyphs_frame, width=10, bg=self.main_color.get(), relief="sunken", cursor="hand2"
        )
        self.main_color_swatch.grid(row=2, column=1, sticky="w", padx=6, pady=4)
        self.main_color_swatch.bind("<Button-1>", lambda _e: self._pick_main_color())
        ttk.Label(glyphs_frame, text="Outline color").grid(row=2, column=2, sticky="w", padx=6, pady=4)
        self.outline_color_swatch = tk.Label(
            glyphs_frame, width=10, bg=self.outline_color.get(), relief="sunken", cursor="hand2"
        )
        self.outline_color_swatch.grid(row=2, column=3, sticky="w", padx=6, pady=4)
        self.outline_color_swatch.bind("<Button-1>", lambda _e: self._pick_outline_color())
        self._add_spin(glyphs_frame, "Tracking", self.tracking, 0, 256, 2, 1)

        # [image]
        image_frame = ttk.LabelFrame(cfg, text="Image")
        image_frame.grid(row=1, column=0, sticky="ew", padx=6, pady=0)
        self._add_spin(image_frame, "Atlas width", self.atlas_width, 32, 8192, 0, 0)
        ttk.Checkbutton(
            image_frame,
            text="Force power of two height",
            variable=self.force_pow2_height,
        ).grid(row=0, column=2, columnspan=2, sticky="w", padx=6, pady=4)
        self._add_spin(image_frame, "Glyph gap", self.glyph_gap, 0, 256, 2, 0)

        ttk.Label(image_frame, text="Compression").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.dds_compression_box = ttk.Combobox(
            image_frame,
            textvariable=self.dds_compression,
            values=["DXT5", "Uncompressed"],
            state="readonly",
            width=12,
        )
        self.dds_compression_box.grid(row=1, column=1, sticky="w", padx=6, pady=4)
        ttk.Checkbutton(
            image_frame,
            text="Mip maps",
            variable=self.generate_mipmaps,
        ).grid(row=1, column=2, columnspan=2, sticky="w", padx=6, pady=4)

        cfg.columnconfigure(0, weight=1)
        glyphs_frame.columnconfigure(1, weight=1)
        glyphs_frame.columnconfigure(3, weight=1)
        glyphs_frame.columnconfigure(5, weight=1)
        image_frame.columnconfigure(1, weight=1)
        image_frame.columnconfigure(3, weight=1)
        image_frame.columnconfigure(5, weight=1)

        self.ttf_frame = ttk.LabelFrame(self, text="Files")
        self.ttf_frame.pack(fill="x", **pad)

        self._add_path_row(self.ttf_frame, "TTF input", self.ttf_path, self._browse_ttf, 0)
        self._add_path_row(self.ttf_frame, "JSON output", self.out_json_path, self._browse_json_out, 1)

        ttk.Label(self.ttf_frame, text="Characters to export").grid(row=3, column=0, sticky="nw", padx=6, pady=4)
        self.char_text = tk.Text(self.ttf_frame, height=10, wrap="word")
        self.char_text.grid(row=3, column=1, columnspan=2, sticky="nsew", padx=6, pady=4)
        self.char_text.insert("1.0", "".join(chr(i) for i in range(32, 127)))
        self.ttf_frame.columnconfigure(1, weight=1)
        self.ttf_frame.columnconfigure(2, weight=1)
        self.ttf_frame.rowconfigure(3, weight=1)

        ttk.Button(self.ttf_frame, text="Import from json", command=self._import_chars_from_json).grid(
            row=4, column=1, sticky="w", padx=6, pady=(0, 4)
        )

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

    def _import_chars_from_json(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not p:
            return
        try:
            chars = chars_from_custom_json_file(Path(p))
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc))
            return

        self.char_text.delete("1.0", "end")
        self.char_text.insert("1.0", "".join(chars))
        self.log_line(f"Imported {len(chars)} characters from {Path(p).name}")

    def _browse_ttf(self) -> None:
        p = filedialog.askopenfilename(filetypes=[("TrueType font", "*.ttf *.otf"), ("All files", "*.*")])
        if p:
            self.ttf_path.set(p)
            if not self.out_json_path.get().strip():
                self.out_json_path.set(str(Path(p).with_suffix(".json")))

    def _browse_json_out(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if p:
            self.out_json_path.set(p)

    def log_line(self, text: str) -> None:
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.update_idletasks()

    def convert(self) -> None:
        try:
            font_path = Path(self.ttf_path.get().strip())
            json_text = self.out_json_path.get().strip()
            json_out = Path(json_text) if json_text else font_path.with_suffix(".json")
            dds_out = json_out.with_suffix(".dds")

            if not font_path.is_file():
                raise FileNotFoundError("Choose a valid TTF/OTF input file.")
            if not self.char_text.get("1.0", "end").strip():
                raise ValueError("Enter or paste the characters to export.")
            if not self.out_json_path.get().strip():
                raise FileNotFoundError("Choose a JSON output file.")

            main_rgb = _hex_to_rgb(self.main_color.get())
            outline_rgb = _hex_to_rgb(self.outline_color.get())
            background_rgb = main_rgb if int(self.outline.get()) == 0 else outline_rgb

            chars_text = self.char_text.get("1.0", "end")
            chars = normalize_chars(chars_text)
            if not chars:
                raise ValueError("Enter or paste the characters to export.")

            unsupported = unsupported_chars_for_font(font_path, chars)
            if unsupported:
                preview = "".join(unsupported[:20])
                more = "" if len(unsupported) <= 20 else f" ... (+{len(unsupported) - 20} more)"
                msg = (
                    "Some characters are not supported by this font:\n\n"
                    f"{preview}{more}\n\n"
                    "Do you want to skip these characters?\n"
                    "Yes = skip unsupported characters\n"
                    "No = try anyway"
                )
                if messagebox.askyesno("Unsupported characters", msg):
                    chars = [ch for ch in chars if ch not in unsupported]
                    if not chars:
                        raise ValueError("After skipping unsupported characters, nothing is left to export.")
                    chars_text = "".join(chars)
                # else: keep original chars_text and try anyway

            self.log_line("Rendering glyphs...")
            convert_ttf_to_custom(
                font_path=font_path,
                charset_text=chars_text,
                height=int(self.height.get()),
                outline_px=int(self.outline.get()),
                bold_px=int(self.bold.get()),
                scale_pct=int(self.scale.get()),
                tracking=int(self.tracking.get()),
                y_offset=int(self.y_offset.get()),
                atlas_width=int(self.atlas_width.get()),
                glyph_gap=int(self.glyph_gap.get()),
                force_pow2_height=bool(self.force_pow2_height.get()),
                main_rgb=main_rgb,
                outline_rgb=outline_rgb,
                background_rgb=background_rgb,
                dds_compression=self.dds_compression.get(),
                generate_mipmaps=bool(self.generate_mipmaps.get()),
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

#!/usr/bin/env python3
"""
Merge tool for the custom JSON + DDS font system.

What it does:
- Loads two or more existing font atlases
- Reads each source's JSON + DDS
- Uses the largest source height as the merged global height
- Keeps each glyph aligned to the same 4x4 phase as its source DDS
- Copies DXT5 blocks directly from the source DDS files into the output DDS
- Gives every glyph a full tallest-height cell, centered by nearest-4px shift
- Adds at least 4 pixels of spacing between packed glyph cells
- Preserves any extra per-glyph JSON attributes and writes one merged DXT5 DDS

Supported JSON input:
- {"global_height": 60, "chars": [...]}
- {"chars": [...]}
- a bare list of glyph entries

Each glyph entry needs at least:
- char
- x
- y
- width

Extra keys are preserved on output.

Dependencies:
    pip install pillow
"""

from __future__ import annotations

import io
import json
import math
import struct
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image


DDS_MAGIC = b"DDS "
DDS_HEADER_SIZE = 124
DDS_PIXELFORMAT_SIZE = 32
DDS_DXT5_FOURCC = b"DXT5"


def round_up(n: int, multiple: int) -> int:
    if multiple <= 1:
        return int(n)
    return ((int(n) + multiple - 1) // multiple) * multiple


def next_pow2(n: int) -> int:
    n = max(1, int(n))
    return 1 if n <= 1 else 1 << (n - 1).bit_length()


def nearest_shift_4(delta_px: int) -> int:
    """Round to the nearest multiple of 4, with ties rounding up."""
    delta_px = max(0, int(delta_px))
    return ((delta_px + 2) // 4) * 4


def load_json_root(json_path: Path) -> Any:
    return json.loads(json_path.read_text(encoding="utf-8"))


def extract_entries_and_height(root: Any) -> tuple[list[dict[str, Any]], int | None]:
    declared_height: int | None = None
    raw_entries: list[dict[str, Any]] | None = None

    if isinstance(root, dict):
        gh = root.get("global_height")
        if isinstance(gh, (int, float)):
            declared_height = int(gh)

        for key in ("chars", "glyphs", "entries", "items"):
            val = root.get(key)
            if isinstance(val, list):
                raw_entries = [x for x in val if isinstance(x, dict)]
                if raw_entries:
                    break
    elif isinstance(root, list):
        raw_entries = [x for x in root if isinstance(x, dict)]

    if not raw_entries:
        raise ValueError("No glyph entries found in the JSON.")

    entries: list[dict[str, Any]] = []
    for item in raw_entries:
        if "char" not in item or "x" not in item or "y" not in item or "width" not in item:
            continue

        # Keep all extra attributes exactly as they came in.
        entry = dict(item)
        entry["char"] = str(entry["char"])
        if not entry["char"]:
            continue
        entry["x"] = int(entry["x"])
        entry["y"] = int(entry["y"])
        entry["width"] = max(1, int(entry["width"]))
        entries.append(entry)

    if not entries:
        raise ValueError("No usable glyph entries were found in the JSON.")

    return entries, declared_height


@dataclass
class Dxt5Atlas:
    width: int
    height: int
    blocks: bytes  # raw 16-byte DXT5 blocks

    @property
    def blocks_x(self) -> int:
        return (self.width + 3) // 4

    @property
    def blocks_y(self) -> int:
        return (self.height + 3) // 4


def build_dds_header(width: int, height: int) -> bytes:
    """Generate a standards-compliant DXT5 DDS header with Pillow."""
    dummy = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
    bio = io.BytesIO()
    dummy.save(bio, format="DDS", pixel_format="DXT5")
    data = bio.getvalue()
    if len(data) < 128 or data[:4] != DDS_MAGIC:
        raise ValueError("Failed to generate DDS header.")
    return data[:128]


def load_dxt5_atlas(dds_path: Path) -> Dxt5Atlas:
    data = dds_path.read_bytes()
    if len(data) < 128 or data[:4] != DDS_MAGIC:
        raise ValueError(f"{dds_path.name} is not a DDS file.")

    header = data[4:128]
    size, flags, height, width, linear_size, depth, mipmaps = struct.unpack("<7I", header[:28])
    if size != DDS_HEADER_SIZE:
        raise ValueError(f"Unsupported DDS header size in {dds_path.name}.")

    pf_off = 72
    pf_size, pf_flags = struct.unpack("<II", header[pf_off:pf_off + 8])
    fourcc = header[pf_off + 8:pf_off + 12]
    if pf_size != DDS_PIXELFORMAT_SIZE or fourcc != DDS_DXT5_FOURCC:
        raise ValueError(f"{dds_path.name} is not a DXT5 DDS file.")

    blocks = data[128:]
    expected = ((width + 3) // 4) * ((height + 3) // 4) * 16
    if len(blocks) < expected:
        raise ValueError(f"{dds_path.name} is truncated.")
    if len(blocks) > expected:
        blocks = blocks[:expected]

    return Dxt5Atlas(width=int(width), height=int(height), blocks=blocks)


def dxt5_block_index(atlas: Dxt5Atlas, block_x: int, block_y: int) -> int:
    return (block_y * atlas.blocks_x + block_x) * 16


def copy_dxt5_rect(
    src: Dxt5Atlas,
    dst_blocks: bytearray,
    dst_width: int,
    src_x: int,
    src_y: int,
    rect_w: int,
    rect_h: int,
    dst_x: int,
    dst_y: int,
) -> None:
    if rect_w % 4 != 0 or rect_h % 4 != 0 or src_x % 4 != 0 or src_y % 4 != 0 or dst_x % 4 != 0 or dst_y % 4 != 0:
        raise ValueError("DXT5 block copy requires 4-pixel aligned coordinates and sizes.")

    dst_blocks_x = (dst_width + 3) // 4
    src_block_x = src_x // 4
    src_block_y = src_y // 4
    dst_block_x = dst_x // 4
    dst_block_y = dst_y // 4
    blocks_w = rect_w // 4
    blocks_h = rect_h // 4

    for by in range(blocks_h):
        for bx in range(blocks_w):
            s_idx = dxt5_block_index(src, src_block_x + bx, src_block_y + by)
            d_idx = (dst_block_y + by) * dst_blocks_x * 16 + (dst_block_x + bx) * 16
            dst_blocks[d_idx:d_idx + 16] = src.blocks[s_idx:s_idx + 16]


def write_dxt5_dds(path: Path, width: int, height: int, blocks: bytes) -> None:
    width = int(width)
    height = int(height)
    header = build_dds_header(width, height)
    path.write_bytes(header + blocks)


@dataclass
class GlyphPlacement:
    char: str
    src: Dxt5Atlas
    src_x: int
    src_y: int
    rect_w: int
    rect_h: int
    cell_h: int
    width: int
    extra_attrs: dict[str, Any]
    glyph_off_x: int = 0
    glyph_off_y: int = 0
    source_shift_y: int = 0
    x: int = 0
    y: int = 0


@dataclass
class FontSource:
    json_path: Path
    dds_path: Path
    height: int
    entries: list[dict[str, Any]]
    atlas: Dxt5Atlas

    @property
    def label(self) -> str:
        return self.json_path.stem


def load_source(json_path: Path, dds_path: Path, height_hint: int | None = None) -> FontSource:
    root = load_json_root(json_path)
    entries, declared_height = extract_entries_and_height(root)
    height = declared_height if declared_height is not None else height_hint
    if height is None:
        raise ValueError(f"{json_path.name} does not declare global_height. Please enter a source height.")
    if height <= 0:
        raise ValueError(f"Invalid source height for {json_path.name}: {height}")

    atlas = load_dxt5_atlas(dds_path)
    return FontSource(json_path=json_path, dds_path=dds_path, height=int(height), entries=entries, atlas=atlas)


def build_glyphs(sources: list[FontSource], output_height: int) -> list[GlyphPlacement]:
    glyphs: list[GlyphPlacement] = []
    seen: set[str] = set()

    cell_h = round_up(output_height, 4)

    for src in sources:
        # Shift each source by the closest 4px multiple toward centered.
        source_shift_y = nearest_shift_4((output_height - src.height) // 2)

        for entry in src.entries:
            ch = str(entry["char"])
            if ch in seen:
                continue
            seen.add(ch)

            orig_x = int(entry["x"])
            orig_y = int(entry["y"])

            src_x = orig_x & ~3
            src_y = orig_y & ~3
            right = round_up(orig_x + int(entry["width"]), 4)
            bottom = round_up(orig_y + src.height, 4)
            rect_w = max(4, right - src_x)
            rect_h = max(4, bottom - src_y)

            if src_x < 0 or src_y < 0 or src_x + rect_w > src.atlas.width or src_y + rect_h > src.atlas.height:
                raise ValueError(f"Glyph {ch!r} is outside the DDS bounds in {src.json_path.name}")

            glyphs.append(
                GlyphPlacement(
                    char=ch,
                    src=src.atlas,
                    src_x=src_x,
                    src_y=src_y,
                    rect_w=rect_w,
                    rect_h=rect_h,
                    cell_h=cell_h,
                    width=int(entry["width"]),
                    extra_attrs={k: v for k, v in entry.items() if k not in {"char", "x", "y", "width"}},
                    glyph_off_x=orig_x - src_x,
                    glyph_off_y=orig_y - src_y,
                    source_shift_y=source_shift_y,
                )
            )

    glyphs.sort(key=lambda g: (ord(g.char[0]) if g.char else 0, g.char))
    return glyphs


def pack_glyphs(
    glyphs: list[GlyphPlacement],
    atlas_width: int,
    row_gap: int = 4,
    force_pow2: bool = False,
) -> tuple[int, int, list[GlyphPlacement]]:
    if not glyphs:
        raise ValueError("No glyphs to pack.")

    atlas_width = max(4, round_up(int(atlas_width), 4))
    atlas_width = max(atlas_width, max(g.rect_w for g in glyphs))
    if force_pow2:
        atlas_width = next_pow2(atlas_width)

    x = 0
    y = 0
    row_h = 0
    packed: list[GlyphPlacement] = []

    for g in glyphs:
        cell_h = max(g.cell_h, g.rect_h + g.source_shift_y)
        if x > 0 and x + g.rect_w > atlas_width:
            y += row_h + row_gap
            x = 0
            row_h = 0

        packed.append(
            GlyphPlacement(
                char=g.char,
                src=g.src,
                src_x=g.src_x,
                src_y=g.src_y,
                rect_w=g.rect_w,
                rect_h=g.rect_h,
                cell_h=g.cell_h,
                width=g.width,
                extra_attrs=dict(g.extra_attrs),
                glyph_off_x=g.glyph_off_x,
                glyph_off_y=g.glyph_off_y,
                source_shift_y=g.source_shift_y,
                x=x,
                y=y,
            )
        )
        x += g.rect_w + row_gap
        row_h = max(row_h, cell_h)

    atlas_h = y + row_h
    atlas_h = round_up(atlas_h, 4)
    if force_pow2:
        atlas_h = next_pow2(atlas_h)

    return atlas_width, atlas_h, packed


def merge_sources(sources: list[FontSource], atlas_width: int, force_pow2: bool = False) -> tuple[bytes, dict[str, Any], int, int]:
    if len(sources) < 2:
        raise ValueError("Add at least two fonts before merging.")

    output_height = max(src.height for src in sources)
    glyphs = build_glyphs(sources, output_height)
    atlas_w, atlas_h, packed = pack_glyphs(glyphs, atlas_width=atlas_width, row_gap=4, force_pow2=force_pow2)

    dst_blocks = bytearray((atlas_w // 4) * (atlas_h // 4) * 16)

    for g in packed:
        # Draw the source glyph inside a full-height cell; the cell itself is aligned
        # to the tallest global height, while the visible source blocks are shifted
        # by a nearest-4 centered amount.
        dst_y = g.y + g.source_shift_y
        copy_dxt5_rect(
            src=g.src,
            dst_blocks=dst_blocks,
            dst_width=atlas_w,
            src_x=g.src_x,
            src_y=g.src_y,
            rect_w=g.rect_w,
            rect_h=g.rect_h,
            dst_x=g.x,
            dst_y=dst_y,
        )

    placements = []
    for g in packed:
        item = dict(g.extra_attrs)
        item.update({
            "char": g.char,
            "x": g.x + g.glyph_off_x,
            "y": g.y,
            "width": g.width,
        })
        placements.append(item)
    out_json = {"global_height": output_height, "chars": placements}
    return bytes(dst_blocks), out_json, atlas_w, atlas_h


class MergeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Custom Font Merge Tool")
        self.geometry("1100x720")

        self.sources: list[FontSource] = []
        self.output_json = tk.StringVar()
        self.output_dds = tk.StringVar()
        self.atlas_width = tk.IntVar(value=1024)
        self.force_pow2 = tk.BooleanVar(value=True)

        self._build_ui()

    def _build_ui(self) -> None:
        pad = {"padx": 6, "pady": 4}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)

        ttk.Label(top, text="Output width").pack(side="left")
        ttk.Spinbox(top, from_=64, to=16384, textvariable=self.atlas_width, width=10).pack(side="left", padx=6)
        ttk.Checkbutton(top, text="Power-of-2 width/height", variable=self.force_pow2).pack(side="left", padx=10)

        ttk.Button(top, text="Add font pair", command=self.add_source).pack(side="left", padx=6)
        ttk.Button(top, text="Remove selected", command=self.remove_selected).pack(side="left", padx=6)
        ttk.Button(top, text="Move up", command=lambda: self.move_selected(-1)).pack(side="left", padx=6)
        ttk.Button(top, text="Move down", command=lambda: self.move_selected(1)).pack(side="left", padx=6)
        ttk.Button(top, text="Set height", command=self.set_selected_height).pack(side="left", padx=6)

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, **pad)

        cols = ("height", "json", "dds")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("height", text="Height")
        self.tree.heading("json", text="JSON")
        self.tree.heading("dds", text="DDS")
        self.tree.column("height", width=90, anchor="center", stretch=False)
        self.tree.column("json", width=420, stretch=True)
        self.tree.column("dds", width=420, stretch=True)
        self.tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)

        out = ttk.LabelFrame(self, text="Output")
        out.pack(fill="x", **pad)

        ttk.Label(out, text="Merged JSON").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(out, textvariable=self.output_json).grid(row=0, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(out, text="Browse", command=self.browse_output_json).grid(row=0, column=2, padx=6, pady=4)

        ttk.Label(out, text="Merged DDS").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(out, textvariable=self.output_dds).grid(row=1, column=1, sticky="ew", padx=6, pady=4)
        ttk.Button(out, text="Browse", command=self.browse_output_dds).grid(row=1, column=2, padx=6, pady=4)
        out.columnconfigure(1, weight=1)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", **pad)
        ttk.Button(bottom, text="Merge now", command=self.merge_now).pack(side="left")
        ttk.Button(bottom, text="Clear log", command=lambda: self.log.delete("1.0", "end")).pack(side="left", padx=6)

        logf = ttk.LabelFrame(self, text="Log")
        logf.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(logf, height=10, wrap="word")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    def log_line(self, text: str) -> None:
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.update_idletasks()

    def refresh_tree(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for idx, src in enumerate(self.sources):
            self.tree.insert("", "end", iid=str(idx), values=(src.height, str(src.json_path), str(src.dds_path)))

    def selected_index(self) -> int | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return int(sel[0])

    def add_source(self) -> None:
        json_path = filedialog.askopenfilename(title="Select font JSON", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not json_path:
            return

        jp = Path(json_path)
        dds_path = filedialog.askopenfilename(
            title="Select matching DDS",
            initialdir=str(jp.parent),
            initialfile=jp.with_suffix(".dds").name,
            filetypes=[("DDS", "*.dds"), ("All files", "*.*")],
        )
        if not dds_path:
            return

        try:
            root = load_json_root(jp)
            _, declared_height = extract_entries_and_height(root)
            height = declared_height
            if height is None:
                height = simpledialog.askinteger(
                    "Source height",
                    f"Enter the global height for {jp.name}:",
                    parent=self,
                    minvalue=1,
                    maxvalue=4096,
                )
                if height is None:
                    return

            src = load_source(jp, Path(dds_path), height_hint=int(height))
            self.sources.append(src)
            self.refresh_tree()
            self.log_line(f"Added {jp.name} ({src.height}px).")
        except Exception as exc:
            messagebox.showerror("Add source failed", str(exc))

    def remove_selected(self) -> None:
        idx = self.selected_index()
        if idx is None:
            return
        removed = self.sources.pop(idx)
        self.refresh_tree()
        self.log_line(f"Removed {removed.json_path.name}.")

    def move_selected(self, delta: int) -> None:
        idx = self.selected_index()
        if idx is None:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self.sources):
            return
        self.sources[idx], self.sources[new_idx] = self.sources[new_idx], self.sources[idx]
        self.refresh_tree()
        self.tree.selection_set(str(new_idx))

    def set_selected_height(self) -> None:
        idx = self.selected_index()
        if idx is None:
            return
        src = self.sources[idx]
        value = simpledialog.askinteger(
            "Set height",
            f"Height for {src.json_path.name}:",
            parent=self,
            initialvalue=src.height,
            minvalue=1,
            maxvalue=4096,
        )
        if value is None:
            return
        src.height = int(value)
        self.refresh_tree()
        self.log_line(f"Updated {src.json_path.name} height to {src.height}px.")

    def browse_output_json(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if p:
            self.output_json.set(p)

    def browse_output_dds(self) -> None:
        initial_name = "output.dds"
        json_current = self.output_json.get().strip()
        if json_current:
            initial_name = Path(json_current).with_suffix(".dds").name
        p = filedialog.asksaveasfilename(
            defaultextension=".dds",
            initialfile=initial_name,
            filetypes=[("DDS", "*.dds"), ("All files", "*.*")],
        )
        if p:
            self.output_dds.set(p)

    def merge_now(self) -> None:
        try:
            if len(self.sources) < 2:
                raise ValueError("Add at least two font sources first.")

            json_out_str = self.output_json.get().strip()
            dds_out_str = self.output_dds.get().strip()
            if not json_out_str:
                raise ValueError("Choose an output JSON path.")
            if not dds_out_str:
                raise ValueError("Choose an output DDS path.")

            json_out = Path(json_out_str)
            dds_out = Path(dds_out_str)

            self.log_line("Merging sources...")
            blocks, merged_json, atlas_w, atlas_h = merge_sources(
                self.sources,
                atlas_width=int(self.atlas_width.get()),
                force_pow2=bool(self.force_pow2.get()),
            )

            json_out.parent.mkdir(parents=True, exist_ok=True)
            dds_out.parent.mkdir(parents=True, exist_ok=True)
            json_out.write_text(json.dumps(merged_json, ensure_ascii=False, indent=2), encoding="utf-8")
            write_dxt5_dds(dds_out, atlas_w, atlas_h, blocks)

            self.log_line(f"Output global height: {merged_json['global_height']}")
            self.log_line(f"Saved JSON: {json_out}")
            self.log_line(f"Saved DDS:  {dds_out}")
            messagebox.showinfo("Done", "Merge finished successfully.")
        except Exception as exc:
            self.log_line("ERROR:")
            self.log_line(str(exc))
            self.log_line(traceback.format_exc())
            messagebox.showerror("Merge failed", str(exc))


def main() -> None:
    app = MergeApp()
    app.mainloop()


if __name__ == "__main__":
    main()

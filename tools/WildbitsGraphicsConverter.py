#!/usr/bin/env python3
"""
Wildbits Graphics Converter
-----------------------
A small windowed tool for Wildbits retro-computer developers (fmr. known as
Foenix) that turns an indexed-color PNG image into the raw palette (.pal)
and bitmap (.bin) files those machines expect -- or, in K2 Mini-LCD mode,
into an R5G6B5 binary for the K2 case's tiny embedded screen.

Three mutually-exclusive output modes (only that mode's files get written):

  Bitmap mode
    Forces the .bin to a fixed screen resolution (320x240 or 320x200),
    no matter what size the source PNG is. Smaller images are padded with
    zero bytes; larger images are cropped (and you'll get a warning).
    Drag the orange rectangle in the preview to choose where the source
    image sits within that fixed frame (free dragging, any direction).

  Tileset mode
    Produces a tile set laid out the way tileDefineTileSet expects, per
    the Wildbits reference manual. Pick a tile size (8x8 or 16x16), then
    choose an arrangement:
      - Square (checked): tiles laid out left-to-right, top-to-bottom in
        a 16x16 tile grid -> a 128x128 (8x8 tiles) or 256x256 (16x16
        tiles) bitmap. Drag the orange rectangle to choose where the
        source image sits within that grid -- free dragging by default,
        or snapped to tile boundaries if "Snap offset to tile grid" is
        checked. You're responsible for keeping your source PNG within
        that pixel width/height; it's cropped with no warning if bigger,
        zero-padded if smaller.
      - Linear / vertical strip (unchecked, default): tiles are stacked
        one tile wide, 256 tiles tall (8x2048 px for 8x8 tiles, 16x4096
        px for 16x16 tiles) -- tile 0's rows first, then tile 1's, and so
        on. Extra source tiles beyond 256 are dropped; missing ones are
        zero-padded. (No draggable offset in this arrangement.)

  K2 Mini-LCD mode
    Exports only an R5G6B5 binary for the K2 case's mini-LCD, forced to
    its native 240x320 buffer. The physical screen only shows a centered
    240x280 window of that buffer (there's a bezel border top and
    bottom), shown as a cyan guide in the preview. Drag freely in the
    preview to choose how your image sits within the 240x320 buffer.

Just drag a PNG onto the window (or use "Browse for Image..."), pick your
mode, and hit Convert. The window always shows exactly where the output
files will be written, with a button to change that folder.

Requirements:
    pip install pillow
    pip install tkinterdnd2   (optional, enables drag-and-drop)

If tkinterdnd2 isn't installed, the app still works fine — you just use
the Browse button instead of dragging a file in.
"""

import math
import os
import struct
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageDraw, ImageTk

# Drag-and-drop support is optional. Fall back gracefully if unavailable.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False


# K2 mini-LCD physical buffer vs. visible (bezel-cut) area
LCD_WIDTH = 240
LCD_HEIGHT = 320
LCD_VISIBLE_HEIGHT = 280
LCD_VISIBLE_Y_INSET = (LCD_HEIGHT - LCD_VISIBLE_HEIGHT) // 2  # 20px top & bottom


# ----------------------------------------------------------------------
# Conversion logic
# ----------------------------------------------------------------------

def _open_indexed_image(image_path):
    """Open a PNG and make sure it's indexed color (P mode)."""
    img = Image.open(image_path)
    if img.mode != 'P':
        raise ValueError(
            "Image must be in indexed color (P mode). "
            "Re-save it as a PNG with a palette (e.g. 'Indexed Color' in "
            "Photoshop/GIMP, or Image > Mode > Indexed)."
        )
    return img


def write_palette_file(img, palette_file, log=print):
    """Write the image's palette as BGRA bytes (Wildbits/Foenix format)."""
    palette = img.getpalette() or []
    rgba_palette = []
    for i in range(0, len(palette), 3):
        red = palette[i]
        green = palette[i + 1]
        blue = palette[i + 2]
        alpha = 255
        rgba_palette.append((blue, green, red, alpha))

    with open(palette_file, 'wb') as palette_f:
        palette_bytes = b''.join(struct.pack('BBBB', *c) for c in rgba_palette)
        palette_f.write(palette_bytes)
    log(f"Wrote palette ({len(rgba_palette)} colors) -> {palette_file}")


def build_offset_canvas_indexed(img, target_w, target_h, offset_x, offset_y):
    """Build a P-mode canvas of size target_w x target_h, where canvas
    pixel (x, y) comes from source pixel (x + offset_x, y + offset_y) --
    i.e. offset is the source-image coordinate that lands at the canvas's
    top-left corner, matching the crop-window rectangle drawn in the
    preview. Anything not covered by the source image (offset pushes the
    window past an edge) stays palette index 0. Returns (raw index bytes
    as bytearray, was_cropped)."""
    canvas = Image.new("P", (target_w, target_h), color=0)
    canvas.putpalette(img.getpalette() or [0] * 768)
    canvas.paste(img, (round(-offset_x), round(-offset_y)))

    img_w, img_h = img.size
    was_cropped = (offset_x > 0 or offset_y > 0 or
                   offset_x + target_w < img_w or offset_y + target_h < img_h)
    return bytearray(canvas.tobytes()), was_cropped


def save_bitmap_mode(image_path, palette_file, bitmap_file, target_w, target_h,
                      offset_x=0, offset_y=0, log=print):
    """Bitmap mode: force the .bin to an exact target resolution."""
    img = _open_indexed_image(image_path)
    write_palette_file(img, palette_file, log=log)

    canvas_bytes, cropped = build_offset_canvas_indexed(img, target_w, target_h, offset_x, offset_y)
    with open(bitmap_file, 'wb') as f:
        f.write(canvas_bytes)

    fit_desc = "source was cropped to fit" if cropped else "source fit with zero-padding as needed"
    log(f"Wrote bitmap ({target_w}x{target_h} forced resolution, offset {offset_x},{offset_y}, "
        f"{fit_desc}) -> {bitmap_file}")
    return cropped


def save_tileset_square(image_path, palette_file, bitmap_file, tile_size,
                         offset_x=0, offset_y=0, log=print):
    """Tileset mode, square arrangement: tiles laid out left-to-right,
    top-to-bottom in a 16x16 tile grid -- i.e. a flat 128x128 or 256x256
    bitmap (same raster layout as bitmap mode, just a different forced
    size). No crop warning: per the Wildbits manual, staying within the
    grid is the user's responsibility here."""
    target = 128 if tile_size == 8 else 256

    img = _open_indexed_image(image_path)
    write_palette_file(img, palette_file, log=log)

    canvas_bytes, cropped = build_offset_canvas_indexed(img, target, target, offset_x, offset_y)
    with open(bitmap_file, 'wb') as f:
        f.write(canvas_bytes)

    note = " (source PNG didn't fully fit the working area and was cropped)" if cropped else ""
    log(f"Wrote square tileset bin ({target}x{target}px, {tile_size}x{tile_size} tiles, "
        f"16x16 tile grid, offset {offset_x},{offset_y}){note} -> {bitmap_file}")


def save_tileset_linear(image_path, palette_file, bitmap_file, tile_size, log=print):
    """Tileset mode, linear/vertical-strip arrangement: tiles stacked one
    tile wide, 256 tiles tall. Tile 0's rows come first, then tile 1's,
    and so on, reading source tiles left-to-right/top-to-bottom. No
    draggable offset -- every source tile is used, in reading order."""
    MAX_TILES = 256

    img = _open_indexed_image(image_path)
    write_palette_file(img, palette_file, log=log)

    img_w, img_h = img.size
    pixel_data = list(img.getdata())

    cols = max(1, math.ceil(img_w / tile_size))
    rows = max(1, math.ceil(img_h / tile_size))
    tile_positions = [(c * tile_size, r * tile_size) for r in range(rows) for c in range(cols)]

    used_positions = tile_positions[:MAX_TILES]
    dropped = max(0, len(tile_positions) - MAX_TILES)
    padded = MAX_TILES - len(used_positions)

    strip_h = MAX_TILES * tile_size
    canvas = bytearray(tile_size * strip_h)  # zero-filled

    for idx, (start_x, start_y) in enumerate(used_positions):
        copy_w = max(0, min(tile_size, img_w - start_x))
        if copy_w == 0:
            continue
        for ty in range(tile_size):
            src_y = start_y + ty
            if src_y >= img_h:
                continue  # leave this tile row zero-padded
            src_start = src_y * img_w + start_x
            dst_start = (idx * tile_size + ty) * tile_size
            canvas[dst_start:dst_start + copy_w] = bytes(pixel_data[src_start:src_start + copy_w])

    with open(bitmap_file, 'wb') as f:
        f.write(canvas)

    extra = []
    if padded:
        extra.append(f"{padded} zero-padded empty tile(s) to reach 256")
    if dropped:
        extra.append(f"{dropped} extra source tile(s) beyond 256 were dropped")
    extra_desc = f" ({', '.join(extra)})" if extra else ""
    log(f"Wrote linear tileset bin ({tile_size}x{strip_h}px, {tile_size}x{tile_size} tiles): "
        f"{len(used_positions)} tile(s) from source{extra_desc} -> {bitmap_file}")


def save_lcd_mode(image_path, output_bin, offset_x=0, offset_y=0, log=print):
    """K2 Mini-LCD mode: force the source PNG onto the LCD's native
    240x320 R5G6B5 buffer. (offset_x, offset_y) is the source-image
    coordinate that lands at the buffer's top-left corner, matching the
    crop-window rectangle drawn in the preview."""
    img = Image.open(image_path)
    if img.mode != 'RGB':
        img = img.convert('RGB')

    canvas = Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT), color=(0, 0, 0))
    canvas.paste(img, (round(-offset_x), round(-offset_y-20)))

    pixels = canvas.load()
    binary_data = bytearray()
    for y in range(LCD_HEIGHT):
        for x in range(LCD_WIDTH):
            red, green, blue = pixels[x, y]
            r5 = (red >> 3) & 0x1F     # 5-bit red
            g6 = (green >> 2) & 0x3F   # 6-bit green
            b5 = (blue >> 3) & 0x1F    # 5-bit blue

            rgb565 = (r5 << 11) | (g6 << 5) | b5

            binary_data.append(rgb565 & 0xFF)         # low byte
            binary_data.append((rgb565 >> 8) & 0xFF)  # high byte

    with open(output_bin, 'wb') as bin_file:
        bin_file.write(binary_data)

    log(f"Wrote R5G6B5 mini-LCD binary ({LCD_WIDTH}x{LCD_HEIGHT}, vertical offset {offset_y}, "
        f"{len(binary_data)} bytes) -> {output_bin}")


# ----------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------

BASE_CLASS = TkinterDnD.Tk if DND_AVAILABLE else tk.Tk


class WildbitsTileConverterApp(BASE_CLASS):
    def __init__(self):
        super().__init__()

        self.title("Wildbits Graphics Converter")
        self.geometry("1180x720")
        self.minsize(980, 620)

        self.image_path = tk.StringVar(value="")
        self.out_name = tk.StringVar(value="")
        self.output_dir = tk.StringVar(value=os.path.expanduser("~"))

        # Output mode ("bitmap", "tileset", or "lcd") and their options
        self.mode = tk.StringVar(value="tileset")
        self.bitmap_resolution = tk.StringVar(value="320x240")
        self.tile_size_choice = tk.StringVar(value="16")   # "8" or "16"
        self.square_layout = tk.BooleanVar(value=False)    # unchecked = linear vertical strip
        self.snap_to_tile_grid = tk.BooleanVar(value=False)  # square mode only

        self.preview_photo = None  # keep a reference so Tk doesn't garbage-collect it
        self.base_image = None     # full-resolution RGBA source image for the preview
        self.orig_w = 0
        self.orig_h = 0
        self.zoom = 1.0            # 1.0 == 100% == native pixel size

        # Draggable crop/pad offset, in source-image pixel units. The
        # orange rectangle's top-left corner sits at this offset.
        self.offset_x = 0.0
        self.offset_y = 0.0
        self._drag_start = None   # (mouse_x, mouse_y, offset_x0, offset_y0) while dragging

        self._build_widgets()
        self._update_tileset_dims_label()
        self._on_mode_change()

        # Redraw the preview overlay live as the user changes mode/settings
        self.tile_size_choice.trace_add("write", lambda *a: self._on_target_dims_changed())
        self.square_layout.trace_add("write", lambda *a: self._on_target_dims_changed())
        self.bitmap_resolution.trace_add("write", lambda *a: self._on_target_dims_changed())
        self.snap_to_tile_grid.trace_add("write", lambda *a: self._render_preview())

    # -- UI construction --------------------------------------------------

    def _build_widgets(self):
        # --- Header (spans full width, kept short so it doesn't eat vertical space) ---
        header = ttk.Frame(self)
        header.pack(fill="x", padx=14, pady=(10, 6))
        ttk.Label(
            header, text="Wildbits Graphics Converter",
            font=("TkDefaultFont", 15, "bold")
        ).pack(side="left")
        ttk.Label(
            header,
            text="   For Wildbits family machines (Jr2/K2) — indexed PNG -> .pal + .bin",
            foreground="#555"
        ).pack(side="left")

        # --- Main body: left = image/preview, right = controls, side by side ---
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")

        # ============================== LEFT COLUMN ==============================

        # --- Drop / browse area (compact, one row) ---
        drop_frame = tk.Frame(left, bg="#eef2f7", highlightthickness=2,
                               highlightbackground="#9fb3c8", highlightcolor="#9fb3c8")
        drop_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.drop_label = tk.Label(
            drop_frame,
            text=self._drop_zone_text(),
            bg="#eef2f7", fg="#33475b",
            justify="center", pady=10, font=("TkDefaultFont", 10)
        )
        self.drop_label.pack(fill="x", expand=True)

        if DND_AVAILABLE:
            drop_frame.drop_target_register(DND_FILES)
            drop_frame.dnd_bind('<<Drop>>', self._on_drop)
            self.drop_label.drop_target_register(DND_FILES)
            self.drop_label.dnd_bind('<<Drop>>', self._on_drop)

        browse_row = ttk.Frame(left)
        browse_row.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(browse_row, text="Browse for Image...",
                   command=self._browse_image).pack(side="left")
        self.file_label = ttk.Label(browse_row, text="No file selected", foreground="#555")
        self.file_label.pack(side="left", padx=10)

        # --- Preview with tile-grid / crop-offset overlay ---
        preview_frame = ttk.LabelFrame(left, text="Preview (drag the orange rectangle to set the offset)")
        preview_frame.grid(row=2, column=0, sticky="nsew")

        self.PREVIEW_MAX_W = 640
        self.PREVIEW_MAX_H = 420

        canvas_area = ttk.Frame(preview_frame)
        canvas_area.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        canvas_area.rowconfigure(0, weight=1)
        canvas_area.columnconfigure(0, weight=1)

        self.preview_canvas = tk.Canvas(canvas_area, bg="#1b1b1b", highlightthickness=0)
        vbar = ttk.Scrollbar(canvas_area, orient="vertical", command=self.preview_canvas.yview)
        hbar = ttk.Scrollbar(canvas_area, orient="horizontal", command=self.preview_canvas.xview)
        self.preview_canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        self.preview_canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")

        # Mouse-wheel zoom (Windows/Mac send <MouseWheel>, Linux X11 sends Button-4/5)
        self.preview_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.preview_canvas.bind("<Button-4>", self._on_mousewheel)
        self.preview_canvas.bind("<Button-5>", self._on_mousewheel)
        self.preview_canvas.bind("<Configure>", lambda e: self._render_preview())

        # Drag the orange rectangle to set the crop/pad offset
        self.preview_canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.preview_canvas.bind("<B1-Motion>", self._on_drag_motion)
        self.preview_canvas.bind("<ButtonRelease-1>", self._on_drag_end)

        zoom_row = ttk.Frame(preview_frame)
        zoom_row.pack(fill="x", padx=8, pady=(4, 8))
        ttk.Label(zoom_row, text="Zoom:").pack(side="left")
        self.zoom_value_label = ttk.Label(zoom_row, text="100%", width=6, foreground="#333")
        self.zoom_value_label.pack(side="left", padx=(4, 10))
        ttk.Button(zoom_row, text="Reset to 100%", command=self._reset_zoom).pack(side="left")
        ttk.Button(zoom_row, text="Reset offset", command=self._reset_offset).pack(side="left", padx=(8, 0))
        self.offset_value_label = ttk.Label(zoom_row, text="", foreground="#333")
        self.offset_value_label.pack(side="left", padx=(10, 0))

        # ============================== RIGHT COLUMN ==============================

        # --- Output base name (used by every mode) ---
        namefrm = ttk.LabelFrame(right, text="Output name")
        namefrm.pack(fill="x", pady=(0, 8))
        row_name = ttk.Frame(namefrm)
        row_name.pack(fill="x", padx=10, pady=8)
        ttk.Label(row_name, text="Output base name:").pack(anchor="w")
        name_sub = ttk.Frame(row_name)
        name_sub.pack(fill="x", pady=(2, 0))
        ttk.Entry(name_sub, textvariable=self.out_name, width=22).pack(side="left")
        self.name_suffix_label = ttk.Label(name_sub, text=" -> name.pal / name.bin", foreground="#777")
        self.name_suffix_label.pack(side="left")

        # --- Output mode selector ---
        mode_frame = ttk.LabelFrame(right, text="Output mode")
        mode_frame.pack(fill="x", pady=(0, 8))
        row_mode = ttk.Frame(mode_frame)
        row_mode.pack(fill="x", padx=10, pady=8)
        ttk.Radiobutton(row_mode, text="Bitmap (fixed screen resolution)",
                         value="bitmap", variable=self.mode,
                         command=self._on_mode_change).pack(anchor="w")
        ttk.Radiobutton(row_mode, text="Tileset (for tileDefineTileSet)",
                         value="tileset", variable=self.mode,
                         command=self._on_mode_change).pack(anchor="w")
        ttk.Radiobutton(row_mode, text="K2 Mini-LCD (R5G6B5 binary only)",
                         value="lcd", variable=self.mode,
                         command=self._on_mode_change).pack(anchor="w")

        # Container that holds all mode-option panels; only one is packed at a time.
        self.mode_options_container = ttk.Frame(right)
        self.mode_options_container.pack(fill="x", pady=(0, 0))

        # --- Bitmap mode options ---
        self.bitmap_frame = ttk.LabelFrame(self.mode_options_container, text="Bitmap settings")
        row_b = ttk.Frame(self.bitmap_frame)
        row_b.pack(fill="x", padx=10, pady=8)
        ttk.Radiobutton(row_b, text="320 x 240", value="320x240",
                         variable=self.bitmap_resolution).pack(anchor="w")
        ttk.Radiobutton(row_b, text="320 x 200", value="320x200",
                         variable=self.bitmap_resolution).pack(anchor="w")
        ttk.Label(
            row_b,
            text="Output is forced to this resolution regardless of the source PNG.\n"
                 "Smaller images are padded with zero bytes; larger images are\n"
                 "cropped (and you'll get a warning). Drag the orange rectangle\n"
                 "in the preview to choose where the image sits in that frame.",
            foreground="#777", justify="left"
        ).pack(anchor="w", pady=(6, 0))

        # --- Tileset mode options ---
        self.tileset_frame = ttk.LabelFrame(self.mode_options_container, text="Tileset settings")
        row_t1 = ttk.Frame(self.tileset_frame)
        row_t1.pack(fill="x", padx=10, pady=(8, 6))
        ttk.Label(row_t1, text="Tile size:").pack(side="left")
        ttk.Radiobutton(row_t1, text="8 x 8", value="8",
                         variable=self.tile_size_choice).pack(side="left", padx=(8, 10))
        ttk.Radiobutton(row_t1, text="16 x 16", value="16",
                         variable=self.tile_size_choice).pack(side="left")

        row_t2 = ttk.Frame(self.tileset_frame)
        row_t2.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Checkbutton(
            row_t2, text="Square arrangement (unchecked = linear vertical strip)",
            variable=self.square_layout
        ).pack(anchor="w")
        ttk.Checkbutton(
            row_t2, text="Snap offset to tile grid (square arrangement only)",
            variable=self.snap_to_tile_grid
        ).pack(anchor="w", pady=(2, 0))
        self.tileset_dims_label = ttk.Label(row_t2, text="", foreground="#333")
        self.tileset_dims_label.pack(anchor="w", pady=(4, 0))
        ttk.Label(
            row_t2,
            text="Square: tiles laid out left-to-right, top-to-bottom in a 16x16 tile\n"
                 "grid (128x128 px for 8x8 tiles, 256x256 px for 16x16 tiles). Drag the\n"
                 "orange rectangle to choose where the image sits (free, or snapped to\n"
                 "the tile grid if checked above). You're responsible for keeping your\n"
                 "source PNG within that width/height -- it's cropped with no warning.\n"
                 "Linear (unchecked): tiles are stacked into a single tile-wide vertical\n"
                 "strip, 256 tiles tall (8x2048 px for 8x8 tiles, 16x4096 px for 16x16).\n"
                 "No draggable offset in this arrangement.",
            foreground="#777", justify="left"
        ).pack(anchor="w", pady=(4, 0))

        # --- LCD mode options ---
        self.lcd_frame = ttk.LabelFrame(self.mode_options_container, text="K2 Mini-LCD settings")
        row_l = ttk.Frame(self.lcd_frame)
        row_l.pack(fill="x", padx=10, pady=8)
        ttk.Label(
            row_l,
            text=f"Output is forced to the LCD's native {LCD_WIDTH}x{LCD_HEIGHT} buffer as an\n"
                 "R5G6B5 binary -- only that file is written in this mode (no .pal/.bin).\n"
                 f"The physical screen only shows a centered {LCD_WIDTH}x{LCD_VISIBLE_HEIGHT} window\n"
                 "(there's a bezel border top and bottom), shown as a cyan guide below.\n"
                 "Drag freely in the preview to reposition your image in the buffer.",
            foreground="#777", justify="left"
        ).pack(anchor="w")

        # --- Output location ---
        outfrm = ttk.LabelFrame(right, text="Output folder")
        outfrm.pack(fill="x", pady=(8, 8))
        row3 = ttk.Frame(outfrm)
        row3.pack(fill="x", padx=10, pady=8)
        self.out_dir_label = ttk.Label(row3, textvariable=self.output_dir, foreground="#333",
                                        wraplength=280, justify="left")
        self.out_dir_label.pack(side="left", fill="x", expand=True, anchor="w")
        ttk.Button(row3, text="Change...", command=self._change_output_dir).pack(side="right", anchor="n")

        # --- Convert button ---
        action_row = ttk.Frame(right)
        action_row.pack(fill="x", pady=(0, 8))
        self.convert_btn = ttk.Button(action_row, text="Convert", command=self._convert)
        self.convert_btn.pack(side="left")
        self.status_label = ttk.Label(action_row, text="", foreground="#0a6")
        self.status_label.pack(side="left", padx=12)

        # --- Log area (fills remaining right-column space) ---
        logfrm = ttk.LabelFrame(right, text="Log")
        logfrm.pack(fill="both", expand=True)
        self.log_text = tk.Text(logfrm, height=5, state="disabled", wrap="word",
                                 bg="#111", fg="#ddd", insertbackground="#ddd")
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

    def _drop_zone_text(self):
        if DND_AVAILABLE:
            return "Drop a PNG here\n(or use Browse for Image below)"
        return "Use \"Browse for Image...\" below to pick a PNG\n" \
               "(install tkinterdnd2 to enable drag-and-drop)"

    # -- Mode / settings helpers ----------------------------------------

    def _on_mode_change(self):
        self.bitmap_frame.pack_forget()
        self.tileset_frame.pack_forget()
        self.lcd_frame.pack_forget()

        mode = self.mode.get()
        if mode == "bitmap":
            self.bitmap_frame.pack(fill="x")
            self.name_suffix_label.config(text=" -> name.pal / name.bin")
        elif mode == "tileset":
            self.tileset_frame.pack(fill="x")
            self.name_suffix_label.config(text=" -> name.pal / name.bin")
        else:
            self.lcd_frame.pack(fill="x")
            self.name_suffix_label.config(text=" -> name_lcd.bin")

        self._reset_offset()

    def _on_target_dims_changed(self):
        self._update_tileset_dims_label()
        self._reset_offset()

    def _tile_size_px(self):
        try:
            return int(self.tile_size_choice.get())
        except ValueError:
            return 16

    def _bitmap_target_size(self):
        w, h = self.bitmap_resolution.get().split("x")
        return int(w), int(h)

    def _update_tileset_dims_label(self):
        tile_size = self._tile_size_px()
        if self.square_layout.get():
            target = 128 if tile_size == 8 else 256
            self.tileset_dims_label.config(
                text=f"-> output: {target} x {target} px (16x16 tile grid, up to 256 tiles)")
        else:
            strip_h = 256 * tile_size
            self.tileset_dims_label.config(
                text=f"-> output: {tile_size} x {strip_h} px (vertical strip, up to 256 tiles)")

    def _drag_config(self):
        """Returns (target_w, target_h, lock_x) describing the draggable
        orange rectangle for the current mode/settings, or None if
        there's no draggable rectangle right now (e.g. linear tileset)."""
        mode = self.mode.get()
        if mode == "bitmap":
            tw, th = self._bitmap_target_size()
            return (tw, th, False)
        elif mode == "tileset":
            if self.square_layout.get():
                tile_size = self._tile_size_px()
                target = 128 if tile_size == 8 else 256
                return (target, target, False)
            return None
        elif mode == "lcd":
            return (LCD_WIDTH, LCD_HEIGHT, False)
        return None

    def _snap_step(self):
        if self.mode.get() == "tileset" and self.square_layout.get() and self.snap_to_tile_grid.get():
            return self._tile_size_px()
        return None

    def _effective_offset(self):
        """Offset actually used for rendering/export -- locks X to 0 for
        modes where only vertical dragging is allowed (K2 LCD)."""
        cfg = self._drag_config()
        if cfg is not None and cfg[2]:  # lock_x
            return 0.0, self.offset_y
        return self.offset_x, self.offset_y

    def _reset_offset(self):
        self.offset_x = 0.0
        self.offset_y = 0.0
        self._update_offset_label()
        self._render_preview()

    def _clamp_offset(self, ox, oy, target_w, target_h):
        if self.orig_w and self.orig_h:
            ox = max(-(target_w - 1), min(self.orig_w - 1, ox))
            oy = max(-(target_h - 1), min(self.orig_h - 1, oy))
        return ox, oy

    def _update_offset_label(self):
        cfg = self._drag_config()
        if cfg is None or self.base_image is None:
            self.offset_value_label.config(text="")
            return
        ox, oy = self._effective_offset()
        if cfg[2]:  # lock_x -> only show vertical offset
            self.offset_value_label.config(text=f"Vertical offset: {round(oy)}px")
        else:
            self.offset_value_label.config(text=f"Offset: {round(ox)}, {round(oy)}px")

    # -- Event handlers -----------------------------------------------

    def _on_drop(self, event):
        path = event.data
        # Handle Tk's brace-wrapping for paths containing spaces
        if path.startswith('{') and path.endswith('}'):
            path = path[1:-1]
        self._set_image_path(path)

    def _browse_image(self):
        path = filedialog.askopenfilename(
            title="Select a PNG",
            filetypes=[("PNG images", "*.png"), ("All files", "*.*")]
        )
        if path:
            self._set_image_path(path)

    def _set_image_path(self, path):
        self.image_path.set(path)
        self.file_label.config(text=os.path.basename(path))
        if not self.out_name.get():
            base = os.path.splitext(os.path.basename(path))[0]
            self.out_name.set(base)
        # default output dir to the image's folder if user hasn't changed it yet
        self._log(f"Selected image: {path}")
        self._load_preview_image(path)

    def _load_preview_image(self, path):
        try:
            source_img = Image.open(path)
            self.orig_w, self.orig_h = source_img.size
            self.base_image = source_img.convert("RGBA")
        except Exception as exc:
            self.base_image = None
            self.preview_canvas.delete("all")
            self.preview_canvas.create_text(
                20, 20, anchor="nw", fill="#c66", width=self.PREVIEW_MAX_W,
                text=f"Couldn't preview image:\n{exc}"
            )
            self.zoom_value_label.config(text="—")
            return

        # Start at whichever is smaller: actual size (100%) or a scale that
        # fits the image in the visible preview area, so big images aren't
        # shown zoomed in past the window on first load.
        fit_scale = min(
            self.PREVIEW_MAX_W / self.orig_w if self.orig_w else 1.0,
            self.PREVIEW_MAX_H / self.orig_h if self.orig_h else 1.0,
        )
        self.zoom = min(1.0, fit_scale) if fit_scale > 0 else 1.0
        self.zoom_value_label.config(text=f"{round(self.zoom * 100)}%")
        self._reset_offset()

    def _render_preview(self):
        self.preview_canvas.delete("all")

        if self.base_image is None:
            cw = self.preview_canvas.winfo_width() or self.PREVIEW_MAX_W
            ch = self.preview_canvas.winfo_height() or self.PREVIEW_MAX_H
            self.preview_canvas.create_text(cw // 2, ch // 2, fill="#777",
                                             text="No image loaded")
            self.preview_canvas.configure(scrollregion=(0, 0, cw, ch))
            return

        disp_w = max(1, round(self.orig_w * self.zoom))
        disp_h = max(1, round(self.orig_h * self.zoom))

        # Nearest-neighbor keeps pixel art crisp when zoomed in; smooth
        # downscaling looks better when zoomed out.
        resample = Image.NEAREST if self.zoom >= 1 else Image.LANCZOS
        resized_source = self.base_image.resize((disp_w, disp_h), resample)

        drag_cfg = self._drag_config()
        rect_bounds = None
        if drag_cfg is not None:
            target_w, target_h, _lock_x = drag_cfg
            ox, oy = self._effective_offset()
            rx0 = ox * self.zoom
            ry0 = oy * self.zoom
            rect_bounds = (rx0, ry0, rx0 + target_w * self.zoom, ry0 + target_h * self.zoom)

        # Canvas bounds = union of the displayed source image and the
        # rectangle (which may extend beyond the image on any side).
        min_x, min_y = 0.0, 0.0
        max_x, max_y = float(disp_w), float(disp_h)
        if rect_bounds:
            rx0, ry0, rx1, ry1 = rect_bounds
            min_x, min_y = min(min_x, rx0), min(min_y, ry0)
            max_x, max_y = max(max_x, rx1), max(max_y, ry1)

        shift_x, shift_y = -min_x, -min_y
        canvas_w = max(1, math.ceil(max_x - min_x))
        canvas_h = max(1, math.ceil(max_y - min_y))

        base = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        base.paste(resized_source, (round(shift_x), round(shift_y)), resized_source)

        overlay = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        grid_color = (255, 230, 0, 175)  # thick transparent yellow
        MIN_T, MAX_T = 2, 8
        thickness = max(MIN_T, min(MAX_T, round(3 * self.zoom)))

        mode = self.mode.get()
        if mode == "tileset" and not self.square_layout.get():
            tile_size = self._tile_size_px()
            self._draw_tile_grid(draw, shift_x, shift_y, disp_w, disp_h,
                                  tile_size, tile_size, thickness, grid_color)
        elif drag_cfg is not None:
            target_w, target_h, _lock_x = drag_cfg
            rx0, ry0, rx1, ry1 = rect_bounds
            rx0s, ry0s, rx1s, ry1s = rx0 + shift_x, ry0 + shift_y, rx1 + shift_x, ry1 + shift_y

            if mode == "tileset":  # square arrangement
                tile_size = self._tile_size_px()
                self._draw_tile_grid(draw, shift_x, shift_y, disp_w, disp_h,
                                      tile_size, tile_size, thickness, grid_color)
                self._draw_rect_boundary(draw, canvas_w, canvas_h, rx0s, ry0s, rx1s, ry1s,
                                          thickness, (255, 140, 0, 220), dim=False)
            elif mode == "bitmap":
                self._draw_rect_boundary(draw, canvas_w, canvas_h, rx0s, ry0s, rx1s, ry1s,
                                          thickness, grid_color, dim=True)
            elif mode == "lcd":
                self._draw_rect_boundary(draw, canvas_w, canvas_h, rx0s, ry0s, rx1s, ry1s,
                                          thickness, grid_color, dim=True)
                visible_h = LCD_VISIBLE_HEIGHT * self.zoom
                inset = ((target_h * self.zoom) - visible_h) / 2
                draw.rectangle([rx0s, ry0s + inset, rx1s, ry1s - inset],
                                outline=(0, 210, 255, 230), width=max(2, thickness - 1))

        composited = Image.alpha_composite(base, overlay)
        self.preview_photo = ImageTk.PhotoImage(composited)
        self.preview_canvas.create_image(0, 0, anchor="nw", image=self.preview_photo)
        self.preview_canvas.configure(scrollregion=(0, 0, canvas_w, canvas_h))

    def _draw_tile_grid(self, draw, ox, oy, w, h, tile_w, tile_h, thickness, color):
        step_x = max(tile_w * self.zoom, 1)
        x = 0.0
        while x <= w:
            draw.line([(ox + x, oy), (ox + x, oy + h)], fill=color, width=thickness)
            x += step_x

        step_y = max(tile_h * self.zoom, 1)
        y = 0.0
        while y <= h:
            draw.line([(ox, oy + y), (ox + w, oy + y)], fill=color, width=thickness)
            y += step_y

        # Explicit outer frame so the right/bottom edges always show, even
        # when the step-based lines above overshoot by a fraction of a px.
        draw.rectangle([(ox, oy), (ox + w - 1, oy + h - 1)], outline=color, width=thickness)

    def _draw_rect_boundary(self, draw, canvas_w, canvas_h, x0, y0, x1, y1, thickness, color, dim=True):
        """Draw the boundary of the forced-output rectangle. If dim=True,
        also darken the area outside it (bitmap/LCD: that area is truly
        lost/padded). Square tileset mode uses dim=False since the grid
        already communicates the working area clearly enough on its own."""
        if dim:
            draw.rectangle([0, 0, canvas_w, canvas_h], fill=(0, 0, 0, 130))
            draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0, 0))
        draw.rectangle([x0, y0, max(x0, x1 - 1), max(y0, y1 - 1)], outline=color, width=thickness)

    def _set_zoom(self, new_zoom, keep_point=None):
        new_zoom = max(0.05, min(new_zoom, 10.0))
        if abs(new_zoom - self.zoom) < 1e-9:
            return
        old_zoom = self.zoom
        self.zoom = new_zoom
        self.zoom_value_label.config(text=f"{round(self.zoom * 100)}%")

        if keep_point is None or self.base_image is None:
            self._render_preview()
            return

        # Keep the same image point under the cursor while zooming, like
        # a typical image editor's scroll-to-zoom.
        cx, cy, screen_x, screen_y = keep_point
        frac_x = cx / max(1, self.orig_w * old_zoom)
        frac_y = cy / max(1, self.orig_h * old_zoom)

        self._render_preview()

        total_w = max(1, self.orig_w * self.zoom)
        total_h = max(1, self.orig_h * self.zoom)
        new_cx = frac_x * total_w
        new_cy = frac_y * total_h
        x0 = max(0.0, min(1.0, (new_cx - screen_x) / total_w))
        y0 = max(0.0, min(1.0, (new_cy - screen_y) / total_h))
        self.preview_canvas.xview_moveto(x0)
        self.preview_canvas.yview_moveto(y0)

    def _on_mousewheel(self, event):
        if self.base_image is None:
            return

        scrolling_down = getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0
        factor = (1 / 1.15) if scrolling_down else 1.15

        cx = self.preview_canvas.canvasx(event.x)
        cy = self.preview_canvas.canvasy(event.y)
        self._set_zoom(self.zoom * factor, keep_point=(cx, cy, event.x, event.y))

    def _reset_zoom(self):
        self._set_zoom(1.0)
        self.preview_canvas.xview_moveto(0)
        self.preview_canvas.yview_moveto(0)

    # -- Rectangle dragging (crop/pad offset) ----------------------------

    def _on_drag_start(self, event):
        if self.base_image is None or self._drag_config() is None:
            return
        self._drag_start = (event.x, event.y, self.offset_x, self.offset_y)

    def _on_drag_motion(self, event):
        if self._drag_start is None:
            return
        cfg = self._drag_config()
        if cfg is None:
            return
        target_w, target_h, lock_x = cfg
        start_x, start_y, orig_ox, orig_oy = self._drag_start

        dx_img = (event.x - start_x) / self.zoom
        dy_img = (event.y - start_y) / self.zoom

        new_ox = orig_ox if lock_x else orig_ox + dx_img
        new_oy = orig_oy + dy_img

        new_ox, new_oy = self._clamp_offset(new_ox, new_oy, target_w, target_h)

        snap = self._snap_step()
        if snap:
            new_ox = round(new_ox / snap) * snap
            new_oy = round(new_oy / snap) * snap

        self.offset_x, self.offset_y = new_ox, new_oy
        self._update_offset_label()
        self._render_preview()

    def _on_drag_end(self, event):
        self._drag_start = None

    def _change_output_dir(self):
        chosen = filedialog.askdirectory(title="Choose output folder",
                                          initialdir=self.output_dir.get())
        if chosen:
            self.output_dir.set(chosen)
            self._log(f"Output folder set to: {chosen}")

    def _log(self, message):
        self.log_text.config(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _convert(self):
        image_path = self.image_path.get().strip()
        name = self.out_name.get().strip()
        out_dir = self.output_dir.get().strip()

        if not image_path:
            messagebox.showwarning("No image", "Drop or browse for a PNG first.")
            return
        if not name:
            messagebox.showwarning("No output name", "Enter an output base name.")
            return
        if not os.path.isdir(out_dir):
            messagebox.showerror("Bad output folder", "The chosen output folder doesn't exist.")
            return

        mode = self.mode.get()

        if mode != "lcd":
            try:
                with Image.open(image_path) as check_img:
                    image_mode = check_img.mode
            except Exception as exc:
                messagebox.showerror("Can't open image", f"Couldn't open this file:\n{exc}")
                return

            if image_mode != 'P':
                messagebox.showwarning(
                    "Not an indexed-color PNG",
                    f"This image is {image_mode} mode, not indexed color (P mode).\n\n"
                    "The palette/bitmap converter needs a PNG saved with a limited "
                    "color palette, not a full-color PNG.\n\n"
                    "To fix it:\n"
                    "  1. Open the PNG in GIMP or Photoshop\n"
                    "  2. GIMP: Image > Mode > Indexed\n"
                    "     Photoshop: Image > Mode > Indexed Color\n"
                    "  3. Choose a palette size that covers your image's colors\n"
                    "  4. Re-export as PNG and load that file here\n\n"
                    "This app won't auto-convert it for you, since quantizing "
                    "colors automatically can change how your art looks — "
                    "you're in the best position to choose the right palette."
                )
                self._log(f"Conversion stopped: image is {image_mode}, not indexed (P mode).")
                return

        offset_x, offset_y = self._effective_offset()

        palette_file = bitmap_file = lcd_bin_file = None
        if mode == "lcd":
            lcd_bin_file = os.path.join(out_dir, f"{name}_lcd.bin")
        else:
            palette_file = os.path.join(out_dir, f"{name}.pal")
            bitmap_file = os.path.join(out_dir, f"{name}.bin")

        self.convert_btn.config(state="disabled")
        self.status_label.config(text="Converting...", foreground="#c80")

        def worker():
            try:
                cropped_warning = False
                if mode == "bitmap":
                    target_w, target_h = self._bitmap_target_size()
                    cropped_warning = save_bitmap_mode(image_path, palette_file, bitmap_file,
                                                        target_w, target_h, offset_x, offset_y,
                                                        log=self._log)
                elif mode == "tileset":
                    tile_size = self._tile_size_px()
                    if self.square_layout.get():
                        save_tileset_square(image_path, palette_file, bitmap_file, tile_size,
                                             offset_x, offset_y, log=self._log)
                    else:
                        save_tileset_linear(image_path, palette_file, bitmap_file, tile_size,
                                             log=self._log)
                else:  # lcd
                    save_lcd_mode(image_path, lcd_bin_file, offset_x, offset_y, log=self._log)

                self.after(0, lambda: self._on_success(palette_file, bitmap_file, lcd_bin_file, cropped_warning))
            except Exception as exc:
                self.after(0, lambda: self._on_failure(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_success(self, palette_file, bitmap_file, lcd_bin_file, cropped_warning):
        self.convert_btn.config(state="normal")
        self.status_label.config(text="Done!", foreground="#0a6")
        self._log("Conversion complete.")

        if cropped_warning:
            messagebox.showwarning(
                "Source image was cropped",
                "Your source PNG was larger than the forced bitmap resolution, "
                "so it was cropped to fit (based on the current offset)."
            )

        parts = []
        if palette_file:
            parts.append(f"Palette written to:\n{palette_file}")
        if bitmap_file:
            parts.append(f"Bitmap written to:\n{bitmap_file}")
        if lcd_bin_file:
            parts.append(f"K2 mini-LCD binary written to:\n{lcd_bin_file}")
        messagebox.showinfo("Conversion complete", "\n\n".join(parts))

    def _on_failure(self, exc):
        self.convert_btn.config(state="normal")
        self.status_label.config(text="Failed", foreground="#c00")
        self._log(f"ERROR: {exc}")
        messagebox.showerror("Conversion failed", str(exc))


if __name__ == '__main__':
    app = WildbitsTileConverterApp()
    app.mainloop()
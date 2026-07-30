#!/usr/bin/env python3
"""
Wildbits / Foenix F256 — Memory Map Planner
============================================================
SKELETON / PLACEHOLDER PROGRAM

This is a starting point, not a hardware-accurate reference. Every address,
region size and MMU rule below is a PLACEHOLDER — replace with the real
values from the Wildbits / F256  documentation once you have them
(search comments for "PLACEHOLDER"). Slot 6 ($C000-$DFFF) and the general
layout match https://f256wiki.wildbitscomputing.com/index.php?title=Memory_Management

Model used here
----------------
* The 6502 CPU only ever sees 64KB ($0000-$FFFF), lowest address at the
  bottom of the CPU panel, highest at the top. It's divided into 8 MMU
  slots of 8KB. Slots 6 ($C000-$DFFF, IO) and 7 ($E000-$FFFF, the "swap
  springboard" code uses to peer into other 8KB pages) are UNMOVABLE —
  they can't be re-mapped to a different physical page from this tool.
* Physical SRAM is a flat byte-addressable pool:
    - Gen 1: 512KB (1 bank)     - Gen 2: 2MB (4 banks, 2x2 grid on screen)
  Bank 0's first 64KB is permanently reserved and can never hold a placed
  object: $0000-$02FF (low mem registers), $0300-$BFFF (MAIN CODE AREA),
  $C000-$DFFF (IO pages), $E000-$FFFF (swap window). Everything above
  $10000, and all of banks 1-3, is free for objects.
* Objects are placed at an EXACT BYTE ADDRESS (not rounded to a page or
  cell) — a real 64-byte 8x8 sprite really is 64 bytes. The 2KB "cell" is
  only the on-screen display/click granularity: if several small objects'
  byte ranges land in the same 2KB cell, that cell renders as a small
  rainbow stripe pattern (rather than crowding tiny illegible slivers into
  the overview), and an "Exploded view" window lets you see/select/inspect
  each one individually with its real color+hatch, at a guaranteed-legible
  minimum width.
* Placing something (from the toolbar OR by dropping/browsing a file) opens
  a small dialog confirming the exact start address (typed, editable)
  before it's committed.
* Objects can be dragged (when they're the sole occupant of a cell),
  resized, or moved-by-typed-address from the Details panel — the latter
  is the reliable way to reposition something too small to comfortably
  grab with the mouse.
* "Compact" packs every UNLOCKED object back-to-back, byte-exact, into the
  lowest free space — which is exactly how several small objects end up
  sharing one 2KB cell. Locked objects never move; they (and the reserved
  zone) act as fixed obstacles unlocked objects pack around.
* Any object can be "Locked" from the Details panel, which protects it from
  both "Wipe all" and "Compact". "Unlock all" clears every lock at once.

Run:  python3 f256k2_memory_map.py
(Optional: `pip install tkinterdnd2` enables real OS drag-and-drop onto the
file dropzone; without it, click-to-browse still works fully.)
"""

import math
import os
import random
import re
import colorsys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

try:
    from tkinterdnd2 import DND_FILES  # optional native OS drag-and-drop
    HAS_DND = True
except ImportError:
    HAS_DND = False

# ---------------------------------------------------------------------------
# Constants (PLACEHOLDERS — adjust to match real F256K2 memory map docs)
# ---------------------------------------------------------------------------

PAGE_SIZE = 0x2000                       # 8KB  — MMU bank-switch granularity
CLICK_UNIT = 0x800                       # 2KB  — on-screen display/click granularity
SUBCELLS_PER_PAGE = PAGE_SIZE // CLICK_UNIT  # 4

CPU_SLOTS = 8                            # 8 x 8KB = 64KB CPU address space
CPU_SIZE = CPU_SLOTS * PAGE_SIZE         # 0x10000

COLUMN_SIZE = 0x10000                    # 64KB per column (8 pages tall)
ROWS_PER_COLUMN = COLUMN_SIZE // PAGE_SIZE   # 8 pages per column

BANK_SIZE = 0x80000                      # 512KB per SRAM bank
PAGES_PER_BANK = BANK_SIZE // PAGE_SIZE  # 64 pages / bank
COLS_PER_BANK = BANK_SIZE // COLUMN_SIZE # 8 columns of 64KB per 512KB bank

GEN1_BANKS = 1                           # Gen.1 Foenix: 512KB total SRAM
GEN2_BANKS = 4                           # Gen.2 Foenix: 2MB total (bank0 + 3 more)
BANKS_PER_ROW = 2                        # 2x2 grid of banks for Gen.2

MIN_SEGMENT_PX = 44                      # exploded-view minimum clickable width

# Mouse-placeable resource "types". `size` is the DEFAULT byte size offered
# in the toolbar — fully editable per-placement (and after placement, via
# the Details panel's resize box).
RESOURCE_TYPES = {
    "bitmap":        dict(label="Bitmap 320x240",  size=320 * 240, color="#4C72B0"),
    "bitmap70Hz":    dict(label="Bitmap 320x200",  size=320 * 200, color="#6D95C3"),
    "sprite8":       dict(label="Sprite 8x8",         size=8 * 8,     color="#DD8452"),
    "sprite16":      dict(label="Sprite 16x16",       size=16 * 16,   color="#E3A06E"),
    "sprite32":      dict(label="Sprite 32x32",       size=32 * 32,   color="#F0C19A"),
    "tileset 8x8":   dict(label="Tileset 8x8",        size=8 * 8 * 256,   color="#00AAB8"),
    "tileset 16x16": dict(label="Tileset 16x16",      size=16 * 16 * 256,   color="#00ECFF"),
    "palette":       dict(label="Palette",            size=1024,      color="#55A868"),
    "midi":          dict(label="MIDI file",          size=4096,      color="#C44E52"),
    "vgm":           dict(label="OPL3 VGM",           size=32768,     color="#8172B2"),
}
RESOURCE_LABELS = [info["label"] for info in RESOURCE_TYPES.values()]
RESOURCE_LABEL_TO_KEY = {info["label"]: key for key, info in RESOURCE_TYPES.items()}

IO_COLOR = "#999999"        # reserved: registers / IO overlay / swap window
CODE_COLOR = "#AB8A62"      # reserved: main code area
FREE_COLOR = "#F2F2F2"
GRID_LINE = "#B0B0B0"
BANK_BORDER = "#333333"
SELECT_COLOR = "#FFD400"
DRAG_OK_COLOR = "#2E8B57"
DRAG_BAD_COLOR = "#C0392B"
PARTIAL_LINE_COLOR = "#888888"
RAINBOW_COLORS = ["#E53935", "#FB8C00", "#FDD835", "#43A047", "#1E88E5", "#8E24AA"]

DRAG_THRESHOLD = 4   # pixels of movement before a press becomes a drag

# Font sizes (bumped ~2 ticks up from a plainer baseline for readability)
F_TINY, F_SMALL, F_MED, F_LARGE = 9, 11, 12, 14


def fmt_addr(addr, mode):
    """Format an address as hex ('$XXXXXX') or decimal, per current toggle."""
    return f"${addr:06X}" if mode == "hex" else str(addr)


def parse_addr_input(text, mode):
    """Parse a typed address: '$1A000', '0x1A000', or plain digits (base
    depends on the current hex/dec display mode). Raises ValueError."""
    text = text.strip()
    if not text:
        raise ValueError("empty address")
    if text.startswith("$"):
        return int(text[1:], 16)
    if text.lower().startswith("0x"):
        return int(text, 16)
    if mode == "hex":
        return int(text, 16)
    return int(text, 10)


# ---------------------------------------------------------------------------
# Small drawing helpers: per-resource hatch patterns + line clipping
# ---------------------------------------------------------------------------

PATTERN_NAMES = ["diag_fwd", "diag_back", "cross", "horiz", "vert", "dots"]


def sanitize_identifier(text):
    """Make text safe to use as an EMBED/pragma identifier (and a unique
    object name): letters, digits, underscore only, can't start with a digit."""
    text = re.sub(r"[^0-9A-Za-z_]", "_", text.strip())
    if not text:
        text = "obj"
    if text[0].isdigit():
        text = "_" + text
    return text


def hex_to_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def is_dark(color):
    r, g, b = hex_to_rgb(color)
    return (0.299 * r + 0.587 * g + 0.114 * b) < 140


def clip_segment_to_rect(x0, y0, x1, y1, rx0, ry0, rx1, ry1):
    """Liang-Barsky clip of segment (x0,y0)-(x1,y1) to rect. Returns tuple or None."""
    dx, dy = x1 - x0, y1 - y0
    p = [-dx, dx, -dy, dy]
    q = [x0 - rx0, rx1 - x0, y0 - ry0, ry1 - y0]
    u1, u2 = 0.0, 1.0
    for pi, qi in zip(p, q):
        if pi == 0:
            if qi < 0:
                return None
        else:
            t = qi / pi
            if pi < 0:
                if t > u2:
                    return None
                u1 = max(u1, t)
            else:
                if t < u1:
                    return None
                u2 = min(u2, t)
    if u1 > u2:
        return None
    return (x0 + u1 * dx, y0 + u1 * dy, x0 + u2 * dx, y0 + u2 * dy)


def _draw_diag(canvas, x0, y0, x1, y1, spacing, color, forward):
    w, h = x1 - x0, y1 - y0
    n0, n1 = -int(h / spacing) - 1, int(w / spacing) + 1
    for i in range(n0, n1 + 1):
        ax = x0 + i * spacing
        if forward:      # "/" — rises to the right
            ay, bx, by = y1, ax + h, y0
        else:             # "\" — falls to the right
            ay, bx, by = y0, ax + h, y1
        seg = clip_segment_to_rect(ax, ay, bx, by, x0, y0, x1, y1)
        if seg:
            canvas.create_line(*seg, fill=color, width=1)


def draw_resource_pattern(canvas, x0, y0, x1, y1, rid, base_color):
    """Overlay a unique-ish hatch pattern for resource `rid` on top of its cell fill."""
    if x1 - x0 < 1 or y1 - y0 < 1:
        return
    rnd = random.Random((rid * 2654435761) & 0xFFFFFFFF)
    pattern = PATTERN_NAMES[rid % len(PATTERN_NAMES)]
    spacing = 5 + rnd.randint(0, 4)
    color = "#FFFFFF" if is_dark(base_color) else "#000000"

    if pattern in ("diag_fwd", "cross"):
        _draw_diag(canvas, x0, y0, x1, y1, spacing, color, forward=True)
    if pattern in ("diag_back", "cross"):
        _draw_diag(canvas, x0, y0, x1, y1, spacing, color, forward=False)
    if pattern == "horiz":
        y = y0 + spacing / 2
        while y < y1:
            canvas.create_line(x0, y, x1, y, fill=color, width=1)
            y += spacing
    if pattern == "vert":
        x = x0 + spacing / 2
        while x < x1:
            canvas.create_line(x, y0, x, y1, fill=color, width=1)
            x += spacing
    if pattern == "dots":
        y = y0 + spacing / 2
        while y < y1:
            x = x0 + spacing / 2
            while x < x1:
                canvas.create_oval(x - 1, y - 1, x + 1, y + 1, fill=color, outline="")
                x += spacing
            y += spacing


def draw_rainbow(canvas, x0, y0, x1, y1):
    n = len(RAINBOW_COLORS)
    w = (x1 - x0) / n
    for i, c in enumerate(RAINBOW_COLORS):
        canvas.create_rectangle(x0 + i * w, y0, x0 + (i + 1) * w, y1, fill=c, outline="")


def draw_cell(canvas, x0, y0, x1, y1, model, cell_index):
    """Fill one 2KB display cell:
       - a reserved zone -> flat reserved color, no hatch
       - free            -> flat free color
       - one object      -> free base + a colored/hatched band for the part of
                             this cell that object actually occupies (so a
                             tiny object shows mostly 'free' with a thin band)
       - 2+ objects       -> a rainbow stripe (see the Exploded View for detail)
    """
    zone = model.cell_reserved(cell_index)
    if zone is not None:
        canvas.create_rectangle(x0, y0, x1, y1, fill=zone["color"], outline="")
        return
    occ = model.cell_occupants(cell_index)
    if len(occ) == 0:
        canvas.create_rectangle(x0, y0, x1, y1, fill=FREE_COLOR, outline="")
        return
    if len(occ) >= 2:
        draw_rainbow(canvas, x0, y0, x1, y1)
        return

    rid = occ[0]
    r = model.resources[rid]
    u0, u1 = model.resource_used_span_in_cell(rid, cell_index)  # byte offsets within cell
    h = y1 - y0
    free_bottom_h = (u0 / CLICK_UNIT) * h            # low-address free space -> bottom
    free_top_h = ((CLICK_UNIT - u1) / CLICK_UNIT) * h  # high-address free space -> top
    canvas.create_rectangle(x0, y0, x1, y1, fill=FREE_COLOR, outline="")
    used_y0, used_y1 = y0 + free_top_h, y1 - free_bottom_h
    if used_y1 > used_y0:
        canvas.create_rectangle(x0, used_y0, x1, used_y1, fill=r["color"], outline="")
        draw_resource_pattern(canvas, x0, used_y0, x1, used_y1, rid, r["color"])
        if free_top_h > 0.5:
            canvas.create_line(x0, used_y0, x1, used_y0, fill=PARTIAL_LINE_COLOR, dash=(2, 1))
        if free_bottom_h > 0.5:
            canvas.create_line(x0, used_y1, x1, used_y1, fill=PARTIAL_LINE_COLOR, dash=(2, 1))
    if r.get("locked"):
        draw_lock_badge(canvas, x0, y0, x1, y1)


def draw_lock_badge(canvas, x0, y0, x1, y1):
    """A tiny padlock drawn from primitives (arc + rect) in a cell's top-right
    corner — no emoji/font dependency, so it always renders."""
    s = max(6, min(x1 - x0, y1 - y0) * 0.5)
    bx1, by0 = x1 - 2, y0 + 2
    bx0 = bx1 - s
    body_top = by0 + s * 0.45
    by1 = body_top + s * 0.55
    canvas.create_arc(bx0, by0, bx1, body_top + s * 0.35, start=0, extent=180,
                       style="arc", outline="#7A5200", width=1)
    canvas.create_rectangle(bx0, body_top, bx1, by1, fill="#FFB300", outline="#7A5200")


def hash_color(text):
    """A deterministic (for this run), pleasant, distinct color for a filename."""
    h = (hash(text) & 0xFFFFFFFF) / 0xFFFFFFFF
    r, g, b = colorsys.hsv_to_rgb(h, 0.55, 0.82)
    return f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"


# ---------------------------------------------------------------------------
# Small icon widgets (erase / add / hex-dec toggle)
# ---------------------------------------------------------------------------

def draw_icon_plus(canvas, size):
    m, pad = size / 2, size * 0.24
    canvas.create_line(m, pad, m, size - pad, width=2, fill="#1B7A3D")
    canvas.create_line(pad, m, size - pad, m, width=2, fill="#1B7A3D")


def draw_icon_trash(canvas, size):
    pad = size * 0.22
    top = size * 0.34
    canvas.create_line(pad * 0.7, top, size - pad * 0.7, top, width=2, fill="#B23A2E")
    canvas.create_rectangle(pad, top, size - pad, size - pad, outline="#B23A2E", width=2)
    canvas.create_line(size * 0.4, top, size * 0.4, size - pad * 1.3, fill="#B23A2E")
    canvas.create_line(size * 0.6, top, size * 0.6, size - pad * 1.3, fill="#B23A2E")
    canvas.create_rectangle(size * 0.38, top - size * 0.12, size * 0.62, top,
                             outline="#B23A2E", width=1)


def draw_icon_hexdec(canvas, size, mode):
    hex_on = mode == "hex"
    canvas.create_text(size * 0.30, size * 0.5, text="0x",
                        font=("TkDefaultFont", F_TINY - 2, "bold" if hex_on else "normal"),
                        fill="#1B4F8A" if hex_on else "#999999")
    canvas.create_text(size * 0.74, size * 0.5, text="10",
                        font=("TkDefaultFont", F_TINY - 2, "bold" if not hex_on else "normal"),
                        fill="#1B4F8A" if not hex_on else "#999999")
    canvas.create_line(size * 0.5, size * 0.18, size * 0.5, size * 0.82, fill="#CCCCCC")


class IconButton(tk.Frame):
    """A tiny canvas-drawn icon that behaves like a clickable (optionally
    toggleable/"active") button, since ttk doesn't ship real icon assets."""

    def __init__(self, master, draw_fn, command, size=30):
        super().__init__(master, highlightthickness=1, highlightbackground="#999999", bd=0)
        self.draw_fn = draw_fn
        self.command = command
        self.size = size
        self.canvas = tk.Canvas(self, width=size, height=size,
                                 highlightthickness=0, background="#FFFFFF", cursor="hand2")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", lambda e: self.command())
        self.redraw()

    def set_active(self, active):
        self.config(highlightbackground="#000000" if active else "#999999",
                     highlightthickness=2 if active else 1)

    def redraw(self):
        self.canvas.delete("all")
        self.draw_fn(self.canvas, self.size)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class MemoryModel:
    """Physical SRAM (byte-addressable) + reserved zones + MMU slots.

    Placed objects live at an exact (start, size) in bytes — no forced
    alignment. The 2KB "cell" is purely a display/click granularity used by
    the canvases; several objects legitimately can, and after 'Compact'
    frequently will, share one.
    """

    def __init__(self, generation=1):
        self.generation = generation
        self.resources = {}        # id -> dict(type,start,size,label,color,name)
        self._next_id = 1
        self._type_counters = {}   # rtype -> next nnn for auto-naming (bitmap_000, ...)

        # slot -> physical page currently mapped into that CPU 8KB window
        self.cpu_slot_map = list(range(CPU_SLOTS))
        self.slot_fixed = [False] * CPU_SLOTS
        self.slot_fixed[0] = True
        self.slot_fixed[6] = True
        self.slot_fixed[7] = True

        # PLACEHOLDER: bank 0's low 64KB mirrors the CPU's fixed layout and is
        # entirely off-limits to placed objects.
        self.reserved = [
            dict(start=0x0000, end=0x0300, label="Low mem registers", color=IO_COLOR),
            dict(start=0x0300, end=0xC000, label="Main code area", color=CODE_COLOR),
            dict(start=0xA000, end=0xC000, label="Swap pages", color=IO_COLOR),
            dict(start=0xC000, end=0xE000, label="I/O pages", color=IO_COLOR),
            dict(start=0xE000, end=0x10000,
                 label="Kernel reserved", color=IO_COLOR),
        ]

    # -- generation / sizing --------------------------------------------------

    @property
    def num_banks(self):
        return GEN1_BANKS if self.generation == 1 else GEN2_BANKS

    @property
    def num_pages(self):
        return self.num_banks * PAGES_PER_BANK

    @property
    def num_bytes(self):
        return self.num_pages * PAGE_SIZE

    def set_generation(self, gen):
        self.generation = gen
        max_bytes = self.num_bytes
        for rid in list(self.resources):
            r = self.resources[rid]
            if r["start"] + r["size"] > max_bytes:
                self.remove_resource(rid)
        max_page = self.num_pages
        for i, p in enumerate(self.cpu_slot_map):
            if p >= max_page:
                self.cpu_slot_map[i] = 0

    # -- overlap checking --------------------------------------------------------

    def is_blocked(self, start, end, ignore_rid=None):
        for z in self.reserved:
            if z["start"] < end and z["end"] > start:
                return True
        for rid, r in self.resources.items():
            if rid == ignore_rid:
                continue
            if r["start"] < end and r["start"] + r["size"] > start:
                return True
        return False

    # -- naming: auto "type_nnn", enforced-unique, user-renamable ---------------

    def _generate_name(self, rtype):
        n = self._type_counters.get(rtype, 0)
        self._type_counters[rtype] = n + 1
        return f"{rtype}_{n:03d}"

    def _unique_name(self, base):
        existing = {r["name"] for r in self.resources.values()}
        if base not in existing:
            return base
        i = 2
        while f"{base}_{i}" in existing:
            i += 1
        return f"{base}_{i}"

    def rename_resource(self, rid, new_name):
        r = self.resources.get(rid)
        if not r:
            return False, "No such object."
        clean = sanitize_identifier(new_name)
        for other_rid, other in self.resources.items():
            if other_rid != rid and other["name"] == clean:
                return False, f'"{clean}" is already used by another object.'
        r["name"] = clean
        return True, None

    # -- resource placement (exact byte address, arbitrary byte size) --------

    def add_resource(self, rtype, start, size_bytes=None):
        info = RESOURCE_TYPES[rtype]
        size = size_bytes if size_bytes is not None else info["size"]
        name = self._unique_name(self._generate_name(rtype))
        return self._add(start, size, info["label"], info["color"], rtype, name)

    def add_file_resource(self, name, size_bytes, start):
        color = hash_color(name)
        clean_name = self._unique_name(sanitize_identifier(name))
        return self._add(start, size_bytes, "File", color, "file", clean_name)

    def _add(self, start, size, label, color, rtype, name):
        if size <= 0:
            return None, "Size must be a positive number of bytes."
        end = start + size
        if start < 0 or end > self.num_bytes:
            return None, "Resource would exceed the current physical memory size."
        if self.is_blocked(start, end):
            return None, ("That range overlaps a reserved zone or another "
                           "object — pick a different start address.")
        rid = self._next_id
        self._next_id += 1
        self.resources[rid] = dict(type=rtype, start=start, size=size, label=label,
                                    color=color, name=name, locked=False)
        return rid, None

    def toggle_lock(self, rid, locked=None):
        r = self.resources.get(rid)
        if not r:
            return False
        r["locked"] = (not r["locked"]) if locked is None else bool(locked)
        return r["locked"]

    def wipe_all(self):
        """Removes every placed object except locked ones. Reserved zones
        (system registers / main code area / IO / swap window) are never
        touched — they aren't in self.resources to begin with. Returns
        (removed_rids, kept_locked_rids)."""
        removed, kept = [], []
        for rid in list(self.resources):
            if self.resources[rid].get("locked"):
                kept.append(rid)
                continue
            removed.append(rid)
            del self.resources[rid]
        return removed, kept

    def remove_resource(self, rid):
        self.resources.pop(rid, None)

    def remove_at_cell(self, cell_index):
        rid = self.cell_sole_resource_id(cell_index)
        if rid is not None:
            self.remove_resource(rid)
        return rid

    # -- cell-level queries (2KB display granularity) ----------------------------

    def cell_reserved(self, cell_index):
        start = cell_index * CLICK_UNIT
        end = start + CLICK_UNIT
        for z in self.reserved:
            if z["start"] < end and z["end"] > start:
                return z
        return None

    def cell_occupants(self, cell_index):
        start = cell_index * CLICK_UNIT
        end = start + CLICK_UNIT
        return [rid for rid, r in self.resources.items()
                if r["start"] < end and r["start"] + r["size"] > start]

    def cell_sole_resource_id(self, cell_index):
        if self.cell_reserved(cell_index) is not None:
            return None
        occ = self.cell_occupants(cell_index)
        return occ[0] if len(occ) == 1 else None

    def cell_content_label(self, cell_index):
        zone = self.cell_reserved(cell_index)
        if zone is not None:
            return zone["label"] + " (reserved)"
        occ = self.cell_occupants(cell_index)
        if not occ:
            return "free"
        if len(occ) == 1:
            r = self.resources[occ[0]]
            tag = " [locked]" if r.get("locked") else ""
            return f'{r["name"]} ({r["label"]}){tag}'
        return f"{len(occ)} objects packed here"

    def resource_used_span_in_cell(self, rid, cell_index):
        """Byte offsets [u0,u1) *within this cell* actually owned by `rid`."""
        r = self.resources.get(rid)
        if not r:
            return (0, 0)
        cell_start = cell_index * CLICK_UNIT
        cell_end = cell_start + CLICK_UNIT
        ov_start = max(r["start"], cell_start)
        ov_end = min(r["start"] + r["size"], cell_end)
        if ov_end <= ov_start:
            return (0, 0)
        return (ov_start - cell_start, ov_end - cell_start)

    # -- drag/drop relocation + typed move ---------------------------------------

    def can_move_resource(self, rid, new_start):
        r = self.resources.get(rid)
        if not r:
            return False
        end = new_start + r["size"]
        if new_start < 0 or end > self.num_bytes:
            return False
        return not self.is_blocked(new_start, end, ignore_rid=rid)

    def move_resource(self, rid, new_start):
        if not self.can_move_resource(rid, new_start):
            return False
        self.resources[rid]["start"] = new_start
        return True

    # -- resize an already-placed object ----------------------------------------

    def resize_resource(self, rid, new_size_bytes):
        r = self.resources.get(rid)
        if not r or new_size_bytes <= 0:
            return False
        end = r["start"] + new_size_bytes
        if end > self.num_bytes:
            return False
        if self.is_blocked(r["start"], end, ignore_rid=rid):
            return False
        r["size"] = new_size_bytes
        return True

    # -- compact everything toward the lowest free addresses ---------------------

    def compact_all(self):
        """Packs every UNLOCKED object back-to-back, byte-exact, into the
        lowest available free space. Locked objects never move — they (and
        the reserved zone) are treated as fixed obstacles that unlocked
        objects pack around. Returns a list of rids that didn't fit
        (best-effort; left at their old position)."""
        order = sorted(
            (rid for rid, r in self.resources.items() if not r.get("locked")),
            key=lambda rid: self.resources[rid]["start"],
        )
        obstacles = [(z["start"], z["end"]) for z in self.reserved]
        for r in self.resources.values():
            if r.get("locked"):
                obstacles.append((r["start"], r["start"] + r["size"]))
        obstacles.sort()

        skipped = []
        cursor = 0
        for rid in order:
            r = self.resources[rid]
            size = r["size"]
            addr = cursor
            for ostart, oend in obstacles:
                if oend <= addr:
                    continue
                if ostart >= addr + size:
                    break
                addr = oend
            if addr + size > self.num_bytes:
                skipped.append(rid)
                continue
            r["start"] = addr
            obstacles.append((addr, addr + size))
            obstacles.sort()
            cursor = addr + size
        return skipped

    def unlock_all(self):
        """Unlocks every currently-locked object. Returns the list of rids
        that were actually unlocked."""
        unlocked = []
        for rid, r in self.resources.items():
            if r.get("locked"):
                r["locked"] = False
                unlocked.append(rid)
        return unlocked

    # -- exports --------------------------------------------------------------

    def _sorted_resources(self):
        return sorted(self.resources.values(), key=lambda r: r["start"])

    def export_list_text(self):
        """Plain 'name, start addr, size' per line, one object per line."""
        items = self._sorted_resources()
        if not items:
            return "// no objects placed\n"
        lines = [f'{r["name"]}, 0x{r["start"]:X}, {r["size"]}' for r in items]
        return "\n".join(lines) + "\n"

    def export_llvm_mos(self):
        """llvm-mos EMBED(name, "placeholder", addr); //NNNN b style, columns
        aligned by name length."""
        items = self._sorted_resources()
        if not items:
            return "// no objects placed\n"
        w = max(len(r["name"]) for r in items)
        lines = [
            f'EMBED({(r["name"] + ",").ljust(w + 5)}"placeholder", 0x{r["start"]:X});'
            f'  //{r["size"]} b'
            for r in items
        ]
        return "\n".join(lines) + "\n"

    def export_oscar64(self):
        """oscar64 #pragma section/region/data + __export const char[] block
        per object. The region is set to the object's own exact byte range."""
        items = self._sorted_resources()
        if not items:
            return "// no objects placed\n"
        blocks = []
        for r in items:
            name = r["name"]
            start_hex = f'0x{r["start"]:X}'
            end_hex = f'0x{r["start"] + r["size"]:X}'
            blocks.append(
                f'// {name}: {r["size"]} bytes\n'
                f'#pragma section( {name}, 0)\n'
                f'#pragma region( {name}, {start_hex}, {end_hex}, , , {{{name}}} )\n'
                f'#pragma data({name})\n'
                f'__export const char {name}[] = {{\n'
                f'\t#embed "placeholder"\n'
                f'}};\n'
            )
        return "\n".join(blocks)

    # -- MMU slot mapping ---------------------------------------------------------

    def map_slot(self, slot, page):
        if self.slot_fixed[slot]:
            return False, "This slot is fixed and cannot be remapped (placeholder rule)."
        if page >= self.num_pages:
            return False, "That physical page doesn't exist at the current generation."
        self.cpu_slot_map[slot] = page
        return True, None


def populate_demo(model: MemoryModel):
    """Drop a few sample resources in (at their default sizes) so the map
    isn't empty on first run — all safely above the reserved $10000 line."""
    demo = [
        ("vgm", 0x10000),        # placeholder: VGM staged right at $10000
        ("bitmap", 0x20000),
        ("sprite32", 0x3C000),
        ("tileset 16x16", 0x40000),
        ("palette", 0x50000),
        ("midi", 0x5A000),
    ]
    for rtype, start in demo:
        model.add_resource(rtype, start)
    # identity-map slots 1..7 onto physical pages 1..7 for a sane default view
    for slot in range(1, CPU_SLOTS):
        model.map_slot(slot, slot)


# ---------------------------------------------------------------------------
# GUI: physical SRAM canvas
#   - banks (512KB groups) sit in a 2x2 grid so Gen.2's 4 banks fit in a
#     normal expanded window instead of one long scrolling row.
#   - each bank is 8 columns of 64KB (8 x 8KB pages stacked), lowest address
#     at the BOTTOM of the column.
#   - every 8KB page is split into 4 clickable/placeable 2KB display cells.
# ---------------------------------------------------------------------------

class PhysicalMapCanvas(tk.Canvas):
    CELL_W = 76
    CELL_H = 68
    COL_GAP = 3
    BANK_GAP_X = 36
    BANK_GAP_Y = 54
    TOP_MARGIN = 44
    LEFT_MARGIN = 16
    BOTTOM_MARGIN = 22

    def __init__(self, master, app, **kw):
        super().__init__(master, background="white", highlightthickness=0, **kw)
        self.app = app
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Motion>", self._on_hover)
        self.bind("<Leave>", lambda e: self.app.set_status(""))

    # -- geometry: 2x2 bank grid ------------------------------------------------

    def bank_width(self):
        return COLS_PER_BANK * (self.CELL_W + self.COL_GAP) - self.COL_GAP

    def bank_height(self):
        return ROWS_PER_COLUMN * self.CELL_H

    def bank_row_col(self, bank):
        return divmod(bank, BANKS_PER_ROW)  # (row, col)

    def bank_x0(self, bank):
        _, col = self.bank_row_col(bank)
        return self.LEFT_MARGIN + col * (self.bank_width() + self.BANK_GAP_X)

    def bank_y0(self, bank):
        row, _ = self.bank_row_col(bank)
        return self.TOP_MARGIN + row * (self.bank_height() + self.BANK_GAP_Y)

    def page_rect(self, page):
        bank, local = divmod(page, PAGES_PER_BANK)
        col, row = divmod(local, ROWS_PER_COLUMN)
        bx0, by0 = self.bank_x0(bank), self.bank_y0(bank)
        x0 = bx0 + col * (self.CELL_W + self.COL_GAP)
        x1 = x0 + self.CELL_W
        screen_row = (ROWS_PER_COLUMN - 1) - row   # row 0 (lowest addr) -> bottom
        y0 = by0 + screen_row * self.CELL_H
        y1 = y0 + self.CELL_H
        return x0, y0, x1, y1

    def subcell_rect(self, page, subcell):
        x0, y0, x1, y1 = self.page_rect(page)
        sub_h = self.CELL_H / SUBCELLS_PER_PAGE
        screen_sub = (SUBCELLS_PER_PAGE - 1) - subcell   # subcell 0 (lowest) -> bottom
        sy0 = y0 + screen_sub * sub_h
        return x0, sy0, x1, sy0 + sub_h

    def cell_rect(self, cell_index):
        page, subcell = divmod(cell_index, SUBCELLS_PER_PAGE)
        return self.subcell_rect(page, subcell)

    def cell_at_xy(self, x, y):
        """Hit-test a canvas point -> dict(page=..., subcell=...) or None."""
        model = self.app.model
        bw, bh = self.bank_width(), self.bank_height()
        for bank in range(model.num_banks):
            bx0, by0 = self.bank_x0(bank), self.bank_y0(bank)
            if not (bx0 <= x < bx0 + bw and by0 <= y < by0 + bh):
                continue
            col = int((x - bx0) // (self.CELL_W + self.COL_GAP))
            cx0 = bx0 + col * (self.CELL_W + self.COL_GAP)
            if not (cx0 <= x < cx0 + self.CELL_W):
                continue  # landed in the small gap between columns
            screen_row = int((y - by0) // self.CELL_H)
            row = (ROWS_PER_COLUMN - 1) - screen_row
            page = bank * PAGES_PER_BANK + col * ROWS_PER_COLUMN + row
            page_y0 = by0 + screen_row * self.CELL_H
            sub_h = self.CELL_H / SUBCELLS_PER_PAGE
            screen_sub = int((y - page_y0) // sub_h)
            screen_sub = min(max(screen_sub, 0), SUBCELLS_PER_PAGE - 1)
            subcell = (SUBCELLS_PER_PAGE - 1) - screen_sub
            return dict(page=page, subcell=subcell)
        return None

    @staticmethod
    def cell_index(page, subcell):
        return page * SUBCELLS_PER_PAGE + subcell

    def cell_addr_range(self, page, subcell):
        start = self.cell_index(page, subcell) * CLICK_UNIT
        return start, start + CLICK_UNIT - 1

    # -- mouse handling: click (place/erase/map/select) vs. drag (relocate) --------

    def _on_press(self, event):
        x, y = self.canvasx(event.x), self.canvasy(event.y)
        cell = self.cell_at_xy(x, y)
        self.app.drag_moved = False
        self.app.press_xy = (x, y)
        self.app.press_cell = cell
        rid = None
        if cell is not None:
            ci = self.cell_index(cell["page"], cell["subcell"])
            rid = self.app.model.cell_sole_resource_id(ci)  # ambiguous cells aren't draggable
        self.app.drag_rid = rid
        if rid is not None:
            r = self.app.model.resources[rid]
            grabbed_addr = self.cell_index(cell["page"], cell["subcell"]) * CLICK_UNIT
            self.app.drag_grab_offset = grabbed_addr - r["start"]
        else:
            self.app.drag_grab_offset = 0
        self.app.drag_target_start = None

    def _on_drag_motion(self, event):
        if self.app.press_xy is None:
            return
        x, y = self.canvasx(event.x), self.canvasy(event.y)
        px, py = self.app.press_xy
        if not self.app.drag_moved:
            if abs(x - px) < DRAG_THRESHOLD and abs(y - py) < DRAG_THRESHOLD:
                return
            self.app.drag_moved = True

        if self.app.drag_rid is None:
            return  # nothing draggable was grabbed; just an ordinary click-drag

        cell = self.cell_at_xy(x, y)
        if cell is not None:
            ci = self.cell_index(cell["page"], cell["subcell"])
            self.app.drag_target_start = ci * CLICK_UNIT - self.app.drag_grab_offset
        self.app.redraw_all()
        self._show_drag_status()

    def _show_drag_status(self):
        rid = self.app.drag_rid
        target = self.app.drag_target_start
        if rid is None or target is None:
            return
        r = self.app.model.resources[rid]
        ok = self.app.model.can_move_resource(rid, target)
        fmt = self.app.addr_mode
        verdict = "drop OK" if ok else "can't drop here (out of range / occupied)"
        self.app.set_status(
            f'Dragging "{r["name"]}" -> new start {fmt_addr(target, fmt)}  [{verdict}]'
        )

    def _on_release(self, event):
        if self.app.drag_moved and self.app.drag_rid is not None:
            rid = self.app.drag_rid
            target = self.app.drag_target_start
            if target is not None:
                moved = self.app.model.move_resource(rid, target)
                self.app.set_status("Resource relocated." if moved
                                     else "Move cancelled — target was invalid.")
            self._reset_drag()
            self.app._refresh_selection_details()
            self.app.redraw_all()
            return

        # a plain click (no drag): dispatch to the current tool, then update
        # the always-on 2KB selection / details panel.
        cell = self.app.press_cell
        self._reset_drag()
        if cell is not None:
            self.app.selected_object_rid = None
            self.app.on_physical_click(cell["page"], cell["subcell"])
            self.app.select_cell(cell["page"], cell["subcell"])
        self.app.redraw_all()

    def _reset_drag(self):
        self.app.drag_rid = None
        self.app.drag_target_start = None
        self.app.drag_moved = False
        self.app.press_xy = None
        self.app.press_cell = None

    def _on_hover(self, event):
        if self.app.drag_moved:
            return
        cell = self.cell_at_xy(self.canvasx(event.x), self.canvasy(event.y))
        if cell is None:
            self.app.set_status("")
            return
        model = self.app.model
        fmt = self.app.addr_mode
        lo, hi = self.cell_addr_range(cell["page"], cell["subcell"])
        ci = self.cell_index(cell["page"], cell["subcell"])
        self.app.set_status(
            f'Page {cell["page"]} / 2KB cell {cell["subcell"]}  |  '
            f'{fmt_addr(lo, fmt)}-{fmt_addr(hi, fmt)}  |  {model.cell_content_label(ci)}'
        )

    # -- drawing ------------------------------------------------------------------

    def redraw(self):
        self.delete("all")
        model = self.app.model
        fmt = self.app.addr_mode

        for bank in range(model.num_banks):
            bx0, by0 = self.bank_x0(bank), self.bank_y0(bank)
            bank_addr = bank * BANK_SIZE
            self.create_text(
                bx0, by0 - 20, anchor="w",
                text=f"Bank {bank}  ({fmt_addr(bank_addr, fmt)}-"
                     f"{fmt_addr(bank_addr + BANK_SIZE - 1, fmt)})"
                     + ("  [Gen1 base]" if bank == 0 else "  [Gen2 extra]"),
                font=("TkDefaultFont", F_MED, "bold"),
            )
            for col in range(COLS_PER_BANK):
                col_addr = bank_addr + col * COLUMN_SIZE
                cx0 = bx0 + col * (self.CELL_W + self.COL_GAP)
                self.create_text(
                    cx0 + self.CELL_W / 2, by0 + self.bank_height() + 12,
                    text=f"+{fmt_addr(col_addr - bank_addr, fmt)}",
                    font=("TkDefaultFont", F_MED - 1), fill="#666666",
                )
                for row in range(ROWS_PER_COLUMN):
                    page = bank * PAGES_PER_BANK + col * ROWS_PER_COLUMN + row
                    self._draw_page_cell(page)
            self.create_rectangle(
                bx0, by0, bx0 + self.bank_width(), by0 + self.bank_height(),
                outline=BANK_BORDER, width=2,
            )

        self._draw_drag_preview()

        rows_of_banks = math.ceil(model.num_banks / BANKS_PER_ROW)
        cols_of_banks = min(model.num_banks, BANKS_PER_ROW)
        total_w = (self.LEFT_MARGIN * 2 + cols_of_banks * self.bank_width()
                   + max(0, cols_of_banks - 1) * self.BANK_GAP_X)
        total_h = (self.TOP_MARGIN + rows_of_banks * self.bank_height()
                   + max(0, rows_of_banks - 1) * self.BANK_GAP_Y + self.BOTTOM_MARGIN + 24)
        self.config(scrollregion=(0, 0, total_w, total_h))

    def _draw_page_cell(self, page):
        model = self.app.model
        x0, y0, x1, y1 = self.page_rect(page)
        sub_h = (y1 - y0) / SUBCELLS_PER_PAGE

        for subcell in range(SUBCELLS_PER_PAGE):
            ci = self.cell_index(page, subcell)
            sx0, sy0, sx1, sy1 = self.subcell_rect(page, subcell)
            draw_cell(self, sx0, sy0, sx1, sy1, model, ci)
            sel = self.app.selected_cell
            if sel is not None and sel == (page, subcell):
                self.create_rectangle(sx0, sy0, sx1, sy1, outline=SELECT_COLOR, width=3)

        for s in range(1, SUBCELLS_PER_PAGE):
            yy = y0 + s * sub_h
            self.create_line(x0, yy, x1, yy, fill=GRID_LINE)

        pending = self.app.pending_page == page
        self.create_rectangle(
            x0, y0, x1, y1,
            outline="#000000" if pending else GRID_LINE,
            width=3 if pending else 1,
        )

    def _draw_drag_preview(self):
        rid = self.app.drag_rid
        target = self.app.drag_target_start
        if rid is None or target is None or not self.app.drag_moved:
            return
        model = self.app.model
        r = model.resources.get(rid)
        if not r:
            return
        ok = model.can_move_resource(rid, target)
        color = DRAG_OK_COLOR if ok else DRAG_BAD_COLOR
        first_cell = target // CLICK_UNIT
        last_cell = (target + r["size"] - 1) // CLICK_UNIT
        for c in range(first_cell, last_cell + 1):
            if 0 <= c < model.num_pages * SUBCELLS_PER_PAGE:
                x0, y0, x1, y1 = self.cell_rect(c)
                self.create_rectangle(x0, y0, x1, y1, outline=color, width=3, dash=(5, 3))


# ---------------------------------------------------------------------------
# GUI: CPU 64KB canvas (the 8 MMU-controlled slots)
# ---------------------------------------------------------------------------

class CpuMapCanvas(tk.Canvas):
    """Shows the 64KB the 6502 can actually address, as 8 x 8KB MMU slots,
    lowest address at the bottom. Each slot is subdivided (left-to-right)
    into its 4 x 2KB display cells so reserved zones / packed objects are
    all visible."""

    CELL_W = 230
    CELL_H = 50
    LEFT_MARGIN = 16
    TOP_MARGIN = 60

    def __init__(self, master, app, **kw):
        super().__init__(master, background="white", highlightthickness=0, **kw)
        self.app = app
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self.app.set_status(""))

    def slot_screen_row(self, slot):
        """Slot 0 ($0000, lowest address) is drawn at the BOTTOM; slot 7 at the TOP."""
        return (CPU_SLOTS - 1) - slot

    def slot_y0(self, slot):
        return self.TOP_MARGIN + self.slot_screen_row(slot) * self.CELL_H

    def slot_at_xy(self, x, y):
        if not (self.LEFT_MARGIN <= x <= self.LEFT_MARGIN + self.CELL_W):
            return None
        screen_row = int((y - self.TOP_MARGIN) // self.CELL_H)
        if 0 <= screen_row < CPU_SLOTS:
            return (CPU_SLOTS - 1) - screen_row
        return None

    def _on_click(self, event):
        slot = self.slot_at_xy(self.canvasx(event.x), self.canvasy(event.y))
        if slot is not None:
            self.app.on_cpu_slot_click(slot)

    def _on_motion(self, event):
        slot = self.slot_at_xy(self.canvasx(event.x), self.canvasy(event.y))
        if slot is None:
            self.app.set_status("")
            return
        model = self.app.model
        fmt = self.app.addr_mode
        lo, hi = slot * PAGE_SIZE, slot * PAGE_SIZE + PAGE_SIZE - 1
        page = model.cpu_slot_map[slot]
        fixed = " (FIXED / unmovable)" if model.slot_fixed[slot] else ""
        self.app.set_status(
            f"CPU slot {slot}{fixed}  |  {fmt_addr(lo, fmt)}-{fmt_addr(hi, fmt)}"
            f"  ->  physical page {page}"
        )

    def redraw(self):
        self.delete("all")
        model = self.app.model
        fmt = self.app.addr_mode

        x0, y0 = self.LEFT_MARGIN - 6, self.TOP_MARGIN - 6
        x1 = self.LEFT_MARGIN + self.CELL_W + 6
        y1 = self.TOP_MARGIN + CPU_SLOTS * self.CELL_H + 6
        self.create_text(x0, y0 - 22,
                          text="6502 address space\n64KB ($0000-$FFFF)",
                          font=("TkDefaultFont", F_SMALL, "bold"), anchor="w", justify="left")
        self.create_rectangle(x0, y0, x1, y1, outline="#000000", width=5)

        for slot in range(CPU_SLOTS):
            page = model.cpu_slot_map[slot]
            fixed = model.slot_fixed[slot]
            sx0 = self.LEFT_MARGIN
            sy0 = self.slot_y0(slot)
            sx1 = sx0 + self.CELL_W
            sy1 = sy0 + self.CELL_H - 4
            sub_w = (sx1 - sx0) / SUBCELLS_PER_PAGE

            for subcell in range(SUBCELLS_PER_PAGE):
                ci = page * SUBCELLS_PER_PAGE + subcell
                cx0 = sx0 + subcell * sub_w
                cx1 = cx0 + sub_w
                draw_cell(self, cx0, sy0, cx1, sy1, model, ci)

            self.create_rectangle(sx0, sy0, sx1, sy1, outline="#000000",
                                   width=3 if fixed else 1,
                                   dash=(4, 2) if fixed else None)

            lo, hi = slot * PAGE_SIZE, slot * PAGE_SIZE + PAGE_SIZE - 1
            tag = "FIXED" if fixed else "swappable"
            label_text = (f"Slot {slot}  {fmt_addr(lo, fmt)}-{fmt_addr(hi, fmt)}\n"
                          f"-> page {page}  [{tag}]")
            self.create_rectangle(sx0 + 2, sy0 + 2, sx0 + 176, sy0 + 40,
                                   fill="#FFFFFF", outline="")
            self.create_text(sx0 + 6, sy0 + (sy1 - sy0) / 2, anchor="w",
                              text=label_text, font=("TkDefaultFont", F_SMALL), fill="#111111")
        self.config(scrollregion=(0, 0, x1 + 10, y1 + 10))


# ---------------------------------------------------------------------------
# Exploded view: a wide proportional bar for one 2KB cell holding 2+ objects
# ---------------------------------------------------------------------------

class ExplodedViewWindow(tk.Toplevel):
    def __init__(self, app, cell_index):
        super().__init__(app)
        self.app = app
        self.cell_index = cell_index
        self.title(f"Exploded view — 2KB cell {cell_index}")
        self.geometry("340x600")
        ttk.Label(self, text="Lowest address at the bottom, like the main map. "
                              "Click a segment to select that object below (you "
                              "can then resize / move / erase it). Updates live.",
                  font=("TkDefaultFont", F_SMALL), foreground="#555555",
                  wraplength=310, justify="left").pack(padx=10, pady=(10, 4), anchor="w")
        self.canvas = tk.Canvas(self, background="white", width=280, highlightthickness=1,
                                 highlightbackground="#999999")
        self.canvas.pack(padx=10, pady=(0, 10), fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.segments = []
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.redraw()

    def _on_close(self):
        if self.app.exploded_view is self:
            self.app.exploded_view = None
        self.destroy()

    def _cell_partition(self, model, ci):
        """Ordered (rid_or_None, length_bytes) list covering the whole 2KB
        cell from LOW address to HIGH address, splitting wherever the owner
        changes (so real gaps between objects show up as their own 'free'
        segments, not just one lump at the end)."""
        spans = []
        for rid in model.cell_occupants(ci):
            u0, u1 = model.resource_used_span_in_cell(rid, ci)
            if u1 > u0:
                spans.append((u0, u1, rid))
        spans.sort()
        partition = []
        cursor = 0
        for u0, u1, rid in spans:
            if u0 > cursor:
                partition.append((None, u0 - cursor))
            partition.append((rid, u1 - u0))
            cursor = u1
        if cursor < CLICK_UNIT:
            partition.append((None, CLICK_UNIT - cursor))
        return partition

    def redraw(self):
        self.canvas.delete("all")
        self.update_idletasks()
        model = self.app.model
        ci = self.cell_index
        fmt = self.app.addr_mode

        W = max(self.canvas.winfo_width(), 260)
        H = max(self.canvas.winfo_height(), 480)

        partition = self._cell_partition(model, ci)
        heights = self._layout_sizes([length for _, length in partition], H)

        y = H  # start at the BOTTOM (lowest address) and build upward
        self.segments = []
        for (rid, _length), h in zip(partition, heights):
            y0, y1 = y - h, y
            if rid is None:
                self.canvas.create_rectangle(0, y0, W, y1, fill=FREE_COLOR, outline=GRID_LINE)
                self.canvas.create_text(W / 2, (y0 + y1) / 2, text="free",
                                         font=("TkDefaultFont", F_SMALL), fill="#777777")
            else:
                r = model.resources[rid]
                self.canvas.create_rectangle(0, y0, W, y1, fill=r["color"], outline=GRID_LINE)
                draw_resource_pattern(self.canvas, 0, y0, W, y1, rid, r["color"])
                txt_color = "#FFFFFF" if is_dark(r["color"]) else "#000000"
                lock_tag = "\n\U0001F512" if r.get("locked") else ""
                self.canvas.create_text(W / 2, (y0 + y1) / 2,
                                         text=f'{r["name"]}\n{r["size"]}B{lock_tag}',
                                         font=("TkDefaultFont", F_SMALL, "bold"),
                                         fill=txt_color, justify="center")
                self.segments.append((y0, y1, rid))
            y = y0

        cell_start = ci * CLICK_UNIT
        cell_end = cell_start + CLICK_UNIT - 1
        self.canvas.create_text(6, 4, anchor="nw",
                                 text=f"high: {fmt_addr(cell_end, fmt)}",
                                 font=("TkDefaultFont", F_TINY), fill="#666666")
        self.canvas.create_text(6, H - 4, anchor="sw",
                                 text=f"low: {fmt_addr(cell_start, fmt)}",
                                 font=("TkDefaultFont", F_TINY), fill="#666666")

    def _layout_sizes(self, sizes, total_px):
        n = len(sizes)
        if n == 0:
            return []
        total_bytes = sum(sizes) or 1
        widths = [total_px * s / total_bytes for s in sizes]
        # enforce a minimum clickable extent (point 1: never painfully tiny),
        # redistributing remaining pixels proportionally among the rest.
        for _ in range(6):
            below = [i for i, w in enumerate(widths) if w < MIN_SEGMENT_PX]
            if not below or len(below) == n:
                break
            fixed_px = len(below) * MIN_SEGMENT_PX
            remain_px = max(0, total_px - fixed_px)
            other = [i for i in range(n) if i not in below]
            other_bytes = sum(sizes[i] for i in other) or 1
            for i in range(n):
                widths[i] = MIN_SEGMENT_PX if i in below else remain_px * sizes[i] / other_bytes
        return widths

    def _on_click(self, event):
        for y0, y1, rid in self.segments:
            if y0 <= event.y < y1:
                self.app.selected_object_rid = rid
                self.app._refresh_selection_details()
                return


# ---------------------------------------------------------------------------
# Export window: shows generated text (list / llvm-mos / oscar64), with
# copy-to-clipboard and save-to-file
# ---------------------------------------------------------------------------

class ExportWindow(tk.Toplevel):
    def __init__(self, app, title, text, default_ext=".txt"):
        super().__init__(app)
        self.title(title)
        self.geometry("820x560")
        self._text = text
        self._default_ext = default_ext

        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", padx=8, pady=6)
        ttk.Button(toolbar, text="Copy to clipboard", command=self._copy).pack(side="left")
        ttk.Button(toolbar, text="Save As\u2026", command=self._save).pack(side="left", padx=6)
        self.copied_var = tk.StringVar(value="")
        ttk.Label(toolbar, textvariable=self.copied_var, foreground="#2E8B57",
                  font=("TkDefaultFont", F_SMALL)).pack(side="left", padx=8)

        txt_frame = ttk.Frame(self)
        txt_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.text_widget = tk.Text(txt_frame, wrap="none", font=("Courier New", F_SMALL))
        self.text_widget.pack(side="left", fill="both", expand=True)
        vsb = ttk.Scrollbar(txt_frame, orient="vertical", command=self.text_widget.yview)
        vsb.pack(side="right", fill="y")
        self.text_widget.configure(yscrollcommand=vsb.set)
        self.text_widget.insert("1.0", text)
        self.text_widget.configure(state="disabled")

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self._text)
        self.copied_var.set("Copied!")
        self.after(1500, lambda: self.copied_var.set(""))

    def _save(self):
        path = filedialog.asksaveasfilename(defaultextension=self._default_ext, parent=self)
        if not path:
            return
        try:
            with open(path, "w") as f:
                f.write(self._text)
            self.copied_var.set(f"Saved to {os.path.basename(path)}")
            self.after(2500, lambda: self.copied_var.set(""))
        except OSError as e:
            messagebox.showwarning("Can't save", str(e), parent=self)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Wildbits — Memory Map Planner")
        self.geometry("1560x950")

        self.model = MemoryModel(generation=1)
        populate_demo(self.model)

        self.addr_mode = "hex"          # "hex" | "dec"
        self.current_tool = tk.StringVar(value="inspect")
        self.current_tool.trace_add("write", lambda *a: self._refresh_tool_icons())
        self.pending_page = None        # physical page picked, awaiting a CPU slot

        # click-vs-drag bookkeeping (see PhysicalMapCanvas mouse handlers)
        self.press_xy = None
        self.press_cell = None
        self.drag_moved = False
        self.drag_rid = None
        self.drag_grab_offset = 0
        self.drag_target_start = None   # a BYTE address while dragging

        # selection: a specific 2KB cell (always), and optionally a specific
        # object id (set via the Exploded View, to disambiguate shared cells)
        self.selected_cell = None
        self.selected_object_rid = None
        self.exploded_view = None       # the currently-open ExplodedViewWindow, if any

        # PLACEHOLDER defaults per:
        # https://f256wiki.wildbitscomputing.com/index.php?title=Memory_Management
        self.slot_descriptions = [""] * CPU_SLOTS
        self.slot_descriptions[0] = (
            "Low mem registers. Kernel args. User code starts at $0300."
        )
        self.slot_descriptions[5] = (
            "Swap page - the springboard window code uses to "
            "peer into any other 8KB sections. (i.e. with FAR_PEEK, FAR_POKE)"
        )
        self.slot_descriptions[6] = "I/O pages. Too useful to change."
        self.slot_descriptions[7] = (
            "Kernel reserved - The Kernel will do its own swapping "
            "in this section. "
        )
        self.slot_desc_widgets = []

        style = ttk.Style(self)
        try:
            style.configure("Danger.TButton", foreground="#B23A2E")
        except Exception:
            pass  # some themes ignore foreground on ttk buttons; harmless

        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self._refresh_tool_icons()
        self.redraw_all()

    # -- layout -----------------------------------------------------------------

    def _build_toolbar(self):
        bar = ttk.Frame(self, padding=6)
        bar.pack(side="top", fill="x")

        ttk.Label(bar, text="Tool:", font=("TkDefaultFont", F_MED)).pack(side="left", padx=(0, 4))
        ttk.Radiobutton(bar, text="Inspect / select", value="inspect",
                         variable=self.current_tool).pack(side="left", padx=2)

        self.add_icon_btn = IconButton(bar, draw_icon_plus,
                                        command=lambda: self.current_tool.set("add"))
        self.add_icon_btn.pack(side="left", padx=(8, 2))
        self.resource_type_var = tk.StringVar(value=RESOURCE_TYPES["bitmap"]["label"])
        type_combo = ttk.Combobox(bar, textvariable=self.resource_type_var,
                                   values=RESOURCE_LABELS, state="readonly", width=17,
                                   font=("TkDefaultFont", F_SMALL))
        type_combo.pack(side="left", padx=2)
        type_combo.bind("<<ComboboxSelected>>", self.on_resource_type_change)
        ttk.Label(bar, text="size (bytes):", font=("TkDefaultFont", F_SMALL)).pack(
            side="left", padx=(4, 2))
        self.size_var = tk.StringVar(value=str(RESOURCE_TYPES["bitmap"]["size"]))
        ttk.Entry(bar, textvariable=self.size_var, width=8,
                  font=("TkDefaultFont", F_SMALL)).pack(side="left", padx=(0, 8))

        self.erase_icon_btn = IconButton(bar, draw_icon_trash,
                                          command=lambda: self.current_tool.set("erase"))
        self.erase_icon_btn.pack(side="left", padx=2)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Radiobutton(bar, text="Map to CPU slot", value="map",
                         variable=self.current_tool).pack(side="left", padx=2)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="Compact \u2193", command=self.on_compact).pack(side="left", padx=2)
        ttk.Button(bar, text="Wipe all\u2026", command=self.on_wipe_all,
                   style="Danger.TButton").pack(side="left", padx=(6, 2))
        ttk.Button(bar, text="Unlock all", command=self.on_unlock_all).pack(
            side="left", padx=(6, 2))

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        self.gen_var = tk.IntVar(value=1)
        ttk.Radiobutton(bar, text="Gen 1 (512KB)", value=1, variable=self.gen_var,
                         command=self.on_generation_change).pack(side="left", padx=2)
        ttk.Radiobutton(bar, text="Gen 2 (2MB, 2x2 banks)", value=2, variable=self.gen_var,
                         command=self.on_generation_change).pack(side="left", padx=2)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        self.hexdec_icon_btn = IconButton(
            bar, lambda c, s: draw_icon_hexdec(c, s, self.addr_mode),
            command=self.toggle_addr_mode,
        )
        self.hexdec_icon_btn.pack(side="left", padx=2)

    def _refresh_tool_icons(self):
        tool = self.current_tool.get()
        if hasattr(self, "add_icon_btn"):
            self.add_icon_btn.set_active(tool == "add")
        if hasattr(self, "erase_icon_btn"):
            self.erase_icon_btn.set_active(tool == "erase")

    def on_resource_type_change(self, event=None):
        key = RESOURCE_LABEL_TO_KEY.get(self.resource_type_var.get())
        if key:
            self.size_var.set(str(RESOURCE_TYPES[key]["size"]))

    def _build_body(self):
        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True)

        # left sidebar: scrollable (mouse wheel works while hovering it) and
        # laid out as 2 side-by-side columns, so its total height is roughly
        # halved and nothing (like the legend) ends up stuck off-screen.
        left_outer = ttk.Frame(body)
        left_outer.pack(side="left", fill="y")
        left_canvas = tk.Canvas(left_outer, highlightthickness=0, width=900)
        left_scroll = ttk.Scrollbar(left_outer, orient="vertical", command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_canvas.pack(side="left", fill="both", expand=True)
        left_scroll.pack(side="left", fill="y")
        left = ttk.Frame(left_canvas, padding=6)
        left_canvas.create_window((0, 0), window=left, anchor="nw")
        left.bind("<Configure>",
                  lambda e: left_canvas.configure(scrollregion=left_canvas.bbox("all")))

        def _wheel(event):
            delta = getattr(event, "delta", 0)
            if delta:
                left_canvas.yview_scroll(-1 if delta > 0 else 1, "units")
            elif getattr(event, "num", None) in (4, 5):
                left_canvas.yview_scroll(-1 if event.num == 4 else 1, "units")

        def _bind_wheel(_e=None):
            left_canvas.bind_all("<MouseWheel>", _wheel)
            left_canvas.bind_all("<Button-4>", _wheel)
            left_canvas.bind_all("<Button-5>", _wheel)

        def _unbind_wheel(_e=None):
            left_canvas.unbind_all("<MouseWheel>")
            left_canvas.unbind_all("<Button-4>")
            left_canvas.unbind_all("<Button-5>")

        left_canvas.bind("<Enter>", _bind_wheel)
        left_canvas.bind("<Leave>", _unbind_wheel)

        columns = ttk.Frame(left)
        columns.pack(fill="both", expand=True)
        colA = ttk.Frame(columns)
        colA.pack(side="left", anchor="n")
        colB = ttk.Frame(columns, padding=(16, 0, 0, 0))
        colB.pack(side="left", anchor="n")

        # -- column A: CPU panel + how-to blurb --
        ttk.Label(colA, text="CPU-visible 64KB (MMU slots) — lowest address at bottom",
                  font=("TkDefaultFont", F_MED, "bold")).pack(anchor="w")
        cpu_row = ttk.Frame(colA)
        cpu_row.pack(fill="x")
        self.cpu_canvas = CpuMapCanvas(cpu_row, self, width=300, height=480)
        self.cpu_canvas.pack(side="left")
        self._build_cpu_descriptions(cpu_row)

        ttk.Label(
            colA,
            text=("Pick 'Add', a type + size, then click a free 2KB cell — you'll "
                  "confirm the exact start address before it's placed. Drag an "
                  "object to relocate it (when it's alone in its cell). 'Map to "
                  "CPU slot': click a physical page, then a slot here."),
            wraplength=540, foreground="#555555", justify="left",
            font=("TkDefaultFont", F_SMALL),
        ).pack(anchor="w", pady=(8, 0))

        # -- column B: details / tools / legend --
        self._build_details(colB)
        self._build_file_dropzone(colB)
        self._build_export(colB)
        self._build_legend(colB)

        # right: physical SRAM view
        right = ttk.Frame(body, padding=6)
        right.pack(side="left", fill="both", expand=True)
        ttk.Label(right, text="Physical SRAM — banks in a 2x2 grid, 8 columns of "
                              "64KB each (lowest address at the bottom)",
                  font=("TkDefaultFont", F_MED, "bold")).pack(anchor="w")

        canvas_frame = ttk.Frame(right)
        canvas_frame.pack(fill="both", expand=True)
        self.phys_canvas = PhysicalMapCanvas(canvas_frame, self)
        vbar = ttk.Scrollbar(canvas_frame, orient="vertical",
                              command=self.phys_canvas.yview)
        hbar = ttk.Scrollbar(right, orient="horizontal",
                              command=self.phys_canvas.xview)
        self.phys_canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        self.phys_canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        hbar.pack(side="bottom", fill="x")

    def _build_cpu_descriptions(self, parent):
        """One small editable text box per CPU slot, aligned to its row on the
        CPU canvas, so the slot's purpose (IO, kernel swap page, etc.) can be
        documented right next to it."""
        total_h = self.cpu_canvas.TOP_MARGIN + CPU_SLOTS * self.cpu_canvas.CELL_H + 10
        frame = tk.Frame(parent, width=260, height=total_h)
        frame.pack(side="left", fill="y", padx=(6, 0))
        frame.pack_propagate(False)

        for slot in range(CPU_SLOTS):
            y0 = self.cpu_canvas.slot_y0(slot)
            h = self.cpu_canvas.CELL_H - 4
            txt = tk.Text(frame, wrap="word", font=("TkDefaultFont", F_TINY),
                           relief="solid", borderwidth=1)
            txt.place(x=0, y=y0, width=254, height=h)
            txt.insert("1.0", self.slot_descriptions[slot])
            if self.model.slot_fixed[slot]:
                txt.configure(background="#FFF3E0")
            txt.bind("<KeyRelease>", lambda e, s=slot: self._on_slot_desc_edit(s))
            self.slot_desc_widgets.append(txt)

    def _on_slot_desc_edit(self, slot):
        txt = self.slot_desc_widgets[slot]
        self.slot_descriptions[slot] = txt.get("1.0", "end-1c")

    def _build_details(self, parent):
        box = ttk.LabelFrame(parent, text="Details (selected 2KB cell)", padding=6)
        box.pack(fill="x", pady=(12, 0))
        self.details_var = tk.StringVar(value="Click any 2KB cell on the map "
                                                "to see its address range here.")
        ttk.Label(box, textvariable=self.details_var, wraplength=280,
                  justify="left", font=("TkDefaultFont", F_SMALL)).pack(anchor="w")

        obj_box = ttk.LabelFrame(parent, text="Object details", padding=6)
        obj_box.pack(fill="x", pady=(8, 0))
        self.object_details_var = tk.StringVar(value="n/a")
        ttk.Label(obj_box, textvariable=self.object_details_var, wraplength=280,
                  justify="left", font=("TkDefaultFont", F_SMALL)).pack(anchor="w")

        name_row = ttk.Frame(obj_box)
        name_row.pack(fill="x", pady=(6, 0))
        ttk.Label(name_row, text="Name:", font=("TkDefaultFont", F_SMALL)).pack(side="left")
        self.name_var = tk.StringVar()
        ttk.Entry(name_row, textvariable=self.name_var, width=16,
                  font=("TkDefaultFont", F_SMALL)).pack(side="left", padx=4)
        ttk.Button(name_row, text="Rename", command=self.on_rename_object).pack(side="left")

        self.lock_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(obj_box, text="Locked (protected from 'Wipe all')",
                         variable=self.lock_var, command=self.on_toggle_lock).pack(
            anchor="w", pady=(4, 0))

        ttk.Button(obj_box, text="Open exploded view for this cell",
                   command=self.on_open_exploded).pack(anchor="w", pady=(6, 4))

        resize_row = ttk.Frame(obj_box)
        resize_row.pack(fill="x", pady=(2, 0))
        ttk.Label(resize_row, text="New size (bytes):",
                  font=("TkDefaultFont", F_SMALL)).pack(side="left")
        self.resize_var = tk.StringVar()
        ttk.Entry(resize_row, textvariable=self.resize_var, width=9,
                  font=("TkDefaultFont", F_SMALL)).pack(side="left", padx=4)
        ttk.Button(resize_row, text="Apply", command=self.on_resize_object).pack(side="left")

        move_row = ttk.Frame(obj_box)
        move_row.pack(fill="x", pady=(4, 0))
        ttk.Label(move_row, text="Move to address:",
                  font=("TkDefaultFont", F_SMALL)).pack(side="left")
        self.move_var = tk.StringVar()
        ttk.Entry(move_row, textvariable=self.move_var, width=9,
                  font=("TkDefaultFont", F_SMALL)).pack(side="left", padx=4)
        ttk.Button(move_row, text="Move", command=self.on_move_object).pack(side="left")

        ttk.Button(obj_box, text="Erase this object", command=self.on_erase_object).pack(
            anchor="w", pady=(6, 0))

    def _build_file_dropzone(self, parent):
        box = ttk.LabelFrame(parent, text="Add file as object", padding=6)
        box.pack(fill="x", pady=(10, 0))
        hint = ("Drag a file here or click to browse\u2026" if HAS_DND else
                "Click to browse for a file\u2026")
        self.dropzone = tk.Label(box, text=hint, relief="ridge", borderwidth=2,
                                  background="#FAFAFA", padx=10, pady=16,
                                  font=("TkDefaultFont", F_MED), cursor="hand2")
        self.dropzone.pack(fill="x")
        self.dropzone.bind("<Button-1>", lambda e: self.on_browse_file())
        if HAS_DND:
            try:
                self.dropzone.drop_target_register(DND_FILES)
                self.dropzone.dnd_bind("<<Drop>>", self._on_file_dropped)
            except Exception:
                pass
        ttk.Label(box, text="The file's real name and byte size are used to build "
                            "a placeholder binary object on the map.",
                  font=("TkDefaultFont", F_TINY), foreground="#555555",
                  wraplength=280).pack(anchor="w", pady=(4, 0))

    def _build_export(self, parent):
        box = ttk.LabelFrame(parent, text="Export placed objects", padding=6)
        box.pack(fill="x", pady=(10, 0))
        ttk.Button(box, text="Plain list (name, start, size)",
                   command=self.on_export_list).pack(fill="x", pady=1)
        ttk.Button(box, text="llvm-mos EMBED(...)",
                   command=self.on_export_llvm_mos).pack(fill="x", pady=1)
        ttk.Button(box, text="oscar64 #pragma section/region",
                   command=self.on_export_oscar64).pack(fill="x", pady=1)

    def _build_legend(self, parent):
        box = ttk.LabelFrame(parent, text="Legend", padding=6)
        box.pack(fill="x", pady=(14, 0))
        entries = [("System (registers / IO / swap, reserved)", IO_COLOR),
                   ("Main code area (reserved)", CODE_COLOR),
                   ("free", FREE_COLOR)]
        entries += [(v["label"], v["color"]) for v in RESOURCE_TYPES.values()]
        for label, color in entries:
            row = tk.Frame(box)
            row.pack(fill="x", pady=1)
            swatch = tk.Canvas(row, width=16, height=16, highlightthickness=1,
                                highlightbackground="#888888")
            swatch.pack(side="left", padx=(0, 6))
            swatch.create_rectangle(0, 0, 16, 16, fill=color, outline="")
            ttk.Label(row, text=label, font=("TkDefaultFont", F_TINY)).pack(side="left")

        row = tk.Frame(box)
        row.pack(fill="x", pady=1)
        swatch = tk.Canvas(row, width=16, height=16, highlightthickness=1,
                            highlightbackground="#888888")
        swatch.pack(side="left", padx=(0, 6))
        draw_rainbow(swatch, 0, 0, 16, 16)
        ttk.Label(row, text="2+ objects packed here — open Exploded View",
                  font=("TkDefaultFont", F_TINY)).pack(side="left")

        ttk.Label(box, text="Each object gets a unique hatch pattern. A lighter "
                            "band = unused space around/after it in that cell. "
                            "A small padlock badge = object is locked (protected "
                            "from 'Wipe all').",
                  font=("TkDefaultFont", F_TINY), foreground="#555555",
                  wraplength=280).pack(anchor="w", pady=(6, 0))

    def _build_statusbar(self):
        self.status_var = tk.StringVar(value="Ready.")
        bar = ttk.Frame(self, relief="sunken")
        bar.pack(side="bottom", fill="x")
        ttk.Label(bar, textvariable=self.status_var, anchor="w",
                  font=("TkDefaultFont", F_SMALL), padding=(6, 3)).pack(fill="x")

    # -- interaction --------------------------------------------------------------

    def set_status(self, text):
        self.status_var.set(text if text else "Ready.")

    def select_cell(self, page, subcell):
        self.selected_cell = (page, subcell)
        self._refresh_selection_details()

    def _resolve_selected_rid(self):
        """Which specific object are the Details controls (resize/move/erase)
        currently talking about? Prefer an explicit Exploded-View pick;
        otherwise fall back to the selected cell IF it has exactly one
        occupant (unambiguous)."""
        if self.selected_object_rid is not None and self.selected_object_rid in self.model.resources:
            return self.selected_object_rid
        if self.selected_cell is not None:
            page, subcell = self.selected_cell
            ci = page * SUBCELLS_PER_PAGE + subcell
            return self.model.cell_sole_resource_id(ci)
        return None

    def _refresh_selection_details(self):
        """Repaints both the 2KB-cell Details panel and the Object details
        panel. Called after selection changes AND after anything that could
        change what's under that cell (placing, erasing, moving, resizing,
        compacting, hex/dec toggling)."""
        if self.selected_cell is None:
            self.details_var.set("Click any 2KB cell on the map "
                                  "to see its address range here.")
        else:
            page, subcell = self.selected_cell
            ci = page * SUBCELLS_PER_PAGE + subcell
            lo, hi = self.phys_canvas.cell_addr_range(page, subcell)
            self.details_var.set(
                f"Physical page {page}, 2KB cell {subcell}\n"
                f"Start: {fmt_addr(lo, self.addr_mode)}\n"
                f"End:   {fmt_addr(hi, self.addr_mode)}\n"
                f"Size:  {CLICK_UNIT} bytes\n"
                f"Contents: {self.model.cell_content_label(ci)}"
            )

        rid = self._resolve_selected_rid()
        if rid is None:
            occ_count = 0
            if self.selected_cell is not None:
                page, subcell = self.selected_cell
                ci = page * SUBCELLS_PER_PAGE + subcell
                if self.model.cell_reserved(ci) is None:
                    occ_count = len(self.model.cell_occupants(ci))
            if occ_count >= 2:
                self.object_details_var.set(
                    f"{occ_count} objects packed in this cell.\n"
                    f"Use 'Open exploded view' above to inspect/select one."
                )
            else:
                self.object_details_var.set(
                    "n/a — no single object is associated with the current selection."
                )
            self.resize_var.set("")
            self.move_var.set("")
            self.name_var.set("")
            self.lock_var.set(False)
        else:
            r = self.model.resources[rid]
            obj_start = r["start"]
            obj_end = obj_start + r["size"] - 1
            lock_tag = "  [LOCKED]" if r.get("locked") else ""
            self.object_details_var.set(
                f'{r["name"]}  ({r["label"]}){lock_tag}\n'
                f"Start: {fmt_addr(obj_start, self.addr_mode)}\n"
                f"End:   {fmt_addr(obj_end, self.addr_mode)}\n"
                f'Size:  {r["size"]} bytes'
            )
            self.resize_var.set(str(r["size"]))
            self.move_var.set(fmt_addr(obj_start, self.addr_mode))
            self.name_var.set(r["name"])
            self.lock_var.set(bool(r.get("locked")))

        self._refresh_exploded_view()

    def _refresh_exploded_view(self):
        """Keeps any open Exploded View window in sync with the model —
        called every time _refresh_selection_details() runs, i.e. after
        every place/erase/move/resize/compact. Auto-closes it if the cell
        it was showing no longer has 2+ objects to explode."""
        win = self.exploded_view
        if win is None:
            return
        try:
            exists = bool(win.winfo_exists())
        except Exception:
            exists = False
        if not exists:
            self.exploded_view = None
            return
        if self.model.cell_reserved(win.cell_index) is not None:
            win.destroy()
            self.exploded_view = None
            return
        if len(self.model.cell_occupants(win.cell_index)) < 2:
            win.destroy()
            self.exploded_view = None
            return
        win.redraw()

    def on_rename_object(self):
        rid = self._resolve_selected_rid()
        if rid is None:
            messagebox.showinfo("No object selected", "Select an object first "
                                 "(click its cell, or pick it in the Exploded View).")
            return
        new_name = self.name_var.get().strip()
        if not new_name:
            messagebox.showwarning("Invalid name", "Enter a name.")
            return
        ok, err = self.model.rename_resource(rid, new_name)
        if not ok:
            messagebox.showwarning("Can't rename", err)
        self._refresh_selection_details()
        self.redraw_all()

    def on_toggle_lock(self):
        rid = self._resolve_selected_rid()
        if rid is None:
            self.lock_var.set(False)
            messagebox.showinfo("No object selected", "Select an object first "
                                 "(click its cell, or pick it in the Exploded View).")
            return
        self.model.toggle_lock(rid, locked=self.lock_var.get())
        self._refresh_selection_details()
        self.redraw_all()

    def on_resize_object(self):
        rid = self._resolve_selected_rid()
        if rid is None:
            messagebox.showinfo("No object selected", "Select an object first "
                                 "(click its cell, or pick it in the Exploded View).")
            return
        try:
            new_size = int(self.resize_var.get())
        except ValueError:
            messagebox.showwarning("Invalid size", "Enter a whole number of bytes.")
            return
        if new_size <= 0:
            messagebox.showwarning("Invalid size", "Size must be a positive number of bytes.")
            return
        if not self.model.resize_resource(rid, new_size):
            messagebox.showwarning(
                "Can't resize",
                "Not enough free space to grow this object in place. Try "
                "'Compact', move a neighbor, or choose a smaller size."
            )
        self._refresh_selection_details()
        self.redraw_all()

    def on_move_object(self):
        rid = self._resolve_selected_rid()
        if rid is None:
            messagebox.showinfo("No object selected", "Select an object first "
                                 "(click its cell, or pick it in the Exploded View).")
            return
        try:
            new_addr = parse_addr_input(self.move_var.get(), self.addr_mode)
        except ValueError:
            messagebox.showwarning("Invalid address", "Enter a valid address, "
                                    "e.g. $12000 or a plain number.")
            return
        if not self.model.move_resource(rid, new_addr):
            messagebox.showwarning(
                "Can't move",
                "That address is out of range, or overlaps a reserved zone "
                "or another object."
            )
        self._refresh_selection_details()
        self.redraw_all()

    def on_erase_object(self):
        rid = self._resolve_selected_rid()
        if rid is None:
            messagebox.showinfo("No object selected", "Select an object first "
                                 "(click its cell, or pick it in the Exploded View).")
            return
        self.model.remove_resource(rid)
        self.selected_object_rid = None
        self._refresh_selection_details()
        self.redraw_all()

    def on_open_exploded(self):
        if self.selected_cell is None:
            messagebox.showinfo("No cell selected", "Click a cell on the map first.")
            return
        page, subcell = self.selected_cell
        ci = page * SUBCELLS_PER_PAGE + subcell
        if self.model.cell_reserved(ci) is not None:
            messagebox.showinfo("Reserved cell", "This cell is a reserved system "
                                 "zone — nothing to explode.")
            return
        occ = self.model.cell_occupants(ci)
        if len(occ) < 2:
            messagebox.showinfo("Nothing to explode", "This cell holds 0 or 1 "
                                 "objects — no packing to inspect.")
            return
        if self.exploded_view is not None:
            try:
                if self.exploded_view.winfo_exists():
                    self.exploded_view.destroy()
            except Exception:
                pass
        self.exploded_view = ExplodedViewWindow(self, ci)

    def on_compact(self):
        skipped = self.model.compact_all()
        self.pending_page = None
        self._refresh_selection_details()
        self.redraw_all()
        if skipped:
            self.set_status(f"Compacted — {len(skipped)} object(s) didn't fit and "
                             f"were left in place.")
        else:
            self.set_status("Compacted all placed objects toward the lowest free addresses.")

    def on_wipe_all(self):
        if not self.model.resources:
            messagebox.showinfo("Nothing to wipe", "There are no placed objects.")
            return
        locked_count = sum(1 for r in self.model.resources.values() if r.get("locked"))
        unlocked_count = len(self.model.resources) - locked_count
        if unlocked_count == 0:
            messagebox.showinfo("Nothing to wipe", "Every placed object is locked — "
                                 "nothing would be removed.")
            return
        note = (f" ({locked_count} locked object(s) will be kept.)" if locked_count else "")
        if not messagebox.askyesno(
            "Wipe all objects?",
            f"This will permanently remove {unlocked_count} placed object(s) "
            f"(bitmaps, sprites, files, etc.).{note} The reserved system/code "
            f"zones are never affected. This can't be undone.\n\nContinue?",
        ):
            return
        removed, kept = self.model.wipe_all()
        if self.selected_object_rid in removed:
            self.selected_object_rid = None
        self.pending_page = None
        self._refresh_selection_details()
        self.redraw_all()
        if kept:
            self.set_status(f"Wiped {len(removed)} object(s); {len(kept)} locked "
                             f"object(s) were kept.")
        else:
            self.set_status(f"Wiped {len(removed)} object(s).")

    def on_unlock_all(self):
        unlocked = self.model.unlock_all()
        self._refresh_selection_details()
        self.redraw_all()
        if unlocked:
            self.set_status(f"Unlocked {len(unlocked)} object(s).")
        else:
            self.set_status("No locked objects to unlock.")

    def on_export_list(self):
        ExportWindow(self, "Export — plain list", self.model.export_list_text(), ".txt")

    def on_export_llvm_mos(self):
        ExportWindow(self, "Export — llvm-mos EMBED", self.model.export_llvm_mos(), ".h")

    def on_export_oscar64(self):
        ExportWindow(self, "Export — oscar64 pragma", self.model.export_oscar64(), ".c")

    def toggle_addr_mode(self):
        self.addr_mode = "dec" if self.addr_mode == "hex" else "hex"
        self.hexdec_icon_btn.redraw()
        self._refresh_selection_details()
        self.redraw_all()

    def on_generation_change(self):
        self.model.set_generation(self.gen_var.get())
        self.pending_page = None
        self.selected_cell = None
        self.selected_object_rid = None
        self._refresh_selection_details()
        self.redraw_all()

    def on_physical_click(self, page, subcell):
        """Runs for a plain (non-drag) click, based on the active tool."""
        tool = self.current_tool.get()
        ci = page * SUBCELLS_PER_PAGE + subcell
        start_addr = page * PAGE_SIZE + subcell * CLICK_UNIT

        if tool == "inspect":
            return  # selection/details handled separately in select_cell()

        if tool == "erase":
            if self.model.cell_reserved(ci) is not None:
                messagebox.showinfo("Reserved", "This is a reserved system zone.")
                return
            occ = self.model.cell_occupants(ci)
            if len(occ) >= 2:
                messagebox.showinfo(
                    "Multiple objects here",
                    "This cell holds multiple objects. Open the Exploded View "
                    "(Details panel) and use 'Erase this object' to remove a "
                    "specific one."
                )
                return
            self.model.remove_at_cell(ci)
            self._refresh_selection_details()
            return

        if tool == "map":
            self.pending_page = page
            self.set_status(
                f"Picked physical page {page}. Now click a CPU slot on the left "
                f"to bank-switch it in."
            )
            return

        if tool == "add":
            key = RESOURCE_LABEL_TO_KEY.get(self.resource_type_var.get())
            if key is None:
                messagebox.showwarning("Pick a type", "Choose a resource type "
                                        "from the dropdown first.")
                return
            try:
                size_bytes = int(self.size_var.get())
            except ValueError:
                messagebox.showwarning("Invalid size", "Enter a whole number of "
                                        "bytes for the size.")
                return
            if size_bytes <= 0:
                messagebox.showwarning("Invalid size", "Size must be a positive "
                                        "number of bytes.")
                return
            info = RESOURCE_TYPES[key]

            def confirm(addr):
                rid, err = self.model.add_resource(key, addr, size_bytes=size_bytes)
                if err:
                    messagebox.showwarning("Can't place resource", err)
                else:
                    self.selected_object_rid = rid
                self._refresh_selection_details()
                self.redraw_all()

            PlacementDialog(self, "Place object", info["label"], size_bytes,
                             start_addr, confirm)

    def on_cpu_slot_click(self, slot):
        if self.pending_page is None:
            self.set_status(
                "Select 'Map to CPU slot', click a physical page first, "
                "then click a slot."
            )
            return
        ok, err = self.model.map_slot(slot, self.pending_page)
        if not ok:
            messagebox.showwarning("Can't map slot", err)
        self.pending_page = None
        self.redraw_all()

    # -- file drop / browse -----------------------------------------------------

    def on_browse_file(self):
        path = filedialog.askopenfilename(title="Choose a file to add as an object")
        if path:
            self._handle_dropped_file(path)

    def _on_file_dropped(self, event):
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        if paths:
            self._handle_dropped_file(paths[0])

    def _handle_dropped_file(self, path):
        try:
            size = os.path.getsize(path)
        except OSError as e:
            messagebox.showwarning("Can't read file", str(e))
            return
        name = os.path.basename(path)
        if size <= 0:
            messagebox.showwarning("Empty file", f'"{name}" is empty (0 bytes).')
            return
        if self.selected_cell is not None:
            page, subcell = self.selected_cell
            suggested = page * PAGE_SIZE + subcell * CLICK_UNIT
        else:
            suggested = 0x10000

        def confirm(addr):
            rid, err = self.model.add_file_resource(name, size, addr)
            if err:
                messagebox.showwarning("Can't place file", err)
            else:
                self.selected_object_rid = rid
            self._refresh_selection_details()
            self.redraw_all()

        PlacementDialog(self, "Place file", name, size, suggested, confirm)

    # -- drawing --------------------------------------------------------------------

    def redraw_all(self):
        self.cpu_canvas.redraw()
        self.phys_canvas.redraw()


class PlacementDialog(tk.Toplevel):
    """Confirms the exact start address (typed, editable) before an object
    or dropped file is actually committed to the map."""

    def __init__(self, app, title, label_text, size_bytes, suggested_start, on_confirm):
        super().__init__(app)
        self.app = app
        self.on_confirm = on_confirm
        self.title(title)
        self.resizable(False, False)

        ttk.Label(self, text=label_text, font=("TkDefaultFont", F_MED, "bold")).pack(
            padx=14, pady=(14, 4), anchor="w")
        ttk.Label(self, text=f"Size: {size_bytes} bytes",
                  font=("TkDefaultFont", F_SMALL)).pack(padx=14, anchor="w")

        row = ttk.Frame(self)
        row.pack(padx=14, pady=(10, 4), fill="x")
        ttk.Label(row, text="Start address:", font=("TkDefaultFont", F_SMALL)).pack(side="left")
        self.addr_var = tk.StringVar(value=fmt_addr(suggested_start, app.addr_mode))
        entry = ttk.Entry(row, textvariable=self.addr_var, width=14,
                           font=("TkDefaultFont", F_SMALL))
        entry.pack(side="left", padx=6)
        entry.focus_set()
        entry.select_range(0, "end")

        btn_row = ttk.Frame(self)
        btn_row.pack(padx=14, pady=(6, 14), anchor="e")
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(btn_row, text="Place", command=self._confirm).pack(side="right")

        self.bind("<Return>", lambda e: self._confirm())
        self.bind("<Escape>", lambda e: self.destroy())
        self.transient(app)
        self.grab_set()

    def _confirm(self):
        try:
            addr = parse_addr_input(self.addr_var.get(), self.app.addr_mode)
        except ValueError:
            messagebox.showwarning("Invalid address",
                                    "Enter a valid address, e.g. $12000 or a plain number.",
                                    parent=self)
            return
        self.on_confirm(addr)
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
#!/usr/bin/env python3
"""
Wildbits Jr2/K2 Uploader v0.6
------------------------------
A small windowed front-end for pweingar/FoenixMgr
(https://github.com/pweingar/FoenixMgr) -- drives your own already-working
`fnxmgr.py` install via subprocess rather than reimplementing the debug
port's wire protocol, so it inherits whatever your install already gets
right for your hardware/cable/OS.

Point it at your FoenixMgr folder once (Browse... below); that path is
remembered in ~/.wildbits_uploader_prefs.json so you won't need to pick
it again next time.

Four things this app does:

  1. Test Connection            -- fnxmgr.py --revision
  2. Send & Run (.pgz/.pgx/.pgZ) -- fnxmgr.py --run-pgz / --run-pgx
  3. Copy to SD Card (F256 only) -- fnxmgr.py --copy
  4. Get Machine Info (experimental)
     The debug port can't see memory-mapped FPGA/IO registers directly
     (confirmed: those are only visible to code actually running on the
     CPU) -- so this builds a tiny hand-assembled probe program
     (wildbits_probe.py), launches it via `fnxmgr.py --run-pgz`, waits
     for the machine to boot and run it, then reads the results back via
     `fnxmgr.py --dump`. Decodes the machine model/generation, a
     "hardware build info" block, and the MicroKernel's own info string,
     reverse-engineered from SuperBASIC's boot-banner source -- see
     wildbits_info.py and wildbits_probe.py for the full explanation and
     caveats. Causes a machine reset (as does every operation here).

Requires only the Python standard library plus your existing FoenixMgr
install (which needs pyserial). tkinterdnd2 is optional, for
drag-and-drop.
"""

import math
import os
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import wildbits_cli as cli
import wildbits_prefs as prefs
import wildbits_probe as probe
import wildbits_info as info
import wildbits_flash as flash

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_AVAILABLE = False


BASE_CLASS = TkinterDnD.Tk if DND_AVAILABLE else tk.Tk

SECTOR_COUNT = 64  # 0x00-0x3F
SECTOR_CHOICES = [f"{i:02X}" for i in range(SECTOR_COUNT)]

# Sector 0 (0x00) is reserved: it is never touched, written, erased,
# moved, or added to. It stays visible in the map and list, but every
# interaction that would modify it is disabled.
PROTECTED_SECTOR = 0
SECTOR_CHOICES_EDITABLE = [f"{i:02X}" for i in range(SECTOR_COUNT) if i != PROTECTED_SECTOR]

MAP_COLS = 8
MAP_ROWS = SECTOR_COUNT // MAP_COLS
MAP_CELL_W = 96
MAP_CELL_H = 58

MAP_COLOR_FREE = "#f2f2f2"
MAP_OUTLINE_FREE = "#bbbbbb"
MAP_COLOR_OCCUPIED = "#bcd9ea"
MAP_OUTLINE_OCCUPIED = "#5b8bab"
MAP_COLOR_CONFLICT = "#ffd9b3"
MAP_OUTLINE_CONFLICT = "#c0392b"
MAP_COLOR_DISABLED = "#e4e4e4"
MAP_COLOR_PENDING_ERASE = "#e3d1f0"
MAP_OUTLINE_PENDING_ERASE = "#7d3c98"
MAP_COLOR_RESERVED = "#dcdcdc"
MAP_OUTLINE_RESERVED = "#8a8a8a"

def estimate_pgz_8kb_blocks(pgz_path):
    """Estimate the number of 8KB blocks required for a given PGZ file."""
    try:
        file_size = os.path.getsize(pgz_path)
        # Account for typical overhead/block headers (~8KB chunk alignment)
        blocks = math.ceil(file_size / 8192)
        return max(1, blocks)
    except OSError:
        return 1


def _format_op_entry(entry):
    """Render one preference-backed operation-log entry as a single
    human-readable line for the History tab."""
    op = entry.get("op", "?")
    at = entry.get("at", "?")
    detail = []
    if op == "convert_pgz":
        detail.append(f"pgz={entry.get('pgz', '?')}")
        detail.append(f"name={entry.get('name', '?')}")
        detail.append(f"start=0x{entry.get('start_sector', '?')}")
        sectors = ",".join(entry.get("sectors", []))
        detail.append(f"blocks={entry.get('count', '?')}" + (f" [{sectors}]" if sectors else ""))
    elif op == "add_block":
        detail.append(f"file={entry.get('file', '?')}")
        detail.append(f"sector=0x{entry.get('sector', '?')}")
    elif op == "delete_blocks":
        sectors = ",".join(entry.get("sectors", []))
        detail.append(f"count={entry.get('count', '?')}" + (f" [{sectors}]" if sectors else ""))
    else:
        detail.extend(f"{k}={v}" for k, v in entry.items() if k not in ("op", "at"))
    suffix = ("  " + "; ".join(detail)) if detail else ""
    return f"[{at}] {op}{suffix}"


def _disable_combobox_wheel(combo):
    """A ttk.Combobox has its own built-in mouse-wheel handling that
    cycles its value on scroll whenever the cursor happens to be over it
    -- with a readonly sector dropdown that means an incidental scroll
    (e.g. scrolling the block list or map past it) silently reassigns
    that row to a different sector. Binding a no-op that returns "break"
    here takes priority over that built-in behavior and stops the event
    from reaching it, without affecting normal scrolling anywhere else."""
    def _ignore_wheel(event):
        return "break"
    combo.bind("<MouseWheel>", _ignore_wheel)
    combo.bind("<Button-4>", _ignore_wheel)
    combo.bind("<Button-5>", _ignore_wheel)


class ConvertPgzDialog(tk.Toplevel):
    """Dialog to pick the start block (with an 8KB-block estimate) plus
    the -name / -desc metadata pgz2flash stores in the flash KUP. On
    confirm calls on_confirm(sector_int, name, desc)."""

    def __init__(self, master, pgz_path, default_sector, on_confirm):
        super().__init__(master)
        self.on_confirm = on_confirm
        self.title("Convert PGZ via pgz2flash")
        self.resizable(False, False)

        estimated_blocks = estimate_pgz_8kb_blocks(pgz_path)

        ttk.Label(self, text="Source PGZ File:", font=("TkDefaultFont", 9, "bold")).pack(
            anchor="w", padx=14, pady=(14, 2))
        ttk.Label(self, text=os.path.basename(pgz_path)).pack(anchor="w", padx=14)

        ttk.Label(
            self,
            text=f"Estimated 8KB Blocks Required: ~{estimated_blocks} block(s)",
            foreground="#0055aa", font=("TkDefaultFont", 9, "bold")
        ).pack(anchor="w", padx=14, pady=(8, 8))

        row = ttk.Frame(self)
        row.pack(fill="x", padx=14, pady=(0, 4))
        ttk.Label(row, text="Starting Sector (-onboard):").pack(side="left")
        self.sector_var = tk.StringVar(value=f"{default_sector:02X}")
        sector_combo = ttk.Combobox(row, textvariable=self.sector_var, values=SECTOR_CHOICES_EDITABLE,
                                     width=5, state="readonly")
        sector_combo.pack(side="left", padx=(6, 0))
        _disable_combobox_wheel(sector_combo)
        ttk.Label(
            row, text="  (the 8KB block where the program is installed)",
            foreground="#777", font=("TkDefaultFont", 8)).pack(side="left")

        name_row = ttk.Frame(self)
        name_row.pack(fill="x", padx=14, pady=(8, 4))
        ttk.Label(name_row, text="Name (-name):").pack(side="left")
        self.name_var = tk.StringVar(value=os.path.splitext(os.path.basename(pgz_path))[0])
        ttk.Entry(name_row, textvariable=self.name_var, width=32).pack(side="left", padx=(6, 0))
        ttk.Label(name_row, text="  (shown by 'lsf'; run it as /name)",
                  foreground="#777", font=("TkDefaultFont", 8)).pack(side="left")

        desc_row = ttk.Frame(self)
        desc_row.pack(fill="x", padx=14, pady=(0, 4))
        ttk.Label(desc_row, text="Description (-desc):").pack(side="left")
        self.desc_var = tk.StringVar(value="")
        ttk.Entry(desc_row, textvariable=self.desc_var, width=32).pack(side="left", padx=(6, 0))

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=14, pady=(14, 14))
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btn_row, text="Convert & Map", command=self._confirm).pack(side="right", padx=(0, 6))

        self.bind("<Return>", lambda e: self._confirm())
        self.bind("<Escape>", lambda e: self.destroy())
        self.transient(master)
        self.grab_set()

    def _confirm(self):
        sector = int(self.sector_var.get(), 16)
        name = self.name_var.get().strip()
        desc = self.desc_var.get().strip()
        if sector == PROTECTED_SECTOR:
            messagebox.showwarning("Sector reserved",
                                   f"Sector 0x{sector:02X} is the boot block and is "
                                   "reserved -- pick another sector.")
            return
        if not name:
            messagebox.showwarning("Name required",
                                   "pgz2flash needs a -name for the program (shown by 'lsf').")
            return
        self.on_confirm(sector, name, desc)
        self.destroy()
        
class DropZone(tk.Frame):
    """A reusable drag-and-drop + browse target for one file at a time."""

    def __init__(self, master, hint_text, on_file_chosen, filetypes, **kw):
        super().__init__(master, bg="#eef2f7", highlightthickness=2,
                          highlightbackground="#9fb3c8", highlightcolor="#9fb3c8", **kw)
        self.on_file_chosen = on_file_chosen
        self.filetypes = filetypes
        self.hint_text = hint_text

        self.label = tk.Label(
            self, text=hint_text, bg="#eef2f7", fg="#33475b",
            justify="center", pady=14, font=("TkDefaultFont", 10)
        )
        self.label.pack(fill="x", expand=True)

        if DND_AVAILABLE:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
            self.label.drop_target_register(DND_FILES)
            self.label.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop(self, event):
        path = event.data
        if path.startswith("{") and path.endswith("}"):
            path = path[1:-1]
        self.on_file_chosen(path)

    def browse(self):
        path = filedialog.askopenfilename(title="Choose a file", filetypes=self.filetypes)
        if path:
            self.on_file_chosen(path)

    def set_selected(self, path):
        self.label.config(text=os.path.basename(path) if path else self.hint_text)


class AddBlockDialog(tk.Toplevel):
    """Asks which sector a newly-added file should go to, defaulting to
    the first free one. Confirming calls on_confirm(sector_int)."""

    def __init__(self, master, file_path, default_sector, on_confirm):
        super().__init__(master)
        self.on_confirm = on_confirm
        self.title("Add Block")
        self.resizable(False, False)

        ttk.Label(self, text="File:", font=("TkDefaultFont", 9, "bold")).pack(
            anchor="w", padx=14, pady=(14, 2))
        ttk.Label(self, text=os.path.basename(file_path)).pack(anchor="w", padx=14)
        ttk.Label(self, text=file_path, foreground="#777",
                  font=("TkDefaultFont", 8), wraplength=360).pack(anchor="w", padx=14, pady=(0, 10))

        row = ttk.Frame(self)
        row.pack(fill="x", padx=14, pady=(0, 4))
        ttk.Label(row, text="Sector:").pack(side="left")
        self.sector_var = tk.StringVar(value=f"{default_sector:02X}")
        sector_combo = ttk.Combobox(row, textvariable=self.sector_var, values=SECTOR_CHOICES_EDITABLE,
                                     width=5, state="readonly")
        sector_combo.pack(side="left", padx=(6, 0))
        _disable_combobox_wheel(sector_combo)
        ttk.Label(row, text="  (any sector -- conflicts with an existing block\n"
                             "  are allowed, just flagged in red)",
                  foreground="#777", font=("TkDefaultFont", 8), justify="left").pack(side="left")

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=14, pady=(14, 14))
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btn_row, text="Add", command=self._confirm).pack(side="right", padx=(0, 6))

        self.bind("<Return>", lambda e: self._confirm())
        self.bind("<Escape>", lambda e: self.destroy())
        self.transient(master)
        self.grab_set()

    def _confirm(self):
        sector = int(self.sector_var.get(), 16)
        if sector == PROTECTED_SECTOR:
            messagebox.showwarning("Sector reserved",
                                   f"Sector 0x{sector:02X} is the boot block and is "
                                   "reserved -- pick another sector.")
            return
        self.on_confirm(sector)
        self.destroy()


class WildbitsUploaderApp(BASE_CLASS):
    def __init__(self):
        super().__init__()
        self.title("Wildbits Uploader v0.6")
        self.geometry("1500x880")
        self.minsize(1000, 560)

        self.foenixmgr_dir = tk.StringVar(value=prefs.get_pref("foenixmgr_dir", ""))
        self.script_path = ""   # resolved fnxmgr.py path, updated by _resolve_script()

        self.port_var = tk.StringVar(value=prefs.get_pref("last_port", ""))
        self.boot_wait_var = tk.StringVar(value=str(info.DEFAULT_BOOT_WAIT_SECONDS))

        self.pgfile_path = tk.StringVar(value="")
        self.sdfile_path = tk.StringVar(value="")

        # Update Flash tab state
        self.firmware_dir = tk.StringVar(value=prefs.get_pref("firmware_release_dir", ""))
        self.csv_path = tk.StringVar(value="")
        self.csv_rows = []          # list of wildbits_flash.BulkCsvRow, current CSV load
        self.row_check_vars = []    # parallel list of tk.BooleanVar, one per csv_rows entry
        self.row_delete_vars = []   # parallel list of tk.BooleanVar, "mark for deletion"
        self.row_sector_vars = []   # parallel list of tk.StringVar (hex "00".."3F"), editable sector
        self.row_widgets_frame = None  # scrollable frame holding the row checkboxes
        self._map_drag_row_index = None  # row index currently being dragged on the flash map, if any

        # pgz2flash conversion state
        self.pgz2flash_dir = tk.StringVar(value=prefs.get_pref("pgz2flash_dir", ""))
        self.placement_stack = []       # stack of [BulkCsvRow, ...] added by each Convert
        self._pgz_staging = None        # lazily-created staging dir for generated blocks

        self._busy = False

        self._build_widgets()
        self._resolve_script(startup=True)

        self._load_startup_state()

    # -- UI construction --------------------------------------------------

    def _build_widgets(self):
        # Everything lives inside a scrollable outer canvas, so no matter
        # how tall/wide the stacked content gets (the Update Flash tab's
        # block list + live map in particular need real estate) or how
        # small the screen/window is, the user can always scroll to
        # reach every part of the window rather than needing to resize it.
        outer_canvas = tk.Canvas(self, highlightthickness=0)
        self.outer_canvas = outer_canvas
        outer_vscroll = ttk.Scrollbar(self, orient="vertical", command=outer_canvas.yview)
        outer_hscroll = ttk.Scrollbar(self, orient="horizontal", command=outer_canvas.xview)
        outer_canvas.configure(yscrollcommand=outer_vscroll.set, xscrollcommand=outer_hscroll.set)
        outer_hscroll.pack(side="bottom", fill="x")
        outer_canvas.pack(side="left", fill="both", expand=True)
        outer_vscroll.pack(side="right", fill="y")

        content = ttk.Frame(outer_canvas)
        content_window = outer_canvas.create_window((0, 0), window=content, anchor="nw")
        self._min_content_width = None  # captured once content reaches its natural size

        def _sync_content_size(event=None):
            # Stretch to fill the viewport when content is narrower than
            # the window (nicer for the simpler stacked panels), but
            # never let it be forced *below* its true natural minimum
            # width -- otherwise the block list + map area would just
            # get silently compressed instead of triggering horizontal
            # scroll. We capture that minimum once, the first time it's
            # available: re-querying winfo_reqwidth() after the canvas
            # has already forced a narrower width tends to report the
            # already-compressed size back, not the original natural
            # one, which would create a shrink-only feedback loop.
            if self._min_content_width is None:
                content.update_idletasks()
                natural = content.winfo_reqwidth()
                if natural > 1:
                    self._min_content_width = natural
            min_width = self._min_content_width or content.winfo_reqwidth()
            canvas_width = outer_canvas.winfo_width()
            outer_canvas.itemconfig(content_window, width=max(canvas_width, min_width))
            outer_canvas.configure(scrollregion=outer_canvas.bbox("all"))

        content.bind("<Configure>", _sync_content_size)
        outer_canvas.bind("<Configure>", _sync_content_size)

        self._bind_outer_wheel()

        header = ttk.Frame(content)
        header.pack(fill="x", padx=14, pady=(12, 6))
        ttk.Label(header, text="Wildbits Uploader v0.6",
                  font=("TkDefaultFont", 15, "bold")).pack(side="left")
        ttk.Label(header, text="   A front-end for your own FoenixMgr install",
                  foreground="#555").pack(side="left")

        # --- FoenixMgr folder panel (shared across tabs) ---
        folderfrm = ttk.LabelFrame(content, text="FoenixMgr install")
        folderfrm.pack(fill="x", padx=14, pady=(0, 8))
        row0 = ttk.Frame(folderfrm)
        row0.pack(fill="x", padx=10, pady=8)
        ttk.Label(row0, text="Folder:").pack(side="left")
        self.folder_label = ttk.Label(row0, textvariable=self.foenixmgr_dir,
                                       foreground="#333", wraplength=560, justify="left")
        self.folder_label.pack(side="left", fill="x", expand=True, padx=(6, 10))
        ttk.Button(row0, text="Browse...", command=self._on_browse_foenixmgr_dir).pack(side="right")
        self.script_status_label = ttk.Label(folderfrm, text="", foreground="#777")
        self.script_status_label.pack(anchor="w", padx=10, pady=(0, 8))

        # --- Connection panel (shared across tabs) ---
        connfrm = ttk.LabelFrame(content, text="Connection")
        connfrm.pack(fill="x", padx=14, pady=(0, 8))

        row1 = ttk.Frame(connfrm)
        row1.pack(fill="x", padx=10, pady=(8, 4))
        ttk.Label(row1, text="Port:").pack(side="left")
        self.port_combo = ttk.Combobox(row1, textvariable=self.port_var, width=24)
        self.port_combo.pack(side="left", padx=(6, 6))
        ttk.Button(row1, text="Refresh", command=self._refresh_ports).pack(side="left")

        row2 = ttk.Frame(connfrm)
        row2.pack(fill="x", padx=10, pady=(0, 8))
        self.test_btn = ttk.Button(row2, text="Test Connection", command=self._on_test_connection)
        self.test_btn.pack(side="left")
        self.info_btn = ttk.Button(row2, text="Get Machine Info (experimental)",
                                    command=self._on_get_machine_info)
        self.info_btn.pack(side="left", padx=(8, 0))
        ttk.Label(row2, text="Boot wait (s):").pack(side="left", padx=(16, 4))
        ttk.Entry(row2, textvariable=self.boot_wait_var, width=5).pack(side="left")

        ttk.Label(
            connfrm,
            text="Every button here talks to the machine via your fnxmgr.py, which resets "
                 "it when done (that's inherent to the debug port protocol). Get Machine Info "
                 "additionally has to run a tiny probe program on the machine itself first, "
                 "since the debug port can't see FPGA/IO registers directly -- give it a "
                 "moment after launch before it reads the results back.",
            foreground="#777", justify="left", wraplength=840
        ).pack(anchor="w", padx=10, pady=(0, 8))

        # --- Tabs for the rest ---
        notebook = ttk.Notebook(content)
        notebook.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        upload_tab = ttk.Frame(notebook)
        flash_tab = ttk.Frame(notebook)
        history_tab = ttk.Frame(notebook)
        self.history_tab = history_tab
        notebook.add(upload_tab, text="Upload / SD Card")
        notebook.add(flash_tab, text="Update Flash")
        notebook.add(history_tab, text="History")
        notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_upload_tab(upload_tab)
        self._build_flash_tab(flash_tab)
        self._build_history_tab(history_tab)

        # --- status + log (shared across tabs) ---
        status_row = ttk.Frame(content)
        status_row.pack(fill="x", padx=14, pady=(0, 4))
        self.status_label = ttk.Label(status_row, text="Ready.", foreground="#0a6")
        self.status_label.pack(side="left")

        logfrm = ttk.LabelFrame(content, text="Log")
        logfrm.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.log_text = tk.Text(logfrm, height=9, state="disabled", wrap="word",
                                 bg="#111", fg="#ddd", insertbackground="#ddd")
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

    # -- Mouse wheel scrolling (outer window vs. the inner block list) ---

    def _outer_wheel_handler(self, event):
        delta = getattr(event, "delta", 0)
        shifted = bool(getattr(event, "state", 0) & 0x0001)  # Shift held
        if delta:
            (self.outer_canvas.xview_scroll if shifted else self.outer_canvas.yview_scroll)(
                -1 if delta > 0 else 1, "units")
        elif getattr(event, "num", None) in (4, 5):
            (self.outer_canvas.xview_scroll if shifted else self.outer_canvas.yview_scroll)(
                -1 if event.num == 4 else 1, "units")

    def _bind_outer_wheel(self, event=None):
        self.outer_canvas.bind_all("<MouseWheel>", self._outer_wheel_handler)
        self.outer_canvas.bind_all("<Shift-MouseWheel>", self._outer_wheel_handler)
        self.outer_canvas.bind_all("<Button-4>", self._outer_wheel_handler)
        self.outer_canvas.bind_all("<Button-5>", self._outer_wheel_handler)

    def _row_wheel_handler(self, event):
        delta = getattr(event, "delta", 0)
        if delta:
            self.row_canvas.yview_scroll(-1 if delta > 0 else 1, "units")
        elif getattr(event, "num", None) in (4, 5):
            self.row_canvas.yview_scroll(-1 if event.num == 4 else 1, "units")
        return "break"

    def _bind_row_wheel(self, event=None):
        self.row_canvas.bind_all("<MouseWheel>", self._row_wheel_handler)
        self.row_canvas.bind_all("<Button-4>", self._row_wheel_handler)
        self.row_canvas.bind_all("<Button-5>", self._row_wheel_handler)

    @staticmethod
    def _disable_combobox_wheel(combo):
        _disable_combobox_wheel(combo)

    def _build_upload_tab(self, parent):
        # --- PGZ/PGX panel ---
        pgfrm = ttk.LabelFrame(parent, text="Send & Run (.pgz / .pgx / .pgZ)")
        pgfrm.pack(fill="x", padx=4, pady=(10, 8))
        self.pg_drop = DropZone(
            pgfrm, "Drop a .pgz/.pgx/.pgZ file here\n(or use Browse below)",
            self._on_pgfile_chosen,
            filetypes=[("Foenix program files", "*.pgz *.pgx *.pgZ"), ("All files", "*.*")]
        )
        self.pg_drop.pack(fill="x", padx=10, pady=(10, 6))
        row_pg = ttk.Frame(pgfrm)
        row_pg.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(row_pg, text="Browse...", command=self.pg_drop.browse).pack(side="left")
        self.pg_file_label = ttk.Label(row_pg, text="No file selected", foreground="#555")
        self.pg_file_label.pack(side="left", padx=10)
        self.pg_upload_btn = ttk.Button(row_pg, text="Upload & Run", command=self._on_upload_run)
        self.pg_upload_btn.pack(side="right")

        # --- SD card panel ---
        sdfrm = ttk.LabelFrame(parent, text="Copy to SD Card root (F256 only)")
        sdfrm.pack(fill="x", padx=4, pady=(0, 8))
        self.sd_drop = DropZone(
            sdfrm, "Drop any file here to copy it to the SD card\n(or use Browse below)",
            self._on_sdfile_chosen,
            filetypes=[("All files", "*.*")]
        )
        self.sd_drop.pack(fill="x", padx=10, pady=(10, 6))
        row_sd = ttk.Frame(sdfrm)
        row_sd.pack(fill="x", padx=10, pady=(0, 4))
        ttk.Button(row_sd, text="Browse...", command=self.sd_drop.browse).pack(side="left")
        self.sd_file_label = ttk.Label(row_sd, text="No file selected", foreground="#555")
        self.sd_file_label.pack(side="left", padx=10)
        self.sd_copy_btn = ttk.Button(row_sd, text="Copy to SD Card", command=self._on_sd_copy)
        self.sd_copy_btn.pack(side="right")
        ttk.Label(
            sdfrm, text="Requires the machine to be idle at the MicroKernel "
                        "(not mid-BASIC-program).",
            foreground="#777", justify="left"
        ).pack(anchor="w", padx=10, pady=(0, 8))

    def _build_flash_tab(self, parent):
        # --- pgz2flash install (converts .pgz into flash blocks) ---
        pgzfrm = ttk.LabelFrame(parent, text="pgz2flash install (for converting a .pgz into flash blocks)")
        pgzfrm.pack(fill="x", padx=4, pady=(10, 8))
        row_p0 = ttk.Frame(pgzfrm)
        row_p0.pack(fill="x", padx=10, pady=8)
        ttk.Label(row_p0, text="Folder:").pack(side="left")
        self.pgz2flash_dir_label = ttk.Label(row_p0, textvariable=self.pgz2flash_dir,
                                              foreground="#333", wraplength=520, justify="left")
        self.pgz2flash_dir_label.pack(side="left", fill="x", expand=True, padx=(6, 10))
        ttk.Button(row_p0, text="Browse...", command=self._on_browse_pgz2flash_dir).pack(side="right")
        self.pgz2flash_status_label = ttk.Label(pgzfrm, text="", foreground="#777")
        self.pgz2flash_status_label.pack(anchor="w", padx=10, pady=(0, 4))
        ttk.Label(
            pgzfrm,
            text="The folder from a pgz2flash release -- downloads at "
                 "https://github.com/rmsk2/pgz2flash/releases . 'Convert PGZ...' runs "
                 "pgz2flash -onboard in the background, then maps the generated blocks "
                 "into the list and flash map below.",
            foreground="#777", justify="left", wraplength=900
        ).pack(anchor="w", padx=10, pady=(0, 8))

        # --- firmware release folder ---
        relfrm = ttk.LabelFrame(parent, text="Firmware release folder")
        relfrm.pack(fill="x", padx=4, pady=(10, 8))
        row_r0 = ttk.Frame(relfrm)
        row_r0.pack(fill="x", padx=10, pady=8)
        ttk.Label(row_r0, text="Folder:").pack(side="left")
        self.firmware_dir_label = ttk.Label(row_r0, textvariable=self.firmware_dir,
                                             foreground="#333", wraplength=520, justify="left")
        self.firmware_dir_label.pack(side="left", fill="x", expand=True, padx=(6, 10))
        ttk.Button(row_r0, text="Browse...", command=self._on_browse_firmware_dir).pack(side="right")
        ttk.Label(
            relfrm,
            text="The folder from a firmware release (github.com/wildbitscomputing/firmware/"
                 "releases) containing bulkgen1.csv/bulkgen2.csv and the .bin blocks -- "
                 "typically the 'shipping/firmware' folder from the release zip.",
            foreground="#777", justify="left", wraplength=880
        ).pack(anchor="w", padx=10, pady=(0, 8))

        # --- CSV selection ---
        csvfrm = ttk.LabelFrame(parent, text="Block map (.csv)")
        csvfrm.pack(fill="x", padx=4, pady=(0, 8))
        row_c0 = ttk.Frame(csvfrm)
        row_c0.pack(fill="x", padx=10, pady=(8, 4))
        ttk.Button(row_c0, text="Browse for CSV...",
                   command=self._on_browse_csv).pack(side="left")
        ttk.Label(row_c0, text="  e.g. bulkgen1.csv / bulkgen2.csv from the release, "
                              "or any other block-map CSV.",
                  foreground="#777").pack(side="left")

        row_c1 = ttk.Frame(csvfrm)
        row_c1.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(row_c1, text="Loaded:").pack(side="left")
        self.csv_path_label = ttk.Label(row_c1, textvariable=self.csv_path,
                                         foreground="#333", wraplength=780, justify="left")
        self.csv_path_label.pack(side="left", padx=(6, 0))

        # --- block list + live map ---
        listfrm = ttk.LabelFrame(parent, text="Blocks to flash")
        listfrm.pack(fill="both", expand=True, padx=4, pady=(0, 8))

        row_l0 = ttk.Frame(listfrm)
        row_l0.pack(fill="x", padx=10, pady=(8, 4))
        ttk.Button(row_l0, text="Check All", command=lambda: self._set_all_checks(True)).pack(side="left")
        ttk.Button(row_l0, text="Clear All", command=lambda: self._set_all_checks(False)).pack(
            side="left", padx=(6, 0))
        ttk.Button(row_l0, text="Add Block...", command=self._on_add_block).pack(side="left", padx=(6, 0))
        ttk.Button(row_l0, text="Convert PGZ...", command=self._on_convert_pgz).pack(
            side="left", padx=(6, 0))
        self.undo_btn = ttk.Button(row_l0, text="Undo Placement", command=self._undo_placement,
                                    state="disabled")
        self.undo_btn.pack(side="left", padx=(6, 0))
        ttk.Label(row_l0, text="  Rows already up to date are unchecked by default when a CSV loads. "
                              "Change a block's address with its dropdown, or drag it on the map.",
                  foreground="#777").pack(side="left", padx=(10, 0))

        split = ttk.Frame(listfrm)
        split.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        split.columnconfigure(0, weight=1)
        split.columnconfigure(1, weight=0)
        split.rowconfigure(0, weight=1)

        # -- left: scrollable checklist --
        canvas_area = ttk.Frame(split)
        canvas_area.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        canvas_area.rowconfigure(0, weight=1)
        canvas_area.columnconfigure(0, weight=1)
        canvas_area.bind("<Enter>", self._bind_row_wheel)
        canvas_area.bind("<Leave>", self._bind_outer_wheel)

        self.row_canvas = tk.Canvas(canvas_area, highlightthickness=0, height=280, width=560)
        row_vbar = ttk.Scrollbar(canvas_area, orient="vertical", command=self.row_canvas.yview)
        self.row_canvas.configure(yscrollcommand=row_vbar.set)
        self.row_canvas.grid(row=0, column=0, sticky="nsew")
        row_vbar.grid(row=0, column=1, sticky="ns")

        self.row_widgets_frame = ttk.Frame(self.row_canvas)
        self._row_frame_window = self.row_canvas.create_window(
            (0, 0), window=self.row_widgets_frame, anchor="nw")
        self.row_widgets_frame.bind(
            "<Configure>",
            lambda e: self.row_canvas.configure(scrollregion=self.row_canvas.bbox("all")))
        self.row_canvas.bind(
            "<Configure>",
            lambda e: self.row_canvas.itemconfig(self._row_frame_window, width=e.width))

        # -- right: live flash map --
        map_area = ttk.Frame(split)
        map_area.grid(row=0, column=1, sticky="n")
        ttk.Label(map_area, text="Flash map (0x00-0x3F)", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")

        self.flash_map_canvas = tk.Canvas(
            map_area, width=MAP_COLS * MAP_CELL_W, height=MAP_ROWS * MAP_CELL_H,
            highlightthickness=1, highlightbackground="#999", bg="white"
        )
        self.flash_map_canvas.pack(pady=(4, 4))
        self.flash_map_canvas.bind("<ButtonPress-1>", self._on_map_press)
        self.flash_map_canvas.bind("<B1-Motion>", self._on_map_motion)
        self.flash_map_canvas.bind("<ButtonRelease-1>", self._on_map_release)

        legend = ttk.Frame(map_area)
        legend.pack(anchor="w")
        for color, text in [(MAP_COLOR_OCCUPIED, "Selected"), (MAP_COLOR_DISABLED, "Unselected"),
                             (MAP_COLOR_CONFLICT, "Conflict"),
                             (MAP_COLOR_PENDING_ERASE, "Pending erase"), (MAP_COLOR_FREE, "Free")]:
            sw = tk.Canvas(legend, width=12, height=12, highlightthickness=1,
                            highlightbackground="#999")
            sw.pack(side="left", padx=(0, 3), pady=2)
            sw.create_rectangle(0, 0, 12, 12, fill=color, outline="")
            ttk.Label(legend, text=text, font=("TkDefaultFont", 8)).pack(side="left", padx=(0, 8))

        ttk.Label(
            map_area, text="Drag a block to a new address, including onto\nan "
                            "occupied one (shown in red as a conflict).",
            foreground="#777", font=("TkDefaultFont", 8), justify="left"
        ).pack(anchor="w", pady=(2, 0))

        ttk.Label(
            listfrm,
            text="Rows preserve the CSV's original order, including a sector appearing "
                 "more than once -- avoid unchecking just one half of a pair unless you "
                 "know what it does.",
            foreground="#777", justify="left", wraplength=1400
        ).pack(anchor="w", padx=10, pady=(4, 8))

        # --- flash button ---
        action_row = ttk.Frame(parent)
        action_row.pack(fill="x", padx=4, pady=(0, 10))
        self.flash_btn = ttk.Button(action_row, text="Flash Selected Blocks",
                                     command=self._on_flash_selected)
        self.flash_btn.pack(side="left")
        self.delete_btn = ttk.Button(action_row, text="Delete Marked Blocks",
                                      command=self._on_delete_marked)
        self.delete_btn.pack(side="left", padx=(8, 0))
        ttk.Label(
            action_row, text="  Runs fnxmgr.py --flash-bulk, then --boot flash. "
                             "'Delete Marked Blocks' writes 8KB of 0xFF to each ticked "
                             "'Del' block to clear it.",
            foreground="#777"
        ).pack(side="left")

    # -- History tab (preference-backed operation log + flash history) ---

    def _build_history_tab(self, parent):
        self.history_text = tk.Text(parent, state="disabled", wrap="word",
                                    bg="#111", fg="#ddd", insertbackground="#ddd")
        self.history_text.pack(fill="both", expand=True, padx=6, pady=(10, 6))
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=6, pady=(0, 10))
        ttk.Button(row, text="Refresh", command=self._refresh_history).pack(side="left")
        ttk.Label(
            row, text="  From ~/.wildbits_uploader_prefs.json -- what you did (operation "
                      "log) and what each flash sector holds (per-sector history).",
            foreground="#777"
        ).pack(side="left")
        self._refresh_history()

    def _on_tab_changed(self, event=None):
        # Keep the History tab current every time it's selected.
        if getattr(self, "history_tab", None) is not None and getattr(self, "history_text", None):
            self._refresh_history()

    def _refresh_history(self):
        if not getattr(self, "history_text", None):
            return
        lines = []

        log = prefs.get_pref(flash.OPERATION_LOG_KEY, []) or []
        lines.append("== Flash operation log ==")
        if not log:
            lines.append("(no convert / add-block / delete operations recorded yet)")
        else:
            lines.extend(_format_op_entry(e) for e in reversed(log))

        lines.append("")
        lines.append("== Per-sector flash history ==")
        history = flash.get_history()
        if not history:
            lines.append("(no sectors flashed yet)")
        else:
            for sector in sorted(history):
                e = history[sector]
                lines.append(
                    f"0x{sector}: {e.get('filename', '?')} "
                    f"(source dated {e.get('source_date', '?')}) "
                    f"-- flashed {e.get('flashed_at', '?')}")

        self._set_history_text("\n".join(lines))

    def _set_history_text(self, text):
        self.history_text.config(state="normal")
        self.history_text.delete("1.0", "end")
        self.history_text.insert("1.0", text)
        self.history_text.config(state="disabled")

    # -- Firmware release folder handling ---------------------------------

    def _on_browse_firmware_dir(self):
        chosen = filedialog.askdirectory(
            title="Choose your firmware release folder (e.g. shipping/firmware)",
            initialdir=self.firmware_dir.get() or os.path.expanduser("~")
        )
        if chosen:
            self.firmware_dir.set(chosen)
            prefs.set_pref("firmware_release_dir", chosen)

    def _on_browse_csv(self):
        if self.csv_path.get():
            initial = os.path.dirname(self.csv_path.get())
        else:
            initial = self.firmware_dir.get().strip() or os.path.expanduser("~")
        chosen = filedialog.askopenfilename(
            title="Choose a block map CSV", initialdir=initial,
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if chosen:
            self._load_csv(chosen)

    def _load_startup_state(self):
        """On launch, prefer restoring the last-committed working block
        list -- the original CSV plus any convert-pgz / add-block /
        delete edits that were confirmed by a successful flash or erase
        -- over just re-parsing the original CSV again. Otherwise those
        edits would silently vanish every time the app restarts, even
        though they're already reflected on the actual hardware."""
        working = flash.load_working_set()
        if working is not None:
            csv_path, rows = working
            self.csv_path.set(csv_path)
            self.csv_rows = rows
            self.placement_stack = []
            self._update_undo_state()
            self._log(
                f"Restored {len(rows)} block(s) from your last committed "
                f"flash/erase" + (f" (base CSV: {csv_path})" if csv_path else "") + ".")
            self._rebuild_row_list()
            return

        last_csv = prefs.get_pref("last_csv_path", "")
        if last_csv and os.path.isfile(last_csv):
            self._load_csv(last_csv, quiet=True)

    def _load_csv(self, csv_path, quiet=False):
        try:
            rows = flash.parse_bulk_csv(csv_path)
        except flash.FlashCsvError as exc:
            messagebox.showerror("Can't load CSV", str(exc))
            return
        self.csv_path.set(csv_path)
        prefs.set_pref("last_csv_path", csv_path)
        if not quiet:
            # A CSV the user explicitly picks supersedes any working set
            # remembered from a previous session's committed flash/erase.
            flash.clear_working_set()
        self.csv_rows = rows
        self.placement_stack = []   # a fresh CSV load supersedes any pending convert-undos
        self._update_undo_state()
        if not quiet:
            self._log(f"Loaded {len(rows)} block(s) from {csv_path}")
        self._rebuild_row_list()

    def _rebuild_row_list(self):
        """Full rebuild: (re)creates every row's widgets and variables,
        including checkbox state reset to computed defaults. Used when a
        new CSV loads, or after a flash completes (since history/freshness
        just changed). Sector edits afterward use _refresh_row_display()
        instead, so they don't discard the user's check/uncheck choices."""
        for child in self.row_widgets_frame.winfo_children():
            child.destroy()
        self.row_check_vars = []
        self.row_delete_vars = []
        self.row_sector_vars = []
        self.row_status_labels = []
        self.row_conflict_labels = []

        for i, row in enumerate(self.csv_rows):
            status_text, up_to_date = flash.row_status(row)
            missing = row.source_date() is None
            default_checked = (not missing) and (up_to_date is not True)
            check_var = tk.BooleanVar(value=default_checked)
            self.row_check_vars.append(check_var)
            check_var.trace_add("write", lambda *a: self._redraw_flash_map())

            # Pending-erase placeholders (see _schedule_vacated_sector)
            # default to already ticked for deletion -- that's the whole
            # point of them existing.
            delete_var = tk.BooleanVar(value=row.pending_erase)
            self.row_delete_vars.append(delete_var)

            sector_var = tk.StringVar(value=row.sector_hex)
            self.row_sector_vars.append(sector_var)

            outer = ttk.Frame(self.row_widgets_frame)
            outer.pack(fill="x", pady=2)

            # Sector 0 is the reserved boot block: it stays visible in the
            # list but can't be deleted, flashed, or moved.
            protected = (row.sector == PROTECTED_SECTOR)

            line = ttk.Frame(outer)
            line.pack(fill="x")
            del_check = ttk.Checkbutton(line, text="Del", variable=delete_var, width=4)
            if protected:
                del_check.state(["disabled"])
            del_check.pack(side="left")
            # A pending-erase placeholder has no real file to flash --
            # disable its flash checkbox so it can't be checked by
            # mistake; only the Del box (already ticked above) applies.
            flash_check = ttk.Checkbutton(line, variable=check_var)
            if row.pending_erase or protected:
                flash_check.state(["disabled"])
            flash_check.pack(side="left")
            combo = ttk.Combobox(line, textvariable=sector_var, values=SECTOR_CHOICES_EDITABLE,
                                  width=4, state="readonly", font=("TkFixedFont", 9))
            if protected:
                combo.state(["disabled"])
            combo.pack(side="left", padx=(2, 6))
            combo.bind("<<ComboboxSelected>>", self._make_sector_change_handler(i))
            self._disable_combobox_wheel(combo)
            label_style = {"foreground": MAP_OUTLINE_PENDING_ERASE} if row.pending_erase else {}
            if protected:
                label_style = {"foreground": MAP_OUTLINE_RESERVED}
            ttk.Label(line, text=row.filename, width=22, anchor="w", **label_style).pack(
                side="left", padx=(0, 8))
            status_label = ttk.Label(line, text=status_text)
            status_label.pack(side="left")
            self.row_status_labels.append(status_label)

            conflict_label = ttk.Label(outer, text="", foreground=MAP_OUTLINE_CONFLICT,
                                        font=("TkDefaultFont", 8, "italic"))
            self.row_conflict_labels.append(conflict_label)

        self._refresh_row_display()

    def _make_sector_change_handler(self, index):
        def handler(event=None):
            try:
                new_sector = int(self.row_sector_vars[index].get(), 16)
            except ValueError:
                return
            self._reassign_row_sector(index, new_sector)
        return handler

    def _clear_vacated_placeholder(self, sector):
        """Drop any pending-erase placeholder currently sitting at
        `sector`. Called whenever real content is about to occupy that
        sector (a move, an add, or a pgz2flash conversion landing there)
        -- the incoming flash write already overwrites whatever was
        there, so a separately scheduled erase for the same sector would
        just be redundant."""
        self.csv_rows = [r for r in self.csv_rows
                          if not (r.pending_erase and r.sector == sector)]

    def _schedule_vacated_sector(self, sector):
        """Ensure there's a pending-erase placeholder row for `sector`,
        so a block moved off of it doesn't leave stale content silently
        sitting in flash -- it shows up in the list (checked in the Del
        column by default) and on the map until an actual Delete Marked
        Blocks commit clears it. Reuses an existing placeholder for the
        same sector rather than stacking duplicates. Returns the row."""
        for row in self.csv_rows:
            if row.pending_erase and row.sector == sector:
                return row
        row = flash.BulkCsvRow(sector, "(vacated -- pending erase)", None, pending_erase=True)
        self.csv_rows.append(row)
        return row

    def _reassign_row_sector(self, index, new_sector):
        """Change row[index]'s sector -- used by both the map drag-and-
        drop and the row's own sector dropdown, which are documented as
        being the same edit. Marks the moved row checked (ready to flash
        at its new sector), clears any pending-erase placeholder already
        sitting at that new sector, and -- if the row's *old* sector
        isn't still used by anything else -- schedules one there, so the
        stale content actually gets cleared on the next Delete Marked
        Blocks commit instead of silently being left behind on the chip."""
        if not (0 <= index < len(self.csv_rows)):
            return
        row = self.csv_rows[index]
        old_sector = row.sector
        if old_sector == PROTECTED_SECTOR or new_sector == PROTECTED_SECTOR:
            return
        if old_sector == new_sector:
            return

        row.sector = new_sector
        self._clear_vacated_placeholder(new_sector)

        still_used = any(r is not row and r.sector == old_sector for r in self.csv_rows)
        if not still_used:
            self._schedule_vacated_sector(old_sector)

        self._rebuild_row_list()

        if row in self.csv_rows and self.row_check_vars:
            self.row_check_vars[self.csv_rows.index(row)].set(True)

    def _compute_conflicts(self):
        """Returns {row_index: [other_row_indices_sharing_the_same_sector]}."""
        by_sector = {}
        for i, row in enumerate(self.csv_rows):
            by_sector.setdefault(row.sector, []).append(i)
        conflicts = {}
        for indices in by_sector.values():
            if len(indices) > 1:
                for i in indices:
                    conflicts[i] = [j for j in indices if j != i]
        return conflicts

    def _refresh_row_display(self):
        """Lightweight update: recomputes conflicts and freshness status
        for each row and updates the existing labels/comboboxes in
        place, preserving checkbox state. Also redraws the flash map."""
        conflicts = self._compute_conflicts()
        for i, row in enumerate(self.csv_rows):
            self.row_sector_vars[i].set(row.sector_hex)

            status_text, up_to_date = flash.row_status(row)
            missing = row.source_date() is None
            if missing:
                color = "#c00"
            elif up_to_date is True:
                color = "#0a6"
            elif up_to_date is False:
                color = "#c60"
            else:
                color = "#777"
            self.row_status_labels[i].config(text=status_text, foreground=color)

            conflict_label = self.row_conflict_labels[i]
            other_indices = conflicts.get(i)
            if other_indices:
                names = ", ".join(os.path.basename(self.csv_rows[j].filename) for j in other_indices)
                conflict_label.config(
                    text=f"\u26a0 Sector 0x{row.sector:02X} also used by: {names}")
                conflict_label.pack(anchor="w", padx=(24, 0))
            else:
                conflict_label.pack_forget()

        self._redraw_flash_map()

    # -- Live flash map (0x00-0x3F), drag-and-drop reassignment ----------

    def _redraw_flash_map(self):
        canvas = getattr(self, "flash_map_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")

        by_sector = {}
        for i, row in enumerate(self.csv_rows):
            by_sector.setdefault(row.sector, []).append(i)

        for sector in range(SECTOR_COUNT):
            col, row_pos = sector % MAP_COLS, sector // MAP_COLS
            x0, y0 = col * MAP_CELL_W, row_pos * MAP_CELL_H
            x1, y1 = x0 + MAP_CELL_W, y0 + MAP_CELL_H

            if sector == PROTECTED_SECTOR:
                canvas.create_rectangle(x0, y0, x1, y1, fill=MAP_COLOR_RESERVED,
                                        outline=MAP_OUTLINE_RESERVED, width=2)
                canvas.create_text(x0 + 4, y0 + 3, anchor="nw", text=f"{sector:02X}",
                                   font=("TkFixedFont", 8), fill="#555")
                canvas.create_text(
                    (x0 + x1) // 2, y0 + MAP_CELL_H // 2,
                    text="RESERVED", font=("TkDefaultFont", 8),
                    fill="#666", justify="center", width=MAP_CELL_W - 6)
                continue

            indices = by_sector.get(sector, [])
            if not indices:
                fill, outline = MAP_COLOR_FREE, MAP_OUTLINE_FREE
            elif len(indices) > 1:
                fill, outline = MAP_COLOR_CONFLICT, MAP_OUTLINE_CONFLICT
            elif self.csv_rows[indices[0]].pending_erase:
                fill, outline = MAP_COLOR_PENDING_ERASE, MAP_OUTLINE_PENDING_ERASE
            else:
                checked = self.row_check_vars[indices[0]].get() if self.row_check_vars else True
                fill = MAP_COLOR_OCCUPIED if checked else MAP_COLOR_DISABLED
                outline = MAP_OUTLINE_OCCUPIED

            canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=2)
            canvas.create_text(x0 + 4, y0 + 3, anchor="nw", text=f"{sector:02X}",
                                font=("TkFixedFont", 8), fill="#555")

            if indices:
                names = [os.path.basename(self.csv_rows[i].filename) for i in indices[:2]]
                label_lines = [n if len(n) <= 15 else n[:13] + "\u2026" for n in names]
                if len(indices) > 2:
                    label_lines.append(f"+{len(indices) - 2} more")
                canvas.create_text(
                    (x0 + x1) // 2, y0 + MAP_CELL_H // 2 + 8,
                    text="\n".join(label_lines), font=("TkDefaultFont", 7),
                    fill="#222", justify="center", width=MAP_CELL_W - 6)

        canvas.configure(scrollregion=(0, 0, MAP_COLS * MAP_CELL_W, MAP_ROWS * MAP_CELL_H))

    def _map_sector_at(self, x, y):
        col, row_pos = int(x // MAP_CELL_W), int(y // MAP_CELL_H)
        if 0 <= col < MAP_COLS and 0 <= row_pos < MAP_ROWS:
            sector = row_pos * MAP_COLS + col
            if sector < SECTOR_COUNT:
                return sector
        return None

    def _on_map_press(self, event):
        self._map_drag_row_index = None
        if not self.csv_rows:
            return
        x = self.flash_map_canvas.canvasx(event.x)
        y = self.flash_map_canvas.canvasy(event.y)
        sector = self._map_sector_at(x, y)
        if sector is None or sector == PROTECTED_SECTOR:
            return
        indices = [i for i, row in enumerate(self.csv_rows) if row.sector == sector]
        if indices:
            self._map_drag_row_index = indices[0]

    def _on_map_motion(self, event):
        if self._map_drag_row_index is None:
            return
        self.flash_map_canvas.delete("drag_highlight")
        x = self.flash_map_canvas.canvasx(event.x)
        y = self.flash_map_canvas.canvasy(event.y)
        sector = self._map_sector_at(x, y)
        if sector is not None and sector != PROTECTED_SECTOR:
            col, row_pos = sector % MAP_COLS, sector // MAP_COLS
            x0, y0 = col * MAP_CELL_W, row_pos * MAP_CELL_H
            self.flash_map_canvas.create_rectangle(
                x0, y0, x0 + MAP_CELL_W, y0 + MAP_CELL_H,
                outline="#222", width=3, tags="drag_highlight")

    def _on_map_release(self, event):
        index = self._map_drag_row_index
        self._map_drag_row_index = None
        self.flash_map_canvas.delete("drag_highlight")
        if index is None:
            return
        x = self.flash_map_canvas.canvasx(event.x)
        y = self.flash_map_canvas.canvasy(event.y)
        sector = self._map_sector_at(x, y)
        if sector is not None and sector != PROTECTED_SECTOR:
            self._reassign_row_sector(index, sector)

    def _set_all_checks(self, checked):
        for var in self.row_check_vars:
            var.set(checked)

    def _first_free_sector(self):
        used = {row.sector for row in self.csv_rows}
        for sector in range(SECTOR_COUNT):
            if sector == PROTECTED_SECTOR:
                continue
            if sector not in used:
                return sector
        return 1

    def _on_add_block(self):
        path = filedialog.askopenfilename(
            title="Choose a file to add as a flash block",
            filetypes=[("Binary files", "*.bin"), ("All files", "*.*")]
        )
        if not path:
            return

        def confirm(sector):
            self._clear_vacated_placeholder(sector)
            row = flash.BulkCsvRow(sector, os.path.basename(path), path)
            self.csv_rows.append(row)
            self._rebuild_row_list()
            # The new row is always last -- explicitly check it so it's
            # ready for the next Flash Selected Blocks commit, regardless
            # of what the default freshness-based checkbox state would
            # have picked.
            if self.row_check_vars:
                self.row_check_vars[-1].set(True)
            flash.record_operation("add_block",
                                   file=os.path.basename(path),
                                   sector=f"{sector:02X}")
            self._log(f"Added block: {os.path.basename(path)} -> sector 0x{sector:02X}")

        AddBlockDialog(self, path, self._first_free_sector(), confirm)

    # -- pgz2flash: convert a .pgz into flash blocks and place them ------

    def _on_browse_pgz2flash_dir(self):
        chosen = filedialog.askdirectory(
            title="Choose your pgz2flash install folder",
            initialdir=self.pgz2flash_dir.get() or os.path.expanduser("~")
        )
        if chosen:
            self.pgz2flash_dir.set(chosen)
            prefs.set_pref("pgz2flash_dir", chosen)
            self._resolve_pgz2flash()

    def _resolve_pgz2flash(self):
        folder = self.pgz2flash_dir.get().strip()
        if not folder:
            self.pgz2flash_status_label.config(
                text="No pgz2flash folder set yet -- pick the folder from a pgz2flash "
                     "release (github.com/rmsk2/pgz2flash/releases).",
                foreground="#c60")
            return
        try:
            exe = cli.find_pgz2flash_executable(folder)
            self.pgz2flash_status_label.config(
                text=f"Using: {exe}", foreground="#0a6")
        except cli.Pgz2FlashNotFound as exc:
            self.pgz2flash_status_label.config(text=str(exc).splitlines()[0], foreground="#c00")

    def _require_pgz2flash(self):
        folder = self.pgz2flash_dir.get().strip()
        if not folder:
            messagebox.showwarning(
                "No pgz2flash folder set",
                "Click Browse... next to 'pgz2flash install' and pick the folder you "
                "downloaded from https://github.com/rmsk2/pgz2flash/releases"
            )
            return False
        try:
            cli.find_pgz2flash_executable(folder)
        except cli.Pgz2FlashNotFound as exc:
            messagebox.showerror("pgz2flash not found", str(exc))
            return False
        return True

    def _on_convert_pgz(self):
        if not self._require_pgz2flash():
            return
        path = filedialog.askopenfilename(
            title="Choose a .pgz to convert into flash blocks",
            filetypes=[("Foenix program files", "*.pgz *.pgZ"), ("All files", "*.*")]
        )
        if not path:
            return

        def confirm(sector, name, desc):
            self._start_pgz_conversion(path, sector, name, desc)

        ConvertPgzDialog(self, path, self._first_free_sector(), confirm)

    def _start_pgz_conversion(self, pgz_path, start_sector, name, desc):
        if self._busy:
            return
        if self._pgz_staging is None:
            self._pgz_staging = tempfile.mkdtemp(prefix="pgz2flash_")
        out_dir = tempfile.mkdtemp(prefix="conv_", dir=self._pgz_staging)

        def worker():
            pgz2flash_exe = cli.find_pgz2flash_executable(self.pgz2flash_dir.get().strip())
            return cli.run_pgz2flash_onboard(
                pgz2flash_exe, pgz_path, start_sector, name, desc, out_dir, log=self._log,
            )

        def on_success(csv_path):
            try:
                new_rows = flash.parse_bulk_csv(csv_path)
            except flash.FlashCsvError as exc:
                self._log(f"Couldn't parse pgz2flash's output CSV: {exc}")
                messagebox.showerror("pgz2flash output error", str(exc))
                return
            for r in new_rows:
                self._clear_vacated_placeholder(r.sector)
            self.csv_rows.extend(new_rows)
            self.placement_stack.append(new_rows)
            self._rebuild_row_list()
            self._update_undo_state()
            flash.record_operation(
                "convert_pgz", pgz=os.path.basename(pgz_path), name=name, desc=desc,
                start_sector=f"{start_sector:02X}", count=len(new_rows),
                sectors=[f"{r.sector:02X}" for r in new_rows])
            self._log(
                f"Installed {len(new_rows)} block(s) from {os.path.basename(pgz_path)} "
                f"into the list/map (start sector 0x{start_sector:02X}).")
            self.status_label.config(
                text=f"Placed {len(new_rows)} block(s) from {os.path.basename(pgz_path)}.",
                foreground="#0a6")

        def on_error(exc):
            self._log(f"pgz2flash conversion failed: {exc}")
            messagebox.showerror("pgz2flash conversion failed", str(exc))

        self._run_in_background(worker, on_success, on_error,
                                f"Converting {os.path.basename(pgz_path)}...",
                                require_script=False)

    def _update_undo_state(self):
        if hasattr(self, "undo_btn"):
            self.undo_btn.config(
                state="normal" if self.placement_stack else "disabled")

    def _undo_placement(self):
        if not self.placement_stack:
            self._update_undo_state()
            return
        rows_to_remove = self.placement_stack.pop()
        remove_ids = {id(r) for r in rows_to_remove}
        self.csv_rows = [r for r in self.csv_rows if id(r) not in remove_ids]
        self._rebuild_row_list()
        self._update_undo_state()
        self._log(f"Undid placement of {len(rows_to_remove)} block(s).")
        self.status_label.config(text=f"Undid placement of {len(rows_to_remove)} block(s).",
                                 foreground="#c80")

    # -- Delete (erase) marked blocks ------------------------------------

    def _on_delete_marked(self):
        marked = [row for row, var in zip(self.csv_rows, self.row_delete_vars) if var.get()]
        marked = [r for r in marked if r.sector != PROTECTED_SECTOR]
        if not marked:
            messagebox.showwarning(
                "Nothing marked for deletion",
                "Tick the 'Del' box on the block(s) you want to clear from flash.")
            return

        proceed = messagebox.askyesno(
            "Delete Marked Blocks",
            f"This writes 8KB of 0xFF to {len(marked)} flash block(s) to clear them. "
            f"On success they'll be removed from the list and map. This can't be "
            f"interrupted safely once started.\n\nContinue?"
        )
        if not proceed:
            return

        total = len(marked)
        prog = self._make_stage_progress(total, "Erasing")

        def worker():
            port = self._selected_port()
            return flash.erase_blocks(self.script_path, port, marked, log=self._log,
                                      progress=prog["on_progress"])

        def on_success(programmed):
            programmed_sectors = {sector for sector, _name in programmed}
            self.csv_rows = [r for r in self.csv_rows if r.sector not in programmed_sectors]
            self._rebuild_row_list()
            flash.save_working_set(self.csv_path.get(), self.csv_rows)
            self._log(f"Deleted {len(programmed_sectors)} block(s) from flash.")
            self.status_label.config(
                text=f"Deleted {len(programmed_sectors)} block(s).",
                foreground="#0a6")

        def on_error(exc):
            done = prog["completed"]()
            self._log(f"Delete failed ({done} of {total} block(s) cleared before it "
                       f"aborted): {exc}")
            self.status_label.config(
                text=f"Erase failed: {done}/{total} block(s) cleared, then aborted.",
                foreground="#c00")
            messagebox.showerror("Delete failed", str(exc))
            self._rebuild_row_list()

        self._run_in_background(worker, on_success, on_error, "Erasing marked blocks...")

    def _on_flash_selected(self):
        if not self.csv_rows:
            messagebox.showwarning("No CSV loaded", "Load a block map CSV first.")
            return
        selected_rows = [row for row, var in zip(self.csv_rows, self.row_check_vars) if var.get()]
        selected_rows = [r for r in selected_rows if r.sector != PROTECTED_SECTOR]
        if not selected_rows:
            messagebox.showwarning("Nothing selected", "Check at least one block to flash.")
            return

        proceed = messagebox.askyesno(
            "Flash Selected Blocks",
            f"This will write {len(selected_rows)} block(s) to flash and then set the "
            f"boot source to flash. This can't be interrupted safely once started -- "
            f"make sure the machine stays powered and connected.\n\nContinue?"
        )
        if not proceed:
            return

        total = len(selected_rows)
        prog = self._make_stage_progress(total, "Flashing")

        def worker():
            port = self._selected_port()
            return flash.flash_bulk(self.script_path, port, selected_rows, log=self._log,
                                     progress=prog["on_progress"])

        def on_success(programmed):
            self._log(f"Done. {len(programmed)} of {len(selected_rows)} block(s) confirmed programmed.")
            self.status_label.config(text="Flash update complete.", foreground="#0a6")
            self._rebuild_row_list()
            flash.save_working_set(self.csv_path.get(), self.csv_rows)

        def on_error(exc):
            done = prog["completed"]()
            self._log(f"Flash update failed ({done} of {total} block(s) written before it "
                       f"aborted -- see the list above): {exc}")
            self.status_label.config(
                text=f"Flash failed: {done}/{total} block(s) written, then aborted.",
                foreground="#c00")
            messagebox.showerror("Flash update failed", str(exc))
            self._rebuild_row_list()

        self._run_in_background(worker, on_success, on_error, "Flashing...")

    def _make_stage_progress(self, total, verb):
        """Returns a progress tracker dict for the background flash/erase
        worker:
          {"on_progress": on_progress(sector, filename, stage),
           "completed":  () -> int  # how many blocks reached 'completed'}
        `on_progress` updates the status line through both stages --
        'writing' as a block starts, 'completed' once it's confirmed
        programmed -- and `completed` lets the failure handler report how
        many blocks actually finished before an abort. `verb` is the
        human action word ('Flashing' / 'Erasing') used in the text."""
        state = {"count": 0, "by_sector": {}, "completed": 0}

        def on_progress(sector, filename, stage):
            if stage == "writing":
                state["count"] += 1
                state["by_sector"][sector] = state["count"]
                self._update_flash_progress(state["count"], total, sector, filename, verb, stage)
            elif stage == "completed":
                state["completed"] += 1
                index = state["by_sector"].get(sector)
                if index is not None:
                    self._update_flash_progress(index, total, sector, filename, verb, stage)

        return {"on_progress": on_progress, "completed": lambda: state["completed"]}

    def _update_flash_progress(self, index, total, sector, filename, verb, stage):
        """Thread-safe: called from the background flash worker as each
        block is written, so the status text shows live per-block status
        instead of a static "please wait". Shows the two stages: the
        'writing' stage while a block is in progress, and the 'completed'
        stage once it's confirmed programmed."""
        if stage == "completed":
            text = f"{verb} block {index}/{total}: sector 0x{sector:02X} completed."
            color = "#0a6"
        else:
            text = f"{verb} block {index}/{total}: sector 0x{sector:02X} ({filename})..."
            color = "#c80"
        self.after(0, lambda: self.status_label.config(text=text, foreground=color))

    def _on_browse_foenixmgr_dir(self):
        chosen = filedialog.askdirectory(
            title="Choose your FoenixMgr folder",
            initialdir=self.foenixmgr_dir.get() or os.path.expanduser("~")
        )
        if chosen:
            self.foenixmgr_dir.set(chosen)
            prefs.set_pref("foenixmgr_dir", chosen)
            self._resolve_script()

    def _resolve_script(self, startup=False):
        folder = self.foenixmgr_dir.get().strip()
        if not folder:
            self.script_status_label.config(
                text="No folder set yet -- click Browse... and pick your FoenixMgr folder.",
                foreground="#c60")
            return
        try:
            self.script_path = cli.find_fnxmgr_script(folder)
            self.script_status_label.config(
                text=f"Using: {self.script_path}", foreground="#0a6")
            if not startup:
                self._log(f"FoenixMgr script found: {self.script_path}")
            self._refresh_ports()
        except cli.FnxMgrNotFound as exc:
            self.script_path = ""
            self.script_status_label.config(text=str(exc).splitlines()[0], foreground="#c00")

    def _require_script(self):
        if not self.script_path:
            messagebox.showwarning(
                "No FoenixMgr folder set",
                "Click Browse... next to 'FoenixMgr install' and pick the folder "
                "you downloaded/cloned FoenixMgr into."
            )
            return False
        return True

    # -- helpers ------------------------------------------------------

    def _refresh_ports(self):
        if not self.script_path:
            return
        try:
            ports = cli.list_ports(self.script_path)
        except cli.FnxMgrError as exc:
            self._log(f"Couldn't list serial ports: {exc}")
            ports = []
        values = [dev for dev, _desc in ports]
        self.port_combo["values"] = values
        if values and not self.port_var.get():
            self.port_var.set(values[0])

    def _log(self, message):
        """Thread-safe: worker threads call this directly, so the actual
        Tk widget update is marshaled onto the main thread via after()."""
        self.after(0, self._append_log, message)

    def _append_log(self, message):
        self.log_text.config(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _set_busy(self, busy, status_text=None, status_color="#c80"):
        self._busy = busy
        state = "disabled" if busy else "normal"
        buttons = [self.test_btn, self.info_btn, self.pg_upload_btn, self.sd_copy_btn,
                   self.flash_btn, self.delete_btn]
        if hasattr(self, "undo_btn"):
            buttons.append(self.undo_btn)
        for btn in buttons:
            btn.config(state=state)
        # The undo button tracks its own availability separately (disabled
        # when there's nothing to undo), so re-apply that after the generic
        # busy-state pass above.
        if not busy and hasattr(self, "undo_btn"):
            self.undo_btn.config(state="normal" if self.placement_stack else "disabled")
        if status_text is not None:
            self.status_label.config(text=status_text, foreground=status_color)

    def _parse_boot_wait(self):
        try:
            return float(self.boot_wait_var.get())
        except ValueError:
            raise ValueError(f"Invalid boot wait time: {self.boot_wait_var.get()!r}")

    def _selected_port(self):
        port = self.port_var.get().strip()
        if not port:
            raise ValueError("Choose a serial port first (click Refresh if the list is empty).")
        prefs.set_pref("last_port", port)
        return port

    def _run_in_background(self, worker_fn, on_success, on_error, busy_text,
                           require_script=True):
        if self._busy:
            return
        if require_script and not self._require_script():
            return
        self._set_busy(True, busy_text)

        def run():
            try:
                result = worker_fn()
                self.after(0, lambda result=result: self._finish_background(on_success, result, True))
            except Exception as exc:
                self.after(0, lambda exc=exc: self._finish_background(on_error, exc, False))

        threading.Thread(target=run, daemon=True).start()

    def _finish_background(self, callback, value, ok):
        """Re-enable the UI after a background op. On success the status
        bar returns to green "Ready."; on failure it's left showing a red
        "Operation failed." so the error stays visible -- a specific
        callback (e.g. flash/erase) may then override it with detail like
        "X of Y written before it aborted"."""
        if ok:
            self._set_busy(False, "Ready.", "#0a6")
        else:
            self._set_busy(False, "Operation failed.", "#c00")
        callback(value)

    # -- Test Connection ------------------------------------------------

    def _on_test_connection(self):
        def worker():
            port = self._selected_port()
            return cli.test_connection(self.script_path, port, log=self._log)

        def on_success(revision):
            names = {0: "RevB2", 1: "RevC4A"}
            name = names.get(revision, f"unknown (0x{revision:X})")
            self._log(f"Connected. Debug port interface revision: {revision} ({name}).")
            messagebox.showinfo("Connection OK", f"Debug port interface revision: {revision} ({name})")

        def on_error(exc):
            self._log(f"Connection test failed: {exc}")
            messagebox.showerror("Connection failed", str(exc))

        self._run_in_background(worker, on_success, on_error, "Testing connection...")

    # -- Get Machine Info ------------------------------------------------

    def _on_get_machine_info(self):
        proceed = messagebox.askyesno(
            "Get Machine Info",
            "This launches a small probe program to read the machine's FPGA/IO "
            "registers, which the debug port can't see directly. It will reset "
            "the machine, wait for it to boot, then read the results back.\n\n"
            "Continue?"
        )
        if not proceed:
            return

        def worker():
            boot_wait = self._parse_boot_wait()
            port = self._selected_port()
            result_bytes = cli.get_machine_info_probe_result(
                self.script_path, port,
                code_addr=probe.DEFAULT_CODE_ADDR, result_addr=probe.DEFAULT_RESULT_ADDR,
                result_len=probe.RESULT_TOTAL_LEN, boot_wait=boot_wait, log=self._log,
            )
            probe_result = probe.decode_probe_result(result_bytes)
            return info.build_machine_info(probe_result)

        def on_success(machine_info):
            self._log(
                f"Model: {machine_info.model_summary}  "
                f"(raw ID: 0x{machine_info.raw_machine_id:02X})\n"
                f"Hardware build info: {machine_info.build_hex} '{machine_info.build_chars}'\n"
                f"MicroKernel info string: {machine_info.kernel_info}"
            )
            messagebox.showinfo(
                "Machine Info",
                f"Model: {machine_info.model_summary}\n"
                f"Raw machine ID: 0x{machine_info.raw_machine_id:02X}\n\n"
                f"Hardware build info: {machine_info.build_hex} "
                f"'{machine_info.build_chars}'\n"
                f"(reverse-engineered from SuperBASIC's boot banner -- treat as "
                f"informational, not an official version number)\n\n"
                f"MicroKernel info string: {machine_info.kernel_info}"
            )

        def on_error(exc):
            self._log(f"Get Machine Info failed: {exc}")
            messagebox.showerror("Get Machine Info failed", str(exc))

        self._run_in_background(worker, on_success, on_error, "Probing machine info...")

    # -- Send & Run -------------------------------------------------------

    def _on_pgfile_chosen(self, path):
        self.pgfile_path.set(path)
        self.pg_drop.set_selected(path)
        self.pg_file_label.config(text=os.path.basename(path))
        self._log(f"Selected program file: {path}")

    def _on_upload_run(self):
        path = self.pgfile_path.get().strip()
        if not path:
            messagebox.showwarning("No file", "Drop or browse for a .pgz/.pgx/.pgZ file first.")
            return
        if not os.path.isfile(path):
            messagebox.showerror("File not found", f"Can't find:\n{path}")
            return

        def worker():
            port = self._selected_port()
            cli.upload_and_run(self.script_path, path, port, log=self._log)

        def on_success(_):
            self._log("Done. Program launched.")
            self.status_label.config(text="Program launched.", foreground="#0a6")

        def on_error(exc):
            self._log(f"Upload & Run failed: {exc}")
            messagebox.showerror("Upload & Run failed", str(exc))

        self._run_in_background(worker, on_success, on_error, "Uploading program...")

    # -- SD Card Copy -----------------------------------------------------

    def _on_sdfile_chosen(self, path):
        self.sdfile_path.set(path)
        self.sd_drop.set_selected(path)
        self.sd_file_label.config(text=os.path.basename(path))
        self._log(f"Selected file for SD card: {path}")

    def _on_sd_copy(self):
        path = self.sdfile_path.get().strip()
        if not path:
            messagebox.showwarning("No file", "Drop or browse for a file first.")
            return
        if not os.path.isfile(path):
            messagebox.showerror("File not found", f"Can't find:\n{path}")
            return

        def worker():
            port = self._selected_port()
            cli.copy_to_sdcard(self.script_path, path, port, log=self._log)

        def on_success(_):
            self._log("Done. The file should now be on the SD card's root.")
            self.status_label.config(text="File copied to SD card.", foreground="#0a6")

        def on_error(exc):
            self._log(f"SD Card Copy failed: {exc}")
            messagebox.showerror("SD Card Copy failed", str(exc))

        self._run_in_background(worker, on_success, on_error, "Copying to SD card...")


if __name__ == "__main__":
    app = WildbitsUploaderApp()
    app.mainloop()

# Wildbits Uploader v0.6

A small GUI front-end for your existing [FoenixMgr](https://github.com/pweingar/FoenixMgr)
install. It drives your own `fnxmgr.py` via subprocess -- same protocol
handling, same `foenixmgr.ini`, same everything you already have working --
this just gives it buttons and drag-and-drop.

<img width="1451" height="1392" alt="image" src="https://github.com/user-attachments/assets/1f7784e5-56aa-4ea1-b99c-e33b961285bf" />


## Setup

1. Files needed (all in one folder): `wildbits_uploader_gui.py`,
   `wildbits_cli.py`, `wildbits_prefs.py`, `wildbits_probe.py`,
   `wildbits_info.py`.
2. `pip install tkinterdnd2` (optional -- enables drag-and-drop; the
   Browse buttons work fine without it).
3. Run: `python3 wildbits_uploader_gui.py`
4. First run: click **Browse...** next to "FoenixMgr install" and pick
   the folder containing (or containing a subfolder that contains)
   `fnxmgr.py`. This is saved to `~/.wildbits_uploader_prefs.json`, so
   you only do it once.
5. Click **Refresh** to list serial ports (via `fnxmgr.py --list-ports`),
   pick yours. That's also remembered for next time.

## What each button does

- **Test Connection** -- `fnxmgr.py --revision`
- **Send & Run** -- `fnxmgr.py --run-pgz` / `--run-pgx` (picked by file extension)
- **Copy to SD Card** -- `fnxmgr.py --copy`
- **Get Machine Info** *(experimental)* -- the debug port can't see
  memory-mapped FPGA/IO registers directly (only code actually running
  on the CPU can). So this builds a tiny hand-assembled 65C02 program
  that reads `$D6A7`-`$D6AD` (machine ID + hardware build info) and the
  MicroKernel's info string at `$E008` into plain RAM, launches it via
  `--run-pgz`, waits for boot, then reads the results back via
  `--dump`. The register layout is reverse-engineered from SuperBASIC's
  own boot-banner source (`bannerinfo.asm`) -- treat the results as
  informational, not an authoritative version API. SuperBASIC's *own*
  build version isn't included: it lives at a symbol-relative address
  inside SuperBASIC's own flash image, not a fixed address, so it can't
  be read reliably this way -- check the boot banner directly for that.
- **Update Flash** (its own tab) -- point this at a firmware release's
  `shipping/firmware` folder (from
  github.com/wildbitscomputing/firmware/releases) if you want a handy
  Browse starting point, then **Browse for CSV...** to load
  `bulkgen1.csv`, `bulkgen2.csv`, or any other block-map CSV (this
  choice is remembered for next time, same as the FoenixMgr folder).
  Each row (sector + 8KB `.bin` file) gets a checkbox and an editable
  address dropdown -- pick any sector (01-3F). **Add
  Block...** lets you bring in a file that isn't in the CSV at all: pick
  any file, then pick its sector in a small dialog (defaults to the
  first free one). It becomes an ordinary row from that point on -- same
  checkbox, same editable address, same conflict detection, same map
  entry -- so you can build a block list from scratch this way too, not
  just add to a loaded CSV. If two rows end up targeting the same
  sector, both get a red conflict note ("also used by: ...") right in
  the list; this is purely informational and never blocks anything.
  Rows already matching what you last flashed there are unchecked by
  default (Check All / Clear All override this).

  Next to the list is a live 8x8 map of all 64 flash sectors --
  filled/empty cells, filenames overlaid on occupied ones, orange for
  conflicts, purple for a pending-erase placeholder (see below), and a
  dimmer shade for occupied-but-unchecked blocks. Sector 00 is drawn as
  a "RESERVED" cell -- you can't interact with it. Drag any other block to a
  different cell on the map to reassign its sector (this is the same
  edit as the dropdown -- they always stay in sync); dropping onto an
  already-occupied cell is allowed and just creates the same red
  conflict indicator.

  Moving a block this way (via the map or its address dropdown) doesn't
  move it on the actual chip by itself -- the sector it just vacated
  still holds the old content until something clears it. So the vacated
  sector automatically gets a placeholder row ("(vacated -- pending
  erase)"), pre-ticked in the **Del** column, so it's cleared for you the
  next time you run Delete Marked Blocks -- unless something else gets
  moved or added onto that same sector first, in which case the
  placeholder is dropped automatically since the new write already
  overwrites whatever was there.

  Flashing runs `fnxmgr.py --flash-bulk` on the checked rows (at
  whatever sectors they're currently assigned to) followed by
  `--boot flash`, exactly like the release's own `update.bat`/`.sh`,
  with the status line updating live through two stages per block --
  *writing* while a block is in progress, then *completed* once it's
  confirmed programmed (same for the delete step). Success vs. failure is
  told apart on the status line too: a successful run ends green
  ("complete"), while a failed one stays red and reports exactly how many
  of the selected blocks were written/cleared before it aborted ("X of Y
  written, then aborted"). Row order is preserved even when a sector
  appears twice in the CSV.

  Per-sector history (which file, and its modified-date at the time) is
  kept in the same preferences file. "Up to date" is a *newer-than*
  comparison against the source file's modification time (not
  filesystem "date created" -- that resets to extraction time on every
  zip unpack, which would make it useless for telling versions apart;
  modification time is what actually survives extraction), so mixing
  partial updates from different release dates still gets tracked
  sensibly.

  The tab also has a **pgz2flash** section: point it at your pgz2flash
  install folder (downloads at
  [github.com/rmsk2/pgz2flash/releases](https://github.com/rmsk2/pgz2flash/releases))
  once -- it's remembered like the FoenixMgr folder. **Convert PGZ...**
  then file-picks a `.pgz`, shows a dialog asking for the starting
  sector (with a live estimate of how many 8KB blocks it will occupy)
  plus the `-name` and `-desc` metadata, and runs `pgz2flash -onboard`
  in the background (no machine connection needed). The generated 8KB
  block files and their block-map CSV are parsed and installed straight
  into the same list and flash map, where they behave exactly like
  CSV-loaded rows -- flash them with the normal button. **Undo
  Placement** removes the blocks from the most recent conversion (you
  can chain several conversions and undo them in order).

  Each row also gets a **Del** checkbox: **Delete Marked Blocks** sends
  an 8KB block of 0xFF (NOR flash's erased state) to each ticked block to
  clear it from flash, and on success removes those entries from the list
  and map. (Unlike a normal flash it does not touch the boot source or
  per-sector history.)

  Every convert / add-block / delete action is also appended to a
  preference-backed operation log (`flash_operation_log` in
  `~/.wildbits_uploader_prefs.json`), so there's a timestamped audit
  trail of *what you did* (e.g. "converted demo.pgz", "added foo.bin",
  "deleted N blocks") alongside the per-sector flash history that says
  *what each sector holds*. Both are viewable in the app's **History**
  tab (a third tab, refreshed automatically each time you open it or via
  its Refresh button).

Every operation resets the machine when it finishes talking to it --
that's inherent to the debug port protocol (EXIT_DEBUG resets), not a
choice this tool makes.

## Notes

- All the actual serial/protocol work happens inside your own
  `fnxmgr.py`; this app just calls it with the right arguments and
  parses its output. If something misbehaves, the same troubleshooting
  you'd already do with FoenixMgr directly (cable, port, `foenixmgr.ini`,
  baud rate) applies here too -- check the Log panel, which shows the
  exact command line run for every action.
- `wildbits_probe.py` and `wildbits_info.py` are pure Python (no serial
  access) -- they only build/decode bytes. `wildbits_cli.py` is the only
  module that shells out to `fnxmgr.py`.

## Changelog

0.6

- launch FoenixMgr properly when FOENIXMGR environment variable is not present
- prevents flash block 00 from being used

0.5

- first public release

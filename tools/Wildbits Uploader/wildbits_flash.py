"""
Flash update support, built from studying an actual firmware release
(wildbitscomputing/firmware, release-2025.13.1's shipping/firmware/):

  bulkgen1.csv / bulkgen2.csv
      Headerless CSV, one row per flash write: `<hex sector>,<relative
      path to an 8KB .bin file>`. The only difference between the two is
      which SuperBASIC build folder (gen1/ vs gen2/) sb01-04.bin come
      from -- everything else (kernel, DOS, file manager, docs, etc.) is
      identical. Row order matters and is NOT deduplicated by sector, so
      flashing always preserves original row order.

  update.bat / update_interactive.bat / update.sh
      Always run two fnxmgr.py commands in sequence:
        1. fnxmgr.py --port PORT --flash-bulk bulkgenN.csv
        2. fnxmgr.py --port PORT --boot flash
      The second command is what makes the machine actually boot from
      the newly-written flash afterward (as opposed to RAM) -- both
      scripts always do this, so this module always does too.

Per-sector history (which .bin was last flashed there, and the source
file's mtime at that time) is kept in the shared preferences file via
wildbits_prefs, so the GUI can show "already up to date" vs "source file
has changed since you last flashed this sector" for each row.
"""

import csv
import os
import re
import tempfile
import time

import wildbits_cli as cli
import wildbits_prefs as prefs

HISTORY_KEY = "flash_history"

# Sector 0 is reserved -- never written or erased, as a last line of
# defense even if a caller slips a block-0 row through.
PROTECTED_SECTOR = 0


class FlashCsvError(Exception):
    pass


class BulkCsvRow:
    def __init__(self, sector, filename, abs_path, pending_erase=False):
        self.sector = sector            # int
        self.filename = filename        # as written in the CSV (relative)
        self.abs_path = abs_path        # resolved absolute path
        # True for a synthetic placeholder row standing in for a sector a
        # block was just moved off of in the editor -- there's no real
        # source file (abs_path is None), it just marks that the sector
        # still holds stale content on the actual chip until an erase is
        # committed. See wildbits_uploader_gui._schedule_vacated_sector.
        self.pending_erase = pending_erase

    @property
    def sector_hex(self):
        return f"{self.sector:02X}"

    def source_date(self):
        """The .bin file's own date, for comparing which version is newer.

        Uses modification time, not filesystem "date created": creation
        dates aren't preserved when a file is extracted from a zip (or
        copied) -- every file from a fresh release download would show
        the same "just now" creation date, making that useless for
        telling versions apart. Modification time *is* preserved by
        essentially all zip/copy tools, so it's what actually reflects
        when the .bin itself was last built/changed upstream.

        Returns None (rather than raising) for a pending-erase
        placeholder, which has no source file at all.
        """
        if not self.abs_path:
            return None
        try:
            return os.path.getmtime(self.abs_path)
        except OSError:
            return None

    def source_size(self):
        if not self.abs_path:
            return None
        try:
            return os.path.getsize(self.abs_path)
        except OSError:
            return None


def parse_bulk_csv(csv_path):
    """Parse a headerless sector,filename CSV. Paths are resolved relative
    to the CSV's own folder. Preserves row order (see module docstring --
    a sector can legitimately appear more than once)."""
    base_dir = os.path.dirname(os.path.abspath(csv_path))
    rows = []
    try:
        with open(csv_path, "r", newline="") as f:
            reader = csv.reader(f)
            for line_num, row in enumerate(reader, start=1):
                if not row or not row[0].strip():
                    continue
                if len(row) < 2:
                    raise FlashCsvError(
                        f"{csv_path}, line {line_num}: expected 'sector,filename', got {row!r}"
                    )
                try:
                    sector = int(row[0].strip(), 16)
                except ValueError:
                    raise FlashCsvError(
                        f"{csv_path}, line {line_num}: '{row[0]}' isn't a valid hex sector number"
                    )
                filename = row[1].strip()
                abs_path = filename if os.path.isabs(filename) else os.path.join(base_dir, filename)
                rows.append(BulkCsvRow(sector, filename, abs_path))
    except FileNotFoundError:
        raise FlashCsvError(f"CSV file not found: {csv_path}")

    if not rows:
        raise FlashCsvError(f"{csv_path} has no rows.")
    return rows


# ---------------------------------------------------------------------
# History tracking (stored in the shared preferences file)
# ---------------------------------------------------------------------

DATE_TOLERANCE_SECONDS = 2.0  # some filesystems only have ~2s timestamp resolution


def get_history():
    return prefs.get_pref(HISTORY_KEY, {}) or {}


def get_history_entry(sector_hex):
    return get_history().get(sector_hex)


def record_flash(sector_hex, filename, source_date):
    history = get_history()
    history[sector_hex] = {
        "filename": filename,
        "source_date": source_date,
        "flashed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    prefs.set_pref(HISTORY_KEY, history)


# ---------------------------------------------------------------------
# Operation log (a preference-backed audit trail of convert / add /
# delete actions, distinct from the per-sector flash history above --
# flash history says *what a sector holds*; this log says *what the user
# did* and when, e.g. "converted a PGZ", "added a block", "deleted N
# blocks").
# ---------------------------------------------------------------------

OPERATION_LOG_KEY = "flash_operation_log"

MAX_OPERATION_LOG = 500  # keep the log bounded so it never grows forever


WORKING_SET_KEY = "flash_working_set"


def save_working_set(csv_path, rows):
    """Persist the current in-editor block list -- the original CSV load
    plus whatever convert-pgz / add-block / delete edits have happened
    since -- so it survives an app restart.

    This is only meant to be called after a successful flash or erase,
    i.e. once the in-memory list has actually been committed to the
    hardware. A pending convert/add/delete that hasn't been flashed or
    erased yet is deliberately NOT persisted here -- it's still just an
    edit, not something that's true of the machine."""
    prefs.set_pref(WORKING_SET_KEY, {
        "csv_path": csv_path,
        "rows": [{"sector": row.sector_hex, "filename": row.filename,
                  "abs_path": row.abs_path} for row in rows],
    })


def load_working_set():
    """Returns (csv_path, [BulkCsvRow, ...]) restored from the last-saved
    working set, or None if there isn't one (or it's empty)."""
    data = prefs.get_pref(WORKING_SET_KEY, None)
    if not data or not data.get("rows"):
        return None
    rows = [BulkCsvRow(int(r["sector"], 16), r["filename"], r["abs_path"])
            for r in data["rows"]]
    return data.get("csv_path", ""), rows


def clear_working_set():
    """Drops any saved working set. Called when the user explicitly loads
    a different block-map CSV, since that supersedes whatever was
    previously committed and remembered."""
    prefs.set_pref(WORKING_SET_KEY, None)


def record_operation(op, **fields):
    """Append a timestamped entry to the persistent operation log in the
    preferences file. `op` is a short label ('convert_pgz', 'add_block',
    'delete_blocks'); `fields` is any extra key/value metadata worth
    keeping (pgz name/desc, sector, filename, count, ...)."""
    history = prefs.get_pref(OPERATION_LOG_KEY, []) or []
    entry = {"op": op, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    entry.update(fields)
    history.append(entry)
    if len(history) > MAX_OPERATION_LOG:
        history = history[-MAX_OPERATION_LOG:]
    prefs.set_pref(OPERATION_LOG_KEY, history)


def _fmt_date(epoch_seconds):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch_seconds))


def row_status(row):
    """Returns (status_text, up_to_date) for a CSV row, where up_to_date
    is True/False, or None for "file missing" or a pending-erase
    placeholder. Compares the candidate .bin's own date against the date
    of whatever was last flashed to that sector -- not exact-match, but
    "is this file at least as new" -- so partial/piecemeal updates from
    different release dates get sensibly compared."""
    if row.pending_erase:
        return ("Vacated by a move -- scheduled for erase", None)

    candidate_date = row.source_date()
    if candidate_date is None:
        return ("File not found", None)

    entry = get_history_entry(row.sector_hex)
    if entry is None:
        return ("Never flashed", False)

    recorded_date = entry.get("source_date")
    when_flashed = entry.get("flashed_at", "?")
    recorded_filename = entry.get("filename", "?")

    if recorded_date is None:
        return (f"Flashed {when_flashed}, but no file date was recorded then", False)

    if candidate_date > recorded_date + DATE_TOLERANCE_SECONDS:
        return (
            f"Newer file available (this file: {_fmt_date(candidate_date)}, "
            f"sector holds one dated {_fmt_date(recorded_date)})", False
        )

    name_note = "" if recorded_filename == row.filename else f", as {recorded_filename}"
    return (f"Up to date (flashed {when_flashed}{name_note}, dated {_fmt_date(recorded_date)})", True)


# ---------------------------------------------------------------------
# Flashing
# ---------------------------------------------------------------------

def _write_temp_csv(rows):
    """Writes a temp CSV with absolute paths (sidesteps any ambiguity
    about fnxmgr.py's working directory when it opens each sector_file)."""
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow([row.sector_hex.lower(), row.abs_path])
    return path


_PROGRESS_RE = re.compile(
    r"Attempting to program sector 0x([0-9A-Fa-f]+) with (.+)"
)


def _match_attempt_line(line):
    """Returns (sector_int, filename) if this line announces the start of
    programming a sector, else None."""
    match = _PROGRESS_RE.match(line.strip())
    if match:
        return int(match.group(1), 16), match.group(2).strip()
    return None


def _is_programmed_line(line):
    return "Flash sector programmed" in line


def _parse_programmed_sectors(stdout):
    """Scan fnxmgr.py's --flash-bulk output for sectors that were
    actually confirmed programmed (i.e. "Attempting..." was immediately
    followed by "...programmed..." before anything else went wrong), so
    history only gets updated for what genuinely succeeded even if a
    later sector in the same run fails."""
    programmed = []
    pending = None  # (sector_int, filename) waiting to see "programmed"
    for line in stdout.splitlines():
        match = _match_attempt_line(line)
        if match:
            pending = match
        elif _is_programmed_line(line) and pending is not None:
            programmed.append(pending)
            pending = None
    return programmed


def _stage_progress_handler(progress):
    """Returns an on_line callback (for run_fnxmgr_streaming) that drives
    a two-stage per-block progress callback: progress(sector, filename,
    stage) is called with stage='writing' when a block's programming
    starts and stage='completed' when it's confirmed finished. Lets the
    caller show live per-block progress that reflects both stages instead
    of just "started"."""
    pending = None  # (sector_int, filename) currently being written

    def on_line(line):
        nonlocal pending
        match = _match_attempt_line(line)
        if match:
            pending = match
            progress(match[0], match[1], "writing")
        elif pending is not None and _is_programmed_line(line):
            progress(pending[0], pending[1], "completed")
            pending = None

    return on_line


def flash_bulk(script_path, port, rows, timeout=180, log=lambda msg: None,
               progress=lambda sector, filename, stage: None):
    """Flash the given rows (a subset of parse_bulk_csv's result, in the
    order they should be written) via fnxmgr.py --flash-bulk, then
    fnxmgr.py --boot flash to make the machine boot from it. Updates
    flash history for every sector confirmed programmed, even if the
    overall run ultimately raises partway through. Calls
    progress(sector_int, filename, stage) in real time as each block is
    written -- stage 'writing' when it starts, 'completed' when it's
    confirmed programmed -- so the caller can show live two-stage status
    instead of a single "please wait" for the whole run. Returns the list
    of (sector_int, filename) that were confirmed programmed."""
    if not rows:
        raise FlashCsvError("No blocks selected.")
    rows = [r for r in rows if r.sector != PROTECTED_SECTOR]
    if not rows:
        raise FlashCsvError("No flashable blocks selected (sector 0 is reserved).")
    pending = [r for r in rows if r.pending_erase]
    if pending:
        sectors = ", ".join(f"0x{r.sector:02X}" for r in pending)
        raise FlashCsvError(
            f"Sector(s) {sectors} are pending-erase placeholders with no file to "
            f"flash -- use Delete Marked Blocks to clear them instead."
        )

    temp_csv = _write_temp_csv(rows)
    row_by_sector_and_name = {(r.sector, os.path.basename(r.abs_path)): r for r in rows}

    try:
        log(f"Flashing {len(rows)} block(s)...")
        try:
            stdout = cli.run_fnxmgr_streaming(
                script_path, ["--port", port, "--flash-bulk", temp_csv],
                timeout=timeout, log=log, on_line=_stage_progress_handler(progress),
            )
        except cli.FnxMgrError as exc:
            # Even on failure, fnxmgr.py's partial stdout (captured on
            # the exception) tells us what *did* succeed before it broke.
            stdout = getattr(exc, "partial_stdout", "") or str(exc)
            programmed = _parse_programmed_sectors(stdout)
            _update_history(programmed, row_by_sector_and_name)
            raise

        programmed = _parse_programmed_sectors(stdout)
        _update_history(programmed, row_by_sector_and_name)

        log("Setting boot source to flash...")
        cli.run_fnxmgr(script_path, ["--port", port, "--boot", "flash"],
                        timeout=timeout, log=log)

        return programmed
    finally:
        try:
            os.unlink(temp_csv)
        except OSError:
            pass


def _update_history(programmed, row_by_sector_and_name):
    for sector, filename_in_output in programmed:
        row = row_by_sector_and_name.get((sector, os.path.basename(filename_in_output)))
        if row is None:
            # fnxmgr echoes back whatever path we gave it, which was
            # already the absolute path -- fall back to matching by
            # sector alone if the basename match didn't hit.
            for (s, _n), candidate in row_by_sector_and_name.items():
                if s == sector:
                    row = candidate
                    break
        if row is not None:
            date_value = row.source_date()
            if date_value is not None:
                record_flash(row.sector_hex, row.filename, date_value)


# ---------------------------------------------------------------------
# Deleting / clearing blocks
# ---------------------------------------------------------------------

def erase_blocks(script_path, port, rows, timeout=180, log=lambda msg: None,
                 progress=lambda sector, filename, stage: None):
    """Clear the given rows' flash sectors by writing an 8KB block of
    0xFF to each, via fnxmgr.py --flash-bulk (each marked row points at a
    fresh 8KB erase-filled .bin in a temp CSV). Reports live two-stage
    progress via progress(sector, filename, stage) -- 'writing' then
    'completed'. Returns the list of (sector_int, filename) that were
    confirmed programmed, so the caller can remove exactly the
    successfully-cleared entries from its list and map.

    Unlike a normal firmware flash this deliberately does *not* change
    the boot source (we're clearing blocks, not installing a new OS), and
    it does not update per-sector history -- a deleted block isn't a
    firmware version to remember. It *does* log the deletion to the
    operation log so the action is tracked in preferences."""
    if not rows:
        raise FlashCsvError("No blocks marked for deletion.")
    rows = [r for r in rows if r.sector != PROTECTED_SECTOR]
    if not rows:
        raise FlashCsvError("No erasable blocks selected (sector 0 is reserved).")

    erase_path = _write_erase_block()
    fd, csv_path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", newline="") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow([row.sector_hex.lower(), erase_path])

    try:
        log(f"Erasing {len(rows)} block(s) (writing 8KB of 0xFF to each)...")
        stdout = cli.run_fnxmgr_streaming(
            script_path, ["--port", port, "--flash-bulk", csv_path],
            timeout=timeout, log=log, on_line=_stage_progress_handler(progress),
        )
    except cli.FnxMgrError as exc:
        # Even on failure, partial stdout tells us what *did* succeed.
        stdout = getattr(exc, "partial_stdout", "") or str(exc)
    finally:
        for path in (erase_path, csv_path):
            try:
                os.unlink(path)
            except OSError:
                pass

    programmed = _parse_programmed_sectors(stdout)
    if programmed:
        record_operation("delete_blocks",
                         count=len(programmed),
                         sectors=[f"{s:02X}" for s, _n in programmed])
    return programmed


def _write_erase_block():
    """Write an 8KB temp file filled with 0xFF -- the erased state of NOR
    flash -- which is what's sent to a block marked for deletion to clear
    it."""
    fd, path = tempfile.mkstemp(suffix=".bin")
    with os.fdopen(fd, "wb") as f:
        f.write(b"\xFF" * 8192)
    return path

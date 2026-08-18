"""
A thin wrapper around the user's own, already-working FoenixMgr install
(https://github.com/pweingar/FoenixMgr) -- shells out to its `fnxmgr.py`
CLI via subprocess rather than reimplementing the debug-port wire
protocol. The user knows their own FoenixMgr setup works; this just
drives it with the right arguments and parses its output.

Only wildbits_probe.py (the hand-assembled probe program's bytes) and
the pure decode logic in wildbits_info.py are still ours -- everything
that actually touches the serial port goes through fnxmgr.py.
"""

import os
import re
import subprocess
import sys
import tempfile
import time

DEFAULT_TIMEOUT = 30  # seconds, per subprocess call


class FnxMgrError(Exception):
    def __init__(self, message, partial_stdout=""):
        super().__init__(message)
        self.partial_stdout = partial_stdout


class FnxMgrNotFound(FnxMgrError):
    pass


# ---------------------------------------------------------------------
# Locating fnxmgr.py within a user-selected folder
# ---------------------------------------------------------------------

def find_fnxmgr_script(base_dir, max_depth=4):
    """Search `base_dir` (and a few levels of subfolders, since a fresh
    GitHub download often nests it as e.g. FoenixMgr-master/FoenixMgr/)
    for fnxmgr.py. Returns the full path, or raises FnxMgrNotFound."""
    base_dir = os.path.abspath(base_dir)
    if not os.path.isdir(base_dir):
        raise FnxMgrNotFound(f"Not a folder: {base_dir}")

    base_depth = base_dir.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(base_dir):
        depth = root.rstrip(os.sep).count(os.sep) - base_depth
        if depth > max_depth:
            dirs[:] = []
            continue
        if "fnxmgr.py" in files:
            return os.path.join(root, "fnxmgr.py")

    raise FnxMgrNotFound(
        f"Couldn't find fnxmgr.py anywhere under:\n{base_dir}\n\n"
        "Pick the folder you downloaded/cloned FoenixMgr into."
    )


# ---------------------------------------------------------------------
# Running fnxmgr.py
# ---------------------------------------------------------------------

def get_script_cwd(script_path):
    """Return the working directory to launch fnxmgr.py from.

    fnxmgr.py lives in a nested FoenixMgr/FoenixMgr/ folder, but is
    expected to be called from the repo root, one level up, where the
    .ini file lives."""
    return os.path.dirname(os.path.dirname(script_path))


def run_fnxmgr(script_path, args, timeout=DEFAULT_TIMEOUT, log=lambda msg: None):
    """Run fnxmgr.py with the given argument list, from the directory
    get_script_cwd() resolves. Returns (stdout, stderr). Raises
    FnxMgrError on a nonzero exit code or if the process doesn't finish
    within `timeout`."""
    cmd = [sys.executable, script_path] + list(args)
    log(f"$ {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd, cwd=get_script_cwd(script_path), capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if partial:
            log(partial.rstrip())
        raise FnxMgrError(
            f"fnxmgr.py didn't finish within {timeout}s. Check the port and that "
            f"the machine is powered on.", partial_stdout=partial,
        )
    except OSError as exc:
        raise FnxMgrError(f"Couldn't run fnxmgr.py: {exc}")

    if result.stdout:
        log(result.stdout.rstrip())
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "(no output)"
        raise FnxMgrError(f"fnxmgr.py exited with code {result.returncode}:\n{detail}",
                           partial_stdout=result.stdout or "")
    if result.stderr.strip():
        # Some non-fatal warnings still land on stderr even with rc==0
        log("[stderr] " + result.stderr.strip())

    return result.stdout, result.stderr


def run_fnxmgr_streaming(script_path, args, timeout=DEFAULT_TIMEOUT,
                          log=lambda msg: None, on_line=lambda line: None):
    """Like run_fnxmgr, but reads fnxmgr.py's stdout line-by-line as it's
    produced (via Popen) instead of waiting for the whole process to
    finish, calling on_line(line) for each one as it arrives. Used for
    --flash-bulk so the caller can show live per-block progress rather
    than a single "please wait" with no feedback until it's all done.
    Returns the full accumulated stdout. Raises FnxMgrError the same way
    run_fnxmgr does."""
    cmd = [sys.executable, script_path] + list(args)
    log(f"$ {' '.join(cmd)}")

    lines = []
    try:
        proc = subprocess.Popen(
            cmd, cwd=get_script_cwd(script_path), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
    except OSError as exc:
        raise FnxMgrError(f"Couldn't run fnxmgr.py: {exc}")

    deadline = time.monotonic() + timeout
    try:
        for line in proc.stdout:
            lines.append(line)
            log(line.rstrip())
            on_line(line.rstrip())
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                raise FnxMgrError(
                    f"fnxmgr.py didn't finish within {timeout}s. Check the port and "
                    f"that the machine is powered on.", partial_stdout="".join(lines),
                )
        returncode = proc.wait(timeout=max(1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise FnxMgrError(
            f"fnxmgr.py didn't finish within {timeout}s. Check the port and that "
            f"the machine is powered on.", partial_stdout="".join(lines),
        )
    finally:
        if proc.stdout:
            proc.stdout.close()

    stdout = "".join(lines)
    if returncode != 0:
        detail = stdout.strip() or "(no output)"
        raise FnxMgrError(f"fnxmgr.py exited with code {returncode}:\n{detail}",
                           partial_stdout=stdout)

    return stdout


def list_ports(script_path, timeout=DEFAULT_TIMEOUT):
    """Parse `fnxmgr.py --list-ports` output into [(device, description), ...]."""
    stdout, _ = run_fnxmgr(script_path, ["--list-ports"], timeout=timeout)
    ports = []
    device = None
    description = None
    for line in stdout.splitlines():
        line = line.rstrip()
        if not line:
            if device:
                ports.append((device, description or ""))
            device = None
            description = None
        elif line.startswith(" "):
            if line.strip().startswith("Description:"):
                description = line.split(":", 1)[1].strip()
        else:
            device = line.strip()
    if device:
        ports.append((device, description or ""))
    return ports


# ---------------------------------------------------------------------
# High-level operations
# ---------------------------------------------------------------------

def test_connection(script_path, port, timeout=DEFAULT_TIMEOUT, log=lambda msg: None):
    """Runs --revision. Returns the revision as an int."""
    stdout, _ = run_fnxmgr(script_path, ["--port", port, "--revision"],
                            timeout=timeout, log=log)
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if re.fullmatch(r"[0-9A-Fa-f]+", line):
            return int(line, 16)
    raise FnxMgrError(f"Couldn't parse a revision code from fnxmgr.py's output:\n{stdout}")


def upload_and_run(script_path, filepath, port, timeout=DEFAULT_TIMEOUT, log=lambda msg: None):
    """Runs --run-pgz or --run-pgx, picked by the file's extension."""
    lower = filepath.lower()
    if lower.endswith(".pgz"):
        flag = "--run-pgz"
    elif lower.endswith(".pgx"):
        flag = "--run-pgx"
    else:
        raise FnxMgrError(f"Unrecognized extension for '{filepath}' (expected .pgz or .pgx).")
    run_fnxmgr(script_path, ["--port", port, flag, filepath], timeout=timeout, log=log)


def copy_to_sdcard(script_path, filepath, port, timeout=DEFAULT_TIMEOUT, log=lambda msg: None):
    run_fnxmgr(script_path, ["--port", port, "--copy", filepath], timeout=timeout, log=log)


# ---------------------------------------------------------------------
# Machine info: build a tiny PGZ for the probe program, launch it via
# --run-pgz, wait for it to finish, then read the results back via
# --dump (8 bytes at a time, to keep the hex-dump output trivial and
# unambiguous to parse -- see the regex in _dump_bytes below).
# ---------------------------------------------------------------------

def _write_probe_pgz(code_addr, code_bytes):
    """PGZ format: 'z' header (4-byte fields) + one (addr,len,data) block
    + a (start_addr, 0) marker. fnxmgr's own pgz.py/foenix.py handles the
    reset-vector/CROSSDEV patching from there -- same as any other pgz."""
    out = bytearray([0x7A])  # 'z' -> 4-byte address/size fields
    out += code_addr.to_bytes(4, "little")
    out += len(code_bytes).to_bytes(4, "little")
    out += code_bytes
    out += code_addr.to_bytes(4, "little")
    out += (0).to_bytes(4, "little")
    return bytes(out)


def _dump_bytes(script_path, port, address, count, timeout, log):
    """Read `count` bytes (<=8, to keep the hexdump format unambiguous --
    fnxmgr inserts an extra space mid-row only past the 8th byte) from
    `address` via --dump, and parse them back out of its pretty-printed
    hex dump output."""
    if count > 8:
        raise ValueError("_dump_bytes only supports up to 8 bytes at a time")
    stdout, _ = run_fnxmgr(
        script_path,
        ["--port", port, "--dump", f"{address:X}", "--count", f"{count:X}"],
        timeout=timeout, log=log,
    )
    match = re.search(r"^[0-9A-Fa-f]{6}:\s*([0-9A-Fa-f]{2,16})\s", stdout, re.MULTILINE)
    if not match:
        raise FnxMgrError(f"Couldn't parse a hex dump from fnxmgr.py's output:\n{stdout}")
    hex_str = match.group(1)
    if len(hex_str) != count * 2:
        raise FnxMgrError(
            f"Expected {count} bytes back from the dump, got {len(hex_str) // 2}."
        )
    return bytes.fromhex(hex_str)


def get_machine_info_probe_result(script_path, port, code_addr, result_addr, result_len,
                                   boot_wait, timeout=DEFAULT_TIMEOUT,
                                   sleep_fn=time.sleep, log=lambda msg: None):
    """Builds and launches the live probe (see wildbits_probe.py), waits
    for it to boot and run, then reads back `result_len` bytes from
    `result_addr`. Returns the raw result bytes. Causes two resets (one
    when --run-pgz launches the probe, one implicitly next time you run
    any other command -- the probe itself just spins forever once done,
    it doesn't reset the machine a second time on its own)."""
    import wildbits_probe as probe

    code = probe.build_probe_program(code_addr=code_addr, result_addr=result_addr)
    pgz_bytes = _write_probe_pgz(code_addr, code)

    with tempfile.NamedTemporaryFile(suffix=".pgz", delete=False) as f:
        f.write(pgz_bytes)
        temp_path = f.name

    try:
        log(f"Launching the probe program (temp file: {temp_path})...")
        run_fnxmgr(script_path, ["--port", port, "--run-pgz", temp_path],
                   timeout=timeout, log=log)

        log(f"Waiting {boot_wait:.1f}s for the machine to boot and run the probe...")
        sleep_fn(boot_wait)

        log("Reading back the probe's results...")
        result = bytearray()
        offset = 0
        while offset < result_len:
            chunk_len = min(8, result_len - offset)
            result += _dump_bytes(script_path, port, result_addr + offset, chunk_len,
                                   timeout, log)
            offset += chunk_len
        return bytes(result)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass

# Append to wildbits_cli.py

class Pgz2FlashNotFound(FnxMgrError):
    pass

def find_pgz2flash_executable(base_dir):
    """Locate pgz2flash executable or python script within base_dir."""
    base_dir = os.path.abspath(base_dir)
    if not os.path.isdir(base_dir):
        raise Pgz2FlashNotFound(f"Not a folder: {base_dir}")

    # Common executable names across platforms
    candidates = ["pgz2flash", "pgz2flash.exe", "pgz2flash.py"]
    for root, _, files in os.walk(base_dir):
        for candidate in candidates:
            if candidate in files:
                return os.path.join(root, candidate)

    raise Pgz2FlashNotFound(
        f"Could not find pgz2flash executable or script under:\n{base_dir}\n\n"
        "Please specify a valid pgz2flash release installation folder."
    )

def run_pgz2flash_onboard(pgz2flash_exec, pgz_path, start_sector, name, desc,
                          out_dir, log=lambda msg: None, timeout=DEFAULT_TIMEOUT):
    """Launch pgz2flash in -onboard mode to convert a .pgz into 8KB flash
    blocks (plus a FoenixMgr block-map CSV) starting at start_sector.

    pgz2flash's own CLI (from rmsk2/pgz2flash) is:
        pgz2flash -pgz <pgz> -name <name> -desc <desc> -onboard <start> -out <prefix>
    where -onboard is the start block number and -out is the *prefix*
    used for the generated .bin block files and the CSV. We pass -out as
    a path inside `out_dir` (a fresh staging folder per call), then return
    the absolute path of the generated block-map CSV so the caller can
    install those blocks into its list/map with its usual CSV machinery."""
    exec_dir = os.path.dirname(pgz2flash_exec)

    # .py releases need the interpreter; binaries (.exe / no-extension) run directly.
    if pgz2flash_exec.lower().endswith(".py"):
        cmd = [sys.executable, pgz2flash_exec]
    else:
        cmd = [pgz2flash_exec]

    prefix = os.path.join(out_dir, os.path.splitext(os.path.basename(pgz_path))[0])
    cmd.extend(["-pgz", pgz_path, "-name", name, "-desc", desc,
                "-onboard", str(start_sector), "-out", prefix])

    log(f"$ {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=exec_dir, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise FnxMgrError(
            f"pgz2flash didn't finish within {timeout}s.",
            partial_stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
        )

    if result.stdout:
        log(result.stdout.rstrip())
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "(no output)"
        raise FnxMgrError(f"pgz2flash exited with code {result.returncode}:\n{detail}",
                          partial_stdout=result.stdout or "")

    # Onboard mode always writes a FoenixMgr block-map CSV alongside the
    # .bin blocks; since out_dir is a fresh staging folder, any .csv here
    # is the one we just generated.
    csvs = [os.path.join(out_dir, n) for n in sorted(os.listdir(out_dir))
            if n.lower().endswith(".csv")]
    if not csvs:
        raise FnxMgrError(
            "pgz2flash ran but produced no block-map CSV to install. "
            f"Looked in:\n{out_dir}"
        )
    return csvs[0]
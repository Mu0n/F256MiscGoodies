"""
Machine/FPGA/kernel info probing.

IMPORTANT, HARDWARE-CONFIRMED CAVEAT: the debug port's READ_MEM talks
straight to system RAM over a sideband bus -- it does NOT go through the
65C02's normal address-decode path, so it can't see memory-mapped IO/FPGA
registers like $D6A7. Those are only visible to code actually executing
on the CPU. So instead of reading $D6A7 etc. directly (which would
silently return garbage/RAM contents, not the register), this module
uploads and runs a tiny probe program (see wildbits_probe.py) that reads
the registers *on the machine* and stashes the results in plain RAM,
which *is* safely readable over the debug port afterward.

Register layout, reverse-engineered from SuperBASIC's own boot-banner
source (wildbitscomputing/superbasic,
source/modules/kernel/startup/bannerinfo.asm) since none of this is part
of the documented debug-port command set:

  $D6A7         Machine ID byte:
                  bit 7 (0x80): core generation -- set = Gen 2, clear = Gen 1
                  bit 4 (0x10): set = K-family, clear = Jr-family
                    K-family:   bit 1 (0x02) clear = K2,  set = K
                    Jr-family:  bit 5 (0x20) clear = Jr,  set = Jr2
                  low bits (see decode_machine_id): Wildbits vs Foenix branding
  $D6A8-$D6A9   Two ASCII characters
  $D6AA-$D6AD   Four bytes, displayed as hex (highest address first) --
                together with $D6A8-$D6A9 this is the "hardware/ROM build
                info" segment of the banner (labeled that way in a
                comment in bannerinfo.asm), so it's presented here as the
                closest available stand-in for "FPGA version".
  $E008         NUL-terminated ASCII string: printed by the banner's
                "print_kernel_info" step, i.e. the MicroKernel's own
                self-reported info/version string.

SuperBASIC's *own* build version (as opposed to the MicroKernel's) is
assembled into SuperBASIC's own flash image as a symbol-relative label,
not a fixed absolute address -- so unlike the items above, it can't be
reliably read even via a live probe without that specific build's
label/map file. It's visible on the boot banner itself; this tool
doesn't attempt to guess an address for it rather than risk showing
something wrong.

This whole probe causes a machine reset when --run-pgz launches it. Once
it's read back, running any other fnxmgr.py command will reset the
machine again anyway (every debug-port session ends that way), returning
it to a normal boot -- so no separate "reset back" step is needed here.

See wildbits_cli.py for the actual orchestration (building the probe's
PGZ file, launching it via `fnxmgr.py --run-pgz`, waiting for boot, and
reading the results back via `fnxmgr.py --dump`). This module only holds
the pure, hardware-independent decoding logic.
"""

from dataclasses import dataclass

DEFAULT_BOOT_WAIT_SECONDS = 3.0


@dataclass
class MachineInfo:
    raw_machine_id: int
    generation: str        # "Gen 1" or "Gen 2"
    family: str             # "K", "K2", "Jr", or "Jr2"
    branding: str           # "wildbits/" or "FOENIX "
    model_summary: str      # e.g. "wildbits/k2 (Gen 2)"
    build_chars: str        # the two ASCII chars at $D6A8-$D6A9
    build_hex: str          # the four bytes at $D6AA-$D6AD as hex, banner order
    kernel_info: str        # NUL-terminated string read from $E008


def decode_machine_id(byte_value):
    """Decode the $D6A7 machine ID byte the same way bannerinfo.asm does."""
    generation = "Gen 2" if (byte_value & 0x80) else "Gen 1"

    if byte_value & 0x10:
        family = "K" if (byte_value & 0x02) else "K2"
    else:
        family = "Jr2" if (byte_value & 0x20) else "Jr"

    masked = byte_value & 0x32
    branding = "wildbits/" if masked in (0x10, 0x22) else "FOENIX "

    model_summary = f"{branding}{family} ({generation})"
    return generation, family, branding, model_summary


def _ascii_or_dot(data):
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def build_machine_info(probe_result):
    """Turn a wildbits_probe.ProbeResult into a decoded MachineInfo."""
    generation, family, branding, model_summary = decode_machine_id(probe_result.machine_id)

    build_chars = _ascii_or_dot(probe_result.build_info[0:2])
    # Banner prints $D6AD, $D6AC, $D6AB, $D6AA in that (descending) order
    build_hex = "".join(f"{b:02X}" for b in reversed(probe_result.build_info[2:6]))

    nul_pos = probe_result.kernel_info_raw.find(b"\x00")
    kernel_bytes = (probe_result.kernel_info_raw if nul_pos == -1
                     else probe_result.kernel_info_raw[:nul_pos])
    kernel_info = _ascii_or_dot(kernel_bytes) or "(empty)"

    return MachineInfo(
        raw_machine_id=probe_result.machine_id,
        generation=generation,
        family=family,
        branding=branding,
        model_summary=model_summary,
        build_chars=build_chars,
        build_hex=build_hex,
        kernel_info=kernel_info,
    )

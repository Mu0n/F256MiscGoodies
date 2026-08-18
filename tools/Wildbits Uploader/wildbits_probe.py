"""
Live machine-info probe.

The debug port's READ_MEM appears to go straight to system RAM over a
sideband bus, bypassing the normal 65C02 address-decode path -- so it
can't see memory-mapped IO/FPGA registers like $D6A7 (confirmed: these
are only visible to code actually executing on the CPU). To read them, we
have to run real code on the machine.

This module hand-assembles a tiny 65C02 program that:
  1. Copies the machine-ID and "build info" IO registers ($D6A7-$D6AD,
     7 bytes) into plain RAM.
  2. Copies the MicroKernel's NUL-terminated info string from $E008 into
     plain RAM too (folded into the same live probe for consistency,
     rather than assuming that address is safe to read passively).
  3. Writes a "done" marker byte.
  4. Loops on itself forever.

It's uploaded and launched the same way a PGZ/PGX program is (reset
vector + CROSSDEV springboard patch, see wildbits_pgfiles.py), so the
MicroKernel's normal boot/hardware-init runs first -- which matters,
since the IO page needs to actually be mapped in for the register reads
to mean anything. The caller then waits for boot to finish, re-enters
debug mode (this does not reset -- it's a separate command from EXIT),
and reads the results back from the plain-RAM scratch area, which *is*
safely readable over the debug port.
"""

from dataclasses import dataclass

# Scratch addresses for the probe. Chosen low in RAM, comfortably apart so
# the (~50 byte) code can't run into the (40 byte) results area.
DEFAULT_CODE_ADDR = 0x0400
DEFAULT_RESULT_ADDR = 0x0460

MACHINE_ID_IO_ADDR = 0xD6A7   # 7 bytes: machine ID + 6 "build info" bytes
KERNEL_INFO_IO_ADDR = 0xE008
KERNEL_INFO_LEN = 32          # copied in full; we trim at the first NUL after reading back

RESULT_MACHINE_LEN = 7
RESULT_KERNEL_OFFSET = RESULT_MACHINE_LEN
RESULT_DONE_OFFSET = RESULT_MACHINE_LEN + KERNEL_INFO_LEN
RESULT_TOTAL_LEN = RESULT_DONE_OFFSET + 1

DONE_MARKER = 0xAA


class Assembler65C02:
    """Just enough of a hand-assembler to build this one probe program
    legibly, instead of a hard-to-verify hand-written hex blob."""

    def __init__(self, origin):
        self.origin = origin
        self.code = bytearray()

    @property
    def pc(self):
        return self.origin + len(self.code)

    def lda_abs(self, addr):
        self.code += bytes([0xAD, addr & 0xFF, (addr >> 8) & 0xFF])
        return self

    def lda_abs_x(self, addr):
        self.code += bytes([0xBD, addr & 0xFF, (addr >> 8) & 0xFF])
        return self

    def sta_abs(self, addr):
        self.code += bytes([0x8D, addr & 0xFF, (addr >> 8) & 0xFF])
        return self

    def sta_abs_x(self, addr):
        self.code += bytes([0x9D, addr & 0xFF, (addr >> 8) & 0xFF])
        return self

    def ldx_imm(self, value):
        self.code += bytes([0xA2, value & 0xFF])
        return self

    def lda_imm(self, value):
        self.code += bytes([0xA9, value & 0xFF])
        return self

    def inx(self):
        self.code += bytes([0xE8])
        return self

    def cpx_imm(self, value):
        self.code += bytes([0xE0, value & 0xFF])
        return self

    def bne(self, target_addr):
        offset = target_addr - (self.pc + 2)
        if not (-128 <= offset <= 127):
            raise ValueError(f"BNE target out of range: offset {offset}")
        self.code += bytes([0xD0, offset & 0xFF])
        return self

    def jmp_abs(self, addr):
        self.code += bytes([0x4C, addr & 0xFF, (addr >> 8) & 0xFF])
        return self


def build_probe_program(code_addr=DEFAULT_CODE_ADDR, result_addr=DEFAULT_RESULT_ADDR):
    """Assemble the probe program. Returns the raw code bytes."""
    asm = Assembler65C02(code_addr)

    # --- Copy the 7 machine-ID/build-info bytes ($D6A7-$D6AD) ---
    for i in range(RESULT_MACHINE_LEN):
        asm.lda_abs(MACHINE_ID_IO_ADDR + i)
        asm.sta_abs(result_addr + i)

    # --- Copy the 32-byte kernel info string ($E008+) via a small loop ---
    loop_start = asm.pc
    asm.ldx_imm(0)
    loop_top = asm.pc
    asm.lda_abs_x(KERNEL_INFO_IO_ADDR)
    asm.sta_abs_x(result_addr + RESULT_KERNEL_OFFSET)
    asm.inx()
    asm.cpx_imm(KERNEL_INFO_LEN)
    asm.bne(loop_top)

    # --- Done marker, then spin forever ---
    asm.lda_imm(DONE_MARKER)
    asm.sta_abs(result_addr + RESULT_DONE_OFFSET)
    done_addr = asm.pc
    asm.jmp_abs(done_addr)

    return bytes(asm.code)


@dataclass
class ProbeResult:
    machine_id: int
    build_info: bytes    # 6 bytes: $D6A8-$D6AD, in memory order
    kernel_info_raw: bytes  # up to 32 bytes, NUL-trimmed by the caller


def decode_probe_result(result_bytes):
    """Decode the RESULT_TOTAL_LEN-byte block read back from result_addr."""
    if len(result_bytes) < RESULT_TOTAL_LEN:
        raise ValueError(
            f"Expected {RESULT_TOTAL_LEN} result bytes, got {len(result_bytes)}."
        )
    machine_id = result_bytes[0]
    build_info = bytes(result_bytes[1:RESULT_MACHINE_LEN])
    kernel_raw = bytes(result_bytes[RESULT_KERNEL_OFFSET:RESULT_KERNEL_OFFSET + KERNEL_INFO_LEN])
    done = result_bytes[RESULT_DONE_OFFSET]

    if done != DONE_MARKER:
        raise ValueError(
            "Probe hasn't finished yet (done-marker not set) -- the machine may "
            "still be booting. Try again, or increase the boot-wait time."
        )

    return ProbeResult(machine_id=machine_id, build_info=build_info, kernel_info_raw=kernel_raw)

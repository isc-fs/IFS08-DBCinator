# SPDX-License-Identifier: proprietary
"""Shared types + bit-arithmetic helpers for the per-ECU specs.

Why this module exists
----------------------
The IFS08 wire contract has cell / NTC grids that span 24+ frames; the
DBC Motorola bit numbering (start_bit = 8*byte_idx + 7 for a BE field
whose MSB is at byte byte_idx) is exactly the kind of arithmetic that
wants to come out of a loop. Keep that loop in one place and call it
from every per-ECU spec.

Per-ECU files (spec/ams.py, spec/vcu.py, spec/udv.py) export a single
MESSAGES list using the Message / Signal dataclasses defined here.
Generators in tools/ collect the lists and emit artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional


# --- Physical layout constants shared by the AMS pit-diag grid -------------
#
# These live in `common.py` rather than `ams.py` only because the pit-tool
# decoders need to mirror them too -- if the cell or NTC count changes,
# every consumer needs to know.

MODULES        = 5
CELLS_PER_MOD  = 19
NTCS_PER_MOD   = 40
TOTAL_CELLS    = MODULES * CELLS_PER_MOD     # 95
TOTAL_NTCS     = MODULES * NTCS_PER_MOD      # 200


# --- DBC bit-numbering helpers ----------------------------------------------

def be_start_bit(byte_idx: int) -> int:
    """Motorola/BE start bit for a field whose MSB sits at byte `byte_idx`.
    DBC convention: bit 7 of byte 0 == 7, bit 7 of byte 1 == 15, ..."""
    return 8 * byte_idx + 7


def le_start_bit(byte_idx: int) -> int:
    """Intel/LE start bit: LSB of byte byte_idx."""
    return 8 * byte_idx


def BE(byte_idx: int, length: int, signed: bool = False) -> "BitField":
    """Big-endian field at byte byte_idx, `length` bits wide."""
    return BitField(start_bit=be_start_bit(byte_idx),
                    length=length, byte_order="0",
                    sign=("-" if signed else "+"))


def LE(byte_idx_or_start_bit: int, length: int, signed: bool = False,
       *, raw: bool = False) -> "BitField":
    """Little-endian field starting at byte `byte_idx_or_start_bit` (default)
    or at exact bit `byte_idx_or_start_bit` if raw=True (for the rare cases
    where you need a sub-byte offset)."""
    start = byte_idx_or_start_bit if raw else le_start_bit(byte_idx_or_start_bit)
    return BitField(start_bit=start, length=length, byte_order="1",
                    sign=("-" if signed else "+"))


# --- Core dataclasses ------------------------------------------------------

@dataclass(frozen=True)
class BitField:
    start_bit: int
    length: int
    byte_order: str   # '0' BE, '1' LE
    sign: str         # '+' '-'


@dataclass
class Signal:
    name: str
    field: BitField
    factor: float = 1.0
    offset: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0
    unit: str = ""
    receivers: List[str] = field(default_factory=lambda: ["Vector__XXX"])
    comment: str = ""
    # Optional: enum dict {0: "Start", 1: "Precharge", ...}; emitted as DBC VAL_.
    enum: Optional[dict] = None


@dataclass
class Message:
    can_id: int
    name: str
    dlc: int
    sender: str
    signals: List[Signal] = field(default_factory=list)
    comment: str = ""
    # Steady-state cadence in milliseconds. None for one-shot / on-demand
    # frames (BL trigger, pit-diag enable/ACK, boot-trace). Used by
    # tools/bus_load.py to estimate bus utilisation; not surfaced in
    # the DBC itself.
    period_ms: Optional[int] = None
    # When this frame is *conditionally* emitted (e.g. pit-diag stream
    # only when enabled, charger-only frames only in Charge state),
    # set the condition name. bus_load.py groups by condition so the
    # report shows base-load vs worst-case-with-everything-on.
    condition: str = "always"

    @property
    def hex_id(self) -> str:
        return f"0x{self.can_id:03X}"


# --- Cross-ECU node list ---------------------------------------------------

# Every ECU on the bus + external tools that emit or consume frames. Each
# Message.sender must be one of these (audited by check_ids.py).
NODES = ["AMS", "VCU", "UDV", "ECU", "Pit_Tool"]

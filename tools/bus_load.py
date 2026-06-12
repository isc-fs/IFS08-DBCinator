#!/usr/bin/env python3
# SPDX-License-Identifier: proprietary
"""Classical-CAN bus-load estimator over one or more DBC files.

For each declared message in the input DBC(s), computes the worst-case
on-wire frame size including stuff bits, multiplies by 1/period to get
bandwidth per second, and reports utilisation as a percentage of the
configured bitrate.

Periods come from the DBC `GenMsgCycleTime` attribute (cantools exposes
this as `Message.cycle_time`). Messages without a cycle_time are
reported under "unknown cadence" -- they don't contribute to the
budget, since we can't bound their rate, but they are surfaced so a
reviewer can decide whether the DBC is missing periodicity metadata.

Why this exists
---------------
Adding "just one more frame" to a busy bus is one of those things
nobody can answer by feel. Run this before merging a wire-contract
change and the answer is concrete. Used by:
  - .github/workflows/verify.yml -- as a self-test, against AMS's
    current dev DBC.
  - .github/workflows/fleet-audit.yml -- as the nightly cross-repo
    check across every ECU's latest tagged DBC.

Worst-case frame size (Classical CAN, 11-bit standard ID):
  - Fixed framing: SOF(1) + ID(11) + RTR(1) + IDE(1) + r0(1) + DLC(4)
                   + CRC(15) + CRC-delim(1) + ACK(1) + ACK-delim(1)
                   + EOF(7) + IFS(3) = 47 bits
  - Data: DLC * 8 bits
  - Worst-case stuff bits: roughly (34 + 8*DLC - 1) / 4 -- bounds
    from the CAN spec's bit-stuffing rule (insert opposite-polarity
    bit after 5 consecutive same-polarity bits in the SOF-through-CRC
    region). The exact count depends on data values; we use the
    worst-case formula to be conservative.

Total bits per second = sum over messages of (bits per frame *
1000 / period_ms). Divide by bitrate to get utilisation.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import List

try:
    import cantools
except ImportError:
    sys.stderr.write("cantools not available. `pip install cantools`.\n")
    sys.exit(2)


def frame_bits_worst_case(dlc: int, *, extended_id: bool = False) -> int:
    """Worst-case bits on the wire for a Classical CAN frame.

    Static framing fields + DLC * 8 data bits + worst-case stuff
    bits + 3-bit interframe space (counted toward the budget because
    the next frame can't pack closer than that).
    """
    fixed = 47 + 20 if extended_id else 47
    data_bits = 8 * dlc
    # Stuff-bit upper bound across the SOF-through-CRC region.
    stuff_region_bits = (fixed - 13) + data_bits - 1
    stuff_max = stuff_region_bits // 4
    return fixed + data_bits + stuff_max


def _fmt_pct(x: float) -> str:
    return f"{100 * x:6.2f} %"


def estimate(dbc_paths: List[pathlib.Path], bitrate: int) -> int:
    print(f"Classical CAN bus-load estimate @ {bitrate} bps")
    print("=" * 72)

    total_pct = 0.0
    unknown: List[str] = []

    for p in dbc_paths:
        if not p.exists():
            sys.stderr.write(f"bus_load: missing DBC: {p}\n")
            return 2
        db = cantools.database.load_file(str(p))
        ecu = p.stem.upper()
        print(f"\n## {ecu}  ({len(db.messages)} messages)")
        print(f"{'ID':>5}  {'Name':<32} {'DLC':>3} {'Period':>9}  {'Bits':>5}  {'BW%':>7}")

        ecu_pct = 0.0
        rows = sorted(db.messages, key=lambda m: m.frame_id)
        for m in rows:
            period = m.cycle_time
            ext = m.is_extended_frame
            bits = frame_bits_worst_case(m.length, extended_id=ext)
            if period is None or period <= 0:
                unknown.append(f"{ecu} 0x{m.frame_id:X} {m.name}")
                period_str = "no cycle"
                util = 0.0
            else:
                util = (bits * (1000.0 / period)) / bitrate
                period_str = f"{period} ms"
            print(f"0x{m.frame_id:03X}  {m.name:<32} {m.length:>3} {period_str:>9}  "
                  f"{bits:>5}  {_fmt_pct(util)}")
            ecu_pct += util

        print(f"    {ecu:<32}                            subtotal  {_fmt_pct(ecu_pct)}")
        total_pct += ecu_pct

    print("\n" + "=" * 72)
    print(f"Total (worst case, all declared periodic frames):              "
          f"{_fmt_pct(total_pct)}")

    if unknown:
        print(f"\n{len(unknown)} message(s) without a GenMsgCycleTime attribute "
              f"(not counted toward the budget):")
        for u in unknown[:20]:
            print(f"  - {u}")
        if len(unknown) > 20:
            print(f"  ... and {len(unknown) - 20} more")

    rc = 0
    if total_pct > 0.80:
        sys.stderr.write(f"\nFAIL: bus load {_fmt_pct(total_pct)} > 80 % ceiling.\n")
        rc = 1
    elif total_pct > 0.60:
        sys.stderr.write(f"\nWARN: bus load {_fmt_pct(total_pct)} > 60 % advisory.\n")
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dbc", action="append", type=pathlib.Path, required=True,
                    metavar="PATH",
                    help="path to a DBC file (repeatable, one per ECU)")
    ap.add_argument("--bitrate", type=int, default=500_000,
                    help="bus bitrate in bps (default: 500000)")
    args = ap.parse_args(argv)
    return estimate(args.dbc, args.bitrate)


if __name__ == "__main__":
    raise SystemExit(main())

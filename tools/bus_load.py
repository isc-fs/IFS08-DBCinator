#!/usr/bin/env python3
# SPDX-License-Identifier: proprietary
"""Classical-CAN bus-load estimator.

Walks every message in spec/{ams,vcu,udv}.py, computes the worst-case
on-wire frame size including stuff bits, multiplies by 1/period to get
bandwidth per second, and reports utilisation as a percentage of the
configured bitrate.

Why this exists
---------------
Adding "just one more frame" to a busy bus is one of those things
nobody can answer by feel. The pit-diag stream (#247) alone adds 56
frames per scan; the ECU TX matrix (#53) ships 8 frames at 50-250 ms
cadence on top of telemetry. Run this before merging a wire-contract
change and the answer is concrete.

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

That sum is the bit count for a single frame. Multiply by frames per
second (= 1000 / period_ms) and divide by the configured bitrate to
get utilisation.

Conditional frames (e.g. pit-diag stream only when enabled) are
reported in two budgets: baseline ("always" condition) and
condition-x-enabled buckets.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from collections import defaultdict
from typing import Iterable, List, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from spec import ams, vcu, udv                       # noqa: E402
from spec.common import Message                      # noqa: E402


ALL_SPECS = {"AMS": ams.MESSAGES, "VCU": vcu.MESSAGES, "UDV": udv.MESSAGES}


def frame_bits_worst_case(dlc: int, *, extended_id: bool = False) -> int:
    """Worst-case bits on the wire for a Classical CAN frame.

    Static framing fields + DLC * 8 data bits + worst-case stuff
    bits + 3-bit interframe space (counted toward the budget because
    you can't pack the next frame closer than that).
    """
    if extended_id:
        # 29-bit ID adds SRR(1) + IDE(1) + ID-B(18) = 20 extra bits.
        fixed = 47 + 20
    else:
        fixed = 47
    data_bits = 8 * dlc
    # Stuff-bit upper bound: every 4 bits in the stuff-affected region
    # (SOF through CRC) can force an inserted opposite-polarity bit
    # in the worst case. Region length = fixed - (EOF + IFS + CRC-delim
    # + ACK + ACK-delim) = fixed - 13, minus 1 for the SOF that the
    # rule starts counting after.
    stuff_region_bits = (fixed - 13) + data_bits - 1
    stuff_max = stuff_region_bits // 4
    return fixed + data_bits + stuff_max


def utilisation_per_message(m: Message, bitrate: int) -> float:
    """Fraction of the bus this message consumes per second (0..1)."""
    if m.period_ms is None or m.period_ms <= 0:
        return 0.0
    bits = frame_bits_worst_case(m.dlc)
    frames_per_sec = 1000.0 / m.period_ms
    return (bits * frames_per_sec) / bitrate


def collect(messages: Iterable[Tuple[str, List[Message]]]) -> dict:
    """Group messages by `condition`, then by ECU, with per-frame stats."""
    by_condition: dict = defaultdict(lambda: defaultdict(list))
    for ecu, msgs in messages:
        for m in msgs:
            by_condition[m.condition][ecu].append(m)
    return by_condition


def fmt_pct(x: float) -> str:
    return f"{100 * x:6.2f} %"


def report(bitrate: int) -> int:
    by_condition = collect(list(ALL_SPECS.items()))

    print(f"Classical CAN bus-load estimate @ {bitrate} bps")
    print("=" * 72)

    cumulative_pct = 0.0
    for cond, ecu_groups in sorted(by_condition.items()):
        cond_pct = 0.0
        print(f"\n## condition: {cond}")
        print(f"{'ID':>5}  {'Name':<30} {'Sender':>9} {'DLC':>3} "
              f"{'Period':>7}  {'Bits':>5}  {'BW%':>7}")
        # Sort by ID for readability.
        rows = []
        for ecu, msgs in ecu_groups.items():
            for m in msgs:
                util = utilisation_per_message(m, bitrate)
                rows.append((m.can_id, m, ecu, util))
        rows.sort(key=lambda r: r[0])

        for can_id, m, _ecu, util in rows:
            bits = frame_bits_worst_case(m.dlc)
            period_str = f"{m.period_ms} ms" if m.period_ms else "one-shot"
            # m.sender is the real bus-side sender (e.g. 0x100 lives
            # in spec/ams.py because AMS *consumes* it, but the VCU
            # is what puts it on the wire and uses the bandwidth).
            print(f"0x{can_id:03X}  {m.name:<30} {m.sender:>9} {m.dlc:>3} "
                  f"{period_str:>7}  {bits:>5}  {fmt_pct(util)}")
            cond_pct += util

        print(f"    {'subtotal':<30}                              "
              f"           {fmt_pct(cond_pct)}")
        cumulative_pct += cond_pct

    print("\n" + "=" * 72)
    print(f"Total (worst case, every conditional ON):           "
          f"           {fmt_pct(cumulative_pct)}")

    # Threshold check. Anything > 60% on Classical CAN at 500 kbps
    # starts queueing visibly on a busy bus; > 80% is alarming.
    rc = 0
    if cumulative_pct > 0.80:
        print(f"\nFAIL: bus load {fmt_pct(cumulative_pct)} > 80 % ceiling.",
              file=sys.stderr)
        rc = 1
    elif cumulative_pct > 0.60:
        print(f"\nWARN: bus load {fmt_pct(cumulative_pct)} > 60 % advisory.",
              file=sys.stderr)
        # Non-fatal but visible in CI logs.
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bitrate", type=int, default=500_000,
                    help="bus bitrate in bps (default: 500000 = 500 kbps)")
    args = ap.parse_args(argv)
    return report(args.bitrate)


if __name__ == "__main__":
    raise SystemExit(main())

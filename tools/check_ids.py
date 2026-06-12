#!/usr/bin/env python3
# SPDX-License-Identifier: proprietary
"""Cross-repo CAN wire-contract audit.

Inputs are parsed Vector .dbc files -- one per ECU. Each firmware repo
owns its own DBC (generated from its code-first DSL by `dbc_dump` and
maintained by the dbcinator bot). This tool catches the failures that
no single-repo DBC can see:

  - Same can_id sent by two different ECUs (ambiguous on the bus,
    one side's frame silently corrupts the other's)
  - DLC mismatch on the same can_id across repos (the sending repo
    emits N bytes, the receiving repo decodes M)
  - Signal-name reuse across messages with INCONSISTENT units (e.g.
    `dc_bus_V` declared once in volts, once in deciamps)

Signal-name reuse across messages with CONSISTENT units is allowed --
the same physical quantity appearing on multiple frames (a heartbeat
`dc_bus_V` on AMS telemetry AND on VCU echo) is a feature.

Exits non-zero on any finding. Used by:
  - .github/workflows/verify.yml -- as a self-test, against AMS's
    current dev DBC.
  - .github/workflows/fleet-audit.yml -- as the nightly cross-repo
    check, pulling the latest tagged DBC from each ECU repo.

Usage:
  python3 -m tools.check_ids --dbc path/to/ams.dbc --dbc path/to/vcu.dbc ...
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

try:
    import cantools
except ImportError:
    sys.stderr.write("cantools not available. `pip install cantools`.\n")
    sys.exit(2)


def _load(path: pathlib.Path):
    if not path.exists():
        sys.stderr.write(f"check_ids: missing DBC: {path}\n")
        sys.exit(2)
    return cantools.database.load_file(str(path))


def _sender_label(repo_label: str, msg) -> str:
    """Use the explicit DBC sender if present; else fall back to the
    repo label the caller passed (e.g. file basename without .dbc)."""
    if msg.senders:
        return ",".join(msg.senders)
    return repo_label


def audit(dbc_paths: List[pathlib.Path]) -> int:
    findings: List[str] = []

    # frame_id -> [(repo_label, msg), ...]
    by_id: Dict[int, List[Tuple[str, "cantools.database.can.Message"]]] = defaultdict(list)
    # signal_name -> {unit -> set(senders)}
    sig_units: Dict[str, Dict[str, set]] = defaultdict(lambda: defaultdict(set))

    for p in dbc_paths:
        repo = p.stem  # e.g. "ams.dbc" -> "ams"
        db = _load(p)
        for m in db.messages:
            by_id[int(m.frame_id)].append((repo, m))
            sender = _sender_label(repo, m)
            for s in m.signals:
                sig_units[s.name][s.unit or ""].add(sender)

    # --- 1. Cross-ECU ID collisions + DLC mismatches ----------------------
    for fid, hits in sorted(by_id.items()):
        senders = {_sender_label(repo, m) for repo, m in hits}
        if len(senders) > 1:
            tags = ", ".join(f"{r}:{m.name} (sender={_sender_label(r, m)}, dlc={m.length})"
                             for r, m in hits)
            findings.append(
                f"ID collision: 0x{fid:X} claimed by {len(senders)} senders -> {tags}"
            )
        # DLC mismatch: same id, same sender, different declared lengths.
        # (Cross-sender DLC mismatch is already covered by the collision above.)
        by_sender: Dict[str, set] = defaultdict(set)
        for r, m in hits:
            by_sender[_sender_label(r, m)].add(m.length)
        for sender, dlcs in by_sender.items():
            if len(dlcs) > 1:
                findings.append(
                    f"DLC mismatch on 0x{fid:X}: sender '{sender}' declares "
                    f"different lengths across DBCs: {sorted(dlcs)}"
                )

    # --- 2. Signal unit drift --------------------------------------------
    for sig_name, unit_to_senders in sorted(sig_units.items()):
        if len(unit_to_senders) > 1:
            tags = "; ".join(f"unit={u!r} via {sorted(s)}"
                             for u, s in sorted(unit_to_senders.items()))
            findings.append(
                f"Unit drift: signal '{sig_name}' carries inconsistent units -> {tags}"
            )

    # --- Report -----------------------------------------------------------
    if not findings:
        msg_total = sum(len(db.messages) for db in (_load(p) for p in dbc_paths))
        print(f"check_ids: OK ({len(dbc_paths)} DBC{'s' if len(dbc_paths) != 1 else ''}, "
              f"{msg_total} messages, no collisions / mismatches / unit drift)")
        return 0

    sys.stderr.write(f"check_ids: {len(findings)} finding{'s' if len(findings) != 1 else ''}\n")
    for f in findings:
        sys.stderr.write(f"  - {f}\n")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dbc", action="append", type=pathlib.Path, required=True,
                    metavar="PATH",
                    help="path to a DBC file (repeatable, one per ECU)")
    args = ap.parse_args(argv)
    return audit(args.dbc)


if __name__ == "__main__":
    raise SystemExit(main())

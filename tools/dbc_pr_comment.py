#!/usr/bin/env python3
# SPDX-License-Identifier: proprietary
"""Render a wire-contract diff between two DBC files as PR-comment markdown.

Used by the dbcinator bot's composite action (actions/dbc-bot/action.yml).
Loaded fresh on every PR run; no state, no side effects beyond writing the
output markdown to --output.

Output layout
-------------
- One-line headline:  green tick + count if unchanged, summary of deltas if not.
- Added messages (BO_ rows new on this PR)         : collapsed <details>.
- Removed messages                                  : same.
- Per-message signal changes                        : one collapsed <details>
                                                      per touched message, listing
                                                      added/removed/changed signals.

A "changed signal" is detected by name + start_bit identity; any of
(length, endianness, signed-ness, factor, offset, unit) differing is
reported as a delta line.

Why we use cantools
-------------------
The DBC is the firmware's wire contract as seen from outside; cantools
is what every consumer (dashboard, pit tool, data logger) actually
parses with. Diffing through cantools means we report the same view
they will see -- not the textual DBC, which can vary in formatting
without any semantic change.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    import cantools
except ImportError:
    sys.stderr.write("cantools not available. Install via `pip install cantools`.\n")
    sys.exit(2)


# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SigSnapshot:
    """Hashable, comparable view of one signal -- the bits a consumer sees."""
    name:        str
    start_bit:   int
    length:      int
    byte_order:  str    # 'little_endian' / 'big_endian'
    is_signed:   bool
    scale:       float
    offset:      float
    unit:        str

    @classmethod
    def from_cantools(cls, s) -> "SigSnapshot":
        return cls(
            name=s.name,
            start_bit=s.start,
            length=s.length,
            byte_order=s.byte_order,
            is_signed=s.is_signed,
            scale=float(s.scale),
            offset=float(s.offset),
            unit=s.unit or "",
        )


@dataclass(frozen=True)
class MsgSnapshot:
    name:       str
    frame_id:   int
    length:     int
    senders:    Tuple[str, ...]
    signals:    Tuple[SigSnapshot, ...]   # ordered by start_bit

    @classmethod
    def from_cantools(cls, m) -> "MsgSnapshot":
        sigs = sorted(
            (SigSnapshot.from_cantools(s) for s in m.signals),
            key=lambda s: (s.start_bit, s.name),
        )
        return cls(
            name=m.name,
            frame_id=int(m.frame_id),
            length=m.length,
            senders=tuple(m.senders or ()),
            signals=tuple(sigs),
        )


def load(path: pathlib.Path) -> Dict[int, MsgSnapshot]:
    """Parse a DBC into {frame_id: snapshot}. Empty dict if missing."""
    if not path.exists() or path.stat().st_size == 0:
        return {}
    db = cantools.database.load_file(str(path))
    return {int(m.frame_id): MsgSnapshot.from_cantools(m) for m in db.messages}


# ---------------------------------------------------------------------------
# Diff computation

def diff_messages(before: Dict[int, MsgSnapshot],
                  after:  Dict[int, MsgSnapshot]
                  ) -> Tuple[List[MsgSnapshot], List[MsgSnapshot], List[Tuple[MsgSnapshot, MsgSnapshot]]]:
    """Return (added, removed, changed). `changed` only includes messages
    whose snapshot differs between before/after."""
    added   = [after[i]  for i in sorted(after)  if i not in before]
    removed = [before[i] for i in sorted(before) if i not in after]
    changed = [(before[i], after[i])
               for i in sorted(set(before) & set(after))
               if before[i] != after[i]]
    return added, removed, changed


def diff_signals(before: MsgSnapshot, after: MsgSnapshot
                 ) -> Tuple[List[SigSnapshot], List[SigSnapshot], List[Tuple[SigSnapshot, SigSnapshot]]]:
    """Per-message: classify signals as added / removed / changed.

    A signal is matched by (name, start_bit). A signal that drifts in
    start_bit shows up as one remove + one add, which is the correct
    framing: "the field at 0x4A0[7] used to be foo, now it's bar."
    """
    bkey = {(s.name, s.start_bit): s for s in before.signals}
    akey = {(s.name, s.start_bit): s for s in after.signals}

    added   = [akey[k] for k in sorted(akey)         if k not in bkey]
    removed = [bkey[k] for k in sorted(bkey)         if k not in akey]
    changed = [(bkey[k], akey[k]) for k in sorted(akey)
               if k in bkey and bkey[k] != akey[k]]
    return added, removed, changed


# ---------------------------------------------------------------------------
# Markdown rendering

_HEADER = "<!-- dbcinator-comment -->\n"   # idempotency marker for --edit-last


def _hex_id(fid: int) -> str:
    return f"0x{fid:03X}"


def _sig_one_line(s: SigSnapshot) -> str:
    """Compact one-liner: name, position, layout, scaling, unit."""
    bo = "LE" if s.byte_order == "little_endian" else "BE"
    sign = "i" if s.is_signed else "u"
    scale = "" if (s.scale == 1.0 and s.offset == 0.0) else f", ×{s.scale:g}+{s.offset:g}"
    unit  = f" [{s.unit}]" if s.unit else ""
    return f"`{s.name}` @ bit {s.start_bit}, {s.length}b {sign}{bo}{scale}{unit}"


def _sig_diff_lines(b: SigSnapshot, a: SigSnapshot) -> List[str]:
    out: List[str] = []
    fields = [
        ("length",     b.length,     a.length),
        ("byte_order", b.byte_order, a.byte_order),
        ("is_signed",  b.is_signed,  a.is_signed),
        ("scale",      b.scale,      a.scale),
        ("offset",     b.offset,     a.offset),
        ("unit",       b.unit,       a.unit),
    ]
    for k, bv, av in fields:
        if bv != av:
            out.append(f"  - `{k}`: `{bv}` → `{av}`")
    return out


def render(before: Optional[Dict[int, MsgSnapshot]],
           after:  Dict[int, MsgSnapshot],
           pr_num: str,
           repo:   str) -> str:
    """Produce the markdown body. `before` may be None (newly-tracked DBC)."""
    body = [_HEADER]

    if before is None or not before:
        if after:
            body.append(f"### 🟢 dbcinator — initialising wire contract\n")
            body.append(f"This PR adds `{len(after)}` message"
                        f"{'s' if len(after) != 1 else ''} to the bot-tracked DBC.\n")
            body.append("<details><summary>Message list</summary>\n")
            for m in sorted(after.values(), key=lambda x: x.frame_id):
                body.append(f"\n- {_hex_id(m.frame_id)} `{m.name}` "
                            f"(DLC {m.length}, {len(m.signals)} signals)")
            body.append("\n\n</details>\n")
            return "".join(body)
        body.append("### 🟢 dbcinator — wire contract empty (no messages)\n")
        return "".join(body)

    added, removed, changed = diff_messages(before, after)

    if not added and not removed and not changed:
        body.append(f"### 🟢 dbcinator — wire contract unchanged\n")
        body.append(f"\n`{len(after)}` message"
                    f"{'s' if len(after) != 1 else ''} on this PR, byte-identical to base.\n")
        return "".join(body)

    # Headline.
    parts = []
    if added:   parts.append(f"+{len(added)} message{'s' if len(added) != 1 else ''}")
    if removed: parts.append(f"−{len(removed)} message{'s' if len(removed) != 1 else ''}")
    if changed: parts.append(f"~{len(changed)} message{'s' if len(changed) != 1 else ''}")
    body.append(f"### 🟡 dbcinator — wire contract changed: {', '.join(parts)}\n")
    body.append(f"\nReview the diff below before merging. Source of truth lives "
                f"in the firmware's `Core/Inc/can/messages/*.def` files; this DBC "
                f"is bot-regenerated from those declarations.\n")

    if added:
        body.append(f"\n<details><summary><b>Added messages ({len(added)})</b></summary>\n")
        for m in added:
            body.append(f"\n#### {_hex_id(m.frame_id)} `{m.name}` "
                        f"(DLC {m.length}, sender {'/'.join(m.senders) or '?'})\n")
            for s in m.signals:
                body.append(f"- {_sig_one_line(s)}\n")
        body.append("\n</details>\n")

    if removed:
        body.append(f"\n<details><summary><b>Removed messages ({len(removed)})</b></summary>\n")
        for m in removed:
            body.append(f"\n#### {_hex_id(m.frame_id)} `{m.name}` (was DLC {m.length})\n")
            for s in m.signals:
                body.append(f"- {_sig_one_line(s)}\n")
        body.append("\n</details>\n")

    if changed:
        body.append(f"\n<details><summary><b>Changed messages ({len(changed)})</b></summary>\n")
        for b, a in changed:
            body.append(f"\n#### {_hex_id(a.frame_id)} `{a.name}`")
            if b.length != a.length:
                body.append(f" — **DLC {b.length} → {a.length}**")
            body.append("\n")
            sig_added, sig_removed, sig_changed = diff_signals(b, a)
            if sig_added:
                body.append("\n*Added signals:*\n")
                for s in sig_added:
                    body.append(f"- {_sig_one_line(s)}\n")
            if sig_removed:
                body.append("\n*Removed signals:*\n")
                for s in sig_removed:
                    body.append(f"- {_sig_one_line(s)}\n")
            if sig_changed:
                body.append("\n*Changed signals:*\n")
                for sb, sa in sig_changed:
                    body.append(f"- `{sa.name}` @ bit {sa.start_bit}\n")
                    for ln in _sig_diff_lines(sb, sa):
                        body.append(f"{ln}\n")
        body.append("\n</details>\n")

    body.append(f"\n<sub>dbcinator bot · {repo} · PR #{pr_num}</sub>\n")
    return "".join(body)


# ---------------------------------------------------------------------------
# CLI

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--before", required=True, type=pathlib.Path,
                    help="path to the in-tree DBC BEFORE this PR's regeneration")
    ap.add_argument("--after",  required=True, type=pathlib.Path,
                    help="path to the freshly-regenerated DBC")
    ap.add_argument("--pr",     required=True,
                    help="PR number (used in comment footer)")
    ap.add_argument("--repo",   required=True,
                    help="owner/repo identifier (used in comment footer)")
    ap.add_argument("--output", required=True, type=pathlib.Path,
                    help="where to write the rendered markdown")
    args = ap.parse_args(argv)

    before = load(args.before)
    after  = load(args.after)

    md = render(before if before else None, after, args.pr, args.repo)
    args.output.write_text(md)
    print(f"wrote {args.output} ({len(md)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

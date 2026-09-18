"""Replay a captured HidD_SetFeature sequence back to the mouse, verbatim.

For settling "we send the same bytes as the vendor, so why does it behave
differently?". Rather than reason about which of our packets might differ, this
sends the vendor's own captured writes, byte for byte, in the captured order,
with nothing of ours in between. If the mouse then behaves the way it did for
the vendor, the difference was in our bytes; if it still does not, the feature
does not work on this unit and no amount of packet-crafting will change that.

    python tools/replay_capture.py captures/macro_create.jsonl --list
    python tools/replay_capture.py captures/macro_create.jsonl --upto 10
    python tools/replay_capture.py captures/macro_create.jsonl --only 0,4,5,6,7

The hook logs each write up to three times, once per wrapper layer, so runs of
identical consecutive packets are collapsed to one. `--list` shows exactly what
would be sent, with indices, so a slice can be chosen before anything is
written.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attackshark import protocol as P                     # noqa: E402
from attackshark.hid_backend import find_interfaces, HidInterface   # noqa: E402

CFG_USAGE_PAGE = 0x000B


def load(path):
    """Captured TX writes, in order, with the wrapper duplicates collapsed."""
    seq = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not (row.get("op") or "").endswith("HidD_SetFeature"):
                continue
            data = row.get("data") or ""
            try:
                pkt = bytes(int(x, 16) for x in data.split())
            except ValueError:
                continue
            if pkt and not (seq and seq[-1] == pkt):
                seq.append(pkt)
    return seq


def describe(i, pkt):
    rid = pkt[0]
    if rid == 0x08:
        slots = [tuple(pkt[3 + j * 3:6 + j * 3]) for j in range(P.BUTTON_SLOTS)]
        named = {b: slots[s] for b, s in sorted(P.BUTTON_SLOT.items())}
        return f"buttons   " + "  ".join(f"b{b}={v}" for b, v in named.items())
    if rid == 0x09:
        used = pkt[1]
        body = pkt[4:used]
        note = ""
        if pkt[3] == 0 and len(body) > 25:
            note = f"  slot={body[0]} count={body[25]}"
        return f"macro     chunk {pkt[3]} used {used}{note}"
    names = {0x04: "sensor", 0x05: "power", 0x06: "polling", 0x0C: "commit"}
    return f"{names.get(rid, '?'):<9} {pkt.hex(' ')[:48]}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("capture")
    ap.add_argument("--list", action="store_true", help="show writes, send nothing")
    ap.add_argument("--upto", type=int, help="send writes 0..N inclusive")
    ap.add_argument("--only", help="send just these indices, comma separated")
    ap.add_argument("--delay", type=float, default=0.03,
                    help="seconds between writes (default 0.03)")
    args = ap.parse_args()

    seq = load(args.capture)
    if not seq:
        print(f"no HidD_SetFeature writes found in {args.capture}")
        return 1

    if args.only:
        picks = [int(x) for x in args.only.split(",") if x.strip() != ""]
    elif args.upto is not None:
        picks = list(range(0, min(args.upto, len(seq) - 1) + 1))
    else:
        picks = list(range(len(seq)))

    bad = [i for i in picks if i < 0 or i >= len(seq)]
    if bad:
        print(f"out of range: {bad} (capture has {len(seq)} writes)")
        return 1

    print(f"{len(seq)} write(s) in {args.capture}; selected {len(picks)}\n")
    for i in picks:
        print(f"  {i:3d}  0x{seq[i][0]:02x}  {describe(i, seq[i])}")

    if args.list:
        return 0

    ifaces = find_interfaces(0x1D57, 0xFA60, CFG_USAGE_PAGE)
    if not ifaces:
        print("\nconfig interface not found (mouse off, asleep, or on the cable?)")
        return 1

    print()
    sent = 0
    with HidInterface(ifaces[0]) as dev:
        for i in picks:
            pkt = seq[i]
            ok, err = dev.set_feature(pkt)
            if not ok:
                print(f"  {i:3d}  0x{pkt[0]:02x}  FAILED (win32 {err})")
                return 1
            sent += 1
            time.sleep(args.delay)
    print(f"replayed {sent} write(s) exactly as captured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

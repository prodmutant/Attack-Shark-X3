"""Watch the status report live and print every change.

What is known: byte 2 is a voltage in 1/16 V. 0x40 = 4.00 V on the dongle, and
the vendor app showed 90 % at that same reading, which is where 4.00 V sits on
a 1S Li-ion curve. 0x50 = 5.00 V appeared while the cable was in - no Li-ion
cell reaches 5 V, so that is the bus, not the cell.

What is not known: bytes 3 and 4. Two samples suggest byte 3 is a charge bit
(1 on the dongle, 0 on the cable) but one bit of evidence is not a finding,
which is why `protocol.parse_status` detects charging from the voltage instead -
that part is physics, not a guess.

This makes the unknown bytes legible by printing transitions rather than a
wall of identical lines:

    python tools/battery_probe.py                 # watch until Ctrl-C
    python tools/battery_probe.py --all           # every sample, not just changes
    python tools/battery_probe.py --seconds 120

The test worth running: start it, then plug the cable in, wait, and unplug.
If byte 3 flips 1 -> 0 on plug and back on unplug, it is the charge bit and
`parse_status` can use it directly instead of inferring from the voltage.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attackshark import protocol as P              # noqa: E402
from attackshark.device import AttackSharkX3       # noqa: E402

LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "captures", "battery.csv")


def describe(st):
    if not st or st.get("kind") != "battery":
        return str(st)
    pct = st["percent"]
    return (f"{st['raw']:<16} {st['volts']:>5.2f} V  "
            f"{'CHARGING' if st['charging'] else (str(pct) + '%').rjust(8)}  "
            f"flags={st['flags']}  extra={st['extra']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interval", type=float, default=6.0)
    ap.add_argument("--seconds", type=float, default=0, help="0 = until Ctrl-C")
    ap.add_argument("--all", action="store_true", help="print every sample")
    ap.add_argument("--csv", default=LOG, help="append samples here")
    args = ap.parse_args()

    if not AttackSharkX3.discover():
        print("no X3 detected")
        return 1

    os.makedirs(os.path.dirname(args.csv), exist_ok=True)
    new = not os.path.exists(args.csv)
    fh = open(args.csv, "a", encoding="utf-8")
    if new:
        fh.write("iso,raw,level_raw,volts,percent,charging,flags,extra\n")

    print("watching the status collection. Plug the cable in and out to see")
    print("which byte moves.  Ctrl-C to stop.\n")
    print(f"{'time':<9} {'raw':<16} {'volts':>7}  {'state':>8}  flags/extra")

    mouse = AttackSharkX3()
    deadline = time.time() + args.seconds if args.seconds else None
    last = None
    n = 0
    try:
        while deadline is None or time.time() < deadline:
            st = mouse.read_status(timeout=8.0)
            if st and st.get("kind") == "battery":
                key = st["raw"]
                fh.write(",".join([
                    time.strftime("%Y-%m-%dT%H:%M:%S"), st["raw"],
                    str(st["level_raw"]), f"{st['volts']:.2f}",
                    "" if st["percent"] is None else str(st["percent"]),
                    "1" if st["charging"] else "0",
                    str(st["flags"]), str(st["extra"])]) + "\n")
                fh.flush()
                n += 1
                if args.all or key != last:
                    mark = "" if last is None or key == last else "   <- CHANGED"
                    print(f"{time.strftime('%H:%M:%S')} {describe(st)}{mark}")
                    last = key
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        fh.close()
    print(f"\n{n} sample(s) appended to {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

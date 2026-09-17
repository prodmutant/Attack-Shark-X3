"""Log the status byte over time so we can find out what it actually means.

The byte sits at 0x40 and does not move minute to minute, so calling it a
percentage was a guess and a wrong one. This settles it from the hardware
instead of from the vendor app:

  * leave it running for a few hours on battery  -> does it fall monotonically?
  * plug the cable in (the mouse becomes PID 0xFA61) -> does it climb?

    python tools/battery_log.py                 # append a sample every 5 min
    python tools/battery_log.py --interval 60   # or faster
    python tools/battery_log.py --report        # summarise what has been logged
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from attackshark.device import AttackSharkX3  # noqa: E402
from attackshark import protocol as P  # noqa: E402

LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "captures", "battery.csv")


def link_now():
    for pid in P.PRODUCT_IDS:
        from attackshark.hid_backend import find_interfaces
        if find_interfaces(P.VENDOR_ID, pid, P.CONFIG_USAGE_PAGE):
            return "wired" if pid == P.PRODUCT_ID_WIRED else "2.4GHz"
    return "absent"


def sample():
    dev = AttackSharkX3()
    st = dev.read_status(timeout=8.0)
    return link_now(), st


def report():
    if not os.path.isfile(LOG):
        print("nothing logged yet")
        return
    rows = [l.strip().split(",") for l in open(LOG) if l.strip()][1:]
    if not rows:
        print("nothing logged yet")
        return
    print(f"{len(rows)} samples over "
          f"{(float(rows[-1][0]) - float(rows[0][0])) / 3600:.1f} h")
    vals = sorted({r[3] for r in rows})
    print(f"distinct values of byte[2]: {', '.join(vals)}")
    links = sorted({r[2] for r in rows})
    print(f"links seen: {', '.join(links)}")
    if len(vals) == 1:
        print("\n-> byte[2] has not moved yet. Keep logging, and charge/discharge\n"
              "   the mouse; a value that never changes is not a battery level.")
    else:
        print("\n-> it moves. First/last few samples:")
        for r in rows[:3] + ["..."] + rows[-3:]:
            print("   ", r if r == "..." else ",".join(r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=300.0)
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        return report()

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    if not os.path.isfile(LOG):
        with open(LOG, "w") as fh:
            fh.write("epoch,iso,link,byte2,byte3,byte4,raw\n")
    print(f"logging every {a.interval:g}s -> {LOG}   (ctrl-c to stop)")
    while True:
        link, st = sample()
        now = time.time()
        raw = (st or {}).get("raw", "")
        b = raw.split()
        row = (f"{now:.0f},{time.strftime('%Y-%m-%d %H:%M:%S')},{link},"
               f"{b[2] if len(b) > 2 else ''},{b[3] if len(b) > 3 else ''},"
               f"{b[4] if len(b) > 4 else ''},{raw}\n")
        with open(LOG, "a") as fh:
            fh.write(row)
        print("  " + row.strip())
        time.sleep(a.interval)


try:
    main()
except KeyboardInterrupt:
    print("\nstopped")

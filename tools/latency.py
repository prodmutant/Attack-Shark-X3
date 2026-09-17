"""Measure what the mouse is really doing: report interval and click delay.

Reads the mouse's own HID input collection and times the gaps between reports.
At 1000 Hz the gaps during movement should sit around 1 ms; 8 ms means the
device is actually running at 125 Hz whatever the config claims.

    python tools/latency.py [seconds]      # move the mouse during the run
"""
import ctypes as C
from ctypes import wintypes as W
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from attackshark.hid_backend import _open, find_interfaces, k32  # noqa: E402
from attackshark import protocol as P  # noqa: E402

MOUSE_USAGE_PAGE, MOUSE_USAGE = 0x0001, 0x0002


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    target = None
    for pid in P.PRODUCT_IDS:
        for d in find_interfaces(P.VENDOR_ID, pid):
            if d["usage_page"] == MOUSE_USAGE_PAGE and d["usage"] == MOUSE_USAGE:
                target = d
                break
        if target:
            break
    if not target:
        print("mouse collection not found")
        return 1

    h = _open(target["path"], False) or _open(target["path"], True)
    if not h:
        print("cannot open the mouse collection")
        return 1

    n = target["input_len"]
    buf = C.create_string_buffer(n)
    got = W.DWORD()
    gaps, clicks, moves = [], 0, 0
    prev_buttons = 0
    t_end = time.perf_counter() + secs
    last = None
    print(f"measuring {secs:g}s - move the mouse and click a few times...\n")
    try:
        while time.perf_counter() < t_end:
            if not k32.ReadFile(h, buf, n, C.byref(got), None):
                break
            now = time.perf_counter()
            data = bytes(buf.raw[:got.value])
            if last is not None:
                gaps.append((now - last) * 1000.0)
            last = now
            if len(data) >= 4:
                b = data[1]
                if b != prev_buttons:
                    clicks += 1
                    prev_buttons = b
                dx = int.from_bytes(data[2:4], "little", signed=True)
                if dx:
                    moves += 1
    finally:
        k32.CloseHandle(h)

    if not gaps:
        print("no input reports seen - was the mouse moved?")
        return 1

    gaps.sort()
    fast = [g for g in gaps if g < 50]        # ignore idle pauses
    print(f"reports      : {len(gaps) + 1}   (button edges {clicks}, moving {moves})")
    if fast:
        med = statistics.median(fast)
        print(f"gap median   : {med:.2f} ms  -> ~{1000.0 / max(med, 0.001):.0f} Hz")
        print(f"gap p05/p95  : {fast[len(fast)//20]:.2f} / {fast[len(fast)*19//20]:.2f} ms")
        print(f"gap max      : {max(fast):.2f} ms")
        stalls = [g for g in gaps if g >= 50]
        if stalls:
            print(f"stalls >50ms : {len(stalls)}  worst {max(stalls):.0f} ms")
        expect = {1000: 1.0, 500: 2.0, 250: 4.0, 125: 8.0}
        best = min(expect, key=lambda r: abs(expect[r] - med))
        print(f"\nlooks like   : {best} Hz")
    return 0


sys.exit(main())

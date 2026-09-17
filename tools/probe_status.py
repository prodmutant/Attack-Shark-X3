"""Probe the 0x000A status collection for an on-demand battery read.

A GUI needs to pull battery when it wants it, not wait for the mouse to
volunteer a report. Tries HidD_GetInputReport and HidD_GetFeature across
report ids on that collection.
"""
import ctypes as C
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from attackshark.hid_backend import _open, find_interfaces, hid, k32  # noqa: E402
from attackshark import protocol as P  # noqa: E402

from ctypes import wintypes as W
hid.HidD_GetInputReport.argtypes = [C.c_void_p, C.c_void_p, W.ULONG]
hid.HidD_GetInputReport.restype = W.BOOL

STATUS_USAGE_PAGE = 0x000A


def main():
    target = None
    for pid in P.PRODUCT_IDS:
        for d in find_interfaces(P.VENDOR_ID, pid, STATUS_USAGE_PAGE):
            target = d
            break
    if not target:
        print("status collection not present")
        return
    print(f"{target['path']}")
    print(f"input_len={target['input_len']} feature_len={target['feature_len']}\n")

    h = _open(target["path"], True) or _open(target["path"], False)
    if not h:
        print("cannot open")
        return
    try:
        n = target["input_len"] or 8
        print("HidD_GetInputReport:")
        for rid in range(0, 16):
            buf = C.create_string_buffer(n)
            buf[0] = bytes([rid])
            if hid.HidD_GetInputReport(h, buf, n):
                print(f"  id 0x{rid:02x} -> {bytes(buf.raw[:n]).hex(' ')}")
        fn = target["feature_len"]
        if fn:
            print("\nHidD_GetFeature:")
            for rid in range(0, 16):
                buf = C.create_string_buffer(fn)
                buf[0] = bytes([rid])
                if hid.HidD_GetFeature(h, buf, fn):
                    print(f"  id 0x{rid:02x} -> {bytes(buf.raw[:min(fn,16)]).hex(' ')}")
        else:
            print("\n(no feature reports on this collection)")
    finally:
        k32.CloseHandle(h)


main()

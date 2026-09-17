"""Read-only probe: try HidD_GetFeature for every report ID on the config interface."""
import ctypes as C
from ctypes import wintypes as W
import sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from hid_enum import enumerate_hid, describe, open_path, hid, k32

VID, PID = 0x1D57, 0xFA60
CFG_USAGE_PAGE = 0x000B

def find_config_iface():
    for p in enumerate_hid():
        d = describe(p)
        if d and (d["vid"], d["pid"]) == (VID, PID) and d["usage_page"] == CFG_USAGE_PAGE:
            return d
    return None

def main():
    d = find_config_iface()
    if not d:
        print("config interface not found (mouse unplugged?)"); return
    n = d["feat_len"]
    print(f"config iface feat_len={n}\n{d['path']}\n")
    h = open_path(d["path"], rw=True)
    mode = "RW"
    if not h:
        h = open_path(d["path"], rw=False); mode = "R0"
    if not h:
        print("cannot open interface"); return
    print(f"opened ({mode})\n")
    try:
        hits = 0
        for rid in range(256):
            buf = C.create_string_buffer(n)
            buf[0] = bytes([rid])
            if hid.HidD_GetFeature(h, buf, n):
                raw = buf.raw
                body = raw[1:]
                nz = body.rstrip(b"\x00")
                print(f"[OK] id=0x{rid:02x} len={n} nonzero={len(nz)}")
                print(f"     {raw[:64].hex(' ')}")
                hits += 1
        print(f"\n{hits} report id(s) answered")
    finally:
        k32.CloseHandle(h)

main()

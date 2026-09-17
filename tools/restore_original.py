"""Write back the exact config bytes the mouse had before this RE session.

Both packets were captured verbatim from the vendor app at first launch,
so this restores the original state rather than guessing at it.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from attackshark.hid_backend import find_interfaces, HidInterface

VID, PID, CFG_UP = 0x1D57, 0xFA60, 0x000B

ORIGINAL = [
    # report 0x05 - power / sleep block (deep sleep back to 10 min)
    bytes.fromhex("05 0f 01 00 03 a8 00 00 ff 01 02 01 ad 00 00".replace(" ", "")),
    # report 0x04 - sensor + DPI block: LOD=2mm, ripple/angle/motion off, stage 2
    bytes.fromhex((
        "04 38 01 01 00 3f 00 00 0f 1f 2f 3f 7f 07 00 00 00 00 00 00 00 02 "
        "00 00 02 ff 00 00 00 ff 00 00 00 ff ff ff 00 00 ff ff ff 00 ff ff "
        "40 00 ff ff ff 01 0e 9a 00 00 00 00").replace(" ", "")),
    # report 0x06 - polling rate 1000 Hz
    bytes.fromhex("06 09 01 01 fe 00 00 00 00".replace(" ", "")),
]

ifaces = find_interfaces(VID, PID, CFG_UP)
if not ifaces:
    print("config interface not found (mouse off or on another link?)")
    sys.exit(1)

with HidInterface(ifaces[0]) as dev:
    for pkt in ORIGINAL:
        ok, err = dev.set_feature(pkt)
        print(f"  report 0x{pkt[0]:02x} len={len(pkt):<3} -> ok={ok} err={err}")
        time.sleep(0.25)
print("done")

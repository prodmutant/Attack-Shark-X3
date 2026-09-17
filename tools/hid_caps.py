"""Enumerate feature/input/output report IDs and their byte lengths."""
import ctypes as C
from ctypes import wintypes as W
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hid_enum import (enumerate_hid, describe, open_path, hid, k32,
                      HIDP_CAPS, GUID)

VID, PID, CFG_UP = 0x1D57, 0xFA60, 0x000B
HidP_Input, HidP_Output, HidP_Feature = 0, 1, 2

class HIDP_VALUE_CAPS(C.Structure):
    _fields_ = [
        ("UsagePage", W.USHORT), ("ReportID", C.c_ubyte), ("IsAlias", C.c_ubyte),
        ("BitField", W.USHORT), ("LinkCollection", W.USHORT),
        ("LinkUsage", W.USHORT), ("LinkUsagePage", W.USHORT),
        ("IsRange", C.c_ubyte), ("IsStringRange", C.c_ubyte),
        ("IsDesignatorRange", C.c_ubyte), ("IsAbsolute", C.c_ubyte),
        ("HasNull", C.c_ubyte), ("Reserved", C.c_ubyte),
        ("BitSize", W.USHORT), ("ReportCount", W.USHORT),
        ("Reserved2", W.USHORT * 5),
        ("UnitsExp", W.ULONG), ("Units", W.ULONG),
        ("LogicalMin", C.c_long), ("LogicalMax", C.c_long),
        ("PhysicalMin", C.c_long), ("PhysicalMax", C.c_long),
        ("u1", W.USHORT), ("u2", W.USHORT), ("u3", W.USHORT), ("u4", W.USHORT),
        ("u5", W.USHORT), ("u6", W.USHORT), ("u7", W.USHORT), ("u8", W.USHORT),
    ]

hid.HidP_GetValueCaps.argtypes = [C.c_int, C.c_void_p, C.POINTER(W.USHORT), C.c_void_p]
hid.HidP_GetValueCaps.restype = C.c_long

def main():
    target = None
    for p in enumerate_hid():
        d = describe(p)
        if d and (d["vid"], d["pid"]) == (VID, PID) and d["usage_page"] == CFG_UP:
            target = d; break
    if not target:
        print("config interface not present"); return
    h = open_path(target["path"], rw=True) or open_path(target["path"], rw=False)
    if not h:
        print("cannot open"); return
    try:
        pp = C.c_void_p()
        if not hid.HidD_GetPreparsedData(h, C.byref(pp)):
            print("no preparsed data"); return
        caps = HIDP_CAPS()
        hid.HidP_GetCaps(pp, C.byref(caps))
        print(f"UsagePage={caps.UsagePage:04x} Usage={caps.Usage:04x}")
        print(f"max feature report bytes = {caps.FeatureReportByteLength}")
        print(f"feature value caps = {caps.NumberFeatureValueCaps}\n")
        for kind, name, n in ((HidP_Feature, "FEATURE", caps.NumberFeatureValueCaps),
                              (HidP_Input, "INPUT", caps.NumberInputValueCaps),
                              (HidP_Output, "OUTPUT", caps.NumberOutputValueCaps)):
            if not n: continue
            arr = (HIDP_VALUE_CAPS * n)()
            cnt = W.USHORT(n)
            r = hid.HidP_GetValueCaps(kind, C.byref(arr), C.byref(cnt), pp)
            if r < 0:
                print(f"{name}: GetValueCaps failed 0x{r & 0xffffffff:08x}"); continue
            print(f"--- {name} ({cnt.value}) ---")
            for v in arr[:cnt.value]:
                total = (v.BitSize * v.ReportCount) // 8 + 1   # +1 for report ID byte
                print(f"  reportID=0x{v.ReportID:02x}  bits={v.BitSize:<3} count={v.ReportCount:<4}"
                      f" => {total} bytes   usagePage={v.UsagePage:04x}")
        hid.HidD_FreePreparsedData(pp)
    finally:
        k32.CloseHandle(h)

main()

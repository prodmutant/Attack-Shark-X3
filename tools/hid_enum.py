"""Enumerate Windows HID interfaces + capabilities. No third-party deps."""
import ctypes as C
from ctypes import wintypes as W
import sys

setupapi = C.WinDLL("setupapi")
hid = C.WinDLL("hid")
k32 = C.WinDLL("kernel32")

class GUID(C.Structure):
    _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD), ("Data4", C.c_ubyte * 8)]

class SP_DEVICE_INTERFACE_DATA(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", W.DWORD), ("Reserved", C.POINTER(W.ULONG))]

class SP_DEVICE_INTERFACE_DETAIL_DATA_W(C.Structure):
    _fields_ = [("cbSize", W.DWORD), ("DevicePath", W.WCHAR * 1024)]

class HIDD_ATTRIBUTES(C.Structure):
    _fields_ = [("Size", W.ULONG), ("VendorID", W.USHORT),
                ("ProductID", W.USHORT), ("VersionNumber", W.USHORT)]

class HIDP_CAPS(C.Structure):
    _fields_ = [("Usage", W.USHORT), ("UsagePage", W.USHORT),
                ("InputReportByteLength", W.USHORT), ("OutputReportByteLength", W.USHORT),
                ("FeatureReportByteLength", W.USHORT), ("Reserved", W.USHORT * 17),
                ("NumberLinkCollectionNodes", W.USHORT),
                ("NumberInputButtonCaps", W.USHORT), ("NumberInputValueCaps", W.USHORT),
                ("NumberInputDataIndices", W.USHORT),
                ("NumberOutputButtonCaps", W.USHORT), ("NumberOutputValueCaps", W.USHORT),
                ("NumberOutputDataIndices", W.USHORT),
                ("NumberFeatureButtonCaps", W.USHORT), ("NumberFeatureValueCaps", W.USHORT),
                ("NumberFeatureDataIndices", W.USHORT)]

# --- explicit prototypes: required or 64-bit handles get truncated to int32 ---
setupapi.SetupDiGetClassDevsW.restype = C.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [C.POINTER(GUID), W.LPCWSTR, W.HWND, W.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.restype = W.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(GUID),
                                                W.DWORD, C.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = W.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [C.c_void_p,
                                                      C.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                      C.c_void_p, W.DWORD,
                                                      C.POINTER(W.DWORD), C.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.restype = W.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [C.c_void_p]
k32.CreateFileW.restype = C.c_void_p
k32.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p,
                            W.DWORD, W.DWORD, C.c_void_p]
k32.CloseHandle.restype = W.BOOL
k32.CloseHandle.argtypes = [C.c_void_p]
for _f in ("HidD_GetAttributes", "HidD_GetPreparsedData", "HidD_FreePreparsedData",
           "HidD_GetManufacturerString", "HidD_GetProductString",
           "HidD_GetSerialNumberString", "HidD_SetFeature", "HidD_GetFeature"):
    getattr(hid, _f).restype = W.BOOL
hid.HidD_GetAttributes.argtypes = [C.c_void_p, C.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [C.c_void_p, C.POINTER(C.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [C.c_void_p]
hid.HidP_GetCaps.argtypes = [C.c_void_p, C.POINTER(HIDP_CAPS)]
hid.HidD_GetManufacturerString.argtypes = [C.c_void_p, C.c_void_p, W.ULONG]
hid.HidD_GetProductString.argtypes = [C.c_void_p, C.c_void_p, W.ULONG]
hid.HidD_GetSerialNumberString.argtypes = [C.c_void_p, C.c_void_p, W.ULONG]
hid.HidD_SetFeature.argtypes = [C.c_void_p, C.c_void_p, W.ULONG]
hid.HidD_GetFeature.argtypes = [C.c_void_p, C.c_void_p, W.ULONG]

DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x02, 0x10
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 1, 2
OPEN_EXISTING = 3
INVALID = C.c_void_p(-1).value

def enumerate_hid():
    g = GUID()
    hid.HidD_GetHidGuid(C.byref(g))
    hdev = setupapi.SetupDiGetClassDevsW(C.byref(g), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    out = []
    i = 0
    while True:
        did = SP_DEVICE_INTERFACE_DATA()
        did.cbSize = C.sizeof(did)
        if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None, C.byref(g), i, C.byref(did)):
            break
        i += 1
        need = W.DWORD()
        setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, C.byref(did), None, 0,
                                                  C.byref(need), None)
        detail = SP_DEVICE_INTERFACE_DETAIL_DATA_W()
        detail.cbSize = 8 if C.sizeof(C.c_void_p) == 8 else 6
        if not setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, C.byref(did), C.byref(detail),
                                                         need, C.byref(need), None):
            continue
        out.append(detail.DevicePath)
    setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out

def open_path(path, rw=True):
    access = (GENERIC_READ | GENERIC_WRITE) if rw else 0
    h = k32.CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE,
                        None, OPEN_EXISTING, 0, None)
    return h if h not in (INVALID, 0, None) else None

def describe(path):
    h = open_path(path, rw=True) or open_path(path, rw=False)
    if not h:
        return None
    try:
        attrs = HIDD_ATTRIBUTES(); attrs.Size = C.sizeof(attrs)
        if not hid.HidD_GetAttributes(h, C.byref(attrs)):
            return None
        pp = C.c_void_p()
        caps = HIDP_CAPS()
        if hid.HidD_GetPreparsedData(h, C.byref(pp)):
            hid.HidP_GetCaps(pp, C.byref(caps))
            hid.HidD_FreePreparsedData(pp)
        buf = C.create_unicode_buffer(256)
        man = buf.value if hid.HidD_GetManufacturerString(h, buf, 512) else ""
        buf2 = C.create_unicode_buffer(256)
        prod = buf2.value if hid.HidD_GetProductString(h, buf2, 512) else ""
        return dict(path=path, vid=attrs.VendorID, pid=attrs.ProductID,
                    ver=attrs.VersionNumber, usage_page=caps.UsagePage, usage=caps.Usage,
                    in_len=caps.InputReportByteLength, out_len=caps.OutputReportByteLength,
                    feat_len=caps.FeatureReportByteLength, manufacturer=man, product=prod)
    finally:
        k32.CloseHandle(h)

if __name__ == "__main__":
    want = None
    if len(sys.argv) > 2:
        want = (int(sys.argv[1], 16), int(sys.argv[2], 16))
    for p in enumerate_hid():
        d = describe(p)
        if not d:
            continue
        if want and (d["vid"], d["pid"]) != want:
            continue
        print(f"{d['vid']:04x}:{d['pid']:04x} ver={d['ver']:04x} "
              f"UP={d['usage_page']:04x} U={d['usage']:04x} "
              f"in={d['in_len']:>3} out={d['out_len']:>3} feat={d['feat_len']:>3} "
              f"| {d['manufacturer']} {d['product']}")
        print(f"     {d['path']}")

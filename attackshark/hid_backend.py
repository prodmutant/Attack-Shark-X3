"""Windows HID transport for Attack Shark X3 class mice.

Dependency-free: talks to hid.dll / setupapi.dll through ctypes. The vendor
software reaches the mouse the same way (hiddriver_1.dll -> HidD_SetFeature).
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W

setupapi = C.WinDLL("setupapi")
hid = C.WinDLL("hid")
k32 = C.WinDLL("kernel32")


class GUID(C.Structure):
    _fields_ = [("Data1", W.DWORD), ("Data2", W.WORD), ("Data3", W.WORD),
                ("Data4", C.c_ubyte * 8)]


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
                ("InputReportByteLength", W.USHORT),
                ("OutputReportByteLength", W.USHORT),
                ("FeatureReportByteLength", W.USHORT), ("Reserved", W.USHORT * 17),
                ("NumberLinkCollectionNodes", W.USHORT),
                ("NumberInputButtonCaps", W.USHORT),
                ("NumberInputValueCaps", W.USHORT),
                ("NumberInputDataIndices", W.USHORT),
                ("NumberOutputButtonCaps", W.USHORT),
                ("NumberOutputValueCaps", W.USHORT),
                ("NumberOutputDataIndices", W.USHORT),
                ("NumberFeatureButtonCaps", W.USHORT),
                ("NumberFeatureValueCaps", W.USHORT),
                ("NumberFeatureDataIndices", W.USHORT)]


# Without explicit prototypes 64-bit handles get truncated to int32.
setupapi.SetupDiGetClassDevsW.restype = C.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [C.POINTER(GUID), W.LPCWSTR, W.HWND, W.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    C.c_void_p, C.c_void_p, C.POINTER(GUID), W.DWORD,
    C.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    C.c_void_p, C.POINTER(SP_DEVICE_INTERFACE_DATA), C.c_void_p, W.DWORD,
    C.POINTER(W.DWORD), C.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [C.c_void_p]
k32.CreateFileW.restype = C.c_void_p
k32.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p,
                            W.DWORD, W.DWORD, C.c_void_p]
k32.CloseHandle.argtypes = [C.c_void_p]
for _f in ("HidD_GetAttributes", "HidD_GetPreparsedData", "HidD_FreePreparsedData",
           "HidD_GetManufacturerString", "HidD_GetProductString",
           "HidD_SetFeature", "HidD_GetFeature"):
    getattr(hid, _f).restype = W.BOOL
hid.HidD_GetAttributes.argtypes = [C.c_void_p, C.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [C.c_void_p, C.POINTER(C.c_void_p)]
hid.HidD_FreePreparsedData.argtypes = [C.c_void_p]
hid.HidP_GetCaps.argtypes = [C.c_void_p, C.POINTER(HIDP_CAPS)]
hid.HidD_GetManufacturerString.argtypes = [C.c_void_p, C.c_void_p, W.ULONG]
hid.HidD_GetProductString.argtypes = [C.c_void_p, C.c_void_p, W.ULONG]
hid.HidD_SetFeature.argtypes = [C.c_void_p, C.c_void_p, W.ULONG]
hid.HidD_GetFeature.argtypes = [C.c_void_p, C.c_void_p, W.ULONG]
k32.ReadFile.argtypes = [C.c_void_p, C.c_void_p, W.DWORD,
                         C.POINTER(W.DWORD), C.c_void_p]
k32.ReadFile.restype = W.BOOL
k32.CancelIoEx.argtypes = [C.c_void_p, C.c_void_p]

DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x02, 0x10
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 1, 2
OPEN_EXISTING = 3
INVALID_HANDLE = C.c_void_p(-1).value


def _paths():
    g = GUID()
    hid.HidD_GetHidGuid(C.byref(g))
    hdev = setupapi.SetupDiGetClassDevsW(C.byref(g), None, None,
                                         DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    out, i = [], 0
    while True:
        did = SP_DEVICE_INTERFACE_DATA()
        did.cbSize = C.sizeof(did)
        if not setupapi.SetupDiEnumDeviceInterfaces(hdev, None, C.byref(g), i,
                                                    C.byref(did)):
            break
        i += 1
        need = W.DWORD()
        setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, C.byref(did), None, 0,
                                                  C.byref(need), None)
        detail = SP_DEVICE_INTERFACE_DETAIL_DATA_W()
        detail.cbSize = 8 if C.sizeof(C.c_void_p) == 8 else 6
        if setupapi.SetupDiGetDeviceInterfaceDetailW(hdev, C.byref(did),
                                                     C.byref(detail), need,
                                                     C.byref(need), None):
            out.append(detail.DevicePath)
    setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return out


def _open(path, rw=True):
    access = (GENERIC_READ | GENERIC_WRITE) if rw else 0
    h = k32.CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE,
                        None, OPEN_EXISTING, 0, None)
    return None if h in (INVALID_HANDLE, 0, None) else h


def describe(path):
    h = _open(path, True) or _open(path, False)
    if not h:
        return None
    try:
        a = HIDD_ATTRIBUTES()
        a.Size = C.sizeof(a)
        if not hid.HidD_GetAttributes(h, C.byref(a)):
            return None
        caps = HIDP_CAPS()
        pp = C.c_void_p()
        if hid.HidD_GetPreparsedData(h, C.byref(pp)):
            hid.HidP_GetCaps(pp, C.byref(caps))
            hid.HidD_FreePreparsedData(pp)
        b1 = C.create_unicode_buffer(128)
        b2 = C.create_unicode_buffer(128)
        man = b1.value if hid.HidD_GetManufacturerString(h, b1, 256) else ""
        prod = b2.value if hid.HidD_GetProductString(h, b2, 256) else ""
        return dict(path=path, vid=a.VendorID, pid=a.ProductID,
                    version=a.VersionNumber, usage_page=caps.UsagePage,
                    usage=caps.Usage, feature_len=caps.FeatureReportByteLength,
                    input_len=caps.InputReportByteLength,
                    manufacturer=man, product=prod)
    finally:
        k32.CloseHandle(h)


def find_interfaces(vid, pid, usage_page=None):
    out = []
    for p in _paths():
        d = describe(p)
        if not d or (d["vid"], d["pid"]) != (vid, pid):
            continue
        if usage_page is not None and d["usage_page"] != usage_page:
            continue
        out.append(d)
    return out


class HidInterface:
    """One opened HID collection; feature reports go in and out through here."""

    def __init__(self, info):
        self.info = info
        self.path = info["path"]
        self.feature_len = info["feature_len"]
        self._h = None

    def open(self):
        self._h = _open(self.path, True) or _open(self.path, False)
        if not self._h:
            raise OSError("cannot open " + self.path)
        return self

    def close(self):
        if self._h:
            k32.CloseHandle(self._h)
            self._h = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()

    def set_feature(self, data, pad_to=None):
        """HidD_SetFeature; data[0] is the report ID. Returns (ok, win32_error)."""
        n = pad_to if pad_to is not None else len(data)
        buf = C.create_string_buffer(bytes(data).ljust(n, b"\0"), n)
        ok = bool(hid.HidD_SetFeature(self._h, buf, n))
        return ok, (0 if ok else k32.GetLastError())

    def read_input(self, timeout=3.0):
        """Block for one input report, giving up after `timeout` seconds.

        The status collection has no feature or input-report *get*, so the only
        way to read battery is to wait for the mouse to send one. It emits a
        report shortly after the collection is opened, which makes this behave
        like an on-demand read.
        """
        import threading
        n = self.info.get("input_len") or 8
        buf = C.create_string_buffer(n)
        got = W.DWORD()
        result = {}

        def worker():
            if k32.ReadFile(self._h, buf, n, C.byref(got), None):
                result["data"] = bytes(buf.raw[:got.value])

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            # unblock the parked ReadFile so the handle can be closed cleanly
            try:
                k32.CancelIoEx(self._h, None)
            except Exception:
                pass
            t.join(0.5)
        return result.get("data")

    def get_feature(self, report_id, length=None):
        n = length or self.feature_len
        buf = C.create_string_buffer(n)
        buf[0] = bytes([report_id])
        if not hid.HidD_GetFeature(self._h, buf, n):
            return None
        return buf.raw

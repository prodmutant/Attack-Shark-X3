"""Prove where a mouse event came from.

The claim this project makes about the filter driver is falsifiable, so it
should be tested rather than believed. Windows exposes two independent ways to
ask where an event originated:

  * A low-level mouse hook sees `LLMHF_INJECTED` in MSLLHOOKSTRUCT.flags.
    win32k sets that bit for everything SendInput produces. It is the check
    every "is this a bot" routine starts with.
  * Raw Input reports the *device handle* that produced each event.
    GetRawInputDeviceInfo turns that into a device path, so you can see whether
    an event is attributed to real hardware, and to which piece of it.
    SendInput events arrive with a null handle.

This runs the same movement three ways - physically, through SendInput, and
through the filter driver - and tabulates what those two mechanisms saw each
time.

    python tools/verify_injection.py            # the three-way comparison
    python tools/verify_injection.py --status   # driver status only
    python tools/verify_injection.py --watch    # physical reports from kernel

Needs to be elevated to reach the driver; the SendInput and physical phases
work either way, so an unelevated run still shows the contrast between them.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attackshark import kdriver, motion            # noqa: E402

u32 = C.WinDLL("user32", use_last_error=True)
k32 = C.WinDLL("kernel32", use_last_error=True)

WM_INPUT = 0x00FF
WM_MOUSEMOVE = 0x0200
WH_MOUSE_LL = 14
LLMHF_INJECTED = 0x00000001
LLMHF_LOWER_IL_INJECTED = 0x00000002

RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIDEV_INPUTSINK = 0x00000100

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001

ULONG_PTR = C.c_ulonglong if C.sizeof(C.c_void_p) == 8 else C.c_ulong


class MSLLHOOKSTRUCT(C.Structure):
    _fields_ = [("pt", W.POINT), ("mouseData", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class RAWINPUTDEVICE(C.Structure):
    _fields_ = [("usUsagePage", C.c_ushort), ("usUsage", C.c_ushort),
                ("dwFlags", W.DWORD), ("hwndTarget", W.HWND)]


class RAWINPUTHEADER(C.Structure):
    _fields_ = [("dwType", W.DWORD), ("dwSize", W.DWORD),
                ("hDevice", W.HANDLE), ("wParam", W.WPARAM)]


class _BUTTONS(C.Structure):
    _fields_ = [("usButtonFlags", C.c_ushort), ("usButtonData", C.c_ushort)]


class _BUNION(C.Union):
    _fields_ = [("ulButtons", W.DWORD), ("b", _BUTTONS)]


class RAWMOUSE(C.Structure):
    _fields_ = [("usFlags", C.c_ushort), ("u", _BUNION),
                ("ulRawButtons", W.DWORD), ("lLastX", C.c_long),
                ("lLastY", C.c_long), ("ulExtraInformation", W.DWORD)]


class RAWINPUT(C.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", C.c_long), ("dy", C.c_long), ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _IU(C.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", W.DWORD), ("u", _IU)]


HOOKPROC = C.WINFUNCTYPE(C.c_longlong, C.c_int, W.WPARAM, W.LPARAM)
WNDPROC = C.WINFUNCTYPE(C.c_longlong, W.HWND, C.c_uint, W.WPARAM, W.LPARAM)

u32.SetWindowsHookExW.argtypes = [C.c_int, HOOKPROC, W.HINSTANCE, W.DWORD]
u32.SetWindowsHookExW.restype = C.c_void_p
u32.CallNextHookEx.argtypes = [C.c_void_p, C.c_int, W.WPARAM, W.LPARAM]
u32.CallNextHookEx.restype = C.c_longlong
u32.UnhookWindowsHookEx.argtypes = [C.c_void_p]
u32.GetRawInputData.argtypes = [W.HANDLE, W.UINT, C.c_void_p,
                                C.POINTER(W.UINT), W.UINT]
u32.GetRawInputDeviceInfoW.argtypes = [W.HANDLE, W.UINT, C.c_void_p, C.POINTER(W.UINT)]
u32.RegisterRawInputDevices.argtypes = [C.POINTER(RAWINPUTDEVICE), W.UINT, W.UINT]
u32.SendInput.argtypes = [C.c_uint, C.POINTER(INPUT), C.c_int]
u32.DefWindowProcW.argtypes = [W.HWND, C.c_uint, W.WPARAM, W.LPARAM]
u32.DefWindowProcW.restype = C.c_longlong


class WNDCLASS(C.Structure):
    _fields_ = [("style", W.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", C.c_int), ("cbWndExtra", C.c_int),
                ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH),
                ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]


_device_names = {}


def device_name(handle):
    """Device path behind a Raw Input device handle, cached."""
    if not handle:
        return "(none - synthesised by SendInput)"
    if handle in _device_names:
        return _device_names[handle]
    size = W.UINT(0)
    u32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, C.byref(size))
    buf = C.create_unicode_buffer(size.value + 1)
    u32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, buf, C.byref(size))
    _device_names[handle] = buf.value or f"(handle {handle})"
    return _device_names[handle]


class Listener:
    """Both observation mechanisms, on one thread with a message pump."""

    def __init__(self):
        self.phase = None
        self.records = {}
        self.lock = threading.Lock()
        self._hwnd = None
        self._hook = None
        self._tid = None
        self._ready = threading.Event()
        self._hookproc = HOOKPROC(self._on_hook)
        self._wndproc = WNDPROC(self._on_msg)
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("listener failed to start")

    def stop(self):
        if self._tid:
            u32.PostThreadMessageW(self._tid, 0x0012, 0, 0)     # WM_QUIT
        self._thread.join(2)

    def begin(self, phase):
        with self.lock:
            self.phase = phase
            self.records[phase] = {"hook": 0, "injected": 0, "lowil": 0,
                                   "raw": 0, "devices": {}, "dx": 0, "dy": 0}

    def end(self):
        with self.lock:
            self.phase = None

    # ------------------------------------------------------------ callbacks --
    def _on_hook(self, code, wparam, lparam):
        if code == 0 and wparam == WM_MOUSEMOVE:
            ms = C.cast(lparam, C.POINTER(MSLLHOOKSTRUCT)).contents
            with self.lock:
                r = self.records.get(self.phase)
                if r is not None:
                    r["hook"] += 1
                    if ms.flags & LLMHF_INJECTED:
                        r["injected"] += 1
                    if ms.flags & LLMHF_LOWER_IL_INJECTED:
                        r["lowil"] += 1
        return u32.CallNextHookEx(None, code, wparam, lparam)

    def _on_msg(self, hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            size = W.UINT(0)
            u32.GetRawInputData(lparam, RID_INPUT, None, C.byref(size),
                                C.sizeof(RAWINPUTHEADER))
            buf = C.create_string_buffer(size.value)
            got = u32.GetRawInputData(lparam, RID_INPUT, buf, C.byref(size),
                                      C.sizeof(RAWINPUTHEADER))
            if got and got != 0xFFFFFFFF:
                ri = C.cast(buf, C.POINTER(RAWINPUT)).contents
                if ri.header.dwType == 0:       # RIM_TYPEMOUSE
                    with self.lock:
                        r = self.records.get(self.phase)
                        if r is not None:
                            r["raw"] += 1
                            r["dx"] += ri.mouse.lLastX
                            r["dy"] += ri.mouse.lLastY
                            name = device_name(ri.header.hDevice)
                            r["devices"][name] = r["devices"].get(name, 0) + 1
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self):
        self._tid = k32.GetCurrentThreadId()

        cls = WNDCLASS()
        cls.lpfnWndProc = self._wndproc
        cls.hInstance = k32.GetModuleHandleW(None)
        cls.lpszClassName = "AsxVerifySink"
        u32.RegisterClassW(C.byref(cls))
        self._hwnd = u32.CreateWindowExW(0, "AsxVerifySink", "AsxVerifySink",
                                         0, 0, 0, 0, 0, W.HWND(-3),   # HWND_MESSAGE
                                         None, cls.hInstance, None)

        rid = RAWINPUTDEVICE(1, 2, RIDEV_INPUTSINK, self._hwnd)
        if not u32.RegisterRawInputDevices(C.byref(rid), 1, C.sizeof(RAWINPUTDEVICE)):
            print(f"warning: RegisterRawInputDevices failed ({C.get_last_error()})")

        self._hook = u32.SetWindowsHookExW(WH_MOUSE_LL, self._hookproc, None, 0)
        if not self._hook:
            print(f"warning: SetWindowsHookEx failed ({C.get_last_error()})")

        self._ready.set()

        msg = W.MSG()
        while u32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(C.byref(msg))
            u32.DispatchMessageW(C.byref(msg))

        if self._hook:
            u32.UnhookWindowsHookEx(self._hook)


def send_input_move(steps):
    for delay_s, ix, iy in steps:
        i = INPUT(type=INPUT_MOUSE)
        i.mi = MOUSEINPUT(ix, iy, 0, MOUSEEVENTF_MOVE, 0, 0)
        u32.SendInput(1, C.byref(i), C.sizeof(INPUT))
        if delay_s > 0:
            time.sleep(delay_s)


def report(name, r):
    if r is None:
        print(f"  {name:<12} (not run)")
        return
    verdict = []
    if r["hook"]:
        verdict.append("INJECTED" if r["injected"] else "not injected")
    devs = ", ".join(f"{d}" for d in r["devices"]) or "(no raw input seen)"
    print(f"  {name}")
    print(f"      hook events      {r['hook']}"
          + (f"   injected: {r['injected']}   lower-IL: {r['lowil']}" if r["hook"] else ""))
    print(f"      raw input        {r['raw']}   total dx,dy = {r['dx']},{r['dy']}")
    print(f"      attributed to    {devs}")
    if verdict:
        print(f"      verdict          {verdict[0]}")
    print()


def phase_physical(lis, seconds):
    print(f"[1] Physical. Move the mouse by hand for {seconds} seconds...")
    lis.begin("physical")
    time.sleep(seconds)
    lis.end()


def phase_sendinput(lis, steps):
    print("[2] SendInput. The old path, for comparison.")
    lis.begin("sendinput")
    time.sleep(0.2)
    send_input_move(steps)
    time.sleep(0.4)
    lis.end()


def phase_driver(lis, steps):
    print("[3] Filter driver.")
    try:
        drv = kdriver.Driver()
    except kdriver.DriverError as e:
        print(f"    unavailable: {e}\n")
        return False
    with drv:
        st = drv.status()
        if not st["connected"]:
            print("    the driver is loaded but not attached to the mouse stack;")
            print("    is the X3 connected? status:", st)
            return False
        lis.begin("driver")
        time.sleep(0.2)
        drv.move(steps)
        # wait for the queue to drain, plus slack
        deadline = time.time() + 5
        while time.time() < deadline and drv.status()["playing"]:
            time.sleep(0.05)
        time.sleep(0.4)
        lis.end()
    return True


def cmd_status():
    try:
        with kdriver.Driver() as d:
            st = d.status()
    except kdriver.DriverError as e:
        print(f"driver: {e}")
        return 1
    width = max(len(k) for k in st)
    print("asxfilter status")
    for k, v in st.items():
        if k == "suppress_buttons" and v:
            v = f"0x{v:04X}  ({', '.join(kdriver.describe_buttons(v))})"
        print(f"  {k:<{width}}  {v}")
    return 0


def cmd_watch():
    try:
        drv = kdriver.Driver()
    except kdriver.DriverError as e:
        print(f"driver: {e}")
        return 1
    print("Physical reports as the kernel sees them. Ctrl-C to stop.\n")
    with drv:
        drv.set_filter(report_events=True)
        try:
            while True:
                for ev in drv.read_events():
                    b = ", ".join(kdriver.describe_buttons(ev["buttons"])) or "-"
                    print(f"  dx={ev['dx']:+5d} dy={ev['dy']:+5d}  {b}"
                          + ("   [suppressed]" if ev["suppressed"] else ""))
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true", help="driver status and exit")
    ap.add_argument("--watch", action="store_true", help="stream physical reports")
    ap.add_argument("--seconds", type=float, default=4.0,
                    help="how long to watch for physical movement")
    ap.add_argument("--dx", type=int, default=120)
    ap.add_argument("--dy", type=int, default=0)
    args = ap.parse_args()

    if args.status:
        return cmd_status()
    if args.watch:
        return cmd_watch()

    steps = motion.plan(args.dx, args.dy, profile="human", seed=1)
    print(f"Plan: {len(steps)} reports, "
          f"{round(sum(s[0] for s in steps) * 1000)} ms, "
          f"total {args.dx},{args.dy}\n")

    lis = Listener()
    lis.start()
    try:
        phase_physical(lis, args.seconds)
        phase_sendinput(lis, steps)
        ran_driver = phase_driver(lis, steps)
    finally:
        lis.stop()

    print("\n" + "=" * 68)
    print("What the two detection mechanisms saw")
    print("=" * 68 + "\n")
    report("physical  (you moved the mouse)", lis.records.get("physical"))
    report("sendinput (the old path)", lis.records.get("sendinput"))
    report("driver    (asxfilter)", lis.records.get("driver") if ran_driver else None)

    p, s, d = (lis.records.get(k) for k in ("physical", "sendinput", "driver"))
    if p and s and d:
        same_dev = set(p["devices"]) & set(d["devices"])
        print("Conclusion")
        print(f"  SendInput was flagged injected      : "
              f"{'yes' if s['injected'] else 'no'}")
        print(f"  Driver output was flagged injected  : "
              f"{'yes' if d['injected'] else 'no'}")
        print(f"  Driver output attributed to the same device as your hand : "
              f"{'yes' if same_dev else 'no'}")
        if same_dev:
            print(f"      {sorted(same_dev)[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

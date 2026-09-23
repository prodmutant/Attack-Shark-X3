"""Talk to the asxfilter kernel driver.

This is the replacement for the SendInput path in `hostrun.py`. The difference
is not cosmetic:

* SendInput hands the event to win32k, which stamps it `LLMHF_INJECTED` and
  gives Raw Input a null device handle. Anything that looks can tell.
* This hands a whole plan to a driver sitting inside the X3's own mouse stack,
  which emits it through the same mouclass entry point mouhid calls for a
  physical report. There is no injection flag to set, and Raw Input attributes
  it to the X3, because as far as every layer above mouclass is concerned the
  mouse moved.

`tools/verify_injection.py` demonstrates both claims rather than asserting them.

The control device is ACLed to SYSTEM and Administrators, so a process using
this must be elevated. `available()` says whether the driver is there at all;
everything else raises `DriverError` if it is not.

Two handles are opened. A blocking `read_events()` would otherwise stall every
other call, because a synchronous file object serialises its I/O. The driver
refcounts opens, so both must close before it resets the physical-side config.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import threading

k32 = C.WinDLL("kernel32", use_last_error=True)

DEVICE_PATH = r"\\.\AttackSharkFilter"

GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 0x00000001, 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = C.c_void_p(-1).value

ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_OPERATION_ABORTED = 995

_TYPE = 0x8ADF
METHOD_BUFFERED = 0
FILE_READ_ACCESS, FILE_WRITE_ACCESS = 1, 2


def _ctl(function, access):
    return (_TYPE << 16) | (access << 14) | (function << 2) | METHOD_BUFFERED


IOCTL_STATUS = _ctl(0x800, FILE_READ_ACCESS)
IOCTL_SUBMIT = _ctl(0x801, FILE_WRITE_ACCESS)
IOCTL_STOP = _ctl(0x802, FILE_WRITE_ACCESS)
IOCTL_SET_FILTER = _ctl(0x803, FILE_WRITE_ACCESS)
IOCTL_READ_EVENTS = _ctl(0x804, FILE_READ_ACCESS)

#: MOUSE_INPUT_DATA ButtonFlags, mirrored from asxfilter_public.h.
LEFT_DOWN, LEFT_UP = 0x0001, 0x0002
RIGHT_DOWN, RIGHT_UP = 0x0004, 0x0008
MIDDLE_DOWN, MIDDLE_UP = 0x0010, 0x0020
BUTTON4_DOWN, BUTTON4_UP = 0x0040, 0x0080
BUTTON5_DOWN, BUTTON5_UP = 0x0100, 0x0200
WHEEL, HWHEEL = 0x0400, 0x0800
ALL_BUTTONS = 0x03FF

#: The filter's interface version this client is written against. A driver
#: reporting less than 1.1 has no wheel field: everything else works, and a
#: wheel bound to a macro scrolls the page as well as firing it.
INTERFACE_VERSION = (1, 1)

#: SuppressWheel values. The wheel is one ButtonFlags bit with a signed delta
#: rather than a transition per direction, so it cannot be expressed in the
#: button mask - withholding a notch means knowing its sign, which is a
#: decision the filter has to make per report.
WHEEL_NONE, WHEEL_UP, WHEEL_DOWN = 0x0, 0x1, 0x2
WHEEL_DIR_BITS = {"up": WHEEL_UP, "down": WHEEL_DOWN}

#: vendor-UI button number -> (down, up). 4 is forward, 5 is back, as
#: everywhere else in this project.
BUTTON_FLAGS = {
    1: (LEFT_DOWN, LEFT_UP),
    2: (RIGHT_DOWN, RIGHT_UP),
    3: (MIDDLE_DOWN, MIDDLE_UP),
    4: (BUTTON4_DOWN, BUTTON4_UP),
    5: (BUTTON5_DOWN, BUTTON5_UP),
}

#: hostrun's button names -> the same pairs
NAME_FLAGS = {
    "left": (LEFT_DOWN, LEFT_UP),
    "right": (RIGHT_DOWN, RIGHT_UP),
    "middle": (MIDDLE_DOWN, MIDDLE_UP),
    "x2": (BUTTON4_DOWN, BUTTON4_UP),       # forward
    "x1": (BUTTON5_DOWN, BUTTON5_UP),       # back
}

SUBMIT_REPLACE = 0x00000001
SUBMIT_RELEASE = 0x00000002

MAX_STEPS_PER_SUBMIT = 4096


class DriverError(RuntimeError):
    pass


class NotInstalled(DriverError):
    pass


class NeedsElevation(DriverError):
    pass


class STEP(C.Structure):
    _fields_ = [("DelayUs", W.DWORD), ("Dx", C.c_long), ("Dy", C.c_long),
                ("Buttons", C.c_ushort), ("Data", C.c_short)]


class EVENT(C.Structure):
    _fields_ = [("Time", C.c_ulonglong), ("Buttons", C.c_ushort),
                ("Data", C.c_short), ("Dx", C.c_long), ("Dy", C.c_long),
                ("Suppressed", W.DWORD)]


class FILTER_CFG(C.Structure):
    _fields_ = [("SuppressButtons", W.DWORD), ("SuppressMove", W.DWORD),
                ("ReportEvents", W.DWORD), ("SuppressWheel", W.DWORD)]


class STATUS(C.Structure):
    _fields_ = [("Version", W.DWORD), ("Attached", W.DWORD),
                ("Connected", W.DWORD), ("Playing", W.DWORD),
                ("Queued", W.DWORD), ("Capacity", W.DWORD),
                ("SuppressButtons", W.DWORD), ("SuppressMove", W.DWORD),
                ("StepsEmitted", C.c_ulonglong),
                ("PhysicalReports", C.c_ulonglong),
                ("Dropped", C.c_ulonglong),
                # Appended, never inserted: an older filter fills the fields
                # above and stops, so the layout it wrote stays a prefix of
                # this one and the tail simply reads back as the zero it was
                # initialised to.
                ("SuppressWheel", W.DWORD)]


k32.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p,
                            W.DWORD, W.DWORD, W.HANDLE]
k32.CreateFileW.restype = W.HANDLE
k32.DeviceIoControl.argtypes = [W.HANDLE, W.DWORD, C.c_void_p, W.DWORD,
                                C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
k32.DeviceIoControl.restype = W.BOOL
k32.CloseHandle.argtypes = [W.HANDLE]
k32.CancelIoEx.argtypes = [W.HANDLE, C.c_void_p]


def _open():
    h = k32.CreateFileW(DEVICE_PATH, GENERIC_READ | GENERIC_WRITE,
                        FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                        OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE or h is None:
        err = C.get_last_error()
        if err == ERROR_FILE_NOT_FOUND:
            raise NotInstalled(
                "the asxfilter driver is not loaded. Build it with "
                "'python tools/build_driver.py' and install it with "
                "'tools/install_driver.ps1'.")
        if err == ERROR_ACCESS_DENIED:
            raise NeedsElevation(
                "access denied opening the filter control device. It is "
                "restricted to Administrators; run this elevated.")
        raise DriverError(f"CreateFile({DEVICE_PATH}) failed ({err})")
    return h


def available():
    """True if the driver is installed and this process may talk to it."""
    try:
        h = _open()
    except DriverError:
        return False
    k32.CloseHandle(h)
    return True


class Driver:
    """An open connection to the filter.

    Use as a context manager, or call close(). Closing both handles returns the
    physical mouse to normal, which is also what happens if the process dies.
    """

    def __init__(self):
        self._h = _open()
        try:
            self._he = _open()
        except DriverError:
            k32.CloseHandle(self._h)
            raise
        self._lock = threading.Lock()
        self._closed = False

    # --------------------------------------------------------------- core --
    def _ioctl(self, handle, code, inbuf=None, outbuf=None):
        inp = C.byref(inbuf) if inbuf is not None else None
        inl = C.sizeof(inbuf) if inbuf is not None else 0
        outp = C.byref(outbuf) if outbuf is not None else None
        outl = C.sizeof(outbuf) if outbuf is not None else 0
        got = W.DWORD(0)
        ok = k32.DeviceIoControl(handle, code, inp, inl, outp, outl, C.byref(got), None)
        if not ok:
            err = C.get_last_error()
            if err == ERROR_OPERATION_ABORTED:
                return None
            raise DriverError(f"ioctl 0x{code:08X} failed ({err})")
        return got.value

    def close(self):
        if self._closed:
            return
        self._closed = True
        k32.CancelIoEx(self._he, None)
        for h in (self._he, self._h):
            k32.CloseHandle(h)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------- status --
    def status(self):
        st = STATUS()
        with self._lock:
            self._ioctl(self._h, IOCTL_STATUS, None, st)
        return {
            "version": f"{st.Version >> 16}.{st.Version & 0xFFFF}",
            "attached": bool(st.Attached),
            "connected": bool(st.Connected),
            "playing": bool(st.Playing),
            "queued": st.Queued,
            "capacity": st.Capacity,
            "suppress_buttons": st.SuppressButtons,
            "suppress_move": bool(st.SuppressMove),
            "suppress_wheel": st.SuppressWheel,
            "wheel_suppression": (st.Version >> 16,
                                  st.Version & 0xFFFF) >= INTERFACE_VERSION,
            "steps_emitted": st.StepsEmitted,
            "physical_reports": st.PhysicalReports,
            "dropped": st.Dropped,
        }

    # ------------------------------------------------------------ emitting --
    def submit(self, steps, replace=False, release=False):
        """Queue steps as (delay_us, dx, dy, buttons, data) tuples.

        Long plans are split across several calls because the driver caps one
        submit; the queue itself is much deeper, so playback does not stutter
        at the seam.
        """
        steps = list(steps)
        if not steps:
            return 0
        sent = 0
        flags_first = (SUBMIT_REPLACE if replace else 0)
        for i in range(0, len(steps), MAX_STEPS_PER_SUBMIT):
            chunk = steps[i:i + MAX_STEPS_PER_SUBMIT]
            last = (i + MAX_STEPS_PER_SUBMIT) >= len(steps)
            flags = (flags_first if i == 0 else 0)
            if last and release:
                flags |= SUBMIT_RELEASE

            arr = (STEP * len(chunk))()
            for j, s in enumerate(chunk):
                delay, dx, dy, buttons, data = s
                arr[j] = STEP(int(delay), int(dx), int(dy), int(buttons), int(data))

            class _Submit(C.Structure):
                _fields_ = [("Count", W.DWORD), ("Flags", W.DWORD),
                            ("Steps", STEP * len(chunk))]

            payload = _Submit(len(chunk), flags, arr)
            with self._lock:
                self._ioctl(self._h, IOCTL_SUBMIT, payload, None)
            sent += len(chunk)
        return sent

    def move(self, plan_steps, replace=False):
        """Submit a `motion.plan()` result: [(delay_s, dx, dy), ...]."""
        return self.submit(
            [(max(0, int(round(d * 1_000_000))), ix, iy, 0, 0) for d, ix, iy in plan_steps],
            replace=replace)

    def click(self, button="left", hold_ms=40, delay_us=0):
        down, up = NAME_FLAGS[button] if isinstance(button, str) else BUTTON_FLAGS[button]
        return self.submit([(delay_us, 0, 0, down, 0),
                            (int(hold_ms * 1000), 0, 0, up, 0)])

    def button(self, button, down, delay_us=0):
        d, u = NAME_FLAGS[button] if isinstance(button, str) else BUTTON_FLAGS[button]
        return self.submit([(delay_us, 0, 0, d if down else u, 0)])

    def wheel(self, notches=1, delay_us=0):
        return self.submit([(delay_us, 0, 0, WHEEL, int(notches) * 120)])

    def stop(self):
        with self._lock:
            self._ioctl(self._h, IOCTL_STOP, None, None)

    # ----------------------------------------------------- physical input --
    def set_filter(self, suppress_buttons=0, suppress_move=False,
                   report_events=False, suppress_wheel=0):
        """Control what the *physical* mouse is allowed to do.

        `suppress_buttons` is a mask of transitions to swallow inside the mouse
        stack, so a button bound to a macro never reaches any application - no
        hook, nothing to notice the click and eat it after the fact.

        `suppress_wheel` does the same one direction at a time, which the
        button mask cannot: a notch is one bit and a signed delta, so up and
        down are told apart by the sign and not by the flag.
        """
        cfg = FILTER_CFG(int(suppress_buttons) & ALL_BUTTONS,
                         1 if suppress_move else 0,
                         1 if report_events else 0,
                         int(suppress_wheel) & (WHEEL_UP | WHEEL_DOWN))
        with self._lock:
            self._ioctl(self._h, IOCTL_SET_FILTER, cfg, None)

    def suppress(self, buttons=(), move=False, report_events=True, wheel=()):
        """set_filter, by button number: suppress([4, 5], wheel=["up"])."""
        mask = 0
        for b in buttons:
            down, up = BUTTON_FLAGS[b] if not isinstance(b, str) else NAME_FLAGS[b]
            mask |= down | up
        wmask = 0
        for d in wheel:
            wmask |= WHEEL_DIR_BITS[d] if isinstance(d, str) else int(d)
        self.set_filter(mask, move, report_events, wmask)
        return mask

    def read_events(self, max_events=64):
        """Block until physical reports arrive; return them.

        Returns [] when the wait was cancelled (close() does that), so a reader
        thread can simply loop until it gets an empty list.
        """
        buf = (EVENT * max_events)()
        got = self._ioctl(self._he, IOCTL_READ_EVENTS, None, buf)
        if got is None:
            return []
        n = got // C.sizeof(EVENT)
        return [{"time": buf[i].Time, "buttons": buf[i].Buttons,
                 "data": buf[i].Data, "dx": buf[i].Dx, "dy": buf[i].Dy,
                 "suppressed": bool(buf[i].Suppressed)}
                for i in range(n)]


def describe_buttons(mask):
    """Readable name list for a ButtonFlags mask."""
    names = [(LEFT_DOWN, "L down"), (LEFT_UP, "L up"), (RIGHT_DOWN, "R down"),
             (RIGHT_UP, "R up"), (MIDDLE_DOWN, "M down"), (MIDDLE_UP, "M up"),
             (BUTTON4_DOWN, "4 down"), (BUTTON4_UP, "4 up"),
             (BUTTON5_DOWN, "5 down"), (BUTTON5_UP, "5 up"),
             (WHEEL, "wheel"), (HWHEEL, "hwheel")]
    return [n for bit, n in names if mask & bit]

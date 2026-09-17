"""Host-side macro engine.

There are two backends, and which one is in use changes what the mouse output
actually *is*:

**kernel** - the asxfilter driver (see docs/DRIVER.md). Mouse movement, buttons
and wheel are handed to a filter inside the X3's own device stack, which emits
them through the same mouclass callback mouhid uses for physical reports. They
carry no injection flag and Raw Input attributes them to the X3 itself. Trigger
buttons are swallowed *in the driver*, so no application sees the click at all,
and there is no hook to notice it after the fact. Timing comes from a kernel
high-resolution timer, so a movement keeps its cadence even under load.

**sendinput** - the fallback when the driver is not installed. Windows marks
everything SendInput produces as injected; the hook checks that flag and
ignores such events, which is what stops a macro bound to left click from
re-triggering itself. It is also why this path is honest about being software:
the flag is visible to anything that looks.

Either way, keyboard steps go out as scan codes via SendInput - a mouse filter
cannot emit keystrokes, and a keyboard filter is a separate piece of work.

Movement is never a single jump. A `move` step is expanded through
`motion.plan()` into one report per tick at the device's own rate, with a
minimum-jerk velocity profile; on the kernel backend the whole trajectory is
submitted as one batch and clocked out below the scheduler.

A hook callback must return promptly or Windows drops the hook, so playback
always happens on a worker thread, never inside the callback.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import random
import threading
import time

from . import macro as M
from . import motion as MO
from . import protocol as P

try:
    from . import kdriver
except Exception:                       # pragma: no cover - non-Windows import
    kdriver = None

u32 = C.WinDLL("user32", use_last_error=True)
k32 = C.WinDLL("kernel32", use_last_error=True)

WH_MOUSE_LL, WH_KEYBOARD_LL = 14, 13
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
LLMHF_INJECTED = 0x00000001
VK_ESCAPE = 0x1B

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP = 0x0080, 0x0100
MOUSEEVENTF_WHEEL = 0x0800
KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE, KEYEVENTF_EXTENDEDKEY = 0x0002, 0x0008, 0x0001
XBUTTON1, XBUTTON2 = 0x0001, 0x0002

ULONG_PTR = C.c_ulonglong if C.sizeof(C.c_void_p) == 8 else C.c_ulong


class MOUSEINPUT(C.Structure):
    _fields_ = [("dx", C.c_long), ("dy", C.c_long), ("mouseData", W.DWORD),
                ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(C.Structure):
    _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _IU(C.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(C.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", W.DWORD), ("u", _IU)]


class MSLLHOOKSTRUCT(C.Structure):
    _fields_ = [("pt", W.POINT), ("mouseData", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KBDLLHOOKSTRUCT(C.Structure):
    _fields_ = [("vkCode", W.DWORD), ("scanCode", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


u32.SendInput.argtypes = [C.c_uint, C.POINTER(INPUT), C.c_int]
u32.SendInput.restype = C.c_uint
HOOKPROC = C.WINFUNCTYPE(C.c_longlong, C.c_int, W.WPARAM, W.LPARAM)
u32.SetWindowsHookExW.argtypes = [C.c_int, HOOKPROC, W.HINSTANCE, W.DWORD]
u32.SetWindowsHookExW.restype = C.c_void_p
u32.CallNextHookEx.argtypes = [C.c_void_p, C.c_int, W.WPARAM, W.LPARAM]
u32.CallNextHookEx.restype = C.c_longlong
u32.UnhookWindowsHookEx.argtypes = [C.c_void_p]

#: HID usage -> PS/2 set-1 scan code. Games read scan codes, not virtual keys.
_SCAN = {}
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    _SCAN[P.HID_KEYS[_c]] = [0x1E, 0x30, 0x2E, 0x20, 0x12, 0x21, 0x22, 0x23, 0x17,
                             0x24, 0x25, 0x26, 0x32, 0x31, 0x18, 0x19, 0x10, 0x13,
                             0x1F, 0x14, 0x16, 0x2F, 0x11, 0x2D, 0x15, 0x2C][_i]
for _i, _c in enumerate("1234567890"):
    _SCAN[P.HID_KEYS[_c]] = 0x02 + _i
for _i in range(1, 11):
    _SCAN[P.HID_KEYS[f"f{_i}"]] = 0x3B + _i - 1
_SCAN[P.HID_KEYS["f11"]] = 0x57
_SCAN[P.HID_KEYS["f12"]] = 0x58
_SCAN.update({
    0x28: 0x1C, 0x29: 0x01, 0x2A: 0x0E, 0x2B: 0x0F, 0x2C: 0x39, 0x2D: 0x0C,
    0x2E: 0x0D, 0x2F: 0x1A, 0x30: 0x1B, 0x31: 0x2B, 0x33: 0x27, 0x34: 0x28,
    0x35: 0x29, 0x36: 0x33, 0x37: 0x34, 0x38: 0x35, 0x39: 0x3A, 0x47: 0x46,
})
#: keys that need the extended-key flag
_EXT = {0x49: 0x52, 0x4A: 0x47, 0x4B: 0x49, 0x4C: 0x53, 0x4D: 0x4F, 0x4E: 0x51,
        0x4F: 0x4D, 0x50: 0x4B, 0x51: 0x50, 0x52: 0x48}
_SCAN.update(_EXT)

_BTN_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, 0),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, 0),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, 0),
    "x1": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON1),
    "x2": (MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP, XBUTTON2),
}

#: vendor-UI button number -> the message pair the hook sees
TRIGGERS = {
    1: (WM_LBUTTONDOWN, WM_LBUTTONUP, None),
    2: (WM_RBUTTONDOWN, WM_RBUTTONUP, None),
    3: (WM_MBUTTONDOWN, WM_MBUTTONUP, None),
    4: (WM_XBUTTONDOWN, WM_XBUTTONUP, XBUTTON2),   # "forward"
    5: (WM_XBUTTONDOWN, WM_XBUTTONUP, XBUTTON1),   # "backward"
}


# ------------------------------------------------------------ synthesis ----
def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    u32.SendInput(len(inputs), arr, C.sizeof(INPUT))


def _mouse(flags, dx=0, dy=0, data=0):
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(dx, dy, data, flags, 0, 0)
    return i


def _key(usage, down):
    scan = _SCAN.get(usage)
    flags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP)
    if usage in _EXT:
        flags |= KEYEVENTF_EXTENDEDKEY
    i = INPUT(type=INPUT_KEYBOARD)
    i.ki = KEYBDINPUT(0, scan or 0, flags, 0, 0)
    return i


def play_step(step, speed=1.0, stop=None):
    """SendInput playback of one step. Used when there is no driver."""
    t = step["t"]
    if t == "key":
        _send(_key(P.HID_KEYS[step["key"]], step["down"]))
    elif t == "mouse":
        down, up, data = _BTN_FLAGS[step["button"]]
        _send(_mouse(down if step["down"] else up, data=data))
    elif t == "move":
        # still a trajectory rather than a jump, just a worse-timed one
        for delay_s, ix, iy in MO.plan(step["dx"], step["dy"]):
            _send(_mouse(MOUSEEVENTF_MOVE, ix, iy))
            wait = delay_s / speed
            if wait > 0:
                if stop is not None:
                    if stop.wait(wait):
                        return
                else:
                    time.sleep(wait)
    elif t == "wheel":
        _send(_mouse(MOUSEEVENTF_WHEEL, data=step["delta"] * 120))


def _delay_us(step, speed):
    ms = step["ms"]
    if step.get("jitter"):
        ms += random.uniform(-step["jitter"], step["jitter"])
    return max(0, int(ms * 1000.0 / speed))


def build_plan(steps, speed=1.0):
    """Compile a run of mouse-only steps into driver steps.

    Returns (plan, duration_s). Delays are folded into the next emitted step
    rather than being executed on this side, so the whole run is clocked by the
    kernel timer: a 40-report movement keeps its 1 ms cadence even if this
    thread is descheduled in the middle of it.
    """
    plan = []
    pending = 0
    for s in steps:
        t = s["t"]
        if t == "delay":
            pending += _delay_us(s, speed)
        elif t == "mouse":
            down, up = kdriver.NAME_FLAGS[s["button"]]
            plan.append((pending, 0, 0, down if s["down"] else up, 0))
            pending = 0
        elif t == "wheel":
            plan.append((pending, 0, 0, kdriver.WHEEL, int(s["delta"]) * 120))
            pending = 0
        elif t == "move":
            for delay_s, ix, iy in MO.plan(s["dx"], s["dy"]):
                us = max(0, int(round(delay_s * 1_000_000 / speed)))
                plan.append((pending + us, ix, iy, 0, 0))
                pending = 0
    return plan, sum(p[0] for p in plan) / 1_000_000.0


#: steps the driver can emit by itself; anything else breaks the batch
_DRIVER_STEPS = ("mouse", "move", "wheel", "delay")


def play(macro, stop: threading.Event, driver=None):
    """Run one macro until it finishes or `stop` is set.

    With a driver, consecutive mouse steps are compiled into a single submit
    and this thread only waits out their duration. A keyboard step ends the
    batch, because a mouse filter cannot type.
    """
    steps = macro["steps"]
    speed = macro["speed"] or 1.0
    mode, count = macro["repeat"], macro["count"]
    passes = 1 if mode == "once" else (count if mode == "count" else 1 << 30)

    done = 0
    while done < passes and not stop.is_set():
        i = 0
        while i < len(steps) and not stop.is_set():

            if driver is not None and steps[i]["t"] in _DRIVER_STEPS:
                j = i
                while j < len(steps) and steps[j]["t"] in _DRIVER_STEPS:
                    j += 1
                plan, seconds = build_plan(steps[i:j], speed)
                if plan:
                    try:
                        driver.submit(plan)
                    except kdriver.DriverError:
                        driver = None           # fall back for the rest of the run
                        continue
                    # the kernel is clocking it; just wait it out, interruptibly
                    if stop.wait(seconds + 0.002):
                        break
                i = j
                continue

            s = steps[i]
            i += 1
            if s["t"] == "delay":
                if stop.wait(_delay_us(s, speed) / 1_000_000.0):
                    break
            else:
                play_step(s, speed, stop)
        done += 1

    _release_all(macro, driver)


def _release_all(macro, driver=None):
    """Never leave a key or button stuck down if a macro is cut short."""
    if driver is not None:
        try:
            driver.stop()           # the driver releases what it emitted itself
        except Exception:
            pass
    for s in macro["steps"]:
        try:
            if s["t"] == "key" and s["down"]:
                _send(_key(P.HID_KEYS[s["key"]], False))
            elif s["t"] == "mouse" and s["down"] and driver is None:
                _, up, data = _BTN_FLAGS[s["button"]]
                _send(_mouse(up, data=data))
        except Exception:
            pass


# --------------------------------------------------------------- engine ----
class Engine:
    """Owns the hook thread and the running macros."""

    def __init__(self):
        self.bindings = {}          # button number -> macro dict
        self.passthrough = {}       # button -> let the real click through too
        self._running = {}          # button -> (thread, stop event)
        self._hook = None
        self._kbhook = None
        self._thread = None
        self._tid = None
        self._lock = threading.Lock()
        self._mproc = HOOKPROC(self._on_mouse)
        self._kproc = HOOKPROC(self._on_key)
        self.last_error = None
        self.driver = None          # kdriver.Driver when the filter is loaded
        self.driver_note = None
        self._reader = None
        self._stopping = False

    # ---------------------------------------------------------- lifecycle --
    @property
    def active(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def backend(self):
        return "kernel" if self.driver is not None else "sendinput"

    def _open_driver(self):
        """Attach to the filter driver, or record why we could not."""
        if kdriver is None:
            self.driver_note = "kdriver unavailable (not Windows?)"
            return
        try:
            drv = kdriver.Driver()
        except kdriver.DriverError as e:
            self.driver_note = str(e)
            return
        try:
            st = drv.status()
        except kdriver.DriverError as e:
            drv.close()
            self.driver_note = str(e)
            return
        self.driver = drv
        if not st["connected"]:
            self.driver_note = ("the filter is loaded but not attached to the "
                                "mouse stack - is the X3 connected?")
        else:
            self.driver_note = None

    def start(self):
        if self.active:
            return True
        self._stopping = False
        self._open_driver()

        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        for _ in range(50):
            if self._kbhook or self._hook:
                break
            time.sleep(0.02)

        if self.driver is not None:
            self._apply_suppression()
            self._reader = threading.Thread(target=self._pump_events, daemon=True)
            self._reader.start()
            return True
        return bool(self._hook)

    def stop(self):
        self._stopping = True
        self.stop_all()
        if self.driver is not None:
            try:
                self.driver.set_filter()        # unsuppress before letting go
            except Exception:
                pass
            self.driver.close()                 # unblocks the reader
            self.driver = None
        if self._tid:
            u32.PostThreadMessageW(self._tid, 0x0012, 0, 0)   # WM_QUIT
        self._thread = None
        self._reader = None

    def _pump(self):
        """Message pump for the hooks.

        The keyboard hook is always installed - it is the panic key. The mouse
        hook is only needed when there is no driver, because with one the
        triggers come out of the kernel instead.
        """
        self._tid = k32.GetCurrentThreadId()
        self._kbhook = u32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kproc, None, 0)
        if self.driver is None:
            self._hook = u32.SetWindowsHookExW(WH_MOUSE_LL, self._mproc, None, 0)
            if not self._hook:
                self.last_error = f"SetWindowsHookEx failed ({C.get_last_error()})"
                return
        msg = W.MSG()
        while u32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(C.byref(msg))
            u32.DispatchMessageW(C.byref(msg))
        for h in (self._hook, self._kbhook):
            if h:
                u32.UnhookWindowsHookEx(h)
        self._hook = self._kbhook = None

    # ------------------------------------------------- kernel event source --
    def _apply_suppression(self):
        """Tell the driver which physical buttons to withhold.

        A bound button is swallowed inside the mouse stack, so no application
        ever sees it. Passthrough works by simply not suppressing it, which is
        better than the hook version: the real click is the genuine article
        rather than a replay of one.
        """
        if self.driver is None:
            return
        with self._lock:
            btns = [b for b in self.bindings if not self.passthrough.get(b)]
        try:
            self.driver.suppress(btns, report_events=True)
        except kdriver.DriverError as e:
            self.last_error = str(e)

    def _pump_events(self):
        """Physical reports straight from the filter, ahead of mouclass."""
        while not self._stopping and self.driver is not None:
            try:
                events = self.driver.read_events()
            except kdriver.DriverError as e:
                self.last_error = str(e)
                return
            if not events:
                return                      # cancelled: we are shutting down
            for ev in events:
                flags = ev["buttons"]
                if not flags:
                    continue
                with self._lock:
                    bound = dict(self.bindings)
                for btn, (down, up) in kdriver.BUTTON_FLAGS.items():
                    macro = bound.get(btn)
                    if macro is None:
                        continue
                    if flags & down:
                        self._trigger(btn, macro)
                    elif flags & up and macro["repeat"] == "hold":
                        self._stop_button(btn)

    # -------------------------------------------------------------- hooks --
    def _on_key(self, code, wparam, lparam):
        if code == 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            kb = C.cast(lparam, C.POINTER(KBDLLHOOKSTRUCT)).contents
            if kb.vkCode == VK_ESCAPE and self._running:
                self.stop_all()          # panic key
        return u32.CallNextHookEx(None, code, wparam, lparam)

    def _which_button(self, wparam, ms):
        for btn, (down, up, xb) in TRIGGERS.items():
            if wparam == down and (xb is None or (ms.mouseData >> 16) == xb):
                return btn, True
            if wparam == up and (xb is None or (ms.mouseData >> 16) == xb):
                return btn, False
        return None, None

    def _on_mouse(self, code, wparam, lparam):
        if code != 0 or wparam == WM_MOUSEMOVE:
            return u32.CallNextHookEx(None, code, wparam, lparam)
        ms = C.cast(lparam, C.POINTER(MSLLHOOKSTRUCT)).contents
        # our own SendInput output must never re-enter the engine
        if ms.flags & LLMHF_INJECTED:
            return u32.CallNextHookEx(None, code, wparam, lparam)

        btn, is_down = self._which_button(wparam, ms)
        if btn is None or btn not in self.bindings:
            return u32.CallNextHookEx(None, code, wparam, lparam)

        macro = self.bindings[btn]
        if is_down:
            self._trigger(btn, macro)
        elif macro["repeat"] == "hold":
            self._stop_button(btn)
        return 1        # swallow the physical click; the macro decides what happens

    # ------------------------------------------------------------ playback --
    def _trigger(self, btn, macro):
        with self._lock:
            running = self._running.get(btn)
            if running and running[0].is_alive():
                if macro["repeat"] == "toggle":
                    running[1].set()
                    return
                return                       # already going; ignore re-press
            stop = threading.Event()
            t = threading.Thread(target=self._run, args=(btn, macro, stop),
                                 daemon=True)
            self._running[btn] = (t, stop)
            t.start()

    def _run(self, btn, macro, stop):
        try:
            if self.passthrough.get(btn) and self.driver is None:
                # SendInput path: the hook ate the click, so replay it first.
                # On the kernel path the button is simply never suppressed.
                down, up, data = _BTN_FLAGS[
                    {1: "left", 2: "right", 3: "middle", 4: "x2", 5: "x1"}[btn]]
                _send(_mouse(down, data=data))
                _send(_mouse(up, data=data))
            play(macro, stop, self.driver)
        finally:
            with self._lock:
                if self._running.get(btn, (None, None))[1] is stop:
                    self._running.pop(btn, None)

    def _stop_button(self, btn):
        with self._lock:
            running = self._running.get(btn)
        if running:
            running[1].set()

    def stop_all(self):
        with self._lock:
            items = list(self._running.values())
        for _, stop in items:
            stop.set()

    # ------------------------------------------------------------- config --
    def bind(self, button, macro, passthrough=False):
        with self._lock:
            self.bindings[int(button)] = M.validate(macro)
            self.passthrough[int(button)] = bool(passthrough)
        self._apply_suppression()

    def unbind(self, button):
        with self._lock:
            self.bindings.pop(int(button), None)
            self.passthrough.pop(int(button), None)
        self._stop_button(int(button))
        self._apply_suppression()

    def status(self):
        st = {
            "active": self.active,
            "error": self.last_error,
            "backend": self.backend,
            "driver": None,
            "driver_note": self.driver_note,
            "bindings": {str(b): {"macro": m["name"], "id": m["id"],
                                  "repeat": m["repeat"],
                                  "passthrough": bool(self.passthrough.get(b))}
                         for b, m in self.bindings.items()},
            "running": [str(b) for b in self._running],
        }
        if self.driver is not None:
            try:
                st["driver"] = self.driver.status()
            except Exception as e:
                st["driver_note"] = str(e)
        return st


ENGINE = Engine()

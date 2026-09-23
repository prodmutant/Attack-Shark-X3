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

**Why the sendinput path can make a game stutter.** A low-level mouse hook is
not a passive observer: every mouse event on the machine is held until the
callback returns, for every application, the foreground game included. When
that callback is Python it must first take the GIL, so the stall is whatever
the interpreter's switch interval is - 5 ms by default, and multiplied by the
number of runnable Python threads. Measured on this machine at a 1000 Hz
report rate: 0.036 ms median with nothing else running, 4.98 ms against one
busy thread, 14.99 ms (120 ms peak) against three. That is the whole defect;
the hook body itself is already trivial.

**The wheel is both an output and a trigger.** A macro can turn it, and a
macro can be bound to it. A notch is not a button: it has no release, so
`hold` repeat means "while it keeps turning" and is ended by a quiet period
rather than by an up event (`_trigger_wheel`). Swallowing a bound notch is
easy on the hook path and needs interface 1.1 on the driver path, because the
direction lives in the sign of a delta rather than in a flag.

Two things keep it down, both applied in `start()`: a much shorter switch
interval so the GIL reaches the hook thread quickly, and not installing the
mouse hook at all when no macro is bound to a button. The kernel backend
avoids the question entirely by never installing one.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import random
import sys
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
try:
    wmm = C.WinDLL("winmm")
except Exception:                       # pragma: no cover - non-Windows
    wmm = None

WH_MOUSE_LL, WH_KEYBOARD_LL = 14, 13
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
WM_MOUSEWHEEL = 0x020A
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
LLMHF_INJECTED = 0x00000001
VK_ESCAPE = 0x1B

#: A hook callback that waits on the GIL holds up every mouse event on the
#: machine. 0.5 ms costs a little throughput in the interpreter and takes the
#: worst case from tens of milliseconds to a fraction of one.
HOOK_SWITCH_INTERVAL = 0.0005
THREAD_PRIORITY_HIGHEST = 2

#: Windows' default timer granularity is 15.6 ms, so a 7 ms delay between two
#: macro steps can become 15.6 ms and the cadence collapses. Games usually
#: raise the resolution themselves, which means a macro tested with one open
#: behaves differently with it closed - ask for 1 ms ourselves so the timing
#: is ours and not a side effect of whatever else is running.
TIMER_RESOLUTION_MS = 1

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

#: The wheel binds like a button, numbered past the physical five. It is not
#: one: a notch is a single event with a direction and no release, so each
#: direction is its own trigger and there is nothing to wait for the end of.
WHEEL_UP, WHEEL_DOWN = 6, 7
WHEEL_TRIGGERS = {WHEEL_UP: "up", WHEEL_DOWN: "down"}
BINDABLE = tuple(sorted(TRIGGERS)) + tuple(sorted(WHEEL_TRIGGERS))
TRIGGER_NAMES = {1: "left", 2: "right", 3: "middle", 4: "forward", 5: "back",
                 WHEEL_UP: "wheel up", WHEEL_DOWN: "wheel down"}

#: `hold` repeat on a wheel trigger means "while it keeps turning", and a
#: wheel never says it has stopped. This is how long a macro keeps running
#: after the last notch - long enough that the gap between two flicks of the
#: same spin does not end it, short enough that it stops when you do.
WHEEL_QUIET_MS = 300


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


#: How far behind schedule a run may fall before catching up is abandoned and
#: the clock is simply restarted. Anything past this and the machine stalled
#: hard enough that firing the backlog as fast as possible would be worse than
#: the gap itself.
RESYNC_S = 0.050


def _hold(stop, due, cap=None):
    """Wait until `due` (a perf_counter stamp). True if the macro was stopped.

    Late wake-ups are not compounded: when the deadline has already passed the
    step runs immediately, and only a stall worse than RESYNC_S abandons the
    original clock instead of trying to make the time back.
    """
    slack = due - time.perf_counter()
    if cap is not None and slack > cap:
        slack = cap
    if slack > 0:
        return stop.wait(slack)
    if slack < -RESYNC_S:
        return stop.wait(0)          # hopeless backlog: caller resyncs below
    return stop.is_set()


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


def play(macro, stop: threading.Event, driver=None, registry=None):
    """Run one macro until it finishes or `stop` is set.

    The macro is compiled to a timeline first, so this loop only ever does one
    thing: wait until an event's moment, then emit it. Overlapping holds and
    simultaneous presses need no special handling - they are simply events
    that share, or straddle, an offset.

    Every offset is measured from one base stamp per pass rather than from the
    previous event, for the reason in the comment below: relative waits
    accumulate their overshoot and walk the whole pattern out of phase.

    With a driver, consecutive mouse events are compiled into a single submit
    and this thread only waits out their duration. A keyboard event ends the
    batch, because a mouse filter cannot type.
    """
    mode, count = macro["repeat"], macro["count"]
    passes = 1 if mode == "once" else (count if mode == "count" else 1 << 30)

    # Sleeping for the length of each delay in turn is not the same as running
    # to a schedule. Every wait returns a little late - a quarter of a
    # millisecond here - and sleeping relative amounts adds those overshoots
    # up: measured at 0.52 ms per cycle on a two-delay loop, which is 126 ms
    # of slip over four seconds, eight whole frames. The pattern slides out of
    # phase with the game's input sampling while every individual delay still
    # looks correct. So keep an absolute deadline and wait until it, letting a
    # late wake-up be absorbed by the next step instead of pushed into it.
    jitter = M.has_jitter(macro)
    events, span = M.compile_timeline(macro, registry)
    steps = M.timeline_steps(events)

    held = set()                         # what is down right now, exactly
    base = time.perf_counter()
    done = 0
    while done < passes and not stop.is_set():
        if jitter and done:                  # re-roll the jitter every pass
            events, span = M.compile_timeline(macro, registry)
            steps = M.timeline_steps(events)

        if driver is not None:
            driver = _run_batched(steps, stop, driver, base, held)
        else:
            for off, ev in events:
                if _hold(stop, base + off / 1000.0):
                    break
                play_step(ev, 1.0, stop)      # speed is already in the offsets
                _track(held, ev)

        base += span / 1000.0
        if time.perf_counter() - base > RESYNC_S:
            base = time.perf_counter()        # fell hopelessly behind; restart
        done += 1

    _release_all(held, driver)


def _run_batched(steps, stop, driver, base, held):
    """Driver path: hand runs of mouse events to the kernel, type the rest."""
    due = base
    i = 0
    while i < len(steps) and not stop.is_set():
        if driver is not None and steps[i]["t"] in _DRIVER_STEPS:
            j = i
            while j < len(steps) and steps[j]["t"] in _DRIVER_STEPS:
                j += 1
            plan, seconds = build_plan(steps[i:j], 1.0)
            if plan:
                try:
                    driver.submit(plan)
                except kdriver.DriverError:
                    driver = None             # fall back for the rest of the run
                    continue
                due += seconds
                if _hold(stop, due, seconds + 0.002):
                    break
            i = j
            continue
        s = steps[i]
        i += 1
        if s["t"] == "delay":
            due += s["ms"] / 1000.0
            if _hold(stop, due):
                break
        else:
            play_step(s, 1.0, stop)
            _track(held, s)
    return driver


def _track(held, step):
    """Remember what is actually down, so only that has to be let go of."""
    if step["t"] == "key":
        key = ("key", step["key"])
    elif step["t"] == "mouse":
        key = ("mouse", step["button"])
    else:
        return
    held.add(key) if step["down"] else held.discard(key)


def _release_all(held, driver=None):
    """Never leave a key or button stuck down if a macro is cut short.

    Only what is still down gets a release. Firing one for every key the macro
    ever touched would work too - a release against an already-released key is
    ignored - but it puts events on the wire that the macro did not ask for,
    and anything watching the stream then has to know to discount them.
    """
    if driver is not None:
        try:
            driver.stop()           # the driver releases what it emitted itself
        except Exception:
            pass
    for kind, name in sorted(held):
        try:
            if kind == "key":
                _send(_key(P.HID_KEYS[name], False))
            elif driver is None:
                _, up, data = _BTN_FLAGS[name]
                _send(_mouse(up, data=data))
        except Exception:
            pass
    held.clear()


# --------------------------------------------------------------- engine ----
class Engine:
    """Owns the hook thread and the running macros."""

    def __init__(self):
        self.bindings = {}          # button number -> macro dict
        self.registry = {}          # macro id -> macro, for `call` steps
        self.passthrough = {}       # button -> let the real click through too
        self._running = {}          # button -> (thread, stop event)
        self._wheel_until = {}      # wheel trigger -> when its spin goes quiet
        self._wheel_watch = {}      # wheel trigger -> the thread watching for it
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
        self.idle_note = None
        self._switch_was = None
        self._timer_raised = False

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

        # Nothing bound means nothing to watch for, and a mouse hook that
        # exists only to call CallNextHookEx still taxes every event in the
        # system. The driver backend has no hook, so it may start regardless.
        if self.driver is None and not self.bindings:
            self.idle_note = ("no macro is bound to a button, so no mouse hook "
                              "is installed")
            return True
        self.idle_note = None

        if self.driver is None and self._switch_was is None:
            self._switch_was = sys.getswitchinterval()
            sys.setswitchinterval(HOOK_SWITCH_INTERVAL)
        if wmm is not None and not self._timer_raised:
            self._timer_raised = wmm.timeBeginPeriod(TIMER_RESOLUTION_MS) == 0

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
        if self._switch_was is not None:
            sys.setswitchinterval(self._switch_was)
            self._switch_was = None
        if self._timer_raised and wmm is not None:
            wmm.timeEndPeriod(TIMER_RESOLUTION_MS)
            self._timer_raised = False
        self._thread = None
        self._reader = None

    def _pump(self):
        """Message pump for the hooks.

        The keyboard hook is always installed - it is the panic key. The mouse
        hook is only needed when there is no driver, because with one the
        triggers come out of the kernel instead.
        """
        self._tid = k32.GetCurrentThreadId()
        # Once this thread has the GIL it should run immediately: it is
        # holding up the whole machine's input while it does.
        try:
            k32.SetThreadPriority(k32.GetCurrentThread(), THREAD_PRIORITY_HIGHEST)
        except Exception:
            pass
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
            bound = [b for b in self.bindings if not self.passthrough.get(b)]
        btns = [b for b in bound if b in TRIGGERS]
        wheel = [WHEEL_TRIGGERS[b] for b in bound if b in WHEEL_TRIGGERS]
        try:
            self.driver.suppress(btns, wheel=wheel, report_events=True)
        except kdriver.DriverError as e:
            self.last_error = str(e)
        if wheel and self.driver is not None:
            # Swallowing a notch needs the field added in interface 1.1. An
            # older filter takes the request, ignores it, and scrolls - so
            # say so rather than letting the page claim the wheel is bound.
            try:
                st = self.driver.status()
            except kdriver.DriverError:
                return
            if not st.get("wheel_suppression"):
                self.driver_note = ("this filter build cannot withhold a "
                                    "wheel notch - the page still scrolls "
                                    "while a wheel macro runs")

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
                if flags & kdriver.WHEEL:
                    # The report has one wheel bit and a signed delta; the
                    # sign is the direction, and the magnitude is how many
                    # notches that one report carried.
                    btn = WHEEL_UP if ev["data"] > 0 else WHEEL_DOWN
                    macro = bound.get(btn)
                    if macro is not None:
                        self._trigger_wheel(btn, macro)
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

        if wparam == WM_MOUSEWHEEL:
            return self._on_wheel(ms, code, wparam, lparam)

        btn, is_down = self._which_button(wparam, ms)
        if btn is None or btn not in self.bindings:
            return u32.CallNextHookEx(None, code, wparam, lparam)

        macro = self.bindings[btn]
        if is_down:
            self._trigger(btn, macro)
        elif macro["repeat"] == "hold":
            self._stop_button(btn)
        return 1        # swallow the physical click; the macro decides what happens

    def _on_wheel(self, ms, code, wparam, lparam):
        """A notch: high word of mouseData is a signed delta, 120 per notch."""
        delta = C.c_short((ms.mouseData >> 16) & 0xFFFF).value
        if not delta:
            return u32.CallNextHookEx(None, code, wparam, lparam)
        btn = WHEEL_UP if delta > 0 else WHEEL_DOWN
        macro = self.bindings.get(btn)
        if macro is None:
            return u32.CallNextHookEx(None, code, wparam, lparam)
        self._trigger_wheel(btn, macro)
        if self.passthrough.get(btn):
            # Passthrough on the wheel is simply not eating the notch. A
            # button has a press and a release to replay in the right order;
            # a notch is one event, and letting the real one carry on up the
            # chain is better than sending a copy of it afterwards.
            return u32.CallNextHookEx(None, code, wparam, lparam)
        return 1

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

    def _trigger_wheel(self, btn, macro):
        """One notch of the wheel, in whichever repeat mode is bound.

        `once`, `count` and `toggle` need nothing special: a notch is a press
        with no release to wait for, so one tick plays one pass - spin faster
        than the macro is long and the extra notches land while it is already
        running, where they are ignored exactly as a re-press would be.

        `hold` is the one that has to be built, because the wheel never says
        it has stopped. The first notch starts the macro and every notch after
        it pushes the deadline out; a watcher stops the macro once the wheel
        has been still for WHEEL_QUIET_MS. So the bind reads the way it sounds:
        one tick, one pass - keep spinning, it keeps going.
        """
        if macro["repeat"] != "hold":
            self._trigger(btn, macro)
            return
        with self._lock:
            self._wheel_until[btn] = time.perf_counter() + WHEEL_QUIET_MS / 1000.0
            spinning = btn in self._wheel_watch
        if spinning:
            return                   # the refreshed deadline is the whole job
        self._trigger(btn, macro)
        watch = threading.Thread(target=self._wheel_quiet, args=(btn,),
                                 daemon=True)
        with self._lock:
            self._wheel_watch[btn] = watch
        watch.start()

    def _wheel_quiet(self, btn):
        """Stop a held wheel macro once the notches stop arriving."""
        try:
            while not self._stopping:
                with self._lock:
                    until = self._wheel_until.get(btn, 0.0)
                slack = until - time.perf_counter()
                if slack <= 0:
                    break
                time.sleep(min(slack, 0.05))
        finally:
            with self._lock:
                self._wheel_watch.pop(btn, None)
            self._stop_button(btn)

    def _run(self, btn, macro, stop):
        try:
            if self.passthrough.get(btn) and self.driver is None:
                # SendInput path: the hook ate the click, so replay it first.
                # On the kernel path the button is simply never suppressed.
                name = {1: "left", 2: "right", 3: "middle",
                        4: "x2", 5: "x1"}.get(btn)
                if name:            # the wheel is let through, never replayed
                    down, up, data = _BTN_FLAGS[name]
                    _send(_mouse(down, data=data))
                    _send(_mouse(up, data=data))
            play(macro, stop, self.driver, self.registry)
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
            "idle_note": self.idle_note,
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

"""Per-application profiles.

A profile names some programs and overrides part of the configuration while
one of them is the foreground window. It never edits the saved configuration:
the dashboard keeps showing and saving the base settings, and what is pushed
to the mouse is `effective(state)` - the base with the active profile laid on
top. Switch away and the base goes back.

Watching the foreground costs nothing while nothing changes. It is a WinEvent
hook (EVENT_SYSTEM_FOREGROUND, out of context), so this thread sleeps in
GetMessage until Windows says the foreground moved.

    state["profiles"] = [{
        "id": "...", "name": "Shooter",
        "exes": ["game.exe"],                  # lower-case image names
        "set": {"dpi_value": 800, "polling_hz": 1000, "lod_mm": 1,
                "motion_sync": false, "ripple": false, "angle_snap": false,
                "engine_on": true},            # any subset
        "bindings": null | {"4": {"id": macro_id, "passthrough": false}}
    }]
    state["profiles_on"] = true
"""
from __future__ import annotations

import copy
import ctypes as C
import os
import threading
import time
import uuid
from ctypes import wintypes as W

SETTABLE = {
    "dpi_value": int, "polling_hz": int, "lod_mm": int,
    "motion_sync": bool, "ripple": bool, "angle_snap": bool, "engine_on": bool,
}


class ProfileError(ValueError):
    pass


def validate(raw, polling_rates=(125, 250, 500, 1000)):
    """A clean profile, or ProfileError saying what is wrong with it."""
    name = str(raw.get("name") or "").strip()[:60]
    if not name:
        raise ProfileError("a profile needs a name")
    exes = []
    for e in raw.get("exes") or []:
        e = os.path.basename(str(e).strip()).lower()
        if e and e not in exes:
            if not e.endswith(".exe"):
                e += ".exe"
            exes.append(e)
    out = {"id": str(raw.get("id") or uuid.uuid4()), "name": name,
           "exes": exes[:32], "set": {}, "bindings": None}
    for k, v in (raw.get("set") or {}).items():
        if k not in SETTABLE or v is None:
            continue
        v = SETTABLE[k](v)
        if k == "dpi_value" and not 50 <= v <= 26000:
            raise ProfileError("DPI must be 50-26000")
        if k == "dpi_value":
            v = int(round(v / 50.0)) * 50
        if k == "polling_hz" and v not in polling_rates:
            raise ProfileError(f"polling rate must be one of {polling_rates}")
        if k == "lod_mm" and v not in (1, 2):
            raise ProfileError("lift-off is 1 or 2 mm")
        out["set"][k] = v
    b = raw.get("bindings")
    if isinstance(b, dict):
        out["bindings"] = {str(int(k)): {"id": str(v.get("id")),
                                         "passthrough": bool(v.get("passthrough"))}
                           for k, v in b.items() if isinstance(v, dict) and v.get("id")}
    return out


def match(state, exe):
    """The profile for this program, if profiles are on and one names it."""
    if not exe or not state.get("profiles_on", True):
        return None
    exe = exe.lower()
    for p in state.get("profiles") or []:
        if exe in (p.get("exes") or []):
            return p
    return None


def effective(state, profile=None):
    """The configuration to push: base state with the profile laid over it."""
    if profile is None:
        profile = WATCHER.active_profile(state)
    if not profile:
        return state
    st = copy.deepcopy(state)
    s = profile.get("set") or {}
    for k, v in s.items():
        if k == "dpi_value":
            stage = st.get("active_stage", 0)
            if 0 <= stage < len(st["dpi"]):
                st["dpi"][stage] = v
        else:
            st[k] = v
    if profile.get("bindings") is not None:
        st["macro_bindings"] = profile["bindings"]
    return st


# --------------------------------------------------------------- watcher ---

u32 = C.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
k32 = C.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WM_QUIT = 0x0012

WINEVENTPROC = C.WINFUNCTYPE(None, W.HANDLE, W.DWORD, W.HWND, W.LONG, W.LONG,
                             W.DWORD, W.DWORD)

if u32 is not None:
    u32.SetWinEventHook.argtypes = [W.DWORD, W.DWORD, W.HMODULE, WINEVENTPROC,
                                    W.DWORD, W.DWORD, W.DWORD]
    u32.SetWinEventHook.restype = W.HANDLE
    u32.UnhookWinEvent.argtypes = [W.HANDLE]
    u32.GetForegroundWindow.restype = W.HWND
    u32.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    u32.GetWindowThreadProcessId.restype = W.DWORD
    u32.GetMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
    u32.GetMessageW.restype = W.BOOL
    u32.DispatchMessageW.argtypes = [C.POINTER(W.MSG)]
    u32.PostThreadMessageW.argtypes = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM]
    u32.IsWindowVisible.argtypes = [W.HWND]
    u32.GetWindowTextLengthW.argtypes = [W.HWND]
    k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    k32.OpenProcess.restype = W.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR,
                                               C.POINTER(W.DWORD)]
    k32.CloseHandle.argtypes = [W.HANDLE]


def exe_of_pid(pid):
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = C.create_unicode_buffer(1024)
        n = W.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, C.byref(n)):
            return buf.value
        return None
    finally:
        k32.CloseHandle(h)


def exe_of_window(hwnd):
    pid = W.DWORD()
    u32.GetWindowThreadProcessId(hwnd, C.byref(pid))
    return exe_of_pid(pid.value) if pid.value else None


def running_programs():
    """Programs with a visible titled window: the list a profile picks from."""
    found = {}
    ENUMPROC = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    def cb(hwnd, _l):
        if u32.IsWindowVisible(hwnd) and u32.GetWindowTextLengthW(hwnd) > 0:
            path = exe_of_window(hwnd)
            if path:
                found.setdefault(os.path.basename(path).lower(), path)
        return True

    u32.EnumWindows(ENUMPROC(cb), 0)
    me = os.path.basename(exe_of_pid(os.getpid()) or "").lower()
    skip = {"explorer.exe", "applicationframehost.exe", "textinputhost.exe",
            "shellexperiencehost.exe", "searchapp.exe", "systemsettings.exe", me}
    return sorted(({"exe": e, "path": p} for e, p in found.items() if e not in skip),
                  key=lambda d: d["exe"])


class Watcher:
    """Follows the foreground program and calls `on_change(profile)` when the
    profile that applies to it changes. Only a change calls back, so
    alt-tabbing between two unprofiled programs sends nothing to the mouse."""

    def __init__(self):
        self.exe = None
        self.active_id = None
        self.on_change = None
        self.load_state = None          # () -> state dict
        self.error = None
        self._tid = None
        self._thread = None
        self._proc = WINEVENTPROC(self._on_event)
        self._pending = None
        self._lock = threading.Lock()

    def active_profile(self, state):
        if not self.active_id:
            return None
        for p in state.get("profiles") or []:
            if p.get("id") == self.active_id:
                return p
        return None

    def start(self):
        if u32 is None or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="profile-watch")
        self._thread.start()

    def recheck(self):
        """Re-evaluate the current foreground - after profiles were edited."""
        if u32 is None:
            return
        self._evaluate(u32.GetForegroundWindow(), force=True)
        self._arm_poll()

    def _on_event(self, _hook, _event, hwnd, _obj, _child, _tid, _time):
        # Several foreground events can land within a few ms (a launcher, a
        # splash window). Settle for 150 ms and evaluate once.
        with self._lock:
            self._pending = hwnd
            if getattr(self, "_timer", None):
                self._timer.cancel()
            self._timer = threading.Timer(0.15, self._settled)
            self._timer.daemon = True
            self._timer.start()

    def _settled(self):
        with self._lock:
            hwnd, self._pending = self._pending, None
        # what is in front NOW, not what the event named: the event can be
        # for a window that has already gone again
        self._evaluate(u32.GetForegroundWindow() or hwnd)
        self._arm_poll()

    def _arm_poll(self):
        """While a profile is active, look again every 1.5 s.

        Windows does not always raise a foreground event when the profiled
        program closes (focus can fall to nothing, or to the desktop), and a
        profile left applied after its game is gone is the one failure that
        would be noticed. Nothing polls while no profile is active.
        """
        if not self.active_id or getattr(self, "_poll", None):
            return

        def again():
            self._poll = None
            if not self.active_id:
                return
            hwnd = u32.GetForegroundWindow()
            # no foreground at all is also what an alt-tab looks like for an
            # instant; only believe it when it is still true on the next look
            self._nulls = 0 if hwnd else getattr(self, "_nulls", 0) + 1
            if hwnd or self._nulls >= 2:
                self._evaluate(hwnd)
            self._arm_poll()
        self._poll = threading.Timer(1.5, again)
        self._poll.daemon = True
        self._poll.start()

    def _evaluate(self, hwnd, force=False):
        path = exe_of_window(hwnd) if hwnd else None
        name = os.path.basename(path).lower() if path else None
        # Our own tray window coming forward (its menu) is not leaving the
        # game. Anything else - including no window at all, which is what is
        # left after the game closes - is judged like any other program.
        if name and name == os.path.basename(exe_of_pid(os.getpid()) or "").lower():
            if not force:
                return
        else:
            self.exe = name
        state = self.load_state() if self.load_state else {}
        prof = match(state, self.exe)
        new_id = prof["id"] if prof else None
        # a forced look (profiles edited) still sends nothing when nothing was
        # and nothing is active: the base is already on the mouse
        if new_id == self.active_id and (not force or new_id is None):
            return
        self.active_id = new_id
        if self.on_change:
            try:
                self.on_change(prof)
                self.error = None
            except Exception as exc:          # mouse unplugged, etc.
                self.error = str(exc)

    def _run(self):
        self._tid = k32.GetCurrentThreadId()
        hook = u32.SetWinEventHook(EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
                                   None, self._proc, 0, 0, WINEVENT_OUTOFCONTEXT)
        if not hook:
            self.error = f"SetWinEventHook failed ({C.get_last_error()})"
            return
        time.sleep(0.2)
        self._evaluate(u32.GetForegroundWindow())
        self._arm_poll()
        msg = W.MSG()
        try:
            while u32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
                u32.DispatchMessageW(C.byref(msg))
        finally:
            u32.UnhookWinEvent(hook)


WATCHER = Watcher()

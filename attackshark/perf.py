"""Measure what the mouse actually does, and what Windows does to it.

Three measurements and one audit, all behind the Tools page:

* **Report rate.** Every Raw Input report from the X3 is timestamped on
  arrival. The gaps between them say what rate the link really runs at, how
  steady it is, and whether reports go missing on the way from the mouse.
* **Clicks.** Press and release edges per button. A release followed by a
  press a few milliseconds later is not a finger, it is a switch bouncing past
  the debounce - which is what the key response setting is for.
* **DPI.** Counts over a measured distance. The sensor's real resolution is
  rarely exactly the number on the box.
* **Windows.** Pointer acceleration, pointer speed and USB selective suspend,
  the three host settings that quietly undo a good mouse.

What the timestamps can and cannot tell you. They are taken in this process
when Windows hands over the WM_INPUT message, not when the mouse sent the
report. A gap in the stream while the mouse moves fast is a real loss; a burst
of reports arriving microseconds apart is this machine delivering late, not
the mouse sending early. The stats keep those two apart instead of blending
them into one jitter figure.

Nothing here writes to the mouse.
"""
from __future__ import annotations

import ctypes as C
import json
import math
import os
import re
import statistics
import subprocess
import sys
import threading
import time
from ctypes import wintypes as W

from . import protocol as P

NOMINAL_RATES = (125, 250, 500, 1000)

# A reports gap longer than this is the hand stopping, not the link dropping.
IDLE_GAP_MS = 25.0
# Counts per report at which a missing report cannot be explained by the
# sensor simply having nothing to say: at 3+ counts a ms the mouse is moving.
FAST_COUNTS = 3
# Release-to-press faster than this is a bouncing switch, not a finger.
CHATTER_MS = 15.0

SAMPLER_SWITCH_INTERVAL = 0.0005
MAX_RUN_S = 180.0
ABANDON_S = 15.0              # no one asked for results this long: stop

# ------------------------------------------------------------------ stats ---


def _pct(sorted_vals, q):
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def _nearest_rate(hz):
    return min(NOMINAL_RATES, key=lambda r: abs(math.log(r / hz))) if hz else None


def polling_stats(recs, configured_hz):
    """Report-rate figures from [(t_s, dx, dy, flags, wheel), ...].

    Only motion reports count - a mouse sitting still sends nothing, so idle
    time says nothing about the link. The stream is split into bursts at every
    pause longer than IDLE_GAP_MS and each burst is measured on its own.
    """
    expected = 1000.0 / configured_hz
    idle = max(IDLE_GAP_MS, 5 * expected)
    moves = [(r[0], abs(r[1]) + abs(r[2])) for r in recs if r[1] or r[2]]

    bursts, cur = [], []
    for t, mag in moves:
        if cur and (t - cur[-1][0]) * 1000.0 > idle:
            bursts.append(cur)
            cur = []
        cur.append((t, mag))
    if cur:
        bursts.append(cur)

    ivals = []
    fast_span, fast_lost, fast_pairs, bunched = 0.0, 0, 0, 0
    for b in bursts:
        if len(b) < 20:
            continue
        bi = [(b[k + 1][0] - b[k][0]) * 1000.0 for k in range(len(b) - 1)]
        for k, iv in enumerate(bi):
            ivals.append(iv)
            if iv < 0.25 * expected:
                bunched += 1
            if b[k][1] >= FAST_COUNTS and b[k + 1][1] >= FAST_COUNTS:
                fast_span += iv
                fast_pairs += 1
                if iv > 1.5 * expected:
                    # A late report followed by ones that arrive early was
                    # delayed, not lost: the short gaps after it pay the long
                    # one back. Only what is left unpaid went missing.
                    paid, n = iv, 0
                    for nxt in bi[k + 1:k + 9]:
                        if nxt >= 0.5 * expected:
                            break
                        paid += nxt
                        n += 1
                    fast_lost += max(0, int(round(paid / expected)) - 1 - n)

    out = {"configured_hz": configured_hz, "expected_ms": expected,
           "reports": len(moves), "intervals": len(ivals),
           "enough": len(ivals) >= 200}
    if not ivals:
        return out

    s = sorted(ivals)
    # Slow movement leaves real gaps - the sensor had nothing to report - so
    # the rate comes from fast movement, where every slot should carry one.
    # Lost reports stay in it: a link that drops them is a slower link.
    if fast_pairs >= 200:
        hz = 1000.0 * fast_pairs / fast_span
    else:
        hz = 1000.0 / statistics.median(s)
    fast_expected = fast_span / expected if fast_span else 0
    out.update({
        "measured_hz": round(hz, 1),
        "looks_like_hz": _nearest_rate(hz),
        "median_ms": round(statistics.median(s), 3),
        "mean_ms": round(statistics.fmean(s), 3),
        "stdev_ms": round(statistics.pstdev(s), 3),
        "p01_ms": round(_pct(s, 0.01), 3),
        "p99_ms": round(_pct(s, 0.99), 3),
        "max_ms": round(s[-1], 3),
        "bunched": bunched,
        "bunched_pct": round(100.0 * bunched / len(s), 2),
        "fast_expected": int(fast_expected),
        "lost": fast_lost,
        "lost_pct": round(100.0 * fast_lost / fast_expected, 3) if fast_expected else None,
        "hist": _histogram(s, expected),
        "timeline": _timeline(bursts),
    })
    out["verdict"] = _poll_verdict(out)
    return out


def live_rate(recs, window=1.0):
    """Reports per second over the last `window` of motion, cheaply."""
    if not recs:
        return {"hz": None}
    lo, hi, end = 0, len(recs), recs[-1][0] - window
    while lo < hi:                               # bisect on the timestamp
        mid = (lo + hi) // 2
        if recs[mid][0] < end:
            lo = mid + 1
        else:
            hi = mid
    tail = [r[0] for r in recs[lo:] if r[1] or r[2]]
    if len(tail) < 20 or tail[-1] - tail[0] < 0.5 * window:
        return {"hz": None}
    gaps = [b - a for a, b in zip(tail, tail[1:])]
    if max(gaps) * 1000.0 > IDLE_GAP_MS:
        return {"hz": None}                      # the hand paused in there
    return {"hz": round((len(tail) - 1) / (tail[-1] - tail[0]), 1)}


def _histogram(sorted_ivals, expected, bins=30):
    """Interval counts from 0 to 3x the expected gap; the last bin is overflow."""
    width = 3.0 * expected / bins
    counts = [0] * (bins + 1)
    for v in sorted_ivals:
        counts[min(bins, int(v / width))] += 1
    return {"width_ms": width, "counts": counts}


def _timeline(bursts, window=0.25):
    """Rate per quarter second, only where the mouse moved the whole window.

    A window the hand stopped halfway through would read as half the rate,
    which is a picture of the hand, not the link.
    """
    if not bursts:
        return []
    t0 = bursts[0][0][0]
    pts = []
    for b in bursts:
        if len(b) < 20:
            continue
        i = 0
        while i < len(b):
            start = b[i][0]
            j = i
            while j + 1 < len(b) and b[j + 1][0] - start < window:
                j += 1
            n, dur = j - i, b[j][0] - start
            if n >= 10 and dur >= 0.8 * window:
                pts.append([round(start - t0, 3), round(n / dur, 1)])
            i = j + 1
    return pts


def _poll_verdict(r):
    notes = []
    if not r["enough"]:
        return ["Not enough movement yet. Move the mouse quickly in circles."]
    cfg, seen = r["configured_hz"], r["looks_like_hz"]
    if seen != cfg:
        notes.append(f"The mouse is running at about {seen} Hz, not the "
                     f"{cfg} Hz it is set to. Press 'Push everything' on the "
                     "dashboard, then measure again.")
    else:
        notes.append(f"Running at the configured {cfg} Hz.")
    if r["lost_pct"] is not None and r["fast_expected"] >= 200:
        if r["lost_pct"] >= 1.0:
            notes.append(f"{r['lost_pct']:.1f}% of reports went missing during "
                         "fast movement. On 2.4 GHz that is radio loss: put the "
                         "receiver on a front port or an extension cable within "
                         "30 cm of the mouse, away from USB 3 ports and Wi-Fi.")
        elif r["lost"]:
            notes.append(f"{r['lost']} report(s) lost during fast movement "
                         f"({r['lost_pct']:.2f}%). Minor.")
        else:
            notes.append("No reports lost during fast movement.")
    if r["bunched_pct"] >= 5:
        notes.append(f"{r['bunched_pct']:.0f}% of reports arrived in bunches. "
                     "That is this PC delivering input late (CPU load, power "
                     "saving, a busy USB controller), not the mouse.")
    return notes


BUTTON_BITS = {1: 0x0001, 2: 0x0004, 3: 0x0010, 4: 0x0040, 5: 0x0100}


def click_stats(recs, key_response_ms):
    """Per-button hold times and release-to-press gaps, with chatter flagged."""
    per = {}
    for t, _dx, _dy, flags, _w in recs:
        if not flags:
            continue
        for btn, down in BUTTON_BITS.items():
            if flags & down:
                per.setdefault(btn, []).append((t, True))
            if flags & (down << 1):
                per.setdefault(btn, []).append((t, False))

    buttons, worst = [], 0.0
    for btn in sorted(per):
        ev = per[btn]
        holds, gaps, chatter = [], [], []
        last_down = last_up = None
        for t, down in ev:
            if down:
                if last_up is not None:
                    g = (t - last_up) * 1000.0
                    gaps.append(g)
                    if g < CHATTER_MS:
                        chatter.append(round(g, 2))
                last_down = t
            elif last_down is not None:
                holds.append((t - last_down) * 1000.0)
                last_up = t
                last_down = None
        if chatter:
            worst = max(worst, max(chatter))
        buttons.append({
            "button": btn,
            "presses": sum(1 for _t, d in ev if d),
            "hold_min_ms": round(min(holds), 1) if holds else None,
            "hold_median_ms": round(statistics.median(holds), 1) if holds else None,
            "gap_min_ms": round(min(gaps), 1) if gaps else None,
            "chatter": chatter,
        })

    advice = None
    if worst:
        want = min(50, int(math.ceil((worst + 2) / 2.0)) * 2)
        if want > key_response_ms:
            advice = {"key_response_ms": want,
                      "why": f"A switch re-triggered {worst:.1f} ms after "
                             f"releasing. Key response {want} ms filters that out."}
        else:
            advice = {"key_response_ms": None,
                      "why": "Chatter got through even at this key response. "
                             "The switch itself is failing."}
    return {"buttons": buttons, "chatter_ms": CHATTER_MS,
            "key_response_ms": key_response_ms, "advice": advice}


def dpi_stats(recs, distance_mm, configured_dpi):
    """Real counts per inch over a measured stroke, plus how straight it was."""
    sx = sum(r[1] for r in recs)
    sy = sum(r[2] for r in recs)
    path = sum(math.hypot(r[1], r[2]) for r in recs)
    major, minor = (sx, sy) if abs(sx) >= abs(sy) else (sy, sx)
    out = {"counts_x": sx, "counts_y": sy, "path": round(path),
           "distance_mm": distance_mm, "configured_dpi": configured_dpi}
    if not major or not distance_mm:
        return out
    cpi = abs(major) / (distance_mm / 25.4)
    out.update({
        "axis": "x" if abs(sx) >= abs(sy) else "y",
        "measured_cpi": round(cpi),
        "error_pct": round(100.0 * (cpi - configured_dpi) / configured_dpi, 1)
        if configured_dpi else None,
        "drift_deg": round(math.degrees(math.atan2(abs(minor), abs(major))), 2),
    })
    return out


def speed_stats(recs, dpi, window=8):
    """Peak hand speed, and the moments the sensor stopped keeping up.

    Speed comes from a sliding window of `window` reports rather than one:
    a single report's gap is too noisy (a late delivery halves it and doubles
    the speed). A malfunction shows up as one of three shapes that a real
    hand cannot make:

    * **dropout** - reports stop dead while moving fast. A hand decelerates
      through low speed first; it does not go from 2 m/s to nothing in 25 ms.
    * **stall** - reports keep coming but carry almost no counts, three in a
      row, straight after high speed.
    * **reversal** - a big report pointing backwards against the stroke.
    """
    moves = [r for r in recs if r[1] or r[2]]
    out = {"dpi": dpi, "reports": len(moves), "events": []}
    if not dpi or len(moves) < window + 2:
        return out
    m_per_count = 0.0254 / dpi
    mags = [math.hypot(r[1], r[2]) for r in moves]
    peak, peak_t, trace = 0.0, 0.0, []
    t0 = moves[0][0]
    speeds = [0.0] * len(moves)
    run = sum(mags[1:window + 1])
    for i in range(window, len(moves)):
        if i > window:
            run += mags[i] - mags[i - window]
        dt = moves[i][0] - moves[i - window][0]
        if dt <= 0 or dt * 1000.0 > IDLE_GAP_MS * 2:
            continue
        v = run * m_per_count / dt
        speeds[i] = v
        if v > peak:
            peak, peak_t = v, moves[i][0] - t0
    step = max(1, len(moves) // 600)            # a chart needs ~600 points
    for i in range(window, len(moves), step):
        trace.append([round(moves[i][0] - t0, 3), round(speeds[i], 3)])

    events = []
    for i in range(window, len(moves) - 3):
        v = speeds[i]
        if v < 1.5:
            continue
        gap = (moves[i + 1][0] - moves[i][0]) * 1000.0
        kind = None
        if gap > IDLE_GAP_MS:
            kind = "dropout"
        else:
            dt = moves[i + 1][0] - moves[i][0]
            want = v * dt / m_per_count          # counts this report should carry
            if want > 10 and all(mags[i + k] < 0.05 * want for k in (1, 2, 3)):
                kind = "stall"
            else:
                vx = sum(moves[k][1] for k in range(i - window + 1, i + 1))
                vy = sum(moves[k][2] for k in range(i - window + 1, i + 1))
                dx, dy = moves[i + 1][1], moves[i + 1][2]
                if vx * dx + vy * dy < 0 and mags[i + 1] > 0.5 * (mags[i] or 1):
                    kind = "reversal"
        if kind and (not events or moves[i][0] - t0 - events[-1]["t"] > 0.05):
            events.append({"t": round(moves[i][0] - t0, 3), "kind": kind,
                           "speed": round(v, 2)})
    out.update({"peak_ms": round(peak, 2), "peak_ips": round(peak / 0.0254, 1),
                "peak_at": round(peak_t, 3), "events": events[:50], "trace": trace})
    return out


def wheel_stats(recs, expected=None):
    """Notches per direction, and notches that went the wrong way.

    A worn or dirty wheel encoder misreads a notch as one in the opposite
    direction. The tell is a lone notch against the direction of the notches
    either side of it within half a second - a finger does not reverse and
    reverse back that fast. With `expected` (notches counted by the user),
    missing or extra notches show as the difference.
    """
    ev = [(r[0], 1 if r[4] > 0 else -1, abs(r[4]) // 120 or 1) for r in recs if r[4]]
    up = sum(n for _t, d, n in ev if d > 0)
    down = sum(n for _t, d, n in ev if d < 0)
    wrong = []
    for i in range(1, len(ev) - 1):
        t, d, _n = ev[i]
        before, after = ev[i - 1], ev[i + 1]
        if before[1] == after[1] != d and t - before[0] < 0.5 and after[0] - t < 0.5:
            wrong.append(round(t - ev[0][0], 3))
    out = {"up": up, "down": down, "events": len(ev), "wrong_way": wrong,
           "expected": expected}
    if expected:
        main = max(up, down)
        out["difference"] = main - expected
    return out


def lod_stats(recs, height_mm):
    """Does it still track at this height? Counts say more than reports."""
    moves = [r for r in recs if r[1] or r[2]]
    counts = sum(abs(r[1]) + abs(r[2]) for r in moves)
    if counts >= 300:
        verdict = "tracks"
    elif counts > 0:
        verdict = "partial"
    else:
        verdict = "stopped"
    return {"height_mm": height_mm, "reports": len(moves), "counts": counts,
            "verdict": verdict}


def still_stats(recs, seconds):
    """Reports from a mouse nobody is touching: sensor noise."""
    moves = [r for r in recs if r[1] or r[2]]
    counts = sum(abs(r[1]) + abs(r[2]) for r in moves)
    biggest = max((abs(r[1]) + abs(r[2]) for r in moves), default=0)
    return {"seconds": seconds, "reports": len(moves), "counts": counts,
            "biggest": biggest,
            "per_min": round(len(moves) * 60.0 / seconds, 1) if seconds else None}


def path_stats(recs, max_points=4000):
    """The raw path, plus how often one axis was exactly zero while moving.

    Angle snapping shows up as the minor axis flattened to exactly 0 on
    strokes that were not perfectly straight. Compare the figure with angle
    snap on and off: the same hand gives a far higher share with it on.
    """
    moves = [r for r in recs if r[1] or r[2]]
    x = y = 0
    pts = []
    step = max(1, len(moves) // max_points)
    fast = flat = 0
    for i, r in enumerate(moves):
        x += r[1]
        y += r[2]
        if i % step == 0:
            pts.append([x, y])
        big, small = max(abs(r[1]), abs(r[2])), min(abs(r[1]), abs(r[2]))
        if big >= 4:
            fast += 1
            if small == 0:
                flat += 1
    if moves and pts[-1] != [x, y]:
        pts.append([x, y])
    return {"points": pts, "reports": len(moves), "step": step,
            "fast": fast, "flat_pct": round(100.0 * flat / fast, 1) if fast else None}


# ---------------------------------------------------------------- sampler ---

u32 = C.WinDLL("user32", use_last_error=True) if os.name == "nt" else None
k32 = C.WinDLL("kernel32", use_last_error=True) if os.name == "nt" else None

WM_INPUT = 0x00FF
WM_QUIT = 0x0012
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIDEV_INPUTSINK = 0x00000100
RIDEV_REMOVE = 0x00000001
RIM_TYPEMOUSE = 0
THREAD_PRIORITY_TIME_CRITICAL = 15


class RAWINPUTDEVICE(C.Structure):
    _fields_ = [("usUsagePage", C.c_ushort), ("usUsage", C.c_ushort),
                ("dwFlags", W.DWORD), ("hwndTarget", W.HWND)]


class RAWINPUTHEADER(C.Structure):
    _fields_ = [("dwType", W.DWORD), ("dwSize", W.DWORD),
                ("hDevice", W.HANDLE), ("wParam", W.WPARAM)]


class _BTN(C.Structure):
    _fields_ = [("usButtonFlags", C.c_ushort), ("usButtonData", C.c_ushort)]


class _BU(C.Union):
    _fields_ = [("ulButtons", W.DWORD), ("b", _BTN)]


class RAWMOUSE(C.Structure):
    _fields_ = [("usFlags", C.c_ushort), ("u", _BU), ("ulRawButtons", W.DWORD),
                ("lLastX", C.c_long), ("lLastY", C.c_long),
                ("ulExtraInformation", W.DWORD)]


class RAWINPUT(C.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


class WNDCLASS(C.Structure):
    _fields_ = [("style", W.UINT), ("lpfnWndProc", C.c_void_p),
                ("cbClsExtra", C.c_int), ("cbWndExtra", C.c_int),
                ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH),
                ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]


WNDPROC = C.WINFUNCTYPE(C.c_ssize_t, W.HWND, C.c_uint, W.WPARAM, W.LPARAM)

if u32 is not None:
    u32.GetRawInputData.argtypes = [W.HANDLE, W.UINT, C.c_void_p,
                                    C.POINTER(W.UINT), W.UINT]
    u32.GetRawInputData.restype = W.UINT
    u32.GetRawInputDeviceInfoW.argtypes = [W.HANDLE, W.UINT, C.c_void_p,
                                           C.POINTER(W.UINT)]
    u32.GetRawInputDeviceInfoW.restype = W.UINT
    u32.RegisterRawInputDevices.argtypes = [C.POINTER(RAWINPUTDEVICE), W.UINT, W.UINT]
    u32.RegisterRawInputDevices.restype = W.BOOL
    u32.DefWindowProcW.argtypes = [W.HWND, C.c_uint, W.WPARAM, W.LPARAM]
    u32.DefWindowProcW.restype = C.c_ssize_t
    u32.RegisterClassW.argtypes = [C.POINTER(WNDCLASS)]
    u32.RegisterClassW.restype = W.ATOM
    u32.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
                                    C.c_int, C.c_int, C.c_int, C.c_int,
                                    W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
    u32.CreateWindowExW.restype = W.HWND
    u32.DestroyWindow.argtypes = [W.HWND]
    u32.GetMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
    u32.GetMessageW.restype = W.BOOL
    u32.DispatchMessageW.argtypes = [C.POINTER(W.MSG)]
    u32.DispatchMessageW.restype = C.c_ssize_t
    u32.PostThreadMessageW.argtypes = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM]
    k32.GetModuleHandleW.argtypes = [W.LPCWSTR]
    k32.GetModuleHandleW.restype = W.HMODULE
    k32.GetCurrentThread.restype = W.HANDLE
    k32.SetThreadPriority.argtypes = [W.HANDLE, C.c_int]
    u32.SystemParametersInfoW.argtypes = [W.UINT, W.UINT, C.c_void_p, W.UINT]
    u32.SystemParametersInfoW.restype = W.BOOL

_X3_IDS = tuple(f"VID_{P.VENDOR_ID:04X}&PID_{pid:04X}" for pid in P.PRODUCT_IDS)


def _device_name(h):
    size = W.UINT(0)
    u32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, None, C.byref(size))
    if not size.value:
        return ""
    buf = C.create_unicode_buffer(size.value + 1)
    u32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, buf, C.byref(size))
    return buf.value


class Sampler:
    """Raw Input from the X3 only, on a message-only window of its own.

    A process gets one Raw Input registration per usage, so this lives only
    while a test runs and removes itself afterwards.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.recs = []
        self.other = 0
        self.mode = None
        self.params = {}
        self.error = None
        self.started = self.last_read = 0.0
        self.ended = None
        self._thread = None
        self._tid = None
        self._ready = threading.Event()
        self._is_x3 = {}
        self._wp = WNDPROC(self._on_msg)
        self._buf = C.create_string_buffer(C.sizeof(RAWINPUT) + 16)
        self._size = W.UINT(0)
        self._switch_was = None
        self.on_stop = None           # called once when this run ends
        self.saved = False

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, mode, params=None):
        self.stop()
        with self.lock:
            self.recs = []
            self.other = 0
            self.mode = mode
            self.params = dict(params or {})
            self.saved = False
            self.error = None
            self.started = self.last_read = time.perf_counter()
            self.ended = None
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="perf-sampler")
        self._thread.start()
        self._ready.wait(3)
        threading.Thread(target=self._watchdog, daemon=True).start()
        if self.error:
            raise OSError(self.error)

    def stop(self):
        t = self._thread
        if t is None:
            return
        if self._tid:
            u32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        t.join(2)
        self._thread = None
        cb, self.on_stop = self.on_stop, None
        if cb:
            cb()

    def snapshot(self):
        with self.lock:
            self.last_read = time.perf_counter()
            return list(self.recs), self.other

    def _watchdog(self):
        me = self._thread
        limit = float(self.params.get("duration_s") or MAX_RUN_S)
        while self._thread is me and self.running:
            time.sleep(0.25 if limit < MAX_RUN_S else 1.0)
            now = time.perf_counter()
            if now - self.started > limit or now - self.last_read > ABANDON_S:
                self.stop()

    def _on_msg(self, hwnd, msg, wparam, lparam):
        if msg != WM_INPUT:
            return u32.DefWindowProcW(hwnd, msg, wparam, lparam)
        t = time.perf_counter()                 # first thing: the timestamp
        self._size.value = len(self._buf)
        got = u32.GetRawInputData(lparam, RID_INPUT, self._buf, C.byref(self._size),
                                  C.sizeof(RAWINPUTHEADER))
        if got and got != 0xFFFFFFFF:
            ri = RAWINPUT.from_buffer(self._buf)
            if ri.header.dwType == RIM_TYPEMOUSE:
                h = ri.header.hDevice or 0
                x3 = self._is_x3.get(h)
                if x3 is None:
                    name = _device_name(h).upper() if h else ""
                    x3 = self._is_x3[h] = any(i in name for i in _X3_IDS)
                m = ri.mouse
                with self.lock:
                    if x3:
                        wheel = C.c_short(m.u.b.usButtonData).value \
                            if m.u.b.usButtonFlags & 0x0400 else 0
                        self.recs.append((t, m.lLastX, m.lLastY,
                                          m.u.b.usButtonFlags, wheel))
                    else:
                        self.other += 1
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self):
        self._tid = k32.GetCurrentThreadId()
        k32.SetThreadPriority(k32.GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL)
        hinst = k32.GetModuleHandleW(None)
        cls = WNDCLASS()
        cls.lpfnWndProc = C.cast(self._wp, C.c_void_p)
        cls.hInstance = hinst
        cls.lpszClassName = "AsxPerfSink"
        u32.RegisterClassW(C.byref(cls))        # already registered is fine
        hwnd = u32.CreateWindowExW(0, "AsxPerfSink", "AsxPerfSink", 0, 0, 0, 0, 0,
                                   W.HWND(-3), None, hinst, None)
        if not hwnd:
            self.error = f"CreateWindowEx failed ({C.get_last_error()})"
            self._ready.set()
            return
        rid = RAWINPUTDEVICE(1, 2, RIDEV_INPUTSINK, hwnd)
        if not u32.RegisterRawInputDevices(C.byref(rid), 1, C.sizeof(rid)):
            self.error = f"RegisterRawInputDevices failed ({C.get_last_error()})"
            u32.DestroyWindow(hwnd)
            self._ready.set()
            return
        # A report waits for this thread to get the GIL before it is stamped.
        self._switch_was = sys.getswitchinterval()
        sys.setswitchinterval(min(self._switch_was, SAMPLER_SWITCH_INTERVAL))
        self._ready.set()
        msg = W.MSG()
        try:
            while u32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
                u32.DispatchMessageW(C.byref(msg))
        finally:
            off = RAWINPUTDEVICE(1, 2, RIDEV_REMOVE, None)
            u32.RegisterRawInputDevices(C.byref(off), 1, C.sizeof(off))
            u32.DestroyWindow(hwnd)
            self._restore_switch()
            self._tid = None
            self.ended = time.perf_counter()

    def _restore_switch(self):
        """Give the interval back - unless the macro hook now needs it low."""
        from .hostrun import ENGINE
        if self._switch_was is None:
            return
        if getattr(ENGINE, "_switch_was", None) is not None:
            ENGINE._switch_was = self._switch_was     # it restores on its stop
        else:
            sys.setswitchinterval(self._switch_was)
        self._switch_was = None


SAMPLER = Sampler() if os.name == "nt" else None


MODES = ("poll", "click", "dpi", "speed", "lod", "still", "path", "link", "wheel")
HISTORY_KEEP = 30


def _history_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "attackshark.perf.json")


def history():
    try:
        with open(_history_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def history_delete(index=None):
    items = history()
    items = [] if index is None else [h for i, h in enumerate(items) if i != int(index)]
    _history_write(items)
    return items


def _history_write(items):
    try:
        with open(_history_path(), "w", encoding="utf-8") as fh:
            json.dump(items[-HISTORY_KEEP:], fh, indent=1)
    except OSError:
        pass


def _remember(poll, link):
    """Keep a finished rate run, so the cable and the receiver can be compared."""
    if not poll.get("enough"):
        return
    items = history()
    items.append({"when": time.strftime("%Y-%m-%d %H:%M"), "link": link or "unknown",
                  "configured_hz": poll["configured_hz"],
                  **{k: poll.get(k) for k in ("measured_hz", "median_ms", "stdev_ms",
                                              "p99_ms", "lost_pct", "bunched_pct")}})
    _history_write(items)


def results(state, link=None):
    """Whatever the running (or last) test has measured so far."""
    if SAMPLER is None:
        return {"mode": None, "running": False}
    recs, other = SAMPLER.snapshot()
    mode, params = SAMPLER.mode, SAMPLER.params
    out = {"mode": mode, "running": SAMPLER.running, "x3_reports": len(recs),
           "other_reports": other,
           "elapsed_s": round((SAMPLER.ended or time.perf_counter()) - SAMPLER.started, 1)
           if SAMPLER.started else 0}
    if mode == "poll" and SAMPLER.running:
        # While it runs, only the last second: a full pass over a minute of
        # reports holds the GIL long enough to delay the stamps it measures.
        out["live"] = live_rate(recs)
    elif mode == "poll":
        out["poll"] = polling_stats(recs, state.get("polling_hz", 1000))
        if not SAMPLER.saved:
            SAMPLER.saved = True
            _remember(out["poll"], link)
    elif mode == "link":
        # the last two seconds, over and over: a meter, not a report
        tail_from = recs[-1][0] - 2.0 if recs else 0
        tail = [r for r in recs[-4000:] if r[0] >= tail_from]
        lp = polling_stats(tail, state.get("polling_hz", 1000))
        out["link"] = {k: lp.get(k) for k in ("measured_hz", "lost", "lost_pct",
                                              "fast_expected", "bunched_pct", "enough")}
    elif mode in ("speed", "lod", "still", "path"):
        stage = state.get("active_stage", 0)
        dpis = state.get("dpi", [])
        dpi = dpis[stage] if 0 <= stage < len(dpis) else None
        if mode == "speed":
            out["speed"] = speed_stats(recs, dpi)
        elif mode == "lod":
            out["lod"] = lod_stats(recs, params.get("height_mm"))
        elif mode == "still":
            out["still"] = still_stats(recs, out["elapsed_s"])
        else:
            out["path"] = path_stats(recs)
    elif mode == "click":
        out["click"] = click_stats(recs, state.get("key_response_ms", 2))
    elif mode == "wheel":
        out["wheel"] = wheel_stats(recs, params.get("expected"))
    elif mode == "dpi":
        stage = state.get("active_stage", 0)
        dpis = state.get("dpi", [])
        dpi = dpis[stage] if 0 <= stage < len(dpis) else None
        out["dpi"] = dpi_stats(recs, float(params.get("distance_mm") or 0), dpi)
    return out


# ---------------------------------------------------------------- windows ---

SPI_GETMOUSE, SPI_SETMOUSE = 0x0003, 0x0004
SPI_GETMOUSESPEED, SPI_SETMOUSESPEED = 0x0070, 0x0071
SPIF_UPDATEINIFILE, SPIF_SENDCHANGE = 0x01, 0x02
USB_SUB = "2a737441-1930-4402-8d77-b2bebba308a3"
USB_SUSPEND = "48e6b7a6-50f5-4782-a5d4-53bb8f07e226"
CREATE_NO_WINDOW = 0x08000000


def _spi_mouse():
    arr = (C.c_int * 3)()
    u32.SystemParametersInfoW(SPI_GETMOUSE, 0, arr, 0)
    return list(arr)


def _spi_speed():
    v = C.c_int()
    u32.SystemParametersInfoW(SPI_GETMOUSESPEED, 0, C.byref(v), 0)
    return v.value


def _usb_suspend():
    """(ac, dc) for USB selective suspend on the active plan, 1 = enabled."""
    try:
        r = subprocess.run(["powercfg", "/q", "SCHEME_CURRENT", USB_SUB, USB_SUSPEND],
                           capture_output=True, text=True, timeout=5,
                           creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    # the labels are localised; the two index values are the last two hex words
    vals = re.findall(r":\s*0x([0-9a-fA-F]{8})\s*$", r.stdout, re.M)
    if len(vals) < 2:
        return None
    return int(vals[-2], 16), int(vals[-1], 16)


# What Windows multiplies every count by at each pointer speed (1..20), with
# enhance pointer precision off. 10 is the only position that is exactly 1.
SPEED_SCALE = (0.03125, 0.0625, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0,
               1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5)


def system_check(state=None):
    if u32 is None:
        return {"items": []}
    items = []
    _t1, _t2, accel = _spi_mouse()
    items.append({
        "id": "epp", "name": "Enhance pointer precision",
        "value": "on" if accel else "off", "want": "off", "ok": not accel,
        "why": "Windows acceleration: the same hand movement travels a "
               "different distance depending on how fast it was. Aim cannot "
               "become muscle memory with it on. Games using raw input ignore "
               "it, the desktop and many games do not.",
    })
    speed = _spi_speed()
    same = None
    if state and speed != 10 and 1 <= speed <= 20:
        stage = state.get("active_stage", 0)
        dpis = state.get("dpi", [])
        if 0 <= stage < len(dpis):
            same = {"dpi": dpis[stage],
                    "equivalent": int(round(dpis[stage] * SPEED_SCALE[speed - 1] / 50.0)) * 50}
    items.append({
        "same_speed": same,
        "id": "speed", "name": "Pointer speed",
        "value": f"{speed} / 20  (slider {speed // 2 + 1} / 11)" if speed % 2 == 0
        else f"{speed} / 20",
        "want": "10 / 20  (slider 6 / 11)", "ok": speed == 10,
        "why": "Any other position scales every count, and below 6/11 Windows "
               "throws counts away, which wastes the sensor's DPI. Change "
               "sensitivity with the DPI stages instead.",
    })
    usb = _usb_suspend()
    if usb is not None:
        ac, dc = usb
        items.append({
            "id": "usb", "name": "USB selective suspend",
            "value": f"plugged in {'on' if ac else 'off'}, battery {'on' if dc else 'off'}",
            "want": "off", "ok": not ac and not dc,
            "why": "Lets Windows power down the receiver between movements. "
                   "The first movement or click after a pause then lags or "
                   "stutters. Fixing it needs administrator rights and turns "
                   "off power saving on the receiver itself too.",
        })
    items.extend(_more_checks())
    return {"items": items}


# The two stock plans that trade responsiveness for power. Anything else - the
# performance plans and vendor plans (GameTurbo and the like) - is left alone.
SLOW_PLANS = {"381b4222-f694-41f0-9685-ff5bb260df2e": "Balanced",
              "a1841308-3541-4fab-bc81-f71556f20b4a": "Power saver"}
HIGH_PERF = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


def _run(args, timeout=6):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)


def _conflicts():
    """Other software that also writes to this mouse: (pid, image) pairs."""
    rc, out = _run(["tasklist", "/fo", "csv", "/nh"])
    if rc != 0:
        return []
    mine = {os.getpid(), os.getppid()}        # onefile exes run as two processes
    found = []
    for line in out.splitlines():
        cells = [c.strip('"') for c in line.split('","')]
        if len(cells) < 2 or not cells[1].isdigit():
            continue
        name, pid = cells[0].strip('"'), int(cells[1])
        low = name.lower()
        if pid in mine:
            continue
        if low == "x3.exe" or "attack shark" in low or "attackshark" in low \
                or low.startswith("prodmutant x3 driver"):
            found.append((pid, name))
    return found


def _more_checks():
    items = []
    conf = _conflicts()
    items.append({
        "id": "conflict", "name": "Other mouse software",
        "value": ", ".join(sorted({n for _p, n in conf})) if conf else "none running",
        "want": "none", "ok": not conf,
        "why": "The mouse answers no reads, so every program that configures it "
               "overwrites the others. The vendor X3.exe, or a second copy of "
               "this driver, will silently undo settings. Closing it here also "
               "pushes this app's settings back to the mouse.",
    })

    rc, out = _run(["powercfg", "/getactivescheme"])
    m = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})\s*\((.*)\)", out) if rc == 0 else None
    if m:
        guid, name = m.group(1).lower(), m.group(2)
        items.append({
            "id": "plan", "name": "Power plan", "value": name,
            "want": "High performance, or any performance plan",
            "ok": guid not in SLOW_PLANS,
            "why": "Balanced and Power saver let the CPU drop into deep idle "
                   "states between inputs, and waking takes time. Note that USB "
                   "selective suspend is set per plan: check it again after "
                   "switching.",
        })

    rc, out = _run(["powercfg", "/qh", "SCHEME_CURRENT", "SUB_PROCESSOR", "CPMINCORES"])
    vals = re.findall(r":\s*0x([0-9a-fA-F]{8})\s*$", out, re.M) if rc == 0 else []
    if len(vals) >= 2:
        ac, dc = int(vals[-2], 16), int(vals[-1], 16)
        items.append({
            "id": "parking", "name": "CPU core parking",
            "value": f"minimum cores {ac}% plugged in, {dc}% on battery",
            "want": "100% (no parking)", "ok": ac >= 100,
            "why": "Parked cores are switched off until load arrives; the "
                   "thread that handles input can land on one and wait for it "
                   "to wake. 100% keeps every core available.",
        })

    gm = _reg_dword(r"Software\Microsoft\GameBar", "AutoGameModeEnabled")
    on = gm is None or gm == 1               # absent means the Windows default: on
    items.append({
        "id": "gamemode", "name": "Game Mode", "value": "on" if on else "off",
        "want": "on", "ok": on,
        "why": "Gives the foreground game priority over background work such "
               "as Windows Update installs. The gain is small but it has no "
               "downside on a desktop.",
    })
    return items


def _reg_dword(path, name):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            v, _t = winreg.QueryValueEx(k, name)
            return int(v)
    except OSError:
        return None


def _set_reg_dword(path, name, value):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))


# ---------------------------------------------------------------- usb port ---

CR_SUCCESS = 0
CM_GETIDLIST_FILTER_ENUMERATOR = 0x00000001
CM_GETIDLIST_FILTER_PRESENT = 0x00000100
CM_DRP_DEVICEDESC, CM_DRP_FRIENDLYNAME, CM_DRP_LOCATION_INFORMATION = 0x01, 0x0D, 0x0E


def _cfg():
    cfg = C.WinDLL("cfgmgr32")
    cfg.CM_Get_Device_ID_List_SizeW.argtypes = [C.POINTER(W.ULONG), W.LPCWSTR, W.ULONG]
    cfg.CM_Get_Device_ID_ListW.argtypes = [W.LPCWSTR, W.LPWSTR, W.ULONG, W.ULONG]
    cfg.CM_Locate_DevNodeW.argtypes = [C.POINTER(W.DWORD), W.LPCWSTR, W.ULONG]
    for fn in ("CM_Get_Parent", "CM_Get_Child", "CM_Get_Sibling"):
        getattr(cfg, fn).argtypes = [C.POINTER(W.DWORD), W.DWORD, W.ULONG]
    cfg.CM_Get_Device_IDW.argtypes = [W.DWORD, W.LPWSTR, W.ULONG, W.ULONG]
    cfg.CM_Get_DevNode_Registry_PropertyW.argtypes = [
        W.DWORD, W.ULONG, C.POINTER(W.ULONG), C.c_void_p, C.POINTER(W.ULONG), W.ULONG]
    return cfg


def _node(cfg, dn):
    buf = C.create_unicode_buffer(512)
    cfg.CM_Get_Device_IDW(dn, buf, 512, 0)

    def prop(code):
        b = C.create_unicode_buffer(512)
        n = W.ULONG(C.sizeof(b))
        if cfg.CM_Get_DevNode_Registry_PropertyW(dn, code, None, b, C.byref(n), 0) == CR_SUCCESS:
            return b.value
        return None
    return {"id": buf.value,
            "name": prop(CM_DRP_FRIENDLYNAME) or prop(CM_DRP_DEVICEDESC) or buf.value,
            "location": prop(CM_DRP_LOCATION_INFORMATION)}


def usb_port():
    """Where the receiver (or cable) is plugged in: the chain up to the USB
    controller, and whatever else shares its hub."""
    if os.name != "nt":
        return {"found": False}
    cfg = _cfg()
    size = W.ULONG()
    flags = CM_GETIDLIST_FILTER_ENUMERATOR | CM_GETIDLIST_FILTER_PRESENT
    cfg.CM_Get_Device_ID_List_SizeW(C.byref(size), "USB", flags)
    buf = C.create_unicode_buffer(size.value + 2)
    if cfg.CM_Get_Device_ID_ListW("USB", buf, size.value, flags) != CR_SUCCESS:
        return {"found": False, "error": "device list unavailable"}
    ids = [i for i in C.wstring_at(C.addressof(buf), size.value).split("\0") if i]
    want = tuple(f"USB\\VID_{P.VENDOR_ID:04X}&PID_{pid:04X}\\" for pid in P.PRODUCT_IDS)
    target = next((i for i in ids if i.upper().startswith(want)), None)
    if not target:
        return {"found": False}
    dn = W.DWORD()
    if cfg.CM_Locate_DevNodeW(C.byref(dn), target, 0) != CR_SUCCESS:
        return {"found": False}
    me = _node(cfg, dn.value)
    chain, cur = [], W.DWORD(dn.value)
    parent = W.DWORD()
    while cfg.CM_Get_Parent(C.byref(parent), cur.value, 0) == CR_SUCCESS:
        n = _node(cfg, parent.value)
        if not n["id"] or n["id"].upper().startswith(("HTREE", "ACPI_HAL")):
            break
        chain.append(n)
        cur = W.DWORD(parent.value)
        if n["id"].upper().startswith("PCI\\"):
            break

    neighbours = []
    if chain:
        hub_dn = W.DWORD()
        cfg.CM_Locate_DevNodeW(C.byref(hub_dn), chain[0]["id"], 0)
        child = W.DWORD()
        ok = cfg.CM_Get_Child(C.byref(child), hub_dn.value, 0) == CR_SUCCESS
        while ok:
            n = _node(cfg, child.value)
            if n["id"] != me["id"]:
                neighbours.append(n)
            nxt = W.DWORD()
            ok = cfg.CM_Get_Sibling(C.byref(nxt), child.value, 0) == CR_SUCCESS
            child = nxt

    controller = next((n for n in chain if n["id"].upper().startswith("PCI\\")), None)
    hub = chain[0] if chain else None
    notes = []
    cname = (controller or {}).get("name", "").lower()
    if "xhci" in cname or "extensible" in cname or "3." in cname:
        notes.append("The controller is USB 3 capable. The receiver itself runs at "
                     "USB 1.1 speed either way; what hurts 2.4 GHz is an active "
                     "USB 3 device (a drive, a hub, a capture card) in the port "
                     "next to it, which radiates in the same band.")
    if hub and not hub["id"].upper().startswith("USB\\ROOT_HUB"):
        notes.append(f"It goes through a hub ({hub['name']}) rather than straight "
                     "into the controller. Fine for a mouse, but a front-panel or "
                     "monitor hub puts the receiver where it can see the mouse; a "
                     "hub behind the PC puts the case in the way.")
    else:
        notes.append("It is plugged straight into the controller's root hub - a "
                     "port on the motherboard (usually the back panel). If that "
                     "puts the case between receiver and mouse, an extension "
                     "cable to the desk usually removes lost reports entirely.")
    return {"found": True, "device": me, "chain": chain, "neighbours": neighbours,
            "notes": notes}


def _script(name):
    for base in (getattr(sys, "_MEIPASS", None),
                 os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
        if base:
            p = os.path.join(base, "tools", name)
            if os.path.isfile(p):
                return p
    return None


def system_fix(what):
    if what == "epp":
        t1, t2, _a = _spi_mouse()
        arr = (C.c_int * 3)(t1, t2, 0)
        ok = u32.SystemParametersInfoW(SPI_SETMOUSE, 0, arr,
                                       SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
        return {"ok": bool(ok)}
    if what == "speed":
        ok = u32.SystemParametersInfoW(SPI_SETMOUSESPEED, 0, C.c_void_p(10),
                                       SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
        return {"ok": bool(ok)}
    if what == "usb":
        script = _script("fix_usb_lag.ps1")
        if not script:
            raise ValueError("tools/fix_usb_lag.ps1 is missing from this build")
        # Elevation is a UAC prompt the user answers; nothing here can skip it.
        rc = C.windll.shell32.ShellExecuteW(
            None, "runas", "powershell.exe",
            f'-NoProfile -ExecutionPolicy Bypass -NoExit -File "{script}"',
            None, 1)
        return {"ok": rc > 32, "launched": rc > 32,
                "note": "Answer the administrator prompt, then replug the receiver."}
    if what == "conflict":
        closed = []
        for pid, name in _conflicts():
            rc, _o = _run(["taskkill", "/PID", str(pid), "/F"])
            if rc == 0:
                closed.append(name)
        return {"ok": bool(closed), "closed": closed, "push": True,
                "note": ("closed " + ", ".join(closed)) if closed
                else "could not close it (it may be running as administrator)"}
    if what == "plan":
        rc, _o = _run(["powercfg", "/setactive", HIGH_PERF])
        return {"ok": rc == 0, "note": None if rc == 0 else
                "this PC has no High performance plan; pick a performance plan "
                "in Control Panel > Power Options"}
    if what == "parking":
        ok = True
        for flag in ("/setacvalueindex", "/setdcvalueindex"):
            rc, _o = _run(["powercfg", flag, "SCHEME_CURRENT", "SUB_PROCESSOR",
                           "CPMINCORES", "100"])
            ok = ok and rc == 0
        _run(["powercfg", "/setactive", "SCHEME_CURRENT"])
        return {"ok": ok, "note": None if ok else "Windows refused; this needs "
                "administrator rights on this PC"}
    if what == "gamemode":
        _set_reg_dword(r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1)
        return {"ok": True}
    raise ValueError(f"unknown fix {what!r}")

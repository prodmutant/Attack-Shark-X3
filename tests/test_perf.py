"""The measurement maths, on synthetic report streams with known answers.

A report-rate tool that misreads its own input is worse than none: it sends
people chasing a receiver problem they do not have. Each case here builds a
stream whose true rate, losses and chatter are known, and checks the stats
recover exactly that.

    python tests/test_perf.py
"""
from __future__ import annotations

import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from attackshark import perf  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not cond:
        failures.append(name)


def stream(hz, seconds, t0=0.0, jitter_ms=0.0, drop=(), mag=5, seed=1):
    rng = random.Random(seed)
    step = 1.0 / hz
    out = []
    for i in range(int(seconds * hz)):
        if i in drop:
            continue
        t = t0 + i * step + rng.uniform(-jitter_ms, jitter_ms) / 1000.0
        out.append((t, mag, -mag, 0, 0))
    return out


# --- report rate ---------------------------------------------------------
for hz in perf.NOMINAL_RATES:
    r = perf.polling_stats(stream(hz, 3.0, jitter_ms=0.05), 1000)
    check(f"{hz} Hz stream reads as {hz}", r["looks_like_hz"] == hz,
          f"measured {r['measured_hz']}")

r = perf.polling_stats(stream(1000, 3.0), 1000)
check("clean 1000 Hz: no losses", r["lost"] == 0 and r["bunched"] == 0)
check("clean 1000 Hz: verdict says configured rate",
      any("configured 1000" in n for n in r["verdict"]))

# the hand stopping for half a second is not a lost report
a = stream(1000, 1.0)
b = stream(1000, 1.0, t0=1.5)
r = perf.polling_stats(a + b, 1000)
check("idle pause is not counted as loss", r["lost"] == 0, f"lost {r['lost']}")
check("idle pause does not drag the rate down", abs(r["measured_hz"] - 1000) < 5,
      f"{r['measured_hz']}")

# 20 single drops during fast motion = 20 lost
drops = set(range(100, 2100, 100))
r = perf.polling_stats(stream(1000, 2.5, drop=drops), 1000)
check("20 dropped reports are counted as 20", r["lost"] == 20, f"lost {r['lost']}")

# slow movement: gaps are the sensor having nothing to say, not loss
slow = stream(1000, 2.0, drop=set(range(0, 2000, 2)), mag=1)
r = perf.polling_stats(slow, 1000)
check("slow movement gaps are not loss", r["lost"] == 0, f"lost {r['lost']}")

# host delivering late: pairs of reports microseconds apart
late = []
for t, dx, dy, f, w in stream(1000, 2.0):
    late.append((t if int(t * 1000) % 2 else t + 0.0009, dx, dy, f, w))
late.sort()
r = perf.polling_stats(late, 1000)
check("bunched delivery detected", r["bunched_pct"] > 40, f"{r['bunched_pct']}%")
check("bunching is not counted as loss", r["lost"] == 0, f"lost {r['lost']}")

# configured 1000 but the mouse runs at 125: the verdict must say so
r = perf.polling_stats(stream(125, 4.0), 1000)
check("rate mismatch named in verdict",
      r["looks_like_hz"] == 125 and any("125 Hz" in n for n in r["verdict"]))

check("too little data says so", not perf.polling_stats(stream(1000, 0.05), 1000)["enough"])

hist = perf.polling_stats(stream(1000, 2.0), 1000)["hist"]
check("histogram holds every interval", sum(hist["counts"]) == 1999)

live = perf.live_rate(stream(1000, 2.0))
check("live rate", abs(live["hz"] - 1000) < 5, f"{live['hz']}")
check("live rate refuses a paused window",
      perf.live_rate(a + stream(1000, 0.3, t0=1.2))["hz"] is None)

# --- clicks --------------------------------------------------------------
DOWN, UP = perf.BUTTON_BITS[1], perf.BUTTON_BITS[1] << 1


def clicks(pairs):
    out, t = [], 0.0
    for hold, gap in pairs:
        out.append((t, 0, 0, DOWN, 0))
        t += hold / 1000.0
        out.append((t, 0, 0, UP, 0))
        t += gap / 1000.0
    return out


r = perf.click_stats(clicks([(80, 200)] * 10), 2)
b = r["buttons"][0]
check("ten clean clicks", b["presses"] == 10 and not b["chatter"] and r["advice"] is None)
check("hold measured", b["hold_min_ms"] == 80.0)

r = perf.click_stats(clicks([(80, 200), (40, 4.5), (60, 200)]), 2)
check("chatter caught", r["buttons"][0]["chatter"] == [4.5])
check("advice raises key response past the chatter",
      r["advice"] and r["advice"]["key_response_ms"] == 8, str(r["advice"]))

r = perf.click_stats(clicks([(80, 200), (40, 9), (60, 200)]), 50)
check("chatter at max key response blames the switch",
      r["advice"] and r["advice"]["key_response_ms"] is None)

# --- DPI -----------------------------------------------------------------
# 100 mm at a true 1600 cpi = 6299 counts, with a slight 1 degree drift
recs = [(i / 1000.0, 63, 1, 0, 0) for i in range(100)]
r = perf.dpi_stats(recs, 100.0, 1600)
check("dpi measured", r["measured_cpi"] == 1600, f"{r['measured_cpi']}")
check("dpi error", r["error_pct"] == 0.0)
check("drift angle", 0.8 < r["drift_deg"] < 1.0, f"{r['drift_deg']}")

r = perf.dpi_stats([(0, 0, -6300, 0, 0)], 100.0, 1600)
check("vertical stroke uses the y axis", r["axis"] == "y" and r["measured_cpi"] == 1600)

# --- speed / spin-out ----------------------------------------------------
# 1600 dpi, 1000 Hz, 40 counts a report = 40*0.0254/1600*1000 = 0.635 m/s
fast = [(i / 1000.0, 40, 0, 0, 0) for i in range(300)]
r = perf.speed_stats(fast, 1600)
check("steady speed measured", abs(r["peak_ms"] - 0.635) < 0.01, f"{r['peak_ms']}")
check("steady motion has no events", not r["events"])

# 3 m/s (189 counts/report) then reports stop dead: a dropout
spin = [(i / 1000.0, 189, 0, 0, 0) for i in range(200)]
spin += [(0.5 + i / 1000.0, 5, 0, 0, 0) for i in range(50)]
r = perf.speed_stats(spin, 1600)
check("dead stop at speed is a dropout", any(e["kind"] == "dropout" for e in r["events"]),
      str(r["events"][:2]))

# 3 m/s then near-zero reports keep coming: a stall
stall = [(i / 1000.0, 189, 0, 0, 0) for i in range(200)]
stall += [(0.2 + i / 1000.0, 1, 0, 0, 0) for i in range(1, 30)]
r = perf.speed_stats(stall, 1600)
check("near-zero reports at speed are a stall", any(e["kind"] == "stall" for e in r["events"]))

# 3 m/s then one big report backwards: a reversal
rev = [(i / 1000.0, 189, 0, 0, 0) for i in range(200)]
rev += [(0.2, -180, 0, 0, 0)] + [(0.2 + i / 1000.0, 189, 0, 0, 0) for i in range(1, 20)]
r = perf.speed_stats(rev, 1600)
check("backwards report at speed is a reversal", any(e["kind"] == "reversal" for e in r["events"]))

# a hand slowing down naturally is not an event
decel = [(i / 1000.0, max(1, 189 - i), 0, 0, 0) for i in range(189)]
check("natural deceleration is not an event", not perf.speed_stats(decel, 1600)["events"])

# --- lift-off / stillness / path ------------------------------------------
check("lift-off tracks", perf.lod_stats(fast, 1.0)["verdict"] == "tracks")
check("lift-off partial", perf.lod_stats(fast[:3], 2.0)["verdict"] == "partial")
check("lift-off stopped", perf.lod_stats([], 3.0)["verdict"] == "stopped")
r = perf.still_stats([(1.0, 1, 0, 0, 0), (2.0, 0, -2, 0, 0)], 10.0)
check("stillness counts noise", r["reports"] == 2 and r["counts"] == 3 and r["biggest"] == 2)
r = perf.path_stats([(0, 5, 0, 0, 0), (0, 5, 1, 0, 0), (0, 0, 0, 0, 0)])
check("path accumulates", r["points"][-1] == [10, 1] and r["flat_pct"] == 50.0)

# --- wheel ---------------------------------------------------------------
down = [(i * 0.05, 0, 0, 0x0400, -120) for i in range(20)]
r = perf.wheel_stats(down, 20)
check("clean scroll: 20 down, none wrong", r["down"] == 20 and not r["wrong_way"] and r["difference"] == 0)
glitch = list(down)
glitch[10] = (glitch[10][0], 0, 0, 0x0400, 120)
r = perf.wheel_stats(glitch)
check("one notch the wrong way is caught", len(r["wrong_way"]) == 1, str(r))
slow = [(0.0, 0, 0, 0x0400, -120), (2.0, 0, 0, 0x0400, 120), (4.0, 0, 0, 0x0400, -120)]
check("a deliberate change of direction is not a fault", not perf.wheel_stats(slow)["wrong_way"])
check("skipped notches show as the difference", perf.wheel_stats(down[:18], 20)["difference"] == -2)

print()
if failures:
    print(f"{len(failures)} failed: " + ", ".join(failures))
    sys.exit(1)
print("all passed")

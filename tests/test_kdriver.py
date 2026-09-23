"""Check the user/kernel contract without needing the driver loaded.

`attackshark/kdriver.py` restates, in ctypes, structures and constants that are
defined in C in `driver/asxfilter/asxfilter_public.h`. Nothing makes the two
agree, and a silent disagreement would not fail loudly - it would put the wrong
bytes in the wrong fields and move the mouse strangely. So the constants are
read back out of the header and compared, and the structure sizes are pinned.

Also exercises the plan compiler, whose one hard guarantee is that a movement
lands exactly where it was asked to.

    python tests/test_kdriver.py
"""
from __future__ import annotations

import ctypes
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from attackshark import hostrun, kdriver, motion      # noqa: E402

HEADER = os.path.join(ROOT, "driver", "asxfilter", "asxfilter_public.h")

failures = []


def check(name, got, want):
    if got != want:
        failures.append(f"{name}: python has {got!r}, C has {want!r}")


def parse_header():
    text = open(HEADER, encoding="utf8").read()
    defines = {}
    for m in re.finditer(r"^#define\s+(ASX_\w+)\s+(.+?)\s*(?://.*)?$", text, re.M):
        name, value = m.group(1), m.group(2).strip()
        v = re.match(r"^0x([0-9A-Fa-f]+)U?L?$|^(\d+)U?L?$", value)
        if v:
            defines[name] = int(v.group(1), 16) if v.group(1) else int(v.group(2))
    return defines


def test_constants(d):
    """Button flags and limits must be identical on both sides."""
    pairs = [
        ("LEFT_DOWN", "ASX_LEFT_DOWN"), ("LEFT_UP", "ASX_LEFT_UP"),
        ("RIGHT_DOWN", "ASX_RIGHT_DOWN"), ("RIGHT_UP", "ASX_RIGHT_UP"),
        ("MIDDLE_DOWN", "ASX_MIDDLE_DOWN"), ("MIDDLE_UP", "ASX_MIDDLE_UP"),
        ("BUTTON4_DOWN", "ASX_BUTTON4_DOWN"), ("BUTTON4_UP", "ASX_BUTTON4_UP"),
        ("BUTTON5_DOWN", "ASX_BUTTON5_DOWN"), ("BUTTON5_UP", "ASX_BUTTON5_UP"),
        ("WHEEL", "ASX_WHEEL"), ("HWHEEL", "ASX_HWHEEL"),
        ("ALL_BUTTONS", "ASX_ALL_BUTTONS"),
        ("SUBMIT_REPLACE", "ASX_SUBMIT_REPLACE"),
        ("SUBMIT_RELEASE", "ASX_SUBMIT_RELEASE"),
        ("MAX_STEPS_PER_SUBMIT", "ASX_MAX_STEPS_PER_SUBMIT"),
        # The wheel is suppressed by direction rather than by transition, so
        # these are their own field's values and not button flags.
        ("WHEEL_UP", "ASX_SUPPRESS_WHEEL_UP"),
        ("WHEEL_DOWN", "ASX_SUPPRESS_WHEEL_DOWN"),
    ]
    for py, c in pairs:
        if c not in d:
            failures.append(f"{c} missing from the header")
            continue
        check(py, getattr(kdriver, py), d[c])

    check("_TYPE", kdriver._TYPE, d.get("ASX_DEVICE_TYPE"))
    # The binding says which interface it is written against; the header says
    # which one it defines. A client newer than the driver is handled at
    # runtime, but these two are one commit and must never disagree.
    ver = d.get("ASX_INTERFACE_VERSION", 0)
    check("INTERFACE_VERSION", kdriver.INTERFACE_VERSION,
          (ver >> 16, ver & 0xFFFF))
    print(f"  {len(pairs) + 2} constants match the C header")


def test_ioctls():
    """CTL_CODE arithmetic, recomputed the way the kernel does it."""
    def ctl(dev, func, method, access):
        return (dev << 16) | (access << 14) | (func << 2) | method

    want = {
        "IOCTL_STATUS": ctl(0x8ADF, 0x800, 0, 1),
        "IOCTL_SUBMIT": ctl(0x8ADF, 0x801, 0, 2),
        "IOCTL_STOP": ctl(0x8ADF, 0x802, 0, 2),
        "IOCTL_SET_FILTER": ctl(0x8ADF, 0x803, 0, 2),
        "IOCTL_READ_EVENTS": ctl(0x8ADF, 0x804, 0, 1),
    }
    for name, value in want.items():
        check(name, getattr(kdriver, name), value)
    print(f"  {len(want)} IOCTL codes correct")


def test_layout():
    """Structure sizes, as the x64 C compiler lays them out."""
    sizes = {
        "STEP": 16,          # DWORD + 2*LONG + USHORT + SHORT
        "EVENT": 24,         # ULONG64 + USHORT + SHORT + 2*LONG + DWORD
        "FILTER_CFG": 16,    # 4 * DWORD, the fourth added in interface 1.1
        # 8 * DWORD + 3 * ULONG64 + the 1.1 DWORD, which is 60 bytes padded
        # out to the structure's 8-byte alignment.
        "STATUS": 64,
    }
    for name, want in sizes.items():
        check(f"sizeof({name})", ctypes.sizeof(getattr(kdriver, name)), want)

    # the field order matters as much as the total
    offsets = [("STEP", "DelayUs", 0), ("STEP", "Dx", 4), ("STEP", "Dy", 8),
               ("STEP", "Buttons", 12), ("STEP", "Data", 14),
               ("EVENT", "Time", 0), ("EVENT", "Buttons", 8),
               ("EVENT", "Dx", 12), ("EVENT", "Dy", 16), ("EVENT", "Suppressed", 20),
               ("STATUS", "StepsEmitted", 32),
               # Appended, never inserted: the 1.0 layout has to stay a prefix
               # of this one, or a 1.0 filter's reply is read as nonsense.
               ("STATUS", "SuppressWheel", 56),
               ("FILTER_CFG", "SuppressWheel", 12)]
    for struct, field, want in offsets:
        got = getattr(getattr(kdriver, struct), field).offset
        check(f"{struct}.{field} offset", got, want)
    print(f"  {len(sizes)} structures and {len(offsets)} field offsets pinned")


def test_plan_lands_exactly():
    """A compiled movement must sum to precisely what was asked for."""
    cases = [(120, 0), (0, -90), (37, 41), (-400, 250), (1, 1), (3, -2), (900, -900)]
    for dx, dy in cases:
        plan, seconds = hostrun.build_plan([{"t": "move", "dx": dx, "dy": dy}])
        sx = sum(s[1] for s in plan)
        sy = sum(s[2] for s in plan)
        if (sx, sy) != (dx, dy):
            failures.append(f"move {dx},{dy} summed to {sx},{sy}")
        if any(s[0] < 0 for s in plan):
            failures.append(f"move {dx},{dy} produced a negative delay")
        if seconds <= 0:
            failures.append(f"move {dx},{dy} has no duration")
        if len(plan) < 2 and (abs(dx) + abs(dy)) > 4:
            failures.append(f"move {dx},{dy} collapsed to {len(plan)} step(s) - a jump")
    print(f"  {len(cases)} movements land exactly and stay multi-step")


def test_plan_folds_delays():
    """A delay belongs to the next emitted step, not to this thread."""
    steps = [{"t": "delay", "ms": 50, "jitter": 0},
             {"t": "mouse", "button": "left", "down": True},
             {"t": "delay", "ms": 40, "jitter": 0},
             {"t": "mouse", "button": "left", "down": False}]
    plan, seconds = hostrun.build_plan(steps)
    check("folded plan length", len(plan), 2)
    check("first delay (us)", plan[0][0], 50_000)
    check("second delay (us)", plan[1][0], 40_000)
    check("down flag", plan[0][3], kdriver.LEFT_DOWN)
    check("up flag", plan[1][3], kdriver.LEFT_UP)
    check("duration", round(seconds, 3), 0.09)

    wheel, _ = hostrun.build_plan([{"t": "wheel", "delta": -2}])
    check("wheel flag", wheel[0][3], kdriver.WHEEL)
    check("wheel data", wheel[0][4], -240)
    print("  delays fold into the following step; wheel encodes 120/notch")


def test_speed_scales():
    slow, s_slow = hostrun.build_plan([{"t": "delay", "ms": 100, "jitter": 0},
                                       {"t": "mouse", "button": "left", "down": True}],
                                      speed=1.0)
    fast, s_fast = hostrun.build_plan([{"t": "delay", "ms": 100, "jitter": 0},
                                       {"t": "mouse", "button": "left", "down": True}],
                                      speed=4.0)
    check("speed 1.0 delay", slow[0][0], 100_000)
    check("speed 4.0 delay", fast[0][0], 25_000)
    print("  speed scales delays")


def test_step_limits():
    """Nothing may exceed what one submit is allowed to carry."""
    plan, _ = hostrun.build_plan([{"t": "move", "dx": 4000, "dy": 4000}])
    over = [s for s in plan if abs(s[1]) > 16384 or abs(s[2]) > 16384]
    if over:
        failures.append(f"{len(over)} steps exceed the driver's per-step clamp")
    # a long plan is chunked by kdriver.submit rather than truncated
    if kdriver.MAX_STEPS_PER_SUBMIT < 1:
        failures.append("MAX_STEPS_PER_SUBMIT is nonsense")
    print(f"  longest test plan is {len(plan)} steps, all within the clamp")


def main():
    print("kdriver <-> asxfilter_public.h")
    if not os.path.exists(HEADER):
        print(f"FAIL - cannot find {HEADER}")
        return 1
    d = parse_header()
    test_constants(d)
    test_ioctls()
    test_layout()
    print("\nplan compiler")
    test_plan_lands_exactly()
    test_plan_folds_delays()
    test_speed_scales()
    test_step_limits()

    print()
    if failures:
        for f in failures:
            print("  FAIL " + f)
        print(f"\nFAIL - {len(failures)} mismatch(es)")
        return 1
    print("PASS - the user-mode binding matches the driver's header")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Motion trajectories.

A single big `dx,dy` jump is what makes synthesised movement feel fake: the
pointer teleports, at constant speed, in a perfectly straight line, with the
rounding error thrown away. Real movement is none of those things.

What this produces instead:

* **Many small increments** at the device's report rate, not one jump. At
  1000 Hz that is one increment per millisecond, the same granularity the
  sensor itself would produce.
* **A minimum-jerk velocity profile.** Human point-to-point reaching follows
  `10t^3 - 15t^4 + 6t^5` (Flash & Hogan, 1985) - it starts at zero velocity,
  peaks in the middle, and lands at zero. Constant velocity is the giveaway.
* **A slight arc.** Hands do not travel in straight lines; a small
  perpendicular offset that is zero at both ends bends the path.
* **Sub-pixel accumulation.** The fractional remainder carries to the next
  tick, so a 3-pixel move over 40 ticks does not become 40 rounding errors.
  The last tick corrects any residual, so the total always lands exactly.
* **Tremor.** A small amount of correlated noise, not white noise - real hand
  jitter drifts rather than flickering.
* **Overshoot and settle** on longer moves, which is what actually happens
  when someone flicks to a target and corrects.

Everything is deterministic given a seed, so a macro can be replayed exactly
when you want that.
"""
from __future__ import annotations

import math
import random

PROFILES = ("instant", "linear", "smooth", "human")

DEFAULT_RATE_HZ = 1000
MIN_TICK_MS = 1.0


def _min_jerk(tau):
    """Position fraction at normalised time tau, zero velocity at both ends."""
    return tau * tau * tau * (10.0 - 15.0 * tau + 6.0 * tau * tau)


def _ease(tau, profile):
    if profile == "linear":
        return tau
    return _min_jerk(tau)


def plan(dx, dy, duration_ms=None, rate_hz=DEFAULT_RATE_HZ, profile="human",
         seed=None, arc=None, tremor=None, overshoot=None):
    """Return [(delay_s, ix, iy), ...] whose increments sum exactly to dx,dy.

    `duration_ms` defaults to something proportional to the distance, the way a
    real movement takes longer the further it goes (Fitts-ish, not exact).
    """
    dx, dy = int(dx), int(dy)
    if profile == "instant" or (dx == 0 and dy == 0):
        return [(0.0, dx, dy)] if (dx or dy) else []

    dist = math.hypot(dx, dy)
    if duration_ms is None:
        # ~8 ms of travel for the first pixel, scaling sublinearly after that
        duration_ms = max(6.0, 10.0 * math.sqrt(dist))
    rate_hz = max(50, min(2000, int(rate_hz)))
    tick_ms = max(MIN_TICK_MS, 1000.0 / rate_hz)
    ticks = max(1, int(round(duration_ms / tick_ms)))

    rng = random.Random(seed)
    if arc is None:
        arc = 0.0 if profile in ("linear", "instant") else dist * rng.uniform(0.02, 0.06)
    if tremor is None:
        tremor = 0.0 if profile in ("linear", "instant") else min(0.9, dist * 0.02)
    if overshoot is None:
        overshoot = 0.0
        if profile == "human" and dist >= 40:
            overshoot = dist * rng.uniform(0.01, 0.035)

    # unit vector along the move, and its perpendicular
    ux, uy = (dx / dist, dy / dist) if dist else (0.0, 0.0)
    px, py = -uy, ux
    arc_sign = 1.0 if rng.random() < 0.5 else -1.0

    # correlated tremor: a slow random walk rather than per-tick white noise
    walk = 0.0

    out = []
    emitted_x = emitted_y = 0
    for i in range(1, ticks + 1):
        tau = i / ticks
        travel = _ease(tau, profile)

        # overshoot: go past, then come back over the last third
        reach = 1.0
        if overshoot:
            if tau < 0.72:
                reach = 1.0 + (overshoot / dist) * (tau / 0.72)
            else:
                k = (tau - 0.72) / 0.28
                reach = 1.0 + (overshoot / dist) * (1.0 - _min_jerk(k))

        base_x = dx * travel * reach
        base_y = dy * travel * reach

        if arc:
            bend = math.sin(math.pi * tau) * arc * arc_sign
            base_x += px * bend
            base_y += py * bend

        if tremor:
            walk = walk * 0.82 + rng.uniform(-1.0, 1.0) * tremor * 0.18
            wob = math.sin(math.pi * tau)      # no tremor at the endpoints
            base_x += px * walk * wob
            base_y += py * walk * wob

        want_x, want_y = int(round(base_x)), int(round(base_y))
        ix, iy = want_x - emitted_x, want_y - emitted_y
        emitted_x, emitted_y = want_x, want_y

        jitter = 1.0
        if profile == "human":
            jitter = rng.uniform(0.85, 1.15)   # report timing is never exact
        if ix or iy:
            out.append((tick_ms * jitter / 1000.0, ix, iy))
        else:
            # keep the clock moving even on a tick that rounds to no pixels
            if out:
                d, ax, ay = out[-1]
                out[-1] = (d + tick_ms * jitter / 1000.0, ax, ay)
            else:
                out.append((tick_ms * jitter / 1000.0, 0, 0))

    # land exactly on target whatever the rounding did
    rx, ry = dx - emitted_x, dy - emitted_y
    if rx or ry:
        out.append((tick_ms / 1000.0, rx, ry))
    return out


def describe(dx, dy, **kw):
    """Summary for the UI: how long it takes and how many reports it sends."""
    steps = plan(dx, dy, **kw)
    return {
        "ticks": len(steps),
        "duration_ms": round(sum(s[0] for s in steps) * 1000),
        "total": (sum(s[1] for s in steps), sum(s[2] for s in steps)),
        "peak_step": max((abs(s[1]) + abs(s[2]) for s in steps), default=0),
    }

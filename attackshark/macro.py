"""Macro model.

A macro is a flat list of steps. No scripting language - every step is a small
typed record the editor can render as a row, and the engine can execute
directly.

    key    {"t":"key",   "key":"a", "down":true}
    mouse  {"t":"mouse", "button":"left", "down":true}
    move   {"t":"move",  "dx":0, "dy":-4}
    wheel  {"t":"wheel", "delta":1}      +up, -down, one notch per unit
    delay  {"t":"delay", "ms":12, "jitter":0}
    call   {"t":"call",  "id":"<macro id>", "times":1}

A macro can also be written as *lanes* - one input per row, blocks placed
along a timeline - and `validate()` derives the steps above from them. A key or
mouse lane holds `hold` and `spam` blocks; a wheel lane holds `tick` and `spam`
blocks, because a notch has no release to place. See `lane_steps()`.

**Sequential by default, positioned when you say so.** A step with no `at`
happens after the one before it, which is what a recorded macro wants. A step
carrying `"at": 40` happens 40 ms from the start of the macro instead, and
leaves the running cursor where it was.

That one field is what makes overlap expressible. A key held with taps
underneath it cannot be written as a sequence at all, because a sequence has
no way to say "while":

    {"t":"key","key":"a","down":true,  "at":0}     A down, held
    {"t":"key","key":"w","down":true,  "at":10}      W tap inside it
    {"t":"key","key":"w","down":false, "at":19}
    {"t":"key","key":"w","down":true,  "at":28}      and another
    {"t":"key","key":"w","down":false, "at":37}
    {"t":"key","key":"a","down":false, "at":50}    A up

`compile_timeline()` turns either style into one sorted list of (offset, event)
pairs, so the engine only ever executes a timeline and nothing downstream has
to care which way a macro was written. A `call` step splices another macro's
timeline in at the cursor, which is macro reuse for the same price - write a
burst once and call it from every macro that needs it.

Two execution targets:

* **host**   - the driver plays it back. Everything works: movement, wheel,
               chaining, composites, per-step jitter.
* **device** - flashed into the mouse, runs with nothing installed. The
               firmware's event is two bytes, `[flags, HID usage]`, so it can
               only hold key presses and releases. `device_support()` says
               whether a given macro fits, and why not when it doesn't.
"""
from __future__ import annotations

import random
import uuid

from . import protocol as P

BUTTONS = ("left", "right", "middle", "x1", "x2")
REPEAT_MODES = ("once", "count", "hold", "toggle")
WHEEL_DIRS = ("up", "down")
LANE_KINDS = ("key", "mouse", "wheel", "macro")

MAX_STEPS = 512
MAX_DELAY_MS = 60_000
MAX_CALL_TIMES = 100
MAX_TIMELINE_EVENTS = 4096
MAX_LANES = 24

#: A key or a button is pressed and released, so its one-shot block is a
#: `hold` with two edges. A wheel notch has no edges at all - it is a single
#: event with a direction - so its one-shot block is a `tick`. Both kinds
#: spam the same way: the one-shot repeated across the width of the block.
PRESS_BLOCKS = ("hold", "spam")
WHEEL_BLOCKS = ("tick", "spam")
BLOCK_TYPES = ("hold", "tick", "spam")
MAX_NOTCHES = 16
MAX_MOVE = 4096

#: Device macro event flags (bit 7 marks a release), confirmed from a capture:
#: `01 06` press 'c', `81 06` release 'c'.
DEV_PRESS, DEV_RELEASE = 0x01, 0x81

#: Report 0x09 carries the macro in 64-byte chunks, 60 payload bytes each.
DEV_REPORT = 0x09
DEV_BLOCK = 0x08
DEV_CHUNK = 64
DEV_CHUNK_PAYLOAD = DEV_CHUNK - 4
DEV_PAYLOAD_LEN = 128          # reassembled block, incl. the trailing checksum

#: Macro slot, stored at payload[0]. Slot 0 is treated by the firmware as
#: "unset": a block uploaded there is accepted, checksums fine, and then never
#: plays - the bound button silently falls back to its default action. The
#: vendor app uses 2, and 2 is verified to play. This cost a whole debugging
#: session; see PROTOCOL.md section 8.
DEV_SLOT = 2
DEV_COUNT_AT = 25              # event count
DEV_EVENTS_AT = 26
DEV_MAX_EVENTS = (DEV_PAYLOAD_LEN - 2 - DEV_EVENTS_AT) // 2

#: Button-slot action code that plays a stored macro (captured as `12 00 08`).
ACTION_MACRO = 0x12


class MacroError(ValueError):
    pass


# ------------------------------------------------------------- validation ---
def _block(raw, lane_kind="key"):
    """One rectangle on a lane: where it starts, how wide, and which kind.

    The one-shot type depends on the lane, because a wheel has nothing to
    hold: `hold` on a wheel lane and `tick` on any other are each other's
    name for the same gesture, so each is quietly read as the other rather
    than refused. That matters in practice - the editor drops a new block
    before it has asked what the lane is.
    """
    if not isinstance(raw, dict):
        raise MacroError("a block must be an object")
    wheel = lane_kind == "wheel"
    allowed = WHEEL_BLOCKS if wheel else PRESS_BLOCKS
    kind = raw.get("type", allowed[0])
    if wheel and kind == "hold":
        kind = "tick"
    elif not wheel and kind == "tick":
        kind = "hold"
    if kind not in allowed:
        raise MacroError(f"a {lane_kind} lane's blocks are one of {allowed}")
    at = int(raw.get("at", 0))
    ms = int(raw.get("ms", 0))
    if not 0 <= at <= MAX_DELAY_MS:
        raise MacroError(f"a block starts at 0..{MAX_DELAY_MS} ms")
    if not 0 <= ms <= MAX_DELAY_MS:
        raise MacroError(f"a block is 0..{MAX_DELAY_MS} ms long")
    out = {"type": kind, "at": at, "ms": ms}
    if kind == "spam":
        # `rate` is press-start to press-start, `hold` is how long each one is
        # down. Both have to clear a frame or the game samples straight past
        # them, but this layer cannot know the frame rate, so it only refuses
        # the values that are nonsense at any frame rate.
        rate = int(raw.get("rate", 18))
        if rate < 1:
            raise MacroError("a spam block needs a rate of at least 1 ms")
        out["rate"] = rate
        if not wheel:
            hold = int(raw.get("hold", 9))
            if hold < 1 or hold >= rate:
                raise MacroError("each press must be held at least 1 ms and "
                                 "released before the next one starts")
            out["hold"] = hold
    if wheel:
        # How far one event turns the wheel. A real notch is one, and a game
        # that reads the delta rather than counting events treats three as
        # three - which is the difference between a weapon-switch bind and a
        # scroll that flies past what you wanted.
        notches = int(raw.get("notches", 1))
        if not 1 <= notches <= MAX_NOTCHES:
            raise MacroError(f"a wheel block turns 1..{MAX_NOTCHES} notches "
                             f"per event")
        out["notches"] = notches
    return out


def _lane(raw):
    """One row of the editor: an input, and the blocks placed along it."""
    if not isinstance(raw, dict):
        raise MacroError("a lane must be an object")
    kind = raw.get("kind", "key")
    lane = {"kind": kind,
            "blocks": [_block(b, kind) for b in raw.get("blocks") or []]}
    if kind == "key":
        key = str(raw.get("key", "")).lower()
        if key not in P.HID_KEYS:
            raise MacroError(f"unknown key {key!r}")
        lane["key"] = key
    elif kind == "mouse":
        b = str(raw.get("button", "")).lower()
        if b not in BUTTONS:
            raise MacroError(f"unknown mouse button {b!r}")
        lane["button"] = b
    elif kind == "wheel":
        d = str(raw.get("direction", "")).lower()
        if d not in WHEEL_DIRS:
            raise MacroError(f"a wheel lane scrolls one of {WHEEL_DIRS}")
        lane["direction"] = d
    elif kind == "macro":
        mid = str(raw.get("id") or "").strip()
        if not mid:
            raise MacroError("a macro lane needs the id of the macro to play")
        lane["id"] = mid
    else:
        raise MacroError(f"unknown lane kind {kind!r}")
    return lane


def _wheel_steps(lane, b):
    """One wheel block -> the notches it turns, each carrying its moment.

    Up is positive, the same sign Windows uses, so a step from a lane and a
    step typed into the list are the same record.
    """
    delta = b.get("notches", 1) * (1 if lane["direction"] == "up" else -1)
    if b["type"] == "spam":
        # Inclusive of the far edge: a 100 ms block at 20 ms is six notches,
        # the first on the left edge and the last on the right, which is what
        # a rectangle that wide looks like it should do.
        starts = range(b["at"], b["at"] + b["ms"] + 1, b["rate"])
    else:
        starts = (b["at"],)
    return [{"t": "wheel", "delta": delta, "at": t} for t in starts]


def lane_steps(lanes):
    """Lanes -> positioned steps. This is where a rectangle becomes input.

    A `hold` block is the obvious pair: press at its left edge, release at its
    right. A `spam` block is that pair repeated across the same width every
    `rate` ms - a burst worth having as one object you can drag, rather than
    forty steps you have to keep in order by hand.

    A wheel lane is the same idea with one edge instead of two. A `tick` is a
    single notch at the left edge and the width is only how the block draws;
    a `spam` is a notch every `rate` ms across that width, which is a wheel
    spun at an exact speed for an exact length of time.

    Everything comes out carrying `at`, so lanes that overlap simply produce
    steps that overlap, and the timeline compiler needs to know nothing about
    any of this.
    """
    steps = []
    for lane in lanes:
        for b in lane["blocks"]:
            if lane["kind"] == "wheel":
                steps.extend(_wheel_steps(lane, b))
                if len(steps) > MAX_STEPS:
                    raise MacroError(
                        f"these blocks expand past {MAX_STEPS} steps - widen "
                        f"the spam rate or shorten the block")
                continue
            starts = [b["at"]]
            hold = b["ms"]
            if b["type"] == "spam":
                hold = b["hold"]
                starts = list(range(b["at"], b["at"] + b["ms"] - hold + 1,
                                    b["rate"]))
            for t in starts:
                if lane["kind"] == "macro":
                    steps.append({"t": "call", "id": lane["id"], "times": 1,
                                  "at": t})
                    continue
                if lane["kind"] == "key":
                    on = {"t": "key", "key": lane["key"], "down": True}
                    off = {"t": "key", "key": lane["key"], "down": False}
                else:
                    on = {"t": "mouse", "button": lane["button"], "down": True}
                    off = {"t": "mouse", "button": lane["button"], "down": False}
                steps.append({**on, "at": t})
                steps.append({**off, "at": t + hold})
            if len(steps) > MAX_STEPS:
                raise MacroError(
                    f"these blocks expand past {MAX_STEPS} steps - widen the "
                    f"spam rate or shorten the block")
    steps.sort(key=lambda s: s["at"])
    return steps


def _at(raw):
    """Absolute placement in ms, or None for "after the previous step"."""
    if raw.get("at") is None:
        return None
    try:
        at = int(raw["at"])
    except (TypeError, ValueError):
        raise MacroError("at must be a whole number of milliseconds")
    if not 0 <= at <= MAX_DELAY_MS:
        raise MacroError(f"at must be 0..{MAX_DELAY_MS} ms")
    return at


def _step(raw):
    out = _step_body(raw)
    at = _at(raw)
    if at is not None:
        out["at"] = at
    return out


def _step_body(raw):
    if not isinstance(raw, dict):
        raise MacroError("a step must be an object")
    t = raw.get("t")
    if t == "call":
        mid = str(raw.get("id") or "").strip()
        if not mid:
            raise MacroError("a call step needs the id of the macro to play")
        times = int(raw.get("times", 1))
        if not 1 <= times <= MAX_CALL_TIMES:
            raise MacroError(f"times must be 1..{MAX_CALL_TIMES}")
        return {"t": "call", "id": mid, "times": times}
    if t == "key":
        key = str(raw.get("key", "")).lower()
        if key not in P.HID_KEYS:
            raise MacroError(f"unknown key {key!r}")
        return {"t": "key", "key": key, "down": bool(raw.get("down", True))}
    if t == "mouse":
        b = str(raw.get("button", "")).lower()
        if b not in BUTTONS:
            raise MacroError(f"unknown mouse button {b!r}")
        return {"t": "mouse", "button": b, "down": bool(raw.get("down", True))}
    if t == "move":
        dx, dy = int(raw.get("dx", 0)), int(raw.get("dy", 0))
        if abs(dx) > MAX_MOVE or abs(dy) > MAX_MOVE:
            raise MacroError(f"move beyond +/-{MAX_MOVE}")
        return {"t": "move", "dx": dx, "dy": dy}
    if t == "wheel":
        return {"t": "wheel", "delta": max(-16, min(16, int(raw.get("delta", 1))))}
    if t == "delay":
        ms = int(raw.get("ms", 10))
        if not 0 <= ms <= MAX_DELAY_MS:
            raise MacroError(f"delay must be 0..{MAX_DELAY_MS} ms")
        jitter = max(0, min(ms if ms else MAX_DELAY_MS, int(raw.get("jitter", 0))))
        return {"t": "delay", "ms": ms, "jitter": jitter}
    raise MacroError(f"unknown step type {t!r}")


def validate(raw):
    """Normalise a macro dict, raising MacroError on anything malformed."""
    if not isinstance(raw, dict):
        raise MacroError("a macro must be an object")
    lanes = raw.get("lanes")
    if lanes is not None:
        if not isinstance(lanes, list):
            raise MacroError("lanes must be a list")
        if len(lanes) > MAX_LANES:
            raise MacroError(f"at most {MAX_LANES} lanes")
        lanes = [_lane(x) for x in lanes]
        # The editor authors lanes; steps are what the engine runs. Deriving
        # one from the other here rather than in the page means they cannot
        # drift apart, whatever wrote the macro.
        steps = lane_steps(lanes)
    else:
        steps = raw.get("steps") or []
    if not isinstance(steps, list):
        raise MacroError("steps must be a list")
    if len(steps) > MAX_STEPS:
        raise MacroError(f"at most {MAX_STEPS} steps")
    mode = raw.get("repeat", "once")
    if mode not in REPEAT_MODES:
        raise MacroError(f"repeat must be one of {REPEAT_MODES}")
    count = int(raw.get("count", 1))
    if not 1 <= count <= 10_000:
        raise MacroError("count must be 1..10000")
    speed = float(raw.get("speed", 1.0))
    if not 0.1 <= speed <= 10.0:
        raise MacroError("speed must be 0.1..10")
    # A lane macro's length is where its last block ends, which leaves no way
    # to say "then wait, then go again" - and a looping macro that repeats the
    # instant its last press lands is a macro with no gap between passes.
    # `span` is a floor on that length, and nothing else: it adds no input.
    span = int(raw.get("span") or 0)
    if not 0 <= span <= MAX_DELAY_MS:
        raise MacroError(f"span must be 0..{MAX_DELAY_MS} ms")
    name = str(raw.get("name") or "macro").strip()[:48] or "macro"
    out = {
        "id": str(raw.get("id") or uuid.uuid4()),
        "name": name,
        "steps": [_step(s) for s in steps],
        "repeat": mode,
        "count": count,
        "speed": speed,
    }
    if span:
        out["span"] = span
    if lanes is not None:
        out["lanes"] = lanes
    return out


def delay_ms(step, speed=1.0):
    """A delay's length in ms, jitter rolled, scaled by the macro's speed."""
    ms = step["ms"]
    if step.get("jitter"):
        ms += random.uniform(-step["jitter"], step["jitter"])
    return max(0.0, ms / speed)


def has_jitter(macro):
    return any(s["t"] == "delay" and s.get("jitter") for s in macro["steps"])


def compile_timeline(macro, registry=None, _stack=()):
    """Flatten a macro into (events, duration_ms).

    `events` is [(offset_ms, step)] sorted by offset - every executable event
    with the moment it happens, relative to the start of the macro. Delays are
    gone by this point: they were only ever a way of writing down an offset.

    Two rules produce it. A step with no `at` sits at the running cursor and
    advances it; a step with `at` sits there and does not. So the sequential
    form and the positioned form compose freely in one list, and a macro that
    uses neither is unchanged by the trip through here.

    `registry` maps macro id -> macro, and is what `call` resolves against. A
    macro that reaches itself through any chain of calls raises rather than
    recursing, since the alternative is a hang with the keyboard held down.
    """
    registry = registry or {}
    # A block's width is its duration, and the last event inside it is not
    # the same thing: a spam block ending in a release 9 ms after its final
    # press still occupies the rest of its width. That difference is the loop
    # period of a held macro, so a lane macro's span is its blocks' extent.
    extent = float(macro.get("span") or 0)
    for lane in macro.get("lanes") or ():
        for b in lane["blocks"]:
            extent = max(extent, float(b["at"] + b["ms"]))
    if macro["id"] in _stack:
        chain = " -> ".join(list(_stack) + [macro["id"]])
        raise MacroError(f"a macro cannot call itself: {chain}")
    stack = tuple(_stack) + (macro["id"],)
    speed = macro["speed"] or 1.0

    events = []
    cursor = 0.0
    end = 0.0
    for s in macro["steps"]:
        placed = s.get("at")
        base = cursor if placed is None else placed / speed

        if s["t"] == "delay":
            if placed is None:
                cursor += delay_ms(s, speed)
            end = max(end, cursor)
            continue

        if s["t"] == "call":
            child = registry.get(s["id"])
            if child is None:
                raise MacroError(f"call to a macro that no longer exists: {s['id']}")
            sub, sub_ms = compile_timeline(child, registry, stack)
            for _ in range(s["times"]):
                for off, ev in sub:
                    events.append((base + off / speed, ev))
                base += sub_ms / speed
            if placed is None:
                cursor = base
            end = max(end, base)
            continue

        events.append((base, s))
        end = max(end, base)
        if len(events) > MAX_TIMELINE_EVENTS:
            raise MacroError(f"more than {MAX_TIMELINE_EVENTS} events once "
                             f"calls are expanded")

    events.sort(key=lambda e: e[0])
    return events, max(end, extent / speed)


def timeline_steps(events):
    """Timeline -> the old interleaved step list, for code that wants one."""
    out = []
    prev = 0.0
    for off, ev in events:
        gap = off - prev
        if gap > 0:
            out.append({"t": "delay", "ms": gap, "jitter": 0})
        out.append(ev)
        prev = off
    return out


def duration_ms(macro, registry=None):
    """Nominal single-pass duration, calls and overlap accounted for."""
    try:
        return compile_timeline(macro, registry)[1]
    except MacroError:
        return sum(s["ms"] for s in macro["steps"]
                   if s["t"] == "delay") / macro["speed"]


def summarise(macro, registry=None):
    kinds = {}
    for s in macro["steps"]:
        kinds[s["t"]] = kinds.get(s["t"], 0) + 1
    ok, why = device_support(macro, registry)
    return {
        "id": macro["id"], "name": macro["name"],
        "steps": len(macro["steps"]), "kinds": kinds,
        "repeat": macro["repeat"], "count": macro["count"],
        "duration_ms": round(duration_ms(macro, registry)),
        "device_ok": ok, "device_note": why,
        "target": "device" if ok else "host",
    }


# ----------------------------------------------------------- device export ---
def device_support(macro, registry=None):
    """Can the mouse itself hold this macro? Returns (ok, explanation)."""
    try:
        events, _ = compile_timeline(macro, registry)
    except MacroError as e:
        return False, str(e)
    # A positioned step carries a time, and the firmware has nowhere to put
    # one: its block is a bare sequence of 2-byte events. A lane macro is
    # made entirely of positioned steps and usually has no `delay` in it at
    # all, so the old "contains a delay" guard waved it straight through and
    # offered to flash a macro the mouse cannot express.
    if macro.get("lanes"):
        return False, ("runs from this app - lanes place inputs in time, and "
                       "the device block has no timing field")
    if any(s.get("at") is not None for s in macro["steps"]):
        return False, ("runs from this app - a positioned step carries a time "
                       "the device block cannot store")
    kinds = {e[1]["t"] for e in events} | {
        s["t"] for s in macro["steps"] if s["t"] == "delay"}
    if "move" in kinds or "wheel" in kinds:
        return False, ("the firmware's macro event is 2 bytes with no room for "
                       "a movement delta - movement runs on the host")
    if "mouse" in kinds:
        return False, ("mouse-button events inside a device macro are not "
                       "decoded yet - runs on the host")
    if "delay" in kinds:
        return False, ("delay encoding inside a device macro is not decoded yet "
                       "- runs on the host")
    events = sum(1 for s in macro["steps"] if s["t"] == "key")
    if events == 0:
        return False, "nothing the firmware can store"
    if events > DEV_MAX_EVENTS:
        return False, f"more than {DEV_MAX_EVENTS} key events"
    if macro["repeat"] not in ("once", "count"):
        return False, "hold/toggle repeat is a host behaviour"
    return True, "fits in the mouse"


def to_device_events(macro):
    """[(flags, usage), ...] - only valid when device_support() says ok."""
    out = []
    for s in macro["steps"]:
        if s["t"] != "key":
            continue
        out.append(((DEV_PRESS if s["down"] else DEV_RELEASE), P.HID_KEYS[s["key"]]))
    return out


def build_device_block(macro, slot=DEV_SLOT):
    """The 128-byte macro block, checksummed the way every other block is."""
    ok, why = device_support(macro)
    if not ok:
        raise MacroError(why)
    events = to_device_events(macro)
    buf = bytearray(DEV_PAYLOAD_LEN)
    buf[0] = slot & 0xFF                # 0 means "unset" - see DEV_SLOT
    buf[4] = 0x01                       # constant in the captured block
    buf[DEV_COUNT_AT] = len(events)
    for i, (flags, usage) in enumerate(events):
        buf[DEV_EVENTS_AT + i * 2] = flags
        buf[DEV_EVENTS_AT + i * 2 + 1] = usage
    total = sum(buf[0:DEV_PAYLOAD_LEN - 2]) & 0xFFFF
    buf[DEV_PAYLOAD_LEN - 2] = (total >> 8) & 0xFF
    buf[DEV_PAYLOAD_LEN - 1] = total & 0xFF
    return bytes(buf)


def build_device_upload(macro, slot=DEV_SLOT):
    """The report 0x09 chunks to send, in order."""
    block = build_device_block(macro, slot)
    chunks = []
    for index in range(0, (len(block) + DEV_CHUNK_PAYLOAD - 1) // DEV_CHUNK_PAYLOAD):
        part = block[index * DEV_CHUNK_PAYLOAD:(index + 1) * DEV_CHUNK_PAYLOAD]
        pkt = bytearray(DEV_CHUNK)
        pkt[0] = DEV_REPORT
        pkt[1] = len(part) + 4          # used bytes in this chunk
        pkt[2] = DEV_BLOCK
        pkt[3] = index
        pkt[4:4 + len(part)] = part
        chunks.append(bytes(pkt))
    return chunks


def button_slot_entry(macro_index=0, loop_field=0x08):
    """The 3-byte button-slot entry that plays stored macro `macro_index`.

    `loop_field` is the byte captured as 0x08 alongside "play once"; its exact
    meaning is not decoded, so the captured value is the default.
    """
    return (ACTION_MACRO, macro_index & 0xFF, loop_field & 0xFF)

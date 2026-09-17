"""Macro model.

A macro is a flat list of steps. No scripting language - every step is a small
typed record the editor can render as a row, and the engine can execute
directly.

    key    {"t":"key",   "key":"a", "down":true}
    mouse  {"t":"mouse", "button":"left", "down":true}
    move   {"t":"move",  "dx":0, "dy":-4}
    wheel  {"t":"wheel", "delta":1}
    delay  {"t":"delay", "ms":12, "jitter":0}

Two execution targets:

* **host**   - the driver plays it back. Everything works: movement, wheel,
               chaining, composites, per-step jitter.
* **device** - flashed into the mouse, runs with nothing installed. The
               firmware's event is two bytes, `[flags, HID usage]`, so it can
               only hold key presses and releases. `device_support()` says
               whether a given macro fits, and why not when it doesn't.
"""
from __future__ import annotations

import uuid

from . import protocol as P

BUTTONS = ("left", "right", "middle", "x1", "x2")
REPEAT_MODES = ("once", "count", "hold", "toggle")

MAX_STEPS = 512
MAX_DELAY_MS = 60_000
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
DEV_COUNT_AT = 25              # event count
DEV_EVENTS_AT = 26
DEV_MAX_EVENTS = (DEV_PAYLOAD_LEN - 2 - DEV_EVENTS_AT) // 2

#: Button-slot action code that plays a stored macro (captured as `12 00 08`).
ACTION_MACRO = 0x12


class MacroError(ValueError):
    pass


# ------------------------------------------------------------- validation ---
def _step(raw):
    if not isinstance(raw, dict):
        raise MacroError("a step must be an object")
    t = raw.get("t")
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
    name = str(raw.get("name") or "macro").strip()[:48] or "macro"
    return {
        "id": str(raw.get("id") or uuid.uuid4()),
        "name": name,
        "steps": [_step(s) for s in steps],
        "repeat": mode,
        "count": count,
        "speed": speed,
    }


def duration_ms(macro):
    """Nominal single-pass duration; movement and keys are treated as instant."""
    return sum(s["ms"] for s in macro["steps"] if s["t"] == "delay") / macro["speed"]


def summarise(macro):
    kinds = {}
    for s in macro["steps"]:
        kinds[s["t"]] = kinds.get(s["t"], 0) + 1
    ok, why = device_support(macro)
    return {
        "id": macro["id"], "name": macro["name"],
        "steps": len(macro["steps"]), "kinds": kinds,
        "repeat": macro["repeat"], "count": macro["count"],
        "duration_ms": round(duration_ms(macro)),
        "device_ok": ok, "device_note": why,
        "target": "device" if ok else "host",
    }


# ----------------------------------------------------------- device export ---
def device_support(macro):
    """Can the mouse itself hold this macro? Returns (ok, explanation)."""
    kinds = {s["t"] for s in macro["steps"]}
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


def build_device_block(macro):
    """The 128-byte macro block, checksummed the way every other block is."""
    ok, why = device_support(macro)
    if not ok:
        raise MacroError(why)
    events = to_device_events(macro)
    buf = bytearray(DEV_PAYLOAD_LEN)
    buf[4] = 0x01                       # constant in the captured block
    buf[DEV_COUNT_AT] = len(events)
    for i, (flags, usage) in enumerate(events):
        buf[DEV_EVENTS_AT + i * 2] = flags
        buf[DEV_EVENTS_AT + i * 2 + 1] = usage
    total = sum(buf[0:DEV_PAYLOAD_LEN - 2]) & 0xFFFF
    buf[DEV_PAYLOAD_LEN - 2] = (total >> 8) & 0xFF
    buf[DEV_PAYLOAD_LEN - 1] = total & 0xFF
    return bytes(buf)


def build_device_upload(macro):
    """The report 0x09 chunks to send, in order."""
    block = build_device_block(macro)
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

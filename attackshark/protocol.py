"""Attack Shark X3 (Beken 2.4G) HID configuration protocol.

Everything here was recovered by observing the vendor tool (X3.exe) talk to the
mouse, then re-deriving the encodings from labelled captures. Every builder in
this module is checked byte-for-byte against the captured corpus by
tests/test_protocol.py.

Framing
-------
Each setting group is its own HID *feature report*. Byte 0 is the report ID and
byte 1 is the total report length, so a report is self-describing::

    [0] report id   [1] total length   [2] profile (always 0x01)   ... payload

Integrity
---------
Two schemes are in use:

* Config blocks (0x04 / 0x05 / 0x08) end their payload with a 16-bit
  **big-endian sum** of every payload byte from index 3 up to the check field.
* Short commands (0x06 / 0x0c) carry each value as a ``(value, 0xFF - value)``
  complement pair instead.
"""
from __future__ import annotations

# ---------------------------------------------------------------- identity ---
VENDOR_ID = 0x1D57
PRODUCT_ID_24G = 0xFA60      # 2.4 GHz receiver
PRODUCT_ID_WIRED = 0xFA61    # USB cable
PRODUCT_IDS = (PRODUCT_ID_24G, PRODUCT_ID_WIRED)

# The configuration channel is a vendor collection on interface MI_02.
CONFIG_USAGE_PAGE = 0x000B
CONFIG_USAGE = 0x0000

# A second vendor collection carries unsolicited status (battery) reports.
STATUS_USAGE_PAGE = 0x000A
STATUS_REPORT_ID = 0x03
STATUS_KIND_BATTERY = 0x10

# ------------------------------------------------------------- report ids ----
REPORT_SENSOR = 0x04    # DPI table, LOD, ripple, angle snap, motion sync
REPORT_POWER = 0x05     # sleep / deep-sleep timers
REPORT_POLLING = 0x06   # polling rate
REPORT_BUTTONS = 0x08   # 18-slot button map
REPORT_COMMIT = 0x0C    # sent first in every apply burst

REPORT_LENGTH = {
    REPORT_SENSOR: 56,
    REPORT_POWER: 15,
    REPORT_POLLING: 9,
    REPORT_BUTTONS: 59,
    REPORT_COMMIT: 10,
}

# Offset of the 16-bit big-endian check field inside each config block.
CHECK_AT = {
    REPORT_SENSOR: 50,
    REPORT_POWER: 11,
    REPORT_BUTTONS: 57,
}

PROFILE_BYTE = 0x01

# ------------------------------------------------------------------ values ---
POLLING_RATES = {1000: 0x01, 500: 0x02, 250: 0x04, 125: 0x08}
POLLING_BY_CODE = {v: k for k, v in POLLING_RATES.items()}

LOD_1MM = 0x00
LOD_2MM = 0x01

DPI_STEP = 50
DPI_MIN = 50
DPI_MAX = 26000
DPI_SLOTS = 8

#: Button action codes confirmed by reassigning a button and diffing the block.
ACTION = {
    "off": 0x01,
    "left_click": 0x02,
    "right_click": 0x03,
    "middle_click": 0x04,
    "backward": 0x05,
    "forward": 0x06,
    "double_click": 0x07,
    "fire_button": 0x08,
    "scroll_up": 0x09,
    "scroll_down": 0x0A,
    "dpi_cycle": 0x0D,
    "dpi_up": 0x0E,
    "dpi_down": 0x0F,
    "easy_aim": 0x10,
    # multimedia submenu
    "media_player": 0x15,
    "next_track": 0x17,
    "play_pause": 0x18,
    "mute": 0x1A,
    "volume_up": 0x1B,
    # browser submenu
    "my_computer": 0x23,
    "browser_home": 0x25,
}
ACTION_NAME = {v: k for k, v in ACTION.items()}

#: Codes seen in the factory table but never produced by a menu selection.
UNMAPPED_ACTIONS = {0x0B, 0x0C, 0x3C}

#: A button can also send a keyboard chord: [0x11, modifier_mask, hid_keycode].
ACTION_SHORTCUT = 0x11

#: Standard HID keyboard modifier bits, as used in the second slot byte.
MOD_LCTRL, MOD_LSHIFT, MOD_LALT, MOD_LGUI = 0x01, 0x02, 0x04, 0x08
MOD_RCTRL, MOD_RSHIFT, MOD_RALT, MOD_RGUI = 0x10, 0x20, 0x40, 0x80
MODIFIERS = {
    "ctrl": MOD_LCTRL, "shift": MOD_LSHIFT, "alt": MOD_LALT, "win": MOD_LGUI,
    "lctrl": MOD_LCTRL, "lshift": MOD_LSHIFT, "lalt": MOD_LALT, "lwin": MOD_LGUI,
    "rctrl": MOD_RCTRL, "rshift": MOD_RSHIFT, "ralt": MOD_RALT, "rwin": MOD_RGUI,
}

#: HID Keyboard/Keypad usage page (0x07) codes, enough for any normal chord.
HID_KEYS = {}
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    HID_KEYS[_c] = 0x04 + _i
for _i, _c in enumerate("1234567890"):
    HID_KEYS[_c] = 0x1E + _i
for _i in range(1, 13):
    HID_KEYS[f"f{_i}"] = 0x3A + _i - 1
HID_KEYS.update({
    "enter": 0x28, "esc": 0x29, "backspace": 0x2A, "tab": 0x2B, "space": 0x2C,
    "-": 0x2D, "=": 0x2E, "[": 0x2F, "]": 0x30, "\\": 0x31, ";": 0x33,
    "'": 0x34, "`": 0x35, ",": 0x36, ".": 0x37, "/": 0x38, "capslock": 0x39,
    "printscreen": 0x46, "scrolllock": 0x47, "pause": 0x48, "insert": 0x49,
    "home": 0x4A, "pageup": 0x4B, "delete": 0x4C, "end": 0x4D, "pagedown": 0x4E,
    "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
})
HID_KEY_NAME = {v: k for k, v in HID_KEYS.items()}


def shortcut(combo: str):
    """'ctrl+shift+s' -> (0x11, modifier_mask, hid_keycode), ready for a slot.

    Verified against the vendor tool: its built-in Cut is ``11 01 1b``
    (Ctrl+X) and Screen Capture is ``11 0a 16`` (Shift+Win+S).
    """
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("empty shortcut")
    mods, key = 0, None
    for part in parts:
        if part in MODIFIERS:
            mods |= MODIFIERS[part]
        elif part in HID_KEYS:
            if key is not None:
                raise ValueError(f"more than one non-modifier key in {combo!r}")
            key = HID_KEYS[part]
        else:
            raise ValueError(f"unknown key {part!r} in {combo!r}")
    if key is None:
        raise ValueError(f"{combo!r} has no non-modifier key")
    return (ACTION_SHORTCUT, mods, key)


def describe_slot(trio):
    """Human-readable name for one 3-byte slot entry."""
    code, p1, p2 = trio
    if code == ACTION_SHORTCUT:
        names = [n for n in ("ctrl", "shift", "alt", "win")
                 if p1 & MODIFIERS[n]]
        names += [n for n in ("rctrl", "rshift", "ralt", "rwin")
                  if p1 & MODIFIERS[n]]
        return "+".join(names + [HID_KEY_NAME.get(p2, f"0x{p2:02x}")])
    return ACTION_NAME.get(code, f"0x{code:02x}")

#: Button-block slot index for each button as numbered in the vendor UI.
#: Slots 3/4/5/8..15 exist in the shared 18-slot table but are not wired to a
#: physical button on the X3; 16/17 carry the wheel defaults.
BUTTON_SLOT = {1: 0, 2: 1, 3: 2, 4: 6, 5: 7}
BUTTON_SLOTS = 18

LIGHT_MODES = {
    0: "off", 1: "static", 2: "breathing", 3: "neon", 4: "color_breathing",
    5: "color_static", 6: "mixed_breathing", 7: "rainbow_wave", 8: "lightning",
    9: "static_mixed", 10: "marquee", 11: "marquee2",
}


# ------------------------------------------------------------- primitives ----
def dpi_to_raw(dpi: int) -> int:
    """800 -> 15, 26000 -> 519. The wire value is ``dpi/50 - 1``."""
    if dpi < DPI_MIN or dpi > DPI_MAX:
        raise ValueError(f"dpi {dpi} outside {DPI_MIN}..{DPI_MAX}")
    if dpi % DPI_STEP:
        raise ValueError(f"dpi {dpi} is not a multiple of {DPI_STEP}")
    return dpi // DPI_STEP - 1


def raw_to_dpi(raw: int) -> int:
    return (raw + 1) * DPI_STEP


def apply_check(buf: bytearray, report_id: int) -> bytearray:
    """Write the 16-bit big-endian payload sum into the block's check field."""
    at = CHECK_AT[report_id]
    total = sum(buf[3:at]) & 0xFFFF
    buf[at] = (total >> 8) & 0xFF
    buf[at + 1] = total & 0xFF
    return buf


def verify_check(buf: bytes, report_id: int) -> bool:
    at = CHECK_AT[report_id]
    return ((buf[at] << 8) | buf[at + 1]) == (sum(buf[3:at]) & 0xFFFF)


def _blank(report_id: int) -> bytearray:
    n = REPORT_LENGTH[report_id]
    b = bytearray(n)
    b[0] = report_id
    b[1] = n
    b[2] = PROFILE_BYTE
    return b


# ---------------------------------------------------------------- builders ---
def build_polling(rate_hz: int) -> bytes:
    """0x06 - polling rate. The wire value is the 1000 Hz divider."""
    if rate_hz not in POLLING_RATES:
        raise ValueError(f"polling rate must be one of {sorted(POLLING_RATES)}")
    code = POLLING_RATES[rate_hz]
    b = _blank(REPORT_POLLING)
    b[3] = code
    b[4] = 0xFF - code
    return bytes(b)


def build_commit() -> bytes:
    """0x0c - leads every apply burst the vendor tool sends."""
    b = _blank(REPORT_COMMIT)
    b[2], b[3] = 0x01, 0xFE
    b[4], b[5] = 0x01, 0xFE
    return bytes(b)


def build_sensor(dpi_list, active_stage, *, lod=LOD_2MM, ripple=False,
                 angle_snap=False, motion_sync=False, colors=None,
                 enabled_mask=None) -> bytes:
    """0x04 - DPI table plus the four sensor toggles.

    ``dpi_list``     up to 8 DPI values (multiples of 50).
    ``active_stage`` zero-based index into ``dpi_list``.
    ``colors``       up to 8 (r, g, b) tuples for the stage indicator LED.
    """
    if not 1 <= len(dpi_list) <= DPI_SLOTS:
        raise ValueError(f"need 1..{DPI_SLOTS} dpi stages")
    if not 0 <= active_stage < len(dpi_list):
        raise ValueError("active_stage outside dpi_list")

    b = _blank(REPORT_SENSOR)
    b[3] = lod & 0xFF
    b[4] = 1 if ripple else 0
    b[5] = enabled_mask if enabled_mask is not None else (1 << len(dpi_list)) - 1
    b[6] = 1 if angle_snap else 0
    b[7] = 1 if motion_sync else 0

    for i, dpi in enumerate(dpi_list):
        raw = dpi_to_raw(dpi)
        b[8 + i] = raw & 0xFF          # low bytes  : slots 0..7
        b[16 + i] = (raw >> 8) & 0xFF  # high bytes : slots 0..7

    b[24] = active_stage + 1           # 1-based on the wire

    colors = list(colors or [])[:DPI_SLOTS]
    for i, (r, g, bl) in enumerate(colors):
        b[25 + i * 3] = r & 0xFF
        b[26 + i * 3] = g & 0xFF
        b[27 + i * 3] = bl & 0xFF

    b[49] = 0x01                       # constant in every observed capture
    return bytes(apply_check(b, REPORT_SENSOR))


def build_power(sleep_min: float, deep_sleep_min: int,
                key_response_ms: int = 4) -> bytes:
    """0x05 - idle timers and the debounce / key response time.

    ``sleep_min``        0.5 .. 30 in half-minute steps (stored as minutes*2).
    ``deep_sleep_min``   1 .. 60 whole minutes, split across two nibbles.
    ``key_response_ms``  2 .. 50 ms in 2 ms steps (stored as ms/2).
    """
    sleep_raw = int(round(sleep_min * 2))
    if not 1 <= sleep_raw <= 60:
        raise ValueError("sleep_min must be 0.5..30")
    if not 1 <= deep_sleep_min <= 60:
        raise ValueError("deep_sleep_min must be 1..60")
    debounce_raw = int(round(key_response_ms / 2))
    if not 1 <= debounce_raw <= 25:
        raise ValueError("key_response_ms must be 2..50")

    b = _blank(REPORT_POWER)
    b[3] = 0x00
    b[4] = (deep_sleep_min & 0xF0) | 0x03
    b[5] = ((deep_sleep_min & 0x0F) << 4) | 0x08
    b[6] = 0x00
    b[7] = 0x00
    b[8] = 0xFF
    b[9] = sleep_raw
    b[10] = debounce_raw
    return bytes(apply_check(b, REPORT_POWER))


def build_buttons(slots) -> bytes:
    """0x08 - the shared 18-slot button table.

    ``slots`` is an iterable of 18 action codes, or of 3-byte sequences when a
    slot carries parameters (macro / shortcut entries use the spare bytes).
    """
    slots = list(slots)
    if len(slots) != BUTTON_SLOTS:
        raise ValueError(f"need exactly {BUTTON_SLOTS} slots")
    b = _blank(REPORT_BUTTONS)
    for i, slot in enumerate(slots):
        trio = (slot, 0, 0) if isinstance(slot, int) else tuple(slot)
        if len(trio) != 3:
            raise ValueError("a slot must be an int or a 3-byte sequence")
        b[3 + i * 3: 6 + i * 3] = bytes(trio)
    return bytes(apply_check(b, REPORT_BUTTONS))


# ----------------------------------------------------------------- parsers ---
def parse_sensor(buf: bytes) -> dict:
    dpi = []
    for i in range(DPI_SLOTS):
        raw = (buf[16 + i] << 8) | buf[8 + i]
        dpi.append(raw_to_dpi(raw))
    mask = buf[5]
    return dict(
        lod_mm=1 if buf[3] == LOD_1MM else 2,
        ripple=bool(buf[4]),
        angle_snap=bool(buf[6]),
        motion_sync=bool(buf[7]),
        enabled_mask=mask,
        stage_count=bin(mask).count("1"),
        dpi=dpi,
        active_stage=buf[24] - 1,
        colors=[tuple(buf[25 + i * 3: 28 + i * 3]) for i in range(DPI_SLOTS)],
        check_ok=verify_check(buf, REPORT_SENSOR),
    )


def parse_power(buf: bytes) -> dict:
    return dict(
        sleep_min=buf[9] / 2,
        deep_sleep_min=(buf[4] & 0xF0) | (buf[5] >> 4),
        key_response_ms=buf[10] * 2,
        check_ok=verify_check(buf, REPORT_POWER),
    )


def parse_polling(buf: bytes) -> dict:
    return dict(
        rate_hz=POLLING_BY_CODE.get(buf[3]),
        code=buf[3],
        complement_ok=(buf[4] == 0xFF - buf[3]),
    )


def parse_buttons(buf: bytes) -> dict:
    slots = [tuple(buf[3 + i * 3: 6 + i * 3]) for i in range(BUTTON_SLOTS)]
    return dict(
        slots=slots,
        actions=[describe_slot(s) for s in slots],
        buttons={btn: describe_slot(slots[idx])
                 for btn, idx in sorted(BUTTON_SLOT.items())},
        check_ok=verify_check(buf, REPORT_BUTTONS),
    )


#: Open-circuit voltage -> state of charge for a single Li-ion cell.
#:
#: The mouse reports a voltage, not a percentage, so a curve is the only way to
#: get a usable figure. This one is anchored on a measurement rather than
#: invented: the device read 0x40 = 4.00 V while the vendor app displayed 90 %,
#: which is exactly where 4.00 V sits on a standard 1S discharge curve. The
#: rest of the points are that curve; they are an approximation, and
#: `percent_verified` stays False to say so.
#: Above this the reading cannot be the cell; it is the 5 V bus.
CELL_MAX_V = 4.35

LIION_CURVE = (
    (3.30, 0), (3.50, 8), (3.60, 20), (3.70, 40), (3.75, 50), (3.80, 62),
    (3.85, 72), (3.90, 80), (3.95, 86), (4.00, 90), (4.05, 94), (4.10, 96),
    (4.15, 98), (4.20, 100),
)


def volts_to_percent(volts):
    """State of charge for a cell voltage, piecewise linear over LIION_CURVE."""
    if volts <= LIION_CURVE[0][0]:
        return 0
    if volts >= LIION_CURVE[-1][0]:
        return 100
    for (v0, p0), (v1, p1) in zip(LIION_CURVE, LIION_CURVE[1:]):
        if v0 <= volts <= v1:
            span = v1 - v0
            frac = 0.0 if span == 0 else (volts - v0) / span
            return int(round(p0 + frac * (p1 - p0)))
    return 0


def parse_status(buf):
    """Decode a status input report from the 0x000A collection.

    Observed: ``03 10 40 01 09`` - report id, kind, then three bytes.

    Byte 2 is a **voltage in 1/16 V**, not a percentage. It reads 0x40 and
    barely moves, which is what a cell voltage does and a percentage does not.
    0x40 / 16 = 4.00 V, and the vendor app showed 90 % at that same reading -
    exactly where 4.00 V falls on a 1S Li-ion curve. So the voltage reading is
    corroborated by an independent source, and `percent` is derived from it
    through LIION_CURVE.

    The percentage is therefore an approximation of the cell's state of charge,
    not a figure the device sent; `percent_verified` is False to say so.
    `tools/battery_log.py` logs the byte across a charge/discharge cycle, which
    would let the curve be replaced with measured points.
    """
    if not buf or len(buf) < 3 or buf[0] != STATUS_REPORT_ID:
        return None
    if buf[1] != STATUS_KIND_BATTERY:
        return {"kind": buf[1], "raw": bytes(buf).hex(" ")}
    volts = round(buf[2] / 16.0, 2)
    # A 1S Li-ion cell tops out around 4.25 V, so anything above this is not
    # the cell being reported - it is the 5 V bus, i.e. the cable is in. Two
    # samples so far: 4.00 V with flags=1 on the dongle, 5.00 V with flags=0
    # while charging. The voltage is the sound part of that; whether flags is
    # the charge bit is a guess on two data points, so it is not used here.
    charging = volts > CELL_MAX_V
    return {
        "kind": "battery",
        "level_raw": buf[2],
        "volts": volts,
        "charging": charging,
        # no meaningful state of charge while the bus voltage is what we see
        "percent": None if charging else volts_to_percent(volts),
        "percent_verified": False,       # derived from a curve, not read
        "flags": buf[3] if len(buf) > 3 else None,
        "extra": buf[4] if len(buf) > 4 else None,
        "raw": bytes(buf).hex(" "),
    }


PARSERS = {
    REPORT_SENSOR: parse_sensor,
    REPORT_POWER: parse_power,
    REPORT_POLLING: parse_polling,
    REPORT_BUTTONS: parse_buttons,
}


def parse(buf: bytes):
    """Decode any report we understand; returns None for unknown report ids."""
    fn = PARSERS.get(buf[0])
    return fn(buf) if fn else None

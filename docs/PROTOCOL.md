# Attack Shark X3 — HID configuration protocol

Reverse engineered from `X3.exe` (Attack Shark X3 Mouse driver, build 2024‑01‑12)
by instrumenting its HID calls and diffing labelled captures. Every statement
marked **confirmed** is backed by captured packets in `captures/`, and
`tests/test_protocol.py` rebuilds 58 of the 60 unique captured packets
byte‑for‑byte from the encodings below (the two skipped are the chunks of an
*empty* macro block, which has no events to rebuild from).

---

## 1. Device identity

| | |
|---|---|
| Vendor ID | `0x1D57` |
| Product ID | `0xFA60` (2.4 GHz receiver) / `0xFA61` (USB cable) |
| Manufacturer string | *(empty)* |
| Product string | `Beken 2.4G Wireless Device` |
| Release | `0x0114` |

Both IDs are hard-coded in `X3.exe` at file offset `0x0074A6`:

```
c7 46 60  57 1d 00 00    mov [esi+0x60], 0x1D57   ; VID
c7 46 64  61 fa 00 00    mov [esi+0x64], 0xFA61   ; PID, wired
c7 46 68  57 1d 00 00    mov [esi+0x68], 0x1D57   ; VID
c7 46 6c  60 fa 00 00    mov [esi+0x6c], 0xFA60   ; PID, 2.4 GHz
```

The app loads `hiddriver_1.dll` and `hiddriver_2.dll` — one instance per link
type — and calls `Set_VIDPID(0x1D57, 0xFA60)` and `Set_VIDPID(0x1D57, 0xFA61)`
respectively. Both DLLs are thin wrappers over `HidD_SetFeature`.

### Interfaces

The composite device exposes six HID collections. Configuration happens on
exactly one of them:

| Interface | Usage page | Usage | Feature len | Role |
|---|---|---|---|---|
| MI_00 | `0001` | `0006` | – | boot keyboard |
| MI_01 | `0001` | `0002` | – | mouse |
| MI_02 COL01 | `0001` | `0080` | – | system control |
| MI_02 COL02 | `000C` | `0001` | – | consumer control |
| MI_02 COL03 | `000A` | `0000` | – | **status / battery** |
| **MI_02 COL04** | **`000B`** | **`0000`** | **262** | **configuration** |
| MI_03 | `0001` | `0006` | – | keyboard |

Open `MI_02 COL04` and use `HidD_SetFeature`. Buffers shorter than the declared
262 bytes are accepted — the vendor tool always sends the exact report length.

### Reads are not supported

`HidD_GetFeature` answers for no report ID. The vendor tool never reads
settings back either: it stores them in
`%APPDATA%\Attack SharkX3Mouse\ms_1\pro.data` and re-pushes the whole set on
every change. Any reimplementation must keep its own state the same way.

### Battery — the `0x000A` collection

The config channel answers nothing, but the collection on usage page `0x000A`
sends a 5-byte **input** report:

```
03 10 40 01 09
|  |  |  |  +-- unknown
|  |  |  +----- flags (always 0x01 observed)
|  |  +-------- battery level - see below
|  +----------- kind: 0x10 = battery
+-------------- report id
```

There is no `HidD_GetInputReport` or feature report here — the level can only be
*received*. In practice the mouse emits one shortly after the collection is
opened, so open / read-one / close behaves like an on-demand read.

**Byte 2 is read as a voltage in sixteenths of a volt**, so `0x40` is 4.00 V,
and the percentage is derived from that through a lithium discharge curve.

The reason is behavioural, and it is worth being honest about how thin it is:
the byte reads `0x40` and barely moves, which is what a cell voltage does and a
raw percentage does not. That is the whole argument. The competing reading -
that the byte is simply a percentage, so `0x40` is 64 % - has **not** been ruled
out by measurement.

The vendor software settles nothing. It reads a flat **100 %** in every capture
screenshot that shows its Power panel, against the same `0x40`, and its meter
looks like a static gradient image rather than a rendered level. 100 % matches
neither 4.00 V on the curve (~90 %) nor a raw `0x40` (64 %). It is consistent
only with the widely reported complaint that the vendor display does not track
the device at all.

This is the weakest link in the battery decode and everything downstream
inherits the uncertainty. [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) §1 sets out the
single discharge test that settles it. This driver reads the device directly.

---

## 2. Framing

Every setting group is a separate feature report. The first two bytes make a
report self-describing:

```
[0] report ID
[1] total report length in bytes
[2] profile index (always 0x01 in every capture)
[3..] payload
```

| Report | Length | Contents |
|---|---|---|
| `0x04` | 56 | DPI table, lift-off, ripple, angle snap, motion sync |
| `0x05` | 15 | sleep / deep-sleep timers |
| `0x06` | 9 | polling rate |
| `0x08` | 59 | 18-slot button map |
| `0x0C` | 10 | leads every apply burst |

When any setting changes, the tool sends the burst in this order:
**`0x0C` → `0x04` → `0x05` → `0x06` → `0x08`**.

### Integrity — two schemes

**Config blocks (`0x04`, `0x05`, `0x08`)** end their payload with a **16-bit
big-endian sum** of every payload byte from index 3 up to the check field:

```python
total = sum(buf[3:at]) & 0xFFFF
buf[at]     = total >> 8
buf[at + 1] = total & 0xFF
```

| Report | Summed range | Check field |
|---|---|---|
| `0x04` | `[3:50]` | `[50:52]` |
| `0x05` | `[3:11]` | `[11:13]` |
| `0x08` | `[3:57]` | `[57:59]` |

Verified against all 41 captured config packets, zero mismatches.

**Short commands (`0x06`, `0x0C`)** instead carry each value as a
`(value, 0xFF - value)` complement pair.

---

## 3. Report `0x04` — sensor and DPI  *(confirmed)*

```
off  size  field
  0   1    0x04
  1   1    0x38 (56)
  2   1    0x01   profile
  3   1    lift-off distance: 0x00 = 1 mm, 0x01 = 2 mm
  4   1    ripple control: 0/1
  5   1    DPI stage enable bitmask (bit i = stage i); 0x3F = 6 stages
  6   1    angle snap: 0/1
  7   1    motion sync: 0/1
  8   8    DPI low byte,  slots 0..7
 16   8    DPI high byte, slots 0..7
 24   1    active DPI stage, 1-based
 25  24    8 x RGB, the DPI stage indicator colour
 49   1    0x01 (constant in every capture; meaning unknown)
 50   2    16-bit BE check
 52   4    zero padding
```

**DPI encoding.** `raw = dpi / 50 - 1`, split little-endian across the two
tables. 26000 DPI → `raw = 519 = 0x0207` → low byte `0x07` in slot 5, high byte
`0x02` in slot 5.

Factory table `0f 1f 2f 3f 7f 07` + high `00 00 00 00 00 02` decodes to
800 / 1600 / 2400 / 3200 / 6400 / 26000.

Default stage colours: red, green, blue, yellow, cyan, magenta, orange
(`ff4000`), white.

Each toggle was confirmed by flipping it alone and observing a single byte
change plus the matching check delta.

---

## 4. Report `0x05` — power management  *(confirmed)*

```
off  size  field
  0   1    0x05
  1   1    0x0F (15)
  2   1    0x01   profile
  3   1    0x00
  4   1    (deep_sleep_min & 0xF0) | 0x03
  5   1    ((deep_sleep_min & 0x0F) << 4) | 0x08
  6   2    0x00 0x00
  8   1    0xFF
  9   1    sleep_minutes * 2      (1..60  =>  0.5..30 min)
 10   1    key response time / 2  (1..25  =>  2..50 ms)
 11   2    16-bit BE check
 13   2    zero padding
```

**Deep sleep** is a whole number of minutes (1..60) split across the *high*
nibbles of two bytes, with constant low nibbles `3` and `8`:

| minutes | `[4]` | `[5]` |
|---|---|---|
| 1 | `03` | `18` |
| 10 | `03` | `a8` |
| 18 | `13` | `28` |
| 30 | `13` | `e8` |
| 60 | `33` | `c8` |

**Sleep** is byte `[9]` in half-minutes: `0x01` → 0.5 min, `0x12` (18) → 9.0 min,
`0x3C` (60) → 30 min. Confirmed against the UI label at each slider position.

**Key response time** (the vendor's debounce slider) is byte `[10]`, also in
2 ms units: `0x02` → 4 ms, `0x0D` (13) → 26 ms, `0x19` (25) → 50 ms. This byte
looked like a constant until the debounce slider was swept, which is a good
argument for varying every control before declaring a field fixed.

---

## 5. Report `0x06` — polling rate  *(confirmed)*

```
06 09 01 VV CC 00 00 00 00      CC = 0xFF - VV
```

`VV` is the divider from 1000 Hz:

| Rate | `VV` | `CC` | Vendor UI label |
|---|---|---|---|
| 1000 Hz | `01` | `fe` | E-sports |
| 500 Hz | `02` | `fd` | Gaming |
| 250 Hz | `04` | `fb` | Office |
| 125 Hz | `08` | `f7` | Power Saving |

---

## 6. Report `0x08` — button map  *(structure confirmed; codes partly mapped)*

```
off  size  field
  0   1    0x08
  1   1    0x3B (59)
  2   1    0x01   profile
  3  54    18 slots x 3 bytes: [action, param, param]
 57   2    16-bit BE check
```

The 18-slot table is shared across models in this OEM family; the X3 wires only
five of them to physical buttons.

| Vendor UI button | Slot | Byte offset | Factory action |
|---|---|---|---|
| 1 Left Click | 0 | 3 | `0x02` left_click |
| 2 Right Click | 1 | 6 | `0x03` right_click |
| 3 Middle Button | 2 | 9 | `0x04` middle_click |
| 4 Forward | 6 | 21 | `0x06` forward |
| 5 Backward | 7 | 24 | `0x05` backward |

Slot→button mapping was established by reassigning button 5 in the UI and
observing byte 24 change. Slots 3/4/5/8..15 hold `0x0D`, `0x3C`, `0x0F`, `0x3C`
and `0x01` from the factory; slots 16/17 hold `0x0A`/`0x09` (wheel down/up).

### Action codes  *(confirmed by reassignment)*

| Code | Action | Code | Action |
|---|---|---|---|
| `0x01` | button off | `0x0E` | DPI + |
| `0x02` | left click | `0x0F` | DPI − |
| `0x03` | right click | `0x10` | easy aim |
| `0x04` | middle click | `0x11` | keyboard shortcut *(see below)* |
| `0x05` | backward | `0x15` | media player |
| `0x06` | forward | `0x17` | next track |
| `0x07` | double click | `0x18` | play / pause |
| `0x08` | fire button | `0x1A` | mute |
| `0x09` | scroll up | `0x1B` | volume + |
| `0x0A` | scroll down | `0x23` | my computer |
| `0x0D` | DPI cycle | `0x25` | browser home |

The DPI codes explain the factory table: slot 3 holds `0x0D` (DPI cycle) and
slot 5 holds `0x0F` (DPI −).

### Keyboard shortcuts — action `0x11`

A slot set to `0x11` uses its two spare bytes as a standard HID chord:

```
[0x11] [modifier mask] [HID keyboard usage]
```

The modifier mask is the usual HID keyboard bitfield — `0x01` LCtrl, `0x02`
LShift, `0x04` LAlt, `0x08` LGUI, and `0x10`/`0x20`/`0x40`/`0x80` for the right
hand side. The third byte is a usage from HID page `0x07` (`a` = `0x04`,
`1` = `0x1E`, `F1` = `0x3A`, …).

Confirmed against the vendor tool's own built-in entries:

| Vendor menu entry | Bytes | Meaning |
|---|---|---|
| Shortcut → Cut | `11 01 1b` | Ctrl + X |
| Shortcut → Screen Capture | `11 0a 16` | Shift + Win + S |

So the whole Shortcut submenu, and the "Assign A Shortcut" dialog, are just
this one encoding. `protocol.shortcut("ctrl+shift+s")` builds it.

**Still unmapped:** `0x0B`, `0x0C`, `0x3C` (factory values in unwired slots),
the remaining multimedia/browser entries (the ranges are clearly `0x15`–`0x1C`
and `0x1D`–`0x26`, but only the listed ones were sampled), and Select A Macro.

---

## 7. Report `0x0C` — apply burst header  *(structure only)*

```
0c 0a 01 fe 01 fe 00 00 00 00
```

Two `(value, 0xFF - value)` complement pairs, both carrying `0x01`. Sent first
in every burst and never seen with any other value, so its parameters could not
be varied from the UI. Most likely a profile select or "begin config" marker.

---

## 8. Report `0x09` — macro upload  *(confirmed)*

Captured in `captures/macro_assign.jsonl` by assigning a macro to button 5 in
the vendor UI. The macro used was "tap `C` seven times", the same one stored in
`macro.data` below.

A macro goes to the device as a **128-byte block sent in 64-byte chunks**:

```
off  size  field
  0   1    report ID, 0x09
  1   1    used bytes in this chunk, including this 4-byte header
  2   1    block ID, 0x08 = macro
  3   1    chunk index, 0-based
  4   n    payload, up to 60 bytes
```

Three chunks carry the block — 60 + 60 + 8 = 128 bytes:

```
09 40 08 00  00 00 00 00 01 00 00 ... 00 0e 01 06 81 06 01 06 81 06 ... 00
09 40 08 01  00 00 00 ... 00                          (entirely zero)
09 0c 08 02  00 00 00 00 00 00 03 f1
```

`0x40` = 64 and `0x0c` = 12 are the used lengths, so the last chunk carries
only 8 payload bytes. Inside the reassembled 128-byte block:

```
off  size  field
  0   1    macro slot            (see below - 0 does NOT work)
  1   3    zero
  4   1    0x01, constant in every capture
  5   20   zero
 25   1    event count            (0x0e = 14, i.e. 7 press/release pairs)
 26   2*n  events                 (spills into chunk 1 past 17 events)
126   2    checksum, 16-bit big-endian sum of bytes 0..125
```

The event table is a flat run inside the 128-byte block, not a per-chunk
structure: a 20-event macro was observed with its last three events in chunk 1,
so chunking is a transport detail applied after the block is laid out.

### The slot byte — `0` means "unset"  *(confirmed the hard way)*

Byte 0 is the macro slot, and **a block uploaded to slot 0 never plays.** The
write succeeds, `HidD_SetFeature` returns true, the checksum is accepted, and
the bound button then silently falls back to its default action as though no
macro were assigned.

This is not obvious from the traffic, because the vendor app writes Macro1 to
slot 0 on every startup. Replaying that capture byte-for-byte — same bytes,
same order, same checksums — reproduces a configuration that does not work,
and it is easy to conclude from it that device macros are broken. They are not:
the vendor uses slot `2` for macros it actually assigns, and slot 2 plays.

Verified: 7 × `c` uploaded to slot 2 and bound to button 5 produced 52 `c`
keystrokes attributed to `HID\VID_1D57&PID_FA60&MI_00` — the mouse's own
keyboard collection — with `LLKHF_INJECTED` clear on every one. The same block
at slot 0 produced nothing. `attackshark/macro.py` defaults to `DEV_SLOT = 2`.

This matters beyond correctness: a macro played by the firmware is genuine
hardware input. It carries no injection flag, needs no driver, no signed
kernel code and no reboot, and is unaffected by Secure Boot — because the mouse
really did send those reports.

Each event is **two bytes**, `[flags, hid_usage]`:

| flags | meaning |
|---|---|
| `0x01` | press |
| `0x81` | release (bit 7 set) |

So `01 06 81 06` is press-then-release of HID usage `0x06`, the `C` key,
repeated seven times.

Checksum check on the captured block: `0x01 + 0x0e + 7 × (0x01+0x06+0x81+0x06)`
= `0x3F1`, and the block ends `03 f1`. Same scheme as every other config block
(§2).

`attackshark/macro.py` builds these (`build_device_upload`), and
`button_slot_entry()` produces the button-map entry that plays one: action
`0x12` with the macro index, captured as `12 00 08` in the same session's
report `0x08`.

### What the firmware cannot store  *(tested, not assumed)*

Two bytes per event is the whole budget, and it is spent on flags and a HID
usage. The first version of this section inferred the consequences from a
single captured macro; they have since been tested, because the answer decides
whether real mouse movement can come from the hardware at all.

**No movement.** Four independent lines of evidence:

1. The vendor's macro editor has exactly three columns. From `res/lan.xml`:
   `macro_list_header_key_text` = "Key", `..._action_text` = "Action",
   `..._delay_text` = "Delay(ms)". There is no movement column and no
   mouse-button column. ("Move Up"/"Move Down" reorder the list.)
2. Every macro block captured — five distinct ones, including a freshly
   recorded macro — contains only types `0x01`/`0x81` with keyboard usages.
3. `0xF9` was tested directly. The Attack Shark v4 driver (a *different*
   product, a keyboard) decodes mouse movement as a 4-byte record
   `[0xF9, delay, dx, dy]` with signed `dx`/`dy`. Uploading that to the X3
   produced no movement, and the bound button reverted to its default action —
   the firmware parsed the block, rejected the unknown type, and discarded the
   whole macro. That rejection is itself a useful signal: *macro plays* versus
   *button falls back* is a clean pass/fail for any candidate opcode.
4. Neither `X3.exe` nor `hiddriver_1.dll` contains any test, factory or
   calibration strings, and no report ID beyond the six documented here.

So movement from a device macro is not available on this mouse. It is a host
concern, which is why `docs/DRIVER.md` exists.

**No per-event delay.** The 20 zero bytes at offset 5 are the only unexplained
space in the block and the only plausible home for timing, but nothing observed
ever set them — the vendor UI has a `Delay(ms)` column and stores delays in
`macro.data`, yet drops them on upload. Playback timing is presumably the key
response time (§4).

**Mouse buttons: not sampled.** The vendor editor does not offer them, so no
capture exists. The usage byte may or may not accept button codes; a sweep
using the pass/fail signal from point 3 would settle it.

`macro.py`'s `device_support()` reports which of these a given macro trips, and
falls back to host playback when it trips any.

### Macro file format

`%APPDATA%\Attack SharkX3Mouse\ms_1\macro.data` — the vendor tool's own store,
which uses a *different*, roomier encoding than what it uploads:

```
off  size  field
  0   52   macro name, UTF-16LE, zero padded
 52   4    event count, little-endian
 56   4*n  events
```

Each event is 4 bytes: `[hid_usage, action, delay_lo, delay_hi]` where action
`1` = press and `2` = release, and the delay is milliseconds. The stored sample
`06 01 0a 00 / 06 02 0a 00 ...` is "tap HID usage `0x06` seven times with 10 ms
between transitions".

Note that the delays present here are **discarded on upload** — the device
block above has no field for them. The vendor tool keeps more state than the
mouse can hold, exactly as it does for every other setting (§1).

---

## 9. Feature set exposed by the vendor UI

From `res/lan.xml` and `res/MS/MS_1/*.xml` — useful for knowing what else the
firmware supports even where the wire encoding is not yet mapped:

- 8 DPI stages with per-stage colour (the X3 ships 6 enabled)
- 4 polling rates, 18 button slots
- lift-off 1/2 mm, key response time (2–50 ms), ripple control, angle snap,
  motion sync
- sleep and deep-sleep timers, move-wake
- 12 lighting modes: LED Off, Static, Breathing, Neon, Color Breathing, Color
  Static, Mixed Breathing, Rainbow Wave, Lightning, Static Mixed Color,
  Marquee, Marquee 2 — **the X3 build of the UI never shows the lighting page,
  so no lighting packet was observed.** Brightness, speed and a 16-colour
  palette exist in the layout.
- battery percentage and charging state (shown in the UI; the source is an
  input report on another collection, not the config channel)

---

## 10. Confidence summary

| Area | Status |
|---|---|
| Device IDs, interface, transport | confirmed |
| Framing, report lengths, both checksum schemes | confirmed, 32/32 packets round-trip |
| Polling rate | confirmed, all 4 values |
| DPI table, active stage, stage colours | confirmed |
| Lift-off, ripple, angle snap, motion sync | confirmed, each isolated |
| Sleep / deep sleep / key response time | confirmed against UI labels |
| Button block structure + 21 action codes | confirmed |
| Keyboard-shortcut encoding (`0x11` + mods + usage) | confirmed against vendor entries |
| Remaining multimedia / browser codes | partially sampled |
| Report `0x0C` semantics | open |
| Battery level (`0x000A` input report) | confirmed, reproducible |
| Status bytes 3 and 4 | unknown |
| Macro upload (`0x09`), chunking, block layout, checksum | confirmed, 5 blocks |
| Macro event encoding (`[flags, usage]`, `0x01`/`0x81`) | confirmed |
| Macro slot byte; slot 0 is inert | confirmed, playback verified |
| Device macro playback (firmware, no injection flag) | confirmed on hardware |
| Macro event delays inside the device block | not encoded — see §8 |
| Movement inside a device macro | tested and absent — see §8 |
| Mouse buttons inside a device macro | not sampled |
| Lighting | not observed |

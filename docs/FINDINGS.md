# Attack Shark X3 — findings

Everything this project established about the device, how it was established,
and what is still open. The wire format itself is in
[`PROTOCOL.md`](PROTOCOL.md); this is the account of what was learned, what was
tried and failed, and what someone picking it up should do next.

What is *broken* rather than merely unknown — the battery estimate, the driver
that has never been loaded — is in [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md).

**Found a bug, or decoded something here marked unknown?**
Email **prodmutant@gmail.com** — especially for the open items in §7.

---

## 1. What the device is

| | |
|---|---|
| Vendor / product | `1D57:FA60` (2.4 GHz receiver), `1D57:FA61` (cable) |
| Product string | `Beken 2.4G Wireless Device` |
| Config channel | HID feature reports on `MI_02 COL04`, usage page `0x000B`, 262 bytes |
| Status channel | input reports on `MI_02 COL03`, usage page `0x000A`, 5 bytes |
| Firmware macro engine | yes, keyboard events only |

The VID/PID pair is hard-coded in `X3.exe` at file offset `0x0074A6`, which is
how the identity was confirmed rather than guessed.

**The mouse answers no reads.** `HidD_GetFeature` returns nothing for every
report ID. The vendor tool does not read settings back either — it keeps them
in `%APPDATA%` and re-pushes the whole set on every change. Any reimplementation
must do the same, and must accept that it can never learn the device's actual
state. This single fact shapes everything else here.

## 2. How it was worked out

Not by disassembly. The method that made it tractable:

1. Drive **one** control at a time in the vendor UI, with a Frida agent logging
   every `HidD_SetFeature` across all three layers the app calls through
   (`hiddriver_*.dll`, `hidapi.dll`, `hid.dll`).
2. Diff the captures. Each toggle moves exactly one byte, so the field map
   falls out directly.
3. Rebuild every captured packet from the decoded fields and require the bytes
   to match. `tests/test_protocol.py` does this for **58 of the 60** unique
   captured packets; the two skipped are the chunks of an *empty* macro, which
   has no events to rebuild from.

`tools/exercise.py` automates step 1 — it spawns the app, drives its UI from a
JSON plan, and labels every captured packet with the action that caused it.

The lesson worth carrying: a field that never changes is not a constant, it is
a control you have not moved yet. Byte `[10]` of report `0x05` looked fixed
until the debounce slider was swept; it is the key response time.

## 3. What is decoded and working

Everything the vendor UI exposes, plus two things it does not:

- **DPI**: 8 stages, 50–26000, `raw = dpi/50 - 1` split little-endian across
  two tables. Active stage, per-stage indicator colour.
- **Polling**: 1000/500/250/125 Hz as a divider with a complement byte.
- **Sensor**: lift-off distance, ripple control, angle snap, motion sync.
- **Power**: sleep (half-minutes), deep sleep (a whole number of minutes split
  across the high nibbles of two bytes), key response time (2 ms units).
- **Buttons**: an 18-slot table of which the X3 wires five, with 21 decoded
  action codes **and arbitrary keyboard shortcuts** (`ctrl+shift+s` and
  friends) via action `0x11`.
- **Macros**: upload into the mouse's own firmware, report `0x09`.
- **Battery**: read from the device, which the vendor app does not do properly
  — see §5.

Two integrity schemes, both confirmed: config blocks end with a 16-bit
big-endian sum of the payload; the two short commands use `(value, 0xFF-value)`
complement pairs instead.

## 4. Device macros: the slot byte

The firmware macro engine works, and this project drives it. Verified on
hardware: 52 keystrokes attributed to `HID\VID_1D57&PID_FA60&MI_00` — the
mouse's **own keyboard collection** — with `LLKHF_INJECTED` clear on every one.
No driver, no kernel code, no reboot, and Secure Boot is irrelevant, because
the mouse really did send them.

Getting there cost a day, for one byte:

> **Macro slot 0 is inert.** A block uploaded there is accepted,
> `HidD_SetFeature` returns true, the checksum is fine — and it never plays.
> The bound button silently falls back to its default action.

This is a trap rather than a bug, because the vendor app writes Macro1 to slot
0 on every startup. Replaying its captured traffic byte-for-byte therefore
reproduces a configuration that does not work, and the obvious conclusion — "the
firmware cannot play macros" — is wrong. The vendor uses slot **2** for macros
it actually assigns. `macro.py` defaults to `DEV_SLOT = 2`.

Anyone reverse-engineering a sibling device in this OEM family should check
this first.

### What the firmware will not store

A stored event is **two bytes**, `[flags, HID usage]`, `0x01` press and `0x81`
release. That is the whole budget, and the consequences were **tested, not
inferred**:

- **No movement.** Four independent lines of evidence:
  1. The vendor's macro editor has exactly three columns — `Key`, `Action`,
     `Delay(ms)` — from `res/lan.xml`. No movement column, no mouse buttons.
  2. Five distinct captured blocks, including a freshly recorded macro, contain
     only types `0x01`/`0x81` with keyboard usages.
  3. `0xF9` was uploaded and **rejected**. Attack Shark's *keyboard* driver (a
     different product, an Electron app whose JavaScript is readable on disk)
     decodes mouse movement as a 4-byte record `[0xF9, delay, dx, dy]`. Sending
     that shape to the X3 plays nothing and the bound button reverts to its
     default, so the firmware parsed the block and discarded it.
  4. Neither `X3.exe` nor `hiddriver_1.dll` contains any test, factory or
     calibration strings, and no report ID beyond the six documented.
- **No per-event delay.** The vendor UI has a `Delay(ms)` column and stores
  delays in `macro.data`, then drops them on upload. The 20 zero bytes at
  offset 5 are the only unexplained space and nothing observed ever set them.
- **Mouse buttons: not sampled.** The editor does not offer them, so no capture
  exists. See §7.

That rejection behaviour is itself useful: **macro plays** versus **button
falls back** is a clean pass/fail signal for testing any candidate opcode,
without needing to see what it does.

## 5. Battery

The vendor app shows a figure that does not track — it reads a flat `100 %` in
every capture screenshot that shows its Power panel, and its meter looks like a
static gradient image rather than a rendered level. This project reads the
device instead. The decode landed here:

1. *"Byte 2 is a voltage in 1/16 V."* `0x40`/16 = **4.00 V**, a sensible
   resting voltage for a 1S cell. The argument is behavioural: the byte barely
   moves, which is what a voltage does and a percentage does not.
2. *"Byte 2 is a percentage."* Then `0x40` is 64 %. **This has not been ruled
   out.** It is less consistent with how little the byte moves, and that is the
   only thing against it. An earlier version of this section claimed the vendor
   app had been seen at 90 %, which would have decided it in favour of (1); no
   screenshot supports that and the claim has been withdrawn. One full
   discharge log settles this — see
   [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) §1.
3. *"Then 5.00 V means a very full battery."* No — `0x50` appeared with the
   cable in. **No lithium cell reaches 5 V**; that is the USB bus. Anything
   above 4.35 V is now reported as *charging* rather than as a percentage.
   (Under reading (2) that same byte would be an unremarkable 80 %, which is
   another way of seeing what rests on the choice.)

Then two faults that a decode cannot fix, because the device lies. It
intermittently emits a **placeholder**:

```
03 10 03 00 ff        ->  0.19 V, trailing byte 0xFF
```

Taking the first report off the collection without checking meant that got
shown as **0 %**, which looks exactly like a flat battery and is the opposite
of the truth. `read_status()` now keeps reading until a plausible report
arrives; a cell that had genuinely reached 0.19 V would be destroyed, so
anything that low is the device saying "nothing to report".

It also emits a spurious **`03 10 50 00 0c`** — 5.00 V, the bus voltage — while
running on the dongle with nothing plugged in. That one cannot be rejected by a
validity check, because 5 V is a perfectly legal reading; it simply was not
true at that moment, and a single frame was enough to flip the display to
"charging". The fix is what you do with any noisy sensor: `read_status()` takes
several reports and returns the most common level, so one bad frame cannot move
the display.

**Known limitation, and it is the hardware's:** the voltage arrives in
sixteenths of a volt, so between an empty cell (3.30 V) and a full one
(4.20 V) there are only about **fifteen values the device can ever send**. The
percentage moves in roughly 5-point steps and then sits still for hours. That
is resolution, not a stalled reading, and the interface shows the measured
voltage beside the percentage so it is visible rather than mysterious.

**The third fault was not the decode at all.** The mouse stops sending status
reports once it sleeps: with the profile's sleep timer at 0.5 min, 9 read
attempts over 25 seconds returned nothing while it sat idle, and every earlier
successful read had happened while it was awake. That is why the reading seemed
to work sometimes and not others. Writing to the config channel does not wake
it — the dongle accepts the packet, the mouse stays asleep — so there is no way
to force a reading; the device has to be moved.

The interface therefore keeps the last known reading with its age, dims it past
three minutes, and the tooltip names the sleep setting responsible rather than
leaving an old number looking like a stuck one.

The status collection's input report is 5 bytes and all five are read — there
is no finer figure hiding in it, and no other collection carries one.

## 6. Movement, and why there is a kernel driver

`SendInput` is detectable two ways, and no amount of realism in the trajectory
changes either: a low-level hook sees `LLMHF_INJECTED`, and Raw Input reports a
**null device handle** instead of a device. The question being asked is not
"does this look like a hand" but "did this come from a device".

The mouse cannot answer it for movement (§4). So `driver/asxfilter/` is a KMDF
filter on the X3's own devnode, between `mouhid` and `mouclass`. It intercepts
`IOCTL_INTERNAL_MOUSE_CONNECT` to capture `MouseClassServiceCallback`, then
emits movement by *calling that function* — same call, arguments and IRQL that
`mouhid` uses for a physical report.

It builds, links against the inbox KMDF 1.31, catalogues and test-signs, and
its user/kernel ABI is covered by `tests/test_kdriver.py`. **It has never been
observed running**, because Secure Boot is enabled on the development machine
and `bcdedit` refuses to set `testsigning` while it is:

```
The value is protected by Secure Boot policy and cannot be modified or deleted.
```

`docs/DRIVER.md` §9 is explicit about what it does and does not defeat. The
short version: it is off by default and most people should leave it that way,
because test signing is itself trivially detectable and a custom filter on the
mouse stack is a louder signal than the flag it removes.

## 7. Still open — the useful work

Ordered by how tractable each looks.

1. **Mouse buttons inside a device macro.** Never sampled, because the vendor
   editor does not offer them. The pass/fail signal from §4 makes this cheap to
   test: upload a macro whose event type is a candidate, bind it, press, and
   watch whether the macro plays or the button falls back. On-device rapid fire
   would be a real feature. `tools/probe_device_macro.py` already does the
   upload, bind and watch.
2. **The delay encoding.** 20 unexplained zero bytes at offset 5 of the macro
   block, and the vendor stores delays but discards them. If they are encoded
   anywhere, that is where.
3. **Lighting.** `res/lan.xml` defines **12 lighting modes** — LED Off, Static,
   Breathing, Neon, Color Breathing, Color Static, Mixed Breathing, Rainbow
   Wave, Lightning, Static Mixed Color, Marquee, Marquee 2 — plus brightness,
   speed and a 16-colour palette. **The X3 build of the vendor UI never shows
   the lighting page**, so nothing could be captured. The definitions exist;
   the traffic does not. A sibling model's software may show that page and
   produce the packets.
4. **Status bytes 3 and 4.** Byte 3 reads `1` on the dongle and `0` with the
   cable in, which looks like a power-source bit — but that is two samples, so
   `parse_status` infers charging from the voltage instead, which is physics.
   Byte 4 has been seen as 7, 8, 9 and 12 and is unexplained.
   `tools/battery_probe.py` prints transitions and logs to
   `captures/battery.csv`; **plug the cable in and out while it runs** and byte
   3 becomes a decoded field rather than a guess.
5. **Report `0x0C`.** Two `(value, 0xFF-value)` pairs, both carrying `0x01`,
   sent first in every burst and never seen with any other value. Probably a
   profile select or a "begin config" marker. Its parameters could not be
   varied from the UI.
6. **The remaining multimedia and browser action codes.** Partially sampled.
7. **A discharge curve from the hardware.** `tools/battery_log.py` samples into
   `captures/battery.csv`. Left running across a full charge cycle it would
   replace the generic Li-ion curve with measured points for this cell.

### What was tried and did not work

Recorded so nobody spends the time twice:

- **Blind-probing undocumented report IDs.** Nothing in either vendor binary
  references a test or calibration mode, and a wrong write to an unknown ID can
  land on a bootloader or flash command with no warning. Low expected value,
  real risk of a brick.
- **Getting movement out of the firmware.** Four independent lines of evidence
  say it is not there (§4).
- **A native desktop application.** Tkinter cannot render this interface — no
  letter-spacing, no gradients, no anti-aliased shapes, no control over font
  weight. A WebView2 host renders it exactly, but that is Edge's engine, so the
  result is a chromeless web app rather than a native one. Both were removed.
  The web interface is the interface; anything else should talk to the JSON API
  in `server.py`.

## 8. What this does better than the vendor software

- **Battery is actually read from the device**, with the placeholder report
  rejected and the measured voltage shown beside the derived percentage. The
  percentage is an estimate and is labelled as one — see
  [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) §1 for exactly how far to trust it.
- **Arbitrary keyboard shortcuts** on any button, not a fixed action list.
- **Macros that run on the mouse**, with the interface telling you *where* each
  macro will run before you save it — a key-only macro needs nothing running
  afterwards, one with movement needs the app open.
- **The protocol is documented**, so the mouse outlives the software. Every
  claim is backed by captured packets in `captures/`, and the test suite
  rebuilds them byte-for-byte.
- **No third-party dependencies.** The HID transport is `ctypes` over
  `hid.dll`/`setupapi.dll`; the interface is served by `http.server`.
- **It does not lose your settings.** Several things write the state file and
  each used to save it wholesale, so the last writer won for every key rather
  than the ones it touched. That destroyed a macro during development.
  `save()` now merges under a cross-process lock, with
  `tests/test_state.py` as the regression.
- **The mouse in the interface is the real outline**, traced from a reference
  drawing by `tools/trace_outline.py` rather than drawn by eye.

## 9. Safety

Writes go to the mouse's configuration flash. Everything sent is byte-identical
in shape to the vendor tool's traffic, and `captures/` is a known-good
reference that `tools/restore_original.py` can re-send. The vendor tool remains
installed and will happily overwrite anything set here.

The kernel filter is the part that can actually hurt, and `docs/DRIVER.md` §10
has the recovery path: the service is demand-start and not boot-critical, so
booting with the dongle unplugged loads no filter at all.

## 10. Legal

Interoperability research on hardware owned by the author, using only
observation of the vendor software's own traffic and its shipped resource
files. No vendor code is included or redistributed.

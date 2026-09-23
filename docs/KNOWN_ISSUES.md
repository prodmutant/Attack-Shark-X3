# Known issues and unfinished work

What is wrong with this driver, what is missing from it, and what someone would
have to do to fix each thing. This is deliberately separate from
[`FINDINGS.md`](FINDINGS.md), which is the research record: that document says
what was *learned*, this one says what is still *broken*.

Nothing here is hidden behind a caveat elsewhere. If a number on screen is a
guess, it is listed below as a guess.

**Found something, or fixed something?** Email **prodmutant@gmail.com**.

---

## 1. The battery percentage is an estimate, and it is not reliably correct

This is the biggest known defect and the one most likely to be noticed. The
percentage shown in the header is **derived**, not reported. The device never
sends a percentage. The interface marks it as unverified for that reason, but
the short version is: treat it as a rough band, not a figure.

### What you will actually see

- The percentage sits on the same number for hours, then jumps several points
  at once.
- It can differ from what the vendor software shows for the same mouse at the
  same moment.
- It occasionally reads high or low right after the mouse wakes.
- With nothing plugged in, it has been seen to claim it is charging.
- After the mouse sleeps, the reading stops updating entirely and goes stale.

### Why, cause by cause

**a. The resolution is about fifteen steps, in hardware.** Byte 2 of the status
report is a single byte in sixteenths of a volt. Across the usable range of a
1S lithium cell — roughly 3.30 V empty to 4.20 V full — that is only about
fifteen distinct values the device can *ever* send. The percentage therefore
moves in visible jumps and then sits still. That is not a stalled reading and
no amount of software can smooth it into a true 1 % figure; the information is
not in the report. The interface exposes `percent_step` so it can say how much
one hardware step is worth at the current level.

**b. The curve is generic, not measured.** `LIION_CURVE` in `protocol.py` is a
textbook 1S lithium discharge curve. The real cell in this mouse has its own
curve, its own internal resistance, and sags under load — a mouse polling at
1000 Hz reads lower than the same mouse idle at the same true charge. So even
where the voltage is exactly right, the percentage it maps to can be off by
several points, and most of that error lands in the middle of the range where
the curve is flattest.

**c. Nothing has actually confirmed what byte 2 means.** The byte reads
`0x40`. If it is a voltage in sixteenths, that is 4.00 V, which maps to about
90 %. If it is a raw percentage, it is 64 %. Both are plausible on their face,
and the decode chose voltage on a purely behavioural argument: the byte barely
moves, which is what a cell voltage does and a percentage does not.

That argument is not nothing, but it is all there is. **The vendor software
does not corroborate it.** Its Power panel reads a flat `100 %` in every
capture screenshot that shows it, against the same `0x40`, and the meter
appears to be a static gradient image rather than a rendered level — 100 %
matches neither 4.00 V on the curve nor a raw `0x40`, so it is evidence only
that the vendor display does not track the device. An earlier version of this
project's notes claimed the vendor app had been seen at ~90 %, which would have
corroborated the voltage reading; no screenshot supports it and the claim has
been withdrawn. This is the single weakest link in the whole battery decode and
everything downstream of it inherits the uncertainty.

**d. The device emits frames that are not measurements.** Two kinds have been
seen with the level rock steady either side:

```
03 10 03 00 ff     0.19 V under the voltage reading - a placeholder
03 10 50 00 0c     5.00 V under the voltage reading - the bus, not the cell
```

The first is easy to reject: a lithium cell that reached 0.19 V would be
destroyed. The second is not, because 5.00 V is a perfectly legal value — it
just was not true at that moment, and it is why "charging" once appeared with
no cable attached. Note that under the *percentage* reading of byte 2 those
same frames are 3 % and 80 %, both entirely ordinary numbers — which is another
way of seeing how much rests on (c).

**e. The mouse sleeps, so there is nothing to read.** The status report is
input-only; there is no feature report to poll and `HidD_GetFeature` answers
nothing. The mouse emits a report shortly after the collection is opened and
then goes quiet, and once it is asleep it emits nothing at all. A reading can
therefore be minutes old with no error anywhere.

**f. The charge flag is inferred, not read.** Byte 3 reads `1` on the dongle
and `0` with the cable in, which looks like a power-source bit — but that is
two samples, so `parse_status` infers charging from the voltage instead.
Voltage is physics and a two-sample guess is not, but it means a cable plugged
into a nearly-full cell is a harder call than it should be.

### What the code already does about it

These are mitigations, not fixes. They stop the display lying loudly; they do
not make the number accurate.

- A validity window (`CELL_MIN_V` … `BUS_MAX_V`) rejects the placeholder frame
  outright, so it can no longer be shown as 0 %.
- `read_status()` takes several reports and returns the **mode**, so a single
  spurious frame cannot move the display. Ties go to the most recent.
- The last good reading is cached beside the state file, so the meter is not
  blank at startup while the mouse is still asleep.
- Readings carry an `age`, and anything older than three minutes is marked
  stale rather than presented as current.
- The measured voltage is shown next to the percentage, because the voltage is
  the part that was actually read.

### What would actually fix it

In the order that gives the most certainty per hour spent:

1. **Settle (c) first — everything else is downstream.** Run
   `tools/battery_log.py` and discharge the mouse from full to flat. If byte 2
   is a percentage it will descend roughly linearly to single digits; if it is
   a voltage in sixteenths it will fall fast at the top, crawl through the
   middle, then collapse — and it will stop around `0x35`–`0x36`, never near
   zero. One full discharge answers this permanently. Record what the vendor
   software claims at three or four points along the way, under controlled
   conditions this time.
2. **Replace the generic curve with measured points.** The same log gives them.
   Pair the byte with elapsed time under a constant load, or better, with a
   multimeter on the cell, and rewrite `LIION_CURVE` from the hardware. Set
   `percent_verified` to True only when the curve came from measurement.
3. **Decode byte 3 properly.** Run `tools/battery_probe.py` and plug the cable
   in and out repeatedly while it prints transitions. Half a dozen clean
   transitions turns the charge flag from a guess into a decoded field and
   removes the "charging with no cable" failure entirely.
4. **Decode byte 4.** Seen as 7, 8, 9 and 12, unexplained. It may be a wake
   reason or a sample counter; if it distinguishes a real measurement from a
   placeholder it would be worth more than all of the above.
5. **Keep the collection open.** The current open / read / close cycle behaves
   like an on-demand read but cannot catch anything the mouse sends in between.
   A persistent reader would see every frame the device volunteers, which is
   the only way to learn what its natural reporting cadence actually is.

Until at least (1) and (2) are done, the honest description of this feature is
"a voltage read from the device, and an estimate derived from it" — which is
what the interface says.

---

## 2. Mouse movement needs a kernel driver, and that driver has never run

### The state of play

The firmware **cannot generate movement**. Four independent lines of evidence
say so (see `FINDINGS.md` §4); the on-device macro engine is keyboard-only.
Anything that moves the pointer therefore has to come from the host.

The host path that exists today is `SendInput`, and it is trivially
distinguishable from a real mouse:

- a low-level mouse hook sees `LLMHF_INJECTED` set on every event;
- Raw Input reports `RAWINPUT.header.hDevice` as NULL, where a physical mouse
  names its devnode.

`tools/verify_injection.py` demonstrates both. This is the reason the filter
driver exists.

A complete KMDF upper filter for the mouse stack is in `driver/asxfilter/`
(~1150 lines of C, INF, shared header). It **builds**, it **signs**, and it has
**never been loaded**, because this machine has Secure Boot enabled and
`bcdedit` refuses to enable test signing under Secure Boot policy:

```
An error has occurred setting the element data.
The value is protected by Secure Boot policy and cannot be modified or deleted.
```

So every claim about the driver's *runtime* behaviour in this repository is a
design claim, not a measurement. It is written down as such. `docs/DRIVER.md`
is the design; this section is what is left to do.

### Finishing it: getting it to load

Three routes, and only the third is both universal and free of trade-offs:

1. **Turn Secure Boot off and use test signing.** Works today, costs nothing,
   and is what `tools/install_driver.ps1` automates. The costs are real: a
   desktop watermark, and **some kernel anti-cheat products refuse to run at
   all** with test signing enabled. If you game on the machine, weigh this
   properly — the point of the exercise was a driver you never have to think
   about, and this is not that.
2. **Attestation-sign it through Microsoft.** An EV code-signing certificate
   plus a Partner Center hardware account gets the `.cat` signed by Microsoft,
   after which it loads on any machine with Secure Boot on and no watermark.
   This is the correct answer for distribution. It costs money annually and the
   identity check is not instant.
3. **Do not ship a driver at all — move the timing into hardware.** A small
   USB device that enumerates as a HID mouse and takes commands over a serial
   link produces movement that is indistinguishable at the driver level because
   it *is* a mouse, with no signing, no Secure Boot question and no watermark.
   This is the approach with the fewest asterisks, and the reason it is not in
   this repository is that it is not a driver.

### Finishing it: what is actually unverified

Assuming it loads, this is the list to work through, in order:

- **Does it attach?** `IOCTL_INTERNAL_MOUSE_CONNECT` must be intercepted and
  the upper filter must sit above `mouhid` and below `mouclass`. Confirm with
  the devnode's driver stack in Device Manager before believing anything else.
- **Does the callback hook hold?** The driver saves the class service callback
  from `CONNECT_DATA` and substitutes its own. If that substitution is wrong
  the mouse stops working entirely — hence the recovery procedure in
  `DRIVER.md` §10, which should be read **before** the first install, not
  after.
- **Does synthesised input arrive clean?** Feed a known step through
  `MouseClassServiceCallback` and check with `tools/verify_injection.py` that
  `LLMHF_INJECTED` is clear and `hDevice` is the real devnode. That single test
  is the entire point of the driver; if it fails, nothing else matters.
- **Is the timing right at DISPATCH_LEVEL?** Emission uses
  `ExAllocateTimer(EX_TIMER_HIGH_RESOLUTION)`. Verify the actual interval under
  load rather than trusting the requested one, and verify that folding a delay
  into the following step (`hostrun.build_plan`) still lands where intended.
- **Does it coexist with the physical mouse?** Real movement and synthesised
  movement share one callback. Moving the mouse during a macro is the case that
  breaks naive implementations.
- **Does it unbind cleanly?** Uninstall, reboot, and confirm the devnode
  returns to the inbox `msmouse.inf` configuration.

### If you are starting fresh

The shape that works, briefly: an upper filter on the mouse class stack that
intercepts `IOCTL_INTERNAL_MOUSE_CONNECT`, keeps the real
`MouseClassServiceCallback` and its context, and exposes a device interface
(`\\.\AttackSharkFilter`) for user mode to submit a compiled plan. Movement is
emitted by calling the saved callback with `MOUSE_INPUT_DATA` from a
high-resolution timer at DISPATCH_LEVEL — which is why the plan is compiled and
submitted whole rather than driven step-by-step from user mode. Two details
that cost time here and will cost you time too: **the KMDF version must match
the inbox `Wdf01000.sys`**, not the newest in the WDK, or it builds perfectly
and refuses to load; and **binding needs `devcon update`, not `pnputil`**,
because the devnode is already claimed by WHQL-signed `msmouse.inf` and a
locally signed package will never win the ranking contest.

---

## 3. Smaller open items

- **The interface trusts anything that can reach it with the right headers.**
  `_request_allowed` refuses cross-origin requests and rebound Host names, which
  is what stops a web page driving the API, but there is no authentication: any
  *program* running as you can drive it, and a program running as you could talk
  to the mouse directly anyway. See [`SECURITY.md`](../SECURITY.md).
- **Two of sixty captured packets are not rebuilt by the test suite.** Both are
  chunks of an *empty* macro, which has no events to rebuild from. Not a defect,
  but the headline "58/60" should not be read as two unexplained failures.
- **No settings can be read back from the device.** `HidD_GetFeature` answers
  nothing for every report ID, so the driver can never learn the mouse's actual
  state — it can only push its own. Everything it shows is what *it* last sent,
  which is why it re-pushes the full configuration at startup. If you change a
  setting from the vendor software while this is running, this will not know.
- **Lighting is entirely undone.** The vendor resources define twelve lighting
  modes, brightness, speed and a sixteen-colour palette, but the X3 build of the
  vendor UI never shows that page, so no traffic could be captured.
- **Firmware macro delays are not encoded.** The device macro block has twenty
  unexplained zero bytes; the vendor software stores delays and then discards
  them on upload. Device macros therefore run at the firmware's own rate.
- **Mouse buttons inside a device macro are untested.** The vendor editor does
  not offer them, so the event encoding was never sampled.
- **The wheel is a host trigger only, and the kernel half of it has never
  run.** Binding a macro to `wheel up` / `wheel down` works on the `SendInput`
  backend, where the low-level hook sees `WM_MOUSEWHEEL` and swallows the notch
  the way it swallows a bound click. The filter-driver path is written and
  pinned by `tests/test_kdriver.py`, but like everything else about that driver
  (§2) it has never been observed running: `SuppressWheel` is a design claim
  until someone loads the filter and scrolls. With a 1.0 filter loaded the
  trigger still fires and the page still scrolls, which the app says out loud.
- **The mouse's own wheel actions are not remapped.** Button slots 16 and 17
  hold the wheel defaults (`0x0A` down, `0x09` up), so in principle a *device*
  macro could be bound to a notch and run with nothing installed. Neither slot
  has been written to, and a wrong write there costs you the scroll wheel until
  the defaults are restored, so the wheel is driven from the host instead.
- **Angle snap, ripple control, motion sync and lift-off distance are written
  but not independently verified.** The bytes are confirmed against captures —
  the mouse receives exactly what the vendor tool sends — but no measurement was
  made of the sensor actually behaving differently afterwards.
- **Report `0x0C` is undecoded.** Two `(value, 0xFF-value)` pairs, always
  `0x01`, sent first in every burst. Probably profile select or a "begin
  config" marker.

---

## 4. Reporting something

Email **prodmutant@gmail.com**. Useful reports include: the exact bytes if you
have them (`tools/capture.py` logs them), what the mouse did versus what you
expected, and whether you are on the dongle or the cable — a surprising number
of behaviours differ between the two.

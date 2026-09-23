# asxfilter — making the mouse move itself

A kernel filter driver that lets this project emit mouse movement which is not
distinguishable, to anything running on the machine, from movement the Attack
Shark X3 produced on its own.

> **Status:** the driver builds, links against the inbox KMDF, catalogues and
> test-signs cleanly, and its user-mode binding is covered by
> `tests/test_kdriver.py`. It has **not yet been observed running on this
> machine**, because Secure Boot is enabled here and Windows refuses to set
> `testsigning` while it is — see [Installing](#installing). Everything below
> that is marked *verified* was checked; everything else is design.

---

## 1. What was wrong with the old path

`hostrun.py` used to play macros with `SendInput`. That works, and for a lot of
purposes it is fine, but it is not what was asked for here, and the reason is
concrete rather than aesthetic. Windows offers two independent ways to ask
where an input event came from, and `SendInput` fails both:

| | physical mouse | `SendInput` |
|---|---|---|
| `MSLLHOOKSTRUCT.flags` in a low-level hook | `0` | `LLMHF_INJECTED` (bit 0) |
| `RAWINPUT.header.hDevice` from Raw Input | the device handle, resolving to `\\?\HID#VID_1D57&PID_FA60&MI_01#...` | `NULL` |

The first is a single bit that win32k sets on everything `SendInput` produces.
The second is worse for a synthesiser, because it is not a flag to be cleared
but an *absence*: there is no device, so there is no handle.

No amount of realism in the trajectory addresses either one.
`attackshark/motion.py` already produced genuinely good movement — minimum-jerk
velocity, sub-pixel accumulation, correlated tremor, overshoot — and it would
still have been rejected by a one-line check, because the question being asked
is not "does this look like a hand" but "did this come from a device".

So the movement has to enter the stack below the point where that question is
answered.

## 2. The mouse can type, but it cannot move

The obvious place for the movement to come from is the mouse, and the X3 does
have a working on-board macro engine: report `0x09`, decoded in
[`PROTOCOL.md` §8](PROTOCOL.md#8-report-0x09--macro-upload). This project drives
it, and it is genuinely the ideal mechanism — a macro played by the firmware
produces real HID reports from real hardware, with no injection flag, no driver
and no reboot. *(verified: 52 keystrokes from the mouse's own keyboard
collection, `LLKHF_INJECTED` clear on every one)*

It cannot do movement. A stored event is **two bytes**, `[flags, HID usage]` —
`0x01` press, `0x81` release — and that is all the firmware will accept. This
was tested rather than inferred, four ways: the vendor's macro editor has only
Key / Action / Delay columns; every captured block contains only key events;
`0xF9` (the movement opcode in Attack Shark's *keyboard* driver, a four-byte
`[type, delay, dx, dy]` record) is rejected outright, with the bound button
falling back to its default action; and neither vendor binary references any
test or calibration mode. §8 has the detail.

Even with room, a firmware macro is a fixed sequence played on a button press:
no host feedback, no "move to what is on screen now". It is the wrong shape for
the job as well as too small for it.

So key macros belong on the device, and movement has to be host-side — but
*where* on the host.

## 3. Where the filter sits

```
                 ┌──────────────────────────────────────┐
   applications  │  Raw Input · hooks · GetCursorPos     │
                 └───────────────▲──────────────────────┘
                                 │   win32k stamps LLMHF_INJECTED here,
                                 │   on anything SendInput produced
                 ┌───────────────┴──────────────────────┐
                 │  mouclass.sys                        │
                 └───────────────▲──────────────────────┘
                                 │  MouseClassServiceCallback(dev, start, end, &n)
                 ┌───────────────┴──────────────────────┐
                 │  ▶ asxfilter.sys ◀                   │   ← us
                 └───────────────▲──────────────────────┘
                                 │  the same call, same arguments, same IRQL
                 ┌───────────────┴──────────────────────┐
                 │  mouhid.sys → HIDCLASS → usbhub      │
                 └───────────────▲──────────────────────┘
                                 │
                            the X3's dongle
```

`asxfilter` is an upper filter on the X3's **own** mouse devnode — bound by
hardware ID, so no other pointing device on the machine is touched.

It gets there by intercepting one IOCTL. When `mouclass` attaches to a mouse it
sends `IOCTL_INTERNAL_MOUSE_CONNECT` down the stack carrying a `CONNECT_DATA`,
which holds the address of `MouseClassServiceCallback`. The filter copies that
pair, substitutes its own callback, and forwards the request
(`filter.c`, `AsxEvtInternalDeviceControl`). From then on:

* every physical report arrives at `AsxServiceCallback` before `mouclass` sees
  it, so the driver can withhold one;
* and the driver holds the one function pointer that lets it *deliver* a
  report, by calling `mouclass` exactly the way `mouhid` does.

That second point is the whole trick. Emitting is not an imitation of the real
path — it is the real path, called from a different place. There is no
injection flag to set, because nothing in this route sets one, and `mouclass`
attributes the report to the device object it handed down at connect time,
which is the X3's.

This is the same layer the well-known input-interception drivers work at. It is
below every user-mode detection method and below win32k; it is *not* below a
driver that enumerates the mouse stack and notices an extra filter, and it is
not the USB wire. §9 is explicit about that.

## 4. Emission and timing

`AsxEmit` (`inject.c`) fills in a `MOUSE_INPUT_DATA` — relative flags, the
`UnitId` mirrored from real reports, button transitions, `LastX`/`LastY` — and
calls the captured callback. The class callback contract is `DISPATCH_LEVEL`,
so `AsxEmit` raises IRQL if it was reached from an IOCTL.

A movement is not one report. `motion.plan()` produces one report per tick at
the device's own rate, and the **whole trajectory is submitted in a single
IOCTL** and clocked out by the driver. That is deliberate:

* Driving 1 ms steps from user mode means a `Sleep` per step, and the Windows
  scheduler will not honour that. A descheduled daemon stretches the middle of
  a movement, which is both wrong and conspicuous.
* The driver uses `ExAllocateTimer(..., EX_TIMER_HIGH_RESOLUTION)`. An ordinary
  kernel timer quantises to the 15.6 ms system tick, which would make a 1 kHz
  cadence meaningless.
* Steps are scheduled against an **absolute** deadline that advances by each
  step's delay, so a 4000-step plan does not accumulate one rearm's worth of
  lateness per step. If it falls more than 20 ms behind (a DPC storm, a
  debugger break) it rebases on now rather than firing a catch-up burst that
  would arrive as one impossible jump.

`hostrun.build_plan()` compiles a run of mouse steps — movement, buttons,
wheel, delays — into one submission, folding each delay into the next emitted
step so the delays are the kernel's to keep too. A keyboard step ends the batch,
because a mouse filter cannot type (§9).

## 5. The other half of control: the physical mouse

Being able to emit is only half of "full control". The driver also decides what
the real mouse is allowed to do, because it sees physical reports first.

`IOCTL_ASX_SET_FILTER` takes a mask of button transitions to **swallow**. A
button bound to a macro is withheld inside the mouse stack, so no application
ever sees the click — there is nothing to notice it and nothing to eat it after
the fact, which is what a low-level hook is reduced to. Movement can be
suppressed the same way.

The wheel cannot, and that is why it has a field of its own. A notch is a
single `ButtonFlags` bit with a signed `ButtonData`, not a transition per
direction, so "swallow wheel up and leave wheel down alone" is not expressible
as a mask — the direction is only known once the report is in hand.
`SuppressWheel` (interface 1.1) carries one bit per direction, and the filter
tests the sign of the delta per report, clearing the wheel bit and its data
while leaving any movement in the same report untouched.

`ASX_FILTER_CFG` grew that field on the end, and `ASX_STATUS` reports it back
the same way, so a 1.0 layout stays a prefix of the 1.1 one: a client built
against the newer header sends four `ULONG`s to a 1.0 driver, which reads the
three it knows and ignores the rest. `hostrun` checks the reported version and
says plainly that the wheel still scrolls rather than pretending it is bound.

`IOCTL_ASX_READ_EVENTS` is an inverted call: it parks in a manual queue and is
completed the moment a physical report arrives, so a macro trigger costs no
polling interval and the notification leaves the kernel before `mouclass` has
seen the click.

Together these replace the low-level mouse hook entirely. `hostrun.Engine`
installs one only when there is no driver; with one, it keeps just the keyboard
hook, for the Escape panic key.

A pleasant consequence: "passthrough" (do the normal click *and* the macro)
stops being a replay. The driver simply does not suppress that button, so the
click that reaches applications is the genuine article.

## 6. Interface

`driver/asxfilter/asxfilter_public.h` is the contract; `attackshark/kdriver.py`
is the binding, and `tests/test_kdriver.py` checks they agree on every constant,
IOCTL code, structure size and field offset — a silent disagreement would not
fail loudly, it would put the wrong bytes in the wrong fields.

Control device `\\.\AttackSharkFilter`, ACLed to SYSTEM and Administrators:
anything holding that handle can move the pointer and withhold clicks from the
whole desktop, which is not something an unprivileged process should be able to
ask for. **Clients must be elevated.**

| IOCTL | Direction | Purpose |
|---|---|---|
| `IOCTL_ASX_STATUS` | out `ASX_STATUS` | attached / connected / playing / queued, and counters |
| `IOCTL_ASX_SUBMIT` | in `ASX_SUBMIT` | queue up to 4096 steps; the ring holds 16384 |
| `IOCTL_ASX_STOP` | — | drop the queue and release anything held down |
| `IOCTL_ASX_SET_FILTER` | in `ASX_FILTER_CFG` | suppression mask, movement suppression, event reporting, wheel direction |
| `IOCTL_ASX_READ_EVENTS` | out `ASX_EVENT[]` | blocks until physical reports arrive |

```c
typedef struct _ASX_STEP {
    unsigned long   DelayUs;    // wait this long, then emit
    long            Dx, Dy;     // relative
    unsigned short  Buttons;    // MOUSE_INPUT_DATA ButtonFlags
    short           Data;       // wheel delta, 120 per notch
} ASX_STEP;                     // 16 bytes
```

## 7. Building

```
python tools/build_driver.py            # build, catalogue, test-sign
python tools/build_driver.py --no-sign  # just the .sys
```

No `.vcxproj`. The WDK's Visual Studio integration targets particular VS
versions and this machine has VS 18, so the script drives `cl.exe` and
`link.exe` directly with the include and library paths the WDK would have
supplied. It is also a readable statement of what a KMDF driver build is.

Two details worth knowing, both learned the hard way:

* **KMDF version must match the running system, not the kit.** Windows 10
  19045 ships `Wdf01000.sys` 1.31; the WDK ships up to 1.35. A driver linked
  against 1.33 builds perfectly and then refuses to load, because `WdfLdr` will
  not bind it to a library the system does not have. `build_driver.py` reads
  the inbox version and defaults to it. *(verified: inbox 1.31, linked 1.31)*
* **`ExAllocatePool2` needs 19041+.** Hence `NTDDI_VERSION=0x0A000008`.

Output in `driver/out/`: `asxfilter.sys`, a stamped `asxfilter.inf`,
`asxfilter.cat`, and the self-signed certificate the installer has to trust.

## 8. Installing

```
tools\install_driver.ps1        # self-elevates
tools\uninstall_driver.ps1      # reverses all of it
```

Four steps, each depending on the last: trust the test certificate, enable test
signing, stage the package, bind it to the devnode. Re-run it after the reboot;
every step is idempotent.

**Secure Boot must be off.** This is not optional and not a matter of policy —
`bcdedit` refuses outright:

```
An error has occurred setting the element data.
The value is protected by Secure Boot policy and cannot be modified or deleted.
```

*(verified on this machine: Secure Boot is on, so the install stops at step 2.)*
Reboot into firmware with `shutdown /r /fw /t 0`, turn Secure Boot off, and run
the installer again.

**Test signing has costs you should weigh.** A desktop watermark, and some
kernel anti-cheat products refuse to run at all in this mode. If that matters,
this driver is the wrong tool and a hardware approach is the right one.

**Binding needs `devcon`, not just `pnputil`.** The devnode is already claimed
by the inbox `msmouse.inf`, which is WHQL signed and therefore outranks
anything signed locally, so "install the better match" will never choose ours.
`devcon update` applies an INF to a named devnode and skips the ranking contest.
The INF chains `msmouse.inf` (`Needs=HID_Mouse_Inst.NT`) rather than replacing
the stack, so `mouhid` and `mouclass` are installed exactly as Windows would
have, and removing the package returns the mouse to the inbox configuration.

## 9. What this does and does not defeat

Being precise about this is the point of the exercise.

**It defeats**, by construction:

- the `LLMHF_INJECTED` hook check — nothing in this route sets it;
- Raw Input device attribution — reports carry the X3's device handle;
- anything reasoning about trajectory shape, which `motion.py` already handled;
- a hook or filter placed *above* us that expects to see the click, when
  suppression is on.

**It does not defeat:**

- **anything that enumerates the mouse device stack.** `asxfilter` is visible
  in `UpperFilters` and as a running service. It is not hiding, and making it
  hide would be a different project with a different purpose.
- **anything that reads the USB wire**, or correlates against the dongle's own
  traffic. The reports are real from `mouclass` upward; they were never on the
  wire. Only hardware fixes that.
- **the fact that test signing is on**, which is itself trivially detectable
  and is the loudest signal here by a wide margin.

**Scope limits**, unrelated to detection:

- A mouse filter cannot emit **keystrokes**. Keyboard steps still go out via
  `SendInput`, flagged injected. A keyboard class filter is the same pattern
  applied to `kbdclass` and is not written.
- Movement is **relative**. Absolute positioning means reading the cursor
  position and computing a delta, which is what the pointer-acceleration
  settings will then alter.

## 10. If something goes wrong

Kernel code that runs on every mouse report can take the machine down. The
design tries to make that survivable rather than assuming it will not happen:

* The service is **demand-start and not boot-critical**. If the driver
  misbehaves, boot with the dongle unplugged: no devnode, no filter loaded, and
  `tools\uninstall_driver.ps1` will run normally.
* Safe Mode also works, and the uninstaller is usable there.
* Suppression config is **volatile and refcounted**. It is cleared when the
  last handle to the control device closes, so a crashed daemon can never leave
  a button swallowed or a plan running with nobody to stop it. This is why
  `kdriver.Driver` opens two handles and why the driver counts opens rather
  than resetting on the first close.
* `IOCTL_ASX_STOP` and handle-close both release any button the driver emitted
  a *down* for, so a cut-short macro cannot leave the left button held.
* Escape is a panic key in `hostrun`, and stops every running macro.
* Per-step deltas are clamped in the driver, and a submit's step count is
  validated against the actual input-buffer length before a single step is
  read — the count arrives from user mode and is not to be trusted even from an
  administrator.

## 11. Verifying

The claim in §1 is falsifiable, so it is tested rather than asserted:

```
python tools/verify_injection.py
```

It listens with both mechanisms at once — a low-level hook for the injected
flag, and Raw Input on a message-only window for the device attribution — then
runs the same movement three ways: by hand, through `SendInput`, and through
the driver. It prints what each mechanism saw for each, and whether the
driver's output was attributed to the same device as your hand.

```
python tools/verify_injection.py --status   # driver state
python tools/verify_injection.py --watch    # physical reports, kernel-side
```

Expected result, once Secure Boot is off and the driver is loaded: the
`SendInput` phase reports `injected: N` with no device, and the driver phase
reports `injected: 0` attributed to `\\?\HID#VID_1D57&PID_FA60&MI_01#...` — the
same string the physical phase produced.

## 12. Files

| | |
|---|---|
| `driver/asxfilter/asxfilter_public.h` | the user/kernel contract |
| `driver/asxfilter/asxfilter.h` | internal state and declarations |
| `driver/asxfilter/driver.c` | entry, devnode, control device, IOCTLs, event ring |
| `driver/asxfilter/filter.c` | connect interception and the service callback |
| `driver/asxfilter/inject.c` | the step queue and the high-resolution timer |
| `driver/asxfilter/asxfilter.inf` | binds to the X3's hardware IDs, chains `msmouse.inf` |
| `tools/build_driver.py` | MSVC + WDK discovery, compile, link, catalogue, sign |
| `tools/install_driver.ps1` / `uninstall_driver.ps1` | certificate, test signing, staging, binding |
| `tools/verify_injection.py` | the proof |
| `attackshark/kdriver.py` | ctypes binding |
| `tests/test_kdriver.py` | checks the binding against the C header |

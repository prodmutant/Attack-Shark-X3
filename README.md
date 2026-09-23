# attackshark-x3

An open-source driver for the **Attack Shark X3** wireless mouse, plus the
reverse-engineering toolkit used to produce it.

The stock software (`X3.exe`, a closed 32-bit DuiLib app) is the only way to
configure this mouse. This project replaces it with ~2600 lines of
dependency-free Python and a ~1600-line local web interface, uploads key macros
into the mouse's own firmware, ships an optional ~1150-line kernel filter driver
for host-side movement, and documents the wire protocol so anyone can port it.

```
attackshark gui                      # local web interface
attackshark info
attackshark polling 500
attackshark dpi 800 1600 3200 --active 1
attackshark button 5 forward
attackshark flags --lod 1 --motion-sync on
attackshark power --sleep 0.5 --deep-sleep 10 --key-response 4
attackshark driver                   # filter driver status
```

## Getting it

There are two ways in, and they are kept apart on purpose.

**A build.** The packaged executable is on the *Releases* page — one file, no
Python needed, tray icon, opens the interface in your browser. Nothing in this
repository is a build: `dist/` is ignored and never committed, so what you clone
is only source, and what you download is only a binary. That way the version you
are running is the one the release says it is.

**The source.** Needs Python 3.8 or later and nothing else — no pip packages, no
runtime to install. Clone it and run `python -m attackshark gui`, or `pip install .`
for the `attackshark` command. Build your own copy of the executable with
`python build_exe.py` (that one does want `pyinstaller` and `pillow`).

## The interface

Three pages, from the nav in the header:

| | |
|---|---|
| **Dashboard** | DPI stages, polling rate, sensor toggles, power timers, profile |
| **Macros** | the macro list, the host engine, and the per-button bindings |
| **Themes** | six palettes and the particle layer |

The mouse stays on the left on every page and does three jobs from one drawing:
it is the way into a button on the dashboard, the binding target on the macros
page, and a live preview on the themes page. Click a button on it, or the same
row in the list beside it, to reassign it. Every other control writes to the
mouse as you release it.

Keyboard shortcuts are captured by pressing them: open a button, switch to the
*Keyboard shortcut* tab, and press the combination you want.

### The mouse drawing

Not drawn by hand. `tools/trace_outline.py` traces the outline in
`captures/ref/mouse_outline.png` and emits the path, so the shell is the
device's real silhouette rather than an ellipse: aspect 0.52, a waist at the
midpoint, and the widest point low in the palm at about 70%.

```
python tools/trace_outline.py captures/ref/mouse_outline.png --points 22
```

Two things the tracer has to get right, both of which look like the shape being
wrong rather than the method being wrong:

* **Sample by arc length, not by row.** Rows starve the nose and tail, where
  the outline runs nearly horizontal, and the curve kinks there.
* **Mirror the right flank.** The side buttons sit on the left only, so that
  edge carries their tabs; filtering them out needs a window so wide it
  chamfers the nose and tail into a rounded rectangle. The right flank is clean
  to begin with, and mirroring it about an axis measured in the tab-free
  quarters removes the tabs exactly, with no smoothing. Measured against a
  de-tabbed left flank the two agree to within 4 units through the body and
  differ by 60+ at the caps, which is the filtering damage made visible.

### Artwork

The shipped mark and backdrop are drawn from the same reference as the mouse
map, by the same route - so the badge in the header, the watermark behind the
right-hand column and the drawing on the left are all one object:

```
python tools/make_mark.py --logo --backdrop
```

The logo is not simply the silhouette. Filled and shrunk to the 74px the header
uses, the outline reads as a rounded blob that could be anything; the seam
between the buttons and the wheel sitting in it are what make it legibly a
mouse, so both are cut back out in the disc colour. The backdrop is stroked
rather than filled for the opposite reason - at 900x1500, behind 42% opacity
and a scrim, a filled silhouette is an enormous pale smudge, because a
silhouette has no detail that survives being blown up that far.

**Your own artwork instead.** Drop `logo.custom.png` or `backdrop.custom.png`
into `attackshark/web/` and they win over the shipped files. Nothing else needs
to know - the page still asks for `logo.png` - and `.gitignore` covers
`*.custom.*`, so your face never becomes a commit. Any image will do:

```
python tools/make_logo.py IMAGE --circle --fit --ring 3d9e60
python tools/make_logo.py IMAGE --box 160,140,380,380     # exact source crop
python tools/make_backdrop.py IMAGE --anchor top          # right-hand backdrop
```

`--fit` is the one that matters for a mark: crop-to-fill puts the corners of the
square exactly where a face keeps its horns and jaw, and the disc then cuts them
off. `--fit` scales the whole image inside the disc instead, and `--ring` draws
the edge that stops a dark mark dissolving into a dark header. `make_backdrop`
cover-fits an image, dims it, and bakes a left-to-right alpha ramp so it melts
into the page rather than sitting on it as a rectangle. Both need Pillow;
nothing else does.

## Macros: on the mouse, or on the host

Macros run in one of two places, and the difference is not cosmetic.

**On the mouse.** Key macros are uploaded into the firmware (report `0x09`) and
played by the hardware itself. Verified on a real device: 52 keystrokes
attributed to `HID\VID_1D57&PID_FA60&MI_00` — the mouse's own keyboard
collection — with the injected flag clear on every one. No driver, no kernel
code, no test signing, no reboot, and Secure Boot is irrelevant, because the
mouse genuinely sent those reports. This is the same mechanism the vendor
software uses, and it is the safe default.

Getting there took one non-obvious discovery: **macro slot 0 is inert.** A block
written to slot 0 is accepted, checksums fine, and never plays — the bound
button silently reverts to its default action. The vendor app writes Macro1 to
slot 0 on startup, so replaying its traffic byte-for-byte reproduces a
configuration that does not work. Slot 2 plays. See §8.

**On the host,** for anything the firmware will not store — which means all
mouse movement. A stored event is two bytes, `[flags, HID usage]`, and that is
all it accepts: the vendor's editor has only Key / Action / Delay columns, every
captured block holds only key events, and `0xF9` — the movement opcode used by
Attack Shark's *keyboard* driver — is rejected outright by this firmware. §8
gives all four lines of evidence. `macro.py`'s `device_support()` decides per
macro and the UI shows which target each one uses.

### The scroll wheel

The wheel is in the macro system twice, at both ends of it.

**As output**, a macro can turn it. On the timeline a wheel lane takes two
kinds of block: a **tick**, one notch at the block's left edge, and a **spam**,
a notch every *n* ms across the block's width — a wheel spun at an exact speed
for an exact length of time, which a hand cannot do twice the same way. Each
event turns one notch by default and up to sixteen, for games that read the
delta rather than counting events. Up is positive, the sign Windows uses.

**As a trigger**, a macro can be bound to the wheel: `wheel up` and `wheel
down` sit under the five buttons in the bindings table, and the notch that
fires a macro is swallowed the way a bound click is, unless *also scroll as
normal* is ticked. A notch has no release, so `hold` repeat means *while it
keeps turning* — the macro runs, every further notch pushes the deadline out,
and it stops 300 ms after the wheel goes still. One tick, one pass; keep
spinning, it keeps going.

Neither end is storable on the mouse: the firmware's macro event is two bytes,
`[flags, HID usage]`, with nowhere to put a direction or a delta. Wheel macros
run from this app.

Host movement goes out through `SendInput`, which Windows marks as injected: a
low-level hook sees `LLMHF_INJECTED`, and Raw Input reports a null device handle
instead of a device. No amount of realism in the trajectory changes that,
because the question being asked is not "does this look like a hand" but "did
this come from a device".

### The optional filter driver

`driver/asxfilter/` answers that question differently. It is a KMDF filter on
the X3's own mouse devnode, between `mouhid` and `mouclass`. It intercepts
`IOCTL_INTERNAL_MOUSE_CONNECT` to capture `MouseClassServiceCallback`, then
emits movement by *calling that function* — the same call, arguments and IRQL
`mouhid` uses for a physical report. Nothing on that route sets an injection
flag, and `mouclass` attributes the report to the X3.

It also owns the physical side: a button bound to a macro is swallowed inside
the driver, so no application sees the click at all, and triggers arrive through
a pending IOCTL that completes before `mouclass` has seen them. The low-level
mouse hook is gone when the driver is loaded. Interface 1.1 adds the wheel to
that: a notch is one flag and a signed delta rather than a transition per
direction, so withholding `wheel up` while `wheel down` still scrolls is a
decision the filter has to make per report, and it has a field of its own
(`SuppressWheel`). A 1.0 filter takes the request, ignores it, and the app
says so rather than claiming the wheel is bound.

```
python tools/build_driver.py        # build, catalogue, test-sign
tools\install_driver.ps1            # trust, test-signing, stage, bind
python tools/verify_injection.py    # prove it
```

**It is off by default and you probably do not want it.** A self-signed kernel
driver needs test signing, which needs Secure Boot off, which brings a desktop
watermark and makes some kernel anti-cheat products refuse to run — and a custom
filter on the mouse stack is a far louder signal than the injected flag it
removes. [`docs/DRIVER.md`](docs/DRIVER.md) §9 is explicit about what it does
and does not defeat.

> Built, signed and unit-tested; **not yet observed running**, because Secure
> Boot is enabled on the machine it was developed on. See `docs/DRIVER.md`.

## Status

The protocol is documented in [`docs/PROTOCOL.md`](docs/PROTOCOL.md), and
[`docs/FINDINGS.md`](docs/FINDINGS.md) is the full account: what was decoded,
what was tried and failed, what is still open, and where to start if you want
to take it further.

**Found a bug, or decoded something marked unknown?** Email
**prodmutant@gmail.com**.
`tests/test_protocol.py` reconstructs **58 of the 60 unique packets** captured
from the vendor tool, byte-for-byte, from the documented encodings — including
the macro-upload chunks, which are rebuilt from the macro they encode at the
block's own slot (the two skipped are the chunks of an empty macro):

```
$ python tests/test_protocol.py
58/60 packets round-tripped exactly, 2 skipped
PASS - protocol.py reproduces every captured packet byte-for-byte

$ python tests/test_kdriver.py
PASS - the user-mode binding matches the driver's header
```

**Working:** DPI table (8 stages, 50–26000), active stage, per-stage colour,
polling rate, lift-off distance, ripple control, angle snap, motion sync, sleep
and deep-sleep timers, key response time, button remapping for all 5 buttons
including DPI/multimedia/browser actions and **arbitrary keyboard shortcuts**
(`ctrl+shift+s` and friends), profile import/export, factory reset, a
**battery level read straight from the device** (the vendor app's figure is
static and wrong), **macro upload into the mouse** for key-only macros, and
**host macros with real mouse movement** via the filter driver.

**Not yet mapped:** lighting effects (the X3 build of the vendor UI never shows
the lighting page, so nothing could be captured), per-event delays inside a
device macro (the firmware block has no field for them), mouse buttons inside a
device macro (not sampled — the vendor editor does not offer them), and a
handful of multimedia/browser codes that were not individually sampled.
See §6, §8 and §10 of the protocol doc.

## Requirements

Windows, Python 3.8+. No third-party packages — the HID transport is `ctypes`
over `hid.dll` / `setupapi.dll`, and the web UI is served by `http.server`.
Pillow is only needed for `tools/make_logo.py`; `frida-tools` only for capturing
new traffic.

The filter driver is optional and needed only for real mouse movement. Building
it wants MSVC and the WDK matching your SDK
(`winget install --id Microsoft.WindowsWDK.10.0.26100`); installing it wants
Secure Boot off and test signing on. Everything else works without it.

```
pip install .            # or: python -m build && pip install dist/*.whl
```

The web assets are declared as package data, so an installed copy serves the
same interface as a source checkout.

### Adding a feature

The front end builds its controls from the catalog the server sends, so a new
protocol field is three small edits: add the encoding in `protocol.py`, list the
field in `server.py`'s `_GROUPS`, and add a control in `web/app.js`.

## How it works

The mouse exposes a vendor HID collection (usage page `0x000B`) on interface
MI_02. Each setting group is a feature report whose first two bytes are the
report ID and the total length. Config blocks end with a 16-bit big-endian sum
of the payload; the two short commands use `(value, 0xFF - value)` pairs
instead.

One thing to know: **the mouse never answers reads.** `HidD_GetFeature` returns
nothing for every report ID, and the vendor tool does not read settings back
either — it keeps them in `%APPDATA%` and re-pushes everything on each change.
This driver does the same, in `%APPDATA%\attackshark.json`. If the mouse and the
state file disagree, run `apply` to resynchronise.

## Layout

```
attackshark/
  hid_backend.py   Windows HID via ctypes (enumerate, open, get/set feature)
  protocol.py      packet builders + parsers, checksums, value encodings
  device.py        high-level API and local state
  macro.py         the macro model, and the device-macro encoder
  motion.py        movement trajectories (minimum-jerk, tremor, overshoot)
  hostrun.py       host macro engine; kernel backend, SendInput fallback
  kdriver.py       ctypes binding to the filter driver
  cli.py           command line front end
  server.py        stdlib HTTP server + JSON API for the web UI
  web/             the interface (index.html, style.css, app.js, logo.png)
driver/asxfilter/  the KMDF mouse filter driver (C, INF, shared header)
docs/PROTOCOL.md   the wire specification
docs/FINDINGS.md   what was found, what failed, what is still open
docs/DRIVER.md     the driver: design, limits, install, recovery
docs/KNOWN_ISSUES.md  what is wrong, what is unfinished, how to finish it
SECURITY.md        threat model, and how to report something
tests/             round-trip verification against the captures
tools/             the RE toolkit (below)
captures/          labelled HID traffic, the evidence behind the spec
plans/             UI action scripts that drive the vendor tool
```

## The RE toolkit

Useful for finishing the unmapped fields, or for doing the same to a different
mouse in this OEM family.

| Tool | What it does |
|---|---|
| `tools/hid_enum.py` | list HID collections and their report sizes |
| `tools/hid_caps.py` | dump feature/input/output report capabilities |
| `tools/hook_hid.js` | Frida agent: logs every HID exchange across all three layers the vendor app uses (`hiddriver_*.dll`, `hidapi.dll`, `hid.dll`) |
| `tools/capture.py` | spawn or attach to `X3.exe` with the agent loaded |
| `tools/exercise.py` | **the useful one** — spawns the app, drives its UI, and labels every captured packet with the action that caused it |
| `tools/ui.ps1` | window automation by `PostMessage` (no focus stealing); handles DuiLib popup menus and sliders |
| `tools/analyze.py` | group captures by action, de-duplicate wrapper double-logging |
| `tools/pe.py`, `tools/exports.py`, `tools/findconst.py` | PE imports/exports and constant search, used to find the hard-coded VID/PID |
| `tools/restore_original.py` | write back the exact bytes the mouse had before the session |
| `tools/build_driver.py` | build the filter driver: MSVC + WDK discovery, compile, link, catalogue, test-sign |
| `tools/install_driver.ps1` | trust the cert, enable test signing, stage the package, bind the devnode |
| `tools/uninstall_driver.ps1` | reverse all of the above |
| `tools/verify_injection.py` | **the proof** — hook flag and Raw Input device attribution, three ways |
| `tools/probe_device_macro.py` | drive the firmware macro engine: selftest, watch, remap sanity check, movement-opcode probe |
| `tools/replay_capture.py` | replay a captured write sequence back to the mouse, verbatim |
| `tools/make_mark.py` | draw the shipped logo and backdrop from the traced outline |
| `tools/make_logo.py` | fit any image into the header logo slot |
| `tools/make_backdrop.py` | turn an image into the faded right-hand backdrop |
| `tools/read_inputs.py` | listen on every collection for input reports |
| `tools/watch_status.py` | timestamp status reports; test what triggers them |
| `tools/battery_probe.py` | watch the status report and print only the changes - plug the cable in and out to see which byte moves |
| `tools/battery_log.py` | log the status byte across a charge cycle into `captures/battery.csv` |
| `tools/probe_status.py`, `tools/hid_probe.py` | poke the status and config collections |
| `tools/latency.py` | measure click-to-report latency |
| `tools/trace_outline.py` | trace a reference drawing into the mouse-map SVG path |

The method that made this tractable: rather than read disassembly, drive one
setting at a time from the vendor UI while logging `HidD_SetFeature`, then diff.
Each toggle moves exactly one byte, which makes the field map fall out directly.

```bash
pip install frida-tools          # only needed for capturing, not for the driver
python tools/exercise.py --plan plans/toggles.json --out captures/toggles.jsonl
python tools/analyze.py captures/toggles.jsonl
```

A plan is a list of UI actions; `label` names the resulting packets:

```json
[
  {"label": "open_lod",  "x": 937, "y": 448, "wait": 2.0},
  {"label": "lod_1mm",   "x": 832, "y": 481, "wait": 4.5},
  {"label": "lod_2mm",   "x": 940, "y": 481, "wait": 4.5},
  {"label": "set_fwd",   "menu": 4, "wait": 4.5}
]
```

## Safety

Writes go to the mouse's configuration flash. Everything this driver sends is
byte-identical in shape to what the vendor tool sends, and the packets in
`captures/` are a known-good reference you can always re-send with
`tools/restore_original.py`. The vendor tool remains installed and will happily
overwrite anything set here.

The kernel filter is the part that can actually hurt: it runs on every mouse
report, and buggy kernel code takes the machine with it. The design assumes it
will go wrong at least once —

* the service is demand-start and not boot-critical, so booting with the dongle
  unplugged loads no filter at all and `tools\uninstall_driver.ps1` runs
  normally (Safe Mode works too);
* suppression is volatile and refcounted, cleared when the last handle to the
  control device closes, so a crashed daemon cannot leave a button swallowed;
* stopping, or closing the handle, releases any button the driver pressed;
* the control device is restricted to SYSTEM and Administrators;
* Escape stops every running macro.

`docs/DRIVER.md` §10 has the full recovery path.

## Legal

Interoperability research on hardware I own, using only observation of the
software's own traffic and its shipped resource files. No vendor code is
included or redistributed.

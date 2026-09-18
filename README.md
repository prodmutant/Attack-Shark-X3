# attackshark-x3

An open-source driver for the **Attack Shark X3** wireless mouse, plus the
reverse-engineering toolkit used to produce it.

The stock software (`X3.exe`, a closed 32-bit DuiLib app) is the only way to
configure this mouse. This project replaces it with ~2600 lines of
dependency-free Python and a ~1500-line local web interface, uploads key macros
into the mouse's own firmware, ships an optional ~1150-line kernel filter driver
for host-side movement, and documents the wire protocol so anyone can port it.

```
attackshark app                      # desktop application
attackshark gui                      # local web interface
attackshark info
attackshark polling 500
attackshark dpi 800 1600 3200 --active 1
attackshark button 5 forward
attackshark flags --lod 1 --motion-sync on
attackshark power --sleep 0.5 --deep-sleep 10 --key-response 4
attackshark driver                   # filter driver status
```

## Two front ends, one interface

`attackshark app` opens a desktop window; `attackshark gui` serves the same
thing at `127.0.0.1:7332`. They are not two builds of the same design - they
are the *same* front end. `desktop/X3Driver` is a WebView2 host: a WinForms
window with no browser chrome, its own icon and taskbar entry, that renders the
interface with Edge's engine. Identical by construction, with nothing to keep
in sync.

An earlier attempt drew the whole interface again in Tkinter. It was the wrong
call and it is gone: Tk has no letter-spacing, no gradients, no anti-aliased
shapes and no control over font weight, so it could not look like this design,
only near it. Two front ends that merely resemble each other is worse than one
rendered twice.

The window starts its own driver service on a port chosen at runtime and takes
it down on exit, so it never collides with a web UI you already have open, and
there is nothing left listening afterwards. Building it needs the .NET SDK and
the Edge WebView2 runtime:

```
dotnet build desktop/X3Driver/X3Driver.csproj -c Release
```

## The interface

`attackshark gui` serves a local page on `127.0.0.1:7332` and opens it. Click a
button on the mouse diagram (or in the list) to reassign it; every other control
writes to the mouse as you release it. The **Wire packets** section at the bottom
shows the exact bytes each change produces, which is handy when extending the
protocol.

Keyboard shortcuts are captured by pressing them: open a button, switch to the
*Keyboard shortcut* tab, and press the combination you want.

### Artwork

Both the header mark and the backdrop are whatever images you drop in:

```
python tools/make_logo.py IMAGE --circle               # header mark
python tools/make_logo.py IMAGE --box 160,140,380,380  # exact source crop
python tools/make_backdrop.py IMAGE --anchor top       # right-hand backdrop
```

`make_logo` centre-crops to a square (or an exact `--box`) so nothing is
squashed, resizes to 256x256, and `--circle` masks it to a disc and rounds the
frame. `make_backdrop` cover-fits the image, dims it, and bakes a left-to-right
alpha ramp so it melts into the page rather than sitting on it as a rectangle -
tune with `--fade` and `--dim`. Both need Pillow; nothing else does.

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
mouse hook is gone when the driver is loaded.

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

The protocol is documented in [`docs/PROTOCOL.md`](docs/PROTOCOL.md).
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
docs/DRIVER.md     the driver: design, limits, install, recovery
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
| `tools/make_logo.py` | fit any image into the header logo slot |
| `tools/make_backdrop.py` | turn an image into the faded right-hand backdrop |
| `tools/read_inputs.py` | listen on every collection for input reports |
| `tools/watch_status.py` | timestamp status reports; test what triggers them |

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

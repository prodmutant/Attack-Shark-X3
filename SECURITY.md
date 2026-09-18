# Security

## Reporting

Email **prodmutant@gmail.com**. Please do not open a public issue for anything
that could be used against someone before it is fixed.

Useful: what you did, what happened, and the smallest thing that reproduces it.
A proof of concept is welcome but not required — a clear description of the
mechanism is usually enough.

## What this software can do, so you can judge what a bug in it is worth

It reconfigures a USB HID device, and two of the things it can do outlast the
program:

- **Settings are written to the mouse**, not to a file the mouse reads. The
  mouse keeps them when the app is closed, when the PC is rebooted, and when
  this software is uninstalled.
- **Macros are uploaded into the mouse's own firmware.** A key macro bound to a
  button keeps running with nothing installed at all — a fresh Windows install
  will still have it, because it lives in the mouse.

So the interesting failure here is not "the app misbehaves while it is open".
It is "something got a keystroke macro into the hardware", and that is the case
the defences below are built around.

## Loopback is not a security boundary

The interface is a local web server on `127.0.0.1:7332`. Binding to the
loopback address keeps the *network* out. It does nothing about the browser
already running on the same machine: any page you visit can make requests to
`http://127.0.0.1:7332/` in the background, and they arrive from the loopback
address like every other request.

On this API that is not a nuisance. `/api/reset` wipes the mouse, and
`/api/macro/save` plus `/api/macro/flash` write a keystroke macro into the
firmware and bind it to a button — so a page you merely visited could leave you
with a mouse button that types whatever it chose, in any application, surviving
reboots and the removal of this software.

Two checks close that, in `_request_allowed` in `server.py`, and neither can be
forged by a web page:

- **Origin.** Browsers set it on every cross-origin request and a script cannot
  remove or change it. Anything carrying an Origin that is not this server is
  refused. A request with *no* Origin is a program rather than a page — curl,
  the CLI, a script — and is allowed, because a web page cannot produce that
  and refusing it would break scripting for nothing.
- **Host.** Without this, an attacker can point a name they own at `127.0.0.1`
  after their page loads — DNS rebinding — and the browser then treats their
  page as same-origin with this server, sends no Origin, and can read every
  response. Requiring a loopback Host means their name never matches.

`tests/test_origin.py` is the table of cases, including both attacks above.
Run it before changing that rule; it will tell you which case you changed.

## What is deliberately not defended

- **Anything already running as your user.** A program on your machine can open
  the HID device itself and talk to the mouse directly. It does not need this
  software and nothing this software does can stop it. That is a property of
  user-space HID access on Windows, not a gap here.
- **Physical access.** Same reasoning, more so.
- **The state file.** `%APPDATA%\attackshark.json` is protected by file
  permissions and nothing else. Anything that can write it can change what gets
  pushed to the mouse the next time the app starts.

## The kernel driver

`driver/asxfilter/` is a KMDF filter for the mouse stack. It **has never been
loaded** — Secure Boot blocked test signing on the machine it was written on,
so every statement about its runtime behaviour is a design claim rather than an
observation. `docs/KNOWN_ISSUES.md` §2 says exactly what is unverified.

Installing it means turning off Secure Boot and enabling test signing, which
has consequences beyond this project — a desktop watermark, and some kernel
anti-cheat products refuse to run at all in that mode. A filter on the mouse
stack is also a far louder signal than the injected flag it removes.

If you install it anyway, read `docs/DRIVER.md` §10 **first**. A mouse filter
that misbehaves can leave you without a working mouse, and the recovery is much
easier to read before that happens than after.

## Supported versions

The latest release. This is a single-maintainer project; there are no
backports.

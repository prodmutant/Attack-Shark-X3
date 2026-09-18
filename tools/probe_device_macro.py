"""Find out what the X3's firmware macro engine can actually do.

The captured macro (`PROTOCOL.md` §8) contained only keystrokes, so the only
event types ever observed were `0x01` press and `0x81` release. That is two
values out of 256, and the conclusion drawn from it - "the firmware cannot move
the mouse" - was an inference, not a measurement. This measures it.

Why it matters: a macro that runs on the device produces genuine HID reports
from the mouse. No injection flag, no driver, no kernel, no test signing, no
reboot, and Secure Boot is irrelevant, because the mouse really did move. If
the firmware has a movement opcode, that is the whole problem solved. If it has
not, we will know rather than assume.

    python tools/probe_device_macro.py --selftest
    python tools/probe_device_macro.py --probe 0x02-0x33
    python tools/probe_device_macro.py --restore

Method: upload a macro whose events carry candidate type bytes, point a button
at it, then watch Raw Input while you press that button. Raw Input reports the
device each event came from, so anything the mouse emits arrives attributed to
the X3 - and a low-level hook confirms it carries no injected flag.

Each candidate's value byte is set equal to its type byte, so if type 0x04
turns out to mean "move X", the pointer moves by exactly 4 and the delta names
the opcode that produced it. One press tests up to 50 candidates.

Safety: the button's previous action is saved to disk before anything is
changed and put back by --restore (or automatically at the end of a run). Only
the event *type* byte is varied, inside a block whose structure, count and
checksum are the confirmed-good ones, so the firmware is never handed a
malformed block.
"""
from __future__ import annotations

import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attackshark import macro as M                    # noqa: E402
from attackshark import protocol as P                 # noqa: E402
from attackshark.device import AttackSharkX3          # noqa: E402

STASH = os.path.join(os.path.expanduser("~"), ".attackshark_probe.json")

u32 = C.WinDLL("user32", use_last_error=True)
k32 = C.WinDLL("kernel32", use_last_error=True)

WM_INPUT = 0x00FF
WH_MOUSE_LL, WH_KEYBOARD_LL = 14, 13
LLMHF_INJECTED = 0x00000001
LLKHF_INJECTED = 0x00000010
RID_INPUT = 0x10000003
RIDI_DEVICENAME = 0x20000007
RIDEV_INPUTSINK = 0x00000100
RIM_TYPEMOUSE, RIM_TYPEKEYBOARD = 0, 1

ULONG_PTR = C.c_ulonglong if C.sizeof(C.c_void_p) == 8 else C.c_ulong


class MSLLHOOKSTRUCT(C.Structure):
    _fields_ = [("pt", W.POINT), ("mouseData", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KBDLLHOOKSTRUCT(C.Structure):
    _fields_ = [("vkCode", W.DWORD), ("scanCode", W.DWORD), ("flags", W.DWORD),
                ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]


class RAWINPUTDEVICE(C.Structure):
    _fields_ = [("usUsagePage", C.c_ushort), ("usUsage", C.c_ushort),
                ("dwFlags", W.DWORD), ("hwndTarget", W.HWND)]


class RAWINPUTHEADER(C.Structure):
    _fields_ = [("dwType", W.DWORD), ("dwSize", W.DWORD),
                ("hDevice", W.HANDLE), ("wParam", W.WPARAM)]


class _BTN(C.Structure):
    _fields_ = [("usButtonFlags", C.c_ushort), ("usButtonData", C.c_ushort)]


class _BU(C.Union):
    _fields_ = [("ulButtons", W.DWORD), ("b", _BTN)]


class RAWMOUSE(C.Structure):
    _fields_ = [("usFlags", C.c_ushort), ("u", _BU), ("ulRawButtons", W.DWORD),
                ("lLastX", C.c_long), ("lLastY", C.c_long),
                ("ulExtraInformation", W.DWORD)]


class RAWKEYBOARD(C.Structure):
    _fields_ = [("MakeCode", C.c_ushort), ("Flags", C.c_ushort),
                ("Reserved", C.c_ushort), ("VKey", C.c_ushort),
                ("Message", C.c_uint), ("ExtraInformation", W.DWORD)]


class _RIU(C.Union):
    _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD)]


class RAWINPUT(C.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("u", _RIU)]


class WNDCLASS(C.Structure):
    _fields_ = [("style", W.UINT), ("lpfnWndProc", C.c_void_p),
                ("cbClsExtra", C.c_int), ("cbWndExtra", C.c_int),
                ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH),
                ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]


HOOKPROC = C.WINFUNCTYPE(C.c_longlong, C.c_int, W.WPARAM, W.LPARAM)
WNDPROC = C.WINFUNCTYPE(C.c_longlong, W.HWND, C.c_uint, W.WPARAM, W.LPARAM)

u32.SetWindowsHookExW.argtypes = [C.c_int, HOOKPROC, W.HINSTANCE, W.DWORD]
u32.SetWindowsHookExW.restype = C.c_void_p
u32.CallNextHookEx.argtypes = [C.c_void_p, C.c_int, W.WPARAM, W.LPARAM]
u32.CallNextHookEx.restype = C.c_longlong
u32.UnhookWindowsHookEx.argtypes = [C.c_void_p]
u32.GetRawInputData.argtypes = [W.HANDLE, W.UINT, C.c_void_p,
                                C.POINTER(W.UINT), W.UINT]
u32.GetRawInputDeviceInfoW.argtypes = [W.HANDLE, W.UINT, C.c_void_p, C.POINTER(W.UINT)]
u32.RegisterRawInputDevices.argtypes = [C.POINTER(RAWINPUTDEVICE), W.UINT, W.UINT]
u32.DefWindowProcW.argtypes = [W.HWND, C.c_uint, W.WPARAM, W.LPARAM]
u32.DefWindowProcW.restype = C.c_longlong

_names = {}


def device_name(h):
    if not h:
        return "(none - synthetic)"
    if h not in _names:
        size = W.UINT(0)
        u32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, None, C.byref(size))
        buf = C.create_unicode_buffer(size.value + 1)
        u32.GetRawInputDeviceInfoW(h, RIDI_DEVICENAME, buf, C.byref(size))
        _names[h] = buf.value or f"(handle {h})"
    return _names[h]


class Watcher:
    """Raw Input for mouse and keyboard, plus the injected-flag hook."""

    def __init__(self):
        self.events = []
        self.lock = threading.Lock()
        self.injected = 0
        self.clean = 0
        self.key_injected = 0
        self.key_clean = 0
        self._hwnd = None
        self._hook = None
        self._tid = None
        self._ready = threading.Event()
        self._hp = HOOKPROC(self._on_hook)
        self._kp = HOOKPROC(self._on_key_hook)
        self._khook = None
        self._wp = WNDPROC(self._on_msg)
        self._t = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._t.start()
        self._ready.wait(5)

    def stop(self):
        if self._tid:
            u32.PostThreadMessageW(self._tid, 0x0012, 0, 0)
        self._t.join(2)

    def reset(self):
        with self.lock:
            self.events.clear()
            self.injected = self.clean = 0
            self.key_injected = self.key_clean = 0

    def _on_hook(self, code, wparam, lparam):
        if code == 0 and wparam == 0x0200:          # WM_MOUSEMOVE
            ms = C.cast(lparam, C.POINTER(MSLLHOOKSTRUCT)).contents
            with self.lock:
                if ms.flags & LLMHF_INJECTED:
                    self.injected += 1
                else:
                    self.clean += 1
        return u32.CallNextHookEx(None, code, wparam, lparam)

    def _on_key_hook(self, code, wparam, lparam):
        """The decisive check: SendInput sets LLKHF_INJECTED, hardware does not."""
        if code == 0:
            kb = C.cast(lparam, C.POINTER(KBDLLHOOKSTRUCT)).contents
            with self.lock:
                if kb.flags & LLKHF_INJECTED:
                    self.key_injected += 1
                else:
                    self.key_clean += 1
        return u32.CallNextHookEx(None, code, wparam, lparam)

    def _on_msg(self, hwnd, msg, wparam, lparam):
        if msg == WM_INPUT:
            size = W.UINT(0)
            u32.GetRawInputData(lparam, RID_INPUT, None, C.byref(size),
                                C.sizeof(RAWINPUTHEADER))
            buf = C.create_string_buffer(size.value)
            got = u32.GetRawInputData(lparam, RID_INPUT, buf, C.byref(size),
                                      C.sizeof(RAWINPUTHEADER))
            if got and got != 0xFFFFFFFF:
                ri = C.cast(buf, C.POINTER(RAWINPUT)).contents
                dev = device_name(ri.header.hDevice)
                with self.lock:
                    if ri.header.dwType == RIM_TYPEMOUSE:
                        m = ri.u.mouse
                        if m.lLastX or m.lLastY or m.u.b.usButtonFlags:
                            self.events.append(
                                {"k": "mouse", "dx": m.lLastX, "dy": m.lLastY,
                                 "btn": m.u.b.usButtonFlags,
                                 "data": m.u.b.usButtonData, "dev": dev})
                    elif ri.header.dwType == RIM_TYPEKEYBOARD:
                        kb = ri.u.keyboard
                        if kb.Message in (0x0100, 0x0104):      # keydown only
                            self.events.append(
                                {"k": "key", "vk": kb.VKey,
                                 "scan": kb.MakeCode, "dev": dev})
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self):
        self._tid = k32.GetCurrentThreadId()
        cls = WNDCLASS()
        cls.lpfnWndProc = C.cast(self._wp, C.c_void_p)
        cls.hInstance = k32.GetModuleHandleW(None)
        cls.lpszClassName = "AsxProbeSink"
        u32.RegisterClassW(C.byref(cls))
        self._hwnd = u32.CreateWindowExW(0, "AsxProbeSink", "AsxProbeSink",
                                         0, 0, 0, 0, 0, W.HWND(-3), None,
                                         cls.hInstance, None)
        rids = (RAWINPUTDEVICE * 2)(
            RAWINPUTDEVICE(1, 2, RIDEV_INPUTSINK, self._hwnd),   # mouse
            RAWINPUTDEVICE(1, 6, RIDEV_INPUTSINK, self._hwnd),   # keyboard
        )
        if not u32.RegisterRawInputDevices(rids, 2, C.sizeof(RAWINPUTDEVICE)):
            print(f"  warning: RegisterRawInputDevices failed ({C.get_last_error()})")
        self._hook = u32.SetWindowsHookExW(WH_MOUSE_LL, self._hp, None, 0)
        self._khook = u32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kp, None, 0)
        self._ready.set()
        msg = W.MSG()
        while u32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(C.byref(msg))
            u32.DispatchMessageW(C.byref(msg))
        for h in (self._hook, self._khook):
            if h:
                u32.UnhookWindowsHookEx(h)


# ------------------------------------------------------------- the device ---
def raw_block(events, slot=2):
    """A 128-byte macro block from raw (type, value) pairs.

    macro.py only emits key events; this bypasses it so the type byte can be
    anything, while keeping the layout, count and checksum that the captured
    block confirmed.
    """
    if len(events) > M.DEV_MAX_EVENTS:
        raise ValueError(f"at most {M.DEV_MAX_EVENTS} events fit in the block")
    buf = bytearray(M.DEV_PAYLOAD_LEN)
    buf[0] = slot & 0xFF        # macro slot/ID; 0 appears to mean "unset"
    buf[4] = 0x01
    buf[M.DEV_COUNT_AT] = len(events)
    for i, (t, v) in enumerate(events):
        buf[M.DEV_EVENTS_AT + i * 2] = t & 0xFF
        buf[M.DEV_EVENTS_AT + i * 2 + 1] = v & 0xFF
    total = sum(buf[0:M.DEV_PAYLOAD_LEN - 2]) & 0xFFFF
    buf[M.DEV_PAYLOAD_LEN - 2] = (total >> 8) & 0xFF
    buf[M.DEV_PAYLOAD_LEN - 1] = total & 0xFF
    return bytes(buf)


def raw_block_bytes(evbytes, count, slot=2):
    """A block whose event area is arbitrary bytes.

    raw_block() assumes the confirmed 2-byte key event. A movement event is a
    different shape - the Attack Shark v4 driver decodes mouse movement as a
    4-byte record [0xF9, delay, dx, dy], with dx/dy signed - so the event area
    has to be written verbatim and the count set independently. The firmware
    parses events sequentially by type, so a wrong type byte desyncs the rest;
    that is why one candidate is tested at a time rather than a mixed batch.
    """
    room = M.DEV_PAYLOAD_LEN - 2 - M.DEV_EVENTS_AT
    if len(evbytes) > room:
        raise ValueError(f"{len(evbytes)} bytes of events, only {room} fit")
    buf = bytearray(M.DEV_PAYLOAD_LEN)
    buf[0] = slot & 0xFF
    buf[4] = 0x01
    buf[M.DEV_COUNT_AT] = count & 0xFF
    buf[M.DEV_EVENTS_AT:M.DEV_EVENTS_AT + len(evbytes)] = evbytes
    total = sum(buf[0:M.DEV_PAYLOAD_LEN - 2]) & 0xFFFF
    buf[M.DEV_PAYLOAD_LEN - 2] = (total >> 8) & 0xFF
    buf[M.DEV_PAYLOAD_LEN - 1] = total & 0xFF
    return bytes(buf)


def upload_raw(mouse, evbytes, count, button, slot=2):
    block = raw_block_bytes(evbytes, count, slot)
    buttons = list(mouse.state["buttons"])
    buttons[P.BUTTON_SLOT[button]] = list(M.button_slot_entry(0))
    mouse.state["buttons"] = buttons
    with mouse:
        mouse._send(P.build_commit(), "commit")
        mouse._send(mouse._sensor_packet(), "sensor")
        mouse._send(mouse._power_packet(), "power")
        mouse._send(P.build_polling(mouse.state["polling_hz"]), "polling")
        mouse._send(P.build_buttons(buttons), "buttons")
        for c in chunks_for(block):
            mouse._send(c, "probe macro chunk")
    mouse.save()


def chunks_for(block):
    out = []
    n = (len(block) + M.DEV_CHUNK_PAYLOAD - 1) // M.DEV_CHUNK_PAYLOAD
    for i in range(n):
        part = block[i * M.DEV_CHUNK_PAYLOAD:(i + 1) * M.DEV_CHUNK_PAYLOAD]
        pkt = bytearray(M.DEV_CHUNK)
        pkt[0] = M.DEV_REPORT
        pkt[1] = len(part) + 4
        pkt[2] = M.DEV_BLOCK
        pkt[3] = i
        pkt[4:4 + len(part)] = part
        out.append(bytes(pkt))
    return out


def stash_button(mouse, button):
    """Remember the button's current action, once, before we touch it."""
    if os.path.exists(STASH):
        return
    slot = P.BUTTON_SLOT[button]
    with open(STASH, "w", encoding="utf-8") as fh:
        json.dump({"button": button, "slot": slot,
                   "value": mouse.state["buttons"][slot],
                   "buttons": mouse.state["buttons"]}, fh)


def upload(mouse, events, button, slot=2):
    """Push a macro block and point `button` at it.

    Order matters, and it is not the obvious one. The vendor tool sends its
    whole configuration burst first - commit, sensor, power, polling, then the
    button map already carrying the macro action - and only then the report
    0x09 chunks:

        0c 04 05 06 08  09/0 09/1 09/2

    That is what `captures/macro_assign.jsonl` shows, so that is what we do.
    Uploading the chunks first and the button map afterwards might well work,
    but a failure would be ambiguous: we could not tell an unsupported event
    type from a sequence the firmware simply ignored.
    """
    block = raw_block(events, slot)

    buttons = list(mouse.state["buttons"])
    buttons[P.BUTTON_SLOT[button]] = list(M.button_slot_entry(0))
    mouse.state["buttons"] = buttons

    with mouse:
        mouse._send(P.build_commit(), "commit")
        mouse._send(mouse._sensor_packet(), "sensor")
        mouse._send(mouse._power_packet(), "power")
        mouse._send(P.build_polling(mouse.state["polling_hz"]), "polling")
        mouse._send(P.build_buttons(buttons), "buttons")
        for c in chunks_for(block):
            mouse._send(c, "probe macro chunk")
    mouse.save()


def restore(quiet=False):
    if not os.path.exists(STASH):
        if not quiet:
            print("nothing stashed; button was never changed")
        return 0
    with open(STASH, encoding="utf-8") as fh:
        s = json.load(fh)
    mouse = AttackSharkX3()
    buttons = list(s["buttons"])
    mouse.state["buttons"] = buttons
    with mouse:
        mouse._send(P.build_buttons(buttons), "buttons restore")
    mouse.save()
    os.remove(STASH)
    if not quiet:
        labels = P.parse_buttons(P.build_buttons(buttons))["buttons"]
        print(f"button {s['button']} restored -> {labels.get(s['button'])}")
    return 0


def collect(watcher, button, seconds, prompt):
    print(f"\n  {prompt}")
    print(f"  Press mouse button {button} now. Waiting {seconds:g}s...", flush=True)
    watcher.reset()
    time.sleep(seconds)
    with watcher.lock:
        return (list(watcher.events), watcher.injected, watcher.clean,
                watcher.key_injected, watcher.key_clean)


def summarise(events, injected, clean, kinjected=0, kclean=0):
    mice = [e for e in events if e["k"] == "mouse"]
    keys = [e for e in events if e["k"] == "key"]
    moves = [e for e in mice if e["dx"] or e["dy"]]
    btns = [e for e in mice if e["btn"]]

    print(f"\n  raw input: {len(moves)} movement, {len(btns)} button, "
          f"{len(keys)} key event(s)")
    print(f"  hook flags: mouse {clean} clean / {injected} injected；"
          .replace("；", ";")
          + f" keyboard {kclean} clean / {kinjected} injected")

    devs = sorted({e["dev"] for e in events})
    for d in devs:
        print(f"    from {d}")

    if keys:
        vks = [e["vk"] for e in keys]
        print(f"  keys seen (VK): {vks[:24]}{' ...' if len(vks) > 24 else ''}")
    if moves:
        print(f"  deltas: {[(e['dx'], e['dy']) for e in moves][:24]}")
    if btns:
        print(f"  button flags: {[hex(e['btn']) for e in btns][:16]}")
    return moves, btns, keys


def cmd_selftest(args):
    """Prove the upload+bind path works, using the known-good captured macro.

    Seven presses and releases of HID usage 0x06 ('c'). If pressing the button
    types ccccccc, then everything about report 0x09 in PROTOCOL.md §8 is
    right and the probe results below can be trusted.
    """
    mouse = AttackSharkX3()
    if not AttackSharkX3.discover():
        print("no X3 detected")
        return 1

    events = []
    for _ in range(7):
        events.append((M.DEV_PRESS, P.HID_KEYS["c"]))
        events.append((M.DEV_RELEASE, P.HID_KEYS["c"]))

    stash_button(mouse, args.button)
    upload(mouse, events, args.button, args.slot)
    print(f"uploaded 7x 'c' into macro slot {args.slot}, bound to button {args.button}")

    w = Watcher()
    w.start()
    try:
        evs, inj, clean, kinj, kclean = collect(
            w, args.button, args.wait,
            "Click into a text field first if you want to see the letters.")
        moves, btns, keys = summarise(evs, inj, clean, kinj, kclean)
    finally:
        w.stop()

    cs = [e for e in keys if e["vk"] == 0x43]       # VK_C
    print()
    if len(cs) >= 7:
        print(f"PASS - the mouse emitted {len(cs)} 'c' keystrokes from firmware.")
        print("       The upload and bind path is confirmed working end to end.")
    elif keys:
        print(f"PARTIAL - {len(keys)} key event(s) but {len(cs)} were 'c'.")
    else:
        print("FAIL - no keystrokes. Either the button was not pressed, the")
        print("       macro did not upload, or action 0x12 is not 'play macro'.")
    if args.restore:
        restore(quiet=True)
        print("button restored")
    return 0


def cmd_watch(args):
    """Watch only. Changes nothing on the mouse.

    For testing whatever is already configured - after a replay, say - without
    our own packets overwriting it first. Keystrokes are reported with the
    device that produced them, so a macro played by the firmware is easy to
    tell from the keyboard under your hands: it arrives from the X3.
    """
    w = Watcher()
    w.start()
    try:
        evs, inj, clean, kinj, kclean = collect(
            w, args.button, args.wait,
            "Press the button whose macro you want to test.")
        moves, btns, keys = summarise(evs, inj, clean, kinj, kclean)
    finally:
        w.stop()

    x3_keys = [e for e in keys if "VID_1D57" in e["dev"]]
    x3_btns = [e for e in btns if "VID_1D57" in e["dev"]]

    print()
    if x3_keys:
        vks = [e["vk"] for e in x3_keys]
        print(f"*** {len(x3_keys)} keystroke(s) came from the X3 itself: {vks}")
        if all(v == 0x43 for v in vks):
            print("    All 'c' - that is the stored macro playing from firmware.")
        print("\nPASS - the firmware played a macro. No driver, no injection.")
    else:
        print("No keystrokes from the X3.")
        if x3_btns:
            seen = 0
            for e in x3_btns:
                seen |= e["btn"]
            print(f"The X3 sent: {ri_names(seen)}")
            print("\nThe button did its ordinary job, so the macro did not play.")
        else:
            print("No button events from the X3 either - was it pressed in time?")
    return 0


def cmd_movetest(args):
    """Can the firmware move the mouse from a macro? The whole question.

    Builds a macro of movement events using one candidate type byte and the
    4-byte [type, delay, dx, dy] layout, and watches for pointer movement
    attributed to the X3 with no injected flag. Movement that the firmware
    generates is genuine hardware input: no driver, no signing, no reboot.
    """
    mouse = AttackSharkX3()
    if not AttackSharkX3.discover():
        print("no X3 detected")
        return 1

    t = args.movetest
    n = args.count
    ev = bytes([t & 0xFF, args.delay & 0xFF,
                args.dx & 0xFF, args.dy & 0xFF]) * n

    stash_button(mouse, args.button)
    upload_raw(mouse, ev, n, args.button, args.slot)

    print(f"macro slot {args.slot}: {n} x [0x{t:02X} 0x{args.delay:02X} "
          f"{args.dx:+d} {args.dy:+d}]  (count={n})")
    print(f"if type 0x{t:02X} means 'move', the pointer should travel about "
          f"{args.dx * n:+d},{args.dy * n:+d} px")

    w = Watcher()
    w.start()
    try:
        evs, inj, clean, kinj, kclean = collect(
            w, args.button, args.wait,
            "Watch the pointer. Movement here comes from the mouse itself.")
        moves, btns, keys = summarise(evs, inj, clean, kinj, kclean)
    finally:
        w.stop()

    x3 = [e for e in moves if "VID_1D57" in e["dev"]]
    big = [e for e in x3 if abs(e["dx"]) >= abs(args.dx) or abs(e["dy"]) >= abs(args.dy)]
    x3_keys = [e for e in keys if "VID_1D57" in e["dev"]]

    print()
    if x3_keys:
        print(f"type 0x{t:02X} was interpreted as a KEY event "
              f"({len(x3_keys)} keystroke(s)) - not movement.")
    elif big:
        print(f"*** MOVEMENT: {len(big)} report(s) at or above the requested step ***")
        print(f"    deltas: {[(e['dx'], e['dy']) for e in big][:12]}")
        print()
        print(f"    Type 0x{t:02X} moves the pointer FROM THE FIRMWARE.")
        print("    No driver, no injection, no test signing, Secure Boot irrelevant.")
    else:
        print(f"type 0x{t:02X} produced no movement and no keystrokes.")
        print("Either it is not a movement opcode, or the firmware ignored it.")
    restore(quiet=True)
    return 0


#: Raw Input ButtonFlags -> what the mouse actually sent
RI_NAMES = {0x0001: "left down", 0x0002: "left up", 0x0004: "right down",
            0x0008: "right up", 0x0010: "middle down", 0x0020: "middle up",
            0x0040: "XBUTTON1 down (back)", 0x0080: "XBUTTON1 up",
            0x0100: "XBUTTON2 down (forward)", 0x0200: "XBUTTON2 up",
            0x0400: "wheel", 0x0800: "hwheel"}


def ri_names(flags):
    return ", ".join(n for b, n in RI_NAMES.items() if flags & b) or hex(flags)


def cmd_sanity(args):
    """Does a plain button remap work at all, with no macro involved?

    The selftest failed with the button still doing its old job, which has two
    very different explanations: either the macro never played, or our button
    map never took effect in the first place. This separates them by remapping
    to an ordinary action - middle click - which needs nothing but report 0x08.

    If the button middle-clicks, the button path is sound and the problem is
    macro-specific. If it still does its old job, the problem is more basic and
    macros were never the issue.
    """
    mouse = AttackSharkX3()
    if not AttackSharkX3.discover():
        print("no X3 detected")
        return 1

    target = "middle_click"
    code = P.ACTION[target]

    stash_button(mouse, args.button)

    buttons = list(mouse.state["buttons"])
    was = buttons[P.BUTTON_SLOT[args.button]]
    buttons[P.BUTTON_SLOT[args.button]] = code
    mouse.state["buttons"] = buttons
    with mouse:
        mouse._send(P.build_commit(), "commit")
        mouse._send(mouse._sensor_packet(), "sensor")
        mouse._send(mouse._power_packet(), "power")
        mouse._send(P.build_polling(mouse.state["polling_hz"]), "polling")
        mouse._send(P.build_buttons(buttons), "buttons")
    mouse.save()

    print(f"button {args.button}: slot {P.BUTTON_SLOT[args.button]} "
          f"{was!r} -> {code} ({target})")

    w = Watcher()
    w.start()
    try:
        evs, inj, clean, kinj, kclean = collect(
            w, args.button, args.wait,
            "Press ONLY button %d. Do not touch the other thumb button."
            % args.button)
        moves, btns, keys = summarise(evs, inj, clean, kinj, kclean)
    finally:
        w.stop()

    x3 = [e for e in btns if "VID_1D57" in e["dev"]]
    print()
    if not x3:
        print("INCONCLUSIVE - no button events from the X3 were seen at all.")
        print("   Was the button pressed inside the window?")
    else:
        seen = 0
        for e in x3:
            seen |= e["btn"]
        print(f"the X3 sent: {ri_names(seen)}")
        if seen & 0x0030:
            print("\nPASS - the remap took effect. Report 0x08 works, so the")
            print("       button path is sound and the macro is the problem.")
        else:
            print("\nFAIL - the button still does its old job, so our button")
            print("       map is not reaching the mouse. That is a more basic")
            print("       problem than macros, and would affect the whole app.")
    restore(quiet=True)
    print("button restored")
    return 0


def cmd_probe(args):
    """Sweep candidate event-type bytes and see what the firmware does."""
    lo, hi = args.probe
    mouse = AttackSharkX3()
    if not AttackSharkX3.discover():
        print("no X3 detected")
        return 1

    candidates = [t for t in range(lo, hi + 1)
                  if t not in (M.DEV_PRESS, M.DEV_RELEASE)]
    if not candidates:
        print("nothing to probe in that range")
        return 1

    w = Watcher()
    w.start()
    findings = {}
    try:
        for start in range(0, len(candidates), M.DEV_MAX_EVENTS):
            batch = candidates[start:start + M.DEV_MAX_EVENTS]
            # value == type, so a movement delta names the opcode that made it
            events = [(t, t) for t in batch]

            stash_button(mouse, args.button)
            upload(mouse, events, args.button, args.slot)
            print(f"\n=== types 0x{batch[0]:02X}..0x{batch[-1]:02X} "
                  f"({len(batch)} candidates) ===")

            evs, inj, clean, kinj, kclean = collect(
                w, args.button, args.wait,
                "Watch the pointer. Any movement means the firmware moved it.")
            moves, btns, keys = summarise(evs, inj, clean, kinj, kclean)

            if moves:
                print("\n  *** MOVEMENT FROM THE DEVICE ***")
                for e in moves:
                    for axis, d in (("x", e["dx"]), ("y", e["dy"])):
                        if d and abs(d) in batch:
                            findings[abs(d)] = f"moves {axis} by the value byte"
                            print(f"      delta {axis}={d:+d} -> type "
                                  f"0x{abs(d):02X} looks like a move opcode")
            if btns:
                print("\n  *** MOUSE BUTTONS FROM THE DEVICE ***")
                for e in btns:
                    print(f"      button flags {hex(e['btn'])}")
    finally:
        w.stop()
        restore(quiet=True)

    print("\n" + "=" * 62)
    if findings:
        print("Firmware event types identified:")
        for t, what in sorted(findings.items()):
            print(f"  0x{t:02X}  {what}")
        print("\nThis is the result that matters: movement generated by the")
        print("mouse itself needs no driver, no signing and no reboot.")
    else:
        print("No movement or button events came from any probed type.")
        print("Keystrokes only, which is consistent with the firmware macro")
        print("engine being keyboard-only. Run the remaining ranges before")
        print("concluding: 0x02-0x7F is the full unexplored space.")
    print("button restored")
    return 0


def parse_range(s):
    if "-" not in s:
        v = int(s, 0)
        return (v, v)
    a, b = s.split("-", 1)
    return (int(a, 0), int(b, 0))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--button", type=int, default=5, choices=[1, 2, 3, 4, 5],
                    help="button to bind the test macro to (default 5, back)")
    ap.add_argument("--wait", type=float, default=8.0,
                    help="seconds to watch after each upload")
    ap.add_argument("--selftest", action="store_true",
                    help="replay the captured 7x'c' macro to validate the path")
    ap.add_argument("--probe", type=parse_range, metavar="LO-HI",
                    help="sweep these event type bytes, e.g. 0x02-0x33")
    ap.add_argument("--movetest", type=lambda v: int(v, 0), metavar="TYPE",
                    help="test one candidate movement opcode, e.g. 0xF9")
    ap.add_argument("--dx", type=int, default=40, help="dx per movement event")
    ap.add_argument("--dy", type=int, default=0, help="dy per movement event")
    ap.add_argument("--count", type=int, default=10, help="movement events")
    ap.add_argument("--delay", type=lambda v: int(v, 0), default=0x02,
                    help="delay byte in each event (default 0x02)")
    ap.add_argument("--slot", type=int, default=2,
                    help="macro slot byte in the block (the vendor used 2; "
                         "0 never played)")
    ap.add_argument("--watch", action="store_true",
                    help="observe only; change nothing on the mouse")
    ap.add_argument("--sanity", action="store_true",
                    help="test a plain button remap, with no macro involved")
    ap.add_argument("--restore", action="store_true",
                    help="put the button back the way it was")
    args = ap.parse_args()

    if args.restore and not (args.selftest or args.probe or args.sanity):
        return restore()
    if args.watch:
        return cmd_watch(args)
    if args.movetest is not None:
        return cmd_movetest(args)
    if args.sanity:
        return cmd_sanity(args)
    if args.selftest:
        return cmd_selftest(args)
    if args.probe:
        return cmd_probe(args)
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Command line front end.

    python -m attackshark info
    python -m attackshark polling 500
    python -m attackshark dpi 800 1600 3200 --active 1
    python -m attackshark flags --lod 1 --motion-sync on
    python -m attackshark power --sleep 0.5 --deep-sleep 10
    python -m attackshark button 5 forward
    python -m attackshark color 0 ff0000
    python -m attackshark apply
    python -m attackshark packets
    python -m attackshark gui
    python -m attackshark app
    python -m attackshark driver
"""
from __future__ import annotations

import argparse
import sys
import time

from . import protocol as P
from .device import AttackSharkX3, DeviceNotFound


def _onoff(v):
    if v is None:
        return None
    s = str(v).lower()
    if s in ("on", "1", "true", "yes"):
        return True
    if s in ("off", "0", "false", "no"):
        return False
    raise argparse.ArgumentTypeError("expected on/off")


def _rgb(s):
    s = s.lstrip("#")
    if len(s) != 6:
        raise argparse.ArgumentTypeError("colour must be RRGGBB hex")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def cmd_info(dev, args):
    found = AttackSharkX3.discover()
    if not found:
        print("No Attack Shark X3 detected.")
        print("  looked for VID 0x%04X, PID 0x%04X (2.4GHz) / 0x%04X (wired)"
              % (P.VENDOR_ID, P.PRODUCT_ID_24G, P.PRODUCT_ID_WIRED))
        return 1
    for d in found:
        print(f"{d['manufacturer']} {d['product']}".strip())
        print(f"  link            {d['link']}")
        print(f"  vid:pid         {d['vid']:04x}:{d['pid']:04x} (rev {d['version']:04x})")
        print(f"  usage page      {d['usage_page']:04x}:{d['usage']:04x}")
        print(f"  feature report  {d['feature_len']} bytes")
        print(f"  path            {d['path']}")
    st = AttackSharkX3().state
    print("\nlocal configuration state:")
    print(f"  dpi stages      {st['dpi']}  (active: stage {st['active_stage']} "
          f"= {st['dpi'][st['active_stage']]} DPI)")
    print(f"  polling         {st['polling_hz']} Hz")
    print(f"  lift-off        {st['lod_mm']} mm")
    print(f"  ripple control  {'on' if st['ripple'] else 'off'}")
    print(f"  angle snap      {'on' if st['angle_snap'] else 'off'}")
    print(f"  motion sync     {'on' if st['motion_sync'] else 'off'}")
    print(f"  sleep           {st['sleep_min']} min / deep {st['deep_sleep_min']} min")
    print(f"  key response    {st.get('key_response_ms', 4)} ms")
    buttons = P.parse_buttons(P.build_buttons(st["buttons"]))["buttons"]
    print("  buttons         " + ", ".join(f"{k}={v}" for k, v in buttons.items()))
    return 0


def cmd_polling(dev, args):
    dev.set_polling(args.rate)
    print(f"polling rate -> {args.rate} Hz")
    return 0


def cmd_dpi(dev, args):
    dev.set_dpi_stages(args.values, active=args.active)
    st = dev.state
    print(f"dpi stages -> {st['dpi']} (active stage {st['active_stage']})")
    return 0


def cmd_stage(dev, args):
    dev.set_active_stage(args.index)
    print(f"active stage -> {args.index} ({dev.state['dpi'][args.index]} DPI)")
    return 0


def cmd_flags(dev, args):
    dev.set_sensor_flags(lod_mm=args.lod, ripple=_onoff(args.ripple),
                         angle_snap=_onoff(args.angle_snap),
                         motion_sync=_onoff(args.motion_sync))
    st = dev.state
    print(f"lod={st['lod_mm']}mm ripple={st['ripple']} "
          f"angle_snap={st['angle_snap']} motion_sync={st['motion_sync']}")
    return 0


def cmd_power(dev, args):
    dev.set_power(sleep_min=args.sleep, deep_sleep_min=args.deep_sleep,
                  key_response_ms=args.key_response)
    st = dev.state
    print(f"sleep {st['sleep_min']} min, deep sleep {st['deep_sleep_min']} min, "
          f"key response {st['key_response_ms']} ms")
    return 0


def cmd_button(dev, args):
    dev.set_button(args.number, args.action)
    print(f"button {args.number} -> {args.action}")
    return 0


def cmd_color(dev, args):
    dev.set_stage_color(args.index, args.rgb)
    print(f"stage {args.index} colour -> #%02x%02x%02x" % args.rgb)
    return 0


def cmd_apply(dev, args):
    dev.apply()
    print("full configuration pushed")
    return 0


def cmd_gui(dev, args):
    from .server import serve
    serve(port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_tray(dev, args):
    from .tray import main as tray_main
    return tray_main(open_browser=not args.no_browser)


def cmd_packets(dev, args):
    for name, pkt in AttackSharkX3().packets().items():
        print(f"{name:<8} {pkt.hex(' ')}")
    return 0


def cmd_app(dev, args):
    """The desktop application - a window, not a browser."""
    from .native import main as app_main
    return app_main()


def cmd_driver(_mouse, args):
    """Report on the filter driver, and optionally move through it."""
    from . import kdriver, motion

    try:
        drv = kdriver.Driver()
    except kdriver.DriverError as e:
        print(f"filter driver: unavailable\n  {e}")
        print("\n  build:   python tools/build_driver.py")
        print("  install: tools\\install_driver.ps1      (needs Secure Boot off)")
        print("  without it, host macros fall back to SendInput, which Windows")
        print("  marks as injected. See docs/DRIVER.md.")
        return 1

    with drv:
        st = drv.status()
        print("filter driver: loaded")
        print(f"  interface       {st['version']}")
        print(f"  attached        {'yes' if st['attached'] else 'no'}")
        print(f"  mouse stack     {'connected' if st['connected'] else 'not connected'}")
        print(f"  queue           {st['queued']}/{st['capacity']}"
              f"   {'playing' if st['playing'] else 'idle'}")
        print(f"  emitted         {st['steps_emitted']} steps"
              + (f", {st['dropped']} dropped" if st["dropped"] else ""))
        print(f"  physical seen   {st['physical_reports']} reports")
        if st["suppress_buttons"]:
            print("  suppressing     "
                  + ", ".join(kdriver.describe_buttons(st["suppress_buttons"])))

        if args.move:
            dx, dy = args.move
            if not st["connected"]:
                print("\nnot attached to the mouse stack - nothing to move")
                return 1
            steps = motion.plan(dx, dy, profile="human")
            ms = round(sum(s[0] for s in steps) * 1000)
            print(f"\nmoving {dx:+d},{dy:+d} as {len(steps)} reports over {ms} ms")
            drv.move(steps)
            time.sleep(ms / 1000.0 + 0.25)
            print("done - verify with: python tools/verify_injection.py")
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="attackshark",
                                 description="Open driver for the Attack Shark X3")
    ap.add_argument("--state", help="path to the local state file")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="show device and current settings").set_defaults(
        fn=cmd_info, needs_device=False)

    p = sub.add_parser("polling", help="set polling rate")
    p.add_argument("rate", type=int, choices=sorted(P.POLLING_RATES))
    p.set_defaults(fn=cmd_polling, needs_device=True)

    p = sub.add_parser("dpi", help="set the DPI stage table")
    p.add_argument("values", type=int, nargs="+")
    p.add_argument("--active", type=int, default=None)
    p.set_defaults(fn=cmd_dpi, needs_device=True)

    p = sub.add_parser("stage", help="select the active DPI stage")
    p.add_argument("index", type=int)
    p.set_defaults(fn=cmd_stage, needs_device=True)

    p = sub.add_parser("flags", help="sensor toggles")
    p.add_argument("--lod", type=int, choices=(1, 2))
    p.add_argument("--ripple")
    p.add_argument("--angle-snap", dest="angle_snap")
    p.add_argument("--motion-sync", dest="motion_sync")
    p.set_defaults(fn=cmd_flags, needs_device=True)

    p = sub.add_parser("power", help="sleep timers")
    p.add_argument("--sleep", type=float, help="0.5 .. 30 minutes")
    p.add_argument("--deep-sleep", dest="deep_sleep", type=int, help="1 .. 60 minutes")
    p.add_argument("--key-response", dest="key_response", type=int,
                   help="debounce, 2 .. 50 ms in 2 ms steps")
    p.set_defaults(fn=cmd_power, needs_device=True)

    p = sub.add_parser("button", help="reassign a button")
    p.add_argument("number", type=int, choices=sorted(P.BUTTON_SLOT))
    p.add_argument("action", choices=sorted(P.ACTION))
    p.set_defaults(fn=cmd_button, needs_device=True)

    p = sub.add_parser("color", help="set a DPI stage indicator colour")
    p.add_argument("index", type=int)
    p.add_argument("rgb", type=_rgb)
    p.set_defaults(fn=cmd_color, needs_device=True)

    sub.add_parser("apply", help="push the whole configuration").set_defaults(
        fn=cmd_apply, needs_device=True)
    sub.add_parser("packets", help="print the bytes apply would send").set_defaults(
        fn=cmd_packets, needs_device=False)

    p = sub.add_parser("tray", help="run in the system tray (what the exe does)")
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_tray, needs_device=False)

    p = sub.add_parser("gui", help="open the local web interface")
    p.add_argument("--port", type=int, default=7332)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(fn=cmd_gui, needs_device=False)

    sub.add_parser("app", help="open the desktop application").set_defaults(
        fn=cmd_app, needs_device=False)

    p = sub.add_parser("driver", help="filter driver status (real mouse movement)")
    p.add_argument("--move", nargs=2, type=int, metavar=("DX", "DY"),
                   help="emit a movement through the driver, as a test")
    p.set_defaults(fn=cmd_driver, needs_device=False)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    kwargs = {"state_path": args.state} if args.state else {}
    if not args.needs_device:
        return args.fn(None, args)
    try:
        with AttackSharkX3(**kwargs) as dev:
            return args.fn(dev, args)
    except DeviceNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

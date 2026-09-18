"""Local web UI for the Attack Shark X3.

Stdlib only: http.server plus json. The browser gets a static page and talks to
a small JSON API that drives attackshark.device. Bound to 127.0.0.1 - this
exposes control of a USB device, so it must never listen on a routable address.

Binding to the loopback address keeps the network out. It does **not** keep
other websites out, and that distinction is the whole of `_request_allowed`
below - see the comment there before changing anything about it.
"""
from __future__ import annotations

import json
import mimetypes
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import hostrun
from . import macro as M
from . import protocol as P
from .device import AttackSharkX3, DeviceNotFound
from .hostrun import ENGINE, TRIGGERS

def _web_root():
    """PyInstaller unpacks bundled data to _MEIPASS; source runs in place."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "attackshark", "web")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


WEB_ROOT = _web_root()
DEFAULT_PORT = 7332

# One lock around the device: the HID handle is not reentrant and the UI can
# fire overlapping requests while a slider is being dragged.
_lock = threading.Lock()


def _catalog():
    """Everything the front end needs to render controls without hardcoding."""
    return {
        "actions": sorted(P.ACTION),
        "action_codes": {k: v for k, v in sorted(P.ACTION.items())},
        "buttons": sorted(P.BUTTON_SLOT),
        "polling_rates": sorted(P.POLLING_RATES, reverse=True),
        "modifiers": ["ctrl", "shift", "alt", "win"],
        "keys": sorted(P.HID_KEYS),
        "dpi": {"min": P.DPI_MIN, "max": P.DPI_MAX, "step": P.DPI_STEP,
                "slots": P.DPI_SLOTS},
        "logo_round": os.path.exists(os.path.join(WEB_ROOT, "logo.round")),
        "macro": {
            "buttons": sorted(TRIGGERS),
            "step_types": ["key", "mouse", "move", "wheel", "delay"],
            "mouse_buttons": list(M.BUTTONS),
            "repeat_modes": list(M.REPEAT_MODES),
            "max_steps": M.MAX_STEPS,
        },
        "limits": {
            "sleep_min": [0.5, 30.0], "deep_sleep_min": [1, 60],
            "key_response_ms": [2, 50],
        },
    }


def _device_info():
    try:
        found = AttackSharkX3.discover()
    except Exception as e:                     # enumeration should never 500
        return {"connected": False, "error": str(e)}
    if not found:
        return {"connected": False}
    d = found[0]
    return {
        "connected": True,
        "link": d["link"],
        "product": d["product"] or "Attack Shark X3",
        "vid": f"{d['vid']:04x}",
        "pid": f"{d['pid']:04x}",
        "version": f"{d['version']:04x}",
        "feature_len": d["feature_len"],
    }


_battery_cache = {"value": None, "at": 0.0}

#: Kept beside the config rather than in it: a reading is observed data, not
#: a setting, and it must never be able to disturb the profile.
def _battery_cache_path():
    return AttackSharkX3().state_path + ".battery"


def _load_battery_cache():
    """A reading from the last run, so the meter is not blank at startup.

    The mouse pushes status on its own schedule and can take seconds to say
    anything, which left the header showing a dash every time the app opened.
    """
    try:
        with open(_battery_cache_path(), encoding="utf-8") as fh:
            saved = json.load(fh)
        if isinstance(saved, dict) and "value" in saved:
            _battery_cache["value"] = saved["value"]
            _battery_cache["at"] = float(saved.get("at") or 0.0)
    except (OSError, ValueError, TypeError):
        pass


def _save_battery_cache():
    try:
        tmp = _battery_cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"value": _battery_cache["value"],
                       "at": _battery_cache["at"]}, fh)
        os.replace(tmp, _battery_cache_path())
    except OSError:
        pass


def _battery():
    """Whatever the monitor last heard. Never blocks a request."""
    v = _battery_cache["value"]
    if v is None:
        return None
    age = time.time() - _battery_cache["at"]
    return dict(v, age=round(age, 1), stale=age > 180)


def _status_monitor(interval=25.0):
    """The mouse pushes battery rather than answering reads, and it emits one
    shortly after its status collection is opened. So: open, take a report,
    close, wait. Runs in the background so the UI is never held up."""
    while True:
        try:
            st = AttackSharkX3().read_status(timeout=8.0)
            # never let a placeholder overwrite a real reading
            if st and st.get("kind") == "battery" and st.get("plausible"):
                _battery_cache["value"] = st
                _battery_cache["at"] = time.time()
                _save_battery_cache()
        except Exception:
            pass
        time.sleep(interval)


def _snapshot(dev_state):
    """State plus the exact bytes we would send, so the UI can show them."""
    mouse = AttackSharkX3()
    mouse.state = dev_state
    packets = {k: v.hex(" ") for k, v in mouse.packets().items()}
    labels = {}
    for btn, slot in P.BUTTON_SLOT.items():
        entry = dev_state["buttons"][slot]
        trio = (entry, 0, 0) if isinstance(entry, int) else tuple(entry)
        labels[str(btn)] = P.describe_slot(trio)
    macros = []
    for raw in dev_state.get("macros", []):
        try:
            macros.append(M.summarise(M.validate(raw)))
        except M.MacroError:
            pass
    return {"state": dev_state, "packets": packets, "button_labels": labels,
            "device": _device_info(), "catalog": _catalog(),
            "battery": _battery(), "macros": macros,
            "engine": ENGINE.status()}


# Which report each field lives in, so a patch only pushes what changed.
_GROUPS = {
    "sensor": {"dpi", "active_stage", "enabled_mask", "lod_mm", "ripple",
               "angle_snap", "motion_sync", "colors"},
    "power": {"sleep_min", "deep_sleep_min", "key_response_ms"},
    "polling": {"polling_hz"},
    "buttons": {"buttons"},
}


def _log_patch(patch, note=""):
    """Record every incoming mutation. Settings drifted twice with no obvious
    cause; without the actual payload there is no way to find the culprit."""
    try:
        import time as _t
        line = f"{_t.strftime('%Y-%m-%d %H:%M:%S')} PATCH {note} {patch}\n"
        with open(AttackSharkX3().state_path + ".log", "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def apply_patch(patch):
    """Merge a patch into local state and push only the affected reports."""
    _log_patch(patch)
    mouse = AttackSharkX3()

    # button_set is sugar: {"button_set": {"5": "ctrl+shift+s"}}
    if "button_set" in patch:
        buttons = list(mouse.state["buttons"])
        for btn, action in patch.pop("button_set").items():
            slot = P.BUTTON_SLOT[int(btn)]
            if isinstance(action, str) and action not in P.ACTION:
                buttons[slot] = list(P.shortcut(action))
            elif isinstance(action, str):
                buttons[slot] = P.ACTION[action]
            else:
                buttons[slot] = action
        patch["buttons"] = buttons

    touched = {g for g, fields in _GROUPS.items() if fields & set(patch)}
    mouse.state.update(patch)
    if "dpi" in patch and "enabled_mask" not in patch:
        mouse.state["enabled_mask"] = (1 << len(mouse.state["dpi"])) - 1
    mouse.state["active_stage"] = min(mouse.state["active_stage"],
                                      len(mouse.state["dpi"]) - 1)

    # Validate by building every packet before sending any of them.
    built = mouse.packets()

    with _lock:
        with mouse:
            mouse._send(built["commit"], "commit")
            for group in ("sensor", "power", "polling", "buttons"):
                if group in touched:
                    mouse._send(built[group], group)
        mouse.save()
    return _snapshot(mouse.state)


def push_all():
    mouse = AttackSharkX3()
    with _lock:
        with mouse:
            mouse.apply()
    return _snapshot(mouse.state)


def reset_factory():
    from .device import FACTORY
    mouse = AttackSharkX3()
    mouse.state = json.loads(json.dumps(FACTORY))
    mouse.state["colors"] = [tuple(c) for c in mouse.state["colors"]]
    with _lock:
        with mouse:
            mouse.apply()
    return _snapshot(mouse.state)


def _sync_engine(mouse):
    """Make the live engine match what is stored."""
    by_id = {}
    for raw in mouse.state.get("macros", []):
        try:
            mac = M.validate(raw)
        except M.MacroError:
            continue
        by_id[mac["id"]] = mac
    ENGINE.bindings.clear()
    ENGINE.passthrough.clear()
    for btn, bind in (mouse.state.get("macro_bindings") or {}).items():
        mac = by_id.get((bind or {}).get("id"))
        if mac:
            ENGINE.bind(int(btn), mac, bool(bind.get("passthrough")))
    if mouse.state.get("engine_on"):
        ENGINE.start()
    else:
        ENGINE.stop()


def macro_save(payload):
    mac = M.validate(payload.get("macro") or {})
    mouse = AttackSharkX3()
    macros = [m for m in mouse.state.get("macros", []) if m.get("id") != mac["id"]]
    macros.append(mac)
    mouse.state["macros"] = macros
    mouse.save()
    _sync_engine(mouse)
    return _snapshot(mouse.state)


def macro_delete(payload):
    mid = payload.get("id")
    mouse = AttackSharkX3()
    mouse.state["macros"] = [m for m in mouse.state.get("macros", [])
                             if m.get("id") != mid]
    binds = mouse.state.get("macro_bindings") or {}
    mouse.state["macro_bindings"] = {b: v for b, v in binds.items()
                                     if (v or {}).get("id") != mid}
    mouse.save()
    _sync_engine(mouse)
    return _snapshot(mouse.state)


def macro_bind(payload):
    btn = str(int(payload.get("button")))
    mouse = AttackSharkX3()
    binds = dict(mouse.state.get("macro_bindings") or {})
    if payload.get("id"):
        binds[btn] = {"id": payload["id"],
                      "passthrough": bool(payload.get("passthrough"))}
    else:
        binds.pop(btn, None)
    mouse.state["macro_bindings"] = binds
    mouse.save()
    _sync_engine(mouse)
    return _snapshot(mouse.state)


def macro_engine(payload):
    mouse = AttackSharkX3()
    mouse.state["engine_on"] = bool(payload.get("on"))
    mouse.save()
    _sync_engine(mouse)
    if mouse.state["engine_on"] and not ENGINE.active:
        raise OSError(ENGINE.last_error or "could not install the input hook")
    return _snapshot(mouse.state)


def _find_macro(mouse, mid):
    for raw in mouse.state.get("macros", []):
        if raw.get("id") == mid:
            return M.validate(raw)
    raise ValueError("no such macro")


def _preview(mac, stop):
    """Play one macro on the best backend available, then clean up.

    If the engine is running it already owns a driver handle; otherwise open a
    temporary one so a preview looks the same as the real thing. The handle has
    to outlive the playback, because closing it stops whatever is in flight.
    """
    own = None
    drv = ENGINE.driver
    if drv is None and hostrun.kdriver is not None:
        try:
            own = drv = hostrun.kdriver.Driver()
        except hostrun.kdriver.DriverError:
            drv = None
    try:
        hostrun.play(mac, stop, drv)
    finally:
        if own is not None:
            own.close()


def macro_test(payload):
    """Play a macro once, right now, so the editor can preview it."""
    mouse = AttackSharkX3()
    mac = _find_macro(mouse, payload.get("id"))
    mac["repeat"], mac["count"] = "once", 1
    stop = threading.Event()
    threading.Thread(target=_preview, args=(mac, stop), daemon=True).start()
    return {"ok": True, "played": mac["name"],
            "backend": "kernel" if (ENGINE.driver or hostrun.kdriver
                                    and hostrun.kdriver.available()) else "sendinput"}


def macro_flash(payload):
    """Upload a key-only macro into the mouse and point a button at it."""
    mouse = AttackSharkX3()
    mac = _find_macro(mouse, payload.get("id"))
    ok, why = M.device_support(mac)
    if not ok:
        raise ValueError(why)
    chunks = M.build_device_upload(mac)
    btn = payload.get("button")
    with _lock:
        with mouse:
            for c in chunks:
                mouse._send(c, "macro chunk")
            if btn is not None:
                buttons = list(mouse.state["buttons"])
                buttons[P.BUTTON_SLOT[int(btn)]] = list(M.button_slot_entry(0))
                mouse.state["buttons"] = buttons
                mouse._send(P.build_buttons(buttons), "buttons")
        mouse.save()
    return _snapshot(mouse.state)


LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def _request_allowed(host, origin, port):
    """Whether a request may be served, from its Host and Origin headers.

    Listening on 127.0.0.1 stops anything on the network reaching this server.
    It does nothing about the browser already running on this machine: any page
    you visit can post to http://127.0.0.1:7332/ in the background, and the
    request arrives from the loopback address like any other.

    That matters more here than it would for most local servers, because of
    what this particular API can do. `/api/reset` wipes the mouse. Worse,
    `/api/macro/save` and `/api/macro/flash` write a **keystroke macro into the
    firmware** and bind it to a button - so a page you merely visited could
    leave a mouse button that types whatever it chose, in any application,
    surviving reboots and the removal of this software, because it lives in the
    mouse rather than on the PC.

    Two headers close it, and neither can be forged by a web page:

    * **Origin.** The browser sets it on every cross-origin request and a
      script cannot change or remove it. A request carrying an Origin that is
      not this server is a page attacking us, so it is refused. A request with
      no Origin at all is a program, not a page - curl, the CLI, a script - and
      those are allowed through, because none of them is the attacker this is
      defending against and refusing them would break scripting for no gain.

    * **Host.** Without checking it, an attacker can point a name they own at
      127.0.0.1 after the page loads - DNS rebinding - and from then on the
      browser considers their page *same origin* with this server, so it sends
      no Origin at all and can read every response. Requiring the Host to be a
      loopback name means their domain never matches and the rebind is inert.
    """
    name = (host or "").strip()
    if not name.endswith("]"):        # "[::1]" carries no port; "[::1]:7332" does
        name = name.rsplit(":", 1)[0]
    if name.lower() not in LOCAL_HOSTS:
        return False
    if origin:
        allowed = {f"http://{h}:{port}" for h in ("127.0.0.1", "localhost")}
        allowed.add(f"http://[::1]:{port}")
        if origin not in allowed:
            return False
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "attackshark"

    def log_message(self, fmt, *args):
        pass                                   # keep the console clean

    # ------------------------------------------------------------ helpers ---
    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, rel):
        path = os.path.normpath(os.path.join(WEB_ROOT, rel.lstrip("/")))
        if not path.startswith(WEB_ROOT) or not os.path.isfile(path):
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ----------------------------------------------------------- requests ---
    def _guard(self):
        """Refuse anything a page on another site sent us. See _request_allowed."""
        if _request_allowed(self.headers.get("Host"),
                            self.headers.get("Origin"),
                            self.server.server_address[1]):
            return True
        self.send_error(403, "cross-origin request refused")
        return False

    def do_GET(self):
        if not self._guard():
            return
        path = self.path.split("?", 1)[0]          # ?theme=... must still serve
        if path in ("/", "/index.html"):
            return self._send_file("index.html")
        if path == "/api/state":
            return self._send_json(_snapshot(AttackSharkX3().state))
        if path.startswith("/api/"):
            return self.send_error(404)
        return self._send_file(path)

    def do_POST(self):
        if not self._guard():
            return
        self.path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send_json({"error": "bad json"}, 400)
        try:
            if self.path == "/api/set":
                return self._send_json(apply_patch(payload))
            if self.path == "/api/apply":
                return self._send_json(push_all())
            if self.path == "/api/reset":
                return self._send_json(reset_factory())
            if self.path == "/api/macro/save":
                return self._send_json(macro_save(payload))
            if self.path == "/api/macro/delete":
                return self._send_json(macro_delete(payload))
            if self.path == "/api/macro/bind":
                return self._send_json(macro_bind(payload))
            if self.path == "/api/macro/engine":
                return self._send_json(macro_engine(payload))
            if self.path == "/api/macro/test":
                return self._send_json(macro_test(payload))
            if self.path == "/api/macro/flash":
                return self._send_json(macro_flash(payload))
        except DeviceNotFound as e:
            return self._send_json({"error": str(e), "kind": "disconnected"}, 503)
        except (M.MacroError, ValueError, KeyError) as e:
            return self._send_json({"error": str(e), "kind": "invalid"}, 400)
        except OSError as e:
            return self._send_json({"error": str(e), "kind": "io"}, 500)
        return self.send_error(404)


def resync_device():
    """Push the whole configuration once at startup.

    The mouse answers no reads, so the app cannot discover what the device
    actually holds - and anything else that talks to it (the vendor tool, a
    capture replay, another machine) silently leaves the two disagreeing. The
    symptom is nasty because it looks like a broken feature: the UI shows
    lift-off 1 mm while the mouse is on 2 mm, so toggling it "does nothing".

    The vendor app has the same problem and solves it the same way, pushing its
    full burst on launch. Doing it here means the device always matches what is
    on screen from the first frame.
    """
    mouse = AttackSharkX3()
    if not AttackSharkX3.discover():
        return False
    with _lock:
        with mouse:
            mouse.apply()
    return True


def serve(port=DEFAULT_PORT, open_browser=True):
    _load_battery_cache()          # show the last known level immediately
    threading.Thread(target=_status_monitor, daemon=True).start()
    try:
        _sync_engine(AttackSharkX3())        # restore bindings from last run
    except Exception:
        pass
    try:
        if resync_device():
            print("configuration pushed - the mouse now matches the interface")
        else:
            print("mouse not detected; settings will be pushed when it appears")
    except Exception as exc:
        print(f"could not push configuration at startup: {exc}")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"attackshark ui -> {url}   (ctrl-c to stop)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()

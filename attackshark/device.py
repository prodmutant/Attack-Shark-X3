"""High-level control for an Attack Shark X3.

Note on state: the mouse does not answer HidD_GetFeature, and the vendor tool
never reads its settings back either - it keeps them in %APPDATA% and pushes
the whole set whenever anything changes. We do the same, so the local state
file is the source of truth. `attackshark.json` next to your profile keeps it.
"""
from __future__ import annotations

import copy
import json
import os
import time
import traceback
from contextlib import contextmanager

from . import protocol as P
from .hid_backend import HidInterface, find_interfaces

DEFAULT_STATE = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "attackshark.json")

#: Factory-ish defaults, taken from the mouse as it shipped.
FACTORY = {
    "dpi": [800, 1600, 2400, 3200, 6400, 26000],
    "active_stage": 1,
    "enabled_mask": 0x3F,
    "lod_mm": 2,
    "ripple": False,
    "angle_snap": False,
    "motion_sync": False,
    "colors": [(0xFF, 0x00, 0x00), (0x00, 0xFF, 0x00), (0x00, 0x00, 0xFF),
               (0xFF, 0xFF, 0x00), (0x00, 0xFF, 0xFF), (0xFF, 0x00, 0xFF),
               (0xFF, 0x40, 0x00), (0xFF, 0xFF, 0xFF)],
    "polling_hz": 1000,
    "sleep_min": 0.5,
    "deep_sleep_min": 10,
    "key_response_ms": 4,
    "buttons": [0x02, 0x03, 0x04, 0x0D, 0x3C, 0x0F, 0x06, 0x05, 0x3C,
                0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x01, 0x0A, 0x09],
    "macros": [],            # our own format, see attackshark/macro.py
    "macro_bindings": {},    # button number -> {"id":..., "passthrough":bool}
    "engine_on": False,
}


class DeviceNotFound(Exception):
    pass


class AttackSharkX3:
    def __init__(self, state_path=DEFAULT_STATE):
        self.state_path = state_path
        self.state = self._load_state()
        # what was on disk when we loaded, so save() can tell which keys this
        # instance actually changed and leave the rest alone
        self._baseline = copy.deepcopy(self.state)
        self._iface = None
        self._info = None

    # ------------------------------------------------------------- state ---
    def _load_state(self):
        st = dict(FACTORY)
        st["colors"] = [tuple(c) for c in FACTORY["colors"]]
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, encoding="utf-8") as fh:
                    saved = json.load(fh)
                st.update(saved)
                st["colors"] = [tuple(c) for c in st["colors"]]
            except (OSError, ValueError, KeyError):
                pass          # a corrupt state file must not brick the CLI
        return st

    @contextmanager
    def _state_lock(self, timeout=8.0):
        """Exclusive across processes, on a sidecar file.

        The lock cannot live on the state file itself: saving replaces that
        file, and a lock held on the old inode would protect nothing.

        If the lock cannot be taken within the timeout the write goes ahead
        anyway. A stuck lock must not leave someone unable to configure their
        mouse; losing an edit is better than refusing to work at all.
        """
        path = self.state_path + ".lock"
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        fh = open(path, "a+b")
        locked = False
        try:
            if fh.seek(0, os.SEEK_END) == 0:
                fh.write(b".")          # a byte to lock
                fh.flush()
            deadline = time.time() + timeout
            while True:
                try:
                    fh.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except OSError:
                    if time.time() >= deadline:
                        break
                    time.sleep(0.04)
            yield locked
        finally:
            if locked:
                try:
                    fh.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            fh.close()

    def save(self):
        """Merge this instance's changes into whatever is on disk now.

        Several things write here - the web UI, the desktop app's service, the
        CLI, the RE tools - and each one loads the whole document, edits part
        of it and writes it back. Writing `self.state` wholesale means the last
        writer wins for *every* key, not just the ones it touched, so a tool
        that only changed a button map would silently delete macros saved by
        the UI a second earlier. That is not hypothetical; it happened.

        So: under the lock, re-read the file, apply only the keys that differ
        from the baseline this instance loaded, and write that. Two writers
        editing different settings both keep their work. Two writers editing
        the same setting still resolve last-write-wins, which is the best
        anyone can do without asking the user.
        """
        d = os.path.dirname(self.state_path)
        if d:
            os.makedirs(d, exist_ok=True)

        with self._state_lock():
            merged = self._load_state()          # whatever is there right now
            missing = object()
            for key, value in self.state.items():
                if value != self._baseline.get(key, missing):
                    merged[key] = value
            for key in self._baseline:
                if key not in self.state and key in merged:
                    del merged[key]              # this instance removed it

            merged["colors"] = [tuple(c) for c in merged["colors"]]

            # write through a temp file: a kill mid-write must not shred it
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(merged, fh, indent=2)
            os.replace(tmp, self.state_path)

        # carry on from the merged document, so this instance now sees the
        # other writers' changes too
        self.state = merged
        self._baseline = copy.deepcopy(merged)
        self._audit()

    def _audit(self):
        """One line per write, with the call site.

        Cheap insurance: a setting once changed without any obvious cause, and
        without this there is no way to tell which code path did it.
        """
        try:
            caller = "?"
            for frame in reversed(traceback.extract_stack()[:-2]):
                if "attackshark" in frame.filename or "tools" in frame.filename:
                    caller = f"{os.path.basename(frame.filename)}:{frame.lineno}"
                    break
            line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} {caller:<20} "
                    f"dpi={self.state['dpi']} stage={self.state['active_stage']} "
                    f"poll={self.state['polling_hz']}\n")
            with open(self.state_path + ".log", "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass          # auditing must never break a write

    # ------------------------------------------------------------ device ---
    @staticmethod
    def discover():
        """Every X3 config interface currently attached, either link type."""
        out = []
        for pid in P.PRODUCT_IDS:
            for info in find_interfaces(P.VENDOR_ID, pid, P.CONFIG_USAGE_PAGE):
                info["link"] = "2.4GHz" if pid == P.PRODUCT_ID_24G else "wired"
                out.append(info)
        return out

    def open(self):
        found = self.discover()
        if not found:
            raise DeviceNotFound(
                "no Attack Shark X3 config interface found "
                "(mouse powered off, or receiver unplugged?)")
        self._info = found[0]
        self._iface = HidInterface(self._info).open()
        return self

    def close(self):
        if self._iface:
            self._iface.close()
            self._iface = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()

    @property
    def info(self):
        return self._info

    def _send(self, packet, label=""):
        if not self._iface:
            raise RuntimeError("device not open")
        ok, err = self._iface.set_feature(packet)
        if not ok:
            raise OSError(f"HidD_SetFeature failed for {label or hex(packet[0])} "
                          f"(win32 error {err})")
        time.sleep(0.03)
        return True

    # --------------------------------------------------------- operations ---
    def _sensor_packet(self):
        s = self.state
        return P.build_sensor(
            s["dpi"], s["active_stage"],
            lod=P.LOD_1MM if s["lod_mm"] == 1 else P.LOD_2MM,
            ripple=s["ripple"], angle_snap=s["angle_snap"],
            motion_sync=s["motion_sync"], colors=s["colors"],
            enabled_mask=s.get("enabled_mask"))

    def _power_packet(self):
        return P.build_power(self.state["sleep_min"], self.state["deep_sleep_min"],
                             self.state.get("key_response_ms", 4))

    def apply(self):
        """Push the whole configuration, in the order the vendor tool uses."""
        self._send(P.build_commit(), "commit")
        self._send(self._sensor_packet(), "sensor")
        self._send(self._power_packet(), "power")
        self._send(P.build_polling(self.state["polling_hz"]), "polling")
        self._send(P.build_buttons(self.state["buttons"]), "buttons")
        self.save()

    def set_polling(self, hz):
        self.state["polling_hz"] = hz
        self._send(P.build_polling(hz), "polling")
        self.save()

    def set_dpi_stages(self, dpi_list, active=None):
        self.state["dpi"] = list(dpi_list)
        self.state["enabled_mask"] = (1 << len(dpi_list)) - 1
        if active is not None:
            self.state["active_stage"] = active
        self.state["active_stage"] = min(self.state["active_stage"],
                                         len(dpi_list) - 1)
        self._send(self._sensor_packet(), "sensor")
        self.save()

    def set_active_stage(self, index):
        self.state["active_stage"] = index
        self._send(self._sensor_packet(), "sensor")
        self.save()

    def set_sensor_flags(self, *, lod_mm=None, ripple=None, angle_snap=None,
                         motion_sync=None):
        for key, val in (("lod_mm", lod_mm), ("ripple", ripple),
                         ("angle_snap", angle_snap), ("motion_sync", motion_sync)):
            if val is not None:
                self.state[key] = val
        self._send(self._sensor_packet(), "sensor")
        self.save()

    def set_stage_color(self, index, rgb):
        colors = list(self.state["colors"])
        colors[index] = tuple(rgb)
        self.state["colors"] = colors
        self._send(self._sensor_packet(), "sensor")
        self.save()

    def set_power(self, sleep_min=None, deep_sleep_min=None, key_response_ms=None):
        if sleep_min is not None:
            self.state["sleep_min"] = sleep_min
        if deep_sleep_min is not None:
            self.state["deep_sleep_min"] = deep_sleep_min
        if key_response_ms is not None:
            self.state["key_response_ms"] = key_response_ms
        self._send(self._power_packet(), "power")
        self.save()

    def set_button(self, button_no, action):
        """`button_no` is the number shown in the vendor UI (1..5).

        `action` is a name from protocol.ACTION, a raw code, a 3-byte
        sequence, or a key chord like "ctrl+shift+s".
        """
        if button_no not in P.BUTTON_SLOT:
            raise ValueError(f"button must be one of {sorted(P.BUTTON_SLOT)}")
        if isinstance(action, str):
            code = P.ACTION[action] if action in P.ACTION else list(P.shortcut(action))
        elif isinstance(action, (list, tuple)):
            code = list(action)
        else:
            code = int(action)
        buttons = list(self.state["buttons"])
        buttons[P.BUTTON_SLOT[button_no]] = code
        self.state["buttons"] = buttons
        self._send(P.build_buttons(buttons), "buttons")
        self.save()

    # ------------------------------------------------------------- status ---
    def read_status(self, timeout=3.0):
        """One status report from the 0x000A collection, or None."""
        found = []
        for pid in P.PRODUCT_IDS:
            found += find_interfaces(P.VENDOR_ID, pid, P.STATUS_USAGE_PAGE)
        if not found:
            return None
        try:
            with HidInterface(found[0]) as iface:
                return P.parse_status(iface.read_input(timeout))
        except OSError:
            return None

    def battery(self, timeout=3.0):
        """Raw status level. Not a percentage - see protocol.parse_status."""
        st = self.read_status(timeout)
        return st.get("level_raw") if st and st.get("kind") == "battery" else None

    # -------------------------------------------------------------- debug ---
    def packets(self):
        """The exact bytes `apply()` would send - useful for diffing."""
        return {
            "commit": P.build_commit(),
            "sensor": self._sensor_packet(),
            "power": self._power_packet(),
            "polling": P.build_polling(self.state["polling_hz"]),
            "buttons": P.build_buttons(self.state["buttons"]),
        }

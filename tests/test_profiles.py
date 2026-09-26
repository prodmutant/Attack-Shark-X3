"""Per-app profiles: validation, matching, and what actually gets pushed.

The rule that matters most: a profile changes what is sent to the mouse and
never the saved configuration.

    python tests/test_profiles.py
"""
from __future__ import annotations

import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from attackshark import profiles as PR  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    if not cond:
        failures.append(name)


base = {"dpi": [800, 1600, 3200], "active_stage": 1, "polling_hz": 500,
        "lod_mm": 2, "motion_sync": False, "engine_on": False,
        "macro_bindings": {"4": {"id": "a", "passthrough": False}}}

p = PR.validate({"name": " Shooter ", "exes": ["C:\Games\Game.EXE", "other", "game.exe"],
                 "set": {"dpi_value": 812, "polling_hz": 1000, "bogus": 1, "lod_mm": None}})
check("name trimmed", p["name"] == "Shooter")
check("exes normalised and deduplicated", p["exes"] == ["game.exe", "other.exe"], str(p["exes"]))
check("unknown and empty fields dropped", set(p["set"]) == {"dpi_value", "polling_hz"})
check("dpi snapped to the 50 step", p["set"]["dpi_value"] == 800)
for bad in ({"name": ""}, {"name": "x", "set": {"dpi_value": 30}},
            {"name": "x", "set": {"polling_hz": 333}}, {"name": "x", "set": {"lod_mm": 3}}):
    try:
        PR.validate(bad)
        check(f"rejects {bad}", False)
    except PR.ProfileError:
        check(f"rejects {bad}", True)

state = dict(copy.deepcopy(base), profiles=[p], profiles_on=True)
check("match by exe, any case", PR.match(state, "GAME.exe") is p)
check("no match for others", PR.match(state, "notepad.exe") is None)
check("profiles off matches nothing", PR.match(dict(state, profiles_on=False), "game.exe") is None)

before = copy.deepcopy(state)
eff = PR.effective(state, p)
check("dpi override lands on the active stage", eff["dpi"] == [800, 800, 3200])
check("rate overridden", eff["polling_hz"] == 1000)
check("untouched fields kept", eff["lod_mm"] == 2)
check("base state not modified", state == before)
check("no profile = base itself", PR.effective(state, None) is state or PR.effective(state, None) == state)

pb = PR.validate({"name": "b", "bindings": {"5": {"id": "m2"}}})
check("bindings replace the base set", PR.effective(state, pb)["macro_bindings"] ==
      {"5": {"id": "m2", "passthrough": False}})
pn = PR.validate({"name": "n"})
check("no bindings keeps the base set", PR.effective(state, pn)["macro_bindings"] == base["macro_bindings"])

print()
if failures:
    print(f"{len(failures)} failed: " + ", ".join(failures))
    sys.exit(1)
print("all passed")

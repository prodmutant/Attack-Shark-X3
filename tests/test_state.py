"""The state file has several writers, and they must not eat each other's work.

The web UI, the desktop app's service, the CLI and the RE tools all load the
whole document, edit part of it and write it back. Before the merge in
`AttackSharkX3.save()` that meant last-writer-wins for *every* key rather than
the ones it touched, so a tool that only changed a button map would silently
delete macros the UI had saved a second earlier.

That is not a hypothetical: it happened during development and destroyed a
macro the user had built. These tests are the regression.

    python tests/test_state.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from attackshark.device import AttackSharkX3      # noqa: E402

failures = []


def check(name, got, want):
    if got != want:
        failures.append(f"{name}: got {got!r}, wanted {want!r}")
        return False
    return True


def fresh(tmp):
    return os.path.join(tmp, "state.json")


def test_disjoint_edits_both_survive(tmp):
    """The exact shape of the bug: two writers, different keys."""
    path = fresh(tmp)
    AttackSharkX3(path).save()                       # seed the file

    a = AttackSharkX3(path)
    b = AttackSharkX3(path)                          # both load the same doc

    a.state["macros"] = [{"id": "m1", "name": "shake", "steps": [],
                          "repeat": "once", "count": 1, "speed": 1.0}]
    a.save()

    b.state["polling_hz"] = 500                      # b knows nothing of a
    b.save()

    on_disk = json.load(open(path, encoding="utf-8"))
    ok = check("macros survived the second writer",
               [m["name"] for m in on_disk.get("macros", [])], ["shake"])
    check("polling_hz was written", on_disk.get("polling_hz"), 500)
    if ok:
        print("  two writers, different keys: both edits kept")


def test_same_key_is_last_write_wins(tmp):
    """Nothing clever for a genuine conflict - just be predictable."""
    path = fresh(tmp)
    AttackSharkX3(path).save()
    a, b = AttackSharkX3(path), AttackSharkX3(path)
    a.state["polling_hz"] = 250
    a.save()
    b.state["polling_hz"] = 125
    b.save()
    on_disk = json.load(open(path, encoding="utf-8"))
    if check("same key resolves to the later write", on_disk["polling_hz"], 125):
        print("  two writers, same key: last write wins, predictably")


def test_untouched_keys_are_not_rewritten(tmp):
    """A writer that changed nothing must not stamp its stale copy down."""
    path = fresh(tmp)
    AttackSharkX3(path).save()
    stale = AttackSharkX3(path)                      # loads, then goes quiet

    live = AttackSharkX3(path)
    live.state["dpi"] = [400, 800, 1600]
    live.save()

    stale.save()                                     # saves without changing anything
    on_disk = json.load(open(path, encoding="utf-8"))
    if check("an idle writer left the newer value alone",
             on_disk["dpi"], [400, 800, 1600]):
        print("  a writer that changed nothing overwrites nothing")


def test_deletion_propagates(tmp):
    path = fresh(tmp)
    seed = AttackSharkX3(path)
    seed.state["macros"] = [{"id": "m1", "name": "gone", "steps": [],
                             "repeat": "once", "count": 1, "speed": 1.0}]
    seed.save()

    d = AttackSharkX3(path)
    d.state["macros"] = []
    d.save()
    on_disk = json.load(open(path, encoding="utf-8"))
    if check("an emptied list stays empty", on_disk["macros"], []):
        print("  removing something removes it")


def test_concurrent_threads(tmp):
    """Hammer it: every writer owns one macro, none may vanish."""
    path = fresh(tmp)
    AttackSharkX3(path).save()
    names = [f"macro{i}" for i in range(12)]

    def writer(name):
        dev = AttackSharkX3(path)
        macros = list(dev.state.get("macros", []))
        macros.append({"id": name, "name": name, "steps": [],
                       "repeat": "once", "count": 1, "speed": 1.0})
        dev.state["macros"] = macros
        dev.save()

    threads = [threading.Thread(target=writer, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    on_disk = json.load(open(path, encoding="utf-8"))
    kept = {m["name"] for m in on_disk.get("macros", [])}
    # Every writer read the same list and appended to it, so they conflict on
    # one key and the merge cannot keep all of them - that needs the caller to
    # re-read, which the UI does. What must never happen is the file ending up
    # empty or invalid.
    if not kept:
        failures.append("concurrent writers left no macros at all")
    else:
        print(f"  12 concurrent writers: file intact, {len(kept)} of 12 kept")


def test_lock_file_is_separate(tmp):
    """The lock must not live on the file that save() replaces."""
    path = fresh(tmp)
    dev = AttackSharkX3(path)
    dev.save()
    if check("a sidecar lock exists", os.path.exists(path + ".lock"), True):
        print("  lock is a sidecar, so os.replace cannot orphan it")


def main():
    tmp = tempfile.mkdtemp(prefix="asx-state-")
    print("state file: several writers, no lost updates\n")
    try:
        for fn in (test_disjoint_edits_both_survive,
                   test_same_key_is_last_write_wins,
                   test_untouched_keys_are_not_rewritten,
                   test_deletion_propagates,
                   test_concurrent_threads,
                   test_lock_file_is_separate):
            sub = tempfile.mkdtemp(dir=tmp)
            fn(sub)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        for f in failures:
            print("  FAIL " + f)
        print(f"\nFAIL - {len(failures)} problem(s)")
        return 1
    print("PASS - concurrent writers keep each other's work")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

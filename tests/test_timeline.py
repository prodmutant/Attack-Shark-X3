"""The timeline compiler: sequencing, overlap, calls, and what it refuses."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attackshark import macro as M                            # noqa: E402

def mac(name, steps, **kw):
    return M.validate({"id": name, "name": name, "steps": steps, **kw})

def offs(events):
    return [(round(o, 3), e["t"], e.get("key") or e.get("button") or "")
            for o, e in events]

fails = []
def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        print(f"       got  {got}\n       want {want}")
        fails.append(label)

print("sequential macros are unchanged by the trip through the compiler")
seq = mac("seq", [{"t": "key", "key": "a", "down": True},
                  {"t": "delay", "ms": 10},
                  {"t": "key", "key": "a", "down": False}])
ev, dur = M.compile_timeline(seq)
check("offsets accumulate", offs(ev), [(0.0, "key", "a"), (10.0, "key", "a")])
check("duration is the delay", dur, 10.0)

print("\n`at` positions a step without moving the cursor")
ovl = mac("ovl", [{"t": "key", "key": "a", "down": True, "at": 0},
                  {"t": "key", "key": "w", "down": True, "at": 10},
                  {"t": "key", "key": "w", "down": False, "at": 19},
                  {"t": "key", "key": "a", "down": False, "at": 50}])
ev, dur = M.compile_timeline(ovl)
check("four events, sorted", offs(ev),
      [(0.0, "key", "a"), (10.0, "key", "w"), (19.0, "key", "w"),
       (50.0, "key", "a")])
check("duration is the last offset", dur, 50.0)
held = [e for o, e in ev if e["key"] == "a"]
check("A spans the W taps", (held[0]["down"], held[1]["down"]), (True, False))

print("\nsimultaneity: two events at one offset both survive")
sim = mac("sim", [{"t": "key", "key": "s", "down": True, "at": 5},
                  {"t": "key", "key": "d", "down": True, "at": 5}])
ev, _ = M.compile_timeline(sim)
check("same offset kept", [o for o, _ in ev], [5.0, 5.0])

print("\nmixing: a positioned step does not disturb the running cursor")
mix = mac("mix", [{"t": "key", "key": "a", "down": True},
                  {"t": "delay", "ms": 10},
                  {"t": "key", "key": "w", "down": True, "at": 100},
                  {"t": "key", "key": "a", "down": False}])
ev, dur = M.compile_timeline(mix)
check("A up still lands at 10", offs(ev),
      [(0.0, "key", "a"), (10.0, "key", "a"), (100.0, "key", "w")])
check("duration covers the placed step", dur, 100.0)

print("\ncall splices a child timeline in at the cursor")
child = mac("child", [{"t": "key", "key": "w", "down": True},
                      {"t": "delay", "ms": 8},
                      {"t": "key", "key": "w", "down": False},
                      {"t": "delay", "ms": 8}])
reg = {"child": child}
parent = mac("parent", [{"t": "delay", "ms": 5},
                        {"t": "call", "id": "child", "times": 3}])
ev, dur = M.compile_timeline(parent, reg)
check("three copies, 16ms apart", [o for o, _ in ev],
      [5.0, 13.0, 21.0, 29.0, 37.0, 45.0])
check("duration is 5 + 3x16", dur, 53.0)

print("\nspeed scales offsets, including inside calls")
fast = M.validate({**parent, "speed": 2.0})
ev, dur = M.compile_timeline(fast, reg)
check("everything halves", [o for o, _ in ev],
      [2.5, 6.5, 10.5, 14.5, 18.5, 22.5])
check("duration halves", dur, 26.5)

print("\nrefusals")
def refuses(label, fn):
    try:
        fn()
    except M.MacroError as e:
        print(f"  ok   {label}  ({e})")
        return
    print(f"  FAIL {label} was accepted")
    fails.append(label)

loop = mac("loop", [{"t": "call", "id": "loop"}])
refuses("a macro calling itself", lambda: M.compile_timeline(loop, {"loop": loop}))
a = mac("a", [{"t": "call", "id": "b"}])
b = mac("b", [{"t": "call", "id": "a"}])
refuses("a two-macro cycle", lambda: M.compile_timeline(a, {"a": a, "b": b}))
miss = mac("miss", [{"t": "call", "id": "nope"}])
refuses("a call to a missing macro", lambda: M.compile_timeline(miss, {}))
refuses("a negative at", lambda: mac("neg", [{"t": "key", "key": "a", "at": -1}]))
refuses("a call with no id", lambda: mac("noid", [{"t": "call"}]))

print("\nround trip: timeline -> steps -> timeline is stable")
ev, _ = M.compile_timeline(ovl)
again = mac("again", M.timeline_steps(ev))
ev2, _ = M.compile_timeline(again)
check("same offsets", offs(ev2), offs(ev))

print()
print("lanes: a hold block is a press and a release at its edges")
lm = M.validate({"name": "l", "lanes": [
    {"kind": "key", "key": "a", "blocks": [{"type": "hold", "at": 10, "ms": 40}]}]})
check("two steps", [(x["at"], x["down"]) for x in lm["steps"]],
      [(10, True), (50, False)])

print()
print("lanes: a spam block is that pair repeated across its width")
sp = M.validate({"name": "s", "lanes": [
    {"kind": "key", "key": "w",
     "blocks": [{"type": "spam", "at": 0, "ms": 90, "rate": 18, "hold": 9}]}]})
downs = [x["at"] for x in sp["steps"] if x["down"]]
check("presses every 18ms", downs, [0, 18, 36, 54, 72])
check("none overruns the block", max(x["at"] for x in sp["steps"]), 81)

print()
print("lanes: overlap is just two lanes at once")
ov = M.validate({"name": "o", "lanes": [
    {"kind": "key", "key": "a", "blocks": [{"type": "hold", "at": 0, "ms": 100}]},
    {"kind": "key", "key": "w",
     "blocks": [{"type": "spam", "at": 9, "ms": 90, "rate": 18, "hold": 9}]}]})
ev, dur = M.compile_timeline(ov)
a_down = next(o for o, e in ev if e["key"] == "a" and e["down"])
a_up = next(o for o, e in ev if e["key"] == "a" and not e["down"])
w = [o for o, e in ev if e["key"] == "w" and e["down"]]
check("A spans every W press", all(a_down < x < a_up for x in w), True)
check("five W presses under it", len(w), 5)

print()
print("lanes: steps are derived, so a caller cannot desync them")
forged = M.validate({"name": "f",
                     "lanes": [{"kind": "key", "key": "a",
                                "blocks": [{"type": "hold", "at": 0, "ms": 10}]}],
                     "steps": [{"t": "key", "key": "z", "down": True}]})
check("the forged steps are ignored", [x["key"] for x in forged["steps"]],
      ["a", "a"])

print()
print("lanes: refusals")
refuses("a spam whose press outlasts its rate",
        lambda: M.validate({"name": "x", "lanes": [
            {"kind": "key", "key": "a", "blocks": [
                {"type": "spam", "at": 0, "ms": 90, "rate": 9, "hold": 9}]}]}))
refuses("a spam that expands past the step cap",
        lambda: M.validate({"name": "x", "lanes": [
            {"kind": "key", "key": "a", "blocks": [
                {"type": "spam", "at": 0, "ms": 60000, "rate": 2, "hold": 1}]}]}))
refuses("an unknown lane kind",
        lambda: M.validate({"name": "x", "lanes": [{"kind": "wat"}]}))

print()
print("lanes: a wheel tick is one notch, and a wheel spam is a spin")
tick = M.validate({"name": "t", "lanes": [
    {"kind": "wheel", "direction": "up",
     "blocks": [{"type": "tick", "at": 30, "ms": 20}]}]})
check("one step, at the left edge", [(x["t"], x["at"], x["delta"])
                                     for x in tick["steps"]],
      [("wheel", 30, 1)])
spin = M.validate({"name": "w", "lanes": [
    {"kind": "wheel", "direction": "down",
     "blocks": [{"type": "spam", "at": 0, "ms": 100, "rate": 20,
                 "notches": 2}]}]})
check("a notch every 20ms, both edges included",
      [x["at"] for x in spin["steps"]], [0, 20, 40, 60, 80, 100])
check("down is negative, and carries its size",
      {x["delta"] for x in spin["steps"]}, {-2})
check("the block's width is the pass length", M.duration_ms(spin), 100.0)

print()
print("lanes: a wheel block's one-shot is a tick, whatever it is called")
coerced = M.validate({"name": "c", "lanes": [
    {"kind": "wheel", "direction": "up",
     "blocks": [{"type": "hold", "at": 0, "ms": 40}]}]})
check("`hold` on a wheel lane is read as a tick",
      coerced["lanes"][0]["blocks"][0]["type"], "tick")
check("and emits exactly one notch", len(coerced["steps"]), 1)
keyed = M.validate({"name": "k", "lanes": [
    {"kind": "key", "key": "a", "blocks": [{"type": "tick", "at": 0, "ms": 40}]}]})
check("`tick` on a key lane is read as a hold",
      keyed["lanes"][0]["blocks"][0]["type"], "hold")

print()
print("lanes: a wheel overlaps everything else, like any other lane")
mix = M.validate({"name": "m", "lanes": [
    {"kind": "key", "key": "w", "blocks": [{"type": "hold", "at": 0, "ms": 80}]},
    {"kind": "wheel", "direction": "up",
     "blocks": [{"type": "spam", "at": 10, "ms": 40, "rate": 20}]}]})
ev, _ = M.compile_timeline(mix)
notches = [o for o, e in ev if e["t"] == "wheel"]
w_down = next(o for o, e in ev if e["t"] == "key" and e["down"])
w_up = next(o for o, e in ev if e["t"] == "key" and not e["down"])
check("three notches inside the held key", notches, [10.0, 30.0, 50.0])
check("W spans all of them", all(w_down < x < w_up for x in notches), True)

print()
print("lanes: wheel refusals")
refuses("a wheel lane with no direction",
        lambda: M.validate({"name": "x", "lanes": [{"kind": "wheel"}]}))
refuses("a sideways wheel",
        lambda: M.validate({"name": "x", "lanes": [
            {"kind": "wheel", "direction": "left"}]}))
refuses("more notches than one event can turn",
        lambda: M.validate({"name": "x", "lanes": [
            {"kind": "wheel", "direction": "up", "blocks": [
                {"type": "tick", "at": 0, "ms": 1, "notches": 99}]}]}))
refuses("a wheel spam that expands past the step cap",
        lambda: M.validate({"name": "x", "lanes": [
            {"kind": "wheel", "direction": "up", "blocks": [
                {"type": "spam", "at": 0, "ms": 60000, "rate": 1}]}]}))

print()
print("lanes: the mouse is not offered a macro it cannot hold")
lane_mac = M.validate({"name": "d", "repeat": "once", "lanes": [
    {"kind": "key", "key": "a", "blocks": [{"type": "hold", "at": 0, "ms": 9}]}]})
check("a lane macro is host-only", M.device_support(lane_mac)[0], False)
placed = M.validate({"name": "p", "repeat": "once",
                     "steps": [{"t": "key", "key": "a", "down": True, "at": 5}]})
check("a positioned step is host-only", M.device_support(placed)[0], False)
plain = M.validate({"name": "q", "repeat": "once",
                    "steps": [{"t": "key", "key": "a", "down": True},
                              {"t": "key", "key": "a", "down": False}]})
check("a plain key macro still fits", M.device_support(plain)[0], True)
scroll = M.validate({"name": "s", "repeat": "once",
                     "steps": [{"t": "wheel", "delta": 1}]})
check("a wheel macro is host-only", M.device_support(scroll)[0], False)

print()
if fails:
    print(f"FAIL - {len(fails)} case(s): {', '.join(fails)}")
    sys.exit(1)
print("PASS - timeline and lanes: sequencing, overlap, spam, wheel, calls, "
      "refusals")

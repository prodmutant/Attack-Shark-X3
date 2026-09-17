"""Spawn X3.exe under Frida, drive its UI, and label every HID frame with the
action that produced it. Output: captures/<name>.jsonl"""
import frida, json, os, subprocess, sys, threading, time, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = r"C:\Program Files (x86)\Attack SharkX3Mouse\X3.exe"
AGENT = os.path.join(HERE, "hook_hid.js")
UI = os.path.join(HERE, "ui.ps1")

def ps(*a):
    return subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", UI] + list(a),
                          capture_output=True, text=True, cwd=ROOT).stdout.strip()

def kill_x3():
    subprocess.run(["powershell", "-Command",
                    "Stop-Process -Name X3 -Force -ErrorAction SilentlyContinue"],
                   capture_output=True)
    time.sleep(1.5)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--settle", type=float, default=8.0)
    ap.add_argument("--mon-x", dest="mon_x", type=int, default=1920)
    ap.add_argument("--mon-y", dest="mon_y", type=int, default=0)
    a = ap.parse_args()

    plan = json.load(open(a.plan, encoding="utf-8"))
    os.makedirs(os.path.join(ROOT, "captures", "shots"), exist_ok=True)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    log = open(a.out, "w", encoding="utf-8")
    lock = threading.Lock()
    mark = {"v": "boot"}

    kill_x3()
    dev = frida.get_local_device()
    pid = dev.spawn([EXE])
    session = dev.attach(pid)
    script = session.create_script(open(AGENT, encoding="utf-8").read())

    counts = {}
    def on_message(msg, data):
        if msg.get("type") != "send":
            return
        p = msg["payload"]
        p["mark"] = mark["v"]
        p["t"] = round(time.time(), 3)
        with lock:
            log.write(json.dumps(p) + "\n"); log.flush()
        if p["dir"] in ("TX", "RX"):
            counts[mark["v"]] = counts.get(mark["v"], 0) + 1

    script.on("message", on_message)
    script.load()
    dev.resume(pid)
    print(f"[*] spawned pid {pid}; settling {a.settle}s")
    time.sleep(a.settle)
    # keep the vendor UI off the primary display while we drive it
    print("[*]", ps("-Action", "move", "-X", str(a.mon_x), "-Y", str(a.mon_y)))
    print("[*]", ps("-Action", "info"))

    for step in plan:
        label = step["label"]
        mark["v"] = label
        with lock:
            log.write(json.dumps({"dir": "##", "op": "MARK", "data": label,
                                  "t": round(time.time(), 3)}) + "\n"); log.flush()
        time.sleep(0.25)
        if "dlg" in step:
            ps("-Action", "dialogclick", "-X", str(step["dlg"][0]),
               "-Y", str(step["dlg"][1]), "-MenuIndex", str(step.get("menu_index", 0)))
        elif "menu" in step:
            ps("-Action", "menuitem", "-Item", str(step["menu"]),
               "-MenuIndex", str(step.get("menu_index", 0)))
        elif "x2" in step:
            ps("-Action", "drag", "-X", str(step["x"]), "-Y", str(step["y"]),
               "-X2", str(step["x2"]), "-Y2", str(step["y2"]))
        elif "x" in step:
            ps("-Action", "click", "-X", str(step["x"]), "-Y", str(step["y"]))
        time.sleep(step.get("wait", 1.2))
        if step.get("dialogshot"):
            ps("-Action", "dialogshot", "-MenuIndex", str(step.get("menu_index", 0)),
               "-Out", f"captures/shots/{step['dialogshot']}.png")
        if step.get("menushot"):
            ps("-Action", "menushot", "-MenuIndex", str(step.get("menu_index", 0)),
               "-Out", f"captures/shots/{step['menushot']}.png")
        if step.get("shot"):
            ps("-Action", "shot", "-Out", f"captures/shots/{step['shot']}.png")
        print(f"    {label:<34} frames={counts.get(label,0)}")

    mark["v"] = "end"
    time.sleep(1.0)
    ps("-Action", "shot", "-Out", a.out.replace(".jsonl", ".png"))
    print(f"[*] done -> {a.out}")
    try:
        session.detach()
    except Exception:
        pass
    kill_x3()

main()

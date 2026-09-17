"""Spawn (or attach to) the vendor driver and log every HID feature exchange."""
import frida, sys, os, time, json, argparse, datetime

EXE = r"C:\Program Files (x86)\Attack SharkX3Mouse\X3.exe"
AGENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook_hid.js")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--attach", metavar="NAME_OR_PID", default=None)
    ap.add_argument("--exe", default=EXE)
    ap.add_argument("--out", default="captures/session.jsonl")
    a = ap.parse_args()

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    log = open(a.out, "a", encoding="utf-8")
    dev = frida.get_local_device()

    if a.attach:
        target = int(a.attach) if a.attach.isdigit() else a.attach
        session = dev.attach(target)
        pid = None
        print(f"[*] attached to {a.attach}")
    else:
        pid = dev.spawn([a.exe])
        session = dev.attach(pid)
        print(f"[*] spawned {a.exe} (pid {pid})")

    script = session.create_script(open(AGENT, encoding="utf-8").read())

    n = [0]
    def on_message(msg, data):
        if msg.get("type") != "send":
            print("[!]", msg); return
        p = msg["payload"]
        p["t"] = round(time.time(), 3)
        log.write(json.dumps(p) + "\n"); log.flush()
        if p.get("op") in ("ready", "error"):
            print(f"[*] {p['data']}"); return
        n[0] += 1
        d = p.get("data", "")
        head = " ".join(d.split(" ")[:20])
        print(f"{p['dir']} {p['op']:<16} {p.get('dev',''):<22} len={p.get('len')} :: {head}")

    script.on("message", on_message)
    script.load()
    if pid is not None:
        dev.resume(pid)

    print("[*] logging -> " + a.out + "   (Ctrl-C to stop)")
    try:
        while True: time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\n[*] {n[0]} frames captured -> {a.out}")
        try: session.detach()
        except Exception: pass

main()

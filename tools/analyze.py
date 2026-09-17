"""Group captured frames by action label, dedupe the wrapper/native double-log."""
import json, sys, collections

def load(path, only_native=True):
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    out, cur = [], "boot"
    for r in rows:
        if r.get("op") == "MARK":
            cur = r["data"]; out.append(("MARK", cur, None, None)); continue
        if r["dir"] not in ("TX", "RX"):
            continue
        op = r["op"].split("!")[-1]
        if only_native and not op.startswith("HidD_"):
            continue          # SetFeature wrapper duplicates HidD_SetFeature
        data = bytes(int(x, 16) for x in (r.get("data") or "").split() if x)
        out.append((r["dir"], cur, op, data))
    return out

def show(path):
    for dir_, mark, op, data in load(path):
        if dir_ == "MARK":
            print(f"\n=== {mark} ==="); continue
        n = data[1] if len(data) > 1 else len(data)
        body = data[:n]
        print(f"  {dir_} {op:<18} cmd=0x{data[0]:02x} len={n:<3} {body.hex(' ')}")

if __name__ == "__main__":
    show(sys.argv[1])

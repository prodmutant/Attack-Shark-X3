"""Listen on every X3 collection and print whatever input reports arrive.

Read-only. Used to find where the mouse reports battery: the config channel
(usage page 0x000B) answers no reads, so status has to arrive as an input
report on one of the other collections.

    python tools/read_inputs.py [seconds]
"""
import ctypes as C
from ctypes import wintypes as W
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from attackshark.hid_backend import _open, find_interfaces, k32  # noqa: E402
from attackshark import protocol as P  # noqa: E402

k32.ReadFile.argtypes = [C.c_void_p, C.c_void_p, W.DWORD,
                         C.POINTER(W.DWORD), C.c_void_p]
k32.ReadFile.restype = W.BOOL

seen = {}
lock = threading.Lock()


def listen(info, stop):
    tag = f"UP{info['usage_page']:04x}:U{info['usage']:04x}"
    n = info["input_len"]
    if not n:
        return
    h = _open(info["path"], True) or _open(info["path"], False)
    if not h:
        with lock:
            print(f"  {tag:<16} cannot open")
        return
    buf = C.create_string_buffer(n)
    got = W.DWORD()
    try:
        while not stop.is_set():
            if not k32.ReadFile(h, buf, n, C.byref(got), None):
                break
            data = bytes(buf.raw[:got.value])
            if not data:
                continue
            with lock:
                key = (tag, data)
                seen[key] = seen.get(key, 0) + 1
                # a mouse-movement report repeats constantly; only show news
                if seen[key] == 1:
                    print(f"  {tag:<16} len={got.value:<3} {data.hex(' ')}")
    finally:
        k32.CloseHandle(h)


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    ifaces = []
    for pid in P.PRODUCT_IDS:
        ifaces += find_interfaces(P.VENDOR_ID, pid)
    if not ifaces:
        print("mouse not found")
        return
    print(f"listening {secs:g}s on {len(ifaces)} collection(s) "
          f"- move the mouse / click a bit\n")
    stop = threading.Event()
    threads = []
    for info in ifaces:
        t = threading.Thread(target=listen, args=(info, stop), daemon=True)
        t.start()
        threads.append(t)
    time.sleep(secs)
    stop.set()
    print(f"\n{len(seen)} distinct report(s)")


main()

"""Timestamp every status report, and test whether a config write triggers one.

    python tools/watch_status.py [seconds]
"""
import ctypes as C
from ctypes import wintypes as W
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from attackshark.hid_backend import _open, find_interfaces, HidInterface, k32  # noqa: E402
from attackshark import protocol as P  # noqa: E402

k32.ReadFile.argtypes = [C.c_void_p, C.c_void_p, W.DWORD,
                         C.POINTER(W.DWORD), C.c_void_p]
k32.ReadFile.restype = W.BOOL
STATUS_USAGE_PAGE = 0x000A

t0 = time.time()


def listen(info, stop):
    h = _open(info["path"], True) or _open(info["path"], False)
    if not h:
        print("cannot open status collection")
        return
    n = info["input_len"]
    buf = C.create_string_buffer(n)
    got = W.DWORD()
    try:
        while not stop.is_set():
            if not k32.ReadFile(h, buf, n, C.byref(got), None):
                break
            data = bytes(buf.raw[:got.value])
            print(f"  t+{time.time()-t0:6.1f}s  {data.hex(' ')}", flush=True)
    finally:
        k32.CloseHandle(h)


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    st = None
    for pid in P.PRODUCT_IDS:
        for d in find_interfaces(P.VENDOR_ID, pid, STATUS_USAGE_PAGE):
            st = d
            break
    if not st:
        print("status collection not present")
        return
    stop = threading.Event()
    threading.Thread(target=listen, args=(st, stop), daemon=True).start()
    print(f"watching {secs:g}s\n")

    # halfway through, poke the config channel and see if status answers
    time.sleep(secs / 2)
    cfg = find_interfaces(P.VENDOR_ID, P.PRODUCT_ID_24G, P.CONFIG_USAGE_PAGE)
    if cfg:
        print(f"  t+{time.time()-t0:6.1f}s  -> sending commit (0x0c)", flush=True)
        try:
            with HidInterface(cfg[0]) as dev:
                dev.set_feature(P.build_commit())
        except OSError as e:
            print("   send failed:", e)
    time.sleep(secs / 2)
    stop.set()
    sys.stdout.flush()
    # the reader thread is parked in a blocking ReadFile; do not wait for it
    os._exit(0)


main()

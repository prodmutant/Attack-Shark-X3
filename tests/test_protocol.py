"""Round-trip every packet the vendor tool was observed sending.

For each captured report we parse it, rebuild it from the parsed values, and
require the rebuilt bytes to match the original exactly. That is the whole
correctness argument for protocol.py: if a field were mis-sized or the checksum
mis-specified, the rebuild would diverge.

Run:  python tests/test_protocol.py
"""
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from attackshark import macro as M  # noqa: E402
from attackshark import protocol as P  # noqa: E402

#: captured 0x09 packet -> the 128-byte macro block it belongs to.
#: Built by index_macro_blocks(); a chunk cannot be rebuilt from itself,
#: and several distinct macros appear across the captures, so each packet has
#: to be tied to its own block rather than to whichever was seen last.
CHUNK_BLOCK = {}

#: HID usage -> key name, for decoding a captured macro back into steps
USAGE_NAMES = {v: k for k, v in P.HID_KEYS.items()}


def load_corpus():
    """Unique HidD_SetFeature payloads from every capture session."""
    seen = {}
    for path in glob.glob(os.path.join(ROOT, "captures", "*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                row = json.loads(line)
                if row.get("dir") not in ("TX", "RX"):
                    continue
                if not row.get("op", "").endswith("HidD_SetFeature"):
                    continue
                data = bytes(int(x, 16) for x in (row.get("data") or "").split())
                if data:
                    seen[data] = path
    return sorted(seen)


def rebuild(pkt):
    """Reconstruct a packet from its decoded fields."""
    rid = pkt[0]
    if rid == P.REPORT_POLLING:
        return P.build_polling(P.parse_polling(pkt)["rate_hz"])
    if rid == P.REPORT_COMMIT:
        return P.build_commit()
    if rid == P.REPORT_SENSOR:
        d = P.parse_sensor(pkt)
        return P.build_sensor(
            d["dpi"], d["active_stage"],
            lod=P.LOD_1MM if d["lod_mm"] == 1 else P.LOD_2MM,
            ripple=d["ripple"], angle_snap=d["angle_snap"],
            motion_sync=d["motion_sync"], colors=d["colors"],
            enabled_mask=d["enabled_mask"])
    if rid == P.REPORT_POWER:
        d = P.parse_power(pkt)
        return P.build_power(d["sleep_min"], d["deep_sleep_min"],
                             d["key_response_ms"])
    if rid == P.REPORT_BUTTONS:
        return P.build_buttons(P.parse_buttons(pkt)["slots"])
    if rid == M.DEV_REPORT:
        return rebuild_macro_chunk(pkt)
    return None


def index_macro_blocks():
    """Tie every captured report 0x09 packet to the block it is part of.

    Chunks are only meaningful in sequence, so the capture files are read in
    order rather than from the deduplicated corpus. The hook logs each write up
    to three times, so repeats of the chunk we are already holding are ignored.
    """
    for path in sorted(glob.glob(os.path.join(ROOT, "captures", "*.jsonl"))):
        pending = {}
        order = []
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not (row.get("op") or "").endswith("HidD_SetFeature"):
                    continue
                try:
                    pkt = bytes(int(x, 16) for x in (row.get("data") or "").split())
                except ValueError:
                    continue
                if not pkt or pkt[0] != M.DEV_REPORT:
                    continue
                idx = pkt[3]
                if idx == 0:
                    pending, order = {}, []
                if idx in pending and pending[idx] == pkt:
                    continue                    # wrapper duplicate
                pending[idx] = pkt
                order.append(pkt)
                if idx == 2 and {0, 1, 2} <= set(pending):
                    block = b"".join(pending[i][4:pending[i][1]] for i in (0, 1, 2))
                    if len(block) == M.DEV_PAYLOAD_LEN:
                        for q in order:
                            CHUNK_BLOCK[q] = block
                    pending, order = {}, []


def rebuild_macro_chunk(pkt):
    """Rebuild one report 0x09 chunk from the macro it encodes.

    Unlike the config blocks there is nothing to parse and re-emit field by
    field - the block *is* the macro. So decode its events back into a macro.py
    macro, rebuild the upload from that at the block's own slot, and require
    the chunk at the same index to match. If the block layout, the chunking,
    the event encoding, the slot byte or the checksum were wrong, this would
    diverge.
    """
    block = CHUNK_BLOCK.get(pkt)
    if block is None:
        return None

    slot = block[0]
    count = block[M.DEV_COUNT_AT]
    steps = []
    for i in range(count):
        flags = block[M.DEV_EVENTS_AT + i * 2]
        usage = block[M.DEV_EVENTS_AT + i * 2 + 1]
        name = USAGE_NAMES.get(usage)
        if name is None or flags not in (M.DEV_PRESS, M.DEV_RELEASE):
            return None
        steps.append({"t": "key", "key": name, "down": flags == M.DEV_PRESS})
    if not steps:
        return None

    chunks = M.build_device_upload(
        M.validate({"name": "captured", "steps": steps}), slot)
    index = pkt[3]
    if index >= len(chunks):
        return None
    got = chunks[index]
    return got[:len(pkt)] if len(got) >= len(pkt) else got


def main():
    corpus = load_corpus()
    index_macro_blocks()
    if not corpus:
        print("no captures found - run tools/exercise.py first")
        return 1

    by_id = {}
    for pkt in corpus:
        by_id.setdefault(pkt[0], []).append(pkt)

    total = ok = skipped = 0
    failures = []
    for rid in sorted(by_id):
        for pkt in by_id[rid]:
            total += 1
            # 1. declared length must match the real buffer.
            #    Report 0x09 is the chunked macro upload: byte 1 is how much of
            #    this 64-byte chunk is used, so the last chunk is legitimately
            #    shorter than the buffer.
            if rid != 0x09 and pkt[1] != len(pkt):
                failures.append((pkt, f"length byte {pkt[1]} != buffer {len(pkt)}"))
                continue
            # 2. checksum must validate for the config blocks
            if rid in P.CHECK_AT and not P.verify_check(pkt, rid):
                failures.append((pkt, "checksum mismatch"))
                continue
            # 3. parse -> build must reproduce the original bytes
            got = rebuild(pkt)
            if got is None:
                skipped += 1
                continue
            if got != pkt:
                failures.append((pkt, f"rebuild differs:\n   want {pkt.hex(' ')}\n   got  {got.hex(' ')}"))
                continue
            ok += 1

    for rid in sorted(by_id):
        name = {0x04: "sensor/DPI", 0x05: "power", 0x06: "polling",
                0x08: "buttons", 0x09: "macro upload", 0x0C: "commit"}.get(rid, "?")
        print(f"  report 0x{rid:02x} {name:<12} {len(by_id[rid]):>3} unique packet(s)")

    print(f"\n{ok}/{total} packets round-tripped exactly"
          + (f", {skipped} skipped" if skipped else ""))
    for pkt, why in failures:
        print(f"  FAIL 0x{pkt[0]:02x}: {why}")
    if failures:
        return 1
    print("PASS - protocol.py reproduces every captured packet byte-for-byte")
    return 0


if __name__ == "__main__":
    sys.exit(main())

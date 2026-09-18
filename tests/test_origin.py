"""The API must refuse requests that another website caused the browser to send.

Binding to 127.0.0.1 keeps the network out; it does not keep other websites
out. Any page can post to this server in the background, and on this API that
is not a nuisance - `/api/macro/flash` writes a keystroke macro into the mouse
firmware, which then survives reboots and the removal of this software.

These are the cases that matter, written as a table so a change to the rule has
to state which of them it is changing.

    python tests/test_origin.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attackshark.server import _request_allowed          # noqa: E402

PORT = 7332

#   host                     origin                          allowed  why
CASES = [
    # the interface itself
    ("127.0.0.1:7332",       "http://127.0.0.1:7332",        True,
     "the page this server served"),
    ("localhost:7332",       "http://localhost:7332",        True,
     "same, reached by name"),

    # programs, which carry no Origin at all - a web page cannot do this
    ("127.0.0.1:7332",       None,                           True,
     "curl, the CLI, a script"),
    ("localhost:7332",       "",                             True,
     "empty Origin is no Origin"),

    # the attack this exists for
    ("127.0.0.1:7332",       "https://evil.example",         False,
     "a page you visited, posting in the background"),
    ("127.0.0.1:7332",       "http://127.0.0.1:8080",        False,
     "another local server; a different origin is a different origin"),
    ("127.0.0.1:7332",       "https://127.0.0.1:7332",       False,
     "scheme is part of the origin"),
    ("127.0.0.1:7332",       "http://127.0.0.1",             False,
     "port is part of the origin"),

    # DNS rebinding: the attacker's name resolves to 127.0.0.1 after load, so
    # the browser thinks their page is same-origin and sends no Origin header.
    # The Host header is what still gives them away.
    ("evil.example",         None,                           False,
     "rebound name, no Origin to catch it"),
    ("evil.example:7332",    None,                           False,
     "same, with the port"),
    ("127.0.0.1.evil.example:7332", None,                    False,
     "a name that merely starts like loopback"),
    ("",                     None,                           False,
     "no Host header at all"),
]


def main():
    bad = 0
    for host, origin, want, why in CASES:
        got = _request_allowed(host, origin, PORT)
        ok = got is want
        bad += not ok
        print("  %s  host=%-30r origin=%-26r %s"
              % ("ok  " if ok else "FAIL", host, origin,
                 ("allowed" if got else "refused") + " - " + why))
    print()
    if bad:
        print("FAIL - %d of %d cases wrong" % (bad, len(CASES)))
        return 1
    print("PASS - %d cases; only this server's own page may drive the API"
          % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())

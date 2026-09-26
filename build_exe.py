"""Package the release executable.

    python build_exe.py

Produces dist/PRODMUTANT X3 Driver.exe - one file, no console, tray icon, with
the web interface and the artwork bundled inside. Needs
`pip install pyinstaller pillow`. There is one build: the one you run is the
one that is released.

The result is deliberately not committed. A build is an output, not a source,
and a repository that carries both is a repository where nobody can tell which
one they are looking at: the binary goes out as a release download, the source
stays here, and `dist/` is ignored so the two can never drift into each other.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "PRODMUTANT X3 Driver"
ENTRY = os.path.join(ROOT, "launch.py")
WEB = os.path.join(ROOT, "attackshark", "web")
ICON = os.path.join(WEB, "logo.ico")

ENTRY_SRC = '''"""Frozen entry point: hand straight to the tray app."""
import multiprocessing
import sys

from attackshark.tray import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
'''


SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
         (256, 256)]


def ensure_icon(ico, png):
    """Make the .ico from its .png once, and leave it alone after that."""
    if os.path.isfile(ico):
        return ico
    if not os.path.isfile(png):
        return None
    try:
        from PIL import Image
    except ImportError:
        sys.exit("need Pillow to make the icon: pip install pillow")
    Image.open(png).convert("RGBA").save(ico, sizes=SIZES)
    print(f"made {ico}")
    return ico


def stage_web(into):
    """A copy of the interface as the repository has it.

    `--add-data` takes a directory and takes all of it, and `attackshark/web`
    is where someone's local `.custom.` art swap lives. Staging a filtered
    copy keeps every build identical to the source - the shipped artwork,
    whoever builds it.
    """
    dst = os.path.join(into, "attackshark", "web")
    shutil.copytree(WEB, dst, ignore=shutil.ignore_patterns("*.custom.*", "__pycache__"))
    left = sorted(f for f in os.listdir(dst) if ".custom." in f)
    assert not left, f"local art swap reached the bundle: {left}"
    return dst


def main():
    name = NAME
    icon = ensure_icon(ICON, os.path.join(WEB, "logo.png"))
    with open(ENTRY, "w", encoding="utf-8") as fh:
        fh.write(ENTRY_SRC)

    stage = tempfile.mkdtemp(prefix="asx-build-")
    web = stage_web(stage)
    sep = ";" if os.name == "nt" else ":"
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",                      # no console window
        "--name", name,
        "--icon", icon,
        # the interface travels inside the exe, from the staged copy
        "--add-data", f"{web}{sep}attackshark{os.sep}web",
        # the Tools page's USB fix runs this script elevated
        "--add-data", f"{os.path.join(ROOT, 'tools', 'fix_usb_lag.ps1')}{sep}tools",
        # ctypes-only project: nothing heavy to pull in
        "--exclude-module", "tkinter",
        "--exclude-module", "PIL",
        "--exclude-module", "numpy",
        "--exclude-module", "frida",
        ENTRY,
    ]
    print(" ".join(args[:6]), "...\n")
    try:
        r = subprocess.run(args, cwd=ROOT)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    if r.returncode != 0:
        sys.exit(r.returncode)

    exe = os.path.join(ROOT, "dist", name + ".exe")
    if os.path.isfile(exe):
        size = os.path.getsize(exe) / (1024 * 1024)
        print(f"\nbuilt {exe}  ({size:.1f} MB)")
    for junk in ("build", name + ".spec", "launch.py"):
        p = os.path.join(ROOT, junk)
        shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else (
            os.remove(p) if os.path.isfile(p) else None)


if __name__ == "__main__":
    main()

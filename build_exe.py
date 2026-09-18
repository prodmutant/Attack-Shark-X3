"""Package the release executable.

    python build_exe.py

Produces dist/PRODMUTANT X3 Driver.exe - one file, no console, tray icon, with
the web interface bundled inside. Needs `pip install pyinstaller pillow`.

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
ICON = os.path.join(ROOT, "attackshark", "web", "logo.ico")

ENTRY_SRC = '''"""Frozen entry point: hand straight to the tray app."""
import multiprocessing
import sys

from attackshark.tray import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
'''


def ensure_icon():
    if os.path.isfile(ICON):
        return
    png = os.path.join(ROOT, "attackshark", "web", "logo.png")
    try:
        from PIL import Image
    except ImportError:
        sys.exit("need Pillow to make the icon: pip install pillow")
    Image.open(png).convert("RGBA").save(
        ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                     (128, 128), (256, 256)])
    print(f"made {ICON}")


def stage_web(into):
    """A copy of the interface with anyone's private artwork left behind.

    `--add-data` takes a directory and takes all of it, and `attackshark/web`
    is exactly where the `.custom.` override files live - so building on a
    machine that has them quietly bakes them into the executable and ships
    them to everybody. That happened once. Staging a filtered copy is the only
    place this can be fixed, because by the time PyInstaller has the directory
    it is already too late.
    """
    dst = os.path.join(into, "attackshark", "web")
    shutil.copytree(os.path.join(ROOT, "attackshark", "web"), dst,
                    ignore=shutil.ignore_patterns("*.custom.*", "__pycache__"))
    left = sorted(f for f in os.listdir(dst) if ".custom." in f)
    assert not left, f"private artwork reached the bundle: {left}"
    return dst


def main():
    ensure_icon()
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
        "--name", NAME,
        "--icon", ICON,
        # the interface travels inside the exe, from the staged copy
        "--add-data", f"{web}{sep}attackshark{os.sep}web",
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

    exe = os.path.join(ROOT, "dist", NAME + ".exe")
    if os.path.isfile(exe):
        size = os.path.getsize(exe) / (1024 * 1024)
        print(f"\nbuilt {exe}  ({size:.1f} MB)")
    for junk in ("build", NAME + ".spec", "launch.py"):
        p = os.path.join(ROOT, junk)
        shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else (
            os.remove(p) if os.path.isfile(p) else None)


if __name__ == "__main__":
    main()

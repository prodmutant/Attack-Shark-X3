"""Turn an image into the right-hand backdrop for the UI.

    python tools/make_backdrop.py path/to/wallpaper.jpg

Scales it to the panel height, fades the left edge to transparent so it melts
into the page instead of sitting on it as a rectangle, and pulls the brightness
down so UI text stays readable on top.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "attackshark", "web", "backdrop.png")

try:
    from PIL import Image, ImageEnhance
except ImportError:
    sys.exit("this tool needs Pillow:  pip install pillow")

WIDTH, HEIGHT = 900, 1500


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--fade", type=float, default=0.62,
                    help="fraction of the width that ramps from clear to solid")
    ap.add_argument("--dim", type=float, default=0.62,
                    help="brightness multiplier (lower = darker)")
    ap.add_argument("--anchor", choices=("top", "center", "bottom"), default="top")
    a = ap.parse_args()

    if not os.path.isfile(a.image):
        sys.exit(f"no such file: {a.image}")

    im = Image.open(a.image).convert("RGB")
    # cover-fit the target box
    scale = max(WIDTH / im.width, HEIGHT / im.height)
    im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                   Image.LANCZOS)
    x = (im.width - WIDTH) // 2
    if a.anchor == "top":
        y = 0
    elif a.anchor == "bottom":
        y = im.height - HEIGHT
    else:
        y = (im.height - HEIGHT) // 2
    im = im.crop((x, y, x + WIDTH, y + HEIGHT))

    im = ImageEnhance.Brightness(im).enhance(a.dim)
    im = ImageEnhance.Color(im).enhance(0.85)

    # horizontal alpha ramp: clear on the left, solid at the right edge
    alpha = Image.new("L", (WIDTH, 1))
    ramp = int(WIDTH * a.fade)
    px = alpha.load()
    for i in range(WIDTH):
        if i < ramp:
            t = i / max(1, ramp)
            px[i, 0] = int(255 * (t ** 2.2))      # ease-in, no visible seam
        else:
            px[i, 0] = 255
    alpha = alpha.resize((WIDTH, HEIGHT))

    out = im.convert("RGBA")
    out.putalpha(alpha)
    out.save(OUT)
    print(f"wrote {OUT}  ({WIDTH}x{HEIGHT}, fade {a.fade:.2f}, dim {a.dim:.2f})")


if __name__ == "__main__":
    main()

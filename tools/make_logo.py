"""Fit any image into the header logo slot.

    python tools/make_logo.py path/to/image.png
    python tools/make_logo.py path/to/image.png --circle
    python tools/make_logo.py --placeholder

Centre-crops to a square (so nothing is squashed), resizes, and writes
attackshark/web/logo.png. --circle masks it to a disc with transparency and
also tells the page to round the frame.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "attackshark", "web", "logo.png")
CSS_FLAG = os.path.join(ROOT, "attackshark", "web", "logo.round")

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("this tool needs Pillow:  pip install pillow")

SIZE = 256


def square_crop(im, focus="center"):
    """Centre-crop to the largest square that fits."""
    w, h = im.size
    side = min(w, h)
    if focus == "top":
        box = ((w - side) // 2, 0, (w + side) // 2, side)
    elif focus == "bottom":
        box = ((w - side) // 2, h - side, (w + side) // 2, h)
    else:
        box = ((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2)
    return im.crop(box)


def circle_mask(im):
    mask = Image.new("L", (SIZE * 4, SIZE * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, SIZE * 4 - 1, SIZE * 4 - 1), fill=255)
    mask = mask.resize((SIZE, SIZE), Image.LANCZOS)   # supersampled edge
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def placeholder():
    """A neutral mark so the header is not empty before a real logo lands."""
    im = Image.new("RGBA", (SIZE, SIZE), (10, 10, 12, 255))
    d = ImageDraw.Draw(im)
    a, r = (233, 233, 236, 255), (224, 32, 29, 255)
    cx, s = SIZE // 2, SIZE // 5
    d.polygon([(cx, 40), (cx + s, 40 + s), (cx, 40 + 2 * s), (cx - s, 40 + s)], outline=a, width=6)
    d.polygon([(cx, SIZE - 40 - 2 * s), (cx + s, SIZE - 40 - s),
               (cx, SIZE - 40), (cx - s, SIZE - 40 - s)], outline=r, width=6)
    d.line([(cx, 40 + 2 * s), (cx, SIZE - 40 - 2 * s)], fill=a, width=4)
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?", help="source image (any format Pillow reads)")
    ap.add_argument("--circle", action="store_true", help="mask to a disc")
    ap.add_argument("--focus", choices=("center", "top", "bottom"), default="center",
                    help="which part to keep when cropping to a square")
    ap.add_argument("--box", help="exact source crop as x,y,w,h (overrides --focus)")
    ap.add_argument("--placeholder", action="store_true")
    a = ap.parse_args()

    if a.placeholder or not a.image:
        im = placeholder()
    else:
        if not os.path.isfile(a.image):
            sys.exit(f"no such file: {a.image}")
        im = Image.open(a.image).convert("RGBA")
        if a.box:
            x, y, w, h = (int(v) for v in a.box.split(","))
            im = im.crop((x, y, x + w, y + h))
        else:
            im = square_crop(im, a.focus)
        im = im.resize((SIZE, SIZE), Image.LANCZOS)

    if a.circle:
        im = circle_mask(im)
        open(CSS_FLAG, "w").close()
    elif os.path.exists(CSS_FLAG):
        os.remove(CSS_FLAG)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    im.save(OUT)
    print(f"wrote {OUT}  ({SIZE}x{SIZE}{', circular' if a.circle else ''})")


if __name__ == "__main__":
    main()

"""Fit any image into the header logo slot.

    python tools/make_logo.py IMAGE --circle --fit
    python tools/make_logo.py IMAGE --circle --fit --ring
    python tools/make_logo.py IMAGE --box 160,140,380,380
    python tools/make_logo.py --placeholder

Writes attackshark/web/logo.png. --circle masks it to a disc and tells the page
to round the frame.

Two ways to get the picture into the square, and the choice matters once it is
masked to a circle:

  default   crop to the largest square that fits, then fill it. Good for a
            photo, wrong for a mark - the corners of the square are exactly
            where a face keeps its horns and jaw, and the disc cuts them off.

  --fit     scale the whole image to sit inside the disc, on a black field.
            Nothing is cropped, so the mark reads complete.

--ring draws a circle just inside the edge, which is what stops a dark mark on
a dark header from dissolving into it.
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


def fit_inside(im, margin=0.055):
    """Scale the whole image to sit within the disc, centred on black.

    The disc's inscribed square is side/sqrt(2), so fitting there would leave a
    lot of empty ring. Most marks have their own dead space in the corners, so
    this fits to the disc's width less a small margin instead - everything
    stays inside the circle in practice, and the mark is not left tiny.
    """
    room = int(SIZE * (1.0 - 2 * margin))
    art = im.copy()
    art.thumbnail((room, room), Image.LANCZOS)
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 255))
    out.paste(art, ((SIZE - art.width) // 2, (SIZE - art.height) // 2), art)
    return out


def draw_ring(im, colour, width):
    """A ring just inside the edge, supersampled so it is not jagged."""
    scale = 4
    big = Image.new("RGBA", (SIZE * scale, SIZE * scale), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    inset = width * scale // 2
    d.ellipse((inset, inset, SIZE * scale - 1 - inset, SIZE * scale - 1 - inset),
              outline=colour, width=width * scale)
    ring = big.resize((SIZE, SIZE), Image.LANCZOS)
    out = im.copy()
    out.alpha_composite(ring)
    return out


def parse_colour(text):
    t = text.strip().lstrip("#")
    if len(t) == 6:
        return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    raise ValueError("colour must be RRGGBB hex")


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
    ap.add_argument("--fit", action="store_true",
                    help="scale the whole image inside the disc instead of "
                         "cropping it to fill - nothing is cut off")
    ap.add_argument("--margin", type=float, default=0.055,
                    help="with --fit, empty border as a fraction (default .055)")
    ap.add_argument("--ring", nargs="?", const="8fd14f", metavar="RRGGBB",
                    help="draw a ring just inside the edge (default green)")
    ap.add_argument("--ring-width", type=int, default=7,
                    help="ring thickness in output pixels (default 7)")
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
        if a.fit:
            im = fit_inside(im, a.margin)
        else:
            if not a.box:
                im = square_crop(im, a.focus)
            im = im.resize((SIZE, SIZE), Image.LANCZOS)

    if a.circle:
        im = circle_mask(im)
        open(CSS_FLAG, "w").close()
    if a.ring:
        # after the mask, so the ring is not clipped by its own edge
        im = draw_ring(im, parse_colour(a.ring), a.ring_width)
    elif os.path.exists(CSS_FLAG):
        os.remove(CSS_FLAG)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    im.save(OUT)
    bits = [f"{SIZE}x{SIZE}"]
    if a.fit: bits.append("fitted")
    if a.circle: bits.append("circular")
    if a.ring: bits.append("ringed")
    print(f"wrote {OUT}  ({', '.join(bits)})")


if __name__ == "__main__":
    main()

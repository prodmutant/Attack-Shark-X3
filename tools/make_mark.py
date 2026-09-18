"""Draw the interface's logo and backdrop from the traced mouse outline.

    python tools/make_mark.py --logo --backdrop

Both assets come out of `captures/ref/mouse_outline.png`, the same hand-drawn
reference `trace_outline.py` uses for the shell in the page - so the mark, the
watermark and the drawing in the interface are all the same object, and all of
them are this project's own work. Nothing here is a stock image and nothing is
traced from anyone else's art.

It reuses `trace_outline`'s primitives rather than restating them, so a change
to how the outline is read moves all three together. What it does not reuse is
`trace()` itself: that fits bezier segments and returns an SVG path string,
which is the right answer for the page and the wrong one for a rasteriser. The
polygon is built here instead, densely enough that at four times the output
size the difference from the curve is below a pixel.

Colours are the default palette's tokens, so the assets sit in the theme rather
than beside it. They are deliberately two-tone: the interface ships six
palettes and a full-colour mark would clash with five of them.
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace_outline import edges, ink_mask, resample, split_drawings  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REF = os.path.join(ROOT, "captures", "ref", "mouse_outline.png")
WEB = os.path.join(ROOT, "attackshark", "web")

BG = (13, 11, 14, 255)          # a touch above --bg, so the badge reads as an object
INK = (234, 227, 226, 255)      # --fg, bone white
ACCENT = (165, 31, 39, 255)     # --accent, deep crimson

SS = 4                          # supersample, then box-filter down


def outline(index, points=320, mirror=True, smooth=9, soften=5):
    """A dense closed polygon around drawing `index` of the reference.

    Mirrors the right flank onto the left for the top view, for the reason
    `trace_outline.trace` gives: the side buttons sit on the left only, so the
    right edge is already a clean silhouette and averaging the two drags the
    button tabs into the shape.
    """
    mask = ink_mask(REF)
    spans = split_drawings(mask)
    if index >= len(spans):
        sys.exit(f"{REF} has {len(spans)} drawing(s); asked for index {index}")
    sub = mask[:, spans[index][0]:spans[index][1]]
    left, right, _y0, _y1 = edges(sub, smooth, soften)
    h = len(left)

    if mirror:
        q = max(1, h // 4)
        ends = np.concatenate([(left[:q] + right[:q]) / 2.0,
                               (left[-q:] + right[-q:]) / 2.0])
        centre = float(np.median(ends))
        right = np.maximum(right, centre)
        left = 2.0 * centre - right

    dense = [(right[i], float(i)) for i in range(h)]
    dense += [(left[i], float(i)) for i in range(h - 1, -1, -1)]
    return resample(dense, points)


def fitted(poly, box_w, box_h, pad):
    """Scale a polygon to sit inside a box, centred, keeping its proportions."""
    return placed(poly, (pad, pad, box_w - pad, box_h - pad))


def placed(poly, rect):
    """Scale a polygon to fit a given rectangle, centred, proportions kept."""
    x0, y0, x1, y1 = rect
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    k = min((x1 - x0) / w, (y1 - y0) / h)
    ox = x0 + ((x1 - x0) - w * k) / 2.0 - min(xs) * k
    oy = y0 + ((y1 - y0) - h * k) / 2.0 - min(ys) * k
    return [(x * k + ox, y * k + oy) for x, y in poly]


def make_logo(path, size=256):
    """A disc, a crimson ring, and the shell seen from above.

    The mark carries its own margin and ring because the page puts no border
    on it - a square frame around a disc leaves four visible corners.

    The silhouette alone is not enough. Filled and shrunk to header size it
    reads as a rounded blob that could be anything; what makes it legibly a
    mouse is the pair of features every mouse has and nothing else does - the
    seam between the two buttons, and the wheel sitting in it. Both are cut
    back out in the disc colour rather than drawn on top, so the mark stays
    two-tone and survives all six palettes.
    """
    n = size * SS
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    ring = max(2, round(n * 0.024))
    d.ellipse([0, 0, n - 1, n - 1], fill=BG)
    d.ellipse([ring // 2, ring // 2, n - 1 - ring // 2, n - 1 - ring // 2],
              outline=ACCENT, width=ring)

    # The shell is much taller than it is wide, so height is what fills the
    # disc; a generous inset here would leave it floating in the middle.
    poly = fitted(outline(0), n, n, pad=n * 0.11)
    d.polygon(poly, fill=INK)

    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    mid = (x0 + x1) / 2.0
    sw, sh = x1 - x0, y1 - y0

    seam = max(1, round(sw * 0.045))
    d.line([(mid, y0 + sh * 0.02), (mid, y0 + sh * 0.46)], fill=BG, width=seam)

    wheel_w = sw * 0.17
    d.rounded_rectangle(
        [mid - wheel_w / 2, y0 + sh * 0.10, mid + wheel_w / 2, y0 + sh * 0.27],
        radius=wheel_w / 2, fill=BG)

    img.resize((size, size), Image.LANCZOS).save(path)
    print(f"  logo      {path}  {size}x{size}")


def make_backdrop(path, w=900, h=1500):
    """The shell as a drawing, enormous and faint, for the right-hand column.

    Outlined rather than filled. Filled, at this size, it is an enormous pale
    blob - there is no detail in a silhouette to survive being blown up, and
    behind the page's 42 % and its scrim it becomes a wash that reads as a
    smudge on the screen. Drawn as strokes it reads as what the reference
    actually is, a technical drawing of the device, which is also what this
    project is about.

    Everything sits in the upper half of the canvas on purpose. The page scales
    this with `cover` against a panel far squarer than 900x1500: the width
    always fills exactly, and what varies is how much of the bottom survives -
    between roughly 55 % and 90 % of the height depending on the window. So the
    whole drawing goes in the top half, where every window shows all of it. A
    drawing you can only see two thirds of does not read as a drawing, it reads
    as stray curves behind the text.

    It is also inset from the left, because of the ramp below: a shape that
    runs into the fade loses the side that would have closed it, and an open
    arc is exactly the stray curve this is trying not to be.

    The alpha ramp is baked into the asset rather than done in CSS - a gradient
    applied afterwards would not know where the artwork ended up after that
    scaling.
    """
    n_w, n_h = w * SS // 2, h * SS // 2
    img = Image.new("RGBA", (n_w, n_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    stroke = max(2, round(n_w * 0.009))
    poly = placed(outline(0),
                  (n_w * 0.30, n_h * 0.11, n_w * 0.94, n_h * 0.55))
    d.line(list(poly) + [poly[0]], fill=INK, width=stroke, joint="curve")

    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    mid = (x0 + x1) / 2.0
    sw, sh = x1 - x0, y1 - y0

    d.line([(mid, y0), (mid, y0 + sh * 0.46)], fill=INK, width=stroke)

    wheel_w = sw * 0.15
    d.rounded_rectangle(
        [mid - wheel_w / 2, y0 + sh * 0.09, mid + wheel_w / 2, y0 + sh * 0.26],
        radius=wheel_w / 2, outline=INK, width=stroke)

    # the two side buttons, which the mirrored outline necessarily drops
    btn_w = sw * 0.055
    for top, bot in ((0.30, 0.40), (0.42, 0.50)):
        d.rounded_rectangle(
            [x0 - btn_w * 0.35, y0 + sh * top, x0 + btn_w, y0 + sh * bot],
            radius=btn_w * 0.35, outline=INK, width=stroke)

    img = img.resize((w, h), Image.LANCZOS)

    # Fade towards the left edge so it melts into the page instead of sitting
    # on it as a rectangle - but only down to a floor, not to nothing. Fading
    # the far side of the drawing to zero deletes the edge that closes the
    # shape, and what is left stops looking like an object.
    ramp = 0.38 + 0.62 * np.linspace(0.0, 1.0, w, dtype=np.float32) ** 1.5
    a = np.asarray(img.getchannel("A")).astype(np.float32) / 255.0
    img.putalpha(Image.fromarray(
        np.clip(a * ramp[None, :] * 255.0, 0, 255).astype(np.uint8)))
    img.save(path)
    print(f"  backdrop  {path}  {w}x{h}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--logo", action="store_true")
    ap.add_argument("--backdrop", action="store_true")
    ap.add_argument("--out", default=WEB, help="directory to write into")
    a = ap.parse_args()
    if not (a.logo or a.backdrop):
        ap.error("nothing to do: pass --logo, --backdrop, or both")
    print(f"tracing {os.path.relpath(REF, ROOT)}")
    if a.logo:
        make_logo(os.path.join(a.out, "logo.png"))
    if a.backdrop:
        make_backdrop(os.path.join(a.out, "backdrop.png"))


if __name__ == "__main__":
    main()

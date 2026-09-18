"""Turn a line-art outline of the mouse into the SVG path the web UI uses.

The button map used to be a hand-drawn bezier, which is guesswork: an ellipse
with a taper reads as an egg, and the real device is not that shape at all. It
has close-to-parallel sides, a slight waist behind the main buttons, and its
widest point low in the palm. Rather than keep nudging control points, this
traces a reference drawing and emits the path.

    python tools/trace_outline.py REFERENCE.png
    python tools/trace_outline.py REFERENCE.png --side          # side view too
    python tools/trace_outline.py REFERENCE.png --points 30

Input: any image with the outline drawn in a colour that is not grey. Both a
top view and a side view side by side is fine - the drawings are split on the
vertical gap between them and the leftmost is taken as the top view.

The side buttons are deliberately smoothed out of the silhouette: they are
drawn separately in the UI as their own hit targets, so the shell wants to be
the bare shape. A wide median filter over each edge does that without flattening
the real curvature.

Output is a `d` attribute, normalised to the requested viewBox, ready to paste
into `attackshark/web/index.html`.
"""
from __future__ import annotations

import argparse
import sys

try:
    import numpy as np
    from PIL import Image
except ImportError:                                     # pragma: no cover
    print("needs Pillow and numpy:  pip install pillow numpy", file=sys.stderr)
    raise SystemExit(1)


def ink_mask(path):
    """Boolean mask of the drawn outline: saturated pixels, any hue."""
    a = np.asarray(Image.open(path).convert("RGB")).astype(int)
    mx = a.max(2)
    mn = a.min(2)
    # bright enough to be a stroke, and coloured enough not to be the paper
    return (mx > 80) & ((mx - mn) > 45)


def split_drawings(mask, min_gap=20):
    """Column ranges of each drawing, split on blank vertical gutters."""
    cols = mask.any(0)
    spans, start = [], None
    run = 0
    for x, on in enumerate(cols):
        if on:
            if start is None:
                start = x
            run = 0
        else:
            run += 1
            if start is not None and run >= min_gap:
                spans.append((start, x - run + 1))
                start = None
    if start is not None:
        spans.append((start, len(cols)))
    return spans


def median_filter(values, window):
    """Median smoothing - removes the side-button tabs without rounding off
    the genuine curvature the way a mean would."""
    n = len(values)
    half = max(1, window // 2)
    out = np.empty(n)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out[i] = np.median(values[lo:hi])
    return out


def box_filter(values, window):
    """Moving average. Runs after the median: the median deletes the tabs,
    this takes the stroke-width jitter out of what is left, which otherwise
    shows up as visible lumps once the curve is drawn."""
    if window < 2:
        return values
    n = len(values)
    half = max(1, window // 2)
    pad = np.concatenate([np.full(half, values[0]), values,
                          np.full(half, values[-1])])
    kern = np.ones(half * 2 + 1) / (half * 2 + 1)
    return np.convolve(pad, kern, mode="valid")[:n]


def edges(mask, smooth=9, soften=5):
    """Left and right boundary per row, smoothed, with the row range."""
    rows = np.nonzero(mask.any(1))[0]
    y0, y1 = int(rows.min()), int(rows.max())
    left = np.empty(y1 - y0 + 1)
    right = np.empty(y1 - y0 + 1)
    for i, y in enumerate(range(y0, y1 + 1)):
        xs = np.nonzero(mask[y])[0]
        if len(xs) == 0:                       # gap in the stroke; hold the last
            left[i] = left[i - 1] if i else 0
            right[i] = right[i - 1] if i else 0
        else:
            left[i] = xs.min()
            right[i] = xs.max()
    if smooth > 1:
        left = median_filter(left, smooth)
        right = median_filter(right, smooth)
    if soften > 1:
        left = box_filter(left, soften)
        right = box_filter(right, soften)
    return left, right, y0, y1


def catmull_rom(points, closed=True):
    """Smooth cubic path through every point. Catmull-Rom gives a curve that
    passes through the samples, so the result still matches the drawing."""
    n = len(points)
    if n < 3:
        return ""
    def at(i):
        return points[i % n] if closed else points[min(max(i, 0), n - 1)]
    d = [f"M{points[0][0]:.1f} {points[0][1]:.1f}"]
    last = n if closed else n - 1
    for i in range(last):
        p0, p1, p2, p3 = at(i - 1), at(i), at(i + 1), at(i + 2)
        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0, p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6.0, p2[1] - (p3[1] - p1[1]) / 6.0)
        d.append(f"C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} "
                 f"{p2[0]:.1f} {p2[1]:.1f}")
    if closed:
        d.append("Z")
    return "".join(d)


def trace(mask, span, points, box_w, box_h, pad, smooth, mirror, soften=5):
    sub = mask[:, span[0]:span[1]]
    left, right, y0, y1 = edges(sub, smooth, soften)
    h = len(left)

    if mirror:
        # Mirror the RIGHT edge rather than averaging the two. The side buttons
        # sit on the left only, so the right edge is already a clean silhouette;
        # averaging drags their tabs into the shape, and filtering them out
        # afterwards needs a window so wide it chamfers the nose and tail into
        # a rounded rectangle.
        #
        # The axis is measured where the device is tab-free: the top and bottom
        # quarters.
        q = max(1, h // 4)
        ends = np.concatenate([(left[:q] + right[:q]) / 2.0,
                               (left[-q:] + right[-q:]) / 2.0])
        centre = float(np.median(ends))
        right = np.maximum(right, centre)      # never cross the axis
        left = 2.0 * centre - right

    src_w = float(right.max() - left.min())
    src_h = float(h)
    scale = min((box_w - 2 * pad) / src_w, (box_h - 2 * pad) / src_h)
    ox = (box_w - src_w * scale) / 2.0 - left.min() * scale
    oy = (box_h - src_h * scale) / 2.0

    def pt(x, i):
        return (x * scale + ox, i * scale + oy)

    # Dense contour first: every row down the right edge, then back up the
    # left. Sampling by row alone would starve the nose and tail, where the
    # outline runs nearly horizontal - which is exactly where kinks appear.
    dense = [pt(right[i], i) for i in range(h)]
    dense += [pt(left[i], i) for i in range(h - 1, -1, -1)]

    ring = resample(dense, points * 2)
    return catmull_rom(ring), (src_w, src_h, src_w / src_h)


def resample(poly, count):
    """Evenly spaced points around a closed polygon, by arc length.

    Spacing by distance rather than by row is what keeps the curvature honest
    at the caps: they get as many samples per millimetre as the flanks do.
    """
    pts = list(poly)
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    seg = []
    total = 0.0
    for a, b in zip(pts, pts[1:]):
        d = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5
        seg.append(d)
        total += d
    if total <= 0:
        return pts[:-1]

    out = []
    step = total / count
    target = 0.0
    walked = 0.0
    i = 0
    for _ in range(count):
        while i < len(seg) and walked + seg[i] < target:
            walked += seg[i]
            i += 1
        if i >= len(seg):
            break
        a, b = pts[i], pts[i + 1]
        t = 0.0 if seg[i] == 0 else (target - walked) / seg[i]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        target += step
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image")
    ap.add_argument("--points", type=int, default=26,
                    help="samples per side (default 26)")
    ap.add_argument("--width", type=int, default=156, help="viewBox width")
    ap.add_argument("--height", type=int, default=300, help="viewBox height")
    ap.add_argument("--pad", type=float, default=7.0, help="viewBox margin")
    ap.add_argument("--smooth", type=int, default=9,
                    help="median window, in source rows, against stroke noise")
    ap.add_argument("--soften", type=int, default=5,
                    help="moving-average window after the median (0 disables)")
    ap.add_argument("--no-mirror", action="store_true",
                    help="keep the drawing's own asymmetry")
    ap.add_argument("--side", action="store_true",
                    help="trace the second drawing (side view) as well")
    args = ap.parse_args()

    mask = ink_mask(args.image)
    spans = split_drawings(mask)
    if not spans:
        print("no outline found - is it drawn in a colour?", file=sys.stderr)
        return 1
    print(f"{len(spans)} drawing(s) at columns {spans}\n")

    wanted = spans if args.side else spans[:1]
    for n, span in enumerate(wanted):
        d, (w, h, ratio) = trace(mask, span, args.points, args.width,
                                 args.height, args.pad, args.smooth,
                                 not args.no_mirror, args.soften)
        label = "top view" if n == 0 else f"drawing {n + 1}"
        print(f"--- {label}: {w:.0f} x {h:.0f} source px, aspect {ratio:.3f} ---")
        print(d)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

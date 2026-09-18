"""Theme tokens, shared by the web UI and the desktop app.

`web/themes.css` is the original, and it stays the original: a browser needs
the CSS, and duplicating colours by hand is how two front ends end up looking
like two products. These tables are generated from it by
`tools/sync_themes.py`, so editing the stylesheet and re-running keeps the
desktop app in step.

Only the tokens Tk can actually use are carried across. The web has gradients,
a backdrop photo and an alpha scrim; a Tk canvas has flat fills, so the washes
are dropped rather than faked.
"""
from __future__ import annotations

#: token -> what it is for, so the desktop app reads as something other than
#: a pile of hex
ROLES = {
    "bg": "window background",
    "panel": "dialog and raised background",
    "line": "hairline rule",
    "line2": "stronger rule, control borders",
    "fg": "primary text",
    "dim": "secondary text",
    "accent": "the one accent",
    "accent_hi": "accent, hovered",
    "accent_dim": "accent at rest, for fills",
    "ok": "the live/attached colour",
    "shell_a": "mouse shell, top of the gradient",
    "shell_b": "mouse shell, bottom",
    "btn_a": "mouse button face, top",
    "btn_b": "mouse button face, bottom",
    "edge": "mouse outline",
    "seam": "mouse seams",
    "surface": "control background",
    "surface_hi": "control background, hovered",
    "input_bg": "text field background",
    "seg_bg": "segmented control background",
    "seg_on": "segmented control, selected",
    "pad": "mouse side-button pad",
}

THEMES = {
    "crimson": {
        "bg": "#050506", "panel": "#0c0a0d", "line": "#1c181d", "line2": "#2e2630",
        "fg": "#eae3e2", "dim": "#877e86",
        "accent": "#a51f27", "accent_hi": "#c8262f", "accent_dim": "#3f1418",
        "ok": "#8fd14f",
        "shell_a": "#1e1824", "shell_b": "#09070b",
        "btn_a": "#282030", "btn_b": "#171320",
        "edge": "#4a3f52", "seam": "#3a3142",
        "surface": "#151016", "surface_hi": "#1d1520",
        "input_bg": "#0a080b", "seg_bg": "#0e0b0f", "seg_on": "#221a26",
        "pad": "#221a28",
    },
    "ember": {
        "bg": "#060404", "panel": "#100a0a", "line": "#231616", "line2": "#3a2222",
        "fg": "#f2e8e6", "dim": "#96837f",
        "accent": "#e01b24", "accent_hi": "#ff2e38", "accent_dim": "#4d1216",
        "ok": "#9fd356",
        "shell_a": "#241416", "shell_b": "#0b0506",
        "btn_a": "#31191c", "btn_b": "#1a0d0f",
        "edge": "#5a3038", "seam": "#452429",
        "surface": "#1a1012", "surface_hi": "#24161a",
        "input_bg": "#0d0708", "seg_bg": "#120b0c", "seg_on": "#2a181c",
        "pad": "#2b181c",
    },
    "venom": {
        "bg": "#040605", "panel": "#0a0f0c", "line": "#16211a", "line2": "#243329",
        "fg": "#e6f2e9", "dim": "#7e9687",
        "accent": "#4ade4a", "accent_hi": "#69f269", "accent_dim": "#15361a",
        "ok": "#4ade4a",
        "shell_a": "#122018", "shell_b": "#050a07",
        "btn_a": "#172b1d", "btn_b": "#0c1710",
        "edge": "#2f5138", "seam": "#24402c",
        "surface": "#0f1712", "surface_hi": "#152018",
        "input_bg": "#070c09", "seg_bg": "#0b120e", "seg_on": "#16251b",
        "pad": "#16251b",
    },
    "paper": {
        "bg": "#f4f4f6", "panel": "#ffffff", "line": "#e2e2e8", "line2": "#cfcfd8",
        "fg": "#16161a", "dim": "#6b6b78",
        "accent": "#b3141c", "accent_hi": "#d0181f", "accent_dim": "#f3dadb",
        "ok": "#2f7d32",
        "shell_a": "#ffffff", "shell_b": "#ebebf0",
        "btn_a": "#f7f7fa", "btn_b": "#e8e8ee",
        "edge": "#b4b4c0", "seam": "#c8c8d2",
        "surface": "#ffffff", "surface_hi": "#f0f0f4",
        "input_bg": "#ffffff", "seg_bg": "#f7f7fa", "seg_on": "#e6e6ee",
        "pad": "#e6e6ee",
    },
    "sakura": {
        "bg": "#faf3f6", "panel": "#ffffff", "line": "#f0dde5", "line2": "#e0c3d1",
        "fg": "#241a1f", "dim": "#7d6570",
        "accent": "#e8558c", "accent_hi": "#f26a9d", "accent_dim": "#fbe1ec",
        "ok": "#3f9e6a",
        "shell_a": "#ffffff", "shell_b": "#f8e9ef",
        "btn_a": "#fdf2f6", "btn_b": "#f6e2ea",
        "edge": "#d9aebf", "seam": "#e6c6d3",
        "surface": "#ffffff", "surface_hi": "#f7ecf1",
        "input_bg": "#ffffff", "seg_bg": "#fdf5f8", "seg_on": "#f4dde7",
        "pad": "#f4dde7",
    },
    "citrine": {
        "bg": "#f7f4e9", "panel": "#fffdf4", "line": "#eae3cd", "line2": "#d7ccab",
        "fg": "#201d12", "dim": "#726a54",
        "accent": "#c99700", "accent_hi": "#e0aa08", "accent_dim": "#f5e7bd",
        "ok": "#4a8a2f",
        "shell_a": "#ffffff", "shell_b": "#f8f2dd",
        "btn_a": "#fffbe9", "btn_b": "#f4ecd3",
        "edge": "#c9b98d", "seam": "#ddd0ab",
        "surface": "#fffdf4", "surface_hi": "#f6f0dd",
        "input_bg": "#fffdf4", "seg_bg": "#fbf7e8", "seg_on": "#eee4c7",
        "pad": "#eee4c7",
    },
}

DEFAULT = "crimson"

#: themes whose background is light, so hairlines and hovers need to darken
#: rather than lighten
LIGHT = {"paper", "sakura", "citrine"}


def palette(name):
    """Token table for a theme, falling back to the default."""
    return THEMES.get(name, THEMES[DEFAULT])


def is_light(name):
    return name in LIGHT

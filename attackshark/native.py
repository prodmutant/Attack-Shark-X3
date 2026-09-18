"""Desktop application for the Attack Shark X3.

A real window, not a browser: `attackshark app`. It talks to the device
directly through `server.apply_patch` and friends, so the desktop and the web
UI share one write path and one set of rules about what gets pushed when. No
HTTP, no localhost port, nothing to leave running.

Tkinter, because the rest of this project has no third-party dependencies and
there is no reason for the desktop build to be the thing that adds one. `ttk`
is avoided on purpose: its themes fight custom colours on Windows, and every
control here is drawn from flat `tk` widgets so the palette in `theme.py` is
the only thing deciding how it looks.

The mouse is drawn from `shell_outline.py`, which the same tracer produced for
the web UI's SVG - one trace, two front ends, no chance of them disagreeing
about the shape of the hardware.
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox

from . import macro as M
from . import protocol as P
from . import server as SRV
from . import theme as TH
from .device import AttackSharkX3
from .shell_outline import BOX, OUTLINE

#: where the main buttons end, in trace units - the same seam the web UI draws
SEAM_Y = 140.0

MONO = ("Consolas", 9)
MONO_B = ("Consolas", 9, "bold")
SANS = ("Segoe UI", 9)
SANS_S = ("Segoe UI", 8)
TITLE = ("Segoe UI", 15, "bold")

PAGES = ("Dashboard", "Macros", "Themes")


# --------------------------------------------------------------- widgets --
class Flat(tk.Button):
    """A button that obeys the palette instead of the platform."""

    def __init__(self, parent, pal, text, command=None, kind="normal", **kw):
        self.pal = pal
        self.kind = kind
        bg = pal["accent"] if kind == "primary" else pal["surface"]
        fg = "#ffffff" if kind == "primary" else pal["fg"]
        super().__init__(parent, text=text, command=command, font=SANS,
                         bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
                         relief="flat", bd=0, padx=13, pady=6,
                         highlightthickness=1,
                         highlightbackground=pal["accent"] if kind == "primary"
                         else pal["line2"],
                         highlightcolor=pal["accent"], cursor="hand2", **kw)
        self.bind("<Enter>", self._on)
        self.bind("<Leave>", self._off)

    def _on(self, _=None):
        if str(self["state"]) == "disabled":
            return
        self.config(bg=self.pal["accent_hi"] if self.kind == "primary"
                    else self.pal["surface_hi"],
                    highlightbackground=self.pal["accent"]
                    if self.kind == "primary" else self.pal["edge"])

    def _off(self, _=None):
        self.config(bg=self.pal["accent"] if self.kind == "primary"
                    else self.pal["surface"],
                    highlightbackground=self.pal["accent"]
                    if self.kind == "primary" else self.pal["line2"])


class Segment(tk.Frame):
    """Square segmented control - the same shape as the web UI's."""

    def __init__(self, parent, pal, options, value, on_pick, hot=None):
        super().__init__(parent, bg=pal["line2"], highlightthickness=0)
        self.pal, self.on_pick = pal, on_pick
        self.hot = hot or (lambda v: True)
        self.buttons = []
        for i, (label, val) in enumerate(options):
            b = tk.Label(self, text=label, font=MONO, padx=11, pady=5,
                         cursor="hand2")
            b.grid(row=0, column=i, padx=(0 if i == 0 else 1, 0), sticky="nsew")
            b.bind("<Button-1>", lambda _e, v=val: self.on_pick(v))
            self.buttons.append((b, val))
        self.select(value)

    def select(self, value):
        p = self.pal
        for b, val in self.buttons:
            on = val == value
            if on and self.hot(val):
                b.config(bg=p["accent"], fg="#ffffff")
            elif on:
                b.config(bg=p["seg_on"], fg=p["fg"])
            else:
                b.config(bg=p["seg_bg"], fg=p["dim"])


class Meter(tk.Canvas):
    """Battery bar. Square, with a nub, like the web one."""

    def __init__(self, parent, pal):
        super().__init__(parent, width=42, height=15, bg=pal["bg"],
                         highlightthickness=0, bd=0)
        self.pal = pal

    def show(self, pct, colour):
        p = self.pal
        self.delete("all")
        self.create_rectangle(0, 1, 36, 13, outline=p["line2"])
        self.create_rectangle(37, 5, 39, 9, outline="", fill=p["line2"])
        if pct:
            w = max(1, int(32 * min(100, max(0, pct)) / 100))
            self.create_rectangle(2, 3, 2 + w, 11, outline="", fill=colour)


class MouseMap(tk.Canvas):
    """The device, drawn from the traced outline, with clickable buttons."""

    W, H = 196, 377            # on-screen size; the trace is in BOX units

    def __init__(self, parent, pal, on_click, on_hover):
        super().__init__(parent, width=self.W, height=self.H, bg=pal["bg"],
                         highlightthickness=0, bd=0)
        self.pal = pal
        self.on_click = on_click
        self.on_hover = on_hover
        self.zones = {}
        self.draw()

    def sx(self, x):
        return x * self.W / BOX[0]

    def sy(self, y):
        return y * self.H / BOX[1]

    def draw(self):
        p = self.pal
        self.config(bg=p["bg"])
        self.delete("all")
        self.zones = {}

        pts = [c for x, y in OUTLINE for c in (self.sx(x), self.sy(y))]
        self.create_polygon(pts, fill=p["shell_a"], outline=p["edge"], width=2,
                            smooth=True, splinesteps=12, tags="shell")

        # The main buttons have to be cut out of the outline itself: Tk has no
        # clipping, so a plain rectangle would hang outside the shell. Take the
        # outline above the seam and split it on the centre line.
        for n, face in ((1, self.face_points(left=True)),
                        (2, self.face_points(left=False))):
            item = self.create_polygon(face, fill=p["btn_a"], outline="",
                                       smooth=True, splinesteps=10)
            self.zones[n] = item
            self.hook(item, n)

        seam = self.sy(SEAM_Y)
        self.create_line(self.sx(11), seam, self.sx(78), self.sy(SEAM_Y + 7),
                         self.sx(145), seam, fill=p["seam"], smooth=True)
        self.create_line(self.sx(78), self.sy(16), self.sx(78), seam,
                         fill=p["seam"])

        self.pad(3, 70, 38, 86, 92)
        self.pad(4, 12, 100, 25, 132)
        self.pad(5, 12, 138, 25, 160)

        r = self.sx(2.5)
        cx, cy = self.sx(78), self.sy(168)
        self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=p["seam"])

        for n, (lx, ly) in {1: (50, 104), 2: (106, 104), 3: (78, 65),
                            4: (18, 116), 5: (18, 149)}.items():
            self.create_text(self.sx(lx), self.sy(ly), text=str(n),
                             fill=p["dim"], font=MONO_B, tags=("lbl%d" % n,))

    def face_points(self, left):
        """One main button: the outline above the seam, split on the axis.

        The outline is a closed ring whose first point sits partway along the
        nose, so selecting by coordinate alone takes points from both ends of
        the traversal and the polygon jumps across the shell. Rotating the ring
        to start at the tail makes the part above the seam a single arc.
        """
        axis = 78.0
        ring = list(OUTLINE)
        tail = max(range(len(ring)), key=lambda i: ring[i][1])
        ring = ring[tail:] + ring[:tail]

        arc = [pt for pt in ring if pt[1] <= SEAM_Y]
        if not arc:
            return []

        # the arc runs up one flank, over the nose and down the other; it
        # crosses the axis exactly once
        cut = next((i for i in range(1, len(arc))
                    if (arc[i - 1][0] >= axis) != (arc[i][0] >= axis)), None)
        if cut is None:
            return []
        first, second = arc[:cut], arc[cut:]
        side = first if (first[0][0] < axis) == left else second
        if not side:
            return []

        closed = [(axis, side[0][1])] + side + [(axis, side[-1][1])]
        return [c for x, y in closed for c in (self.sx(x), self.sy(y))]

    def pad(self, n, x0, y0, x1, y1):
        """A side button or the wheel slot: visible, and its own hit target."""
        p = self.pal
        item = self.create_rectangle(self.sx(x0), self.sy(y0),
                                     self.sx(x1), self.sy(y1),
                                     outline=p["seam"], fill=p["pad"])
        self.zones[n] = item
        self.hook(item, n)

    def hook(self, item, n):
        # not _bind: Canvas already has one, and shadowing it breaks tag_bind
        self.tag_bind(item, "<Button-1>", lambda _e: self.on_click(n))
        self.tag_bind(item, "<Enter>", lambda _e: self.on_hover(n, True))
        self.tag_bind(item, "<Leave>", lambda _e: self.on_hover(n, False))

    def highlight(self, n, on):
        p = self.pal
        item = self.zones.get(n)
        if not item:
            return
        rest = p["btn_a"] if n in (1, 2) else p["pad"]
        self.itemconfig(item, fill=p["accent_dim"] if on else rest)
        for i in self.find_withtag("lbl%d" % n):
            self.itemconfig(i, fill=p["fg"] if on else p["dim"])


# ------------------------------------------------------------------- app --
class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.theme_name = TH.DEFAULT
        self.pal = TH.palette(self.theme_name)
        self.snap = None
        self.page = PAGES[0]
        self._widgets = {}

        self.title("PRODMUTANT — X3 Driver")
        self.geometry("1180x760")
        self.minsize(940, 640)
        self.configure(bg=self.pal["bg"])

        self.build()
        self.refresh()
        # the mouse pushes battery rather than answering reads, so it arrives
        # on its own schedule; poll off-thread and never block the UI
        threading.Thread(target=self._battery_loop, daemon=True).start()
        self.after(2500, self._tick)

    # ------------------------------------------------------------- chrome --
    def build(self):
        p = self.pal
        for w in self.winfo_children():
            w.destroy()

        head = tk.Frame(self, bg=p["bg"], height=64)
        head.pack(fill="x")
        head.pack_propagate(False)

        brand = tk.Frame(head, bg=p["bg"])
        brand.pack(side="left", padx=(18, 0))
        tk.Label(brand, text="PRODMUTANT", font=TITLE, bg=p["bg"],
                 fg=p["fg"]).pack(anchor="w")
        tk.Label(brand, text="X 3   D R I V E R", font=SANS_S, bg=p["bg"],
                 fg=p["dim"]).pack(anchor="w")

        nav = tk.Frame(head, bg=p["bg"])
        nav.pack(side="left", padx=26)
        self._nav = {}
        for name in PAGES:
            holder = tk.Frame(nav, bg=p["bg"])
            holder.pack(side="left")
            lab = tk.Label(holder, text=name.upper(), font=SANS, bg=p["bg"],
                           fg=p["fg"] if name == self.page else p["dim"],
                           padx=13, pady=6, cursor="hand2")
            lab.pack()
            rule = tk.Frame(holder, height=2, bg=p["accent"] if name == self.page
                            else p["bg"])
            rule.pack(fill="x")
            lab.bind("<Button-1>", lambda _e, n=name: self.show(n))
            self._nav[name] = (lab, rule)

        status = tk.Frame(head, bg=p["bg"])
        status.pack(side="right", padx=18)
        self.meter = Meter(status, p)
        self.meter.pack(side="left")
        self.batt_lab = tk.Label(status, text="—", font=MONO, bg=p["bg"],
                                 fg=p["fg"], width=5, anchor="w")
        self.batt_lab.pack(side="left", padx=(6, 18))
        self.link_dot = tk.Canvas(status, width=8, height=8, bg=p["bg"],
                                  highlightthickness=0)
        self.link_dot.pack(side="left")
        self.link_lab = tk.Label(status, text="connecting…", font=SANS,
                                 bg=p["bg"], fg=p["fg"])
        self.link_lab.pack(side="left", padx=8)

        tk.Frame(self, height=1, bg=p["line2"]).pack(fill="x")

        body = tk.Frame(self, bg=p["bg"])
        body.pack(fill="both", expand=True)

        side = tk.Frame(body, bg=p["bg"], width=232)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.map = MouseMap(side, p, self.assign, self.hover)
        self.map.pack(pady=(16, 10))
        self.btnlist = tk.Frame(side, bg=p["bg"])
        self.btnlist.pack(fill="x", padx=16)

        tk.Frame(body, width=1, bg=p["line"]).pack(side="left", fill="y")

        self.pages = tk.Frame(body, bg=p["bg"])
        self.pages.pack(side="left", fill="both", expand=True)
        self.page_frames = {}
        for name in PAGES:
            f = tk.Frame(self.pages, bg=p["bg"])
            self.page_frames[name] = f
        self.build_dashboard(self.page_frames["Dashboard"])
        self.build_macros(self.page_frames["Macros"])
        self.build_themes(self.page_frames["Themes"])
        self.show(self.page)

    def show(self, name):
        self.page = name
        p = self.pal
        for n, (lab, rule) in self._nav.items():
            lab.config(fg=p["fg"] if n == name else p["dim"])
            rule.config(bg=p["accent"] if n == name else p["bg"])
        for n, f in self.page_frames.items():
            f.pack_forget()
        self.page_frames[name].pack(fill="both", expand=True, padx=22, pady=18)

    # ------------------------------------------------------------ helpers --
    def h2(self, parent, text):
        p = self.pal
        box = tk.Frame(parent, bg=p["bg"])
        box.pack(fill="x", pady=(16, 8))
        tk.Label(box, text=text.upper(), font=SANS_S, bg=p["bg"], fg=p["dim"]
                 ).pack(anchor="w")
        tk.Frame(box, height=1, bg=p["line"]).pack(fill="x", pady=(5, 0))
        return box

    def hint(self, parent, text):
        tk.Label(parent, text=text, font=SANS_S, bg=self.pal["bg"],
                 fg=self.pal["dim"], justify="left", wraplength=560
                 ).pack(anchor="w", pady=(6, 0))

    def patch(self, body):
        try:
            self.snap = SRV.apply_patch(body)
        except Exception as exc:
            messagebox.showerror("Attack Shark X3", str(exc))
            return
        self.refresh()

    # --------------------------------------------------------- dashboard --
    def build_dashboard(self, root):
        p = self.pal
        cols = tk.Frame(root, bg=p["bg"])
        cols.pack(fill="both", expand=True)
        left = tk.Frame(cols, bg=p["bg"])
        left.pack(side="left", fill="both", expand=True, padx=(0, 26))
        right = tk.Frame(cols, bg=p["bg"])
        right.pack(side="left", fill="both", expand=True)

        self.h2(left, "DPI stages")
        self.dpi_box = tk.Frame(left, bg=p["bg"])
        self.dpi_box.pack(fill="x")
        row = tk.Frame(left, bg=p["bg"])
        row.pack(fill="x", pady=(8, 0))
        Flat(row, p, "Add stage", self.add_stage).pack(side="left")
        Flat(row, p, "Remove stage", self.del_stage).pack(side="left", padx=6)
        self.dpi_hint = tk.Label(row, text="", font=SANS_S, bg=p["bg"], fg=p["dim"])
        self.dpi_hint.pack(side="left", padx=10)

        self.h2(left, "Profile")
        prow = tk.Frame(left, bg=p["bg"])
        prow.pack(fill="x")
        Flat(prow, p, "Push everything", self.push_all).pack(side="left")
        Flat(prow, p, "Export…", self.export).pack(side="left", padx=6)
        Flat(prow, p, "Import…", self.do_import).pack(side="left")
        Flat(prow, p, "Factory reset", self.factory).pack(side="left", padx=6)
        self.hint(left, "The mouse answers no reads, so settings are pushed to "
                        "it and never read back. Everything is sent once at "
                        "startup, which is what keeps the app and the device "
                        "from drifting apart.")

        self.h2(right, "Polling rate")
        self.seg_poll = tk.Frame(right, bg=p["bg"])
        self.seg_poll.pack(anchor="w")

        self.h2(right, "Sensor")
        self.sensor_box = tk.Frame(right, bg=p["bg"])
        self.sensor_box.pack(fill="x")

        self.h2(right, "Power & response")
        self.power_box = tk.Frame(right, bg=p["bg"])
        self.power_box.pack(fill="x")

    def add_stage(self):
        st = self.snap["state"]
        if len(st["dpi"]) >= P.DPI_SLOTS:
            return
        self.patch({"dpi": list(st["dpi"]) + [st["dpi"][-1]]})

    def del_stage(self):
        st = self.snap["state"]
        if len(st["dpi"]) <= 1:
            return
        self.patch({"dpi": list(st["dpi"])[:-1]})

    def push_all(self):
        try:
            self.snap = SRV.push_all()
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Attack Shark X3", str(exc))

    def factory(self):
        if messagebox.askyesno("Factory reset",
                               "Reset every setting to the factory table?"):
            self.snap = SRV.reset_factory()
            self.refresh()

    def export(self):
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("Profile", "*.json")])
        if not path:
            return
        import json
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.snap["state"], fh, indent=2)

    def do_import(self):
        path = filedialog.askopenfilename(filetypes=[("Profile", "*.json")])
        if not path:
            return
        import json
        try:
            with open(path, encoding="utf-8") as fh:
                self.patch(json.load(fh))
        except Exception as exc:
            messagebox.showerror("Import", str(exc))

    # ------------------------------------------------------------ macros --
    def build_macros(self, root):
        p = self.pal
        self.h2(root, "Macros")
        er = tk.Frame(root, bg=p["bg"])
        er.pack(fill="x")
        self.engine_box = tk.Frame(er, bg=p["bg"])
        self.engine_box.pack(side="left")
        self.engine_hint = tk.Label(er, text="", font=SANS_S, bg=p["bg"],
                                    fg=p["dim"], wraplength=620, justify="left")
        self.engine_hint.pack(side="left", padx=12)

        self.mac_box = tk.Frame(root, bg=p["bg"])
        self.mac_box.pack(fill="x", pady=(10, 0))

        self.h2(root, "Button bindings")
        self.bind_box = tk.Frame(root, bg=p["bg"])
        self.bind_box.pack(fill="x")
        self.hint(root, "Key-only macros are stored in the mouse and play from "
                        "its own firmware - nothing has to be running. Anything "
                        "with movement runs from this app instead.")

    # ------------------------------------------------------------ themes --
    def build_themes(self, root):
        p = self.pal
        self.h2(root, "Theme")
        self.hint(root, "The mouse on the left is the preview.")
        grid = tk.Frame(root, bg=p["bg"])
        grid.pack(anchor="w", pady=(10, 0))
        for i, name in enumerate(TH.THEMES):
            pal = TH.palette(name)
            cell = tk.Frame(grid, bg=pal["line2"], highlightthickness=1,
                            highlightbackground=p["accent"] if name == self.theme_name
                            else p["line2"], cursor="hand2")
            cell.grid(row=i // 3, column=i % 3, padx=6, pady=6, sticky="w")
            strip = tk.Frame(cell, bg=pal["line2"])
            strip.pack(fill="x")
            for tok in ("bg", "panel", "accent", "fg"):
                tk.Frame(strip, bg=pal[tok], width=34, height=30).pack(side="left")
            lab = tk.Label(cell, text=name, font=MONO, bg=pal["surface"],
                           fg=pal["fg"] if name == self.theme_name else pal["dim"],
                           anchor="w", padx=8, pady=5, width=15)
            lab.pack(fill="x")
            for w in (cell, strip, lab):
                w.bind("<Button-1>", lambda _e, n=name: self.set_theme(n))

    def set_theme(self, name):
        self.theme_name = name
        self.pal = TH.palette(name)
        self.configure(bg=self.pal["bg"])
        self.build()          # every widget carries palette colours; rebuild
        self.refresh()

    # ----------------------------------------------------------- refresh --
    def refresh(self):
        if self.snap is None:
            try:
                self.snap = SRV._snapshot(AttackSharkX3().state)
            except Exception as exc:
                messagebox.showerror("Attack Shark X3", str(exc))
                return
        p = self.pal
        snap, st, cat = self.snap, self.snap["state"], self.snap["catalog"]

        dev = snap["device"]
        self.link_dot.delete("all")
        self.link_dot.create_rectangle(
            0, 0, 8, 8, outline="",
            fill=p["ok"] if dev.get("connected") else p["accent"])
        self.link_lab.config(
            text=dev.get("product", "not connected") if dev.get("connected")
            else "not connected")

        self.render_battery(snap.get("battery"))
        self.render_buttons(snap)
        self.render_dpi(st, cat)

        for w in self.seg_poll.winfo_children():
            w.destroy()
        Segment(self.seg_poll, p,
                [(f"{r} Hz", r) for r in cat["polling_rates"]],
                st["polling_hz"], lambda v: self.patch({"polling_hz": v})
                ).pack(anchor="w")

        for w in self.sensor_box.winfo_children():
            w.destroy()
        fields = [("Lift-off distance", "lod_mm", [("1 mm", 1), ("2 mm", 2)], None),
                  ("Motion sync", "motion_sync", [("off", False), ("on", True)], True),
                  ("Ripple control", "ripple", [("off", False), ("on", True)], True),
                  ("Angle snap", "angle_snap", [("off", False), ("on", True)], True)]
        for i, (label, key, opts, hot) in enumerate(fields):
            cell = tk.Frame(self.sensor_box, bg=p["bg"])
            cell.grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 26), pady=6)
            tk.Label(cell, text=label, font=SANS_S, bg=p["bg"], fg=p["dim"]
                     ).pack(anchor="w", pady=(0, 4))
            Segment(cell, p, opts, st[key],
                    lambda v, k=key: self.patch({k: v}),
                    (lambda v: v is True) if hot else (lambda v: False)
                    ).pack(anchor="w")

        for w in self.power_box.winfo_children():
            w.destroy()
        for key, lo, hi, step, fmt in (
                ("sleep_min", 0.5, 30, 0.5, lambda v: f"{v:.1f} min"),
                ("deep_sleep_min", 1, 60, 1, lambda v: f"{int(v)} min"),
                ("key_response_ms", 2, 50, 2, lambda v: f"{int(v)} ms")):
            self.slider(self.power_box, key, lo, hi, step, st[key], fmt)

        self.render_macros(snap)

    def slider(self, parent, key, lo, hi, step, value, fmt):
        p = self.pal
        row = tk.Frame(parent, bg=p["bg"])
        row.pack(fill="x", pady=5)
        tk.Label(row, text=key.replace("_", " ").capitalize(), font=SANS_S,
                 bg=p["bg"], fg=p["dim"], width=15, anchor="w").pack(side="left")
        out = tk.Label(row, text=fmt(value), font=MONO, bg=p["bg"], fg=p["fg"],
                       width=8, anchor="e")
        out.pack(side="right")
        sc = tk.Scale(row, from_=lo, to=hi, resolution=step, orient="horizontal",
                      showvalue=False, bg=p["bg"], fg=p["fg"], troughcolor=p["line2"],
                      activebackground=p["accent"], highlightthickness=0, bd=0,
                      sliderrelief="flat", length=180, width=10,
                      command=lambda v: out.config(text=fmt(float(v))))
        sc.set(value)
        sc.pack(side="left", fill="x", expand=True, padx=8)
        # only write when the drag ends: a Scale fires per pixel, and each
        # write is a HID transaction
        sc.bind("<ButtonRelease-1>", lambda _e: self.patch({key: sc.get()}))

    def render_battery(self, b):
        p = self.pal
        if not b:
            self.meter.show(0, p["dim"])
            self.batt_lab.config(text="—", fg=p["dim"])
            return
        if b.get("charging"):
            self.meter.show(100, p["dim"])
            self.batt_lab.config(text="chg", fg=p["dim"])
            return
        pct = b.get("percent")
        if pct is None:
            self.meter.show(0, p["dim"])
            self.batt_lab.config(text="—", fg=p["dim"])
            return
        colour = p["accent"] if pct <= 20 else p["ok"]
        self.meter.show(pct, colour)
        self.batt_lab.config(text=f"{pct}%", fg=colour)

    def render_buttons(self, snap):
        p = self.pal
        for w in self.btnlist.winfo_children():
            w.destroy()
        for n in snap["catalog"]["buttons"]:
            row = tk.Frame(self.btnlist, bg=p["bg"], cursor="hand2")
            row.pack(fill="x")
            tk.Frame(row, height=1, bg=p["line"]).pack(fill="x")
            inner = tk.Frame(row, bg=p["bg"])
            inner.pack(fill="x", pady=5)
            tk.Label(inner, text=str(n), font=MONO, bg=p["bg"], fg=p["dim"],
                     width=2, highlightthickness=1,
                     highlightbackground=p["line2"]).pack(side="left")
            tk.Label(inner, text=snap["button_labels"].get(str(n), "—"),
                     font=MONO, bg=p["bg"], fg=p["fg"]).pack(side="left", padx=8)
            for w in (row, inner):
                w.bind("<Button-1>", lambda _e, b=n: self.assign(b))
                w.bind("<Enter>", lambda _e, b=n: self.hover(b, True))
                w.bind("<Leave>", lambda _e, b=n: self.hover(b, False))

    def render_dpi(self, st, cat):
        p = self.pal
        for w in self.dpi_box.winfo_children():
            w.destroy()
        for i, dpi in enumerate(st["dpi"]):
            row = tk.Frame(self.dpi_box, bg=p["bg"])
            row.pack(fill="x", pady=2)
            tk.Label(row, text=str(i + 1), font=MONO, bg=p["bg"], fg=p["dim"],
                     width=2).pack(side="left")
            ent = tk.Entry(row, font=MONO, width=7, bg=p["input_bg"], fg=p["fg"],
                           relief="flat", justify="right",
                           highlightthickness=1, highlightbackground=p["line2"],
                           insertbackground=p["fg"])
            ent.insert(0, str(dpi))
            ent.pack(side="left", padx=6)
            ent.bind("<Return>", lambda _e, k=i, e=ent: self.set_dpi(k, e.get()))
            ent.bind("<FocusOut>", lambda _e, k=i, e=ent: self.set_dpi(k, e.get()))

            sw = tk.Frame(row, bg="#%02x%02x%02x" % tuple(st["colors"][i]),
                          width=26, height=16, highlightthickness=1,
                          highlightbackground=p["line2"])
            sw.pack(side="left", padx=6)

            act = tk.Label(row, text="active" if st["active_stage"] == i else "",
                           font=SANS_S, bg=p["bg"],
                           fg=p["accent"] if st["active_stage"] == i else p["dim"],
                           width=7, cursor="hand2")
            act.pack(side="left")
            act.bind("<Button-1>", lambda _e, k=i: self.patch({"active_stage": k}))
        self.dpi_hint.config(
            text=f"{len(st['dpi'])} of {cat['dpi']['slots']} stages · "
                 f"{cat['dpi']['min']}–{cat['dpi']['max']} in steps of {cat['dpi']['step']}")

    def set_dpi(self, i, raw):
        try:
            value = int(float(raw))
        except ValueError:
            return
        step = self.snap["catalog"]["dpi"]["step"]
        value = max(self.snap["catalog"]["dpi"]["min"],
                    min(self.snap["catalog"]["dpi"]["max"],
                        round(value / step) * step))
        if self.snap["state"]["dpi"][i] == value:
            return
        dpi = list(self.snap["state"]["dpi"])
        dpi[i] = value
        self.patch({"dpi": dpi})

    def render_macros(self, snap):
        p = self.pal
        eng = snap.get("engine") or {}
        for w in self.engine_box.winfo_children():
            w.destroy()
        Segment(self.engine_box, p, [("off", False), ("on", True)],
                bool(eng.get("active")),
                lambda v: self.macro_call(SRV.macro_engine, {"on": v}),
                lambda v: v is True).pack()
        backend = eng.get("backend")
        self.engine_hint.config(
            text=("host macros only fire while this is on" if not eng.get("active")
                  else ("asxfilter: movement comes from the mouse stack"
                        if backend == "kernel" else
                        "SendInput fallback: output is flagged injected")))

        for w in self.mac_box.winfo_children():
            w.destroy()
        macros = snap.get("macros") or []
        if not macros:
            tk.Label(self.mac_box, text="no macros yet", font=SANS_S, bg=p["bg"],
                     fg=p["dim"]).pack(anchor="w")
        for m in macros:
            row = tk.Frame(self.mac_box, bg=p["bg"])
            row.pack(fill="x", pady=1)
            tk.Frame(row, height=1, bg=p["line"]).pack(fill="x")
            inner = tk.Frame(row, bg=p["bg"])
            inner.pack(fill="x", pady=5)
            tk.Label(inner, text=m["name"], font=SANS, bg=p["bg"], fg=p["fg"],
                     width=22, anchor="w").pack(side="left")
            tag = tk.Label(inner, text=m["target"], font=MONO, bg=p["bg"],
                           fg=p["ok"] if m["target"] == "device" else p["dim"],
                           highlightthickness=1,
                           highlightbackground=p["ok"] if m["target"] == "device"
                           else p["line2"], padx=5)
            tag.pack(side="left", padx=(0, 10))
            tk.Label(inner, text=f"{m['steps']} steps · {m['duration_ms']}ms",
                     font=MONO, bg=p["bg"], fg=p["dim"]).pack(side="left")
            Flat(inner, p, "Delete",
                 lambda i=m["id"], n=m["name"]: self.del_macro(i, n)
                 ).pack(side="right", padx=3)
            fl = Flat(inner, p, "Flash",
                      lambda i=m["id"]: self.macro_call(SRV.macro_flash, {"id": i}))
            if not m["device_ok"]:
                fl.config(state="disabled")
            fl.pack(side="right", padx=3)
            Flat(inner, p, "Test",
                 lambda i=m["id"]: self.macro_call(SRV.macro_test, {"id": i})
                 ).pack(side="right", padx=3)

        for w in self.bind_box.winfo_children():
            w.destroy()
        binds = snap["state"].get("macro_bindings") or {}
        names = [("— none —", "")] + [(m["name"], m["id"]) for m in macros]
        for btn in snap["catalog"]["macro"]["buttons"]:
            row = tk.Frame(self.bind_box, bg=p["bg"])
            row.pack(fill="x", pady=3)
            tk.Label(row, text=str(btn), font=MONO, bg=p["bg"], fg=p["dim"],
                     width=3).pack(side="left")
            cur = (binds.get(str(btn)) or {}).get("id", "")
            var = tk.StringVar(value=next((n for n, i in names if i == cur),
                                          "— none —"))
            opt = tk.OptionMenu(row, var, *[n for n, _ in names],
                                command=lambda _v, b=btn, v=var, nm=names:
                                self.bind_macro(b, dict((a, c) for a, c in nm)[v.get()]))
            opt.config(font=MONO, bg=p["seg_bg"], fg=p["fg"], relief="flat",
                       highlightthickness=1, highlightbackground=p["line2"],
                       activebackground=p["surface_hi"], width=20, anchor="w")
            opt["menu"].config(bg=p["panel"], fg=p["fg"], font=MONO)
            opt.pack(side="left")

    def bind_macro(self, button, macro_id):
        self.macro_call(SRV.macro_bind,
                        {"button": button, "id": macro_id or None,
                         "passthrough": False})

    def del_macro(self, macro_id, name):
        if messagebox.askyesno("Delete macro", f'Delete "{name}"?'):
            self.macro_call(SRV.macro_delete, {"id": macro_id})

    def macro_call(self, fn, payload):
        try:
            result = fn(payload)
            if isinstance(result, dict) and "state" in result:
                self.snap = result
            self.refresh()
        except Exception as exc:
            messagebox.showerror("Attack Shark X3", str(exc))

    # ------------------------------------------------------------ buttons --
    def hover(self, n, on):
        self.map.highlight(n, on)

    def assign(self, n):
        AssignDialog(self, n)

    # -------------------------------------------------------------- loops --
    def _battery_loop(self):
        while True:
            try:
                st = AttackSharkX3().read_status(timeout=8.0)
                if st and st.get("kind") == "battery":
                    SRV._battery_cache["value"] = st
                    import time as _t
                    SRV._battery_cache["at"] = _t.time()
            except Exception:
                pass
            import time as _t
            _t.sleep(25)

    def _tick(self):
        try:
            fresh = SRV._snapshot(self.snap["state"]) if self.snap else None
            if fresh:
                before = self.snap.get("battery") or {}
                after = fresh.get("battery") or {}
                if (before.get("raw") != after.get("raw")
                        or before.get("percent") != after.get("percent")
                        or self.snap["device"].get("connected")
                        != fresh["device"].get("connected")):
                    self.snap = fresh
                    self.render_battery(fresh.get("battery"))
                    dev = fresh["device"]
                    self.link_dot.delete("all")
                    self.link_dot.create_rectangle(
                        0, 0, 8, 8, outline="",
                        fill=self.pal["ok"] if dev.get("connected")
                        else self.pal["accent"])
                    self.link_lab.config(
                        text=dev.get("product") if dev.get("connected")
                        else "not connected")
        except Exception:
            pass
        self.after(2500, self._tick)


class AssignDialog(tk.Toplevel):
    """Pick a preset action for one button."""

    def __init__(self, app, button):
        super().__init__(app)
        p = app.pal
        self.app, self.button = app, button
        self.title(f"Button {button}")
        self.configure(bg=p["panel"])
        self.resizable(False, False)
        self.transient(app)
        self.grab_set()

        tk.Label(self, text=f"BUTTON {button}", font=SANS_S, bg=p["panel"],
                 fg=p["dim"]).pack(anchor="w", padx=16, pady=(14, 8))

        grid = tk.Frame(self, bg=p["panel"])
        grid.pack(padx=16)
        actions = app.snap["catalog"]["actions"]
        for i, name in enumerate(actions):
            b = Flat(grid, p, name, lambda a=name: self.pick(a))
            b.config(font=MONO, padx=8, pady=4, width=15)
            b.grid(row=i // 3, column=i % 3, padx=3, pady=3, sticky="ew")

        row = tk.Frame(self, bg=p["panel"])
        row.pack(fill="x", padx=16, pady=14)
        Flat(row, p, "Cancel", self.destroy).pack(side="right")

    def pick(self, action):
        self.app.patch({"button_set": {str(self.button): action}})
        self.destroy()


def main():
    if not AttackSharkX3.discover():
        # still open: the app is useful for editing a profile with the mouse
        # asleep, and the push at startup will simply have nothing to talk to
        print("no Attack Shark X3 detected - opening anyway")
    try:
        SRV.resync_device()
    except Exception:
        pass
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

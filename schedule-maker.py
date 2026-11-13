"""
Schedule Maker  (Python + Tkinter + Pillow)

Install:  pip install pillow
Run:      python schedule_maker.py

How to use
- Type a name -> "Add Name". Drag names up/down to reorder. Right-click a name to delete.
- Type a time (e.g. 8:00AM-5:00PM) -> "Add Time Box". Type anything -> "Add Text Box".
  Boxes appear in the top tray. Drag a box from the tray into any cell (it is copied,
  so you can reuse it). Drag boxes between cells to move them. Right-click a box to delete.
- "Generate" makes the final image: copy it or save it as PNG.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, colorchooser
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageFont, ImageTk

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
NAME_W, DAY_W, HEADER_H = 160, 110, 40          # minimum widths (columns grow to fit)
REM_H = 110   # remarks box height
CHIP_H, CHIP_GAP, MIN_ROW, TRAY_MIN, PAD, MARGIN = 26, 4, 56, 100, 28, 10
COLORS = {"time": ("#cfe4ff", "#2b6cb0"), "text": ("#d6f5d6", "#2f855a")}
SAVE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule_data.json")


def fit(text, max_w, measure):
    """Trim text with an ellipsis until it fits max_w."""
    if measure(text) <= max_w:
        return text
    while len(text) > 1 and measure(text + "…") > max_w:
        text = text[:-1]
    return text + "…"


def chip_colors(c):
    """(background, border, text) for a box; custom color gets auto black/white text."""
    bg, fg = COLORS[c["kind"]]
    col = c.get("color")
    if not col:
        return bg, fg, "#1a202c"
    r, g, b = (int(col[i:i + 2], 16) for i in (1, 3, 5))
    text = "#1a202c" if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else "white"
    return col, "#2d3748", text


def known_tag(t):
    return t in ("b", "i", "u") or t.startswith(("sz_", "fg_"))


def inside(r, x, y):
    return r[0] <= x <= r[2] and r[1] <= y <= r[3]


class App:
    def __init__(self, root):
        self.root = root
        root.title("Schedule Maker")
        self.employees = []   # {"id": int, "name": str}
        self.cells = {}       # (emp_id, day) -> [{"kind","text"}]
        self.tray = []        # [{"kind","text"}]
        self.next_id = 1
        self.drag = None
        self.load()

        self.f = tkfont.Font(family="Arial", size=-13)
        self.fb = tkfont.Font(family="Arial", size=-13, weight="bold")

        bar = ttk.Frame(root, padding=6)
        bar.pack(fill="x")
        self.name_var, self.chip_var = tk.StringVar(), tk.StringVar()
        self.title_var = tk.StringVar(value="WEEKLY SCHEDULE")
        self.t = {k: tk.StringVar(value=v) for k, v in
                  dict(h1="8", m1="00", a1="AM", h2="5", m2="00", a2="PM").items()}
        digits = (root.register(lambda s: s == "" or (s.isdigit() and len(s) <= 2)), "%P")

        def num(parent, var, lo, hi, fmt):
            return ttk.Spinbox(parent, from_=lo, to=hi, width=3, wrap=True, textvariable=var,
                               format=fmt, validate="key", validatecommand=digits)

        def ampm(parent, var):
            return ttk.Combobox(parent, textvariable=var, values=["AM", "PM"], width=4, state="readonly")

        # row 1: names + title + generate
        r1 = ttk.Frame(bar)
        r1.pack(fill="x", pady=2)
        ttk.Label(r1, text="Name:").pack(side="left")
        e1 = ttk.Entry(r1, textvariable=self.name_var, width=24)
        e1.pack(side="left", padx=4)
        e1.bind("<Return>", lambda e: self.add_name())
        ttk.Button(r1, text="Add Name", command=self.add_name).pack(side="left")
        ttk.Label(r1, text="     Title:").pack(side="left")
        ttk.Entry(r1, textvariable=self.title_var, width=26).pack(side="left", padx=4)
        ttk.Button(r1, text="Generate", command=self.generate).pack(side="left", padx=12)

        # row 2: time box
        r2 = ttk.Frame(bar)
        r2.pack(fill="x", pady=2)
        ttk.Label(r2, text="Time:  from").pack(side="left")
        num(r2, self.t["h1"], 1, 12, "%.0f").pack(side="left", padx=(4, 0))
        ttk.Label(r2, text=":").pack(side="left")
        num(r2, self.t["m1"], 0, 59, "%02.0f").pack(side="left")
        ampm(r2, self.t["a1"]).pack(side="left", padx=4)
        ttk.Label(r2, text="to").pack(side="left")
        num(r2, self.t["h2"], 1, 12, "%.0f").pack(side="left", padx=(4, 0))
        ttk.Label(r2, text=":").pack(side="left")
        num(r2, self.t["m2"], 0, 59, "%02.0f").pack(side="left")
        ampm(r2, self.t["a2"]).pack(side="left", padx=4)
        ttk.Button(r2, text="Add Time Box", command=self.add_time).pack(side="left", padx=8)

        # row 3: text box
        r3 = ttk.Frame(bar)
        r3.pack(fill="x", pady=2)
        ttk.Label(r3, text="Text:").pack(side="left")
        e2 = ttk.Entry(r3, textvariable=self.chip_var, width=40)
        e2.pack(side="left", padx=4)
        e2.bind("<Return>", lambda e: self.add_text())
        ttk.Button(r3, text="Add Text Box", command=self.add_text).pack(side="left")
        ttk.Label(r3, text="     Box color:").pack(side="left")
        self.color = None
        self.swatch = tk.Label(r3, text="default", width=8, relief="solid", bd=1, bg="#e2e8f0", cursor="hand2")
        self.swatch.pack(side="left", padx=4)
        self.swatch.bind("<Button-1>", lambda e: self.pick_color())
        ttk.Button(r3, text="Reset", command=lambda: self.set_color(None)).pack(side="left")
        ttk.Label(r3, text="  (applies to new boxes · double-click a box to recolor it)").pack(side="left")

        frame = ttk.Frame(root)
        frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(frame, bg="white", highlightthickness=0, width=1150, height=650)
        vs = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self._rfonts = {}
        self.remarks_box = tk.Text(self.canvas, wrap="word", font=self.f, relief="solid", bd=1, undo=True)
        for text, tags in getattr(self, "saved_remarks", []):
            for t in tags:
                self.ensure_tag(t)
            self.remarks_box.insert("end", text, tuple(tags))
        self.refresh_fonts()
        self.remarks_box.bind("<KeyRelease>", lambda e: (self.refresh_fonts(), self.save()))

        bar2 = self.rem_bar = ttk.Frame(self.canvas)
        for label, tag, fnt in (("B", "b", ("Arial", 10, "bold")), ("I", "i", ("Arial", 10, "italic")),
                                ("U", "u", ("Arial", 10, "underline"))):
            tk.Button(bar2, text=label, width=3, font=fnt,
                      command=lambda t=tag: self.toggle_style(t)).pack(side="left", padx=1)
        self.size_var = tk.StringVar(value="13")
        cb = ttk.Combobox(bar2, textvariable=self.size_var, values=["10", "13", "16", "20", "26", "32"],
                          width=4, state="readonly")
        cb.pack(side="left", padx=(8, 1))
        cb.bind("<<ComboboxSelected>>",
                lambda e: self.set_exclusive("sz_", None if self.size_var.get() == "13" else "sz_" + self.size_var.get()))
        ttk.Button(bar2, text="Text color", command=self.pick_remark_color).pack(side="left", padx=4)
        ttk.Button(bar2, text="Clear format", command=self.clear_format).pack(side="left")
        ttk.Label(bar2, text="  select text first").pack(side="left")

        sg = getattr(self, "saved_sig", {})
        self.sig_show = tk.BooleanVar(value=sg.get("show", True))
        self.sig_name = tk.StringVar(value=sg.get("name", ""))
        self.sig_title = tk.StringVar(value=sg.get("title", ""))
        self.sig_align = tk.StringVar(value=sg.get("align", "Right"))
        sb = self.sig_bar = ttk.Frame(self.canvas)
        ttk.Checkbutton(sb, text="Show signature area", variable=self.sig_show).pack(side="left")
        ttk.Label(sb, text="   Name:").pack(side="left")
        ttk.Entry(sb, textvariable=self.sig_name, width=26).pack(side="left", padx=4)
        ttk.Label(sb, text="Title (optional):").pack(side="left")
        ttk.Entry(sb, textvariable=self.sig_title, width=26).pack(side="left", padx=4)
        ttk.Label(sb, text="Position:").pack(side="left")
        ttk.Combobox(sb, textvariable=self.sig_align, values=["Left", "Center", "Right"],
                     width=7, state="readonly").pack(side="left", padx=4)
        for v in (self.sig_show, self.sig_name, self.sig_title, self.sig_align):
            v.trace_add("write", lambda *a: self.save())

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_motion)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Double-Button-1>", self.on_double)
        for b in ("<Button-3>", "<Button-2>"):
            self.canvas.bind(b, self.on_right_click)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 * (e.delta // 120 or (1 if e.delta > 0 else -1)), "units"))

        self.redraw()

    # ---------- data ----------
    def save(self):
        data = {
            "employees": self.employees, "tray": self.tray, "next_id": self.next_id,
            "cells": [[k[0], k[1], v] for k, v in self.cells.items()],
            "remarks": [[t, tags] for t, tags, _ in self.get_runs()],
            "signature": self.sig_data(),
        }
        try:
            with open(SAVE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
        except OSError:
            pass

    def sig_data(self):
        if hasattr(self, "sig_show"):
            return {"show": self.sig_show.get(), "name": self.sig_name.get(),
                    "title": self.sig_title.get(), "align": self.sig_align.get()}
        return getattr(self, "saved_sig", {"show": True, "name": "", "title": "", "align": "Right"})

    def get_runs(self):
        """Remarks as [(text, [format tags], start_index)]."""
        box = getattr(self, "remarks_box", None)
        if not box:
            return [(t, g, None) for t, g in getattr(self, "saved_remarks", [])]
        active, runs = set(), []
        for key, val, idx in box.dump("1.0", "end-1c", tag=True, text=True):
            if key == "tagon":
                active.add(val)
            elif key == "tagoff":
                active.discard(val)
            elif key == "text":
                runs.append((val, sorted(t for t in active if known_tag(t)), idx))
        return runs

    def ensure_tag(self, tag):
        box = self.remarks_box
        if tag == "u":
            box.tag_configure("u", underline=True)
        elif tag.startswith("fg_"):
            box.tag_configure(tag, foreground=tag[3:])

    def rfont(self, b, i, size):
        key = (b, i, size)
        if key not in self._rfonts:
            self._rfonts[key] = tkfont.Font(family="Arial", size=-size, weight="bold" if b else "normal",
                                            slant="italic" if i else "roman")
        return self._rfonts[key]

    def refresh_fonts(self):
        """Bold/italic/size can't be combined by plain tags, so apply one combined font tag per run."""
        box = self.remarks_box
        for t in box.tag_names():
            if t.startswith("vf_"):
                box.tag_remove(t, "1.0", "end")
        for text, tags, idx in self.get_runs():
            size = next((int(t[3:]) for t in tags if t.startswith("sz_")), 13)
            b, i = "b" in tags, "i" in tags
            name = f"vf_{int(b)}{int(i)}_{size}"
            box.tag_configure(name, font=self.rfont(b, i, size))
            box.tag_add(name, idx, f"{idx}+{len(text)}c")
        box.tag_raise("sel")

    def selection(self):
        try:
            return self.remarks_box.index("sel.first"), self.remarks_box.index("sel.last")
        except tk.TclError:
            messagebox.showinfo("Schedule Maker", "Select some remarks text first.")
            return None

    def toggle_style(self, tag):
        sel = self.selection()
        if not sel:
            return
        box, (a, b) = self.remarks_box, sel
        n = len(box.get(a, b))
        all_on = all(tag in box.tag_names(f"{a}+{k}c") for k in range(n))
        self.ensure_tag(tag)
        (box.tag_remove if all_on else box.tag_add)(tag, a, b)
        self.after_format()

    def set_exclusive(self, prefix, tag):
        sel = self.selection()
        if not sel:
            return
        box, (a, b) = self.remarks_box, sel
        for t in box.tag_names():
            if t.startswith(prefix):
                box.tag_remove(t, a, b)
        if tag:
            self.ensure_tag(tag)
            box.tag_add(tag, a, b)
        self.after_format()

    def pick_remark_color(self):
        if not self.selection():
            return
        col = colorchooser.askcolor(title="Text color")[1]
        if col:
            self.set_exclusive("fg_", "fg_" + col)

    def clear_format(self):
        sel = self.selection()
        if not sel:
            return
        for t in self.remarks_box.tag_names():
            if known_tag(t):
                self.remarks_box.tag_remove(t, *sel)
        self.after_format()

    def after_format(self):
        self.refresh_fonts()
        self.save()

    def load(self):
        try:
            with open(SAVE_FILE, encoding="utf-8") as f:
                d = json.load(f)
            self.employees, self.tray, self.next_id = d["employees"], d["tray"], d["next_id"]
            self.cells = {(a, b): v for a, b, v in d["cells"]}
            self.saved_sig = d.get("signature", {})
            rem = d.get("remarks", [])
            self.saved_remarks = [[rem, []]] if isinstance(rem, str) else rem
        except (OSError, ValueError, KeyError):
            pass

    def add_name(self):
        n = self.name_var.get().strip()
        if not n:
            return
        self.employees.append({"id": self.next_id, "name": n})
        self.next_id += 1
        self.name_var.set("")
        self.redraw()

    def set_color(self, col):
        self.color = col
        self.swatch.config(bg=col or "#e2e8f0", text="" if col else "default")

    def pick_color(self):
        col = colorchooser.askcolor(initialcolor=self.color or "#cfe4ff", title="Box color")[1]
        if col:
            self.set_color(col)

    def new_chip(self, kind, text):
        c = {"kind": kind, "text": text}
        if self.color:
            c["color"] = self.color
        return c

    def on_double(self, e):
        x, y = self.cxy(e)
        target = None
        for r in self.L["tray"]:
            if inside(r, x, y):
                target = self.tray[r[4]]
        for r in self.L["chips"]:
            if inside(r, x, y):
                target = self.cells[(r[4], r[5])][r[6]]
        if target:
            self.drag = None
            col = colorchooser.askcolor(initialcolor=chip_colors(target)[0], title="Box color")[1]
            if col:
                target["color"] = col
                self.redraw()

    def add_text(self):
        t = self.chip_var.get().strip()
        if not t:
            return
        self.tray.append(self.new_chip("text", t))
        self.chip_var.set("")
        self.redraw()

    def add_time(self):
        try:
            h1, m1, h2, m2 = (int(self.t[k].get()) for k in ("h1", "m1", "h2", "m2"))
            if not (1 <= h1 <= 12 and 1 <= h2 <= 12 and 0 <= m1 <= 59 and 0 <= m2 <= 59):
                raise ValueError
        except ValueError:
            messagebox.showwarning("Schedule Maker", "Hours must be 1-12 and minutes 0-59.")
            return
        text = f"{h1}:{m1:02d}{self.t['a1'].get()} - {h2}:{m2:02d}{self.t['a2'].get()}"
        self.tray.append(self.new_chip("time", text))
        self.redraw()

    # ---------- layout ----------
    def layout(self, m=None, mb=None):
        """Columns grow to fit the longest text. m/mb = text measure functions."""
        m = m or self.f.measure
        mb = mb or self.fb.measure
        L = {"tray": [], "names": [], "cells": [], "chips": []}
        name_w = max([NAME_W] + [mb(e["name"]) + PAD for e in self.employees])
        day_w = []
        for d in range(7):
            w = [m(c["text"]) + PAD for e in self.employees for c in self.cells.get((e["id"], d), [])]
            day_w.append(max([DAY_W, mb(DAYS[d]) + PAD] + w))
        col_x = [MARGIN + name_w]
        for w in day_w:
            col_x.append(col_x[-1] + w)
        right = col_x[-1]
        L.update(name_w=name_w, day_w=day_w, col_x=col_x, right=right)

        x, y = MARGIN, MARGIN + 22
        for i, c in enumerate(self.tray):
            w = max(TRAY_MIN, m(c["text"]) + PAD)
            if x + w > right and x > MARGIN:
                x, y = MARGIN, y + CHIP_H + 6
            L["tray"].append((x, y, x + w, y + CHIP_H, i))
            x += w + 6
        top = y + CHIP_H + 26
        L["top"] = top
        y = top + HEADER_H
        for emp in self.employees:
            n = max([len(self.cells.get((emp["id"], d), [])) for d in range(7)] + [0])
            h = max(MIN_ROW, 8 + n * (CHIP_H + CHIP_GAP))
            L["names"].append((MARGIN, y, MARGIN + name_w, y + h, emp["id"]))
            for d in range(7):
                x1, x2 = col_x[d], col_x[d + 1]
                L["cells"].append((x1, y, x2, y + h, emp["id"], d))
                for k, _ in enumerate(self.cells.get((emp["id"], d), [])):
                    cy = y + 6 + k * (CHIP_H + CHIP_GAP)
                    L["chips"].append((x1 + 6, cy, x2 - 6, cy + CHIP_H, emp["id"], d, k))
            y += h
        L["bottom"] = y
        L["rem"] = (MARGIN, y + 62, right, y + 62 + REM_H)
        L["sig_y"] = y + 62 + REM_H + 24
        return L

    # ---------- drawing ----------
    def chip(self, r, c, tag=None):
        bg, fg, tx = chip_colors(c)
        self.canvas.create_rectangle(r[0], r[1], r[2], r[3], fill=bg, outline=fg, tags=tag)
        self.canvas.create_text((r[0] + r[2]) / 2, (r[1] + r[3]) / 2,
                                text=fit(c["text"], r[2] - r[0] - 8, self.f.measure),
                                font=self.f, fill=tx, tags=tag)

    def redraw(self):
        c = self.canvas
        c.delete("all")
        L = self.L = self.layout()
        c.create_text(MARGIN, MARGIN + 8, anchor="w", font=self.fb, fill="#555",
                      text="BOXES  (drag into a cell · right-click to delete)")
        if not self.tray:
            c.create_text(MARGIN, MARGIN + 36, anchor="w", fill="#aaa", font=self.f,
                          text="No boxes yet — add a Time Box or Text Box above.")
        for x1, y1, x2, y2, i in L["tray"]:
            self.chip((x1, y1, x2, y2), self.tray[i])

        top, right = L["top"], L["right"]
        nw, cx = L["name_w"], L["col_x"]
        c.create_rectangle(MARGIN, top, MARGIN + nw, top + HEADER_H, fill="#2d3748", outline="#1a202c")
        c.create_text(MARGIN + nw / 2, top + HEADER_H / 2, text="NAME", font=self.fb, fill="white")
        for d, day in enumerate(DAYS):
            x1, x2 = cx[d], cx[d + 1]
            c.create_rectangle(x1, top, x2, top + HEADER_H, fill="#2d3748", outline="#1a202c")
            c.create_text((x1 + x2) / 2, top + HEADER_H / 2, text=day, font=self.fb, fill="white")
        emps = {e["id"]: e["name"] for e in self.employees}
        for x1, y1, x2, y2, eid in L["names"]:
            c.create_rectangle(x1, y1, x2, y2, fill="#edf2f7", outline="#1a202c")
            c.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=emps[eid], font=self.fb)
        for x1, y1, x2, y2, eid, d in L["cells"]:
            c.create_rectangle(x1, y1, x2, y2, fill="white", outline="#1a202c")
        for x1, y1, x2, y2, eid, d, k in L["chips"]:
            self.chip((x1, y1, x2, y2), self.cells[(eid, d)][k])
        if not self.employees:
            c.create_text(MARGIN, top + HEADER_H + 20, anchor="w", fill="#aaa", font=self.f,
                          text="Add a name above to start.")
        rx1, ry1, rx2, ry2 = L["rem"]
        c.create_text(rx1, ry1 - 46, anchor="w", font=self.fb, fill="#555", text="REMARKS")
        c.create_window(rx1, ry1 - 34, anchor="nw", window=self.rem_bar)
        c.create_window(rx1, ry1, anchor="nw", window=self.remarks_box, width=rx2 - rx1, height=REM_H)
        sy = L["sig_y"]
        c.create_text(rx1, sy, anchor="w", font=self.fb, fill="#555", text="SIGNATURE AREA")
        c.create_window(rx1, sy + 12, anchor="nw", window=self.sig_bar)
        c.configure(scrollregion=(0, 0, right + MARGIN, sy + 60))
        self.save()

    # ---------- mouse ----------
    def cxy(self, e):
        return self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)

    def on_press(self, e):
        x, y = self.cxy(e)
        L = self.L
        self.drag = None
        for r in L["tray"]:
            if inside(r, x, y):
                self.drag = {"type": "tray", "chip": dict(self.tray[r[4]])}
                return
        for r in L["chips"]:
            if inside(r, x, y):
                _, _, _, _, eid, d, k = r
                self.drag = {"type": "cell", "chip": self.cells[(eid, d)][k], "src": (eid, d, k)}
                return
        for r in L["names"]:
            if inside(r, x, y):
                self.drag = {"type": "name", "eid": r[4]}
                return

    def target_cell(self, x, y):
        for r in self.L["cells"]:
            if inside(r, x, y):
                return r
        return None

    def target_name(self, y):
        names = self.L["names"]
        if not names:
            return None
        if y < names[0][1]:
            return names[0]
        for r in names:
            if r[1] <= y <= r[3]:
                return r
        return names[-1]

    def on_motion(self, e):
        if not self.drag:
            return
        x, y = self.cxy(e)
        c = self.canvas
        c.delete("ghost")
        c.delete("hl")
        if self.drag["type"] == "name":
            name = next(n["name"] for n in self.employees if n["id"] == self.drag["eid"])
            c.create_rectangle(x - 90, y - 16, x + 90, y + 16, fill="#bee3f8", outline="#2b6cb0", tags="ghost")
            c.create_text(x, y, text=fit(name, 170, self.fb.measure), font=self.fb, tags="ghost")
            t = self.target_name(y)
            if t:
                c.create_rectangle(t[0], t[1], t[2], t[3], outline="#dd6b20", width=3, tags="hl")
        else:
            self.chip((x - 60, y - 13, x + 60, y + 13), self.drag["chip"], tag="ghost")
            t = self.target_cell(x, y)
            if t:
                c.create_rectangle(t[0], t[1], t[2], t[3], outline="#dd6b20", width=3, tags="hl")

    def on_release(self, e):
        if not self.drag:
            return
        x, y = self.cxy(e)
        self.canvas.delete("ghost")
        self.canvas.delete("hl")
        d = self.drag
        self.drag = None
        if d["type"] == "name":
            t = self.target_name(y)
            if t:
                ids = [n["id"] for n in self.employees]
                new_i = ids.index(t[4])
                emp = self.employees.pop(ids.index(d["eid"]))
                self.employees.insert(new_i, emp)
        else:
            t = self.target_cell(x, y)
            if t:
                key = (t[4], t[5])
                if d["type"] == "cell":
                    eid, day, k = d["src"]
                    if (eid, day) != key:
                        self.cells[(eid, day)].pop(k)
                        self.cells.setdefault(key, []).append(d["chip"])
                else:
                    self.cells.setdefault(key, []).append(dict(d["chip"]))
        self.redraw()

    def on_right_click(self, e):
        x, y = self.cxy(e)
        L = self.L
        for r in L["tray"]:
            if inside(r, x, y):
                self.tray.pop(r[4])
                return self.redraw()
        for r in L["chips"]:
            if inside(r, x, y):
                self.cells[(r[4], r[5])].pop(r[6])
                return self.redraw()
        for r in L["names"]:
            if inside(r, x, y):
                eid = r[4]
                self.employees = [n for n in self.employees if n["id"] != eid]
                for d in range(7):
                    self.cells.pop((eid, d), None)
                return self.redraw()

    # ---------- generate image ----------
    def pil_font(self, size, bold=False, italic=False):
        key = (size, bold, italic)
        cache = self.__dict__.setdefault("_pf", {})
        if key in cache:
            return cache[key]
        names = {(0, 0): ["arial.ttf", "DejaVuSans.ttf"], (1, 0): ["arialbd.ttf", "DejaVuSans-Bold.ttf"],
                 (0, 1): ["ariali.ttf", "DejaVuSans-Oblique.ttf"],
                 (1, 1): ["arialbi.ttf", "DejaVuSans-BoldOblique.ttf"]}[(int(bold), int(italic))]
        font = None
        for n in names:
            try:
                font = ImageFont.truetype(n, int(size))
                break
            except OSError:
                pass
        cache[key] = font or ImageFont.load_default()
        return cache[key]

    def build_image(self):
        S, TITLE_H = 2, 50
        m1, mb1 = self.pil_font(13), self.pil_font(13, True)
        L = self.layout(lambda s: m1.getlength(s), lambda s: mb1.getlength(s))
        dy = L["top"] - MARGIN - TITLE_H
        f, fb, ft = self.pil_font(13 * S), self.pil_font(13 * S, True), self.pil_font(20 * S, True)
        W = int((L["right"] + MARGIN) * S)

        # remarks: rich text, wrapped to the table width
        max_w = (L["right"] - MARGIN - 20) * S
        lines, cur, cur_w = [], [], 0
        has_text = False
        for text, tags, _ in self.get_runs():
            size = next((int(t[3:]) for t in tags if t.startswith("sz_")), 13)
            color = next((t[3:] for t in tags if t.startswith("fg_")), "#000000")
            style = ("b" in tags, "i" in tags, "u" in tags, size, color)
            for tok in re.findall(r"\n|[^\S\n]+|\S+", text):
                if tok == "\n":
                    lines.append(cur)
                    cur, cur_w = [], 0
                    continue
                fnt = self.pil_font(size * S, style[0], style[1])
                w = fnt.getlength(tok)
                if tok.strip():
                    has_text = True
                if cur and tok.strip() and cur_w + w > max_w:
                    lines.append(cur)
                    cur, cur_w = [], 0
                cur.append((tok, style, fnt, w))
                cur_w += w
        lines.append(cur)
        line_hs = [max([st[3] for _, st, _, _ in ln] + [13]) * 1.4 for ln in lines]
        remarks = has_text
        rem_h = max(50, sum(line_hs) + 16) if remarks else 0
        sg = self.sig_data()
        sig_on = bool(sg["show"])
        has_title = bool(sg["title"].strip())
        sig_h = (20 + 45 + 24 + (20 if has_title else 0) + 10) if sig_on else 0
        extra = ((30 + rem_h + 10) if remarks else 0) + sig_h
        H = int((L["bottom"] - dy + MARGIN + extra) * S)
        img = Image.new("RGB", (W, H), "white")
        dr = ImageDraw.Draw(img)

        def P(r):  # scale a rect (x1,y1,x2,y2)
            return [r[0] * S, (r[1] - dy) * S, r[2] * S, (r[3] - dy) * S]

        def center(r, text, font, fill="black"):
            p = P(r)
            text = fit(text, p[2] - p[0] - 12 * S, lambda s: font.getlength(s))
            dr.text(((p[0] + p[2]) / 2, (p[1] + p[3]) / 2), text, font=font, fill=fill, anchor="mm")

        dr.text((W / 2, (MARGIN + TITLE_H / 2) * S), self.title_var.get(), font=ft, fill="black", anchor="mm")
        top = L["top"]
        hdr = (MARGIN, top, MARGIN + L["name_w"], top + HEADER_H)
        dr.rectangle(P(hdr), fill="#2d3748", outline="black")
        center(hdr, "NAME", fb, "white")
        for d, day in enumerate(DAYS):
            r = (L["col_x"][d], top, L["col_x"][d + 1], top + HEADER_H)
            dr.rectangle(P(r), fill="#2d3748", outline="black")
            center(r, day, fb, "white")
        emps = {e["id"]: e["name"] for e in self.employees}
        for r in L["names"]:
            dr.rectangle(P(r[:4]), fill="#edf2f7", outline="black")
            center(r[:4], emps[r[4]], fb)
        for r in L["cells"]:
            dr.rectangle(P(r[:4]), fill="white", outline="black")
        for r in L["chips"]:
            ch = self.cells[(r[4], r[5])][r[6]]
            bg, fg, tx = chip_colors(ch)
            dr.rectangle(P(r[:4]), fill=bg, outline=fg)
            center(r[:4], ch["text"], f, tx)
        if remarks:
            ry = L["bottom"] + 12
            dr.text((MARGIN * S, (ry - dy) * S), "REMARKS", font=fb, fill="black")
            by = ry + 18
            dr.rectangle([MARGIN * S, (by - dy) * S, L["right"] * S, (by + rem_h - dy) * S],
                         fill="#fffbea", outline="black")
            y = by + 8
            for ln, lh in zip(lines, line_hs):
                x = (MARGIN + 10) * S
                base = (y - dy) * S + (lh / 1.4) * S * 1.1
                for tok, st, fnt, w in ln:
                    dr.text((x, base), tok, font=fnt, fill=st[4], anchor="ls")
                    if st[2] and tok.strip():
                        dr.line([x, base + 2 * S, x + w, base + 2 * S], fill=st[4], width=max(1, S))
                    x += w
                y += lh
        if sig_on:
            y0 = L["bottom"] + ((30 + rem_h) if remarks else 0) + 20 + 45      # signature line y
            name, title = sg["name"].strip(), sg["title"].strip()
            need = max([fb.getlength(name), f.getlength(title)] + [0]) / S + 24
            lw = max(240, need)
            right_edge = L["right"]
            if sg["align"] == "Left":
                x1 = MARGIN + 10
            elif sg["align"] == "Center":
                x1 = (right_edge + MARGIN - lw) / 2
            else:
                x1 = right_edge - 10 - lw
            cx = (x1 + lw / 2) * S
            yy = (y0 - dy) * S
            dr.line([x1 * S, yy, (x1 + lw) * S, yy], fill="black", width=max(2, S))
            if name:
                dr.text((cx, yy + 6 * S), name, font=fb, fill="black", anchor="mt")
            if title:
                dr.text((cx, yy + (6 + 20) * S), title, font=f, fill="#333333", anchor="mt")
        return img

    def generate(self):
        if not self.employees:
            messagebox.showinfo("Schedule Maker", "Add at least one name first.")
            return
        img = self.build_image()
        path = os.path.join(tempfile.gettempdir(), "schedule.png")
        img.save(path)

        win = tk.Toplevel(self.root)
        win.title("Generated Schedule")
        prev = img.copy()
        prev.thumbnail((1200, 800))
        tkimg = ImageTk.PhotoImage(prev)
        lbl = ttk.Label(win, image=tkimg)
        lbl.image = tkimg
        lbl.pack(padx=8, pady=8)
        row = ttk.Frame(win)
        row.pack(pady=(0, 8))
        ttk.Button(row, text="Copy Image", command=lambda: self.copy_image(path)).pack(side="left", padx=4)
        ttk.Button(row, text="Save PNG", command=lambda: self.save_png(img)).pack(side="left", padx=4)

    def save_png(self, img):
        p = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG", "*.png")],
                                         initialfile="schedule.png")
        if p:
            img.save(p)

    def copy_image(self, path):
        try:
            if sys.platform.startswith("win"):
                ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                      f"$i=[System.Drawing.Image]::FromFile('{path}'); "
                      "[System.Windows.Forms.Clipboard]::SetImage($i)")
                subprocess.run(["powershell", "-STA", "-NoProfile", "-Command", ps], check=True)
            elif sys.platform == "darwin":
                subprocess.run(["osascript", "-e",
                                f'set the clipboard to (read (POSIX file "{path}") as «class PNGf»)'], check=True)
            else:
                with open(path, "rb") as f:
                    subprocess.run(["xclip", "-selection", "clipboard", "-t", "image/png"], stdin=f, check=True)
            messagebox.showinfo("Schedule Maker", "Image copied. You can paste it now.")
        except Exception:
            messagebox.showerror("Schedule Maker", f"Could not copy automatically.\nImage saved at:\n{path}")


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
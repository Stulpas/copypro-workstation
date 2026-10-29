"""Shared dark-theme chrome for CopyPro Tools.

Rounded panels and buttons are drawn with Pillow so the existing Tkinter
layouts can stay in place. Callers should keep PhotoImage-bearing widgets
alive; each helper stores its images on the instance.
"""
import math
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageFont, ImageTk

# ── Palette ───────────────────────────────────────────────────────────────────
BG          = "#0F0F13"
SURFACE     = "#1A1A22"
SURFACE2    = "#23232E"
BORDER      = "#2E2E3D"
ACCENT      = "#5B6BFF"
ACCENT2     = "#8B5CF6"
SUCCESS     = "#22C55E"
WARNING     = "#F59E0B"
DANGER      = "#EF4444"
TEXT        = "#F0F0F8"
MUTED       = "#7878A0"
DROP_HL     = "#3B4BCC"
CARD        = "#1C1C26"
ACCENT_SOFT = "#2A2F55"
TAB_BAR     = "#16161E"
ROW_BG      = CARD

FONT      = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_UI   = ("Segoe UI", 8, "bold")

_BTN_COLORS = {
    "primary":   (ACCENT, TEXT, ACCENT2),
    "secondary": (SURFACE2, TEXT, "#2C2C3A"),
    "success":   (SUCCESS, "#000000", "#16A34A"),
    "danger":    (DANGER, TEXT, "#F87171"),
    "ghost":     (CARD, MUTED, SURFACE2),
}


def hex_rgb(color):
    value = (color or "#000000").lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def widget_bg(widget):
    try:
        color = widget.cget("bg")
        if color:
            return color
    except Exception:
        pass
    try:
        color = widget.cget("background")
        if color:
            return color
    except Exception:
        pass
    return BG


def rounded_image(width, height, radius, fill, outline=None, outline_width=1, backdrop=None):
    width = max(2, int(width))
    height = max(2, int(height))
    radius = max(1, min(int(radius), width // 2, height // 2))
    base = hex_rgb(backdrop or fill) + (255,)
    img = Image.new("RGBA", (width, height), base)
    draw = ImageDraw.Draw(img)
    box = (0, 0, width - 1, height - 1)
    fill_rgba = hex_rgb(fill) + (255,)
    if outline:
        draw.rounded_rectangle(box, radius=radius, fill=fill_rgba,
                               outline=hex_rgb(outline) + (255,), width=outline_width)
    else:
        draw.rounded_rectangle(box, radius=radius, fill=fill_rgba)
    return ImageTk.PhotoImage(img)


def measure_text(parent, text, font):
    try:
        face = tkfont.Font(root=parent, font=font)
        return max(face.measure(text or " "), 12), max(face.metrics("linespace"), 12)
    except Exception:
        return max(8 * len(text or " "), 12), 16


class RoundedFrame(tk.Frame):
    """Frame with a rounded card background. Pack children into ``inner``."""

    def __init__(self, parent, radius=12, fill=CARD, outline=BORDER, pad=10, **kw):
        parent_bg = kw.pop("bg", None) or widget_bg(parent)
        super().__init__(parent, bg=parent_bg, highlightthickness=0, bd=0, **kw)
        self._parent_bg = parent_bg
        self._radius = radius
        self._fill = fill
        self._outline = outline
        self._photo = None
        self._last_size = (0, 0)
        self._bg = tk.Label(self, bg=parent_bg, bd=0, highlightthickness=0)
        self._bg.place(x=0, y=0, relwidth=1, relheight=1)
        self.inner = tk.Frame(self, bg=fill, highlightthickness=0, bd=0)
        inset = max(8, int(radius) - 2) if pad is None else int(pad)
        self.inner.pack(fill="both", expand=True, padx=inset, pady=inset)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, event=None):
        width = max(self.winfo_width(), 4)
        height = max(self.winfo_height(), 4)
        if (width, height) == self._last_size:
            return
        self._last_size = (width, height)
        self._photo = rounded_image(
            width, height, self._radius, self._fill, self._outline,
            backdrop=self._parent_bg,
        )
        self._bg.configure(image=self._photo)


class RoundedButton(tk.Canvas):
    """Rounded action button that still supports pack(fill='x') and config(state=)."""

    def __init__(self, parent, text, command=None, style="primary", padx=14, pady=7,
                 font=None, radius=9, **kw):
        self._text = text
        self._command = command
        self._style = style if style in _BTN_COLORS else "primary"
        self._padx = int(kw.pop("ipadx", padx) or 14)
        self._pady = int(kw.pop("ipady", pady) or 7)
        self._font = font or FONT_BOLD
        self._radius = radius
        self._state = "normal"
        self._hover = False
        self._photo = None
        self._item_bg = None
        self._item_text = None
        fill, fg, hover = _BTN_COLORS[self._style]
        self._fill = kw.pop("bg", fill)
        self._fg = kw.pop("fg", fg)
        self._hover_fill = hover
        for extra in ("relief", "bd", "borderwidth", "activebackground",
                      "activeforeground", "cursor", "highlightthickness",
                      "takefocus", "overrelief"):
            kw.pop(extra, None)
        text_w, text_h = measure_text(parent, text, self._font)
        width = text_w + self._padx * 2
        height = text_h + self._pady * 2
        super().__init__(
            parent, width=width, height=height, bg=widget_bg(parent),
            highlightthickness=0, bd=0, cursor="hand2", **kw,
        )
        self.bind("<Configure>", self._redraw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", lambda _e: None)
        self.bind("<ButtonRelease-1>", self._on_click)
        self._redraw()

    def _colors(self):
        if self._state == "disabled":
            return SURFACE2, MUTED
        if self._hover:
            return self._hover_fill, self._fg
        return self._fill, self._fg

    def _redraw(self, event=None):
        width = max(self.winfo_width(), int(self.cget("width") or 4), 4)
        height = max(self.winfo_height(), int(self.cget("height") or 4), 4)
        fill, fg = self._colors()
        self._photo = rounded_image(
            width, height, self._radius, fill, backdrop=widget_bg(self),
        )
        self.delete("all")
        self._item_bg = self.create_image(0, 0, image=self._photo, anchor="nw")
        self._item_text = self.create_text(
            width / 2, height / 2, text=self._text, fill=fg,
            font=self._font, anchor="center",
        )

    def _on_enter(self, _event=None):
        if self._state == "disabled":
            return
        self._hover = True
        self._redraw()

    def _on_leave(self, _event=None):
        self._hover = False
        self._redraw()

    def _on_click(self, event=None):
        if self._state == "disabled" or self._command is None:
            return
        if event is not None and not (0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()):
            return
        self._command()

    def config(self, **kw):
        return self.configure(**kw)

    def configure(self, **kw):
        redraw = False
        if "text" in kw:
            self._text = kw.pop("text")
            redraw = True
        if "command" in kw:
            self._command = kw.pop("command")
        if "state" in kw:
            self._state = kw.pop("state")
            super().configure(cursor="arrow" if self._state == "disabled" else "hand2")
            redraw = True
        if "bg" in kw:
            self._fill = kw.pop("bg")
            redraw = True
        if "fg" in kw:
            self._fg = kw.pop("fg")
            redraw = True
        if kw:
            super().configure(**kw)
        if redraw:
            self._redraw()

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "state":
            return self._state
        if key == "bg":
            return self._fill
        if key == "fg":
            return self._fg
        return super().cget(key)

    def __setitem__(self, key, value):
        self.configure(**{key: value})

    def __getitem__(self, key):
        return self.cget(key)


class DropZone(tk.Canvas):
    """Dashed rounded drop target. Bind clicks and DnD on this widget."""

    def __init__(self, parent, text, height=48, compact=False, **kw):
        self._text = text
        self._compact = compact
        parent_bg = widget_bg(parent)
        super().__init__(
            parent, height=36 if compact else height, bg=parent_bg,
            highlightthickness=0, bd=0, cursor="hand2", **kw,
        )
        self.bind("<Configure>", self._redraw)
        self._redraw()

    def set_text(self, text):
        self._text = text
        self._redraw()

    def config(self, **kw):
        return self.configure(**kw)

    def configure(self, **kw):
        if "text" in kw:
            self._text = kw.pop("text")
            self._redraw()
        if kw:
            super().configure(**kw)

    def _redraw(self, _event=None):
        width = max(self.winfo_width(), 8)
        height = max(self.winfo_height(), 8)
        self.delete("all")
        pad = 3
        x1, y1, x2, y2 = pad, pad, width - pad, height - pad
        radius = 10
        self.create_rectangle(0, 0, width, height, fill=widget_bg(self.master), outline="")
        # Soft fill, then dashed outline so it reads as a drop well.
        self._fill_round_rect(x1, y1, x2, y2, radius, CARD)
        self._stroke_round_rect(x1, y1, x2, y2, radius, ACCENT_SOFT)
        font = ("Segoe UI", 8, "bold") if self._compact else ("Segoe UI", 10)
        self.create_text(
            width / 2, height / 2, text=self._text, fill=MUTED,
            font=font, anchor="center", justify="center",
        )

    def _fill_round_rect(self, x1, y1, x2, y2, radius, fill):
        self.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill, outline="")
        self.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill, outline="")
        for cx, cy in (
            (x1 + radius, y1 + radius),
            (x2 - radius, y1 + radius),
            (x1 + radius, y2 - radius),
            (x2 - radius, y2 - radius),
        ):
            self.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                             fill=fill, outline="")

    def _stroke_round_rect(self, x1, y1, x2, y2, radius, color):
        dash = (5, 4)
        self.create_line(x1 + radius, y1, x2 - radius, y1, fill=color, dash=dash, width=1)
        self.create_line(x2, y1 + radius, x2, y2 - radius, fill=color, dash=dash, width=1)
        self.create_line(x2 - radius, y2, x1 + radius, y2, fill=color, dash=dash, width=1)
        self.create_line(x1, y2 - radius, x1, y1 + radius, fill=color, dash=dash, width=1)
        self.create_arc(x1, y1, x1 + 2 * radius, y1 + 2 * radius, start=90, extent=90,
                        style="arc", outline=color, width=1)
        self.create_arc(x2 - 2 * radius, y1, x2, y1 + 2 * radius, start=0, extent=90,
                        style="arc", outline=color, width=1)
        self.create_arc(x1, y2 - 2 * radius, x1 + 2 * radius, y2, start=180, extent=90,
                        style="arc", outline=color, width=1)
        self.create_arc(x2 - 2 * radius, y2 - 2 * radius, x2, y2, start=270, extent=90,
                        style="arc", outline=color, width=1)


def rounded_btn(parent, text, command=None, style="primary", **kw):
    return RoundedButton(parent, text, command=command, style=style, **kw)


def drop_zone(parent, text, height=48, compact=False, **kw):
    return DropZone(parent, text, height=height, compact=compact, **kw)


def section_title(parent, text, bg=None):
    """Uppercase muted heading with a small accent pip."""
    bg = bg or SURFACE
    row = tk.Frame(parent, bg=bg)
    pip = tk.Frame(row, bg=ACCENT, width=3, height=11)
    pip.pack(side="left", padx=(0, 8))
    pip.pack_propagate(False)
    tk.Label(
        row, text=text, bg=bg, fg=MUTED,
        font=("Segoe UI", 8, "bold"),
    ).pack(side="left")
    return row


def settings_panel(parent, width=258):
    """Fixed-width rounded settings column. Caller packs or grids ``outer``."""
    outer = tk.Frame(parent, bg=BG, width=width)
    outer.pack_propagate(False)
    outer.grid_propagate(False)
    wrap = RoundedFrame(outer, fill=SURFACE, radius=14, outline=BORDER, pad=4)
    wrap.pack(fill="both", expand=True, padx=8, pady=8)
    return outer, wrap.inner


def card(parent, fill=CARD, radius=12, outline=BORDER, pad=10, **kw):
    return RoundedFrame(parent, radius=radius, fill=fill, outline=outline, pad=pad, **kw)


RAIL_W = 64
RAIL_EXPANDED = 220
RAIL_ANIM_MS = 180
RAIL_ANIM_STEP_MS = 16
RAIL_EXPAND_DWELL_MS = 280
RAIL_GROUP_H = 22


def _line_icon(name, size=20, color=TEXT, bg=TAB_BAR):
    """Tiny line icons for the sidebar. Photos are kept by the caller."""
    img = Image.new("RGBA", (size, size), hex_rgb(bg) + (255,))
    draw = ImageDraw.Draw(img)
    ink = hex_rgb(color) + (255,)
    s = size - 1
    m = max(3, size // 6)

    def line(*xy):
        draw.line(xy, fill=ink, width=max(1, size // 12))

    def rect(box):
        draw.rounded_rectangle(box, radius=max(1, size // 10), outline=ink, width=max(1, size // 12))

    if name == "app":
        draw.rounded_rectangle((1, 1, s - 1, s - 1), radius=5, fill=hex_rgb(ACCENT) + (255,))
        try:
            draw.text((size * 0.28, size * 0.06), "C", fill=(255, 255, 255, 255),
                      font=ImageFont.load_default())
        except Exception:
            pass
    elif name == "resizer":
        line(m, m, m, m + 6, m + 6, m)
        line(s - m, s - m, s - m, s - m - 6, s - m - 6, s - m)
        rect((m + 3, m + 3, s - m - 3, s - m - 3))
    elif name == "converter":
        line(m, size * 0.38, s - m - 3, size * 0.38)
        line(s - m - 7, size * 0.38 - 4, s - m - 3, size * 0.38, s - m - 7, size * 0.38 + 4)
        line(s - m, size * 0.68, m + 3, size * 0.68)
        line(m + 7, size * 0.68 - 4, m + 3, size * 0.68, m + 7, size * 0.68 + 4)
    elif name == "scan":
        rect((m, m + 1, s - m, s - m - 1))
        line(m + 4, size * 0.5, s - m - 4, size * 0.5)
    elif name == "layout":
        gap = (s - 2 * m) / 2
        for i in (0, 1):
            for j in (0, 1):
                x = m + i * gap
                y = m + j * gap
                rect((x, y, x + gap - 2, y + gap - 2))
    elif name == "polaroid":
        rect((m + 1, m, s - m - 1, s - m))
        draw.rectangle((m + 4, m + 3, s - m - 4, s - m - 6), outline=ink, width=1)
    elif name == "collage":
        rect((m, m, s - m - 4, s - m - 4))
        rect((m + 4, m + 4, s - m, s - m))
    elif name == "cutline":
        draw.ellipse((m, m, s - m, s - m), outline=ink, width=max(1, size // 12))
        line(size * 0.5, m + 1, size * 0.5, s - m - 1)
    elif name == "pdf":
        rect((m + 1, m, s - m - 1, s - m))
        line(s - m - 1, m, s - m - 1, m + 5, s - m - 6, m)
    elif name == "counter":
        for i, y in enumerate((m + 1, size * 0.5 - 1, s - m - 2)):
            line(m + 5, y, s - m, y)
            draw.ellipse((m, y - 1, m + 3, y + 2), fill=ink)
    elif name == "settings":
        cx = cy = size / 2
        r = size * 0.28
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ink, width=max(1, size // 12))
        for deg in range(0, 360, 60):
            rad = math.radians(deg)
            line(cx + r * 0.4 * math.cos(rad), cy + r * 0.4 * math.sin(rad),
                 cx + (r + 3) * math.cos(rad), cy + (r + 3) * math.sin(rad))
    elif name == "chevron":
        line(m + 2, m + 4, size * 0.5, s - m - 2, s - m - 2, m + 4)
    else:
        rect((m, m, s - m, s - m))
    return ImageTk.PhotoImage(img)


class RoundedField(tk.Frame):
    """Entry sitting in a rounded well. Pack/grid the frame; Entry methods are forwarded."""

    def __init__(self, parent, textvariable=None, width=8, font=None, **kw):
        parent_bg = widget_bg(parent)
        super().__init__(parent, bg=parent_bg, highlightthickness=0, bd=0)
        self._parent_bg = parent_bg
        self._fill = SURFACE2
        self._photo = None
        self._bg = tk.Label(self, bg=parent_bg, bd=0, highlightthickness=0)
        self._bg.place(x=0, y=0, relwidth=1, relheight=1)
        self._entry = tk.Entry(
            self, textvariable=textvariable, bg=self._fill, fg=TEXT,
            insertbackground=TEXT, relief="flat", bd=0, highlightthickness=0,
            font=font or FONT, width=width, **kw,
        )
        self._entry.pack(fill="both", expand=True, padx=8, pady=5)
        self.bind("<Configure>", self._redraw)
        self._redraw()

    def bind(self, sequence=None, func=None, add=None):
        if sequence in ("<Configure>", None) or (sequence and str(sequence).startswith("<Configure")):
            return super().bind(sequence, func, add)
        return self._entry.bind(sequence, func, add)

    def _redraw(self, _event=None):
        width = max(self.winfo_width(), 8)
        height = max(self.winfo_height(), 8)
        self._photo = rounded_image(width, height, 8, self._fill, backdrop=self._parent_bg)
        self._bg.configure(image=self._photo)

    def configure(self, **kw):
        if "bg" in kw:
            self._fill = kw.pop("bg")
            self._entry.configure(bg=self._fill)
            self._redraw()
        if kw:
            self._entry.configure(**kw)

    def config(self, **kw):
        return self.configure(**kw)

    def focus_set(self):
        return self._entry.focus_set()

    def focus_force(self):
        return self._entry.focus_force()

    def focus(self):
        return self._entry.focus_set()

    def __getattr__(self, name):
        return getattr(self._entry, name)


class RoundedCheck(tk.Frame):
    def __init__(self, parent, text="", variable=None, command=None, fg=None, **kw):
        kw.pop("bg", None)
        kw.pop("selectcolor", None)
        kw.pop("activebackground", None)
        kw.pop("activeforeground", None)
        kw.pop("font", None)
        kw.pop("anchor", None)
        kw.pop("relief", None)
        kw.pop("bd", None)
        kw.pop("highlightthickness", None)
        kw.pop("indicatoron", None)
        super().__init__(parent, bg=widget_bg(parent), highlightthickness=0, bd=0, **kw)
        self._var = variable if variable is not None else tk.BooleanVar(value=False)
        self._command = command
        self._state = "normal"
        self._box = tk.Canvas(self, width=18, height=18, bg=widget_bg(parent),
                              highlightthickness=0, bd=0, cursor="hand2")
        self._box.pack(side="left")
        self._label = tk.Label(
            self, text=text, bg=widget_bg(parent), fg=fg or TEXT,
            font=FONT, cursor="hand2",
        )
        self._label.pack(side="left", padx=(6, 0))
        self._photo = None
        for w in (self, self._box, self._label):
            w.bind("<Button-1>", self._toggle)
        try:
            self._var.trace_add("write", lambda *_: self._redraw())
        except Exception:
            pass
        self._redraw()

    def _on(self):
        try:
            return bool(self._var.get())
        except Exception:
            return False

    def _toggle(self, _event=None):
        if self._state == "disabled":
            return
        self._var.set(not self._on())
        if self._command:
            self._command()

    def _redraw(self):
        on = self._on()
        fill = SURFACE2 if self._state == "disabled" else (ACCENT if on else SURFACE2)
        outline = BORDER if self._state == "disabled" else (ACCENT if on else BORDER)
        self._photo = rounded_image(18, 18, 5, fill, outline=outline,
                                    backdrop=widget_bg(self))
        self._box.delete("all")
        self._box.create_image(0, 0, image=self._photo, anchor="nw")
        if on:
            mark = MUTED if self._state == "disabled" else TEXT
            self._box.create_line(4, 10, 7, 13, 14, 5, fill=mark, width=2)

    def configure(self, **kw):
        if "text" in kw:
            self._label.configure(text=kw.pop("text"))
        if "command" in kw:
            self._command = kw.pop("command")
        if "state" in kw:
            self._state = kw.pop("state")
            self._redraw()
        if "fg" in kw:
            self._label.configure(fg=kw.pop("fg"))
        if "variable" in kw:
            self._var = kw.pop("variable")
            self._redraw()
        if kw:
            super().configure(**kw)

    def config(self, **kw):
        return self.configure(**kw)

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "text":
            return self._label.cget("text")
        return super().cget(key)


class RoundedCombo(tk.Canvas):
    """Readonly rounded dropdown. Supports textvariable, values, and <<ComboboxSelected>>."""

    def __init__(self, parent, textvariable=None, values=(), state="readonly",
                 width=20, font=None, **kw):
        kw.pop("style", None)
        super().__init__(
            parent, height=30, bg=widget_bg(parent),
            highlightthickness=0, bd=0, cursor="hand2", **kw,
        )
        self._var = textvariable if textvariable is not None else tk.StringVar()
        self._values = list(values)
        self._state = state or "readonly"
        self._font = font or FONT
        self._char_width = int(width or 12)
        self._photo = None
        self._pop = None
        self._chevron = _line_icon("chevron", 10, MUTED, SURFACE2)
        try:
            self._var.trace_add("write", lambda *_: self._redraw())
        except Exception:
            pass
        self.bind("<Configure>", self._redraw)
        self.bind("<Button-1>", self._toggle)
        self.configure(width=max(72, self._char_width * 8))
        self._redraw()

    def _text(self):
        try:
            return str(self._var.get())
        except Exception:
            return ""

    def _redraw(self, _event=None):
        width = max(self.winfo_width(), int(self.cget("width") or 72), 72)
        height = max(self.winfo_height(), 30)
        fill = SURFACE2 if self._state != "disabled" else CARD
        self._photo = rounded_image(width, height, 8, fill, outline=BORDER,
                                    backdrop=widget_bg(self))
        self.delete("all")
        self.create_image(0, 0, image=self._photo, anchor="nw")
        self.create_text(10, height / 2, text=self._text(), fill=TEXT if self._state != "disabled" else MUTED,
                         font=self._font, anchor="w")
        self.create_image(width - 14, height / 2, image=self._chevron)

    def _toggle(self, _event=None):
        if self._state == "disabled":
            return
        if self._pop is not None:
            self._close()
            return
        self._open()

    def _open(self):
        self.update_idletasks()
        pop = tk.Toplevel(self)
        pop.wm_overrideredirect(True)
        pop.configure(bg=BORDER)
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 2
        width = max(self.winfo_width(), 80)
        inner = tk.Frame(pop, bg=SURFACE2)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        values = self._values or [self._text()]
        height = min(12, max(1, len(values)))
        lb = tk.Listbox(
            inner, bg=SURFACE2, fg=TEXT, selectbackground=ACCENT,
            selectforeground=TEXT, relief="flat", bd=0, highlightthickness=0,
            activestyle="none", font=self._font, height=height,
            exportselection=False,
        )
        lb.pack(fill="both", expand=True)
        for value in values:
            lb.insert("end", str(value))
        current = self._text()
        for i, value in enumerate(values):
            if str(value) == current:
                lb.selection_set(i)
                lb.see(i)
                break
        pop.geometry(f"{width}x{max(28, height * 22) + 2}+{x}+{y}")

        def choose(_event=None):
            sel = lb.curselection()
            if not sel:
                return
            raw = values[sel[0]]
            self._var.set(raw)
            self._close()
            self.event_generate("<<ComboboxSelected>>")

        lb.bind("<ButtonRelease-1>", choose)
        lb.bind("<Return>", choose)
        pop.bind("<Escape>", lambda _e: self._close())
        self._pop = pop
        self._outside = self.winfo_toplevel().bind("<Button-1>", self._click_outside, add="+")
        pop.after(10, lambda: pop.focus_force())

    def _click_outside(self, event):
        if self._pop is None:
            return
        widget = event.widget
        try:
            if str(widget).startswith(str(self._pop)) or widget is self:
                return
        except Exception:
            pass
        self._close()

    def _close(self):
        if self._pop is not None:
            try:
                self._pop.destroy()
            except Exception:
                pass
            self._pop = None
        top = self.winfo_toplevel()
        if getattr(self, "_outside", None):
            try:
                top.unbind("<Button-1>", self._outside)
            except Exception:
                pass
            self._outside = None

    def configure(self, **kw):
        redraw = False
        if "values" in kw:
            self._values = list(kw.pop("values") or [])
            redraw = True
        if "state" in kw:
            self._state = kw.pop("state")
            self.configure(cursor="arrow" if self._state == "disabled" else "hand2")
            redraw = True
        if "textvariable" in kw:
            self._var = kw.pop("textvariable")
            redraw = True
        if kw:
            super().configure(**kw)
        if redraw:
            self._redraw()

    def config(self, **kw):
        return self.configure(**kw)

    def cget(self, key):
        if key == "values":
            return tuple(self._values)
        if key == "state":
            return self._state
        return super().cget(key)

    def get(self):
        return self._text()

    def set(self, value):
        self._var.set(value)

    def __setitem__(self, key, value):
        self.configure(**{key: value})

    def destroy(self):
        self._close()
        super().destroy()


class Segmented(tk.Canvas):
    def __init__(self, parent, variable, options, command=None, orient="horizontal", **kw):
        self._var = variable
        self._command = command
        self._orient = orient
        self._items = []
        for item in options:
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                self._items.append((item[0], item[1]))
            else:
                self._items.append((item, str(item)))
        n = max(1, len(self._items))
        height = 32 if orient == "horizontal" else 30 * n
        super().__init__(
            parent, height=height, bg=widget_bg(parent),
            highlightthickness=0, bd=0, cursor="hand2", **kw,
        )
        self._photo = None
        self._hit = []
        try:
            self._var.trace_add("write", lambda *_: self._redraw())
        except Exception:
            pass
        self.bind("<Configure>", self._redraw)
        self.bind("<Button-1>", self._click)
        self._redraw()

    def _value(self):
        try:
            return self._var.get()
        except Exception:
            return self._items[0][0] if self._items else ""

    def _redraw(self, _event=None):
        width = max(self.winfo_width(), 40)
        n = max(1, len(self._items))
        height = 32 if self._orient == "horizontal" else max(30 * n, self.winfo_height(), 30)
        self._photo = rounded_image(width, height, 9, SURFACE2, outline=BORDER,
                                    backdrop=widget_bg(self))
        self.delete("all")
        self.create_image(0, 0, image=self._photo, anchor="nw")
        self._sel_imgs = []
        self._hit = []
        current = self._value()
        if self._orient == "horizontal":
            slot = width / n
            for i, (value, label) in enumerate(self._items):
                x1, x2 = i * slot, (i + 1) * slot
                self._hit.append((x1, 0, x2, height, value))
                if value == current or str(value) == str(current):
                    pad = 2
                    sel = rounded_image(max(8, int(x2 - x1) - 4), height - 4, 7, ACCENT,
                                        backdrop=SURFACE2)
                    self._sel_imgs.append(sel)
                    self.create_image(x1 + 2, pad, image=sel, anchor="nw")
                    fill = TEXT
                else:
                    fill = MUTED
                self.create_text((x1 + x2) / 2, height / 2, text=label, fill=fill,
                                 font=FONT_UI, anchor="center")
        else:
            slot = height / n
            for i, (value, label) in enumerate(self._items):
                y1, y2 = i * slot, (i + 1) * slot
                self._hit.append((0, y1, width, y2, value))
                if value == current or str(value) == str(current):
                    sel = rounded_image(max(8, width - 4), max(8, int(y2 - y1) - 4), 7, ACCENT,
                                        backdrop=SURFACE2)
                    self._sel_imgs.append(sel)
                    self.create_image(2, y1 + 2, image=sel, anchor="nw")
                    fill = TEXT
                else:
                    fill = MUTED
                self.create_text(12, (y1 + y2) / 2, text=label, fill=fill,
                                 font=FONT, anchor="w")

    def _click(self, event):
        for x1, y1, x2, y2, value in self._hit:
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                if str(self._value()) != str(value):
                    self._var.set(value)
                    if self._command:
                        self._command()
                elif self._command:
                    self._command()
                return


class RoundedSlider(tk.Canvas):
    def __init__(self, parent, from_=0, to=100, variable=None, orient="horizontal",
                 command=None, resolution=None, **kw):
        kw.pop("showvalue", None)
        kw.pop("troughcolor", None)
        kw.pop("highlightthickness", None)
        kw.pop("bd", None)
        kw.pop("font", None)
        kw.pop("bg", None)
        kw.pop("fg", None)
        super().__init__(
            parent, height=22, bg=widget_bg(parent),
            highlightthickness=0, bd=0, cursor="hand2", **kw,
        )
        self._from = float(from_)
        self._to = float(to)
        self._var = variable
        self._command = command
        self._res = resolution
        self._photo = None
        if self._var is not None:
            try:
                self._var.trace_add("write", lambda *_: self._redraw())
            except Exception:
                pass
        self.bind("<Configure>", self._redraw)
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self._redraw()

    def _raw(self):
        try:
            return float(self._var.get())
        except Exception:
            return self._from

    def _frac(self):
        span = self._to - self._from
        if span == 0:
            return 0
        return max(0.0, min(1.0, (self._raw() - self._from) / span))

    def _set(self, frac):
        value = self._from + frac * (self._to - self._from)
        if self._res:
            value = round(value / self._res) * self._res
        if self._var is not None:
            current = self._var.get()
            if isinstance(current, int) and self._res is None:
                value = int(round(value))
            self._var.set(value)
        if self._command:
            self._command(str(value))
        self._redraw()

    def _drag(self, event):
        pad = 10
        width = max(self.winfo_width(), 20)
        frac = (event.x - pad) / max(1, width - pad * 2)
        self._set(max(0.0, min(1.0, frac)))

    def _redraw(self, _event=None):
        width = max(self.winfo_width(), 40)
        height = max(self.winfo_height(), 22)
        self.delete("all")
        self.create_rectangle(0, 0, width, height, fill=widget_bg(self), outline="")
        y = height / 2
        pad = 10
        self.create_line(pad, y, width - pad, y, fill=SURFACE2, width=6)
        x = pad + self._frac() * (width - pad * 2)
        self.create_line(pad, y, x, y, fill=ACCENT, width=6)
        r = 7
        self.create_oval(x - r, y - r, x + r, y + r, fill=TEXT, outline=ACCENT)


class RoundedProgress(tk.Canvas):
    def __init__(self, parent, orient="horizontal", mode="determinate", **kw):
        super().__init__(
            parent, height=8, bg=widget_bg(parent),
            highlightthickness=0, bd=0, **kw,
        )
        self._value = 0.0
        self._maximum = 100.0
        self._photo_bg = None
        self._photo_fg = None
        self.bind("<Configure>", self._redraw)
        self._redraw()

    def _redraw(self, _event=None):
        width = max(self.winfo_width(), 20)
        height = max(self.winfo_height(), 8)
        self._photo_bg = rounded_image(width, height, height / 2, SURFACE2,
                                       backdrop=widget_bg(self))
        self.delete("all")
        self.create_image(0, 0, image=self._photo_bg, anchor="nw")
        frac = 0 if self._maximum <= 0 else max(0.0, min(1.0, self._value / self._maximum))
        fill_w = int(width * frac)
        if fill_w >= 4:
            self._photo_fg = rounded_image(fill_w, height, height / 2, ACCENT,
                                           backdrop=widget_bg(self))
            self.create_image(0, 0, image=self._photo_fg, anchor="nw")

    def configure(self, **kw):
        if "value" in kw:
            self._value = float(kw.pop("value") or 0)
        if "maximum" in kw:
            self._maximum = float(kw.pop("maximum") or 1)
        if kw:
            super().configure(**kw)
        self._redraw()

    def config(self, **kw):
        return self.configure(**kw)

    def __setitem__(self, key, value):
        self.configure(**{key: value})

    def __getitem__(self, key):
        if key == "value":
            return self._value
        if key == "maximum":
            return self._maximum
        return super().cget(key)


class IconRail(tk.Frame):
    """Collapsed icon rail that overlays labels on hover."""

    def __init__(self, host, groups, on_select, on_settings=None):
        super().__init__(host, bg=TAB_BAR, width=RAIL_W, highlightthickness=0, bd=0)
        self.host = host
        self.groups = groups
        self.on_select = on_select
        self.on_settings = on_settings
        self.active = groups[0][1][0][0] if groups else None
        self._expanded = False
        self._enter_job = None
        self._leave_job = None
        self._anim_job = None
        self._photos = {}
        self._item_labels = []
        self._group_heads = []
        self._item_rows = {}
        self._build()
        self.place(x=0, y=0, relheight=1, width=RAIL_W)
        self.lift()

    def spacer(self):
        bar = tk.Frame(self.host, bg=TAB_BAR, width=RAIL_W, highlightthickness=0, bd=0)
        bar.pack_propagate(False)
        return bar

    def _icon(self, name, active=False):
        key = (name, active)
        if key not in self._photos:
            color = TEXT if active else MUTED
            bg = ACCENT_SOFT if active else TAB_BAR
            self._photos[key] = _line_icon(name, 20, color, bg)
        return self._photos[key]

    def _build(self):
        head = tk.Frame(self, bg=TAB_BAR)
        head.pack(fill="x", pady=(14, 6))
        tk.Label(head, image=self._icon("app", True), bg=TAB_BAR).pack(side="left", padx=(22, 8))
        self._wordmark = tk.Label(head, text="CopyPro", bg=TAB_BAR, fg=TEXT,
                                  font=("Segoe UI", 10, "bold"))
        tools = tk.Frame(self, bg=TAB_BAR)
        tools.pack(fill="both", expand=True)
        for group_name, items in self.groups:
            block = tk.Frame(tools, bg=TAB_BAR)
            block.pack(fill="x", pady=(4, 0))
            slot = tk.Frame(block, bg=TAB_BAR, height=RAIL_GROUP_H)
            slot.pack(fill="x")
            slot.pack_propagate(False)
            divider = tk.Frame(slot, bg=BORDER, height=1)
            gl = tk.Label(
                slot, text=group_name.upper(), bg=TAB_BAR, fg=MUTED,
                font=("Segoe UI", 7, "bold"), anchor="w",
            )
            self._group_heads.append((slot, gl, divider))
            for key, label, icon in items:
                row = tk.Frame(block, bg=TAB_BAR, cursor="hand2")
                row.pack(fill="x", padx=8, pady=2)
                pip = tk.Frame(row, bg=TAB_BAR, width=3, height=22)
                pip.pack(side="left", padx=(0, 8))
                pip.pack_propagate(False)
                ic = tk.Label(row, image=self._icon(icon), bg=TAB_BAR, cursor="hand2")
                ic.pack(side="left")
                text = tk.Label(
                    row, text=label, bg=TAB_BAR, fg=MUTED,
                    font=("Segoe UI", 9), anchor="w", cursor="hand2",
                )
                self._item_labels.append(text)
                self._item_rows[key] = (row, ic, text, pip, icon)
                for widget in (row, ic, text, pip):
                    widget.bind("<Button-1>", lambda _e, k=key: self._choose(k))
        foot = tk.Frame(self, bg=TAB_BAR)
        foot.pack(side="bottom", fill="x", pady=12)
        gear = tk.Frame(foot, bg=TAB_BAR, cursor="hand2")
        gear.pack(fill="x", padx=8)
        glbl = tk.Label(gear, image=self._icon("settings"), bg=TAB_BAR, cursor="hand2")
        glbl.pack(side="left")
        settings_lbl = tk.Label(
            gear, text="Settings", bg=TAB_BAR, fg=MUTED,
            font=("Segoe UI", 9), cursor="hand2",
        )
        self._item_labels.append(settings_lbl)
        self._settings_label = settings_lbl
        if self.on_settings:
            for widget in (gear, glbl, settings_lbl):
                widget.bind("<Button-1>", lambda _e: self.on_settings())
        self._bind_hover(self)
        self._apply_active()
        self._apply_expanded(False, animate=False)

    def _bind_hover(self, widget):
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        for child in widget.winfo_children():
            self._bind_hover(child)

    def _pointer_xy(self, event=None):
        if event is not None:
            return event.x_root, event.y_root
        return self.winfo_pointerxy()

    def _pointer_in_collapsed_strip(self, event=None):
        try:
            x, y = self._pointer_xy(event)
            rx, ry = self.winfo_rootx(), self.winfo_rooty()
            height = self.winfo_height()
            return rx <= x < rx + RAIL_W and ry <= y < ry + height
        except Exception:
            return False

    def _pointer_over_rail(self):
        try:
            x, y = self.winfo_pointerxy()
            widget = self.winfo_containing(x, y)
            return widget is not None and str(widget).startswith(str(self))
        except Exception:
            return False

    def _cancel_enter_job(self):
        if self._enter_job:
            try:
                self.after_cancel(self._enter_job)
            except Exception:
                pass
            self._enter_job = None

    def _on_enter(self, event=None):
        if self._leave_job:
            self.after_cancel(self._leave_job)
            self._leave_job = None
        if self._expanded:
            return
        if not self._pointer_in_collapsed_strip(event):
            return
        if self._enter_job:
            return
        self._enter_job = self.after(RAIL_EXPAND_DWELL_MS, self._expand_if_still_in)

    def _expand_if_still_in(self):
        self._enter_job = None
        if self._expanded:
            return
        if not self._pointer_in_collapsed_strip():
            return
        self._apply_expanded(True)

    def _on_leave(self, _event=None):
        if self._leave_job:
            self.after_cancel(self._leave_job)
        self._leave_job = self.after(180, self._collapse_if_out)

    def _collapse_if_out(self):
        self._leave_job = None
        if self._pointer_over_rail():
            return
        self._apply_expanded(False)

    def _cancel_anim(self):
        if self._anim_job:
            try:
                self.after_cancel(self._anim_job)
            except Exception:
                pass
            self._anim_job = None

    def _rail_width(self):
        try:
            width = int(float(self.place_info().get("width") or 0))
        except Exception:
            width = 0
        if width <= 0:
            width = self.winfo_width() or RAIL_W
        return width

    def _animate_width(self, target, on_done=None):
        self._cancel_anim()
        start = self._rail_width()
        if start == target:
            self.place_configure(width=target)
            self.lift()
            if on_done:
                on_done()
            return
        elapsed = [0]

        def tick():
            elapsed[0] += RAIL_ANIM_STEP_MS
            t = min(1.0, elapsed[0] / RAIL_ANIM_MS)
            eased = 1.0 - (1.0 - t) ** 3
            width = int(round(start + (target - start) * eased))
            self.place_configure(width=width)
            self.lift()
            if t < 1.0:
                self._anim_job = self.after(RAIL_ANIM_STEP_MS, tick)
            else:
                self._anim_job = None
                self.place_configure(width=target)
                if on_done:
                    on_done()

        self._anim_job = self.after(RAIL_ANIM_STEP_MS, tick)

    def _show_labels(self, show):
        if show:
            self._wordmark.pack(side="left")
            for _slot, gl, divider in self._group_heads:
                divider.place_forget()
                gl.pack(fill="both", expand=True, padx=18)
            for text in self._item_labels:
                text.pack(side="left", padx=(10, 0))
        else:
            self._wordmark.pack_forget()
            for _slot, gl, divider in self._group_heads:
                gl.pack_forget()
                divider.place(relx=0.5, rely=0.5, relwidth=0.25, height=1, anchor="center")
            for text in self._item_labels:
                text.pack_forget()

    def _apply_expanded(self, expanded, animate=True):
        self._expanded = expanded
        if not expanded:
            self._cancel_enter_job()
        if not animate:
            self._cancel_anim()
            self._show_labels(expanded)
            self.place_configure(width=RAIL_EXPANDED if expanded else RAIL_W)
            self.lift()
            self._apply_active()
            return
        if expanded:
            self._show_labels(True)
            self._animate_width(RAIL_EXPANDED)
        else:
            self._animate_width(
                RAIL_W,
                on_done=lambda: self._show_labels(False) if not self._expanded else None,
            )
        self._apply_active()

    def _choose(self, key):
        self.set_active(key)
        if self.on_select:
            self.on_select(key)

    def set_active(self, key):
        self.active = key
        self._apply_active()

    def _apply_active(self):
        for key, (row, ic, text, pip, icon) in self._item_rows.items():
            on = key == self.active
            bg = ACCENT_SOFT if on else TAB_BAR
            fg = TEXT if on else MUTED
            row.configure(bg=bg)
            ic.configure(bg=bg, image=self._icon(icon, active=on))
            text.configure(bg=bg, fg=fg)
            pip.configure(bg=ACCENT if on else bg)


def combo(parent, textvariable=None, values=(), state="readonly", width=20, **kw):
    return RoundedCombo(parent, textvariable=textvariable, values=values,
                        state=state, width=width, **kw)


def segmented(parent, variable, options, command=None, orient="horizontal", **kw):
    return Segmented(parent, variable, options, command=command, orient=orient, **kw)


def check(parent, text="", variable=None, command=None, **kw):
    return RoundedCheck(parent, text=text, variable=variable, command=command, **kw)


def field(parent, textvariable=None, width=8, **kw):
    return RoundedField(parent, textvariable=textvariable, width=width, **kw)


def slider(parent, from_=0, to=100, variable=None, command=None, **kw):
    return RoundedSlider(parent, from_=from_, to=to, variable=variable, command=command, **kw)


def progress(parent, **kw):
    return RoundedProgress(parent, **kw)

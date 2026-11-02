"""THE NOTE BESIDE WHAT WAS HANDED OVER (the user, 2026-11-01).

When the user opens something the work handed over - an email, an event, a
file - how it is used and what to check come up on the desktop beside it
(agent/work/handed.py `note`): a card of its own, on top of every window
until it is closed.

As the user asked for it:

  - LOOKS AS TODAY'S APPS DO: a dark card with its own bar - no system title
    bar - rounded where the system rounds windows (Windows 11), flat
    buttons; dragged by its bar; each thing to check a box to tick.
  - TWO SIDES: on the left what was handed over itself - an email's To, Cc
    and Subject and its words - saying when it is already on the clipboard,
    with Copy to copy it again; on the right how it is used and what to
    check. Each side scrolls on its own, the wheel the side it is over;
    wider than the screen, both are narrowed.
  - EACH SIDE ZOOMED ON ITS OWN: its own - and +, and Ctrl +, Ctrl -, Ctrl 0
    and Ctrl with the wheel for the side the pointer is over (else the side
    last pressed in) - each kept for the next note. One ✕ closes both.
    Zoomed, nothing is built again (the user, 2026-11-01): each side's words
    are in fonts of its own, named in Tk, and a zoom only sizes them.
  - NEVER TALLER THAN THE SCREEN: past that, each side scrolls - a thin bar
    of its own, dragged, and the wheel.
  - ITS WORDS CAN BE SELECTED AND COPIED: how it is used, what to check,
    the item's lines - each a text that cannot be written in, dragged
    across to select, Ctrl+C, Ctrl+A for all of one, and the right button's
    menu (Copy, Select all); a box to tick is ticked by its box alone.
  - WHAT WAS HANDED OVER CAN BE CHANGED THERE (the user, 2026-11-01): its
    words are written in as in any editor - Ctrl+X, C, V, Z, and the right
    button's Cut, Copy, Paste, Select all - and Copy copies them as they now
    are; changed after they were put on the clipboard, it says so. SAVE
    keeps them where the work keeps them: opened again from anywhere, it is
    what the user saved - shown only while there is something not saved (the
    user, 2026-11-01): after a change, gone once saved, back at the next.
  - ITS SIZE IS THE USER'S: dragged by any edge or corner, as a window is
    (the user, 2026-11-01), the sides shared out anew - and how they share
    it, by the line between them, dragged; both kept while a side is zoomed.
    WHAT IT SAYS FOLLOWS AS IT IS DRAGGED, nothing built again (the user: "no
    refresh"): the sides share the width by weight, their words wrap anew
    and their bars show when what they say no longer fits (`_reflow`). Its
    size and its sides' share, as the user left them, are kept for the next
    note (`on_layout`), as each side's zoom is (the user, 2026-11-01).
  - AS LONG AS THE WORK MADE IT (a review of a paper is long, and is what it
    is): what was handed over is shown whole, its words scrolled within
    themselves under its lines - not drawn whole into a canvas, which grows
    past what a window can be.
"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import Any, Callable, Dict, List, Optional

#: Its colours: the agent's dark bubbles', with the amber of what to mind.
PALETTE = {"edge": "#3a4152", "bar": "#262b37", "bg": "#1d212b", "ink": "#e9ecf3",
           "muted": "#9aa3b5", "accent": "#eba55a", "on_accent": "#1d212b",
           "hover": "#323848", "ticked": "#5fbf8f", "thumb": "#4a5266", "thumb_on": "#6a7389",
           "select": "#3a4d7a"}

#: The smallest it can be dragged to, and a side's narrowest.
LEAST, SIDE_LEAST = (320, 200), 160

#: Its rim, inside its outline, where its edges are taken to size it; and the
#: line between the sides, as wide as it is taken by.
RIM, DIVIDER = 5, 7

#: Each edge and corner it is sized by, and the pointer shown over it.
EDGES = {"w": "sb_h_double_arrow", "e": "sb_h_double_arrow", "n": "sb_v_double_arrow",
         "s": "sb_v_double_arrow", "nw": "top_left_corner", "ne": "top_right_corner",
         "sw": "bottom_left_corner", "se": "bottom_right_corner"}

#: Words longer than this are not measured line by line to be fitted: they
#: scroll within their box whatever its height.
MEASURED_MOST = 20000

#: The sizes a side can be zoomed to, and the one it starts at.
ZOOMS = (0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.6, 1.8, 2.0, 2.25)
ZOOM_START = 2

#: The sides: what was handed over ("item"), how to use it ("how").
SIDES = ("item", "how")

#: Its sides' widths at the first zoom, and where it stands: this far from
#: the screen's right edge and top, and from its bottom (the taskbar's room).
LEFT_WIDTH, WIDTH, RIGHT, TOP, BOTTOM = 460, 380, 24, 64, 64

#: What each kind of item is called on its side.
KINDS = {"email": "Email", "event": "Event", "text": "Text to paste", "file": "File",
         "folder": "Folder", "answer": "Answer", "message": "Message", "form": "Form to fill"}


class HandedNote:
    """One note on the screen. `words` puts the application's own words in
    the user's language; `family` and `size` are the popups' font (the
    user's setting); `zoom` the step each side opens at - one for both, or
    {"item": n, "how": n} - and `on_zoom(side, step)` told each new one."""

    def __init__(self, root: Any, note: Dict[str, Any], words: Callable[[str], str],
                 family: str, size: int, zoom: Any = ZOOM_START,
                 on_zoom: Optional[Callable[[str, int], None]] = None,
                 on_save: Optional[Callable[[str], bool]] = None,
                 kept_size: Any = None, split: Any = None,
                 on_layout: Optional[Callable[[tuple, Optional[float]], None]] = None) -> None:
        self.root = root
        self.note = note
        self.words = words
        self.family = family
        self.size = max(8, int(size or 10))
        given = zoom if isinstance(zoom, dict) else {side: zoom for side in SIDES}
        self.zooms: Dict[str, int] = {
            side: min(max(int(given.get(side, ZOOM_START)), 0), len(ZOOMS) - 1)
            for side in SIDES}
        self.on_zoom = on_zoom
        #: Where Save keeps the words as changed - none, and there is no Save.
        self.on_save = on_save
        self.ticked: List[bool] = [False] * len(list(note.get("check") or []))
        self.window: Any = None
        self._scale = ZOOMS[ZOOM_START]
        self._drag = (0, 0)
        self._place: Optional[tuple] = None
        #: The size the user dragged it to, if they did.
        self._size: Optional[tuple] = None
        self._resizing: Optional[tuple] = None
        #: The share of the width the left side has, by the line dragged.
        self._split: Optional[float] = None
        self._splitting = False
        # AS THE USER LEFT THE LAST ONE (on_layout): its size, its sides' share.
        try:
            if kept_size and len(kept_size) == 2 and min(int(kept_size[0]),
                                                          int(kept_size[1])) > 0:
                self._size = (max(LEAST[0], int(kept_size[0])), max(LEAST[1], int(kept_size[1])))
        except (TypeError, ValueError):
            pass
        try:
            if split is not None and 0.05 <= float(split) <= 0.95:
                self._split = float(split)
        except (TypeError, ValueError):
            pass
        self.on_layout = on_layout
        self._texts: List[Any] = []
        self._long: Any = None
        self._areas: Dict[str, Any] = {}
        self._panes: List[tuple] = []
        self._last_side = ""
        #: Its fonts, named in Tk - each side's own, and its bar's - by name:
        #: (whose, size more or less, weight). A zoom sizes them; nothing else.
        self._fonts: Dict[str, tuple] = {}
        self._ctx = "chrome"
        #: What was handed over as the user changed it here, if they did -
        #: and whether that is not saved yet.
        self._edited: Optional[str] = None
        self._unsaved = False
        self._save_button: Any = None
        self._words: Any = None

    # -- the window --------------------------------------------------------------------------
    def show(self) -> None:
        window = tk.Toplevel(self.root)
        self.window = window
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=PALETTE["edge"])
        window.title(self.words("Before you use it"))
        modifier = "Command" if sys.platform == "darwin" else "Control"
        for keys, step in ((("plus", "equal", "KP_Add"), 1), (("minus", "KP_Subtract"), -1)):
            for key in keys:
                window.bind(f"<{modifier}-{key}>", lambda _e, s=step: self.zoom_by(s))
        for key in ("0", "KP_0"):
            window.bind(f"<{modifier}-{key}>",
                        lambda _e: self.zoom_to(self._side_now(), ZOOM_START))
        window.bind("<Escape>", lambda _e: self.close())
        # Pressed anywhere in it, it takes the keys - never before: what was
        # just opened beside it keeps them. (A window's binding is every
        # widget's in it.)
        window.bind("<Button-1>", self._pressed, add="+")
        self._build()
        self._rounded()

    def close(self) -> None:
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None
            for name in self._fonts:
                try:
                    self.root.tk.call("font", "delete", name)
                except tk.TclError:
                    pass
            self._fonts = {}

    @property
    def zoom(self) -> int:
        """The larger of the sides' zooms - the bar's and the foot's."""
        return max(self.zooms.values())

    def zoom_by(self, step: int, side: str = "") -> None:
        side = side or self._side_now()
        self.zoom_to(side, self.zooms[side] + step)

    def zoom_to(self, side: str, zoom: int) -> None:
        zoom = min(max(zoom, 0), len(ZOOMS) - 1)
        if side not in self.zooms or zoom == self.zooms[side] or self.window is None:
            return
        self.zooms[side] = zoom
        self._last_side = side
        if self.on_zoom is not None:
            self.on_zoom(side, zoom)
        # Its fonts sized - what they write follows - and laid out for them.
        self._size_fonts()
        self._reflow()

    @staticmethod
    def _within(widget: Any, area: Any) -> bool:
        """Whether a widget is `area` or inside it - by whole steps of its
        path: ".!frame3" is not inside ".!frame"."""
        path, area = str(widget or ""), str(area)
        return path == area or path.startswith(area + ".")

    def _side_of(self, widget: Any) -> str:
        """The side a widget is in - "" for the bar and the foot."""
        for side, area in self._areas.items():
            if self._within(widget, area):
                return side
        return ""

    def _side_now(self) -> str:
        """The side the pointer is over, else the side last pressed in, else
        the first there is."""
        try:
            x, y = self.window.winfo_pointerxy()
            side = self._side_of(self.window.winfo_containing(x, y))
        except (tk.TclError, AttributeError):
            side = ""
        return side or self._last_side or next(iter(self._areas), "how")

    def _px(self, value: float) -> int:
        return int(round(value * self._scale))

    def _points(self, whose: str, more: int) -> int:
        zoom = self.zoom if whose == "chrome" else self.zooms.get(whose, ZOOM_START)
        return max(7, int(round((self.size + more) * ZOOMS[zoom])))

    def _font(self, more: int = 0, weight: str = "normal") -> str:
        """A font of the part being made (`_ctx`: a side, or the bar and the
        foot), named in Tk - zoomed by sizing it, nothing made again. Made in
        Tk itself: a font object of Python's, let go on another thread, would
        call Tk from there."""
        name = f"handednote{id(self)}_{self._ctx}_{more}_{weight}"
        points = self._points(self._ctx, more)
        if name in self._fonts:
            self.root.tk.call("font", "configure", name, "-size", points)
        else:
            self.root.tk.call("font", "create", name, "-family", self.family,
                              "-size", points, "-weight", weight)
            self._fonts[name] = (self._ctx, more, weight)
        return name

    def _size_fonts(self) -> None:
        """Every font of it at its side's zoom now."""
        for name, (whose, more, _weight) in self._fonts.items():
            try:
                self.root.tk.call("font", "configure", name, "-size", self._points(whose, more))
            except tk.TclError:
                continue

    def _pressed(self, event: Any = None) -> None:
        """The keys to it - to the words pressed in, so Ctrl+C copies them -
        and the side pressed in, for Ctrl + and Ctrl -."""
        try:
            if self.window is None:
                return
            target = getattr(event, "widget", None)
            self._last_side = self._side_of(target) or self._last_side
            (target if isinstance(target, tk.Text) else self.window).focus_force()
        except tk.TclError:
            pass

    def _grab(self, event: Any) -> None:
        self._drag = (event.x_root - self.window.winfo_x(), event.y_root - self.window.winfo_y())

    def _move(self, event: Any) -> None:
        """Moved by its bar - at most once a frame (it flickered)."""
        x, y = event.x_root - self._drag[0], event.y_root - self._drag[1]
        self._sized_once_a_frame(f"+{x}+{y}")

    def _rounded(self) -> None:
        """Rounded corners where Windows rounds windows (11 and later)."""
        if not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            self.window.update_idletasks()
            handle = ctypes.windll.user32.GetParent(self.window.winfo_id()) or \
                self.window.winfo_id()
            prefer = ctypes.c_int(2)                     # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, 33, ctypes.byref(prefer),
                                                       ctypes.sizeof(prefer))
        except Exception:                                # pragma: no cover - square, then
            pass

    # -- what it shows ------------------------------------------------------------------------
    def _build(self) -> None:
        window = self.window
        if window.winfo_ismapped():
            # Zoomed: where it stands, by its right edge and its top.
            self._place = (window.winfo_x() + window.winfo_width(), window.winfo_y())
        for child in window.winfo_children():
            child.destroy()
        self._texts, self._long, self._areas, self._panes = [], None, {}, []
        self._words = None
        # LAID OUT ON THE SCREEN, UNSEEN, BEFORE ITS WORDS ARE MEASURED: a
        # text not yet on the screen is as narrow as nothing, and counts a
        # line for every letter. Unseen while it is (no flicker), at the size
        # it asks for (`geometry("")`), shown once it is placed.
        self._seen(False)
        window.geometry("")
        item = self.note.get("item") or {}
        sides = ([("item", int(LEFT_WIDTH * ZOOMS[self.zooms["item"]]))]
                 if item.get("text") or item.get("meta") or item.get("parts") else []) \
            + [("how", int(WIDTH * ZOOMS[self.zooms["how"]]))]
        # Wider than the screen: every side narrowed alike.
        room_wide = window.winfo_screenwidth() - 2 * RIGHT
        around = (len(sides) - 1) * DIVIDER + 2 + 2 * RIM
        whole = sum(one for _side, one in sides)
        if whole + around > room_wide:
            sides = [(side, int(one * (room_wide - around) / whole)) for side, one in sides]
            whole = sum(one for _side, one in sides)
        if self._size is not None:
            # The width the user dragged it to, shared out as the sides were.
            want = max(LEAST[0], min(self._size[0], room_wide)) - around
            sides = [(side, max(SIDE_LEAST, int(one * want / whole))) for side, one in sides]
            whole = sum(one for _side, one in sides)
        if self._split is not None and len(sides) == 2:
            # Shared as the line between them was dragged.
            left = min(max(SIDE_LEAST, int(whole * self._split)), max(SIDE_LEAST,
                                                                      whole - SIDE_LEAST))
            sides = [(sides[0][0], left), (sides[1][0], max(SIDE_LEAST, whole - left))]
        width = sum(one for _side, one in sides) + around
        self._scale, self._ctx = ZOOMS[self.zoom], "chrome"
        # Its outline, then its rim - where its edges are taken - then the card.
        rim = tk.Frame(window, bg=PALETTE["bg"])
        rim.pack(fill="both", expand=True, padx=1, pady=1)
        card = tk.Frame(rim, bg=PALETTE["bg"])
        card.pack(fill="both", expand=True, padx=RIM, pady=RIM)
        self._edges(rim)
        bar = self._bar(card)
        foot = self._foot(card)
        # Each side what it says, scrolled when the screen is not tall enough.
        middle = tk.Frame(card, bg=PALETTE["bg"])
        middle.pack(side="top", fill="both", expand=True)
        middle.rowconfigure(0, weight=1)
        self._middle, self._columns, self._scrolling, self._unfitted = middle, {}, [], set()
        panes = []
        column = 0
        for n, (side, wide) in enumerate(sides):
            self._scale, self._ctx = ZOOMS[self.zooms[side]], side
            if n:
                self._divider(middle).grid(row=0, column=column, sticky="ns")
                column += 1
            pane = tk.Frame(middle, bg=PALETTE["bg"], width=wide)
            pane.grid(row=0, column=column, sticky="nsew")
            # The sides share the width by weight - as they were made, as the
            # line between them was dragged - whatever the width is.
            middle.columnconfigure(column, weight=max(1, wide), uniform="sides",
                                   minsize=SIDE_LEAST)
            self._columns[side] = column
            column += 1
            self._areas[side] = pane
            thumb = tk.Canvas(pane, bg=PALETTE["bg"], highlightthickness=0, bd=0,
                              width=self._px(10))
            wrap = wide - self._px(10) - 2 * self._px(18)
            if side == "item" and not item.get("parts"):
                # Its lines above; its words under them, scrolled within
                # themselves however long they are.
                inner = tk.Frame(pane, bg=PALETTE["bg"], padx=self._px(18), pady=self._px(6))
                # Laid out before its words are measured: a text with no
                # width yet counts a line for every letter.
                inner.pack(side="left", fill="both", expand=True)
                words = self._item(inner, item, wrap)
                panes.append((side, pane, thumb, inner, words, wide))
            else:
                canvas = tk.Canvas(pane, bg=PALETTE["bg"], highlightthickness=0, bd=0,
                                   width=wide - self._px(10))
                body = tk.Frame(canvas, bg=PALETTE["bg"], padx=self._px(18), pady=self._px(6))
                shown_in = canvas.create_window(0, 0, window=body, anchor="nw")
                # Its words as wide as the side, whatever its width becomes.
                canvas.bind("<Configure>", lambda e, c=canvas, i=shown_in: (
                    c.itemconfigure(i, width=max(1, e.width)), self._reflow_soon()))
                if side == "item":
                    self._item(body, item, wrap)             # a form: its parts, each a box
                else:
                    self._body(body, wrap)
                panes.append(("canvas", pane, thumb, canvas, body, wide, side))
        window.update()
        self._fit_texts()
        window.update_idletasks()
        room = window.winfo_screenheight() - TOP - BOTTOM \
            - bar.winfo_reqheight() - foot.winfo_reqheight() - 2 - 2 * RIM

        def natural(one: tuple) -> int:
            if one[0] == "item":
                return room if one[4] is not None and one[4] is self._long \
                    else one[3].winfo_reqheight()
            return one[4].winfo_reqheight()
        if self._size is not None:
            shown = max(60, min(max(LEAST[1], self._size[1]) - bar.winfo_reqheight()
                                - foot.winfo_reqheight() - 2 - 2 * RIM, room))
        else:
            shown = max(60, min(max(natural(one) for one in panes), room))
        for one in panes:
            side, pane, thumb = one[0], one[1], one[2]
            self._ctx = one[6] if side == "canvas" else side
            self._scale = ZOOMS[self.zooms[self._ctx]]
            if side == "item":
                inner, words = one[3], one[4]
                scrolls = False
                if words is not None:
                    above = inner.winfo_reqheight() - words.winfo_reqheight()
                    line = max(1, int(words.tk.call("font", "metrics", words.cget("font"),
                                                    "-linespace")))
                    fits = max(1, (shown - above) // line)    # `above` holds its padding
                    scrolls = words is self._long or int(words.cget("height")) > fits
                    # From now on its box is as tall as the side leaves it,
                    # and scrolls within itself.
                    words.configure(height=min(int(words.cget("height")), fits))
                    self._unfitted.add(words)
                    self._scroller(words, thumb, shown)
                    self._scrolling.append(("text", words, thumb, None, inner))
                    if scrolls:
                        thumb.pack(side="right", fill="y", before=inner)
                self._panes.append((words if words is not None else inner, scrolls, pane))
            else:                                        # a canvas: either side
                canvas, body, wide = one[3], one[4], one[5]
                tall = body.winfo_reqheight()
                canvas.configure(height=shown, scrollregion=(0, 0, wide, max(tall, shown)))
                canvas.pack(side="left", fill="both", expand=True)
                self._scroller(canvas, thumb, shown)
                self._scrolling.append(("canvas", canvas, thumb, body, canvas))
                if tall > shown:
                    thumb.pack(side="right", fill="y", before=canvas)
                self._panes.append((canvas, tall > shown, pane))
        self._scale, self._ctx = ZOOMS[self.zoom], "chrome"
        self._wheel()
        # Sized from here on - by its edges, its corner, the line between its
        # sides - what it says follows, nothing built again.
        window.bind("<Configure>", self._configured, add="+")
        self._grip.lift()                    # its corner over whatever was laid out after
        for strip in self._strips:
            strip.lift()
        window.update_idletasks()
        height = bar.winfo_reqheight() + shown + foot.winfo_reqheight() + 2 + 2 * RIM
        right, y = self._place or (window.winfo_screenwidth() - RIGHT, TOP)
        x = max(0, min(right - width, window.winfo_screenwidth() - width))
        y = max(0, min(y, window.winfo_screenheight() - height - BOTTOM // 2))
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.update_idletasks()
        self._seen(True)

    def _configured(self, event: Any) -> None:
        """Its own size changed - not a part of it, not where it stands: moved
        by its bar, nothing is laid out again (it flickered)."""
        if event.widget is not self.window:
            return
        size = (event.width, event.height)
        if size == getattr(self, "_laid_for", None):
            return
        self._laid_for = size
        self._reflow_soon()

    def _sized_once_a_frame(self, geometry: str) -> None:
        """A size asked for while dragged, given at most once a frame - every
        pointer move given at once drew the whole of it again and again."""
        self._asked_geometry = geometry
        if getattr(self, "_geometry_due", None) is None:
            self._geometry_due = self.window.after(16, self._give_geometry)

    def _give_geometry(self) -> None:
        self._geometry_due = None
        if self.window is not None and getattr(self, "_asked_geometry", None):
            self.window.geometry(self._asked_geometry)

    def _reflow_soon(self) -> None:
        """What it says laid out again for its size - once, a moment after
        the last change, however many come while it is dragged."""
        if self.window is None or getattr(self, "_reflowing", False):
            return
        due = getattr(self, "_reflow_due", None)
        if due is not None:
            try:
                self.window.after_cancel(due)
            except tk.TclError:
                pass
        self._reflow_due = self.window.after(40, self._reflow)

    def _reflow(self) -> None:
        """WHAT IT SAYS FOLLOWS ITS SIZE (module docstring): its words
        wrapped anew, as tall as their lines; each side's bar shown when what
        it says no longer fits, and gone when it does - nothing built again."""
        self._reflow_due = None
        if self.window is None:
            return
        self._reflowing = True
        try:
            self._fit_texts()
            self.window.update_idletasks()
            panes = []
            for kind, scrolled, thumb, body, before in getattr(self, "_scrolling", []):
                if not scrolled.winfo_exists():
                    continue
                if kind == "canvas":
                    tall, high = body.winfo_reqheight(), scrolled.winfo_height()
                    scrolled.configure(scrollregion=(0, 0, scrolled.winfo_width(),
                                                     max(tall, high)))
                    scrolls = tall > high + 1
                else:
                    first, last = scrolled.yview()
                    scrolls = last - first < 0.999
                if scrolls and not thumb.winfo_ismapped():
                    thumb.pack(side="right", fill="y", before=before)
                elif not scrolls and thumb.winfo_ismapped():
                    thumb.pack_forget()
                    scrolled.yview_moveto(0)
                draw = getattr(thumb, "_draw", None)
                if draw is not None:
                    draw(*map(str, scrolled.yview()))
                panes.append((scrolled, scrolls, self._areas.get(self._side_of(scrolled), None)
                              or scrolled))
            if panes:
                self._panes = panes
        except tk.TclError:                              # pragma: no cover - closed meanwhile
            pass
        finally:
            self._reflowing = False

    def _seen(self, seen: bool) -> None:
        """Seen, or not while it is laid out - where the system can (it is
        then only laid out where it already stands)."""
        try:
            self.window.attributes("-alpha", 1.0 if seen else 0.0)
        except tk.TclError:                              # pragma: no cover - seen throughout
            pass

    def _bar(self, card: Any) -> Any:
        """Its own bar, dragged to move it - one ✕ for both sides."""
        bar = tk.Frame(card, bg=PALETTE["bar"], padx=self._px(12), pady=self._px(7))
        bar.pack(side="top", fill="x")
        dot = tk.Canvas(bar, width=self._px(9), height=self._px(9), bg=PALETTE["bar"],
                        highlightthickness=0, bd=0)
        dot.create_oval(1, 1, self._px(9) - 1, self._px(9) - 1, fill=PALETTE["accent"],
                        outline="")
        dot.pack(side="left", padx=(0, self._px(8)))
        heading = tk.Label(bar, text=self.words("Before you use it"), bg=PALETTE["bar"],
                           fg=PALETTE["muted"], font=self._font(-1, "bold"))
        heading.pack(side="left")
        self._flat(bar, "✕", self.close, PALETTE["bar"]).pack(side="right")
        for one in (bar, dot, heading):
            one.bind("<ButtonPress-1>", self._grab)
            one.bind("<B1-Motion>", self._move)
        return bar

    def _foot(self, card: Any) -> Any:
        foot = tk.Frame(card, bg=PALETTE["bg"], padx=self._px(18), pady=self._px(10))
        foot.pack(side="bottom", fill="x")
        hint = "⌘ + / − to zoom" if sys.platform == "darwin" else "Ctrl + / − to zoom"
        tk.Label(foot, text=self.words(hint), bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=self._font(-2)).pack(side="left")
        done = tk.Label(foot, text=self.words("Done"), bg=PALETTE["accent"],
                        fg=PALETTE["on_accent"], font=self._font(-1, "bold"),
                        padx=self._px(14), pady=self._px(4), cursor="hand2")
        done.pack(side="right", padx=(0, self._px(10)))
        done.bind("<Button-1>", lambda _e: self.close())
        grip = tk.Label(card, text="◢", bg=PALETTE["bg"], fg=PALETTE["thumb_on"],
                        font=self._font(-1),
                        cursor="size_nw_se" if sys.platform.startswith("win")
                        else "bottom_right_corner")
        grip.place(relx=1.0, rely=1.0, anchor="se")
        grip.bind("<ButtonPress-1>", self._resize_start)
        grip.bind("<B1-Motion>", self._resize_move)
        grip.bind("<ButtonRelease-1>", self._resize_end)
        self._grip = grip
        return foot

    # -- sized by its edges, shared by the line between its sides ---------------------------
    def _edges(self, rim: Any) -> None:
        """Its edges and corners, each a strip of its rim to size it by."""
        self._strips = []
        corner = 3 * RIM
        for edge, cursor in EDGES.items():
            strip = tk.Frame(rim, bg=PALETTE["bg"], cursor=cursor)
            wide = corner if len(edge) == 2 or edge in "ns" else RIM
            high = corner if len(edge) == 2 or edge in "we" else RIM
            place: Dict[str, Any] = {"width": wide, "height": high}
            if edge in "we":
                place.update(height=-2 * corner, relheight=1, y=corner)
            if edge in "ns":
                place.update(width=-2 * corner, relwidth=1, x=corner)
            place.update(relx=1.0 if "e" in edge else 0.0, rely=1.0 if "s" in edge else 0.0,
                         anchor=("s" if "s" in edge else "n" if "n" in edge else "")
                         + ("e" if "e" in edge else "w" if "w" in edge else "") or "nw")
            if edge == "w":
                place.update(anchor="nw", rely=0.0)
            if edge == "e":
                place.update(anchor="ne", rely=0.0)
            if edge == "n":
                place.update(anchor="nw", relx=0.0)
            if edge == "s":
                place.update(anchor="sw", relx=0.0)
            strip.place(**place)
            strip.bind("<ButtonPress-1>", lambda e, edge=edge: self._edge_start(e, edge))
            strip.bind("<B1-Motion>", self._edge_move)
            strip.bind("<ButtonRelease-1>", self._resize_end)
            self._strips.append(strip)

    def _edge_start(self, event: Any, edge: str) -> None:
        window = self.window
        self._resizing = (event.x_root, event.y_root, window.winfo_width(),
                          window.winfo_height(), window.winfo_x(), window.winfo_y(), edge)

    def _edge_move(self, event: Any) -> None:
        """Sized by the edge taken: the right and bottom ones as by the
        corner; the left and top ones moving it as well."""
        if self._resizing is None or len(self._resizing) < 7:
            return
        x0, y0, width, height, left, top, edge = self._resizing
        dx, dy = event.x_root - x0, event.y_root - y0
        if "e" in edge:
            width = max(LEAST[0], width + dx)
        if "w" in edge:
            grown = max(LEAST[0], width - dx)
            left, width = left + width - grown, grown
        if "s" in edge:
            height = max(LEAST[1], min(height + dy, self.window.winfo_screenheight() - TOP))
        if "n" in edge:
            grown = max(LEAST[1], height - dy)
            top, height = top + height - grown, grown
        self._sized_once_a_frame(f"{width}x{height}+{left}+{top}")

    def _divider(self, middle: Any) -> None:
        """The line between the sides - dragged to share the width anew."""
        divider = tk.Frame(middle, bg=PALETTE["bg"], width=DIVIDER, cursor="sb_h_double_arrow")
        line = tk.Frame(divider, bg=PALETTE["edge"], width=1, cursor="sb_h_double_arrow")
        line.place(relx=0.5, rely=0.0, relheight=1.0, anchor="n")
        for one in (divider, line):
            one.bind("<ButtonPress-1>", self._split_start)
            one.bind("<B1-Motion>", self._split_move)
            one.bind("<ButtonRelease-1>", self._split_end)
        self._divider_frame = divider
        return divider

    def _split_start(self, event: Any) -> None:
        self._splitting = True
        self._split_move(event)

    def _split_move(self, event: Any) -> None:
        """The sides shared at the pointer, as it is dragged - their words
        following (`_reflow`)."""
        if not self._splitting:
            return
        left, right = self._areas.get("item"), self._areas.get("how")
        if left is None or right is None:
            return
        start = left.winfo_rootx()
        whole = right.winfo_rootx() + right.winfo_width() - start - DIVIDER
        if whole <= 2 * SIDE_LEAST:
            return
        at = min(max(event.x_root - start, SIDE_LEAST), whole - SIDE_LEAST)
        self._split = at / whole
        weight = 1000
        self._middle.columnconfigure(self._columns["item"], weight=max(1, int(weight * self._split)))
        self._middle.columnconfigure(self._columns["how"],
                                     weight=max(1, weight - int(weight * self._split)))

    def _split_end(self, _event: Any = None) -> None:
        """Let go: the share kept - for a zoom, and the next note."""
        self._splitting = False
        self._reflow_soon()
        self._tell_layout()

    def _tell_layout(self) -> None:
        """Its size and its sides' share, kept for the next note (`on_layout`)."""
        if self.on_layout is None or self.window is None:
            return
        try:
            self.window.update_idletasks()
            self.on_layout((self.window.winfo_width(), self.window.winfo_height()), self._split)
        except Exception:                                # pragma: no cover - this one keeps it
            pass

    def _resize_start(self, event: Any) -> None:
        self._resizing = (event.x_root, event.y_root, self.window.winfo_width(),
                          self.window.winfo_height())

    def _resize_move(self, event: Any) -> None:
        if self._resizing is None:
            return
        x, y, width, height = self._resizing
        width = max(LEAST[0], width + event.x_root - x)
        height = max(LEAST[1], min(height + event.y_root - y,
                                   self.window.winfo_screenheight() - TOP))
        self._sized_once_a_frame(f"{width}x{height}")

    def _resize_end(self, _event: Any = None) -> None:
        """Let go: its size and where it stands kept - for a zoom, which lays
        it out again; what it says has followed already (`_reflow`)."""
        if self._resizing is None:
            return
        self._resizing = None
        if getattr(self, "_geometry_due", None) is not None:
            self.window.after_cancel(self._geometry_due)
            self._give_geometry()                        # the last size asked for
        self.window.update_idletasks()
        self._size = (self.window.winfo_width(), self.window.winfo_height())
        self._place = (self.window.winfo_x() + self.window.winfo_width(), self.window.winfo_y())
        self._reflow()
        self._tell_layout()

    def _flat(self, parent: Any, text: str, act: Callable[[], None], bg: str,
              small: bool = False) -> Any:
        button = tk.Label(parent, text=text, bg=bg, fg=PALETTE["muted"],
                          font=self._font(-1, "bold") if small else self._font(1),
                          padx=self._px(7), pady=0, cursor="hand2")
        button.bind("<Enter>", lambda _e: button.configure(bg=PALETTE["hover"], fg=PALETTE["ink"]))
        button.bind("<Leave>", lambda _e: button.configure(bg=bg, fg=PALETTE["muted"]))
        button.bind("<Button-1>", lambda _e: act())
        return button

    def _zoomer(self, row: Any, side: str) -> None:
        """A side's own - and +, at the right of its first row."""
        for text, step in (("+", 1), ("−", -1)):
            self._flat(row, text, lambda s=step: self.zoom_by(s, side),
                       PALETTE["bg"]).pack(side="right")

    def _selectable(self, parent: Any, text: str, font: tuple, fg: str, wrap: int,
                    pack: Optional[Dict[str, Any]] = None, editable: bool = False) -> Any:
        """WORDS THAT CAN BE SELECTED AND COPIED (module docstring): a text
        that cannot be written in - or, `editable`, one that can - as wide
        as `wrap`, made as tall as its lines once it is laid out
        (`_fit_texts`); Ctrl+C and the right button's menu copy from it."""
        box = tk.Text(parent, wrap="word", font=font, bg=PALETTE["bg"], fg=fg, relief="flat",
                      bd=0, highlightthickness=0, padx=0, pady=0, cursor="xterm", height=1,
                      selectbackground=PALETTE["select"], selectforeground=PALETTE["ink"],
                      inactiveselectbackground=PALETTE["select"], exportselection=True,
                      takefocus=0, undo=editable, insertbackground=PALETTE["ink"])
        # Measured by Tk itself: a font object of Python's, let go on another
        # thread, would call Tk from there (and Tk ends the process).
        zero = int(parent.tk.call("font", "measure", font, "0"))
        box.configure(width=max(8, wrap // max(1, zero)))
        box.insert("1.0", text)
        if editable:
            box.edit_reset()                             # nothing to undo but the user's own
            box.edit_modified(False)
        else:
            box.configure(state="disabled")
        box.pack(**(pack or {"fill": "x"}))

        def everything(_event: Any = None) -> str:
            box.tag_add("sel", "1.0", "end-1c")
            return "break"

        def copy(_event: Any = None) -> str:
            if box.tag_ranges("sel"):
                self._copy(box.get("sel.first", "sel.last"))
            return "break"

        def menu(event: Any) -> str:
            self._menu(box, editable, copy, everything).tk_popup(event.x_root, event.y_root)
            return "break"
        modifier = "Command" if sys.platform == "darwin" else "Control"
        box.bind(f"<{modifier}-a>", everything)
        box.select_everything = everything               # what Ctrl+A does, by name
        box.bind("<<Copy>>", copy)
        box.bind("<Button-3>" if sys.platform != "darwin" else "<Button-2>", menu)
        for name in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            box.bind(name, self._on_wheel)
        self._texts.append(box)
        return box

    def _menu(self, box: Any, editable: bool, copy: Callable[[], Any],
              everything: Callable[[], Any]) -> Any:
        """The right button's menu for a text: Copy and Select all - and,
        where it can be written in, Cut and Paste."""
        menu = tk.Menu(self.window, tearoff=0, bg=PALETTE["bar"], fg=PALETTE["ink"],
                       activebackground=PALETTE["select"], activeforeground=PALETTE["ink"],
                       bd=0, font=self._font(-1))
        chosen = bool(box.tag_ranges("sel"))
        if editable:
            menu.add_command(label=self.words("Cut"), state="normal" if chosen else "disabled",
                             command=lambda: box.event_generate("<<Cut>>"))
        menu.add_command(label=self.words("Copy"), state="normal" if chosen else "disabled",
                         command=copy)
        if editable:
            menu.add_command(label=self.words("Paste"),
                             command=lambda: box.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label=self.words("Select all"), command=everything)
        return menu

    def _fit_texts(self) -> None:
        """Each text as tall as its lines, as they wrap - but words too long
        to measure, which scroll within their box whatever."""
        for box in self._texts:
            if box is self._long or box in getattr(self, "_unfitted", ()):
                continue
            try:
                lines = box.count("1.0", "end", "update", "displaylines")
                lines = lines[0] if isinstance(lines, tuple) else lines
                box.configure(height=max(1, int(lines or 1)))
            except (tk.TclError, TypeError, ValueError):
                continue

    def _item(self, body: Any, item: Dict[str, Any], wrap: int) -> Any:
        """WHAT WAS HANDED OVER, on the left: its kind - with Copy and its own
        zoom - whether it is on the clipboard already, its lines (To,
        Subject, When...), its words - whole, however long: the text they
        are in, which scrolls."""
        head = tk.Frame(body, bg=PALETTE["bg"])
        head.pack(fill="x", pady=(self._px(4), self._px(2)))
        tk.Label(head, text=self.words(KINDS.get(item.get("kind"), "Result")),
                 bg=PALETTE["bg"], fg=PALETTE["accent"], font=self._font(-1, "bold"),
                 anchor="w").pack(side="left")
        self._zoomer(head, "item")
        self._save_button = None
        copy = None
        if item.get("text"):
            # COPY ALL (the user, 2026-11-01): the whole of it, as it now is -
            # said on the button when done.
            copy = self._flat(head, self.words("Copy all"), lambda: None, PALETTE["bg"],
                              small=True)
            copy.bind("<Button-1>", lambda _e, b=copy: (self._copy_item(), self._flash(b)))
            copy.pack(side="right", padx=(0, self._px(8)))
        if (item.get("text") or item.get("parts")) and True:
            if item.get("editable", True) and self.on_save is not None:
                # Made now, shown beside Copy only while something is not saved.
                self._save_button = self._flat(head, self.words("Save"), self._save,
                                               PALETTE["accent"], small=True)
                self._save_button.configure(fg=PALETTE["on_accent"])
                self._save_button.bind("<Leave>", lambda _e: self._save_button.configure(
                    bg=PALETTE["accent"], fg=PALETTE["on_accent"]))
                self._save_after = copy                  # none for a form: its parts have theirs
                if self._unsaved:
                    self._show_save(True)
        self._head = head
        self._copied = tk.Label(body, text="", bg=PALETTE["bg"], fg=PALETTE["ticked"],
                                font=self._font(-1, "bold"), wraplength=wrap, justify="left",
                                anchor="w")
        if self.note.get("copied") and self._edited is not None:
            self._copied.configure(fg=PALETTE["accent"], text="✎  " + self.words(
                "Changed - press Copy to copy what you changed"))
            self._copied.pack(fill="x", pady=(0, self._px(4)))
        elif self.note.get("copied"):
            self._copied.configure(text="✓  " + self.words(
                "Already on your clipboard - paste it with Ctrl+V"
                if sys.platform != "darwin" else
                "Already on your clipboard - paste it with ⌘V"))
            self._copied.pack(fill="x", pady=(0, self._px(4)))
        meta = list(item.get("meta") or [])[:8]
        if meta:
            self._selectable(body, "\n".join(str(line)[:600] for line in meta),
                             self._font(-1), PALETTE["muted"], wrap)
        if item.get("parts"):
            self._parts(body, list(item["parts"]), wrap)
            return None
        if not item.get("text"):
            return None
        if meta:
            tk.Frame(body, bg=PALETTE["edge"], height=1).pack(fill="x", pady=self._px(8))
        text = self._edited if self._edited is not None else str(item["text"])
        words = self._selectable(body, text, self._font(), PALETTE["ink"], wrap,
                                 {"fill": "both", "expand": True},
                                 editable=bool(item.get("editable", True)))
        self._words = words
        words.bind("<<Modified>>", self._changed, add="+")
        if len(str(item["text"])) > MEASURED_MOST:
            self._long = words
            words.configure(height=1)
        return words

    def _flash(self, button: Any, said: str = "Copied") -> None:
        """WHAT A PRESS DID, SAID ON THE BUTTON PRESSED (the user, 2026-11-01:
        Copy gave no sign): "✓ Copied" in green a moment, then as it was."""
        try:
            words = button.cget("text")
            if words.startswith("✓"):
                return                                   # pressed again meanwhile
            button.configure(text="✓  " + self.words(said), fg=PALETTE["ticked"])

            def back() -> None:
                try:
                    button.configure(text=words, fg=PALETTE["muted"])
                except tk.TclError:
                    pass
            button.after(1500, back)
        except tk.TclError:
            pass

    def _copy_item(self) -> None:
        """What was handed over copied as it now is - changed or not."""
        box = getattr(self, "_words", None)
        text = box.get("1.0", "end-1c") if box is not None else \
            str((self.note.get("item") or {}).get("text") or "")
        self._copy(text)

    def _changed(self, _event: Any = None) -> None:
        """Written in: kept through a zoom or a resize, and said - what is
        on the clipboard, and what is saved, are not this."""
        box = getattr(self, "_words", None)
        if box is None or not box.edit_modified():
            return
        box.edit_modified(False)
        self._edited = box.get("1.0", "end-1c")
        self._unsaved = True
        self._show_save(True)
        self._say("✎  " + self.words("Changed - Save keeps it, Copy copies it"
                                     if self.on_save is not None else
                                     "Changed - press Copy to copy what you changed"),
                  PALETTE["accent"])

    def _save(self) -> None:
        """Kept where the work keeps it (`on_save`), and said."""
        box = getattr(self, "_words", None)
        parts = [one for one in getattr(self, "_part_boxes", []) if one.winfo_exists()]
        if (box is None and not parts) or self.on_save is None:
            return
        try:
            saved = self.on_save([one.get("1.0", "end-1c") for one in parts] if parts
                                 else box.get("1.0", "end-1c"))
        except Exception:                                # pragma: no cover - said not saved
            saved = False
        if saved is True:
            self._edited_saved = True
            self._unsaved = False
            self._show_save(False)
            self._say("✓  " + self.words("Saved - opened again, it is this"), PALETTE["ticked"])
        else:
            self._say("✕  " + self.words(saved if isinstance(saved, str) and saved
                                         else "Not saved - copy it, so it is not lost"),
                      PALETTE["accent"])

    def _parts(self, body: Any, parts: List[Dict[str, Any]], wrap: int) -> None:
        """A FORM'S PARTS, each in its own box under the label of the box it
        goes into, with its own Copy - written in as the rest, saved together."""
        self._part_boxes = []
        texts = self._edited if isinstance(self._edited, list) else \
            [str(one.get("text") or "") for one in parts]
        for n, part in enumerate(parts):
            row = tk.Frame(body, bg=PALETTE["bg"])
            row.pack(fill="x", pady=(self._px(10), self._px(2)))
            tk.Label(row, text=f"{n + 1}.  {part.get('field') or ''}", bg=PALETTE["bg"],
                     fg=PALETTE["accent"], font=self._font(-1, "bold"),
                     wraplength=wrap - self._px(70), justify="left",
                     anchor="w").pack(side="left")
            box = self._selectable(body, texts[n] if n < len(texts) else "", self._font(),
                                   PALETTE["ink"], wrap, editable=True)
            box.bind("<<Modified>>", lambda _e, b=box: self._part_changed(b), add="+")
            copy = self._flat(row, self.words("Copy"), lambda: None, PALETTE["bg"], small=True)
            copy.bind("<Button-1>", lambda _e, b=box, c=copy: (
                self._copy(b.get("1.0", "end-1c")), self._flash(c)))
            copy.pack(side="right")
            self._part_boxes.append(box)

    def _part_changed(self, box: Any) -> None:
        if not box.edit_modified():
            return
        box.edit_modified(False)
        self._edited = [one.get("1.0", "end-1c") for one in self._part_boxes]
        self._unsaved = True
        self._show_save(True)
        self._say("✎  " + self.words("Changed - Save keeps it, Copy copies it"
                                     if self.on_save is not None else
                                     "Changed - press Copy to copy what you changed"),
                  PALETTE["accent"])

    def _show_save(self, shown: bool) -> None:
        """Save beside Copy, or not."""
        button = self._save_button
        try:
            if button is None or not button.winfo_exists():
                return
            if shown and not button.winfo_ismapped():
                after = getattr(self, "_save_after", None)
                if after is not None and after.winfo_exists():
                    button.pack(side="right", padx=(0, self._px(6)), after=after)
                else:
                    button.pack(side="right", padx=(0, self._px(6)))
            elif not shown:
                button.pack_forget()
        except tk.TclError:
            pass

    def _say(self, words: str, colour: str) -> None:
        """The line under the item's kind - copied, changed, saved."""
        label = getattr(self, "_copied", None)
        try:
            if label is not None and label.winfo_exists():
                label.configure(text=words, fg=colour)
                if not label.winfo_ismapped():
                    label.pack(fill="x", pady=(0, self._px(4)), after=self._head)
        except tk.TclError:
            pass

    def _copy(self, text: str) -> None:
        """Copied again, said so."""
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(text)
        except tk.TclError:
            return
        label = getattr(self, "_copied", None)
        words = "✓  " + self.words("Copied - paste it with Ctrl+V" if sys.platform != "darwin"
                                   else "Copied - paste it with ⌘V")
        try:
            if label is not None and label.winfo_exists():
                label.configure(text=words, fg=PALETTE["ticked"])
                if not label.winfo_ismapped():
                    label.pack(fill="x", pady=(0, self._px(4)), after=self._head)
        except tk.TclError:
            pass

    def _body(self, body: Any, wrap: int) -> None:
        """How it is used, on the right: its own zoom, its title, how, what to check."""
        note = self.note
        row = tk.Frame(body, bg=PALETTE["bg"])
        row.pack(fill="x")
        self._zoomer(row, "how")
        self._selectable(body, str(note.get("title") or "")[:200], self._font(3, "bold"),
                         PALETTE["ink"], wrap,
                         {"fill": "x", "pady": (self._px(2), self._px(2))})
        if note.get("how"):
            self._heading(body, "How to use it")
            self._selectable(body, str(note["how"])[:1200], self._font(), PALETTE["ink"], wrap)
        checks = list(note.get("check") or [])[:10]
        if checks:
            self._heading(body, "Check before you use it")
            for n, one in enumerate(checks):
                self._check(body, n, str(one)[:300], wrap)

    def _heading(self, body: Any, text: str) -> None:
        tk.Label(body, text=self.words(text), bg=PALETTE["bg"], fg=PALETTE["accent"],
                 font=self._font(-1, "bold"), anchor="w").pack(fill="x",
                                                               pady=(self._px(12), self._px(4)))

    def _check(self, body: Any, n: int, text: str, wrap: int) -> None:
        """One thing to check: a box to tick - ticked by its box, so what it
        says can be selected - and what it says."""
        row = tk.Frame(body, bg=PALETTE["bg"])
        row.pack(fill="x", pady=(self._px(3), 0))
        box = tk.Label(row, bg=PALETTE["bg"], font=self._font(1), width=2, anchor="nw",
                       cursor="hand2")
        box.pack(side="left", anchor="n")
        said = self._selectable(row, text, self._font(), PALETTE["ink"], wrap - self._px(28),
                                {"side": "left", "fill": "x", "expand": True})

        def draw() -> None:
            on = self.ticked[n]
            box.configure(text="☑" if on else "☐",
                          fg=PALETTE["ticked"] if on else PALETTE["muted"])
            said.configure(fg=PALETTE["muted"] if on else PALETTE["ink"])

        def tick(_event: Any = None) -> None:
            self.ticked[n] = not self.ticked[n]
            draw()
        box.bind("<Button-1>", tick)
        draw()

    # -- scrolling ----------------------------------------------------------------------------
    def _scroller(self, scrolled: Any, thumb: Any, shown: int) -> None:
        """A thin bar of its own, beside what scrolls (a canvas, or a text):
        dragged, or pressed above or below its thumb."""
        least, inset, wide = self._px(28), self._px(3), self._px(10)

        def tall() -> int:
            return max(1, thumb.winfo_height() if thumb.winfo_ismapped() else shown)

        def draw(first: str, last: str) -> None:
            shown = tall()
            thumb.delete("all")
            top, bottom = float(first) * shown, float(last) * shown
            # Long enough to see and take, however long what it scrolls.
            if bottom - top < least:
                top = min(top, shown - least)
                bottom = top + least
            thumb.create_rectangle(inset, top + 2, wide - inset, bottom - 2,
                                   fill=PALETTE["thumb"], outline="", tags="thumb")
        scrolled.configure(yscrollcommand=draw)
        held = {"y": None, "at": 0.0}

        def press(event: Any) -> None:
            shown = tall()
            first, last = scrolled.yview()
            top = min(first * shown, shown - least)
            if top <= event.y <= max(last * shown, top + least):
                held["y"], held["at"] = event.y, first
                thumb.itemconfigure("thumb", fill=PALETTE["thumb_on"])
            else:
                scrolled.yview_scroll(-1 if event.y < top else 1, "pages")

        def drag(event: Any) -> None:
            if held["y"] is not None:
                scrolled.yview_moveto(held["at"] + (event.y - held["y"]) / tall())

        def release(_event: Any) -> None:
            held["y"] = None
            thumb.itemconfigure("thumb", fill=PALETTE["thumb"])
        thumb.bind("<ButtonPress-1>", press)
        thumb.bind("<B1-Motion>", drag)
        thumb.bind("<ButtonRelease-1>", release)
        thumb._draw = draw
        draw(*map(str, scrolled.yview()))

    def _wheel(self) -> None:
        """The wheel scrolls the side it is over; with Ctrl, zooms it."""
        for name in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.window.bind(name, self._on_wheel)

    def _on_wheel(self, event: Any) -> str:
        delta = event.delta if event.delta else (120 if event.num == 4 else -120)
        if event.state & 0x0004:                         # Ctrl held
            side = self._side_of(event.widget)
            if side:
                self.zoom_by(1 if delta > 0 else -1, side)
            return "break"
        for scrolled, scrolls, side in self._panes:
            if scrolls and self._within(event.widget, side):
                step = -1 if delta > 0 else 1
                scrolled.yview_scroll(step * max(1, abs(int(delta)) // 120) * 3, "units")
        return "break"


__all__ = ["HandedNote", "PALETTE", "ZOOMS", "ZOOM_START", "SIDES"]

"""THE PANELS OF THE AVATAR'S MENU, AND THE VINE OUT OF ITS SIDE (the user, 2026-11-04: "3个按钮取消，
to do 和collection 放在右键的框里显示。完整西瓜的时候，还是单击跳出chat, 收起到边框的时候会有一个西瓜藤连出来，
一个chat按钮，可以点击", "绿色底色（绿色的西瓜藤)").

To do and Collections are drawn here, by the toolkit of the avatar and the chat
bubble: there at once, where a page would first start a browser. What they list is
read off this thread, and shown when it comes. Chat is the chat bubble.

A PANEL DOES WHAT THE OVERVIEW DOES (the user, 2026-11-04): each item has its buttons
under it, there without a press, to delegate it, open what came of it or remove
it; its groups fold, its words zoom with Ctrl + and Ctrl -, and what is selected
is copied from the right button.
"""

from __future__ import annotations

import logging
import math
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from .chat_bubble import RIM as EDGE, LOOK, ScrollBar, beside, edge_strips, sized_to, work_area
from .on_top import (borderless, keys_on_hover, on_release, rounded, stay_on_top, stays_through_peek,
                     with_copy)

log = logging.getLogger(__name__)

#: How often the vine looks where the avatar is, in milliseconds - a safety only: it moves
#: as the avatar moves (`place`, told by its window).
FOLLOW_EVERY_MS = 1000
#: Where it waits, off every screen: withdrawn and shown again, a window can take the
#: keyboard from what the user is typing in.
PARKED = -32000
#: How long the part of the screen windows may use is taken as known, in seconds.
AREA_FOR_SECONDS = 5.0
#: The button's side; how far its middle stands past the avatar's window, level with the middle of
#: the leaves (the user, 2026-11-04: "chat应该是整体在4个叶子中间的感觉") and all of it outside that
#: window: on it, the pointer going to the button brought the head out. Where the
#: vine starts, tucked: this far in, between the leaves; how much shows then, and how much
#: more until it starts from its body. How far the vine bows out.
ICON, PAST, STEM_IN, TUCKED_OUT, HEAD_MORE, BOW = 44, 30, 26, 34, 50, 4
#: How often it grows, and in how many steps.
GROW_EVERY_MS, GROW_STEPS = 16, 10
#: Not a colour of the icons: what is see-through around them, where a window can be.
SEE_THROUGH = "#ff00fe"
KEY = (255, 0, 254)
#: The button's green, lit from above; lighter under the pointer; the rim; its mark. The vine.
GREEN_TOP, GREEN_LIT, GREEN_LOW = (128, 196, 92), (156, 216, 118), (66, 142, 62)
RIM, INK = (34, 92, 44), (246, 255, 240)
VINE, VINE_DARK = "#5aa845", "#2f6b2a"
PANEL_WIDTH, PANEL_TALLEST = 400, 520
#: The most the words grow or shrink, in points.
MOST_ZOOM = 8
#: The smallest its words go: at a higher floor most of them were there already, and
#: Ctrl - seemed to do nothing.
SMALLEST_WORDS = 5
#: Held down, Ctrl + draws as often as it can, and is written down this long after, in
#: milliseconds: written at each step, the file held every step up.
ZOOM_KEPT_AFTER_MS = 400
#: The wheel glides (the user, 2026-11-04: "上下滚轮滑动的时候会一卡一卡的"): pixels a notch,
#: how often a step, and how much of what is left at each.
WHEEL_PIXELS, ROLL_EVERY_MS, ROLL_SHARE = 72, 12, 0.3


def vine_path(box: tuple, side: str, area: tuple, grown: float = 1.0) -> Tuple[List[Tuple[int, int]],
                                                                               Tuple[int, int]]:
    """The vine of an avatar in a `side`: the four points of its curve and where the button's corner
    is. IT GROWS OUT OF THE AVATAR (the user, 2026-11-04: "藤应该是从西瓜上长出来") and the button is in
    the middle of it, between the leaves, going with it as its head comes out; on the screen
    whatever. `grown`, 0 to 1: how far it has come."""
    x, y, width, height = box
    left, top, right, bottom = area
    middle_x, middle_y = x + width // 2, y + height // 2
    half = ICON // 2
    if side == "right":
        seen, stem = right - x, (x + STEM_IN, middle_y)
        end, bow = (x - PAST, middle_y), (0, -BOW)
    elif side == "left":
        seen, stem = x + width - left, (x + width - STEM_IN, middle_y)
        end, bow = (x + width + PAST, middle_y), (0, -BOW)
    elif side == "top":
        seen, stem = y + height - top, (middle_x, y + height - STEM_IN)
        end, bow = (middle_x, y + height + PAST), (BOW, 0)
    else:
        seen, stem = bottom - y, (middle_x, y + STEM_IN)
        end, bow = (middle_x, y - PAST), (BOW, 0)
    end = (min(max(end[0], left + half), right - half), min(max(end[1], top + half), bottom - half))
    out = min(1.0, max(0.0, (seen - TUCKED_OUT) / HEAD_MORE))
    start = (int(stem[0] + (middle_x - stem[0]) * out), int(stem[1] + (middle_y - stem[1]) * out))
    tip = (int(start[0] + (end[0] - start[0]) * grown), int(start[1] + (end[1] - start[1]) * grown))

    def along(share: float) -> Tuple[int, int]:
        return (int(start[0] + (tip[0] - start[0]) * share + bow[0] * grown),
                int(start[1] + (tip[1] - start[1]) * share + bow[1] * grown))
    return [start, along(0.35), along(0.7), tip], (end[0] - half, end[1] - half)


_DEFERRED: List[Any] = []


def _deferred() -> Any:
    """Windows' way of moving several windows as one step, its types told once."""
    if not _DEFERRED:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.BeginDeferWindowPos.restype = wintypes.HANDLE
        user32.BeginDeferWindowPos.argtypes = [ctypes.c_int]
        user32.DeferWindowPos.restype = wintypes.HANDLE
        user32.DeferWindowPos.argtypes = [wintypes.HANDLE, wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.EndDeferWindowPos.argtypes = [wintypes.HANDLE]
        _DEFERRED.append(user32)
    return _DEFERRED[0]


def move_together(windows: Sequence[Any], spots: Sequence[Tuple[int, int]],
                  under: Any = None) -> None:
    """The icons moved as one step (the user, 2026-11-04: on a busy computer they followed in
    jerks): on Windows the system moves them all at once, at half the time of asking for each.
    `under`: the window they stay behind, in that same step ("时刻确保西瓜是在按钮前面的")."""
    if sys.platform.startswith("win"):
        try:
            user32 = _deferred()
            step = user32.BeginDeferWindowPos(len(windows))
            behind = int(under.wm_frame(), 16) if under is not None else None
            flags = 0x0211 if behind else 0x0215          # no size, no focus; order only then
            for window, (x, y) in zip(windows, spots):
                frame = int(window.wm_frame(), 16)
                step = step and user32.DeferWindowPos(step, frame, behind, x, y, 0, 0, flags)
                behind = frame if behind else None        # each behind the one before
            if step and user32.EndDeferWindowPos(step):
                return
        except (OSError, ValueError, AttributeError):
            log.debug("the icons could not be moved as one", exc_info=True)
    for window, (x, y) in zip(windows, spots):
        window.geometry(f"+{x}+{y}")
    if under is not None:
        under.lift()


def tendril(x: float, y: float, angle: float, size: float = 8.0) -> List[float]:
    """A tendril out of a stem at x, y: a spiral tightening as it goes."""
    middle = (x + size * math.cos(angle), y + size * math.sin(angle))
    out: List[float] = []
    for step in range(19):
        share = step / 18
        turn = angle + math.pi + share * 3.4 * math.pi
        out += [middle[0] + size * (1 - share * 0.85) * math.cos(turn),
                middle[1] + size * (1 - share * 0.85) * math.sin(turn)]
    return out


def melon_leaf(x: float, y: float, angle: float, size: float = 9.0) -> List[float]:
    """A melon's leaf round x, y, pointing along `angle`: five rounded lobes, deeply cut."""
    out: List[float] = []
    for step in range(40):
        turn = 2 * math.pi * step / 40
        reach = size * (0.5 + 0.5 * abs(math.cos(2.5 * turn)))
        out += [x + reach * math.cos(turn + angle), y + reach * math.sin(turn + angle)]
    return out


def _mark(draw: Any, scale: int) -> None:
    """A speech bubble, on a grid of 50."""
    def at(*points: float) -> List[float]:
        return [one * scale * ICON / 50 for one in points]
    draw.rounded_rectangle(at(12, 14, 38, 32), radius=7 * scale, fill=INK)
    draw.polygon(at(18, 29, 16, 39, 27, 31), fill=INK)
    for x in (19, 25, 31):
        draw.ellipse(at(x - 1.9, 21.1, x + 1.9, 24.9), fill=GREEN_LOW)


def face(lit: bool = False) -> Any:
    """The button as a picture: a green disc, lit from above, with a darker rim. Its edge
    is whole pixels - a window has no half see-through ones - so the rim hides the
    steps. None without Pillow."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    scale = 4
    side = ICON * scale
    top, low = (GREEN_LIT if lit else GREEN_TOP), GREEN_LOW
    ground = Image.new("RGB", (side, side))
    pen = ImageDraw.Draw(ground)
    for y in range(side):
        share = y / side
        pen.line((0, y, side, y), fill=tuple(int(a + (b - a) * share) for a, b in zip(top, low)))
    shine = Image.new("L", (side, side), 0)
    ImageDraw.Draw(shine).ellipse((side * .16, side * .05, side * .84, side * .46), fill=70)
    ground.paste((255, 255, 255), mask=shine)
    _mark(ImageDraw.Draw(ground), scale)
    disc = Image.new("L", (side, side), 0)
    ImageDraw.Draw(disc).ellipse((scale, scale, side - scale, side - scale), fill=255)
    rim = Image.new("L", (side, side), 0)
    ImageDraw.Draw(rim).ellipse((scale, scale, side - scale, side - scale), outline=255,
                                width=int(2.2 * scale))
    ground.paste(RIM, mask=rim)
    out = Image.new("RGB", (side, side), KEY)
    out.paste(ground, mask=disc)
    small = out.resize((ICON, ICON), Image.LANCZOS)
    edge = disc.resize((ICON, ICON), Image.LANCZOS).point(lambda one: 255 if one >= 128 else 0)
    whole = Image.new("RGB", (ICON, ICON), KEY)
    whole.paste(small, mask=edge)
    return whole


class _Icon:
    """The round button, a window of its own."""

    def __init__(self, root: Any, says: str, act: Callable[[], None], font: tuple) -> None:
        self.root, self.says, self.font = root, says, font
        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        ground = SEE_THROUGH
        try:
            self.window.attributes("-transparentcolor", SEE_THROUGH)
        except tk.TclError:              # no such thing here: a square, in the card's colour
            ground = LOOK["bg"]
        self.window.configure(bg=ground)
        self.canvas = tk.Canvas(self.window, width=ICON, height=ICON, bg=ground, bd=0,
                                highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        self.faces = self._faces(ground)
        self.shown = self.canvas.create_image(0, 0, anchor="nw", image=self.faces[0]) \
            if self.faces else self.canvas.create_oval(
                1, 1, ICON - 2, ICON - 2, fill="#80c45c", outline="#225c2c", width=2)
        self.tip: Any = None
        self.canvas.bind("<Enter>", self._over)
        self.canvas.bind("<Leave>", self._off)
        on_release(self.canvas, act)
        stay_on_top(self.window)
        stays_through_peek(self.window)

    def _faces(self, ground: str) -> List[Any]:
        try:
            from PIL import ImageTk
            out = []
            colour = tuple(int(ground[at:at + 2], 16) for at in (1, 3, 5))
            for lit in (False, True):
                picture = face(lit)
                if picture is None:
                    return []
                if colour != KEY:                # no see-through here: on the card's colour
                    pixels = picture.load()
                    for x in range(picture.width):
                        for y in range(picture.height):
                            if pixels[x, y] == KEY:
                                pixels[x, y] = colour
                out.append(ImageTk.PhotoImage(picture, master=self.window))
            return out
        except Exception:
            log.debug("the icons could not be drawn", exc_info=True)
            return []

    def _lit(self, on: bool) -> None:
        if self.faces:
            self.canvas.itemconfigure(self.shown, image=self.faces[1 if on else 0])

    def _over(self, _event: Any = None) -> None:
        self._lit(True)
        tip = self.tip = tk.Toplevel(self.root)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=self.says, bg=LOOK["bg"], fg=LOOK["ink"], padx=6, pady=2,
                 font=(self.font[0], max(8, self.font[1] - 2))).pack()
        tip.update_idletasks()
        tip.geometry(f"+{self.window.winfo_x() + ICON // 2 - tip.winfo_reqwidth() // 2}"
                     f"+{self.window.winfo_y() - tip.winfo_reqheight() - 2}")

    def _off(self, _event: Any = None) -> None:
        self._lit(False)
        tip, self.tip = self.tip, None
        if tip is not None:
            tip.destroy()


class ChatVine:
    """A vine out of the avatar in a side, a green button at its end: the chat, for an avatar
    that cannot be clicked for it. `side()` says the side it is in, "" when it is not;
    `front`, its window, stays in front of both."""

    def __init__(self, root: Any, says: str, act: Callable[[], None], avatar: Callable[[], tuple],
                 font: tuple, side: Callable[[], str], front: Any = None) -> None:
        self.avatar, self.side, self.front = avatar, side, front
        self.icon = _Icon(root, says, act, font)
        self.stem = tk.Toplevel(root)
        self.stem.overrideredirect(True)
        self.stem.attributes("-topmost", True)
        ground = SEE_THROUGH
        try:
            self.stem.attributes("-transparentcolor", SEE_THROUGH)
        except tk.TclError:              # no such thing here: on the card's colour
            ground = LOOK["bg"]
        self.stem.configure(bg=ground)
        self.canvas = tk.Canvas(self.stem, bg=ground, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        stay_on_top(self.stem)
        stays_through_peek(self.stem)
        self.out = False
        self.grown = 0.0
        self.at: Optional[Tuple[int, int]] = None
        self._growing = False
        self._drawn: Any = None
        self._area: Optional[tuple] = None
        self._area_at = 0.0
        self._park()
        self.follow()

    def around(self, box: tuple) -> tuple:
        """`box` and the button, as one (x, y, width, height)."""
        if not self.out or self.at is None:
            return box
        left, top = min(box[0], self.at[0]), min(box[1], self.at[1])
        return (left, top, max(box[0] + box[2], self.at[0] + ICON) - left,
                max(box[1] + box[3], self.at[1] + ICON) - top)

    def _screen(self, box: tuple) -> tuple:
        """The part of the screen the avatar is on that windows may use: asked of the
        system now and then - and at once when it has left that part, for another screen."""
        now = time.monotonic()
        x, y = box[0] + box[2] // 2, box[1] + box[3] // 2
        known = self._area
        if known is None or now - self._area_at > AREA_FOR_SECONDS or not (
                known[0] - box[2] <= x < known[2] + box[2]
                and known[1] - box[3] <= y < known[3] + box[3]):
            window = self.stem
            self._area = work_area(window, box) or (0, 0, window.winfo_screenwidth(),
                                                    window.winfo_screenheight())
            self._area_at = now
        return self._area

    def _park(self) -> None:
        self.out, self.grown, self.at, self._drawn = False, 0.0, None, None
        move_together([self.icon.window, self.stem], [(PARKED, PARKED)] * 2)

    def place(self, _event: Any = None) -> None:
        """Where the avatar is now: told as it moves."""
        try:
            side = self.side()
            if not side:
                if self.out:
                    self._park()
                return
            if not self.out:
                self.out, self.grown = True, 0.0
            if self.grown < 1.0 and not self._growing:
                self._growing = True
                self.stem.after(GROW_EVERY_MS, self._grow)
            self._draw(side)
        except tk.TclError:              # the application is closing
            pass

    def _grow(self) -> None:
        self._growing = False
        if self.out:
            self.grown = min(1.0, self.grown + 1 / GROW_STEPS)
            self.place()

    def _draw(self, side: str) -> None:
        box = self.avatar()
        points, self.at = vine_path(box, side, self._screen(box), self.grown)
        full = self.grown >= 1.0
        if (points, self.at, full) == self._drawn:
            return
        self._drawn = (points, self.at, full)
        pad = 20
        left = min(one[0] for one in points) - pad
        top = min(one[1] for one in points) - pad
        width = max(one[0] for one in points) + pad - left
        height = max(one[1] for one in points) + pad - top
        self.stem.geometry(f"{width}x{height}")
        self.canvas.delete("all")
        flat = [value for x, y in points for value in (x - left, y - top)]
        self.canvas.create_line(*flat, smooth=True, width=3, fill=VINE, capstyle="round")
        # AS A MELON'S VINE GROWS (the user, 2026-11-04): a thin stem, a curling tendril on one side
        # and a lobed leaf on the other, once it has come that far.
        if self.grown > 0.5:
            across = math.atan2(flat[7] - flat[1], flat[6] - flat[0]) + math.pi / 2
            x, y = flat[2], flat[3]
            self.canvas.create_line(*tendril(x, y, across), smooth=True, width=2, fill=VINE,
                                    capstyle="round")
            x, y = flat[4], flat[5]
            self.canvas.create_line(x, y, x - 5 * math.cos(across), y - 5 * math.sin(across),
                                    width=2, fill=VINE)
            self.canvas.create_polygon(*melon_leaf(x - 12 * math.cos(across), y - 12 * math.sin(across),
                                                   across + math.pi),
                                       fill=VINE, outline=VINE_DARK)
        spot = self.at if full else (PARKED, PARKED)
        move_together([self.icon.window, self.stem], [spot, (left, top)], self.front)

    def follow(self) -> None:
        self.place()
        try:
            self.stem.after(FOLLOW_EVERY_MS, self.follow)
        except tk.TclError:              # the application is closing
            pass


class QuickPanel:
    """One list, beside the avatar. `read()` gives its sections, off Tk's thread:
    [{"name": "", "rows": [{"id", "title", "detail", "note", "url", "actions":
    [{"key", "label", ...}]}]}]; `post(fn)` runs fn on Tk's thread; `act(action,
    done)` does an action of a row and calls `done` when it has. A section with a
    "group" is one an item can be moved to, under that name; "keep": shown when empty.
    `adds`: what can be added, as on the Overview (the user, 2026-11-04): {"label",
    "fields": [hints], "keys": [names], "path", "fixed": {...}}, posted as `act` posts."""

    def __init__(self, root: Any, title: str, read: Callable[[], List[Dict[str, Any]]],
                 post: Callable[[Callable[[], None]], None], words: Callable[[str], str],
                 font: tuple, empty: str, searches: bool = False,
                 act: Optional[Callable[[Dict[str, Any], Callable[[], None]], None]] = None,
                 zoom: int = 0, on_zoom: Optional[Callable[[int], None]] = None,
                 adds: Sequence[Dict[str, Any]] = (), size: Optional[tuple] = None,
                 on_size: Optional[Callable[[tuple], None]] = None) -> None:
        self.root, self.title, self.read, self.post, self.words = root, title, read, post, words
        self.font, self.empty, self.searches = font, empty, searches
        self.act, self.zoom, self.on_zoom = act, int(zoom or 0), on_zoom
        self.window: Any = None
        self.opened = False
        self.sections: List[Dict[str, Any]] = []
        #: The groups folded stay while the application runs.
        self.folded: Set[str] = set()
        #: Those folded while searching (the user, 2026-11-04): a search opens every group,
        #: so that nothing found is hidden, and they fold again from there.
        self.folded_searching: Set[str] = set()
        self.opening: Set[str] = set()
        #: The row whose Remove was pressed: "Remove it? Yes / No", as on the Overview.
        self.asking = ""
        #: The row whose Move was pressed: the groups it can go to.
        self.moving = ""
        self.adds = list(adds)
        self.form: Any = None
        self.size, self.on_size = (tuple(size) if size else None), on_size
        self._resizing: Optional[tuple] = None
        self._asked_geometry = ""
        self._geometry_due: Any = None
        self._zoom_due: Any = None
        self._save_due: Any = None
        self._rolling = 0.0
        self._roll_due: Any = None
        self.faces: List[Any] = []
        self._fonts: Dict[str, Tuple[Any, int]] = {}
        self._drag = (0, 0)
        self._marks = 0

    @property
    def _built(self) -> bool:
        try:
            return self.window is not None and bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    @property
    def is_open(self) -> bool:
        return self.opened and self._built

    def toggle(self, near: tuple) -> None:
        if self.is_open:
            self.close()
        else:
            self.show(near)

    def close(self) -> None:
        """Out of sight, and kept: opened again it is only moved back."""
        if self._save_due is not None:
            self._save_zoom()
        self.opened = False
        changed = bool(self.asking or self.moving)
        self.asking = self.moving = ""
        self._rolling = 0.0
        if not self._built:
            return
        try:
            self._close_form()
            if self.box is not None and self.box.get():
                self.box.delete(0, "end")
                self.box.hinted()
                changed = True
            if changed:                      # now, not when it is opened again
                self._draw()
            self.window.geometry(f"+{PARKED}+{PARKED}")
        except tk.TclError:
            pass

    # -- its words' size ---------------------------------------------------------------
    def _sized(self, more: int, weight: str = "normal") -> Any:
        name = f"{more}{weight}"
        if name not in self._fonts:
            font = tkfont.Font(root=self.root, family=self.font[0], weight=weight,
                               size=max(SMALLEST_WORDS, self.font[1] + more + self.zoom))
            self._fonts[name] = (font, more)
        return self._fonts[name][0]

    def zoom_by(self, step: int) -> None:
        self.zoom_to(self.zoom + step)

    def zoom_to(self, zoom: int) -> None:
        self.zoom = max(-MOST_ZOOM, min(MOST_ZOOM, int(zoom)))
        if not self.is_open:
            self._draw_zoom()
            self._save_zoom()
            return
        # ONCE A FRAME, HOWEVER FAST THE KEY REPEATS (the user, 2026-11-04: "ctrl +- 变动也一卡一卡的").
        if self._zoom_due is None:
            self._zoom_due = self.window.after_idle(self._draw_zoom)
        if self._save_due is not None:
            self.window.after_cancel(self._save_due)
        self._save_due = self.window.after(ZOOM_KEPT_AFTER_MS, self._save_zoom)

    def _draw_zoom(self) -> None:
        self._zoom_due = None
        top = self.list.index("@0,0") if self.is_open else ""
        for font, more in self._fonts.values():
            font.configure(size=max(SMALLEST_WORDS, self.font[1] + more + self.zoom))
        if top:
            self.list.yview(top)             # the line at its top stays there

    def _save_zoom(self) -> None:
        self._save_due = None
        if self.on_zoom is not None:
            self.on_zoom(self.zoom)

    def _keys(self, window: Any) -> None:
        modifier = "Command" if sys.platform == "darwin" else "Control"
        for keys, step in ((("plus", "equal", "KP_Add"), 1), (("minus", "KP_Subtract"), -1)):
            for key in keys:
                window.bind(f"<{modifier}-{key}>", lambda _e, s=step: (self.zoom_by(s), "break")[1])
        for key in ("0", "KP_0"):
            window.bind(f"<{modifier}-{key}>", lambda _e: (self.zoom_to(0), "break")[1])
        # WITH THE WHEEL TOO: it goes by where the pointer is, whatever has the keyboard.
        for one in (self.list, window):
            one.bind(f"<{modifier}-MouseWheel>", self._wheel)
            one.bind(f"<{modifier}-Button-4>", lambda _e: (self.zoom_by(1), "break")[1])
            one.bind(f"<{modifier}-Button-5>", lambda _e: (self.zoom_by(-1), "break")[1])
        # Over a button in the list too: the window under the pointer has the wheel.
        for one in (self.list, window):
            one.bind("<MouseWheel>", self._roll)
        window.bind("<Button-1>", self._clicked, add="+")
        window.bind("<Escape>", lambda _e: self.close())
        keys_on_hover(window)

    def _roll(self, event: Any) -> str:
        notches = event.delta / (1 if sys.platform == "darwin" else 120)
        self._rolling -= notches * WHEEL_PIXELS
        if self._roll_due is None:
            self._roll_step()
        return "break"

    def _roll_step(self) -> None:
        self._roll_due = None
        left = self._rolling
        if abs(left) < 1 or not self.is_open:
            self._rolling = 0.0
            return
        step = int(left * ROLL_SHARE) or (1 if left > 0 else -1)
        try:
            was = self.list.yview()
            self.list.yview_scroll(step, "pixels")
            self._rolling = 0.0 if self.list.yview() == was else left - step   # at its end
            self._roll_due = self.list.after(ROLL_EVERY_MS, self._roll_step)
        except tk.TclError:
            self._rolling = 0.0

    # -- sized by its edges, as the chat is -----------------------------------------------
    def _edge_start(self, event: Any, edge: str) -> None:
        window = self.window
        self._resizing = (event.x_root, event.y_root, window.winfo_width(), window.winfo_height(),
                          window.winfo_x(), window.winfo_y(), edge)

    def _edge_move(self, event: Any) -> None:
        if self._resizing is None:
            return
        width, height, left, top = sized_to(self._resizing, event.x_root, event.y_root)
        self._asked_geometry = f"{width}x{height}+{left}+{top}"
        if self._geometry_due is None:       # once a frame, as the chat does
            self._geometry_due = self.window.after_idle(self._give_geometry)

    def _give_geometry(self) -> None:
        self._geometry_due = None
        if self.is_open and self._asked_geometry:
            self.window.geometry(self._asked_geometry)

    def _edge_end(self, _event: Any = None) -> None:
        """Let go: its size kept, for the next time it opens."""
        if self._resizing is None:
            return
        self._resizing = None
        if self._geometry_due is not None:
            self.window.after_cancel(self._geometry_due)
            self._give_geometry()
        self.window.update_idletasks()
        self.size = (self.window.winfo_width(), self.window.winfo_height())
        if self.on_size is not None:
            self.on_size(self.size)

    def _wheel(self, event: Any) -> str:
        self.zoom_by(1 if event.delta > 0 else -1)
        return "break"

    def _take_keys(self) -> None:
        """The keyboard to it, to the search box as in the chat: pressing an icon does
        not leave it there, and Ctrl + and Ctrl - went to the program behind."""
        if not self.is_open:
            return
        try:
            self.window.focus_force()
            if self.box is not None:
                self.box.focus_set()
        except (tk.TclError, AttributeError):
            pass

    def _clicked(self, _event: Any = None) -> None:
        """A press anywhere in it gives it the keyboard, where it last was."""
        try:
            self.window.focus_lastfor().focus_force()
        except (tk.TclError, AttributeError):
            pass

    def _field(self, parent: Any, hint: str) -> Any:
        """A box to write in, its hint in it until something is written."""
        box = tk.Entry(parent, bg=LOOK["field"], fg=LOOK["ink"], insertbackground=LOOK["ink"],
                       relief="flat", bd=0, highlightthickness=0, font=self._sized(-1))
        said = tk.Label(box, text=hint, bg=LOOK["field"], fg=LOOK["muted"], font=self._sized(-1))
        said.place(x=4, rely=0.5, anchor="w")

        def hinted(_event: Any = None) -> None:
            if box.get():
                said.place_forget()
            else:
                said.place(x=4, rely=0.5, anchor="w")
        box.hinted = hinted
        box.bind("<KeyRelease>", hinted, add="+")
        for one in (box, said):
            one.bind("<Button-1>", lambda _e: box.focus_force(), add="+")
        return box

    def _word_button(self, parent: Any, words: str, press: Any, primary: bool = False) -> Any:
        one = tk.Label(parent, text=words, bg=LOOK["primary"] if primary else LOOK["field"],
                       fg=LOOK["on_primary"] if primary else LOOK["ink"], padx=9, pady=3,
                       cursor="hand2", font=self._sized(-2, "bold" if primary else "normal"))
        on_release(one, press)
        return one

    # -- adding, as on the Overview ------------------------------------------------------
    def _open_form(self, hints: Sequence[str], go: Callable[[List[str]], None], yes: str) -> None:
        """Boxes over the list; the first must be filled. Enter is yes, Esc is no."""
        self._close_form()
        form = self.form = tk.Frame(self.card, bg=LOOK["bg"])
        form.pack(fill="x", pady=(8, 0), before=self.body)
        boxes = [self._field(form, hint) for hint in hints]

        def yes_pressed(_event: Any = None) -> str:
            said = [box.get().strip() for box in boxes]
            if said[0]:
                self._close_form()
                go(said)
            return "break"

        def no_pressed(_event: Any = None) -> str:
            self._close_form()
            return "break"
        for box in boxes:
            box.pack(fill="x", ipady=4, pady=(0, 4))
            box.bind("<Return>", yes_pressed)
            box.bind("<Escape>", no_pressed)
        row = tk.Frame(form, bg=LOOK["bg"])
        row.pack(fill="x")
        self._word_button(row, yes, yes_pressed, True).pack(side="left", padx=(0, 4))
        self._word_button(row, self.words("Cancel"), no_pressed).pack(side="left")
        boxes[0].focus_force()

    def _close_form(self) -> None:
        form, self.form = self.form, None
        if form is not None:
            try:
                form.destroy()
                self._take_keys()
            except tk.TclError:
                pass

    def _add(self, add: Dict[str, Any]) -> None:
        def go(said: List[str]) -> None:
            body = dict(add.get("fixed") or {}, **dict(zip(add["keys"], said)))
            self._do({"id": "panel"}, {"key": add["keys"][0], "path": add["path"], "body": body,
                                       "changes": True})
        self._open_form(add["fields"], go, self.words("Add"))

    # -- moved from group to group ---------------------------------------------------------
    def _groups(self) -> List[Tuple[str, str]]:
        """What can be moved to: (shown, its name)."""
        return [(str(one.get("name") or ""), str(one["group"])) for one in self.sections
                if one.get("group")]

    def _start_moving(self, ident: str) -> None:
        self.moving = ident
        self._draw()

    def _move_row(self, row: Dict[str, Any], action: Dict[str, Any], to: str) -> None:
        self.moving = ""
        self._do(row, dict(action, path="/api/lists/groups/move", changes=True, body={
            "list": action["list"], "item": row.get("id"), "to": to}))

    def _move_to_a_new_one(self, row: Dict[str, Any], action: Dict[str, Any]) -> None:
        self._open_form([self.words("The new group's name")],
                        lambda said: self._move_row(row, action, said[0]), self.words("Move"))

    # -- drawn -------------------------------------------------------------------------
    def prepare(self) -> None:
        """MADE AHEAD, OUT OF SIGHT (the user, 2026-11-04: "点击...弹出来都要卡一下"): made at the
        press, its window and its list were some 150 ms before anything showed."""
        if not self._built:
            self._build()
        self._read_it(quiet=True)

    def show(self, near: tuple) -> None:
        if not self._built:
            self._build()
        self.opened = True
        self._fit(near)
        self.list.yview_moveto(0)
        self._take_keys()                    # opened by a press: Ctrl + works at once
        self.window.after(80, self._take_keys)   # and once that press is over
        self._read_it(quiet=bool(self.sections))

    def _build(self) -> None:
        window = tk.Toplevel(self.root)
        self.window = window
        borderless(window)
        window.attributes("-topmost", True)
        window.configure(bg=LOOK["outline"])
        self.form = None
        # ITS EDGES ARE A RIM OF THEIR OWN, as the chat's: placed in a frame with padding,
        # they stood inside it, over the words and the ✕ (the user, 2026-11-04).
        rim = tk.Frame(window, bg=LOOK["bg"])
        rim.pack(fill="both", expand=True, padx=1, pady=1)
        card = self.card = tk.Frame(rim, bg=LOOK["bg"], padx=14 - EDGE, pady=10 - EDGE)
        card.pack(fill="both", expand=True, padx=EDGE, pady=EDGE)
        head = tk.Frame(card, bg=LOOK["bg"])
        head.pack(fill="x")
        dot = tk.Canvas(head, width=10, height=10, bg=LOOK["bg"], highlightthickness=0, bd=0)
        dot.create_oval(1, 1, 9, 9, fill=LOOK["dot"], outline="")
        dot.pack(side="left", padx=(0, 7))
        name = tk.Label(head, text=self.title, bg=LOOK["bg"], fg=LOOK["ink"],
                        font=self._sized(0, "bold"))
        name.pack(side="left")
        close = tk.Label(head, text="✕", bg=LOOK["bg"], fg=LOOK["muted"], cursor="hand2", padx=4,
                         font=self._sized(0))
        close.pack(side="right")
        on_release(close, self.close)
        for one in (head, dot, name):
            one.bind("<ButtonPress-1>", self._grab)
            one.bind("<B1-Motion>", self._move)
        self.box: Any = None
        if self.searches:
            self.box = self._field(card, self.words("Search"))
            self.box.pack(fill="x", pady=(8, 0), ipady=4)
            self.box.bind("<KeyRelease>", lambda _e: self._draw(), add="+")
        if self.adds:
            tools = tk.Frame(card, bg=LOOK["bg"])
            tools.pack(fill="x", pady=(8, 0))
            for add in self.adds:
                self._word_button(tools, add["label"], lambda one=add: self._add(one)).pack(
                    side="left", padx=(0, 4))
        body = self.body = tk.Frame(card, bg=LOOK["bg"])
        body.pack(fill="both", expand=True, pady=(8, 0))
        self.bar = ScrollBar(body)
        self.list = tk.Text(body, wrap="word", bg=LOOK["bg"], fg=LOOK["ink"], relief="flat", bd=0,
                            highlightthickness=0, padx=0, pady=0, cursor="arrow", width=1, height=1,
                            font=self._sized(-1), selectbackground=LOOK["select"],
                            inactiveselectbackground=LOOK["select"], selectforeground=LOOK["ink"],
                            yscrollcommand=self.bar.shown, takefocus=0, spacing3=2)
        self.bar.scrolls(self.list)
        self.list.pack(side="left", fill="both", expand=True)
        self.list.tag_configure("group", foreground=LOOK["muted"], font=self._sized(-1, "bold"),
                                spacing1=8)
        self.list.tag_configure("title", font=self._sized(-1, "bold"), spacing1=6)
        self.list.tag_configure("muted", foreground=LOOK["muted"], font=self._sized(-2))
        self.list.bind("<Button-1>", lambda _e: self.list.focus_force(), add="+")
        with_copy(self.list, self.words("Copy"))
        self._keys(window)
        # Its corners no wider than the margin: a larger one stood over the ✕.
        edge_strips(rim, self._edge_start, self._edge_move, self._edge_end, corner=2 * EDGE)
        self._write([("group", self.words("Loading…"), None)])
        window.geometry(f"{PANEL_WIDTH}x{PANEL_TALLEST}+{PARKED}+{PARKED}")
        rounded(window)
        stay_on_top(window)

    def _fit(self, near: tuple) -> None:
        window = self.window
        window.update_idletasks()
        size = self.size or (PANEL_WIDTH, PANEL_TALLEST)
        area = work_area(window, near) or (0, 0, window.winfo_screenwidth(),
                                           window.winfo_screenheight())
        # What fits that screen, as it is: kept from a larger one, or a small one.
        size = (min(size[0], area[2] - area[0] - 20), min(size[1], area[3] - area[1] - 20))
        x, y = beside(near, size, (window.winfo_screenwidth(), window.winfo_screenheight()), area)
        window.geometry(f"{size[0]}x{size[1]}+{x}+{y}")
        # Moved now: Tk moves a window when it is idle, and the system telling it where the
        # window was on taking the keyboard, next, wiped this out - it stayed out of sight.
        window.update_idletasks()

    def _grab(self, event: Any) -> None:
        self._drag = (event.x_root - self.window.winfo_x(), event.y_root - self.window.winfo_y())

    def _move(self, event: Any) -> None:
        self.window.geometry(f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    # -- what it lists -----------------------------------------------------------------
    def _read_it(self, quiet: bool = False) -> None:
        """`quiet`: what is drawn stays if the list is as it was."""
        def look() -> None:
            try:
                found = list(self.read())
            except Exception:
                log.warning("what a panel lists could not be read", exc_info=True)
                found = []
            self.post(lambda: self._got(found, quiet))
        threading.Thread(target=look, name="quick-panel", daemon=True).start()

    def _got(self, sections: List[Dict[str, Any]], quiet: bool = False) -> None:
        same = quiet and sections == self.sections
        self.sections = sections
        if self._built and not same:
            self._draw()

    def _shown_sections(self) -> List[Dict[str, Any]]:
        asked = (self.box.get() if self.box is not None else "").lower().split()
        out = []
        for section in self.sections:
            rows = [row for row in section["rows"] if all(
                word in " ".join(str(row.get(key) or "") for key in
                                 ("title", "detail", "note", "url")).lower() for word in asked)]
            if rows or (section.get("keep") and not asked):
                out.append(dict(section, rows=rows))
        return out

    def _searching(self) -> bool:
        return bool(self.box is not None and self.box.get().strip())

    def _fold(self, name: str) -> None:
        folds = self.folded_searching if self._searching() else self.folded
        folds.symmetric_difference_update({name})
        self._draw()

    def _ask(self, ident: str) -> None:
        self.asking = ident
        self._draw()

    def _do(self, row: Dict[str, Any], action: Dict[str, Any]) -> None:
        """Pressed: it says so, and takes no second press until it is done. What
        changed the list is read again."""
        key = f'{row.get("id")}:{action["key"]}'
        if self.act is None or key in self.opening:
            return
        self.opening.add(key)
        self.asking = ""
        self._draw()

        def done() -> None:
            self.opening.discard(key)
            if self.is_open:
                if action.get("changes"):
                    self._read_it()
                else:
                    self._draw()
        self.act(action, lambda: self.post(done))

    def _buttons_of(self, row: Dict[str, Any]) -> List[Tuple[str, Any, str]]:
        """A row's buttons: (words, press, look)."""
        ident = str(row.get("id") or "")
        out: List[Tuple[str, Any, str]] = []
        for action in row["actions"]:
            if action.get("moves") and f'{ident}:{action["key"]}' in self.opening:
                out.append((self.words("Moving..."), None, "said"))
            elif action.get("moves") and self.moving == ident:
                out.append((self.words("Move to…"), None, "said"))
                for shown, name in self._groups():
                    if name != row.get("group"):
                        out.append((shown, lambda to=name, a=action: self._move_row(row, a, to), ""))
                out.append((self.words("+ Add group"),
                            lambda a=action: self._move_to_a_new_one(row, a), ""))
                out.append((self.words("Cancel"), lambda: self._start_moving(""), ""))
            elif action.get("moves"):
                out.append((action["label"], lambda: self._start_moving(ident), ""))
            elif action.get("confirm") and self.asking == ident:
                out.append((self.words("Remove it?"), None, "said"))
                out.append((self.words("Yes"), lambda a=action: self._do(row, a), "danger"))
                out.append((self.words("No"), lambda: self._ask(""), ""))
            elif f'{ident}:{action["key"]}' in self.opening:
                out.append((self.words("Opening..."), None, "said"))
            elif action.get("confirm"):
                out.append((action["label"], lambda: self._ask(ident), ""))
            else:
                out.append((action["label"], lambda a=action: self._do(row, a),
                            "primary" if not out else ""))
        return out

    def _draw(self) -> None:
        parts: List[Tuple[str, str, Any]] = []
        searching = self._searching()
        if not searching:
            self.folded_searching.clear()
        for section in self._shown_sections():
            name = str(section.get("name") or "")
            folded = name in (self.folded_searching if searching else self.folded)
            if name:
                parts.append(("group", f'{"▸" if folded else "▾"} {name} ({len(section["rows"])})',
                              lambda one=name: self._fold(one)))
            if folded:
                continue
            for row in section["rows"]:
                parts.append(("title", str(row.get("title") or ""), None))
                for key in ("detail", "note"):
                    if row.get(key):
                        parts.append(("muted", str(row[key]), None))
                if row.get("actions"):
                    parts.append(("buttons", "", self._buttons_of(
                        dict(row, group=section.get("group") or ""))))
        self._write(parts or [("muted", self.empty, None)])

    def _face(self, words: str, press: Any, look: str) -> Any:
        """A button in the list: a window in the text."""
        ground, ink = {"primary": (LOOK["primary"], LOOK["on_primary"]),
                       "danger": (LOOK["slow"], "#2a0f0c"), "said": (LOOK["bg"], LOOK["muted"]),
                       }.get(look, (LOOK["field"], LOOK["ink"]))
        one = tk.Label(self.list, text=words, bg=ground, fg=ink, padx=9, pady=3,
                       font=self._sized(-2, "bold" if look == "primary" else "normal"),
                       cursor="hand2" if press else "arrow")
        if press is not None:
            on_release(one, press)
        self.faces.append(one)
        return one

    def _write(self, parts: List[Tuple[str, str, Any]]) -> None:
        said = self.list
        # WHERE IT WAS, BY THE LINE AT ITS TOP (the user, 2026-11-04: Remove sent the list
        # back to the top): a share of its height means another line once the
        # buttons in it are laid out again.
        top = said.index("@0,0")
        said.configure(state="normal")
        said.delete("1.0", "end")
        for one in self.faces:
            one.destroy()
        self.faces = []
        for tag, text, press in parts:
            if tag == "buttons":
                for words, go, look in press:
                    said.window_create("end", window=self._face(words, go, look), padx=2, pady=3)
                said.insert("end", "\n")
                continue
            tags = tuple(tag.split())
            if press is not None:
                self._marks += 1
                mark = f"press{self._marks}"
                said.tag_bind(mark, "<ButtonRelease-1>", lambda _e, go=press: go())
                said.tag_bind(mark, "<Enter>", lambda _e: said.configure(cursor="hand2"))
                said.tag_bind(mark, "<Leave>", lambda _e: said.configure(cursor="arrow"))
                tags += (mark,)
            said.insert("end", text + "\n", tags)
        said.configure(state="disabled")
        said.update_idletasks()
        said.yview(top)


__all__ = ["ChatVine", "QuickPanel", "vine_path"]

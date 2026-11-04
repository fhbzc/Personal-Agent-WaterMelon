"""A CONVERSATION IN A BUBBLE BESIDE THE AVATAR (the user, 2026-11-01: "a
single click is not the chat page in the browser - a bubble the user types
into").

Pressed once, the avatar opens this: the same dark card as its notification
bubbles, beside it, with what was said in it - selectable - and a box to
write in: Enter sends, Shift+Enter starts a new line. What is sent goes to
the same conversation the chat page has (`agent.handle_message`), on a
thread of its own - the avatar is never held up while the agent thinks -
and the answer comes back on Tk's thread (`post`). "Open the full chat"
opens the page on that conversation. Pressed again while it is open, the
avatar puts the writing back in it.

MINIMISED TO A BAR, CLOSED FOR A NEW ONE (the user, 2026-11-03: "要有最小化
功能，最小化之后是一个bar, expand之后内容不会丢，点击关闭之后再打开就是一个新的
session"): "—" (or Escape) leaves its line alone, a bar - what was said and
what is half written kept; "▢", a double click on the bar or the avatar
pressed opens it again as it was. An answer that comes meanwhile lights the
bar's dot. ✕ closes it, and the next one opened is a new conversation.

ITS SIZE IS THE USER'S (the user, 2026-11-01): dragged by any edge or
corner, as a window is - then what was said fills it and scrolls - and kept
for the next time it opens (`on_size`).
"""

from __future__ import annotations

import logging
import re
import sys
import threading
import time
import tkinter as tk
from typing import Any, Callable, Dict, Optional
from .on_top import on_release

log = logging.getLogger(__name__)

#: What was said long ago, folded away while the bubble is sized.
FOLDED = "folded-while-sized"

#: Its colours - the quiet notification bubble's.
LOOK = {"outline": "#3a4152", "bg": "#1d212b", "ink": "#e9ecf3", "muted": "#9aa3b5",
        "dot": "#7c9cff", "field": "#262b37", "you": "#b9c6ff", "primary": "#5b7cfa",
        "primary_hover": "#7190ff", "on_primary": "#ffffff", "select": "#3a4d7a",
        "fast": "#8ab4ff", "slow": "#ff8f85"}

#: How wide it is at the popups' first size, and how tall what was said may
#: grow before it scrolls.
WIDTH, SAID_TALLEST = 380, 340

#: The sizes its words can be zoomed to (as the note beside what was handed
#: over), and the one it starts at.
ZOOMS = (0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.6, 1.8, 2.0, 2.25)
ZOOM_START = 2

#: The smallest it is dragged to; its rim, where its edges are taken; and the
#: pointer over each edge and corner.
LEAST, RIM = (300, 160), 5
EDGES = {"w": "sb_h_double_arrow", "e": "sb_h_double_arrow", "n": "sb_v_double_arrow",
         "s": "sb_v_double_arrow", "nw": "top_left_corner", "ne": "top_right_corner",
         "sw": "bottom_left_corner", "se": "bottom_right_corner"}


#: How far from the avatar a bubble stands, and from the screen's edges.
GAP, MARGIN = 8, 10


def beside(avatar: tuple, size: tuple, screen: tuple,
           area: Optional[tuple] = None) -> tuple:
    """WHERE A BUBBLE GOES: beside the avatar (the user, 2026-11-01: "beside
    the logo") - at its left, level with its middle; at its right where the
    left has no room - and on the screen, whatever. `avatar` is (x, y,
    width, height); `size` the bubble's; `screen` the screen's; `area` the
    part of it windows may use (left, top, right, bottom - `work_area`):
    above the taskbar, wherever and however tall it is - else the screen
    less a taskbar's guessed room."""
    ax, ay, aw, ah = avatar
    width, height = size
    sw, sh = screen
    left, top, right, bottom = area or (0, 0, sw, sh - 40)
    x = ax - width - GAP
    if x < left + MARGIN:
        x = ax + aw + GAP                            # no room at its left
    x = max(left + MARGIN, min(x, right - width - MARGIN))
    y = ay + ah // 2 - height // 2
    y = max(top + MARGIN, min(y, bottom - height - MARGIN))
    return int(x), int(y)


def work_area(widget: Any) -> Optional[tuple]:
    """The part of the screen windows may use - (left, top, right, bottom),
    the taskbar's room left out - where the system says it (Windows; on a
    Mac the menu bar's and the Dock's, 2026-11-02); None elsewhere, and
    `beside` guesses."""
    if sys.platform == "darwin":
        try:
            import AppKit
            screen = AppKit.NSScreen.screens()[0]
            whole, seen = screen.frame(), screen.visibleFrame()
            # The system counts from the bottom; Tk from the top.
            top = int(whole.size.height - (seen.origin.y + seen.size.height))
            bottom = int(whole.size.height - seen.origin.y)
            left, right = int(seen.origin.x), int(seen.origin.x + seen.size.width)
            if right > left and bottom > top:
                return (left, top, right, bottom)
        except Exception:                                # pragma: no cover - guessed then
            pass
        return None
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            if rect.right > rect.left and rect.bottom > rect.top:
                return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:                                    # pragma: no cover - guessed then
        pass
    return None


#: The most attached to one message (agent/chat_files.py); how large a picture
#: attached is seen over the box.
MOST_ATTACHED = 10
THUMB = 72
#: How often the reply so far is drawn, in seconds.
LIVE_EVERY = 0.12
#: The most lines the box where the user writes grows to.
BOX_TALLEST = 6


class ChatBubble:
    """One conversation bubble. `send(text, conversation_id)` asks the agent
    (on another thread) and returns its answer as `agent.handle_message`
    does - with a third argument, the ids of the files attached, when there
    are any; `post(fn)` runs fn on Tk's thread; `open_full(conversation_id)`
    opens the chat page; `words` the application's words in the user's
    language; `on_conversation` is told the conversation once it has one;
    `files` keeps what is attached (agent/chat_files.py: dragged onto it -
    where a window can take that, ui/file_drop.py - pasted, or "+" beside the
    box; the user, 2026-11-04)."""

    def __init__(self, root: Any, *, name: str, family: str, size: int,
                 send: Callable[[str, Optional[str]], Any], post: Callable[[Callable], None],
                 open_full: Callable[[Optional[str]], None],
                 words: Callable[[str], str] = lambda text: text,
                 conversation_id: Optional[str] = None,
                 on_conversation: Optional[Callable[[str], None]] = None,
                 kept_size: Any = None,
                 on_size: Optional[Callable[[tuple], None]] = None,
                 zoom: Any = ZOOM_START,
                 on_zoom: Optional[Callable[[int], None]] = None,
                 files: Any = None, streams: bool = False, turn: Any = None) -> None:
        self.root, self.name = root, name
        #: Two speeds (agent/two_speed.py): `turn(text, conversation, files,
        #: mentions, on_done)` gives the quick reply; None when it is off.
        self.turn = turn
        self._folds = 0
        #: `send` takes on_text=, and the reply is shown as it is written.
        self.streams = streams
        self.files = files
        #: What is attached, until it is sent; and what is still being read.
        self.attached: list = []
        self._adding = 0
        #: Sent in this conversation, to @; @ed in what is being written; and
        #: the list that comes up, while it is up: (where the @ is, files, which).
        self.sent_files: list = []
        self.mentions: list = []
        self._mention: Optional[list] = None
        self._thumbs: list = []
        self.family, self.size = family, max(8, int(size or 10))
        #: ZOOMED BY CTRL + AND CTRL - (the user, 2026-11-02: "chat bubble 也要
        #: 支持ctrl + - 缩放，并且作为setting记录"): the step it is at, kept by
        #: `on_zoom` for the next time it opens. Its fonts are named in Tk - a
        #: zoom sizes them; nothing is made again.
        try:
            self.zoom = min(max(int(zoom), 0), len(ZOOMS) - 1)
        except (TypeError, ValueError):
            self.zoom = ZOOM_START
        self.on_zoom = on_zoom
        self._fonts: Dict[str, tuple] = {}
        self.send, self.post, self.open_full, self.words = send, post, open_full, words
        self.conversation_id = conversation_id
        self.on_conversation = on_conversation
        self.window: Any = None
        self.waiting = False
        #: The size the user left it at - kept, given back (`on_size`).
        self._size: Optional[tuple] = None
        try:
            if kept_size and len(kept_size) == 2:
                self._size = (max(LEAST[0], int(kept_size[0])), max(LEAST[1], int(kept_size[1])))
        except (TypeError, ValueError):
            self._size = None
        self.on_size = on_size
        self._resizing: Optional[tuple] = None
        #: Minimised to its line (`minimize`): what was packed, and where it was.
        self.minimized = False
        self._packed: list = []
        self._restore: Optional[tuple] = None

    @property
    def is_open(self) -> bool:
        try:
            return self.window is not None and bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def _points(self, more: int) -> int:
        return max(7, int(round((self.size + more) * ZOOMS[self.zoom])))

    def _font(self, more: int = 0, weight: str = "normal", underline: bool = False) -> str:
        """A font of it, named in Tk, at its zoom - a zoom sizes it."""
        name = f"chatbubble{id(self)}_{more}_{weight}_{int(underline)}"
        if name not in self._fonts:
            self.root.tk.call("font", "create", name, "-family", self.family,
                              "-size", self._points(more), "-weight", weight,
                              "-underline", int(underline))
            self._fonts[name] = (more,)
        return name

    def zoom_by(self, step: int) -> None:
        self.zoom_to(self.zoom + step)

    def zoom_to(self, zoom: int) -> None:
        """Its words at another size - the box, what was said, its line -
        kept for the next time (`on_zoom`)."""
        zoom = min(max(int(zoom), 0), len(ZOOMS) - 1)
        if zoom == self.zoom or not self.is_open:
            return
        self.zoom = zoom
        for name, (more,) in self._fonts.items():
            try:
                self.root.tk.call("font", "configure", name, "-size", self._points(more))
            except tk.TclError:
                continue
        if self.on_zoom is not None:
            self.on_zoom(zoom)
        self._draw_fold()
        self._fit()

    def _forget_fonts(self) -> None:
        for name in self._fonts:
            try:
                self.root.tk.call("font", "delete", name)
            except tk.TclError:
                pass
        self._fonts = {}

    # -- the window --------------------------------------------------------------------------
    def show(self, near: Optional[tuple] = None) -> None:
        """Opened - or, open already, the writing put back in it."""
        if self.is_open:
            self.window.deiconify()
            self.window.lift()
            if self.minimized:
                self.expand()
            self._focus()
            return
        self.minimized = False
        width = self._size[0] if self._size else WIDTH + (self.size - 9) * 20
        window = tk.Toplevel(self.root)
        self.window = window
        from .on_top import borderless
        borderless(window)                   # written in, on a Mac too
        window.attributes("-topmost", True)
        window.configure(bg=LOOK["outline"])
        # Its outline, its rim - where its edges are taken - then the card.
        rim = tk.Frame(window, bg=LOOK["bg"])
        rim.pack(fill="both", expand=True, padx=1, pady=1)
        card = tk.Frame(rim, bg=LOOK["bg"], padx=14 - RIM, pady=12 - RIM)
        card.pack(fill="both", expand=True, padx=RIM, pady=RIM)
        # Its line: who it is with; the full chat; ✕.
        head = tk.Frame(card, bg=LOOK["bg"])
        head.pack(fill="x")
        dot = tk.Canvas(head, width=10, height=10, bg=LOOK["bg"], highlightthickness=0, bd=0)
        self._dot = (dot, dot.create_oval(1, 1, 9, 9, fill=LOOK["dot"], outline=""))
        dot.pack(side="left", padx=(0, 7))
        title = tk.Label(head, text=self.name, bg=LOOK["bg"], fg=LOOK["ink"],
                         font=self._font(0, "bold"))
        title.pack(side="left")
        close = tk.Label(head, text="✕", bg=LOOK["bg"], fg=LOOK["muted"], font=self._font(1),
                         cursor="hand2", padx=4)
        close.pack(side="right")
        on_release(close, self.close)
        # Minimise and open again: DRAWN, as large as the ✕ beside them (the
        # user, 2026-11-03: "最大化的正方形按钮...和叉叉不一样大" - the font's
        # square was a small one).
        self.fold = tk.Canvas(head, bg=LOOK["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.fold.pack(side="right", padx=(0, 2))
        self._close_label = close
        on_release(self.fold, self._fold_or_open)
        self._draw_fold()
        full = tk.Label(head, text=self.words("Open the full chat"), bg=LOOK["bg"],
                        fg=LOOK["muted"], font=self._font(-2, underline=True), cursor="hand2")
        full.pack(side="right", padx=(0, 10))
        on_release(full, self._open_full)
        for one in (head, dot, title):
            one.bind("<ButtonPress-1>", self._grab)
            one.bind("<B1-Motion>", self._move)
            one.bind("<Double-Button-1>", lambda _e: self.expand())
        self.card = card
        # What was said: selectable, scrolled once it is tall - with a slim
        # bar on its right while it does not all fit (the user, 2026-11-03).
        self.said_box = tk.Frame(card, bg=LOOK["bg"])
        self.bar = ScrollBar(self.said_box)
        self.said = tk.Text(self.said_box, wrap="word", font=self._font(), bg=LOOK["bg"], fg=LOOK["ink"],
                            relief="flat", bd=0, highlightthickness=0, padx=0, pady=0,
                            height=1, width=1, cursor="xterm", takefocus=0,
                            selectbackground=LOOK["select"], selectforeground=LOOK["ink"],
                            inactiveselectbackground=LOOK["select"])
        self.said.tag_configure("you", foreground=LOOK["you"], font=self._font(-1, "bold"),
                                spacing1=8)
        self.said.tag_configure("agent", foreground=LOOK["muted"], font=self._font(-1, "bold"),
                                spacing1=8)
        self.said.tag_configure("waiting", foreground=LOOK["muted"], font=self._font(-1))
        self.said.tag_configure("fast", foreground=LOOK["fast"])
        self.said.configure(state="disabled", yscrollcommand=self.bar.shown)
        self.bar.scrolls(self.said)
        self.said.pack(side="left", fill="both", expand=True)
        self.said_box.pack(fill="both", expand=True, pady=(8, 0))
        self.said_box.pack_forget()                      # until something is said
        self.hint = tk.Label(card, text=self.words(
            "Ask anything, or tell it what to do - Enter sends, Shift+Enter a new line"),
            bg=LOOK["bg"], fg=LOOK["muted"], font=self._font(-2), wraplength=width - 30,
            justify="left", anchor="w")
        self.hint.pack(fill="x", pady=(8, 0))
        # Where the user writes.
        row = tk.Frame(card, bg=LOOK["field"], padx=10, pady=8)
        row.pack(fill="x", pady=(10, 0))
        self.row = row
        self.files_row = tk.Frame(card, bg=LOOK["bg"])   # while there are files
        self.mention_row = tk.Frame(card, bg=LOOK["field"])   # while @ is being written
        self.clip = None
        if self.files is not None:
            from .file_drop import accept_files
            accept_files(window, self._dropped, self._file_over)
            self.clip = tk.Label(row, text="+", bg=LOOK["field"], fg=LOOK["muted"],
                                 font=self._font(2, "bold"), padx=2, pady=0, cursor="hand2")
            self.clip.pack(side="left", padx=(0, 6))
            on_release(self.clip, self._pick)
        self.box = tk.Text(row, wrap="word", font=self._font(), bg=LOOK["field"], fg=LOOK["ink"],
                           insertbackground=LOOK["ink"], relief="flat", bd=0,
                           highlightthickness=0, height=1, width=1, undo=True,
                           selectbackground=LOOK["select"])
        self.box.pack(side="left", fill="both", expand=True)
        self.button = tk.Label(row, text=self.words("Send"), bg=LOOK["primary"],
                               fg=LOOK["on_primary"], font=self._font(-1, "bold"),
                               padx=12, pady=4, cursor="hand2")
        self.button.pack(side="right", padx=(8, 0))
        self.button.bind("<Enter>", lambda _e: self.button.configure(bg=LOOK["primary_hover"]))
        self.button.bind("<Leave>", lambda _e: self.button.configure(bg=LOOK["primary"]))
        on_release(self.button, self.submit)
        self.box.bind("<Return>", self._enter)
        self.box.bind("<KP_Enter>", self._enter)
        self.box.bind("<<Paste>>", self._pasted, add="+")
        self.box.bind("<KeyRelease>", self._watch_mentions, add="+")
        self.box.bind("<KeyRelease>", self._fit_box, add="+")
        for key, step in (("<Down>", 1), ("<Up>", -1)):
            self.box.bind(key, lambda _e, s=step: self._move_mention(s))
        self.box.bind("<Tab>", self._pick_mention)
        self.box.bind("<Escape>", self._drop_mention)
        window.bind("<Escape>", lambda _e: self.minimize())
        # Ctrl + / Ctrl - / Ctrl 0 (Command on a Mac): its words zoomed.
        modifier = "Command" if sys.platform == "darwin" else "Control"
        for keys, step in ((("plus", "equal", "KP_Add"), 1), (("minus", "KP_Subtract"), -1)):
            for key in keys:
                window.bind(f"<{modifier}-{key}>", lambda _e, s=step: (self.zoom_by(s), "break")[1])
        for key in ("0", "KP_0"):
            window.bind(f"<{modifier}-{key}>", lambda _e: (self.zoom_to(ZOOM_START), "break")[1])
        self._edges(rim)
        window.update_idletasks()
        self.width = width
        if self._size:
            # As the user left it: what was said fills it.
            self.said_box.pack(fill="both", expand=True, pady=(8, 0), before=row)
            self.hint.pack_forget()
            self.hint.pack(fill="x", pady=(8, 0), before=self.said_box)
            window.geometry(f"{self._size[0]}x{self._size[1]}")
        else:
            window.geometry(f"{width}x{window.winfo_reqheight()}")
        self._place(near)
        self._rounded()
        from .on_top import stay_on_top
        stay_on_top(window)                  # on a Mac, asked before it was laid out: lost
        self._focus()

    # -- minimised to a bar --------------------------------------------------------------
    def _draw_fold(self) -> None:
        """"—" while it is open, a square while it is a bar - each as wide as
        the ✕ beside it is, at its zoom."""
        fold = getattr(self, "fold", None)
        if fold is None:
            return
        try:
            font = self._font(1)
            glyph = int(self.root.tk.call("font", "measure", font, "✕"))
            line = int(self.root.tk.call("font", "metrics", font, "-linespace"))
        except tk.TclError:
            return
        side = max(7, int(round(glyph * 0.72)))
        width, height = glyph + 8, line
        fold.configure(width=width, height=height)
        fold.delete("all")
        left, top = (width - side) / 2, (height - side) / 2
        thick = max(1.0, side / 9)
        if self.minimized:
            fold.create_rectangle(left, top, left + side, top + side, outline=LOOK["muted"],
                                  width=thick)
        else:
            middle = top + side / 2
            fold.create_line(left, middle, left + side, middle, fill=LOOK["muted"], width=thick)

    def _fold_or_open(self) -> None:
        if self.minimized:
            self.expand()
        else:
            self.minimize()

    def minimize(self) -> None:
        """Only its line left - a bar, its bottom where the bubble's was; what
        was said and what is half written stay as they are."""
        if not self.is_open or self.minimized:
            return
        self._hide_mentions()
        window = self.window
        window.update_idletasks()
        width, height = window.winfo_width(), window.winfo_height()
        x, y = window.winfo_x(), window.winfo_y()
        self._restore = (width, height, x, y)
        self._packed = []
        for widget in self.card.pack_slaves():
            if widget in (self.said_box, self.hint, self.files_row, self.row):
                info = dict(widget.pack_info())
                info.pop("in", None)
                self._packed.append((widget, info))
                widget.pack_forget()
        self.minimized = True
        self._draw_fold()
        window.update_idletasks()
        bar = window.winfo_reqheight()
        window.geometry(f"{width}x{bar}+{x}+{y + height - bar}")

    def expand(self) -> None:
        """As it was before it was minimised - and what came meanwhile in it."""
        if not self.is_open or not self.minimized:
            return
        self.minimized = False
        for widget, info in self._packed:
            widget.pack(**info)
        self._packed = []
        # An answer that came while it was a bar: what was said shown.
        if self.said.get("1.0", "end-1c") and not self.said_box.winfo_ismapped():
            self.hint.pack_forget()
            self.said_box.pack(fill="both", expand=True, pady=(8, 0), before=self._under_said())
        self._draw_files()
        self._draw_fold()
        dot, oval = self._dot
        dot.itemconfigure(oval, fill=LOOK["dot"])
        if self._restore is not None:
            width, height, _x, _y = self._restore
            window = self.window
            window.update_idletasks()
            bottom = window.winfo_y() + window.winfo_height()
            if self._size:
                window.geometry(f"{width}x{height}+{window.winfo_x()}+{max(10, bottom - height)}")
            else:
                self._bottom = bottom
                self._fit()
        try:
            self.said.see("end")
        except tk.TclError:
            pass
        self._focus()

    def close(self) -> None:
        """Closed: the next one opened is a new conversation (the user,
        2026-11-03)."""
        self.conversation_id = None
        self.minimized = False
        self._let_go(self.attached)
        self.attached, self.sent_files, self.mentions = [], [], []
        self._mention = None
        if self.window is not None:
            from .file_drop import let_go
            let_go(self.window)
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None
            self._forget_fonts()

    def _focus(self) -> None:
        try:
            self.window.focus_force()
            self.box.focus_set()
        except tk.TclError:
            pass

    def _place(self, near: Optional[tuple]) -> None:
        """Beside the avatar (`beside`) - `near` is where it is: (x, y,
        width, height) - on the screen."""
        window = self.window
        window.update_idletasks()
        width, height = window.winfo_width(), window.winfo_height()
        screen = (window.winfo_screenwidth(), window.winfo_screenheight())
        if near and len(near) == 2:
            near = (near[0], near[1], 0, 0)
        avatar = near or (screen[0] - 140, screen[1] - 240, 100, 100)
        x, y = beside(avatar, (width, height), screen, work_area(window))
        window.geometry(f"+{x}+{y}")
        # Its bottom where it was put: grown, it grows upwards.
        self._bottom = y + height

    def _rounded(self) -> None:
        if not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            handle = ctypes.windll.user32.GetParent(self.window.winfo_id()) or \
                self.window.winfo_id()
            prefer = ctypes.c_int(2)                     # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, 33, ctypes.byref(prefer),
                                                       ctypes.sizeof(prefer))
        except Exception:                                # pragma: no cover - square, then
            pass

    def _edges(self, rim: Any) -> None:
        """Its edges and corners, each a strip of its rim to size it by."""
        corner = 3 * RIM
        for edge, cursor in EDGES.items():
            strip = tk.Frame(rim, bg=LOOK["bg"], cursor=cursor)
            place: Dict[str, Any] = {"width": corner, "height": corner}
            if edge in ("w", "e"):
                place = {"width": RIM, "height": -2 * corner, "relheight": 1, "y": corner}
            elif edge in ("n", "s"):
                place = {"height": RIM, "width": -2 * corner, "relwidth": 1, "x": corner}
            place.update(relx=1.0 if "e" in edge else 0.0,
                         rely=1.0 if edge in ("s", "sw", "se") else 0.0,
                         anchor=("s" if edge in ("s", "sw", "se") else "n")
                         + ("e" if "e" in edge else "w"))
            if edge in ("w", "e"):
                place["rely"] = 0.0
                place["anchor"] = "ne" if edge == "e" else "nw"
            strip.place(**place)
            strip.bind("<ButtonPress-1>", lambda e, edge=edge: self._edge_start(e, edge))
            strip.bind("<B1-Motion>", self._edge_move)
            strip.bind("<ButtonRelease-1>", self._edge_end)
            strip.lift()

    def _edge_start(self, event: Any, edge: str) -> None:
        if self.minimized:
            return                                       # a bar is not sized
        window = self.window
        self._resizing = (event.x_root, event.y_root, window.winfo_width(),
                          window.winfo_height(), window.winfo_x(), window.winfo_y(), edge)
        self._fold_what_cannot_show()
        if not self._size:
            # From now on what was said fills what the user makes it.
            self.said.configure(height=1)
            if not self.said_box.winfo_ismapped():
                self.hint.pack_forget()
                self.said_box.pack(fill="both", expand=True, pady=(8, 0),
                                   before=self.box.master)
                self.hint.pack(fill="x", pady=(8, 0), before=self.said_box)

    def _fold_what_cannot_show(self) -> None:
        """WHILE IT IS SIZED, what was said long before is folded away - what
        could not be seen even were it as tall as the screen (the user,
        2026-11-03: sizing it went in jerks). Each new width had every line
        of the conversation measured again; measured, a large bubble's sizes
        came 37 a second, with the old turns folded 65. Unfolded when it is
        let go, and scrolled to its end as it was."""
        said = self.said
        try:
            line = max(1, int(said.tk.call("font", "metrics", said.cget("font"), "-linespace")))
            keep = self.window.winfo_screenheight() // line + 5
            said.tag_configure(FOLDED, elide=True)
            start = said.index(f"end - {keep} display lines linestart")
            if said.compare(start, ">", "1.0"):
                said.tag_add(FOLDED, "1.0", start)
                said.see("end")
        except tk.TclError:
            pass

    def _edge_move(self, event: Any) -> None:
        if self._resizing is None:
            return
        x0, y0, width, height, left, top, edge = self._resizing
        dx, dy = event.x_root - x0, event.y_root - y0
        if "e" in edge:
            width = max(LEAST[0], width + dx)
        if "w" in edge:
            grown = max(LEAST[0], width - dx)
            left, width = left + width - grown, grown
        if "s" in edge:
            height = max(LEAST[1], height + dy)
        if "n" in edge:
            grown = max(LEAST[1], height - dy)
            top, height = top + height - grown, grown
        # Given at most once a frame: every pointer move given at once drew
        # the whole of it again and again - it flickered. AND AS SOON AS THE
        # LAST ONE IS DRAWN, not a fixed wait more (the user, 2026-11-03: it
        # went in jerks): Tk draws when it is idle, and so is this given -
        # after the drawing of the one before. Measured, a large bubble's
        # every size took 28 ms to draw; with 16 ms waited too, 30 a second.
        self._asked_geometry = f"{width}x{height}+{left}+{top}"
        self._size = (width, height)
        if getattr(self, "_geometry_due", None) is None:
            self._geometry_due = self.window.after_idle(self._give_geometry)

    def _give_geometry(self) -> None:
        self._geometry_due = None
        if self.is_open and getattr(self, "_asked_geometry", None):
            self.window.geometry(self._asked_geometry)

    def _edge_end(self, _event: Any = None) -> None:
        """Let go: its size kept, for the next time it opens."""
        if self._resizing is None:
            return
        self._resizing = None
        if getattr(self, "_geometry_due", None) is not None:
            self.window.after_cancel(self._geometry_due)
            self._give_geometry()                        # the last size asked for
        self.said.tag_remove(FOLDED, "1.0", "end")     # all of it again
        self.window.update_idletasks()
        self._size = (self.window.winfo_width(), self.window.winfo_height())
        self.width = self._size[0]
        self._bottom = self.window.winfo_y() + self._size[1]
        try:
            self.said.see("end")
        except tk.TclError:
            pass
        if self.on_size is not None:
            self.on_size(self._size)

    def _grab(self, event: Any) -> None:
        self._drag = (event.x_root - self.window.winfo_x(), event.y_root - self.window.winfo_y())

    def _move(self, event: Any) -> None:
        """Moved by its line - at most once a frame (it flickered)."""
        x, y = event.x_root - self._drag[0], event.y_root - self._drag[1]
        self._asked_geometry = f"+{x}+{y}"
        if getattr(self, "_geometry_due", None) is None:
            self._geometry_due = self.window.after_idle(self._give_geometry)

    def _open_full(self) -> None:
        self.open_full(self.conversation_id)
        self.close()

    # -- talking ---------------------------------------------------------------------------
    def _enter(self, event: Any) -> str:
        """Enter sends; Shift+Enter is a new line."""
        if event.state & 0x0001:                         # Shift held
            return ""
        if self._mention:
            return self._pick_mention()
        self.submit()
        return "break"

    def submit(self) -> None:
        """What was written, sent - on a thread of its own; the answer
        written in when it comes."""
        if self.waiting or not self.is_open:
            return
        text = self.box.get("1.0", "end-1c").strip()
        if self._adding or not (text or self.attached):
            return
        self.box.delete("1.0", "end")
        self.box.configure(height=1)
        sent, self.attached = self.attached, []
        if sent:
            self._draw_files()
        self._hide_mentions()
        ids = [one["id"] for one in sent]
        mentioned = [one["id"] for one in self.mentions if f"@{one['name']}" in text]
        self.mentions = []
        self.sent_files.extend(sent)
        self._write(self.words("You"), "you",
                    "\n".join([*([text] if text else []), *[f"[{one['name']}]" for one in sent]]))
        self._write("", "waiting", self.words("Thinking…"))
        self.waiting = True
        self.button.configure(bg=LOOK["field"], fg=LOOK["muted"])

        fold: Dict[str, Any] = {}

        def ask() -> None:
            try:
                if callable(self.turn) and self.turn(None) is True:
                    said = self.turn(text, self.conversation_id, ids, mentioned,
                                     lambda done: self.post(lambda: self._fold_done(fold, done))) or {}
                    fold.update(turn=said.get("turn"))
                elif self.streams:
                    said = self.send(text, self.conversation_id, ids, mentioned,
                                     on_text=self._told_live()) or {}
                else:
                    said = (self.send(text, self.conversation_id, ids, mentioned) if mentioned
                            else self.send(text, self.conversation_id, ids) if ids
                            else self.send(text, self.conversation_id)) or {}
            except Exception as exc:                     # said in the bubble
                log.warning("the bubble's message could not be answered", exc_info=True)
                said = {"error": str(exc)[:300]}
            self.post(lambda: self._answered(said))
            if fold.get("turn"):                         # under the reply, so after it
                self.post(lambda: self._fold_begin(fold))
        threading.Thread(target=ask, name="chat-bubble", daemon=True).start()

    def _fit_box(self, _event: Any = None) -> None:
        """Where the user writes: one line high, growing with what is written
        until BOX_TALLEST, then scrolled."""
        try:
            lines = self.box.count("1.0", "end", "update", "displaylines")
            lines = lines[0] if isinstance(lines, tuple) else lines
            wanted = max(1, min(int(lines or 1), BOX_TALLEST))
            if wanted != int(self.box.cget("height")):
                self.box.configure(height=wanted)
                self._fit()
        except tk.TclError:
            pass

    # -- what is attached ---------------------------------------------------------------
    def _dropped(self, paths: list) -> None:
        """Files dragged onto it. Told from within the system's message: only
        handed on, the rest done when Tk is next free."""
        def take() -> None:
            if self.minimized:
                self.expand()
            self._add(lambda: self.files.add_paths(paths))
        self.post(take)

    def _file_over(self, on: bool) -> None:
        """A file dragged over it, or gone: told within the system's call."""
        self.post(lambda: self._cover(on))

    def _cover(self, on: bool) -> None:
        """THE MOMENT A FILE IS OVER IT, ALL OF IT IS WHERE TO LET GO (the user,
        2026-11-04: as Google Drive, not the pointer changing)."""
        if not self.is_open:
            return
        cover = getattr(self, "drop_cover", None)
        if not on:
            if cover is not None:
                cover.destroy()
                self.drop_cover = None
            return
        if cover is not None:
            return
        cover = self.drop_cover = tk.Canvas(self.card, bg=LOOK["field"], highlightthickness=0, bd=0)
        cover.place(x=0, y=0, relwidth=1, relheight=1)
        self.window.update_idletasks()
        width, height = cover.winfo_width(), cover.winfo_height()
        cover.create_rectangle(3, 3, width - 3, height - 3, outline=LOOK["primary"], width=2,
                               dash=(7, 5))
        words = cover.create_text(width / 2 + 22, height / 2, fill=LOOK["ink"],
                                  font=self._font(1, "bold"),
                                  text=self.words("Drop files to add them"))
        left = cover.bbox(words)[0] - 42
        middle = height / 2
        cover.create_oval(left, middle - 16, left + 32, middle + 16, fill=LOOK["primary"], outline="")
        mark = left + 16
        cover.create_line(mark, middle + 8, mark, middle - 8, fill=LOOK["on_primary"], width=2.4)
        cover.create_line(mark - 6, middle - 2, mark, middle - 8, mark + 6, middle - 2,
                          fill=LOOK["on_primary"], width=2.4)

    def _pick(self) -> None:
        """"+": files chosen."""
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(parent=self.window)
        if paths:
            self._add(lambda: self.files.add_paths(paths))

    def _pasted(self, _event: Any = None) -> None:
        """Ctrl+V: words go in the box as ever, and what was copied that the
        box cannot take is attached (the application reads the clipboard
        itself: a WeChat screenshot needs no going round)."""
        if self.files is None:
            return
        try:
            words = bool(self.root.clipboard_get().strip())
        except tk.TclError:                              # no words there
            words = False
        self._add(lambda: self.files.from_clipboard(words), quiet=words)

    def _add(self, read: Callable[[], Dict[str, Any]], quiet: bool = False) -> None:
        """Files read on a thread of their own; a large one takes a moment."""
        self._adding += 1

        def work() -> None:
            try:
                said = read()
            except Exception as exc:                     # said in the bubble
                log.warning("the bubble's file could not be read", exc_info=True)
                said = {"error": str(exc)[:200]}
            self.post(lambda: self._added(said, quiet))
        threading.Thread(target=work, name="chat-bubble-file", daemon=True).start()

    def _added(self, said: Dict[str, Any], quiet: bool) -> None:
        self._adding -= 1
        if not self.is_open:
            return
        files = said.get("files") or []
        self.attached.extend(files[:max(0, MOST_ATTACHED - len(self.attached))])
        note = str(said.get("error") or "")
        if said.get("skipped"):
            note = self.words("Too large or not a file:") + " " + ", ".join(said["skipped"])
        elif not files and not note and not quiet:
            note = self.words("Nothing to attach was found")
        self._draw_files(note)

    def _unattach(self, one: Dict[str, Any]) -> None:
        self.attached.remove(one)
        self._draw_files()
        self._let_go([one])

    def _let_go(self, files: list) -> None:
        """Taken off before they were sent: what they were being read for
        stops, and they go."""
        forget = getattr(self.files, "forget", None)
        if files and forget is not None:
            threading.Thread(target=forget, args=([one["id"] for one in files],),
                             name="chat-bubble-file", daemon=True).start()

    def _under_said(self) -> Any:
        """What comes right under what was said."""
        return self.files_row if self.files_row.winfo_manager() else self.row

    def _draw_files(self, note: str = "") -> None:
        """A line for each file, over the box; none, and nothing is there."""
        row = self.files_row
        for one in row.winfo_children():
            one.destroy()
        self._thumbs = []
        pictures = tk.Frame(row, bg=LOOK["bg"])
        for one in self.attached:
            seen = self._thumb(one, THUMB)
            if seen is not None:                         # seen before it is sent
                holder = tk.Frame(pictures, bg=LOOK["bg"])
                tk.Label(holder, image=seen, bg=LOOK["bg"], bd=0).pack(side="left")
                gone = tk.Label(holder, text="×", bg=LOOK["bg"], fg=LOOK["muted"],
                                font=self._font(0), padx=4, cursor="hand2")
                gone.pack(side="left", anchor="n")
                on_release(gone, lambda o=one: self._unattach(o))
                holder.pack(side="left", padx=(0, 4))
                continue
            line = tk.Frame(row, bg=LOOK["bg"])
            name = str(one["name"])
            name = name if len(name) <= 40 else name[:24] + "…" + name[-14:]
            tk.Label(line, text=name, bg=LOOK["bg"], fg=LOOK["ink"], font=self._font(-2),
                     anchor="w").pack(side="left")
            gone = tk.Label(line, text="×", bg=LOOK["bg"], fg=LOOK["muted"], font=self._font(0),
                            padx=6, cursor="hand2")
            gone.pack(side="left")
            on_release(gone, lambda o=one: self._unattach(o))
            line.pack(fill="x")
        if pictures.winfo_children():
            pictures.pack(fill="x", pady=(0, 4))
        if note:
            tk.Label(row, text=note, bg=LOOK["bg"], fg=LOOK["muted"], font=self._font(-2),
                     anchor="w", wraplength=max(120, self.width - 40),
                     justify="left").pack(fill="x")
        if self.minimized:
            return
        if self.attached or note:
            if not row.winfo_manager():
                row.pack(fill="x", pady=(8, 0), before=self.row)
        else:
            row.pack_forget()
        self._fit()

    def _thumb(self, one: Dict[str, Any], side: int) -> Any:
        """A file's picture for Tk, kept while it is shown - or None."""
        if not one.get("picture") or self.files is None:
            return None
        try:
            from PIL import ImageTk
            seen = ImageTk.PhotoImage(self.files.thumbnail(one["id"], side), master=self.window)
        except Exception:                                # its name, then
            log.debug("no picture of the file", exc_info=True)
            return None
        self._thumbs.append(seen)
        return seen

    # -- @ a file of the conversation (the user, 2026-11-04) ---------------------------------
    def _watch_mentions(self, event: Any = None) -> None:
        if getattr(event, "keysym", "") in ("Up", "Down", "Tab", "Escape", "Return", "KP_Enter"):
            return
        if self.files is None or self.minimized:
            return
        typed = re.search(r"(^|\s)@([^\s@]*)$", self.box.get("insert linestart", "insert"))
        if not typed:
            self._hide_mentions()
            return
        query = typed.group(2).lower()
        every = [*self.attached, *reversed(self.sent_files)]
        shown = [one for n, one in enumerate(every)
                 if query in str(one["name"]).lower()
                 and one["id"] not in [other["id"] for other in every[:n]]][:6]
        if not shown:
            self._hide_mentions()
            return
        self._mention = [len(typed.group(2)) + 1, shown, 0]
        self._draw_mentions()

    def _draw_mentions(self) -> None:
        row = self.mention_row
        for one in row.winfo_children():
            one.destroy()
        for n, one in enumerate(self._mention[1]):
            on = n == self._mention[2]
            line = tk.Label(row, text=str(one["name"]), anchor="w", padx=8, pady=2, cursor="hand2",
                            bg=LOOK["select"] if on else LOOK["field"], fg=LOOK["ink"],
                            font=self._font(-1))
            line.pack(fill="x")
            on_release(line, lambda o=one: self._pick_mention(chosen=o))
        if not row.winfo_manager():
            row.pack(fill="x", pady=(8, 0), before=self.row)
            self._fit()

    def _hide_mentions(self) -> None:
        self._mention = None
        if self.mention_row.winfo_manager():
            self.mention_row.pack_forget()
            self._fit()

    def _move_mention(self, step: int) -> Optional[str]:
        if not self._mention:
            return None
        self._mention[2] = (self._mention[2] + step) % len(self._mention[1])
        self._draw_mentions()
        return "break"

    def _drop_mention(self, _event: Any = None) -> Optional[str]:
        if not self._mention:
            return None
        self._hide_mentions()
        return "break"

    def _pick_mention(self, _event: Any = None, chosen: Any = None) -> Optional[str]:
        if not self._mention:
            return None
        back, shown, which = self._mention
        one = chosen or shown[which]
        self.box.delete(f"insert-{back}c", "insert")
        self.box.insert("insert", f"@{one['name']} ")
        self.mentions.append(one)
        self._hide_mentions()
        self.box.focus_set()
        self._fit_box()
        return "break"

    def _told_live(self) -> Callable[[str], None]:
        """Told from the thread reading the model, many times a second: to Tk
        only every so often."""
        last = [0.0]

        def told(text: str) -> None:
            now = time.monotonic()
            if text and now - last[0] >= LIVE_EVERY:
                last[0] = now
                self.post(lambda: self._show_live(text))
        return told

    def _show_live(self, text: str) -> None:
        """The reply so far, in place of "Thinking…"."""
        if self.waiting and self.is_open:
            self._unwrite_waiting()
            self._write("", "waiting", text)

    def _answered(self, said: Any) -> None:
        self.waiting = False
        if not self.is_open:
            return
        said = said if isinstance(said, dict) else {"answer": str(said)}
        conversation = said.get("conversation_id")
        if conversation and conversation != self.conversation_id:
            self.conversation_id = str(conversation)
            if self.on_conversation is not None:
                self.on_conversation(self.conversation_id)
        self._unwrite_waiting()
        answer = str(said.get("answer") or said.get("response") or "").strip()
        if said.get("error") and not answer:
            answer = self.words("It could not answer:") + " " + str(said["error"])
        self._write(self.name, "agent", "")
        self._write_reply(answer or "…", "fast" if said.get("layer") == "fast" else "")
        self.button.configure(bg=LOOK["primary"], fg=LOOK["on_primary"])
        self._focus()

    def _write_reply(self, text: str, tag: str) -> None:
        """A reply under its name, blue when it is the quick one."""
        self.said.configure(state="normal")
        self.said.insert("end-1c", text, (tag,) if tag else ())
        self.said.configure(state="disabled")
        self.said.see("end")
        self._fit()

    # -- the full agent's, folded under the quick reply (agent/two_speed.py) ---------------
    def _fold_begin(self, fold: Dict[str, Any]) -> None:
        """A line that it is at work - unless it was stopped, or is done."""
        if not fold.get("turn") or not self.is_open or "head" in fold:
            return
        self._folds += 1
        fold["head"], fold["body"] = f"fold_head{self._folds}", f"fold_body{self._folds}"
        said = self.said
        said.tag_configure(fold["head"], foreground=LOOK["muted"], font=self._font(-1))
        said.configure(state="normal")
        said.insert("end-1c", "\n" + self.words("Looking into it…"), (fold["head"],))
        said.configure(state="disabled")
        self._fit()
        if "done" in fold:
            self._fold_done(fold, fold["done"])

    def _fold_done(self, fold: Dict[str, Any], done: Dict[str, Any]) -> None:
        """Its answer, folded under a line clicked to open it."""
        if not self.is_open:
            return
        if "head" not in fold:
            fold["done"] = done                          # before its line was drawn
            return
        said, head, body = self.said, fold["head"], fold["body"]
        found = said.tag_ranges(head)
        if not found:
            return
        answer = str(done.get("answer") or done.get("error") or "").strip()
        did = done.get("did") or {}
        said.configure(state="normal")
        said.delete(found[0], found[1])
        if answer or did:
            title = " · ".join(
                [*self._did_words(did),
                 *([self.words("A correction" if done.get("kind") == "correction" else "More detail")]
                   if answer else [])])
            at = said.index(found[0])
            said.tag_configure(head, foreground=LOOK["slow"], font=self._font(-1, "bold"),
                               underline=True)
            said.tag_configure(body, foreground=LOOK["slow"], elide=True)
            said.insert(at, "\n" + answer, (body,))
            said.insert(at, "\n▸ " + title, (head,))
            said.tag_bind(head, "<Button-1>", lambda _e: self._fold_toggle(body))
            said.tag_bind(head, "<Enter>", lambda _e: said.configure(cursor="hand2"))
            said.tag_bind(head, "<Leave>", lambda _e: said.configure(cursor="xterm"))
        said.configure(state="disabled")
        self._fit()

    def _did_words(self, did: Dict[str, Any]) -> list:
        """What a turn changed, for the line of its fold."""
        parts = []
        if did.get("remembered"):
            parts.append(self.words("Remembered"))
        if did.get("quiet"):
            parts.append(self.words("Quiet rule added"))
        if did.get("rules") == 1:
            parts.append(self.words("1 rule changed"))
        elif did.get("rules"):
            parts.append(self.words("{0} rules changed").format(did["rules"]))
        return parts

    def _fold_toggle(self, body: str) -> None:
        shut = bool(int(self.said.tag_cget(body, "elide") or 0))
        self.said.tag_configure(body, elide=not shut)
        self._fit()

    def _write(self, who: str, tag: str, text: str) -> None:
        """A turn added to what was said - who, then their words - grown
        until SAID_TALLEST, then scrolled to its end."""
        said = self.said
        if not self.said_box.winfo_ismapped() and not self.minimized:
            self.hint.pack_forget()
            self.said_box.pack(fill="both", expand=True, pady=(8, 0), before=self._under_said())
        said.configure(state="normal")
        if said.get("1.0", "end-1c"):
            said.insert("end", "\n")
        if who:
            said.insert("end", who + "\n", (tag,))
            said.insert("end", text)
        else:
            said.insert("end", text, (tag,))
        said.configure(state="disabled")
        said.see("end")
        self._fit()

    def _unwrite_waiting(self) -> None:
        said = self.said
        found = said.tag_ranges("waiting")
        if found:
            said.configure(state="normal")
            said.delete(f"{found[0]} -1c" if said.compare(found[0], ">", "1.0") else found[0],
                        found[-1])
            said.configure(state="disabled")

    def _fit(self) -> None:
        """What was said as tall as its lines, until SAID_TALLEST - or, sized
        by the user, filling what they left it, scrolled to its end. A bar
        stays a bar: its dot lit, for what came."""
        if self.minimized:
            dot, oval = self._dot
            dot.itemconfigure(oval, fill=LOOK["you"])
            return
        if self._size:
            try:
                self.said.see("end")
            except tk.TclError:
                pass
            return
        try:
            self.window.update_idletasks()
            lines = self.said.count("1.0", "end", "update", "displaylines")
            lines = lines[0] if isinstance(lines, tuple) else lines
            line = max(1, int(self.said.tk.call("font", "metrics", self.said.cget("font"),
                                                "-linespace")))
            most = max(3, (SAID_TALLEST + (self.size - 9) * 20) // line)
            self.said.configure(height=max(1, min(int(lines or 1) + 1, most)))
            self.window.update_idletasks()
            # As wide as it was; as tall as it now asks, grown upwards.
            high = self.window.winfo_reqheight()
            bottom = getattr(self, "_bottom", self.window.winfo_y() + self.window.winfo_height())
            self.window.geometry(f"{self.width}x{high}+{self.window.winfo_x()}"
                                 f"+{max(10, bottom - high)}")
            self.said.see("end")
        except tk.TclError:
            pass


class ScrollBar:
    """A slim bar in the bubble's own colours (a system scrollbar is grey on
    Windows, whatever the card): a thumb that says how much of what was said
    is in view and where, dragged or clicked on - drawn only while what was
    said does not all fit. Its place is always kept: a bar packed and taken
    away as the text scrolls changes the text's width, which scrolls it
    again - round and round (measured: the bubble hung)."""

    WIDE = 8

    def __init__(self, parent: Any) -> None:
        self.canvas = tk.Canvas(parent, width=self.WIDE, bg=LOOK["bg"], highlightthickness=0,
                                bd=0, cursor="arrow")
        self.view = (0.0, 1.0)
        self.text: Any = None
        self._grabbed: Optional[tuple] = None
        self.canvas.bind("<Configure>", lambda _e: self._draw())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_grabbed", None))

    def scrolls(self, text: Any) -> None:
        self.text = text
        self.canvas.pack(side="right", fill="y", padx=(6, 0))

    def shown(self, first: Any, last: Any) -> None:
        """The text's yscrollcommand: where the view is."""
        self.view = (float(first), float(last))
        self._draw()

    @property
    def wanted(self) -> bool:
        """Whether what was said does not all fit."""
        return self.view[0] > 0.0 or self.view[1] < 1.0

    def _thumb(self) -> tuple:
        high = max(1, self.canvas.winfo_height())
        top, bottom = self.view[0] * high, self.view[1] * high
        if bottom - top < 24:                    # never too small to take hold of
            middle = (top + bottom) / 2
            top, bottom = max(0, middle - 12), min(high, middle + 12)
        return top, bottom, high

    def _draw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        if not self.wanted:
            return
        top, bottom, _high = self._thumb()
        wide = self.WIDE
        canvas.create_line(wide / 2, top + wide / 2, wide / 2, bottom - wide / 2,
                           width=wide - 2, capstyle="round", fill=LOOK["outline"])

    def _press(self, event: Any) -> None:
        if not self.wanted:
            return
        top, bottom, high = self._thumb()
        if top <= event.y <= bottom:
            self._grabbed = (event.y, self.view[0])
        elif self.text is not None:              # beside the thumb: a page that way
            self.text.yview_scroll(-1 if event.y < top else 1, "pages")

    def _drag(self, event: Any) -> None:
        if self._grabbed is None or self.text is None:
            return
        high = max(1, self.canvas.winfo_height())
        start_y, start_first = self._grabbed
        self.text.yview_moveto(max(0.0, start_first + (event.y - start_y) / high))


__all__ = ["ChatBubble", "LOOK", "ScrollBar"]

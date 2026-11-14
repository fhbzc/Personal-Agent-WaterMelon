"""A CONVERSATION IN A BUBBLE BESIDE THE AVATAR (the user, 2026-11-01: "a
single click is not the chat page in the browser - a bubble the user types
into").

Pressed once, the avatar opens this: the same dark card as its notification
bubbles, beside it, with what was said in it - selectable - and a box to
write in: Enter sends, Shift+Enter starts a new line. What is sent goes to
the same conversation the chat page has (`agent.handle_message`), on a
thread of its own - the avatar is never held up while the agent thinks -
and the answer comes back on Tk's thread (`post`). No link to the full chat
page in it (the user, 2026-11-09: "chat里不需要open the full chat"). Pressed
again while it is open, the avatar puts the writing back in it.

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

import itertools
import logging
import re
import sys
import threading
import time
import tkinter as tk
import webbrowser
from typing import Any, Callable, Dict, List, Optional, Tuple
from . import sizes
from .on_top import keys_on_hover, on_release

log = logging.getLogger(__name__)

#: What was said long ago, folded away while the bubble is sized.
FOLDED = "folded-while-sized"

#: Its colours - the quiet notification bubble's.
LOOK = {"outline": "#313133", "bg": "#181819", "ink": "#ffffff", "muted": "#94979d",
        "dot": "#4b91ff", "field": "#313133", "field_line": "#48484b", "you": "#9fc3ff",
        "primary": "#4b91ff",
        "primary_hover": "#6aa5ff", "on_primary": "#ffffff", "select": "#2d4a7a",
        "fast": "#8ab4ff", "slow": "#ff8f85", "link": "#6aa5ff"}
#: How far a press may move and still be a click, in pixels.
CLICK_SLACK = 4
#: The minimised bar's room from the screen's edges, in pixels.
BAR_MARGIN = 12

#: A LINK IN A REPLY (a classmate's, 2026-11-05: `[words](address)` as it was written, not to be
#: pressed): as a model writes one, or an address alone. Web pages only; what ends a sentence is
#: not part of an address. WITHOUT ITS https:// (the user, 2026-11-06: a call's
#: "meet.google.com/ruv-..." in a notification): a site's name, of the endings sites have, with a
#: path after it, so that a file's name or a mail address is not taken for one.
LINK = re.compile(r"\[([^\]\n]+)\]\((https?://(?:[^\s()]|\([^\s()]*\))+)\)"
                  r"|(https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+)"
                  r"|(?<![\w@./\-])((?:[a-z0-9\-]+\.)+(?:com|org|net|edu|gov|io|ai|co|us|uk|cn|app|dev)"
                  r"/[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+)", re.IGNORECASE)
#: Each link's own mark, in whatever box it is written.
_LINKS = itertools.count(1)


def links_in(text: str) -> List[Tuple[str, str]]:
    """The text in order, as (words, address): "" where they are not a link."""
    parts: List[Tuple[str, str]] = []
    at = 0
    for found in LINK.finditer(text):
        address = found.group(2) or found.group(3) or found.group(4)
        words = found.group(1) or ""
        left = ""
        if not words:
            bare = address.rstrip(".,;:!?'")
            if bare.endswith(")") and "(" not in bare:
                bare = bare[:-1]
            address, left, words = bare, address[len(bare):], bare
        if found.start() > at:
            parts.append((text[at:found.start()], ""))
        parts.append((words, "https://" + address if found.group(4) else address))
        if left:
            parts.append((left, ""))
        at = found.end()
    if at < len(text):
        parts.append((text[at:], ""))
    return parts


def open_link(said: Any, address: str) -> None:
    """Not at the end of selecting words. Off this thread: a browser can take its time."""
    if not said.tag_ranges("sel"):
        threading.Thread(target=webbrowser.open, args=(address,), daemon=True,
                         name="open-a-link").start()


def write_links(said: Any, at: str, text: str, tags: Tuple[str, ...], ink: str,
                cursor: str = "xterm") -> None:
    """Words at `at` in a Text, what is a link in them pressed to open it. `cursor`: the box's own."""
    said.mark_set("writing", at)
    for words, address in links_in(text):
        if not address:
            said.insert("writing", words, tags)
            continue
        link = f"link{next(_LINKS)}"
        said.tag_configure(link, foreground=ink, underline=True)
        said.tag_bind(link, "<ButtonRelease-1>", lambda _e, one=address: open_link(said, one))
        said.tag_bind(link, "<Enter>", lambda _e: said.configure(cursor="hand2"))
        said.tag_bind(link, "<Leave>", lambda _e: said.configure(cursor=cursor))
        said.insert("writing", words, (*tags, link))
    said.mark_unset("writing")


#: The picture sent with a message, at most this wide and tall in the bubble.
PICTURE_WIDE, PICTURE_TALL = 260, 170
#: Pressed, it is seen this large at most: a small copy, quick to open.
VIEW_SIDE = 1280

#: How long after it is opened the box is fitted to a draft put in it.
FIT_AFTER_MS = 30
#: How long after the last key what is written is kept, in milliseconds.
DRAFT_AFTER_MS = 400

#: How often what is in front is looked at while it is open.
FRONT_EVERY_MS = 1000

#: How wide it is at the popups' first size, and how tall what was said may
#: grow before it scrolls. Every size here is as on sizes.REFERENCE, taken to the screen by `px`.
WIDTH, SAID_TALLEST = 380, 340

#: The sizes its words can be zoomed to (as the note beside what was handed
#: over), and the one it starts at.
ZOOMS, ZOOM_START = sizes.ZOOMS, sizes.ZOOM_START

#: The smallest it is dragged to; its rim, where its edges are taken; and the
#: pointer over each edge and corner.
LEAST, RIM = (300, 160), 7
#: AT THE CORNERS, THE SYSTEM'S OWN DIAGONAL POINTER (the user, 2026-11-09: "左下角右下角那个为什么不用和
#: 其他边框拉动大小一样的鼠标"): on Windows the X corner cursors were brackets, unlike any window's.
_NW_SE, _NE_SW = (("size_nw_se", "size_ne_sw") if sys.platform.startswith("win")
                  else ("top_left_corner", "top_right_corner"))
EDGES = {"w": "sb_h_double_arrow", "e": "sb_h_double_arrow", "n": "sb_v_double_arrow",
         "s": "sb_v_double_arrow", "nw": _NW_SE, "ne": _NE_SW,
         "sw": _NE_SW if sys.platform.startswith("win") else "bottom_left_corner",
         "se": _NW_SE if sys.platform.startswith("win") else "bottom_right_corner"}


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


def _monitor_work_area(x: int, y: int) -> Optional[tuple]:
    """The part windows may use of the monitor at x, y, or the nearest (Windows)."""
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    user32 = ctypes.WinDLL("user32")
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    info = Info()
    info.cbSize = ctypes.sizeof(Info)
    monitor = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)    # the nearest
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        work = info.rcWork
        if work.right > work.left and work.bottom > work.top:
            return (work.left, work.top, work.right, work.bottom)
    return None


def work_area(widget: Any, at: Optional[tuple] = None) -> Optional[tuple]:
    """The part of the screen windows may use - (left, top, right, bottom),
    the taskbar's room left out - where the system says it (Windows; on a
    Mac the menu bar's and the Dock's, 2026-11-02); None elsewhere, and
    `beside` guesses. `at`: a point on the monitor meant, or a box (x, y, width, height):
    the avatar on a second screen (the user, 2026-11-04: "万一屏幕大小不一样"), what stands
    beside it goes by that one, on Windows; the main one asked for, it was pulled across."""
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
        if at:
            found = _monitor_work_area(at[0] + (at[2] // 2 if len(at) == 4 else 0),
                                       at[1] + (at[3] // 2 if len(at) == 4 else 0))
            if found:
                return found
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
    box; the user, 2026-11-04); `front` reads what is in front of the user, shown
    over the box as what goes with a message (agent/front_screen.py)."""

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
                 files: Any = None, streams: bool = False, turn: Any = None,
                 front: Optional[Callable[[], Dict[str, str]]] = None,
                 on_late: Optional[Callable[[str, str], None]] = None,
                 draft: str = "", on_draft: Optional[Callable[[str], None]] = None) -> None:
        self.root, self.name = root, name
        #: WHAT WAS WRITTEN AND NOT SENT comes back with it (the user, 2026-11-04): put in
        #: the box when it is opened, and told to `on_draft` as it changes.
        self.draft = draft
        self.on_draft = on_draft
        self._draft_due: Any = None
        self.front = front
        #: Told (answer, conversation) of a reply that came once it was closed.
        self.on_late = on_late
        #: The window taken off with ✕: left out until another is in front.
        self.front_off = ""
        self._front_line = ""
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
        #: The pictures shown in what was said - kept, or Tk draws them blank.
        self._pictures: list = []
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
        #: By the screen (sizes.py): how much larger than written, the least, the rim.
        self._screen = sizes.scale(root)
        self._least = (sizes.px(root, LEAST[0]), sizes.px(root, LEAST[1]))
        self._rim = sizes.px(root, RIM)
        #: The size the user left it at - kept, given back (`on_size`).
        self._size: Optional[tuple] = None
        try:
            if kept_size and len(kept_size) == 2:
                self._size = (max(self._least[0], int(kept_size[0])),
                              max(self._least[1], int(kept_size[1])))
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
        return max(sizes.SMALLEST_WORDS, int(round((self.size + more) * ZOOMS[self.zoom])))

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
        if not self.is_open:
            self.zoom = zoom                             # for when it next opens
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
        width = self._size[0] if self._size else self._grown(WIDTH)
        window = tk.Toplevel(self.root)
        self.window = window
        from .on_top import borderless
        borderless(window)                   # written in, on a Mac too
        window.attributes("-topmost", True)
        window.configure(bg=LOOK["outline"])
        # Its outline, its rim - where its edges are taken - then the card.
        rim = tk.Frame(window, bg=LOOK["bg"])
        rim.pack(fill="both", expand=True, padx=1, pady=1)
        rim_px = self._rim
        card = tk.Frame(rim, bg=LOOK["bg"], padx=sizes.px(self.root, 14) - rim_px,
                        pady=sizes.px(self.root, 12) - rim_px)
        card.pack(fill="both", expand=True, padx=rim_px, pady=rim_px)
        # Its line: who it is with; minimise; ✕.
        head = self.head = tk.Frame(card, bg=LOOK["bg"])
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
        self._line_parts = (head, dot, title)
        self._mark_line()
        for one in (head, dot, title):
            one.bind("<ButtonPress-1>", self._grab)
            one.bind("<B1-Motion>", self._move)
            # MINIMISED, ITS LINE OPENS IT where it is not a button (the user, 2026-11-07: "最小化
            # 之后最大化一定要点那个正方形，其实可以点这个header的其它非按钮位置都行") - a click,
            # not a drag: moved further than CLICK_SLACK, it was being moved.
            # OPEN, ITS LINE MINIMISES IT the same way (the user, 2026-11-08: "最大化的时候也可以点击非
            # 按钮的header位置变成最小化"). No double click: two clicks are two changes.
            one.bind("<ButtonRelease-1>", self._line_released, add="+")
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
        # ITS EDGE DRAWN (the user, 2026-11-05: a classmate could not see where to write): the
        # field and the card are all but one colour.
        row = tk.Frame(card, bg=LOOK["field"], padx=10, pady=8, highlightthickness=1,
                       highlightbackground=LOOK["field_line"])
        # ITS ROOM FIRST, at the bottom (the user, 2026-11-05: at large words, with what is on the
        # screen over two lines, it was squeezed to nothing): what was said has what is left.
        row.pack(side="bottom", fill="x", pady=(10, 0), after=head)
        self.row = row
        self.front_row = tk.Frame(card, bg=LOOK["bg"])   # while something is in front
        self._front_line = ""
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
        self.box.bind("<FocusIn>", lambda _e: row.configure(highlightbackground=LOOK["primary"]),
                      add="+")
        self.box.bind("<FocusOut>", lambda _e: row.configure(highlightbackground=LOOK["field_line"]),
                      add="+")
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
        self.box.bind("<KeyRelease>", self._draft_changed, add="+")
        if self.draft:
            self.box.insert("1.0", self.draft)
            self.draft = ""
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
        # WITH THE WHEEL TOO (the user, 2026-11-05): it goes by where the pointer is, whatever has
        # the keyboard. On each Text as well: theirs would scroll it first.
        for one in (window, self.said, self.box):
            one.bind(f"<{modifier}-MouseWheel>", self._wheel)
            one.bind(f"<{modifier}-Button-4>", lambda _e: (self.zoom_by(1), "break")[1])
            one.bind(f"<{modifier}-Button-5>", lambda _e: (self.zoom_by(-1), "break")[1])
        # PRESSED, THE KEYBOARD COMES TO IT, so Ctrl + and Ctrl - do. Not for the pointer alone:
        # Ctrl V in another program with the pointer resting here would be pasted here.
        window.bind("<Button-1>", self._pressed, add="+")
        self._edges(rim)
        keys_on_hover(window)
        window.update_idletasks()
        self.width = width
        if self._size:
            # As the user left it: what was said fills it.
            self.said_box.pack(fill="both", expand=True, pady=(8, 0))
            self.hint.pack_forget()
            self.hint.pack(fill="x", pady=(8, 0), before=self.said_box)
            window.geometry(f"{self._size[0]}x{self._size[1]}")
        else:
            window.geometry(f"{width}x{window.winfo_reqheight()}")
        self._place(near)
        self._rounded()
        window.after(FIT_AFTER_MS, self._fit_opened)
        self._watch_front()
        from .on_top import stay_on_top
        stay_on_top(window)                  # on a Mac, asked before it was laid out: lost
        self._focus()

    # -- minimised to a bar --------------------------------------------------------------
    def _draw_fold(self) -> None:
        """"—" while it is open, a square while it is a bar - each as wide as
        the ✕ beside it is, at its zoom."""
        self._mark_line()
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
            if widget in (self.front_row, self.said_box, self.hint, self.files_row, self.row):
                info = dict(widget.pack_info())
                info.pop("in", None)
                self._packed.append((widget, info))
                widget.pack_forget()
        self.minimized = True
        self._draw_fold()
        window.update_idletasks()
        bar = window.winfo_reqheight()
        # IN THE SCREEN'S BOTTOM RIGHT CORNER, ALWAYS (the user, 2026-11-07: "如果我窗口已经很低了，
        # 你最小化直接放到屏幕外面了" - "其实最小化可以放在固定位置，比如屏幕右下"): the taskbar's
        # room left out; expanded, it goes back where it was.
        area = work_area(window, (x, y, width, height)) or (
            0, 0, window.winfo_screenwidth(), window.winfo_screenheight())
        left, top, right, bottom = area
        at_x = max(left, right - width - BAR_MARGIN)
        at_y = max(top, bottom - bar - BAR_MARGIN)
        window.geometry(f"{width}x{bar}+{at_x}+{at_y}")

    def expand(self) -> None:
        """As it was before it was minimised - and what came meanwhile in it."""
        if not self.is_open or not self.minimized:
            return
        self.minimized = False
        for widget, info in self._packed:
            widget.pack(**info)
        self._packed = []
        if self.front_row.winfo_children():              # what came in front while it was a bar
            self._place_front()
        # An answer that came while it was a bar: what was said shown.
        if self.said.get("1.0", "end-1c") and not self.said_box.winfo_ismapped():
            self.hint.pack_forget()
            self.said_box.pack(fill="both", expand=True, pady=(8, 0))
        self._draw_files()
        self._draw_fold()
        dot, oval = self._dot
        dot.itemconfigure(oval, fill=LOOK["dot"])
        if self._restore is not None:
            # BACK WHERE IT WAS before it went to the corner (2026-11-07) - held on the screen.
            width, height, x, y = self._restore
            window = self.window
            window.update_idletasks()
            area = work_area(window, (x, y, width, height)) or (
                0, 0, window.winfo_screenwidth(), window.winfo_screenheight())
            x = max(area[0], min(x, area[2] - width))
            y = max(area[1], min(y, area[3] - height))
            if self._size:
                window.geometry(f"{width}x{height}+{x}+{y}")
            else:
                window.geometry(f"+{x}+{y}")
                self._bottom = y + height
                self._fit()
        try:
            self.said.see("end")
        except tk.TclError:
            pass
        self._focus()

    def close(self) -> None:
        """Closed: the next one opened is a new conversation (the user,
        2026-11-03) - with what was written and not sent in its box."""
        self._keep_draft()
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
            self._take_the_keys()
            self.window.focus_force()
            self.box.focus_set()
        except tk.TclError:
            pass
        # WRITTEN IN AT ONCE, NO MOUSE (the user, 2026-11-13: "快捷键打开chat的时候，自动获取焦点（也就是我不用移鼠标)"):
        # asked again a moment after, when the system had not given it yet.
        try:
            self.window.after(150, self._focus_again)
        except (tk.TclError, AttributeError):
            pass

    def _focus_again(self) -> None:
        try:
            if self.window is not None and self.window.focus_get() is None:
                self._take_the_keys()
                self.window.focus_force()
                self.box.focus_set()
        except (KeyError, tk.TclError):
            pass

    def _take_the_keys(self) -> None:
        """ON WINDOWS, THE KEYS TO IT: a program in the background is not let bring its window in front -
        Tk's own asking was let fall, the keys left with the window before. Its input joined a moment to
        the one in front, it may (as platform_layer/wechat_open.py does)."""
        import os
        if not sys.platform.startswith("win") or os.environ.get("PA_NO_DIALOGS"):
            return                                       # under the tests: never the screen's keys
        try:
            import ctypes
            user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
            self.window.update_idletasks()
            handle = user32.GetParent(self.window.winfo_id()) or self.window.winfo_id()
            front = user32.GetForegroundWindow()
            if front == handle:
                return
            theirs = user32.GetWindowThreadProcessId(front, None)
            mine = kernel32.GetCurrentThreadId()
            joined = theirs and theirs != mine and user32.AttachThreadInput(mine, theirs, True)
            try:
                user32.SetForegroundWindow(handle)
                user32.BringWindowToTop(handle)
                user32.SetFocus(handle)
            finally:
                if joined:
                    user32.AttachThreadInput(mine, theirs, False)
        except Exception:                                # pragma: no cover - Tk's own asking, then
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
        x, y = beside(avatar, (width, height), screen, work_area(window, avatar))
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
        # Corners no larger than the room around the card: over it, they took the box's corners
        # (the user, 2026-11-05: two black ones, once the box stood out from the ground).
        edge_strips(rim, self._edge_start, self._edge_move, self._edge_end,
                    corner=sizes.px(self.root, 12), rim_width=self._rim)

    def _grown(self, written: int) -> int:
        """A size that goes by the words: by the screen, and more for larger popup words than
        the screen gives them."""
        return int(round(written * self._screen + (self.size - 9 * self._screen) * 20))

    def _wheel(self, event: Any) -> str:
        self.zoom_by(1 if event.delta > 0 else -1)
        return "break"

    def _pressed(self, _event: Any = None) -> None:
        try:
            if self.window.focus_get() is None:
                self._focus()
        except (KeyError, tk.TclError):      # a menu's, or gone
            pass

    def _edge_start(self, event: Any, edge: str) -> None:
        if self.minimized:
            # A bar is not sized: taken by its edge, it is opened to be.
            self.expand()
            return
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
        width, height, left, top = sized_to(self._resizing, event.x_root, event.y_root, self._least)
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
        self._pressed_at = (event.x_root, event.y_root)
        self._dragging = False

    def _mark_line(self, cursor: str = "") -> None:
        """WHAT A PRESS ON ITS LINE WILL DO, by the pointer (the user, 2026-11-07: "如果是手型就是
        展开（松开才生效），如果发现有拖动就变成变的形状的鼠标，这样就是移动效果，不展开"): a hand -
        a click opens it, or minimises it when it is open (2026-11-08) - and the move cursor while
        it is dragged."""
        cursor = cursor or "hand2"                 # a click minimises it or opens it
        for one in getattr(self, "_line_parts", ()):
            try:
                one.configure(cursor=cursor)
            except tk.TclError:              # pragma: no cover - the window went
                pass

    def _line_released(self, event: Any) -> None:
        at = getattr(self, "_pressed_at", None)
        dragged, self._dragging = getattr(self, "_dragging", False), False
        self._mark_line()
        if dragged or at is None:
            return
        if abs(event.x_root - at[0]) <= CLICK_SLACK and abs(event.y_root - at[1]) <= CLICK_SLACK:
            if self.minimized:
                self.expand()
            else:
                self.minimize()

    def _move(self, event: Any) -> None:
        """Moved by its line - at most once a frame (it flickered)."""
        at = getattr(self, "_pressed_at", None)
        if not getattr(self, "_dragging", False) and at is not None and (
                abs(event.x_root - at[0]) > CLICK_SLACK or abs(event.y_root - at[1]) > CLICK_SLACK):
            self._dragging = True                # being moved: no longer a click
            self._mark_line("fleur")
        x, y = event.x_root - self._drag[0], event.y_root - self._drag[1]
        self._asked_geometry = f"+{x}+{y}"
        if getattr(self, "_geometry_due", None) is None:
            self._geometry_due = self.window.after_idle(self._give_geometry)

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
        self._keep_draft()
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
        # Where the picture that goes with it is shown, if one does (`picture_sent`).
        self.said.mark_set("sent_end", "end-1c")
        self.said.mark_gravity("sent_end", "left")
        self._write("", "waiting", self.words("Thinking…"))
        self.waiting = True
        self.button.configure(bg=LOOK["field"], fg=LOOK["muted"])

        fold: Dict[str, Any] = {}

        def ask() -> None:
            try:
                if callable(self.turn) and self.turn(None) is True:
                    said = self.turn(text, self.conversation_id, ids, mentioned,
                                     lambda done: self.post(lambda: self._fold_done(fold, done))) or {}
                    fold.update(turn=said.get("turn"), quick=said.get("answer"),
                                conversation=said.get("conversation_id"))
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
            self.post(lambda: self._answered(said, fold))
            if fold.get("turn"):                         # under the reply, so after it
                self.post(lambda: self._fold_begin(fold))
        threading.Thread(target=ask, name="chat-bubble", daemon=True).start()

    def _draft_changed(self, _event: Any = None) -> None:
        """Kept a moment after the typing stops: a crash loses no more than that."""
        if self.on_draft is None or not self.is_open:
            return
        if self._draft_due is not None:
            self.window.after_cancel(self._draft_due)
        self._draft_due = self.window.after(DRAFT_AFTER_MS, self._keep_draft)

    def _keep_draft(self) -> None:
        if self._draft_due is not None and self.is_open:
            self.window.after_cancel(self._draft_due)
        self._draft_due = None
        if self.on_draft is not None and self.is_open:
            self.on_draft(self.box.get("1.0", "end-1c"))

    def _fit_opened(self) -> None:
        """The box fitted to a draft, once it is on the screen. Asked twice: the first
        count after it is laid out still has its lines as they were at no width."""
        if not self.is_open:
            return
        if self.box.winfo_width() <= 1:      # not laid out yet, however long that takes
            self.window.after(FIT_AFTER_MS, self._fit_opened)
            return
        self.box.count("1.0", "end", "update", "displaylines")
        self._fit_box()

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

    # -- what goes with a message ------------------------------------------------------
    def _watch_front(self) -> None:
        """Read off Tk's thread, while it is open: the user moves on behind it."""
        if self.front is None or not self.is_open:
            return

        def look() -> None:
            try:
                said = self.front() or {}
            except Exception:
                log.debug("what is in front could not be read", exc_info=True)
                said = {}
            self.post(lambda: self._draw_front(said))
        threading.Thread(target=look, name="bubble-front", daemon=True).start()
        self.window.after(FRONT_EVERY_MS, self._watch_front)

    def _draw_front(self, said: Dict[str, str]) -> None:
        if not self.is_open:
            return
        from ..agent.front_screen import shown_line
        title = str(said.get("title") or "")
        if title != self.front_off:
            self.front_off = ""                # another window: told again
        line = "" if self.front_off else shown_line(said)
        if line == self._front_line:
            return
        self._front_line = line
        row = self.front_row
        for one in row.winfo_children():
            one.destroy()
        if not line:
            row.pack_forget()
        else:
            words = self.words("On your screen, given as context: {0}").replace("{0}", line[:110])
            # ONE LINE, CUT TO FIT (the user, 2026-11-11: "窗口名不一样长也会导致chat不断变化"): a
            # longer title wrapped to two, a shorter one back to one - the bubble's height changed
            # at every switch of window.
            font = self._font(-2)
            # NO ✕ (the user, 2026-11-08: "on your screen, given XX as context 这个，不用叉叉"): what is in
            # front is told, not to be taken off here.
            shown = tk.Label(row, text=self._cut_to(words, font, self.width - 40), bg=LOOK["bg"],
                             fg=LOOK["muted"], font=font, justify="left", anchor="w")
            shown.pack(side="left", fill="x", expand=True)
            self._place_front()
        # NOT SCROLLED TO THE END (the user, 2026-11-11: "我做一个屏幕/窗口的切换他就会自动拖到chat的
        # 最底下"): a window switched to is no reason to move what they are reading.
        self._fit(to_end=False)

    def _cut_to(self, words: str, font: Any, room: int) -> str:
        """`words` as one line of at most `room` pixels: cut, and "…" said so."""
        from tkinter import font as tkfont
        try:
            measure = tkfont.Font(root=self.window, font=font).measure
            if measure(words) <= room:
                return words
            low, high = 0, len(words)
            while low < high:                    # the longest beginning that fits with "…"
                middle = (low + high + 1) // 2
                if measure(words[:middle].rstrip() + "…") <= room:
                    low = middle
                else:
                    high = middle - 1
            return words[:low].rstrip() + "…"
        except tk.TclError:                      # pragma: no cover - as it was
            return words

    def _place_front(self) -> None:
        """RIGHT UNDER ITS HEAD, always (the user, 2026-11-05: over Send one moment, under the
        chat the next: it went by what happened to be there first). Not on a bar."""
        if not self.minimized:
            self.front_row.pack(fill="x", pady=(8, 0), after=self.head)

    def _without_front(self, title: str) -> None:
        self.front_off = title
        self._draw_front({"title": title})

    def _draw_files(self, note: str = "") -> None:
        """A line for each file, over the box; none, and nothing is there."""
        row = self.files_row
        for one in row.winfo_children():
            one.destroy()
        self._thumbs = []
        pictures = tk.Frame(row, bg=LOOK["bg"])
        for one in self.attached:
            seen = self._thumb(one, sizes.px(self.root, THUMB))
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
                row.pack(side="bottom", fill="x", pady=(8, 0), after=self.row)
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
            row.pack(side="bottom", fill="x", pady=(8, 0), after=self.row)
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

    def _told_late(self, said: Dict[str, Any], fold: Optional[Dict[str, Any]] = None) -> None:
        """Closed before the reply came: it is said elsewhere, once."""
        fold = fold if fold is not None else {}
        if self.on_late is None or fold.get("told"):
            return
        answer = str(said.get("answer") or said.get("response") or "").strip()
        if said.get("nothing") or not answer:
            # Nothing to add to the quick reply: unless that was not read either.
            answer = "" if fold.get("seen") else str(fold.get("quick") or "").strip()
        answer = answer or str(said.get("error") or "").strip()
        if answer:
            fold["told"] = True
            self.on_late(answer, str(said.get("conversation_id") or fold.get("conversation") or ""))

    def _answered(self, said: Any, fold: Optional[Dict[str, Any]] = None) -> None:
        self.waiting = False
        said = said if isinstance(said, dict) else {"answer": str(said)}
        if not self.is_open:
            if not (fold or {}).get("turn"):             # with a turn, its end says
                self._told_late(said, fold)
            return
        if fold is not None:
            fold["seen"] = True
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
        if not said.get("layer"):
            self._write_said("end-1c", list(said.get("actions") or []))
        self.button.configure(bg=LOOK["primary"], fg=LOOK["on_primary"])
        self._focus()

    def _write_reply(self, text: str, tag: str) -> None:
        """A reply under its name, blue when it is the quick one."""
        self.said.configure(state="normal")
        self._write_links("end-1c", text, (tag,) if tag else ())
        self.said.configure(state="disabled")
        self.said.see("end")
        self._fit()

    def _write_links(self, at: str, text: str, tags: Tuple[str, ...]) -> None:
        """Words at `at`, what is a link in them pressed to open it."""
        write_links(self.said, at, text, tags, LOOK["link"])

    def _open_link(self, address: str) -> None:
        open_link(self.said, address)

    # -- the full agent's, folded under the quick reply (agent/two_speed.py) ---------------
    def _fold_begin(self, fold: Dict[str, Any]) -> None:
        """A line that it is at work - unless it was stopped, or is done."""
        if not self.is_open and "done" in fold:
            self._told_late(fold["done"], fold)
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
            self._told_late(done, fold)
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
        actions = list(done.get("actions") or []) or self._did_words(did)
        said.configure(state="normal")
        at = said.index(found[0])
        said.delete(found[0], found[1])
        # NEVER FOLDED (the user, 2026-11-09): what it did, in small grey words - after its fold's
        # line, in the order they come (a mark that moves on past each thing written at it).
        said.mark_set("fold_said", at)
        said.mark_gravity("fold_said", "right")
        if answer:
            title = self.words("A correction" if done.get("kind") == "correction" else "More detail")
            # QUESTIONS TO ANSWER (the user, 2026-11-09): said on its line, so that it is opened.
            if done.get("asks"):
                title += " · " + self.words("Questions to answer")
            said.tag_configure(head, foreground=LOOK["slow"], font=self._font(-1, "bold"),
                               underline=True)
            said.tag_configure(body, foreground=LOOK["slow"], elide=True)
            said.mark_set("title", at)
            said.mark_gravity("title", "left")
            self._write_links(at, "\n" + answer, (body,))
            said.insert("title", "\n▸ " + title, (head,))
            said.mark_unset("title")
            said.tag_bind(head, "<Button-1>", lambda _e: self._fold_toggle(body))
            said.tag_bind(head, "<Enter>", lambda _e: said.configure(cursor="hand2"))
            said.tag_bind(head, "<Leave>", lambda _e: said.configure(cursor="xterm"))
        said.configure(state="disabled")
        self._write_said("fold_said", actions)
        said.mark_unset("fold_said")
        self._fit()

    def _write_said(self, at: str, actions: list) -> None:
        """WHAT IT DID, a small grey line each - written at `at`, in the conversation itself."""
        if not actions:
            return
        said = self.said
        said.tag_configure("action", foreground=LOOK["muted"], font=self._font(-2))
        said.configure(state="normal")
        for line in actions:
            said.insert(at, "\n" + str(line), ("action",))
        said.configure(state="disabled")
        self._fit()

    def _did_words(self, did: Dict[str, Any]) -> list:
        """What a turn changed, for the line of its fold."""
        parts = []
        if did.get("remembered"):
            parts.append(self.words("Remembered"))
        if did.get("quiet"):
            parts.append(self.words("Blocker added"))
        if did.get("rules") == 1:
            parts.append(self.words("1 rule changed"))
        elif did.get("rules"):
            parts.append(self.words("{0} rules changed").format(did["rules"]))
        return parts

    def _fold_toggle(self, body: str) -> None:
        """Opened or shut WHERE IT IS (the user, 2026-11-06: More detail, opened, went to the end of
        the chat): what is read stays put, and its line stays in view."""
        said = self.said
        shut = bool(int(said.tag_cget(body, "elide") or 0))
        top = said.index("@0,0")
        said.tag_configure(body, elide=not shut)
        self._fit(to_end=False)
        said.yview(top)
        found = said.tag_ranges(body.replace("body", "head"))
        if found:
            said.see(f"{found[0]} +1c")          # its first character is the line break before it

    def _write(self, who: str, tag: str, text: str) -> None:
        """A turn added to what was said - who, then their words - grown
        until SAID_TALLEST, then scrolled to its end."""
        said = self.said
        if not self.said_box.winfo_ismapped() and not self.minimized:
            self.hint.pack_forget()
            self.said_box.pack(fill="both", expand=True, pady=(8, 0))
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

    def picture_sent(self, image: Any, sees: Optional[bool] = None) -> None:
        """THE PICTURE OF THE SCREEN THAT GOES WITH THE MESSAGE, under it (the user, 2026-11-08): shown
        at once, small - its words still being read - and said once it is known whether it went
        (`picture_settled`); pressed, seen larger, in a window of its own. `image`: the picture,
        or its file. `sees` given: it is settled already."""
        if not self.is_open or "sent_end" not in self.said.mark_names():
            return
        try:
            from PIL import Image, ImageTk
            if isinstance(image, (str, bytes)) or hasattr(image, "__fspath__"):
                with Image.open(image) as found:
                    image = found.convert("RGB")
            view = image.convert("RGB")
            view.thumbnail((VIEW_SIDE, VIEW_SIDE))
            small = view.copy()
            small.thumbnail((max(80, min(PICTURE_WIDE, self.width - 60)), PICTURE_TALL))
            picture = ImageTk.PhotoImage(small, master=self.window)
        except Exception:
            log.debug("the picture sent could not be shown", exc_info=True)
            return
        self._pictures.append(picture)               # kept, or Tk shows nothing
        said = self.said
        tag = f"sent_picture{len(self._pictures)}"
        self._last_picture = tag
        said.tag_configure(tag, foreground=LOOK["muted"], font=self._font(-2))
        said.tag_bind(tag, "<ButtonRelease-1>", lambda _e, one=view: self._open_picture(one))
        said.tag_bind(tag, "<Enter>", lambda _e: said.configure(cursor="hand2"))
        said.tag_bind(tag, "<Leave>", lambda _e: said.configure(cursor="xterm"))
        said.configure(state="normal")
        line = self.words("Your screen, taken - checking it before it goes…")
        said.tag_configure(tag + "_line", foreground=LOOK["muted"], font=self._font(-2))
        # At a mark that stays put, what goes in last is first: the picture, then the line over it.
        name = said.image_create("sent_end", image=picture, padx=0, pady=2)
        said.insert("sent_end", "\n", (tag,))
        said.insert("sent_end", line, (tag, tag + "_line"))
        said.insert("sent_end", "\n", (tag,))
        at = said.index(name)
        said.tag_add(tag, at, f"{at} +1c")
        said.configure(state="disabled")
        said.mark_unset("sent_end")
        self._fit()
        if sees is not None:
            self.picture_settled("sent" if sees else "words")

    def picture_withheld(self, why: str) -> None:
        """NO PICTURE OF THE SCREEN WENT WITH THE MESSAGE, under it, and why (the user, 2026-11-12):
        private mode, or a file in a privacy folder open - none was taken."""
        if not self.is_open or "sent_end" not in self.said.mark_names():
            return
        said = self.said
        tag = "sent_withheld"
        said.tag_configure(tag, foreground=LOOK["muted"], font=self._font(-2))
        said.configure(state="normal")
        line = self.words("No picture of your screen went with it") + f" ({self.words(why)})"
        said.insert("sent_end", line + "\n", (tag,))
        said.configure(state="disabled")
        said.mark_unset("sent_end")
        self._fit()

    def picture_settled(self, how: str, why: str = "") -> None:
        """What became of the picture last shown: "sent", "words" (only its words: the model sees
        no pictures), or "not" - and `why`."""
        tag = getattr(self, "_last_picture", "")
        if not self.is_open or not tag:
            return
        found = self.said.tag_ranges(tag + "_line")
        if not found:
            return
        line = (self.words("Sent with it, a picture of your screen:") if how == "sent" else
                self.words("Sent with it, the words read in this picture of your screen "
                           "(this model sees no pictures):") if how == "words" else
                self.words("Not sent with it") + (f" ({self.words(why)})" if why else ""))
        said = self.said
        said.configure(state="normal")
        said.delete(found[0], found[1])
        said.insert(found[0], line, (tag, tag + "_line"))
        said.configure(state="disabled")
        self._last_picture = ""
        self._fit()

    def _open_picture(self, view: Any) -> None:
        """SEEN LARGER, IN A WINDOW OF ITS OWN (the user, 2026-11-08: "压缩后的低分辨率图，方便我打开
        view"): the small copy kept in memory - no file, nothing opened elsewhere. A click or Esc
        closes it."""
        try:
            from PIL import ImageTk
            shown = view.copy()
            shown.thumbnail((int(self.window.winfo_screenwidth() * 0.8),
                             int(self.window.winfo_screenheight() * 0.8)))
            top = tk.Toplevel(self.window)
            top.title(self.words("Your screen, as it went with the message"))
            top.attributes("-topmost", True)
            photo = ImageTk.PhotoImage(shown, master=top)
            label = tk.Label(top, image=photo, bd=0, cursor="hand2")
            label.image = photo                      # kept, or Tk shows nothing
            label.pack()
            label.bind("<ButtonRelease-1>", lambda _e: top.destroy())
            top.bind("<Escape>", lambda _e: top.destroy())
            top.focus_force()
        except Exception:
            log.debug("the picture could not be opened", exc_info=True)

    def _unwrite_waiting(self) -> None:
        said = self.said
        found = said.tag_ranges("waiting")
        if found:
            said.configure(state="normal")
            said.delete(f"{found[0]} -1c" if said.compare(found[0], ">", "1.0") else found[0],
                        found[-1])
            said.configure(state="disabled")

    def _fit(self, to_end: bool = True) -> None:
        """What was said as tall as its lines, until SAID_TALLEST - or, sized
        by the user, filling what they left it, scrolled to its end unless told not to.
        A bar stays a bar: its dot lit, for what came."""
        if self.minimized:
            dot, oval = self._dot
            dot.itemconfigure(oval, fill=LOOK["you"])
            return
        if self._size:
            try:
                if to_end:
                    self.said.see("end")
            except tk.TclError:
                pass
            return
        try:
            self.window.update_idletasks()
            # BY ITS PIXELS, NOT ITS LINES: each name has room above it, which a line more than
            # was written covers only while the words are large (a bar on a short chat, on a Mac
            # and at the smallest zoom).
            high = self.said.count("1.0", "end", "update", "ypixels")
            high = high[0] if isinstance(high, tuple) else high
            line = max(1, int(self.said.tk.call("font", "metrics", self.said.cget("font"),
                                                "-linespace")))
            most = max(3, self._grown(SAID_TALLEST) // line)
            self.said.configure(height=max(1, min(-(-int(high or 0) // line) + 1, most)))
            self.window.update_idletasks()
            # As wide as it was; as tall as it now asks, grown upwards.
            high = self.window.winfo_reqheight()
            bottom = getattr(self, "_bottom", self.window.winfo_y() + self.window.winfo_height())
            self.window.geometry(f"{self.width}x{high}+{self.window.winfo_x()}"
                                 f"+{max(10, bottom - high)}")
            if to_end:
                self.said.see("end")
        except tk.TclError:
            pass


def edge_strips(rim: Any, start: Callable[[Any, str], None], move: Callable[[Any], None],
                end: Callable[[Any], None], corner: int = 0, rim_width: int = RIM) -> None:
    """Edges and corners of `rim`, each a strip to size a window by."""
    corner = corner or 3 * rim_width
    for edge, cursor in EDGES.items():
        strip = tk.Frame(rim, bg=LOOK["bg"], cursor=cursor)
        if edge == "se":
            # THE ONE THAT SHOWS (the user, 2026-11-05): nothing said a window of no frame can be
            # sized at all. Three strokes in its corner, as a window's grip has.
            strip = tk.Canvas(rim, bg=LOOK["bg"], cursor=cursor, highlightthickness=0, bd=0,
                              width=corner, height=corner)
            for share in (0.3, 0.55, 0.8):
                far = round(corner * share)
                strip.create_line(corner - 2, corner - 2 - far, corner - 2 - far, corner - 2,
                                  fill=LOOK["muted"])
        place: Dict[str, Any] = {"width": corner, "height": corner}
        if edge in ("w", "e"):
            place = {"width": rim_width, "height": -2 * corner, "relheight": 1, "y": corner}
        elif edge in ("n", "s"):
            place = {"height": rim_width, "width": -2 * corner, "relwidth": 1, "x": corner}
        place.update(relx=1.0 if "e" in edge else 0.0,
                     rely=1.0 if edge in ("s", "sw", "se") else 0.0,
                     anchor=("s" if edge in ("s", "sw", "se") else "n")
                     + ("e" if "e" in edge else "w"))
        if edge in ("w", "e"):
            place["rely"] = 0.0
            place["anchor"] = "ne" if edge == "e" else "nw"
        strip.place(**place)
        strip.bind("<ButtonPress-1>", lambda e, edge=edge: start(e, edge))
        strip.bind("<B1-Motion>", move)
        strip.bind("<ButtonRelease-1>", end)
        tk.Misc.lift(strip)                  # a Canvas's own lift is of what is drawn on it


def sized_to(began: tuple, x: int, y: int, least: tuple = LEAST) -> tuple:
    """Where an edge pulled from `began` - (x, y, width, height, left, top, edge) - to x, y
    leaves the window: (width, height, left, top)."""
    x0, y0, width, height, left, top, edge = began
    dx, dy = x - x0, y - y0
    if "e" in edge:
        width = max(least[0], width + dx)
    if "w" in edge:
        grown = max(least[0], width - dx)
        left, width = left + width - grown, grown
    if "s" in edge:
        height = max(least[1], height + dy)
    if "n" in edge:
        grown = max(least[1], height - dy)
        top, height = top + height - grown, grown
    return width, height, left, top


class ScrollBar:
    """A slim bar in the bubble's own colours (a system scrollbar is grey on
    Windows, whatever the card): a thumb that says how much of what was said
    is in view and where, dragged or clicked on - drawn only while what was
    said does not all fit. Its place is always kept: a bar packed and taken
    away as the text scrolls changes the text's width, which scrolls it
    again - round and round (measured: the bubble hung).

    `wide` is the strip that takes the mouse and `thumb` what is drawn, at its right: what is drawn
    can be narrower than what can be pressed (the user, 2026-11-06: a notification's could not be
    hit, 8 pixels between two margins that take nothing)."""

    WIDE = 8

    def __init__(self, parent: Any, wide: int = WIDE, thumb: int = WIDE, bg: str = "",
                 ink: str = "", hot: str = "") -> None:
        self.wide, self.thumb = wide, thumb
        self.ink = ink or LOOK["outline"]
        self.hot = hot or self.ink
        self._over = False
        # height: a Canvas asks for seven centimetres of its own unless told, and that is what took
        # the room of the box to write in.
        self.canvas = tk.Canvas(parent, width=wide, height=1, bg=bg or LOOK["bg"], highlightthickness=0,
                                bd=0, cursor="arrow")
        self.view = (0.0, 1.0)
        self.text: Any = None
        self._grabbed: Optional[tuple] = None
        self.canvas.bind("<Configure>", lambda _e: self._draw())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_grabbed", None))
        self.canvas.bind("<Enter>", lambda _e: self._hover(True))
        self.canvas.bind("<Leave>", lambda _e: self._hover(False))

    def _hover(self, over: bool) -> None:
        self._over = over
        self._draw()

    def scrolls(self, text: Any) -> None:
        self.text = text
        self.canvas.pack(side="right", fill="y", padx=(max(0, 6 - (self.wide - self.thumb)), 0))

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
        end, at = self.thumb / 2, self.wide - self.thumb / 2
        canvas.create_line(at, top + end, at, bottom - end, width=self.thumb - 2, capstyle="round",
                           fill=self.hot if self._over else self.ink)

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

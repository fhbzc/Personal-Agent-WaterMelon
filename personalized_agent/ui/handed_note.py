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
  - ZOOMED WHOLE, WITH EVERY OTHER WINDOW OF THE AGENT'S (the user, 2026-11-05): - and +, and
    Ctrl +, Ctrl -, Ctrl 0 and Ctrl with the wheel. One ✕ closes both.
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
  - FOLDED TO ITS BAR (the user, 2026-11-02: "要有一个最小化选项，可以把它
    collapse (像zoom那个collapse一样，留一个bar)"): – beside the ✕ - or a
    double click on the bar - leaves only its bar, where it stood by its
    right edge and top, still on top and dragged by it; □ there, or a double
    click again, opens it as it was - its size, its sides, where each was
    scrolled to, nothing built again.
"""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from typing import Any, Callable, Dict, List, Optional
from . import sizes
from .on_top import on_release

log = logging.getLogger(__name__)

#: Its colours: the agent's dark bubbles', with the amber of what to mind.
PALETTE = {"edge": "#313133", "bar": "#232325", "bg": "#181819", "ink": "#ffffff",
           "muted": "#94979d", "accent": "#4b91ff", "on_accent": "#ffffff",
           "hover": "#313133", "ticked": "#5fbf8f", "thumb": "#48484b", "thumb_on": "#6a6a6e",
           "select": "#2d4a7a"}

#: The smallest it can be dragged to, and a side's narrowest.
LEAST, SIDE_LEAST = (320, 200), 160

#: Its rim, inside its outline, where its edges are taken to size it; and the
#: line between the sides, as wide as it is taken by.
RIM, DIVIDER = 5, 7

#: Each edge and corner it is sized by, and the pointer shown over it.
# The pointer over each edge and corner: the chat's, one table (the system's diagonal at corners).
from .chat_bubble import EDGES  # noqa: E402

#: Words longer than this are not measured line by line to be fitted: they
#: scroll within their box whatever its height.
MEASURED_MOST = 20000

#: The sizes a side can be zoomed to, and the one it starts at.
ZOOMS, ZOOM_START = sizes.ZOOMS, sizes.ZOOM_START

#: The sides: what was handed over ("item"), how to use it ("how").
SIDES = ("item", "how")
#: EACH PART ZOOMED BY ITSELF, AND KEPT (the user, 2026-11-08: "project instruction panel 内部，四个小
#: panel是支持单独的 缩放（并且记住历史选择)。 同样的，item instruction panel 左右两个也是支持单独缩放
#: 的"): the two sides, and a whole project's guide and list of what it made.
PARTS = ("item", "how", "guide", "made")
#: Closed: hidden at once, its parts let go of this long after, out of sight.
CLOSE_AFTER_MS = 30
#: The window an item was shown in, let go this long after the new one is drawn over it.
SWAP_AFTER_MS = 80
#: A whole project's guide and its list of what it made, at most these shares of the screen tall
#: as they open - each scrolled within, the lines between them dragged.
GUIDE_SHARE, MADE_SHARE = 0.28, 0.2
#: What a project's row's button says.
OPEN_ITEM = {"email": "Open the email", "event": "Open the event", "file": "Open the file",
             "folder": "Open the folder", "message": "Open the chat", "form": "Open the form",
             "page": "Open the page"}
#: What is read in the note itself, opened nowhere else: no Open again.
OPENED_HERE = ("answer", "text")
#: ITS SCROLL BAR, wider (the user, 2026-11-08: "右边那个拉动的bar也太窄了"): the bar, and what is
#: taken in it, at the size the note is drawn.
SCROLL_WIDE, SCROLL_INSET = 14, 3
#: Moved further than this on its bar, it was being moved - not a click that folds it.
CLICK_SLACK = 4
#: A WHOLE PROJECT'S NOTE, told apart by its ground (the user, 2026-11-08: "整个project的to do panel,
#: 底色和per to do 的不一样").
PROJECT_PALETTE = {"edge": "#2c3850", "bar": "#1e2738", "bg": "#151c2b", "hover": "#2c3850",
                   "thumb": "#3c4a66", "thumb_on": "#56678a"}
#: THE APPROVALS PANEL (the user, 2026-11-09: "每个审批session可以用类似project instruction的panel。但是
#: 现在最上面那个project level instruction可以删去，就一个是选项，一个是instruction进行审批"): the rows to
#: approve, each with its answers, at most this share of the screen tall as it opens; below, the
#: one chosen - what the agent made and how it is used.
APPROVAL_SHARE = 0.34
#: The answers to what the agent made, as the calendar gives them (app.js agendaActions) - nothing is
#: sent for the user yet: they send it, then say so here.
APPROVAL_ANSWERS = (("go", "Approve - let it do it"), ("as_it_was", "I sent it as it was"),
                    ("changed", "I sent it, changed"), ("not_right", "Not right"))
#: A row's state, said under its title.
APPROVAL_STATES = {"working": "The agent is still working on it", "not_started": "Not started yet",
                   "closed": "Done, or no longer on your plate",
                   "answered": "Answered - the agent carries on"}

#: Its sides' widths at the first zoom, and where it stands: this far from
#: the screen's right edge and top, and from its bottom (the taskbar's room).
LEFT_WIDTH, WIDTH, RIGHT, TOP, BOTTOM = 460, 380, 24, 64, 64

#: The most of the work's name its bar shows; and the widest it is folded to
#: (at the first zoom), whatever the name.
HEADING_MOST, FOLDED_WIDEST = 60, 420

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
                 on_open: Optional[Callable[[], Any]] = None,
                 on_item: Optional[Callable[[int], Any]] = None,
                 on_tick: Optional[Callable[[List[str]], Any]] = None,
                 on_folder: Optional[Callable[[], Any]] = None,
                 kept_size: Any = None, split: Any = None,
                 on_layout: Optional[Callable[[tuple, Optional[float]], None]] = None,
                 palette: Optional[Dict[str, str]] = None,
                 on_approve: Optional[Callable[[int, str], Any]] = None) -> None:
        self.root = root
        self.note = note
        #: Its colours: a whole project's note has a ground of its own (PROJECT_PALETTE).
        self.palette = dict(PALETTE, **(palette or {}))
        self.words = words
        self.family = family
        self.size = max(8, int(size or 10))
        # By the screen (sizes.py): the words come so from the desktop's popup font.
        self._screen = sizes.scale(root)
        self._least = (sizes.px(root, LEAST[0]), sizes.px(root, LEAST[1]))
        self._side_least = sizes.px(root, SIDE_LEAST)
        self._rim = sizes.px(root, RIM)
        given = zoom if isinstance(zoom, dict) else {side: zoom for side in PARTS}
        self.zooms: Dict[str, int] = {
            side: min(max(int(given.get(side, ZOOM_START)), 0), len(ZOOMS) - 1)
            for side in PARTS}
        self.on_zoom = on_zoom
        #: Where Save keeps the words as changed - none, and there is no Save.
        self.on_save = on_save
        #: OPENED AGAIN FROM HERE (the user, 2026-11-08: "在to do help panel里重新打开deliverable的东西"):
        #: the page, the folder, the file, the email - "" when it opened, else why not, in words.
        self.on_open = on_open
        #: A WHOLE PROJECT'S ITEMS, each opened in a panel of its own (the user, 2026-11-08).
        self.on_item = on_item
        #: TICKED, KEPT (the user, 2026-11-08: "checklist 用户是可以编辑打勾的，且数据会保存"): told the
        #: words ticked at each tick; `note["ticked"]` the words ticked before - by their words,
        #: so a list said a little differently keeps what is the same.
        self.on_tick = on_tick
        #: ITS WORK'S FOLDER, OPENED FROM ITS BAR when it made several things (the user, 2026-11-08).
        self.on_folder = on_folder
        #: THE APPROVALS PANEL'S ANSWERS: `on_approve(row, how)` - the desktop keeps it off Tk's thread
        #: and tells `approved` how it went.
        self.on_approve = on_approve
        before = {str(one) for one in (note.get("ticked") or [])}
        self.ticked: List[bool] = [str(one)[:300] in before or str(one) in before
                                   for one in list(note.get("check") or [])]
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
                self._size = (max(self._least[0], int(kept_size[0])), max(self._least[1], int(kept_size[1])))
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
        #: FOLDED TO ITS BAR (module docstring): whether it is, and its size
        #: before it was; its bar, its foot, its middle, the – / □ in its bar.
        self.folded = False
        self._unfolded_size: Optional[tuple] = None
        self._bar_frame: Any = None
        self._foot_frame: Any = None
        self._middle: Any = None
        self._fold_button: Any = None
        self._strip_places: List[Dict[str, Any]] = []

    # -- the window --------------------------------------------------------------------------
    def show(self) -> None:
        window = tk.Toplevel(self.root)
        self.window = window
        from .on_top import borderless
        borderless(window)                   # its words written in, on a Mac too
        window.attributes("-topmost", True)
        window.configure(bg=self.palette["edge"])
        window.title(self.heading())
        modifier = "Command" if sys.platform == "darwin" else "Control"
        for keys, step in ((("plus", "equal", "KP_Add"), 1), (("minus", "KP_Subtract"), -1)):
            for key in keys:
                window.bind(f"<{modifier}-{key}>", lambda _e, s=step: self.zoom_by(s))
        for key in ("0", "KP_0"):
            window.bind(f"<{modifier}-{key}>",
                        lambda _e: self.zoom_to(self._side_now(), ZOOM_START))
        from .on_top import PINCH
        if PINCH:                            # two fingers, as every window of the agent's
            from .touch_zoom import listen
            window.after(120, lambda: listen(window, PINCH[0]))
        window.bind("<Escape>", lambda _e: self.close())
        # Pressed anywhere in it, it takes the keys - never before: what was
        # just opened beside it keeps them. (A window's binding is every
        # widget's in it.)
        window.bind("<Button-1>", self._pressed, add="+")
        self._build()
        self._rounded()
        from .on_top import stay_on_top
        stay_on_top(window)                  # on a Mac, asked before it was laid out: lost

    def close(self) -> None:
        """GONE AT ONCE (the user, 2026-11-08: "点击叉掉之后要卡一秒才关掉，能让他即时关吗"): hidden the
        moment ✕ is let go; its many parts let go of a moment later, out of sight - destroyed
        first, they held it on the screen as long as that took (a quarter of a second for a
        small project's, more for a large one)."""
        if self.window is None:
            return
        window, fonts = self.window, list(self._fonts)
        self.window, self._fonts = None, {}
        try:
            window.withdraw()
        except tk.TclError:
            pass

        def let_go() -> None:
            try:
                window.destroy()
            except tk.TclError:
                pass
            for name in fonts:
                try:
                    self.root.tk.call("font", "delete", name)
                except tk.TclError:
                    pass
        try:
            self.root.after(CLOSE_AFTER_MS, let_go)
        except (tk.TclError, RuntimeError):              # pragma: no cover - Tk gone meanwhile
            let_go()

    def heading(self) -> str:
        """ITS BAR SAYS WHOSE IT IS (the user, 2026-11-02: "header应该是这个
        project的name, 而不是before you use it"): the work's name, cut short
        past HEADING_MOST - else, from where no work is named, the words of
        before."""
        name = " ".join(str(self.note.get("work") or "").split())
        if not name:
            return self.words("Before you use it")
        return name if len(name) <= HEADING_MOST else name[:HEADING_MOST - 1].rstrip() + "…"

    # -- folded to its bar (module docstring) ------------------------------------------------
    def toggle_folded(self) -> None:
        if self.folded:
            self.unfold()
        else:
            self.fold()

    def fold(self) -> None:
        """Only its bar left, where it stood by its right edge and top: its
        middle, its foot and its edges taken away, nothing destroyed."""
        window = self.window
        if window is None or self.folded or self._bar_frame is None:
            return
        try:
            window.update_idletasks()
            self._unfolded_size = (window.winfo_width(), window.winfo_height())
            # THE PANES ON TOP, AS TALL AS THEY ARE: folded to its bar, the window squeezes them to
            # nothing, and they stayed so once it opened again (the user, 2026-11-10).
            self._folded_panes = [(part, part.winfo_height())
                                  for part in getattr(self, "_stack_parts", None) or []
                                  if part.winfo_exists()]
            right, top = window.winfo_x() + window.winfo_width(), window.winfo_y()
            self.folded = True
            for part in (self._middle, self._foot_frame):
                if part is not None:
                    part.pack_forget()
            for one in [getattr(self, "_grip", None)] + list(getattr(self, "_strips", [])):
                if one is not None:
                    one.place_forget()
            # FOLDED, ITS BAR IS ITS NAME, – AND ✕ (the user, 2026-11-09: "open folder这些按键也在最小化
            # 的header上"): what it opens comes back with it.
            folder = getattr(self, "_folder_button", None)
            if folder is not None:
                folder.pack_forget()
            self._draw_fold(folded=True)
            window.update_idletasks()
            bar = self._bar_frame
            width = min(self._unfolded_size[0], self._px(FOLDED_WIDEST),
                        max(bar.winfo_reqwidth() + 2 + 2 * self._rim, self._px(240)))
            height = bar.winfo_reqheight() + 2 + 2 * self._rim
            window.geometry(f"{width}x{height}+{max(0, right - width)}+{top}")
        except tk.TclError:                              # pragma: no cover - closed meanwhile
            pass

    def unfold(self) -> None:
        """Opened as it was - its size, its sides, where each was scrolled
        to - by the right edge and top its bar now has."""
        window = self.window
        if window is None or not self.folded:
            return
        try:
            right, top = window.winfo_x() + window.winfo_width(), window.winfo_y()
            # Packed again in the order they were made: the bar, the foot, the middle.
            if self._foot_frame is not None:
                self._foot_frame.pack(side="bottom", fill="x")
            if self._middle is not None:
                self._middle.pack(side="top", fill="both", expand=True)
            grip = getattr(self, "_grip", None)
            if grip is not None:
                grip.place(relx=1.0, rely=1.0, anchor="se")
                grip.lift()
            for strip, place in zip(getattr(self, "_strips", []), self._strip_places):
                strip.place(**place)
                strip.lift()
            folder = getattr(self, "_folder_button", None)
            if folder is not None:
                folder.pack(side="right", padx=(0, self._px(6)), after=self._fold_button)
            self._draw_fold(folded=False)
            self.folded = False
            width, height = self._unfolded_size or (window.winfo_reqwidth(),
                                                    window.winfo_reqheight())
            x = max(0, min(right - width, window.winfo_screenwidth() - width))
            y = max(0, min(top, window.winfo_screenheight() - height - BOTTOM // 2))
            window.geometry(f"{width}x{height}+{x}+{y}")
            self._restore_panes()
        except tk.TclError:                              # pragma: no cover - closed meanwhile
            pass

    def _restore_panes(self) -> None:
        """The panes on top given back the heights they had as it folded."""
        stack = getattr(self, "_stack", None)
        panes = getattr(self, "_folded_panes", None) or []
        if stack is None or not panes:
            return
        try:
            for part, height in panes:
                if part.winfo_exists() and height > 1:
                    stack.paneconfigure(part, height=height)
            self.window.update_idletasks()
        except tk.TclError:                              # pragma: no cover - closed meanwhile
            pass

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
        self.zooms[side] = zoom                  # that part alone, the others as they are
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

    def _open_again(self) -> None:
        """What was handed over, opened again where it opens - said here when it would not."""
        if self.on_open is None:
            return
        try:
            why = self.on_open()
        except Exception as exc:                 # pragma: no cover - said, not raised
            why = str(exc) or "it could not be opened"
        if why and getattr(self, "_copied", None) is not None:
            self._copied.configure(fg=self.palette["accent"], text="✕  " + self.words(str(why)))
            self._copied.pack(fill="x", pady=(0, self._px(4)))

    def _side_of(self, widget: Any) -> str:
        """The side a widget is in - "" for the bar and the foot."""
        for side, area in self._areas.items():
            if self._within(widget, area):
                return side
        # A WHOLE PROJECT'S PARTS ABOVE, each zoomed by itself (the user, 2026-11-08).
        for part, (_canvas, area, _inner) in zip(("guide", "made"), getattr(self, "_top_scrolls", [])):
            if self._within(widget, area):
                return part
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
        return int(round(value * self._scale * self._screen))

    def _points(self, whose: str, more: int) -> int:
        zoom = self.zoom if whose == "chrome" else self.zooms.get(whose, ZOOM_START)
        return max(sizes.SMALLEST_WORDS, int(round((self.size + more) * ZOOMS[zoom])))

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
        self._pressed_at = (event.x_root, event.y_root)
        self._dragging = False

    def _move(self, event: Any) -> None:
        """Moved by its bar - at most once a frame (it flickered)."""
        at = getattr(self, "_pressed_at", None)
        if not getattr(self, "_dragging", False) and at is not None and (
                abs(event.x_root - at[0]) > CLICK_SLACK or abs(event.y_root - at[1]) > CLICK_SLACK):
            self._dragging = True                # being moved: no longer a click
            for one in getattr(self, "_bar_parts", ()):
                try:
                    one.configure(cursor="fleur")
                except tk.TclError:      # pragma: no cover - the note went
                    pass
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
        self._top_scrolls: List[Any] = []
        self._words = None
        self.folded = False                  # built whole; folded only by the user
        # LAID OUT ON THE SCREEN, UNSEEN, BEFORE ITS WORDS ARE MEASURED: a
        # text not yet on the screen is as narrow as nothing, and counts a
        # line for every letter. Unseen while it is (no flicker), at the size
        # it asks for (`geometry("")`), shown once it is placed.
        self._seen(False)
        window.geometry("")
        item = self.note.get("item") or {}
        sides, width, around = self._side_widths(item)
        self._scale, self._ctx = ZOOMS[self.zoom], "chrome"
        # Its outline, then its rim - where its edges are taken - then the card.
        rim = tk.Frame(window, bg=self.palette["bg"])
        rim.pack(fill="both", expand=True, padx=1, pady=1)
        card = tk.Frame(rim, bg=self.palette["bg"])
        card.pack(fill="both", expand=True, padx=self._rim, pady=self._rim)
        self._edges(rim)
        bar = self._bar(card)
        foot = self._foot(card)
        # Each side what it says, scrolled when the screen is not tall enough.
        # A WHOLE PROJECT'S: THREE PARTS, ONE ABOVE ANOTHER (the user, 2026-11-08: "everything it made
        # 应该是个单独的panel ... 分成上下3块"; "上下的高度也可以用鼠标调整") - its guide, what it made,
        # the item chosen - each scrolled by itself, the lines between them dragged.
        stack = None
        tops_parts: List[Any] = []
        if (self.note.get("project") and self.note.get("items")) or self.note.get("approval"):
            stack = tk.PanedWindow(card, orient="vertical", bd=0, sashwidth=self._px(7),
                                   sashrelief="flat", bg=self.palette["edge"], opaqueresize=True,
                                   showhandle=False)
            stack.pack(side="top", fill="both", expand=True)
            if self.note.get("approval"):
                # NO GUIDE ON TOP: the rows to approve, then the one chosen.
                tops_parts = [self._approval_part(stack, width - around)]
            else:
                tops_parts = list(self._project_parts(stack, width - around))
            self._stack_parts = tops_parts            # their heights kept through a fold (`fold`)
            for part in tops_parts:
                stack.add(part, minsize=self._px(48))
            stack.bind("<ButtonRelease-1>", lambda _e: self._keep_stack(), add="+")
        middle = tk.Frame(stack if stack is not None else card, bg=self.palette["bg"])
        if stack is not None:
            stack.add(middle, minsize=self._px(80))
        else:
            middle.pack(side="top", fill="both", expand=True)
        middle.rowconfigure(0, weight=1)
        self._middle, self._columns, self._scrolling, self._unfitted = middle, {}, [], set()
        panes = self._make_sides(middle, sides, item)
        window.update()
        self._fit_texts()
        window.update_idletasks()
        room = window.winfo_screenheight() - TOP - BOTTOM \
            - bar.winfo_reqheight() - foot.winfo_reqheight() - 2 - 2 * self._rim
        # THE PROJECT'S TWO PARTS COUNTED IN (the user, 2026-11-08: an item chosen showed only once
        # the window was dragged - its height had left them out, and the item below was cut off).
        tops = 0
        if stack is not None:
            heights = self._stack_heights(room)
            for part, height in zip(tops_parts, heights):
                stack.paneconfigure(part, height=height)
            tops = sum(heights[:len(tops_parts)]) + len(tops_parts) * int(stack.cget("sashwidth"))
            room = max(self._px(120), room - tops)

        def natural(one: tuple) -> int:
            if one[0] == "item":
                return room if one[4] is not None and one[4] is self._long \
                    else one[3].winfo_reqheight()
            return one[4].winfo_reqheight()
        if self._size is not None:
            shown = max(60, min(max(self._least[1], self._size[1]) - bar.winfo_reqheight()
                                - foot.winfo_reqheight() - 2 - 2 * self._rim - tops, room))
        else:
            shown = max(60, min(max(natural(one) for one in panes), room))
        self._fit_sides(panes, shown)
        self._scale, self._ctx = ZOOMS[self.zoom], "chrome"
        self._wheel()
        # Sized from here on - by its edges, its corner, the line between its
        # sides - what it says follows, nothing built again.
        window.bind("<Configure>", self._configured, add="+")
        self._grip.lift()                    # its corner over whatever was laid out after
        for strip in self._strips:
            strip.lift()
        window.update_idletasks()
        height = bar.winfo_reqheight() + tops + shown + foot.winfo_reqheight() + 2 + 2 * self._rim
        right, y = self._place or (window.winfo_screenwidth() - RIGHT, TOP)
        x = max(0, min(right - width, window.winfo_screenwidth() - width))
        y = max(0, min(y, window.winfo_screenheight() - height - BOTTOM // 2))
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.update_idletasks()
        self._seen(True)

    def _side_widths(self, item: Dict[str, Any]) -> tuple:
        """The sides and how wide each is - by its zoom, the screen, the width it was dragged to and
        the line between them: (sides, the whole width, what is around them)."""
        window = self.window
        sides = ([("item", int(LEFT_WIDTH * self._screen * ZOOMS[self.zooms["item"]]))]
                 if item.get("text") or item.get("meta") or item.get("parts") else []) \
            + [("how", int(WIDTH * self._screen * ZOOMS[self.zooms["how"]]))]
        # Wider than the screen: every side narrowed alike.
        room_wide = window.winfo_screenwidth() - 2 * RIGHT
        around = (len(sides) - 1) * DIVIDER + 2 + 2 * self._rim
        whole = sum(one for _side, one in sides)
        if whole + around > room_wide:
            sides = [(side, int(one * (room_wide - around) / whole)) for side, one in sides]
            whole = sum(one for _side, one in sides)
        if self._size is not None:
            # The width the user dragged it to, shared out as the sides were.
            want = max(self._least[0], min(self._size[0], room_wide)) - around
            sides = [(side, max(self._side_least, int(one * want / whole))) for side, one in sides]
            whole = sum(one for _side, one in sides)
        if self._split is not None and len(sides) == 2:
            # Shared as the line between them was dragged.
            left = min(max(self._side_least, int(whole * self._split)), max(self._side_least,
                                                                      whole - self._side_least))
            sides = [(sides[0][0], left), (sides[1][0], max(self._side_least, whole - left))]
        width = sum(one for _side, one in sides) + around
        return sides, width, around

    def _make_sides(self, middle: Any, sides: List[Any], item: Dict[str, Any]) -> List[Any]:
        """Each side made in `middle`: what was handed over, how it is used - to be fitted."""
        panes: List[Any] = []
        column = 0
        for n, (side, wide) in enumerate(sides):
            self._scale, self._ctx = ZOOMS[self.zooms[side]], side
            if n:
                self._divider(middle).grid(row=0, column=column, sticky="ns")
                column += 1
            pane = tk.Frame(middle, bg=self.palette["bg"], width=wide)
            pane.grid(row=0, column=column, sticky="nsew")
            # The sides share the width by weight - as they were made, as the
            # line between them was dragged - whatever the width is.
            middle.columnconfigure(column, weight=max(1, wide), uniform="sides",
                                   minsize=self._side_least)
            self._columns[side] = column
            column += 1
            self._areas[side] = pane
            thumb = tk.Canvas(pane, bg=self.palette["bg"], highlightthickness=0, bd=0,
                              width=self._px(SCROLL_WIDE))
            wrap = wide - self._px(SCROLL_WIDE) - 2 * self._px(18)
            if side == "item" and not item.get("parts"):
                # Its lines above; its words under them, scrolled within
                # themselves however long they are.
                inner = tk.Frame(pane, bg=self.palette["bg"], padx=self._px(18), pady=self._px(6))
                # Laid out before its words are measured: a text with no
                # width yet counts a line for every letter.
                inner.pack(side="left", fill="both", expand=True)
                words = self._item(inner, item, wrap)
                panes.append((side, pane, thumb, inner, words, wide))
            else:
                canvas = tk.Canvas(pane, bg=self.palette["bg"], highlightthickness=0, bd=0,
                                   width=wide - self._px(SCROLL_WIDE))
                body = tk.Frame(canvas, bg=self.palette["bg"], padx=self._px(18), pady=self._px(6))
                shown_in = canvas.create_window(0, 0, window=body, anchor="nw")
                # Its words as wide as the side, whatever its width becomes.
                canvas.bind("<Configure>", lambda e, c=canvas, i=shown_in: (
                    c.itemconfigure(i, width=max(1, e.width)), self._reflow_soon()))
                if side == "item":
                    self._item(body, item, wrap)             # a form: its parts, each a box
                else:
                    self._body(body, wrap)
                panes.append(("canvas", pane, thumb, canvas, body, wide, side))
        return panes

    def _fit_sides(self, panes: List[Any], shown: int) -> None:
        """Each side made as tall as `shown`, scrolled within itself when what it says is taller."""
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

    def _swap_item_below(self) -> bool:
        """A WHOLE PROJECT'S ITEM CHOSEN, SHOWN WITH NOTHING ELSE TOUCHED (the user, 2026-11-08: "有没有
        完全没有refresh的方法"): the item's sides made behind the ones shown, as tall, and put in
        their place at once; its guide and the list of what it made as they are, the list's row for
        it marked. False where it cannot be (not a project's panel): built again, then."""
        stack, old = getattr(self, "_stack", None), getattr(self, "_middle", None)
        if stack is None or old is None or self.window is None:
            return False
        try:
            if not (stack.winfo_exists() and old.winfo_exists()):
                return False
            self.window.update_idletasks()
            shown = max(60, old.winfo_height())
            self._size = (self.window.winfo_width(), self.window.winfo_height())
            self._mark_made_rows()
            item = self.note.get("item") or {}
            sides, _width, _around = self._side_widths(item)
            fresh = tk.Frame(stack, bg=self.palette["bg"])
            fresh.place(in_=old, x=0, y=0, relwidth=1, relheight=1)
            fresh.lower(old)                     # behind the one shown while it is made
            fresh.rowconfigure(0, weight=1)
            kept_texts = [one for one in self._texts if not self._within(one, old)]
            self._texts, self._long = kept_texts, None
            self._areas, self._columns, self._scrolling, self._unfitted = {}, {}, [], set()
            self._panes = []
            self._middle = fresh
            panes = self._make_sides(fresh, sides, item)
            self.window.update_idletasks()
            self._fit_texts()
            self.window.update_idletasks()
            self._fit_sides(panes, shown)
            self._scale, self._ctx = ZOOMS[self.zoom], "chrome"
            height = old.winfo_height()
            fresh.place_forget()
            stack.add(fresh, after=old, minsize=self._px(80), height=height)
            stack.forget(old)
            old.destroy()
            self._texts = [one for one in self._texts if one.winfo_exists()]
            self._grip.lift()
            for strip in self._strips:
                strip.lift()
            return True
        except tk.TclError:
            log.debug("the item could not be put in place", exc_info=True)
            return False

    def _mark_made_rows(self) -> None:
        """The list of what it made, its rows drawn again - the one now shown below marked."""
        rows = getattr(self, "_made_rows", None)
        if rows is None or not rows.winfo_exists():
            return
        if self.note.get("approval"):
            self._draw_approval_rows()
            return
        self._scale, self._ctx = ZOOMS[self.zooms["made"]], "made"
        wrap = getattr(self, "_made_wrap", self._px(300))
        current = int(self.note.get("n") or 0)
        for child in rows.winfo_children():
            child.destroy()
        for one in list(self.note.get("items") or [])[:30]:
            self._project_row(rows, one, current, wrap)
        self._scale, self._ctx = ZOOMS[self.zoom], "chrome"

    def _configured(self, event: Any) -> None:
        """Its own size changed - not a part of it, not where it stands: moved
        by its bar, nothing is laid out again (it flickered)."""
        if event.widget is not self.window or self.folded:
            return                           # folded: only its bar, nothing to lay out
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
        if self.window is None or self.folded:
            return                           # laid out when it is opened again
        self._reflowing = True
        try:
            # ITS NEW SIZE GIVEN TO ITS PARTS BEFORE THEIR WORDS ARE MEASURED:
            # measured first, on a busy machine they were measured at the
            # width they had - narrowed, the words kept their old lines
            # (measured 2026-11-02: 2 of 3 under load, before and after).
            self.window.update_idletasks()
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
        """Its own bar, dragged to move it - one ✕ for both sides, and –
        beside it to fold the note to this bar (□ to open it again)."""
        # OF THE BODY'S OWN GROUND, a line under it (the user, 2026-11-08: a ring of the ground
        # showed round a bar of another, plainest at the rounded corners).
        ground = self.palette["bg"]
        bar = tk.Frame(card, bg=ground, padx=self._px(12), pady=self._px(7))
        bar.pack(side="top", fill="x")
        tk.Frame(card, bg=self.palette["edge"], height=1).pack(side="top", fill="x")
        self._bar_frame = bar
        dot = tk.Canvas(bar, width=self._px(9), height=self._px(9), bg=ground,
                        highlightthickness=0, bd=0)
        dot.create_oval(1, 1, self._px(9) - 1, self._px(9) - 1, fill=self.palette["accent"],
                        outline="")
        # Its buttons packed first: a long name is cut short, never them.
        close = self._flat(bar, "✕", self.close, ground)
        close.pack(side="right")
        self._close_button = close
        # FOLDED OR OPEN, DRAWN AS THE CHAT'S IS (the user, 2026-11-08: "最大化按钮没有对齐，大小也不对";
        # "你之前chat的框不是画的挺好，参考那个画"): a line, or a square, as wide as the ✕ beside it,
        # in the middle of its line - not a letter, whose size and place each font gives its own.
        self._fold_button = tk.Canvas(bar, bg=ground, highlightthickness=0, bd=0, cursor="hand2")
        self._fold_ink = self.palette["muted"]
        self._draw_fold()
        on_release(self._fold_button, self.toggle_folded)
        self._fold_button.pack(side="right")
        if self.note.get("many") and self.on_folder is not None:
            # SEVERAL THINGS: their folder, from here (the user, 2026-11-08: "instruction panel如果有多个
            # deliverable也会有一个open folder的选项").
            folder = self._flat(bar, self.words("Open folder"), lambda: None, ground, small=True)
            on_release(folder, lambda b=folder: (self.on_folder(), self._flash(b)))
            folder.pack(side="right", padx=(0, self._px(6)))
            self._folder_button = folder
        # UNDER THE POINTER, ONLY THEIR MARK BRIGHTER - no square (the user, 2026-11-08: "右上角还是
        # 有一个小的黑框": the square they lit, on a project's dark blue).
        close.bind("<Enter>", lambda _e: close.configure(bg=ground, fg=self.palette["ink"]))
        close.bind("<Leave>", lambda _e: close.configure(bg=ground, fg=self.palette["muted"]))

        def lit(on: bool) -> None:
            self._fold_ink = self.palette["ink"] if on else self.palette["muted"]
            self._draw_fold()
        self._fold_button.bind("<Enter>", lambda _e: lit(True))
        self._fold_button.bind("<Leave>", lambda _e: lit(False))
        dot.pack(side="left", padx=(0, self._px(8)))
        heading = tk.Label(bar, text=self.heading(), bg=ground,
                           fg=self.palette["muted"], font=self._font(-1, "bold"), anchor="w")
        heading.pack(side="left", fill="x", expand=True)
        # A CLICK ON ITS BAR FOLDS IT, AND UNFOLDS IT, where it is no button (the user, 2026-11-08:
        # "可以点击header的其它位置collapose, expand") - a drag moves it; no double click.
        self._bar_parts = (bar, dot, heading)
        for one in self._bar_parts:
            one.bind("<ButtonPress-1>", self._grab)
            one.bind("<B1-Motion>", self._move)
            one.bind("<ButtonRelease-1>", self._bar_released, add="+")
            one.configure(cursor="hand2")
        return bar

    def _bar_released(self, event: Any) -> None:
        at = getattr(self, "_pressed_at", None)
        dragged, self._dragging = getattr(self, "_dragging", False), False
        for one in getattr(self, "_bar_parts", ()):
            try:
                one.configure(cursor="hand2")
            except tk.TclError:          # pragma: no cover - the note went
                pass
        if dragged or at is None:
            return
        if abs(event.x_root - at[0]) <= CLICK_SLACK and abs(event.y_root - at[1]) <= CLICK_SLACK:
            self.toggle_folded()

    def _foot(self, card: Any) -> Any:
        foot = tk.Frame(card, bg=self.palette["bg"], padx=self._px(18), pady=self._px(10))
        foot.pack(side="bottom", fill="x")
        self._foot_frame = foot
        hint = "⌘ + / − to zoom" if sys.platform == "darwin" else "Ctrl + / − to zoom"
        tk.Label(foot, text=self.words(hint), bg=self.palette["bg"], fg=self.palette["muted"],
                 font=self._font(-2)).pack(side="left")
        done = tk.Label(foot, text=self.words("Done"), bg=self.palette["accent"],
                        fg=self.palette["on_accent"], font=self._font(-1, "bold"),
                        padx=self._px(14), pady=self._px(4), cursor="hand2")
        done.pack(side="right", padx=(0, self._px(10)))
        on_release(done, self.close)
        grip = tk.Label(card, text="◢", bg=self.palette["bg"], fg=self.palette["thumb_on"],
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
        self._strips, self._strip_places = [], []
        corner = 3 * self._rim
        for edge, cursor in EDGES.items():
            strip = tk.Frame(rim, bg=self.palette["bg"], cursor=cursor)
            wide = corner if len(edge) == 2 or edge in "ns" else self._rim
            high = corner if len(edge) == 2 or edge in "we" else self._rim
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
            self._strip_places.append(place)         # placed again when opened again
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
            width = max(self._least[0], width + dx)
        if "w" in edge:
            grown = max(self._least[0], width - dx)
            left, width = left + width - grown, grown
        if "s" in edge:
            height = max(self._least[1], min(height + dy, self.window.winfo_screenheight() - TOP))
        if "n" in edge:
            grown = max(self._least[1], height - dy)
            top, height = top + height - grown, grown
        if "n" in edge or "w" in edge:
            # BY ITS TOP OR LEFT, AN OUTLINE WHILE IT IS DRAGGED (the user, 2026-11-09: "调整上边框
            # 巨卡"): moved as well as sized, every part of it was drawn again each step - 150 to
            # 260 ms a step, measured, against 5 to 7 by the bottom. Where it will be is shown;
            # it is put there once, let go (`_resize_end`).
            self._asked_geometry = f"{width}x{height}+{left}+{top}"
            self._ghost_at(left, top, width, height)
            return
        self._sized_once_a_frame(f"{width}x{height}+{left}+{top}")

    #: The outline's line, in pixels.
    GHOST_LINE = 2

    def _ghost_at(self, left: int, top: int, width: int, height: int) -> None:
        """Four thin lines where it will be: no part of it drawn again while it is dragged."""
        lines = getattr(self, "_ghost", None)
        if not lines:
            lines = []
            for _ in range(4):
                line = tk.Toplevel(self.window)
                line.overrideredirect(True)
                line.attributes("-topmost", True)
                line.configure(bg=self.palette.get("ink", "#333333"))
                lines.append(line)
            self._ghost = lines
        thick = self.GHOST_LINE
        spots = ((width, thick, left, top), (width, thick, left, top + height - thick),
                 (thick, height, left, top), (thick, height, left + width - thick, top))
        for line, (wide, high, x, y) in zip(lines, spots):
            line.geometry(f"{max(1, wide)}x{max(1, high)}+{x}+{y}")

    def _ghost_gone(self) -> bool:
        lines, self._ghost = getattr(self, "_ghost", None), None
        for line in lines or []:
            try:
                line.destroy()
            except tk.TclError:
                pass
        return bool(lines)

    def _divider(self, middle: Any) -> None:
        """The line between the sides - dragged to share the width anew."""
        divider = tk.Frame(middle, bg=self.palette["bg"], width=DIVIDER, cursor="sb_h_double_arrow")
        line = tk.Frame(divider, bg=self.palette["edge"], width=1, cursor="sb_h_double_arrow")
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
        if whole <= 2 * self._side_least:
            return
        at = min(max(event.x_root - start, self._side_least), whole - self._side_least)
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
        width = max(self._least[0], width + event.x_root - x)
        height = max(self._least[1], min(height + event.y_root - y,
                                   self.window.winfo_screenheight() - TOP))
        self._sized_once_a_frame(f"{width}x{height}")

    def _resize_end(self, _event: Any = None) -> None:
        """Let go: its size and where it stands kept - for a zoom, which lays
        it out again; what it says has followed already (`_reflow`)."""
        if self._resizing is None:
            return
        self._resizing = None
        if self._ghost_gone():
            self._give_geometry()                        # where its outline was: there, at once
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
        button = tk.Label(parent, text=text, bg=bg, fg=self.palette["muted"],
                          font=self._font(-1, "bold") if small else self._font(1),
                          padx=self._px(7), pady=0, cursor="hand2")
        button.bind("<Enter>", lambda _e: button.configure(bg=self.palette["hover"], fg=self.palette["ink"]))
        button.bind("<Leave>", lambda _e: button.configure(bg=bg, fg=self.palette["muted"]))
        on_release(button, act)                  # done when let go over it
        return button

    def _draw_fold(self, folded: Optional[bool] = None) -> None:
        """Its fold mark: a line while it is open, a square while it is folded - each as wide as
        the ✕ beside it is (0.72 of it), in the middle of a box as wide as the ✕'s, as tall as its
        line (chat_bubble._draw_fold)."""
        fold = getattr(self, "_fold_button", None)
        if fold is None:
            return
        folded = self.folded if folded is None else folded
        try:
            font = self._bar_font(1)
            glyph = int(self.root.tk.call("font", "measure", font, "✕"))
            line = int(self.root.tk.call("font", "metrics", font, "-linespace"))
        except tk.TclError:
            return
        side = max(7, int(round(glyph * 0.72)))
        width, height = glyph + 2 * self._px(7), line
        close = getattr(self, "_close_button", None)
        if close is not None:
            # As large as the ✕'s own box, its border and all: the two on one line, one size.
            close.update_idletasks()
            width, height = max(width, close.winfo_reqwidth()), max(height, close.winfo_reqheight())
        fold.configure(width=width, height=height)
        fold.delete("all")
        left, top = (width - side) / 2, (height - side) / 2
        thick = max(1.0, side / 9)
        ink = getattr(self, "_fold_ink", self.palette["muted"])
        if folded:
            fold.create_rectangle(left, top, left + side, top + side, outline=ink, width=thick)
        else:
            middle = top + side / 2
            fold.create_line(left, middle, left + side, middle, fill=ink, width=thick)
        self._fold_mark = "square" if folded else "line"

    def _bar_font(self, more: int) -> str:
        """A font of the bar's, `more` points larger than its words."""
        was = (self._scale, self._ctx)
        self._scale, self._ctx = ZOOMS[self.zoom], "chrome"
        try:
            return self._font(more)
        finally:
            self._scale, self._ctx = was

    def _zoomer(self, row: Any, side: str) -> None:
        """A side's own - and +, at the right of its first row."""
        for text, step in (("+", 1), ("−", -1)):
            self._flat(row, text, lambda s=step: self.zoom_by(s, side),
                       self.palette["bg"]).pack(side="right")

    def _selectable(self, parent: Any, text: str, font: tuple, fg: str, wrap: int,
                    pack: Optional[Dict[str, Any]] = None, editable: bool = False) -> Any:
        """WORDS THAT CAN BE SELECTED AND COPIED (module docstring): a text
        that cannot be written in - or, `editable`, one that can - as wide
        as `wrap`, made as tall as its lines once it is laid out
        (`_fit_texts`); Ctrl+C and the right button's menu copy from it."""
        box = tk.Text(parent, wrap="word", font=font, bg=self.palette["bg"], fg=fg, relief="flat",
                      bd=0, highlightthickness=0, padx=0, pady=0, cursor="xterm", height=1,
                      selectbackground=self.palette["select"], selectforeground=self.palette["ink"],
                      inactiveselectbackground=self.palette["select"], exportselection=True,
                      takefocus=0, undo=editable, insertbackground=self.palette["ink"])
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
        menu = tk.Menu(self.window, tearoff=0, bg=self.palette["bar"], fg=self.palette["ink"],
                       activebackground=self.palette["select"], activeforeground=self.palette["ink"],
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
        head = tk.Frame(body, bg=self.palette["bg"])
        head.pack(fill="x", pady=(self._px(4), self._px(2)))
        tk.Label(head, text=self.words(KINDS.get(item.get("kind"), "Result")),
                 bg=self.palette["bg"], fg=self.palette["accent"], font=self._font(-1, "bold"),
                 anchor="w").pack(side="left")
        self._zoomer(head, "item")
        self._save_button = None
        copy = None
        if item.get("text"):
            # COPY ALL (the user, 2026-11-01): the whole of it, as it now is -
            # said on the button when done.
            copy = self._flat(head, self.words("Copy all"), lambda: None, self.palette["bg"],
                              small=True)
            on_release(copy, lambda b=copy: (self._copy_item(), self._flash(b)))
            copy.pack(side="right", padx=(0, self._px(8)))
        if self.on_open is not None and item.get("kind") not in OPENED_HERE:
            # OPENED FROM HERE: where it opens - its page, folder, file, email; an answer or a text
            # is read here, and has none. ON A LINE OF ITS OWN, under what it is (the user,
            # 2026-11-08: "open XX放在email(item名)下面一行。单独成行") - "Open the email", no "again".
            line = tk.Frame(body, bg=self.palette["bg"])
            line.pack(fill="x", pady=(0, self._px(4)))
            again = self._flat(line, self.words(OPEN_ITEM.get(item.get("kind"), "Open")),
                               lambda: None, self.palette["bg"], small=True)
            on_release(again, lambda b=again: (self._open_again(), self._flash(b)))
            again.pack(side="left")
        if (item.get("text") or item.get("parts")) and True:
            if item.get("editable", True) and self.on_save is not None:
                # Made now, shown beside Copy only while something is not saved.
                self._save_button = self._flat(head, self.words("Save"), self._save,
                                               self.palette["accent"], small=True)
                self._save_button.configure(fg=self.palette["on_accent"])
                self._save_button.bind("<Leave>", lambda _e: self._save_button.configure(
                    bg=self.palette["accent"], fg=self.palette["on_accent"]))
                self._save_after = copy                  # none for a form: its parts have theirs
                if self._unsaved:
                    self._show_save(True)
        self._head = head
        self._copied = tk.Label(body, text="", bg=self.palette["bg"], fg=self.palette["ticked"],
                                font=self._font(-1, "bold"), wraplength=wrap, justify="left",
                                anchor="w")
        if self.note.get("copied") and self._edited is not None:
            self._copied.configure(fg=self.palette["accent"], text="✎  " + self.words(
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
                             self._font(-1), self.palette["muted"], wrap)
        if item.get("parts"):
            self._parts(body, list(item["parts"]), wrap)
            return None
        if not item.get("text"):
            return None
        if meta:
            tk.Frame(body, bg=self.palette["edge"], height=1).pack(fill="x", pady=self._px(8))
        text = self._edited if self._edited is not None else str(item["text"])
        words = self._selectable(body, text, self._font(), self.palette["ink"], wrap,
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
            button.configure(text="✓  " + self.words(said), fg=self.palette["ticked"])

            def back() -> None:
                try:
                    button.configure(text=words, fg=self.palette["muted"])
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
                  self.palette["accent"])

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
            self._say("✓  " + self.words("Saved - opened again, it is this"), self.palette["ticked"])
        else:
            self._say("✕  " + self.words(saved if isinstance(saved, str) and saved
                                         else "Not saved - copy it, so it is not lost"),
                      self.palette["accent"])

    def _parts(self, body: Any, parts: List[Dict[str, Any]], wrap: int) -> None:
        """A FORM'S PARTS, each in its own box under the label of the box it
        goes into, with its own Copy - written in as the rest, saved together."""
        self._part_boxes = []
        texts = self._edited if isinstance(self._edited, list) else \
            [str(one.get("text") or "") for one in parts]
        for n, part in enumerate(parts):
            row = tk.Frame(body, bg=self.palette["bg"])
            row.pack(fill="x", pady=(self._px(10), self._px(2)))
            tk.Label(row, text=f"{n + 1}.  {part.get('field') or ''}", bg=self.palette["bg"],
                     fg=self.palette["accent"], font=self._font(-1, "bold"),
                     wraplength=wrap - self._px(70), justify="left",
                     anchor="w").pack(side="left")
            box = self._selectable(body, texts[n] if n < len(texts) else "", self._font(),
                                   self.palette["ink"], wrap, editable=True)
            box.bind("<<Modified>>", lambda _e, b=box: self._part_changed(b), add="+")
            copy = self._flat(row, self.words("Copy"), lambda: None, self.palette["bg"], small=True)
            on_release(copy, lambda b=box, c=copy: (
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
                  self.palette["accent"])

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
                label.configure(text=words, fg=self.palette["ticked"])
                if not label.winfo_ismapped():
                    label.pack(fill="x", pady=(0, self._px(4)), after=self._head)
        except tk.TclError:
            pass

    def _body(self, body: Any, wrap: int) -> None:
        """How it is used, on the right: its own zoom, its title, how, what to check."""
        note = self.note
        row = tk.Frame(body, bg=self.palette["bg"])
        row.pack(fill="x")
        self._zoomer(row, "how")
        self._selectable(body, str(note.get("title") or "")[:200], self._font(3, "bold"),
                         self.palette["ink"], wrap,
                         {"fill": "x", "pady": (self._px(2), self._px(2))})
        # WHAT THE AGENT NEEDS TO KNOW (the user, 2026-11-10): the question, standing on its own.
        if note.get("question"):
            # THE QUESTION, THE ANSWER'S WINDOW RIGHT UNDER IT, THEN WHAT IT RESTS ON (seen on this
            # screen, 2026-11-10: the Answer button below the fold, under the details).
            asked, _gap, about = str(note["question"])[:3000].partition("\n\n")
            self._heading(body, "The agent needs to know")
            self._selectable(body, asked, self._font(1, "bold"), self.palette["ink"], wrap)
            if self.on_approve is not None and self.note.get("approval"):
                self._answer_part(body, wrap)
            if about.strip():
                self._selectable(body, about.strip(), self._font(-1), self.palette["muted"], wrap,
                                 {"fill": "x", "pady": (self._px(10), 0)})
        # HOW THE AGENT WOULD DO IT ITSELF, once approved (the user, 2026-11-10): first, apart from
        # the how-to written for a person.
        if note.get("agent_plan"):
            self._heading(body, "How the agent would do it itself")
            self._selectable(body, str(note["agent_plan"])[:3000], self._font(), self.palette["ink"],
                             wrap)
            # THE COMMANDS IT WILL RUN, approved - these alone, exactly (agent/work/runner.py
            # plan_commands): read before the one click.
            self._heading(body, "Commands it will run, exactly")
            commands = [str(one) for one in note.get("agent_commands") or []]
            self._selectable(body, "\n".join(commands)[:3000] if commands else self.words(
                "None - it runs no command outside its own folder."), self._font(),
                self.palette["ink"], wrap)
        if note.get("how"):
            self._heading(body, "How to use it")
            self._selectable(body, str(note["how"])[:1200], self._font(), self.palette["ink"], wrap)
        checks = list(note.get("check") or [])[:10]
        if checks:
            self._heading(body, "Check before you use it")
            for n, one in enumerate(checks):
                self._check(body, n, str(one)[:300], wrap)
        items = list(note.get("items") or [])
        if items and self.on_item is not None and not note.get("project"):
            self._heading(body, "Everything it made")
            for one in items[:30]:
                self._item_link(body, one, wrap)

    # -- A WHOLE PROJECT'S PANEL (the user, 2026-11-08: "先上下分2块，然后下面一块再左右分，上面是project
    # level to do, 包括可以选择的每个item to do"; "每行右边一个按钮，代表打开file同时切换到这个item的
    # instruction panel, 如果直接点左边就是只切换instruction") -------------------------------------
    def _scrolled_part(self, stack: Any, wide: int, header: bool = False) -> tuple:
        """A part of a whole project's panel, scrolled by itself by its own bar: (the part, what is
        written in it, how wide its words wrap) - and, `header`, a row above what scrolls, that
        stays where it is (the part's header)."""
        area = tk.Frame(stack, bg=self.palette["bg"])
        if header:
            self._part_header = tk.Frame(area, bg=self.palette["bg"], padx=self._px(18))
            self._part_header.pack(side="top", fill="x", pady=(self._px(6), self._px(2)))
        thumb = tk.Canvas(area, bg=self.palette["bg"], highlightthickness=0, bd=0,
                          width=self._px(SCROLL_WIDE))
        canvas = tk.Canvas(area, bg=self.palette["bg"], highlightthickness=0, bd=0, height=10)
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=self.palette["bg"], padx=self._px(18), pady=self._px(6))
        shown_in = canvas.create_window(0, 0, window=inner, anchor="nw")

        def fit(_event: Any = None) -> None:
            tall, high = max(1, inner.winfo_reqheight()), max(1, canvas.winfo_height())
            canvas.configure(scrollregion=(0, 0, max(1, canvas.winfo_width()), max(tall, high)))
            if tall > high + 1 and not thumb.winfo_ismapped():
                thumb.pack(side="right", fill="y", before=canvas)
            elif tall <= high + 1 and thumb.winfo_ismapped():
                thumb.pack_forget()
        inner.bind("<Configure>", fit)
        canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(shown_in, width=max(1, e.width)),
                                              fit()))
        self._scroller(canvas, thumb, 200)
        self._top_scrolls.append((canvas, area, inner))
        wrap = max(self._px(200), wide - 2 * self._px(18) - self._px(SCROLL_WIDE))
        return area, inner, wrap

    def _project_parts(self, stack: Any, wide: int) -> tuple:
        """A whole project's two parts above the item: its guide - the project, how to use it all,
        what to check - and what it made, a row for each, that chooses what is shown below."""
        project = self.note.get("project") or {}
        self._scale, self._ctx = ZOOMS[self.zooms["guide"]], "guide"
        guide, top, wrap = self._scrolled_part(stack, wide)
        # EACH PART ITS OWN - AND + (the user, 2026-11-08: "另外两个panel还没有+-"), as the sides have.
        first = tk.Frame(top, bg=self.palette["bg"])
        first.pack(fill="x")
        self._zoomer(first, "guide")
        if project.get("title"):
            self._selectable(top, str(project["title"])[:200], self._font(3, "bold"),
                             self.palette["ink"], wrap, {"fill": "x", "pady": (0, self._px(2))})
        if project.get("about"):
            # ITS GUIDE, step by step, as its work wrote it - or, from before guides, what it said -
            # whole: its part scrolls.
            self._heading(top, "How to use it all" if project.get("guided") else "About the whole")
            self._about_text = self._selectable(top, str(project["about"]), self._font(-1),
                                                self.palette["ink"], wrap)
        checks = list(project.get("check") or [])[:10]
        if checks:
            self._heading(top, "Check before you are done")
            before = {str(one) for one in (project.get("ticked") or [])}
            ticks = [str(one)[:300] in before for one in checks]
            for n, one in enumerate(checks):
                self._project_check(top, ticks, n, str(one)[:300], wrap)
        self._scale, self._ctx = ZOOMS[self.zooms["made"]], "made"
        made, inner, wrap = self._scrolled_part(stack, wide, header=True)
        # ITS HEADER PRESSED, IT FOLDS TO ITSELF, OR OPENS (the user, 2026-11-08: "everything it made
        # 这个是可以点击header collapose的") - folded as it was when another item is chosen.
        folded = bool(getattr(self, "_made_folded", False))
        # A HEADER (the user, 2026-11-08: "要看起来像是一个header (不需要那个箭头，字体调大)"): its words
        # large, nothing before them; pressed, the part folds - its own - and + beside.
        # ITS HEADER STAYS WHERE IT IS (the user, 2026-11-08: "everything it made 是第二个panel的header,
        # 不会随着下拉框被拉下去"): above what scrolls, not in it.
        top_row = self._part_header
        self._zoomer(top_row, "made")
        head = tk.Label(top_row, text=self.words("Everything it made"), bg=self.palette["bg"],
                        fg=self.palette["ink"], cursor="hand2", font=self._font(3, "bold"),
                        anchor="w")
        head.pack(side="left", fill="x", expand=True)
        rows = tk.Frame(inner, bg=self.palette["bg"])
        if not folded:
            rows.pack(fill="x")
        current = int(self.note.get("n") or 0)
        for one in list(self.note.get("items") or [])[:30]:
            self._project_row(rows, one, current, wrap)
        self._stack, self._made_area, self._made_head, self._made_rows = stack, made, head, rows
        self._made_wrap = wrap
        on_release(head, self._fold_made)
        return guide, made

    # -- the approvals panel -----------------------------------------------------------------
    def _approval_part(self, stack: Any, wide: int) -> Any:
        """THE APPROVALS' PART, ON TOP: a header that stays where it is, the one line on what is
        asked, and a row for each thing - pressed, shown below; its answers beside it."""
        self._scale, self._ctx = ZOOMS[self.zooms["made"]], "made"
        made, inner, wrap = self._scrolled_part(stack, wide, header=True)
        top_row = self._part_header
        self._zoomer(top_row, "made")
        head = tk.Label(top_row, text=self.words("To approve"), bg=self.palette["bg"],
                        fg=self.palette["ink"], font=self._font(3, "bold"), anchor="w")
        head.pack(side="left", fill="x", expand=True)
        self._selectable(inner, self.words(
            "Approve it, and the agent does what it said it would - only that, its evidence kept. "
            "Or send it yourself, then mark it here."),
            self._font(-1), self.palette["muted"], wrap, {"fill": "x", "pady": (0, self._px(4))})
        rows = tk.Frame(inner, bg=self.palette["bg"])
        rows.pack(fill="x")
        self._stack, self._made_area, self._made_head, self._made_rows = stack, made, head, rows
        self._made_wrap = wrap
        self._draw_approval_rows()
        return made

    def _draw_approval_rows(self) -> None:
        rows = self._made_rows
        self._scale, self._ctx = ZOOMS[self.zooms["made"]], "made"
        for child in rows.winfo_children():
            child.destroy()
        current = int(self.note.get("approval_at") or 0)
        listed = list((self.note.get("approval") or {}).get("rows") or [])[:30]
        # READY ON TOP, THE REST FOLDED (the user, 2026-11-10): what is not ready yet - the agent still
        # on it, not begun, or gone - under one line, opened when pressed.
        later = [k for k, one in enumerate(listed)
                 if str(one.get("state") or "") not in ("ready", "needs_answer")]
        for k, one in enumerate(listed):
            if k not in later:
                self._approval_row(rows, k, one, k == current, self._made_wrap)
        if later:
            opened = bool(getattr(self, "_approval_later_open", False)) or current in later
            fold = tk.Label(rows, text=("▾  " if opened else "▸  ") + self.words("Not ready yet")
                            + f" ({len(later)})", bg=self.palette["bg"], fg=self.palette["muted"],
                            font=self._font(-1, "bold"), anchor="w", cursor="hand2")
            fold.pack(fill="x", pady=(self._px(8), 0))
            on_release(fold, self._fold_approval_later)
            self._approval_fold = fold
            if opened:
                for k in later:
                    self._approval_row(rows, k, listed[k], k == current, self._made_wrap)
        self._scale, self._ctx = ZOOMS[self.zoom], "chrome"

    def _fold_approval_later(self) -> None:
        """The things not ready yet, shown or folded again."""
        self._approval_later_open = not bool(getattr(self, "_approval_later_open", False))
        self._draw_approval_rows()

    def _approval_row(self, top: Any, k: int, one: Dict[str, Any], chosen: bool, wrap: int) -> None:
        """One thing to approve: its title - pressed, what the agent made is shown below - what state
        it is in, and the three answers; answered, what was said."""
        ground = self.palette["accent"] if chosen else self.palette["bar"]
        ink = self.palette["on_accent"] if chosen else self.palette["ink"]
        row = tk.Frame(top, bg=ground, cursor="hand2")
        row.pack(fill="x", pady=(self._px(3), 0))
        title = str(one.get("title") or "").strip() or self.words("Result")
        pick = tk.Label(row, text=("▸  " if chosen else "") + title, bg=ground, fg=ink,
                        font=self._font(0, "bold" if chosen else "normal"), anchor="w",
                        justify="left", wraplength=wrap - self._px(20), cursor="hand2")
        pick.pack(side="top", fill="x", padx=(self._px(10), self._px(8)), pady=(self._px(6), 0))
        #: The row's answers: pressed, they answer - everything else in it chooses it.
        answers: List[Any] = []
        answered = str(one.get("answered") or "")
        said = dict(APPROVAL_ANSWERS).get(answered, "")
        state = str(one.get("state") or "")
        under = tk.Frame(row, bg=ground)
        under.pack(side="top", fill="x", padx=(self._px(10), self._px(4)), pady=(self._px(2), self._px(6)))
        if answered.startswith("answer:"):
            tk.Label(under, text="✓  " + self.words(APPROVAL_STATES["answered"]), bg=ground,
                     fg=self.palette["ticked"] if not chosen else ink, font=self._font(-1, "bold"),
                     anchor="w").pack(side="left")
        elif state == "needs_answer" and not answered:
            # ASKED (the user, 2026-11-10: "make the question clear") - answered below, chosen.
            tk.Label(under, text=str(one.get("question") or ""), bg=ground, fg=ink, font=self._font(-1),
                     anchor="w", justify="left", wraplength=wrap - self._px(30)).pack(fill="x")
            tk.Label(under, text=self.words("Answer it below."), bg=ground,
                     fg=ink if chosen else self.palette["muted"], font=self._font(-1, "bold"),
                     anchor="w").pack(fill="x")
        elif answered:
            words = ("✓  " + self.words(said)) if said else self.words(APPROVAL_STATES["closed"])
            tk.Label(under, text=words, bg=ground, fg=self.palette["ticked"] if not chosen else ink,
                     font=self._font(-1, "bold"), anchor="w").pack(side="left")
        elif state != "ready":
            tk.Label(under, text=self.words(APPROVAL_STATES.get(state, APPROVAL_STATES["not_started"])),
                     bg=ground, fg=self.palette["muted"] if not chosen else ink, font=self._font(-1),
                     anchor="w").pack(side="left")
        else:
            first, *others = APPROVAL_ANSWERS
            # THE ONE CLICK, ITS OWN LINE (the user, 2026-11-10: "一键approve按钮"): the main button.
            main_bg = self.palette["bg"] if chosen else self.palette["accent"]
            main_fg = self.palette["ink"] if chosen else self.palette["on_accent"]
            go = tk.Label(under, text=self.words(first[1]), bg=main_bg, fg=main_fg,
                          font=self._font(-1, "bold"), padx=self._px(9), pady=self._px(2),
                          cursor="hand2")
            on_release(go, lambda h=first[0]: self._answer(k, h))
            answers.append(go)
            go.pack(side="left")
            more = tk.Frame(row, bg=ground)
            more.pack(side="top", fill="x", padx=(self._px(4), self._px(4)), pady=(0, self._px(6)))
            for how, words in others:
                button = self._flat(more, self.words(words), lambda: None, ground, small=True)
                if chosen:                       # readable on the chosen row's colour
                    button.configure(fg=ink)
                    button.bind("<Leave>", lambda _e, b=button: b.configure(bg=ground, fg=ink))
                on_release(button, lambda h=how: self._answer(k, h))
                answers.append(button)
                button.pack(side="left", padx=(0, self._px(6)))
        # CHOSEN BY ANY PART OF IT BUT ITS ANSWERS (the user, 2026-11-11: "approval的一行的非button位置
        # 都能选中"): its ground, its title, its state, its question, the space beside the buttons.
        def chooses(widget: Any) -> None:
            if widget in answers:
                return
            on_release(widget, lambda: self._choose_approval(k))
            try:
                widget.configure(cursor="hand2")
            except tk.TclError:                  # pragma: no cover - one that takes none
                pass
            for child in widget.winfo_children():
                chooses(child)
        chooses(row)

    def _answer_part(self, body: Any, wrap: int) -> None:
        """THE ANSWER'S WINDOW (the user, 2026-11-10: "你得给我一个回答的窗口呀"): right under the
        question, a box to write in, and Answer - Ctrl+Enter too; answered, said so in its place."""
        area = tk.Frame(body, bg=self.palette["bg"])
        area.pack(fill="x", pady=(self._px(8), 0))
        self._answer_area = area
        k = int(self.note.get("approval_at") or 0)
        rows = (self.note.get("approval") or {}).get("rows") or []
        if 0 <= k < len(rows) and str(rows[k].get("answered") or "").startswith("answer:"):
            self._answered_here()
            return
        box = tk.Text(area, height=2, wrap="word", font=self._font(), relief="flat",
                      bg=self.palette["bar"], fg=self.palette["ink"],
                      insertbackground=self.palette["ink"], padx=self._px(6), pady=self._px(4))
        box.pack(fill="x")
        self._answer_box = box
        send = tk.Label(area, text=self.words("Answer"), bg=self.palette["accent"],
                        fg=self.palette["on_accent"], font=self._font(-1, "bold"),
                        padx=self._px(12), pady=self._px(3), cursor="hand2")
        send.pack(anchor="w", pady=(self._px(6), 0))
        on_release(send, self._send_answer)
        box.bind("<Control-Return>", lambda _e: (self._send_answer(), "break")[1])

    def _send_answer(self) -> None:
        """What was written under the question, sent for the row it is of."""
        box = getattr(self, "_answer_box", None)
        if box is None:
            return
        try:
            text = box.get("1.0", "end").strip()
        except tk.TclError:
            return
        if text:
            self._answer(int(self.note.get("approval_at") or 0), "answer:" + text)

    def _answered_here(self) -> None:
        """The answer's window, once answered: said so in its place."""
        area = getattr(self, "_answer_area", None)
        try:
            if area is None or not area.winfo_exists():
                return
            for child in area.winfo_children():
                child.destroy()
            tk.Label(area, text="✓  " + self.words(APPROVAL_STATES["answered"]), bg=self.palette["bg"],
                     fg=self.palette["ticked"], font=self._font(-1, "bold"),
                     anchor="w").pack(fill="x")
        except tk.TclError:
            pass
        self._answer_box = None

    def _choose_approval(self, k: int) -> None:
        """A row chosen: what the agent made for it shown below (`on_item(k)` brings its note to
        `show_item`)."""
        if self.on_item is None or k == int(self.note.get("approval_at") or 0):
            return
        self.note["approval_at"] = k
        self._draw_approval_rows()
        self.on_item(k)

    def _answer(self, k: int, how: str) -> None:
        rows = (self.note.get("approval") or {}).get("rows") or []
        if self.on_approve is None or not 0 <= k < len(rows) or rows[k].get("answered"):
            return
        rows[k]["answered"] = "..."                       # not twice while it is kept
        try:
            self.on_approve(k, how)
        except Exception:                                # pragma: no cover - said on the row
            log.warning("an approval could not be kept", exc_info=True)
            rows[k]["answered"] = ""

    def approved(self, k: int, how: str, ok: bool) -> None:
        """How an answer went: kept - said on its row; not - its answers back."""
        rows = (self.note.get("approval") or {}).get("rows") or []
        if not 0 <= k < len(rows):
            return
        rows[k]["answered"] = how if ok else ""
        try:
            if self.window is not None and self._made_rows.winfo_exists():
                self._draw_approval_rows()
        except (tk.TclError, AttributeError):
            pass
        if ok and how.startswith("answer:") and k == int(self.note.get("approval_at") or 0):
            self._answered_here()

    def _fold_made(self) -> None:
        """What it made, folded to its header - or opened again, as tall as it was."""
        stack, made, head, rows = (getattr(self, name, None) for name in
                                   ("_stack", "_made_area", "_made_head", "_made_rows"))
        if stack is None or made is None:
            return
        try:
            folding = not getattr(self, "_made_folded", False)
            if folding:
                self._made_open_height = made.winfo_height()
                rows.pack_forget()
                self.window.update_idletasks()
                stack.paneconfigure(made, height=self._part_header.winfo_reqheight()
                                    + self._px(12))
            else:
                rows.pack(fill="x")
                stack.paneconfigure(made, height=max(self._px(48), int(getattr(
                    self, "_made_open_height", 0) or self._px(160))))
            self._made_folded = folding
            self._keep_stack()
        except tk.TclError:
            pass

    def _stack_heights(self, room: int) -> tuple:
        """How tall the guide and the list of what it made are: as the user dragged them, else as
        tall as they need, at most their shares of the screen - room left for the item below."""
        kept = getattr(self, "_stack_kept", None)
        if kept:
            return kept
        screen = self.window.winfo_screenheight()
        tall = [max(self._px(48), part[2].winfo_reqheight()) for part in self._top_scrolls[:2]]
        if self.note.get("approval"):
            return (min(tall[0], int(screen * APPROVAL_SHARE), max(self._px(48), room // 2)),)
        guide = min(tall[0], int(screen * GUIDE_SHARE), max(self._px(48), room // 3))
        made = min(tall[1], int(screen * MADE_SHARE), max(self._px(48), room // 4))
        return guide, made

    def _keep_stack(self) -> None:
        """The lines between the parts dragged: their heights kept for this panel - an item chosen
        below, they stay."""
        try:
            parts = self._top_scrolls[:1 if self.note.get("approval") else 2]
            if parts and len(parts) == (1 if self.note.get("approval") else 2):
                self._stack_kept = tuple(part[1].winfo_height() for part in parts)
        except tk.TclError:
            pass

    def _project_check(self, top: Any, ticks: List[bool], n: int, text: str, wrap: int) -> None:
        """One thing to check across the whole project: ticked, kept as the project's own."""
        row = tk.Frame(top, bg=self.palette["bg"])
        row.pack(fill="x", pady=(self._px(2), 0))
        box = tk.Label(row, bg=self.palette["bg"], font=self._font(1), width=2, anchor="nw",
                       cursor="hand2")
        box.pack(side="left", anchor="n")
        said = tk.Label(row, text=text, bg=self.palette["bg"], font=self._font(-1),
                        wraplength=wrap - self._px(28), justify="left", anchor="w")
        said.pack(side="left", fill="x", expand=True)
        checks = [str(one)[:300] for one in ((self.note.get("project") or {}).get("check") or [])]

        def draw() -> None:
            on = ticks[n]
            box.configure(text="☑" if on else "☐",
                          fg=self.palette["ticked"] if on else self.palette["muted"])
            said.configure(fg=self.palette["muted"] if on else self.palette["ink"])

        def tick(_event: Any = None) -> None:
            ticks[n] = not ticks[n]
            draw()
            words = [one for k, one in enumerate(checks[:len(ticks)]) if ticks[k]]
            project = self.note.get("project") or {}
            project["ticked"] = words                    # as it is, when the item below changes
            if self.on_tick is not None:
                try:
                    self.on_tick(words, {"run": self.note.get("run"), "n": "project"})
                except Exception:                # pragma: no cover - ticked here still
                    log.debug("what was ticked could not be kept", exc_info=True)
        box.bind("<Button-1>", tick)
        draw()

    def _project_row(self, top: Any, one: Dict[str, Any], current: int, wrap: int) -> None:
        n = int(one.get("n") or 0)
        chosen = n == current
        # A ROW THAT IS A BUTTON (the user, 2026-11-08: "类似于一行一行的按钮"): its own ground,
        # lighter under the pointer, the one shown below marked.
        ground = self.palette["accent"] if chosen else self.palette["bar"]
        ink = self.palette["on_accent"] if chosen else self.palette["ink"]
        row = tk.Frame(top, bg=ground, cursor="hand2")
        row.pack(fill="x", pady=(self._px(3), 0))
        kind = self.words(KINDS.get(str(one.get("kind") or ""), "Result"))
        title = str(one.get("title") or "").strip()
        words = ("▸  " if chosen else "") + (f"{kind}: {title}" if title else kind)
        pick = tk.Label(row, text=words, bg=ground, fg=ink,
                        font=self._font(0, "bold" if chosen else "normal"), anchor="w",
                        justify="left", wraplength=wrap - self._px(170), cursor="hand2")
        pick.pack(side="left", fill="x", expand=True, padx=(self._px(10), 0), pady=self._px(6))
        on_release(pick, lambda: self._choose(n, False))
        on_release(row, lambda: self._choose(n, False))
        if not chosen:
            def lit(on: bool) -> None:
                for widget in (row, pick):
                    widget.configure(bg=self.palette["hover"] if on else ground)
            for widget in (row, pick):
                widget.bind("<Enter>", lambda _e: lit(True), add="+")
                widget.bind("<Leave>", lambda _e: lit(False), add="+")
        if str(one.get("kind") or "") not in OPENED_HERE:
            # "Open the email", not "... again" (the user, 2026-11-08: "不要加again").
            opens = self._flat(row, self.words(OPEN_ITEM.get(str(one.get("kind") or ""), "Open")),
                               lambda: None, ground, small=True)
            on_release(opens, lambda: self._choose(n, True))
            opens.pack(side="right", padx=(self._px(8), self._px(4)))

    def _choose(self, n: int, opening: bool) -> None:
        """An item chosen in the project's part: shown below (`on_item(n, opening)` brings its
        note to `show_item`) - and, `opening`, opened where it opens."""
        if self.on_item is None:
            return
        if n == int(self.note.get("n") or 0) and not opening:
            return
        try:
            self.on_item(n, opening)
        except TypeError:                        # one that only shows it
            self.on_item(n)

    def show_item(self, told: Dict[str, Any]) -> None:
        """Below the project's part, this item's note now: what it is, how it is used, what to
        check - ticked as it was left. The project's part as it was."""
        if self.window is None:
            return
        kept = {key: self.note.get(key) for key in ("project", "items", "scope", "work", "approval")}
        if "approval_at" in self.note:
            kept["approval_at"] = self.note["approval_at"]     # chosen in `_choose_approval`
        self.note = dict(told, **{key: value for key, value in kept.items() if value})
        before = {str(one) for one in (self.note.get("ticked") or [])}
        self.ticked = [str(one)[:300] in before or str(one) in before
                       for one in list(self.note.get("check") or [])]
        self._unsaved = False
        if self._swap_item_below():
            return
        # NO FLASH OF THE WHOLE OF IT (the user, 2026-11-08: "切换之后还是要整个panel刷新，能不能不出现
        # 整个panel fresh一下的视觉效果"): made in a window of its own where this one stands, as large,
        # the lines between its parts where they were - shown over this one, which goes once it is
        # drawn. Built again in place, it was hidden while laid out, and the whole of it blinked.
        old = self.window
        try:
            old.update_idletasks()
            self._keep_stack()
            self._place = (old.winfo_x() + old.winfo_width(), old.winfo_y())
            self._size = (old.winfo_width(), old.winfo_height())
        except tk.TclError:
            pass
        self.show()

        def gone() -> None:
            try:
                old.destroy()
            except tk.TclError:
                pass
        try:
            self.window.after(SWAP_AFTER_MS, gone)
        except tk.TclError:                              # pragma: no cover - closed meanwhile
            gone()

    def _item_link(self, body: Any, one: Dict[str, Any], wrap: int) -> None:
        """One thing the project's work made: pressed, its own panel."""
        kind = self.words(KINDS.get(str(one.get("kind") or ""), "Result"))
        title = str(one.get("title") or "").strip()
        link = tk.Label(body, text="↗  " + (f"{kind}: {title}" if title else kind),
                        bg=self.palette["bg"], fg=self.palette["accent"], font=self._font(),
                        wraplength=wrap, justify="left", anchor="w", cursor="hand2")
        link.pack(fill="x", pady=(self._px(3), 0))
        n = int(one.get("n") or 0)
        on_release(link, lambda: self.on_item(n) if self.on_item is not None else None)

    def _ticked_words(self) -> List[str]:
        checks = list(self.note.get("check") or [])
        return [str(one)[:300] for n, one in enumerate(checks) if n < len(self.ticked)
                and self.ticked[n]]

    def _heading(self, body: Any, text: str) -> None:
        tk.Label(body, text=self.words(text), bg=self.palette["bg"], fg=self.palette["accent"],
                 font=self._font(-1, "bold"), anchor="w").pack(fill="x",
                                                               pady=(self._px(12), self._px(4)))

    def _check(self, body: Any, n: int, text: str, wrap: int) -> None:
        """One thing to check: a box to tick - ticked by its box, so what it
        says can be selected - and what it says."""
        row = tk.Frame(body, bg=self.palette["bg"])
        row.pack(fill="x", pady=(self._px(3), 0))
        box = tk.Label(row, bg=self.palette["bg"], font=self._font(1), width=2, anchor="nw",
                       cursor="hand2")
        box.pack(side="left", anchor="n")
        said = self._selectable(row, text, self._font(), self.palette["ink"], wrap - self._px(28),
                                {"side": "left", "fill": "x", "expand": True})

        def draw() -> None:
            on = self.ticked[n]
            box.configure(text="☑" if on else "☐",
                          fg=self.palette["ticked"] if on else self.palette["muted"])
            said.configure(fg=self.palette["muted"] if on else self.palette["ink"])

        def tick(_event: Any = None) -> None:
            self.ticked[n] = not self.ticked[n]
            draw()
            if self.on_tick is not None:
                try:
                    self.on_tick(self._ticked_words(), self.note)
                except Exception:                # pragma: no cover - ticked here still
                    log.debug("what was ticked could not be kept", exc_info=True)
        box.bind("<Button-1>", tick)
        draw()

    # -- scrolling ----------------------------------------------------------------------------
    def _scroller(self, scrolled: Any, thumb: Any, shown: int) -> None:
        """A thin bar of its own, beside what scrolls (a canvas, or a text):
        dragged, or pressed above or below its thumb."""
        least, inset, wide = self._px(28), self._px(SCROLL_INSET), self._px(SCROLL_WIDE)

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
                                   fill=self.palette["thumb"], outline="", tags="thumb")
        scrolled.configure(yscrollcommand=draw)
        held = {"y": None, "at": 0.0}

        def press(event: Any) -> None:
            shown = tall()
            first, last = scrolled.yview()
            top = min(first * shown, shown - least)
            if top <= event.y <= max(last * shown, top + least):
                held["y"], held["at"] = event.y, first
                thumb.itemconfigure("thumb", fill=self.palette["thumb_on"])
            else:
                scrolled.yview_scroll(-1 if event.y < top else 1, "pages")

        def drag(event: Any) -> None:
            if held["y"] is not None:
                scrolled.yview_moveto(held["at"] + (event.y - held["y"]) / tall())

        def release(_event: Any) -> None:
            held["y"] = None
            thumb.itemconfigure("thumb", fill=self.palette["thumb"])
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
        for canvas, area, _inner in getattr(self, "_top_scrolls", []):
            if self._within(event.widget, area):
                # Over one of a whole project's parts: it scrolls, the others as they are.
                canvas.yview_scroll((-1 if delta > 0 else 1) * max(1, abs(int(delta)) // 120) * 3,
                                    "units")
                return "break"
        for scrolled, scrolls, side in self._panes:
            if scrolls and self._within(event.widget, side):
                step = -1 if delta > 0 else 1
                scrolled.yview_scroll(step * max(1, abs(int(delta)) // 120) * 3, "units")
        return "break"


__all__ = ["HandedNote", "PALETTE", "ZOOMS", "ZOOM_START", "SIDES"]

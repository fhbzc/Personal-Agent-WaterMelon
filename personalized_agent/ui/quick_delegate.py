"""THE QUICK DELEGATE WORK BOX - the shortcut's (Settings > Shortcuts).

Pressed, Ctrl+Alt+D opened the Delegate work box as a page in a browser
window of its own: three and a half seconds of the browser starting before
anything showed, measured. This box is the agent's own window, drawn by the
same toolkit as the avatar, so it is there at once: a TITLE and a
DESCRIPTION of what to have done, the folders and files it is about, Create -
the same word as the page's box.
The title is the work's (its folder's name too); the description is what the
plan is drafted from. Only then is the page opened - on the
piece of work, its context already being collected and its plan drafted, so
the browser starts while the plan is made rather than before anything is.

IT LOOKS AS THE AGENT'S OTHER WINDOWS DO (the user, 2026-11-04: "这么老旧的windows框"):
the chat bubble's card, not the system's title bar and buttons.

IT SEES WHAT IS IN FRONT (the user, 2026-11-04: "应该让他看到屏幕的focus (当然告诉模型work
可能和屏幕的focus无关)"): the box says what will go with the work, and it can be
taken off; the model is told it may have nothing to do with it.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import sizes
from .chat_bubble import LOOK
from .on_top import borderless, keys_on_hover, on_release, rounded, stay_on_top

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 540, 520


class QuickDelegate:
    """One box at a time: pressed again while it is open, it comes to the
    front instead of a second."""

    def __init__(self, root: Any, app: Any, open_box: Callable[[str], None],
                 words: Callable[[str], str], post: Callable[[Callable[[], None]], None]) -> None:
        self.root = root
        self.app = app
        self.open_box = open_box
        self.words = words
        self.post = post
        self.window: Any = None
        self.folders: List[str] = []
        self.files: List[str] = []
        #: What was in front when it was opened; {} once taken off.
        self.front: Dict[str, str] = {}
        self._drag = (0, 0)
        self._sending = False

    def _read_front(self) -> Dict[str, str]:
        try:
            from ..agent.front_screen import front_now
            return front_now(self.app.db, self.app.config)
        except Exception:
            log.debug("what is in front could not be read", exc_info=True)
            return {}

    # -- drawn -------------------------------------------------------------
    def _font(self, more: int = 0, weight: str = "normal") -> Any:
        """Named, so that a zoom changes what is drawn; the same for the same size."""
        import tkinter.font as tkfont
        held = self.__dict__.setdefault("_fonts", {})
        if (more, weight) not in held:
            held[(more, weight)] = tkfont.Font(
                root=self.root, family=tkfont.nametofont("TkDefaultFont").actual("family"),
                size=max(6, sizes.px(self.root, 10 + more) + self._zoomed()), weight=weight)
        return held[(more, weight)]

    def _zoomed(self) -> int:
        """The one zoom of the agent's windows (`sizes.popup_zoom`): kept, in no page of Settings."""
        return sizes.popup_zoom(getattr(self.app, "config", None))

    def _zoom(self, step: int) -> str:
        zoom = sizes.within_zoom(self._zoomed() + step) if step else 0
        try:
            self.app.config.set("popup_zoom", zoom, internal=True)
        except AttributeError:               # nowhere to keep it
            pass
        self.show_zoom(zoom)
        return "break"

    def show_zoom(self, zoom: int) -> None:
        """Its words at that step: another window was zoomed, or this one."""
        for (more, _weight), font in self.__dict__.get("_fonts", {}).items():
            font.configure(size=max(6, 10 + more + zoom))

    def _button(self, parent: Any, words: str, act: Callable[[], None],
                primary: bool = False) -> Any:
        import tkinter as tk
        fill, hover = ((LOOK["primary"], LOOK["primary_hover"]) if primary
                       else (LOOK["field"], LOOK["outline"]))
        face = tk.Label(parent, text=words, bg=fill, cursor="hand2", padx=12, pady=5,
                        fg=LOOK["on_primary"] if primary else LOOK["ink"],
                        font=self._font(0, "bold" if primary else "normal"), takefocus=1)
        face.bind("<Enter>", lambda _e: face.configure(bg=hover))
        face.bind("<Leave>", lambda _e: face.configure(bg=fill))
        face.bind("<Return>", lambda _e: act())
        on_release(face, act)
        return face

    def _label(self, parent: Any, words: str, muted: bool = False, more: int = 0) -> Any:
        import tkinter as tk
        return tk.Label(parent, text=words, bg=LOOK["bg"], font=self._font(more),
                        fg=LOOK["muted"] if muted else LOOK["ink"], justify="left",
                        wraplength=sizes.px(self.root, WIDTH - 44))

    def _field(self) -> Dict[str, Any]:
        return {"bg": LOOK["field"], "fg": LOOK["ink"], "insertbackground": LOOK["ink"],
                "relief": "flat", "bd": 0, "highlightthickness": 1,
                "highlightbackground": LOOK["outline"], "highlightcolor": LOOK["primary"],
                "selectbackground": LOOK["select"], "selectforeground": LOOK["ink"],
                "font": self._font()}

    def show(self) -> None:
        if self.window is not None:
            try:
                self._to_front()
                return
            except Exception:
                self.window = None
        import tkinter as tk
        _ = self.words
        pictures = getattr(self.app, "front_pictures", None)
        if pictures is not None:
            pictures.take()                  # before this box covers anything
        self.folders, self.files = [], []
        self.front = self._read_front()
        self._sending = False
        window = tk.Toplevel(self.root)
        self.window = window
        borderless(window)
        window.attributes("-topmost", True)
        window.configure(bg=LOOK["outline"])
        window.title(_("Delegate work"))
        # In the middle of the screen the pointer is on.
        wide, high = sizes.px(window, WIDTH), sizes.px(window, HEIGHT)
        x = window.winfo_pointerx() - wide // 2
        y = window.winfo_pointery() - high // 2
        window.geometry(f"{wide}x{high}+{max(0, x)}+{max(0, y)}")
        frame = tk.Frame(window, bg=LOOK["bg"], padx=16, pady=12)
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        # Its line: what it is, ✕ - moved by it.
        head = tk.Frame(frame, bg=LOOK["bg"])
        head.pack(fill="x", pady=(0, 10))
        dot = tk.Canvas(head, width=10, height=10, bg=LOOK["bg"], highlightthickness=0, bd=0)
        dot.create_oval(1, 1, 9, 9, fill=LOOK["dot"], outline="")
        dot.pack(side="left", padx=(0, 7))
        name = tk.Label(head, text=_("Delegate work"), bg=LOOK["bg"], fg=LOOK["ink"],
                        font=self._font(1, "bold"))
        name.pack(side="left")
        close = tk.Label(head, text="✕", bg=LOOK["bg"], fg=LOOK["muted"], font=self._font(1),
                         cursor="hand2", padx=4)
        close.pack(side="right")
        on_release(close, self.close)
        for one in (head, dot, name):
            one.bind("<ButtonPress-1>", self._grab)
            one.bind("<B1-Motion>", self._move)
        self._label(frame, _("Title"), muted=True).pack(anchor="w")
        self.title = tk.Entry(frame, **self._field())
        self.title.pack(fill="x", pady=(4, 10), ipady=5)
        self._label(frame, _("Description"), muted=True).pack(anchor="w")
        self.text = tk.Text(frame, height=6, wrap="word", undo=True, padx=8, pady=6,
                            **self._field())
        self.text.pack(fill="both", expand=True, pady=(4, 8))
        # Tab out of the description, as in a form - not a tab typed into it.
        self.text.bind("<Tab>", lambda _e: (self.next_button.focus_set(), "break")[1])
        picks = tk.Frame(frame, bg=LOOK["bg"])
        picks.pack(fill="x")
        self._button(picks, _("Add folder"), self._add_folder).pack(side="left")
        self._button(picks, _("Add files"), self._add_files).pack(side="left", padx=6)
        self.chosen = tk.Frame(frame, bg=LOOK["bg"])
        self.chosen.pack(fill="x", pady=(6, 0))
        self.front_row = tk.Frame(frame, bg=LOOK["bg"])
        self.front_row.pack(fill="x", pady=(6, 0))
        self._draw_front()
        self._label(frame, _("Create collects the context and drafts a plan for you to "
                             "approve. Nothing runs before you press Start."),
                    muted=True, more=-1).pack(anchor="w", pady=(6, 0))
        self.note = tk.Label(frame, text="", bg=LOOK["bg"], fg=LOOK["slow"], font=self._font(-1))
        self.note.pack(anchor="w")
        buttons = tk.Frame(frame, bg=LOOK["bg"])
        buttons.pack(fill="x", pady=(8, 0))
        self.next_button = self._button(buttons, _("Create"), self.submit, primary=True)
        self.next_button.pack(side="right")
        self._button(buttons, _("Cancel"), self.close).pack(side="right", padx=6)
        self._label(buttons, _("Ctrl+Enter to create, Esc to close"), muted=True,
                    more=-1).pack(side="left")
        window.bind("<Escape>", lambda _e: self.close())
        window.bind("<Control-Return>", lambda _e: (self.submit(), "break")[1])
        modifier = "Command" if sys.platform == "darwin" else "Control"
        for key, step in (("plus", 1), ("equal", 1), ("KP_Add", 1), ("minus", -1), ("KP_Subtract", -1),
                          ("0", 0)):
            window.bind(f"<{modifier}-{key}>", lambda _e, one=step: self._zoom(one))
        window.bind(f"<{modifier}-MouseWheel>",
                    lambda event: self._zoom(1 if event.delta > 0 else -1))
        keys_on_hover(window)
        self._frame = frame
        self._fit()
        rounded(window)
        stay_on_top(window)
        self._to_front()

    def _fit(self) -> None:
        """As tall as what is in it, its buttons always on it: folders added and
        a long title in front make it taller."""
        window = getattr(self, "window", None)
        if window is None:
            return
        window.update_idletasks()
        window.geometry(f"{sizes.px(window, WIDTH)}x{self._frame.winfo_reqheight() + 2}")

    def _grab(self, event: Any) -> None:
        self._drag = (event.x_root - self.window.winfo_x(), event.y_root - self.window.winfo_y())

    def _move(self, event: Any) -> None:
        self.window.geometry(f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    def _to_front(self) -> None:
        window = self.window
        window.deiconify()
        window.attributes("-topmost", True)
        window.lift()
        window.focus_force()
        self.title.focus_set()
        # On top while it is opened, not for good: it is a window like others.
        window.after(400, lambda: self._not_topmost(window))

    @staticmethod
    def _not_topmost(window: Any) -> None:
        try:
            window.attributes("-topmost", False)
        except Exception:
            pass

    def close(self) -> None:
        window, self.window = self.window, None
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass

    # -- what is in front --------------------------------------------------
    def _draw_front(self) -> None:
        for child in self.front_row.winfo_children():
            child.destroy()
        said = self.front.get("title") or self.front.get("application") or ""
        if not said:
            return
        words = self.words("On your screen, given as context: {0}").replace("{0}", said[:70])
        self._label(self.front_row, words, muted=True, more=-1).pack(side="left")
        off = self._label(self.front_row, "✕", muted=True)
        off.configure(cursor="hand2")
        off.pack(side="right")
        on_release(off, self._without_front)

    def _without_front(self) -> None:
        self.front = {}
        self._draw_front()
        self._fit()

    # -- what it is about --------------------------------------------------
    def _add_folder(self) -> None:
        from tkinter import filedialog
        chosen = filedialog.askdirectory(parent=self.window, mustexist=True)
        if chosen and chosen not in self.folders:
            self.folders.append(str(Path(chosen)))
            self._draw_chosen()
        self._to_front()

    def _add_files(self) -> None:
        from tkinter import filedialog
        for chosen in filedialog.askopenfilenames(parent=self.window) or ():
            if chosen and str(Path(chosen)) not in self.files:
                self.files.append(str(Path(chosen)))
        self._draw_chosen()
        self._to_front()

    def _draw_chosen(self) -> None:
        import tkinter as tk
        for child in self.chosen.winfo_children():
            child.destroy()
        for items in (self.folders, self.files):
            for one in list(items):
                row = tk.Frame(self.chosen, bg=LOOK["bg"])
                row.pack(fill="x")
                self._label(row, one, more=-1).pack(side="left")
                off = self._label(row, "✕", muted=True)
                off.configure(cursor="hand2")
                off.pack(side="right")
                on_release(off, lambda i=items, o=one: self._remove(i, o))
        self._fit()

    def _remove(self, items: List[str], one: str) -> None:
        if one in items:
            items.remove(one)
        self._draw_chosen()

    # -- handed over -------------------------------------------------------
    def submit(self) -> None:
        if self._sending:
            return
        said = self.text.get("1.0", "end").strip()
        title = self.title.get().strip()
        if not (said or title or self.folders or self.files):
            self.note.configure(text=self.words("Say what you want done."))
            return
        folders, files = list(self.folders), list(self.files)
        front = dict(self.front)
        self._sending = True
        self.note.configure(text="")

        def hand_over() -> None:
            try:
                pictures = getattr(self.app, "front_pictures", None)
                shot = pictures.latest(title=front.get("title", "")) \
                    if pictures is not None and front else {}
                if shot:
                    front.update(picture=shot["path"], picture_words=shot["words"])
                options = {"front": front} if front else None
                draft = self.app.drafts.create(folders=folders, files=files, options=options,
                                               source="shortcut", write_first=True)
                composed = self.app.drafts.compose(draft["id"], said, title=title)
                if composed.get("error"):
                    raise ValueError(composed["error"])
                self.post(lambda: (self.close(), self.open_box(
                    f"/?solo=delegate#delegate={draft['id']}")))
            except Exception as exc:
                log.exception("could not hand the work over")
                message = str(exc)[:200]
                self.post(lambda: self._failed(message))
        threading.Thread(target=hand_over, name="quick-delegate", daemon=True).start()

    def _failed(self, message: str) -> None:
        if self.window is None:
            return
        self._sending = False
        self.note.configure(text=message)


__all__ = ["QuickDelegate"]

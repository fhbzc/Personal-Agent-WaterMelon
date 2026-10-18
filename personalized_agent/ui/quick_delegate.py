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

Nothing is read from the program that was in front.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable, List, Optional

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 540, 420


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

    # -- shown -------------------------------------------------------------
    def show(self) -> None:
        if self.window is not None:
            try:
                self._to_front()
                return
            except Exception:
                self.window = None
        import tkinter as tk
        from tkinter import ttk
        _ = self.words
        self.folders, self.files = [], []
        window = tk.Toplevel(self.root)
        self.window = window
        window.title(_("Delegate work"))
        window.resizable(True, True)
        window.minsize(420, 300)
        window.protocol("WM_DELETE_WINDOW", self.close)
        # In the middle of the screen the pointer is on.
        x = window.winfo_pointerx() - WIDTH // 2
        y = window.winfo_pointery() - HEIGHT // 2
        window.geometry(f"{WIDTH}x{HEIGHT}+{max(0, x)}+{max(0, y)}")
        frame = ttk.Frame(window, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=_("Title")).pack(anchor="w")
        self.title = ttk.Entry(frame)
        self.title.pack(fill="x", pady=(4, 10))
        ttk.Label(frame, text=_("Description")).pack(anchor="w")
        self.text = tk.Text(frame, height=7, wrap="word", undo=True, relief="solid",
                            borderwidth=1, padx=6, pady=6)
        self.text.pack(fill="both", expand=True, pady=(4, 8))
        # Tab out of the description, as in a form - not a tab typed into it.
        self.text.bind("<Tab>", lambda _e: (self.next_button.focus_set(), "break")[1])
        picks = ttk.Frame(frame)
        picks.pack(fill="x")
        ttk.Button(picks, text=_("Add folder"), command=self._add_folder).pack(side="left")
        ttk.Button(picks, text=_("Add files"), command=self._add_files).pack(side="left", padx=6)
        self.chosen = ttk.Frame(frame)
        self.chosen.pack(fill="x", pady=(6, 0))
        ttk.Label(frame, text=_("Create collects the context and drafts a plan for you to "
                                "approve. Nothing runs before you press Start."),
                  foreground="#6b7280", wraplength=WIDTH - 40, justify="left").pack(
            anchor="w", pady=(6, 0))
        self.note = ttk.Label(frame, text="", foreground="#b42318")
        self.note.pack(anchor="w")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))
        self.next_button = ttk.Button(buttons, text=_("Create"), command=self.submit)
        self.next_button.pack(side="right")
        ttk.Button(buttons, text=_("Cancel"), command=self.close).pack(side="right", padx=6)
        ttk.Label(buttons, text=_("Ctrl+Enter to create, Esc to close"),
                  foreground="#6b7280").pack(side="left")
        window.bind("<Escape>", lambda _e: self.close())
        window.bind("<Control-Return>", lambda _e: (self.submit(), "break")[1])
        self._to_front()

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
        from tkinter import ttk
        for child in self.chosen.winfo_children():
            child.destroy()
        for kind, items in (("folder", self.folders), ("file", self.files)):
            for one in list(items):
                row = ttk.Frame(self.chosen)
                row.pack(fill="x")
                ttk.Label(row, text=one).pack(side="left")
                ttk.Button(row, text="×", width=2,
                           command=lambda i=items, o=one: self._remove(i, o)).pack(side="right")

    def _remove(self, items: List[str], one: str) -> None:
        if one in items:
            items.remove(one)
        self._draw_chosen()

    # -- handed over -------------------------------------------------------
    def submit(self) -> None:
        said = self.text.get("1.0", "end").strip()
        title = self.title.get().strip()
        if not (said or title or self.folders or self.files):
            self.note.configure(text=self.words("Say what you want done."))
            return
        folders, files = list(self.folders), list(self.files)
        self.next_button.configure(state="disabled")
        self.note.configure(text="")

        def hand_over() -> None:
            try:
                draft = self.app.drafts.create(folders=folders, files=files,
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
        self.next_button.configure(state="normal")
        self.note.configure(text=message)


__all__ = ["QuickDelegate"]

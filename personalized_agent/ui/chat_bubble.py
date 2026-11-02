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
avatar puts the writing back in it; Escape or ✕ closes it.

ITS SIZE IS THE USER'S (the user, 2026-11-01): dragged by any edge or
corner, as a window is - then what was said fills it and scrolls - and kept
for the next time it opens (`on_size`).
"""

from __future__ import annotations

import logging
import sys
import threading
import tkinter as tk
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)

#: Its colours - the quiet notification bubble's.
LOOK = {"outline": "#3a4152", "bg": "#1d212b", "ink": "#e9ecf3", "muted": "#9aa3b5",
        "dot": "#7c9cff", "field": "#262b37", "you": "#b9c6ff", "primary": "#5b7cfa",
        "primary_hover": "#7190ff", "on_primary": "#ffffff", "select": "#3a4d7a"}

#: How wide it is at the popups' first size, and how tall what was said may
#: grow before it scrolls.
WIDTH, SAID_TALLEST = 380, 340

#: The smallest it is dragged to; its rim, where its edges are taken; and the
#: pointer over each edge and corner.
LEAST, RIM = (300, 160), 5
EDGES = {"w": "sb_h_double_arrow", "e": "sb_h_double_arrow", "n": "sb_v_double_arrow",
         "s": "sb_v_double_arrow", "nw": "top_left_corner", "ne": "top_right_corner",
         "sw": "bottom_left_corner", "se": "bottom_right_corner"}


#: How far from the avatar a bubble stands, and from the screen's edges.
GAP, MARGIN = 8, 10


def beside(avatar: tuple, size: tuple, screen: tuple) -> tuple:
    """WHERE A BUBBLE GOES: beside the avatar (the user, 2026-11-01: "beside
    the logo") - at its left, level with its middle; at its right where the
    left has no room - and on the screen, whatever. `avatar` is (x, y,
    width, height); `size` the bubble's; `screen` the screen's."""
    ax, ay, aw, ah = avatar
    width, height = size
    sw, sh = screen
    x = ax - width - GAP
    if x < MARGIN:
        x = ax + aw + GAP                            # no room at its left
    x = max(MARGIN, min(x, sw - width - MARGIN))
    y = ay + ah // 2 - height // 2
    y = max(MARGIN, min(y, sh - height - MARGIN - 40))   # the taskbar's room
    return int(x), int(y)


class ChatBubble:
    """One conversation bubble. `send(text, conversation_id)` asks the agent
    (on another thread) and returns its answer as `agent.handle_message`
    does; `post(fn)` runs fn on Tk's thread; `open_full(conversation_id)`
    opens the chat page; `words` the application's words in the user's
    language; `on_conversation` is told the conversation once it has one."""

    def __init__(self, root: Any, *, name: str, family: str, size: int,
                 send: Callable[[str, Optional[str]], Any], post: Callable[[Callable], None],
                 open_full: Callable[[Optional[str]], None],
                 words: Callable[[str], str] = lambda text: text,
                 conversation_id: Optional[str] = None,
                 on_conversation: Optional[Callable[[str], None]] = None,
                 kept_size: Any = None,
                 on_size: Optional[Callable[[tuple], None]] = None) -> None:
        self.root, self.name = root, name
        self.family, self.size = family, max(8, int(size or 10))
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

    @property
    def is_open(self) -> bool:
        try:
            return self.window is not None and bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def _font(self, more: int = 0, weight: str = "normal") -> tuple:
        return (self.family, max(7, self.size + more), weight)

    # -- the window --------------------------------------------------------------------------
    def show(self, near: Optional[tuple] = None) -> None:
        """Opened - or, open already, the writing put back in it."""
        if self.is_open:
            self.window.deiconify()
            self.window.lift()
            self._focus()
            return
        width = self._size[0] if self._size else WIDTH + (self.size - 9) * 20
        window = tk.Toplevel(self.root)
        self.window = window
        window.overrideredirect(True)
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
        dot.create_oval(1, 1, 9, 9, fill=LOOK["dot"], outline="")
        dot.pack(side="left", padx=(0, 7))
        title = tk.Label(head, text=self.name, bg=LOOK["bg"], fg=LOOK["ink"],
                         font=self._font(0, "bold"))
        title.pack(side="left")
        close = tk.Label(head, text="✕", bg=LOOK["bg"], fg=LOOK["muted"], font=self._font(1),
                         cursor="hand2", padx=4)
        close.pack(side="right")
        close.bind("<Button-1>", lambda _e: self.close())
        full = tk.Label(head, text=self.words("Open the full chat"), bg=LOOK["bg"],
                        fg=LOOK["muted"], font=self._font(-2, "underline"), cursor="hand2")
        full.pack(side="right", padx=(0, 10))
        full.bind("<Button-1>", lambda _e: self._open_full())
        for one in (head, dot, title):
            one.bind("<ButtonPress-1>", self._grab)
            one.bind("<B1-Motion>", self._move)
        # What was said: selectable, scrolled once it is tall.
        self.said = tk.Text(card, wrap="word", font=self._font(), bg=LOOK["bg"], fg=LOOK["ink"],
                            relief="flat", bd=0, highlightthickness=0, padx=0, pady=0,
                            height=1, width=1, cursor="xterm", takefocus=0,
                            selectbackground=LOOK["select"], selectforeground=LOOK["ink"],
                            inactiveselectbackground=LOOK["select"])
        self.said.tag_configure("you", foreground=LOOK["you"], font=self._font(-1, "bold"),
                                spacing1=8)
        self.said.tag_configure("agent", foreground=LOOK["muted"], font=self._font(-1, "bold"),
                                spacing1=8)
        self.said.tag_configure("waiting", foreground=LOOK["muted"], font=self._font(-1))
        self.said.configure(state="disabled")
        self.said.pack(fill="both", expand=True, pady=(8, 0))
        self.said.pack_forget()                          # until something is said
        self.hint = tk.Label(card, text=self.words(
            "Ask anything, or tell it what to do - Enter sends, Shift+Enter a new line"),
            bg=LOOK["bg"], fg=LOOK["muted"], font=self._font(-2), wraplength=width - 30,
            justify="left", anchor="w")
        self.hint.pack(fill="x", pady=(8, 0))
        # Where the user writes.
        row = tk.Frame(card, bg=LOOK["field"], padx=10, pady=8)
        row.pack(fill="x", pady=(10, 0))
        self.box = tk.Text(row, wrap="word", font=self._font(), bg=LOOK["field"], fg=LOOK["ink"],
                           insertbackground=LOOK["ink"], relief="flat", bd=0,
                           highlightthickness=0, height=2, width=1, undo=True,
                           selectbackground=LOOK["select"])
        self.box.pack(side="left", fill="both", expand=True)
        self.button = tk.Label(row, text=self.words("Send"), bg=LOOK["primary"],
                               fg=LOOK["on_primary"], font=self._font(-1, "bold"),
                               padx=12, pady=4, cursor="hand2")
        self.button.pack(side="right", padx=(8, 0), anchor="s")
        self.button.bind("<Enter>", lambda _e: self.button.configure(bg=LOOK["primary_hover"]))
        self.button.bind("<Leave>", lambda _e: self.button.configure(bg=LOOK["primary"]))
        self.button.bind("<Button-1>", lambda _e: self.submit())
        self.box.bind("<Return>", self._enter)
        self.box.bind("<KP_Enter>", self._enter)
        window.bind("<Escape>", lambda _e: self.close())
        self._edges(rim)
        window.update_idletasks()
        self.width = width
        if self._size:
            # As the user left it: what was said fills it.
            self.said.pack(fill="both", expand=True, pady=(8, 0), before=row)
            self.hint.pack_forget()
            self.hint.pack(fill="x", pady=(8, 0), before=self.said)
            window.geometry(f"{self._size[0]}x{self._size[1]}")
        else:
            window.geometry(f"{width}x{window.winfo_reqheight()}")
        self._place(near)
        self._rounded()
        self._focus()

    def close(self) -> None:
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None

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
        x, y = beside(avatar, (width, height), screen)
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
        window = self.window
        self._resizing = (event.x_root, event.y_root, window.winfo_width(),
                          window.winfo_height(), window.winfo_x(), window.winfo_y(), edge)
        if not self._size:
            # From now on what was said fills what the user makes it.
            self.said.configure(height=1)
            if not self.said.winfo_ismapped():
                self.hint.pack_forget()
                self.said.pack(fill="both", expand=True, pady=(8, 0), before=self.box.master)
                self.hint.pack(fill="x", pady=(8, 0), before=self.said)

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
        # the whole of it again and again - it flickered.
        self._asked_geometry = f"{width}x{height}+{left}+{top}"
        self._size = (width, height)
        if getattr(self, "_geometry_due", None) is None:
            self._geometry_due = self.window.after(16, self._give_geometry)

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
            self._geometry_due = self.window.after(16, self._give_geometry)

    def _open_full(self) -> None:
        self.open_full(self.conversation_id)
        self.close()

    # -- talking ---------------------------------------------------------------------------
    def _enter(self, event: Any) -> str:
        """Enter sends; Shift+Enter is a new line."""
        if event.state & 0x0001:                         # Shift held
            return ""
        self.submit()
        return "break"

    def submit(self) -> None:
        """What was written, sent - on a thread of its own; the answer
        written in when it comes."""
        if self.waiting or not self.is_open:
            return
        text = self.box.get("1.0", "end-1c").strip()
        if not text:
            return
        self.box.delete("1.0", "end")
        self._write(self.words("You"), "you", text)
        self._write("", "waiting", self.words("Thinking…"))
        self.waiting = True
        self.button.configure(bg=LOOK["field"], fg=LOOK["muted"])

        def ask() -> None:
            try:
                said = self.send(text, self.conversation_id) or {}
            except Exception as exc:                     # said in the bubble
                log.warning("the bubble's message could not be answered", exc_info=True)
                said = {"error": str(exc)[:300]}
            self.post(lambda: self._answered(said))
        threading.Thread(target=ask, name="chat-bubble", daemon=True).start()

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
        self._write(self.name, "agent", answer or "…")
        self.button.configure(bg=LOOK["primary"], fg=LOOK["on_primary"])
        self._focus()

    def _write(self, who: str, tag: str, text: str) -> None:
        """A turn added to what was said - who, then their words - grown
        until SAID_TALLEST, then scrolled to its end."""
        said = self.said
        if not said.winfo_ismapped():
            self.hint.pack_forget()
            said.pack(fill="both", expand=True, pady=(8, 0), before=self.box.master)
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
        by the user, filling what they left it, scrolled to its end."""
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


__all__ = ["ChatBubble", "LOOK"]

"""A PART OF THE SCREEN, CUT OUT BY THE USER FOR THE SMALL SCREEN (side-board.js; the user, 2026-11-17: "我主屏幕很多矩形框，
能不能我点一个快捷键，我能把某个矩形框的内容拖到image里面").

Its shortcut pressed (Settings > Shortcuts), the screen the pointer is on is frozen as it is, dimmed, over all of it;
a drag marks the part - clear inside its blue edge - and let go, that part is kept, a PNG at `out`. Esc, or a press
without a drag, keeps nothing. In a process of its own: in the screen's own pixels (the application's Tk is not), its
window over every other, gone when done. What is cut out is what the user sees - nothing of it is read here.
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path
from typing import List, Optional, Tuple

#: The packaged application's flag that makes it this (__main__.py).
FLAG = "--snip"
#: A drag smaller than this, either way, is a press: nothing cut.
LEAST = 6
#: How often the clear part follows the pointer, at most (ms).
FOLLOW_MS = 16


def command(out: Path) -> List[str]:
    """How it is started: this module by its name, or the packaged application with its flag."""
    if getattr(sys, "frozen", False):
        return [sys.executable, FLAG, str(out)]
    return [sys.executable, "-m", "personalized_agent.ui.snip", str(out)]


def box_of(start: Tuple[int, int], end: Tuple[int, int], size: Tuple[int, int]) -> Optional[Tuple[int, int, int, int]]:
    """The part marked from `start` to `end`, kept within the screen (`size`) - None when it is a press, not a drag."""
    left, right = sorted((max(0, min(size[0], start[0])), max(0, min(size[0], end[0]))))
    top, bottom = sorted((max(0, min(size[1], start[1])), max(0, min(size[1], end[1]))))
    if right - left < LEAST or bottom - top < LEAST:
        return None
    return (left, top, right, bottom)


def _screen_under_the_pointer() -> Tuple[int, int, int, int]:
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD)]
    user32 = ctypes.windll.user32
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    user32.MonitorFromPoint.restype = ctypes.c_void_p
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    info = MonitorInfo()
    info.cbSize = ctypes.sizeof(MonitorInfo)
    user32.GetMonitorInfoW(ctypes.c_void_p(user32.MonitorFromPoint(point, 2)), ctypes.byref(info))
    rect = info.rcMonitor
    return (rect.left, rect.top, rect.right, rect.bottom)


def snip(out: str) -> int:
    """The part cut out, kept at `out`: 0 when one was, 1 when not, 2 where it cannot be done."""
    if not sys.platform.startswith("win") or not out:
        return 2
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)          # per monitor: the screen's own pixels
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    import tkinter as tk

    from PIL import ImageEnhance, ImageGrab, ImageTk
    left, top, right, bottom = _screen_under_the_pointer()
    shot = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    size = shot.size
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.geometry(f"{size[0]}x{size[1]}+{left}+{top}")
    canvas = tk.Canvas(root, width=size[0], height=size[1], highlightthickness=0, bd=0, cursor="crosshair")
    canvas.pack()
    dimmed = ImageTk.PhotoImage(ImageEnhance.Brightness(shot).enhance(0.55), master=root)
    canvas.create_image(0, 0, image=dimmed, anchor="nw")
    clear = canvas.create_image(0, 0, anchor="nw")
    edge = canvas.create_rectangle(0, 0, 0, 0, outline="#4b91ff", width=2, state="hidden")
    canvas.create_text(size[0] // 2, 34, fill="#ffffff", font=("Segoe UI", 15, "bold"),
                       text="Drag over the part to put in the small screen - Esc to cancel")
    state = {"start": None, "box": None, "photo": None, "kept": False, "later": None}

    def show() -> None:
        state["later"] = None
        box = state["box"]
        if not box:
            canvas.itemconfigure(edge, state="hidden")
            canvas.itemconfigure(clear, image="")
            return
        state["photo"] = ImageTk.PhotoImage(shot.crop(box), master=root)
        canvas.itemconfigure(clear, image=state["photo"])
        canvas.coords(clear, box[0], box[1])
        canvas.coords(edge, *box)
        canvas.itemconfigure(edge, state="normal")
        canvas.tag_raise(edge)

    def pressed(event) -> None:
        state["start"] = (event.x, event.y)
        state["box"] = None

    def moved(event) -> None:
        if state["start"] is None:
            return
        state["box"] = box_of(state["start"], (event.x, event.y), size)
        if state["later"] is None:
            state["later"] = root.after(FOLLOW_MS, show)

    def let_go(event) -> None:
        if state["start"] is None:
            return
        box = box_of(state["start"], (event.x, event.y), size)
        state["start"] = None
        if box is None:
            return                                           # a press: mark again, or Esc
        shot.crop(box).save(out, "PNG")
        state["kept"] = True
        root.destroy()

    canvas.bind("<ButtonPress-1>", pressed)
    canvas.bind("<B1-Motion>", moved)
    canvas.bind("<ButtonRelease-1>", let_go)
    root.bind("<Escape>", lambda _event: root.destroy())
    root.bind("<ButtonPress-3>", lambda _event: root.destroy())   # a right press: as Esc

    def to_the_front() -> None:
        root.lift()
        root.focus_force()
    root.after(30, to_the_front)
    root.mainloop()
    return 0 if state["kept"] else 1


if __name__ == "__main__":
    sys.exit(snip(sys.argv[1] if len(sys.argv) > 1 else ""))

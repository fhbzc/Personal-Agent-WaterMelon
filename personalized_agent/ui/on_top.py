"""ON TOP ONCE IT IS ON THE SCREEN (2026-11-02, found by the tests on the
lab's Mac): Tk on macOS drops "-topmost" asked of a window not yet laid out
- the note beside what was handed over, the bubbles, the small windows came
up under others there. Asked again once the window is laid out, it holds;
on Windows and Linux asking twice changes nothing."""

from __future__ import annotations

import sys
from typing import Any


def borderless(window: Any) -> None:
    """A window with no system title bar - its own bar instead - THAT CAN
    STILL BE WRITTEN IN. On a Mac a window Tk makes overrideredirect never
    becomes the key window: nothing typed reaches it (found on the lab's
    Mac, 2026-11-02 - the chat bubble could not be written in). There Tk's
    own "plain" style instead: no title bar, typed into, kept on top
    (measured). Elsewhere, overrideredirect. Asked before it is shown."""
    if sys.platform == "darwin":
        try:
            window.tk.call("::tk::unsupported::MacWindowStyle", "style", window._w,
                           "plain", "none")
            return
        except Exception:                                # pragma: no cover - as before, then
            pass
    window.overrideredirect(True)


def rounded(window: Any) -> None:
    """Rounded where Windows rounds windows (11 and later); square elsewhere."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        handle = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        prefer = ctypes.c_int(2)                         # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, 33, ctypes.byref(prefer),
                                                   ctypes.sizeof(prefer))
    except Exception:                                    # pragma: no cover - square, then
        pass


def stays_through_peek(window: Any) -> bool:
    """NOT PUT AWAY BY A PEEK (the user, 2026-11-04: the pointer resting on a window's preview in
    the taskbar, and the avatar went): Windows hides every other window then, on top or not,
    unless it is told this one stays. Elsewhere there is no such thing."""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes
        window.update_idletasks()
        stays = ctypes.c_int(1)
        return ctypes.windll.dwmapi.DwmSetWindowAttribute(        # DWMWA_EXCLUDED_FROM_PEEK
            int(window.wm_frame(), 16), 12, ctypes.byref(stays), ctypes.sizeof(stays)) == 0
    except Exception:                                    # pragma: no cover - it goes in a peek
        return False


#: How often a window under the pointer looks whether Ctrl is down, in milliseconds.
CTRL_EVERY_MS = 50


def ctrl_down() -> bool:
    """Whether Ctrl is held, whichever program has the keyboard (Windows)."""
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000)
    except Exception:                                    # pragma: no cover - not known
        return False


def keys_on_hover(window: Any) -> None:
    """THE POINTER ON IT AND CTRL DOWN, THE KEYS GO TO IT (the user, 2026-11-04: "所有的panel缩放都支持鼠标+
    ctrl +-"): a window has them only once it has the keyboard. Not for the pointer alone: passing
    over a notification, what was being typed elsewhere would have gone nowhere. Something in it
    that has the keyboard already, a box being written in, keeps it. Where Ctrl cannot be
    asked of the system, the pointer is enough."""
    watching = {"on": False}

    def take() -> None:
        try:
            held = window.focus_get()
        except Exception:                                # another window's, or none
            held = None
        if held is None or not str(held).startswith(str(window)):
            try:
                window.focus_force()
            except Exception:                            # pragma: no cover - gone
                pass

    def inside() -> bool:
        x, y = window.winfo_pointerxy()
        left, top = window.winfo_rootx(), window.winfo_rooty()
        return left <= x < left + window.winfo_width() and top <= y < top + window.winfo_height()

    def watch() -> None:
        try:
            if not inside():
                watching["on"] = False
                return
            if ctrl_down():
                take()
            window.after(CTRL_EVERY_MS, watch)
        except Exception:                                # the window has gone
            watching["on"] = False

    def came_in(_event: Any = None) -> None:
        if not sys.platform.startswith("win"):
            take()
        elif not watching["on"]:
            watching["on"] = True
            watch()
    window.bind("<Enter>", came_in, add="+")


def selected_words(widget: Any) -> str:
    """What is selected in a Text, or "" - nothing is copied unasked."""
    ranges = widget.tag_ranges("sel")
    return widget.get(ranges[0], ranges[1]) if ranges else ""


def copy_to_clipboard(widget: Any, words: str) -> None:
    widget.clipboard_clear()
    widget.clipboard_append(words)


def with_copy(widget: Any, label: str, whole: str = "") -> None:
    """COPY FROM THE RIGHT BUTTON (the user, 2026-11-04: "选择之后右键有复制按钮"): what is
    selected, or `whole` with nothing selected, if it is given."""
    import tkinter as tk

    def menu(event: Any) -> str:
        words = selected_words(widget) or whole
        if words:
            popup = tk.Menu(widget, tearoff=0)
            popup.add_command(label=label, command=lambda: copy_to_clipboard(widget, words))
            popup.tk_popup(event.x_root, event.y_root)
        return "break"
    widget.bind("<Button-3>", menu)
    if sys.platform == "darwin":
        widget.bind("<Button-2>", menu)
        widget.bind("<Control-Button-1>", menu)


def pressed_shade(widget: Any, color: str) -> str:
    """`color` as it looks pressed: a shade darker - lighter on a dark one,
    where darker would hardly show."""
    try:
        red, green, blue = (part / 257 for part in widget.winfo_rgb(color))
    except Exception:                                    # pragma: no cover - not a colour
        return color
    light = (0.299 * red + 0.587 * green + 0.114 * blue) / 255
    if light < 0.35:
        mixed = [part + (255 - part) * 0.22 for part in (red, green, blue)]
    else:
        mixed = [part * 0.82 for part in (red, green, blue)]
    return "#%02x%02x%02x" % tuple(int(round(min(255, max(0, part)))) for part in mixed)


def on_release(widget: Any, act: Any) -> None:
    """PRESSED AS A BUTTON IS (the user, 2026-11-02: "应该是放开鼠标才确定，
    现在好像是点击就确定了"): `act()` when the button is let go over it -
    pressed and dragged off it, let go elsewhere, nothing is done.

    AND FELT WHEN PRESSED (the user, 2026-11-02: "点击之后要有一个触感（左键
    有反馈，虽然不是完全选择，但是要有个反馈）"): pressed, it goes a shade
    darker at once; dragged off while held, back as it was - let go there,
    nothing is done - and pressed again when dragged back on."""
    pressed = {"down": False, "rest": None, "shade": None}

    def inside(event: Any) -> bool:
        try:
            return 0 <= event.x < widget.winfo_width() and 0 <= event.y < widget.winfo_height()
        except Exception:                                # pragma: no cover - gone meanwhile
            return False

    def now() -> str:
        try:
            return str(widget.cget("bg")).lower()
        except Exception:                                # e.g. a themed widget
            return ""

    def show_pressed() -> None:
        if pressed["shade"] and now() != pressed["shade"]:
            try:
                widget.configure(bg=pressed["shade"])
            except Exception:                            # pragma: no cover - gone meanwhile
                pass

    def show_rest() -> None:
        # Only the shade it was given is taken back: a colour the widget's
        # own hover (or leave) put on meanwhile is left as it is.
        if pressed["shade"] and now() == pressed["shade"]:
            try:
                widget.configure(bg=pressed["rest"])
            except Exception:                            # pragma: no cover - gone meanwhile
                pass

    def down(_event: Any) -> None:
        pressed["down"] = True
        rest = now()
        pressed["rest"] = rest or None
        pressed["shade"] = pressed_shade(widget, rest).lower() if rest else None
        show_pressed()

    # Dragged off and on while held: told by Enter and Leave, which Tk sends
    # while the button is down too - no <B1-Motion> of its own, which would
    # make a button read as a handle the window is dragged by.
    def entered(_event: Any) -> None:
        if pressed["down"]:
            show_pressed()

    def left(_event: Any) -> None:
        if pressed["down"]:
            show_rest()

    def up(event: Any) -> None:
        was, pressed["down"] = pressed["down"], False
        here = inside(event)
        show_rest()
        pressed["rest"] = pressed["shade"] = None
        if was and here:
            act()

    widget.bind("<ButtonPress-1>", down, add="+")
    widget.bind("<Enter>", entered, add="+")
    widget.bind("<Leave>", left, add="+")
    widget.bind("<ButtonRelease-1>", up, add="+")


def stay_on_top(window: Any) -> None:
    """`window` kept above other windows - asked once it is laid out."""
    try:
        window.update_idletasks()
        window.attributes("-topmost", True)
    except Exception:                                    # pragma: no cover - closed meanwhile
        pass


__all__ = ["stay_on_top", "stays_through_peek", "keys_on_hover", "borderless", "on_release"]

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


__all__ = ["stay_on_top", "borderless", "on_release"]

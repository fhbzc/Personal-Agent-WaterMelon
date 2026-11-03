"""Draw the existing avatar frames over a clear Aqua window (PR #5).

Some Tk Aqua builds omit PhotoImage content in transparent windows. AppKit
can display the same RGBA frames in a native image view while Tk continues
to own the window's placement and mouse events.
"""

from __future__ import annotations

import io
import logging
from typing import Any, Callable, Optional

import AppKit
from Foundation import NSData

log = logging.getLogger(__name__)


def right_clicks_while_away(window: Any, size: int,
                            on_right: Callable[[int, int], None]) -> Optional[Any]:
    """THE RIGHT BUTTON ON THE AVATAR WHILE ANOTHER PROGRAM IS IN FRONT (the
    user, 2026-11-02, on the lab's Mac: "右键logo没有东西弹出来", no way to
    quit): Tk on a Mac drops a right click on a window of a program not in
    front - measured: none reached its bindings, though the click reached
    the program. Heard here before Tk drops it: the program brought to the
    front and `on_right(x, y)` told where, in Tk's screen coordinates (from
    the top). In front already, Tk hears it itself and this lets it be.
    The monitor is returned - kept by the caller as long as it listens."""
    app = AppKit.NSApplication.sharedApplication()
    window.update_idletasks()
    native = next((one for one in app.windows()
                   if str(one.title()) == window.title()
                   and int(one.contentView().bounds().size.width) == size
                   and int(one.contentView().bounds().size.height) == size), None)
    if native is None:
        return None
    # By number: two Python handles on one window are not the same object.
    number = native.windowNumber()

    def heard(event: Any) -> Any:
        try:
            if event.windowNumber() == number and not app.isActive():
                app.activateIgnoringOtherApps_(True)
                where = AppKit.NSEvent.mouseLocation()
                top = AppKit.NSScreen.screens()[0].frame().size.height
                on_right(int(where.x), int(top - where.y))
                return None                              # told once, not to Tk too
        except Exception:                                # pragma: no cover - Tk's, then
            log.debug("the avatar's right click could not be told", exc_info=True)
        return event

    return AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
        AppKit.NSEventMaskRightMouseDown, heard)


class _AvatarImageView(AppKit.NSImageView):
    def hitTest_(self, point: Any) -> Any:
        # Let the Tk canvas beneath the image receive clicks and dragging.
        return None


class NativeAvatar:
    def __init__(self, window: Any, canvas: Any, size: int) -> None:
        window.update_idletasks()
        self.window, self.canvas = window, canvas
        self.native = next(
            one for one in AppKit.NSApplication.sharedApplication().windows()
            if str(one.title()) == window.title()
            and int(one.contentView().bounds().size.width) == size
            and int(one.contentView().bounds().size.height) == size
        )
        content = self.native.contentView()
        self.view = _AvatarImageView.alloc().initWithFrame_(content.bounds())
        self.view.setImageScaling_(AppKit.NSImageScaleNone)
        self.view.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        content.addSubview_(self.view)
        try:
            canvas.configure(bg="systemTransparent")
            window.configure(bg="systemTransparent")
            window.attributes("-transparent", True)
            self.native.setOpaque_(False)
            self.native.setBackgroundColor_(AppKit.NSColor.clearColor())
            self.native.setHasShadow_(False)
            window.attributes("-alpha", 1.0)
        except Exception:
            self.close()
            raise

    def show(self, frame: Any) -> None:
        # PNG preserves the RGBA pixels produced by the existing animator.
        encoded = io.BytesIO()
        frame.save(encoded, format="PNG", compress_level=1)
        pixels = encoded.getvalue()
        data = NSData.dataWithBytes_length_(pixels, len(pixels))
        image = AppKit.NSImage.alloc().initWithData_(data)
        if image is None:
            raise ValueError("macOS could not decode the avatar frame")
        self.view.setImage_(image)

    def close(self) -> None:
        self.view.removeFromSuperview()
        self.window.attributes("-transparent", False)
        self.window.configure(bg="#101216")
        self.canvas.configure(bg="#101216")
        self.window.attributes("-alpha", 0.96)

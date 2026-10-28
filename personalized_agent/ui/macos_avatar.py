"""Draw the existing avatar frames over a clear Aqua window (PR #5).

Some Tk Aqua builds omit PhotoImage content in transparent windows. AppKit
can display the same RGBA frames in a native image view while Tk continues
to own the window's placement and mouse events.
"""

from __future__ import annotations

import io
from typing import Any

import AppKit
from Foundation import NSData


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

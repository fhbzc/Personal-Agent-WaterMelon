"""THE DELEGATE WORK BOX IN A PANEL OF THE AGENT'S OWN, ON A MAC (the user,
2026-11-02: "delegate work 为什么在Mac里是个网页，而不是一个panel").

On Windows the box opens in Edge's app mode - a window the size of the box,
no tabs, no address bar (platform_layer/app_window.py). A Mac has no
browser that does that unless Chrome or Edge was installed, and many Macs
have only Safari: the box opened as a page in it. Here it opens in a window
of the agent's own, with Apple's WebKit in it - the same page, drawn and
tested once - on every Mac, and at once (no browser to start).

Nothing of the user's browser is in it, nor of it in theirs: its store is
one that keeps nothing (`nonPersistentDataStore`). A link the page opens in
a new window goes to the user's own browser. The page closing itself
(`window.close()`, when the box is done) closes the window.

Made on the main thread only - Tk's, on a Mac (desktop.py puts it there).
"""

from __future__ import annotations

import logging
from typing import Any, List, Tuple

log = logging.getLogger(__name__)

#: The window's title: the agent's name in it, as the page's own title has
#: it - the screen's listener knows the agent's own windows by it.
TITLE = "Delegate work - Personal Agent WaterMelon"

#: The panels open now, each with its view and delegates - kept, or Python
#: lets them go while AppKit still uses them.
_open: List[Tuple[Any, ...]] = []


def available() -> bool:
    """Whether a panel can be made here: a Mac, with Apple's WebKit."""
    try:
        import AppKit  # noqa: F401
        import WebKit  # noqa: F401
        return True
    except Exception:
        return False


def _classes() -> Tuple[Any, Any]:
    """The page's and the window's delegates - made once."""
    global _UI, _WINDOW
    try:
        return _UI, _WINDOW
    except NameError:
        pass
    import AppKit

    class _PageDelegate(AppKit.NSObject):
        """WKUIDelegate: the page closing itself, and its new windows."""

        def webViewDidClose_(self, view: Any) -> None:
            window = view.window()
            if window is not None:
                window.close()

        def webView_createWebViewWithConfiguration_forNavigationAction_windowFeatures_(
                self, view: Any, configuration: Any, action: Any, features: Any) -> Any:
            # Not a window of ours: the user's own browser.
            url = action.request().URL()
            if url is not None:
                AppKit.NSWorkspace.sharedWorkspace().openURL_(url)
            return None

    class _WindowDelegate(AppKit.NSObject):
        """NSWindowDelegate: closed, let go."""

        def windowWillClose_(self, note: Any) -> None:
            closing = note.object()
            _open[:] = [one for one in _open if one[0] is not closing
                        and one[0].windowNumber() != closing.windowNumber()]

    _UI, _WINDOW = _PageDelegate, _WindowDelegate
    return _UI, _WINDOW


def open_panel(url: str, size: Tuple[int, int] = (780, 900)) -> bool:
    """`url` in a panel of its own, in the middle of the screen, in front -
    False where one cannot be made, for the caller to open it otherwise."""
    try:
        import AppKit
        import Foundation
        import WebKit
        page_delegate, window_delegate = _classes()
        seen = AppKit.NSScreen.mainScreen().visibleFrame()
        width = min(float(size[0]), seen.size.width - 40)
        height = min(float(size[1]), seen.size.height - 40)
        frame = ((seen.origin.x + (seen.size.width - width) / 2,
                  seen.origin.y + (seen.size.height - height) / 2), (width, height))
        style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                 | AppKit.NSWindowStyleMaskResizable | AppKit.NSWindowStyleMaskMiniaturizable)
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, AppKit.NSBackingStoreBuffered, False)
        window.setTitle_(TITLE)
        window.setReleasedWhenClosed_(False)
        configuration = WebKit.WKWebViewConfiguration.alloc().init()
        configuration.setWebsiteDataStore_(WebKit.WKWebsiteDataStore.nonPersistentDataStore())
        view = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            window.contentView().bounds(), configuration)
        view.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        page, closing = page_delegate.alloc().init(), window_delegate.alloc().init()
        view.setUIDelegate_(page)
        window.setDelegate_(closing)
        window.contentView().addSubview_(view)
        view.loadRequest_(Foundation.NSURLRequest.requestWithURL_(
            Foundation.NSURL.URLWithString_(url)))
        _open.append((window, view, page, closing))
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        window.makeKeyAndOrderFront_(None)
        return True
    except Exception:
        log.warning("no panel of its own for the box - the browser instead", exc_info=True)
        return False


__all__ = ["available", "open_panel", "TITLE"]

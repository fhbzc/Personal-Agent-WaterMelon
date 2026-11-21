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

THE WHOLE PAGE TOO (the user, 2026-11-05: "Mac 用WebKit做"; a double click is that window, brought
back as it was left, whatever browser the Mac has). It keeps what the page keeps. A page in a
browser has what WebKit alone does not, so it is given here: a page's confirm, prompt and alert,
choosing a file, the keys of the Edit menu (Tk's menu has none), and its zoom.

Made on the main thread only - Tk's, on a Mac (desktop.py puts it there).
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Tuple

log = logging.getLogger(__name__)

#: The window's title: the agent's name in it, as the page's own title has
#: it - the screen's listener knows the agent's own windows by it.
TITLE = "Delegate work - Personal Agent WaterMelon"
#: The page's window, and how much a step of its zoom is.
PAGE_TITLE = "WaterMelon"
PAGE_SIZE = (1400, 900)
ZOOM_STEP, ZOOM_LEAST, ZOOM_MOST = 0.1, 0.5, 3.0
_page: List[Any] = []

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

        # WHAT A PAGE ASKS OF ITS BROWSER: without these, confirm() is "no", prompt() nothing, and
        # naming or deleting anything on the page did nothing.
        def webView_runJavaScriptAlertPanelWithMessage_initiatedByFrame_completionHandler_(
                self, _view: Any, message: Any, _frame: Any, done: Any) -> None:
            _asked(message).runModal()
            done()

        def webView_runJavaScriptConfirmPanelWithMessage_initiatedByFrame_completionHandler_(
                self, _view: Any, message: Any, _frame: Any, done: Any) -> None:
            alert = _asked(message, "OK", "Cancel")
            done(alert.runModal() == AppKit.NSAlertFirstButtonReturn)

        def webView_runJavaScriptTextInputPanelWithPrompt_defaultText_initiatedByFrame_completionHandler_(
                self, _view: Any, message: Any, text: Any, _frame: Any, done: Any) -> None:
            alert = _asked(message, "OK", "Cancel")
            field = AppKit.NSTextField.alloc().initWithFrame_(((0, 0), (300, 24)))
            field.setStringValue_(text or "")
            alert.setAccessoryView_(field)
            done(field.stringValue() if alert.runModal() == AppKit.NSAlertFirstButtonReturn else None)

        def webView_runOpenPanelWithParameters_initiatedByFrame_completionHandler_(
                self, _view: Any, parameters: Any, _frame: Any, done: Any) -> None:
            panel = AppKit.NSOpenPanel.openPanel()
            panel.setAllowsMultipleSelection_(bool(parameters.allowsMultipleSelection()))
            panel.setCanChooseDirectories_(bool(parameters.allowsDirectories()))
            done(panel.URLs() if panel.runModal() == AppKit.NSModalResponseOK else None)

    class _WindowDelegate(AppKit.NSObject):
        """NSWindowDelegate: closed, let go."""

        def windowWillClose_(self, note: Any) -> None:
            closing = note.object()
            _open[:] = [one for one in _open if one[0] is not closing
                        and one[0].windowNumber() != closing.windowNumber()]
            for title in [key for key, one in _side.items() if one[0] is closing]:
                _side.pop(title, None)

    _UI, _WINDOW = _PageDelegate, _WindowDelegate
    return _UI, _WINDOW


def _asked(message: Any, *buttons: str) -> Any:
    """A page's question, as the system asks one."""
    import AppKit
    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(str(message or ""))
    for one in buttons:
        alert.addButtonWithTitle_(one)
    return alert


def open_panel(url: str, size: Tuple[int, int] = (780, 900), title: str = TITLE,
               keeps: bool = False, frame: Any = None, front: bool = True) -> bool:
    """`url` in a panel of its own, in the middle of the screen (or at `frame`), in front -
    False where one cannot be made, for the caller to open it otherwise. `keeps`: what the page
    stores is there the next time."""
    try:
        import AppKit
        import Foundation
        import WebKit
        page_delegate, window_delegate = _classes()
        seen = AppKit.NSScreen.mainScreen().visibleFrame()
        width = min(float(size[0]), seen.size.width - 40)
        height = min(float(size[1]), seen.size.height - 40)
        frame = frame or ((seen.origin.x + (seen.size.width - width) / 2,
                           seen.origin.y + (seen.size.height - height) / 2), (width, height))
        style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                 | AppKit.NSWindowStyleMaskResizable | AppKit.NSWindowStyleMaskMiniaturizable)
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, AppKit.NSBackingStoreBuffered, False)
        window.setTitle_(title)
        window.setReleasedWhenClosed_(False)
        configuration = WebKit.WKWebViewConfiguration.alloc().init()
        configuration.setWebsiteDataStore_(WebKit.WKWebsiteDataStore.defaultDataStore() if keeps
                                           else WebKit.WKWebsiteDataStore.nonPersistentDataStore())
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
        if front:                                  # else ordered in by the caller (`side_panel`)
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            window.makeKeyAndOrderFront_(None)
        return True
    except Exception:
        log.warning("no panel of its own for the box - the browser instead", exc_info=True)
        return False


#: THE PANELS DOWN THE SIDE OF THE SCREEN (the waiting notifications, the user, 2026-11-13: "直接占满整个
#: 侧边框的那种"; "有没有更natural的方式从侧边弹出"; "Mac上的面板为什么不改呢"): by title - slid in from the
#: right edge as tall as the screen, slid out and kept (ordered out), slid back in at once.
_side: dict = {}


def _no_title_bar(window: Any, view: Any) -> None:
    """THE SIDE PANEL WITHOUT A WINDOW'S BUTTONS (the user, 2026-11-21: "mac的sidebar还有自己的最大化，最小化，叉叉，这个能不能
    取消"): no close, minimise or zoom button and no title shown - the page has its own (put away, full screen) - its page
    up to the top edge; the title kept, by which the agent's own windows are known. Its edge still drags its width."""
    import AppKit
    try:
        for button in (AppKit.NSWindowCloseButton, AppKit.NSWindowMiniaturizeButton, AppKit.NSWindowZoomButton):
            found = window.standardWindowButton_(button)
            if found is not None:
                found.setHidden_(True)
        window.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        window.setTitlebarAppearsTransparent_(True)
        window.setStyleMask_(window.styleMask() | AppKit.NSWindowStyleMaskFullSizeContentView)
        window.setMovableByWindowBackground_(False)
        if view is not None:
            view.setFrame_(window.contentView().bounds())
    except Exception:
        log.debug("the side panel's title bar could not be taken off", exc_info=True)


def _side_frames(window: Any, width: float) -> Tuple[Any, Any]:
    """(off the right edge, in place) for a side panel `width` wide, on the screen it is on."""
    import AppKit
    screen = (window.screen() if window is not None else None) or AppKit.NSScreen.mainScreen()
    seen = screen.visibleFrame()
    width = min(float(width), seen.size.width)
    right = seen.origin.x + seen.size.width
    return (((right, seen.origin.y), (width, seen.size.height)),
            ((right - width, seen.origin.y), (width, seen.size.height)))


#: How long it slides, in or out, and in so many steps (the system's own animation of a window's
#: frame did not move it, tried on the lab Mac 2026-11-13: it stayed where it was).
SIDE_SLIDE_SECONDS, SIDE_SLIDE_STEPS = 0.18, 12


def _slide_to(window: Any, frame: Any) -> None:
    """`window` slid to `frame`: quick, then easing into place."""
    import time
    x0, x1 = float(window.frame().origin.x), float(frame[0][0])
    for step in range(1, SIDE_SLIDE_STEPS + 1):
        done = 1 - (1 - step / SIDE_SLIDE_STEPS) ** 3
        window.setFrame_display_(((x0 + (x1 - x0) * done, frame[0][1]), frame[1]), True)
        time.sleep(SIDE_SLIDE_SECONDS / SIDE_SLIDE_STEPS)


def side_panel(url: str, width: float, title: str, activate: bool = True) -> bool:
    """`url` in a panel down the right side, slid in - the one put away by `hide_side_panel` slid back
    in, one shown already to the front. `activate` False (a notification's): shown, the program the user
    is in left in front. False where one cannot be made. On the main thread."""
    try:
        import AppKit
        found = _side.get(title)
        if found is None:
            off, _in_place = _side_frames(None, width)
            if not open_panel(url, (width, 900), title, frame=off, front=False):
                return False
            found = next((one for one in _open if str(one[0].title()) == title), None)
            if found is None:
                return False
            _side[title] = found
            _no_title_bar(found[0], found[1])
            # SEEN THROUGH IN THE AGENT'S PICTURE ONLY (the user, 2026-11-16: "chat现在截图的时候，忽视/穿透 sidebar" -
            # "只是自己截图看不到，其它是可以的"): that picture is made of the other programs' windows alone
            # (macos.py user_screen_image), this one of this process's; the user's own screenshots have it.
        window = found[0]
        if window.isVisible() and not window.isMiniaturized() and window.alphaValue() > 0:
            if activate:
                AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
                window.makeKeyAndOrderFront_(None)
            return True
        if window.isMiniaturized():
            window.deminiaturize_(None)
        off, in_place = _side_frames(window, window.frame().size.width or width)
        # Unseen while it is put off the edge - the system would pull a window ordered in off the
        # screen back onto it - then slid in.
        window.setAlphaValue_(0.0)
        if activate:
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            window.makeKeyAndOrderFront_(None)
        else:
            window.orderFrontRegardless()                  # seen, the user's program still in front
        window.setFrame_display_(off, False)
        window.setAlphaValue_(1.0)
        _slide_to(window, in_place)
        return True
    except Exception:
        log.warning("the side panel could not be slid in", exc_info=True)
        return False


def hide_side_panel(title: str) -> bool:
    """The side panel titled `title` slid out to the right edge and put away - kept, for
    `side_panel`. On the main thread."""
    found = _side.get(title)
    if found is None:
        return False
    try:
        window = found[0]
        if window.isVisible():
            # PUT AWAY AT ONCE (2026-11-16, as on Windows - the user: closing quick): sliding out held the
            # main thread, the avatar with it, for 0.18 s.
            window.orderOut_(None)
        return True
    except Exception:
        log.warning("the side panel could not be slid out", exc_info=True)
        return False


def side_panel_strip(title: str) -> Optional[Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int]]]:
    """(the panel's strip, the rest of its screen's visible part) as the Accessibility API counts - left, top,
    right, bottom from the top left of the main screen - for the other windows to be moved beside it
    (platform_layer/dock_mac.py). None when it is not shown. On the main thread."""
    import AppKit
    found = _side.get(title)
    if found is None or not found[0].isVisible():
        return None
    window = found[0]
    screen = window.screen() or AppKit.NSScreen.mainScreen()
    height = AppKit.NSScreen.screens()[0].frame().size.height       # the main screen: where y counts from
    seen, frame = screen.visibleFrame(), window.frame()

    def flipped(x: float, y: float, w: float, h: float) -> Tuple[int, int, int, int]:
        return (int(x), int(height - (y + h)), int(x + w), int(height - y))
    strip = flipped(frame.origin.x, seen.origin.y, frame.size.width, seen.size.height)
    rest = flipped(seen.origin.x, seen.origin.y, frame.origin.x - seen.origin.x, seen.size.height)
    return strip, rest


def resize_side_panel(title: str, width: float) -> bool:
    """The side panel titled `title`, shown, made `width` wide - its right edge where it is (its picture's
    column opened or put away, 2026-11-16). On the main thread."""
    found = _side.get(title)
    if found is None:
        return False
    try:
        window = found[0]
        if not window.isVisible():
            return False
        _off, in_place = _side_frames(window, width)
        window.setFrame_display_(in_place, True)
        return True
    except Exception:
        log.warning("the side panel could not be made wider or narrower", exc_info=True)
        return False


def keep_side_panel_above(title: str, above: bool) -> bool:
    """PINNED, ABOVE THE OTHERS (the user, 2026-11-21: "mac上的pin好像没用，其它页面还是会放到pin的上面（比如最大化之后）"):
    the side panel titled `title` kept at the floating level - no other program's window, maximized or brought to the
    front, goes over it - or back at the normal level. On the main thread."""
    import AppKit
    found = _side.get(title)
    if found is None:
        return False
    try:
        found[0].setLevel_(AppKit.NSFloatingWindowLevel if above else AppKit.NSNormalWindowLevel)
        return True
    except Exception:
        log.warning("the side panel's level could not be set", exc_info=True)
        return False


def fill_side_panel(title: str) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """THE SIDE PANEL OVER THE WHOLE SCREEN ON A MAC (the user, 2026-11-21: "你mac side panel怎么没有全屏这个"): the panel
    titled `title`, shown, made as large as its screen's room for windows (under the menu bar, beside the Dock). Its
    frame before - for `put_side_panel` - or None where it is not shown. On the main thread."""
    import AppKit
    found = _side.get(title)
    if found is None or not found[0].isVisible():
        return None
    try:
        window = found[0]
        frame = window.frame()
        before = ((float(frame.origin.x), float(frame.origin.y)), (float(frame.size.width), float(frame.size.height)))
        seen = (window.screen() or AppKit.NSScreen.mainScreen()).visibleFrame()
        window.setFrame_display_(((seen.origin.x, seen.origin.y), (seen.size.width, seen.size.height)), True)
        return before
    except Exception:
        log.warning("the side panel could not be made to fill the screen", exc_info=True)
        return None


def put_side_panel(title: str, frame: Any) -> bool:
    """The side panel titled `title` put back at `frame` (from `fill_side_panel`). On the main thread."""
    found = _side.get(title)
    if found is None or not frame:
        return False
    try:
        found[0].setFrame_display_((tuple(frame[0]), tuple(frame[1])), True)
        return True
    except Exception:
        log.warning("the side panel could not be put back from the whole screen", exc_info=True)
        return False


def has_side_panel(title: str) -> bool:
    """Whether a side panel titled `title` is made (shown or put away) - asked from any thread."""
    return title in _side


def side_panel_shown(title: str) -> bool:
    """Whether the side panel titled `title` is on the screen. On the main thread."""
    found = _side.get(title)
    try:
        return found is not None and bool(found[0].isVisible()) and not found[0].isMiniaturized()
    except Exception:
        return False


def _the_page() -> Any:
    """What is open of the page's window: (window, view, ...), or None."""
    for one in _open:
        if str(one[0].title()) == PAGE_TITLE:
            return one
    return None


def bring_back_the_page() -> bool:
    """The page's window to the front, as it was left; False when it is not open."""
    found = _the_page()
    if found is None:
        return False
    try:
        import AppKit
        if found[0].isMiniaturized():
            found[0].deminiaturize_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        found[0].makeKeyAndOrderFront_(None)
        return True
    except Exception:
        log.warning("the page's window could not be brought back", exc_info=True)
        return False


#: Command with these, in the page's window: what the Edit menu would do, by its action.
EDIT_KEYS = {"c": "copy:", "v": "paste:", "x": "cut:", "a": "selectAll:", "z": "undo:"}


def zoomed(now: float, key: str) -> float:
    """The page's zoom after Command and that key."""
    if key == "0":
        return 1.0
    step = ZOOM_STEP if key in ("=", "+") else -ZOOM_STEP
    return round(min(ZOOM_MOST, max(ZOOM_LEAST, now + step)), 2)


def _listen_for_the_keys() -> None:
    """Once: the keys of the page's window, which no menu of Tk's gives it."""
    if _page:
        return
    import AppKit

    def heard(event: Any) -> Any:
        found = _the_page()
        if found is None or event.window() is None or \
                event.window().windowNumber() != found[0].windowNumber() or \
                not event.modifierFlags() & AppKit.NSEventModifierFlagCommand:
            return event
        key = str(event.charactersIgnoringModifiers() or "").lower()
        view = found[1]
        if key in EDIT_KEYS:
            AppKit.NSApplication.sharedApplication().sendAction_to_from_(EDIT_KEYS[key], None, None)
        elif key in ("=", "+", "-", "0"):
            view.setPageZoom_(zoomed(float(view.pageZoom()), key))
        elif key == "r":
            view.reload()
        elif key == "w":
            found[0].performClose_(None)
        else:
            return event
        return None
    _page.append(AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
        AppKit.NSEventMaskKeyDown, heard))


def open_the_page(url: str) -> bool:
    """The whole page in its window. Two fingers on a trackpad zoom it, as in a browser."""
    if not open_panel(url, PAGE_SIZE, PAGE_TITLE, keeps=True):
        return False
    try:
        _the_page()[1].setAllowsMagnification_(True)
        _listen_for_the_keys()
    except Exception:
        log.warning("the page's window has no keys of its own", exc_info=True)
    return True


__all__ = ["available", "open_panel", "TITLE", "PAGE_TITLE", "open_the_page",
           "bring_back_the_page", "zoomed", "side_panel", "hide_side_panel", "has_side_panel",
           "side_panel_shown"]

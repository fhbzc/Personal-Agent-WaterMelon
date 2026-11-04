"""FILES DRAGGED ONTO A WINDOW (the user, 2026-11-04: the chat bubble takes a file
dragged onto it, and shows it is ready the moment one is over it, as Google
Drive does). Tk has no way of its own, so on Windows the window is registered
as a drop target (OLE's IDropTarget, written out here): the system says when a
file comes over, goes, or is let go. Failing that it is told to accept files
(DragAcceptFiles) and reads the message sent when one is let go (WM_DROPFILES),
with nothing to say while it is over. On a Mac a clear view is laid over the
window, as the avatar's is (ui/macos_avatar.py), that the pointer goes through
and files do not. On Linux, tkdnd (the tkinterdnd2 package, Tk's own extension
for this). Where none of them is there it says it could not, and the caller
offers another way.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

WM_DROPFILES, WM_NCDESTROY = 0x0233, 0x0082
GWLP_WNDPROC = -4
GA_ROOT = 2

#: What keeps each window's hook alive while the window is: a callback the
#: system holds, freed under it, would end the process.
_held: Dict[int, tuple] = {}


def accept_files(window: Any, dropped: Callable[[List[str]], None],
                 over: Optional[Callable[[bool], None]] = None) -> bool:
    """`dropped(paths)` told of files dragged onto `window`, and `over(True)`
    and `over(False)` of files coming over it and going - each on Tk's thread
    and within the system's call: they must only hand it on. Whether it can."""
    try:
        if sys.platform == "darwin":
            return _mac(window, dropped, over)
        if not sys.platform.startswith("win"):
            return _tkdnd(window, dropped, over)
        return _ole(window, dropped, over) or _windows(window, dropped)
    except Exception:
        log.info("this window cannot take files dragged onto it", exc_info=True)
        return False


def let_go(window: Any) -> None:
    """Before `window` goes: the system no longer drops anything on it."""
    if sys.platform == "darwin":
        view = _held.pop(id(window), (None, None))[1]
        if view is not None:
            view.unregisterDraggedTypes()
            view.removeFromSuperview()
        return
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        handle = user32.GetAncestor(int(window.winfo_id()), GA_ROOT)
        if handle and _held.pop(int(handle), (None,))[0] == "ole":
            ctypes.windll.ole32.RevokeDragDrop(wintypes.HWND(handle))
    except Exception:
        log.debug("the window was not let go of", exc_info=True)


def _tkdnd(window: Any, dropped: Callable[[List[str]], None],
           over: Optional[Callable[[bool], None]]) -> bool:
    """Registered on the window alone: tkdnd goes up from the widget under the
    pointer to it, so what is drawn over the window while a file is there is
    no leaving and coming back."""
    from tkinterdnd2 import COPY, DND_FILES, TkinterDnD
    TkinterDnD._require(window)

    def say(on: bool) -> None:
        if over is not None:
            over(on)

    def entered(event: Any) -> str:
        say(True)
        return COPY

    def left(event: Any) -> str:
        say(False)
        return COPY

    def let_fall(event: Any) -> str:
        say(False)
        paths = [str(one) for one in window.tk.splitlist(event.data)]
        if paths:
            dropped(paths)
        return COPY

    window.drop_target_register(DND_FILES)
    window.dnd_bind("<<DropEnter>>", entered)
    window.dnd_bind("<<DropPosition>>", lambda event: COPY)
    window.dnd_bind("<<DropLeave>>", left)
    window.dnd_bind("<<Drop>>", let_fall)
    return True


#: The class of the view laid over a window on a Mac: one, a class made twice
#: under one name ending the process.
_mac_view: Any = None


def _mac_view_class() -> Any:
    """Told only through `dropped` and `over`, which must not touch Tk: a call
    into Tk from AppKit ended the process (measured on the lab's Mac)."""
    global _mac_view
    if _mac_view is not None:
        return _mac_view
    import AppKit

    class FileDropView(AppKit.NSView):
        def hitTest_(self, point: Any) -> Any:
            return None                                  # the pointer goes through to Tk

        def draggingEntered_(self, info: Any) -> int:
            if self.told[1] is not None:
                self.told[1](True)
            return AppKit.NSDragOperationCopy

        def draggingUpdated_(self, info: Any) -> int:
            return AppKit.NSDragOperationCopy

        def draggingExited_(self, info: Any) -> None:
            if self.told[1] is not None:
                self.told[1](False)

        def prepareForDragOperation_(self, info: Any) -> bool:
            return True

        def performDragOperation_(self, info: Any) -> bool:
            if self.told[1] is not None:
                self.told[1](False)
            urls = info.draggingPasteboard().readObjectsForClasses_options_(
                [AppKit.NSURL], {"NSPasteboardURLReadingFileURLsOnlyKey": True}) or []
            paths = [str(one.path()) for one in urls]
            if paths:
                self.told[0](paths)
            return bool(paths)

    _mac_view = FileDropView
    return _mac_view


def _mac(window: Any, dropped: Callable[[List[str]], None],
         over: Optional[Callable[[bool], None]]) -> bool:
    import AppKit
    window.update_idletasks()
    size = (window.winfo_width(), window.winfo_height())
    native = next((one for one in AppKit.NSApplication.sharedApplication().windows()
                   if str(one.title()) == window.title()
                   and int(one.contentView().bounds().size.width) == size[0]
                   and int(one.contentView().bounds().size.height) == size[1]), None)
    if native is None:
        return False
    content = native.contentView()
    view = _mac_view_class().alloc().initWithFrame_(content.bounds())
    view.told = (dropped, over)
    view.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
    view.registerForDraggedTypes_([AppKit.NSPasteboardTypeFileURL])
    content.addSubview_(view)
    _held[id(window)] = ("mac", view)
    return True


def _ole(window: Any, dropped: Callable[[List[str]], None],
         over: Optional[Callable[[bool], None]]) -> bool:
    """The window as an OLE drop target: IDropTarget, its table of calls
    written out by hand, for a window of one thread that pumps its messages."""
    import ctypes
    from ctypes import wintypes

    ole32, shell32, user32 = ctypes.windll.ole32, ctypes.windll.shell32, ctypes.windll.user32
    result, pointer, word = ctypes.c_long, ctypes.c_void_p, wintypes.DWORD
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    ole32.OleInitialize.argtypes = [pointer]
    ole32.OleInitialize.restype = result
    ole32.RegisterDragDrop.argtypes = [wintypes.HWND, pointer]
    ole32.RegisterDragDrop.restype = result
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR,
                                       wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT

    class Format(ctypes.Structure):
        _fields_ = [("format", wintypes.WORD), ("device", pointer), ("aspect", word),
                    ("index", ctypes.c_long), ("medium", word)]

    class Medium(ctypes.Structure):
        _fields_ = [("medium", word), ("handle", pointer), ("release", pointer)]

    ole32.ReleaseStgMedium.argtypes = [ctypes.POINTER(Medium)]
    ole32.ReleaseStgMedium.restype = None
    CF_HDROP, DVASPECT_CONTENT, TYMED_HGLOBAL, COPY, NONE = 15, 1, 1, 1, 0

    def table_of(thing: int) -> Any:
        return ctypes.cast(ctypes.cast(thing, ctypes.POINTER(pointer))[0], ctypes.POINTER(pointer))

    def has_files(thing: int) -> bool:
        ask = ctypes.WINFUNCTYPE(result, pointer, ctypes.POINTER(Format))(table_of(thing)[5])
        wanted = Format(CF_HDROP, None, DVASPECT_CONTENT, -1, TYMED_HGLOBAL)
        return ask(thing, ctypes.byref(wanted)) == 0

    def files_in(thing: int) -> List[str]:
        get = ctypes.WINFUNCTYPE(result, pointer, ctypes.POINTER(Format),
                                 ctypes.POINTER(Medium))(table_of(thing)[3])
        wanted, medium = Format(CF_HDROP, None, DVASPECT_CONTENT, -1, TYMED_HGLOBAL), Medium()
        if get(thing, ctypes.byref(wanted), ctypes.byref(medium)) != 0:
            return []
        try:
            found = []
            for n in range(shell32.DragQueryFileW(medium.handle, 0xFFFFFFFF, None, 0)):
                size = shell32.DragQueryFileW(medium.handle, n, None, 0) + 1
                name = ctypes.create_unicode_buffer(size)
                shell32.DragQueryFileW(medium.handle, n, name, size)
                found.append(name.value)
            return found
        finally:
            ole32.ReleaseStgMedium(ctypes.byref(medium))

    state = {"files": False}

    def say_over(on: bool) -> None:
        if over is not None:
            try:
                over(on)
            except Exception:
                log.debug("nobody heard of the file over the window", exc_info=True)

    def query(_this: int, wanted: int, out: int) -> int:
        asked = ctypes.string_at(wanted, 16)
        tail = b"\x00\x00\x00\x00\x00\x00\x00\x00\xc0\x00\x00\x00\x00\x00\x00\x46"
        if asked in (b"\x00" * 4 + tail[4:], b"\x22\x01\x00\x00" + tail[4:]):
            ctypes.cast(out, ctypes.POINTER(pointer))[0] = _this
            return 0
        ctypes.cast(out, ctypes.POINTER(pointer))[0] = None
        return ctypes.c_long(0x80004002).value            # E_NOINTERFACE

    def count(_this: int) -> int:
        return 1                                         # held here: never freed by it

    def enter(_this: int, thing: int, _keys: int, _at: int, effect: Any) -> int:
        state["files"] = has_files(thing)
        effect[0] = COPY if state["files"] else NONE
        if state["files"]:
            say_over(True)
        return 0

    def move(_this: int, _keys: int, _at: int, effect: Any) -> int:
        effect[0] = COPY if state["files"] else NONE
        return 0

    def leave(_this: int) -> int:
        if state["files"]:
            say_over(False)
        state["files"] = False
        return 0

    def drop(_this: int, thing: int, _keys: int, _at: int, effect: Any) -> int:
        effect[0] = COPY if state["files"] else NONE
        say_over(False)
        state["files"] = False
        try:
            found = files_in(thing)
            if found:
                dropped(found)
        except Exception:
            log.warning("files dragged onto the window were lost", exc_info=True)
        return 0

    place = ctypes.c_longlong                            # a POINTL, in one register
    calls = [
        ctypes.WINFUNCTYPE(result, pointer, pointer, pointer)(query),
        ctypes.WINFUNCTYPE(word, pointer)(count),
        ctypes.WINFUNCTYPE(word, pointer)(count),
        ctypes.WINFUNCTYPE(result, pointer, pointer, word, place, ctypes.POINTER(word))(enter),
        ctypes.WINFUNCTYPE(result, pointer, word, place, ctypes.POINTER(word))(move),
        ctypes.WINFUNCTYPE(result, pointer)(leave),
        ctypes.WINFUNCTYPE(result, pointer, pointer, word, place, ctypes.POINTER(word))(drop)]
    table = (pointer * len(calls))(*[ctypes.cast(one, pointer) for one in calls])
    target = (pointer * 1)(ctypes.cast(table, pointer))

    window.update_idletasks()
    handle = user32.GetAncestor(int(window.winfo_id()), GA_ROOT)
    if not handle:
        return False
    if (ole32.OleInitialize(None) & 0xFFFFFFFF) not in (0, 1):
        return False
    if ole32.RegisterDragDrop(handle, ctypes.cast(target, pointer)) != 0:
        return False
    _held[int(handle)] = ("ole", calls, table, target)
    return True


def _windows(window: Any, dropped: Callable[[List[str]], None]) -> bool:
    import ctypes
    from ctypes import wintypes

    user32, shell32 = ctypes.windll.user32, ctypes.windll.shell32
    result = ctypes.c_ssize_t
    procedure = ctypes.WINFUNCTYPE(result, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                   wintypes.LPARAM)
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
    user32.CallWindowProcW.restype = result
    shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR,
                                       wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragFinish.argtypes = [wintypes.HANDLE]
    shell32.DragFinish.restype = None

    window.update_idletasks()
    handle = user32.GetAncestor(int(window.winfo_id()), GA_ROOT)
    if not handle:
        return False

    def paths_of(drop: int) -> List[str]:
        out = []
        for n in range(shell32.DragQueryFileW(drop, 0xFFFFFFFF, None, 0)):
            size = shell32.DragQueryFileW(drop, n, None, 0) + 1
            name = ctypes.create_unicode_buffer(size)
            shell32.DragQueryFileW(drop, n, name, size)
            out.append(name.value)
        return out

    def hook(hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == WM_DROPFILES:
            try:
                dropped(paths_of(wparam))
            except Exception:
                log.warning("files dragged onto the window were lost", exc_info=True)
            finally:
                shell32.DragFinish(wparam)
            return 0
        answer = user32.CallWindowProcW(old, hwnd, message, wparam, lparam)
        if message == WM_NCDESTROY:
            _held.pop(int(handle), None)
        return answer

    callback = procedure(hook)
    old = user32.SetWindowLongPtrW(handle, GWLP_WNDPROC, ctypes.cast(callback, ctypes.c_void_p))
    if not old:
        return False
    _held[int(handle)] = ("message", callback, old)
    shell32.DragAcceptFiles(handle, True)
    return True


__all__ = ["accept_files", "let_go"]

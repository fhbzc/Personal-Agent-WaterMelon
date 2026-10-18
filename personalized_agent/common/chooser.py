"""Native directory chooser (section 12, as amended).

A directory setting is chosen, not typed. The chooser belongs to the machine
the agent runs on, so the frontend cannot open it: it asks the backend, which
opens the platform's own dialog and returns what was selected.

Nothing here writes anything. Choosing a location only reports it; the setting
is stored through the ordinary settings path, with the ordinary validation.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)

DIALOG_TIMEOUT_SECONDS = 300.0

#: The window toolkit's own thread, when the desktop window is running:
#: (its root, a way to run a function on that thread). A dialog is opened
#: there - see `_show`.
_HOST: Optional[tuple] = None
#: When the dialog now open was opened (time.monotonic), or 0.
OPEN_SINCE = 0.0


def late(by: float) -> None:
    """The window thread got round to its own work `by` seconds late while a
    dialog was open: logged, since a dialog stutters exactly then."""
    if OPEN_SINCE and by > 0.3:
        log.info("the window thread was %.2fs late while a chooser was open", by)


def dialogs_on(root: Any, submit: Callable[[Callable[[], None]], None]) -> None:
    """Open the dialogs on the thread that owns the app's windows.

    THE FOLDER DIALOG DID NOT APPEAR. It made a second Tk of its own on a
    thread of the API server's, while the desktop character already runs
    Tk on the main thread - and Tk is not made for two at once: the dialog
    failed without a word, or opened behind everything. With the window
    running, the dialog is asked for on its thread, as a child of its root.
    """
    global _HOST
    _HOST = (root, submit)


def _show(dialog: Callable[[Any], Any], what: str) -> Any:
    """Run `dialog(parent)` and return what it chose (None when cancelled),
    or the exception it raised, or None after the timeout."""
    result: "queue.Queue" = queue.Queue(maxsize=1)
    host = _HOST
    asked = time.monotonic()

    def on_its_thread(parent_of: Callable[[], Any], done: Callable[[Any], None]) -> None:
        global OPEN_SINCE
        try:
            started = time.monotonic()
            parent = parent_of()
            parent.attributes("-topmost", True)
            made = OPEN_SINCE = time.monotonic()
            chosen = dialog(parent)
            closed = time.monotonic()
            OPEN_SINCE = 0.0
            done(parent)
            # HOW LONG EACH PART TOOK, to tell a dialog slow to come up from
            # one slow while it is open (see `late`).
            log.info("the %s chooser: %.2fs before the window thread took it, "
                     "%.2fs to make its window, open %.1fs", what,
                     started - asked, made - started, closed - made)
            result.put(chosen or None)
        except Exception as exc:                   # pragma: no cover - no display
            OPEN_SINCE = 0.0
            log.warning("the %s chooser could not be opened", what, exc_info=True)
            result.put(exc)

    if host is not None:
        import tkinter
        root, submit = host

        def make() -> Any:
            top = tkinter.Toplevel(root)
            top.withdraw()
            return top

        submit(lambda: on_its_thread(make, lambda top: top.destroy()))
    else:
        def own() -> Any:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            return root

        # The dialog needs its own thread: the API request must not block
        # the server for as long as the user takes to decide, and Tk wants a
        # thread of its own to own the window.
        threading.Thread(target=lambda: on_its_thread(own, lambda r: r.destroy()),
                         name=f"{what}-chooser", daemon=True).start()
    try:
        return result.get(timeout=DIALOG_TIMEOUT_SECONDS)
    except queue.Empty:
        return TimeoutError("the chooser was left open too long")


def warm_up(root: Any) -> None:
    """THE FIRST FOLDER DIALOG A PROCESS OPENS took two to three and a half
    seconds to appear on Windows, and every later one well under one:
    Explorer's parts - and the extensions installed into it - load the
    first time one is shown, and creating one without showing it loads
    nothing that helps (all measured). So, once, soon after the
    application starts, one is shown where it cannot be seen - made fully
    transparent and moved off the screen the moment its window exists, long
    before it would appear - and closed straight away. The user's first
    click then opens one as fast as any later click.

    On the thread that owns the windows (`root`'s), which it holds for those
    seconds - at start, before anyone is choosing a folder. Nothing anywhere
    but Windows, and nothing under the tests."""
    if sys.platform != "win32" or not available():
        return
    import ctypes
    import tkinter
    import uuid
    from ctypes import wintypes
    from tkinter import filedialog

    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    title = f"Personal Agent WaterMelon - getting the folder dialog ready {uuid.uuid4().hex[:8]}"
    began = time.monotonic()

    def out_of_sight_and_closed() -> None:
        while time.monotonic() - began < 15:
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                style = user32.GetWindowLongW(hwnd, -20)             # GWL_EXSTYLE
                user32.SetWindowLongW(hwnd, -20, style | 0x80000 | 0x80)  # layered, tool
                user32.SetLayeredWindowAttributes(hwnd, 0, 0, 2)     # alpha 0
                user32.SetWindowPos(hwnd, None, -32000, -32000, 0, 0,
                                    0x1 | 0x4 | 0x10)  # no size, no z-order, no activation
                user32.PostMessageW(hwnd, 0x10, 0, 0)                # WM_CLOSE
                return
            time.sleep(0.003)

    hidden = None
    try:
        hidden = tkinter.Toplevel(root)
        hidden.overrideredirect(True)
        hidden.attributes("-alpha", 0.0)
        hidden.geometry("1x1+-32000+-32000")
        hidden.update_idletasks()
        threading.Thread(target=out_of_sight_and_closed, name="chooser-warm-up",
                         daemon=True).start()
        filedialog.askdirectory(parent=hidden, title=title, mustexist=True)
        log.info("the folder dialog made ready in %.2fs", time.monotonic() - began)
    except Exception:                               # pragma: no cover - no display
        log.debug("could not get the folder dialog ready", exc_info=True)
    finally:
        if hidden is not None:
            try:
                hidden.destroy()
            except Exception:                       # pragma: no cover
                pass


def available() -> bool:
    """Whether this machine can show a directory chooser at all.

    Never under the tests (PA_NO_DIALOGS): a test that reaches the real
    chooser would open dialogs on the desktop of whoever runs them."""
    if os.environ.get("PA_NO_DIALOGS"):
        return False
    try:
        if importlib.util.find_spec("tkinter") is None:
            return False
    except Exception:
        return False
    if sys.platform.startswith("linux") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    return True


def choose_directory(title: str = "Choose a directory",
                     start_at: Optional[str] = None) -> Dict[str, Any]:
    """Open the platform's directory chooser and report what was selected.

    Returns ``{"selected": <path or None>, "cancelled": bool, ...}``. A dialog
    that cannot be shown is reported as such rather than raising, so the
    interface can fall back to a typed path (section 12, as amended).
    """
    if not available():
        return {"selected": None, "cancelled": False, "available": False,
                "reason": "this machine has no directory chooser; type the path instead"}

    def dialog(parent: Any) -> Any:
        from tkinter import filedialog
        initial = start_at if start_at and Path(start_at).is_dir() else None
        return filedialog.askdirectory(parent=parent, title=title,
                                       initialdir=initial, mustexist=True)

    chosen = _show(dialog, "directory")
    if isinstance(chosen, TimeoutError):
        return {"selected": None, "cancelled": True, "available": True,
                "reason": str(chosen)}
    if isinstance(chosen, Exception):
        return {"selected": None, "cancelled": False, "available": False,
                "reason": f"the directory chooser could not be opened: {chosen}"}
    if not chosen:
        return {"selected": None, "cancelled": True, "available": True, "reason": ""}
    return {"selected": str(Path(chosen)), "cancelled": False, "available": True,
            "reason": "", **describe_directory(chosen)}


def application_filetypes() -> list:
    """What counts as an application on this platform."""
    if sys.platform.startswith("win"):
        return [("Applications", "*.exe"), ("All files", "*.*")]
    if sys.platform == "darwin":
        return [("Applications", "*.app"), ("All files", "*")]
    return [("All files", "*")]


def choose_application(title: str = "Choose an application",
                       start_at: Optional[str] = None) -> Dict[str, Any]:
    """Open the platform's file chooser, filtered to applications.

    The rest of the system matches activity by the application's own name, so
    that is what is stored; the full path is returned too, for display.
    """
    if not available():
        return {"selected": None, "name": None, "cancelled": False, "available": False,
                "reason": "this machine has no file chooser; type the name instead"}

    def dialog(parent: Any) -> Any:
        from tkinter import filedialog
        initial = start_at if start_at and Path(start_at).is_dir() else _programs_dir()
        return filedialog.askopenfilename(parent=parent, title=title,
                                          initialdir=initial,
                                          filetypes=application_filetypes())

    chosen = _show(dialog, "application")
    if isinstance(chosen, TimeoutError):
        return {"selected": None, "name": None, "cancelled": True,
                "available": True, "reason": str(chosen)}
    if isinstance(chosen, Exception):
        return {"selected": None, "name": None, "cancelled": False, "available": False,
                "reason": f"the application chooser could not be opened: {chosen}"}
    if not chosen:
        return {"selected": None, "name": None, "cancelled": True, "available": True,
                "reason": ""}
    path = Path(chosen)
    # macOS bundles are directories; their name is what activity reports.
    name = path.name
    return {"selected": str(path), "name": name, "cancelled": False,
            "available": True, "reason": ""}


def choose_file(title: str = "Choose a file", filetypes: Optional[list] = None,
                start_at: Optional[str] = None) -> Dict[str, Any]:
    """Open the platform's file chooser for one existing file - a lessons
    package's zip, say. Reported as `choose_directory` reports, and never
    shown under the tests (PA_NO_DIALOGS)."""
    if not available():
        return {"selected": None, "cancelled": False, "available": False,
                "reason": "this machine has no file chooser; type the path instead"}

    def dialog(parent: Any) -> Any:
        from tkinter import filedialog
        initial = start_at if start_at and Path(start_at).is_dir() else None
        return filedialog.askopenfilename(parent=parent, title=title,
                                          initialdir=initial,
                                          filetypes=filetypes or [("All files", "*")])

    chosen = _show(dialog, "file")
    if isinstance(chosen, TimeoutError):
        return {"selected": None, "cancelled": True, "available": True,
                "reason": str(chosen)}
    if isinstance(chosen, Exception):
        return {"selected": None, "cancelled": False, "available": False,
                "reason": f"the file chooser could not be opened: {chosen}"}
    if not chosen:
        return {"selected": None, "cancelled": True, "available": True, "reason": ""}
    return {"selected": str(Path(chosen)), "cancelled": False, "available": True,
            "reason": ""}


def _programs_dir() -> Optional[str]:
    for candidate in (os.environ.get("ProgramFiles"), "/Applications", "/usr/bin"):
        if candidate and Path(candidate).is_dir():
            return candidate
    return None


def describe_directory(path: str) -> Dict[str, Any]:
    """Whether a location can actually be used, said when it is chosen.

    Section 12, as amended: an unusable location is reported now, not when
    something later fails on it.
    """
    location = Path(path).expanduser()
    if not location.exists():
        return {"usable": False, "problem": "that location does not exist"}
    if not location.is_dir():
        return {"usable": False, "problem": "that is a file, not a directory"}
    if not os.access(location, os.R_OK):
        return {"usable": False, "problem": "that directory cannot be read"}
    return {"usable": True, "problem": ""}

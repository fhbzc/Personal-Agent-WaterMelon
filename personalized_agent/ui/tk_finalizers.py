"""TK'S CLEAN-UP OF WHAT PYTHON LETS GO OF, DONE BY TK'S THREAD IN ITS OWN TIME (2026-11-17: the app stopped
ten seconds after a start, "Fatal Python error: PyEval_RestoreThread: ... the current Python thread state is
NULL", the main thread in Tk's mainloop and nowhere else).

Why it could: _tkinter keeps, for the thread Tk runs on, the Python thread state it takes back when Tcl calls
Python (`tcl_tstate`). The main loop sets it before each event; every Tkinter call clears it on its way out
(LEAVE_TCL). From a Tk callback that does not matter - the callback's own end sets it again. But Python that
Windows calls directly within an event - a ctypes window procedure (ui/touch_zoom.py, ui/file_drop.py) - has
no such end: a Tkinter call from there leaves it empty, and the next Tk callback in that same event aborts
Python. Our window procedures call no Tkinter themselves; what does is a finalizer - a Variable, an image or a
font let go of while the garbage collector runs, which since Python 3.12 it may do as any Python function
starts, a window procedure's too.

So a finalizer only keeps its Tcl clean-up for later, and Tk's thread does it from one of its own callbacks,
where a Tkinter call is safe. `install` once, with the root, before the main loop.
"""

from __future__ import annotations

import collections
import logging
from typing import Any, Callable, Deque

log = logging.getLogger(__name__)

#: How often Tk's thread does what was kept.
EVERY_MS = 2000

_LATER: Deque[Callable[[], None]] = collections.deque()
_INSTALLED = [False]


def _keep(work: Callable[[], None]) -> None:
    _LATER.append(work)                  # any thread; deque.append is atomic


def kept() -> int:
    """How many clean-ups wait."""
    return len(_LATER)


def done_now() -> int:
    """Do what was kept - on Tk's thread, from a Tk callback (or before the main loop). How many."""
    done = 0
    while True:
        try:
            work = _LATER.popleft()
        except IndexError:
            return done
        try:
            work()
        except Exception:                    # the application gone meanwhile: nothing to clean
            pass
        done += 1


def _quietly(call: Callable[..., Any], *args: Any) -> None:
    try:
        call(*args)
    except Exception:
        pass


def _variable_let_go(self: Any) -> None:
    tk = getattr(self, "_tk", None)
    if tk is None:
        return
    name, commands = self._name, self._tclCommands
    self._tclCommands = None

    def later() -> None:
        try:
            if tk.getboolean(tk.call("info", "exists", name)):
                tk.globalunsetvar(name)
        except Exception:
            pass
        for one in commands or ():
            _quietly(tk.deletecommand, one)
    _keep(later)


def _image_let_go(self: Any) -> None:
    name = getattr(self, "name", None)
    tk = getattr(self, "tk", None)
    if not name or tk is None:
        return
    self.name = None
    _keep(lambda: _quietly(tk.call, "image", "delete", name))


def _font_let_go(self: Any) -> None:
    tk = getattr(self, "_tk", None)
    if tk is None or not getattr(self, "delete_font", False):
        return
    name = self.name
    _keep(lambda: _quietly(tk.call, "font", "delete", name))


def _pil_let_go(attribute: str) -> Callable[[Any], None]:
    def let_go(self: Any) -> None:
        photo = self.__dict__.get(attribute)
        if photo is not None:
            _image_let_go(photo)         # its name taken: the tkinter image's own finalizer does nothing
    return let_go


def _classes() -> list:
    """(key, class, our finalizer) for each kind whose finalizer calls Tcl."""
    import tkinter
    import tkinter.font
    out = [("tkinter.Variable", tkinter.Variable, _variable_let_go),
           ("tkinter.Image", tkinter.Image, _image_let_go),
           ("tkinter.font.Font", tkinter.font.Font, _font_let_go)]
    try:
        from PIL import ImageTk
        out += [(f"PIL.ImageTk.{kind.__name__}", kind, _pil_let_go(f"_{kind.__name__}__photo"))
                for kind in (ImageTk.PhotoImage, ImageTk.BitmapImage)]
    except Exception:                        # no PIL: only tkinter's own
        log.debug("PIL's images keep their own clean-up", exc_info=True)
    return out


#: Their own finalizers, for `uninstall`.
_ORIGINAL: dict = {}


def install(root: Any) -> bool:
    """Every finalizer's Tcl call kept for later; Tk's thread does them every EVERY_MS. Whether it was now."""
    if _INSTALLED[0]:
        return False
    for key, kind, ours in _classes():
        _ORIGINAL[key] = kind.__dict__.get("__del__")
        kind.__del__ = ours
    _INSTALLED[0] = True

    def tick() -> None:
        done_now()
        try:
            root.after(EVERY_MS, tick)
        except Exception:                    # the root gone: the end
            pass
    root.after(EVERY_MS, tick)
    return True


def uninstall() -> None:
    """Back to tkinter's and PIL's own finalizers (tests)."""
    for key, kind, _ours in _classes():
        if key in _ORIGINAL:
            if _ORIGINAL[key] is None:
                del kind.__del__
            else:
                kind.__del__ = _ORIGINAL[key]
    _ORIGINAL.clear()
    _INSTALLED[0] = False


__all__ = ["EVERY_MS", "done_now", "install", "kept", "uninstall"]

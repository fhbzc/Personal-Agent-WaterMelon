"""TO THE RECYCLE BIN, NOT GONE (the user, 2026-10-31: a project's work
folder cleared "move to the recycle bin") - each system's own:

  - WINDOWS: the shell's own delete with undo (SHFileOperationW,
    FOF_ALLOWUNDO) - in the Recycle Bin, put back from there;
  - MACOS: the Finder's Trash, through NSFileManager (trashItemAtURL) - "Put
    Back" works;
  - LINUX: the freedesktop.org trash of the user's home - the file in
    `Trash/files`, and beside it in `Trash/info` where it was and when, as
    every desktop's file manager reads it.

Nothing here deletes anything for good: a system with no trash says so, and
the caller leaves the file where it is.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


class NoTrash(RuntimeError):
    """This system has no trash to move to: nothing was moved."""


def move_to_trash(path: Path) -> None:
    """`path` - a file or a folder, with everything in it - to the trash."""
    path = Path(path)
    if not path.exists():
        return
    if sys.platform.startswith("win"):
        _windows(path)
    elif sys.platform == "darwin":
        _macos(path)
    else:
        _freedesktop(path)


def _windows(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE = 3
    FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400
    operation = SHFILEOPSTRUCTW(
        hwnd=None, wFunc=FO_DELETE, pFrom=str(path.resolve()) + "\0", pTo=None,
        fFlags=FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI)
    said = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if said != 0 or operation.fAnyOperationsAborted:
        raise OSError(f"could not move {path} to the Recycle Bin (code {said})")


def _macos(path: Path) -> None:
    try:
        from Foundation import NSFileManager, NSURL  # type: ignore
    except ImportError as exc:                         # pragma: no cover - the app has it
        raise NoTrash("no Foundation to reach the Trash with") from exc
    url = NSURL.fileURLWithPath_(str(path.resolve()))
    done, _moved, error = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
        url, None, None)
    if not done:
        raise OSError(f"could not move {path} to the Trash: {error}")


def _freedesktop(path: Path) -> None:
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Trash"
    files, info = home / "files", home / "info"
    files.mkdir(parents=True, exist_ok=True)
    info.mkdir(parents=True, exist_ok=True)
    name, n = path.name, 1
    while (files / name).exists() or (info / f"{name}.trashinfo").exists():
        n += 1
        name = f"{path.stem}.{n}{path.suffix}"
    (info / f"{name}.trashinfo").write_text(
        "[Trash Info]\n"
        f"Path={quote(str(path.resolve()))}\n"
        f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n", encoding="utf-8")
    shutil.move(str(path), str(files / name))

"""WORDS ON THE CLIPBOARD, PUT THERE BY THE APPLICATION ITSELF (the user,
2026-11-01).

A reply opened from the Overview's to-do opened Gmail first and asked the
page to copy after - and a browser lets only the page in front write the
clipboard: Gmail was in front, the copy was refused, and the note on the
desktop said it was there. So what is to be pasted is put on the system's
clipboard by the application, before anything is opened, whatever is in
front - and said to be there only when it is:

  - WINDOWS: the clipboard's own calls (OpenClipboard, SetClipboardData,
    Unicode text), tried again for a moment while another program holds it;
  - MACOS: pbcopy;
  - LINUX: wl-copy, xclip or xsel - whichever is there.

It writes nothing but the clipboard.

WHAT WAS COPIED, READ WHEN THE USER PASTES IN CHAT (the user, 2026-11-04: a WeChat
screenshot had to be pasted into WeChat and copied again before a page would
take it). A browser sees only some of what a program puts there, so the
application reads the clipboard itself: `files_on_it` - files copied, a picture
in any of the usual forms, and the pictures an HTML copy points to on this
computer. Only when the user pastes, and nothing but that one paste. When
nothing is found the NAMES of the forms there are logged - never what is in them.
"""

from __future__ import annotations

import base64
import html as html_text
import io
import logging
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

log = logging.getLogger(__name__)

#: How often, and how long apart, the clipboard is asked for while another
#: program holds it open.
TRIES, BETWEEN = 10, 0.05
#: What an HTML copy may point to; the picture forms Windows names.
PICTURE_FILES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
NAMED_PICTURES = ("PNG", "image/png", "JFIF", "image/jpeg", "GIF", "image/gif", "image/webp",
                  "image/bmp")


def copy(text: str) -> bool:
    """`text` on the clipboard - whether it is."""
    text = str(text or "")
    try:
        if sys.platform.startswith("win"):
            return _windows(text)
        if sys.platform == "darwin":
            return _run(["pbcopy"], text, {"LANG": "en_US.UTF-8"})
        for command in (["wl-copy"], ["xclip", "-selection", "clipboard"],
                        ["xsel", "--clipboard", "--input"]):
            if shutil.which(command[0]):
                return _run(command, text)
    except Exception:                                    # pragma: no cover - not copied
        log.debug("the clipboard could not be written", exc_info=True)
    return False


def _run(command: list, text: str, env: dict = None) -> bool:
    import os
    done = subprocess.run(command, input=text.encode("utf-8"), timeout=5,
                          env=dict(os.environ, **(env or {})),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return done.returncode == 0


def _windows(text: str) -> bool:
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    data = text.encode("utf-16-le") + b"\0\0"
    for _ in range(TRIES):
        if user32.OpenClipboard(None):
            break
        time.sleep(BETWEEN)
    else:
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        at = kernel32.GlobalLock(handle)
        if not at:
            kernel32.GlobalFree(handle)
            return False
        ctypes.memmove(at, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)              # still ours when it was refused
            return False
        return True                                  # the clipboard's now
    finally:
        user32.CloseClipboard()


def files_on_it(with_text: bool = False) -> List[Any]:
    """What was copied, to attach: each a Path, or the bytes of a picture.
    `with_text`: words are being pasted too - then only the pictures an HTML
    copy holds (a spreadsheet's cells come with a picture of themselves)."""
    try:
        if sys.platform.startswith("win"):
            return _read_windows(with_text)
        if sys.platform == "darwin":
            return _read_mac(with_text)
        return [] if with_text else _read_pillow()
    except Exception:
        log.info("the clipboard could not be read", exc_info=True)
        return []


def as_picture(data: bytes) -> bytes:
    """A picture's bytes in a form the models take; PNG unless it is one."""
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        if image.format in ("PNG", "JPEG", "GIF", "WEBP"):
            return data
        out = io.BytesIO()
        image.convert("RGB").save(out, "PNG")
        return out.getvalue()


def pictures_in_html(said: Any) -> List[Any]:
    """The pictures an HTML copy holds: those on this computer, and those
    written in it. One on the web is not fetched."""
    if isinstance(said, bytes):
        said = said.decode("utf-8", "ignore")
    found: List[Any] = []
    for src in re.findall(r"""<img\b[^>]*?\bsrc\s*=\s*["']?([^"'>\s]+)""", str(said or ""), re.I):
        src = html_text.unescape(src)
        if src.lower().startswith("data:image/") and ";base64," in src:
            try:
                found.append(base64.b64decode(src.split(",", 1)[1]))
            except ValueError:
                pass
            continue
        if src.lower().startswith("file:"):
            src = urllib.request.url2pathname(urllib.parse.urlsplit(src).path)
        elif not re.match(r"^([A-Za-z]:[\\/]|/)", src):
            continue
        path = Path(src)
        if path.suffix.lower() in PICTURE_FILES and path.is_file():
            found.append(path)
    return found


def _read_pillow() -> List[Any]:
    """Files copied, or a picture, as Pillow reads them."""
    from PIL import ImageGrab
    said = None
    for _ in range(TRIES):
        try:
            said = ImageGrab.grabclipboard()
            break
        except OSError:                                  # another program has it open
            time.sleep(BETWEEN)
    if isinstance(said, list):
        return [Path(one) for one in said]
    if said is None:
        return []
    out = io.BytesIO()
    # A screenshot's fourth byte is often no alpha at all, and all zero.
    (said if said.format == "PNG" else said.convert("RGB")).save(out, "PNG")
    return [out.getvalue()]


def _read_windows(with_text: bool) -> List[Any]:
    names, held = _windows_held()
    pictures = pictures_in_html(held.get("HTML Format"))
    if with_text:
        return pictures
    found = _read_pillow() or pictures
    if not found:
        found = [held[name] for name in NAMED_PICTURES if held.get(name)][:1]
    if not found:
        log.info("nothing to attach on the clipboard; the forms there: %s", names)
    return found


def _windows_held() -> Tuple[List[str], Dict[str, bytes]]:
    """The names of the forms on the clipboard, and what is in those wanted."""
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
    user32.EnumClipboardFormats.restype = wintypes.UINT
    user32.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    for _ in range(TRIES):
        if user32.OpenClipboard(None):
            break
        time.sleep(BETWEEN)
    else:
        return [], {}
    names: List[str] = []
    held: Dict[str, bytes] = {}
    try:
        kind = user32.EnumClipboardFormats(0)
        while kind:
            buffer = ctypes.create_unicode_buffer(128)
            name = buffer.value if user32.GetClipboardFormatNameW(kind, buffer, 128) else str(kind)
            names.append(name)
            if name == "HTML Format" or name in NAMED_PICTURES:
                handle = user32.GetClipboardData(kind)
                at = kernel32.GlobalLock(handle) if handle else None
                if at:
                    held[name] = ctypes.string_at(at, kernel32.GlobalSize(handle))
                    kernel32.GlobalUnlock(handle)
            kind = user32.EnumClipboardFormats(kind)
    finally:
        user32.CloseClipboard()
    return names, held


def _read_mac(with_text: bool) -> List[Any]:
    try:
        from AppKit import NSPasteboard
        from Foundation import NSURL
    except ImportError:
        return [] if with_text else _read_pillow()
    board = NSPasteboard.generalPasteboard()
    pictures = pictures_in_html(board.stringForType_("public.html"))
    if with_text:
        return pictures
    urls = board.readObjectsForClasses_options_(
        [NSURL], {"NSPasteboardURLReadingFileURLsOnlyKey": True}) or []
    found: List[Any] = [Path(str(one.path())) for one in urls]
    if not found:
        for kind in ("public.png", "public.jpeg", "public.tiff"):
            data = board.dataForType_(kind)
            if data:
                found = [bytes(data)]
                break
    if not found and not pictures:
        log.info("nothing to attach on the clipboard; the forms there: %s",
                 [str(one) for one in board.types() or []])
    return found or pictures


__all__ = ["copy", "files_on_it", "as_picture", "pictures_in_html"]

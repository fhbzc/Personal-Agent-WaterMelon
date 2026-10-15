"""Reading each desktop's own record of what its user opened (section 19).

One question - "did the person open this file, or did a program read it?" -
asked of three systems that each already answer it for their own purposes.
None of this needs a privilege, an entitlement or a daemon; it is reading
what the desktop writes anyway.

What goes in these lists is the point. Explorer writes a shortcut when the
user opens a file; LaunchServices stamps ``kMDItemLastUsedDate`` when it
hands a file to an application; GTK appends to ``recently-used.xbel`` when a
file goes through an open dialog. A program calling ``open()`` does none of
these things, which is exactly the distinction that the filesystem
notification itself cannot make.

Each function returns ``{identifier: when}``, where the identifier is the
most specific one that platform will give up - a full path where the record
holds one, a bare filename where it does not.

Paths are returned **as the filesystem spells them**. Normalising case is
for comparing, and doing it here once put ``readme.md`` in front of a user
who had opened ``README.md``.
"""

from __future__ import annotations

import logging
import os
import struct
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree
from ..common import children

log = logging.getLogger(__name__)

#: A cap on how much of any of these lists is read. A desktop that has been
#: running for months has a long Recent folder, and the window being asked
#: about is a few hours.
AT_MOST = 500


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------
def windows_recent(since: float) -> Dict[str, float]:
    """What Explorer recorded the user opening.

    Every file the user opens through the shell gets a shortcut in one
    folder, named after the file and stamped with the time. The shortcut also
    contains the path it points at, which is worth digging out: two files
    called ``notes.md`` in different projects are different files, and the
    folder alone cannot tell them apart.
    """
    folder = _windows_recent_folder()
    if folder is None:
        return {}
    opened: Dict[str, float] = {}
    # Narrowed by time before anything is sorted or read. A folder that has
    # been filling up for months holds a thousand shortcuts and the window
    # being asked about holds two; scanning is one pass over the directory
    # entries, and only what falls inside the window is opened and parsed.
    try:
        with os.scandir(folder) as entries:
            recent = [(entry.stat().st_mtime, entry.path, entry.name)
                      for entry in entries if entry.name.endswith(".lnk")]
    except OSError:
        return {}
    recent = sorted((row for row in recent if row[0] >= since), reverse=True)
    for at, path, name in recent[:AT_MOST]:
        shortcut = Path(path)
        target = _what_the_shortcut_points_at(shortcut)
        if target:
            opened[os.path.abspath(target)] = at
        # The bare name is recorded as well as the path, never instead of it:
        # the path can be missing from the shortcut, and a name is still
        # better evidence than nothing.
        opened.setdefault(name[:-4], at)
    return opened


def _windows_recent_folder() -> Optional[Path]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    folder = Path(appdata) / "Microsoft" / "Windows" / "Recent"
    return folder if folder.is_dir() else None


def _modified_at(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


#: Offsets into a shell link, from MS-SHLLINK. The header is a fixed 0x4C
#: bytes; the flags say which of the optional structures follow it.
_HEADER_SIZE = 0x4C
_HAS_TARGET_ID_LIST = 0x01
_HAS_LINK_INFO = 0x02


def _what_the_shortcut_points_at(shortcut: Path) -> Optional[str]:
    """The path inside a .lnk, without asking the shell to resolve it.

    Resolving a shortcut properly means COM, which means loading the shell
    into this process and possibly touching the network for one that points
    at a share. The file says where it points; reading it is enough, and a
    shortcut we cannot parse simply yields nothing.
    """
    try:
        raw = shortcut.read_bytes()
    except OSError:
        return None
    if len(raw) < _HEADER_SIZE or raw[:4] != b"\x4c\x00\x00\x00":
        return None
    flags = struct.unpack_from("<I", raw, 20)[0]
    at = _HEADER_SIZE
    if flags & _HAS_TARGET_ID_LIST:
        try:
            at += 2 + struct.unpack_from("<H", raw, at)[0]
        except struct.error:
            return None
    if not flags & _HAS_LINK_INFO or at + 24 > len(raw):
        return None
    try:
        header_size = struct.unpack_from("<I", raw, at + 4)[0]
        local_offset = struct.unpack_from("<I", raw, at + 16)[0]
        suffix_offset = struct.unpack_from("<I", raw, at + 24 - 4)[0]
    except struct.error:
        return None
    unicode_path = header_size >= 0x24
    if unicode_path:
        try:
            local_offset = struct.unpack_from("<I", raw, at + 28)[0]
            suffix_offset = struct.unpack_from("<I", raw, at + 32)[0]
        except struct.error:
            unicode_path = False
    base = _string_at(raw, at + local_offset, unicode_path)
    if not base:
        return None
    suffix = _string_at(raw, at + suffix_offset, unicode_path) if suffix_offset else ""
    return base + (suffix or "")


def _string_at(raw: bytes, start: int, as_unicode: bool) -> str:
    if start <= 0 or start >= len(raw):
        return ""
    if as_unicode:
        end = start
        while end + 1 < len(raw) and raw[end:end + 2] != b"\x00\x00":
            end += 2
        try:
            return raw[start:end].decode("utf-16-le", "ignore")
        except (UnicodeDecodeError, ValueError):     # pragma: no cover
            return ""
    end = raw.find(b"\x00", start)
    if end < 0:
        end = len(raw)
    return raw[start:end].decode("mbcs" if os.name == "nt" else "utf-8", "ignore")


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------
def macos_recent(since: float, run=None) -> Dict[str, float]:
    """What LaunchServices recorded the user opening.

    ``kMDItemLastUsedDate`` is the cleanest signal of the three: Spotlight
    keeps it, LaunchServices sets it when it opens a document on the user's
    behalf, and a program reading the file does not move it. Spotlight is
    asked which files have one inside the window, and then what it says.
    """
    run = run or _run
    seconds = max(60, int(time.time() - since))
    found = run(["mdfind", "-0",
                 f"kMDItemLastUsedDate >= $time.now(-{seconds})"])
    if not found:
        return {}
    paths = [p for p in found.split("\0") if p][:AT_MOST]
    if not paths:
        return {}
    stamps = run(["mdls", "-name", "kMDItemLastUsedDate", "-raw"] + paths)
    return _pair_up(paths, stamps or "")


def _pair_up(paths: List[str], stamps: str) -> Dict[str, float]:
    """``mdls -raw`` gives one value per file, in the order asked.

    With no separator between them, which is why the count is checked: a
    mismatch means the values cannot be trusted to line up with the paths,
    and a wrong time attached to a real path is worse than no answer.
    """
    values = [v for v in stamps.split("\0")] if "\0" in stamps else stamps.splitlines()
    if len(values) != len(paths):
        return {}
    opened: Dict[str, float] = {}
    for path, value in zip(paths, values):
        at = _as_epoch(value.strip())
        if at:
            opened[os.path.abspath(path)] = at
    return opened


def _as_epoch(value: str) -> Optional[float]:
    """``2026-09-19 03:11:34 +0000`` as seconds, or nothing."""
    if not value or value == "(null)":
        return None
    for layout in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
        try:
            import datetime as _dt

            parsed = _dt.datetime.strptime(value, layout)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_dt.timezone.utc)
            return parsed.timestamp()
        except ValueError:
            continue
    return None


def _run(command: List[str]) -> str:       # pragma: no cover - platform tool
    try:
        done = children.run_short(command, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------
def linux_recent(since: float, home: Optional[Path] = None) -> Dict[str, float]:
    """What the desktop's recent-documents list holds.

    GTK and anything using the freedesktop list write ``recently-used.xbel``;
    KDE keeps a directory of small desktop files instead. Both are written
    when a file goes through an open dialog or a file manager, and neither is
    touched by a program reading a file.
    """
    base = Path(home or os.path.expanduser("~")) / ".local" / "share"
    opened: Dict[str, float] = {}
    opened.update(_read_xbel(base / "recently-used.xbel", since))
    opened.update(_read_kde_recent(base / "RecentDocuments", since))
    return opened


def _read_xbel(path: Path, since: float) -> Dict[str, float]:
    try:
        tree = ElementTree.parse(str(path))
    except (OSError, ElementTree.ParseError):
        return {}
    opened: Dict[str, float] = {}
    for bookmark in list(tree.getroot())[:AT_MOST * 4]:
        href = bookmark.get("href") or ""
        if not href.startswith("file://"):
            continue
        at = _as_epoch_iso(bookmark.get("visited") or bookmark.get("modified"))
        if at is None or at < since:
            continue
        opened[_from_file_url(href)] = at
    return opened


def _read_kde_recent(folder: Path, since: float) -> Dict[str, float]:
    try:
        entries = list(folder.glob("*.desktop"))[:AT_MOST]
    except OSError:
        return {}
    opened: Dict[str, float] = {}
    for entry in entries:
        at = _modified_at(entry)
        if at < since:
            continue
        try:
            for line in entry.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("URL[$e]=") or line.startswith("URL="):
                    url = line.split("=", 1)[1].strip()
                    if url.startswith("file://"):
                        opened[_from_file_url(url)] = at
                    break
        except OSError:
            continue
    return opened


def _from_file_url(url: str) -> str:
    from urllib.parse import unquote, urlparse

    return os.path.abspath(unquote(urlparse(url).path))


def _as_epoch_iso(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    import datetime as _dt

    text = value.replace("Z", "+00:00")
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.timestamp()

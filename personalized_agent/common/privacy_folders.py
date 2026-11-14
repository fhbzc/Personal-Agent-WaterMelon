"""TWO PRIVACY PROTECTIONS (the user, 2026-11-12).

PRIVACY FOLDERS - folders or files the user adds (`PATHS_SETTING`):
  - the monitor records only the outermost name - the folder or file the
    user added - never a name inside it, and never reads what is in it;
  - no search reads it (the local index walks round it; the agents' tools
    and the code their runs run are refused it);
  - while one of its files is open anywhere on the screen, NO picture of the
    screen is taken at all (the user: "隐私文件打开就整屏不截 ... 直接是不截图，
    而不是截图涂黑"), and a window showing one is not read.

PRIVATE MODE (`MODE_SETTING`) - turned on and off from the avatar's menu: while
on, nothing of the user's activity is recorded - no screen read or taken, no
file recorded - until it is turned off; the watermelon is red meanwhile.

A WEBSITE'S PRIVATE MODE (the user, 2026-11-12: "你的private mode 要follow
https://agentssociety.org/ 的protocol") - the protocol "agent-private-mode",
version 1, as agentssociety.org gives it (/private-mode, API.md section 9,
/.well-known/agent-private-mode): a site the user asked to, signals on every
page it sends - the header `Agent-Private-Mode: on`, `<meta name="agent-
private-mode" content="on">`, `<html data-agent-private-mode="on">`, a window
title starting "[Private]", and words at the top of the page. While a page
carrying it is open, an agent following it takes no picture of it, does not
read it (nor by OCR), does not operate it, and keeps and sends nothing of it -
what it saw before it noticed, discarded. Here (the user: "我只排除这个窗口"):
THAT WINDOW ALONE IS LEFT OUT, as a privacy folder's file is - it is not read,
nothing of it is kept, and while it is open no picture of the screen is taken
at all (the user's rule: "直接是不截图，而不是截图涂黑"); the rest of what the user
does is recorded as ever, and private mode stays the user's own switch.

PRIVATE WEB ADDRESSES - in the same list ("也可以允许我加url, 这个url下的所有网址
都不会view和截图，和现在这个一样"): a line `https://host/path` makes every page
under it a PRIVATE PAGE, as one carrying the signal: never read (the
extension's words, the browser's history, the agents' own web reads), its
window not read, no picture while it is open. A page's window is known by its
title: the title of a page at a private address is remembered when it is seen
(the extension says each page's address with its words; the browser's history
says it too), and a window whose title begins with one is a private page's.

Which window shows a file is not something the system tells: it is judged
from what IS told - each window's title (the folder's path or name, or the name
of a file or folder in it) and each program's command line (a file opened by
being passed to it). A window that names none of these cannot be recognised;
a name common enough to be elsewhere too counts as private - the safe side.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

PATHS_SETTING = "listener_privacy_paths"
MODE_SETTING = "listener_private_mode"

#: Why there is no picture, in the words the chat shows (the user, 2026-11-12: "加个注释").
NO_PICTURE_PRIVATE_MODE = "private mode is on"
NO_PICTURE_PRIVACY_FOLDER = "a file in a privacy folder is open"
NO_PICTURE_PRIVATE_PAGE = "a private web page is open"

#: THE PROTOCOL'S SIGNAL (agent-private-mode, version 1), where it is looked for.
SITE_TITLE_MARK = "[Private]"
SITE_HEADER = "Agent-Private-Mode"
SITE_META = "agent-private-mode"
#: Said by a tool refused a path in one.
REFUSED = ("that is in one of the user's privacy folders: what is in it is never read")

#: The names inside the privacy folders, looked for again at most this often; at most this
#: many, and no deeper than this.
NAMES_FOR_SECONDS = 120.0
MOST_NAMES = 20000
DEEPEST = 8
#: A name shorter than this is not looked for in titles: it would be in every one.
SHORTEST_NAME = 4
SHORTEST_FOLDER_NAME = 6
#: What is open on the screen, judged again at most this often.
OPEN_FOR_SECONDS = 3.0
#: The programs' command lines, read again at most this often (a full read is a fraction of a
#: second on a busy machine).
COMMAND_LINES_FOR_SECONDS = 15.0

_config: Any = None
_lock = threading.RLock()
_names: Dict[str, Any] = {"key": None, "at": 0.0, "names": []}
_open: Dict[str, Any] = {"key": None, "at": 0.0, "said": None}
_commands: Dict[str, Any] = {"at": 0.0, "lines": []}
_site: Dict[str, Any] = {"at": 0.0, "said": False}
#: Where the windows' titles come from for the private pages - None: this desktop's (a test gives
#: its own).
site_titles: Any = None
_data_dir: Any = None
#: THE PRIVATE PAGES' TITLES, as seen - lower case, with when; kept this long. Fewer letters than
#: SHORTEST_PAGE_TITLE is not a title to know a window by.
_pages: Dict[str, float] = {}
PAGES_FOR_SECONDS = 12 * 3600.0
SHORTEST_PAGE_TITLE = 4
#: Shared with the extension's line to the agent (platform_layer/native_host.py), in its folder.
ADDRESSES_FILE, PAGES_FILE = "private_addresses.json", "private_pages.json"


def use(config: Any, data_dir: Any = None) -> None:
    """The application's settings, for those who ask without them (the file tools); its data
    folder, where the private web addresses are told to the extension's line (`share`)."""
    global _config, _data_dir
    _config = config
    if data_dir is not None:
        _data_dir = Path(data_dir)
        share()


# -- private web addresses -----------------------------------------------------------------------
def is_address(line: Any) -> bool:
    import re
    return bool(re.match(r"(?i)^\s*https?://[^/\s]+", str(line or "")))


def _address_key(url: Any) -> Optional[Tuple[str, str]]:
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    host = host[4:] if host.startswith("www.") else host
    return host, (parts.path or "/").rstrip("/").lower()


def addresses(config: Any = None) -> List[str]:
    """The private web addresses, as the user added them."""
    config = _cfg(config)
    if config is None:
        return []
    try:
        return [str(one).strip() for one in config.lines(PATHS_SETTING) if is_address(one)]
    except Exception:
        return []


def address_in(url: Any, among: List[str]) -> str:
    """The private address `url` is at or under - "" when none. Its host the same (or one under
    an address given with no path), its path the same or below."""
    key = _address_key(url)
    if key is None:
        return ""
    host, path = key
    for one in among:
        root = _address_key(one)
        if root is None:
            continue
        same_host = host == root[0] or (not root[1] and host.endswith("." + root[0]))
        if same_host and (not root[1] or path == root[1] or path.startswith(root[1] + "/")):
            return one
    return ""


def address_is_private(url: Any, config: Any = None) -> str:
    return address_in(url, addresses(config))


def _extension_folder() -> Optional[Path]:
    if _data_dir is None:
        return None
    try:
        from ..platform_layer.browser_extension import folder
        return folder(_data_dir)
    except Exception:
        return None


def share(config: Any = None) -> None:
    """The private addresses, told to the extension's line to the agent: it keeps nothing of a
    page at one (platform_layer/browser_extension.keep_visible)."""
    where = _extension_folder()
    if where is None:
        return
    try:
        where.mkdir(parents=True, exist_ok=True)
        (where / ADDRESSES_FILE).write_text(json.dumps(addresses(config)), encoding="utf-8")
    except OSError:
        log.debug("the private addresses could not be told to the extension", exc_info=True)


def remember_private_page(title: Any) -> None:
    """A private page's title, seen: its window is known by it."""
    said = str(title or "").strip().lower()
    if len(said) >= SHORTEST_PAGE_TITLE:
        with _lock:
            _pages[said] = time.time()
            _site.update(at=0.0)


def _page_titles() -> List[str]:
    """The private pages' titles known - seen here, or by the extension's line."""
    now = time.time()
    where = _extension_folder()
    if where is not None:
        try:
            told = json.loads((where / PAGES_FILE).read_text(encoding="utf-8"))
            with _lock:
                for title, at in (told or {}).items():
                    key = str(title).strip().lower()
                    if len(key) >= SHORTEST_PAGE_TITLE and float(at) > _pages.get(key, 0.0):
                        _pages[key] = float(at)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    with _lock:
        for key in [one for one, at in _pages.items() if now - at > PAGES_FOR_SECONDS]:
            _pages.pop(key, None)
        return list(_pages)


def _cfg(config: Any) -> Any:
    return config if config is not None else _config


def _norm(path: Any) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))


def _real(path: str) -> str:
    try:
        return os.path.normcase(os.path.realpath(path))
    except (OSError, ValueError):
        return path


def roots(config: Any = None) -> List[Path]:
    """The privacy folders and files, as the user added them."""
    config = _cfg(config)
    if config is None:
        return []
    try:
        lines = config.lines(PATHS_SETTING)
    except Exception:
        return []
    out, seen = [], set()
    for line in lines:
        line = str(line).strip().strip('"')
        if not line or is_address(line):         # a private web address, not a place on disk
            continue
        path = Path(os.path.abspath(os.path.expanduser(line)))
        if _norm(path) not in seen:
            seen.add(_norm(path))
            out.append(path)
    return out


def _inside(key: str, root: str) -> bool:
    return key == root or key.startswith(root.rstrip(os.sep) + os.sep)


def covering(path: Any, config: Any = None) -> Optional[Path]:
    """The privacy folder or file `path` is (in) - the outermost one - or None."""
    if not path:
        return None
    try:
        key = _norm(path)
    except (TypeError, ValueError):
        return None
    found = None
    for root in roots(config):
        root_key = _norm(root)
        if _inside(key, root_key) or _inside(_real(key), _real(root_key)):
            if found is None or len(root_key) < len(_norm(found)):
                found = root
    return found


def is_private(path: Any, config: Any = None) -> bool:
    return covering(path, config) is not None


def chosen_private_mode(config: Any = None) -> bool:
    """The user's own switch (the avatar's menu)."""
    config = _cfg(config)
    if config is None:
        return False
    try:
        return bool(config.get(MODE_SETTING, False))
    except Exception:
        return False


def private_mode(config: Any = None) -> bool:
    """Private mode: the user's own switch (a website asking leaves its window out alone -
    `private_page_open`)."""
    return chosen_private_mode(config)


# -- a website asking (agent-private-mode) -------------------------------------------------------
def site_signal(text: Any) -> bool:
    """Whether a window's or a page's title carries the protocol's signal."""
    return str(text or "").lstrip().startswith(SITE_TITLE_MARK)


def page_signals(headers: Any = None, html_text: Any = "") -> bool:
    """Whether a page as fetched carries the signal: its header, its meta or its html attribute."""
    import re
    try:
        said = (headers.get(SITE_HEADER) if headers is not None else "") or ""
    except Exception:
        said = ""
    if str(said).strip().lower() == "on":
        return True
    head = str(html_text or "")[:200_000]
    return bool(re.search(r'(?is)<meta[^>]+name\s*=\s*["\']?agent-private-mode["\']?[^>]*content\s*=\s*'
                          r'["\']?on\b', head)
                or re.search(r'(?is)<meta[^>]+content\s*=\s*["\']?on["\']?[^>]*name\s*=\s*'
                             r'["\']?agent-private-mode\b', head)
                or re.search(r'(?is)<html[^>]+data-agent-private-mode\s*=\s*["\']?on\b', head))


def is_private_page_title(title: Any, known: Optional[List[str]] = None) -> bool:
    """Whether a window's or a page's title is a private page's: the signal, or the title of a
    page at a private address (a browser's window: that title, then the browser's name)."""
    if site_signal(title):
        return True
    said = str(title or "").strip().lower()
    if not said:
        return False
    return any(said == one or said.startswith(one + " - ") or said.startswith(one + " — ")
               or said.startswith(one + " – ") for one in (known if known is not None
                                                               else _page_titles()))


def private_page_of(data: Dict[str, Any], config: Any = None) -> bool:
    """Whether what a source brings is of a private page: its title carries the signal or is a
    private page's, or its address is a private one (its title then remembered)."""
    titles = [data.get(key) for key in ("window_title", "title", "page_title") if data.get(key)]
    for key in ("url", "address", "page_url"):
        if data.get(key) and address_is_private(data.get(key), config):
            for title in titles:
                remember_private_page(title)
            return True
    known = _page_titles()
    return any(is_private_page_title(one, known) for one in titles)


def private_page_open(titles: Optional[List[str]] = None) -> bool:
    """WHETHER A PRIVATE PAGE IS OPEN ON THE SCREEN: a window's title carrying the signal, or a
    private page's. Judged again at most every OPEN_FOR_SECONDS."""
    now = time.monotonic()
    if titles is not None:
        known = _page_titles()
        return any(is_private_page_title(one, known) for one in titles)
    with _lock:
        if now - _site["at"] < OPEN_FOR_SECONDS:
            return bool(_site["said"])
    try:
        titles = list(site_titles() if callable(site_titles)
                      else (_windows_titles() or _platform_titles()))
    except Exception:
        titles = []
    known = _page_titles()
    said = any(is_private_page_title(one, known) for one in titles)
    with _lock:
        if said != _site["said"]:
            log.info("a private web page is %s", "open" if said else "no longer open")
        _site.update(at=now, said=said)
    return said


def window_not_to_read(application: Any, title: Any, config: Any = None) -> bool:
    """A window not to be read: showing a privacy folder's file, or a private page."""
    return window_is_private(application, title, config) is not None or \
        is_private_page_title(title)


def recording(config: Any = None) -> bool:
    """Whether the user's activity is recorded at all: monitoring on, private mode off."""
    config = _cfg(config)
    if config is None:
        return True
    try:
        return bool(config.get("listener_enabled", True)) and not private_mode(config)
    except Exception:
        return True


# -- which window shows one --------------------------------------------------------------------
def _walk_names(found: List[Path]) -> List[str]:
    """The names inside the privacy folders - worth looking for in a title - lower case."""
    names: List[str] = []
    for root in found:
        if not root.is_dir():
            continue
        stack: List[Tuple[str, int]] = [(str(root), 0)]
        while stack and len(names) < MOST_NAMES:
            folder, depth = stack.pop()
            try:
                with os.scandir(folder) as entries:
                    listed = list(entries)
            except OSError:
                continue
            for entry in listed:
                name = entry.name.lower()
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                # A file by its whole name, suffix and all ("report.docx"); a folder only by a
                # name long enough not to be a word in every other title ("docs" is in
                # "Google Docs").
                if (len(name) >= SHORTEST_FOLDER_NAME if is_dir
                        else len(name) >= SHORTEST_NAME and "." in name[1:]):
                    names.append(name)
                if is_dir and depth + 1 < DEEPEST:
                    stack.append((entry.path, depth + 1))
                if len(names) >= MOST_NAMES:
                    break
    return names


def _inner_names(config: Any) -> List[str]:
    found = roots(config)
    key = tuple(_norm(one) for one in found)
    now = time.monotonic()
    with _lock:
        if _names["key"] == key and now - _names["at"] < NAMES_FOR_SECONDS:
            return _names["names"]
    names = _walk_names(found)
    with _lock:
        _names.update(key=key, at=now, names=names)
    return names


def _bounded(text: str, name: str) -> bool:
    """`name` in `text`, not as part of a longer word of letters and digits (a name in
    Chinese, which has no spaces round it, is found wherever it is)."""
    start = text.find(name)
    while start >= 0:
        before = text[start - 1] if start > 0 else " "
        end = start + len(name)
        after = text[end] if end < len(text) else " "
        if not (before.isascii() and before.isalnum()) and not (after.isascii() and after.isalnum()):
            return True
        start = text.find(name, start + 1)
    return False


def _paths_in(text: str) -> List[str]:
    lowered = text.lower()
    return [lowered, lowered.replace("/", "\\"), lowered.replace("\\", "/")]


def names_private(text: str, config: Any = None) -> Optional[Path]:
    """The privacy folder whose path or name - or the name of something in it - `text` (a
    window's title, a command line) holds, or None."""
    text = str(text or "")
    if not text.strip():
        return None
    found = roots(config)
    if not found:
        return None
    forms = _paths_in(text)
    for root in found:
        whole = str(root).lower()
        if any(whole in one or whole.replace("\\", "/") in one for one in forms):
            return root
    lowered = text.lower()
    for root in found:
        name = root.name.lower()
        if len(name) >= 2 and _bounded(lowered, name):
            return root
    inner = _inner_names(config)
    if inner:
        for name in inner:
            if name in lowered and _bounded(lowered, name):
                # Which of the folders holds it does not matter to what is said: the
                # first one is named - its own name, never the one inside it.
                return next((one for one in found if one.is_dir()), found[0])
    return None


def window_is_private(application: Any, title: Any, config: Any = None) -> Optional[Path]:
    """The privacy folder a window shows something of, judged by its title - or None."""
    return names_private(f"{title or ''}", config)


def _command_lines() -> List[str]:
    now = time.monotonic()
    with _lock:
        if now - _commands["at"] < COMMAND_LINES_FOR_SECONDS:
            return list(_commands["lines"])
    lines: List[str] = []
    try:
        import psutil
        own = os.getpid()
        for process in psutil.process_iter(["pid", "cmdline"]):
            try:
                if process.info.get("pid") == own:
                    continue
                parts = process.info.get("cmdline") or []
                if len(parts) > 1:
                    lines.append("\0".join(str(one) for one in parts[1:]))
            except Exception:
                continue
    except Exception:
        log.debug("the programs' command lines could not be read", exc_info=True)
    with _lock:
        _commands.update(at=now, lines=lines)
    return lines


def _windows_titles() -> List[str]:
    """The title of every window open on this desktop - minimised ones too (Windows)."""
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        titles: List[str] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        own = os.getpid()

        def each(hwnd, _param):
            try:
                if user32.IsWindowVisible(hwnd):
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length and pid.value != own:
                        buffer = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buffer, length + 1)
                        if buffer.value:
                            titles.append(buffer.value)
            except Exception:
                pass
            return True

        user32.EnumWindows(callback_type(each), 0)
        return titles
    except Exception:
        log.debug("the windows' titles could not be read", exc_info=True)
        return []


def _platform_titles() -> List[str]:
    """Elsewhere, what the platform can say: the windows on the screen, else the front one."""
    try:
        from ..platform_layer import get_platform
        activity = get_platform().activity
    except Exception:
        return []
    titles: List[str] = []
    try:
        listed = getattr(activity, "screen_windows", None)
        if callable(listed):
            titles = [str(one.get("title") or "") for one in listed() or [] if not one.get("ours")]
    except Exception:
        titles = []
    if not titles:
        try:
            front = activity.get_active_window()
            if front is not None and front.title and front.pid != os.getpid():
                titles = [str(front.title)]
        except Exception:
            pass
    return titles


def private_file_open(config: Any = None, titles: Optional[List[str]] = None) -> Optional[Path]:
    """THE PRIVACY FOLDER ONE OF WHOSE FILES IS OPEN NOW - in any window's title, or passed to
    a program on its command line - or None. Judged again at most every OPEN_FOR_SECONDS.
    `titles`: the windows' titles, where the caller has them."""
    found = roots(config)
    if not found:
        return None
    key = tuple(_norm(one) for one in found)
    now = time.monotonic()
    if titles is None:
        with _lock:
            if _open["key"] == key and now - _open["at"] < OPEN_FOR_SECONDS:
                return _open["said"]
    said: Optional[Path] = None
    for title in (titles if titles is not None
                  else (_windows_titles() or _platform_titles())):
        said = window_is_private("", title, config)
        if said is not None:
            break
    if said is None:
        for line in _command_lines():
            for part in line.split("\0"):
                part = part.strip().strip('"')
                if part and (os.sep in part or "/" in part):
                    said = covering(part, config)
                    if said is not None:
                        break
            if said is not None:
                break
    if titles is None:
        with _lock:
            _open.update(key=key, at=now, said=said)
    return said


def why_no_picture(config: Any = None) -> str:
    """Why no picture of the screen may be taken now - "" when one may."""
    if chosen_private_mode(config):
        return NO_PICTURE_PRIVATE_MODE
    if private_file_open(config) is not None:
        return NO_PICTURE_PRIVACY_FOLDER
    if private_page_open():
        return NO_PICTURE_PRIVATE_PAGE
    return ""


def withheld(why: str) -> bool:
    """Whether `why` (a FrontPictures.why) is one of these two."""
    return why in (NO_PICTURE_PRIVATE_MODE, NO_PICTURE_PRIVACY_FOLDER, NO_PICTURE_PRIVATE_PAGE)


def forget() -> None:
    """What was found, forgotten - the folders changed (and for a test)."""
    with _lock:
        _names.update(key=None, at=0.0, names=[])
        _open.update(key=None, at=0.0, said=None)
        _commands.update(at=0.0, lines=[])
        _site.update(at=0.0, said=False)
        _pages.clear()


__all__ = ["PATHS_SETTING", "MODE_SETTING", "REFUSED", "NO_PICTURE_PRIVATE_MODE",
           "NO_PICTURE_PRIVACY_FOLDER", "NO_PICTURE_PRIVATE_PAGE", "SITE_TITLE_MARK",
           "SITE_HEADER", "SITE_META", "use", "roots", "covering", "is_private",
           "chosen_private_mode", "site_signal", "page_signals", "is_address", "addresses",
           "address_in", "address_is_private", "share", "remember_private_page",
           "is_private_page_title", "private_page_of", "private_page_open", "window_not_to_read",
           "private_mode", "recording", "names_private", "window_is_private",
           "private_file_open", "why_no_picture", "withheld", "forget"]

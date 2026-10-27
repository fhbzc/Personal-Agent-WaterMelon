"""Filesystem activity provider (section 19).

Creation, modification, deletion, rename, move, directory creation and
directory deletion.  A permission failure on one watched directory must not
terminate the whole Listener, so each root is watched independently and
failures are recorded per root.
"""

from __future__ import annotations

import logging
import os
import re
import collections
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...common.events.models import ListenerEvent
from ..core.provider import ListenerProvider, PluginManifest
from ..core.manager import STOPPING_GRACE_SECONDS, ListenerManager

log = logging.getLogger(__name__)
# pypdf says, at WARNING, every small fault it works round in a PDF it reads
# - hundreds of lines for one malformed file, none of them anything to act
# on. Its errors still reach the log.
logging.getLogger("pypdf").setLevel(logging.ERROR)

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG = True
except Exception:  # pragma: no cover - optional dependency
    FileSystemEventHandler = object  # type: ignore
    Observer = None  # type: ignore
    _WATCHDOG = False

#: What the operating system tells us, and what we call it.
#:
#: Windows reports a directory change for eight different reasons - the
#: content was written, the file was *read*, an attribute changed, the
#: security descriptor changed - and watchdog collapses all of them into one
#: "modified". Recorded as written, a file the user only opened is a file
#: they are told they edited.
#:
#: Which it was is not in the notification, but it is in the file: content
#: that changed has a new modification time, and content that did not, does
#: not. So the two are told apart by remembering what the file looked like
#: last time and asking.
EVENT_MAP = {
    ("created", False): "filesystem.created",
    ("created", True): "filesystem.directory_created",
    ("modified", False): "filesystem.modified",
    # A directory reports itself modified whenever anything inside it
    # changes, so every file event arrives twice: once as the file and once
    # as each directory above it. Nothing reads it - the Overview skips it,
    # the templater discards it - and it was a fifth of what was stored.
    # Not mapped at all, so it is never emitted rather than emitted and
    # dropped by everyone downstream.
    ("deleted", False): "filesystem.deleted",
    ("deleted", True): "filesystem.directory_deleted",
    ("moved", False): "filesystem.moved",
    ("moved", True): "filesystem.directory_moved",
}


class _Handler(FileSystemEventHandler):  # type: ignore[misc]
    def __init__(self, provider: "FilesystemProvider") -> None:
        self.provider = provider

    def on_any_event(self, event) -> None:  # pragma: no cover - driven by the OS
        try:
            self.provider.handle_raw(event)
        except Exception:
            log.debug("filesystem event handling failed", exc_info=True)


def _visible_text(markup: str) -> str:
    """What a person would read on a web page, without its markup.

    The tags, the scripts and the styles were kept as they were, so the
    20,000 characters taken from an .html file were spent mostly on
    angle brackets and JavaScript. The standard library's own HTML parser
    walks it: text inside <script>, <style>, <svg>, <noscript> and <head>
    (apart from the title) is skipped, and block elements start new lines
    so paragraphs stay paragraphs.
    """
    from html.parser import HTMLParser

    skipped = {"script", "style", "svg", "noscript", "template", "head"}
    blocks = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5",
              "h6", "section", "article", "header", "footer", "pre", "td",
              "blockquote", "title"}

    class Reader(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.out: List[str] = []
            self.hidden = 0
            self.in_title = False

        def handle_starttag(self, tag, attrs):
            if tag == "title":
                self.in_title = True
            elif tag in skipped:
                self.hidden += 1
            if tag in blocks:
                self.out.append("\n")

        def handle_endtag(self, tag):
            if tag == "title":
                self.in_title = False
            elif tag in skipped and self.hidden:
                self.hidden -= 1
            if tag in blocks:
                self.out.append("\n")

        def handle_data(self, data):
            if self.in_title or not self.hidden:
                self.out.append(data)

    reader = Reader()
    try:
        reader.feed(markup)
        reader.close()
    except Exception:                    # malformed beyond parsing: keep what came
        pass
    lines = [" ".join(line.split()) for line in "".join(reader.out).split("\n")]
    return "\n".join(line for line in lines if line)


class _NoReader(Exception):
    """The library a kind of document needs is not installed."""


def _document_text(path: str, kind: str, most: int) -> str:
    """The text inside a PDF, a Word document or a PowerPoint deck."""
    if kind == ".pdf":
        return _pdf_text(path, most)
    if kind == ".docx":
        return _office_text(path, ("word/document.xml",), "w", most)
    if kind == ".pptx":
        return _office_text(path, None, "a", most)
    return ""


def _pdf_text(path: str, most: int) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise _NoReader("reading a PDF's text needs pypdf, which is not "
                        "installed") from None
    reader = PdfReader(path)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            raise _NoReader("the PDF is protected by a password") from None
    said: List[str] = []
    length = 0
    # Page by page, and no further than needed: a three-hundred-page
    # report's first pages are what is kept, so the rest is not parsed.
    for page in reader.pages:
        text = page.extract_text() or ""
        said.append(text)
        length += len(text)
        if length >= most:
            break
    return "\n".join(said)


_OFFICE_TEXT = {"w": re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S),
                "a": re.compile(r"<a:t(?:\s[^>]*)?>(.*?)</a:t>", re.S)}
_OFFICE_BREAK = {"w": "</w:p>", "a": "</a:p>"}


def _office_text(path: str, parts, tag: str, most: int) -> str:
    """Text from the XML inside a Word or PowerPoint file.

    Both are zip archives of XML, with every run of visible text in a
    <w:t> or <a:t> element; paragraphs end at </w:p> or </a:p>. Read this
    way it needs no library at all, so it cannot be missing.
    """
    import zipfile
    from html import unescape

    with zipfile.ZipFile(path) as archive:
        if parts is None:
            # Slides in their order - slide10 after slide9, not slide1.
            parts = sorted(
                (name for name in archive.namelist()
                 if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
                key=lambda name: int(re.search(r"(\d+)", name).group(1)))
        said: List[str] = []
        length = 0
        for index, part in enumerate(parts, start=1):
            xml = archive.read(part).decode("utf-8", errors="replace")
            paragraphs = []
            for block in xml.split(_OFFICE_BREAK[tag]):
                text = "".join(_OFFICE_TEXT[tag].findall(block))
                if text.strip():
                    paragraphs.append(unescape(text))
            if tag == "a" and paragraphs:
                paragraphs.insert(0, f"- slide {index} -")
            chunk = "\n".join(paragraphs)
            said.append(chunk)
            length += len(chunk)
            if length >= most:
                break
    return "\n\n".join(part for part in said if part)


class FilesystemProvider(ListenerProvider):
    provider_id = "filesystem"
    category = "filesystem"

    def __init__(self, config: Any, platform: Any = None) -> None:
        super().__init__()
        self.config = config
        #: Asked what was in front when a file changed. Being at the keyboard
        #: says *somebody* was working; it does not say that this particular
        #: write was theirs, because a background program writes while they
        #: work. What was on screen is the evidence for which it was, and it
        #: is recorded rather than judged - the reader can see "edited while
        #: you were in the editor" and "written while you were reading mail"
        #: and tell them apart, which no rule here could do for them.
        self.platform = platform
        #: What was in front a moment ago, and when we last asked. A burst of
        #: ten thousand events must not be ten thousand calls into Win32.
        self._front = ("", "", 0.0)
        self._observer = None
        self._roots: Dict[str, str] = {}
        self._poll_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._snapshot: Dict[str, float] = {}
        #: path -> (modification time, size), for the one question asked of
        #: it: has the content changed since we last heard about this file.
        #: Bounded, because a machine has more files than this is worth
        #: remembering about, and the oldest half goes when it is full.
        self._seen: "collections.OrderedDict[str, tuple]" = collections.OrderedDict()
        self._events = 0
        self._opened = 0
        self._swept = 0
        self._sweeps = 0
        self._from_the_desktop = 0
        self._desktop_thread: Optional[threading.Thread] = None
        #: When each path was last reported opened, from either source, so
        #: that one opening is one event however many ways we hear about it.
        self._said_opened: "collections.OrderedDict[str, float]" = \
            collections.OrderedDict()
        #: Events waiting to be seen in company, and the thread that flushes
        #: them. See _hold_briefly.
        self._holding: List[tuple] = []
        self._holding_lock = threading.Lock()
        self._flusher: Optional[threading.Thread] = None

    # -- lifecycle ----------------------------------------------------------
    def watch_paths(self) -> List[Path]:
        paths = []
        for raw in self.config.lines("listener_watch_paths"):
            path = Path(raw).expanduser()
            if path.exists() and path.is_dir():
                paths.append(path)
            else:
                self._roots[str(path)] = "missing"
        return paths

    def settings_changed(self) -> None:
        """Watch what the settings now say to watch.

        The roots are read once, when the provider starts. Edited while it is
        running, the setting therefore did nothing at all until the next
        restart - and said nothing about that, so the honest reading of the
        panel was that the change had been made and had no effect.

        Restarted only when the list actually differs: every listener setting
        arrives here, and rebuilding the watch tree because an unrelated
        switch moved would drop whatever was in flight for no reason.
        """
        wanted = {str(path) for path in self.watch_paths()}
        watching = {root for root, state in self._roots.items() if state != "missing"}
        if wanted == watching:
            return
        log.info("watched directories changed: %s", sorted(wanted))
        try:
            self.stop()
        except Exception:                # pragma: no cover - defensive
            log.debug("could not stop the watcher cleanly", exc_info=True)
        self.start()

    def _start_watching_the_desktop(self) -> None:
        if self._desktop_thread and self._desktop_thread.is_alive():
            return
        self._desktop_thread = threading.Thread(
            target=self._watch_what_they_opened,
            name="listener-what-they-opened", daemon=True)
        self._desktop_thread.start()

    def start(self) -> None:
        roots = self.watch_paths()
        if not roots:
            raise RuntimeError("no existing directory is configured for filesystem monitoring")
        self._stop.clear()
        if _WATCHDOG:
            self._observer = Observer()
            handler = _Handler(self)
            for root in roots:
                try:
                    self._observer.schedule(handler, str(root), recursive=True)
                    self._roots[str(root)] = "watching"
                except (OSError, PermissionError) as exc:
                    # Section 19: a permission failure disables one root only.
                    self._roots[str(root)] = f"error: {exc}"
                    self.record_error(f"{root}: {exc}")
            self._observer.start()
        else:
            for root in roots:
                self._roots[str(root)] = "polling"
            self._poll_thread = threading.Thread(target=self._poll_loop, args=(roots,),
                                                 name="listener-filesystem", daemon=True)
            self._poll_thread.start()
        # Whichever backend is watching, the desktop's own record is read
        # beside it: it is the only prompt account of a file being opened.
        self._start_watching_the_desktop()

    #: How often the desktop's own record of what the user opened is read.
    #:
    #: It is a directory scan of a few milliseconds once the window is
    #: narrowed by time, and it is the only source here that is prompt.
    WHAT_THEY_OPENED_EVERY = 10.0

    #: How far back that record is asked for on each pass. Wider than the
    #: interval so that nothing falls between two passes.
    WHAT_THEY_OPENED_SINCE = 900.0

    #: The first look at what was opened waits this long after a start, so
    #: as not to take the interpreter from the desktop window as it comes
    #: up; WHAT_THEY_OPENED_SINCE reaches back past it.
    QUIET_AT_START = 60.0

    #: One opening of one file is one event, however many ways we hear about
    #: it. The desktop stamps its shortcut when the application asks it to
    #: and the filesystem reports the access time when Windows flushes, so
    #: the two timestamps for a single opening are close but not equal -
    #: measured on one machine, 17:10:35 and 17:11:17 for the same file.
    SAME_OPENING_SECONDS = 300.0

    def _watch_what_they_opened(self) -> None:
        """Report what the desktop says the user opened, without waiting.

        The filesystem's own account of a read arrives when Windows flushes
        the access time, which is up to an hour later (section 19, as
        amended). The desktop's account is immediate: Explorer writes its
        shortcut as the file opens. Measured on one machine - a file opened
        at 17:10:35 was in the Recent folder at 17:10:35 and had still not
        been reported by the filesystem three minutes later.

        So the same record that is used as *evidence* about who opened a
        file is also read as a *source*. What it covers, it covers at once
        and with no inference at all: a program calling open() never appears
        in it.
        """
        # QUIET AT START (measured 2026-10-26): not while the application
        # comes up - a minute later, the record asked fifteen minutes back.
        if self._stop.wait(self.QUIET_AT_START):
            return
        while not self._stop.is_set():
            try:
                self._ask_the_desktop()
            except Exception:                # pragma: no cover - defensive
                log.debug("could not read what the user opened", exc_info=True)
            self._stop.wait(self.WHAT_THEY_OPENED_EVERY)

    def _ask_the_desktop(self) -> None:
        activity = getattr(self.platform, "activity", None)
        ask = getattr(activity, "files_the_user_opened", None)
        if ask is None:
            return
        opened = ask(time.time() - self.WHAT_THEY_OPENED_SINCE) or {}
        for identifier, at in opened.items():
            # The record holds a full path where it can and a bare filename
            # where it cannot. A name on its own is enough to recognise a
            # file somebody else reported, and not enough to report one.
            if not os.path.isabs(str(identifier)):
                continue
            path = str(identifier)
            try:
                if not os.path.isfile(path):
                    continue
            except OSError:                  # pragma: no cover - defensive
                continue
            if self._already_said_opened(path, float(at)):
                continue
            self._from_the_desktop += 1
            data = {"path": path, "said_by": "the desktop"}
            # READ, LIKE AN OPENING THE WATCHER SAW. This path passed the
            # path along and nothing else - and a file opened by double-
            # clicking it often arrives only this way, so it was the one
            # most likely to be something the user actually read.
            self._excerpt("filesystem.opened", data)
            self._emit_event("filesystem.opened", data, float(at))

    def _already_said_opened(self, path: str, at: float) -> bool:
        """Whether this opening has been reported already, by either source."""
        key = os.path.normcase(os.path.abspath(path))
        last = self._said_opened.get(key)
        if last is not None and abs(at - last) <= self.SAME_OPENING_SECONDS:
            return True
        self._said_opened[key] = at
        self._said_opened.move_to_end(key)
        while len(self._said_opened) > self.REMEMBERED:
            self._said_opened.popitem(last=False)
        return False

    def stop(self) -> None:
        self._stop.set()
        if self._desktop_thread:
            self._desktop_thread.join(timeout=STOPPING_GRACE_SECONDS)
            self._desktop_thread = None
        self.flush_held_events()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=STOPPING_GRACE_SECONDS)
            except Exception:
                log.debug("observer shutdown failed", exc_info=True)
            self._observer = None
        if self._poll_thread:
            self._poll_thread.join(timeout=STOPPING_GRACE_SECONDS)
            self._poll_thread = None

    # -- watchdog path ------------------------------------------------------
    def handle_raw(self, raw_event) -> None:
        kind = getattr(raw_event, "event_type", "modified")
        is_dir = bool(getattr(raw_event, "is_directory", False))
        event_type = EVENT_MAP.get((kind, is_dir))
        if event_type is None:
            return
        data: Dict[str, Any] = {}
        happened_at: Optional[float] = None
        if kind == "moved":
            data["source_path"] = str(getattr(raw_event, "src_path", ""))
            data["destination_path"] = str(getattr(raw_event, "dest_path", ""))
            data["path"] = data["destination_path"]
            source_name = os.path.basename(data["source_path"])
            dest_name = os.path.basename(data["destination_path"])
            if os.path.dirname(data["source_path"]) == os.path.dirname(data["destination_path"]) \
                    and source_name != dest_name:
                event_type = "filesystem.renamed"
        else:
            data["path"] = str(getattr(raw_event, "src_path", ""))
            if event_type == "filesystem.modified":
                event_type, happened_at = self._read_or_written(data["path"], data)
                if event_type is None:
                    return
        # What is in it, not only that it moved. A path and a timestamp
        # say somebody touched `fetch.py`; they do not say whether they
        # were fixing the rate limiting or reading it to remember what it
        # did, and that difference is the whole of what a state is for.
        self._excerpt(event_type, data)
        if event_type == "filesystem.opened" and self._already_said_opened(
                data.get("path") or "", happened_at or time.time()):
            # The desktop already reported this opening, promptly, while this
            # notification waited for Windows to flush the access time.
            return
        self._emit_event(event_type, data, happened_at)

    #: The most of a file that is ever kept, in characters.
    #:
    #: Twenty thousand is about eight pages - enough that a source file is
    #: whole and a paper is recognisable, and small enough that a day of
    #: editing does not become a second copy of the user's disk. The
    #: setting can lower it or turn it off; it cannot raise it past this,
    #: because an unbounded excerpt is not an excerpt.
    MOST_OF_A_FILE = 20000
    #: Documents whose text is taken out of them rather than read as text:
    #: a PDF through pypdf, Word and PowerPoint straight from the XML inside
    #: them, which needs nothing but the standard library.
    DOCUMENTS = (".pdf", ".docx", ".pptx")
    #: The largest document opened to find its text in. Its size is mostly
    #: pictures and fonts, so the text-file ceiling would refuse ordinary
    #: slide decks.
    LARGEST_DOCUMENT = 60_000_000

    #: What is worth keeping the text of.
    #:
    #: Suffixes rather than sniffing: reading the first bytes of every
    #: file the operating system mentions, to find out it was a .dll, is
    #: the cost this list exists to avoid. Anything not named here is
    #: recorded exactly as before - a path, a name and a time.
    TEXT_LIKE = (
        ".txt", ".md", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl",
        ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".html",
        ".htm", ".css", ".js", ".ts", ".tsx", ".jsx", ".py", ".pyi", ".r",
        ".do", ".sql", ".sh", ".ps1", ".bat", ".c", ".h", ".cpp", ".hpp",
        ".java", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".m",
        ".tex", ".bib", ".ipynb", ".gitignore", ".env.example",
    )

    #: Names whose CONTENTS are never kept, whatever their suffix.
    #:
    #: A file called `.env` is a text file by every test this code can
    #: apply and is a list of the user's secrets. The event is still
    #: recorded - that the file was opened is a fact about the afternoon -
    #: with nothing from inside it.
    NEVER_INSIDE = ("id_rsa", "id_ed25519", ".env", ".netrc", ".pem",
                    ".key", ".pfx", ".p12", ".kdbx", "credentials",
                    "secrets", "password", "token", "apikey", "api_key")

    def _excerpt(self, event_type: str, data: Dict[str, Any]) -> None:
        """Put the beginning of the file on the event, when that is proper.

        Bounded three ways, because this is the one provider that can turn
        a record of activity into a copy of the disk: by how much of any
        one file is taken, by which files are looked inside at all, and by
        a setting that can switch it off entirely. What it is NOT bounded
        by is what the file says - this reads the user's own documents,
        which is the point, and the judgement about which of those matter
        belongs to the review that reads the events and not here.
        """
        if event_type not in ("filesystem.modified", "filesystem.opened"):
            return
        # EVERY SKIP SAYS WHY, in `content_withheld`. Most of these used to
        # return quietly, so a file with no text in its event could not be
        # told apart from one that was never looked at - and "why is so
        # little kept?" had to be answered by reconstructing it by hand.
        most = self._how_much_of_a_file()
        if most <= 0:
            data["content_withheld"] = "keeping file contents is switched off"
            return
        path = str(data.get("path") or "")
        if not path:
            return
        name = os.path.basename(path).lower()
        # The name check comes FIRST, before the suffix one. `secrets.json`
        # and `credentials.yaml` are text files by every test this code can
        # apply, so a suffix check running first would read them; and `.env`
        # is not in the suffix list at all, so putting this second meant the
        # commonest secret file was skipped for the wrong reason and the
        # event said nothing about why.
        if any(word in name for word in self.NEVER_INSIDE):
            data["content_withheld"] = "the name says it holds credentials"
            return
        document = next((kind for kind in self.DOCUMENTS
                         if name.endswith(kind)), "")
        if not document and not name.endswith(self.TEXT_LIKE):
            suffix = os.path.splitext(name)[1] or "no suffix"
            data["content_withheld"] = (f"{suffix} is not a kind of file "
                                        f"whose text is read")
            return
        # NOT OPENED WHEN THE MANAGER WILL THROW IT AWAY. The ignored folders
        # and file names - node_modules, .git, __pycache__, a virtual
        # environment's site-packages, a hive log - are dropped when the
        # event reaches the manager, a second after it was built here; and
        # building it opened the file and read up to twenty thousand
        # characters of it, on the thread that receives every notification.
        # A dependency install is thousands of .js and .py files, every one
        # read to be discarded. The same two questions are asked here, of
        # the manager's own filter, before anything is opened:
        # a path they refuse is refused whatever its event carries, so
        # leaving its content out changes nothing that is kept.
        if self._settings_refuse(path):
            return
        try:
            info = os.stat(path)
        except OSError:
            data["content_withheld"] = "the file was gone by the time it was read"
            return
        # A file far larger than the cap is still excerpted - the first
        # pages of a big log are worth having - but one that is enormous
        # is not opened at all, because the read itself is the cost. A
        # document is measured differently: its size is mostly images and
        # fonts, and the text is a small part of it.
        ceiling = (self.LARGEST_DOCUMENT if document
                   else max(most * 8, 2_000_000))
        if info.st_size > ceiling:
            data["content_withheld"] = f"{info.st_size} bytes, too big to read"
            return
        try:
            if document:
                said = _document_text(path, document, most + 1)
            else:
                with open(path, "r", encoding="utf-8", errors="replace") as handle:
                    # A web page is read whole - its words can start after
                    # a long head of scripts and styles - and then reduced
                    # to what a person would see on it.
                    if name.endswith((".html", ".htm")):
                        said = _visible_text(handle.read(most * 8))[:most + 1]
                    else:
                        said = handle.read(most + 1)
        except _NoReader as exc:
            data["content_withheld"] = str(exc)
            return
        except Exception as exc:         # a damaged or locked file, any kind
            data["content_withheld"] = f"could not be read: {exc}"[:200]
            return
        if not said.strip():
            data["content_withheld"] = (
                "no text in it - a scanned PDF is pictures of pages"
                if document == ".pdf" else "nothing in it but whitespace")
            return
        data["content_cut"] = len(said) > most
        data["content"] = said[:most]
        data["content_chars"] = len(data["content"])
        if document:
            data["content_from"] = document.lstrip(".")
        data["content_of"] = ("what was written" if
                              event_type == "filesystem.modified"
                              else "what was read")

    def _settings_refuse(self, path: str) -> bool:
        """Whether the manager's filter will drop an event about this path.

        Only the two questions that depend on nothing but the path and the
        settings: its folders, and its name. Whether someone was at the
        keyboard, whose doing it was, and what the agent wrote a moment ago
        all change from one second to the next and are left to the manager,
        which asks them when the event arrives.

        Asked of the manager's own filter, and only when the events go to the
        manager: whoever else is listening has not said it drops anything,
        so they get every event as it always was.
        """
        if not path:
            return False
        receiver = getattr(self._emit, "__self__", None)
        if not isinstance(receiver, ListenerManager):
            return False
        if (getattr(self._emit, "__func__", None) is not ListenerManager._on_event
                and not receiver.receives_from(self, self._emit)):
            return False
        try:
            privacy = receiver.privacy
            return (not privacy.path_allowed(path)
                    or not privacy.file_name_allowed(path))
        except Exception:                # a filter that cannot answer: read it
            log.debug("could not ask the path filter", exc_info=True)
            return False

    def _how_much_of_a_file(self) -> int:
        """The cap, from the settings, never above the one in the code."""
        try:
            asked = int(self.config.get("listener_file_excerpt_chars",
                                        self.MOST_OF_A_FILE))
        except (TypeError, ValueError):
            return self.MOST_OF_A_FILE
        return max(0, min(asked, self.MOST_OF_A_FILE))

    #: How many files we remember the shape of. Enough for the directories a
    #: person actually works in; a sweep of something enormous evicts the
    #: oldest rather than growing without end.
    REMEMBERED = 20000

    def _read_or_written(self, path: str, data: Dict[str, Any]) -> tuple:
        """Which of the two this notification is about.

        A file whose modification time has not moved since we last heard
        about it was not written: it was read, or an attribute changed. On
        this machine that is most of them - of 399 recent notifications only
        4 were about a file written in the seconds before, and the rest were
        the memory library being *read* by the indexer, reported an hour
        later when Windows got round to flushing the access time.

        Recorded as writes they said a file had been edited when nobody had
        touched it. They are worth keeping - what the user opened is a thing
        that happened - but not under that name.

        Returns when it happened as well as what it was, because for a read
        those are not the same moment. See _when_it_was_read.
        """
        try:
            info = os.stat(path)
            shape = (info.st_mtime_ns, info.st_size)
        except OSError:
            # Gone already: nothing to compare, and nothing to date it by.
            return "filesystem.modified", None
        # NEITHER WRITTEN NOR READ: a notification about a file whose
        # modification time has not moved and whose access time is old is
        # about something else - its permissions or attributes. Granting
        # the agents' code read access across the home changed every file's
        # permissions, and each change came here as a notification and was
        # recorded as the user opening that file - hundreds of them, each
        # PDF's text read out as it went. Nothing happened to it that the
        # user did: not recorded at all.
        if time.time() - info.st_mtime > self.WRITTEN_WITHIN_SECONDS \
                and self._when_it_was_read(info) is None:
            return None, None
        known = self._seen.get(path)
        self._seen[path] = shape
        self._seen.move_to_end(path)
        if len(self._seen) > self.REMEMBERED:
            for _ in range(self.REMEMBERED // 2):
                self._seen.popitem(last=False)
        if known is None:
            # First time we have heard of it. It may have been written a
            # moment ago or a year ago, and the modification time says which.
            if time.time() - info.st_mtime <= self.WRITTEN_WITHIN_SECONDS:
                return "filesystem.modified", None
            self._opened += 1
            return "filesystem.opened", self._when_it_was_read(info)
        if known == shape:
            self._opened += 1
            return "filesystem.opened", self._when_it_was_read(info)
        return "filesystem.modified", None

    #: How far back a file's own account of when it was read is believed.
    #:
    #: Long enough to cover the delay - an hour is the usual flush - with room
    #: to spare. Beyond it the access time is describing something else
    #: entirely, and a record dated to yesterday would land outside every
    #: window that would ever read it.
    BACKDATED_AT_MOST = 6 * 3600.0

    def _when_it_was_read(self, info: Any) -> Optional[float]:
        """When the file was actually opened, as opposed to when we were told.

        Windows keeps the last access time in memory and writes it to disk
        when it gets round to it, and the notification comes with the write,
        not with the read. Measured on this machine: three files read at
        22:12:07 produced no notification at all for the next thirty-five
        seconds, while a single second - 03:02:35 - carried 47 files that had
        been read across the preceding hour.

        Dated by arrival, all of them are "read at 03:02:35", which is both
        wrong and unusable: a person cannot have opened forty-seven files in
        one second, and the record says nothing about the hour in which they
        actually opened them one at a time. The file itself knows, so it is
        asked.
        """
        try:
            read_at = float(info.st_atime)
        except (TypeError, ValueError, AttributeError):   # pragma: no cover
            return None
        now = time.time()
        if read_at > now or now - read_at > self.BACKDATED_AT_MOST:
            return None
        return read_at

    #: How recently a file must have been written for a first sighting to be
    #: read as a write. Longer than any plausible gap between the write and
    #: the notification - it arrives in under a second - and far shorter than
    #: the hour Windows takes to report an access.
    WRITTEN_WITHIN_SECONDS = 60.0

    # -- polling fallback ---------------------------------------------------
    def _poll_loop(self, roots: List[Path]) -> None:  # pragma: no cover - fallback path
        while not self._stop.is_set():
            try:
                current: Dict[str, float] = {}
                for root in roots:
                    for path in root.rglob("*"):
                        try:
                            current[str(path)] = path.stat().st_mtime
                        except OSError:
                            continue
                if self._snapshot:
                    for path, mtime in current.items():
                        previous = self._snapshot.get(path)
                        if previous is None:
                            self._emit_event("filesystem.created", {"path": path})
                        elif previous != mtime:
                            self._emit_event("filesystem.modified", {"path": path})
                    # The poller has no equivalent problem: it compares
                    # modification times itself, so a file it did not see
                    # change is never reported at all.
                    for path in self._snapshot.keys() - current.keys():
                        self._emit_event("filesystem.deleted", {"path": path})
                self._snapshot = current
            except Exception as exc:
                self.record_error(str(exc)[:200])
            self._stop.wait(5.0)

    # -- emission -----------------------------------------------------------
    #: How long the answer to "what is in front" is reused. Shorter than any
    #: plausible switch between windows, long enough that a burst of file
    #: events costs one question rather than thousands.
    FRONT_CACHE_SECONDS = 1.0

    def _in_front(self):
        """The application and window in front, asked at most once a second."""
        if self.platform is None:
            return "", ""
        application, title, asked = self._front
        now = time.time()
        if now - asked <= self.FRONT_CACHE_SECONDS:
            return application, title
        try:
            window = self.platform.activity.get_active_window()
            application = getattr(window, "application", "") or ""
            title = getattr(window, "title", "") or ""
        except Exception:                # pragma: no cover - defensive
            application, title = "", ""
        self._front = (application, title, now)
        return application, title

    #: How long an event waits to be seen in company.
    #:
    #: Long enough to gather a sweep, short enough that it does not matter:
    #: nothing here is read in real time, and a second's delay on a record of
    #: what happened today is not a delay anybody can perceive.
    BURST_WINDOW_SECONDS = 1.0

    #: How many file events in that window a person could plausibly cause.
    #:
    #: Opening a file touches it and perhaps a lock or a backup beside it;
    #: saving touches two or three. A program walking a tree touches
    #: hundreds. Measured over two hours on one machine: of 16,721 file
    #: events, **40 arrived alone** and 4,874 arrived in seconds holding more
    #: than a hundred - the busiest second held 1,076, which was a commit
    #: reading every file in a repository. Counting that as a thousand acts
    #: of reading is not a small error; it is the whole record.
    BURST_IS_A_PROGRAM = 12

    #: How fresh an event has to be for the window in front to be evidence
    #: about it. A read reported an hour after it happened says nothing about
    #: what is on screen now, and labelling it with the current window would
    #: file forty-seven files the user opened across the morning under
    #: whatever they happened to be looking at when Windows flushed.
    STILL_THE_SAME_MOMENT = 30.0

    def _emit_event(self, event_type: str, data: Dict[str, Any],
                    happened_at: Optional[float] = None) -> None:
        self._events += 1
        if happened_at is None or time.time() - happened_at <= self.STILL_THE_SAME_MOMENT:
            application, title = self._in_front()
            if application:
                data = {**data, "while_using": application, "while_showing": title}
        self._hold_briefly(event_type, data, happened_at)

    def _hold_briefly(self, event_type: str, data: Dict[str, Any],
                      happened_at: Optional[float] = None) -> None:
        """Wait a moment to see whether this event arrived alone.

        A sweep cannot be recognised from one event - only from how many
        came with it - so the decision cannot be made at the moment of
        arrival. They are held for a second and judged together.
        """
        with self._holding_lock:
            self._holding.append((event_type, data, happened_at or time.time()))
            if self._flusher is None or not self._flusher.is_alive():
                self._flusher = threading.Thread(
                    target=self._flush_after_the_window,
                    name="listener-filesystem-burst", daemon=True)
                self._flusher.start()

    def _flush_after_the_window(self) -> None:
        self._stop.wait(self.BURST_WINDOW_SECONDS)
        self.flush_held_events()

    def flush_held_events(self) -> None:
        """Judge what is being held now, rather than when the window ends.

        Called when the window ends, and when the provider stops: events
        still waiting at shutdown would otherwise be the last second of the
        user's day, dropped every time the listener is closed.
        """
        with self._holding_lock:
            batch, self._holding = self._holding, []
            # Whoever flushes ends the window; the next event starts another.
            self._flusher = None
        if not batch:
            return
        # Grouped by when the files were touched, not by when we were told.
        # Writes are reported as they happen and the two are the same; reads
        # are reported in one lump an hour later, and judging that lump as a
        # whole would throw away every read there is - the lump is Windows
        # flushing, not a person doing anything.
        batch.sort(key=lambda row: row[2])
        together: List[tuple] = []
        for row in batch:
            if together and row[2] - together[-1][2] > self.TOGETHER_WITHIN_SECONDS:
                self._judge(together)
                together = []
            together.append(row)
        self._judge(together)

    #: How close two events have to be to count as having happened together.
    #:
    #: Measured from each event to the one before it rather than by cutting
    #: the clock into seconds: a tree walk that starts at 11:59.998 is one
    #: tree walk, and dividing it at the second boundary leaves a tail of
    #: three files that looks exactly like somebody opening three files.
    TOGETHER_WITHIN_SECONDS = 1.0

    def _judge(self, together: List[tuple]) -> None:
        """One group of events that happened at the same time, kept or not."""
        if not together:
            return
        if len(together) > self.BURST_IS_A_PROGRAM:
            # A program went through a directory. That it happened is worth
            # counting; that it touched nine hundred files is not nine
            # hundred things the user did.
            self._swept += len(together)
            self._sweeps += 1
            log.debug("ignored a sweep of %d file events", len(together))
            return
        for event_type, data, happened_at in together:
            self._send(event_type, data, happened_at)

    def _send(self, event_type: str, data: Dict[str, Any],
              happened_at: Optional[float] = None) -> None:
        when = (datetime.fromtimestamp(happened_at, timezone.utc)
                if happened_at else None)
        self.emit(
            ListenerEvent(
                event_type=event_type,
                source=self.provider_id,
                data=data,
                timestamp=when or datetime.now(timezone.utc),
                metadata={"backend": "watchdog" if _WATCHDOG else "polling"},
            )
        )

    # -- interface ----------------------------------------------------------
    def _watcher_alive(self) -> bool:
        """Whether anything is still listening, as opposed to still existing.

        An observer that has stopped delivering goes on being an object with
        a root attached to it, so "is there an observer" is not the question
        anyone wants answered. Watching a whole drive is the case that showed
        it: the notification buffer for a busy volume overflows, the emitter
        thread ends, and the provider reports `watching` with a frozen count
        for as long as it is left alone - a monitor that is off and says it
        is on, which section 19 calls worse than one that is off.
        """
        if self._poll_thread is not None:
            return bool(self._poll_thread.is_alive())
        if self._observer is None:
            return False
        try:
            if not self._observer.is_alive():
                return False
            emitters = list(getattr(self._observer, "emitters", []) or [])
        except Exception:                # pragma: no cover - defensive
            return True
        if not emitters:
            return False
        return any(emitter.is_alive() for emitter in emitters)

    def get_status(self) -> Dict[str, Any]:
        alive = self._watcher_alive()
        roots = dict(self._roots)
        if not alive:
            roots = {root: ("stopped" if state == "watching" else state)
                     for root, state in roots.items()}
        return {
            "running": alive,
            "backend": "watchdog" if _WATCHDOG else "polling",
            "roots": roots,
            "events": self._events,
            "opened": self._opened,
            "swept_past": self._swept,
            "sweeps": self._sweeps,
            "from_the_desktop": self._from_the_desktop,
            "watching": alive,
            "last_error": self.last_error or (
                "" if alive else
                "the watcher stopped delivering events - a directory too busy "
                "to watch, most often a whole drive. Narrow the watched "
                "directories and restart."),
        }

    def supported_event_types(self) -> List[str]:
        return sorted(set(EVENT_MAP.values())
                      | {"filesystem.renamed", "filesystem.opened"})

    def get_capabilities(self) -> Dict[str, Any]:
        return {"realtime": _WATCHDOG, "recursive": True, "rename_detection": True}

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.provider_id,
            version="1.0.0",
            supported_platforms=["windows", "macos", "linux"],
            supported_event_types=self.supported_event_types(),
            required_permissions=["read_filesystem_metadata"],
            configuration_schema={"listener_watch_paths": "multiline"},
            description="Local filesystem change monitoring.",
        )

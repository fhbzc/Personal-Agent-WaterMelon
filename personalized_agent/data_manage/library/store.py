"""The five folders, on disk (section 39.1, as amended).

This replaces the knowledge graph as the place the agent's memory lives. The
graph is kept, unused, in ``deprecated/`` - what it did is described there,
along with why it was set aside.

The short version: a graph stored what the agent knew as nodes joined by
weighted edges, and everything about it was invisible. The user could not read
it, the weights on which retrieval depended were guesses nobody could check,
and the only way to find out what the agent believed was to ask it. Files in
folders can be opened, and that turns out to matter more than the structure
that was lost.

Nothing here is clever. Records are files, folders are categories, and the
index is a derived artefact that can be deleted and rebuilt. That is on
purpose: the previous design failed in ways that were hard to see, and the
replacement is shaped so that looking at it is enough.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from .intro import intro_for
from ...common.agent_writes import mark_written
from ...common.events.models import utcnow
from .record import FOLDERS, INDEX, Record

log = logging.getLogger(__name__)

#: Held while a record's file name is chosen and its file written.
_NAMING = threading.Lock()

INTRO_NAME = "intro.txt"

#: Characters that cannot go in a filename on Windows, plus the ones that make
#: a name annoying to type at a shell.
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')

#: How much of the title goes into the filename. Long enough to recognise at a
#: glance in a file listing, short enough that the path stays under the
#: limits Windows still enforces.
TITLE_IN_NAME = 60


def _slug(title: str) -> str:
    cleaned = _UNSAFE.sub(" ", title or "").strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = cleaned.strip(".-")            # a name may not start or end thus
    return (cleaned[:TITLE_IN_NAME] or "untitled").lower()


class DataLibrary:
    """The folders, and what is in them."""

    def __init__(self, root: Any) -> None:
        self.root = Path(root)

    # -- layout -------------------------------------------------------------
    def folder(self, name: str) -> Path:
        return self.root / name

    @property
    def index_dir(self) -> Path:
        return self.root / INDEX

    def ensure(self) -> None:
        """Make the layout, and write each intro that is missing.

        An intro that exists is left exactly as it is, including one the user
        has rewritten - see the note in ``intro.py``.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        for name in FOLDERS:
            path = self.folder(name)
            path.mkdir(parents=True, exist_ok=True)
            intro = path / INTRO_NAME
            if not intro.exists():
                mark_written(intro)
                intro.write_text(intro_for(name), encoding="utf-8")

    def intro(self, name: str) -> str:
        path = self.folder(name) / INTRO_NAME
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return intro_for(name)

    # -- writing ------------------------------------------------------------
    def write(self, record: Record) -> Record:
        """Store a record and return it with its path filled in.

        The filename carries the date and the title so that a directory
        listing is itself readable; the id is in the file rather than in the
        name, because a user who renames a file should not thereby break the
        index that points at it.
        """
        if record.folder not in FOLDERS:
            raise ValueError(f"unknown folder: {record.folder}")
        directory = self.folder(record.folder)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = record.created_at.strftime("%Y-%m-%d")
        name = f"{stamp}-{_slug(record.title)}"
        # ONE WRITER AT A TIME CHOOSES A NAME. Summaries are written from
        # calls running at once, and two of the same title on the same day
        # would otherwise both find the name free and one write over the
        # other.
        with _NAMING:
            path = directory / f"{name}.md"
            # Two records written in the same second with the same title is
            # ordinary - one round can write several about one subject.
            suffix = 2
            while path.exists():
                path = directory / f"{name}-{suffix}.md"
                suffix += 1
            record.updated_at = record.updated_at or record.created_at
            # The count cannot see a file appear inside the same tick of the
            # directory's clock as the last one, and a round writing several
            # records does exactly that.
            self._counts_changed()
            mark_written(path)
            path.write_text(record.to_text(), encoding="utf-8")
        record.path = str(path)
        return record

    def put(self, record: Record) -> Record:
        """Write a record, replacing the file it came from if it has one.

        The rule everywhere else is that nothing is ever rewritten: a round
        writes new files, and saying the same thing twice is how the library
        records that it happened twice (section 49.5).

        States are the exception, and deliberately. A state is not an
        observation, it is a claim about what is true *now* - it gets extended,
        it gets closed, it follows the task it accompanies when the user drags
        that task across the calendar. A folder holding one file per change
        would say a dozen contradictory things about one afternoon and leave
        the reader to work out which is current, which is exactly the failure
        the state's own status field exists to prevent.

        So a state's file is the state, and it is kept in step with it. What
        changed is not lost: the history goes in the body, under the current
        text, so that opening the file shows what is true now and what it was
        before that.
        """
        if not record.path:
            return self.write(record)
        path = Path(record.path)
        record.updated_at = utcnow()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            mark_written(path)
            path.write_text(record.to_text(), encoding="utf-8")
        except OSError as exc:
            log.info("could not update %s (%s); writing a new file", path, exc)
            record.path = ""
            return self.write(record)
        return record

    # -- reading ------------------------------------------------------------
    def read(self, path: Any) -> Optional[Record]:
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.info("could not read %s (%s)", path, exc)
            return None
        folder = path.parent.name
        return Record.from_text(text, folder=folder, path=str(path))

    def files(self, folder: Optional[str] = None) -> Iterator[Path]:
        names = [folder] if folder else list(FOLDERS)
        for name in names:
            directory = self.folder(name)
            if not directory.is_dir():
                continue
            # scandir rather than iterdir: a directory entry already carries
            # whether it is a file, while Path.is_file() is another stat per
            # entry - seven thousand of them to answer a question about
            # counts. The name is checked first because it is free.
            try:
                with os.scandir(directory) as scan:
                    entries = sorted(scan, key=lambda entry: entry.name)
            except OSError:
                continue
            for entry in entries:
                if entry.name == INTRO_NAME:
                    continue
                if not entry.name.lower().endswith((".md", ".txt")):
                    continue
                try:
                    if not entry.is_file():
                        continue
                except OSError:                  # pragma: no cover - defensive
                    continue
                yield Path(entry.path)

    def records(self, folder: Optional[str] = None,
                limit: Optional[int] = None) -> List[Record]:
        out: List[Record] = []
        for path in self.files(folder):
            record = self.read(path)
            if record is not None:
                out.append(record)
            if limit is not None and len(out) >= limit:
                break
        return out

    def get(self, record_id: str, folder: Optional[str] = None) -> Optional[Record]:
        """A record by its id - in one folder when it is known which, which
        is most of the time and much less to read."""
        for path in self.files(folder):
            record = self.read(path)
            if record is not None and record.id == record_id:
                return record
        return None

    def recent(self, folder: str, limit: int = 50) -> List[Record]:
        """A folder's newest records. Each file's name begins with the day it
        was written, so the newest are the last names in order - a listing,
        not a search."""
        paths = sorted(self.files(folder), key=lambda path: Path(path).name,
                       reverse=True)
        out: List[Record] = []
        for path in paths:
            record = self.read(path)
            if record is not None:
                out.append(record)
            if len(out) >= limit:
                break
        return out

    # -- what the indexer needs --------------------------------------------
    @staticmethod
    def fingerprint(path: Any) -> str:
        """What 'unchanged since it was indexed' means.

        Content rather than modification time: a file copied, restored from a
        backup or touched by an editor that rewrites on save has a new mtime
        and the same content, and reindexing all of those wastes the round.

        Reading it is the expensive part, which is what ``stamp`` exists to
        avoid - see the note there.
        """
        try:
            data = Path(path).read_bytes()
        except OSError:
            return ""
        return hashlib.sha256(data).hexdigest()[:16]

    @staticmethod
    def stamp(path: Any) -> str:
        """A cheap guess at whether a file is the one we already read.

        Modification time and size, from one ``stat`` - no bytes read at all.

        This does not replace the content hash, it stands in front of it. The
        hash is right and the stamp is fast, so a file whose stamp matches what
        was recorded is taken as unchanged, and a file whose stamp differs is
        hashed to find out whether it really changed. Every file that a copy,
        a restore or a re-save touched without changing still costs exactly one
        read, once - the same guarantee as before.

        What it buys: finding the handful of changed files stops meaning
        reading the entire library. At two thousand records that was 36 MB and
        a second of work, every five minutes, to discover that nothing had
        happened.
        """
        try:
            info = Path(path).stat()
        except OSError:
            return ""
        return f"{info.st_mtime_ns}:{info.st_size}"

    def stats(self) -> Dict[str, Any]:
        """How much is in the library, counted only when it has changed.

        This is asked constantly - by the status the interface polls every
        few seconds, by the Memory tab, by the agent's own tools, by every
        heartbeat round - and it was counting every file each time. Measured
        on one machine with 7,715 records: **0.4 to 1.1 seconds a call**, and
        the Memory tab asked twice in one request.

        A folder's modification time moves when a file is added to it or
        removed from it, and stands still when one is merely rewritten -
        which is exactly the distinction a count needs. So five stats replace
        seven thousand.

        **But a directory's clock is coarse.** Measured on this filesystem:
        six files written in a loop moved it four times, because its
        resolution is a millisecond or two and writes inside one tick are one
        tick. A round that writes three records can therefore add three files
        the count cannot see. So the library says so itself when it writes or
        removes one, and a short expiry catches anything that changed outside
        this process within the same tick.
        """
        stamp = self._folder_stamps()
        cached = getattr(self, "_counts_cache", None)
        now = time.monotonic()
        if (cached is not None and cached[0] == stamp
                and now - cached[2] <= self.COUNTS_TTL_SECONDS):
            counts = cached[1]
        else:
            counts = {name: sum(1 for _ in self.files(name)) for name in FOLDERS}
            self._counts_cache = (stamp, counts, now)
        return {"root": str(self.root), "folders": dict(counts),
                "records": sum(counts.values()),
                # NOT MEASURED HERE. This is the status the interface
                # polls, and measuring touches every file: 23,578 of them
                # took twenty seconds on one machine, once a minute, with
                # every panel of the overview waiting behind it.
                "bytes": self.size(wait=False)["bytes"]}

    #: How long a count survives a folder whose clock has not moved.
    #:
    #: Only reached when the modification time is unchanged, so this is the
    #: window in which a change made outside this process, inside the same
    #: millisecond as the last one, stays invisible. Everything this process
    #: does is seen at once, by saying so.
    COUNTS_TTL_SECONDS = 20.0

    def _counts_changed(self) -> None:
        """Whatever was counted is out of date, and this process knows it."""
        self._counts_cache = None

    def _folder_stamps(self) -> tuple:
        """When each folder last gained or lost a file."""
        stamps = []
        for name in FOLDERS:
            try:
                stamps.append(self.folder(name).stat().st_mtime_ns)
            except OSError:
                stamps.append(0)
        return tuple(stamps)

    #: How long a measured size is reused for. Measuring means a stat() per
    #: file, which is a few milliseconds at two thousand records - fine once a
    #: minute and absurd on every query, which is exactly when it is wanted
    #: (section 14.36). A minute is far shorter than the five between rounds,
    #: so the number is never meaningfully stale.
    SIZE_TTL_SECONDS = 60.0

    def size(self, max_age: Optional[float] = None,
             wait: bool = True) -> Dict[str, Any]:
        """How much is on disk, measured at most once a minute.

        Recorded against every timing, because the question the Debugger is
        answering is how the cost of a search moves as the library grows, and
        that needs the size *at the time of the query* rather than the size
        now.
        """
        ttl = self.SIZE_TTL_SECONDS if max_age is None else max_age
        cached = getattr(self, "_size_cache", None)
        now = time.monotonic()
        # `ttl > 0` rather than `age <= ttl` alone: the clock here has a
        # resolution of about 15 ms, so two calls in the same tick are the same
        # instant, and `age <= 0` would hand back the cache to a caller who
        # asked for a fresh measurement.
        if cached is not None and ttl > 0 and now - cached[0] <= ttl:
            return cached[1]
        # NOT WAITED FOR, when the caller is answering somebody: the last
        # measurement is given, and a new one is taken in the background.
        # Before the first measurement there is no size to give.
        if not wait:
            self._measure_in_the_background()
            return (cached[1] if cached is not None
                    else {"records": None, "bytes": None})
        return self._measure()

    def _measure_in_the_background(self) -> None:
        """One measurement at a time, in a thread of its own."""
        lock = self.__dict__.setdefault("_measuring_lock", threading.Lock())
        if not lock.acquire(blocking=False):
            return

        def measure() -> None:
            try:
                self._measure()
            except Exception:          # pragma: no cover - a count, not a step
                log.debug("could not measure the library", exc_info=True)
            finally:
                lock.release()

        threading.Thread(target=measure, name="library-size",
                         daemon=True).start()

    def _measure(self) -> Dict[str, Any]:
        records, total = 0, 0
        for path in self.files():
            records += 1
            try:
                total += path.stat().st_size
            except OSError:
                continue
        measured = {"records": records, "bytes": total}
        self._size_cache = (time.monotonic(), measured)
        return measured


__all__ = ["DataLibrary", "INTRO_NAME"]

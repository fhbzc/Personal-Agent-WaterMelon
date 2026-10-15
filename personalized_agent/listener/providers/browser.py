"""Browser activity provider (section 17).

Collection differs per browser and per operating system, so collection is kept
strictly separate from normalization: the platform layer says *where* history
lives, a per family reader knows *how* to read it, and this provider only
produces the normalized ``browser.navigation`` event.

Detailed in-page interaction tracing (section 18) is deliberately out of scope
here; it belongs to an opt-in browser integration plugin.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...common.events.models import ListenerEvent
from ...common.own_interface import is_our_own_interface, our_port
from ...platform_layer import get_platform
from ..core.provider import ListenerProvider, PluginManifest
from ..core.manager import STOPPING_GRACE_SECONDS

log = logging.getLogger(__name__)

CHROMIUM_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _chromium_time(value: int) -> datetime:
    return CHROMIUM_EPOCH + timedelta(microseconds=int(value or 0))


def _firefox_time(value: int) -> datetime:
    return datetime.fromtimestamp(int(value or 0) / 1_000_000, tz=timezone.utc)


class BrowserProvider(ListenerProvider):
    provider_id = "browser"
    category = "browser"

    def __init__(self, config: Any, platform: Any = None, poll_seconds: float = 60.0) -> None:
        super().__init__()
        self.config = config
        self.platform = platform or get_platform()
        self.poll_seconds = poll_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._cursors: Dict[str, datetime] = {}
        self._emitted = 0
        self._sources: Dict[str, str] = {}
        #: browser -> (database path, its (modification time, size) when it
        #: was copied, when that was, the rows read from the copy). See _read.
        self._last_read: Dict[str, Tuple[str, Tuple[int, int], float,
                                         List[Tuple[str, str, datetime]]]] = {}

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        paths = self.platform.activity.get_browser_history_paths()
        if not paths:
            raise RuntimeError("no readable browser history database was found")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="listener-browser", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=STOPPING_GRACE_SECONDS)
            self._thread = None

    def _loop(self) -> None:
        # Seed the cursors so the first poll does not replay the whole history.
        start = datetime.now(timezone.utc) - timedelta(minutes=10)
        for name in self.platform.activity.get_browser_history_paths():
            self._cursors.setdefault(name, start)
        while not self._stop.is_set():
            try:
                self.poll()
            except Exception as exc:
                self.record_error(str(exc)[:200])
            self._stop.wait(self.poll_seconds)

    # -- collection ---------------------------------------------------------
    #: However still a database looks, it is copied and read again after this
    #: long. Its modification time is what says it changed, and this bounds
    #: what a time that failed to move could cost: a visit recorded late,
    #: never a visit lost - it carries its own time, and the cursor is by
    #: that time.
    REREAD_AT_LEAST_EVERY = 15 * 60.0

    def _read(self, browser: str, path: Path) -> List[Tuple[str, str, datetime]]:
        """Copy first: the live database is locked while the browser runs.

        NOT COPIED WHEN IT HAS NOT CHANGED. Every minute, every history
        database was copied whole and queried - measured on one machine,
        Firefox's places.sqlite is 84 MB, so about 5 GB an hour of disk
        traffic, nearly all of it a copy of a file identical to the one
        copied a minute before. What is read is the copy of the database
        file and nothing else, so a database file whose modification time
        and size are where they were gives back the rows it gave last time,
        and those rows are used again without copying anything.

        Only the database file is compared, not the -wal beside it: the
        -wal is not copied, so nothing in it reaches these rows, and
        Firefox writes it on every visit while the database itself changes
        only when the -wal is folded into it.
        """
        try:
            info = os.stat(path)
            shape: Optional[Tuple[int, int]] = (info.st_mtime_ns, info.st_size)
        except OSError:
            shape = None
        known = self._last_read.get(browser)
        if (shape is not None and known is not None and known[0] == str(path)
                and known[1] == shape
                and time.monotonic() - known[2] < self.REREAD_AT_LEAST_EVERY):
            return list(known[3])
        self._last_read.pop(browser, None)
        parsed = self._copy_and_query(browser, path)
        if parsed is not None and shape is not None:
            self._last_read[browser] = (str(path), shape, time.monotonic(), list(parsed))
        return parsed or []

    def _copy_and_query(self, browser: str,
                        path: Path) -> Optional[List[Tuple[str, str, datetime]]]:
        """The rows of a fresh copy, or None when it could not be read."""
        temp = Path(str(path) + f".agentcopy-{browser}")
        try:
            shutil.copy2(path, temp)
        except (OSError, PermissionError) as exc:
            self._sources[browser] = f"unreadable: {exc}"
            return None
        try:
            conn = sqlite3.connect(f"file:{temp}?mode=ro", uri=True, timeout=5)
            try:
                if browser.lower() == "firefox":
                    rows = conn.execute(
                        "SELECT url, title, last_visit_date FROM moz_places "
                        "WHERE last_visit_date IS NOT NULL ORDER BY last_visit_date DESC LIMIT 200"
                    ).fetchall()
                    parsed = [(r[0], r[1] or "", _firefox_time(r[2])) for r in rows]
                else:
                    rows = conn.execute(
                        "SELECT url, title, last_visit_time FROM urls "
                        "ORDER BY last_visit_time DESC LIMIT 200"
                    ).fetchall()
                    parsed = [(r[0], r[1] or "", _chromium_time(r[2])) for r in rows]
            finally:
                conn.close()
            self._sources[browser] = "ok"
            return parsed
        except sqlite3.Error as exc:
            self._sources[browser] = f"query failed: {exc}"
            return None
        finally:
            try:
                temp.unlink()
            except OSError:
                pass

    def poll(self) -> int:
        emitted = 0
        for browser, path in self.platform.activity.get_browser_history_paths().items():
            cursor = self._cursors.get(browser, datetime.now(timezone.utc) - timedelta(minutes=10))
            newest = cursor
            port = our_port(self.config)
            for url, title, visited in self._read(browser, Path(path)):
                if visited <= cursor:
                    continue
                # Section 9, as amended: this application's own panel is not
                # a site the user visited. It is served over HTTP and opened
                # in the ordinary browser, so it went into the history like
                # any other page and came back as browsing - sixth most
                # visited host on one machine, ahead of the search engine.
                if is_our_own_interface(url, port):
                    newest = max(newest, visited)
                    continue
                newest = max(newest, visited)
                emitted += 1
                self._emitted += 1
                self.emit(
                    ListenerEvent(
                        event_type="browser.navigation",
                        source=self.provider_id,
                        application=browser,
                        timestamp=visited,
                        data={"title": title, "url": url, "browser": browser},
                        metadata={"collection": "history_database"},
                    )
                )
            self._cursors[browser] = newest
        return emitted

    # -- interface ----------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "browsers": dict(self._sources),
            "emitted": self._emitted,
            "last_error": self.last_error,
        }

    def supported_event_types(self) -> List[str]:
        return ["browser.navigation"]

    def get_capabilities(self) -> Dict[str, Any]:
        available = self.platform.activity.get_browser_history_paths()
        return {
            "history_databases": sorted(available),
            "realtime": False,
            "interaction_tracing": False,
        }

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.provider_id,
            version="1.0.0",
            supported_platforms=["windows", "macos", "linux"],
            supported_event_types=self.supported_event_types(),
            required_permissions=["read_browser_history"],
            description="Browser navigation context from local history databases.",
        )

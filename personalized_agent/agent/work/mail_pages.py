"""THE MAIL PAGES THE USER OPENED, KEPT LONGER (the user, 2026-11-01).

A reply is opened in the thread it answers (handed.py), found among the
webmail pages the user opened - titled by their subjects - and checked
against who was on the page while it was open. The listener's own records
of both are kept eight days (`raw_activity_keep_days`): an email opened
before that was not found. So two longer memories, of the mail pages alone:

  - KEPT HERE (`keep`): each webmail page showing a message, from the
    records before they go - its address, its site, its title, when it was
    opened (the last few times), and the people on it: the email addresses,
    and the names written with them, read from its window while it was open.
    Never what the emails say. Kept KEEP_DAYS from when it was last opened.
    Looked at as the reply is opened and before the records are let go
    (data_manage/storage/housekeeping.py).
  - THE BROWSERS' OWN HISTORIES (`from_history`) - months of them: copied
    into the agent's own folder, read for webmail message pages only, and
    the copy removed. Who was on such a page is not known: it is unchecked.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: WEBMAIL WHOSE PAGES ARE TITLED BY THE MESSAGE THEY SHOW, and its name.
SITES = (("mail.google.com/mail/", "Gmail"), ("outlook.live.com/mail", "Outlook"),
         ("outlook.office.com/mail", "Outlook"), ("outlook.office365.com/mail", "Outlook"),
         ("mail.yahoo.com", "Yahoo Mail"), ("mail.qq.com", "QQ Mail"),
         ("mail.163.com", "163 Mail"), ("mail.126.com", "126 Mail"),
         ("mail.proton.me", "Proton Mail"), ("mail.zoho.", "Zoho Mail"),
         ("icloud.com/mail", "iCloud Mail"), ("app.fastmail.com", "Fastmail"))

#: How long a page is kept after it was last opened; how many of its times.
KEEP_DAYS, VISITS_MOST = 180, 10

#: What was read from a page while it was open: from this long before it was
#: opened to this long after - and so the window's words are taken in only
#: once that long has passed, when the page they may belong to is known.
READ_BEFORE, READ_AFTER = timedelta(minutes=1), timedelta(minutes=20)

#: How many people are kept for one page, and how many records are gone
#: through at a time.
PEOPLE_MOST, ROWS_AT_A_TIME = 40, 20000

#: How far back the browsers' histories are read, and for how long what they
#: gave is used again.
HISTORY_DAYS, HISTORY_FOR = 365, 600.0

ADDRESS = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
#: A name written with its address: "Ada Lovelace <ada@example.com>".
NAMED = re.compile(r"([^\s<>\"'@,;:()][^<>\"@,;:()\n]{1,60}?)\s*<\s*([\w.+-]+@[\w-]+(?:\.[\w-]+)+)\s*>")

SCHEMA = """
CREATE TABLE IF NOT EXISTS work_mail_pages (
    url        TEXT PRIMARY KEY,
    site       TEXT NOT NULL,
    title      TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    visits     TEXT NOT NULL DEFAULT '[]',
    people     TEXT NOT NULL DEFAULT '[]',
    read       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_work_mail_pages_seen ON work_mail_pages(last_seen);
CREATE TABLE IF NOT EXISTS work_mail_pages_read_to (
    kind TEXT PRIMARY KEY,
    at   INTEGER NOT NULL
);
"""

_lock = threading.Lock()


def site_of(url: str) -> str:
    """The webmail a page is of, when it shows one message - "" for any
    other page (a list, a search, any other site)."""
    url = str(url or "")
    if not url.startswith("https://"):
        return ""
    site = next((name for where, name in SITES if where in url), "")
    if site == "Gmail" and not re.search(r"#[^?]*/[A-Za-z0-9]{16,}$", url):
        return ""                          # a list or a search, not a message
    return site


def _ensure(db: Any) -> None:
    for statement in SCHEMA.split(";"):
        if statement.strip():
            db.execute(statement)


def _read_to(db: Any, kind: str) -> int:
    row = db.query_one("SELECT at FROM work_mail_pages_read_to WHERE kind = ?", (kind,))
    return int(row["at"]) if row else 0


def _set_read_to(db: Any, kind: str, at: int) -> None:
    db.execute("INSERT INTO work_mail_pages_read_to (kind, at) VALUES (?, ?) "
               "ON CONFLICT(kind) DO UPDATE SET at = excluded.at", (kind, at))


def people_in(text: str) -> List[str]:
    """The people on a page: the names written with addresses, and every
    address - nothing else of what it says."""
    found: List[str] = []
    for name, address in NAMED.findall(text or ""):
        name = " ".join(name.split()).strip(" -|·")
        if len(name) >= 2:
            found.append(name)
        found.append(address)
    found += ADDRESS.findall(text or "")
    out: List[str] = []
    seen = set()
    for one in found:
        if one.casefold() not in seen:
            seen.add(one.casefold())
            out.append(one)
    return out


def keep(db: Any) -> Dict[str, int]:
    """THE MAIL PAGES FROM THE RECORDS, BEFORE THEY GO (module docstring):
    the pages opened since last time, the people read from their windows,
    and the pages not opened in KEEP_DAYS let go."""
    if db is None:
        return {}
    with _lock:
        _ensure(db)
        said = {"pages": 0, "read": 0, "let_go": 0}
        # The pages opened.
        after = _read_to(db, "pages")
        rows = db.query("SELECT rowid, timestamp, data FROM raw_events "
                        "WHERE event_type = 'browser.navigation' AND rowid > ? "
                        "ORDER BY rowid LIMIT ?", (after, ROWS_AT_A_TIME))
        for row in rows:
            after = max(after, int(row["rowid"]))
            text = str(row["data"] or "")
            if "mail" not in text:
                continue
            try:
                page = json.loads(text)
            except ValueError:
                continue
            url, title = str(page.get("url") or ""), str(page.get("title") or "")
            site = site_of(url)
            if not site or not title.strip():
                continue
            _opened(db, url, site, title, str(row["timestamp"] or ""))
            said["pages"] += 1
        _set_read_to(db, "pages", after)
        # Who was on them: the windows' words, once READ_AFTER has passed.
        titles = _titles(db)
        after = _read_to(db, "people")
        until = (datetime.now(timezone.utc) - READ_AFTER).isoformat()
        rows = db.query("SELECT rowid, timestamp, data FROM raw_events "
                        "WHERE event_type = 'screen.text' AND rowid > ? AND timestamp <= ? "
                        "ORDER BY rowid LIMIT ?", (after, until, ROWS_AT_A_TIME))
        for row in rows:
            after = max(after, int(row["rowid"]))
            if not titles:
                continue
            try:
                read = json.loads(str(row["data"] or "{}"))
            except ValueError:
                continue
            window = str(read.get("window_title") or "")
            if not any(name in window for _where, name in SITES):
                continue
            for title, urls in titles.items():
                if title in window:
                    said["read"] += _was_read(db, urls, str(row["timestamp"] or ""),
                                              people_in(str(read.get("text") or "")))
                    break
        _set_read_to(db, "people", after)
        gone = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
        said["let_go"] = _let_go(db, gone)
        return said


def _let_go(db: Any, gone: str) -> int:
    before = db.query_one("SELECT COUNT(*) AS n FROM work_mail_pages WHERE last_seen < ?",
                          (gone,))
    db.execute("DELETE FROM work_mail_pages WHERE last_seen < ?", (gone,))
    return int(before["n"]) if before else 0


def _opened(db: Any, url: str, site: str, title: str, at: str) -> None:
    row = db.query_one("SELECT visits, last_seen FROM work_mail_pages WHERE url = ?", (url,))
    if row is None:
        db.execute("INSERT INTO work_mail_pages (url, site, title, first_seen, last_seen, "
                   "visits) VALUES (?, ?, ?, ?, ?, ?)",
                   (url, site, title[:400], at, at, json.dumps([at])))
        return
    visits = [one for one in json.loads(row["visits"] or "[]") if one != at] + [at]
    visits = sorted(visits)[-VISITS_MOST:]
    db.execute("UPDATE work_mail_pages SET title = ?, last_seen = ?, visits = ? WHERE url = ?",
               (title[:400], max(str(row["last_seen"] or ""), at), json.dumps(visits), url))


def _titles(db: Any) -> Dict[str, List[str]]:
    """The kept pages by their titles, longest first - a title inside
    another's is not taken for it."""
    out: Dict[str, List[str]] = {}
    for row in db.query("SELECT url, title FROM work_mail_pages"):
        out.setdefault(str(row["title"]), []).append(str(row["url"]))
    return dict(sorted(out.items(), key=lambda one: -len(one[0])))


def _was_read(db: Any, urls: List[str], at: str, people: List[str]) -> int:
    """Words read at `at` from a window of that title: given to the pages of
    that title opened around then. How many pages took them."""
    try:
        moment = datetime.fromisoformat(at)
    except ValueError:
        return 0
    taken = 0
    for url in urls:
        row = db.query_one("SELECT visits, people FROM work_mail_pages WHERE url = ?", (url,))
        if row is None:
            continue
        near = False
        for visit in json.loads(row["visits"] or "[]"):
            try:
                opened = datetime.fromisoformat(visit)
            except ValueError:
                continue
            if opened - READ_BEFORE <= moment <= opened + READ_AFTER:
                near = True
                break
        if not near:
            continue
        had = json.loads(row["people"] or "[]")
        known = {one.casefold() for one in had}
        had += [one for one in people if one.casefold() not in known]
        db.execute("UPDATE work_mail_pages SET people = ?, read = 1 WHERE url = ?",
                   (json.dumps(had[:PEOPLE_MOST], ensure_ascii=False), url))
        taken += 1
    return taken


def kept(db: Any) -> List[Dict[str, Any]]:
    """Every page kept: {"url", "in", "title", "at", "visits", "people", "read"}."""
    if db is None:
        return []
    with _lock:
        _ensure(db)
        rows = db.query("SELECT * FROM work_mail_pages ORDER BY last_seen DESC")
    return [{"url": row["url"], "in": row["site"], "title": row["title"],
             "at": row["last_seen"], "visits": json.loads(row["visits"] or "[]"),
             "people": json.loads(row["people"] or "[]"), "read": bool(row["read"])}
            for row in rows]


# -- the browsers' own histories ------------------------------------------------------------------
_history: Dict[str, Any] = {}
_history_lock = threading.Lock()

CHROMIUM_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def from_history(platform: Any, folder: Path) -> List[Tuple[str, str, str, str]]:
    """THE WEBMAIL MESSAGE PAGES IN THE BROWSERS' OWN HISTORIES (module
    docstring): (url, site, title, when last opened) - each history copied
    into `folder`, the agent's own, read, and the copy removed."""
    with _history_lock:
        if _history and time.time() - _history.get("at", 0) < HISTORY_FOR:
            return list(_history["pages"])
    pages: List[Tuple[str, str, str, str]] = []
    try:
        where = platform.activity.get_browser_history_paths()
    except Exception:                      # pragma: no cover - no history then
        where = {}
    since = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    for browser, path in dict(where or {}).items():
        pages += _pages_of(str(browser), Path(path), Path(folder), since)
    with _history_lock:
        _history.clear()
        _history.update(at=time.time(), pages=pages)
    return list(pages)


def _pages_of(browser: str, path: Path, folder: Path,
              since: datetime) -> List[Tuple[str, str, str, str]]:
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / f"mail-history-{re.sub(r'[^A-Za-z0-9]+', '-', browser)}.sqlite"
    try:
        shutil.copy2(path, copy)
    except OSError:
        return []
    out: List[Tuple[str, str, str, str]] = []
    try:
        # Immutable: read as it is, nothing written beside it (no -wal, no -shm).
        conn = sqlite3.connect(f"file:{copy.as_posix()}?mode=ro&immutable=1", uri=True,
                               timeout=5)
        try:
            if browser.lower() == "firefox":
                rows = conn.execute(
                    "SELECT url, title, last_visit_date FROM moz_places WHERE url LIKE "
                    "'https://%mail%' AND last_visit_date >= ?",
                    (int(since.timestamp() * 1_000_000),)).fetchall()
                moment = lambda value: datetime.fromtimestamp(  # noqa: E731
                    int(value or 0) / 1_000_000, tz=timezone.utc)
            else:
                rows = conn.execute(
                    "SELECT url, title, last_visit_time FROM urls WHERE url LIKE "
                    "'https://%mail%' AND last_visit_time >= ?",
                    (int((since - CHROMIUM_EPOCH).total_seconds() * 1_000_000),)).fetchall()
                moment = lambda value: CHROMIUM_EPOCH + timedelta(  # noqa: E731
                    microseconds=int(value or 0))
        finally:
            conn.close()
        for url, title, when in rows:
            site = site_of(url)
            if site and str(title or "").strip():
                out.append((str(url), site, str(title), moment(when).isoformat()))
    except sqlite3.Error:
        return []
    finally:
        for one in (copy, *(Path(str(copy) + tail) for tail in ("-wal", "-shm", "-journal"))):
            try:
                os.remove(one)
            except OSError:
                pass
    return out


__all__ = ["SITES", "KEEP_DAYS", "site_of", "people_in", "keep", "kept", "from_history"]

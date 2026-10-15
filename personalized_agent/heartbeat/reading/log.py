"""WHAT THE USER READ - kept as a log of pieces, in English, for what to
recommend to them next (Customized Recommendation).

After each agent heartbeat, what was read since the last time - the pages
the browser opened (its history), how long each window was in front, and
what the screen said - is put
together into PIECES: one per thing read or watched, however many events it
took. Then, for the new ones:

  1. TRIAGE   one call: which are things read or watched (an article, a
              paper, a video, a post, documentation) and not a tool being
              used; and which are worth reading again in full from their
              address - chosen from the title, the address and what the
              screen showed of them.
  2. FETCH    those, from the public web: only http(s), never a page on
              this computer or the local network, never an address that
              carries a sign-in, never a site the user keeps from the
              listener.
  3. EXTRACT  in batches: what kind it is, its topics and the people or
              things it is about, the one to three points it made - all in
              English, whatever language it was in - and a sentence.
  4. PLACE    each piece is embedded and placed in a topic (topics.py).

A piece read again is not written again: its time and visits grow, and how
much the user engaged with it with them.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

#: The events a piece is put together from.
READ_TYPES = ("browser.navigation", "application.focus_ended", "screen.text",
              "screen.shot")
#: Screenshots whose words are read in one pass, at most - each read once,
#: when it was taken, and kept (runtime/documents.py); this for one that was not.
MOST_SHOTS_READ = 20
#: The first time, how far back: not the whole history at once.
FIRST_LOOK_HOURS = 24
#: Events read in one go; the rest the next time.
MOST_EVENTS = 1500
#: Pieces judged in one triage call, and extracted in one call.
TRIAGE_BATCH = 40
EXTRACT_BATCH = 12
#: Pages read again from their address, at most, each time.
MOST_FETCHES = 8
#: What of a page read again is kept to extract from; what of the screen.
FETCH_CHARS = 6000
TEXT_CHARS = 1500
#: Where the log stands: the time of the last event put into it.
UNTIL = "reading_log_until"

#: A browser's name at the end of its window title.
_BROWSER_TAIL = re.compile(
    r"\s+[-–—]\s+(?:[^-–—]*\s[-–—]\s+)?(?:google chrome|"
    r"chromium|mozilla firefox|firefox|microsoft\W*edge|brave|opera|safari|vivaldi)\s*$",
    re.I)
#: OPENING SOME ADDRESSES DOES SOMETHING - unsubscribes, confirms, signs in,
#: accepts an invitation - or shows what only the link's holder may see.
#: Read again, the agent would do it for the user, on their accounts: never.
#: Their parameters:
_SECRET = re.compile(r"(?i)[?&#;](?:[^=&#;]*(?:token|key|auth|session|sig|password|passwd|"
                     r"code|ticket|secret|otp|nonce|hash|confirm|unsubscribe|optout|opt_out|"
                     r"verify|verification|invite|invitation|activate|reset|login|signin|"
                     r"magic|share|access|email|mail|uid|user)[^=&#;]*)=")
#: ... their paths:
_ACTION_PATH = re.compile(r"(?i)/(?:unsubscribe|optout|opt-out|confirm[\w-]*|verify[\w-]*|"
                          r"activate[\w-]*|invit[\w-]*|reset[\w-]*|password[\w-]*|login|log-in|signin|"
                          r"sign-in|signup|sign-up|oauth[\w-]*|auth[\w-]*|sso|magic[\w-]*|accept[\w-]*|"
                          r"approve[\w-]*|redirect|click|track[\w-]*|account[\w-]*|checkout|pay[\w-]*)"
                          r"(?:[/?#.]|$)")
#: ... a value like a one-time key: a long run of letters and digits.
_ONE_TIME = re.compile(r"[?&#;][^=&#;]+=[A-Za-z0-9_\-.%]{24,}")
#: ... and the sites of mail, files and documents shared by link: what is
#: on them is the user's, however public its address looks.
_PRIVATE_SITES = ("mail.", "outlook.", "hotmail.", "live.com", "163.com", "126.com",
                  "qq.com", "yeah.net", "proton.me", "protonmail.", "drive.google.",
                  "docs.google.", "sheets.google.", "slides.google.", "forms.google.",
                  "photos.google.", "dropbox.", "onedrive.", "1drv.ms", "sharepoint.",
                  "icloud.", "box.com", "notion.so", "notion.site", "feishu.", "larksuite.",
                  "yuque.", "shimo.im", "docs.qq.", "kdocs.", "wetransfer.", "mega.nz",
                  "pan.baidu.", "aliyundrive.", "quip.", "airtable.", "figma.com",
                  "slack.com", "teams.microsoft.", "zoom.us", "calendly.")
#: Windows of this application are not reading.
_OURS = ("personalizedagent", "personalized agent", "personalized_agent")

TRIAGE = """Below are things that were on the user's screen - pages, windows, documents,
videos - each with its id, how long it was in front, its address if it had
one, and some of what the screen showed of it.

{items}

For each, say:
  - keep: true when it is something the user READ, WATCHED or CONSULTED for
    its content - an article, a paper, a video, a post, a thread, a
    documentation page, a report. false for a tool being used: a file
    manager, a settings or sign-in page, an inbox or a list of search
    results, an editor or terminal, a chat box, this agent's own windows.
  - fetch: true when what is shown here of it is thin and reading the whole
    page from its address would tell much more about what they read - and
    it is a public page (a news article, a paper, a blog, documentation),
    not one of their accounts. At most {most} of them; only the most
    valuable.

Return ONLY this JSON object:
{{"items": [{{"id": "...", "keep": true, "fetch": false}}]}}"""

EXTRACT = """Below are things the user read or watched, each with its id and what is known
of it - its title, its address, how long they spent on it, and its text.

{items}

For each, whatever language it is in, write down what it was:
  - kind: article, paper, video, post, thread, documentation, report, help
    or other. "help" is anything looked up to get one thing done: how to fix
    an error, how to install or set something up, an AI assistant helping
    with a problem - whatever it is about
  - topics: one to four short topics it is about, from general to specific
  - entities: the people, organisations, projects or products it is about
  - points: the one to three things it said that someone who read it would
    now know - facts, findings, claims, news; not "it discusses X"
  - summary: one sentence

Return ONLY this JSON object:
{{"pieces": [{{"id": "...", "kind": "...", "topics": ["..."], "entities": ["..."],
              "points": ["..."], "summary": "..."}}]}}"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(when: Optional[datetime] = None) -> str:
    return (when or _now()).isoformat()


def _when(text: Any) -> Optional[datetime]:
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _loads(text: Any, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _json_of(text: str) -> Optional[Dict[str, Any]]:
    """The JSON object in a model's answer, or None."""
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        said = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return said if isinstance(said, dict) else None


def clean_title(title: Any) -> str:
    """A window's title without its browser's name."""
    return _BROWSER_TAIL.sub("", " ".join(str(title or "").split())).strip()


def title_key(title: Any) -> str:
    return re.sub(r"\W+", " ", clean_title(title).casefold()).strip()[:160]


def url_key(url: Any) -> str:
    """An address as one thing: no scheme, no www, no fragment, no trailing
    slash."""
    url = str(url or "").strip()
    url = re.sub(r"(?i)^https?://(www\.)?", "", url).split("#", 1)[0]
    return url.rstrip("/").casefold()[:500]


def public_address(url: Any) -> bool:
    """Whether an address may be read again from the web: http(s), a host
    on the internet - not this computer, not the local network - nothing
    that opening does (unsubscribe, confirm, sign in, accept), no key or
    sign-in carried in it, and not a site of mail, files or documents
    shared by link."""
    url = str(url or "")
    if not re.match(r"(?i)^https?://", url) or _SECRET.search(url) or \
            _ONE_TIME.search(url) or "@" in url.split("//", 1)[-1].split("/", 1)[0]:
        return False
    parsed = urlparse(url)
    if _ACTION_PATH.search(parsed.path or "/"):
        return False
    host = (parsed.hostname or "").lower()
    if not host or host == "localhost" or host.endswith((".local", ".localhost",
                                                         ".internal", ".lan")):
        return False
    if any(site in host for site in _PRIVATE_SITES):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "." in host
    return address.is_global


def engagement(seconds: float, visits: int, characters: int) -> float:
    """How much the user engaged with a piece: time on it (ten minutes is
    full), coming back to it, and having read much of it."""
    spent = min(1.0, math.log1p(max(0.0, seconds)) / math.log1p(600))
    again = 0.2 * min(3, max(0, visits - 1))
    read = 0.3 if characters >= 500 else 0.0
    return round(spent + again + read, 3)


class ReadingLog:
    """The pieces the user read, and the topics they fall into."""

    def __init__(self, db: Any, raw_store: Any, models: Any = None, config: Any = None,
                 embedder: Any = None, fetch: Optional[Callable[[str], Any]] = None,
                 allowed: Optional[Callable[[str], bool]] = None) -> None:
        from .topics import TopicModel
        self.db = db
        self.raw_store = raw_store
        self.models = models
        self.config = config
        self.embedder = embedder
        if fetch is None:
            from ...agent.work.tools import fetch as _fetch
            fetch = _fetch
        self.fetch = fetch
        #: Whether a site may be kept - the listener's own lists.
        self.allowed = allowed or (lambda _url: True)
        self.topics = TopicModel(db, models)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.progress: Dict[str, Any] = {"running": False}

    # -- running ---------------------------------------------------------------------------
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, going: Callable[[], bool] = lambda: True) -> bool:
        """What was read since the last time, put in the log - in a thread of
        its own, one at a time. Whether it started."""
        with self._lock:
            if self.running():
                return False

            def run() -> None:
                try:
                    self.catch_up(going)
                except Exception as exc:          # pragma: no cover - never the heartbeat's
                    log.warning("the reading log could not be written", exc_info=True)
                    self.progress.update(running=False, error=str(exc)[:300])

            self._thread = threading.Thread(target=run, name="reading", daemon=True)
            self._thread.start()
            return True

    def wait(self, timeout: float = 60.0) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def catch_up(self, going: Callable[[], bool] = lambda: True) -> Dict[str, Any]:
        """One pass (module docstring)."""
        self.progress = {"running": True, "stage": "reading what was on screen",
                         "started_at": _iso(), "error": ""}
        report: Dict[str, Any] = {}
        since = _when(self.config.get(UNTIL, "") if self.config is not None else "") \
            or _now() - timedelta(hours=FIRST_LOOK_HOURS)
        events = self._events(since)
        report["events"] = len(events)
        self._shots_read = 0
        items = self._sittings(events)
        fresh, again = self._against_the_log(items)
        report["read_again"] = again
        if fresh and going():
            self.progress.update(stage="choosing what was read", total=len(fresh))
            kept, fetching = self._triage(fresh)
            if kept is not None:
                report["kept"] = len(kept)
                self.progress.update(stage="reading pages again")
                report["fetched"] = self._fetch_again(kept, fetching, going)
                self.progress.update(stage="writing down what each said")
                report["written"] = self._extract(kept, going)
            else:
                # The model could not be asked: the same events next time.
                self.progress.update(running=False, finished_at=_iso(),
                                     stage="", note="the model could not be asked")
                return report
        if events and self.config is not None:
            self.config.set(UNTIL, events[-1]["at"], internal=True)
        report["topics"] = self.topics.place_waiting(self.embedder)
        self.progress.update(running=False, stage="", finished_at=_iso(), last=report)
        return report

    # -- 0. the events, as pieces ------------------------------------------------------------
    def _events(self, since: datetime) -> List[Dict[str, Any]]:
        rows = self.db.query(
            "SELECT id, event_type, timestamp, application, data FROM raw_events WHERE "
            "timestamp > ? AND event_type IN (" + ",".join("?" * len(READ_TYPES)) + ") "
            "ORDER BY timestamp, rowid LIMIT ?",
            (_iso(since), *READ_TYPES, MOST_EVENTS))
        return [{"id": row["id"], "type": row["event_type"], "at": row["timestamp"],
                 "app": str(row["application"] or ""), "data": _loads(row["data"], {})}
                for row in rows]

    def _sittings(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The events put together: a piece per address, or per window that
        had none - the screen's text and the time in front joined to the
        page by its title."""
        pieces: Dict[str, Dict[str, Any]] = {}

        def piece(key: str, at: str) -> Dict[str, Any]:
            if key not in pieces:
                pieces[key] = {"key": key, "url": "", "title": "", "app": "", "seconds": 0.0,
                               "visits": 0, "text": "", "first": at, "last": at}
            one = pieces[key]
            one["last"] = max(one["last"], at)
            return one

        for event in events:
            data, kind, at = event["data"], event["type"], event["at"]
            if any(mark in (event["app"] + " " + str(data.get("window_title") or "")).lower()
                   for mark in _OURS):
                continue
            if kind == "browser.navigation":
                url = str(data.get("url") or "")
                if not re.match(r"(?i)^https?://", url) or not self.allowed(url) or \
                        (urlparse(url).hostname or "") in ("localhost", "127.0.0.1"):
                    continue
                one = piece("u:" + url_key(url), at)
                one["url"] = one["url"] or url
                one["title"] = clean_title(data.get("title")) or one["title"]
                one["visits"] += 1
                continue
            title = clean_title(data.get("window_title"))
            if not title:
                continue
            one = piece("t:" + title_key(title), at)
            one["title"] = one["title"] or title
            one["app"] = one["app"] or str(data.get("application") or event["app"] or "")
            if kind == "application.focus_ended":
                one["seconds"] += float(data.get("duration_seconds") or 0.0)
            elif kind == "screen.text":
                text = str(data.get("text") or "")
                if text and text not in one["text"]:
                    one["text"] = (one["text"] + "\n" + text).strip()[-TEXT_CHARS * 2:]
            elif kind == "screen.shot":
                # A picture of the window - a slide, a figure, a video: its
                # words, read on this computer.
                text = self._shot_words(str(data.get("path") or ""))
                if text and text not in one["text"]:
                    one["text"] = (one["text"] + "\n" + text).strip()[-TEXT_CHARS * 2:]
        # A window's piece joined to its page's, where its title is the page's.
        by_title = {title_key(one["title"]): one for one in pieces.values()
                    if one["key"].startswith("u:") and one["title"]}
        out = []
        for one in pieces.values():
            if one["key"].startswith("t:") and one["key"][2:] in by_title:
                page = by_title[one["key"][2:]]
                page["seconds"] += one["seconds"]
                page["text"] = (page["text"] + "\n" + one["text"]).strip()[-TEXT_CHARS * 2:]
                page["app"] = page["app"] or one["app"]
                page["first"], page["last"] = (min(page["first"], one["first"]),
                                               max(page["last"], one["last"]))
                continue
            if one["title"] or one["url"]:
                out.append(one)
        return out

    def _shot_words(self, path: str) -> str:
        """A screenshot's words: from its copy, or read now - a few a pass."""
        if not path:
            return ""
        from ...runtime import documents
        try:
            kept = documents.copy_of(path)
            if kept is not None:
                return str(kept.get("text") or "")
            if self._shots_read >= MOST_SHOTS_READ:
                return ""
            self._shots_read += 1
            return str(documents.extract(path).get("text") or "")
        except OSError:
            return ""

    def _against_the_log(self, items: List[Dict[str, Any]]):
        """What is new, and what was read before - the second only counted."""
        fresh, again = [], 0
        for one in items:
            row = None
            if one["url"]:
                row = self.db.query_one("SELECT * FROM reading_pieces WHERE url_key = ?",
                                        (url_key(one["url"]),))
            if row is None and one["title"]:
                row = self.db.query_one("SELECT * FROM reading_pieces WHERE title_key = ? AND "
                                        "url_key = ''", (title_key(one["title"]),))
            if row is None:
                fresh.append(one)
                continue
            again += 1
            seconds = float(row["seconds"] or 0) + one["seconds"]
            visits = int(row["visits"] or 0) + max(1, one["visits"])
            self.db.execute(
                "UPDATE reading_pieces SET seconds = ?, visits = ?, last_at = ?, "
                "engagement = ?, updated_at = ? WHERE id = ?",
                (seconds, visits, max(str(row["last_at"]), one["last"]),
                 engagement(seconds, visits, int(row["characters"] or 0)), _iso(), row["id"]))
        return fresh, again

    # -- 1. triage --------------------------------------------------------------------------
    def _triage(self, items: List[Dict[str, Any]]):
        """The pieces that are reading, and those worth reading again whole
        - or None when the model could not be asked."""
        for n, one in enumerate(items):
            one["id"] = f"p{n}"
        kept, fetching = [], set()
        for start in range(0, len(items), TRIAGE_BATCH):
            part = items[start:start + TRIAGE_BATCH]
            lines = "\n".join(
                f"[{one['id']}] {one['title'][:200]}" + (f" <{one['url'][:300]}>" if one["url"]
                                                         else f" ({one['app'][:60]})")
                + f" - {round(one['seconds'])}s"
                + (f"\n    {' '.join(one['text'].split())[:300]}" if one["text"] else "")
                for one in part)
            said = self._ask(TRIAGE.format(items=lines, most=MOST_FETCHES))
            if said is None:
                return None, set()
            verdicts = {str(v.get("id")): v for v in said.get("items") or []
                        if isinstance(v, dict)}
            for one in part:
                verdict = verdicts.get(one["id"]) or {}
                if verdict.get("keep") is True:
                    kept.append(one)
                    if verdict.get("fetch") is True and one["url"]:
                        fetching.add(one["id"])
        return kept, fetching

    # -- 2. fetch ---------------------------------------------------------------------------
    def _fetch_again(self, kept: List[Dict[str, Any]], fetching: set,
                     going: Callable[[], bool]) -> int:
        done = 0
        for one in kept:
            if done >= MOST_FETCHES or not going():
                break
            if one["id"] not in fetching or not public_address(one["url"]) \
                    or not self.allowed(one["url"]):
                continue
            try:
                page = self.fetch(one["url"])
            except Exception:                     # pragma: no cover - the screen's text stands
                continue
            text = str((page or {}).get("text") or "") if isinstance(page, dict) else ""
            if len(text) > len(one["text"]):
                one["text"] = text[:FETCH_CHARS]
                one["fetched"] = True
                done += 1
        return done

    # -- 3. extract -------------------------------------------------------------------------
    def _extract(self, kept: List[Dict[str, Any]], going: Callable[[], bool]) -> int:
        from ...common.events.models import new_id
        written = 0
        for start in range(0, len(kept), EXTRACT_BATCH):
            if not going():
                break
            part = kept[start:start + EXTRACT_BATCH]
            lines = "\n\n".join(
                f"[{one['id']}] {one['title'][:200]}" + (f" <{one['url'][:300]}>"
                                                         if one["url"] else "")
                + f" - {round(one['seconds'])}s\n"
                + " ".join(one["text"].split())[:FETCH_CHARS if one.get("fetched")
                                                else TEXT_CHARS]
                for one in part)
            said = self._ask(EXTRACT.format(items=lines))
            if said is None:
                break
            got = {str(p.get("id")): p for p in said.get("pieces") or [] if isinstance(p, dict)}
            for one in part:
                p = got.get(one["id"])
                if p is None:
                    continue
                words = lambda key, most, chars: [str(x)[:chars] for x in p.get(key) or []
                                                  if str(x).strip()][:most]
                characters = len(one["text"])
                self.db.execute(
                    "INSERT INTO reading_pieces (id, first_at, last_at, url, url_key, title, "
                    "title_key, app, kind, topics, entities, points, summary, seconds, visits, "
                    "characters, fetched, engagement, topic_id, vector, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', NULL, ?, ?)",
                    (new_id("read"), one["first"], one["last"], one["url"][:1000],
                     url_key(one["url"]) if one["url"] else "", one["title"][:300],
                     title_key(one["title"]), one["app"][:80],
                     str(p.get("kind") or "other")[:20],
                     json.dumps(words("topics", 4, 60)), json.dumps(words("entities", 8, 80)),
                     json.dumps(words("points", 3, 400)), str(p.get("summary") or "")[:400],
                     round(one["seconds"], 1), max(1, one["visits"]), characters,
                     int(bool(one.get("fetched"))),
                     engagement(one["seconds"], max(1, one["visits"]), characters),
                     _iso(), _iso()))
                written += 1
            self.progress.update(done=min(len(kept), start + len(part)), total=len(kept))
        return written

    def _ask(self, prompt: str) -> Optional[Dict[str, Any]]:
        if self.models is None:
            return None
        from ...models.base import ModelRequest
        from ...common.languages import in_english
        try:
            response = self.models.invoke(ModelRequest(
                messages=[{"role": "user", "content": prompt}],
                system="You keep a log of what one person read, faithfully and briefly. "
                       "Return only the JSON object asked for.",
                max_tokens=6000, task="reading",
                # Kept to recommend from and compared with one another.
                language=in_english("everything you write down: kinds, topics, people and "
                                    "things, points and sentences")), role="heartbeat")
        except Exception as exc:
            log.info("the reading log could not ask the model: %s", exc)
            return None
        return _json_of(getattr(response, "text", "") or "")

    # -- what the page shows ----------------------------------------------------------------------
    def pieces(self, most: int = 100) -> List[Dict[str, Any]]:
        out = []
        for row in self.db.query("SELECT * FROM reading_pieces ORDER BY last_at DESC LIMIT ?",
                                 (int(most),)):
            one = {key: row[key] for key in ("id", "first_at", "last_at", "url", "title", "app",
                                            "kind", "summary", "seconds", "visits",
                                            "engagement", "topic_id")}
            one["fetched"] = bool(row["fetched"])
            for key in ("topics", "entities", "points"):
                one[key] = _loads(row[key], [])
            out.append(one)
        return out

    def describe(self) -> Dict[str, Any]:
        count = self.db.query_one("SELECT COUNT(*) AS n FROM reading_pieces") or {}
        return {"progress": dict(self.progress),
                "until": str(self.config.get(UNTIL, "") or "") if self.config is not None else "",
                "count": int(count.get("n") or 0),
                "topics": self.topics.describe(),
                "pieces": self.pieces()}


__all__ = ["ReadingLog", "clean_title", "title_key", "url_key", "public_address",
           "engagement", "UNTIL"]

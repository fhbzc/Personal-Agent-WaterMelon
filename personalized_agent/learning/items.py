"""THE LEARNING LIST: what is learned beside the user's own folders (the
user, 2026-10-24).

An ITEM is a web page or site, a passage of text, a folder or a file -
added by the user (the Learning panel's Add, the "Learn" button beside
selected text, Explorer's "Add to learning"), or by the state heartbeat on
evidence that the user endorses it (learning/interests.py). Each says:

  WHOSE       "mine" - the user's own work: learned in full, its decisions
              are the user's judgments; "others" - someone else's, learned
              as a reference the user endorses, never as the user's own.
  WHAT        any of cases, rules, reference.
  ANGLE       what to learn of it, the purpose - given to the reading model:
              for the user's own an emphasis, for someone else's the bounds
              of what is learned (they endorse that part, not the rest).
  SCOPE       one page, the pages under an address, a repository, a
              folder, a file, the passage itself.
  CADENCE     once; at every learning update; every N days.
  SOURCE      what it was split from (sources.py), when it was: an account,
              a site, a folder handed over whole - and its other folders or
              addresses (`more`) when one piece of work spans several.

Each is learned in the learning's own updates, under its daily limit, as a
unit of its own (`unit_id`). Deactivated, it is not read and nothing
learned from it is given (store.held_units). Deleted, it is gone with all
that was learned from it, and its source is put on the NEVER LIST, which
nothing - the heartbeat included - adds back; the user may take it off.

A page that can no longer be read is not read again until it can be: what
was learned from it stays.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .store import LearningStore, dumps, loads, new_id, now

ORIGINS = ("you", "heartbeat")
KINDS = ("url", "text", "folder", "file")
WHOSE = ("mine", "others")
LEARNS = ("cases", "rules", "reference")
SCOPES = ("page", "prefix", "repo", "folder", "file", "text")
STATES = ("active", "inactive")
#: new: not read yet; pending: read once, waiting for the user to look
#: (their own online work, and what the heartbeat added); learned; and
#: unreachable: its page could not be read the last time.
STATUSES = ("new", "pending", "learned", "unreachable")
#: The never list, in the learning's settings: paths and web addresses.
NEVER = "learning_excluded"
#: Units of the user's own put on the never list: {"id", "title"} each.
NEVER_UNITS = "learning_never_units"
#: Tokens past which an item is marked large in the list.
LARGE_TOKENS = 500_000
#: The most of a passage kept.
TEXT_MOST = 200_000


#: A folder's own files only - not the folders in it: a part of a unit split
#: from a folder (sources.py), written after the folder's path.
FILES_ONLY = "|files"


def part_path(part: str) -> str:
    """A unit's part as the path or address it is at."""
    part = str(part or "")
    return part[:-len(FILES_ONLY)] if part.endswith(FILES_ONLY) else part


def folder_parts(item: Dict[str, Any]) -> tuple:
    """A folder item's folders, and those of them read for their own files
    only: ([paths], [paths])."""
    if item.get("kind") != "folder":
        return [], []
    parts = [str(one) for one in [item.get("source"), *(item.get("more") or [])] if one]
    return ([part_path(one) for one in parts],
            [part_path(one) for one in parts if one.endswith(FILES_ONLY)])


def unit_id_of(item_id: str) -> str:
    return f"item:{item_id}"


def norm_url(url: str) -> str:
    """An address as compared: its scheme and site in lower case, no
    fragment, no trailing slash."""
    url = str(url or "").strip()
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url.lower()
    if not parts.scheme or not parts.netloc:
        return url.lower()
    path = parts.path.rstrip("/")
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path,
                                    parts.query, ""))


def is_url(text: str) -> bool:
    return bool(re.match(r"(?i)^https?://\S+$", str(text or "").strip()))


def _norm_path(path: Any) -> str:
    import os
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except (TypeError, ValueError):
        return ""


def under(source: str, listed: str) -> bool:
    """Whether a source lies at or under one on the never list: an address
    under an address, a path under a path."""
    if is_url(source) or is_url(listed):
        if not (is_url(source) and is_url(listed)):
            return False
        a, b = norm_url(source), norm_url(listed)
        return a == b or a.startswith(b + "/")
    import os
    a, b = _norm_path(source), _norm_path(listed)
    return bool(b) and (a == b or a.startswith(b.rstrip(os.sep) + os.sep))


def item_row(db: Any, item_id: str) -> Optional[Dict[str, Any]]:
    """An item as kept, read with the database alone (for whoever has no
    settings at hand: the finder, the references)."""
    try:
        return _row_out(db.query_one("SELECT * FROM learn_items WHERE id = ?",
                                     (str(item_id or ""),)))
    except Exception:
        return None


def item_of_unit(db: Any, unit_id: str) -> Optional[Dict[str, Any]]:
    unit_id = str(unit_id or "")
    return item_row(db, unit_id[len("item:"):]) if unit_id.startswith("item:") else None


def cadence_ok(cadence: str) -> bool:
    return cadence in ("once", "update") or bool(re.match(r"^days:\d{1,3}$", cadence or ""))


def _row_out(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out = dict(row)
    out["learn"] = loads(out.get("learn"), [])
    out["evidence"] = loads(out.get("evidence"), [])
    out["fetched"] = loads(out.get("fetched"), {})
    out["more"] = loads(out.get("more"), []) or []
    out["source_id"] = out.get("source_id") or ""
    out["unit_id"] = unit_id_of(out["id"])
    out["large"] = int(out.get("estimate") or 0) >= LARGE_TOKENS
    return out


class Items:
    def __init__(self, db: Any, config: Any) -> None:
        self.db = db
        self.config = config
        self.store = LearningStore(db)

    # -- the never list -------------------------------------------------------
    def never(self) -> List[str]:
        return [str(one) for one in (self.config.get(NEVER, []) or []) if str(one).strip()]

    def never_units(self) -> List[Dict[str, Any]]:
        return [dict(one) for one in (self.config.get(NEVER_UNITS, []) or [])
                if isinstance(one, dict) and one.get("id")]

    def on_never(self, source: str) -> bool:
        source = part_path(source)
        return bool(source) and any(under(source, one) for one in self.never())

    def put_never(self, source: str) -> None:
        if not source or self.on_never(source):
            return
        self.config.set(NEVER, self.never() + [source], internal=True)

    def put_never_unit(self, unit_id: str, title: str) -> None:
        kept = [one for one in self.never_units() if one["id"] != unit_id]
        self.config.set(NEVER_UNITS, kept + [{"id": unit_id, "title": title, "at": now()}],
                        internal=True)

    def take_off_never(self, key: str) -> bool:
        """Off the never list: a path or an address, or a unit's id."""
        listed = self.never()
        kept = [one for one in listed if one != key and norm_url(one) != norm_url(key)
                and _norm_path(one) != _norm_path(key)]
        units = self.never_units()
        kept_units = [one for one in units if one["id"] != key]
        if len(kept) == len(listed) and len(kept_units) == len(units):
            return False
        self.config.set(NEVER, kept, internal=True)
        self.config.set(NEVER_UNITS, kept_units, internal=True)
        return True

    def unit_never(self, unit_id: str) -> bool:
        return any(one["id"] == unit_id for one in self.never_units())

    # -- the items -------------------------------------------------------------
    def all(self) -> List[Dict[str, Any]]:
        return [_row_out(row) for row in self.db.query(
            "SELECT * FROM learn_items ORDER BY added_at")]

    def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        return _row_out(self.db.query_one("SELECT * FROM learn_items WHERE id = ?",
                                          (str(item_id or ""),)))

    def of_unit(self, unit_id: str) -> Optional[Dict[str, Any]]:
        unit_id = str(unit_id or "")
        return self.get(unit_id[len("item:"):]) if unit_id.startswith("item:") else None

    def same(self, kind: str, source: str, text: str = "") -> Optional[Dict[str, Any]]:
        """The item already listed for this source, if there is one."""
        for one in self.all():
            if one["kind"] != kind:
                continue
            if kind == "text":
                if " ".join(one["text"].split()) == " ".join(str(text).split()):
                    return one
            elif kind == "url":
                if norm_url(one["source"]) == norm_url(source):
                    return one
            elif _norm_path(one["source"]) == _norm_path(source):
                return one
        return None

    @staticmethod
    def clean_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
        """A plan as kept: every field one of its allowed values; raises
        ValueError on what cannot be kept."""
        kind = str(plan.get("kind") or "")
        if kind not in KINDS:
            raise ValueError("an item is a web address, a passage of text, a folder or a file")
        source = str(plan.get("source") or "").strip()
        text = str(plan.get("text") or "")
        if kind == "url":
            if not is_url(source):
                raise ValueError("a web address starts with http:// or https://")
        elif kind == "text":
            if not text.strip():
                raise ValueError("there is no text to keep")
            text = text[:TEXT_MOST]
        else:
            if not source:
                raise ValueError("no path given")
            path = Path(source).expanduser()
            own = kind == "folder" and source.endswith(FILES_ONLY)
            path = Path(part_path(source)).expanduser()
            if kind == "folder" and not path.is_dir():
                raise ValueError(f"{path} is not a folder on this computer")
            if kind == "file" and not path.is_file():
                raise ValueError(f"{path} is not a file on this computer")
            source = str(path) + (FILES_ONLY if own else "")
        whose = str(plan.get("whose") or "mine")
        if whose not in WHOSE:
            raise ValueError("whose is mine or others")
        learn = [one for one in plan.get("learn") or [] if one in LEARNS]
        if not learn:
            raise ValueError("choose what to learn: cases, rules or a reference")
        scope = str(plan.get("scope") or {"url": "page", "text": "text", "folder": "folder",
                                          "file": "file"}[kind])
        if scope not in SCOPES:
            raise ValueError("the scope is a page, the pages under an address, a repository, "
                             "a folder, a file or the passage")
        cadence = str(plan.get("cadence") or ("once" if kind == "text" else "update"))
        if not cadence_ok(cadence):
            raise ValueError("how often: once, at every update, or every N days")
        try:
            estimate = max(0, int(plan.get("estimate") or 0))
        except (TypeError, ValueError):
            estimate = 0
        evidence = [one for one in plan.get("evidence") or [] if isinstance(one, dict)][:20]
        # A unit's other parts: of its own kind, each as its source is.
        more = []
        for one in plan.get("more") or []:
            one = str(one or "").strip()
            if kind == "url" and is_url(one):
                more.append(one)
            elif kind == "folder" and one and Path(part_path(one)).expanduser().is_dir():
                more.append(str(Path(part_path(one)).expanduser())
                            + (FILES_ONLY if one.endswith(FILES_ONLY) else ""))
        return {"kind": kind, "source": source, "text": text,
                "more": list(dict.fromkeys(more))[:200],
                "source_id": str(plan.get("source_id") or "")[:40],
                "title": str(plan.get("title") or "")[:200]
                or (source[:200] if source else text.strip().splitlines()[0][:80]),
                "about": str(plan.get("about") or "")[:600],
                "whose": whose, "learn": list(dict.fromkeys(learn)),
                "angle": str(plan.get("angle") or "")[:600], "evidence": evidence,
                "scope": scope, "cadence": cadence, "estimate": estimate}

    def add(self, plan: Dict[str, Any], origin: str = "you") -> Dict[str, Any]:
        """An item added, from its plan. Raises ValueError: a source on the
        never list - whoever adds it - one already listed, a plan that is
        not whole."""
        if origin not in ORIGINS:
            raise ValueError("an item is added by you or by the heartbeat")
        clean = self.clean_plan(plan)
        if clean["kind"] != "text" and self.on_never(clean["source"]):
            raise ValueError("it is on the list of what is never learned - take it off "
                             "that list first")
        if self.same(clean["kind"], clean["source"], clean["text"]):
            raise ValueError("it is in the learning list already")
        stamp = now()
        row = dict(clean, id=new_id("L"), origin=origin, state="active", status="new",
                   note="", fetched={}, added_at=stamp, updated_at=stamp, read_at="")
        self._put(row)
        return self.get(row["id"])

    def _put(self, row: Dict[str, Any]) -> None:
        values = dict(row)
        for name in ("learn", "evidence", "fetched", "more"):
            if name in values and not isinstance(values[name], str):
                values[name] = dumps(values[name])
        for name in ("unit_id", "large"):
            values.pop(name, None)
        names = list(values)
        self.db.execute(
            f"INSERT INTO learn_items ({', '.join(names)}) VALUES "
            f"({', '.join('?' for _ in names)}) ON CONFLICT(id) DO UPDATE SET "
            + ", ".join(f"{name} = excluded.{name}" for name in names if name != "id"),
            tuple(values[name] for name in names))

    def change(self, item_id: str, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Its plan changed: what to learn, the angle, whose, how often. The
        source stays what it was."""
        item = self.get(item_id)
        if item is None:
            raise ValueError("no such item")
        merged = dict(item, **{key: value for key, value in plan.items()
                               if key in ("title", "about", "whose", "learn", "angle", "scope",
                                          "cadence")})
        clean = self.clean_plan(dict(merged, kind=item["kind"], source=item["source"],
                                     text=item["text"]))
        row = dict(item, **{key: clean[key] for key in ("title", "about", "whose", "learn",
                                                        "angle", "scope", "cadence")})
        row["updated_at"] = now()
        self._put(row)
        return self.get(item_id)

    def save(self, item: Dict[str, Any]) -> None:
        row = dict(item)
        row["updated_at"] = now()
        self._put(row)

    def set_state(self, item_id: str, active: bool) -> Optional[Dict[str, Any]]:
        item = self.get(item_id)
        if item is None:
            return None
        item["state"] = "active" if active else "inactive"
        self.save(item)
        unit = self.store.unit(item["unit_id"])
        if unit is not None:
            self.store.set_unit_state(item["unit_id"], "inactive" if not active else
                                      ("pending" if item["status"] == "pending" else "read"))
        return self.get(item_id)

    def remove(self, item_id: str) -> Optional[Dict[str, Any]]:
        """The item's row gone - what was learned from it is the caller's to
        forget (service.forget). Its source goes on the never list."""
        item = self.get(item_id)
        if item is None:
            return None
        self.db.execute("DELETE FROM learn_items WHERE id = ?", (item_id,))
        if item["kind"] != "text":
            self.put_never(item["source"])
        return item

    # -- when each is read -------------------------------------------------------
    def due(self, moment: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """The active items to read in an update now: never read, and those
        whose cadence has come round - not one that is read once and was."""
        moment = moment or datetime.now(timezone.utc)
        out = []
        for item in self.all():
            if item["state"] != "active":
                continue
            if not item["read_at"]:
                out.append(item)
                continue
            cadence = item["cadence"]
            if cadence == "once":
                continue
            if cadence == "update":
                out.append(item)
                continue
            try:
                days = int(cadence.split(":", 1)[1])
                last = datetime.fromisoformat(item["read_at"].replace("Z", "+00:00"))
            except (ValueError, IndexError):
                out.append(item)
                continue
            if moment - last >= timedelta(days=days):
                out.append(item)
        return out


__all__ = ["Items", "ORIGINS", "KINDS", "WHOSE", "LEARNS", "SCOPES",
           "STATUSES", "NEVER", "NEVER_UNITS", "LARGE_TOKENS", "unit_id_of",
           "norm_url", "is_url", "under"]

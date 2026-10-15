"""The user's connections, kept: people, their accounts and what they have
been doing - and the user's own changes to them, which are the lessons.

PEOPLE come from three places (the user's decision): the agent heartbeat's
step 2, reading what the user had in front of them (heartbeat/connections/
people.py); a search of the public web for one the user's work needs or one
long unseen (round.py); and the user, adding, editing and deleting them in
the Connections panel. Nothing is read from inside an account of the user's
- the agent never signs in to anything - and nothing is ever posted.

THE CONNECTION LESSONS are the user's own changes - each add, edit, delete
and merge, with when, and the reason if they gave one. Kept as they were
done, not summarised, up to `KEEP_ACTIONS`; past that the oldest are folded
into one summary by the model (round.py). They are given, dated, every time
the model adds or updates people: a person the user deleted a month ago is
not added back on a hunch - only on strong evidence that is newer, said why.

Everything here goes into the lessons package with the rest of the lessons
(heartbeat/lessons_package.py).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)

#: The relationships offered first. A person may have SEVERAL - a
#: colleague who is a friend too - the first the MAIN one, which the panel
#: groups them by; the user may write any other.
RELATIONSHIPS = ("family", "partner", "friend", "colleague", "manager", "report",
                 "mentor", "student", "classmate", "collaborator", "client",
                 "recruiter", "acquaintance", "other")

#: How close, 1 (barely) to 5 (closest) - grouped by in the panel.
CLOSENESS = {1: "barely", 2: "loosely", 3: "regularly", 4: "closely", 5: "closest"}

#: The user's own changes kept word for word; past it, the oldest are
#: summarised (round.py) - the user's decision.
KEEP_ACTIONS = 100

#: The fields of a person the user and the agent edit. A person has
#: RELATIONSHIPS and AFFILIATIONS - organisations, each with their role
#: there - the first of each the main one: "relationship", "org" and
#: "role" are always the main one's. Their job or PROFESSION is a field of
#: its own.
FIELDS = ("name", "relationship", "relationships", "closeness", "org", "role",
          "affiliations", "profession", "professions", "location", "locations", "summary")

#: The most relationships and affiliations one person keeps.
MOST_RELATIONSHIPS = 8
MOST_AFFILIATIONS = 8

#: THE FIELDS THAT ARE A LIST WITH A MAIN ONE: a person may have several
#: relationships, jobs and places, and the single field is always the first
#: of its list - "profession" the main of "professions".
LISTED = (("relationship", "relationships"), ("profession", "professions"),
          ("location", "locations"))
#: The most of each a person keeps.
MOST_LISTED = 8

#: A person's activity kept, newest first.
MOST_ACTIVITY = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS connection_people (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    aliases         TEXT NOT NULL DEFAULT '[]',
    relationship    TEXT NOT NULL DEFAULT '',
    closeness       INTEGER NOT NULL DEFAULT 0,
    org             TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL DEFAULT '',
    location        TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    profession      TEXT NOT NULL DEFAULT '',
    relationships   TEXT NOT NULL DEFAULT '[]',
    affiliations    TEXT NOT NULL DEFAULT '[]',
    professions     TEXT NOT NULL DEFAULT '[]',
    locations       TEXT NOT NULL DEFAULT '[]',
    tags            TEXT NOT NULL DEFAULT '[]',
    user_set        TEXT NOT NULL DEFAULT '[]',
    added_by        TEXT NOT NULL DEFAULT 'agent',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    checked_at      TEXT NOT NULL DEFAULT '',
    deleted_at      TEXT NOT NULL DEFAULT '',
    deleted_by      TEXT NOT NULL DEFAULT '',
    deleted_reason  TEXT NOT NULL DEFAULT '',
    avatar          TEXT NOT NULL DEFAULT '',
    avatar_from     TEXT NOT NULL DEFAULT '',
    avatar_tried_at TEXT NOT NULL DEFAULT '',
    is_self         INTEGER NOT NULL DEFAULT 0,
    field_times     TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS connection_accounts (
    id          TEXT PRIMARY KEY,
    person_id   TEXT NOT NULL,
    platform    TEXT NOT NULL DEFAULT '',
    handle      TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    found_at    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_connection_accounts_person ON connection_accounts(person_id);
CREATE TABLE IF NOT EXISTS connection_activity (
    id          TEXT PRIMARY KEY,
    person_id   TEXT NOT NULL,
    at          TEXT NOT NULL DEFAULT '',
    what        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    evidence    TEXT NOT NULL DEFAULT '[]',
    updated_at  TEXT NOT NULL,
    found_at    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_connection_activity_person ON connection_activity(person_id, at);
CREATE TABLE IF NOT EXISTS connection_actions (
    id          TEXT PRIMARY KEY,
    at          TEXT NOT NULL,
    by          TEXT NOT NULL,
    action      TEXT NOT NULL,
    person_id   TEXT NOT NULL DEFAULT '',
    person_name TEXT NOT NULL DEFAULT '',
    before      TEXT NOT NULL DEFAULT '{}',
    after       TEXT NOT NULL DEFAULT '{}',
    reason      TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_connection_actions_at ON connection_actions(by, at);
CREATE TABLE IF NOT EXISTS connection_sources (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    person_id   TEXT NOT NULL DEFAULT '',
    fetched_at  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connection_lessons (
    id          TEXT PRIMARY KEY,
    text        TEXT NOT NULL DEFAULT '',
    covers      INTEGER NOT NULL DEFAULT 0,
    until       TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connection_inbox (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL DEFAULT 'my_profile',
    person_id   TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    change      TEXT NOT NULL DEFAULT '{}',
    sources     TEXT NOT NULL DEFAULT '[]',
    found_at    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    decided_at  TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connection_names (
    kind        TEXT NOT NULL,
    key         TEXT NOT NULL,
    canonical   TEXT NOT NULL,
    by          TEXT NOT NULL DEFAULT 'agent',
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS connection_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connection_briefings (
    id          TEXT PRIMARY KEY,
    at          TEXT NOT NULL,
    needs       TEXT NOT NULL DEFAULT '[]',
    items       TEXT NOT NULL DEFAULT '[]',
    stats       TEXT NOT NULL DEFAULT '{}'
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _loads(text: Any, default: Any) -> Any:
    try:
        value = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        return default
    return default if value is None else value


def _text(value: Any, most: int) -> str:
    return " ".join(str(value or "").split())[:most]


def name_key(name: Any) -> str:
    """A name as compared: case, accents, spaces and punctuation set aside -
    "Tan, Mei" and "mei tan" are not made one here (that is the model's to
    judge), but "Mei  Tan" and "mei tan" are."""
    text = unicodedata.normalize("NFKC", str(name or "")).casefold()
    return re.sub(r"[\s\.\-_'’,·]+", "", text)


def url_key(url: Any) -> str:
    text = str(url or "").strip().lower()
    text = re.sub(r"^https?://(www\.)?", "", text)
    return text.split("?", 1)[0].split("#", 1)[0].rstrip("/")


#: Who the user is in their own profile: themselves, and nothing else.
MYSELF = "myself"


class ConnectionsStore:
    """The tables, and every change to them - each recorded as an action of
    the user's or the agent's."""

    def __init__(self, db: Any, folder: Any = None) -> None:
        self.db = db
        #: Where the pictures are kept (`avatars/`), in the application's
        #: protected state; None: no pictures (a store in a test, say).
        self.folder = Path(folder) if folder else None
        self._lock = threading.RLock()
        self.ensure()

    #: Columns added since the tables were first made, with their default.
    ADDED = {"connection_people": ("avatar", "avatar_from", "avatar_tried_at", "profession",
                                   "relationships", "affiliations", "professions",
                                   "locations"),
             "connection_accounts": ("found_at", "created_at"),
             "connection_activity": ("found_at", "created_at")}
    #: ... and those that are numbers.
    ADDED_NUMBERS = {"connection_people": ("is_self",)}

    def ensure(self) -> None:
        with self.db.write() as conn:
            conn.executescript(SCHEMA)
            for table, columns in self.ADDED.items():
                have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                for column in columns:
                    if column not in have:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT "
                                     "NOT NULL DEFAULT ''")
            for table, columns in self.ADDED_NUMBERS.items():
                have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                for column in columns:
                    if column not in have:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER "
                                     "NOT NULL DEFAULT 0")
            have = {row[1] for row in conn.execute("PRAGMA table_info(connection_people)")}
            if "field_times" not in have:
                conn.execute("ALTER TABLE connection_people ADD COLUMN field_times TEXT "
                             "NOT NULL DEFAULT '{}'")
            # Records from before their times were kept: written - and found -
            # when they were last changed, the nearest there is.
            for table in ("connection_accounts", "connection_activity"):
                conn.execute(f"UPDATE {table} SET created_at = updated_at WHERE created_at = ''")
                conn.execute(f"UPDATE {table} SET found_at = updated_at WHERE found_at = ''")
            # People from before a person had several relationships and
            # affiliations: the one each had is their main one.
            for row in conn.execute(
                    "SELECT id, relationship, org, role, relationships, affiliations FROM "
                    "connection_people WHERE relationships IN ('', '[]') OR affiliations "
                    "IN ('', '[]')").fetchall():
                rels = _loads(row[4], []) or ([row[1]] if row[1] else [])
                affs = _loads(row[5], []) or ([{"org": row[2], "role": row[3]}]
                                              if row[2] or row[3] else [])
                conn.execute("UPDATE connection_people SET relationships = ?, affiliations = ? "
                             "WHERE id = ?", (json.dumps(rels, ensure_ascii=False),
                                              json.dumps(affs, ensure_ascii=False), row[0]))
            # ... and from before they had several jobs and places: the one
            # each had is their main one.
            for single, many in (("profession", "professions"), ("location", "locations")):
                conn.execute(f"UPDATE connection_people SET {many} = json_array({single}) "
                             f"WHERE {many} IN ('', '[]') AND {single} != ''")
                conn.execute(f"UPDATE connection_people SET {many} = '[]' WHERE {many} = ''")
        self._sources_to_me()

    # -- reading -------------------------------------------------------------
    # -- one spelling for one thing --------------------------------------------
    def canonical(self, kind: str, value: Any) -> str:
        """The spelling kept for `value` - a profile's label ("label") or an
        organisation ("org"): one learned to mean the same (`merge_names`),
        a known other name of a platform, or one already in use that differs
        only in case, spaces or punctuation; else the value as given."""
        value = _text(value, 160)
        key = name_key(value)
        if not key:
            return value
        row = self.db.query_one("SELECT canonical FROM connection_names WHERE kind = ? "
                                "AND key = ?", (kind, key))
        if row is not None:
            return row["canonical"]
        if kind == "label" and key in LABEL_ALIASES:
            return LABEL_ALIASES[key]
        for one in self.names_in_use(kind):
            if name_key(one["name"]) == key:
                return one["name"]
        return value

    def _spelled(self, values: Dict[str, Any]) -> Dict[str, Any]:
        if values.get("org"):
            values["org"] = self.canonical("org", values["org"])
        if values.get("affiliations"):
            values["affiliations"] = _merged_affiliations(
                [dict(one, org=self.canonical("org", one["org"]) if one["org"] else "")
                 for one in values["affiliations"]], [])
        return values

    def _shaped(self, values: Dict[str, Any], person: Optional[Dict[str, Any]],
                by: str) -> Dict[str, Any]:
        """THE MAIN ONE AND THE REST: "relationship" is the first of
        "relationships", and "org" and "role" the first of "affiliations" -
        whichever was given. The user's lists are as they gave them; the
        agent's are added to what is known, its first made the main one -
        unless the user chose the main one themselves: then it stays, and
        what the agent found is added after it. One relationship, or an
        organisation and role, given alone changes the main one.

        THE USER'S CHOICE IS KEPT ONLY ON THEIR OWN PROFILE. On a contact's,
        what the agent found on the web may replace what the user wrote -
        it is newer than their memory of it (the user's decision,
        2026-10-13); their change is still in the history."""
        mine = set((person or {}).get("user_set") or []) \
            if (person or {}).get("is_self") else set()
        # RELATIONSHIPS, JOBS AND PLACES: a list, its first the main one.
        for single, many in LISTED:
            have = list((person or {}).get(many) or [])
            if many not in values and single not in values:
                continue
            if many in values:
                new = list(values[many])
            else:
                one = values[single]
                new = ([one] if one else []) + [
                    r for r in have if r not in (one, (person or {}).get(single))]
                if by != "user":
                    new = [one] if one else []
            if by != "user" and person is not None:
                new = (_merged(have, new) if mine & {single, many}
                       else _merged(new, have))
            values[many] = new[:MOST_LISTED]
            values[single] = new[0] if new else ""
        have_affs = [dict(one) for one in (person or {}).get("affiliations") or []]
        if "affiliations" in values or "org" in values or "role" in values:
            if "affiliations" in values:
                new_affs = list(values["affiliations"])
                if by != "user" and person is not None:
                    new_affs = (_merged_affiliations(have_affs, new_affs)
                                if mine & {"org", "role", "affiliations"}
                                else _merged_affiliations(new_affs, have_affs))
            else:
                main = dict(have_affs[0]) if have_affs else {"org": "", "role": ""}
                agent = by != "user" and person is not None
                other_org = ("org" in values
                             and name_key(values["org"]) != name_key(main["org"]))
                if agent and other_org and main["org"] and mine & {"org", "affiliations"}:
                    # The main organisation is the user's: another the agent
                    # found goes after it.
                    new_affs = _merged_affiliations(
                        have_affs, [{"org": values["org"], "role": values.get("role", "")}])
                else:
                    if other_org:
                        # Another organisation: the role there is the one given.
                        main = {"org": values["org"], "role": values.get("role", "")}
                    elif "org" in values:
                        main["org"] = values["org"]
                    if "role" in values and not (agent and mine & {"role", "affiliations"}):
                        main["role"] = values["role"]
                    rest = [one for one in have_affs[1:]
                            if name_key(one["org"]) != name_key(main["org"]) or not main["org"]]
                    new_affs = ([main] if main["org"] or main["role"] else []) + rest
            new_affs = new_affs[:MOST_AFFILIATIONS]
            values["affiliations"] = new_affs
            values["org"] = new_affs[0]["org"] if new_affs else ""
            values["role"] = new_affs[0]["role"] if new_affs else ""
        return values

    def names_in_use(self, kind: str) -> List[Dict[str, Any]]:
        """Each label of a profile ("label"), or each organisation ("org"),
        in use - with how many use it and how many of those the user set -
        most used first. Labels also offer the known platforms, unused."""
        if kind == "label":
            rows = self.db.query(
                "SELECT a.platform AS name, COUNT(*) AS n, SUM(CASE WHEN a.source = "
                "'added by you' THEN 1 ELSE 0 END) AS u FROM connection_accounts a "
                "JOIN connection_people p ON p.id = a.person_id WHERE p.deleted_at = '' "
                "AND a.platform != '' GROUP BY a.platform")
            out = [{"name": row["name"], "count": row["n"], "user": int(row["u"] or 0)}
                   for row in rows]
        else:
            out = []
            for row in self.db.query("SELECT org, affiliations, user_set FROM connection_people "
                                     "WHERE deleted_at = ''"):
                mine = bool({"org", "affiliations"} & set(_loads(row["user_set"], [])))
                orgs = [one.get("org") for one in _loads(row["affiliations"], [])
                        if isinstance(one, dict) and one.get("org")] or (
                            [row["org"]] if row["org"] else [])
                for org in dict.fromkeys(orgs):
                    hit = next((one for one in out if one["name"] == org), None)
                    if hit is None:
                        out.append({"name": org, "count": 1, "user": int(mine)})
                    else:
                        hit["count"] += 1
                        hit["user"] += int(mine)
        out.sort(key=lambda one: (-one["count"], one["name"].casefold()))
        if kind == "label":
            have = {name_key(one["name"]) for one in out}
            out += [{"name": name, "count": 0, "user": 0} for _domain, name in PLATFORMS
                    if name_key(name) not in have and not have.add(name_key(name))]
        return out

    def merge_names(self, kind: str, canonical: str, same: Iterable[str],
                    by: str = "agent") -> int:
        """`same` mean what `canonical` means: each is written as it from now
        on - learned, so a later one is too - and the profiles or people that
        use it are changed to it. How many records changed."""
        canonical = _text(canonical, 160)
        if kind not in ("label", "org") or not canonical:
            raise ValueError("kind is label or org, and the name kept is needed")
        changed = 0
        at = now()
        with self._lock:
            for one in same:
                one = _text(one, 160)
                if not one or one == canonical:
                    continue
                self.db.execute(
                    "INSERT INTO connection_names (kind, key, canonical, by, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(kind, key) DO UPDATE SET "
                    "canonical = excluded.canonical, by = excluded.by, "
                    "updated_at = excluded.updated_at", (kind, name_key(one), canonical, by, at))
                if kind == "label":
                    for row in self.db.query("SELECT id FROM connection_accounts WHERE "
                                             "platform = ?", (one,)):
                        self.db.execute("UPDATE connection_accounts SET platform = ?, "
                                        "updated_at = ? WHERE id = ?", (canonical, at, row["id"]))
                        changed += 1
                else:
                    for row in self.db.query("SELECT id, org, affiliations FROM "
                                             "connection_people"):
                        affs = [dict(a, org=canonical) if isinstance(a, dict)
                                and a.get("org") == one else a
                                for a in _loads(row["affiliations"], [])]
                        if row["org"] != one and affs == _loads(row["affiliations"], []):
                            continue
                        self.db.execute(
                            "UPDATE connection_people SET org = ?, affiliations = ?, "
                            "updated_at = ? WHERE id = ?",
                            (canonical if row["org"] == one else row["org"],
                             json.dumps(_merged_affiliations(affs, []), ensure_ascii=False),
                             at, row["id"]))
                        changed += 1
        return changed

    def relationships_in_use(self) -> List[str]:
        """The relationships offered: the usual ones, then any other the
        user or the agent wrote, most used first."""
        counts: Dict[str, int] = {}
        for row in self.db.query("SELECT relationships FROM connection_people WHERE "
                                 "deleted_at = ''"):
            for one in _loads(row["relationships"], []):
                if isinstance(one, str) and one and one not in RELATIONSHIPS:
                    counts[one] = counts.get(one, 0) + 1
        return [*RELATIONSHIPS, *sorted(counts, key=lambda one: (-counts[one], one))]

    def meta(self, key: str, value: Optional[str] = None) -> str:
        """A little state of the connections' own: read, or written."""
        if value is not None:
            self.db.execute("INSERT INTO connection_meta (key, value, updated_at) VALUES "
                            "(?, ?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                            "updated_at = excluded.updated_at", (key, value, now()))
            return value
        row = self.db.query_one("SELECT value FROM connection_meta WHERE key = ?", (key,))
        return row["value"] if row is not None else ""

    # -- the inbox: what the user is asked to check ------------------------------
    #: The user's own details the agent may suggest; never changed without them.
    MY_FIELDS = ("name", "org", "role", "location", "profession")

    def propose_for_me(self, change: Dict[str, Any], why: str = "",
                       sources: Iterable[str] = (), found_at: str = "") -> Optional[Dict[str, Any]]:
        """A change to THE USER'S OWN profile details, found on a public page
        - their name, organisation, role or place, or a profile of theirs to
        add or one to take off. Never made: put in their inbox, to accept or
        dismiss. None when there is nothing to ask - it already says so, the
        same is waiting, or they dismissed the same before."""
        me = self.me()
        field = str(change.get("field") or "").strip()
        if field in self.MY_FIELDS:
            value = _text(change.get("value"), 200)
            if not value or name_key(value) == name_key(me.get(field)):
                return None
            clean = {"field": field, "value": value, "before": me.get(field) or ""}
            label = {"name": "Your name", "org": "Your organisation", "role": "Your role",
                     "location": "Where you are", "profession": "Your profession"}[field]
            title = f"{label}: {clean['before'] or '(none)'} \u2192 {value}"
        elif field == "affiliation":
            org, role = _text(change.get("org"), 160), _text(change.get("role"), 160)
            if not org:
                return None
            main = (me.get("affiliations") or [{}])[0]
            if name_key(main.get("org")) == name_key(org) and (
                    not role or name_key(main.get("role")) == name_key(role)):
                return None
            clean = {"field": field, "org": org, "role": role,
                     "before": " at ".join(filter(None, [main.get("role"), main.get("org")]))}
            title = (f"Your main organisation: {clean['before'] or '(none)'} \u2192 "
                     + " at ".join(filter(None, [role, org])))
        elif field == "add_profile":
            url = _text(change.get("url"), 500)
            if not re.match(r"^https?://", url, re.I):
                return None
            if any(url_key(one["url"]) == url_key(url) for one in me["accounts"]):
                return None
            clean = {"field": field, "url": url,
                     "platform": _text(change.get("platform"), 40) or platform_of(url)}
            title = f"A profile of yours to add: {clean['platform']} {url}"
        elif field == "remove_profile":
            url = url_key(change.get("url"))
            account = next((one for one in me["accounts"] if url and url_key(one["url"]) == url),
                           None)
            if account is None:
                return None
            clean = {"field": field, "url": account["url"], "account_id": account["id"]}
            title = f"A profile to take off yours: {account['url']}"
        else:
            raise ValueError("field is name, org, role, location, profession, affiliation, "
                             "add_profile or remove_profile")
        same = json.dumps({k: v for k, v in clean.items() if k not in ("before", "account_id")},
                          sort_keys=True)
        for row in self.db.query("SELECT * FROM connection_inbox WHERE status IN "
                                 "('open', 'dismissed')"):
            had = _loads(row["change"], {})
            if json.dumps({k: v for k, v in had.items() if k not in ("before", "account_id")},
                          sort_keys=True) == same:
                return None
        at = now()
        ident = new_id("inbox")
        self.db.execute(
            "INSERT INTO connection_inbox (id, kind, person_id, title, detail, change, sources, "
            "found_at, created_at, updated_at) VALUES (?, 'my_profile', ?, ?, ?, ?, ?, ?, ?, ?)",
            (ident, me["id"], title, _text(why, 800), json.dumps(clean, ensure_ascii=False),
             json.dumps([_text(one, 500) for one in sources if str(one).strip()][:6]),
             found_at or at, at, at))
        return self.inbox_item(ident)

    def inbox_item(self, item_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM connection_inbox WHERE id = ?", (str(item_id),))
        if row is None:
            return None
        item = dict(row)
        item["change"] = _loads(item["change"], {})
        item["sources"] = _loads(item["sources"], [])
        return item

    def inbox(self, status: str = "open", most: int = 50) -> List[Dict[str, Any]]:
        """The inbox: what waits for the user ("open"), or what they decided
        ("decided"), newest first."""
        where = "status = 'open'" if status == "open" else "status != 'open'"
        order = "created_at DESC" if status == "open" else "decided_at DESC"
        return [self.inbox_item(row["id"]) for row in self.db.query(
            f"SELECT id FROM connection_inbox WHERE {where} ORDER BY {order}, rowid DESC "
            "LIMIT ?", (int(most),))]

    def decide(self, item_id: str, accept: bool, reason: str = "") -> Dict[str, Any]:
        """The user's answer to one inbox item: accepted, the change is made
        - as theirs, one of their lessons; dismissed, it is not asked again."""
        with self._lock:
            item = self.inbox_item(item_id)
            if item is None:
                raise KeyError("no such item")
            if item["status"] != "open":
                raise ValueError("already decided")
            change = item["change"]
            said = reason or "found by the agent; checked by you"
            if accept:
                field = change.get("field")
                if field in self.MY_FIELDS:
                    self.edit(item["person_id"], by="user", reason=said,
                              found_at=item["found_at"], **{field: change.get("value")})
                elif field == "affiliation":
                    # Their main one now; the others kept after it.
                    have = (self.get(item["person_id"]) or {}).get("affiliations") or []
                    self.edit(item["person_id"], by="user", reason=said,
                              found_at=item["found_at"], affiliations=[
                                  {"org": change.get("org") or "", "role": change.get("role") or ""},
                                  *[one for one in have if name_key(one.get("org"))
                                    != name_key(change.get("org"))]])
                elif field == "add_profile":
                    self.add_profile(item["person_id"], change.get("url") or "",
                                     change.get("platform") or "", by="user", reason=said)
                elif field == "remove_profile":
                    try:
                        self.remove_profile(item["person_id"], change.get("account_id") or "",
                                            by="user", reason=said)
                    except KeyError:
                        pass              # taken off already
            at = now()
            self.db.execute("UPDATE connection_inbox SET status = ?, decided_at = ?, reason = ?, "
                            "updated_at = ? WHERE id = ?",
                            ("accepted" if accept else "dismissed", at, _text(reason, 500), at,
                             item["id"]))
            return self.inbox_item(item["id"])

    # -- the user themselves -------------------------------------------------
    def me(self) -> Dict[str, Any]:
        """THE USER, as a person of their own: their profile links, picture
        and what they have been doing - looked up as a contact is, and never
        listed among the contacts. Made the first time it is asked for."""
        with self._lock:
            row = self.db.query_one("SELECT * FROM connection_people WHERE is_self = 1 "
                                    "ORDER BY created_at LIMIT 1")
            if row is None:
                at = now()
                self.db.execute(
                    "INSERT INTO connection_people (id, name, relationship, added_by, "
                    "created_at, updated_at, is_self) VALUES (?, 'You', ?, 'user', ?, ?, 1)",
                    (new_id("person"), MYSELF, at, at))
                row = self.db.query_one("SELECT * FROM connection_people WHERE is_self = 1 "
                                        "ORDER BY created_at LIMIT 1")
            # ONLY ONE IS THE USER. A second - from a lessons package made on
            # another computer, say - is folded into the first: its profiles
            # and activity moved over, its details filling empty ones.
            for extra in self.db.query("SELECT * FROM connection_people WHERE is_self = 1 "
                                       "AND id != ?", (row["id"],)):
                for table in ("connection_accounts", "connection_activity", "connection_inbox"):
                    self.db.execute(f"UPDATE {table} SET person_id = ? WHERE person_id = ?",
                                    (row["id"], extra["id"]))
                kept = self._person(row)
                other = self._person(extra)
                fill = {key: other[key] for key in self.MY_FIELDS
                        if other[key] and (not kept[key] or (key == "name"
                                                              and kept[key] == "You"))}
                if fill:
                    times = dict(kept["field_times"])
                    times.update({key: other["field_times"][key] for key in fill
                                  if key in other["field_times"]})
                    self.db.execute(
                        f"UPDATE connection_people SET {', '.join(f'{k} = ?' for k in fill)}, "
                        "field_times = ? WHERE id = ?",
                        (*fill.values(), json.dumps(times), row["id"]))
                self.db.execute("DELETE FROM connection_people WHERE id = ?", (extra["id"],))
                row = self.db.query_one("SELECT * FROM connection_people WHERE id = ?",
                                        (row["id"],))
            return self.get(row["id"], with_details=True)

    def _sources_to_me(self) -> None:
        """The public pages the user gave before there was a profile of
        their own. One given for a contact is that contact's profile now. One
        given for nobody may be theirs or someone else's - the box never
        asked - so it is NOT made theirs: it is put in their inbox, to accept
        as theirs, or dismiss and add on the person it belongs to."""
        rows = self.db.query("SELECT * FROM connection_sources")
        for row in rows:
            owner = self.get(row["person_id"]) if row["person_id"] else None
            if owner is not None and not owner["is_self"]:
                self.add_account(owner["id"], platform_of(row["url"]), "", row["url"],
                                 source=row["label"] or "added by you",
                                 found_at=row["updated_at"])
            elif re.match(r"^https?://", str(row["url"] or ""), re.I):
                self.propose_for_me(
                    {"field": "add_profile", "url": row["url"]},
                    "You added this page before"
                    + (f" (as \u201c{row['label']}\u201d)" if row["label"] else "")
                    + ", when a page was not marked as yours or someone else's. Accept it "
                    "if it is yours; if it is someone else's, dismiss it and add it on their "
                    "page.", [row["url"]], found_at=row["updated_at"])
            self.db.execute("DELETE FROM connection_sources WHERE id = ?", (row["id"],))

    def _person(self, row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        person = dict(row)
        person["is_self"] = bool(person.get("is_self"))
        if person["is_self"]:
            person["relationship"], person["relationships"] = MYSELF, [MYSELF]
        for name in ("aliases", "tags", "user_set", "relationships", "affiliations",
                     "professions", "locations"):
            person[name] = _loads(person.get(name), [])
        # From before the lists were kept: the one there was.
        for single, many in LISTED:
            if not person[many] and person.get(single):
                person[many] = [person[single]]
        if not person["affiliations"] and (person.get("org") or person.get("role")):
            person["affiliations"] = [{"org": person.get("org") or "",
                                       "role": person.get("role") or ""}]
        person["profession"] = person.get("profession") or ""
        # Each field's own times: when what it says was found (seen, or seen
        # again), when it was last changed, and by what.
        person["field_times"] = _loads(person.get("field_times"), {})
        person["closeness"] = int(person.get("closeness") or 0)
        person["deleted"] = bool(person.get("deleted_at"))
        return person

    def get(self, person_id: str, with_details: bool = False) -> Optional[Dict[str, Any]]:
        person = self._person(self.db.query_one(
            "SELECT * FROM connection_people WHERE id = ?", (str(person_id),)))
        if person is not None and with_details:
            person["accounts"] = self.accounts(person["id"])
            person["activity"] = self.activity(person["id"], 50)
            person["actions"] = [self._action(row) for row in self.db.query(
                "SELECT * FROM connection_actions WHERE person_id = ? ORDER BY at DESC, rowid DESC "
                "LIMIT 30", (person["id"],))]
        return person

    def people(self, include_deleted: bool = False,
               include_self: bool = False) -> List[Dict[str, Any]]:
        """The contacts - the user themselves not among them unless asked
        (`me`)."""
        where = [] if include_deleted else ["deleted_at = ''"]
        if not include_self:
            where.append("is_self = 0")
        rows = self.db.query("SELECT * FROM connection_people "
                             + (f"WHERE {' AND '.join(where)} " if where else "")
                             + "ORDER BY name COLLATE NOCASE")
        out = [self._person(row) for row in rows]
        last = {row["person_id"]: row["at"] for row in self.db.query(
            "SELECT person_id, MAX(at) AS at FROM connection_activity GROUP BY person_id")}
        for person in out:
            person["last_activity_at"] = last.get(person["id"], "")
        return out

    def accounts(self, person_id: str) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.db.query(
            "SELECT * FROM connection_accounts WHERE person_id = ? ORDER BY platform",
            (person_id,))]

    def activity(self, person_id: str, most: int = 20) -> List[Dict[str, Any]]:
        out = []
        for row in self.db.query("SELECT * FROM connection_activity WHERE person_id = ? "
                                 "ORDER BY at DESC, updated_at DESC LIMIT ?",
                                 (person_id, int(most))):
            item = dict(row)
            item["evidence"] = _loads(item.get("evidence"), [])
            out.append(item)
        return out

    def search(self, query: str = "", relationship: str = "", tag: str = "",
               most: int = 20) -> List[Dict[str, Any]]:
        """People whose name, aliases, relationship, organisation, role,
        place, summary, tags or recent activity hold every word of `query`."""
        words = [name_key(one) for one in str(query or "").split() if name_key(one)]
        found = []
        for person in self.people():
            if relationship and name_key(relationship) not in {
                    name_key(one) for one in person["relationships"]}:
                continue
            if tag and name_key(tag) not in {name_key(one) for one in person["tags"]}:
                continue
            if words:
                said = " ".join([person["name"], *person["aliases"], *person["relationships"],
                                 *[f"{one.get('org', '')} {one.get('role', '')}"
                                   for one in person["affiliations"]],
                                 *person["professions"], *person["locations"],
                                 person["summary"], *person["tags"],
                                 *[one["what"] for one in self.activity(person["id"], 10)]])
                hay = name_key(said)
                if not all(word in hay for word in words):
                    continue
            found.append(person)
        found.sort(key=lambda one: (one["closeness"] or 0, one["last_activity_at"] or ""),
                   reverse=True)
        return found[:most]

    def find(self, name: str = "", accounts: Iterable[Dict[str, Any]] = (),
             include_deleted: bool = True) -> Optional[Dict[str, Any]]:
        """The person an account (by its address or handle) or a name is -
        exactly; None when none is."""
        for account in accounts or []:
            url = url_key(account.get("url"))
            handle = name_key(account.get("handle"))
            platform = name_key(account.get("platform"))
            for row in self.db.query("SELECT * FROM connection_accounts"):
                if (url and url_key(row["url"]) == url) or (
                        handle and platform and name_key(row["handle"]) == handle
                        and name_key(row["platform"]) == platform):
                    person = self.get(row["person_id"])
                    if person and (include_deleted or not person["deleted"]):
                        return person
        key = name_key(name)
        if not key:
            return None
        for person in self.people(include_deleted=include_deleted):
            if key in {name_key(person["name"]), *[name_key(one) for one in person["aliases"]]}:
                return person
        return None

    # -- the actions ---------------------------------------------------------
    def _action(self, row: Dict[str, Any]) -> Dict[str, Any]:
        action = dict(row)
        action["before"] = _loads(action.get("before"), {})
        action["after"] = _loads(action.get("after"), {})
        return action

    #: THE LESSONS ENGINE (heartbeat/lessons/engine.py), set by the
    #: application: each change the user makes to a contact is a piece of
    #: its evidence - their own profile not among them.
    lessons: Any = None

    def evidence_text(self, one: Dict[str, Any]) -> str:
        """One of the user's changes to their contacts, as evidence: what
        they did, to whom, from what to what, and why."""
        lines = [f"The user {self.DID.get(one['action'], one['action'])} "
                 f"{one.get('person_name') or 'a contact'}."]
        if one["action"] == "edit":
            for key, value in (one.get("after") or {}).items():
                lines.append(f"- {key}: {_shown((one.get('before') or {}).get(key))!r} -> "
                             f"{_shown(value)!r}")
        elif one.get("after"):
            lines.append(f"- {json.dumps(one['after'], ensure_ascii=False, default=str)[:400]}")
        if one.get("reason"):
            lines.append(f"The user said: {one['reason']}")
        return "\n".join(lines)[:2000]

    def _record(self, by: str, action: str, person: Optional[Dict[str, Any]],
                before: Dict[str, Any], after: Dict[str, Any], reason: str = "") -> str:
        ident = new_id("cact")
        at = now()
        self.db.execute(
            "INSERT INTO connection_actions (id, at, by, action, person_id, person_name, "
            "before, after, reason, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ident, at, by, action, (person or {}).get("id", ""),
             (person or {}).get("name", "") or after.get("name", "") or before.get("name", ""),
             json.dumps(before, ensure_ascii=False, default=str),
             json.dumps(after, ensure_ascii=False, default=str), _text(reason, 1000), at))
        if by == "user" and self.lessons is not None and not (person or {}).get("is_self"):
            try:
                one = {"id": ident, "action": action, "at": at, "reason": _text(reason, 1000),
                       "person_name": (person or {}).get("name", "") or after.get("name", "")
                       or before.get("name", ""), "before": before, "after": after}
                self.lessons.record("connections", ident, self.evidence_text(one),
                                    action=action, reason=one["reason"])
            except Exception:             # pragma: no cover - the change stands
                log.warning("the change could not be learned from", exc_info=True)
        return ident

    #: THE USER'S OWN PROFILE IS NOT A LESSON: a change to it - found by
    #: the agent and checked by them, or made by them - says nothing of how
    #: they deal with their contacts. Kept, never listed or given.
    NOT_SELF = "person_id NOT IN (SELECT id FROM connection_people WHERE is_self = 1)"

    def actions(self, by: str = "user", most: int = KEEP_ACTIONS) -> List[Dict[str, Any]]:
        """The changes made by `by`, newest first - none to the user's own
        profile."""
        return [self._action(row) for row in self.db.query(
            f"SELECT * FROM connection_actions WHERE by = ? AND {self.NOT_SELF} "
            "ORDER BY at DESC, rowid DESC LIMIT ?", (by, int(most)))]

    def lessons_summary(self) -> Dict[str, Any]:
        row = self.db.query_one("SELECT * FROM connection_lessons WHERE id = 'summary'")
        return dict(row) if row else {"text": "", "covers": 0, "until": ""}

    def lessons_text(self) -> str:
        """WHAT THE AGENT IS GIVEN of the user's connection lessons: those
        the lessons engine has learned from their changes - never the
        changes themselves. Without an engine, the changes, dated: the
        summary of the older ones, then each kept one, newest first."""
        if self.lessons is not None:
            return self.lessons.text("connections") or "None learned yet."
        return self.changes_text()

    def changes_text(self) -> str:
        """The user's own changes to their contacts, dated - the history, as
        it is shown; the lessons are learned from these, not given them."""
        summary = self.lessons_summary()
        kept = self.actions("user", KEEP_ACTIONS)
        if not kept and not summary.get("text"):
            return "None yet: the user has not changed any contact."
        lines = []
        if summary.get("text"):
            lines.append(f"Earlier ({summary.get('covers', 0)} changes, up to "
                         f"{str(summary.get('until') or '')[:10]}), summarised:\n"
                         f"{summary['text']}")
        for one in kept:
            lines.append(f"- {str(one['at'])[:16].replace('T', ' ')} UTC  {one['action']}  "
                         f"{one['person_name']} [{one['person_id']}]"
                         + (f"  {_change_said(one['before'], one['after'])}"
                            if one["action"] == "edit" else "")
                         + (f"  {_move_said(one['before'], one['after'])}"
                            if one["action"] == "move an account" else "")
                         + (f"  {_account_said((one['after'] or one['before']).get('account'))}"
                            if one["action"] in ("add a profile", "remove a profile") else "")
                         + (f"  - the user's reason: {one['reason']}" if one["reason"] else ""))
        return "\n".join(lines)

    #: How each change is said in the Lessons panel.
    DID = {"add": "added", "edit": "edited", "delete": "deleted", "add back": "added back",
           "merge": "merged", "move an account": "moved an account",
           "add a profile": "added a profile", "remove a profile": "removed a profile",
           "add a profile page": "added a page", "remove a profile page": "removed a page"}

    def describe_lessons(self) -> Dict[str, Any]:
        """The connection lessons as the Lessons panel shows each kind: every
        change the user made, newest first - when, what, to whom, from what
        to what, and their reason - and the summary of the older ones. Not
        read by the model into lessons of its own: given as they are."""
        summary = self.lessons_summary()
        kept = self.actions("user", KEEP_ACTIONS)
        deleted = {person["id"] for person in self.people(include_deleted=True)
                   if person["deleted"]}
        lessons = []
        for one in kept:
            changes = []
            if one["action"] == "edit":
                changes = [{"field": key, "before": _shown(one["before"].get(key)),
                            "after": _shown(value)} for key, value in one["after"].items()]
            elif one["action"] == "move an account":
                changes = [{"field": "account", "before": one["before"].get("from") or "",
                            "after": one["after"].get("to") or ""}]
            elif one["action"] == "merge":
                changes = [{"field": "merged", "before": (one["before"].get("merged") or {})
                            .get("name", ""), "after": one["after"].get("into") or ""}]
            title = one["person_name"] or str(one["after"].get("url")
                                              or one["before"].get("url") or "")
            lessons.append({
                "id": one["id"], "acted_at": one["at"], "action": one["action"].replace(" ", "-"),
                "did": self.DID.get(one["action"], one["action"]), "title": title,
                "person_id": one["person_id"],
                "event": {"user_said": one["reason"], "changes": changes},
                "deleted_now": one["action"] == "delete" and one["person_id"] in deleted})
        out = {"kind": "connections", "title": "Connection lessons",
               "actions": len(kept) + int(summary.get("covers") or 0), "waiting": 0,
               "kept": len(kept), "most": KEEP_ACTIONS, "summary": summary,
               "lessons": lessons}
        if self.lessons is not None:
            # What the engine learned from these changes, and how each was read.
            learned = self.lessons.describe("connections")
            read = learned.pop("read")
            for one in lessons:
                one["read"] = read.get(one["id"]) or []
            out.update(engine=learned, waiting=learned["waiting"], summary={})
        return out

    def forget_action(self, action_id: str) -> bool:
        """One of the user's changes taken out of the lessons: no longer
        given to the model. The change itself stands."""
        row = self.db.query_one("SELECT id FROM connection_actions WHERE id = ? AND by = 'user' "
                                f"AND {self.NOT_SELF}", (str(action_id),))
        if row is None:
            return False
        self.db.execute("DELETE FROM connection_actions WHERE id = ?", (row["id"],))
        if self.lessons is not None:
            self.lessons.forget_source("connections", row["id"])
        return True

    def overflowing_actions(self) -> List[Dict[str, Any]]:
        """The user's changes past the most kept word for word - oldest
        first - to be summarised."""
        return [self._action(row) for row in self.db.query(
            f"SELECT * FROM connection_actions WHERE by = 'user' AND {self.NOT_SELF} "
            "ORDER BY at DESC, rowid DESC "
            "LIMIT -1 OFFSET ?", (KEEP_ACTIONS,))][::-1]

    def fold_actions(self, folded: List[Dict[str, Any]], text: str) -> None:
        """The summary of the older lessons is now `text`, and the ones it
        covers go - kept only in it."""
        if not folded:
            return
        summary = self.lessons_summary()
        covers = int(summary.get("covers") or 0) + len(folded)
        until = max(str(one["at"]) for one in folded)
        self.db.execute(
            "INSERT INTO connection_lessons (id, text, covers, until, updated_at) "
            "VALUES ('summary', ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "text = excluded.text, covers = excluded.covers, until = excluded.until, "
            "updated_at = excluded.updated_at",
            (_text(text, 6000), covers, until, now()))
        for one in folded:
            self.db.execute("DELETE FROM connection_actions WHERE id = ?", (one["id"],))

    # -- changing ------------------------------------------------------------
    def add(self, name: str, by: str = "user", reason: str = "", found_at: str = "",
            **fields: Any) -> Dict[str, Any]:
        """A person added - by the user, or by the agent."""
        name = _text(name, 120)
        if not name:
            raise ValueError("a person needs a name")
        with self._lock:
            ident = new_id("person")
            at = now()
            # What the user GAVE is theirs - not what follows from it (an
            # organisation given alone does not make its empty role theirs).
            given = set(self._clean(fields))
            values = self._shaped(self._spelled(self._clean(fields)), None, by)
            user_set = sorted(given) if by == "user" else []
            found = found_at or at
            times = {key: {"found_at": found, "updated_at": at, "by": by}
                     for key in ["name", *values] if key not in ("aliases", "tags")}
            self.db.execute(
                "INSERT INTO connection_people (id, name, aliases, relationship, relationships, "
                "closeness, org, role, affiliations, profession, location, summary, tags, "
                "user_set, added_by, created_at, updated_at, field_times, professions, "
                "locations) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ident, name, json.dumps(values.get("aliases", []), ensure_ascii=False),
                 values.get("relationship", ""),
                 json.dumps(values.get("relationships", []), ensure_ascii=False),
                 values.get("closeness", 0), values.get("org", ""), values.get("role", ""),
                 json.dumps(values.get("affiliations", []), ensure_ascii=False),
                 values.get("profession", ""), values.get("location", ""),
                 values.get("summary", ""), json.dumps(values.get("tags", []), ensure_ascii=False),
                 json.dumps(user_set), by, at, at, json.dumps(times),
                 json.dumps(values.get("professions", []), ensure_ascii=False),
                 json.dumps(values.get("locations", []), ensure_ascii=False)))
            person = self.get(ident)
            self._record(by, "add", person, {}, self._said(person), reason)
            return person

    def edit(self, person_id: str, by: str = "user", reason: str = "", found_at: str = "",
             **fields: Any) -> Dict[str, Any]:
        """A person's fields changed. On the user's own profile, what they
        set the agent leaves as it is (`user_set`); on a contact's, the agent
        may overwrite it with what it found (`_shaped`)."""
        with self._lock:
            person = self.get(person_id)
            if person is None:
                raise KeyError("no such person")
            if person["is_self"]:
                # Who the user is to themselves is not theirs or the agent's
                # to change.
                fields = {key: value for key, value in fields.items()
                          if key not in ("relationship", "relationships")}
            given = set(self._clean(fields))
            values = self._shaped(self._spelled(self._clean(fields)), person, by)
            if by != "user":
                # On their own profile, the lists the agent only adds to
                # (`_shaped`); the main one the user chose stays. A contact's
                # the agent may correct.
                guarded = person["user_set"] if person["is_self"] else []
                values = {key: value for key, value in values.items()
                          if key not in guarded
                          or key in ("tags", "aliases", "relationships", "affiliations",
                                     "professions", "locations")}
                if "tags" in values:
                    values["tags"] = _merged(person["tags"], values["tags"])
                if "aliases" in values:
                    values["aliases"] = _merged(person["aliases"], values["aliases"])
            changed = {key: value for key, value in values.items() if person.get(key) != value}
            # What is said again as it is was FOUND again: it is still so as
            # of now - its found time moves, its changed time does not.
            at = now()
            found = found_at or at
            times = dict(person["field_times"])
            for key in values:
                if key in ("aliases", "tags"):
                    continue
                one = dict(times.get(key) or {})
                one["found_at"] = max(str(one.get("found_at") or ""), found)
                if key in changed:
                    one.update(updated_at=at, by=by)
                times[key] = one
            if not changed:
                if times != person["field_times"]:
                    self.db.execute("UPDATE connection_people SET field_times = ? WHERE id = ?",
                                    (json.dumps(times), person_id))
                return self.get(person_id)
            before = {key: person.get(key) for key in changed}
            user_set = person["user_set"]
            if by == "user":
                user_set = sorted(set(user_set) | (set(changed) & given))
            sets = [f"{key} = ?" for key in changed] + ["user_set = ?", "updated_at = ?",
                                                        "field_times = ?"]
            params = [json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                      for value in changed.values()]
            self.db.execute(f"UPDATE connection_people SET {', '.join(sets)} WHERE id = ?",
                            (*params, json.dumps(user_set), at, json.dumps(times), person_id))
            # Recorded as it was asked for: an organisation given, as the
            # organisation - not again as the list it changed (`_shaped`).
            asked = {key: value for key, value in changed.items() if key in given} or changed
            self._record(by, "edit", person, {key: before[key] for key in asked}, asked,
                         reason)
            return self.get(person_id)

    def delete(self, person_id: str, by: str = "user", reason: str = "") -> Dict[str, Any]:
        """Taken off the list - kept, marked deleted, so that the agent knows
        not to add them back without strong new evidence."""
        with self._lock:
            person = self.get(person_id)
            if person is None:
                raise KeyError("no such person")
            if person["is_self"]:
                raise ValueError("your own profile cannot be deleted")
            if person["deleted"]:
                return person
            at = now()
            self.db.execute("UPDATE connection_people SET deleted_at = ?, deleted_by = ?, "
                            "deleted_reason = ?, updated_at = ? WHERE id = ?",
                            (at, by, _text(reason, 1000), at, person_id))
            self._record(by, "delete", person, self._said(person), {}, reason)
            return self.get(person_id)

    def restore(self, person_id: str, by: str = "user", reason: str = "") -> Dict[str, Any]:
        """Added back - by the user, or by the agent on strong new evidence."""
        with self._lock:
            person = self.get(person_id)
            if person is None:
                raise KeyError("no such person")
            if not person["deleted"]:
                return person
            self.db.execute("UPDATE connection_people SET deleted_at = '', deleted_by = '', "
                            "deleted_reason = '', updated_at = ? WHERE id = ?",
                            (now(), person_id))
            restored = self.get(person_id)
            self._record(by, "add back", restored, {"deleted_at": person["deleted_at"]},
                         self._said(restored), reason)
            return restored

    def merge(self, keep_id: str, other_id: str, by: str = "user",
              reason: str = "") -> Dict[str, Any]:
        """Two entries that are one person: the other's accounts, activity,
        aliases and tags moved to the one kept, what the kept one has empty
        filled from the other (organisation, role, place, relationship,
        closeness, summary), and the other gone. The page shows all of it
        before it is done (`merge_preview`)."""
        with self._lock:
            keep, other = self.get(keep_id), self.get(other_id)
            if keep is None or other is None or keep_id == other_id:
                raise KeyError("no such pair of people")
            if other["is_self"]:
                raise ValueError("your own profile cannot be merged into another")
            preview = self.merge_preview(keep_id, other_id)
            filled = shown = preview["filled"]
            # The main ones stay this person's; the other's are added after.
            rels = _merged(keep["relationships"], other["relationships"])[:MOST_RELATIONSHIPS]
            affs = _merged_affiliations(keep["affiliations"],
                                        other["affiliations"])[:MOST_AFFILIATIONS]
            self.db.execute("UPDATE connection_people SET relationships = ?, affiliations = ?, "
                            "relationship = ?, org = ?, role = ? WHERE id = ?",
                            (json.dumps(rels, ensure_ascii=False),
                             json.dumps(affs, ensure_ascii=False), rels[0] if rels else "",
                             affs[0]["org"] if affs else "", affs[0]["role"] if affs else "",
                             keep_id))
            # Their jobs and places too, after this one's.
            for single, many in (("profession", "professions"), ("location", "locations")):
                both = _merged(keep[many], other[many])[:MOST_LISTED]
                self.db.execute(f"UPDATE connection_people SET {many} = ?, {single} = ? "
                                "WHERE id = ?", (json.dumps(both, ensure_ascii=False),
                                                 both[0] if both else "", keep_id))
            filled = {key: value for key, value in filled.items()
                      if key not in ("relationship", "org", "role", "profession", "location")}
            if filled:
                # Each with its own times: found when the other's was found.
                times = dict(keep["field_times"])
                for key in filled:
                    if key in other["field_times"]:
                        times[key] = dict(other["field_times"][key])
                self.db.execute(
                    f"UPDATE connection_people SET {', '.join(f'{k} = ?' for k in filled)}, "
                    "field_times = ? WHERE id = ?",
                    (*filled.values(), json.dumps(times), keep_id))
            self.db.execute("UPDATE connection_accounts SET person_id = ? WHERE person_id = ?",
                            (keep_id, other_id))
            self.db.execute("UPDATE connection_activity SET person_id = ? WHERE person_id = ?",
                            (keep_id, other_id))
            aliases = _merged(keep["aliases"], [other["name"], *other["aliases"]])
            aliases = [one for one in aliases if name_key(one) != name_key(keep["name"])]
            self.db.execute("UPDATE connection_people SET aliases = ?, tags = ?, updated_at = ? "
                            "WHERE id = ?",
                            (json.dumps(aliases, ensure_ascii=False),
                             json.dumps(_merged(keep["tags"], other["tags"]), ensure_ascii=False),
                             now(), keep_id))
            if not keep.get("avatar") and other.get("avatar"):
                # Their picture, when the one kept has none.
                self.db.execute("UPDATE connection_people SET avatar = ?, avatar_from = ? "
                                "WHERE id = ?", (other["avatar"], other["avatar_from"], keep_id))
            elif other.get("avatar"):
                self._drop_picture(other["avatar"])
            self.db.execute("DELETE FROM connection_people WHERE id = ?", (other_id,))
            self._record(by, "merge", keep, {"merged": self._said(other)},
                         {"into": keep["name"], **({"filled": shown} if shown else {})},
                         reason)
            return self.get(keep_id)

    #: What a merge fills in on the person kept, when theirs is empty.
    FILLED_BY_MERGE = ("relationship", "closeness", "org", "role", "profession", "location",
                       "summary")

    def merge_preview(self, keep_id: str, other_id: str) -> Dict[str, Any]:
        """What merging `other_id` into `keep_id` would do, nothing done:
        the accounts and activity that move, the names that become other
        names, the tags added, and the empty fields filled from them."""
        keep, other = self.get(keep_id), self.get(other_id)
        if keep is None or other is None or keep_id == other_id:
            raise KeyError("no such pair of people")
        names = [one for one in [other["name"], *other["aliases"]]
                 if name_key(one) not in {name_key(keep["name"]),
                                          *[name_key(a) for a in keep["aliases"]]}]
        tags = [one for one in other["tags"]
                if name_key(one) not in {name_key(t) for t in keep["tags"]}]
        filled = {key: other[key] for key in self.FILLED_BY_MERGE
                  if not keep.get(key) and other.get(key)}
        # Their other relationships and organisations, after this one's.
        relationships = [one for one in other["relationships"]
                         if name_key(one) not in {name_key(r) for r in keep["relationships"]}]
        professions = [one for one in other["professions"]
                       if name_key(one) not in {name_key(r) for r in keep["professions"]}]
        locations = [one for one in other["locations"]
                     if name_key(one) not in {name_key(r) for r in keep["locations"]}]
        affiliations = [one for one in other["affiliations"] if name_key(one.get("org"))
                        not in {name_key(a.get("org")) for a in keep["affiliations"]}]
        count = self.db.query_one("SELECT COUNT(*) AS n FROM connection_activity "
                                  "WHERE person_id = ?", (other_id,))
        return {"keep": {"id": keep["id"], "name": keep["name"]},
                "other": {"id": other["id"], "name": other["name"]},
                "accounts": self.accounts(other_id), "activity_count": int(count["n"]),
                "activity": self.activity(other_id, 5), "names": names, "tags": tags,
                "filled": filled, "relationships": relationships,
                "professions": professions, "locations": locations,
                "affiliations": affiliations}

    def add_account(self, person_id: str, platform: str = "", handle: str = "",
                    url: str = "", source: str = "",
                    found_at: str = "") -> Optional[Dict[str, Any]]:
        """An account of theirs - once; one already known, seen again, has
        its found time moved on."""
        platform, handle, url = _text(platform, 40), _text(handle, 120), _text(url, 500)
        if not (handle or url):
            return None
        platform = self.canonical("label", platform)
        at = now()
        found = found_at or at
        for one in self.accounts(person_id):
            if (url and url_key(one["url"]) == url_key(url)) or (
                    handle and name_key(one["handle"]) == name_key(handle)
                    and name_key(one["platform"]) == name_key(platform)):
                if found > str(one.get("found_at") or ""):
                    self.db.execute("UPDATE connection_accounts SET found_at = ? WHERE id = ?",
                                    (found, one["id"]))
                    one["found_at"] = found
                return one
        ident = new_id("cacc")
        self.db.execute("INSERT INTO connection_accounts (id, person_id, platform, handle, url, "
                        "source, updated_at, found_at, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (ident, person_id, platform, handle, url, _text(source, 200), at,
                         found, at))
        return {"id": ident, "platform": platform, "handle": handle, "url": url,
                "found_at": found, "created_at": at, "updated_at": at}

    def move_account(self, account_id: str, to_person_id: str, by: str = "user",
                     reason: str = "") -> Dict[str, Any]:
        """An account put on the person it belongs to - one the agent gave
        the wrong person, say. Recorded once, with both people: the account
        left one and went to the other. The person it went to, whole."""
        with self._lock:
            row = self.db.query_one("SELECT * FROM connection_accounts WHERE id = ?",
                                    (str(account_id),))
            to = self.get(to_person_id)
            if row is None or to is None or to["deleted"]:
                raise KeyError("no such account or person")
            if row["person_id"] == to["id"]:
                return self.get(to["id"], with_details=True)
            came_from = self.get(row["person_id"])
            account = {key: row[key] for key in ("platform", "handle", "url") if row[key]}
            self.db.execute("UPDATE connection_accounts SET person_id = ?, updated_at = ? "
                            "WHERE id = ?", (to["id"], now(), row["id"]))
            self._record(by, "move an account", to,
                         {"account": account, "from": (came_from or {}).get("name", "")},
                         {"account": account, "to": to["name"]}, reason)
            return self.get(to["id"], with_details=True)

    def add_profile(self, person_id: str, url: str, platform: str = "", handle: str = "",
                    by: str = "user", reason: str = "") -> Dict[str, Any]:
        """A profile link of a person's - or the user's own - added by hand:
        one of the user's lessons."""
        url = _text(url, 500)
        if url and not re.match(r"^https?://", url, re.I):
            raise ValueError("a profile's address, beginning http:// or https://")
        if not (url or handle):
            raise ValueError("a profile needs an address or a handle")
        person = self.get(person_id)
        if person is None or person["deleted"]:
            raise KeyError("no such person")
        account = self.add_account(person_id, platform or platform_of(url), handle, url,
                                   source="added by you" if by == "user" else "")
        self._record(by, "add a profile", person, {},
                     {"account": {k: v for k, v in (account or {}).items()
                                  if k in ("platform", "handle", "url") and v}}, reason)
        return self.get(person_id, with_details=True)

    def found_profile(self, person_id: str, url: str, platform: str = "",
                      handle: str = "", why: str = "") -> bool:
        """A contact's public profile the agent found on the web, added - not
        the user's own (theirs go to their inbox), never one already there,
        and never one the user took off them. Whether it was added."""
        url = _text(url, 500)
        if not re.match(r"^https?://", url, re.I):
            return False
        person = self.get(person_id)
        if person is None or person["deleted"] or person["is_self"]:
            return False
        key = url_key(url)
        if any(url_key(one["url"]) == key for one in self.accounts(person_id)):
            return False
        for row in self.db.query("SELECT before FROM connection_actions WHERE person_id = ? "
                                 "AND by = 'user' AND action = 'remove a profile'",
                                 (person_id,)):
            if url_key(str((_loads(row["before"], {}).get("account") or {}).get("url")
                           or "")) == key:
                return False
        account = self.add_account(person_id, platform or platform_of(url), handle, url,
                                   source="found on the public web")
        if account is None:
            return False
        self._record("agent", "add a profile", person, {},
                     {"account": {k: v for k, v in account.items()
                                  if k in ("platform", "handle", "url") and v}},
                     _text(f"found on the public web{': ' + why if why else ''}", 1000))
        return True

    def remove_profile(self, person_id: str, account_id: str, by: str = "user",
                       reason: str = "") -> Dict[str, Any]:
        row = self.db.query_one("SELECT * FROM connection_accounts WHERE id = ? AND "
                                "person_id = ?", (str(account_id), str(person_id)))
        if row is None:
            raise KeyError("no such profile")
        self.db.execute("DELETE FROM connection_accounts WHERE id = ?", (row["id"],))
        account = {key: row[key] for key in ("platform", "handle", "url") if row[key]}
        self._record(by, "remove a profile", self.get(person_id), {"account": account}, {},
                     reason)
        return self.get(person_id, with_details=True)

    def add_activity(self, person_id: str, what: str, at: str = "", source: str = "",
                     url: str = "", evidence: Iterable[str] = (),
                     found_at: str = "") -> Optional[str]:
        """One dated thing they did, said or that happened to them - once.
        Three times kept with it: when it HAPPENED (`at`; when it was found,
        where that is not known), when it was FOUND (`found_at` - the page
        read, the window seen), and when it was written (`created_at`)."""
        what = _text(what, 600)
        if not what:
            return None
        written = now()
        found = found_at or written
        day = str(at or "")[:10]
        for one in self.activity(person_id, 60):
            if name_key(one["what"]) == name_key(what) and str(one["at"])[:10] == day:
                if found > str(one.get("found_at") or ""):
                    self.db.execute("UPDATE connection_activity SET found_at = ? WHERE id = ?",
                                    (found, one["id"]))
                return None
        ident = new_id("cev")
        self.db.execute("INSERT INTO connection_activity (id, person_id, at, what, source, url, "
                        "evidence, updated_at, found_at, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (ident, person_id, str(at or found)[:40], what, _text(source, 200),
                         _text(url, 500), json.dumps([str(one)[:80] for one in evidence][:10]),
                         written, found, written))
        extra = self.db.query("SELECT id FROM connection_activity WHERE person_id = ? "
                              "ORDER BY at DESC LIMIT -1 OFFSET ?", (person_id, MOST_ACTIVITY))
        for row in extra:
            self.db.execute("DELETE FROM connection_activity WHERE id = ?", (row["id"],))
        return ident

    # -- the picture ---------------------------------------------------------
    def avatar_path(self, person: Optional[Dict[str, Any]]) -> Optional[Path]:
        """The file of a person's picture, when there is one."""
        if not person or not person.get("avatar") or self.folder is None:
            return None
        path = self.folder / "avatars" / Path(str(person["avatar"])).name
        return path if path.is_file() else None

    def set_avatar(self, person_id: str, data: bytes, ext: str, came_from: str) -> str:
        """A picture of a person, kept: the file named for them."""
        if self.folder is None:
            raise RuntimeError("no folder for pictures")
        ext = ext if ext in ("png", "jpg", "gif", "webp") else "png"
        folder = self.folder / "avatars"
        folder.mkdir(parents=True, exist_ok=True)
        person = self.get(person_id)
        if person is None:
            raise KeyError("no such person")
        name = f"{person_id}.{ext}"
        partial = folder / f".{name}.partial"
        partial.write_bytes(data)
        partial.replace(folder / name)
        if person.get("avatar") and person["avatar"] != name:
            self._drop_picture(person["avatar"])
        at = now()
        self.db.execute("UPDATE connection_people SET avatar = ?, avatar_from = ?, "
                        "avatar_tried_at = ?, updated_at = ? WHERE id = ?",
                        (name, _text(came_from, 500), at, at, person_id))
        return name

    def avatar_tried(self, person_id: str) -> None:
        self.db.execute("UPDATE connection_people SET avatar_tried_at = ? WHERE id = ?",
                        (now(), person_id))

    def _drop_picture(self, name: str) -> None:
        if self.folder is None:
            return
        try:
            (self.folder / "avatars" / Path(str(name)).name).unlink(missing_ok=True)
        except OSError:
            log.debug("could not remove a picture", exc_info=True)

    def mark_checked(self, person_id: str) -> None:
        self.db.execute("UPDATE connection_people SET checked_at = ? WHERE id = ?",
                        (now(), person_id))

    # -- the public profiles the user gave ----------------------------------------
    def sources(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.db.query(
            "SELECT * FROM connection_sources ORDER BY updated_at DESC")]

    def add_source(self, url: str, label: str = "", person_id: str = "") -> Dict[str, Any]:
        url = _text(url, 500)
        if not re.match(r"^https?://", url, re.I):
            raise ValueError("a public page's address, beginning http:// or https://")
        for one in self.sources():
            if url_key(one["url"]) == url_key(url):
                return one
        ident = new_id("csrc")
        self.db.execute("INSERT INTO connection_sources (id, url, label, person_id, updated_at) "
                        "VALUES (?, ?, ?, ?, ?)", (ident, url, _text(label, 120),
                                                   str(person_id or ""), now()))
        self._record("user", "add a profile page", None, {}, {"url": url, "label": label})
        return dict(self.db.query_one("SELECT * FROM connection_sources WHERE id = ?", (ident,)))

    def remove_source(self, source_id: str) -> bool:
        row = self.db.query_one("SELECT * FROM connection_sources WHERE id = ?", (source_id,))
        if row is None:
            return False
        self.db.execute("DELETE FROM connection_sources WHERE id = ?", (source_id,))
        self._record("user", "remove a profile page", None, {"url": row["url"]}, {})
        return True

    def source_fetched(self, source_id: str) -> None:
        self.db.execute("UPDATE connection_sources SET fetched_at = ? WHERE id = ?",
                        (now(), source_id))

    # -- what the user needs to know -------------------------------------------------
    #: THE ISSUES KEPT: every "what you need to know" is kept as an issue of
    #: its own, numbered and dated - the newest this many; older ones, and
    #: the pictures only they used, are deleted.
    MOST_ISSUES = 100

    def save_briefing(self, needs: List[Any], items: List[Any], stats: Dict[str, Any]) -> str:
        ident = new_id("cbr")
        last = self.briefing() or {}
        issue = int((last.get("stats") or {}).get("issue") or 0) + 1
        stats = dict(stats, issue=issue)
        self.db.execute("INSERT INTO connection_briefings (id, at, needs, items, stats) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (ident, now(), json.dumps(needs, ensure_ascii=False, default=str),
                         json.dumps(items, ensure_ascii=False, default=str),
                         json.dumps(stats, ensure_ascii=False, default=str)))
        for row in self.db.query("SELECT id FROM connection_briefings ORDER BY at DESC, "
                                 "rowid DESC LIMIT -1 OFFSET ?", (self.MOST_ISSUES,)):
            self.db.execute("DELETE FROM connection_briefings WHERE id = ?", (row["id"],))
        self.keep_briefing_pictures()
        return ident

    def briefings(self) -> List[Dict[str, Any]]:
        """Every issue kept, newest first: its id, number, when and how many
        stories."""
        out = []
        for row in self.db.query("SELECT id, at, items, stats FROM connection_briefings "
                                 "ORDER BY at DESC, rowid DESC"):
            stats = _loads(row["stats"], {})
            out.append({"id": row["id"], "at": row["at"],
                        "issue": int(stats.get("issue") or 0),
                        "stories": len(_loads(row["items"], []))})
        return out

    # -- the pictures of its stories ------------------------------------------------------
    def save_briefing_picture(self, data: bytes, ext: str) -> str:
        """A story's picture, kept under the name of what it is."""
        import hashlib
        if self.folder is None:
            raise RuntimeError("no folder for pictures")
        ext = ext if ext in ("png", "jpg", "gif", "webp") else "png"
        folder = self.folder / "briefing"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{hashlib.sha1(data).hexdigest()[:20]}.{ext}"
        if not (folder / name).exists():
            partial = folder / f".{name}.partial"
            partial.write_bytes(data)
            partial.replace(folder / name)
        return name

    def briefing_picture_path(self, name: str) -> Optional[Path]:
        if self.folder is None:
            return None
        path = self.folder / "briefing" / Path(str(name)).name
        return path if path.is_file() else None

    def keep_briefing_pictures(self, names: Iterable[str] = ()) -> None:
        """Only the pictures of the stories of the issues kept stay - and
        `names`, those of the one being written."""
        if self.folder is None or not (self.folder / "briefing").is_dir():
            return
        kept = {Path(str(one)).name for one in names}
        for row in self.db.query("SELECT items FROM connection_briefings"):
            for item in _loads(row["items"], []):
                image = item.get("image") if isinstance(item, dict) else None
                if isinstance(image, dict) and image.get("kept"):
                    kept.add(Path(str(image["kept"])).name)
        for path in (self.folder / "briefing").iterdir():
            if path.is_file() and path.name not in kept and not path.name.startswith("."):
                path.unlink(missing_ok=True)

    def briefing(self, ident: str = "") -> Optional[Dict[str, Any]]:
        """The newest issue - or the one named."""
        row = (self.db.query_one("SELECT * FROM connection_briefings WHERE id = ?", (ident,))
               if ident else self.db.query_one(
                   "SELECT * FROM connection_briefings ORDER BY at DESC, rowid DESC LIMIT 1"))
        if row is None:
            return None
        stats = _loads(row["stats"], {})
        return {"id": row["id"], "at": row["at"], "needs": _loads(row["needs"], []),
                "items": _loads(row["items"], []), "stats": stats,
                "issue": int(stats.get("issue") or 0)}

    # -- helpers -------------------------------------------------------------------------
    @staticmethod
    def _clean(fields: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, value in fields.items():
            if value is None:
                continue
            if key == "relationship":
                out[key] = _relationship(value)
            elif key == "relationships":
                items = value if isinstance(value, list) else str(value).split(",")
                out[key] = _merged([], [one for one in map(_relationship, items) if one]
                                   )[:MOST_RELATIONSHIPS]
            elif key == "affiliations":
                out[key] = _merged_affiliations(
                    [one for one in map(_affiliation, value if isinstance(value, list) else [])
                     if one], [])[:MOST_AFFILIATIONS]
            elif key == "closeness":
                try:
                    out[key] = max(0, min(5, int(value)))
                except (TypeError, ValueError):
                    continue
            elif key in ("tags", "aliases"):
                items = value if isinstance(value, list) else str(value).split(",")
                out[key] = _merged([], [_text(one, 60) for one in items if _text(one, 60)])[:30]
            elif key in ("professions", "locations"):
                items = value if isinstance(value, list) else str(value).split(",")
                out[key] = _merged([], [_text(one, 160) for one in items
                                        if _text(one, 160)])[:MOST_LISTED]
            elif key in ("name", "org", "role", "location", "profession"):
                out[key] = _text(value, 160)
            elif key == "summary":
                out[key] = _text(value, 1200)
        return out

    @staticmethod
    def _said(person: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not person:
            return {}
        return {key: person.get(key) for key in (*FIELDS, "tags", "aliases")
                if person.get(key) not in (None, "", [], 0)}


def _relationship(value: Any) -> str:
    """A relationship as kept: in lower case, a few words."""
    return _text(value, 40).lower()


def _affiliation(value: Any) -> Optional[Dict[str, str]]:
    """An organisation and the role there - from {"org", "role"}, or an
    organisation's name alone; None for neither."""
    if isinstance(value, dict):
        one = {"org": _text(value.get("org"), 160), "role": _text(value.get("role"), 160)}
    else:
        one = {"org": _text(value, 160), "role": ""}
    return one if one["org"] or one["role"] else None


def _merged_affiliations(have: List[Dict[str, Any]], more: List[Dict[str, Any]]
                         ) -> List[Dict[str, str]]:
    """Affiliations, one per organisation - the first kept, a role it lacks
    taken from a later one at the same organisation."""
    out: List[Dict[str, str]] = []
    for one in [*(have or []), *(more or [])]:
        if not isinstance(one, dict):
            continue
        one = {"org": str(one.get("org") or ""), "role": str(one.get("role") or "")}
        key = name_key(one["org"])
        hit = next((kept for kept in out if key and name_key(kept["org"]) == key), None)
        if hit is None:
            if one["org"] or one["role"]:
                out.append(one)
        elif not hit["role"] and one["role"]:
            hit["role"] = one["role"]
    return out


def _merged(have: List[str], more: List[str]) -> List[str]:
    out, seen = [], set()
    for one in [*(have or []), *(more or [])]:
        key = name_key(one)
        if key and key not in seen:
            seen.add(key)
            out.append(str(one))
    return out


#: Other names of the platforms - written as the name kept.
LABEL_ALIASES = {
    "twitter": "X", "推特": "X", "x": "X",
    "linkedin": "LinkedIn", "领英": "LinkedIn",
    "github": "GitHub", "googlescholar": "Scholar", "scholar": "Scholar",
    "谷歌学术": "Scholar", "知乎": "Zhihu", "zhihu": "Zhihu", "微博": "Weibo",
    "weibo": "Weibo", "微信": "WeChat", "wechat": "WeChat", "weixin": "WeChat",
    "豆瓣": "Douban", "douban": "Douban", "哔哩哔哩": "Bilibili", "b站": "Bilibili",
    "bilibili": "Bilibili", "油管": "YouTube", "youtube": "YouTube",
    "facebook": "Facebook", "脸书": "Facebook", "instagram": "Instagram", "ins": "Instagram",
    "researchgate": "ResearchGate", "medium": "Medium", "homepage": "Homepage",
    "website": "Homepage", "个人主页": "Homepage", "主页": "Homepage",
}

#: Platforms told by their address.
PLATFORMS = (("linkedin.com", "LinkedIn"), ("github.com", "GitHub"), ("x.com", "X"),
             ("twitter.com", "X"), ("facebook.com", "Facebook"), ("instagram.com", "Instagram"),
             ("weibo.com", "Weibo"), ("zhihu.com", "Zhihu"), ("scholar.google", "Scholar"),
             ("researchgate.net", "ResearchGate"), ("youtube.com", "YouTube"),
             ("bilibili.com", "Bilibili"), ("medium.com", "Medium"), ("douban.com", "Douban"))


def platform_of(url: Any) -> str:
    """The platform an address is on, or its host."""
    key = url_key(url)
    for domain, name in PLATFORMS:
        if domain in key.split("/")[0]:
            return name
    return key.split("/")[0]


def _shown(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(one) for one in value)
    return "" if value in (None, 0) else str(value)


def _change_said(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    return "; ".join(f"{key}: {before.get(key)!r} -> {value!r}"
                     for key, value in (after or {}).items())[:400]


def _account_said(account: Any) -> str:
    account = account or {}
    return " ".join(str(account.get(key)) for key in ("platform", "handle", "url")
                    if account.get(key))


def _move_said(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    account = (after or {}).get("account") or {}
    said = " ".join(str(account.get(key)) for key in ("platform", "handle", "url")
                    if account.get(key))
    return f"the account {said} moved from {before.get('from') or '?'} to {after.get('to') or '?'}"

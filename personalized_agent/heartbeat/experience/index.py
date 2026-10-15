"""The words each stage is found by - worked out by code, never by a model.

A stage is used as a reference many times, so what it can be found by is
worked out once, when it is written, and kept: the terms of what it says
(its summary, its input, its one decision, its output, the user's words in
judging it, the card it is an attempt at) and the places it worked in (the
kinds of file, the folders, the sites its references point to). Stages
written before there was an index are indexed the first time they are
looked for, by the same code - it costs nothing but reading them.

A term is worth what it is rare: one that half the stages have says nothing
about any of them (its weight in a look-up is its inverse document
frequency, worked out at look-up time from the counts kept here).
"""

from __future__ import annotations

import math
import re
import threading
from pathlib import PurePath
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import urlparse

from ...data_manage.text import COMMON_WORDS, _continuous

#: Words that say nothing about which work a stage is: the language of
#: stages themselves, and of English.
STOP_WORDS = COMMON_WORDS | frozenset("""
about above after again against all also am any because before below between
both can could did do does doing done down during each few further had has
have having how into just more most much must nor now off once only other
out over own same should some such them through too under until very what
when where which while who whom why will with would yet one two three
user users agent agents stage stages work working worked asked ask asks made
make makes making used use uses using way ways thing things get got gets
first next last then still already each every whether without within
see sees seen want wants wanted mainly older newer edit edited edits save
saves saved pre re rn non new old part parts set sets put puts took take
""".split())

#: PAIRS OF CHARACTERS THAT SAY NOTHING about which work a stage is: the
#: words a Chinese sentence is held together by. Matched on them, an
#: instruction about a booking page for a reading club found a stage about
#: the wording of a panel - by "可以", "这个", "每个", "最终".
CJK_STOP_PAIRS = frozenset("""
可以 这个 那个 每个 一个 这些 那些 一些 最终 最后 我们 你们 他们 她们 它们 自己
大家 什么 怎么 怎样 这样 那样 因为 所以 但是 如果 还是 就是 不是 没有 现在 然后
需要 应该 能够 进行 已经 时候 还有 或者 而且 以及 这里 那里 之后 之前 一下 一样
的话 是否 不要 不能 不会 可能 其实 只是 比如 例如 还要 也是 都是 就会 我的 你的
他的 她的 为了 关于 对于 通过 用户 东西 问题 一起 让他 给我 我要 你要 要求 希望
""".split())

#: How much a term counts by where in the stage it was found: the decision
#: and the output are what the stage is; the summary retells them.
FIELD_WEIGHT = {"decision": 2.0, "output": 2.0, "card": 1.5, "words": 1.5,
                "input": 1.0, "summary": 1.0, "reference": 1.5, "keywords": 2.0}

#: A term in a stage counts at most this much, wherever else it appears.
MOST_WEIGHT = 4.0

_WORD = re.compile(r"[\w'.+#-]+", re.UNICODE)

#: What the index is built by. Raised when the way terms are worked out
#: changes, and every stage is indexed again.
INDEX_VERSION = 5

#: Not words: times and dates ("2026-10-06t18", "06t18", "18:27"), a
#: reference's id in the text ("r6"), a hash. Each one stage's own, and
#: about nothing.
_NOT_A_WORD = re.compile(
    r"^(\d{2,4}[-t:]\d|\d+t\d|r\d{1,3}$|[0-9a-f]{7,40}$|\d+(st|nd|rd|th|s|m|h|ms|px)?$)")


def terms_of(text: Any) -> List[str]:
    """The terms of a piece of text, in order: words where there are words,
    overlapping pairs of characters in scripts written without spaces, and
    an identifier both whole and in its parts - "check_status" is found by
    "check_status" and by "status"."""
    out: List[str] = []
    for raw in _WORD.findall(str(text or "").lower()):
        raw = raw.strip("'.-+#")
        if raw.endswith("'s"):
            raw = raw[:-2]
        if not raw or _NOT_A_WORD.match(raw):
            continue
        if any(_continuous(c) for c in raw):
            run = ""
            for c in raw + " ":
                if _continuous(c):
                    run += c
                    continue
                # A word that says nothing breaks the run: no pair is made
                # across it either ("上可" of "早上可以").
                pieces, i = [""], 0
                while i < len(run):
                    if run[i:i + 2] in CJK_STOP_PAIRS:
                        pieces.append("")
                        i += 2
                        continue
                    pieces[-1] += run[i]
                    i += 1
                for piece in pieces:
                    if len(piece) == 1 and piece == run:
                        out.append(piece)
                    for j in range(len(piece) - 1):
                        out.append(piece[j:j + 2])
                run = ""
            continue
        # An identifier in its parts too - parts of three letters or more:
        # "re-run" is not found by "re".
        pieces = [raw] + ([one for one in re.split(r"[_./-]+", raw) if len(one) >= 3]
                          if re.search(r"[_./-]", raw) else [])
        for piece in pieces:
            if len(piece) < 2 or piece in STOP_WORDS or _NOT_A_WORD.match(piece):
                continue
            out.append(piece)
    return out


def is_shown_word(term: str) -> bool:
    """A term a person reads as a word - not a pair of characters cut out
    of a sentence in a script with no spaces, which is found by but not
    read."""
    return not (len(term) == 2 and all(_continuous(c) for c in term))


def places_of(location: Any) -> List[str]:
    """Where a reference points, as terms: "env:ext:.tex", "env:dir:trade",
    "env:file:main.tex", "env:host:overleaf.com". The kind of file, the
    folders on its way, and the site - what says which environment the work
    was done in."""
    text = str(location or "").strip()
    if not text:
        return []
    out: List[str] = []
    if "://" in text:
        host = (urlparse(text).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host:
            out.append(f"env:host:{host}")
        return out
    # "D:/papers/trade @ 4b1e9c2" - a repository and a commit.
    path_text = text.split(" @ ", 1)[0].split(" in ", 1)[-1].strip().strip("'\"")
    # Only a path is a place: a conversation's session and time, or an
    # event's id, names no folder.
    if not re.search(r"[\\/]", path_text) and not re.fullmatch(
            r"[\w.\-]+\.[A-Za-z0-9]{1,6}", path_text):
        return out
    try:
        path = PurePath(path_text.replace("\\", "/"))
    except (TypeError, ValueError):
        return out
    parts = [one for one in path.parts if one not in ("/", "\\")]
    if not parts:
        return out
    name = parts[-1].lower()
    if "." in name and not name.startswith("."):
        out.append(f"env:file:{name}")
        out.append(f"env:ext:.{name.rsplit('.', 1)[1]}")
        folders = parts[:-1]
    else:
        folders = parts
    home = {"users", "home", "desktop", "documents", "downloads", "onedrive",
            _HOME_NAME}
    for folder in folders[-3:]:
        folder = folder.lower().rstrip(":/\\")
        if len(folder) < 2 or folder in home or re.fullmatch(r"[a-z]:?", folder):
            continue
        out.append(f"env:dir:{folder}")
    return out


#: The user's own folder name: on every path of theirs, about none of them.
try:
    from pathlib import Path as _Path
    _HOME_NAME = _Path.home().name.lower()
except (KeyError, RuntimeError):     # pragma: no cover - no home
    _HOME_NAME = ""


def stage_terms(stage: Dict[str, Any],
                card: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """Every term a stage is found by, with how much it counts in it."""
    work = stage.get("work") or {}
    decided = work.get("decision") or {}
    output = work.get("output") or {}
    said = stage.get("evaluation") or {}
    made = stage.get("deliverable") or {}
    fields: Dict[str, List[str]] = {
        "summary": [stage.get("summary", "")],
        "input": [one.get("what", "") for one in work.get("input") or []
                  if isinstance(one, dict)],
        "decision": [decided.get("about", ""), decided.get("chose", ""),
                     decided.get("why", ""), decided.get("rejected", ""),
                     *[str(one) for one in decided.get("options") or []]],
        "output": [output.get("what", ""), output.get("design", ""),
                   made.get("excerpt", ""),
                   *[one.get("requirement", "") for one in
                     output.get("specification") or [] if isinstance(one, dict)]],
        "words": [*(said.get("criteria") or []), *(said.get("words") or [])],
        "reference": [one.get("what", "") for one in
                      [*(work.get("references") or []),
                       *(made.get("references") or [])] if isinstance(one, dict)],
        "card": [(card or {}).get("title", ""), (card or {}).get("statement", "")],
        # The words it is looked for by, in English, whatever language the
        # stage is written in - searches are in English (search_stages).
        "keywords": [str(one) for one in work.get("keywords") or []],
    }
    weights: Dict[str, float] = {}
    for field, texts in fields.items():
        for text in texts:
            for term in terms_of(text):
                weights[term] = min(MOST_WEIGHT,
                                    weights.get(term, 0.0) + FIELD_WEIGHT[field])
    for one in [*(work.get("references") or []), *(made.get("references") or []),
                *(work.get("input") or [])]:
        if isinstance(one, dict):
            for place in places_of(one.get("location")):
                weights[place] = 1.0
    if stage.get("medium"):
        weights[f"env:medium:{stage['medium']}"] = 1.0
    return weights


class StageIndex:
    """The kept terms of one scope's stages."""

    def __init__(self, db: Any, scope: str) -> None:
        self.db = db
        self.scope = scope

    def add(self, stage: Dict[str, Any],
            card: Optional[Dict[str, Any]] = None) -> int:
        """Index one stage - again, if it was: what it says may have been
        corrected."""
        weights = stage_terms(stage, card)
        terms_written()
        self.db.execute("DELETE FROM stage_terms WHERE scope = ? AND stage_id = ?",
                        (self.scope, stage["id"]))
        if weights:
            self.db.executemany(
                "INSERT INTO stage_terms (scope, stage_id, term, weight) "
                "VALUES (?, ?, ?, ?)",
                [(self.scope, stage["id"], term[:120], weight)
                 for term, weight in weights.items()])
        terms_written()
        return len(weights)

    def indexed(self) -> set:
        return {row["stage_id"] for row in self.db.query(
            "SELECT DISTINCT stage_id FROM stage_terms WHERE scope = ?",
            (self.scope,))}

    def ensure(self, stages: Sequence[Dict[str, Any]],
               cards: Dict[str, Dict[str, Any]]) -> int:
        """Index every stage that is not yet - the ones written before there
        was an index - by the same code as a new one."""
        have = self.indexed()
        made = 0
        for stage in stages:
            if stage["id"] not in have:
                self.add(stage, cards.get(stage.get("card_id") or ""))
                made += 1
        return made

    def terms(self, stage_ids: Iterable[str], only: Optional[Iterable[str]] = None,
              places: bool = False) -> Dict[str, Dict[str, float]]:
        """Each stage's terms with their weights. With `only` and `places`,
        just those terms and the "env:" ones - all a search compares a stage
        by - found through the index on terms, not by reading every row of
        every stage: 53,235 rows were 0.17 seconds of each search."""
        if only is not None or places:
            return self._some_terms(stage_ids, list(dict.fromkeys(
                str(one) for one in (only or []))), places)
        wanted = [str(one) for one in stage_ids]
        out: Dict[str, Dict[str, float]] = {}
        for start in range(0, len(wanted), 400):
            chunk = wanted[start:start + 400]
            if not chunk:
                continue
            holes = ", ".join("?" for _ in chunk)
            for row in self.db.query(
                    f"SELECT stage_id, term, weight FROM stage_terms WHERE "
                    f"scope = ? AND stage_id IN ({holes})", (self.scope, *chunk)):
                out.setdefault(row["stage_id"], {})[row["term"]] = row["weight"]
        return out

    def _some_terms(self, stage_ids: Iterable[str], words: List[str],
                    places: bool) -> Dict[str, Dict[str, float]]:
        wanted = {str(one) for one in stage_ids}
        out: Dict[str, Dict[str, float]] = {}
        rows: List[Dict[str, Any]] = []
        if places:
            # Every term that starts "env:" sorts from "env:" to before
            # "env;" - the next character - and nothing else does.
            rows += self.db.query(
                "SELECT stage_id, term, weight FROM stage_terms WHERE scope = ? "
                "AND term >= 'env:' AND term < 'env;'", (self.scope,))
        for start in range(0, len(words), 400):
            chunk = words[start:start + 400]
            holes = ", ".join("?" for _ in chunk)
            rows += self.db.query(
                f"SELECT stage_id, term, weight FROM stage_terms WHERE scope = ? "
                f"AND term IN ({holes})", (self.scope, *chunk))
        for row in rows:
            if row["stage_id"] in wanted:
                out.setdefault(row["stage_id"], {})[row["term"]] = row["weight"]
        return out

    def rarity(self, terms: Iterable[str]) -> Dict[str, float]:
        """Each term's inverse document frequency among this scope's
        stages: log(1 + N / df) - a term every stage has is worth little,
        one stage's own term a lot."""
        wanted = list(dict.fromkeys(str(one) for one in terms))
        total_rows = self.db.query(
            "SELECT COUNT(DISTINCT stage_id) AS n FROM stage_terms WHERE scope = ?",
            (self.scope,))
        total = max(1, int(total_rows[0]["n"]) if total_rows else 1)
        out: Dict[str, float] = {}
        for start in range(0, len(wanted), 400):
            chunk = wanted[start:start + 400]
            if not chunk:
                continue
            holes = ", ".join("?" for _ in chunk)
            counts = {row["term"]: int(row["df"]) for row in self.db.query(
                f"SELECT term, COUNT(*) AS df FROM stage_terms WHERE scope = ? "
                f"AND term IN ({holes}) GROUP BY term", (self.scope, *chunk))}
            for term in chunk:
                out[term] = math.log(1.0 + total / max(1, counts.get(term, 0)))
        return out

    def clear(self) -> None:
        terms_written()
        self.db.execute("DELETE FROM stage_terms WHERE scope = ?", (self.scope,))
        terms_written()


#: How many times the kept terms have been written in this process - raised
#: before and after each write, so a reading that overlaps one can tell.
_WRITES = [0]
_WRITES_LOCK = threading.Lock()


def terms_written() -> None:
    """Say the kept terms are being, or have been, written - every write to
    stage_terms calls this on either side of it."""
    with _WRITES_LOCK:
        _WRITES[0] += 1


def terms_version(db: Any, scope: str) -> tuple:
    """What the kept terms of a scope are now, as far as reading what was
    worked out from them goes: the writes made here, and - for one made any
    other way, a test clearing the table, say - how many rows there are and
    the newest. Both from the index, a few milliseconds; their weights as
    well would read every row, 75 ms."""
    rows = db.query("SELECT COUNT(*) AS n, MAX(rowid) AS top "
                    "FROM stage_terms WHERE scope = ?", (scope,))
    row = rows[0] if rows else {}
    return (_WRITES[0], row.get("n"), row.get("top"))


#: How many of a stage's words are shown as what it is found by.
SHOWN_WORDS = 12


#: What each stage is found by, as last worked out - by database and scope,
#: with the version of the kept terms it was worked out from.
_FOUND: Dict[tuple, tuple] = {}
_FOUND_LOCK = threading.Lock()


def found_by(index: "StageIndex", stage_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """For each stage, what it is found by, as a person reads it: its
    strongest words - weight in the stage times how rare the word is - and
    the places it worked (kinds of file, folders, sites).

    KEPT UNTIL THE TERMS CHANGE. It depends on the stage's own terms and on
    how rare each is among all the scope's stages, so any write to the terms
    can change it, and nothing else can. The graph of every project asked
    for it on every stage on every request - 1.2 seconds of /all - and the
    terms change only when a stage is written or corrected. What was worked
    out while they were being written is not kept."""
    wanted = list(dict.fromkeys(str(one) for one in stage_ids))
    key = (id(index.db), str(getattr(index.db, "path", "")), index.scope)
    version = terms_version(index.db, index.scope)
    with _FOUND_LOCK:
        held = _FOUND.get(key)
    known: Dict[str, Any] = dict(held[1]) if held and held[0] == version else {}
    missing = [one for one in wanted if one not in known]
    if missing:
        fresh = _found_by_now(index, missing)
        if terms_version(index.db, index.scope) != version:
            # Written meanwhile: this reading is given, and not kept.
            return _found_by_now(index, wanted)
        known.update({one: fresh.get(one) for one in missing})
        with _FOUND_LOCK:
            _FOUND[key] = (version, known)
    return {one: {"words": list(known[one]["words"]),
                  "places": list(known[one]["places"])}
            for one in wanted if known.get(one) is not None}


def _found_by_now(index: "StageIndex", stage_ids: Sequence[str]
                  ) -> Dict[str, Dict[str, Any]]:
    terms = index.terms(stage_ids)
    rarity = index.rarity({term for found in terms.values() for term in found
                           if not term.startswith("env:")})
    out: Dict[str, Dict[str, Any]] = {}
    for stage_id, found in terms.items():
        words = sorted((term for term in found if not term.startswith("env:")
                        and is_shown_word(term)),
                       key=lambda term: (-found[term] * rarity.get(term, 0.0), term))
        places = sorted(term.split(":", 2)[-1] for term in found
                        if term.startswith(("env:ext:", "env:dir:", "env:host:")))
        out[stage_id] = {"words": words[:SHOWN_WORDS], "places": places[:12]}
    return out


def index_everything(db: Any, again: bool = False) -> Dict[str, int]:
    """Index every stage that is not yet, in every scope - the user's own,
    the Build now test's, each piece of work's - by the same code a new
    stage is indexed by. Run once at start, in the background: a stage is
    written indexed, so after this there is nothing left to do. `again`:
    every stage, from nothing - for when INDEX_VERSION has changed."""
    from .store import ExperienceStore
    if again:
        terms_written()
        db.execute("DELETE FROM stage_terms")
        terms_written()
    made: Dict[str, int] = {}
    for row in db.query("SELECT DISTINCT scope FROM experience_stages"):
        scope = str(row["scope"])
        store = ExperienceStore(db, scope)
        cards = {card["id"]: card for card in store.cards()}
        count = StageIndex(db, scope).ensure(store.stages(), cards)
        if count:
            made[scope] = count
    return made

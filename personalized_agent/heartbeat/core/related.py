"""WHAT IS RELATED, FOUND BY ITS WORDS (the user, 2026-11-09: "你在做这些management的时候，都要把相关
context给他，比如根据keyword检索的最近activity(包括chat, email record等)。模型进行总体判断").

A to-do kept for a reply already sent, a meeting taken off the calendar for a to-do removed, a summary
saying "学姐" where the contacts knew who: each was a model judging from the one line it was handed.
Now whatever manages these is handed what was found about the same thing - the user's activity as
the agent read it (their mail, chats and pages among it), what was said in the chat, and the people
who may be meant - and the model judges on the whole of it.

THE WORDS TO LOOK FOR ARE THE AGENT'S (the user, 2026-11-09: "这个找词是agent自己生成search keyword,
他会结合上下文生成"): `search_words` asks the model, once for many things, what a record about each
would say - names as they are written and might be written, places, the thing itself in English and
in Chinese, dates - and those words, with the thing's own, are looked for, each weighed by how rare
it is among the lines read. And `chat_just_before` hands over the chat of the last stretch as it was
said, found by no words at all: "take it off my to-dos, the calendar is enough" names nothing.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger(__name__)

#: How many states are read to find the related ones among; how many are given.
MOST_READ, MOST_GIVEN = 600, 6
#: The words a line must share with what it is about, at least; and how much they must weigh,
#: each weighed by how rare it is among the lines read (a word on every line - "user", "WeChat" -
#: weighs nothing; one on few weighs much).
LEAST_SHARED = 2
LEAST_WEIGHT = 2.5
#: Fewer lines than this, and how rare a word is among them says nothing: sharing three is enough.
FEW_LINES = 30
DETAIL_CHARS = 280


def _words(text: Any) -> set:
    from ..lessons.retrieve import COMMON_WORDS, words_of
    return words_of(str(text or "")) - COMMON_WORDS


def _shared(asked: set, text: str) -> int:
    return len(asked & _words(text))


def _weighed(texts: Sequence[str]) -> Any:
    """Each word's weight among these texts: log((how many + 1) / how many have it) - a word on
    every line weighs next to nothing, one on a single line much."""
    import math
    seen: Dict[str, int] = {}
    worded = [_words(one) for one in texts]
    for words in worded:
        for word in words:
            seen[word] = seen.get(word, 0) + 1
    total = max(1, len(worded))

    def weight(asked: set, words: set) -> float:
        return sum(math.log((total + 1) / seen[word]) for word in asked & words if word in seen)
    return worded, weight


def _enough(shared: int, weighs: float, asked: set, lines: int) -> bool:
    if shared < min(LEAST_SHARED, len(asked)):
        return False
    return weighs >= LEAST_WEIGHT or (lines < FEW_LINES and shared >= LEAST_SHARED + 1)


def activity(db: Any, about: str, since: str = "", most: int = MOST_GIVEN,
             leave_out: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """The user's activity about the same thing, from `since`: those sharing the most of its words
    first, the newest of equals first. Each with its id and time, to be pointed to."""
    asked = _words(about)
    if not asked:
        return []
    where, params = ["status != 'retired'", "about = 'user'", "source != 'task'"], []
    if since:
        where.append("COALESCE(starts_at, created_at) >= ?")
        params.append(since)
    try:
        rows = db.query(f"SELECT id, title, detail, starts_at, created_at FROM states WHERE "
                        f"{' AND '.join(where)} ORDER BY COALESCE(starts_at, created_at) DESC "
                        "LIMIT ?", (*params, MOST_READ))
    except Exception:                               # pragma: no cover - a look, not a step
        return []
    found = []
    worded, weight = _weighed([f"{row.get('title') or ''} {row.get('detail') or ''}" for row in rows])
    for n, row in enumerate(rows):
        if row["id"] in leave_out:
            continue
        shared, weighs = len(asked & worded[n]), weight(asked, worded[n])
        if _enough(shared, weighs, asked, len(worded)):
            found.append((-weighs, n, {"id": row["id"],
                                       "at": str(row.get("starts_at") or row.get("created_at") or "")[:16],
                                       "written": str(row.get("created_at") or "")[:19],
                                       "title": str(row.get("title") or "")[:200],
                                       "detail": " ".join(str(row.get("detail") or "").split())[:DETAIL_CHARS]}))
    found.sort(key=lambda one: (one[0], one[1]))
    return [one[2] for one in found[:most]]


def chat(db: Any, about: str, since: str = "", most: int = 4) -> List[Dict[str, Any]]:
    """What was said in the chat about it - the user's own words first among equals."""
    asked = _words(about)
    if not asked:
        return []
    try:
        rows = db.query("SELECT id, role, content, created_at FROM messages WHERE created_at >= ? "
                        "ORDER BY created_at DESC LIMIT ?", (since or "", MOST_READ))
    except Exception:                               # pragma: no cover
        return []
    found = []
    worded, weight = _weighed([str(row.get("content") or "") for row in rows])
    for n, row in enumerate(rows):
        shared, weighs = len(asked & worded[n]), weight(asked, worded[n])
        if _enough(shared, weighs, asked, len(worded)):
            found.append((-weighs, row.get("role") != "user", n,
                          {"id": row["id"], "at": str(row.get("created_at") or "")[:16],
                           "written": str(row.get("created_at") or "")[:19],
                           "who": "the user" if row.get("role") == "user" else "the agent",
                           "said": " ".join(str(row.get("content") or "").split())[:DETAIL_CHARS]}))
    found.sort(key=lambda one: one[:3])
    return [one[3] for one in found[:most]]


def people(db: Any, about: str, most: int = 6) -> List[Dict[str, Any]]:
    """Who may be meant: a contact named or called in it - by name, another name, or what the user
    calls them (学姐) - and those whose own recent doings share its words."""
    text = str(about or "")
    asked = _words(text)
    try:
        rows = db.query("SELECT id, name, aliases, called, relationship, summary, tags FROM "
                        "connection_people WHERE (deleted_at IS NULL OR deleted_at = '') "
                        "AND (is_self IS NULL OR is_self = 0)")
    except Exception:                               # pragma: no cover
        return []
    scored: Dict[str, List[Any]] = {}
    by_id = {row["id"]: row for row in rows}
    worded, weight = _weighed([f"{row.get('summary') or ''} {' '.join(_list(row.get('tags')))}"
                               for row in rows])
    for n, row in enumerate(rows):
        names = [str(row.get("name") or "")] + _list(row.get("aliases")) + _list(row.get("called"))
        named = any(len(one) >= 2 and one.casefold() in text.casefold() for one in names if one)
        score = (100.0 if named else 0.0) + weight(asked, worded[n])
        if named or score >= LEAST_WEIGHT * 2:
            scored[row["id"]] = [score, row]
    # And by what they were doing: a doing that shares the rare words of it.
    try:
        doings = db.query("SELECT person_id, what FROM connection_activity ORDER BY at DESC "
                          "LIMIT ?", (MOST_READ,))
    except Exception:                               # pragma: no cover
        doings = []
    worded, weight = _weighed([str(row.get("what") or "") for row in doings])
    for n, row in enumerate(doings):
        weighs = weight(asked, worded[n])
        person = by_id.get(row.get("person_id"))
        if person is None or weighs < LEAST_WEIGHT * 2:
            continue
        # Their best match, not the sum: one seen often is not meant more for it.
        kept = scored.setdefault(person["id"], [0.0, person])
        kept[0] = max(kept[0], weighs) if kept[0] < 100 else kept[0]
    ranked = sorted(scored.values(), key=lambda one: -one[0])[:most]
    return [{"name": str(row.get("name") or ""),
             **({"called": _list(row.get("called"))} if _list(row.get("called")) else {}),
             **({"relationship": str(row.get("relationship"))} if row.get("relationship") else {}),
             "about": " ".join(str(row.get("summary") or "").split())[:160]}
            for _score, row in ranked]


def _list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(one) for one in value if str(one or "").strip()]
    try:
        loaded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(one) for one in loaded if str(one or "").strip()] if isinstance(loaded, list) else []


def context(db: Any, about: str, since: str = "", leave_out: Sequence[str] = (),
            words: Sequence[str] = ()) -> Dict[str, Any]:
    """All three, as handed to a model; `words`: the agent's own search words for it, looked for
    beside its own."""
    asked = " ".join([str(about or ""), *[str(one) for one in words or []]])
    return {"activity_about_it": activity(db, asked, since, leave_out=leave_out),
            "said_in_the_chat": chat(db, asked, since),
            "people_who_may_be_meant": people(db, asked)}


WORDS_SYSTEM = """You write the words to search a person's own records with - their activity as an
assistant read it from their screen (mail, chats, pages), their chat with the assistant, their
contacts - to find what is about each thing below. The records are written in more than one
language (much of the chat in Chinese), and a word is found only as it is written there - so each
thing gets its words TWICE: first "english", 6 to 15 words or short phrases a record about it would
contain, in English ("meeting", "report"); then "as_written", the same words as the records
themselves would write them - translated into Chinese and into any other language the thing or its
people are in ("见面", "报告"), every person's name as it is written and as it may be written (full,
short, in the other script: "Margaret Ashby", "Maggie Ashby", "Ashby"). Places and organisations,
file names, dates and numbers as written. Not the words every record has ("user", "screen",
"WeChat"). Answer in JSON only:
{"things": [{"id": "its id", "english": ["...", "..."], "as_written": ["...", "..."]}]}"""
MOST_WORDS = 30          # both lists together


def search_words(models: Any, things: Sequence[Dict[str, str]], task: str) -> Dict[str, List[str]]:
    """The agent's search words for each thing ({"id", "text"}), by its id - one call for all of
    them. Nothing when no model answers: then its own words are looked for alone."""
    things = [one for one in things if str(one.get("text") or "").strip()]
    if not things or models is None:
        return {}
    from ...models.base import ModelRequest
    from ...common.languages import in_english
    try:
        response = models.invoke(ModelRequest(
            messages=[{"role": "user", "content": json.dumps(
                {"things": [{"id": str(one["id"]), "text": str(one["text"])[:1500]}
                            for one in things]}, ensure_ascii=False)}],
            system=WORDS_SYSTEM, max_tokens=min(4000, 300 + 150 * len(things)), task=task,
            language=in_english('each thing\'s "english" list (its "as_written" list keeps the '
                                'records\' own languages - Chinese and any other)')),
            role="heartbeat")
        from ...heartbeat.states.heartbeat import _as_json
        said = _as_json(str(getattr(response, "text", "") or "")) or {}
    except Exception:                               # pragma: no cover - its own words, then
        log.info("the search words could not be had", exc_info=True)
        return {}
    out: Dict[str, List[str]] = {}
    for one in (said.get("things") or []) if isinstance(said, dict) else []:
        if not isinstance(one, dict):
            continue
        # BOTH LANGUAGES: the records' own words first (found as written), then the English ones.
        lists = [one.get(key) for key in ("as_written", "english", "words")]
        kept: List[str] = []
        for word in (word for each in lists if isinstance(each, list) for word in each):
            word = " ".join(str(word or "").split())[:60]
            if word and word.lower() not in {have.lower() for have in kept}:
                kept.append(word)
        if any(isinstance(each, list) for each in lists):    # answered, even with none: kept
            out[str(one.get("id"))] = kept[:MOST_WORDS]
    return out


#: The chat just before, as said: how far back, and how many lines.
JUST_BEFORE_MINUTES, JUST_BEFORE_LINES = 60, 8


def chat_just_before(db: Any, until: str = "", minutes: int = JUST_BEFORE_MINUTES,
                     most: int = JUST_BEFORE_LINES) -> List[Dict[str, Any]]:
    """The last lines of the chat before `until` (now), within `minutes`, oldest first - found by
    no words: what the user said as they did it."""
    from datetime import datetime, timedelta, timezone
    end = until or datetime.now(timezone.utc).isoformat()
    try:
        since = (datetime.fromisoformat(end) - timedelta(minutes=minutes)).isoformat()
        rows = db.query("SELECT id, role, content, created_at FROM messages WHERE created_at >= ? "
                        "AND created_at <= ? ORDER BY created_at DESC LIMIT ?", (since, end, most))
    except Exception:                               # pragma: no cover
        return []
    return [{"id": row["id"], "at": str(row.get("created_at") or "")[:19],
             "who": "the user" if row.get("role") == "user" else "the agent",
             "said": " ".join(str(row.get("content") or "").split())[:DETAIL_CHARS]}
            for row in reversed(rows)]


__all__ = ["activity", "chat", "people", "context", "search_words", "chat_just_before",
           "MOST_GIVEN", "WORDS_SYSTEM"]

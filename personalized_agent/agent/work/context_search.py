"""WHAT BEARS ON A PIECE OF WORK HANDED OVER - found, not guessed.

1. WHAT WAS HANDED OVER, IN ENGLISH WORDS: the user's language may be any of
   five, and what the agent keeps of them - states, tasks' English copies,
   stages' keywords - is English. One model call turns the text, the folder
   and file names and the page title into English search keywords.
2. SEARCHED, BY ITS WORDS (BM25, of the TF-IDF family), over everything still
   kept - every state, every task, every stage - not the last half hour.
   Only a match above a RELEVANCE threshold goes in; those that do are ranked
   by relevance weighed by RECENCY (halving every three weeks, never below a
   quarter: an old match that is clearly on the subject still counts), at
   most twenty states, five tasks and five stages.
What was found is shown with the high-level plan, which is drafted from it
alone - quickly (delegate.py); the run is given it as references, and
searches further itself - the records again (the work tool
search_records), its files and the web - as it plans the detail.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

#: A match at least this relevant goes in (relevance: the share of the
#: query's weight a record matches - 0 to 1). Set on the user's own records
#: (2026-10-15), with queries of 8 to 14 English words: what clearly bore on
#: the work scored 0.2 to 0.3, what was related 0.12 to 0.2, and what only
#: shared a common word under 0.1.
RELEVANT = 0.12
#: Recency: halves every this many days, never below this.
HALF_LIFE_DAYS = 21.0
RECENCY_FLOOR = 0.25
#: At most this many of each.
MOST = {"state": 20, "task": 5, "stage": 5}
#: BM25's two constants, as usual.
K1, B = 1.2, 0.75

STOP = frozenset("""a an the and or but if then else of to in on at by for with from as is are
was were be been being it its this that these those there here into out up down over under
about after before than so such not no yes do does did done have has had having will would
can could should may might must shall i you he she we they them their our your my me us
what which who whom whose when where why how all any each every some more most other
another same own just also very too only both either neither one two user users""".split())

KEYWORDS_PROMPT = """TURN WHAT THE USER HANDED OVER INTO ENGLISH SEARCH WORDS.

What they handed over may be in any language. What it will be matched
against - their activity, their tasks, the stages of their past work - is
kept in English. Give the words those records would use: the subject, the
names of things (files, tools, people, places, papers, products - as they are
written), and the actions. Translate what is not English; keep names as they
are.

Return ONLY this JSON object:
{"keywords": ["8 to 14 English words or short phrases"],
 "sentence": "one English sentence saying what the work is about"}"""


def tokens(text: str) -> List[str]:
    """The words of an English text, lowered, the common ones left out, a
    plural taken back to its word."""
    out = []
    for word in re.findall(r"[a-z0-9][a-z0-9+#._-]*[a-z0-9+#]|[a-z0-9]", str(text).lower()):
        if word in STOP or len(word) < 2:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out


def recency(at: Any, now: Optional[datetime] = None) -> float:
    try:
        when = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return RECENCY_FLOOR
    age = max(0.0, ((now or datetime.now(timezone.utc)) - when).total_seconds() / 86400)
    return max(RECENCY_FLOOR, 0.5 ** (age / HALF_LIFE_DAYS))


class Index:
    """The records to search, by their English words (BM25)."""

    def __init__(self, records: Iterable[Dict[str, Any]]) -> None:
        self.records = [dict(one, words=Counter(tokens(one["text"]))) for one in records]
        self.records = [one for one in self.records if one["words"]]
        self.count = len(self.records)
        self.average = (sum(sum(one["words"].values()) for one in self.records)
                        / self.count) if self.count else 1.0
        held: Counter = Counter()
        for one in self.records:
            held.update(one["words"].keys())
        self.idf = {word: math.log(1 + (self.count - n + 0.5) / (n + 0.5))
                    for word, n in held.items()}

    def search(self, query: Iterable[str], now: Optional[datetime] = None
               ) -> List[Dict[str, Any]]:
        """Each record above the threshold: its relevance (0-1), its recency
        and the two together, highest first - at most MOST of each kind."""
        asked = Counter(tokens(" ".join(query)))
        weights = {word: self.idf.get(word, 0.0) for word in asked}
        best_possible = sum(w * (K1 + 1) for w in weights.values()) or 1.0
        found: Dict[str, List[Dict[str, Any]]] = {}
        for one in self.records:
            length = sum(one["words"].values())
            score = 0.0
            for word, weight in weights.items():
                tf = one["words"].get(word, 0)
                if tf:
                    score += weight * tf * (K1 + 1) / (
                        tf + K1 * (1 - B + B * length / self.average))
            relevance = score / best_possible
            if relevance < RELEVANT:
                continue
            fresh = recency(one.get("at"), now)
            found.setdefault(one["kind"], []).append(
                {k: v for k, v in one.items() if k != "words"}
                | {"relevance": round(relevance, 3), "recency": round(fresh, 3),
                   "score": round(relevance * fresh, 4)})
        out = []
        for kind, items in found.items():
            items.sort(key=lambda one: -one["score"])
            out += items[:MOST.get(kind, 5)]
        out.sort(key=lambda one: -one["score"])
        return out


def records(db: Any, leave_out: Any = None) -> List[Dict[str, Any]]:
    """Everything still kept, by its English words: every state, every task
    (its English copy, else its words), every stage (its English keywords).

    With `leave_out` (learning/leave_out.py - a Work debugger way's): none
    that rests on a folder left out - a state by the files and pages it
    rests on, a task and a stage by those of their states - and of the rest,
    no word that names one."""
    out: List[Dict[str, Any]] = []
    places = _places_of_states(db) if leave_out else {}
    try:
        for row in db.query("SELECT id, title, detail, starts_at, ends_at FROM states"):
            out.append({"kind": "state", "id": row["id"], "title": str(row["title"] or ""),
                        "text": f"{row['title'] or ''} {row['detail'] or ''}",
                        "detail": str(row["detail"] or "")[:400],
                        "at": row["ends_at"] or row["starts_at"],
                        "places": places.get(row["id"], [])})
    except Exception:
        pass
    try:
        for row in db.query("SELECT id, members, described, updated_at FROM state_tasks "
                            "WHERE scope = 'live' AND COALESCE(retired_at, '') = ''"):
            said = _loads(row["described"])
            english = said.get("in_english") if isinstance(said.get("in_english"), dict) else {}
            title = english.get("title") or said.get("title") or ""
            text = " ".join(str(english.get(k) or said.get(k) or "")
                            for k in ("title", "summary", "goal", "doing", "status"))
            # A project's own row: the folders it is worked in, beside its states'.
            folders = [str(one.get("path") if isinstance(one, dict) else one)
                       for one in said.get("directories") or []]
            out.append({"kind": "task", "id": row["id"], "title": str(said.get("title") or title),
                        "text": text, "detail": str(said.get("summary") or "")[:400],
                        "at": row["updated_at"],
                        "places": folders + _of_states(places, row["members"])})
    except Exception:
        pass
    try:
        for row in db.query("SELECT id, task_id, summary, work, states, ended, began FROM "
                            "experience_stages WHERE scope = 'live'"):
            work = _loads(row["work"])
            words = " ".join(str(one) for one in work.get("keywords") or [])
            if not words:
                continue
            out.append({"kind": "stage", "id": row["id"], "title": str(row["summary"] or "")[:200],
                        "text": words, "detail": str(row["summary"] or "")[:400],
                        "at": row["ended"] or row["began"],
                        "places": _of_states(places, row["states"])})
    except Exception:
        pass
    if not leave_out:
        return out
    kept = []
    for one in out:
        if leave_out.record_out(one["places"], f"{one['title']} {one['text']} {one['detail']}"):
            continue
        kept.append(dict(one, **{key: leave_out.redact(one[key])
                                 for key in ("title", "text", "detail")}))
    return kept


def _places_of_states(db: Any) -> Dict[str, List[str]]:
    """Each state's files, folders and pages (task_projects.state_places)."""
    from types import SimpleNamespace
    from ...heartbeat.states.task_projects import state_places
    out: Dict[str, List[str]] = {}
    try:
        for row in db.query("SELECT id, metadata FROM states"):
            out[row["id"]] = state_places(SimpleNamespace(metadata=_loads(row["metadata"])))
    except Exception:
        pass
    return out


def _of_states(places: Dict[str, List[str]], ids: Any) -> List[str]:
    """The places of the states a task or a stage is made of - each as
    often as its states rest on it."""
    try:
        said = json.loads(ids or "[]")
    except (TypeError, ValueError):
        said = []
    return [place for one in said if isinstance(said, list)
            for place in places.get(str(one), [])]


def _loads(raw: Any) -> Dict[str, Any]:
    try:
        said = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return said if isinstance(said, dict) else {}


def english_words(models: Any, handed: Dict[str, Any]) -> Dict[str, Any]:
    """What was handed over, as English search words - one model call. Without
    a model, the words of what was handed over as they are."""
    from ...common.languages import in_english
    from ...models.base import ModelRequest
    try:
        response = models.invoke(ModelRequest(
            messages=[{"role": "user", "content": json.dumps(handed, ensure_ascii=False)[:20000]}],
            system=KEYWORDS_PROMPT, max_tokens=600, task="work_draft",
            language=in_english("the keywords and the sentence")), role="primary")
        from ...heartbeat.states.heartbeat import _as_json
        said = _as_json(getattr(response, "text", "") or "") or {}
    except Exception:
        said = {}
    keywords = [str(one).strip()[:80] for one in said.get("keywords") or [] if str(one).strip()]
    if not keywords:
        keywords = list(dict.fromkeys(tokens(json.dumps(handed, ensure_ascii=False))))[:20]
    return {"keywords": keywords[:14], "sentence": str(said.get("sentence") or "")[:400]}


__all__ = ["Index", "records", "english_words", "tokens", "recency", "RELEVANT",
           "HALF_LIFE_DAYS", "RECENCY_FLOOR", "MOST"]

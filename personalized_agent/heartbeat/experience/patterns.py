"""How the user works, in two layers: patterns, and principles above them.

Built from the nature records - what the agent has concluded about the
user, each written from a window of their activity - the way tasks are
built from states: by meaning, with a model naming each group. A pattern is
a group of records about one way of working; a principle is a group of
patterns. Only these two layers are written up; the records under them are
left as they are.

The groups are cut WIDE, on purpose: what is worth keeping here is the
highest-level thing that is still one thing. A pattern seen once is an
anecdote, so a group needs several records before it is a pattern.

They are worked out again as records arrive, and a group keeps its id and
its words while it is recognisably the same group - so the record of how
the user works does not change its wording every time it is read.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..states.hierarchy import cluster, cut
from ..states.tasks import FAR as TASK_FAR
from ..states.tasks import RESUMMARISE_AFTER
from .store import model_task
from ...common.languages import in_english
from .store import ExperienceStore, LIVE, now

log = logging.getLogger(__name__)

#: Where the tree of records is cut into patterns - as a distance, 1 minus
#: the cosine. Wide, so each is the highest-level thing that is still one
#: thing. Measured on 1,373 nature records: 0.55 gave 99 patterns, 0.6 gave
#: 77 covering 94% of them, 0.65 put a third of everything in one.
PATTERN_CUT = 0.6

#: PRINCIPLES ARE GROUPED BY THE MODEL, not by distance. Every pattern is
#: about the same person, so their meanings sit close together, and cutting
#: their tree put 70 of 77 in one principle at every height tried. What
#: separates principles is the rule behind them, which is a reading, not a
#: distance - so the model reads every pattern and groups them.
PRINCIPLES_FEWEST, PRINCIPLES_MOST = 4, 10

#: How many patterns need words before principles are first grouped from
#: them - enough for the principles to be about more than a few of them.
PRINCIPLES_FROM = 12

#: The fewest records a pattern rests on. Fewer is an anecdote.
PATTERN_LEAST = 3

#: THE GROUPS FOLLOW THE TASKS' RULES. A pattern or principle is worded
#: again, as a task is named again, once RESUMMARISE_AFTER of what it holds
#: is not what its words were written from - come since OR GONE SINCE. It
#: used to be once it had grown by half, so a pattern that lost a third of
#: its natures to another, or to a split, kept words about what had left.
#: And at least as many members as a group rests on must have changed, so
#: one nature appended to a pattern of three does not reword it.

#: A PRINCIPLE THAT TOOK NEW PATTERNS IS CUT AGAIN BY MEANING, and split
#: when its patterns fall into parts as far apart as patterns under two
#: different principles are. Principles are not FOUND by distance - the
#: model groups them, see above - so this only catches one that a new
#: pattern made into two. Measured on the user's 49 patterns under 7
#: principles: pattern centres under one principle sat p50 0.63 apart,
#: under different ones p50 0.72. Cut at 0.72, one principle of the seven
#: fell into two (4 and 3 patterns); at 0.6 to 0.7 two did, into parts of
#: two patterns, closer than different principles are; at 0.8 none.
PRINCIPLE_CUT = 0.72

#: The fewest patterns each part of a split principle must hold. One
#: pattern far from the rest is a principle's odd case, not a second rule.
PRINCIPLE_PART_LEAST = 2

#: What a group's `written_by` keeps whatever its words: where it was
#: split from, what was split off it, and how it came to be what it is.
KEPT_NOTES = ("split_from", "split_off", "history")

#: How many of a group's earlier versions it keeps - its words before each
#: rewording, and each split - newest last.
HISTORY_KEPT = 20

#: What each kind of nature is, as the prompts say it. WORK is for the agent
#: planning and organising the user's work, so its patterns are written as
#: something that can be acted on; PERSONAL is for a companion.
KIND_WORDS = {
    "work": {
        "items": "what the user wants from their work",
        "pattern": "their taste and standards in one kind of work, or how "
                   "they like to work together - written so that an agent "
                   "planning or doing their work can act on it",
        "detail": "where it shows, and what it means for how their work "
                  "should be planned and done",
        "principles": "the few rules their work should be done by - what "
                      "an agent planning any piece of it should hold to",
    },
    # THE LESSONS, grouped the same way (heartbeat/lessons/engine.py): each
    # item one learned from the user's own edits.
    "notifications": {
        "items": "how the user wants to be notified",
        "pattern": "one way they want reminders and notices made, timed or left "
                   "out - written so that the agent deciding a notification can act on it",
        "detail": "where it shows, and when it does not hold if the items say",
        "principles": "the few rules every notification the agent makes should keep",
    },
    "connections": {
        "items": "how the user keeps their contacts",
        "pattern": "one way they want the people around them recorded, kept or "
                   "left out - written so that the agent keeping their contacts can act on it",
        "detail": "where it shows, and when it does not hold if the items say",
        "principles": "the few rules the agent should keep the user's contacts by",
    },
    "personal": {
        "items": "who the user is",
        "pattern": "a habit, a rhythm, an interest, or something they care "
                   "about - what a companion who knew them well would know",
        "detail": "where it shows, what it looks like day to day, and where "
                  "it does not hold if the items say",
        "principles": "the few things that most describe who they are",
    },
}

PATTERN_PROMPT = """SAY WHAT THESE OBSERVATIONS SHOW ABOUT {items}.

Each item below is something the agent concluded about the user from a
stretch of their activity. They were grouped because they mean similar
things. Write the PATTERN they share: {pattern}.

  - statement: ONE sentence, the user as its subject, as high-level as the
    items allow while still being true of all of them. Not a list of the
    items, and not one item reworded.
  - detail: two to four sentences - {detail}.

Only the user's own words, where you quote them, stay exactly as they
wrote them.

Return ONLY this JSON object:
{"statement": "...", "detail": "..."}"""

PLACE_PROMPT = """PUT THESE NEW PATTERNS UNDER THE PRINCIPLES THEY BELONG TO.

Below are the principles - {principles} - each with its id, and some new
patterns. Put each new pattern under the principle it is most clearly a
case of. Only when a pattern is a case of none of them, propose a new
principle for it - as high-level as it can be while still saying something.

Return ONLY this JSON object:
{"placed": {"<pattern id>": "<principle id>"},
 "new_principles": [{"statement": "...", "detail": "...",
                     "patterns": ["pattern ids"]}]}"""

REWORD_PROMPT = """SAY THE PRINCIPLE THESE PATTERNS SHARE.

Each item below is a pattern - {pattern} - all under one principle that has
grown since it was last put into words. Write the principle again so that
it covers every one of them and still says something.

Return ONLY this JSON object:
{"statement": "...", "detail": "..."}"""

PRINCIPLE_PROMPT = """GROUP THESE PATTERNS UNDER THE PRINCIPLES BEHIND THEM.

Each item below is a pattern - {pattern} - with its id and how many
observations it rests on. Find the PRINCIPLES: {principles}. Between
{fewest} and {most} of them, each as high-level as it can be while still
saying something - "the user works carefully" covers everything and says
nothing.

Put EVERY pattern under exactly one principle - the one it is most clearly
a case of.

  - statement: ONE sentence, the user as its subject.
  - detail: two or three sentences - how it shows across its patterns.

Only the user's own words, where you quote them, stay exactly as they
wrote them.

Return ONLY this JSON object:
{"principles": [{"statement": "...", "detail": "...",
                 "patterns": ["the ids of its patterns"]}]}"""


def worded(prompt: str, kind: str) -> str:
    """A prompt with what its kind of nature is filled in."""
    words = KIND_WORDS.get(kind) or KIND_WORDS["work"]
    for key, value in words.items():
        prompt = prompt.replace("{" + key + "}", value)
    return prompt


class _Text:
    """A record as the embedder reads it: an id and words."""

    def __init__(self, record_id: str, title: str, detail: str) -> None:
        self.id = record_id
        self.title = title
        self.detail = detail


#: Sources whose nature records are left out: the summaries compaction made
#: of others, which would count twice, and the data heartbeat's, which no
#: longer runs - its records were read from a search of the whole library,
#: and nothing it said is part of the experience now.
LEFT_OUT = ("compaction", "data_heartbeat")


#: Each nature record's file as last read - by path, with its modification
#: time and size then - so a file is parsed again only when it changed.
_READ: Dict[str, Tuple[Tuple[int, int], Any]] = {}
_READ_LOCK = threading.Lock()


def folder_records(library: Any, folder: str = "user_nature") -> List[Any]:
    """`library.records(folder)`, the same records in the same order, with
    each file parsed again only when it changed since it was last read.

    THE RECORDS ARE FILES, AND THEY WERE READ OVER AND OVER: 358 of them,
    0.25 to 0.44 seconds a reading, and the overview's picture of the
    patterns read them twice, a search for a step of work twice, a pattern
    round five times. Listing the folder and asking each file's time and
    size is a few milliseconds; a file written meanwhile - a new record, or
    one rewritten in place - has another, and is read again. The records
    are shared between callers and must not be changed by them; nothing
    here does."""
    files = getattr(library, "files", None)
    read = getattr(library, "read", None)
    if files is None or read is None:
        return list(library.records(folder))
    # Each file's time and size from the folder's listing: on Windows the
    # listing carries them, where a stat per file was 0.1 ms each - most of
    # what is left of a reading once nothing is parsed again.
    stamps: Dict[str, Tuple[int, int]] = {}
    where = getattr(library, "folder", None)
    try:
        with os.scandir(where(folder) if where else "") as scan:
            for entry in scan:
                try:
                    info = entry.stat()
                except OSError:
                    continue
                stamps[entry.path] = (info.st_mtime_ns, info.st_size)
    except (OSError, TypeError):
        pass
    out = []
    for path in files(folder):
        key = str(path)
        stamp = stamps.get(key)
        if stamp is None:
            try:
                info = os.stat(path)
                stamp = (info.st_mtime_ns, info.st_size)
            except OSError:
                stamp = None
        with _READ_LOCK:
            held = _READ.get(key)
        if stamp is not None and held is not None and held[0] == stamp:
            record = held[1]
        else:
            record = read(path)
            # A file that could not be read - held open by an editor, say -
            # is tried again next time, as it always was.
            if stamp is not None and record is not None:
                with _READ_LOCK:
                    _READ[key] = (stamp, record)
        if record is not None:
            out.append(record)
    return out


def nature_records(library: Any) -> List[Any]:
    """The nature records to group: the agent heartbeat's and the ones the
    stages showed - not compaction's summaries, nor the data heartbeat's."""
    out = []
    for record in folder_records(library, "user_nature"):
        if int(getattr(record, "level", 0) or 0) > 0:
            continue
        if str(getattr(record, "source", "") or "") in LEFT_OUT:
            continue
        # One the lessons engine retired - contradicted, no longer holding -
        # or the user rejected is not grouped, retrieved or given.
        if str((getattr(record, "metadata", None) or {}).get("lesson_state") or "")                 in ("retired", "rejected"):
            continue
        out.append(record)
    return out


def groups(ids: Sequence[str], vectors: Dict[str, List[float]],
           embedder: Any, height: float, least: int) -> List[List[str]]:
    """The groups the tree of these is cut into, largest first."""
    order = [one for one in ids if one in vectors]
    if len(order) < 2:
        return [[one] for one in order] if least <= 1 else []
    pairs = embedder.pairwise(order, vectors)
    distance = {(i, j): 1.0 - float(score) for i, j, score in pairs}
    merges = cluster(len(order), distance)
    out = [[order[seat] for seat in group]
           for group in cut(len(order), merges, height)]
    out = [group for group in out if len(group) >= least]
    out.sort(key=len, reverse=True)
    return out


def centre(members: Sequence[str], vectors: Dict[str, List[float]]
           ) -> List[float]:
    rows = [vectors[one] for one in members if one in vectors]
    if not rows:
        return []
    total = [sum(values) for values in zip(*rows)]
    size = sum(value * value for value in total) ** 0.5 or 1.0
    return [value / size for value in total]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _as_json(text: str) -> Dict[str, Any]:
    text = text or ""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else text[text.find("{"):text.rfind("}") + 1]
    try:
        out = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def parts(members: Sequence[str], vectors: Dict[str, List[float]],
          embedder: Any, height: float, least: int) -> List[List[str]]:
    """The parts a group falls into when its own members are cut again the
    way the first build cut everything - [] when it is still one.

    Two or more parts, each of at least `least`, or it is one group: a few
    members far from the rest are its odd cases, not another group. What
    belongs to no part goes with the part whose centre it is nearest, so
    nothing a group held is dropped. Largest first.
    """
    found = groups(members, vectors, embedder, height, least)
    if len(found) < 2:
        return []
    placed = {one for part in found for one in part}
    middles = [centre(part, vectors) for part in found]
    for one in members:
        if one in placed or one not in vectors:
            continue
        nearest = max(range(len(found)),
                      key=lambda at: _dot(vectors[one], middles[at]))
        found[nearest].append(one)
    found.sort(key=len, reverse=True)
    return found


def _changed(group: Dict[str, Any]) -> Tuple[int, int]:
    """How many of a group's members are not the ones its words were
    written from - come since, or gone since - and how many it was
    written from. A group named before the members were kept is judged by
    how much its size changed - the least its members can have changed by."""
    said = group.get("written_by") or {}
    was = said.get("summarised")
    if isinstance(was, list):
        before, now_held = set(was), set(group.get("members") or [])
        return len(before ^ now_held), len(before)
    named = int(said.get("named_size") or 0)
    return abs(len(group.get("members") or []) - named), named


def changed_since_named(group: Dict[str, Any]) -> float:
    """The share of a pattern's or principle's members that are not the ones
    its words were written from, against how many it was written from."""
    changed, named = _changed(group)
    return changed / named if named else 0.0


def wants_words(group: Dict[str, Any], least: int = PATTERN_LEAST) -> bool:
    """Whether a group has no words, or words about what it no longer is:
    RESUMMARISE_AFTER of it changed, and at least `least` members - as many
    as a group of its kind rests on. Without the second, a pattern worded
    from three natures was worded again for every one appended to it: on
    a replay of the user's last ninety natures, ten at a build, 35 patterns
    were worded again; with it, 15 - against 18 by the old rule, once it
    had grown by half."""
    if not group.get("statement"):
        return True
    changed, _named = _changed(group)
    return changed >= least and \
        changed_since_named(group) >= RESUMMARISE_AFTER


def changed_why(group: Dict[str, Any]) -> str:
    """Why a group is worded again, as its history says it."""
    return f"{round(100 * changed_since_named(group))}% of members changed"


def remember(group: Dict[str, Any], why: str, at: str = "") -> None:
    """The version a group is about to stop being, kept on it - as a task
    keeps what it was: when, its words, how many members they were written
    from, and why it changed. In `written_by`, which every rewrite of the
    group carries over, so its history survives everything that rebuilds
    it; the store hands it out as the group's `history`."""
    said = dict(group.get("written_by") or {})
    entry = {"at": at or now(),
             "statement": str(group.get("statement") or ""),
             "detail": str(group.get("detail") or "")[:300],
             "members": int(said.get("named_size") or 0)
             or len(group.get("members") or []),
             "why": why}
    said["history"] = (list(said.get("history") or []) + [entry])[-HISTORY_KEPT:]
    group["written_by"] = said


class PatternBuilder:
    """Works the patterns and principles out again, keeping what is the same."""

    def __init__(self, db: Any, library: Any, embedder: Any, models: Any,
                 scope: str = LIVE,
                 going: Optional[Callable[[], bool]] = None,
                 kind: str = "work") -> None:
        self.store = ExperienceStore(db, scope)
        #: The kind of nature this builder groups: work or personal. It
        #: reads only that kind's records and touches only its patterns.
        self.kind = kind
        self.library = library
        self.embedder = embedder
        self.models = models
        self.going = going or (lambda: True)
        #: What its model calls cost, for the round's account of each part.
        self.used = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        #: Where to say how far the naming has got: patterns and principles
        #: found, and how many have words.
        self.progress: Dict[str, Any] = {}

    #: How much a group needs, by default the natures' - a builder for
    #: another kind of item (the lessons) sets its own.
    pattern_least = PATTERN_LEAST
    principles_from = PRINCIPLES_FROM
    principles_fewest = PRINCIPLES_FEWEST
    principles_most = PRINCIPLES_MOST

    def _records(self, until: str = "") -> List[Any]:
        """The items grouped: this kind's nature records, written by the
        round's cutoff. Another builder groups other items the same way."""
        from .natures import kinds_of
        kinds = self.store.nature_kinds()
        return [record for record in nature_records(self.library)
                if (not until or record.created_at.isoformat() <= until)
                and self.kind in kinds_of(record, kinds)]

    def vectors(self, records: Sequence[Any]) -> Dict[str, List[float]]:
        texts = [_Text(record.id, record.title, record.summary or "")
                 for record in records]
        return dict(self.embedder.for_states(texts)) if texts else {}

    def build(self, budget: int = 80, until: str = "") -> Dict[str, Any]:
        """Keep the patterns and principles up to date - by appending.

        THE FIRST TIME, everything is grouped: the records into patterns,
        the patterns into principles. AFTER THAT, nothing is regrouped. A
        new record joins the pattern it is nearest, when it is inside that
        pattern's own spread, the way a state joins a task; what fits no
        pattern waits, and is grouped only among what is waiting. A new
        pattern is put under a principle; a principle is only proposed when
        none fits. A pattern - or principle - that took something new is
        cut again by meaning, and split if it has become two. A pattern or
        principle is worded again once RESUMMARISE_AFTER of it is not what
        it was worded from, gained or lost - otherwise its words stand, so
        a few natures appended do not reword it.

        BY MEANING ONLY, never by where a nature was seen: a nature is what
        holds across the user's projects, so the project it came from says
        nothing about which way of working it is.

        RESUMABLE, step by step: every change is saved as it is made, so a
        build that is stopped loses at most the call in flight.
        """
        report = {"calls": 0, "patterns": 0, "principles": 0, "named": 0,
                  "appended": 0, "new_patterns": 0}
        if self.embedder is None:
            report["skipped"] = "no embedder"
            return report
        # THE KEPT VECTORS ARE LOOKED IN BEFORE THE MODEL IS STARTED, where
        # the embedder can (`read_states`). Asking `available()` first
        # started the model - sixteen seconds and more, while a pattern
        # build holds it - and then every record's vector was usually kept
        # already: nothing here reads with the model but the missing ones.
        # It is started, as before, when one is missing.
        read = getattr(self.embedder, "read_states", None)
        if read is None and not self.embedder.available():
            report["skipped"] = "no embedder"
            return report
        # Only this kind's records, written by the round's cutoff.
        records = self._records(until)
        by_id = {record.id: record for record in records}
        if read is None:
            vectors = self.vectors(records)
        else:
            said = read([_Text(record.id, record.title, record.summary or "")
                         for record in records])
            if said is None:
                report["skipped"] = "no embedder"
                return report
            vectors = dict(said)
        report["records"] = len(records)
        patterns = self.store.patterns("pattern", kind=self.kind)
        if not patterns:
            found = groups(list(by_id), vectors, self.embedder, PATTERN_CUT,
                           self.pattern_least)
            patterns = [self.store.save_pattern(
                {"layer": "pattern", "members": members, "support": len(members),
                 "statement": "", "detail": "", "written_by": {},
                 "nature_kind": self.kind})
                for members in found]
            report["new_patterns"] = len(patterns)
        else:
            patterns, grown = self._append(patterns, by_id, vectors, report)
            patterns = self._split(patterns, grown, vectors, report)
        report["patterns"] = len(patterns)
        if not patterns:
            # Nothing of this kind to group yet: done, not unfinished.
            self.progress.update(patterns=0, named=0)
            return report
        self.progress.update(patterns=len(patterns), named=sum(
            1 for p in patterns if p.get("statement")))

        # Every pattern without words gets them, largest first, each saved
        # as soon as it is written - and then every one whose words are
        # about what it no longer is. Those keep their old words until the
        # new ones are written, so a round that runs out of calls does not
        # leave a pattern nameless, or take it out of its principle. One
        # whose natures are all gone has nothing to be worded from, and is
        # under no principle.
        held = [p for p in patterns if p["members"]]
        wanting = [p for p in held if wants_words(p)]
        wanting.sort(key=lambda p: (bool(p.get("statement")), -len(p["members"])))
        for pattern in wanting:
            if report["calls"] >= budget or not self.going():
                break
            items = [by_id[one] for one in pattern["members"] if one in by_id]
            lines = "\n".join(f"- {r.title}: {(r.summary or '')[:300]}"
                              for r in items[:14])
            answered, writer = self._ask(worded(PATTERN_PROMPT, self.kind),
                                         lines)
            report["calls"] += 1
            if answered.get("statement"):
                renamed = bool(pattern.get("statement"))
                if renamed:
                    remember(pattern, changed_why(pattern))
                pattern.update(statement=str(answered["statement"])[:600],
                               detail=str(answered.get("detail") or "")[:3000],
                               written_by=self._named(pattern, writer))
                self.store.save_pattern(pattern)
                report["named"] += 1
                if not renamed:
                    self.progress["named"] = self.progress.get("named", 0) + 1
        # PRINCIPLES FROM WHAT HAS WORDS, not once everything has: a build
        # that names a few patterns a round would otherwise leave the top of
        # the tree empty for days. The patterns named later are put under
        # the principles there are, the way a new pattern always is.
        named = [p for p in held if p.get("statement")]
        enough = len(named) >= min(len(held), self.principles_from)
        if enough:
            self._principles(named, vectors, budget, report)
        else:
            report["principles"] = len(self.store.patterns(
                "principle", kind=self.kind))
        if any(wants_words(p) for p in held):
            report["unfinished"] = "patterns still to name"
        return report

    @staticmethod
    def _named(group: Dict[str, Any], writer: Dict[str, str]) -> Dict[str, Any]:
        """Who wrote a group's words, and the members they were written
        from - what says, later, how much of it is no longer that. Where it
        was split from, what was split off it, and its history stay whatever
        its words: what was split off a principle is not put back under it."""
        was = group.get("written_by") or {}
        return dict(writer, named_size=len(group["members"]),
                    summarised=list(group["members"]),
                    **{key: was[key] for key in KEPT_NOTES if was.get(key)})

    # -- appending ----------------------------------------------------------
    def _append(self, patterns: List[Dict[str, Any]], by_id: Dict[str, Any],
                vectors: Dict[str, List[float]], report: Dict[str, Any]
                ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """New records into the patterns they fit; the rest grouped apart.
        With the ids of the patterns that took something."""
        known = {one for p in patterns for one in p["members"]}
        # Gone from the library: dropped from their pattern.
        for pattern in patterns:
            members = [one for one in pattern["members"] if one in by_id]
            if members != pattern["members"]:
                pattern.update(members=members, support=len(members))
                self.store.save_pattern(pattern)
        waiting = [one for one in by_id if one not in known and one in vectors]
        shapes = {}
        for pattern in patterns:
            middle = centre(pattern["members"], vectors)
            apart = [1.0 - _dot(vectors[one], middle)
                     for one in pattern["members"] if one in vectors]
            mean = sum(apart) / len(apart) if apart else 0.0
            spread = (sum((d - mean) ** 2 for d in apart) / len(apart)) ** 0.5 \
                if apart else 0.0
            # MATCHED UNLESS IT DEVIATES ENOUGH - the rule a state joins a
            # task by: the nearest pattern takes it, unless it is further
            # off than a task would let a state be, or than this pattern's
            # own members are when they are more spread out than that.
            shapes[pattern["id"]] = (middle, max(mean + 2.0 * spread, TASK_FAR))
        grown: Dict[str, Dict[str, Any]] = {}
        left = []
        for one in waiting:
            best, distance = None, 2.0
            for pattern in patterns:
                middle, edge = shapes[pattern["id"]]
                if not middle:
                    continue
                apart = 1.0 - _dot(vectors[one], middle)
                if apart <= edge and apart < distance:
                    best, distance = pattern, apart
            if best is None:
                left.append(one)
                continue
            best["members"] = best["members"] + [one]
            best["support"] = len(best["members"])
            grown[best["id"]] = best
            report["appended"] += 1
        # Their words are looked at with every pattern's, in `build`: what
        # it gained and what it lost both count.
        for pattern in grown.values():
            self.store.save_pattern(pattern)
        # What fits no pattern, grouped among itself only.
        for members in groups(left, vectors, self.embedder, PATTERN_CUT,
                              self.pattern_least):
            patterns.append(self.store.save_pattern(
                {"layer": "pattern", "members": members, "support": len(members),
                 "statement": "", "detail": "", "written_by": {},
                 "nature_kind": self.kind}))
            report["new_patterns"] += 1
        return patterns, list(grown)

    def _split(self, patterns: List[Dict[str, Any]], grown: Sequence[str],
               vectors: Dict[str, List[float]], report: Dict[str, Any]
               ) -> List[Dict[str, Any]]:
        """A pattern that took new natures, split if it has become two.

        ITS OWN MEMBERS CUT AGAIN AS THE FIRST BUILD CUT EVERYTHING - the
        same tree, the same height, the same least size. Appending lets a
        pattern take what is near its centre, and its centre moves as it
        takes; after enough, what it holds can be two ways of working the
        first build would have kept apart. Only a pattern that took
        something is looked at: one that took nothing is as it was.

        The largest part keeps the pattern - its id, its principle, and its
        words unless too much of it has gone. Each other part becomes a new
        pattern with no words and no principle, named and placed as any new
        pattern is.
        """
        by_id = {p["id"]: p for p in patterns}
        made = []
        for pattern_id in grown:
            pattern = by_id.get(pattern_id)
            if pattern is None:
                continue
            found = parts(pattern["members"], vectors, self.embedder,
                          PATTERN_CUT, self.pattern_least)
            if not found:
                continue
            at = now()
            became = []
            for members in found[1:]:
                new = self.store.save_pattern(
                    {"layer": "pattern", "members": members,
                     "support": len(members), "statement": "", "detail": "",
                     "nature_kind": self.kind,
                     "written_by": {"split_from": pattern["id"], "history": [
                         {"at": at, "split_from": pattern["id"],
                          "statement": str(pattern.get("statement") or ""),
                          "members": len(members),
                          "why": f"split: made of {len(members)} members "
                                 f"of {pattern['id']}"}]}})
                made.append(new)
                became.append(f"{len(members)} members became {new['id']}")
                report["new_patterns"] += 1
            remember(pattern, "split: " + "; ".join(became), at)
            pattern["members"] = found[0]
            pattern["support"] = len(found[0])
            self.store.save_pattern(pattern)
            report["split"] = report.get("split", 0) + 1
        return patterns + made

    def _principles(self, patterns: List[Dict[str, Any]],
                    vectors: Dict[str, List[float]], budget: int,
                    report: Dict[str, Any]) -> None:
        """Principles the first time; after that, new patterns put under them."""
        principles = self.store.patterns("principle", kind=self.kind)
        by_pattern = {p["id"]: p for p in patterns}
        alive = set(by_pattern)
        # Patterns that are gone leave their principle.
        for principle in principles:
            members = [one for one in principle["members"] if one in alive]
            if members != principle["members"]:
                principle["members"] = members
                self.store.save_pattern(principle)
        principles = [p for p in principles if p["members"]]
        placed = {one for p in principles for one in p["members"]}
        unplaced = [p for p in patterns if p["id"] not in placed]
        if principles and not unplaced:
            self._reword_principles(principles, by_pattern, budget, report)
            report["principles"] = len(principles)
            return
        if report["calls"] >= budget or not self.going():
            report["unfinished"] = "principles still to place"
            report["principles"] = len(principles)
            return
        # A pattern split off a principle is not put back under it: it was
        # split off because it is not the same thing, and put back it would
        # only be split off again.
        not_under: Dict[str, set] = {}
        for principle in principles:
            for one in (principle.get("written_by") or {}).get("split_off") or []:
                not_under.setdefault(one, set()).add(principle["id"])
        #: The principles there were that took a pattern this build.
        took: set = set()
        if not principles:
            lines = "\n".join(f"- [{p['id']}] ({len(p['members'])}) "
                              f"{p['statement']}" for p in patterns)
            prompt = (worded(PRINCIPLE_PROMPT, self.kind)
                      .replace("{fewest}", str(self.principles_fewest))
                      .replace("{most}", str(self.principles_most)))
            answered, writer = self._ask(prompt, lines, tokens=4000)
            report["calls"] += 1
            made = answered.get("principles") or []
            existing: Dict[str, Dict[str, Any]] = {}
        else:
            lines = ("THE PRINCIPLES:\n" + "\n".join(
                f"- [{p['id']}] {p['statement']}" for p in principles)
                + "\n\nTHE NEW PATTERNS:\n" + "\n".join(
                f"- [{p['id']}] ({len(p['members'])}) {p['statement']}"
                for p in unplaced))
            answered, writer = self._ask(worded(PLACE_PROMPT, self.kind),
                                         lines, tokens=3000)
            report["calls"] += 1
            existing = {p["id"]: p for p in principles}
            for pattern_id, principle_id in (answered.get("placed") or {}).items():
                if pattern_id in by_pattern and principle_id in existing and \
                        pattern_id not in placed and \
                        principle_id not in not_under.get(pattern_id, ()):
                    existing[principle_id]["members"].append(pattern_id)
                    placed.add(pattern_id)
                    took.add(principle_id)
            made = answered.get("new_principles") or []
        for said in made:
            if not isinstance(said, dict) or not said.get("statement"):
                continue
            members = [one for one in said.get("patterns") or []
                       if one in by_pattern and one not in placed]
            if not members:
                continue
            placed.update(members)
            principle = {"layer": "principle", "members": members,
                         "nature_kind": self.kind,
                         "statement": str(said["statement"])[:600],
                         "detail": str(said.get("detail") or "")[:3000],
                         "written_by": self._named({"members": members}, writer)}
            principles.append(principle)
        if not principles:
            report["unfinished"] = "the principles came back empty"
            return
        # A pattern the model left out goes with the nearest principle.
        for pattern in [p for p in patterns if p["id"] not in placed]:
            mine = centre(pattern["members"], vectors)
            allowed = [pr for pr in principles
                       if pr.get("id") not in not_under.get(pattern["id"], ())]
            best = max(allowed or principles, key=lambda pr: _dot(mine, centre(
                [r for one in pr["members"] for r in by_pattern[one]["members"]],
                vectors)))
            best["members"].append(pattern["id"])
            if best.get("id"):
                took.add(best["id"])
        for principle in principles:
            principle["support"] = sum(len(by_pattern[one]["members"])
                                       for one in principle["members"]
                                       if one in by_pattern)
            saved = self.store.save_pattern(principle)
            principle["id"] = saved["id"]
            for one in principle["members"]:
                if one in by_pattern and \
                        by_pattern[one].get("parent") != saved["id"]:
                    by_pattern[one]["parent"] = saved["id"]
                    self.store.save_pattern(by_pattern[one])
        self._split_principles([p for p in principles if p["id"] in took],
                               by_pattern, vectors, report)
        self._reword_principles(principles, by_pattern, budget, report)
        report["principles"] = len(principles)

    def _split_principles(self, principles: List[Dict[str, Any]],
                          by_pattern: Dict[str, Dict[str, Any]],
                          vectors: Dict[str, List[float]],
                          report: Dict[str, Any]) -> None:
        """A principle that took new patterns, split if it has become two.

        Its patterns' centres cut again - a pattern is to a principle what
        a nature is to a pattern - at PRINCIPLE_CUT, each part of at least
        PRINCIPLE_PART_LEAST. The largest part keeps the principle; the
        patterns of every other part leave it with no principle, and are
        put under one - or a new one is proposed - in the next build, as
        any new pattern is, never back under this one.
        """
        for principle in principles:
            centres = {one: centre(by_pattern[one]["members"], vectors)
                       for one in principle["members"] if one in by_pattern}
            centres = {one: middle for one, middle in centres.items() if middle}
            found = parts(list(centres), centres, self.embedder, PRINCIPLE_CUT,
                          PRINCIPLE_PART_LEAST)
            if not found:
                continue
            leaving = {one for part in found[1:] for one in part}
            remember(principle, f"split: {len(leaving)} patterns left it, to "
                                f"be placed anew: {', '.join(sorted(leaving))}")
            principle["members"] = [one for one in principle["members"]
                                    if one not in leaving]
            principle["support"] = sum(len(by_pattern[one]["members"])
                                       for one in principle["members"]
                                       if one in by_pattern)
            said = dict(principle.get("written_by") or {})
            said["split_off"] = sorted(set(said.get("split_off") or [])
                                       | leaving)
            principle["written_by"] = said
            self.store.save_pattern(principle)
            for one in sorted(leaving):
                by_pattern[one]["parent"] = ""
                self.store.save_pattern(by_pattern[one])
            report["principles_split"] = report.get("principles_split", 0) + 1
            report["unfinished"] = "principles still to place"

    def _reword_principles(self, principles: List[Dict[str, Any]],
                           by_pattern: Dict[str, Dict[str, Any]], budget: int,
                           report: Dict[str, Any]) -> None:
        """A principle whose words are about what it no longer is - a share
        of its patterns come or gone since - is worded again."""
        for principle in principles:
            if not wants_words(principle, PRINCIPLE_PART_LEAST):
                continue
            if report["calls"] >= budget or not self.going():
                report["unfinished"] = "principles still to reword"
                return
            lines = "\n".join(f"- {by_pattern[one]['statement']}"
                              for one in principle["members"] if one in by_pattern)
            answered, writer = self._ask(worded(REWORD_PROMPT, self.kind),
                                         lines)
            report["calls"] += 1
            if answered.get("statement"):
                remember(principle, changed_why(principle))
                principle.update(statement=str(answered["statement"])[:600],
                                 detail=str(answered.get("detail") or "")[:3000],
                                 written_by=self._named(principle, writer))
                self.store.save_pattern(principle)

    def _ask(self, prompt: str, items: str, tokens: int = 800
             ) -> Tuple[Dict[str, Any], Dict[str, str]]:
        if self.models is None:
            return {}, {}
        from ...models.base import ModelRequest
        try:
            response = self.models.invoke(
                ModelRequest(messages=[{"role": "user", "content": items}],
                             system=prompt, max_tokens=tokens,
                             task=model_task(self.store.scope),
                             # Patterns and principles are grouped from
                             # natures, and kept with them, in English.
                             language=in_english("the patterns and principles"),
                             metadata={"kind": "experience patterns"}),
                role="heartbeat")
        except Exception as exc:          # pragma: no cover - a call
            log.info("could not name a pattern: %s", exc)
            return {}, {}
        from .store import count_use
        count_use(self.used, response)
        return (_as_json(getattr(response, "text", "") or ""),
                {"model": str(getattr(response, "model", "") or ""),
                 "provider": str(getattr(response, "provider", "") or ""),
                 "at": now()})

"""THE LESSONS LIKE THIS MOMENT - one way of finding them, for every decision
(the user, 2026-11-03: "用的时候用统一的context retrieval方法"; a notification
being set up - by the heartbeat or by any trigger - looks here first).

The lessons are kept as learning keeps its work: EXAMPLES, each marked with its
class, with its exact content and the user's own words (`lesson_evidence`), and
above them the possible universal RULES drawn from them (`lesson_rules`) - all
of it in English for the model, never shown to the user (the Lessons debugger
apart). Here both are looked for together:

  1. THE MOMENT SAID AGAIN - a window's title and words are in any language:
     one small call says it in plain English, in the terms the lessons use
     (`rewrite_task`) - only when it is not English already, and there is a
     lesson to compare it with.
  2. BY MEANING - the moment against each rule's statement and each example's
     English, by the state heartbeat's embedding model (English on both sides
     now); with no model, by the words they share.
  3. GIVEN TOGETHER - the rules most like it, with how sure each is, and the
     examples most like it: what happened, what the user did, their words,
     and the start of the exact content.

EVERY TYPE IS LOOKED AT (the user, 2026-11-04): what was learned of a contact or
of the user's work bears on a notification too, each said to be from where it is:
the other types' rules, the work's lessons and principles, the lesson cards.

NO QUOTA (the user, 2026-11-07): all of them are ranked together by how like the
moment they are. A decision's own types count OWN_BONUS times; a candidate rule
CANDIDATE_SHARE times, and is said to be one, with how little it rests on; what
is retired or rejected never comes. Below a floor nothing is given - fewer, even
none, is right: with half the room kept for its own types and no floor, the same
four rules came back for nine moments in ten that had nothing to do with them.

THE CONTENT, NOT THE SITUATION (the user, 2026-11-07): what a caller asks with is
what the thing is about - the words on the screen, the work, the page - never
the Activator's name, the mode or the hour: a rest moment asked with its
situation pulled work lessons. Every retrieval is RECORDED, in a build that
carries the Debugger (`lesson_retrievals`, read in the Lessons debugger).
"""

from __future__ import annotations

import contextvars
import json
import logging
import math
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

#: How the moment is said again before the lessons like it are looked for.
RETRIEVAL_PROMPT = """Below is what a moment is about: the words on a screen, a piece
of work, a page, a list - in any language. Say what it is about again in one or
two plain English sentences, in the general terms that lessons about how the
user wants things done would use: the subject, and what kind of content or work
it is. Translate what is not English. No names of people. Answer with the
sentences alone."""

#: What a run that names or groups the user's work is told of the lessons found.
THE_USERS_OWN = """WHAT THE USER HAS DONE THEMSELVES, in moments like this - names they gave,
what they deleted, how they grouped things. These are their own actions, not the agent's:
they come first.
"""

#: The moments said again, the newest kept.
MOST_REWRITTEN = 64
#: The types with examples and rules of this form.
TYPES = ("notifications", "recommendations", "connections", "general")
#: How long what was learned of why the user judges as they do is read once for.
REASONS_FOR_SECONDS = 600.0
#: What is shown of an example's exact content.
MOST_SHOWN = 500

#: NO QUOTA (the user, 2026-11-07): a decision's own types count this much more, and a
#: candidate rule this share of its likeness - it rests on a case or two.
OWN_BONUS = 1.2
CANDIDATE_SHARE = 0.8
#: THE FLOORS, below which nothing is given (the user, 2026-11-07: "giving fewer, even none, is
#: right"), on the score after the bonus and the share. MEASURED on a copy of the user's database
#: (2026-11-07): each of the 68 non-work examples' English as the moment, against the 11 rules of
#: those kinds (37 pairs where the example supports the rule) and the other examples.
#: BY MEANING (all-MiniLM-L6-v2): a rule the example supports scored p10 0.35, median 0.52; one it
#: does not, median 0.21, p90 0.39. At 0.35, 92% of the supported rules and 94% of the examples of
#: the same class are kept, 15% of the unrelated rules. Sixty task titles of the user's work and
#: forty things they read, as moments: the best non-work lesson each found had a median of 0.27
#: and 0.24 - most such moments now get none, as they should.
#: BY WORDS (shared words over the root of both counts, common words left out): supported p10
#: 0.12, median 0.20; unrelated median 0, p90 0.09. At 0.10, 97% of the supported are kept, 7%
#: of the unrelated. (The old measure, shared over the lesson's own count, kept 92% to 15% at
#: its best floor, and a short moment hardly reached any lesson.)
MEANING_FLOOR = 0.35
#: WHY THEY JUDGE SO, AT A MOMENT TO SPEAK (2026-11-07): of the user's reasons, those of what is
#: worth their attention, and their motives, bear on whether and what to tell them; the others -
#: what their work must be, how they make and judge it - came in at 0.35-0.42 on title words
#: alone ("Watermelon": the app's design rules; a podcast video: how to make slides). At a
#: notification's or a recommendation's moment those count this share of their likeness.
SPEAKING_KINDS = ("notifications", "recommendations")
SPEAKING_ASPECTS = ("attention", "motive")
OTHER_ASPECT_SHARE = 0.8
#: A RULE IS FOUND BY ITS OWN EXAMPLES TOO (2026-11-07): a rule is said in general words ("when a
#: chat message shows someone asked the user to send something"), the moment in particular ones,
#: and the two were 0.23-0.27 alike where the rule's own examples were 0.32-0.56. A rule counts
#: the best of its statement and this many of its supporting examples.
OWN_EXAMPLES = 5
WORDS_FLOOR = 0.10
#: Words too common to say two texts are alike.
COMMON_WORDS = frozenset(
    "the and for with that this when what while from about into user users agent they them "
    "their was were are has have had not you your its who which then than been being one ones "
    "any all".split())
#: The retrievals recorded, the newest kept.
MOST_RECORDED = 5000
#: Where a retrieval was asked from, when the caller says it around the call (`retrieval_site`).
_SITE: contextvars.ContextVar = contextvars.ContextVar("lesson_retrieval_site", default="")


@contextmanager
def retrieval_site(name: str) -> Iterator[None]:
    """WHERE A RETRIEVAL IS FROM (the user, 2026-11-07: every retrieval recorded, with its
    site), for a call that goes through another's hands - `calendar_nature.relevant_text` -
    and cannot pass it on."""
    token = _SITE.set(str(name or ""))
    try:
        yield
    finally:
        _SITE.reset(token)


def _recording() -> bool:
    """RECORDED ONLY WHERE THE DEBUGGER IS (the user, 2026-11-07): an installed build without
    it keeps nothing of what was retrieved."""
    from ... import build_flags
    return bool(build_flags.debugger_available())


def is_english(text: str) -> bool:
    """Whether a text is English already: nine letters in ten Latin."""
    letters = [ch for ch in str(text or "") if ch.isalpha()]
    if not letters:
        return True
    return sum(1 for ch in letters if ch.isascii()) / len(letters) >= 0.9


def words_of(text: str) -> set:
    """A text's words in any language: Latin words of three letters or more,
    and pairs of Chinese, Japanese or Korean characters."""
    text = str(text or "").lower()
    found = set(re.findall(r"[a-zÀ-ɏЀ-ӿ]{3,}", text))
    for run in re.findall(r"[぀-ヿ一-鿿가-힯]+", text):
        found.update(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return found


def words_alike(query: str, text: str) -> float:
    """How alike two texts are by their words: those shared over the root of both counts,
    the common ones left out (measured against the old measure: see WORDS_FLOOR)."""
    asked, said = words_of(query) - COMMON_WORDS, words_of(text) - COMMON_WORDS
    if not asked or not said:
        return 0.0
    return len(asked & said) / math.sqrt(len(asked) * len(said))


class LessonRetriever:
    """Rules and examples, found by what a moment is like."""

    def __init__(self, engine: Any, embedder: Any = None, models: Any = None) -> None:
        self.engine = engine
        self.embedder = embedder
        self.models = models
        self._vectors: Dict[str, List[float]] = {}
        self._by_meaning = False
        self._rewritten: Dict[str, str] = {}
        self._reasons: List[Dict[str, Any]] = []
        self._reasons_at = 0.0
        self._recorded = 0

    # -- what there is -------------------------------------------------------------------
    def _learned_reasons(self) -> List[Dict[str, Any]]:
        """WHY THE USER JUDGES AS THEY DO (learning/reasons.py), found as a lesson is, for every
        decision (the user, 2026-11-06): each with when it holds. What they turned off is not
        given. Hypotheses: a decision leans on them, and never tells the user their motives."""
        if self._reasons_at and time.monotonic() - self._reasons_at < REASONS_FOR_SECONDS:
            return self._reasons
        out: List[Dict[str, Any]] = []
        try:
            from ...learning.find import Finder
            from ...learning.from_lessons import learned_from
            finder = Finder(self.engine.db)
            for reason in finder.store.reasons(("active",)):
                if not finder._giveable(reason):
                    continue
                when = str((reason.get("condition") or {}).get("text") or "")
                source = learned_from(finder.store, reason["id"], most=2)
                out.append({"id": reason["id"], "kind": "why they judge so", "state": "active",
                            "aspect": str(reason.get("aspect") or ""),
                            "statement": f"{reason['statement']}" + (f" (When: {when})" if when else "")
                            + (f" {source}" if source else ""),
                            "confidence": 0.7 if reason.get("source") == "inferred" else 0.85,
                            "pinned": False, "cases": len(finder.store.evidence(reason["id"]))})
        except Exception:
            log.debug("what was learned of the user's reasons could not be read", exc_info=True)
        self._reasons, self._reasons_at = out, time.monotonic()
        return out

    def _rules(self, kinds: Sequence[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for kind in kinds:
            out.extend(self.engine.rules(kind, ("active", "candidate")))
        return out

    def _other_rules(self, kinds: Sequence[str]) -> List[Dict[str, Any]]:
        """What the other types hold: their rules; the work's, its principles
        and the lesson cards."""
        out = self._rules([one for one in TYPES if one not in kinds])
        if "work" in kinds:
            return out
        out.extend(self._learned_reasons())
        # CANDIDATES TOO (the user, 2026-11-07): the work's that bear on a moment were often
        # candidates; they come at CANDIDATE_SHARE of their likeness, and said to be candidates.
        out.extend(self.engine.rules("work", ("active", "candidate")))
        try:
            for one in self.engine.groups("work").get("principles") or []:
                out.append({"id": one["id"], "kind": "work principle", "statement": one["statement"],
                            "state": "active", "confidence": 0.8, "pinned": False})
        except Exception:
            log.debug("the principles could not be read", exc_info=True)
        try:
            from ..experience.lesson_cards import LessonCards
            for one in LessonCards(self.engine.db).cards():
                out.append({"id": one["id"], "kind": "work card", "state": "active",
                            "statement": f"{one['question']} {one['answer']}".strip(),
                            "confidence": 0.8 if one.get("status") == "kept" else 0.6,
                            "pinned": False})
        except Exception:
            log.debug("the lesson cards could not be read", exc_info=True)
        return out

    def _examples(self, kinds: Sequence[str]) -> List[Dict[str, Any]]:
        marks = ",".join("?" for _ in kinds)
        return [dict(row) for row in self.engine.db.query(
            f"SELECT * FROM lesson_evidence WHERE kind IN ({marks}) AND action != 'outcome' "
            "ORDER BY at DESC LIMIT 400", tuple(kinds))]

    def _own_examples(self, rules: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
        """Each rule's supporting examples, as they are looked for by (`_said`), at most
        OWN_EXAMPLES, newest first: by rule id."""
        ids = [str(one.get("id") or "") for one in rules if str(one.get("id") or "")]
        out: Dict[str, List[str]] = {}
        try:
            for start in range(0, len(ids), 400):
                part = ids[start:start + 400]
                for row in self.engine.db.query(
                        "SELECT l.rule_id, e.english, e.summary, e.category, e.reason "
                        "FROM lesson_links l JOIN lesson_evidence e ON e.id = l.evidence_id "
                        f"WHERE l.relation = 'supports' AND l.rule_id IN ({', '.join('?' for _ in part)}) "
                        "ORDER BY e.at DESC", tuple(part)):
                    mine = out.setdefault(row["rule_id"], [])
                    if len(mine) < OWN_EXAMPLES:
                        text = self._said(dict(row))
                        if text:
                            mine.append(text)
        except Exception:
            log.debug("the rules' own examples could not be read", exc_info=True)
        return out

    @staticmethod
    def _said(example: Dict[str, Any]) -> str:
        """What an example is looked for by, IN ENGLISH (the user, 2026-11-07): its English
        copy, else its summary; its class; the user's words when they are English."""
        reason = str(example.get("reason") or "")
        return " ".join(str(part) for part in (
            example.get("english") or example.get("summary") or "", example.get("category") or "",
            reason if is_english(reason) else "") if part)

    # -- the moment ------------------------------------------------------------------------
    def moment_in_english(self, text: str, task: str) -> str:
        """THE MOMENT IN THE LESSONS' TERMS - the text itself when no model answers."""
        text = str(text or "").strip()[:4000]
        if not text or self.models is None or not task:
            return text
        if text in self._rewritten:
            return self._rewritten[text]
        from ...common.languages import in_english
        from ...models.base import ModelRequest
        try:
            response = self.models.invoke(ModelRequest(
                messages=[{"role": "user", "content": text}], system=RETRIEVAL_PROMPT,
                max_tokens=200, task=task, language=in_english("the description")),
                role="heartbeat")
            said = " ".join(str(getattr(response, "text", "") or "").split())[:600]
        except Exception:
            log.debug("the moment could not be said again for the lessons", exc_info=True)
            said = ""
        said = said or text
        kept = dict(self._rewritten)
        kept[text] = said
        while len(kept) > MOST_REWRITTEN:
            kept.pop(next(iter(kept)))
        self._rewritten = kept
        return said

    def _scored(self, query: str, texts: List[str]) -> Tuple[List[float], bool]:
        """How like `query` each text is, and whether by meaning (else by words). Said back
        together, not kept on the retriever: pages are looked for side by side."""
        embedder = self.embedder
        if embedder is not None:
            try:
                wanted = [one for one in dict.fromkeys(texts) if one not in self._vectors]
                if embedder.available():
                    read = embedder.embed([query] + wanted)
                    if read and len(read) == len(wanted) + 1:
                        kept = dict(self._vectors)
                        kept.update(zip(wanted, read[1:]))
                        self._vectors = kept
                        from ..states.tasks import _cosine
                        return [_cosine(read[0], kept[one]) for one in texts], True
            except Exception:
                log.debug("the lessons could not be read by meaning", exc_info=True)
        return [words_alike(query, one) for one in texts], False

    def _similar(self, query: str, texts: List[str]) -> List[float]:
        scores, self._by_meaning = self._scored(query, texts)
        return scores

    @staticmethod
    def _site(site: str) -> str:
        """Where a retrieval is from: as the caller said it, else around the call, else the
        function that asked (a pass-through such as calendar_nature's skipped)."""
        if site:
            return str(site)
        said = _SITE.get()
        if said:
            return said
        frame = sys._getframe(2)
        while frame is not None and str(frame.f_globals.get("__name__", "")).endswith(
                ("lessons.retrieve", "core.calendar_nature")):
            frame = frame.f_back
        if frame is None:
            return "unnamed"
        return f"{str(frame.f_globals.get('__name__', '')).rsplit('.', 1)[-1]}.{frame.f_code.co_name}"

    # -- finding -----------------------------------------------------------------------------
    def retrieve(self, kinds: Sequence[str], moment: str, rules: int = 8, examples: int = 4,
                 rewrite_task: str = "", site: str = "") -> Dict[str, Any]:
        """The rules and examples most like `moment` (what the thing is about), of every
        type: ranked together, `kinds` counting OWN_BONUS times, candidates CANDIDATE_SHARE,
        and none below the floor. Each comes with its `score`."""
        site = self._site(site)
        kinds = tuple(kinds)
        others = [one for one in TYPES if one not in kinds]
        all_rules = self._rules(kinds) + self._other_rules(kinds)
        all_examples = self._examples(kinds) + (self._examples(others) if others else [])
        moment = str(moment or "").strip()
        found: Dict[str, Any] = {"moment": moment, "query": "", "rules": [], "examples": [],
                                 "by_meaning": False, "site": site, "without_english": 0}
        if moment and (all_rules or all_examples):
            # IN ENGLISH (the user, 2026-11-07): the lessons are; a moment that is not is said
            # again in English first, and kept for when it comes again.
            query = moment if is_english(moment) else self.moment_in_english(moment, rewrite_task)
            # An example without its English copy is compared by what it has: said how many.
            found["without_english"] = sum(1 for one in all_examples if not one.get("english"))
            if found["without_english"]:
                log.debug("%s of %s examples have no English copy: compared by their summary",
                          found["without_english"], len(all_examples))
            own = self._own_examples(all_rules)
            theirs = [text for one in all_rules for text in own.get(str(one.get("id") or ""), [])]
            texts = ([str(one["statement"]) for one in all_rules] + [self._said(one)
                                                                     for one in all_examples]
                     + theirs)
            scores, by_meaning = self._scored(query, texts)
            floor = MEANING_FLOOR if by_meaning else WORDS_FLOOR
            # Each rule the best of its statement and its own examples.
            of_theirs = scores[len(all_rules) + len(all_examples):]
            scores = list(scores[:len(all_rules) + len(all_examples)])
            at = 0
            for index, one in enumerate(all_rules):
                count = len(own.get(str(one.get("id") or ""), []))
                if count:
                    scores[index] = max([scores[index], *of_theirs[at:at + count]])
                at += count
            speaking = any(kind in kinds for kind in SPEAKING_KINDS)

            def ranked(items: List[Dict[str, Any]], marks: List[float], room: int,
                       of_rules: bool) -> List[Dict[str, Any]]:
                """NO QUOTA (the user, 2026-11-07): one ranking, the bonus and the share in it,
                nothing below the floor."""
                weighed = []
                for item, mark in zip(items, marks):
                    score = float(mark) * (OWN_BONUS if str(item.get("kind") or "") in kinds else 1.0)
                    if of_rules and item.get("state") == "candidate":
                        score *= CANDIDATE_SHARE
                    if (of_rules and speaking and item.get("kind") == "why they judge so"
                            and str(item.get("aspect") or "") not in SPEAKING_ASPECTS):
                        score *= OTHER_ASPECT_SHARE
                    if score >= floor:
                        weighed.append((score, item))
                weighed.sort(key=lambda pair: -pair[0])
                return [dict(item, score=round(score, 4)) for score, item in weighed[:max(0, room)]]
            found.update(query=query, by_meaning=by_meaning,
                         rules=ranked(all_rules, scores[:len(all_rules)], rules, True),
                         examples=ranked(all_examples, scores[len(all_rules):], examples, False))
        given = sum(len(str(one.get("statement") or "")) for one in found["rules"]) + sum(
            len(self._said(one)) for one in found["examples"])
        found["record"] = self._record(found, given)
        return found

    # -- the record --------------------------------------------------------------------------
    def _record(self, found: Dict[str, Any], chars: int) -> str:
        """EVERY RETRIEVAL KEPT (the user, 2026-11-07), in a build with the Debugger only: where
        it was asked from, with what, and what came back with its score."""
        if not _recording():
            return ""
        db = getattr(self.engine, "db", None)
        if db is None:
            return ""
        try:
            from ...common.events.models import new_id
            ident = new_id("lret")
            results = [{"id": one.get("id"), "kind": one.get("kind"), "type": "rule",
                        "state": one.get("state"), "score": one.get("score")}
                       for one in found["rules"]] + [
                {"id": one.get("id"), "kind": one.get("kind"), "type": "example",
                 "state": one.get("action") or "", "score": one.get("score")}
                for one in found["examples"]]
            db.execute(
                "INSERT INTO lesson_retrievals (id, at, site, query, query_english, by_meaning, "
                "results, chars_given, without_english) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ident, datetime.now(timezone.utc).isoformat(), found["site"][:200],
                 found["moment"][:4000], str(found["query"] or "")[:4000],
                 1 if found["by_meaning"] else 0, json.dumps(results, ensure_ascii=False),
                 int(chars), int(found.get("without_english") or 0)))
            self._recorded += 1
            if self._recorded % 100 == 1:
                db.execute("DELETE FROM lesson_retrievals WHERE id NOT IN (SELECT id FROM "
                           "lesson_retrievals ORDER BY at DESC LIMIT ?)", (MOST_RECORDED,))
            return ident
        except Exception:
            log.debug("a retrieval could not be recorded", exc_info=True)
            return ""

    def _given(self, record: str, chars: int) -> None:
        """What the decision was given, in characters, once the text is made."""
        if not record:
            return
        try:
            self.engine.db.execute("UPDATE lesson_retrievals SET chars_given = ? WHERE id = ?",
                                   (int(chars), record))
        except Exception:
            log.debug("a retrieval's size could not be kept", exc_info=True)

    # -- what is given -----------------------------------------------------------------------
    def text(self, kinds: Sequence[str], moment: str, rules: int = 8, examples: int = 4,
             rewrite_task: str = "", site: str = "") -> str:
        """What a decision is given: the rules, then the examples."""
        found = self.retrieve(kinds, moment, rules, examples, rewrite_task, self._site(site))
        return self._said_of(found, kinds)

    def in_parts(self, kinds: Sequence[str], parts: Sequence[str], rules: int = 8,
                 examples: int = 4, rewrite_task: str = "", site: str = "") -> str:
        """A LONG MOMENT ASKED IN PARTS (the user, 2026-11-07: "按单个项目检索，不截取整段"): the
        meaning model reads about 250 words of a text, so a stretch of many states was found by
        its first ones. Each part is asked; each lesson kept once, at its best score; the best
        `rules` and `examples` of all given."""
        best_rules: Dict[str, Dict[str, Any]] = {}
        best_examples: Dict[str, Dict[str, Any]] = {}
        for part in [one for one in parts if str(one or "").strip()]:
            found = self.retrieve(kinds, part, rules, examples, rewrite_task, self._site(site))
            for into, items in ((best_rules, found["rules"]), (best_examples, found["examples"])):
                for item in items:
                    key = str(item.get("id") or "")
                    if key not in into or float(item.get("score") or 0) > float(
                            into[key].get("score") or 0):
                        into[key] = item
        merged = {
            "rules": sorted(best_rules.values(), key=lambda one: -float(one.get("score") or 0))[:rules],
            "examples": sorted(best_examples.values(),
                               key=lambda one: -float(one.get("score") or 0))[:examples],
            "record": ""}
        return self._said_of(merged, kinds)

    def _said_of(self, found: Dict[str, Any], kinds: Sequence[str]) -> str:
        lines: List[str] = []
        for rule in found["rules"]:
            cases = rule["cases"] if "cases" in rule else self.engine._cases(rule["id"])
            sure = f"{round(float(rule['confidence']) * 100)}% sure"
            if rule["state"] == "candidate":
                # A CANDIDATE SAID TO BE ONE (the user, 2026-11-07), with how little it rests on.
                how = f"a candidate, from {cases} case{'' if cases == 1 else 's'}; {sure}"
            else:
                how = (f"{'follow' if rule['confidence'] >= 0.8 or rule['pinned'] else 'lean on it'}"
                       f"; {sure}, {cases} case{'' if cases == 1 else 's'}")
            kind = str(rule.get("kind") or "")
            where = "" if kind in kinds else f"from {kind}: "
            lines.append(f"- [{rule['id']}] {where}{rule['statement']} ({how}"
                         f"{', set by the user' if rule['pinned'] else ''})")
        if found["examples"]:
            if lines:
                lines.append("")
            lines.append("WHAT THE USER ACTUALLY DID, in moments like this:")
            for one in found["examples"]:
                lines.append(self._example_line(one))
        said = "\n".join(lines)
        self._given(found.get("record", ""), len(said))
        return said

    def the_users_own(self, moment: str, task: str, site: str = "") -> str:
        """The lessons like this moment, said to be the user's own doing; ""
        when there are none, or they could not be read - a run goes on."""
        try:
            found = self.text(("general",), moment, rewrite_task=task, site=self._site(site))
        except Exception:
            log.info("the lessons could not be looked for", exc_info=True)
            return ""
        return THE_USERS_OWN + found if found else ""

    @staticmethod
    def _example_line(one: Dict[str, Any]) -> str:
        said = str(one.get("english") or one.get("summary") or "").strip()[:400]
        line = f"- {str(one.get('at') or '')[:10]}"
        if one.get("category"):
            line += f" [{one['category']}]"
        line += f" {one.get('action') or ''}: {said}"
        if one.get("reason"):
            line += f"\n    the user's words: \"{str(one['reason'])[:300]}\""
        content = str(one.get("content") or "").strip()
        if content:
            line += f"\n    exactly: {content[:MOST_SHOWN]}"
        return line


def recorded(db: Any, site: str = "", most: int = 200) -> Dict[str, Any]:
    """THE RETRIEVALS, newest first, for the Lessons debugger: each with its query and what
    came back, with scores; and every site with how many it asked."""
    most = max(1, min(1000, int(most)))
    sites = [{"site": row["site"], "count": int(row["n"]), "last": row["last"]}
             for row in db.query("SELECT site, COUNT(*) AS n, MAX(at) AS last FROM "
                                 "lesson_retrievals GROUP BY site ORDER BY last DESC")]
    rows = db.query("SELECT * FROM lesson_retrievals WHERE (? = '' OR site = ?) "
                    "ORDER BY at DESC LIMIT ?", (site, site, most))
    statements: Dict[str, str] = {}
    out: List[Dict[str, Any]] = []
    for row in rows:
        one = dict(row)
        try:
            one["results"] = json.loads(one.get("results") or "[]")
        except ValueError:
            one["results"] = []
        out.append(one)
        statements.update({str(r.get("id")): "" for r in one["results"] if r.get("id")})
    # What each one says, read once: a rule's statement, an example's English.
    ids = [ident for ident in statements if ident]
    for start in range(0, len(ids), 400):
        part = ids[start:start + 400]
        marks = ",".join("?" for _ in part)
        for row in db.query(f"SELECT id, statement FROM lesson_rules WHERE id IN ({marks})",
                            tuple(part)):
            statements[row["id"]] = str(row["statement"] or "")
        for row in db.query(f"SELECT id, english, summary FROM lesson_evidence WHERE id IN "
                            f"({marks})", tuple(part)):
            statements[row["id"]] = str(row["english"] or row["summary"] or "")
        try:
            for row in db.query(f"SELECT id, question, answer FROM lesson_cards WHERE id IN "
                                f"({marks})", tuple(part)):
                statements[row["id"]] = f"{row['question']} {row['answer']}".strip()
        except Exception:
            log.debug("the lesson cards could not be read", exc_info=True)
    for one in out:
        for result in one["results"]:
            result["said"] = statements.get(str(result.get("id")), "")[:400]
    return {"sites": sites, "retrievals": out, "recording": _recording()}


__all__ = ["LessonRetriever", "words_of", "words_alike", "is_english", "retrieval_site",
           "recorded", "RETRIEVAL_PROMPT", "OWN_BONUS", "CANDIDATE_SHARE", "MEANING_FLOOR",
           "SPEAKING_KINDS", "SPEAKING_ASPECTS", "OTHER_ASPECT_SHARE", "OWN_EXAMPLES",
           "WORDS_FLOOR"]

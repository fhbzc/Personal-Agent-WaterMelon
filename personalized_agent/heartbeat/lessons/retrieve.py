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
     (`rewrite_task`). Only when there is a choice to make: with no more
     lessons than are asked for, all of them are given and nothing is asked.
  2. BY MEANING - the moment against each rule's statement and each example's
     English, by the state heartbeat's embedding model (English on both sides
     now); with no model, by the words they share.
  3. GIVEN TOGETHER - the rules most like it, with how sure each is, and the
     examples most like it: what happened, what the user did, their words,
     and the start of the exact content.

EVERY TYPE IS LOOKED AT (the user, 2026-11-04): what was learned of a contact or
of the user's work bears on a notification too. A decision's own types come
first - half the room is theirs - and the rest goes to whatever else is most
like the moment, each said to be from where it is: the other types' rules, the
work's lessons in force and its principles (not its candidates: hundreds, and
mostly noise), and the lesson cards. What is hardly like it is left out.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Sequence

log = logging.getLogger(__name__)

#: How the moment is said again before the lessons like it are looked for.
RETRIEVAL_PROMPT = """Below is a moment in which the user's agent is about to decide
something: a window's title, the words that appeared, the project, what it may
say or write. Say it again in one or two plain English sentences, in general
terms that lessons about how the user wants things done would use: what the
user is doing, what kind of window and content it is, and what the decision is
about. Translate what is not English. No names of people. Answer with the
sentences alone."""

#: The moments said again, the newest kept.
MOST_REWRITTEN = 64
#: The types with examples and rules of this form.
TYPES = ("notifications", "connections", "general")
#: How like the moment another type's lesson must be, by meaning.
OTHERS_AT = 0.25
#: What is shown of an example's exact content.
MOST_SHOWN = 500


def words_of(text: str) -> set:
    """A text's words in any language: Latin words of three letters or more,
    and pairs of Chinese, Japanese or Korean characters."""
    text = str(text or "").lower()
    found = set(re.findall(r"[a-zÀ-ɏЀ-ӿ]{3,}", text))
    for run in re.findall(r"[぀-ヿ一-鿿가-힯]+", text):
        found.update(run[i:i + 2] for i in range(max(1, len(run) - 1)))
    return found


class LessonRetriever:
    """Rules and examples, found by what a moment is like."""

    def __init__(self, engine: Any, embedder: Any = None, models: Any = None) -> None:
        self.engine = engine
        self.embedder = embedder
        self.models = models
        self._vectors: Dict[str, List[float]] = {}
        self._by_meaning = False
        self._rewritten: Dict[str, str] = {}

    # -- what there is -------------------------------------------------------------------
    def _rules(self, kinds: Sequence[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for kind in kinds:
            out.extend(self.engine.rules(kind, ("active", "candidate")))
        return out

    def _other_rules(self, kinds: Sequence[str]) -> List[Dict[str, Any]]:
        """What the other types hold: their rules; the work's in force, its
        principles and the lesson cards."""
        out = self._rules([one for one in TYPES if one not in kinds])
        if "work" in kinds:
            return out
        out.extend(self.engine.rules("work", ("active",)))
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

    @staticmethod
    def _said(example: Dict[str, Any]) -> str:
        """What an example is looked for by: its English, else its summary."""
        return " ".join(str(part) for part in (
            example.get("english") or example.get("summary") or "", example.get("category") or "",
            example.get("reason") or "") if part)

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

    def _similar(self, query: str, texts: List[str]) -> List[float]:
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
                        self._by_meaning = True
                        return [_cosine(read[0], kept[one]) for one in texts]
            except Exception:
                log.debug("the lessons could not be read by meaning", exc_info=True)
        self._by_meaning = False
        asked = words_of(query)
        return [len(asked & words_of(one)) / max(1, len(words_of(one))) for one in texts]

    # -- finding -----------------------------------------------------------------------------
    def retrieve(self, kinds: Sequence[str], moment: str, rules: int = 8, examples: int = 4,
                 rewrite_task: str = "") -> Dict[str, Any]:
        """The rules and examples most like `moment`, `kinds` first and then
        any other type; all of them when there are no more than asked for."""
        own_rules, own_examples = self._rules(kinds), self._examples(kinds)
        other_rules = self._other_rules(kinds)
        others = [one for one in TYPES if one not in kinds]
        other_examples = self._examples(others) if others else []
        moment = str(moment or "").strip()
        all_rules, all_examples = own_rules + other_rules, own_examples + other_examples
        if not moment or (len(all_rules) <= rules and len(all_examples) <= examples):
            return {"query": moment, "rules": all_rules[:rules], "examples": all_examples[:examples]}
        query = self.moment_in_english(moment, rewrite_task)
        texts = [str(one["statement"]) for one in all_rules] + [self._said(one)
                                                                 for one in all_examples]
        scores = self._similar(query, texts)
        least = OTHERS_AT if self._by_meaning else 1e-9

        def chosen(own: List[Dict[str, Any]], other: List[Dict[str, Any]], marks: List[float],
                   room: int) -> List[Dict[str, Any]]:
            """Half for its own types; the rest to what is most like it."""
            mine = sorted(range(len(own)), key=lambda n: -marks[n])
            theirs = sorted((n for n in range(len(own), len(own) + len(other))
                             if marks[n] >= least), key=lambda n: -marks[n])
            first = mine[:(room + 1) // 2]
            rest = sorted(mine[len(first):] + theirs, key=lambda n: -marks[n])[:room - len(first)]
            every = own + other
            return [every[n] for n in first + rest]
        return {"query": query,
                "rules": chosen(own_rules, other_rules, scores[:len(all_rules)], rules),
                "examples": chosen(own_examples, other_examples, scores[len(all_rules):], examples)}

    def text(self, kinds: Sequence[str], moment: str, rules: int = 8, examples: int = 4,
             rewrite_task: str = "") -> str:
        """What a decision is given: the rules, then the examples."""
        found = self.retrieve(kinds, moment, rules, examples, rewrite_task)
        lines: List[str] = []
        for rule in found["rules"]:
            cases = self.engine._cases(rule["id"])
            how = ("follow" if rule["state"] == "active" and (
                rule["confidence"] >= 0.8 or rule["pinned"]) else
                "lean on it" if rule["state"] == "active" else "a possible rule, still forming")
            kind = str(rule.get("kind") or "")
            where = "" if kind in kinds else f"from {kind}: "
            lines.append(f"- [{rule['id']}] {where}{rule['statement']} ({how}; "
                         f"{round(float(rule['confidence']) * 100)}% sure, {cases} case"
                         f"{'' if cases == 1 else 's'}"
                         f"{', set by the user' if rule['pinned'] else ''})")
        if found["examples"]:
            if lines:
                lines.append("")
            lines.append("WHAT THE USER ACTUALLY DID, in moments like this:")
            for one in found["examples"]:
                lines.append(self._example_line(one))
        return "\n".join(lines)

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


__all__ = ["LessonRetriever", "words_of", "RETRIEVAL_PROMPT"]

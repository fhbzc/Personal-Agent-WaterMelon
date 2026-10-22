"""REASONS: why the user judges as they do - kept as hypotheses, tested by
what they do next.

A reason says WHY ("when you present in person, the audience should listen
to you, not read the screen"), WHERE it holds (a condition in the shared
words, with its exceptions), WHAT it predicts elsewhere, and WHEN IT WOULD
FAIL. It is STATED when the user said it, INFERRED otherwise. Each judgment
bears on it: it SUPPORTS it (and was perhaps PREDICTED by it), marks its
BOUNDARY, CONTRADICTS it, or is from BEFORE a change.

After each update's reading, three steps, each a model call only where
there is something for it:

  EXPLAIN  every new judgment against the reasons that may bear on it
           (picked by code: the same object or next to it, a prediction
           about it, a condition its case falls in). Nothing is rewritten.
  REVISE   a reason a judgment conflicts with: (a) its condition does not
           hold here - a boundary; (b) another reason wins here; (c) it is
           narrowed or replaced; (d) a one-off; (e) the user CHANGED, from
           a date on. A rewrite (c, e) is checked against ALL the reason's
           evidence (`_verify`): nothing the user said is lost (in (e) only
           what is from before the change, with the user's own later word
           or change against it), at least `KEEP_SHARE` of the rest is kept,
           a prediction that came true counts twice; in (e) all that is set
           aside is dated before the change, and after it at least two
           cases (or the user's own word) bear the new version out. Refused
           once: asked again with why; refused twice: the rewrite stands
           beside the reason as its RIVAL, until the evidence tells them
           apart (`compete`).
  ABDUCE   judgments no reason explains, together: a reason that explains
           them all - falsifiable, as deep as can still fail - or that one
           belongs to a reason already kept.

Dates are given to the model with everything; nothing is weighted by age
by code - the model may say a pattern is old, and (e) is how it says so.
A reason is ACTIVE (given to the reviewer as a rule) on one rule the user
wrote, two cases the user changed or approved, or three they accepted.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .match import coverage, fits_its_cases
from .store import WEIGHT, LearningStore, dumps, now
from .vocab import DIMENSIONS, OBJECT, Vocabulary

log = logging.getLogger(__name__)

#: Judgments explained in one call; reasons shown with them, at most.
EXPLAIN_BATCH = 25
REASONS_SHOWN = 15
#: A rewrite keeps at least this share of the evidence that is not the
#: user's own word.
KEEP_SHARE = 0.8
#: Evidence shown to a revision, at most (all the user's own words, and
#: the rest sampled across cases).
EVIDENCE_SHOWN = 15
#: Judgments checked in one verification call.
VERIFY_BATCH = 30
#: Pool judgments read together for a new reason.
ABDUCE_BATCH = 40
#: A rival wins on this much weighted evidence more than the other...
RIVAL_LEAD = 3.0
#: ...or, undecided this long, the reason it challenged stays.
RIVAL_DAYS = 90
#: A reason shares this much of its support with another: the same, asked.
SAME_SHARE = 0.5

REASON_SHAPE = """{"statement": "one sentence: why the user judges so",
 "aspect": "requirement | output | evaluation | habit",
 "condition": {"dims": {"deliverable": ["deliverable:..."], "presentation": ["..."]},
               "excluded": {"purpose": ["purpose:..."]},
               "text": "in words: when it holds"},
 "objects": ["object:..."],
 "predictions": [{"context": {"presentation": ["presentation:read alone"]},
                  "object": "object:...", "direction": "...", "text": "in words"}],
 "fails_when": "where it would not hold - what would show it wrong"}"""

EXPLAIN_PROMPT = """WHICH OF THE USER'S REASONS EXPLAIN THESE NEW JUDGMENTS OF THEIRS?

Below are REASONS already kept about why the user judges their work as they
do - each with its id, when it holds, what it predicts, how much bears on
it and when it was first and last seen - and NEW JUDGMENTS: what the user
decided or changed in a piece of work, with the case it was in (what kind
of thing, for whom, how presented) and its date.

For each judgment say:
  - explained_by: the reasons that, applied to this case, would lead to
    exactly this judgment - and "predicted": true when it is one of the
    reason's own written predictions;
  - conflicts: the reasons that, applied to this case, would lead to the
    OPPOSITE - with why in one sentence.
A reason whose condition plainly does not hold for this case neither
explains nor conflicts. Most judgments bear on one reason or none. Dates
matter: a reason last seen long ago may simply be old.

Return ONLY this JSON object:
{"judgments": [{"id": "J-...", "explained_by": [{"reason": "R-...", "predicted": false}],
                "conflicts": [{"reason": "R-...", "why": "..."}]}]}"""

REVISE_PROMPT = """A REASON OF THE USER'S IS CONTRADICTED. WHAT IS TRUE?

Below is a REASON - why the user judges so, when it holds, what it
predicts - with the EVIDENCE it rests on (dated), and NEW JUDGMENTS that
conflict with it. Other reasons kept are listed by one line each.

Choose ONE:
  "a" - its condition does not hold for the new case: it is a BOUNDARY. Say
        what to except ("excluded", in the shared words) and, if worth it,
        a prediction for such cases.
  "b" - ANOTHER REASON WINS HERE: an existing one (its id) or a new one;
        say "when" in words.
  "c" - the reason is TOO WIDE OR WRONG: rewrite it so it explains its
        evidence AND the new case - narrower, or a different reason.
  "d" - a ONE-OFF: the user made an exception this once (a hurry, a
        special demand) - say what made it so.
  "e" - the user CHANGED: they used to judge so, and from a date on they do
        not. Give "change_at" (YYYY-MM-DD) and the reason as it is now. Only
        when the evidence before that date and after it really differ.

Keep the reason as deep as can still be shown wrong - never "the user
values quality". The user's own words, quoted, stay as they wrote them.
{feedback}
Return ONLY this JSON object:
{"choice": "a|b|c|d|e", "why": "one or two sentences",
 "excluded": {"dim": ["term ids"]}, "prediction": null,
 "winner": {"reason": "R-... or new", "when": "in words", "new": null},
 "rewrite": null,
 "change_at": "", "set_aside": ["J-... the evidence from before the change"]}
where "rewrite" (c, e) and "winner.new" (b) are each one reason:
""" + REASON_SHAPE

VERIFY_PROMPT = """DOES THIS REASON EXPLAIN EACH OF THESE JUDGMENTS OF THE USER'S?

The REASON is shown with when it holds and what it predicts; then the
user's judgments, each with its case. For each say "yes" - applied to that
case, the reason leads to that judgment; "outside" - its condition does not
hold for that case; or "no" - it would lead elsewhere.

Return ONLY this JSON object:
{"judgments": {"J-...": "yes|outside|no"}}"""

ABDUCE_PROMPT = """WHY DOES THE USER JUDGE SO? FIND THE REASONS BEHIND THESE JUDGMENTS.

Below are judgments of the user's that no reason kept explains yet - what
they decided or changed, in which case (what kind of thing, for whom, how
presented), when, in their words where they gave them - and the reasons
already kept, one line each.

  - A judgment that one of the kept reasons does explain after all: say so
    in "belongs".
  - Otherwise find NEW REASONS: each explains at least two of these
    judgments - or one, when the user said why in their own words. Judgments
    about different things (slides and emails) may share one reason. A
    reason is the user's goal or constraint, as deep as can STILL BE SHOWN
    WRONG: "the audience should listen to you, not read the screen" - not
    "you value clear communication". Say where it would fail ("fails_when")
    and what it predicts in cases not seen yet.
  - "stated": true only when the user said the reason in their own words.
  - Leave a judgment that shares a reason with none of the others alone.

Return ONLY this JSON object:
{"belongs": [{"judgment": "J-...", "reason": "R-..."}],
 "reasons": [{...one reason..., "stated": false, "explains": ["J-..."]}]}
where each reason is:
""" + REASON_SHAPE

SAME_PROMPT = """ARE THESE TWO REASONS OF THE USER'S THE SAME REASON?

They rest on much of the same evidence. Say "same" when an agent would act
the same on either; then give the one wording that keeps both (the user's
own words as they wrote them).

Return ONLY this JSON object:
{"same": true or false, "statement": "", "condition_text": ""}"""

TERMS_PROMPT = """WHICH OF THESE WORDS MEAN THE SAME THING?

Below are the terms under one parent in a shared word list, each with its
definition and an example. Say which ones mean the same thing - an agent
would tag the same cases with either. Leave apart what differs.

Return ONLY this JSON object:
{"same": [["dim:term to keep", "dim:term that becomes it", ...], ...]}"""

SPLIT_PROMPT = """THIS TERM HAS GROWN TOO WIDE. SPLIT IT.

Below is a term of a shared word list and the judgments tagged with it.
Propose two to six terms under it that tell them apart, and put each
judgment under one (or leave it on the term itself when none fits).

Return ONLY this JSON object:
{"terms": [{"term": "", "definition": ""}],
 "judgments": {"J-...": "the new term, or \\"\\" to leave it"}}"""


def _days_between(a: str, b: str) -> float:
    try:
        one = datetime.fromisoformat(a[:10])
        two = datetime.fromisoformat(b[:10])
    except ValueError:
        return 0.0
    return (two - one).days


def _users_own(judgment: Dict[str, Any]) -> bool:
    """The user's own word: what they wrote for the agents, what an agent
    noted from their words, or what they said in so many words."""
    return judgment.get("strength") in ("written", "noted") or bool(judgment.get("said"))


class Reasons:
    def __init__(self, db: Any, ask: Callable[..., Dict[str, Any]]) -> None:
        self.db = db
        self.store = LearningStore(db)
        self.vocab = Vocabulary(db)
        self.ask = ask
        self.report: Dict[str, Any] = defaultdict(int)

    # -- shown to a model -----------------------------------------------------
    def _case_line(self, case_id: str) -> Tuple[str, Dict[str, Any]]:
        case = self.store.case(case_id) or {}
        return (f"{case.get('title', '')} [{', '.join(t for v in (case.get('facets') or {}).values() for t in v)}]",
                case.get("facets") or {})

    def _judgment_line(self, judgment: Dict[str, Any]) -> str:
        case, _facets = self._case_line(judgment["case_id"])
        said = f' said: "{judgment["said"]}"' if judgment.get("said") else ""
        why = f" why: {judgment['why']}" if judgment.get("why") else ""
        return (f"{judgment['id']} ({judgment.get('at') or '?'}, {judgment['strength']}) "
                f"in case {judgment['case_id']} {case}: {judgment['aspect']} / "
                f"{judgment['object']}{(' / ' + judgment['item']) if judgment.get('item') else ''}"
                f" -> {judgment['direction']}.{said}{why}")

    def _reason_line(self, reason: Dict[str, Any], example: bool = True) -> str:
        cond = reason.get("condition") or {}
        preds = "; ".join(str(p.get("text") or "") for p in reason.get("predictions") or [])
        line = (f"{reason['id']} [{reason['source']}, {reason['state']}, support "
                f"{reason['support']:.1f}, against {reason['contra']:.1f}, first seen "
                f"{reason.get('first_seen', '')[:10]}, last seen {reason.get('last_seen', '')[:10]}"
                f"{', changed since ' + reason['since'][:10] if reason.get('since') else ''}]: "
                f"{reason['statement']} WHEN: {cond.get('text', '')} "
                f"(in words of the list: {dumps(cond.get('dims') or {})}, except "
                f"{dumps(cond.get('excluded') or {})}). PREDICTS: {preds or '-'}")
        if example:
            supports = [one for one in self.store.evidence(reason["id"]) if one["role"] == "supports"]
            if supports:
                line += " EXAMPLE: " + self._judgment_line(supports[-1])
        return line

    # -- picking the reasons that may bear on judgments ------------------------
    def _shortlist(self, judgments: Sequence[Dict[str, Any]],
                   reasons: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        terms = self.vocab.by_id()
        scored = []
        for reason in reasons:
            score = 0.0
            objects = [self.vocab.resolve(one, terms) for one in reason.get("objects") or []]
            predicted = [self.vocab.resolve(p.get("object"), terms)
                         for p in reason.get("predictions") or [] if isinstance(p, dict)]
            for judgment in judgments:
                for one in objects:
                    how = self.vocab.relation(judgment["object"], one, terms)
                    score += {"same": 3, "under": 2, "over": 2, "sibling": 1}.get(how, 0)
                for one in predicted:
                    if self.vocab.relation(judgment["object"], one, terms) in ("same", "under", "over"):
                        score += 2
                _line, facets = self._case_line(judgment["case_id"])
                how, _why = coverage(reason.get("condition") or {}, facets, self.vocab, terms)
                score += {"covered": 1.0, "near": 0.5, "unknown": 0.25}.get(how, 0)
            if score > 0:
                scored.append((score, reason))
        scored.sort(key=lambda one: -one[0])
        return [reason for _score, reason in scored[:REASONS_SHOWN]]

    # -- explain --------------------------------------------------------------
    def explain(self, judgments: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
        """Link new judgments to the reasons that explain them; what
        conflicts, by reason; what nothing explains goes to the pool."""
        conflicts: Dict[str, List[str]] = defaultdict(list)
        reasons = self.store.reasons()
        terms = self.vocab.by_id()
        # By the branch of what they are about: like with like.
        by_branch: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for judgment in judgments:
            chain = self.vocab.ancestors(judgment["object"], terms)
            by_branch[chain[-1] if chain else judgment["object"]].append(judgment)
        for branch, group in by_branch.items():
            for start in range(0, len(group), EXPLAIN_BATCH):
                batch = group[start:start + EXPLAIN_BATCH]
                shortlist = self._shortlist(batch, reasons)
                if not shortlist:
                    for judgment in batch:
                        self.store.set_judgment_state(judgment["id"], "pool")
                    continue
                text = "REASONS:\n" + "\n".join(self._reason_line(r) for r in shortlist) + \
                    "\n\nNEW JUDGMENTS:\n" + "\n".join(self._judgment_line(j) for j in batch)
                said = self.ask("reasons", EXPLAIN_PROMPT, text, 4000, "the answer")
                if not said or said.get("_failed"):
                    self.report["explain_failed"] += 1
                    continue          # left "new": tried again next time
                known = {r["id"] for r in shortlist}
                answers = {str(one.get("id")): one for one in said.get("judgments") or []
                           if isinstance(one, dict)}
                for judgment in batch:
                    answer = answers.get(judgment["id"]) or {}
                    linked = False
                    for one in answer.get("explained_by") or []:
                        rid = str((one or {}).get("reason") or "")
                        if rid in known:
                            self.store.link(rid, judgment["id"], "supports",
                                            predicted=bool((one or {}).get("predicted")))
                            linked = True
                            self.report["explained"] += 1
                    for one in answer.get("conflicts") or []:
                        rid = str((one or {}).get("reason") or "")
                        if rid in known:
                            conflicts[rid].append(judgment["id"])
                            self.store.link(rid, judgment["id"], "contradicts",
                                            why=str((one or {}).get("why") or ""))
                            linked = True
                    self.store.set_judgment_state(judgment["id"], "linked" if linked else "pool")
        for rid in {one["id"] for one in reasons}:
            self.rescore(rid)
        return conflicts

    # -- scoring --------------------------------------------------------------
    def rescore(self, reason_id: str) -> Optional[Dict[str, Any]]:
        reason = self.store.reason(reason_id)
        if not reason:
            return None
        evidence = self.store.evidence(reason_id)
        supports = [one for one in evidence if one["role"] == "supports"]
        against = [one for one in evidence if one["role"] == "contradicts"
                   and not str(one.get("link_why") or "").startswith("one-off")]
        support = sum(WEIGHT.get(one["strength"], 1.0) * (1.5 if one["predicted"] else 1.0)
                      for one in supports)
        contra = sum(WEIGHT.get(one["strength"], 1.0) for one in against)
        dates = sorted(str(one.get("at") or "") for one in supports if one.get("at"))
        reason["support"], reason["contra"] = round(support, 2), round(contra, 2)
        if dates:
            reason["first_seen"], reason["last_seen"] = dates[0], dates[-1]
        if reason["state"] in ("candidate", "active"):
            reason["state"] = "active" if self._enough(supports) and \
                (support + 1) / (support + contra + 2) >= 0.5 else "candidate"
        self.store.save_reason(reason)
        return reason

    @staticmethod
    def _enough(supports: Sequence[Dict[str, Any]]) -> bool:
        """One rule the user wrote, two cases they changed or approved, or
        three they let stand."""
        if any(one["strength"] in ("written", "noted") for one in supports):
            return True
        strong = {one["case_id"] for one in supports if one["strength"] in ("changed", "approved")}
        if len(strong) >= 2:
            return True
        return len({one["case_id"] for one in supports}) >= 3

    # -- revise ---------------------------------------------------------------
    def _evidence_shown(self, reason_id: str) -> List[Dict[str, Any]]:
        evidence = [one for one in self.store.evidence(reason_id)
                    if one["role"] in ("supports", "boundary", "before")]
        own = [one for one in evidence if _users_own(one)]
        rest = [one for one in evidence if not _users_own(one)]
        # The rest across as many different cases as there are.
        seen_cases, sample = set(), []
        for one in sorted(rest, key=lambda j: str(j.get("at") or ""), reverse=True):
            if one["case_id"] not in seen_cases:
                sample.append(one)
                seen_cases.add(one["case_id"])
        for one in rest:
            if len(own) + len(sample) >= EVIDENCE_SHOWN:
                break
            if one not in sample:
                sample.append(one)
        return own + sample[:max(0, EVIDENCE_SHOWN - len(own))]

    def revise(self, reason_id: str, conflicting: Sequence[str]) -> str:
        """Settle a conflict; what was done (a-e, rival, or "")."""
        reason = self.store.reason(reason_id)
        if not reason or reason["state"] == "retired":
            return ""
        news = [self.store.judgment(one) for one in conflicting]
        news = [one for one in news if one]
        if not news:
            return ""
        others = [r for r in self.store.reasons() if r["id"] != reason_id]
        feedback = ""
        for attempt in range(2):
            text = ("THE REASON:\n" + self._reason_line(reason, example=False)
                    + "\n\nITS EVIDENCE:\n" + "\n".join(
                        f"[{one['role']}{', predicted' if one['predicted'] else ''}] "
                        + self._judgment_line(one) for one in self._evidence_shown(reason_id))
                    + "\n\nTHE NEW JUDGMENTS THAT CONFLICT:\n"
                    + "\n".join(self._judgment_line(one) for one in news)
                    + "\n\nOTHER REASONS:\n" + "\n".join(
                        f"{r['id']}: {r['statement']}" for r in others[:60])
                    + "\n\nTHE WORD LIST:\n" + self.vocab.listing(list(DIMENSIONS) + [OBJECT]))
            said = self.ask("reasons", REVISE_PROMPT.replace("{feedback}", feedback), text,
                            4000, "the answer")
            if not said or said.get("_failed"):
                self.report["revise_failed"] += 1
                return ""
            choice = str(said.get("choice") or "").lower()[:1]
            why = str(said.get("why") or "")[:400]
            if choice == "a":
                return self._boundary(reason, news, said, why)
            if choice == "b":
                return self._other_wins(reason, news, said, why)
            if choice == "d":
                for one in news:
                    self.store.link(reason_id, one["id"], "contradicts", why="one-off: " + why)
                self.rescore(reason_id)
                self.report["one_off"] += 1
                return "d"
            if choice in ("c", "e"):
                ok, failed = self._rewrite(reason, news, said, choice, why)
                if ok:
                    return choice
                feedback = ("\nYOUR LAST ANSWER WAS REFUSED: " + failed
                            + " Answer again - a version that keeps that evidence, or "
                            "another choice.\n")
                continue
            return ""
        # Refused twice: the rewrite stands beside the reason as its rival.
        return self._make_rival(reason, news, said)

    def _boundary(self, reason: Dict[str, Any], news: Sequence[Dict[str, Any]],
                  said: Dict[str, Any], why: str) -> str:
        condition = dict(reason.get("condition") or {})
        excluded = dict(condition.get("excluded") or {})
        terms = self.vocab.by_id()
        for dim, values in (said.get("excluded") or {}).items():
            if dim not in DIMENSIONS:
                continue
            tags = [self.vocab.resolve(one, terms) for one in values or []]
            excluded[dim] = sorted(set(excluded.get(dim) or []) | {t for t in tags if t})
        condition["excluded"] = excluded
        reason["condition"] = condition
        prediction = said.get("prediction")
        if isinstance(prediction, dict) and prediction.get("text"):
            reason["predictions"] = list(reason.get("predictions") or []) + [prediction]
        reason["history"] = list(reason.get("history") or []) + [
            {"at": now(), "what": "boundary", "why": why, "judgments": [one["id"] for one in news]}]
        self.store.save_reason(reason)
        for one in news:
            self.store.link(reason["id"], one["id"], "boundary", why=why)
        self.rescore(reason["id"])
        self.report["boundary"] += 1
        return "a"

    def _other_wins(self, reason: Dict[str, Any], news: Sequence[Dict[str, Any]],
                    said: Dict[str, Any], why: str) -> str:
        winner = said.get("winner") if isinstance(said.get("winner"), dict) else {}
        wid = str(winner.get("reason") or "")
        if not (wid.startswith("R-") and self.store.reason(wid)):
            new = winner.get("new") if isinstance(winner.get("new"), dict) else None
            if not new:
                return ""
            wid = self._new_reason(new, [one["id"] for one in news],
                                   stated=any(one.get("said") for one in news))
            if not wid:
                return ""
        for one in news:
            self.store.unlink(reason["id"], one["id"])
            self.store.link(reason["id"], one["id"], "boundary", why="another reason wins: " + why)
            self.store.link(wid, one["id"], "supports")
        self.store.override(wid, reason["id"], str(winner.get("when") or why),
                            news[0]["id"])
        self.rescore(wid)
        self.rescore(reason["id"])
        self.report["overridden"] += 1
        return "b"

    def _clean_reason(self, said: Dict[str, Any], case_ids: Sequence[str]) -> Dict[str, Any]:
        """A reason as a model wrote it, its tags read through the word
        list and its condition made to hold for its own cases."""
        terms = self.vocab.by_id()
        condition = said.get("condition") if isinstance(said.get("condition"), dict) else {}
        dims = {dim: [t for t in (self.vocab.resolve(v, terms) for v in values or []) if t]
                for dim, values in (condition.get("dims") or {}).items() if dim in DIMENSIONS}
        excluded = {dim: [t for t in (self.vocab.resolve(v, terms) for v in values or []) if t]
                    for dim, values in (condition.get("excluded") or {}).items()
                    if dim in DIMENSIONS}
        condition = {"dims": {k: v for k, v in dims.items() if v},
                     "excluded": {k: v for k, v in excluded.items() if v},
                     "text": str(condition.get("text") or "")[:400]}
        facets = [(self.store.case(one) or {}).get("facets") or {} for one in case_ids]
        condition = fits_its_cases(condition, facets, self.vocab, terms)
        predictions = []
        for one in said.get("predictions") or []:
            if isinstance(one, dict) and one.get("text"):
                predictions.append({"context": one.get("context") or {},
                                    "object": self.vocab.resolve(one.get("object"), terms),
                                    "direction": str(one.get("direction") or "")[:200],
                                    "text": str(one["text"])[:300]})
        return {"statement": str(said.get("statement") or "")[:500],
                "aspect": str(said.get("aspect") or ""),
                "condition": condition,
                "objects": [t for t in (self.vocab.resolve(o, terms) for o in said.get("objects") or []) if t],
                "predictions": predictions[:8],
                "fails_when": str(said.get("fails_when") or "")[:300]}

    def _verify(self, version: Dict[str, Any], judgments: Sequence[Dict[str, Any]]
                ) -> Dict[str, str]:
        """What a version of a reason says of each judgment: yes, outside, no."""
        out: Dict[str, str] = {}
        line = (f"{version['statement']} WHEN: {(version.get('condition') or {}).get('text', '')} "
                f"PREDICTS: {'; '.join(p.get('text', '') for p in version.get('predictions') or [])}")
        for start in range(0, len(judgments), VERIFY_BATCH):
            batch = judgments[start:start + VERIFY_BATCH]
            said = self.ask("reasons", VERIFY_PROMPT, "THE REASON: " + line + "\n\nJUDGMENTS:\n"
                            + "\n".join(self._judgment_line(one) for one in batch), 2000,
                            "the answer")
            answers = said.get("judgments") if isinstance(said.get("judgments"), dict) else {}
            for one in batch:
                out[one["id"]] = str(answers.get(one["id"]) or "unchecked").lower()
        return out

    def _rewrite(self, reason: Dict[str, Any], news: Sequence[Dict[str, Any]],
                 said: Dict[str, Any], choice: str, why: str) -> Tuple[bool, str]:
        rewrite = said.get("rewrite") if isinstance(said.get("rewrite"), dict) else None
        if not rewrite or not rewrite.get("statement"):
            return False, "it gave no rewritten reason."
        evidence = [one for one in self.store.evidence(reason["id"])
                    if one["role"] in ("supports", "boundary")]
        change_at = str(said.get("change_at") or "")[:10]
        set_aside = set(str(one) for one in said.get("set_aside") or [])
        if choice == "e":
            if not change_at:
                return False, "a change over time needs the date it changed (change_at)."
            # ALL that is set aside is from before the change.
            early = [one for one in evidence if one["id"] in set_aside]
            late_named = [one for one in early if str(one.get("at") or "") >= change_at]
            if late_named:
                return False, (f"{', '.join(one['id'] for one in late_named)} is from after "
                               f"{change_at} and cannot be set aside as from before it.")
            if any(str(one.get("at") or "") < change_at for one in news):
                return False, "the conflicting judgment is from before the change."
        else:
            set_aside = set()
        kept_evidence = [one for one in evidence if one["id"] not in set_aside]
        cases = list({one["case_id"] for one in kept_evidence + list(news)})
        version = self._clean_reason(rewrite, cases)
        verdicts = self._verify(version, kept_evidence + list(news))
        # The new judgments must be explained (or outside, for a boundary).
        if any(verdicts.get(one["id"]) not in ("yes",) for one in news):
            return False, "the rewritten reason does not explain the new judgments."
        lost_own = [one for one in kept_evidence if one["role"] == "supports"
                    and _users_own(one) and verdicts.get(one["id"]) != "yes"]
        if lost_own:
            return False, (f"it no longer explains {', '.join(one['id'] for one in lost_own)}, "
                           "the user's own words.")
        if choice == "e":
            # A set-aside word of the user's needs their own later word or
            # change against it.
            own_aside = [one for one in evidence if one["id"] in set_aside and _users_own(one)]
            strong_after = [one for one in news if _users_own(one) or one["strength"] == "changed"]
            if own_aside and not strong_after:
                return False, ("the user's own words from before cannot be set aside without "
                               "their own later word or change against them.")
            after = [one for one in kept_evidence + list(news)
                     if str(one.get("at") or "") >= change_at and verdicts.get(one["id"]) == "yes"]
            if len({one["case_id"] for one in after}) < 2 and not any(_users_own(one) for one in after):
                return False, ("after the change, at least two cases - or the user's own word - "
                               "must bear the new version out.")
        weight = total = 0.0
        for one in kept_evidence:
            if one["role"] != "supports" or _users_own(one):
                continue
            share = 2.0 if one["predicted"] else 1.0
            total += share
            if verdicts.get(one["id"]) == "yes":
                weight += share
        if total and weight / total < KEEP_SHARE:
            return False, (f"it keeps only {weight:.0f} of {total:.0f} of the evidence "
                           f"(at least {int(KEEP_SHARE * 100)}% must be kept).")
        boundary_lost = [one for one in kept_evidence if one["role"] == "boundary"
                         and verdicts.get(one["id"]) == "yes"]
        # Accepted: the reason becomes the version; what it no longer
        # explains goes back to the pool.
        reason["history"] = list(reason.get("history") or []) + [{
            "at": now(), "what": "changed over time" if choice == "e" else "rewritten",
            "why": why, "before": {key: reason.get(key) for key in (
                "statement", "condition", "objects", "predictions", "since")},
            "until": change_at if choice == "e" else "",
            "set_aside": sorted(set_aside)}]
        for key in ("statement", "aspect", "condition", "objects", "predictions"):
            if version.get(key) or key == "predictions":
                reason[key] = version[key]
        if choice == "e":
            reason["since"] = change_at
        self.store.save_reason(reason)
        for one in evidence:
            if one["id"] in set_aside:
                self.store.link(reason["id"], one["id"], "before", why=f"before {change_at}")
            elif verdicts.get(one["id"]) == "yes":
                self.store.link(reason["id"], one["id"], "supports", predicted=one["predicted"])
            elif verdicts.get(one["id"]) == "outside" and one["role"] == "boundary":
                continue
            else:
                self.store.unlink(reason["id"], one["id"])
                if not self.store.links_of_judgment(one["id"]):
                    self.store.set_judgment_state(one["id"], "pool")
        for one in news:
            self.store.link(reason["id"], one["id"], "supports")
        for one in boundary_lost:
            self.store.link(reason["id"], one["id"], "supports")
        self.rescore(reason["id"])
        self.report["rewritten" if choice == "c" else "changed_over_time"] += 1
        return True, ""

    def _make_rival(self, reason: Dict[str, Any], news: Sequence[Dict[str, Any]],
                    said: Dict[str, Any]) -> str:
        rewrite = said.get("rewrite") if isinstance(said.get("rewrite"), dict) else None
        if not rewrite or reason.get("rival"):
            # A reason has one rival at a time: the conflict is evidence.
            self.rescore(reason["id"])
            return ""
        rid = self._new_reason(rewrite, [one["id"] for one in news],
                               stated=any(one.get("said") for one in news), state="competing")
        if not rid:
            return ""
        reason["rival"], reason["rival_since"] = rid, now()
        reason["state"] = "competing"
        self.store.save_reason(reason)
        rival = self.store.reason(rid)
        rival["rival"], rival["rival_since"] = reason["id"], now()
        self.store.save_reason(rival)
        self.report["rivals"] += 1
        return "rival"

    def _new_reason(self, said: Dict[str, Any], judgment_ids: Sequence[str], stated: bool,
                    state: str = "candidate") -> str:
        judgments = [self.store.judgment(one) for one in judgment_ids]
        judgments = [one for one in judgments if one]
        if not judgments or not str(said.get("statement") or "").strip():
            return ""
        if not str(said.get("fails_when") or "").strip():
            self.report["not_falsifiable"] += 1
            return ""
        version = self._clean_reason(said, [one["case_id"] for one in judgments])
        reason = {"statement": version["statement"], "aspect": version["aspect"],
                  "source": "stated" if stated and any(_users_own(one) for one in judgments)
                  else "inferred",
                  "condition": dict(version["condition"], fails_when=version["fails_when"]),
                  "objects": version["objects"] or sorted({one["object"] for one in judgments}),
                  "predictions": version["predictions"], "state": state, "history": []}
        rid = self.store.save_reason(reason)
        for one in judgments:
            self.store.link(rid, one["id"], "supports")
            self.store.set_judgment_state(one["id"], "linked")
        self.rescore(rid)
        self.report["new_reasons"] += 1
        return rid

    # -- abduce ---------------------------------------------------------------
    def abduce(self) -> None:
        pool = self.store.judgments("state = 'pool' ORDER BY at")
        if len(pool) < 1:
            return
        everyone = self.store.reasons()
        for start in range(0, len(pool), ABDUCE_BATCH):
            batch = pool[start:start + ABDUCE_BATCH]
            if len(batch) < 2 and not any(_users_own(one) for one in batch):
                continue
            text = ("JUDGMENTS NO REASON EXPLAINS:\n"
                    + "\n".join(self._judgment_line(one) for one in batch)
                    + "\n\nREASONS KEPT:\n" + ("\n".join(
                        f"{r['id']}: {r['statement']}" for r in everyone) or "none yet")
                    + "\n\nTHE WORD LIST:\n" + self.vocab.listing(list(DIMENSIONS) + [OBJECT]))
            said = self.ask("reasons", ABDUCE_PROMPT, text, 6000, "the reasons")
            if not said or said.get("_failed"):
                self.report["abduce_failed"] += 1
                continue
            known = {r["id"] for r in everyone}
            here = {one["id"]: one for one in batch}
            for one in said.get("belongs") or []:
                if isinstance(one, dict) and one.get("judgment") in here \
                        and one.get("reason") in known:
                    self.store.link(one["reason"], one["judgment"], "supports")
                    self.store.set_judgment_state(one["judgment"], "linked")
                    self.rescore(one["reason"])
            for one in said.get("reasons") or []:
                if not isinstance(one, dict):
                    continue
                ids = [j for j in one.get("explains") or [] if j in here]
                own = [here[j] for j in ids if _users_own(here[j])]
                if len(ids) < 2 and not own:
                    continue
                self._new_reason(one, ids, stated=bool(one.get("stated")))

    # -- rivals ---------------------------------------------------------------
    def compete(self) -> None:
        """Settle the rivals the evidence has told apart - or that waited
        too long."""
        for reason in self.store.reasons(("competing",)):
            rival = self.store.reason(reason.get("rival") or "")
            if not rival or rival["state"] == "retired":
                reason["state"], reason["rival"] = "active", ""
                self.store.save_reason(reason)
                self.rescore(reason["id"])
                continue
            if rival["state"] == "competing" and reason["id"] > rival["id"]:
                continue          # each pair once, from the lower id
            since = reason.get("rival_since") or ""
            mine = {one["id"]: one for one in self.store.evidence(reason["id"])
                    if str(one.get("created_at") or "") >= since}
            theirs = {one["id"]: one for one in self.store.evidence(rival["id"])
                      if str(one.get("created_at") or "") >= since}

            def lead(a: Dict[str, Any], b: Dict[str, Any]) -> float:
                score = 0.0
                for jid, one in a.items():
                    other = b.get(jid)
                    if one["role"] == "supports" and (other is None or other["role"] == "contradicts"):
                        score += WEIGHT.get(one["strength"], 1.0)
                return score

            ahead = lead(mine, theirs) - lead(theirs, mine)
            old = bool(since) and (datetime.now(timezone.utc) - datetime.fromisoformat(
                since.replace("Z", "+00:00"))).days >= RIVAL_DAYS
            both_against = any(one["role"] == "contradicts" for one in mine.values()) and \
                any(one["role"] == "contradicts" for one in theirs.values())
            if ahead >= RIVAL_LEAD or (old and ahead >= 0 and not both_against):
                self._retire(rival, f"its rival {reason['id']} held better")
                reason["state"], reason["rival"] = "active", ""
            elif -ahead >= RIVAL_LEAD:
                self._retire(reason, f"its rival {rival['id']} held better")
                rival["state"], rival["rival"] = "active", ""
                self.store.save_reason(rival)
                self.rescore(rival["id"])
                continue
            elif old:
                self._retire(rival, f"undecided after {RIVAL_DAYS} days: the reason stays")
                reason["state"], reason["rival"] = "active", ""
            else:
                continue
            self.store.save_reason(reason)
            self.rescore(reason["id"])

    def _retire(self, reason: Dict[str, Any], why: str) -> None:
        reason["state"] = "retired"
        reason["rival"] = ""
        reason["history"] = list(reason.get("history") or []) + [
            {"at": now(), "what": "retired", "why": why}]
        self.store.save_reason(reason)
        for one in self.store.evidence(reason["id"]):
            if not [l for l in self.store.links_of_judgment(one["id"])
                    if l["reason_id"] != reason["id"]]:
                self.store.set_judgment_state(one["id"], "pool")

    # -- the same reason twice -------------------------------------------------
    def merge_same(self) -> None:
        reasons = self.store.reasons(("candidate", "active"))
        support = {r["id"]: {one["id"] for one in self.store.evidence(r["id"])
                             if one["role"] == "supports"} for r in reasons}
        done = set()
        for one in reasons:
            for other in reasons:
                if one["id"] >= other["id"] or one["id"] in done or other["id"] in done:
                    continue
                a, b = support[one["id"]], support[other["id"]]
                if not a or not b or len(a & b) / min(len(a), len(b)) < SAME_SHARE:
                    continue
                said = self.ask("reasons", SAME_PROMPT, "ONE: " + self._reason_line(one, False)
                                + "\nTWO: " + self._reason_line(other, False), 800, "the answer")
                if not said.get("same"):
                    continue
                keep, gone = (one, other) if one["created_at"] <= other["created_at"] else (other, one)
                if said.get("statement"):
                    keep["history"] = list(keep.get("history") or []) + [
                        {"at": now(), "what": "merged", "gone": gone["id"],
                         "before": keep["statement"]}]
                    keep["statement"] = str(said["statement"])[:500]
                    if keep["source"] != "stated" and gone["source"] == "stated":
                        keep["source"] = "stated"
                self.store.save_reason(keep)
                for link in self.store.links_of_reason(gone["id"]):
                    self.store.link(keep["id"], link["judgment_id"], link["role"],
                                    why=link["why"], predicted=bool(link["predicted"]))
                self._retire(gone, f"the same as {keep['id']}")
                self.rescore(keep["id"])
                done.add(gone["id"])
                self.report["merged"] += 1

    # -- the word list ---------------------------------------------------------
    def tend_terms(self, first_learning: bool) -> None:
        self.report["terms_folded"] += len(self.vocab.prune(first_learning))
        terms = self.vocab.by_id()
        parents = defaultdict(list)
        for row in terms.values():
            if row["state"] in ("trial", "kept") and row["parent"]:
                parents[row["parent"]].append(row)
        for parent, children in parents.items():
            if len(children) < 2:
                continue
            siblings = self.vocab.children(parent)
            text = f"PARENT: {parent}\n" + "\n".join(
                f"- {row['id']}: {row['definition']} (e.g. {row['example']})" for row in siblings)
            said = self.ask("terms", TERMS_PROMPT, text, 1000, "the answer")
            for group in said.get("same") or []:
                if isinstance(group, list) and len(group) >= 2:
                    for gone in group[1:]:
                        if self.vocab.merge(str(gone), str(group[0])):
                            self.report["terms_merged"] += 1
        # A term that grew too wide is split.
        rows = self.db.query("SELECT object, COUNT(*) AS n FROM learn_judgments GROUP BY object "
                             "HAVING n > 60")
        for row in rows:
            tag = row["object"]
            if self.vocab.depth(tag) >= 3:
                continue
            judgments = self.store.judgments("object = ? ORDER BY at DESC LIMIT 120", (tag,))
            said = self.ask("terms", SPLIT_PROMPT, f"THE TERM: {tag}\n" + "\n".join(
                f"{one['id']}: {one['direction']} ({one['item']})" for one in judgments), 3000,
                "the terms")
            made = {}
            for one in said.get("terms") or []:
                if isinstance(one, dict) and one.get("term"):
                    new = self.vocab.add(OBJECT, str(one["term"]), tag, str(one.get("definition") or ""))
                    if new and new != tag:
                        made[str(one["term"]).strip().lower()] = new
            for jid, term in (said.get("judgments") or {}).items():
                new = made.get(str(term or "").strip().lower())
                if new:
                    self.db.execute("UPDATE learn_judgments SET object = ? WHERE id = ?",
                                    (new, jid))
            if made:
                self.report["terms_split"] += 1

    # -- one update's reasoning ---------------------------------------------------
    def run(self, first_learning: bool, stopped: Optional[Callable[[], bool]] = None
            ) -> Dict[str, Any]:
        news = self.store.judgments("state = 'new' ORDER BY at")
        if news:
            conflicts = self.explain(news)
            for rid, ids in conflicts.items():
                if stopped is not None and stopped():
                    break
                self.revise(rid, ids)
        if stopped is None or not stopped():
            self.abduce()
        if stopped is None or not stopped():
            self.compete()
        if first_learning and (stopped is None or not stopped()):
            self.merge_same()
        if stopped is None or not stopped():
            self.tend_terms(first_learning)
        return dict(self.report)

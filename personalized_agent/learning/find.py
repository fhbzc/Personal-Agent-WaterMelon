"""WHAT A WORK RUN'S REVIEWER IS GIVEN: its questions, answered from what was
learned of the user's work.

The reviewer first writes the job's terms (from the shared word list) and
its QUESTIONS - each of a kind: an extra requirement, the output's form and
style, how a result is judged, how the work is done, or a fact or limit of
this project. Then:

  REASONS, by whether their condition covers the job (`match.coverage`):
  covered, near (one dimension off by a sibling), or unknown - never the
  ones plainly outside; then a model reads each one's condition and says
  whether it holds this time (yes / no / unknown - an unknown becomes a
  question for the reviewer), WHICH OF THE QUESTIONS IT ANSWERS and how
  well, and - answering none - whether this user would expect it of this
  job all the same. Each question is given its `PER_QUESTION` best; the
  unasked, `EXPECTED` more; the rest none (the user, 2026-10-23: thirty-four
  reasons stacked under five headings answered "which stack?" with commit
  habits). A reason the user stated comes first among equals; an inferred
  one is given only on two observations or more, said to be inferred; each
  with one short example. A rival is one line beside it.

  CASES, chosen as the reasons are (the user, 2026-10-24): the `CASE_POOL`
  with most of the job's terms (`match.share`) go to the same model call,
  each with what it looks like and how it was made, which says how alike
  each is to the job, which questions it helps answer, and which are the
  same project. The most alike `DETAILED` are given in full - where the
  deliverables are, what they look like, every judgment in them, how they
  were made - one place a project, its other cases with it; the rest as a
  short index; a fact or a way of working no reason answers points to the
  cases said to answer it. When those in full come to more than
  `CASES_CHARS`, a second call picks, of each long one, the judgments that
  bear on the questions; the rest are opened with `open_case`. What a case
  was made with and how stays whole: the answer to "which stack, which
  tools" is there, not in a reason. With no model, by their terms.

  PACKED BY PRIORITY within `PACKAGE_CHARS`: each question's first reason,
  then the cases and the pointer to the facts, then the unasked reasons,
  then each question's second and third - a reason that does not fit is
  left out whole, never cut through.

  THE JUDGMENTS no reason explains yet, in those cases - plainly said.

The reviewer may open more (`open_case`, `open_reason`) and search the
files themselves. A case is expanded once in a run; after, its id is
enough. Nothing here is learned.

WHAT THE LEARNING LIST BROUGHT (items.py) is given as what it is: a case of
someone else's work says so, with what of it the user endorses; a reason
ADOPTED from it says where it comes from - so the reviewer knows which is
the user's own and which they endorse, and weighs them itself.

LEFT OUT (leave_out.py): a run may be given nothing of some folders - a
Work debugger project's own, and those the user names for it. A case of
one is never shown nor opened; of any other, the judgments and places that
lie in one are not shown; and a reason is given on what is left of its
support.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .leave_out import LeaveOut
from .match import closeness, coverage, share
from .store import LearningStore, dumps
from .vocab import ASPECTS, DIMENSIONS, Vocabulary

#: Cases given in full; in the short index; judged by the model, at most.
DETAILED = 3
INDEXED = 10
CASE_POOL = 10
#: Cases of one unit among the detailed, at most.
PER_UNIT = 2
#: What a package may take, at most.
PACKAGE_CHARS = 40_000
#: Reasons for one question; reasons asked about by none that the user
#: would expect all the same.
PER_QUESTION = 5
EXPECTED = 5
#: How well a reason must answer a question to be given for it: 2, bears on
#: it. One that only touches it (1) took the places of better answers - the
#: design-first habit under "how is the merged result shown" (2026-10-23).
ANSWERS_AT_LEAST = 2
#: The cases in full, together, beyond which their judgments are picked; and
#: the judgments of one long case picked, at most.
CASES_CHARS = 15_000
JUDGMENTS_PICKED = 10
#: Reasons on a page of `open_reason`'s cases.
PAGE = 15

QUESTION_KINDS = {
    "requirement": "an extra requirement the user would expect",
    "output": "the form, content or style of the output",
    "evaluation": "how a result is checked and judged",
    "habit": "how the work is done: steps, tools, habits",
    "fact": "a fact or limit of this project",
}

HOLDS_PROMPT = """DOES EACH OF THESE REASONS OF THE USER'S HOLD FOR THIS JOB - AND
WHICH OF THE REVIEWER'S QUESTIONS DOES IT ANSWER? HOW ALIKE TO THIS JOB IS
EACH OF THESE CASES OF THEIR PAST WORK?

Each reason says why the user judges their work so, and WHEN it holds (and
what it is excepted from). For the job described, say for each:
  "holds": "yes" - its condition holds here: "here" says in one sentence
           what it asks of this job;
           "no" - it does not<NO>;
           "unknown" - it depends on something the job does not say: write
           the question that would settle it.
  "answers": the reviewer's questions it bears on, by number, each with 3
           (answers it), 2 (bears on it) or 1 (touches it) - {} for none.
           Bearing on a question is about what the reason says, not its
           heading: a habit of committing does not answer "which stack".
  "expected": true only when it answers none of the questions and is still
           a requirement this user would expect of this job.
A reason last seen long ago may be one the user has left: say "no"<NO_SHORT>
when the record shows they have moved on.

Each CASE is a piece of the user's past work. Say for each:
  "alike": 3 - work of the same kind, made much as this job would be;
           2 - alike in good part; 1 - a little; 0 - not alike.
  "answers": the reviewer's questions it helps answer - what was made, with
           what and how - by number, 3, 2 or 1 as above; {} for none.
  "same_as": the id of another case listed that is the same project, or
           "".

Return ONLY this JSON object:
{"reasons": {"R-...": {"holds": "yes|no|unknown", "here": "",<WHY> "ask": "",
                       "answers": {"1": 3}, "expected": false}, ...},
 "cases": {"C-...": {"alike": 2, "answers": {"1": 2}, "same_as": ""}, ...}}"""

#: Why a reason does not hold: written in a debugger run only - one that does
#: not hold is given to no reviewer, so its why is read by the user alone, as
#: they look into a run (the user, 2026-10-24).
_WHY_NOT = (" (say why in a few words)", " with why", ' "why": "",')

#: THE SECOND CALL, when the cases chosen are long: of each, the judgments
#: worth the reviewer's reading - knowing which cases were chosen, and of a
#: project of several cases, of all of them together.
PICK_PROMPT = """WHICH OF THE USER'S JUDGMENTS IN THESE CASES OF THEIR PAST WORK ARE MOST
WORTH THE REVIEWER'S READING, FOR ITS QUESTIONS AND THIS JOB?

For each case, the ids of the judgments most worth reading, most first - at
most the number said.

Return ONLY this JSON object:
{"cases": {"C-...": ["J-...", ...]}}"""


def _line(text: Any, most: int = 300) -> str:
    return " ".join(str(text or "").split())[:most]


def _scores(said: Any, asked: Any) -> Dict[int, int]:
    """Question number -> 1..3, of the questions asked."""
    out: Dict[int, int] = {}
    for number, score in (said.items() if isinstance(said, dict) else []):
        try:
            number, score = int(number), int(score)
        except (TypeError, ValueError):
            continue
        if number in asked and score > 0:
            out[number] = min(score, 3)
    return out


def _judgment_line(one: Dict[str, Any]) -> str:
    said = f' - "{_line(one["said"], 300)}"' if one.get("said") else ""
    why = f" (why: {_line(one['why'], 200)})" if one.get("why") else ""
    explained = "" if one["state"] == "linked" else " [no reason explains it yet]"
    return (f"    {one['id']} {one.get('at') or '?'} [{one['strength']}] "
            f"{one.get('item') or ''} {one['object'].split(':', 1)[-1]}: "
            f"{_line(one['direction'], 200)}{said}{why}{explained}")


class Finder:
    def __init__(self, db: Any, leave_out: Optional[LeaveOut] = None) -> None:
        self.db = db
        self.store = LearningStore(db)
        self.vocab = Vocabulary(db)
        #: Cases expanded already in this run.
        self.expanded: set = set()
        # What the user turned off in the learning list, and what waits for
        # them to look at its first reading, is given to no run.
        self.leave_out = (leave_out or LeaveOut()).holding(self.store.held_units())
        self._out: Optional[Tuple[set, set]] = None
        self._items: Dict[str, Optional[Dict[str, Any]]] = {}

    def item_of_case(self, case: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The learning list's item a case was read from, if it was."""
        unit_id = str(case.get("unit_id") or "")
        if not unit_id.startswith("item:"):
            return None
        if unit_id not in self._items:
            from .items import item_of_unit
            self._items[unit_id] = item_of_unit(self.db, unit_id)
        return self._items[unit_id]

    def _endorsed(self, item: Dict[str, Any]) -> str:
        angle = " ".join(str(item.get("angle") or "").split())[:200]
        return (f"someone else's work the user endorses: {item.get('title', '')}"
                + (f" - for: {angle}" if angle else ""))

    def adopted_from(self, reason: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The item a reason adopted from someone else's work rests on."""
        for one in self.store.evidence(reason["id"]):
            case = self.store.case(one["case_id"]) or {}
            item = self.item_of_case(case)
            if item is not None:
                return item
        return None

    def _parts_out(self) -> Tuple[set, set]:
        """(cases, judgments) left out of this run - found once, at its first ask."""
        if self._out is None:
            self._out = self.leave_out.parts_out(self.store)
        return self._out

    def out(self) -> set:
        """The cases left out of this run whole."""
        return self._parts_out()[0]

    def judgments_out(self) -> set:
        return self._parts_out()[1]

    def _kept(self, judgments: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = self.judgments_out()
        return [one for one in judgments if one["id"] not in out]

    def _supports(self, reason_id: str) -> List[Dict[str, Any]]:
        return [one for one in self._kept(self.store.evidence(reason_id)) if one["role"] == "supports"]

    # -- what the reviewer tags a job with -------------------------------------
    def word_list(self) -> str:
        self.vocab.ensure_fixed()
        return self.vocab.listing(DIMENSIONS)

    def job_facets(self, said: Any) -> Dict[str, List[str]]:
        terms = self.vocab.by_id()
        out: Dict[str, List[str]] = {}
        if not isinstance(said, dict):
            return out
        for dim in DIMENSIONS:
            values = said.get(dim) or []
            values = [values] if isinstance(values, str) else values
            tags = []
            for value in values:
                value = str(value or "")
                tag = self.vocab.resolve(value if ":" in value else f"{dim}:{value}", terms)
                if tag and tag.startswith(dim + ":"):
                    tags.append(tag)
            if tags:
                out[dim] = list(dict.fromkeys(tags))
        return out

    def anything_learned(self) -> bool:
        counts = self.store.counts()
        return bool(counts.get("cases") or counts.get("reasons"))

    # -- reasons --------------------------------------------------------------
    def _giveable(self, reason: Dict[str, Any]) -> bool:
        if reason["state"] == "retired":
            return False
        supports = self._supports(reason["id"])
        if reason["source"] == "adopted":
            # From someone else's work the user endorses: while that rests here.
            return bool(supports)
        if reason["source"] == "stated":
            # Said by the user - but not when all it rests on is left out.
            return bool(supports) or not self.judgments_out() or not any(
                one["role"] == "supports" for one in self.store.evidence(reason["id"]))
        return len(supports) >= 2

    def candidates(self, facets: Dict[str, List[str]]) -> List[Tuple[Dict[str, Any], str, List[str]]]:
        terms = self.vocab.by_id()
        out = []
        for reason in self.store.reasons(("active", "competing", "candidate")):
            if not self._giveable(reason):
                continue
            how, why = coverage(reason.get("condition") or {}, facets, self.vocab, terms)
            if how != "outside":
                out.append((reason, how, why))
        return out

    def holds(self, job: str, facets: Dict[str, List[str]],
              candidates: Sequence[Tuple[Dict[str, Any], str, List[str]]],
              ask: Optional[Callable[[str, str, int], Dict[str, Any]]],
              questions: Sequence[Dict[str, Any]] = (),
              cases: Sequence[Dict[str, Any]] = (), explain: bool = True
              ) -> Dict[str, Dict[str, Any]]:
        """Each candidate's verdict - whether it holds, how it bears here,
        why not (`explain`: a debugger run), what would settle it, which
        questions it answers (number -> 1..3) and whether it is expected
        unasked. Of each of `cases`, how alike it is, which questions it
        helps answer and which case above it is the same project - kept in
        `self.case_verdicts`."""
        self.picked = {}
        self.case_verdicts = {}
        if not candidates and not cases:
            return {}
        if ask is None:
            return {reason["id"]: {"holds": "yes" if how == "covered" else "unknown", "why": "",
                                   "answers": {}, "expected": False, "coverage": how}
                    for reason, how, _why in candidates}
        lines = [f"THE JOB: {job}", f"ITS TERMS: {dumps(facets)}", ""]
        if questions:
            lines.append("THE REVIEWER'S QUESTIONS:")
            lines += [f"  {n}. ({one.get('kind') or 'requirement'}) {_line(one.get('q'), 300)}"
                      for n, one in enumerate(questions, 1)]
            lines.append("")
        lines.append("THE REASONS:")
        for reason, how, why in candidates:
            cond = reason.get("condition") or {}
            lines.append(
                f"{reason['id']}: {reason['statement']} WHEN: {cond.get('text', '')} "
                f"EXCEPT: {dumps(cond.get('excluded') or {})} "
                f"(by the word list: {how}{'; ' + '; '.join(why) if why else ''}; "
                f"last seen {str(reason.get('last_seen') or '')[:10]}"
                f"{'; changed since ' + reason['since'][:10] if reason.get('since') else ''})")
        if cases:
            lines += ["", "THE CASES (the user's past work, most of the job's terms first):"]
            for case in cases:
                tags = "; ".join(f"{dim}: {', '.join(tag.split(':', 1)[-1] for tag in values)}"
                                 for dim, values in (case.get("facets") or {}).items() if values)
                mine = self._kept(self.store.judgments_of(case["id"]))
                last = max([str(one.get("at") or "")[:10] for one in mine] + [
                    str(one.get("at") or "")[:10] for one in case.get("locations") or []
                    if isinstance(one, dict)] + [""])
                lines.append(f"{case['id']}: {case.get('title', '')} [{tags}] - {len(mine)} "
                             f"judgments of the user's" + (f", last {last}" if last else ""))
                if case.get("looks_like"):
                    lines.append("  looks like: " + _line(case["looks_like"], 200))
                if case.get("how_made"):
                    lines.append("  made: " + _line(case["how_made"], 150))
        no, no_short, why = _WHY_NOT if explain else ("", "", "")
        prompt = HOLDS_PROMPT.replace("<NO>", no).replace("<NO_SHORT>", no_short) \
            .replace("<WHY>", why)
        said = ask(prompt, "\n".join(lines), 3000) or {}
        verdicts = said.get("reasons") if isinstance(said.get("reasons"), dict) else said
        asked = range(1, len(questions) + 1)
        out = {}
        for reason, how, _why in candidates:
            one = verdicts.get(reason["id"]) if isinstance(verdicts.get(reason["id"]), dict) else {}
            holds = str(one.get("holds") or ("yes" if how == "covered" else "unknown")).lower()
            answers = _scores(one.get("answers"), asked)
            out[reason["id"]] = {"holds": holds, "why": _line(one.get("why"), 200),
                                 "here": _line(one.get("here"), 300) if holds == "yes" else "",
                                 "ask": _line(one.get("ask"), 300), "coverage": how,
                                 "answers": answers, "expected": bool(one.get("expected"))}
        judged = said.get("cases") if isinstance(said.get("cases"), dict) else {}
        listed = [case["id"] for case in cases]
        for cid in listed:
            one = judged.get(cid)
            if not isinstance(one, dict):
                continue
            try:
                alike = max(0, min(3, int(one.get("alike") or 0)))
            except (TypeError, ValueError):
                alike = 0
            same = str(one.get("same_as") or "")
            self.case_verdicts[cid] = {
                "alike": alike, "answers": _scores(one.get("answers"), asked),
                "same_as": same if same in listed and same != cid else ""}
        return out

    # -- cases ----------------------------------------------------------------
    def similar_cases(self, facets: Dict[str, List[str]]) -> List[Dict[str, Any]]:
        terms = self.vocab.by_id()
        scored = []
        counts = {row["case_id"]: row["n"] for row in self.db.query(
            "SELECT case_id, COUNT(*) AS n FROM learn_judgments GROUP BY case_id")}
        out = self.out()
        for case in self.store.cases():
            if case["id"] in out:
                continue
            item = self.item_of_case(case)
            if item is not None and not ({"cases", "reference"} & set(item.get("learn") or [])):
                continue
            score = share(facets, case.get("facets") or {}, self.vocab, terms)
            if score <= 0:
                continue
            richness = min(3, counts.get(case["id"], 0))
            recency = str(case.get("updated_at") or "")
            scored.append((round(score, 6), richness, recency, case))
        scored.sort(key=lambda one: one[:3], reverse=True)
        return [one[-1] for one in scored]

    def pick(self, facets: Dict[str, List[str]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        ranked = self.similar_cases(facets)
        terms = self.vocab.by_id()
        detailed: List[Dict[str, Any]] = []
        per_unit: Dict[str, int] = defaultdict(int)

        def same(case: Dict[str, Any], dim: str) -> bool:
            mine, theirs = facets.get(dim) or [], (case.get("facets") or {}).get(dim) or []
            return any(self.vocab.relation(a, b, terms) in ("same", "under", "over")
                       for a in mine for b in theirs)

        # One of the same deliverable and one of the same purpose first.
        for dim in ("deliverable", "purpose"):
            for case in ranked:
                if same(case, dim) and case not in detailed and per_unit[case["unit_id"]] < PER_UNIT:
                    detailed.append(case)
                    per_unit[case["unit_id"]] += 1
                    break
        for case in ranked:
            if len(detailed) >= DETAILED:
                break
            if case not in detailed and per_unit[case["unit_id"]] < PER_UNIT:
                detailed.append(case)
                per_unit[case["unit_id"]] += 1
        index = [case for case in ranked if case not in detailed][:INDEXED]
        return detailed[:DETAILED], index

    def chosen(self, facets: Dict[str, List[str]], ranked: Sequence[Dict[str, Any]]
               ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
        """(in full, in the index, each in full -> its project's cases, it
        first): by how alike the model said each is, one place a project,
        none it said is not alike in full; by their terms when it said
        nothing."""
        said = getattr(self, "case_verdicts", {}) or {}
        if not said:
            detailed, index = self.pick(facets)
            return detailed, index, {case["id"]: [case] for case in detailed}
        order = {case["id"]: n for n, case in enumerate(ranked)}
        by_id = {case["id"]: case for case in ranked}
        # The same project, joined whichever way the model said it.
        parent = {cid: cid for cid in said if cid in by_id}

        def top(cid: str) -> str:
            while parent[cid] != cid:
                cid = parent[cid]
            return cid

        for cid in parent:
            other = said[cid].get("same_as") or ""
            if other in parent and top(other) != top(cid):
                parent[top(cid)] = top(other)
        projects: Dict[str, List[str]] = {}
        for cid in parent:
            projects.setdefault(top(cid), []).append(cid)

        def alike(cid: str) -> int:
            return int((said.get(cid) or {}).get("alike") or 0)

        groups_by_head = []
        for ids in projects.values():
            ids.sort(key=lambda cid: (-alike(cid), order[cid]))
            groups_by_head.append(ids)
        groups_by_head.sort(key=lambda ids: (-alike(ids[0]), order[ids[0]]))
        detailed: List[Dict[str, Any]] = []
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for ids in groups_by_head:
            if len(detailed) < DETAILED and alike(ids[0]) > 0:
                detailed.append(by_id[ids[0]])
                groups[ids[0]] = [by_id[cid] for cid in ids]
        index = [by_id[ids[0]] for ids in groups_by_head
                 if ids[0] not in groups and alike(ids[0]) > 0]
        index += [case for case in ranked if case["id"] not in said]
        return detailed, index[:INDEXED], groups

    def _judgments_of(self, group: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [one for case in group for one in self._kept(self.store.judgments_of(case["id"]))]

    def pick_judgments(self, job: str, questions: Sequence[Dict[str, Any]],
                       long_cases: Sequence[Dict[str, Any]],
                       groups: Dict[str, List[Dict[str, Any]]],
                       ask: Optional[Callable[[str, str, int], Dict[str, Any]]]) -> None:
        """THE SECOND CALL: of each long case chosen - with its project's
        other cases - the judgments worth reading, in `self.picked`."""
        if ask is None or not long_cases:
            return
        lines = [f"THE JOB: {job}", "", "THE REVIEWER'S QUESTIONS:"]
        lines += [f"  {n}. ({one.get('kind') or 'requirement'}) {_line(one.get('q'), 300)}"
                  for n, one in enumerate(questions, 1)]
        lines += ["", f"CASES' JUDGMENTS (pick at most {JUDGMENTS_PICKED} of each):"]
        for case in long_cases:
            lines.append(f"CASE {case['id']}: {case.get('title', '')}")
            for one in self._judgments_of(groups[case["id"]]):
                said = f' "{_line(one["said"], 120)}"' if one.get("said") else ""
                lines.append(f"  {one['id']} [{one.get('aspect') or ''}, {one['strength']}] "
                             f"{one['object'].split(':', 1)[-1]}: "
                             f"{_line(one['direction'], 160)}{said}")
        said = ask(PICK_PROMPT, "\n".join(lines), 1500) or {}
        picks = said.get("cases") if isinstance(said.get("cases"), dict) else {}
        for case in long_cases:
            mine = {one["id"] for one in self._judgments_of(groups[case["id"]])}
            chosen = [str(one) for one in picks.get(case["id"]) or [] if str(one) in mine]
            if chosen:
                self.picked[case["id"]] = list(dict.fromkeys(chosen))[:JUDGMENTS_PICKED]

    def case_text(self, case: Dict[str, Any], full: bool = True,
                  only: Optional[Sequence[str]] = None, mark: bool = True) -> str:
        facets = case.get("facets") or {}
        terms = ", ".join(tag.split(":", 1)[1] for values in facets.values() for tag in values)
        if not full or case["id"] in self.expanded:
            return f"{case['id']} {case.get('title', '')} [{terms}] - {_line(case.get('looks_like'), 200)}"
        if mark:
            self.expanded.add(case["id"])
        lines = [f"CASE {case['id']}: {case.get('title', '')}", f"  terms: {terms}"]
        item = self.item_of_case(case)
        if item is not None:
            lines.append("  NOT THE USER'S OWN WORK - " + self._endorsed(item)
                         if item["whose"] == "others" else
                         f"  the user's own work, from their learning list: {item['source'] or item['title']}")
        places = [one for one in case.get("locations") or []
                  if not self.leave_out.location_out(one)]
        if places:
            lines.append("  where the deliverables are:")
            for one in places[-12:]:
                lines.append(f"    {one.get('what') or 'file'}: {one.get('path')} "
                             f"({one.get('at') or '?'})")
        if case.get("looks_like"):
            lines.append("  what it looks like: " + _line(case["looks_like"], 1500))
        if case.get("versions"):
            lines.append("  versions: " + "; ".join(
                f"{one.get('at', '?')}: {_line(one.get('what'), 160)}" for one in case["versions"][-6:]))
        if case.get("how_made"):
            lines.append("  how it was made: " + _line(case["how_made"], 600))
        judgments = self._kept(self.store.judgments_of(case["id"]))
        more = 0
        if only is not None:
            # Of a long case, those picked for the questions, most first.
            by_id = {one["id"]: one for one in judgments}
            picked = [by_id[one] for one in only if one in by_id]
            more, judgments = len(judgments) - len(picked), picked
        if judgments:
            lines.append("  the user's judgments in it" + (" that bear on your questions:"
                                                            if only is not None else ":"))
            for one in (judgments if only is not None
                        else sorted(judgments, key=lambda j: str(j.get("at") or ""))):
                lines.append(_judgment_line(one))
        if more:
            lines.append(f"  ({more} more of the user's judgments in it: open_case {case['id']})")
        return "\n".join(lines)

    def group_text(self, group: Sequence[Dict[str, Any]],
                   only: Optional[Sequence[str]] = None) -> str:
        """A project of several cases as one: the first in full, the others
        by what they look like - with their judgments, or those of `only`."""
        first, *others = group
        text = [self.case_text(first, only=only)]
        for case in others:
            self.expanded.add(case["id"])
            judgments = self._kept(self.store.judgments_of(case["id"]))
            text.append(f"  THE SAME PROJECT, ALSO {case['id']}: {case.get('title', '')} - "
                        f"{_line(case.get('looks_like'), 200)}")
            if only is not None:
                by_id = {one["id"]: one for one in judgments}
                picked = [by_id[one] for one in only if one in by_id]
                more, judgments = len(judgments) - len(picked), picked
            else:
                more = 0
                judgments = sorted(judgments, key=lambda j: str(j.get("at") or ""))
            text += [_judgment_line(one) for one in judgments]
            if more:
                text.append(f"  ({more} more of the user's judgments in it: open_case {case['id']})")
        return "\n".join(text)

    # -- the package ----------------------------------------------------------
    def answer(self, job: str, facets: Dict[str, List[str]],
               questions: Sequence[Dict[str, Any]],
               ask: Optional[Callable[[str, str, int], Dict[str, Any]]] = None,
               had: Optional[Dict[str, Any]] = None, explain: bool = True) -> Dict[str, Any]:
        """The text the reviewer is given for its questions, and what it
        rests on (for the run's log). `had`: the reasons and cases its
        context holds already ({"reasons": ids, "cases": ids, "index": ids})
        - in "text" each is named, not given again; "whole" gives them all,
        for the package kept to be given alone later. `explain`: why a reason
        does not hold, written (a debugger run)."""
        questions = [one for one in questions if isinstance(one, dict)]
        candidates = self.candidates(facets)
        ranked = self.similar_cases(facets)
        verdicts = self.holds(job, facets, candidates, ask, questions,
                              cases=ranked[:CASE_POOL], explain=explain)
        detailed, index, groups = self.chosen(facets, ranked)
        whole = {case["id"]: self.group_text(groups[case["id"]]) for case in detailed}
        for case in detailed:
            for one in groups[case["id"]]:
                self.expanded.discard(one["id"])
        long_cases = []
        if sum(len(one) for one in whole.values()) > CASES_CHARS:
            long_cases = [case for case in detailed
                          if len(self._judgments_of(groups[case["id"]])) > JUDGMENTS_PICKED]
            self.pick_judgments(job, questions, long_cases, groups, ask)
        given: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}
        new_questions = []
        for reason, _how, _why in candidates:
            verdict = verdicts.get(reason["id"]) or {}
            if verdict.get("holds") != "no":
                given[reason["id"]] = (reason, verdict)
        if not any(verdict.get("answers") for _r, verdict in given.values()):
            # No model said which question each answers: by its kind.
            for reason, verdict in given.values():
                verdict["answers"] = {n: ANSWERS_AT_LEAST for n, q in enumerate(questions, 1)
                                      if (q.get("kind") or "requirement") == reason.get("aspect")}
                verdict["expected"] = not verdict["answers"]

        def rank(rid: str, number: int = 0) -> Tuple:
            reason, verdict = given[rid]
            confidence = (reason["support"] + 1) / (reason["support"] + reason["contra"] + 2)
            return (-(verdict.get("answers") or {}).get(number, 0), verdict.get("holds") != "yes",
                    reason["source"] != "stated", -confidence)

        by_question = {n: sorted((rid for rid, (_r, v) in given.items()
                                  if (v.get("answers") or {}).get(n, 0) >= ANSWERS_AT_LEAST),
                                 key=lambda rid, n=n: rank(rid, n))[:PER_QUESTION]
                       for n in range(1, len(questions) + 1)}
        answering = {rid for mine in by_question.values() for rid in mine}
        expected = sorted((rid for rid, (_r, v) in given.items()
                           if rid not in answering and v.get("expected")), key=rank)[:EXPECTED]
        for rid in list(answering) + expected:
            verdict = given[rid][1]
            if verdict.get("holds") == "unknown" and verdict.get("ask"):
                new_questions.append({"q": verdict["ask"], "kind": given[rid][0].get("aspect") or "requirement",
                                      "from": rid})

        # The cases: the long ones by their judgments picked (or, picked by
        # none, by kind, words and strength); always with what they were
        # made with.
        cases_text = {}
        for case in detailed:
            only = None
            if case in long_cases:
                only = self.picked.get(case["id"]) or self._by_tags(
                    case, questions, self._judgments_of(groups[case["id"]]))
            cases_text[case["id"]] = self.group_text(groups[case["id"]], only=only)
        # A fact or a way of working no reason answers outright: where the
        # user's past work says it, and where this project's files do - said
        # in full once, then pointed back to.
        facts_asked = [n for n, q in enumerate(questions, 1) if q.get("kind") in ("fact", "habit")
                       and not any((given[rid][1].get("answers") or {}).get(n) == 3
                                   for rid in by_question.get(n, []))]
        made = [case["id"] for case in detailed] + [case["id"] for case in index[:4]]
        said_of = getattr(self, "case_verdicts", {}) or {}

        def made_for(n: int) -> List[str]:
            """The cases said to help answer question n - else the closest."""
            mine = [cid for cid in [case["id"] for case in detailed + index]
                    if (said_of.get(cid) or {}).get("answers", {}).get(n, 0) >= ANSWERS_AT_LEAST]
            return mine or made

        pointer = ("  No reason learned answers this outright. What the user used and did before is in "
                   "the cases below - what each looks like and how it was made ({}); "
                   "what this machine and project have, find out (search_files, read_file, run_python).")
        pointed = ("  No reason learned answers this outright - see under QUESTION "
                   f"{facts_asked[0]}; the cases for it: {{}}." if facts_asked else "")

        # Packed by priority: each question's first; the unasked; the rest.
        order = [mine[0] for mine in by_question.values() if mine] + expected + \
            [rid for mine in by_question.values() for rid in mine[1:]]
        head = ["WHAT WAS LEARNED OF THE USER'S OWN WORK, FOR YOUR QUESTIONS.",
                "Reasons are why the user judges their work so - with when each holds, as read "
                "from what they did. A reason SAID BY THE USER comes first; an INFERRED one is "
                "the record's reading, weigh it as such."]
        tail_most = 300 + sum(len(one) + 1 for one in cases_text.values()) + sum(
            len(self.case_text(case, full=False)) + 3 for case in index) + sum(
            len(q["q"]) + 40 for q in new_questions)
        room = PACKAGE_CHARS - len("\n".join(head)) - tail_most - sum(
            len(_line(q.get("q"), 300)) + 120 for q in questions) - (len(pointer) + 200) - (
            len(pointed) + 200) * len(facts_asked)
        texts: Dict[str, str] = {}
        for rid in dict.fromkeys(order):
            text = self._reason_text(*given[rid], facets)
            if len(text) + 1 <= room:
                texts[rid] = text
                room -= len(text) + 1

        def shown_as(had: Dict[str, Any]) -> Tuple[str, List[str], List[str]]:
            """The package, with what `had` holds named and not given again."""
            had_reasons = {str(one) for one in had.get("reasons") or []}
            had_cases = {str(one) for one in had.get("cases") or []}
            had_index = had_cases | {str(one) for one in had.get("index") or []}
            tail = []
            if any(q.get("kind") == "fact" for q in questions) or facts_asked:
                tail.append("\n== FOR THE FACTS OF THIS PROJECT: what the cases below say they "
                            "were made with and how; and search its files (search_files, "
                            "read_file). ==")
            if detailed:
                tail.append("\n== THE CASES MOST LIKE THIS JOB (in full) ==")
                tail += [f"CASE {case['id']}: {case.get('title', '')} - given above, in full"
                         if case["id"] in had_cases else cases_text[case["id"]]
                         for case in detailed]
            if index:
                tail.append("\n== OTHER CASES LIKE IT (open_case for more) ==")
                tail += [f"  {case['id']} {case.get('title', '')} - given above"
                         if case["id"] in had_index else "  " + self.case_text(case, full=False)
                         for case in index]
            for q in new_questions:
                tail.append(f"\nA QUESTION FOR YOU, from {q['from']}: {q['q']}")
            out = list(head) if not had_reasons else [head[0]]
            shown: List[str] = []
            first_pointer = True
            for n, q in enumerate(questions, 1):
                out.append(f"\n== QUESTION {n} ({q.get('kind') or 'requirement'}): "
                           f"{_line(q.get('q'), 300)}")
                mine = [rid for rid in by_question.get(n, []) if rid in texts]
                for rid in mine:
                    out.append(f"{rid} - given above" if rid in shown or rid in had_reasons
                               else texts[rid])
                    shown.append(rid)
                if n in facts_asked:
                    out.append((pointer if first_pointer else pointed).format(
                        ", ".join(made_for(n))))
                    first_pointer = False
                elif not mine:
                    out.append("  (nothing learned bears on this)")
            unasked = [rid for rid in expected if rid in texts]
            if unasked:
                out.append("\n== NOT ASKED, BUT THIS USER WOULD EXPECT IT OF THIS JOB ==")
                out += [f"{rid} - given above" if rid in had_reasons else texts[rid]
                        for rid in unasked]
                shown += unasked
            if not shown and not detailed:
                out = ["NOTHING LEARNED OF THE USER'S WORK BEARS ON THIS JOB YET. Answer your "
                       "questions from the user's words, their files, and - for what they leave "
                       "open - the web (as told), and say so."]
            else:
                out += tail
            text = "\n".join(out)
            if len(text) > PACKAGE_CHARS:
                text = text[:PACKAGE_CHARS] + "\n... (cut: open_case / open_reason for more)"
            return text, list(dict.fromkeys(shown)), unasked

        whole, shown, unasked = shown_as({})
        text = shown_as(had)[0] if had and any(had.values()) else whole
        return {"text": text, "whole": whole,
                "found": {"reasons": shown,
                          "holds": {rid: given[rid][1] for rid in shown},
                          "by_question": {str(n): [rid for rid in mine if rid in texts]
                                          for n, mine in by_question.items()},
                          "expected": unasked,
                          "cases": [case["id"] for case in detailed],
                          "judgments_picked": {case["id"]: self.picked.get(case["id"]) or "by kind"
                                               for case in long_cases},
                          "index": [case["id"] for case in index],
                          "same_project": {cid: [one["id"] for one in group[1:]]
                                           for cid, group in groups.items() if len(group) > 1},
                          "cases_judged": said_of,
                          "questions_raised": new_questions,
                          "facets": facets}}

    def _reason_text(self, reason: Dict[str, Any], verdict: Dict[str, Any],
                     facets: Dict[str, List[str]]) -> str:
        """A reason as given: what it says, when it holds, whether here - and
        one short example, the case nearest the job."""
        cond = reason.get("condition") or {}
        confidence = (reason["support"] + 1) / (reason["support"] + reason["contra"] + 2)
        tag = "said by the user" if reason["source"] == "stated" else \
            f"inferred, confidence {confidence:.1f}"
        if reason["source"] == "adopted":
            item = self.adopted_from(reason)
            tag = "adopted from " + (self._endorsed(item) if item else
                                     "someone else's work the user endorses")
        lines = [f"{reason['id']} [{tag}] {reason['statement']}",
                 f"    when: {cond.get('text', '')}"
                 + (f" - except {dumps(cond.get('excluded'))}" if cond.get("excluded") else "")]
        state = {"yes": verdict.get("here") or "its condition holds here",
                 "unknown": "it may hold - see the question"}
        lines.append(f"    here: {state.get(verdict.get('holds'), 'it may hold')}"
                     + (f" ({verdict.get('coverage')}: not seen in a job like this yet)"
                        if verdict.get("coverage") == "near" else "")
                     + (f" - {verdict['why']}" if verdict.get("why") else ""))
        if reason.get("rival"):
            rival = self.store.reason(reason["rival"])
            if rival:
                lines.append(f"    another explanation, not settled yet: {rival['id']} "
                             f"{_line(rival['statement'], 200)}")
        terms = self.vocab.by_id()
        nearest = sorted(self._supports(reason["id"]), key=lambda j: -closeness(
            facets, (self.store.case(j["case_id"]) or {}).get("facets") or {}, self.vocab, terms))[:1]
        for one in nearest:
            case = self.store.case(one["case_id"]) or {}
            title = _line(case.get("title"), 60).split(":")[0].split(" (")[0]
            lines.append(f"    e.g. {case.get('id', '')} {title}: {_line(one['direction'], 120)}")
        return "\n".join(lines)

    def _by_tags(self, case: Dict[str, Any], questions: Sequence[Dict[str, Any]],
                 judgments: Optional[Sequence[Dict[str, Any]]] = None) -> List[str]:
        """A long case's judgments picked with no model: of the kinds asked
        about most, the tools it was made with when a stack or a tool is asked
        about, the user's own words and what they changed first."""
        kinds = defaultdict(int)
        for one in questions:
            kinds[one.get("kind") or "requirement"] += 1
        tools = any(one.get("kind") in ("fact", "habit") for one in questions)
        strength = {"changed": 0, "approved": 1, "noted": 2}

        def score(one: Dict[str, Any]) -> Tuple:
            about = str(one.get("object") or "")
            return (-(tools and "tool" in about), -kinds.get(one.get("aspect") or "", 0),
                    not one.get("said"), strength.get(one.get("strength"), 3),
                    str(one.get("at") or ""))

        mine = sorted(judgments if judgments is not None
                      else self._kept(self.store.judgments_of(case["id"])), key=score)
        return [one["id"] for one in mine[:JUDGMENTS_PICKED]]

    # -- the reviewer's tools ---------------------------------------------------
    def open_case(self, case_id: str) -> str:
        case = self.store.case(str(case_id or ""))
        if not case or case["id"] in self.out():
            return f"no case {case_id}"
        self.expanded.discard(case["id"])
        return self.case_text(case)

    def open_reason(self, reason_id: str, page: int = 1,
                    facets: Optional[Dict[str, List[str]]] = None) -> str:
        reason = self.store.reason(str(reason_id or ""))
        if not reason or (self.judgments_out() and not self._giveable(reason)):
            return f"no reason {reason_id}"
        terms = self.vocab.by_id()
        cond = reason.get("condition") or {}
        lines = [f"REASON {reason['id']} [{reason['source']}, {reason['state']}]: {reason['statement']}",
                 f"  when: {cond.get('text', '')} (terms {dumps(cond.get('dims') or {})}, "
                 f"except {dumps(cond.get('excluded') or {})})",
                 f"  would fail: {cond.get('fails_when', '')}",
                 "  predicts: " + "; ".join(_line(p.get("text"), 200) for p in reason.get("predictions") or []),
                 f"  support {reason['support']}, against {reason['contra']}; first seen "
                 f"{str(reason.get('first_seen'))[:10]}, last seen {str(reason.get('last_seen'))[:10]}"]
        if reason.get("since"):
            lines.append(f"  as it is since {reason['since'][:10]}")
        for one in reason.get("history") or []:
            lines.append(f"  history {str(one.get('at'))[:10]}: {one.get('what')} - {_line(one.get('why'), 200)}")
        for row in self.store.overrides():
            if row["loser"] == reason["id"]:
                lines.append(f"  {row['winner']} wins over it when: {row['when_text']}")
            if row["winner"] == reason["id"]:
                lines.append(f"  it wins over {row['loser']} when: {row['when_text']}")
        evidence = self._kept(self.store.evidence(reason["id"]))
        if facets:
            evidence.sort(key=lambda j: -closeness(facets, (self.store.case(j["case_id"]) or {})
                                                   .get("facets") or {}, self.vocab, terms))
        start = max(0, (int(page or 1) - 1) * PAGE)
        chunk = evidence[start:start + PAGE]
        lines.append(f"  its cases ({len(evidence)} judgments; page {page}):")
        for one in chunk:
            case = self.store.case(one["case_id"]) or {}
            said = f' "{_line(one["said"], 200)}"' if one.get("said") else ""
            lines.append(f"    [{one['role']}] {one['id']} {one.get('at') or '?'} in "
                         f"{case.get('id', '')} {case.get('title', '')}: "
                         f"{_line(one['direction'], 160)}{said}")
        if start + PAGE < len(evidence):
            lines.append(f"  (more: page {int(page or 1) + 1})")
        return "\n".join(lines)

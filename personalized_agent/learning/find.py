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
  question for the reviewer). A reason the user stated always comes first;
  an inferred one is given only on two observations or more, said to be
  inferred. A rival is one line beside it.

  CASES, by how alike they are to the job, whatever reason they bear on:
  the closest `DETAILED` in full - where the deliverables are, what they
  look like, every judgment in them, how they were made - diverse (one or
  two of a unit) and covering the same deliverable and the same purpose
  where there are such; the next ones as a short index.

  THE JUDGMENTS no reason explains yet, in those cases - plainly said.

The reviewer may open more (`open_case`, `open_reason`) and search the
files themselves. A case is expanded once in a run; after, its id is
enough. Nothing here is learned.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .match import closeness, coverage
from .store import LearningStore, dumps
from .vocab import ASPECTS, DIMENSIONS, Vocabulary

#: Cases given in full; in the short index.
DETAILED = 3
INDEXED = 10
#: Cases of one unit among the detailed, at most.
PER_UNIT = 2
#: What a package may take, at most.
PACKAGE_CHARS = 40_000
#: Reasons on a page of `open_reason`'s cases.
PAGE = 15

QUESTION_KINDS = {
    "requirement": "an extra requirement the user would expect",
    "output": "the form, content or style of the output",
    "evaluation": "how a result is checked and judged",
    "habit": "how the work is done: steps, tools, habits",
    "fact": "a fact or limit of this project",
}

HOLDS_PROMPT = """DOES EACH OF THESE REASONS OF THE USER'S HOLD FOR THIS JOB?

Each reason says why the user judges their work so, and WHEN it holds (and
what it is excepted from). For the job described, say for each:
  "yes" - its condition holds here;
  "no" - it does not (say why in a few words);
  "unknown" - it depends on something the job does not say: write the
  question that would settle it.
A reason last seen long ago may be one the user has left: say "no" with why
when the record shows they have moved on.

Return ONLY this JSON object:
{"R-...": {"holds": "yes|no|unknown", "why": "", "ask": ""}, ...}"""


def _line(text: Any, most: int = 300) -> str:
    return " ".join(str(text or "").split())[:most]


class Finder:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.store = LearningStore(db)
        self.vocab = Vocabulary(db)
        #: Cases expanded already in this run.
        self.expanded: set = set()

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
        if reason["source"] == "stated":
            return True
        supports = [one for one in self.store.evidence(reason["id"]) if one["role"] == "supports"]
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
              ask: Optional[Callable[[str, str, int], Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
        if not candidates or ask is None:
            return {reason["id"]: {"holds": "yes" if how == "covered" else "unknown", "why": ""}
                    for reason, how, _why in candidates}
        lines = [f"THE JOB: {job}", f"ITS TERMS: {dumps(facets)}", "", "THE REASONS:"]
        for reason, how, why in candidates:
            cond = reason.get("condition") or {}
            lines.append(
                f"{reason['id']}: {reason['statement']} WHEN: {cond.get('text', '')} "
                f"EXCEPT: {dumps(cond.get('excluded') or {})} "
                f"(by the word list: {how}{'; ' + '; '.join(why) if why else ''}; "
                f"last seen {str(reason.get('last_seen') or '')[:10]}"
                f"{'; changed since ' + reason['since'][:10] if reason.get('since') else ''})")
        said = ask(HOLDS_PROMPT, "\n".join(lines), 3000) or {}
        out = {}
        for reason, how, _why in candidates:
            one = said.get(reason["id"]) if isinstance(said.get(reason["id"]), dict) else {}
            holds = str(one.get("holds") or ("yes" if how == "covered" else "unknown")).lower()
            out[reason["id"]] = {"holds": holds, "why": _line(one.get("why"), 200),
                                 "ask": _line(one.get("ask"), 300), "coverage": how}
        return out

    # -- cases ----------------------------------------------------------------
    def similar_cases(self, facets: Dict[str, List[str]]) -> List[Dict[str, Any]]:
        terms = self.vocab.by_id()
        scored = []
        counts = {row["case_id"]: row["n"] for row in self.db.query(
            "SELECT case_id, COUNT(*) AS n FROM learn_judgments GROUP BY case_id")}
        for case in self.store.cases():
            score = closeness(facets, case.get("facets") or {}, self.vocab, terms)
            if score <= 0:
                continue
            richness = min(3, counts.get(case["id"], 0)) * 0.3
            recency = str(case.get("updated_at") or "")
            scored.append((score + richness, recency, case))
        scored.sort(key=lambda one: (one[0], one[1]), reverse=True)
        return [case for _s, _r, case in scored]

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

    def case_text(self, case: Dict[str, Any], full: bool = True) -> str:
        facets = case.get("facets") or {}
        terms = ", ".join(tag.split(":", 1)[1] for values in facets.values() for tag in values)
        if not full or case["id"] in self.expanded:
            return f"{case['id']} {case.get('title', '')} [{terms}] - {_line(case.get('looks_like'), 200)}"
        self.expanded.add(case["id"])
        lines = [f"CASE {case['id']}: {case.get('title', '')}", f"  terms: {terms}"]
        if case.get("locations"):
            lines.append("  where the deliverables are:")
            for one in case["locations"][-12:]:
                lines.append(f"    {one.get('what') or 'file'}: {one.get('path')} "
                             f"({one.get('at') or '?'})")
        if case.get("looks_like"):
            lines.append("  what it looks like: " + _line(case["looks_like"], 1500))
        if case.get("versions"):
            lines.append("  versions: " + "; ".join(
                f"{one.get('at', '?')}: {_line(one.get('what'), 160)}" for one in case["versions"][-6:]))
        if case.get("how_made"):
            lines.append("  how it was made: " + _line(case["how_made"], 600))
        judgments = self.store.judgments_of(case["id"])
        if judgments:
            lines.append("  the user's judgments in it:")
            for one in sorted(judgments, key=lambda j: str(j.get("at") or "")):
                said = f' - "{_line(one["said"], 300)}"' if one.get("said") else ""
                why = f" (why: {_line(one['why'], 200)})" if one.get("why") else ""
                explained = "" if one["state"] == "linked" else " [no reason explains it yet]"
                lines.append(f"    {one['id']} {one.get('at') or '?'} [{one['strength']}] "
                             f"{one.get('item') or ''} {one['object'].split(':', 1)[-1]}: "
                             f"{_line(one['direction'], 200)}{said}{why}{explained}")
        return "\n".join(lines)

    # -- the package ----------------------------------------------------------
    def answer(self, job: str, facets: Dict[str, List[str]],
               questions: Sequence[Dict[str, Any]],
               ask: Optional[Callable[[str, str, int], Dict[str, Any]]] = None
               ) -> Dict[str, Any]:
        """The text the reviewer is given for its questions, and what it
        rests on (for the run's log)."""
        candidates = self.candidates(facets)
        verdicts = self.holds(job, facets, candidates, ask)
        terms = self.vocab.by_id()
        given: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        new_questions = []
        for reason, how, _why in candidates:
            verdict = verdicts.get(reason["id"]) or {}
            if verdict.get("holds") == "no":
                continue
            if verdict.get("holds") == "unknown" and verdict.get("ask"):
                new_questions.append({"q": verdict["ask"], "kind": reason.get("aspect") or "requirement",
                                      "from": reason["id"]})
            given.append((reason, verdict))
        given.sort(key=lambda one: (one[0]["source"] != "stated",
                                    -(one[0]["support"] + 1) / (one[0]["support"] + one[0]["contra"] + 2)))
        detailed, index = self.pick(facets)

        def reason_text(reason: Dict[str, Any], verdict: Dict[str, Any]) -> str:
            cond = reason.get("condition") or {}
            confidence = (reason["support"] + 1) / (reason["support"] + reason["contra"] + 2)
            tag = "said by the user" if reason["source"] == "stated" else \
                f"inferred, confidence {confidence:.1f}"
            lines = [f"{reason['id']} [{tag}] {reason['statement']}"]
            lines.append(f"    when: {cond.get('text', '')}"
                         + (f" - except {dumps(cond.get('excluded'))}" if cond.get("excluded") else ""))
            state = {"yes": "its condition holds here", "unknown": "it may hold - see the question"}
            lines.append(f"    here: {state.get(verdict.get('holds'), 'it may hold')}"
                         + (f" ({verdict.get('coverage')}: not seen in a job like this yet)"
                            if verdict.get("coverage") == "near" else "")
                         + (f" - {verdict['why']}" if verdict.get("why") else ""))
            if reason.get("predictions"):
                lines.append("    predicts: " + "; ".join(_line(p.get("text"), 160)
                                                         for p in reason["predictions"][:4]))
            lines.append(f"    first seen {str(reason.get('first_seen') or '?')[:10]}, last seen "
                         f"{str(reason.get('last_seen') or '?')[:10]}"
                         + (f", as it is since {reason['since'][:10]}" if reason.get("since") else ""))
            if reason.get("rival"):
                rival = self.store.reason(reason["rival"])
                if rival:
                    lines.append(f"    another explanation, not settled yet: {rival['id']} "
                                 f"{_line(rival['statement'], 200)}")
            supports = [one for one in self.store.evidence(reason["id"]) if one["role"] == "supports"]
            examples = sorted(supports, key=lambda j: -closeness(
                facets, (self.store.case(j["case_id"]) or {}).get("facets") or {}, self.vocab, terms))[:2]
            for one in examples:
                case = self.store.case(one["case_id"]) or {}
                said = f' "{_line(one["said"], 160)}"' if one.get("said") else ""
                lines.append(f"    e.g. {case.get('id', '')} {case.get('title', '')}: "
                             f"{_line(one['direction'], 160)}{said}")
            return "\n".join(lines)

        by_kind: Dict[str, List[str]] = defaultdict(list)
        shown_reasons: List[str] = []
        for reason, verdict in given:
            kind = reason.get("aspect") if reason.get("aspect") in ASPECTS else "requirement"
            by_kind[kind].append(reason_text(reason, verdict))
            shown_reasons.append(reason["id"])
        out = ["WHAT WAS LEARNED OF THE USER'S OWN WORK, FOR YOUR QUESTIONS.",
               "Reasons are why the user judges their work so - with when each holds, as read "
               "from what they did. A reason SAID BY THE USER comes first; an INFERRED one is "
               "the record's reading, weigh it as such."]
        asked_kinds = {str(q.get("kind") or "") for q in questions}
        for kind in list(QUESTION_KINDS):
            if kind == "fact":
                continue
            mine = [q for q in questions if q.get("kind") == kind]
            if not by_kind.get(kind) and not mine:
                continue
            out.append(f"\n== {QUESTION_KINDS[kind].upper()} ==")
            for q in mine:
                out.append(f"  your question: {_line(q.get('q'), 300)}")
            out += by_kind.get(kind) or ["  (nothing learned bears on this)"]
        for q in new_questions:
            out.append(f"\nA QUESTION FOR YOU, from {q['from']}: {q['q']}")
        if detailed:
            out.append("\n== THE CASES MOST LIKE THIS JOB (in full) ==")
            out += [self.case_text(case) for case in detailed]
        if index:
            out.append("\n== OTHER CASES LIKE IT (open_case for more) ==")
            out += ["  " + self.case_text(case, full=False) for case in index]
        if "fact" in asked_kinds:
            out.append("\n== FOR THE FACTS OF THIS PROJECT: search its files (search_files, "
                       "read_file); the cases above say where its deliverables are. ==")
        if not given and not detailed:
            out = ["NOTHING LEARNED OF THE USER'S WORK BEARS ON THIS JOB YET. Answer your "
                   "questions from the user's words, their files, and - for what they leave "
                   "open - the web (as told), and say so."]
        text = "\n".join(out)
        if len(text) > PACKAGE_CHARS:
            text = text[:PACKAGE_CHARS] + "\n... (cut: open_case / open_reason for more)"
        return {"text": text,
                "found": {"reasons": shown_reasons,
                          "holds": {rid: verdicts.get(rid) for rid in shown_reasons},
                          "cases": [case["id"] for case in detailed],
                          "index": [case["id"] for case in index],
                          "questions_raised": new_questions,
                          "facets": facets}}

    # -- the reviewer's tools ---------------------------------------------------
    def open_case(self, case_id: str) -> str:
        case = self.store.case(str(case_id or ""))
        if not case:
            return f"no case {case_id}"
        self.expanded.discard(case["id"])
        return self.case_text(case)

    def open_reason(self, reason_id: str, page: int = 1,
                    facets: Optional[Dict[str, List[str]]] = None) -> str:
        reason = self.store.reason(str(reason_id or ""))
        if not reason:
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
        evidence = self.store.evidence(reason["id"])
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

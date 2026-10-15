"""What each stage of the user's work shows about how they work - written
from the stage, and linked to it.

WORK NATURE ONLY, and of it two things: how the user EVALUATES a result,
and what OUTPUT they want - its format, content and style. Both are read
from the stages of their work, where they are recorded in detail, rather
than guessed from fifteen minutes of activity; each comes with the case it
was seen in, and where that case can be opened. Who the user is outside
their work is the agent heartbeat's to say.

The stage writer says them with each stage it writes; this pass reads the
stages written before it did. Each record is linked to the stages that show
it, and then grouped with the rest into patterns and principles.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from .store import model_task
from ...common.languages import in_english
from .store import ExperienceStore, count_use, now

log = logging.getLogger(__name__)

#: Stages read in one call.
STAGES_PER_CALL = 6

#: What a nature written from a stage is about: WORK NATURE ONLY, and of it
#: two things - how the user evaluates a result, and what output they want.
#: Who they are outside their work is the agent heartbeat's to say.
ASPECTS = ("evaluation", "output")

#: What work nature is - the same words for the stage writer, which says it
#: with each stage it writes, and for the pass that reads stages written
#: before it did.
NATURE_GUIDE = """ONLY WORK NATURE, AND OF IT ONLY TWO THINGS - say which in "aspect":

  - "evaluation" - HOW THE USER EVALUATES A RESULT: what they look at, what
    they run or test, the bar it must clear, and what makes them send it
    back. The output's specification, an evaluation's criteria and verdict,
    and the user's own words when they judged are where this shows.
      "Judges a UI change on the live page in dark mode, never from the
       code or a screenshot."
      "Sends a figure back when its point does not read without the
       caption."
      "Accepts a refactor only once the full test suite passes on their own
       machine."
  - "output" - WHAT OUTPUT THE USER WANTS: its FORMAT (structure, length,
    layout, file type), its CONTENT (what goes in, what is left out, how much
    detail), its STYLE (tone, wording, look). The deliverable, the design
    the user chose or approved, and what they changed in a draft are where
    this shows.
      "Wants a reply letter to answer each referee point under the point
       quoted in full, shortest first."
      "Wants panel text in plain sentences, with no internal labels or
       ids."
      "Prefers one idea per slide, a figure filling it and a one-line
       title that states the finding."

Nothing else: not how they plan or decide, not how they work with their
agents, not who they are outside the work - only how they judge a result
and what they want it to be.

EACH ONE SPECIFIC ENOUGH TO ACT ON: an agent reading it knows what to check
or what to make. "The user is careful" or "likes clean output" is not one.

EACH ONE WITH ITS EXAMPLE - A REAL CASE, not your account of it. In
"example": "said" is THE USER'S OWN WORDS, quoted exactly as the stage has
them - their message, comment or review, the text they wrote or changed;
"what" is a short label of the case, one line - the figure sent back, the
letter, the page checked; and "references" is EVERY id of the stage's
references the case touched (["r1", "r2", "r4"]) - all of them, not one.
An example with neither the user's words nor a reference is not kept, and
a nature with no example is not kept.
  {"aspect": "output",
   "title": "The user wants a reply letter to answer each referee point
             under the point quoted in full.",
   "example": {"said": "put each of their comments above your answer, in
                        full - I don't want them hunting for it",
               "what": "the reply to the second referee, seven points",
               "references": ["r2", "r3"]}}

THE SAME TRAIT AGAIN IS AN EXAMPLE, NOT A NEW NATURE. Where the user's
natures already known are listed (KNOWN NATURES, each with its id) and a
stage shows one of them again - the same way of judging, the same thing
wanted, in other words or in another piece of work - do not write it
again; give its id and the new case:
  {"same_as": "rec_...", "example": {"said": "...", "what": "...",
                                     "references": ["r1", "r5"]}}
and the case is added to that nature. Write a new nature only for a trait
none of them is.

A PREFERENCE IS THE USER'S ONLY WHERE THE USER SHOWED IT - a decision the
user made, or one the agent made that the user then accepted, rejected or
changed. A choice the agent made alone, that the user never weighed in on,
says nothing about the user."""

STAGE_NATURE_PROMPT = """SAY WHAT THESE STAGES OF THE USER'S WORK SHOW ABOUT THE USER.

Each item below is one stage of a piece of the user's work, as it actually
happened: the input in hand, the one main decision made from it - and
whether THE USER or THE AGENT made it - and the output: what it was, what
it had to satisfy, how it was designed, and what was produced. Write what
these stages show about the user that an agent would want to know to do
their work the way they would.

""" + NATURE_GUIDE + """

Most stages show at least one - a stage with an evaluation, a specification
or a decision of the user's almost always does. A stage may show none.

  - Grounded in the stages: give the ids of the stages it rests on, and in
    evidence what in them shows it - quote the user's own words where the
    stage has them.
  - Not a restatement of one stage's events: what the stage is evidence OF.
  - An empty list is a fine answer for stages that show nothing new.

The user's own words, quoted, stay as they wrote them.

Return ONLY this JSON object - each finding EITHER a known nature seen
again, OR a new one:
{"natures": [{"same_as": "a KNOWN NATURE's id - this trait again",
              "stages": ["stage ids"],
              "example": {"said": "the user's own words, quoted exactly",
                          "what": "a short label of the case, one line",
                          "references": ["every reference id it touched: r1, r3, ..."]}},
             {"stages": ["stage ids"],
              "aspect": "evaluation|output",
              "title": "ONE sentence, the user as its subject",
              "content": "a short paragraph: what it is, how it shows, and what
                          it means for doing their work",
              "confidence": 1-7,
              "evidence": ["what in the stages shows it"],
              "example": {"said": "the user's own words, quoted exactly",
                          "what": "a short label of the case, one line",
                          "references": ["every reference id it touched: r1, r3, ..."]},
              "reasoning": "how that evidence gets to this"}]}"""


def _as_json(text: str) -> Dict[str, Any]:
    text = text or ""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else text[text.find("{"):text.rfind("}") + 1]
    try:
        out = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return out if isinstance(out, dict) else {}


def _stage_said(stage: Dict[str, Any]) -> str:
    """One stage, as the nature writer reads it: what was known, what was
    decided and by whom, and what the output had to be."""
    work = stage.get("work") or {}
    made = stage.get("deliverable") or {}
    said = stage.get("evaluation") or {}
    lines = [f"[{stage['id']}] {stage['kind']} · {stage.get('medium', '')} · "
             f"{str(stage.get('began', ''))[:10]}",
             f"  {stage.get('summary', '')}"]
    for one in (work.get("input") or [])[:8]:
        lines.append(f"  input ({one.get('from') or '?'}): {one.get('what', '')}")
    decided = work.get("decision") or {}
    if decided.get("chose"):
        lines.append(f"  decided BY THE {str(decided.get('by') or '?').upper()}: "
                     f"{decided.get('about', '')} -> {decided['chose']}"
                     + (f"; options: {'; '.join(decided['options'])}"
                        if decided.get("options") else "")
                     + f"; why: {decided.get('why', '')}"
                     + (f"; turned down: {decided['rejected']}"
                        if decided.get("rejected") else ""))
    output = work.get("output") or {}
    if output.get("what"):
        lines.append(f"  output: {output['what']}")
    for one in (output.get("specification") or [])[:8]:
        lines.append(f"  the output had to: {one.get('requirement', '')} "
                     f"({one.get('source', '')})")
    if output.get("design"):
        lines.append(f"  design: {output['design'][:600]}")
    rationale = output.get("rationale") or {}
    if rationale.get("why"):
        lines.append(f"  why designed so ({'the user said' if rationale.get('from') == 'user' else 'inferred'}): "
                     f"{rationale['why'][:400]}")
    if said.get("criteria"):
        lines.append("  judged by: " + "; ".join(said["criteria"][:6]))
    if said.get("words"):
        lines.append("  the user's words: " + " ".join(f"“{w}”" for w in said["words"][:6]))
    if said.get("verdict"):
        lines.append(f"  verdict: {said['verdict']}")
    if made.get("excerpt"):
        lines.append(f"  produced ({made.get('type', '')}): {made['excerpt'][:400]}")
    for one in (work.get("references") or [])[:12]:
        if one.get("id"):
            lines.append(f"  reference {one['id']}: {one.get('location', '')}"
                         + (f" - {one['what']}" if one.get("what") else ""))
    return "\n".join(lines)


#: The user's existing natures shown beside the stages, closest first - so a
#: trait seen again is added to the nature it is, not written as another.
NEAR_SHOWN = 12

#: A NATURE'S WORDS STAND while its examples grow. It is worded again, from
#: all its examples, only once this share of them is new since it was last
#: worded - and at least REWORD_LEAST of them: one more case of the same
#: trait is an example, not a reason to say the trait differently.
REWORD_SHARE = 0.3
REWORD_LEAST = 2

#: How many places one example keeps - every reference the case touched,
#: up to a bound a front matter line can carry.
EXAMPLE_REFERENCES = 24

REWORD_PROMPT = """SAY THIS NATURE AGAIN, FROM ALL ITS EXAMPLES.

It is one trait of the user's work - {aspect} - written when it had
{worded} example(s). It now has {count}: the cases below, each with the
user's own words where they were kept. Say the SAME trait again so that it
holds for every one of them: as specific as they allow, so an agent reading
it knows what to check or what to make. Not a new trait, and not a list of
the cases.

The user's own words, quoted, stay as they wrote them.

Return ONLY this JSON object:
{"title": "ONE sentence, the user as its subject",
 "content": "a short paragraph: what it is, how it shows, and what it means
             for doing their work"}"""



#: WHAT NATURES ARE WRITTEN IN: English, whatever the user reads in. They are
#: matched to each other, grouped and searched by their words - and by a
#: model of meaning that reads English - which works in one language.
NATURES_LANGUAGE = "English"
#: Said to the model: natures are matched, grouped and searched by their
#: words, and in one language that works.
NATURES_SAID = in_english("the natures - they are matched, grouped and searched by their "
                          "words")

class StageNatures:
    """Writes the nature records a batch of stages shows, linked to them -
    and adds a case to a nature already known when a stage shows it again."""

    def __init__(self, db: Any, library: Any, models: Any, scope: str,
                 embedder: Any = None) -> None:
        self.db = db
        self.store = ExperienceStore(db, scope)
        self.library = library
        self.models = models
        self.used = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        #: What the records are written in: English, always (NATURES_LANGUAGE).
        self.language = NATURES_LANGUAGE
        #: What finds the known natures closest to the stages: the vectors
        #: the patterns already keep for them, when the model is up.
        self.embedder = embedder
        #: A task's project and title, for each example - set by the
        #: service, which knows them. None leaves them to be named on reading.
        self.names = None

    def natures(self) -> "NatureWriter":
        return NatureWriter(self.library, self.store, self.models,
                            language=self.language, names=self.names,
                            used=self.used, embedder=self.embedder)

    def write(self, stages: Sequence[Dict[str, Any]], budget: int,
              going=None) -> Dict[str, Any]:
        report = {"calls": 0, "stages": 0, "natures": 0, "examples": 0}
        going = going or (lambda: True)
        natures = self.natures()
        for start in range(0, len(stages), STAGES_PER_CALL):
            if report["calls"] >= budget or not going() or self.models is None:
                break
            batch = list(stages[start:start + STAGES_PER_CALL])
            shown = natures.show([_stage_text(s) for s in batch])
            answered, writer = self._ask(batch, shown)
            report["calls"] += 1
            if answered is None:
                break
            ids = {stage["id"] for stage in batch}
            for one in answered.get("natures") or []:
                done = self._write_one(one, ids, writer, batch, natures)
                if done == "new":
                    report["natures"] += 1
                elif done == "added":
                    report["examples"] += 1
            for stage in batch:
                self.store.mark_natured(stage["id"])
            report["stages"] += len(batch)
        report["calls"] += natures.reworded
        return report

    def _ask(self, batch: Sequence[Dict[str, Any]], shown: str = ""):
        from ...models.base import ModelRequest
        asked = "\n\n".join(_stage_said(s) for s in batch)
        if shown:
            asked += "\n\n" + shown
        try:
            response = self.models.invoke(
                ModelRequest(messages=[{"role": "user", "content": asked}],
                             system=STAGE_NATURE_PROMPT, max_tokens=6000,
                             task=model_task(self.store.scope, "stages"),
                             language=NATURES_SAID,
                             metadata={"kind": "experience stage natures"}),
                role="heartbeat")
        except Exception as exc:          # pragma: no cover - a call
            log.info("could not read stages for their natures: %s", exc)
            return None, {}
        count_use(self.used, response)
        return (_as_json(getattr(response, "text", "") or ""),
                {"model": str(getattr(response, "model", "") or ""),
                 "provider": str(getattr(response, "provider", "") or ""),
                 "at": now()})

    def _write_one(self, said: Dict[str, Any], ids: set,
                   writer: Dict[str, str],
                   batch: Sequence[Dict[str, Any]] = (),
                   natures: Optional["NatureWriter"] = None) -> str:
        if not isinstance(said, dict):
            return ""
        stages = [str(one) for one in said.get("stages") or [] if str(one) in ids]
        rows = {stage["id"]: stage for stage in batch if stage["id"] in stages}
        return (natures or self.natures()).write(said, stages, writer, rows=rows)


def _stage_text(stage: Dict[str, Any]) -> str:
    """What a stage is about, in a few words - to find the natures nearest
    it: its KEYWORDS, which are written in English as the natures are (the
    summary and the rest are in the user's language). A stage from before
    keywords: its summary, the decision and the output, and the user's words."""
    work = stage.get("work") or {}
    keywords = [str(one) for one in work.get("keywords") or [] if str(one).strip()]
    if keywords:
        return " ".join(keywords)[:1500]
    decided = work.get("decision") or {}
    output = work.get("output") or {}
    said = stage.get("evaluation") or {}
    return " ".join(str(one) for one in [
        stage.get("summary") or "", decided.get("about") or "",
        decided.get("chose") or "", output.get("what") or "",
        " ".join(str(w) for w in (said.get("words") or [])[:4]),
        " ".join(str(c) for c in (said.get("criteria") or [])[:4])] if one)[:1500]


def _said_of(value: Any) -> str:
    """The user's own words in an example, as one text - a list of quotes
    is kept as its quotes, one to a line."""
    if isinstance(value, (list, tuple)):
        value = "\n".join(str(one).strip() for one in value if str(one).strip())
    return str(value or "").strip()


def make_example(said: Any, stage_id: str = "",
                 references: Sequence[Dict[str, Any]] = (),
                 stage: Optional[Dict[str, Any]] = None,
                 names: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    """ONE REAL CASE of a nature: the user's own words, a short label, and
    every place it touched - or None, when it has neither the user's words
    nor a place that can be opened: the model's account of a case is not a
    case."""
    if isinstance(said, str):
        said = {"what": said}
    if not isinstance(said, dict):
        return None
    words = _said_of(said.get("said"))
    what = str(said.get("what") or "").strip()
    # The example's references, as the stage keeps them: where it can be
    # opened, not just an id. Every one it names - many are allowed.
    by_id: Dict[str, Dict[str, Any]] = {}
    for one in references or []:
        if isinstance(one, dict) and one.get("id"):
            by_id.setdefault(str(one["id"]), one)
    pointed: List[Dict[str, Any]] = []
    for one in said.get("references") or []:
        found = by_id.get(str(one).strip())
        if found is not None and found not in pointed:
            pointed.append(found)
    places = [{"id": str(one.get("id") or ""),
               "location": str(one.get("location") or "")[:600],
               "what": str(one.get("what") or "")[:300]}
              for one in pointed if str(one.get("location") or "").strip()]
    if not words and not places:
        return None
    stage = stage or {}
    at = str(stage.get("ended") or stage.get("began") or "") or now()
    out = {"stage": str(stage_id or stage.get("id") or ""), "at": at,
           "task_id": str(stage.get("task_id") or ""),
           "said": words[:1600], "what": what[:300],
           "references": places[:EXAMPLE_REFERENCES]}
    if names is not None and out["task_id"]:
        try:
            named = (names([out["task_id"]]) or {}).get(out["task_id"]) or {}
        except Exception:                 # pragma: no cover - a name, not a step
            named = {}
        if named.get("project"):
            out["project"] = str(named["project"])
        if named.get("task"):
            out["task"] = str(named["task"])
    return out


def examples_of(record: Any) -> List[Dict[str, Any]]:
    """Every case a nature was seen in, as its record keeps them, oldest
    first. A record written before it kept more than one has its one
    example, with the stage it was written from."""
    meta = getattr(record, "metadata", None) or {}
    said = meta.get("examples")
    if isinstance(said, str):
        try:
            said = json.loads(said)
        except ValueError:
            said = None
    if isinstance(said, list):
        return [dict(one) for one in said if isinstance(one, dict)]
    one = example_of(record)
    if not (one.get("what") or one.get("said") or one.get("references")):
        return []
    stages = stages_of(record)
    written = getattr(record, "created_at", None)
    return [{"stage": stages[0] if stages else "",
             "at": written.isoformat() if hasattr(written, "isoformat")
             else str(written or ""),
             "said": _said_of(one.get("said")), "what": str(one.get("what") or ""),
             "references": [dict(r) for r in one.get("references") or []
                            if isinstance(r, dict)]}]


def worded_from(record: Any) -> int:
    """How many examples a nature had when it was last worded - one, for a
    record written before this was kept: it was worded from its one case."""
    meta = getattr(record, "metadata", None) or {}
    try:
        return max(0, int(meta.get("worded_examples")))
    except (TypeError, ValueError):
        return min(1, len(examples_of(record)))


def wants_rewording(count: int, worded: int) -> bool:
    """Whether a nature's examples have changed enough since it was worded
    to word it again: REWORD_SHARE of them new, and REWORD_LEAST at least."""
    new = count - worded
    return count > 0 and new >= REWORD_LEAST and new >= REWORD_SHARE * count


def examples_said(examples: Sequence[Dict[str, Any]]) -> List[str]:
    """The examples, as the record's text says them: each its label, the
    user's words and every place it can be opened."""
    lines: List[str] = []
    for one in examples:
        lines.append(f"For example: {one.get('what') or '(a case)'}")
        if one.get("said"):
            lines.append("  The user said: “"
                         + re.sub(r"\s+", " ", str(one["said"])) + "”")
        for place in one.get("references") or []:
            if place.get("location"):
                lines.append(f"  - {place['location']}"
                             + (f" ({place['what']})" if place.get("what") else ""))
    return lines


def nature_body(aspect: str, content: str, examples: Sequence[Dict[str, Any]],
                confidence: int, stages: Sequence[str], reasoning: str = "",
                evidence: Sequence[str] = ()) -> str:
    """A nature record's text: what it is, every example, and what it rests on."""
    return "\n".join([f"Kind: work nature - {ASPECT_SAID.get(aspect, aspect)}.", "",
                      content, "", *examples_said(examples), "",
                      f"Confidence: {confidence} of 7.",
                      *(["", "How this was reached:", str(reasoning)[:600]]
                        if reasoning else []),
                      *(["", "What it rests on:", *[f"- {e}" for e in evidence]]
                        if evidence else []),
                      "", "From the stages: " + ", ".join(stages)])


#: The examples in a record's text: "For example:" lines and what is
#: indented under them.
_EXAMPLES_IN_BODY = re.compile(r"^For example:.*(?:\n(?:For example:|  ).*)*",
                               re.MULTILINE)


def _with_examples(body: str, examples: Sequence[Dict[str, Any]],
                   stages: Sequence[str] = ()) -> str:
    """A record's text with its examples - and the stages it rests on - as
    they are now; the rest of it as it was written."""
    said = "\n".join(examples_said(examples))
    body = str(body or "")
    if _EXAMPLES_IN_BODY.search(body):
        body = _EXAMPLES_IN_BODY.sub(lambda _m: said, body, count=1)
    else:
        body = body.rstrip() + "\n\n" + said
    if stages:
        line = "From the stages: " + ", ".join(stages)
        if re.search(r"^From the stages: .*$", body, re.MULTILINE):
            body = re.sub(r"^From the stages: .*$", lambda _m: line, body,
                          count=1, flags=re.MULTILINE)
        else:
            body = body.rstrip() + "\n\n" + line
    return body


def _confidence_in(body: str) -> int:
    found = re.search(r"^Confidence: (\d) of 7\.", str(body or ""), re.MULTILINE)
    return int(found.group(1)) if found else 4


def _legacy_example(example: Dict[str, Any]) -> str:
    """The first example, in the shape `metadata.example` always had - so
    what read one example still reads it."""
    return json.dumps({"what": example.get("what") or "",
                       "said": example.get("said") or "",
                       "references": list(example.get("references") or [])},
                      ensure_ascii=False)


def add_example(library: Any, store: Optional[ExperienceStore], record: Any,
                example: Dict[str, Any], stages: Sequence[str] = (),
                reword: Optional[Any] = None, run: str = "", by: str = "") -> Any:
    """ONE MORE CASE OF A NATURE ALREADY KNOWN - no new nature.

    The case is appended to its examples (newest last) and the stage linked
    to it. Its words stand: it is worded again, from all its examples, only
    once they have changed a lot since it was worded (`wants_rewording`) -
    and then through `revise_nature`, so what it said before is kept in its
    history. `reword(record, examples)` gives the new words, or None."""
    examples = examples_of(record)
    same = next((one for one in examples if example.get("stage")
                 and one.get("stage") == example.get("stage")
                 and (one.get("said") or "") == (example.get("said") or "")), None)
    if same is not None:
        # The same case read again: one example, with every place either named.
        known = {r.get("location") for r in same.get("references") or []}
        same["references"] = (list(same.get("references") or [])
                              + [r for r in example.get("references") or []
                                 if r.get("location") not in known]
                              )[:EXAMPLE_REFERENCES]
        same["what"] = same.get("what") or example.get("what") or ""
    else:
        examples.append(dict(example))
    worded = worded_from(record)
    all_stages = list(dict.fromkeys(stages_of(record) + [str(s) for s in stages if s]))
    metadata = {"examples": json.dumps(examples, ensure_ascii=False),
                "worded_examples": worded}
    if not example_of(record) and examples:
        metadata["example"] = _legacy_example(examples[0])
    if reword is not None and wants_rewording(len(examples), worded):
        said = reword(record, examples) or {}
        title = str(said.get("title") or "").strip()
        content = str(said.get("content") or "").strip()
        if title and content:
            metadata["worded_examples"] = len(examples)
            aspect = str((getattr(record, "metadata", None) or {}).get("aspect") or "")
            body = nature_body(aspect, content, examples,
                               _confidence_in(record.body), all_stages)
            return revise_nature(
                library, record, title=title, summary=content[:500], body=body,
                metadata=metadata, stages=stages, store=store, run=run, by=by,
                why=f"worded again from its {len(examples)} examples - "
                    f"{len(examples) - worded} of them new since it was worded")
    return revise_nature(library, record,
                         body=_with_examples(record.body, examples, all_stages),
                         metadata=metadata, stages=stages, store=store,
                         wording_kept=True)


class NatureWriter:
    """Writes what stages show about the user: a new nature, or one more
    example of a nature already known.

    THE KNOWN NATURES ARE PUT IN FRONT OF THE MODEL (`show`) - the ones
    closest in meaning to the stages being read - so a trait seen again is
    said to be that trait (`same_as`) and becomes one more of its examples.
    Without them every lesson wrote its own nature, and the same trait from
    ten lessons was ten natures, each with one example."""

    def __init__(self, library: Any, store: ExperienceStore, models: Any = None,
                 language: str = "English", names: Any = None,
                 used: Optional[Dict[str, int]] = None, embedder: Any = None) -> None:
        self.library = library
        self.store = store
        self.models = models
        # English whatever is passed: a nature is matched, grouped and
        # searched by its words (NATURES_LANGUAGE).
        self.language = NATURES_LANGUAGE
        self.names = names
        self.used = used if used is not None else {"calls": 0, "tokens_in": 0,
                                                    "tokens_out": 0}
        self.embedder = embedder
        #: The natures shown, by id: the ones a `same_as` may name.
        self.known: Dict[str, Any] = {}
        #: Model calls made to word a nature again.
        self.reworded = 0

    # -- the known natures, closest first -------------------------------------
    def show(self, texts: Sequence[str], limit: int = NEAR_SHOWN) -> str:
        """The known natures closest to these texts, as the model is shown
        them - '' when there are none. Each is remembered as one a
        `same_as` may name."""
        found = closest_natures(self.library, self.store, self.embedder,
                                [t for t in texts if t], limit)
        self.known.update({record.id: record for record in found})
        if not found:
            return ""
        lines = ["KNOWN NATURES - the user's natures closest to these stages. "
                 "One shown again is a case of it: \"same_as\" its id, not a "
                 "new nature."]
        for record in found:
            meta = getattr(record, "metadata", None) or {}
            count = len(examples_of(record))
            lines.append(f"- [{record.id}] {meta.get('aspect') or '?'} · "
                         f"{count} example{'' if count == 1 else 's'} · {record.title}")
        return "\n".join(lines)

    # -- writing --------------------------------------------------------------
    def write(self, said: Dict[str, Any], stages: Sequence[str],
              writer: Dict[str, str],
              references: Sequence[Dict[str, Any]] = (),
              rows: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
        """One finding: "new" for a nature written, "added" for an example
        added to a known one, "" for nothing kept."""
        if not isinstance(said, dict):
            return ""
        stages = [str(one) for one in stages]
        if not stages:
            return ""
        rows = rows or {}
        first = rows.get(stages[0]) or {"id": stages[0]}
        # The references of the stage the case is in first - each stage
        # names its own r1, r2 - then the others'.
        refs = list(references) + [
            one for stage_id in stages
            for one in ((rows.get(stage_id) or {}).get("work") or {}).get("references")
            or []]
        example = make_example(said.get("example"), stages[0], refs, first,
                               self.names)
        if example is None:
            return ""
        same = str(said.get("same_as") or "").strip()
        if same:
            record = self.known.get(same)
            if record is None:
                # A nature it was not shown: not guessed at, and not written
                # as a new one either - it said it was not new.
                return ""
            by = f"{writer.get('model', '')} ({writer.get('provider', '')})".strip()
            saved = add_example(self.library, self.store, record, example, stages,
                                reword=self._reword if self.models is not None
                                else None, by=by)
            self.known[saved.id] = saved
            return "added"
        record = _write_new(self.library, self.store, said, stages, writer, example)
        if record is None:
            return ""
        self.known[record.id] = record
        return "new"

    def _reword(self, record: Any, examples: Sequence[Dict[str, Any]]
                ) -> Optional[Dict[str, Any]]:
        from ...models.base import ModelRequest
        meta = getattr(record, "metadata", None) or {}
        aspect = str(meta.get("aspect") or "")
        system = (REWORD_PROMPT.replace("{aspect}", ASPECT_SAID.get(aspect, aspect))
                  .replace("{worded}", str(worded_from(record)))
                  .replace("{count}", str(len(examples))))
        asked = "\n".join([f"THE NATURE: {record.title}", record.summary or "", "",
                           *examples_said(examples)])
        try:
            response = self.models.invoke(
                ModelRequest(messages=[{"role": "user", "content": asked}],
                             system=system, max_tokens=1200,
                             task=model_task(self.store.scope, "stages"),
                             language=NATURES_SAID,
                             metadata={"kind": "experience nature reworded"}),
                role="heartbeat")
        except Exception as exc:          # pragma: no cover - a call
            log.info("could not word a nature again: %s", exc)
            return None
        self.reworded += 1
        count_use(self.used, response)
        return _as_json(getattr(response, "text", "") or "")


def _write_new(library: Any, store: ExperienceStore, said: Dict[str, Any],
               stages: Sequence[str], writer: Dict[str, str],
               example: Dict[str, Any]) -> Any:
    """A new nature record, with its first example: written to the user's
    nature and linked to the stages it rests on."""
    title = str(said.get("title") or "").strip()
    aspect = str(said.get("aspect") or "").strip().lower()
    # Only how they evaluate and what output they want.
    if not title or aspect not in ASPECTS:
        return None
    kind = "work"
    from ...data_manage.library import USER_NATURE, Record
    try:
        confidence = max(1, min(7, int(said.get("confidence") or 4)))
    except (TypeError, ValueError):
        confidence = 4
    evidence = [str(one)[:300] for one in said.get("evidence") or []][:8]
    content = str(said.get("content") or "")
    stages = list(stages)
    body = nature_body(aspect, content, [example], confidence, stages,
                       str(said.get("reasoning") or ""), evidence)
    record = library.write(Record(
        folder=USER_NATURE, title=title[:200], summary=content[:500], body=body,
        references=[f"stage: {one}" for one in stages],
        source="experience",
        # As JSON: a record's front matter is one line per key.
        metadata={"nature_kind": kind, "aspect": aspect,
                  "example": _legacy_example(example),
                  "examples": json.dumps([example], ensure_ascii=False),
                  "worded_examples": 1,
                  "stages": stages,
                  "written_by": f"{writer.get('model', '')} "
                                f"({writer.get('provider', '')})".strip()}))
    store.set_nature_kind(record.id, kind, writer)
    for stage_id in stages:
        store.link_stage_nature(stage_id, record.id)
    return record


def write_nature(library: Any, store: ExperienceStore, said: Dict[str, Any],
                 stages: Sequence[str], writer: Dict[str, str],
                 references: Sequence[Dict[str, Any]] = (),
                 natures: Optional[NatureWriter] = None,
                 rows: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
    """One finding of what a stage shows about the user: a new nature, or -
    `same_as` a nature `natures` was shown - one more example of it.
    "new", "added" or "" for nothing kept."""
    natures = natures or NatureWriter(library, store)
    return natures.write(said, stages, writer, references, rows)


def _terms(text: str) -> Dict[str, int]:
    from .index import terms_of
    out: Dict[str, int] = {}
    for term in terms_of(text):
        out[term] = out.get(term, 0) + 1
    return out


def closest_natures(library: Any, store: ExperienceStore, embedder: Any,
                    texts: Sequence[str], limit: int = NEAR_SHOWN) -> List[Any]:
    """The user's work natures closest in meaning to any of these texts.

    BY THE VECTORS THE PATTERNS ALREADY KEEP for every nature, where the
    embedding model is up - a nature's vector is read once and kept, and
    only the texts asked about are read now. Starting the model for this
    alone would be sixteen seconds a call, so where it is not up the words
    are compared instead: each term weighted by how rare it is among the
    natures."""
    from .natures import kinds_of
    from .patterns import nature_records
    if not texts or library is None:
        return []
    kinds = store.nature_kinds()
    records = [record for record in nature_records(library)
               if "work" in kinds_of(record, kinds)]
    if not records:
        return []
    scores = _by_vectors(embedder, records, texts)
    if scores is None:
        scores = _by_terms(records, texts)
    ranked = sorted((one for one in records if scores.get(one.id, 0.0) > 0.0),
                    key=lambda one: -scores[one.id])
    return ranked[:limit]


def _by_vectors(embedder: Any, records: Sequence[Any], texts: Sequence[str]
                ) -> Optional[Dict[str, float]]:
    if embedder is None:
        return None
    try:
        if not (embedder.status() or {}).get("loaded"):
            return None
        from .patterns import _Text, _dot
        vectors = embedder.read_states([_Text(r.id, r.title, r.summary or "")
                                        for r in records])
        asked = embedder.embed([str(t)[:2000] for t in texts])
    except Exception:                     # pragma: no cover - fall back to words
        log.debug("could not compare natures by meaning", exc_info=True)
        return None
    if not vectors or not asked:
        return None
    return {one: max(_dot(vector, mine) for mine in asked)
            for one, vector in vectors.items()}


def _by_terms(records: Sequence[Any], texts: Sequence[str]) -> Dict[str, float]:
    """Each nature's closeness to the texts by the words they share, each
    word weighted by how rare it is among the natures."""
    import math
    docs = {r.id: _terms(f"{r.title} {r.summary or ''}") for r in records}
    seen: Dict[str, int] = {}
    for terms in docs.values():
        for term in terms:
            seen[term] = seen.get(term, 0) + 1
    rare = {term: math.log(1 + len(docs) / count) for term, count in seen.items()}

    def weighted(terms: Dict[str, int]) -> Dict[str, float]:
        out = {t: n * rare.get(t, math.log(1 + len(docs))) for t, n in terms.items()}
        size = math.sqrt(sum(v * v for v in out.values())) or 1.0
        return {t: v / size for t, v in out.items()}

    asked = [weighted(_terms(t)) for t in texts]
    out: Dict[str, float] = {}
    for one, terms in docs.items():
        mine = weighted(terms)
        out[one] = max((sum(v * mine.get(t, 0.0) for t, v in q.items())
                        for q in asked), default=0.0)
    return out


def example_of(record: Any) -> Dict[str, Any]:
    """The case a nature was seen in, as its record keeps it - {} for a
    record written without one."""
    said = (getattr(record, "metadata", None) or {}).get("example") or {}
    if isinstance(said, str):
        try:
            said = json.loads(said)
        except ValueError:
            return {}
    return said if isinstance(said, dict) else {}


def stages_of(record: Any) -> List[str]:
    """The stages a nature was written from, as its record keeps them. The
    front matter is one line per key, so a list written there comes back
    as its text - "['stage_a', 'stage_b']" - and is read back from it."""
    said = (getattr(record, "metadata", None) or {}).get("stages") or []
    if isinstance(said, str):
        text = said.strip()
        try:
            said = json.loads(text)
        except ValueError:
            said = re.findall(r"stage_[A-Za-z0-9_-]+", text)
    if not isinstance(said, (list, tuple)):
        return []
    return list(dict.fromkeys(str(one).strip() for one in said if str(one).strip()))


#: How much of an earlier version's body is kept with it.
HISTORY_BODY = 4000


def history_of(record: Any) -> List[Dict[str, Any]]:
    """A nature's earlier versions, newest first - [] for one never
    rewritten. Kept in its record, as JSON on one line of the front matter."""
    said = (getattr(record, "metadata", None) or {}).get("history") or []
    if isinstance(said, str):
        try:
            said = json.loads(said)
        except ValueError:
            return []
    if not isinstance(said, list):
        return []
    out = [dict(one) for one in said if isinstance(one, dict)]
    # Newest first by when each was replaced - and, written in the same
    # instant, the later written first: they are kept in the order made.
    order = sorted(enumerate(out), key=lambda pair: (
        str(pair[1].get("replaced_at") or pair[1].get("at") or ""), pair[0]), reverse=True)
    return [one for _at, one in order]


def revise_nature(library: Any, record: Any, *, title: Optional[str] = None,
                  summary: Optional[str] = None, body: Optional[str] = None,
                  metadata: Optional[Dict[str, Any]] = None, why: str = "",
                  run: str = "", by: str = "",
                  merged_from: Sequence[str] = (), split_from: str = "",
                  stages: Sequence[str] = (),
                  store: Optional[ExperienceStore] = None,
                  wording_kept: bool = False,
                  merged_records: Sequence[Any] = ()) -> Any:
    """REWRITE A NATURE IN PLACE, KEEPING WHAT IT SAID BEFORE.

    A nature is written once, from the stage that first showed it
    (`write_nature`); after that it changes only here - a case added to it
    (`add_example`), and this is where a rewrite goes - rewording, merging other
    natures into it, splitting it - so that the earlier version is never
    lost: the version being replaced is added to `metadata.history` with
    when it was written, when it was replaced, why, by which run, and the
    natures merged into it or the one it was split from. Stages it now also
    rests on are added to it and linked.

    ONLY ITS EXAMPLES CHANGED (`wording_kept`): a case appended, its text
    and examples rewritten, its words the same - no earlier version is
    added, as nothing it said has changed. The natures merged into it
    (`merged_records`) are kept in the history whole, since their files go.

    The record given is not changed - records read through the shared
    reading (patterns.folder_records) are shared - and the rewritten copy
    is returned."""
    import copy
    from ...common.events.models import utcnow
    old_meta = dict(getattr(record, "metadata", None) or {})
    new = copy.copy(record)
    new.metadata = dict(old_meta)
    if metadata:
        new.metadata.update({k: v for k, v in metadata.items() if k != "history"})
    if title is not None:
        new.title = str(title)[:200]
    if summary is not None:
        new.summary = str(summary)[:500]
    if body is not None:
        new.body = str(body)
    added = [str(one) for one in stages if str(one).strip()]
    if added:
        new.metadata["stages"] = list(dict.fromkeys(stages_of(record) + added))
    changed = (new.title != record.title or new.summary != (record.summary or "")
               or (new.body or "") != (record.body or "")
               or bool(merged_from) or bool(split_from))
    if changed and not wording_kept:
        written = getattr(record, "updated_at", None) or getattr(record, "created_at", None)
        earlier = list(reversed(history_of(record)))      # oldest first, as kept
        earlier.append({
            "at": written.isoformat() if hasattr(written, "isoformat") else str(written or ""),
            "replaced_at": utcnow().isoformat(),
            "title": record.title, "summary": record.summary or "",
            "body": (record.body or "")[:HISTORY_BODY],
            "example": example_of(record),
            "why": str(why or "")[:600], "run": str(run or "")[:200],
            "by": str(by or "")[:200],
            "merged_from": [str(one) for one in merged_from if str(one).strip()],
            "split_from": str(split_from or ""),
            "merged": [{"id": one.id, "title": one.title,
                        "summary": one.summary or "",
                        "body": (one.body or "")[:HISTORY_BODY // 2],
                        "examples": examples_of(one)}
                       for one in merged_records]})
        new.metadata["history"] = json.dumps(earlier, ensure_ascii=False)
    saved = library.put(new)
    if store is not None:
        for stage_id in added:
            store.link_stage_nature(stage_id, saved.id)
    return saved


#: How each aspect is said in a record's text.
ASPECT_SAID = {"evaluation": "how they evaluate a result",
               "output": "what output they want"}

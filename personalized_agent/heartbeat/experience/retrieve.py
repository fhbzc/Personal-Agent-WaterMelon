"""Finding the experience that bears on a step of work - by code alone.

No model and no embedding: a past stage is found by what it IS, compared
field by field with the step in hand, in a fixed order where each question
only breaks the ties the ones before it left:

  1. KIND - the step's own kind first, then its neighbour in the loop
     (an evaluation and the adjustment it leads to; an execution and its
     delivery; a goal and its plan). Any other kind is not looked at.
  2. TYPE - what the work produces: the same, a related one (a document
     and slides, data and a figure), or another.
  3. PLACE IN ITS CARD - the attempts before it in its card, matched from
     the latest back: "after a plan and a rejected evaluation" matches a
     stage that came after the same; the longest match first, then the same
     level of the tree (root, deliverable, part).
  4. ENVIRONMENT - the kinds of file, folders and sites its references
     point to, shared with the step's.
  5. THE SAME PROJECT.
  6. WHAT IT SHOWS OF THE USER - a decision the user made, a specification
     they set, a verdict in their words.
  7. WORDS - the terms of the step found in the stage, each worth what it is
     rare (index.py), and what it counts in the stage.
  8. RECENCY.

Around each match, its chain: the stages before it and after it along the
graph's edges, a few steps either way - why it happened, and what came of
it. Then the rules: the nature records linked to those stages (or to the
states they are made of), the patterns they are in, the principles above -
work nature only.

Every match says why it was found.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .index import StageIndex, terms_of
from .patterns import nature_records
from .stage_natures import examples_of
from .store import ExperienceStore, LIVE

log = logging.getLogger(__name__)

#: How many steps before and after a matched stage it brings. A step either
#: side was measured to add useful context; the user asked for more, now
#: that a stage records the work itself, and the nearer steps still count
#: for more (FALLOFF).
STEPS_AROUND = 1

#: How many matched stages seed the chains.
SEEDS = 5

#: How many rules are kept of each kind - each kind its own room, so the
#: specific nature records are not crowded out by the general.
KEPT_PER_KIND = {"principle": 5, "pattern": 8, "nature": 20}

#: How many cases each rule comes with: a nature's own cases, a pattern's
#: natures, a principle's patterns.
EXAMPLES_PER_RULE = 5

#: A STAGE IS A MATCH ONLY ON WHAT IT SAYS: a stage whose words match
#: less than this share of the best match's is not handed over - nor is
#: one that matches none of them. The five were filled whatever matched,
#: and a stage of the right kind came first whatever it was about.
RELEVANT_SHARE = 0.25

#: How much each likeness counts, once the words match: a share more of
#: the words' score for each.
LIKENESS = {"kind": 0.15, "type": 0.15, "place": 0.1, "level": 0.1,
            "environment": 0.1, "project": 0.3, "shows": 0.05,
            # A PROJECT LIKE THIS ONE: the stage's project's goal sharing the
            # distinctive words of this work's goal - a website with
            # accounts beside another one, not beside the agent's own
            # internals that happen to say "token" too. As much as the
            # share of the goal's words it has.
            "like_this": 0.6}

#: How close to the most alike past project another must be to count as
#: alike too - its share of that one's score; at LIKE_FROM it counts nothing,
#: as close as that one, all of LIKENESS["like_this"].
LIKE_FROM = 0.5


#: A nature found by its own words, as much as one linked to a matched stage.
NATURE_BY_WORDS = 1.0

#: STRICTER MATCHING (2026-10-17, measured on the user's own run first): a
#: match on words is a match on DISTINCTIVE words. A word in more than this
#: share of the user's stages - "task", "token" in a record of building an
#: agent - says nothing of which work a stage is, and counts for nothing;
#: so do these fillers, whatever their share.
GENERIC_SHARE = 0.10
#: ...once there are this many stages to tell by: in a small record every
#: word is in a large share of it, and none would be left to match on.
GENERIC_FROM = 50
IGNORED_WORDS = frozenset({"per", "app", "apps", "via", "etc", "eg", "ie", "vs"})
#: A stage is a match only when it shares this many distinctive words with
#: the step: one shared word was enough, and "task", "shared" brought the
#: grouping of the user's states to a website's build.
MIN_SHARED_WORDS = 2
#: WHAT IT SAYS DECIDES: a stage qualifies by its words alone, beside the
#: best match's words; its likenesses (same kind, shows how the user judges)
#: then order those that qualify - they no longer carry a weak match over.
WORDS_DECIDE = True
#: A RULE MUST BEAR ON THE STEP: a nature found by its own words shares this
#: many distinctive words with it; and of each kind - nature, pattern,
#: principle - only those scoring at least this share of the best are
#: handed over, however few. The quotas are a most, not a number to fill.
NATURE_MIN_WORDS = 2
RULE_MIN_SHARE = 0.3

#: The principles with the most support, given every time.
ALWAYS_PRINCIPLES = 0

#: A step away from a matched stage is worth this much of it.
FALLOFF = 0.7

#: A nature record linked to a stage's states, not to the stage, counts
#: this much: the same evidence, a step further from the stage.
THROUGH_STATES = 0.5

#: The kinds next to each other in the loop.
LOOP_NEIGHBOURS = {"goal": ("plan",), "plan": ("goal", "adjustment"),
                   "execution": ("delivery",), "delivery": ("execution",),
                   "evaluation": ("adjustment",),
                   "adjustment": ("evaluation", "plan")}

#: Types of work close enough that one is an example for the other.
RELATED_TYPES = {frozenset(pair) for pair in (
    ("document", "slides"), ("document", "figure"), ("data", "figure"),
    ("email", "message"), ("code", "setup"), ("slides", "figure"),
    ("meeting", "message"))}

#: How many attempts back the place in a card is matched.
PLACE_DEPTH = 3


def _place_of(stage: Dict[str, Any]) -> str:
    """One attempt as a step of its card's history: its kind, and the
    verdict when it was judged."""
    verdict = str((stage.get("evaluation") or {}).get("verdict") or "").lower()
    if verdict.startswith("rej"):
        return f"{stage['kind']}:rejected"
    if verdict.startswith(("acc", "pass")):
        return f"{stage['kind']}:accepted"
    return str(stage["kind"])


def _shared_tail(one: Sequence[str], other: Sequence[str]) -> int:
    """How many of the latest steps two histories share, back from the end."""
    n = 0
    while n < min(len(one), len(other), PLACE_DEPTH) and \
            one[-1 - n] == other[-1 - n]:
        n += 1
    return n


def _type_match(mine: str, theirs: str) -> int:
    if not mine or not theirs:
        return 0
    if mine == theirs:
        return 2
    return 1 if frozenset((mine, theirs)) in RELATED_TYPES else 0


class Retriever:
    """Experience for one step: past stages like it, and the rules they lead
    to. `embedder` is accepted and not used: nothing here is a guess by
    meaning."""

    def __init__(self, db: Any, library: Any, embedder: Any = None,
                 scope: str = LIVE) -> None:
        self.store = ExperienceStore(db, scope)
        self.index = StageIndex(db, scope)
        self.library = library

    # -- the step, as a question ---------------------------------------------------
    def profile_of_stage(self, stage: Dict[str, Any], unit_tasks: Sequence[str] = (),
                         text: str = "") -> Dict[str, Any]:
        """A step of work asked about as a stage - a proposed one, or one in
        the record: its kind, type, place in its card, environment, project
        and words."""
        from .index import stage_terms
        cards = {card["id"]: card for card in self.store.cards()}
        card = cards.get(stage.get("card_id") or "")
        before = [_place_of(one) for one in self.store.stages()
                  if one.get("card_id") == stage.get("card_id") and card
                  and str(one.get("began") or "") < str(stage.get("began") or "~")
                  and one["id"] != stage.get("id")]
        from .stages import depth_of, level_of
        return {"kind": stage.get("kind", ""),
                "type": (card or {}).get("type") or stage.get("medium") or "",
                "before": before[-PLACE_DEPTH:],
                "level": level_of(depth_of(card, cards)) if card else "",
                "environment": [term for term in stage_terms(stage, card)
                                if term.startswith("env:")
                                and not term.startswith("env:medium:")],
                "tasks": list(unit_tasks),
                "text": text or " ".join(filter(None, [
                    stage.get("summary", ""), (card or {}).get("title", ""),
                    (card or {}).get("statement", "")]))}

    # -- looking ---------------------------------------------------------------------
    def retrieve(self, query: str, kinds: Sequence[str] = (),
                 medium: str = "", steps: int = STEPS_AROUND,
                 seeds: int = SEEDS, kept: Optional[Dict[str, int]] = None,
                 leave_out_tasks: Sequence[str] = ()) -> Dict[str, Any]:
        """Past work like a step said in words, with the kinds it may be."""
        return self.find({"kinds": list(kinds), "type": medium, "text": query,
                          "leave_out_tasks": list(leave_out_tasks)},
                         steps=steps, seeds=seeds, kept=kept)

    def find(self, profile: Dict[str, Any], steps: int = STEPS_AROUND,
             seeds: int = SEEDS, kept: Optional[Dict[str, int]] = None
             ) -> Dict[str, Any]:
        """The stages most like the step `profile` describes, ranked level by
        level, each with why it was found; their chains; and the rules they
        lead to."""
        out: Dict[str, Any] = {"query": profile.get("text", ""), "stages": [],
                               "rules": [], "chains": [], "steps": steps,
                               "kept": kept}
        cards = {card["id"]: card for card in self.store.cards()}
        # EVERY STAGE IS READ ONCE A SEARCH. It was read three times - for
        # the candidates, their places in their cards, and the chains - each
        # time the whole scope parsed from JSON; the three readings were the
        # same rows, so one does for all of them (nothing below changes a
        # stage it was given).
        every_stage = self.store.stages()
        stages = every_stage
        # Stages written before there was an index are indexed now, by the
        # same code as any other.
        self.index.ensure(stages, cards)
        left_out = set(profile.get("leave_out") or [])
        left_out_tasks = set(profile.get("leave_out_tasks") or [])
        # Only these tasks' stages - a search kept to one project.
        only = set(profile.get("only_tasks") or [])
        stages = [s for s in stages if s["id"] not in left_out
                  and s["task_id"] not in left_out_tasks
                  and (not only or s["task_id"] in only)]
        by_id = {s["id"]: s for s in stages}

        # 1. The kinds looked at: the step's own, then its neighbours.
        own = [k for k in ([profile.get("kind")] if profile.get("kind") else [])
               + list(profile.get("kinds") or []) if k]
        near = {n for k in own for n in LOOP_NEIGHBOURS.get(k, ()) if n not in own}
        if own:
            stages = [s for s in stages if s["kind"] in own or s["kind"] in near]

        # Each candidate's place in its card: the attempts before it.
        from .stages import depth_of, level_of
        history: Dict[str, List[Dict[str, Any]]] = {}
        for stage in sorted(every_stage, key=lambda s: (
                str(s.get("began") or ""), str(s.get("created_at") or ""))):
            history.setdefault(stage.get("card_id") or "", []).append(stage)
        before_of: Dict[str, List[str]] = {}
        for card_id, run in history.items():
            for n, stage in enumerate(run):
                before_of[stage["id"]] = [_place_of(one) for one in
                                          run[max(0, n - PLACE_DEPTH):n]]

        words = list(dict.fromkeys(terms_of(profile.get("text", ""))))
        rarity = self.index.rarity(words) if words else {}
        # rarity is log(1 + N/df): the share of stages a word is in, back
        # from it - more than GENERIC_SHARE, and it tells no work apart.
        counted = len(self.index.indexed()) if words else 0
        distinctive = {w for w in words if w not in IGNORED_WORDS and (
            counted < GENERIC_FROM or (rarity.get(w, 0.0) > 0 and 1.0 / max(
                1e-9, math.exp(rarity[w]) - 1.0) <= GENERIC_SHARE))}
        # As many words as the step has to give, up to MIN_SHARED_WORDS.
        needed = min(MIN_SHARED_WORDS, max(1, len(distinctive)))
        # Only the terms a stage is compared by: the step's words, and the
        # places ("env:..."). Every term of every stage was fetched - 53,235
        # rows, the slowest part of a search - to look up a handful in each.
        found_terms = self.index.terms([s["id"] for s in stages],
                                       only=words, places=True)
        # A PROJECT LIKE THIS ONE: this work's goal, in English words
        # (`goal_text`), against each past project's stages as a whole - a
        # project's few top cards say too little to compare. Each goal word
        # counts by its rarity and by the share of the project's stages that
        # say it; the projects are then put against the closest one, and
        # only those near it (from LIKE_FROM of its score) count as alike.
        goal_words = [w for w in dict.fromkeys(terms_of(profile.get("goal_text", "")))
                      if w not in IGNORED_WORDS]
        project_of = {s["id"]: (cards.get(s.get("card_id") or "") or {}).get("unit_id")
                      or s["task_id"] for s in every_stage}
        alike: Dict[str, float] = {}
        alike_words: Dict[str, List[str]] = {}
        if goal_words:
            goal_rarity = self.index.rarity(goal_words)
            said = self.index.terms(list(project_of), only=goal_words)
            size: Dict[str, int] = {}
            uses: Dict[str, Dict[str, int]] = {}
            for stage_id, project in project_of.items():
                size[project] = size.get(project, 0) + 1
                for w in said.get(stage_id, {}):
                    if w in goal_rarity:
                        uses.setdefault(project, {})[w] = uses.get(project, {}).get(w, 0) + 1
            score_of = {project: sum(goal_rarity[w] * n / size[project]
                                     for w, n in used.items())
                        for project, used in uses.items()}
            best = max(score_of.values(), default=0.0)
            for project, score in score_of.items():
                if best > 0 and score >= LIKE_FROM * best:
                    alike[project] = (score / best - LIKE_FROM) / (1 - LIKE_FROM)
                    alike_words[project] = sorted(
                        uses[project], key=lambda w: -goal_rarity[w] * uses[project][w])

        def like_this(stage: Dict[str, Any]) -> Tuple[float, List[str]]:
            project = project_of.get(stage["id"])
            return alike.get(project, 0.0), alike_words.get(project, [])
        environment = set(profile.get("environment") or [])
        tasks = set(profile.get("tasks") or [])
        mine_type = str(profile.get("type") or "")
        mine_before = list(profile.get("before") or [])
        mine_level = str(profile.get("level") or "")

        ranked = []
        for stage in stages:
            card = cards.get(stage.get("card_id") or "")
            theirs = (card or {}).get("type") or stage.get("medium") or ""
            terms = found_terms.get(stage["id"], {})
            kind_level = 1 if (not own or stage["kind"] in own) else 0
            type_level = _type_match(mine_type, theirs)
            tail = _shared_tail(mine_before, before_of.get(stage["id"], []))
            level = level_of(depth_of(card, cards)) if card else ""
            same_level = 1 if mine_level and level == mine_level else 0
            shared_places = sorted(environment & {t for t in terms if t.startswith("env:")})
            place_level = min(2, len(shared_places))
            same_project = 1 if tasks and stage["task_id"] in tasks else 0
            work = stage.get("work") or {}
            shows = (int(((work.get("decision") or {}).get("by")) == "user")
                     + int(bool((work.get("output") or {}).get("specification")))
                     + int(bool((stage.get("evaluation") or {}).get("words"))))
            similar, similar_words = like_this(stage)
            matched_words = [w for w in words if w in terms and w in distinctive]
            score = sum(rarity.get(w, 0.0) * terms[w] for w in matched_words)
            if len(matched_words) < needed:
                score = 0.0
            if words:
                # WHAT IT SAYS FIRST: the likenesses count only on top of a
                # match on words, as shares more of it.
                total = score * (1 + LIKENESS["kind"] * kind_level
                                 + LIKENESS["type"] * type_level
                                 + LIKENESS["place"] * min(1, tail)
                                 + LIKENESS["level"] * same_level
                                 + LIKENESS["environment"] * min(1, place_level)
                                 + LIKENESS["project"] * same_project
                                 + LIKENESS["shows"] * min(1, shows)
                                 + LIKENESS["like_this"] * similar)
                key = (round(total, 6), round(score, 6), str(stage.get("began") or ""))
            else:
                # Nothing said in words: by its likenesses alone, as before.
                key = (kind_level, type_level, tail, same_level, place_level,
                       same_project, shows, 0.0, str(stage.get("began") or ""))
            why = []
            why.append("same kind" if kind_level else f"next in the loop ({stage['kind']})")
            if type_level == 2:
                why.append(f"same type ({theirs})")
            elif type_level == 1:
                why.append(f"related type ({theirs})")
            if tail:
                why.append("same place in its card: after "
                           + " → ".join(before_of[stage["id"]][-tail:]))
            if same_level:
                why.append(f"a {level} too")
            if shared_places:
                why.append("same environment: " + ", ".join(
                    one.split(":", 2)[-1] for one in shared_places[:4]))
            if same_project:
                why.append("same project")
            if similar:
                why.append("a project like this one: " + ", ".join(similar_words[:5]))
            if shows:
                why.append("shows how you decide and judge")
            if matched_words:
                why.append("words: " + ", ".join(matched_words[:6]))
            ranked.append((key, stage["id"], why))
        ranked.sort(key=lambda one: one[0], reverse=True)
        if words:
            # Only what matches - and well enough beside the best: by its
            # words alone (WORDS_DECIDE), its likenesses then ordering them.
            at = 1 if WORDS_DECIDE else 0
            best = max((one[0][at] for one in ranked), default=0.0)
            ranked = [one for one in ranked
                      if one[0][1] > 0 and one[0][at] >= RELEVANT_SHARE * best]
            if not ranked:
                out["none_close"] = ("no past stage is close to this: nothing in "
                                     "the user's record matches its words")
        out["matches"] = [{"id": stage_id, "why": why}
                          for _key, stage_id, why in ranked[:max(seeds, 10)]]
        reasons = {stage_id: why for _key, stage_id, why in ranked}

        # 2. Around each match, in order: what led to it, it, what came of it.
        up: Dict[str, List[str]] = {}
        down: Dict[str, List[str]] = {}
        everything = {s["id"]: s for s in every_stage
                      if s["task_id"] not in left_out_tasks
                      and s["id"] not in left_out}
        for edge in self.store.edges():
            up.setdefault(edge["source"], []).append(edge["target"])
            down.setdefault(edge["target"], []).append(edge["source"])

        def reach(start: str, links: Dict[str, List[str]]) -> Dict[str, int]:
            found: Dict[str, int] = {}
            frontier = [(start, 0)]
            while frontier:
                here, hops = frontier.pop(0)
                if hops >= steps:
                    continue
                for nxt in links.get(here, []):
                    if nxt in everything and nxt not in found and nxt != start:
                        found[nxt] = hops + 1
                        frontier.append((nxt, hops + 1))
            return found

        def card_of(stage: Dict[str, Any]) -> Dict[str, Any]:
            card = cards.get(stage.get("card_id") or "")
            if card is None:
                return {}
            return {"title": card["title"], "statement": card["statement"],
                    "level": level_of(depth_of(card, cards)),
                    "status": card["status"],
                    "specification": card.get("specification") or [],
                    "checked_by": card.get("checked_by") or {}}

        weight: Dict[str, float] = {}
        shown: set = set()
        for rank, (_key, seed, _why) in enumerate(ranked[:seeds]):
            if seed in shown:
                continue
            base = 1.0 / (rank + 1)
            chain = [(one, -hops) for one, hops in sorted(
                reach(seed, up).items(), key=lambda p: everything[p[0]]["began"])]
            chain.append((seed, 0))
            chain += [(one, hops) for one, hops in sorted(
                reach(seed, down).items(), key=lambda p: everything[p[0]]["began"])]
            rows = []
            for stage_id, position in chain:
                if stage_id in shown:
                    continue
                shown.add(stage_id)
                value = base * (FALLOFF ** abs(position))
                weight[stage_id] = max(weight.get(stage_id, 0.0), value)
                rows.append(dict(
                    everything[stage_id], position=position,
                    card=card_of(everything[stage_id]), weight=round(value, 4),
                    found="matched" if position == 0 else
                    f"{abs(position)} step{'s' if abs(position) > 1 else ''}"
                    f" {'before' if position < 0 else 'after'} a match",
                    why=reasons.get(stage_id, []) if position == 0 else []))
            out["chains"].append({"matched": seed, "why": reasons.get(seed, []),
                                  "stages": rows})
        out["stages"] = [row for chain in out["chains"] for row in chain["stages"]]

        # 3. The rules: the work nature linked to those stages, the patterns
        # it is in, the principles above.
        from .natures import kinds_of
        kinds = self.store.nature_kinds()
        records = nature_records(self.library)
        work_ids = {r.id for r in records if "work" in kinds_of(r, kinds)}
        by_record = {r.id: r for r in records if r.id in work_ids}
        nature_score: Dict[str, float] = {}
        for stage_id, linked in self.store.natures_of_stages(list(weight)).items():
            for record in linked:
                if record in work_ids:
                    nature_score[record] = nature_score.get(record, 0.0) \
                        + weight[stage_id] / max(1, len(linked))
        # And through the states a stage is made of: what the agent heartbeat
        # wrote from them is linked to them - evidence too, a step further.
        for stage_id in weight:
            states = everything[stage_id].get("states") or []
            linked = [one for ones in self.store.natures_of(states).values()
                      for one in ones if one in work_ids]
            for record in dict.fromkeys(linked):
                nature_score[record] = nature_score.get(record, 0.0) + (
                    THROUGH_STATES * weight[stage_id] / max(1, len(linked)))
        # NATURES FOUND BY THEIR OWN WORDS, too - not only through stages:
        # a step with no past work like it still meets the user's ways of
        # judging that bear on it (cost, failure, how it is checked).
        if words:
            said_of = {}
            for record_id, record in by_record.items():
                shown = examples_of(record)
                said_of[record_id] = set(terms_of(" ".join(
                    [record.title or "", record.summary or ""]
                    + [str(one.get("what") or "") + " " + str(one.get("said") or "")
                       for one in shown])))
            counts: Dict[str, int] = {}
            for terms_here in said_of.values():
                for term in terms_here:
                    counts[term] = counts.get(term, 0) + 1
            total_natures = max(1, len(said_of))
            telling = [w for w in words if w not in IGNORED_WORDS and counts.get(w)
                       and (total_natures < GENERIC_FROM
                            or counts[w] / total_natures <= GENERIC_SHARE)]
            weight_of = {w: math.log(1 + total_natures / counts[w]) for w in telling}
            whole = sum(math.log(1 + total_natures) for _w in words) or 1.0
            for record_id, terms_here in said_of.items():
                shared = [w for w in telling if w in terms_here]
                if len(shared) < min(NATURE_MIN_WORDS, max(1, len(telling))):
                    continue
                hit = sum(weight_of[w] for w in shared)
                if hit > 0:
                    nature_score[record_id] = nature_score.get(record_id, 0.0) \
                        + NATURE_BY_WORDS * hit / whole
        patterns = self.store.patterns("pattern", kind="work")
        principles = self.store.patterns("principle", kind="work")
        pattern_score = {p["id"]: sum(nature_score.get(one, 0.0)
                                      for one in p["members"]) for p in patterns}
        principle_score = {p["id"]: sum(pattern_score.get(one, 0.0)
                                        for one in p["members"]) for p in principles}
        # The principles the user's work shows most, every time: how they
        # judge whatever the step is.
        for p in sorted(principles, key=lambda one: -int(one.get("support") or 0)
                        )[:ALWAYS_PRINCIPLES]:
            principle_score[p["id"]] = max(principle_score.get(p["id"], 0.0), 1e-6)
        rules = []
        patterns_by_id = {p["id"]: p for p in patterns}

        def cases(record: Any) -> List[Dict[str, Any]]:
            """A nature's cases, the newest first: the user's words, what
            the case was, and the stage it was seen in."""
            out = []
            for one in reversed(examples_of(record)):
                case = {key: one[key] for key in ("said", "what", "stage", "at")
                        if one.get(key)}
                if case:
                    out.append(case)
            return out[:EXAMPLES_PER_RULE]

        for kind, scores, rows in (
                ("principle", principle_score, {p["id"]: p for p in principles}),
                ("pattern", pattern_score, patterns_by_id),
                ("nature", nature_score, None)):
            best = max(scores.values(), default=0.0) or 1.0
            for key, value in scores.items():
                if value <= 0:
                    continue
                if rows is not None:
                    row = rows[key]
                    # When it was last seen: a way of working not seen for
                    # long may be one the user has left behind.
                    said = {"statement": row["statement"], "detail": row["detail"],
                            "support": row["support"],
                            "last_active": str(row.get("last_active") or "")}
                    # WHAT SHOWS IT: a pattern's natures, each with a case;
                    # a principle's patterns - the closest to this step first.
                    members = list(row.get("members") or [])
                    if kind == "pattern":
                        members.sort(key=lambda one: nature_score.get(one, 0.0), reverse=True)
                        said["examples"] = [
                            {"nature": by_record[one].title,
                             **({"case": cases(by_record[one])[0]}
                                if cases(by_record[one]) else {})}
                            for one in members if one in by_record][:EXAMPLES_PER_RULE]
                    else:
                        members.sort(key=lambda one: pattern_score.get(one, 0.0), reverse=True)
                        said["examples"] = [
                            {"pattern": patterns_by_id[one]["statement"]}
                            for one in members if one in patterns_by_id][:EXAMPLES_PER_RULE]
                else:
                    record = by_record.get(key)
                    if record is None:
                        continue
                    shown = cases(record)
                    said = {"statement": record.title,
                            "detail": record.summary or "",
                            "support": max(1, len(examples_of(record))),
                            "examples": shown}
                    # HOW SURE, as the lessons engine has it (lessons/natures.py):
                    # in force and sure - follow it; still forming, or less
                    # sure - lean on it.
                    meta = getattr(record, "metadata", None) or {}
                    if meta.get("lesson_state"):
                        sure = float(meta.get("confidence") or 0.5)
                        said["how_sure"] = ("follow" if meta["lesson_state"] == "active"
                                            and sure >= 0.8 else "lean on it")
                        said["confidence"] = round(sure, 2)
                rules.append({"kind": kind, "id": key, **said,
                              "score": round(value / best, 4)})
        quotas = dict(KEPT_PER_KIND, **(kept or {}))
        chosen = []
        for kind in ("principle", "pattern", "nature"):
            mine = sorted((rule for rule in rules if rule["kind"] == kind
                           and rule["score"] >= RULE_MIN_SHARE),
                          key=lambda rule: rule["score"], reverse=True)
            chosen += mine[:max(0, int(quotas.get(kind, 0)))]
        out["rules"] = chosen
        return out


def backfill_links(db: Any, library: Any) -> int:
    """Link the states and nature records each past heartbeat run wrote.

    Before links were kept, the only record of which run wrote what is the
    log of the agent's actions - "wrote a state", with its title - and the
    nature records' own note of the run that wrote them. Every state a run
    wrote is linked to every nature record it wrote: coarser than a link a
    run makes now, which names the states each record rests on, and marked
    as made this way.
    """
    records_of: Dict[str, List[str]] = {}
    for record in nature_records(library):
        for reference in getattr(record, "references", []) or []:
            if str(reference).startswith("heartbeat run: "):
                run = str(reference).split(": ", 1)[1].strip()
                records_of.setdefault(run, []).append(record.id)
    if not records_of:
        return 0
    states_of: Dict[str, List[str]] = {}
    for row in db.query("SELECT task_id, result FROM raw_edits WHERE "
                        "action = 'wrote a state'"):
        run = str(row["task_id"] or "")
        if run not in records_of:
            continue
        found = db.query("SELECT id FROM states WHERE title = ? ORDER BY "
                         "created_at DESC LIMIT 1",
                         (str(row["result"] or "")[:200],))
        if found:
            states_of.setdefault(run, []).append(found[0]["id"])
    made = 0
    for run, states in states_of.items():
        for state in dict.fromkeys(states):
            for record in records_of[run]:
                db.execute("INSERT OR IGNORE INTO nature_links (state_id, "
                           "record_id, run_id, created_at) VALUES (?, ?, ?, "
                           "datetime('now'))", (state, record, f"{run} (by run)"))
                made += 1
    return made

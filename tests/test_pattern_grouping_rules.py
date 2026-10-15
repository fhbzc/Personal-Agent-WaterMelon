"""The patterns and principles are kept by appending, split by meaning,
and worded again on what changed.

A nature joins the pattern it is nearest, and a pattern the principle it
is placed under - by meaning only. A pattern or principle that took
something is cut again, as the first build cut, and split when it has
become two. A group is worded again once 30% of it is not what its words
were written from, gained or lost - and not for a few appended."""

from __future__ import annotations

import contextlib
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from personalized_agent.data_manage.library import USER_NATURE, Record
from personalized_agent.data_manage.library.store import DataLibrary
from personalized_agent.data_manage.storage.database import Database
from personalized_agent.heartbeat.experience.patterns import (
    PATTERN_CUT, PATTERN_LEAST, PatternBuilder, changed_since_named, parts,
    wants_words)
from personalized_agent.heartbeat.experience.store import ExperienceStore


def _at(degrees: float, third: float = 0.0):
    """A unit vector in the plane of the first two axes, `degrees` from the
    first - so how far apart two are is read straight off their angles."""
    angle = math.radians(degrees)
    vector = [math.cos(angle), math.sin(angle), third]
    size = math.sqrt(sum(one * one for one in vector))
    return [one / size for one in vector]


class _Embedder:
    """Every record's vector is set by its title."""

    def __init__(self, by_title):
        self.by_title = by_title

    def available(self):
        return True

    def loaded(self):
        return contextlib.nullcontext(self)

    def read_states(self, texts):
        return {one.id: self.by_title[one.title] for one in texts
                if one.title in self.by_title}

    def pairwise(self, order, vectors, floor=0.0):
        return [(i, j, sum(a * b for a, b in zip(vectors[order[i]],
                                                 vectors[order[j]])))
                for i in range(len(order)) for j in range(i + 1, len(order))]


class _Models:
    """A model whose answer depends on what it is asked."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def invoke(self, request, role=""):
        self.asked.append(request)
        text = self.answer(request)

        class Reply:
            pass

        reply = Reply()
        reply.text = json.dumps(text)
        reply.model, reply.provider, reply.usage = "test-model", "test", None
        reply.tool_calls = []
        return reply


def _named(request):
    if "PRINCIPLES BEHIND THEM" in request.system:
        return {"principles": []}
    if "PUT THESE NEW PATTERNS" in request.system:
        return {}
    return {"statement": "Worded again.", "detail": "d"}


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pattern-rules-"))
        self.db = Database(self.dir / "agent.db")
        self.library = DataLibrary(self.dir / "data")
        self.library.ensure()
        self.store = ExperienceStore(self.db)
        self.vectors = {}

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def builder(self, answer=_named):
        made = PatternBuilder(self.db, self.library, _Embedder(self.vectors),
                              _Models(answer))
        return made

    def nature(self, title, vector):
        record = self.library.write(Record(
            folder=USER_NATURE, title=title, summary=title, source="experience",
            metadata={"nature_kind": "work"}))
        self.vectors[title] = vector
        return record.id

    def pattern(self, members, statement="", **more):
        return self.store.save_pattern(dict(
            {"layer": "pattern", "members": list(members),
             "support": len(members), "statement": statement, "detail": "",
             "nature_kind": "work",
             "written_by": {"named_size": len(members),
                            "summarised": list(members)} if statement else {}},
            **more))

    def principle(self, members, statement="A rule."):
        return self.store.save_pattern(
            {"layer": "principle", "members": list(members),
             "support": len(members), "statement": statement, "detail": "",
             "nature_kind": "work",
             "written_by": {"named_size": len(members),
                            "summarised": list(members)}})



class TheMeaningPartsTests(_Base):

    def test_two_clear_groups_are_two_parts_and_nothing_is_dropped(self) -> None:
        vectors = {"a1": _at(0), "a2": _at(2), "a3": _at(4), "a4": _at(1),
                   "b1": _at(80), "b2": _at(82), "b3": _at(84),
                   # Far from both: belongs to no part, and goes with the
                   # one it is nearest.
                   "odd": _at(50, 1.5)}
        found = parts(list(vectors), vectors, _Embedder({}), PATTERN_CUT,
                      PATTERN_LEAST)
        self.assertEqual([sorted(one) for one in found],
                         [["a1", "a2", "a3", "a4"], ["b1", "b2", "b3", "odd"]])

    def test_one_group_or_a_part_too_small_is_not_a_split(self) -> None:
        close = {f"a{n}": _at(n * 5) for n in range(6)}
        self.assertEqual(parts(list(close), close, _Embedder({}), PATTERN_CUT,
                               PATTERN_LEAST), [])
        # Two far off are its odd cases, not a second pattern.
        close.update({"b1": _at(85), "b2": _at(87)})
        self.assertEqual(parts(list(close), close, _Embedder({}), PATTERN_CUT,
                               PATTERN_LEAST), [])


class APatternThatTookNaturesIsSplitByMeaningTests(_Base):
    """S: a pattern cut again, as the first build cut, when it took something."""

    def _drifted(self):
        # Three natures about one thing and three about another, let in one
        # at a time as its centre moved: worded from all six.
        first = [self.nature(f"first {n}", _at(n)) for n in range(3)]
        second = [self.nature(f"second {n}", _at(75 + n)) for n in range(3)]
        pattern = self.pattern(first + second, "Both.")
        self.principle([pattern["id"]])
        return first, second, pattern

    def test_it_falls_into_two_and_the_larger_part_keeps_it(self) -> None:
        first, second, pattern = self._drifted()
        newcomer = self.nature("second again", _at(76))
        builder = self.builder()
        said = builder.build(budget=10)
        self.assertEqual(said["appended"], 1, said)
        self.assertEqual(said.get("split"), 1, said)
        rows = {p["id"]: p for p in self.store.patterns("pattern")}
        kept = rows[pattern["id"]]
        self.assertEqual(set(kept["members"]), set(second + [newcomer]))
        [new] = [p for p in rows.values() if p["id"] != pattern["id"]]
        self.assertEqual(set(new["members"]), set(first))
        self.assertEqual(new["written_by"]["split_from"], pattern["id"])
        # Its principle kept; the part that left is put under one.
        self.assertTrue(kept["parent"])
        asked = [r.system.split("\n", 1)[0] for r in builder.models.asked]
        self.assertTrue(any("PUT THESE NEW PATTERNS" in one for one in asked))
        self.assertTrue(new["parent"])
        # Three of six gone and one come: worded again, from what it holds.
        self.assertEqual(kept["statement"], "Worded again.")
        self.assertEqual(set(kept["written_by"]["summarised"]),
                         set(second + [newcomer]))

    def test_one_that_took_nothing_is_not_looked_at(self) -> None:
        self._drifted()
        builder = self.builder()
        said = builder.build(budget=10)
        self.assertNotIn("split", said)
        self.assertEqual(len(self.store.patterns("pattern")), 1)
        self.assertEqual(builder.models.asked, [])

    def test_a_few_appended_neither_split_nor_reword_it(self) -> None:
        held = [self.nature(f"held {n}", _at(n)) for n in range(10)]
        pattern = self.pattern(held, "Ten alike.")
        self.principle([pattern["id"]])
        for n in range(2):
            self.nature(f"more {n}", _at(3 + n))
        builder = self.builder()
        said = builder.build(budget=10)
        self.assertEqual(said["appended"], 2, said)
        self.assertNotIn("split", said)
        [row] = self.store.patterns("pattern")
        self.assertEqual(len(row["members"]), 12)
        self.assertEqual(row["statement"], "Ten alike.")
        self.assertEqual(builder.models.asked, [])


class APrincipleThatTookPatternsIsSplitByMeaningTests(_Base):
    """S: a principle's patterns cut again when it took a new one."""

    def _setup(self, newcomer_degrees):
        def pattern_at(name, degrees):
            members = [self.nature(f"{name} {n}", _at(degrees, n * 0.01))
                       for n in range(3)]
            return self.pattern(members, f"{name}.")

        near = [pattern_at(f"near {n}", n * 3) for n in range(3)]
        far = pattern_at("far", 85)
        principle = self.principle([p["id"] for p in near + [far]])
        newcomer = pattern_at("newcomer", newcomer_degrees)
        return near, far, principle, newcomer

    def _answer(self, principle, newcomer, then=None):
        def answer(request):
            if "PUT THESE NEW PATTERNS" in request.system:
                content = request.messages[0]["content"]
                if newcomer["id"] in content and then is None:
                    return {"placed": {newcomer["id"]: principle["id"]},
                            "new_principles": []}
                return then(content) if then else {}
            return _named(request)
        return answer

    def test_a_principle_made_two_gives_the_smaller_part_back(self) -> None:
        near, far, principle, newcomer = self._setup(87)
        builder = self.builder(self._answer(principle, newcomer))
        said = builder.build(budget=10)
        self.assertEqual(said.get("principles_split"), 1, said)
        self.assertEqual(said.get("unfinished"), "principles still to place")
        row = next(p for p in self.store.patterns("principle")
                   if p["id"] == principle["id"])
        self.assertEqual(set(row["members"]), {p["id"] for p in near})
        leaving = {far["id"], newcomer["id"]}
        self.assertEqual(set(row["written_by"]["split_off"]), leaving)
        parents = {p["id"]: p["parent"] for p in self.store.patterns("pattern")}
        self.assertEqual({parents[one] for one in leaving}, {""})
        # One of four gone: under a third, its words stand.
        self.assertEqual(row["statement"], "A rule.")

        # Next build: put back under it they are not; a new one is proposed.
        def then(content):
            return {"placed": {one: principle["id"] for one in leaving},
                    "new_principles": [{"statement": "Another rule.",
                                        "detail": "d",
                                        "patterns": sorted(leaving)}]}

        again = self.builder(self._answer(principle, newcomer, then))
        said = again.build(budget=10)
        self.assertNotIn("unfinished", said)
        rows = {p["statement"]: p for p in self.store.patterns("principle")}
        self.assertEqual(set(rows["Another rule."]["members"]), leaving)
        self.assertEqual(set(rows["A rule."]["members"]),
                         {p["id"] for p in near})

    def test_one_alike_and_one_odd_pattern_stay_one_principle(self) -> None:
        near, far, principle, newcomer = self._setup(5)
        builder = self.builder(self._answer(principle, newcomer))
        said = builder.build(budget=10)
        self.assertNotIn("principles_split", said)
        row = next(p for p in self.store.patterns("principle")
                   if p["id"] == principle["id"])
        self.assertEqual(len(row["members"]), 5)


class APatternIsWordedAgainOnChurnTests(_Base):
    """A3: the members it was worded from, and 0.3 of them changed."""

    def test_churn_counts_gains_and_losses(self) -> None:
        was = [f"n{i}" for i in range(10)]
        named = {"statement": "s", "written_by": {"summarised": was}}
        self.assertFalse(wants_words(dict(named, members=was)))
        self.assertFalse(wants_words(dict(named, members=was[:8])))
        self.assertTrue(wants_words(dict(named, members=was[:7])))
        self.assertTrue(wants_words(dict(named, members=was + ["a", "b", "c"])))
        # Two gone, two come: the same size, and 40% not what was worded.
        self.assertAlmostEqual(changed_since_named(
            dict(named, members=was[2:] + ["a", "b"])), 0.4)
        self.assertTrue(wants_words({"statement": "", "members": was}))

    def test_without_the_members_the_size_decides(self) -> None:
        named = {"statement": "s", "written_by": {"named_size": 10}}
        self.assertTrue(wants_words(dict(named, members=list("abcdefg"))))
        self.assertFalse(wants_words(dict(named, members=list("abcdefgh"))))
        self.assertFalse(wants_words({"statement": "s", "written_by": {},
                                      "members": ["a"]}))

    def test_a_few_appended_to_a_small_pattern_do_not_reword_it(self) -> None:
        # A third more, but one nature: fewer than a pattern rests on.
        named = {"statement": "s", "written_by": {"summarised": ["a", "b", "c"]}}
        self.assertFalse(wants_words(dict(named, members=["a", "b", "c", "d"])))
        self.assertFalse(wants_words(dict(named, members=list("abcde"))))
        self.assertTrue(wants_words(dict(named, members=list("abcdef"))))
        # A principle's least is two patterns.
        self.assertTrue(wants_words(dict(named, members=list("abcde")), 2))

    def test_a_pattern_that_lost_members_is_worded_again(self) -> None:
        kept = [self.nature(f"kept {n}", _at(n)) for n in range(4)]
        # Three of its seven natures are gone from the library.
        losing = self.pattern(kept + ["gone-1", "gone-2", "gone-3"], "Old words.")
        steady = [self.nature(f"steady {n}", _at(90 + n)) for n in range(4)]
        same = self.pattern(steady, "Steady words.")
        self.principle([losing["id"], same["id"]])
        builder = self.builder()
        said = builder.build(budget=10)
        rows = {p["id"]: p for p in self.store.patterns("pattern")}
        self.assertEqual(rows[losing["id"]]["statement"], "Worded again.")
        self.assertEqual(rows[losing["id"]]["written_by"]["summarised"], kept)
        self.assertEqual(rows[same["id"]]["statement"], "Steady words.")
        self.assertEqual(said["named"], 1)
        # And once worded, it is not asked about again.
        again = self.builder()
        again.build(budget=10)
        self.assertEqual(again.models.asked, [])

    def test_old_words_stand_until_new_ones_are_written(self) -> None:
        kept = [self.nature(f"kept {n}", _at(n)) for n in range(4)]
        losing = self.pattern(kept + ["gone-1", "gone-2", "gone-3"], "Old words.")
        self.principle([losing["id"]])
        said = self.builder().build(budget=0)
        [row] = self.store.patterns("pattern")
        self.assertEqual(row["statement"], "Old words.")
        self.assertEqual(said.get("unfinished"), "patterns still to name")
        [principle] = self.store.patterns("principle")
        self.assertEqual(principle["members"], [losing["id"]])


class APrincipleIsWordedAgainOnChurnTests(_Base):
    """B3: losses count, every build - not only growth by half."""

    def _setup(self, gone):
        patterns = []
        for n in range(4):
            members = [self.nature(f"p{n} {k}", _at(n * 20 + k)) for k in range(3)]
            patterns.append(self.pattern(members, f"Pattern {n}."))
        principle = self.principle([p["id"] for p in patterns])
        for pattern in patterns[:gone]:
            # Every one of its natures gone: the pattern leaves its principle.
            self.store.save_pattern(dict(pattern, members=["gone"],
                                         written_by={"named_size": 1,
                                                     "summarised": ["gone"]}))
        return principle

    def test_one_of_four_gone_keeps_its_words(self) -> None:
        principle = self._setup(1)
        builder = self.builder()
        builder.build(budget=10)
        self.assertFalse(any("SAY THE PRINCIPLE" in r.system
                             for r in builder.models.asked))
        [row] = [p for p in self.store.patterns("principle")
                 if p["id"] == principle["id"]]
        self.assertEqual(row["statement"], "A rule.")

    def test_two_of_four_gone_is_worded_again(self) -> None:
        principle = self._setup(2)
        builder = self.builder()
        builder.build(budget=10)
        self.assertTrue(any("SAY THE PRINCIPLE" in r.system
                            for r in builder.models.asked))
        [row] = [p for p in self.store.patterns("principle")
                 if p["id"] == principle["id"]]
        self.assertEqual(row["statement"], "Worded again.")
        self.assertEqual(len(row["written_by"]["summarised"]), 2)
        # Its words before, kept with why they changed.
        [was] = row["history"]
        self.assertEqual(was["statement"], "A rule.")
        self.assertEqual(was["members"], 4)
        self.assertEqual(was["why"], "50% of members changed")


class AGroupKeepsItsHistoryTests(_Base):
    """Each rewording and each split leaves the version before on the group,
    and every later rewrite of it carries that along."""

    def _row(self, pattern_id):
        return next(p for p in self.store.patterns() if p["id"] == pattern_id)

    def test_a_rewording_is_remembered_and_survives_what_follows(self) -> None:
        kept = [self.nature(f"kept {n}", _at(n)) for n in range(4)]
        losing = self.pattern(kept + ["gone-1", "gone-2", "gone-3"],
                              "Old words.", detail="How it showed.")
        self.principle([losing["id"]])
        self.builder().build(budget=10)
        [was] = self._row(losing["id"])["history"]
        self.assertEqual({k: was[k] for k in ("statement", "detail", "members",
                                              "why")},
                         {"statement": "Old words.", "detail": "How it showed.",
                          "members": 7, "why": "43% of members changed"})
        self.assertTrue(was["at"])
        # A later build that appends without rewording keeps it ...
        self.nature("one more", _at(1))
        self.builder().build(budget=10)
        row = self._row(losing["id"])
        self.assertEqual(len(row["members"]), 5)
        self.assertEqual(len(row["history"]), 1)
        # ... and so does the next rewording, adding its own.
        for n in range(3):
            self.nature(f"three more {n}", _at(2 + n))
        self.builder().build(budget=10)
        row = self._row(losing["id"])
        self.assertEqual([one["statement"] for one in row["history"]],
                         ["Old words.", "Worded again."])
        self.assertEqual(row["written_by"]["history"], row["history"])

    def test_a_split_is_remembered_on_both_sides(self) -> None:
        first = [self.nature(f"first {n}", _at(n)) for n in range(3)]
        second = [self.nature(f"second {n}", _at(75 + n)) for n in range(3)]
        pattern = self.pattern(first + second, "Both.")
        self.principle([pattern["id"]])
        self.nature("second again", _at(76))
        self.builder().build(budget=10)
        row = self._row(pattern["id"])
        [new] = [p for p in self.store.patterns("pattern")
                 if p["id"] != pattern["id"]]
        split, reworded = row["history"]
        self.assertEqual(split["why"], f"split: 3 members became {new['id']}")
        self.assertEqual(split["statement"], "Both.")
        self.assertEqual(reworded["statement"], "Both.")
        self.assertEqual(reworded["why"], "67% of members changed")
        # The new one says where it came from - and still does once named.
        [came] = new["history"]
        self.assertEqual(came["split_from"], pattern["id"])
        self.assertEqual(came["members"], 3)
        self.assertTrue(came["at"])
        self.assertEqual(new["statement"], "Worded again.")
        self.assertEqual(new["written_by"]["split_from"], pattern["id"])

    def test_a_principle_split_is_remembered(self) -> None:
        def pattern_at(name, degrees):
            members = [self.nature(f"{name} {n}", _at(degrees, n * 0.01))
                       for n in range(3)]
            return self.pattern(members, f"{name}.")

        near = [pattern_at(f"near {n}", n * 3) for n in range(3)]
        far = pattern_at("far", 85)
        principle = self.principle([p["id"] for p in near + [far]])
        newcomer = pattern_at("newcomer", 87)

        def answer(request):
            if "PUT THESE NEW PATTERNS" in request.system:
                return {"placed": {newcomer["id"]: principle["id"]}}
            return _named(request)

        self.builder(answer).build(budget=10)
        [split] = self._row(principle["id"])["history"]
        self.assertEqual(split["statement"], "A rule.")
        self.assertTrue(split["why"].startswith("split: 2 patterns left it"))
        self.assertIn(far["id"], split["why"])


if __name__ == "__main__":
    unittest.main()

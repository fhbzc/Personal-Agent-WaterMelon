"""THE LESSONS KEEP THEMSELVES (heartbeat/lessons/engine.py; the user, 2026-11-07: keeping the
rules is the system's job, not hand edits). A new case is weighed against the lessons pointing
the other way as it is linked; two lessons pointing opposite ways are narrowed, merged or one
retired as the model says; a candidate retires only when contradicted more than supported, never
for being old; a case no lesson explains is taken up again once; lessons worded the same merge,
their evidence joined; a correction blames only the lesson the model names; the user's own
lessons are never rewritten or retired; an answer cut off or unreadable changes nothing, is
recorded as failed, and is not asked again. Everything here is made up."""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timedelta, timezone

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.common.events.models import new_id
from personalized_agent.models.base import ModelResponse, ModelUsage

KIND = "notifications"
CUT = "max_tokens"

#: What each of the engine's questions says, to know which is asked.
FEATURES = "give each one the features"
JUDGE = "judge:"
INDUCE = "Is there ONE rule"
BLAME = "CAUSED the part the user corrected"
RETAKE = "no lesson explains yet"
DUPLICATES = "worded much alike"
OPPOSITES = "OPPOSITE ways"


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class _Fake:
    """A model answering each question by its handler: a dict, a text, or (text, stop_reason).
    A question with no handler gets a whole answer that says nothing."""

    def __init__(self, **handlers):
        self.handlers, self.asked = dict(handlers), []

    def invoke(self, request, role=""):
        prompt = request.messages[0]["content"]
        self.asked.append(prompt)
        for marker, answer in self.handlers.items():
            if globals()[marker] in prompt:
                said = answer(prompt) if callable(answer) else answer
                break
        else:
            said = {"features": {}, "judgements": [], "rule": None, "records": [],
                    "pairs": [], "caused": []}
        text, stop = said if isinstance(said, tuple) else (said, "end_turn")
        if not isinstance(text, str):
            text = json.dumps(text)
        return ModelResponse(text=text, stop_reason=stop, usage=ModelUsage(1, 1),
                             model="test-model", provider="test")

    def count(self, marker: str) -> int:
        return sum(1 for one in self.asked if globals()[marker] in one)


def _pairs_of(prompt):
    """Each pair the prompt shows: its id and its two lessons' ids, in order."""
    out = []
    for block in re.split(r"(?=PAIR \[p\d+\])", prompt)[1:]:
        pid = re.match(r"PAIR \[(p\d+)\]", block).group(1)
        out.append((pid, re.findall(r"\n  [AB] \[(les_[0-9a-f]+)\]", block)))
    return out


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.db = self.app.db
        self.engine = self.app.lessons
        self.model = _Fake()
        self.engine.models = self.model

    def _rule(self, statement, keywords, state="candidate", cases=1, source="wechat"):
        rule = self.engine._new_rule(KIND, {"statement": statement, "conditions": {
            "source": source, "keywords": keywords}}, "made up for a test", state=state)
        for _ in range(cases):
            self._case(rule, "supports")
        self.engine.score(KIND)
        return rule

    def _case(self, rule, relation, days=1.0, summary="The user deleted a made-up reminder"):
        at = _ago(days)
        evidence = self.engine.record(KIND, new_id("calact"), summary, action="delete", at=at)
        self.db.execute("UPDATE lesson_evidence SET processed_at = ?, features = '{}' "
                        "WHERE id = ?", (at, evidence))
        self.db.execute("INSERT INTO lesson_links (evidence_id, rule_id, relation, why, at) "
                        "VALUES (?, ?, ?, 'made up', ?)", (evidence, rule, relation, at))
        return evidence

    def _unlinked(self, summary, days=10.0, keywords=("garden",)):
        at = _ago(days)
        evidence = self.engine.record(KIND, new_id("calact"), summary, action="delete", at=at)
        self.db.execute("UPDATE lesson_evidence SET processed_at = ?, features = ? WHERE id = ?",
                        (at, json.dumps({"source": "wechat", "keywords": list(keywords),
                                         "action": "delete"}), evidence))
        return evidence

    def _links(self, rule):
        return {row["evidence_id"]: row["relation"] for row in self.db.query(
            "SELECT evidence_id, relation FROM lesson_links WHERE rule_id = ?", (rule,))}

    def _rule_now(self, rule):
        return self.engine.rule(rule)


class WeighedBothWaysAtLinkingTests(_Base):

    def test_a_new_case_contradicts_the_close_lesson_pointing_the_other_way(self) -> None:
        remind = self._rule("Remind the user of a meeting on the same day", ["meeting"],
                            cases=0)
        never = self._rule("Do not remind the user of a meeting on the same day",
                           ["meeting", "same day"], source="email", cases=0)

        def judge(prompt):
            out = []
            for block in re.split(r"(?=NEW RECORD \[evi_)", prompt)[1:]:
                record = re.search(r"\[(evi_[0-9a-f]+)\]", block).group(1)
                for lesson, said in re.findall(r"LESSON \[(les_[0-9a-f]+)\] \(([^)]*)\)", block):
                    out.append({"record": record, "lesson": lesson, "why": "made up",
                                "relation": "contradicts" if said.startswith("close to")
                                else "supports"})
            return {"judgements": out}
        self.model.handlers.update(
            FEATURES=lambda prompt: {"features": {one: {"source": "wechat",
                                                        "keywords": ["garden"]}
                                                  for one in re.findall(r"\[(evi_[0-9a-f]+)\]",
                                                                        prompt)}},
            JUDGE=judge)
        case = self.engine.record(KIND, "calact_1", "The user kept a made-up reminder of a "
                                  "garden club meeting set for today", action="got_it")
        self.engine.learn(KIND)
        [asked] = [one for one in self.model.asked if JUDGE in one]
        self.assertIn(f"LESSON [{never}] (close to lesson {remind})", asked,
                      "in the same call that links it")
        self.assertEqual(self._links(remind), {case: "supports"})
        self.assertEqual(self._links(never), {case: "contradicts"})
        # Contradicted more than supported: the candidate retires, saying why.
        said = self._rule_now(never)
        self.assertEqual(said["state"], "retired")
        self.assertIn("contradicted more than supported", said["history"][-1]["why"])
        self.assertEqual(self._rule_now(remind)["state"], "candidate")


class OppositesTests(_Base):

    def _pair(self):
        a = self._rule("Remind the user of meetings arranged in chat", ["meeting", "chat"],
                       state="active", cases=2)
        b = self._rule("Do not remind the user of meetings arranged in chat",
                       ["meeting", "chat"], cases=1)
        return a, b

    def _decide(self, **said):
        def opposites(prompt):
            return {"pairs": [dict(said, pair=pid) for pid, _ids in _pairs_of(prompt)]}
        self.model.handlers.update(
            DUPLICATES=lambda prompt: {"pairs": [{"pair": pid, "same": False, "why": "opposite"}
                                                 for pid, _ids in _pairs_of(prompt)]},
            OPPOSITES=opposites)

    def test_narrowed_as_the_model_says(self) -> None:
        a, b = self._pair()
        self._decide(decision="narrow", lesson=b, why="only when the time is not set",
                     statement="Do not remind the user of chat meetings whose time is not set",
                     conditions={"source": "wechat", "other": "time not set"})
        weight = self._rule_now(b)["support"]
        report = self.engine.upkeep(KIND)
        self.assertEqual(report["opposed"], 1)
        said = self._rule_now(b)
        self.assertEqual(said["statement"],
                         "Do not remind the user of chat meetings whose time is not set")
        self.assertEqual(said["conditions"]["other"], "time not set")
        self.assertIn(f"narrowed: against {a}", said["history"][-1]["why"])
        self.assertIn("only when the time is not set", said["history"][-1]["why"])
        self.assertEqual(self._rule_now(a)["statement"],
                         "Remind the user of meetings arranged in chat")
        # Only the user's actions move how sure a lesson is: upkeep changes no weight.
        self.assertAlmostEqual(said["support"], weight, places=3)
        self.assertEqual(said["contradict"], 0)
        self.assertEqual(len(self._links(b)), 1)

    def test_merged_as_the_model_says(self) -> None:
        a, b = self._pair()
        self._decide(decision="merge", statement="Remind the user of chat meetings once a "
                     "time is agreed", why="one lesson")
        self.engine.upkeep(KIND)
        self.assertEqual(self._rule_now(b)["state"], "retired")
        self.assertEqual(self._rule_now(b)["history"][-1]["merged_into"], a)
        kept = self._rule_now(a)
        self.assertEqual(kept["statement"], "Remind the user of chat meetings once a time "
                                            "is agreed")
        self.assertEqual(len(self._links(a)), 3, "the evidence joined")

    def test_one_retired_as_the_model_says(self) -> None:
        a, b = self._pair()
        self._decide(decision="retire", lesson=a, why="the later cases say otherwise")
        self.engine.upkeep(KIND)
        said = self._rule_now(a)
        self.assertEqual(said["state"], "retired")
        self.assertIn(f"opposite to {b}", said["history"][-1]["why"])
        self.assertIn("the later cases say otherwise", said["history"][-1]["why"])
        self.assertEqual(self._rule_now(b)["state"], "candidate")

    def test_not_asked_again_once_answered(self) -> None:
        self._pair()
        self._decide(decision="none", why="different cases")
        self.engine.upkeep(KIND)
        self.engine.upkeep(KIND)
        self.assertEqual(self.model.count("OPPOSITES"), 1)
        self.assertEqual(self.model.count("DUPLICATES"), 1)


class CandidatesTests(_Base):

    def test_retired_only_when_contradicted_more_than_supported_never_for_being_old(self) -> None:
        against = self._rule("Ask before adding a made-up club event", ["club"], cases=1)
        self._case(against, "contradicts")
        self._case(against, "contradicts")
        old = self._rule("Leave made-up kite days alone", ["kite"], cases=0)
        self._case(old, "supports", days=400)
        even = self._rule("Keep made-up quiz nights", ["quiz"], cases=1)
        self._case(even, "contradicts")
        self.engine.learn(KIND)
        said = self._rule_now(against)
        self.assertEqual(said["state"], "retired")
        self.assertIn("contradicted more than supported", said["history"][-1]["why"])
        self.assertEqual(self._rule_now(old)["state"], "candidate", "long without evidence: stays")
        self.assertEqual(self._rule_now(even)["state"], "candidate", "not more against than for")


class RetakeTests(_Base):

    def test_unlinked_evidence_is_taken_up_once(self) -> None:
        rule = self._rule("Do not remind the user of made-up garden chores", ["garden"])
        fits = self._unlinked("The user deleted a made-up reminder to weed the garden")
        new = self._unlinked("The user deleted a made-up reminder to feed the fish",
                             keywords=("fish",))
        silent = self._unlinked("The user deleted a made-up reminder to call the plumber",
                                keywords=("plumber",))
        fresh = self._unlinked("The user deleted a made-up reminder to sweep", days=1)

        def retake(prompt):
            ids = re.findall(r"RECORD \[(evi_[0-9a-f]+)\]", prompt)
            out = []
            if fits in ids:
                out.append({"record": fits, "decision": "fits", "lesson": rule,
                            "relation": "supports", "why": "a garden chore"})
            if new in ids:
                out.append({"record": new, "decision": "new", "why": "pets",
                            "rule": {"statement": "Do not remind the user to feed the fish",
                                     "conditions": {"keywords": ["fish"]}}})
            return {"records": out}
        self.model.handlers["RETAKE"] = retake
        report = self.engine.upkeep(KIND)
        self.assertEqual((report["linked"], report["made"]), (1, 1))
        self.assertEqual(self._links(rule)[fits], "supports")
        made = [one for one in self.engine.rules(KIND) if "fish" in one["statement"]]
        self.assertEqual(len(made), 1)
        self.assertEqual(self._links(made[0]["id"]), {new: "supports"})
        self.assertEqual(made[0]["state"], "candidate")
        # The one the answer left out: recorded as failed, not asked again.
        self.assertTrue(any(silent in one["what"] for one in report["failed"]))
        features = json.loads(self.db.query_one(
            "SELECT features FROM lesson_evidence WHERE id = ?", (silent,))["features"])
        self.assertTrue(features["retaken"]["as"].startswith("failed"))
        asked = next(one for one in self.model.asked if RETAKE in one)
        self.assertNotIn(fresh, asked, "just read: the pool's grouping first")
        self.engine.upkeep(KIND)
        self.assertEqual(self.model.count("RETAKE"), 1, "once each")

    def test_the_round_takes_them_up(self) -> None:
        self._unlinked("The user deleted a made-up reminder to feed the fish", keywords=("fish",))
        self.model.handlers["RETAKE"] = lambda prompt: {"records": [
            {"record": one, "decision": "none", "why": "nothing general"}
            for one in re.findall(r"RECORD \[(evi_[0-9a-f]+)\]", prompt)]}
        said = self.engine.learn_due()
        self.assertEqual(said[KIND]["upkeep"]["retaken"], 1)
        self.engine.learn_due()
        self.assertEqual(self.model.count("RETAKE"), 1)


class DuplicatesTests(_Base):

    def test_merged_with_the_evidence_and_history_joined(self) -> None:
        a = self._rule("Write each version in the update log of the README", ["readme"],
                       state="active", cases=2)
        b = self._rule("Write each version in the README update log", ["readme"], cases=1)
        self.model.handlers["DUPLICATES"] = lambda prompt: {"pairs": [
            {"pair": pid, "same": True, "why": "word for word",
             "statement": "Add every version to the README's update log"}
            for pid, _ids in _pairs_of(prompt)]}
        report = self.engine.upkeep(KIND)
        self.assertEqual(report["merged"], 1)
        kept, gone = self._rule_now(a), self._rule_now(b)
        self.assertEqual(kept["statement"], "Add every version to the README's update log")
        self.assertEqual(kept["state"], "active")
        self.assertEqual(len(self._links(a)), 3, "the evidence joined")
        self.assertEqual(gone["state"], "retired")
        self.assertEqual(gone["history"][-1]["merged_into"], a)
        self.assertTrue(any(one.get("joined_from") == b for one in kept["history"]),
                        "the history joined")
        self.assertEqual(self.model.count("OPPOSITES"), 0, "nothing left to pair")


class BlameTests(_Base):

    def _used(self):
        a = self._rule("Remind the user of made-up choir practice", ["choir"], state="active",
                       cases=3)
        b = self._rule("Add made-up choir practice to the calendar at 7am", ["choir"],
                       state="active", cases=3)
        self.assertIsNotNone(self.engine.used(KIND, [a, b], "task_1", "added"))
        return a, b

    def _against(self, rule):
        return [one for one, relation in self._links(rule).items() if relation == "contradicts"]

    def test_only_the_lesson_that_caused_it_is_blamed(self) -> None:
        a, b = self._used()
        self.model.handlers["BLAME"] = lambda prompt: {"caused": [b], "why": "the hour was wrong"}
        self.assertEqual(self.engine.outcome(KIND, "task_1", "moved it to the evening"), 1)
        self.assertEqual((self._against(a), self._against(b)), ([], []), "not on the click")
        self.assertIn(KIND, self.engine.due())
        self.engine.learn(KIND)
        [asked] = [one for one in self.model.asked if BLAME in one]
        self.assertIn(a, asked)
        self.assertIn(b, asked)
        self.assertIn("moved it to the evening", asked)
        self.assertEqual(self._against(a), [])
        self.assertEqual(len(self._against(b)), 1)
        self.assertGreater(self._rule_now(b)["contradict"], 1.4)
        self.assertEqual(self._rule_now(a)["contradict"], 0)
        self.engine.learn(KIND)
        self.assertEqual(self.model.count("BLAME"), 1, "once")

    def test_no_whole_answer_records_nothing(self) -> None:
        for answer in ('{"caused": ["les_', "I cannot tell.", {"why": "no list"}):
            with self.subTest(answer=answer):
                a, b = self._used()
                self.model.handlers["BLAME"] = (answer, CUT) if answer.__class__ is str and \
                    answer.startswith("{") else answer
                before = self.model.count("BLAME")
                self.engine.outcome(KIND, "task_1", "deleted it")
                report = self.engine.learn(KIND)
                self.assertEqual(self.model.count("BLAME"), before + 1)
                self.assertEqual(len(report["blame"]["failed"]), 1)
                self.assertEqual((self._against(a), self._against(b)), ([], []),
                                 "never spread over every lesson used")
                self.engine.learn(KIND)
                self.assertEqual(self.model.count("BLAME"), before + 1, "not asked again")


class TheUsersOwnTests(_Base):

    def test_pinned_and_stated_lessons_are_never_rewritten_or_retired(self) -> None:
        pinned = self._rule("Remind the user of meetings arranged in chat", ["meeting", "chat"],
                            state="active", cases=1)
        self.engine.set_state(pinned, "pin")
        evidence = self.engine.record("general", "chat_1", "The user said so in the chat")
        stated = self.engine.rule_from_the_user(KIND, "Never remind the user of meetings "
                                                      "arranged in chat at night", evidence)
        other = self._rule("Do not remind the user of meetings arranged in chat",
                           ["meeting", "chat"], cases=1)
        users = {pinned, stated}

        def opposites(prompt):
            out = []
            for pid, ids in _pairs_of(prompt):
                mine = [one for one in ids if one in users]
                out.append({"pair": pid, "decision": "retire" if len(out) % 2 else "narrow",
                            "lesson": mine[0] if mine else ids[0], "why": "made up",
                            "statement": "a rewritten lesson"})
            return {"pairs": out}
        self.model.handlers.update(
            DUPLICATES=lambda prompt: {"pairs": [{"pair": pid, "same": False, "why": "no"}
                                                 for pid, _ids in _pairs_of(prompt)]},
            OPPOSITES=opposites)
        before = {one: self._rule_now(one) for one in users}
        self.engine.upkeep(KIND)
        self.assertEqual(self.model.count("OPPOSITES"), 1, "asked, and told to change them")
        for one in users:
            now = self._rule_now(one)
            self.assertEqual((now["state"], now["statement"]),
                             (before[one]["state"], before[one]["statement"]))
        for prompt in self.model.asked:
            for _pid, ids in _pairs_of(prompt):
                self.assertFalse(set(ids) <= users, "two of the user's own: never asked")
        self.assertEqual(self._rule_now(other)["state"], "candidate")


class CutOrUnreadableTests(_Base):

    def test_a_cut_batch_is_halved_and_failing_at_its_smallest_is_recorded(self) -> None:
        a = self._rule("Remind the user of made-up kite days", ["kite"], state="active")
        b = self._rule("Remind the user of made-up kite days by the sea", ["kite"])
        c = self._rule("Remind the user of made-up kite days in the park", ["kite"])
        before = {one: self._rule_now(one) for one in (a, b, c)}

        def duplicates(prompt):
            pairs = _pairs_of(prompt)
            if len(pairs) > 1:
                return '{"pairs": [{"pair": "p1", "same": tr', CUT
            return "Sorry, no."
        self.model.handlers.update(DUPLICATES=duplicates,
                                   OPPOSITES=lambda prompt: ('{"pairs": [', CUT))
        report = self.engine.upkeep(KIND)
        asked_duplicates = self.model.count("DUPLICATES")
        self.assertGreater(asked_duplicates, 1, "halved")
        self.assertEqual({one["what"].split(":")[0] for one in report["failed"]},
                         {"duplicates", "opposites"})
        self.assertTrue(all(one["why"] for one in report["failed"]))
        for one in (a, b, c):
            now = self._rule_now(one)
            self.assertEqual((now["state"], now["statement"], now["history"]),
                             (before[one]["state"], before[one]["statement"],
                              before[one]["history"]), "nothing changed")
        asked_opposites = self.model.count("OPPOSITES")
        self.config_cleared()
        self.engine.upkeep(KIND)
        self.assertEqual((self.model.count("DUPLICATES"), self.model.count("OPPOSITES")),
                         (asked_duplicates, asked_opposites), "never asked the same way again")

    def config_cleared(self):
        self.app.config.set(self.engine.UPKEPT.format(kind=KIND), "", internal=True)


if __name__ == "__main__":
    unittest.main()

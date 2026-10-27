"""THE LESSONS, LEARNED FROM EVIDENCE (heartbeat/lessons/engine.py): one
system for every kind of lesson. A lesson never comes from one action: it
forms once several edits point the same way, is given to the agent only
then, grows or narrows with what comes after - including what came of the
agent acting on it - and the user's say wins."""

from __future__ import annotations

from datetime import date, timedelta
import json
import re
import unittest

from support import TemporaryAgent

from personalized_agent.heartbeat.lessons.engine import (
    LessonEngine, PROMOTE_SUPPORT, SHORTLIST, relevance)
from personalized_agent.models.base import ModelResponse, ModelUsage



#: A day ago, whenever the tests run: evidence counts half as much every 60
#: days (lessons/engine.py), and a fixed date in a stage put these tests out
#: of date - two stages of 2026-10-01 fell just short of a lesson on
#: 2026-10-26.
RECENT = (date.today() - timedelta(days=1)).isoformat()

class _Model:
    """Answers each of the cycle's questions from what it is shown: the
    features of each record, a judgement for each pair, one rule for a
    group, a narrowing for a contradicted lesson."""

    def __init__(self):
        self.asked = []
        self.narrowed = False

    def invoke(self, request, role=""):
        prompt = request.messages[0]["content"]
        self.asked.append(prompt)
        if "give each one the features" in prompt:
            ids = re.findall(r"\[(evi_[0-9a-f]+)\]", prompt)
            return self._said({"features": {one: {
                "source": "wechat", "event_type": "meeting", "person": "",
                "keywords": ["meeting", "chat", "time"]} for one in ids}})
        if "judge:" in prompt:
            out = []
            for block in re.split(r"(?=NEW RECORD \[evi_)", prompt)[1:]:
                record = re.search(r"\[(evi_[0-9a-f]+)\]", block).group(1)
                for lesson in re.findall(r"LESSON \[((?:les|rec)_[0-9a-zA-Z]+)\]", block):
                    relation = "contradicts" if "added this event" in block.split("\n")[0] \
                        else "supports"
                    out.append({"record": record, "lesson": lesson,
                                "relation": relation, "why": "as it says"})
            return self._said({"judgements": out})
        if "Is there ONE rule" in prompt:
            ids = re.findall(r"\[(evi_[0-9a-f]+)\]", prompt)
            return self._said({"rule": {"statement": "Do not remind about meetings "
                                                     "arranged in chat until the time is set",
                                        "conditions": {"source": "wechat",
                                                       "event_type": "meeting",
                                                       "keywords": ["meeting", "chat"]}},
                               "explains": ids})
        if "This lesson is being contradicted" in prompt:
            self.narrowed = True
            return self._said({"decision": "narrow", "why": "only unconfirmed times",
                               "rules": [{"statement": "Do not remind about chat meetings "
                                                       "whose time is not confirmed",
                                          "conditions": {"source": "wechat",
                                                         "event_type": "meeting",
                                                         "other": "time not confirmed"}}]})
        return self._said({})

    @staticmethod
    def _said(value):
        return ModelResponse(text=json.dumps(value), usage=ModelUsage(10, 5))


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.model = _Model()
        self.engine = LessonEngine(self.app.db, self.model, self.app.config)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _deleted(self, n, learn=False):
        return self.engine.record("notifications", f"calact_{n}",
                                  f"The user deleted this event.\nTitle: talk {n}, "
                                  "time not set, arranged in WeChat", action="delete",
                                  learn=learn)


class TheCycleTests(_Base):

    def test_one_action_is_no_lesson_three_alike_are(self) -> None:
        self._deleted(1)
        self.engine.learn("notifications")
        self.assertEqual(self.engine.rules("notifications"), [], "one action: nothing yet")
        self.assertEqual(self.engine.text("notifications"), "")
        self._deleted(2)
        self._deleted(3)
        report = self.engine.learn("notifications")
        self.assertEqual(report["induced"], 1)
        [rule] = self.engine.rules("notifications", ("active",))
        self.assertGreaterEqual(rule["support"], PROMOTE_SUPPORT - 0.01)
        self.assertEqual(rule["conditions"]["source"], "wechat")
        # What the agent is given: the lesson, its id, how sure - no action.
        text = self.engine.text("notifications")
        self.assertIn(f"[{rule['id']}] Do not remind about meetings", text)
        self.assertIn("3 cases", text)
        self.assertNotIn("talk 1", text)

    def test_new_evidence_is_judged_against_the_lessons_that_bear_on_it(self) -> None:
        for n in range(3):
            self._deleted(n)
        self.engine.learn("notifications")
        self._deleted(9)
        self.engine.learn("notifications")
        judged = [one for one in self.model.asked if "judge:" in one][-1]
        self.assertIn("NEW RECORD", judged)
        self.assertIn("relevance", judged)
        [rule] = self.engine.rules("notifications", ("active",))
        self.assertEqual(self.engine.describe("notifications")["lessons"][0]["cases"], 4)
        self.assertIn("4 cases", self.engine.text("notifications"))
        self.assertEqual(self.engine.reading("notifications")["calact_9"][0]["rule"],
                         rule["id"])

    def test_contradicted_it_is_narrowed_not_thrown_away(self) -> None:
        for n in range(3):
            self._deleted(n)
        self.engine.learn("notifications")
        for n in range(6):
            self.engine.record("notifications", f"calact_add{n}",
                               "The user added this event.\nTitle: talk on the 20th, 7pm, "
                               "arranged in WeChat", action="add", learn=False)
        self.engine.learn("notifications")
        self.assertTrue(self.model.narrowed)
        [rule] = self.engine.rules("notifications", ("active", "candidate"))
        self.assertIn("not confirmed", rule["statement"])
        self.assertIn("narrowed", rule["history"][-1]["why"])

    def test_the_shortlist_is_ranked_and_at_most_twenty(self) -> None:
        rules = [{"id": f"les_{n}", "statement": f"rule {n}",
                  "conditions": {"source": "wechat" if n % 2 else "email",
                                 "keywords": ["meeting"] if n < 25 else ["tax"]}}
                 for n in range(40)]
        listed = self.engine.shortlist({"source": "wechat", "keywords": ["meeting"]}, rules)
        self.assertEqual(len(listed), SHORTLIST)
        scores = [score for score, _rule in listed]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(score > 0 for score in scores))
        self.assertGreater(relevance({"person": "Tan"}, {"person": "tan"}), 2.9)


class TheAgentActingOnALessonTests(_Base):

    def _lesson(self):
        for n in range(3):
            self._deleted(n)
        self.engine.learn("notifications")
        return self.engine.rules("notifications", ("active",))[0]

    def test_a_correction_counts_against_the_lessons_it_followed(self) -> None:
        rule = self._lesson()
        self.assertIsNotNone(self.engine.used("notifications", [rule["id"], "les_none"],
                                              "task_1", "added"))
        before = self.engine.rule(rule["id"])["confidence"]
        self.assertEqual(self.engine.outcome("notifications", "task_1", "deleted it"), 1)
        after = self.engine.rule(rule["id"])
        self.assertLess(after["confidence"], before)
        self.assertGreater(after["contradict"], 1.4)
        self.assertEqual(self.engine.outcome("notifications", "task_1", "again"), 0,
                         "once")

    def test_left_alone_it_is_not_evidence(self) -> None:
        # Only what the user does moves a lesson: the agent's decision left
        # alone is no longer waited on, and nothing more.
        rule = self._lesson()
        before = self.engine.rule(rule["id"])
        self.engine.used("notifications", [rule["id"]], "task_2", "added")
        self.app.db.execute("UPDATE lesson_uses SET at = '2020-01-01T00:00:00+00:00'")
        self.assertEqual(self.engine.settle_uses("notifications"), 1)
        self.engine.score("notifications")
        after = self.engine.rule(rule["id"])
        self.assertEqual((after["support"], after["confidence"]),
                         (before["support"], before["confidence"]))
        self.assertEqual(self.engine.due(), [], "nothing for the user to have done")

    def test_what_the_agents_did_is_taken_out_once(self) -> None:
        rule = self._lesson()
        self.engine._linked_evidence("notifications", rule["id"], "supports", 0.3,
                                     "What the agent did on this lesson (added) was left "
                                     "standing.", outcome_of="use_old")
        self.engine.score("notifications")
        self.assertEqual(self.engine.forget_the_agents_own(), 1)
        self.assertEqual(self.app.db.query_one(
            "SELECT COUNT(*) AS n FROM lesson_evidence WHERE summary LIKE 'What the agent%'")["n"], 0)


class TheUsersSayTests(_Base):

    def test_reject_pin_reword_and_take_back(self) -> None:
        for n in range(3):
            self._deleted(n)
        self.engine.learn("notifications")
        rule = self.engine.rules("notifications", ("active",))[0]
        self.engine.set_state(rule["id"], "edit", "Never remind me of chat meetings")
        self.assertEqual(self.engine.rule(rule["id"])["statement"],
                         "Never remind me of chat meetings")
        self.engine.set_state(rule["id"], "pin")
        self.assertIn("set by the user", self.engine.text("notifications"))
        self.engine.set_state(rule["id"], "reject")
        self.assertEqual(self.engine.text("notifications"), "")
        with self.assertRaises(ValueError):
            self.engine.set_state(rule["id"], "explode")
        # An action taken back takes its evidence with it.
        self.assertEqual(self.engine.forget_source("notifications", "calact_1"), 1)
        self.assertNotIn("calact_1", self.engine.reading("notifications"))


class TheAppLearnsFromEveryKindTests(unittest.TestCase):
    """Every kind of lesson through the one engine: a notification action
    and a change to a contact are evidence; the user's own profile is not."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.models = _Model()
        self.app.lessons.models = self.app.models

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_actions_become_evidence_and_the_agent_gets_only_lessons(self) -> None:
        from personalized_agent.common.events.models import ScheduledTask, utcnow
        task = ScheduledTask(title="Talk with Dai", description="maybe Thursday",
                             scheduled_at=utcnow())
        for _ in range(3):
            self.app.calendar_nature.record("delete", task, said="time not set yet")
        # Not learned on the click: with the state heartbeat's round.
        self.assertEqual(self.app.calendar_nature.text(), "")
        self.assertEqual(self.app.lessons.due(), ["notifications"])
        self.app.lessons.learn_due()
        text = self.app.calendar_nature.text()
        self.assertIn("[les_", text)
        self.assertNotIn("maybe Thursday", text)
        ada = self.app.connections.add("Ada", by="user", reason="my advisor")
        me = self.app.connections.me()
        self.app.connections.edit(me["id"], by="user", role="student")
        self.app.lessons.wait()
        sources = {row["source_id"] for row in self.app.db.query(
            "SELECT source_id FROM lesson_evidence WHERE kind = 'connections'")}
        self.assertEqual(len(sources), 1, "the change to Ada, not to the user's profile")
        self.assertIn("None learned yet", self.app.connections.lessons_text())
        del ada

    def test_the_old_lessons_go_and_the_actions_are_learned_from_again(self) -> None:
        self.app.db.execute("INSERT INTO calendar_nature (id, created_at, kind, content, "
                            "action_id) VALUES ('calnat_old', '2026-01-01', 'lesson', "
                            "'an old lesson', NULL)")
        self.app.db.execute("INSERT INTO calendar_actions (id, acted_at, action, task_id, "
                            "event) VALUES ('calact_old', '2026-01-01T00:00:00+00:00', "
                            "'delete', 't', '{\"task\": {\"title\": \"Old one\"}}')")
        self.app.config.set(self.app.LESSONS_FRESH, False, internal=True)
        self.assertTrue(self.app.lessons_fresh_start())
        self.assertEqual(self.app.db.query("SELECT * FROM calendar_nature WHERE kind = "
                                           "'lesson'"), [])
        evidence = self.app.db.query("SELECT * FROM lesson_evidence WHERE source_id = "
                                     "'calact_old'")
        self.assertEqual(len(evidence), 1)
        self.assertIn("Old one", evidence[0]["summary"])
        self.assertFalse(self.app.lessons_fresh_start(), "once")


class OneAlgorithmForEveryKindTests(unittest.TestCase):
    """The natures of the work are kept as the lessons are: their stages the
    evidence; a new stage supports or contradicts them; how sure, in force
    or forming, written back onto them; retired ones left out. Every kind is
    learned with the state heartbeat's round when something is new, and
    each kind of lesson grouped again when its lessons changed."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.model = _Model()
        self.app.lessons.models = self.model
        from personalized_agent.heartbeat.experience.store import ExperienceStore
        self.store = ExperienceStore(self.app.db)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _nature(self, title, stages):
        from personalized_agent.data_manage.library import USER_NATURE, Record
        record = self.app.library.write(Record(
            folder=USER_NATURE, title=title, summary="checks meeting chat time",
            source="experience", metadata={"nature_kind": "work"}))
        for summary in stages:
            stage = self.store.add_stage({"task_id": "t", "kind": "evaluation",
                                          "summary": summary, "states": [],
                                          "began": RECENT, "ended": RECENT})
            self.store.link_stage_nature(stage["id"], record.id)
        return record

    def _record(self, record_id):
        from personalized_agent.heartbeat.experience.patterns import folder_records
        return next(one for one in folder_records(self.app.library, "user_nature")
                    if one.id == record_id)

    def test_a_nature_is_kept_as_a_lesson_is(self) -> None:
        two = self._nature("Confirms the meeting time in chat", ["stage one", "stage two"])
        one = self._nature("Likes short headings", ["stage three"])
        self.assertIn("work", self.app.lessons.due())
        report = self.app.lessons.learn("work")
        self.assertEqual(report["synced"]["natures"], 2)
        self.assertEqual(report["synced"]["links"], 3)
        # Two stages: in force. One: still forming - formed, not yet borne out.
        self.assertEqual(self.app.lessons.rule(two.id)["state"], "active")
        self.assertEqual(self.app.lessons.rule(one.id)["state"], "candidate")
        meta = self._record(two.id).metadata
        self.assertEqual(meta["lesson_state"], "active")
        self.assertGreaterEqual(float(meta["confidence"]), 0.7)
        self.assertEqual(self._record(one.id).metadata["lesson_state"], "candidate")
        self.assertNotIn("work", self.app.lessons.due(), "nothing new since")

    def test_a_new_stage_contradicting_it_narrows_it(self) -> None:
        nature = self._nature("Confirms the meeting time in chat", ["stage one", "stage two"])
        self.app.lessons.learn("work")
        # Stages written since: matched against the natures they bear on.
        for n in range(6):
            self.store.add_stage({"task_id": "t", "kind": "evaluation",
                                  "summary": f"added this event: a chat meeting {n}",
                                  "states": [], "began": RECENT, "ended": RECENT})
        self.app.config.set("lessons_work_since", "2000-01-01T00:00:00+00:00", internal=True)
        self.app.lessons.learn("work")
        rule = self.app.lessons.rule(nature.id)
        self.assertTrue(self.model.narrowed)
        self.assertIn("not confirmed", rule["statement"])
        self.assertIn("not confirmed", self._record(nature.id).title, "written back")

    def test_a_retired_nature_is_left_out_of_what_is_given(self) -> None:
        from personalized_agent.heartbeat.experience.patterns import nature_records
        nature = self._nature("Confirms the meeting time in chat", ["stage one"])
        self.app.lessons.learn("work")
        self.app.lessons.set_state(nature.id, "reject")
        self.app.lessons.natures.write_back(self.app.lessons)
        self.assertNotIn(nature.id, [one.id for one in nature_records(self.app.library)])

    def test_the_work_runs_never_reach_the_natures(self) -> None:
        # The reviewer's criteria and its reopenings are the agents' doing:
        # the work runs are given no engine to tell.
        self.assertIsNone(getattr(self.app.work, "lessons", None))

    def test_each_kind_of_lesson_is_grouped_again_only_when_it_changed(self) -> None:
        grouped = []
        self.app.lessons.grouper = lambda kind: grouped.append(kind) or {"patterns": 0}
        for n in range(3):
            self.app.lessons.record("notifications", f"calact_{n}",
                                    f"The user deleted this event.\nTitle: talk {n}",
                                    action="delete")
        self.app.lessons.learn_due()
        self.assertIn("notifications", grouped)
        grouped.clear()
        self.app.lessons.learn_due()
        self.assertNotIn("notifications", grouped, "nothing changed")

    def test_each_kind_says_where_it_stands_for_its_bar(self) -> None:
        self.app.lessons.record("notifications", "calact_y", "The user deleted this event.",
                                action="delete")
        said = self.app.lessons.standing()
        self.assertEqual(set(said), {"notifications", "connections", "work"})
        self.assertTrue(said["notifications"]["due"])
        self.assertEqual((said["notifications"]["waiting"], said["notifications"]["share"]),
                         (1, 0))
        self.assertEqual(said["connections"]["share"], 100, "nothing new: up to date")
        self.app.lessons.learn("notifications")
        said = self.app.lessons.standing()["notifications"]
        self.assertFalse(said["running"])
        self.assertTrue(said["finished_at"])
        self.assertEqual(said["share"], 100)
        # And the round's picture carries them, beside what the user needs to know.
        work = self.app.state_heartbeat.the_picture().get("work") or {}
        self.assertIn("lessons", work)

    def test_the_state_heartbeat_learns_them_when_due(self) -> None:
        self.assertIs(self.app.state_heartbeat.lessons, self.app.lessons)
        self.app.lessons.record("notifications", "calact_x", "The user deleted this event.",
                                action="delete")
        self.assertTrue(self.app.state_heartbeat._learn_lessons())
        import time
        for _ in range(100):
            if not getattr(self.app.state_heartbeat, "_lessons_running", False):
                break
            time.sleep(0.05)
        self.assertNotIn("notifications", self.app.lessons.due())


if __name__ == "__main__":
    unittest.main()

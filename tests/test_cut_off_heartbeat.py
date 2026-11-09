"""AN ANSWER CUT OFF IS NOT AN ANSWER, in the heartbeats (the user, 2026-11-07: "完整计划开始").

An answer stopped at the output limit, empty, or not JSON was read as {} - "nothing to do" - and
what it was about was marked read, done, seen or judged, and never asked again. Each place here
takes only a whole answer (models/answers.py); a batch cut off is asked again in halves,
and what is still not whole is recorded as failed and moved past - never the same question
again the same way. Everything here is made up.
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.common.events.models import ListenerEvent, utcnow
from personalized_agent.heartbeat.core import heartbeat as beat
from personalized_agent.heartbeat.core import triggers
from personalized_agent.heartbeat.states import task_projects as tp

#: The ways an answer is not whole: cut by the model's word, cut mid-JSON, empty, not JSON.
NOT_WHOLE = {
    "cut by the model's word": ('{"states": []}', "max_tokens"),
    "JSON left open": ('{"states": [{"title": "Orchard survey", "detail": "Counting', "end_turn"),
    "empty": ("", "end_turn"),
    "not JSON": ("I could not decide what the user was doing.", "end_turn"),
}


def _reply(text, stop="end_turn"):
    return SimpleNamespace(text=text, stop_reason=stop, tool_calls=None, usage=None,
                           raw_content=text, model="fake", provider="fake")


class _Models:
    """Answers each call by its task: `answers[task]` is (text, stop_reason) or a function of
    the request; anything else is a whole, empty answer."""

    def __init__(self, answers=None, default=('{"states": []}', "end_turn")):
        self.answers = dict(answers or {})
        self.default = default
        self.asked = []

    def invoke(self, request, *args, **kwargs):
        self.asked.append(request)
        said = self.answers.get(getattr(request, "task", ""), self.default)
        if callable(said):
            said = said(request)
        return _reply(*said)

    def resolve(self, *a, **k):
        return SimpleNamespace(to_dict=lambda: {})

    def check_availability(self, *a, **k):
        return SimpleNamespace(available=True, to_dict=lambda: {}, state=None)


# -- 1. step one and the mark of how far the summaries got ----------------------------------

class StepOneRecordsAFailedStretchAndMovesPastItTests(unittest.TestCase):
    """The user, 2026-11-07: never ask the same thing again the same way."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.beat = self.app.heartbeat
        self.app.config.set("heartbeat_interval_seconds", 900)
        self.mark = (utcnow() - timedelta(minutes=28)).isoformat()
        self.app.config.set(beat.SUMMARISED_UNTIL, self.mark, internal=True)
        self.beat._began_at = utcnow() - timedelta(minutes=15)
        self.app.raw_store.add_event(ListenerEvent(
            event_type="screen.text", source="screen", timestamp=utcnow() - timedelta(minutes=5),
            application="Code.exe", data={"text": "Orchard survey: the tree counts"}))

    def _run(self, step_one):
        self.beat.models = _Models({"heartbeat:step_state": step_one},
                                   default=('{"notifications": []}', "end_turn"))
        return self.beat.run_once(trigger="manual_debug")

    def _moved(self):
        moved = beat._as_utc(self.app.config.get(beat.SUMMARISED_UNTIL))
        return abs((utcnow() - moved).total_seconds()) < 30

    def _step_one_prompts(self):
        return [json.dumps([m.get("content") for m in one.messages], default=str)
                for one in self.beat.models.asked if one.task == "heartbeat:step_state"]

    def test_an_answer_not_whole_writes_nothing_is_recorded_and_moved_past(self) -> None:
        for name, said in NOT_WHOLE.items():
            with self.subTest(name):
                self.app.config.set(beat.SUMMARISED_UNTIL, self.mark, internal=True)
                before = len(self.app.config.get(beat.STATE_FAILED, None) or [])
                with self.assertLogs("personalized_agent.heartbeat.core.heartbeat", "WARNING"):
                    report = self._run(said)
                self.assertTrue(self._moved(), "moved past the failed stretch")
                self.assertTrue(report["states_failed"]["why"])
                self.assertEqual(report["states_failed"]["until"],
                                 self.app.config.get(beat.SUMMARISED_UNTIL))
                kept = self.app.config.get(beat.STATE_FAILED, None)
                self.assertEqual(len(kept), before + 1)
                self.assertIn({"step": "step_state", "why": report["states_failed"]["why"]},
                              report["failed"])
                step = next(one for one in report["steps"] if one["id"] == "step_state")
                self.assertTrue(step["not_taken"])
                self.assertNotIn("states_written", report)

    def test_not_asked_again_the_same_way(self) -> None:
        self._run(("", "end_turn"))
        self.assertTrue(any("the tree counts" in one for one in self._step_one_prompts()))
        self._run(('{"states": []}', "end_turn"))
        self.assertFalse(any("the tree counts" in one for one in self._step_one_prompts()),
                         "the next review reads new work, not the failed stretch")

    def test_an_answer_without_states_is_not_whole(self) -> None:
        report = self._run(('{"state_analysis": "The user is surveying an orchard."}', "end_turn"))
        self.assertIn("states_failed", report)

    def test_an_idle_stretch_moves_on_and_is_no_failure(self) -> None:
        report = self._run(('{"states": []}', "end_turn"))
        self.assertNotIn("states_failed", report)
        self.assertTrue(self._moved())
        self.assertFalse(self.app.config.get(beat.STATE_FAILED, None))

    def test_every_round_spent_asking_for_more_is_a_failure(self) -> None:
        call = SimpleNamespace(id="c1", name="search_records", arguments={"words": ["orchard"]})
        models = _Models({"heartbeat:step_state": ("Let me look further.", "tool_use")},
                         default=('{"notifications": []}', "end_turn"))
        real = models.invoke

        def invoke(request, *a, **k):
            reply = real(request, *a, **k)
            if request.task == "heartbeat:step_state":
                reply.tool_calls = [call]
            return reply

        models.invoke = invoke
        self.beat.models = models
        self.app.config.set("heartbeat_state_max_rounds", 2)
        report = self.beat.run_once(trigger="manual_debug")
        self.assertIn("states_failed", report)
        self.assertTrue(self._moved())


class ReadLateAfterTheNotifyStepTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.beat = self.app.heartbeat

    def _run(self, notify):
        self.app.config.set(beat.READ_LATE, ["state_orchard"], internal=True)
        self.beat.models = _Models({"heartbeat:step_notify": notify})
        return self.beat.run_once(trigger="manual_debug")

    def test_a_failed_step_lets_them_go_and_says_which(self) -> None:
        for name, said in NOT_WHOLE.items():
            with self.subTest(name):
                report = self._run((said[0].replace("states", "notifications"), said[1]))
                self.assertEqual(self.app.config.get(beat.READ_LATE, None), [])
                self.assertEqual(report["read_late_failed"]["ids"], ["state_orchard"])
                self.assertTrue(report["read_late_failed"]["why"])

    def test_a_whole_answer_clears_them_and_no_failure_is_said(self) -> None:
        report = self._run(('{"notifications": []}', "end_turn"))
        self.assertEqual(self.app.config.get(beat.READ_LATE, None), [])
        self.assertNotIn("read_late_failed", report)


class AStepNotAnsweredWholeIsAskedAgainInHalvesTests(unittest.TestCase):
    """The user, 2026-11-07: "失败之后分段问重做, 如果还解决不了的标为失败" - asked again shorter,
    once; what is still not whole is the failed part."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.beat = self.app.heartbeat
        self.app.config.set("heartbeat_interval_seconds", 900)
        self.app.config.set(beat.SUMMARISED_UNTIL,
                            (utcnow() - timedelta(minutes=28)).isoformat(), internal=True)
        self.beat._began_at = utcnow() - timedelta(minutes=15)
        for minutes, application, text in ((20, "EXCEL.EXE", "the barn ledger"),
                                           (5, "Code.exe", "the tree counts")):
            self.app.raw_store.add_event(ListenerEvent(
                event_type="screen.text", source="screen",
                timestamp=utcnow() - timedelta(minutes=minutes),
                application=application, data={"text": text}))

    @staticmethod
    def _said(request):
        return json.dumps([m.get("content") for m in request.messages], default=str)

    def _step_one(self, second_half):
        def answer(request):
            text = self._said(request)
            if "the barn ledger" in text and "the tree counts" in text:
                return ('{"states": [{"title": "Orchard', "end_turn")
            if "the barn ledger" in text:
                return (json.dumps({"states": [{"title": "Barn ledger", "detail": "d"}]}),
                        "end_turn")
            return second_half
        return answer

    def _run(self, step_one, notify=('{"notifications": []}', "end_turn")):
        self.beat.models = _Models({"heartbeat:step_state": step_one,
                                    "heartbeat:step_notify": notify},
                                   default=('{}', "end_turn"))
        return self.beat.run_once(trigger="manual_debug")

    def _asked(self, task):
        return [self._said(one) for one in self.beat.models.asked if one.task == task]

    def test_both_halves_answered_write_both_and_nothing_failed(self) -> None:
        report = self._run(self._step_one(
            (json.dumps({"states": [{"title": "Orchard survey", "detail": "d"}]}), "end_turn")))
        asked = self._asked("heartbeat:step_state")
        self.assertEqual(len(asked), 3, "the whole, then each half")
        self.assertTrue("the barn ledger" in asked[1] and "the tree counts" not in asked[1])
        self.assertTrue("the tree counts" in asked[2] and "the barn ledger" not in asked[2])
        titles = [one.get("title") for one in report.get("states_written") or []]
        self.assertIn("Barn ledger", titles)
        self.assertIn("Orchard survey", titles)
        self.assertNotIn("states_failed", report)
        self.assertFalse(self.app.config.get(beat.STATE_FAILED, None))
        step = next(one for one in report["steps"] if one["id"] == "step_state")
        self.assertEqual(len(step["halves"]), 2)

    def test_a_half_still_not_whole_is_the_part_that_failed(self) -> None:
        with self.assertLogs("personalized_agent.heartbeat.core.heartbeat", "WARNING"):
            report = self._run(self._step_one(('{"states": [{"title": "Orch', "max_tokens")))
        self.assertEqual(len(self._asked("heartbeat:step_state")), 3, "asked once in halves")
        titles = [one.get("title") for one in report.get("states_written") or []]
        self.assertEqual(titles, ["Barn ledger"])
        failed = report["states_failed_parts"]
        self.assertEqual(len(failed), 1)
        halves = next(one for one in report["steps"] if one["id"] == "step_state")["halves"]
        self.assertEqual(failed[0]["from"], halves[1]["from"])
        self.assertEqual(failed[0]["until"], halves[1]["until"])
        self.assertEqual(len(self.app.config.get(beat.STATE_FAILED, None)), 1)

    def test_neither_half_whole_is_the_whole_stretch_failed(self) -> None:
        def cut(request):
            return ('{"states": [{"title": "Orch', "max_tokens")
        with self.assertLogs("personalized_agent.heartbeat.core.heartbeat", "WARNING"):
            report = self._run(cut)
        self.assertEqual(len(self._asked("heartbeat:step_state")), 3)
        self.assertNotIn("states_written", report)
        self.assertTrue(report["states_failed"]["why"])

    def test_the_notifications_step_is_asked_again_in_halves(self) -> None:
        two = (json.dumps({"states": [{"title": "Barn ledger", "detail": "d"},
                                      {"title": "Orchard survey", "detail": "d"}]}), "end_turn")

        def notify(request):
            # The states it was given, not the line saying what the steps before wrote.
            text = self._said(request)
            barn, orchard = "open  Barn ledger" in text, "open  Orchard survey" in text
            if barn and orchard:
                return ('{"notifications": [{"title": "Feed', "max_tokens")
            if barn:
                return ('{"notifications": [{"title": "Feed the goats"}]}', "end_turn")
            return ('{"notifications": []}', "end_turn")

        self.beat.models = _Models({"heartbeat:step_state": lambda request: two,
                                    "heartbeat:step_notify": notify},
                                   default=('{}', "end_turn"))
        report = self.beat.run_once(trigger="manual_debug")
        asked = self._asked("heartbeat:step_notify")
        self.assertEqual(len(asked), 3, "the whole, then each half")
        self.assertIn("Feed the goats", asked[2], "the second half is told what the first scheduled")
        self.assertNotIn("not_taken", next(one for one in report["steps"]
                                           if one["id"] == "step_notify") or {})
        self.assertFalse(any(one.get("step") == "step_notify" for one in report.get("failed") or []))

    def test_the_read_late_id_is_read_from_its_line(self) -> None:
        self.assertEqual(beat._read_late_id("[state_orchard] Mon 2 Nov .. open  Orchard"),
                         "state_orchard")
        self.assertEqual(beat._read_late_id("no id here"), "")


class TheSameNotificationCheckTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.beat = self.agent.app.heartbeat

    def test_a_check_cut_off_is_no_verdict_and_is_logged(self) -> None:
        self.beat.models = _Models({"heartbeat:same_notification": (
            '{"same_as": "", "create_anyway": false, "why": "a different', "end_turn")})
        alike = [SimpleNamespace(id="task_1", title="Send the orchard counts",
                                 description="Due Friday.", scheduled_at=utcnow(),
                                 task_payload={})]
        with self.assertLogs("personalized_agent.heartbeat.core.heartbeat", "WARNING"):
            check = self.beat._is_it_the_same(
                {"type": "reminder", "title": "Send the orchard counts", "message": "Friday."},
                utcnow(), alike, None)
        self.assertFalse(check["create"])
        self.assertIn("could not be read", check["why"])
        self.assertEqual(len(self.beat.models.asked), 1, "not asked again")

    def test_a_check_not_whole_is_asked_again_against_the_closest_half(self) -> None:
        answers = iter([('{"same_as": "", "why": "a diff', "max_tokens"),
                        ('{"same_as": "task_1", "create_anyway": false, "why": "the same"}',
                         "end_turn")])
        self.beat.models = _Models({"heartbeat:same_notification": lambda request: next(answers)})
        alike = [SimpleNamespace(id="task_2", title="Water the orchard rows",
                                 description="Weekend.", scheduled_at=utcnow(), task_payload={}),
                 SimpleNamespace(id="task_1", title="Send the orchard counts",
                                 description="Due Friday.", scheduled_at=utcnow(),
                                 task_payload={})]
        check = self.beat._is_it_the_same(
            {"type": "reminder", "title": "Send the orchard counts", "message": "Due Friday."},
            utcnow(), alike, None)
        self.assertEqual(len(self.beat.models.asked), 2, "asked again, once")
        again = self.beat.models.asked[1].messages[0]["content"]
        self.assertIn("task_1", again)
        self.assertNotIn("task_2", again, "only the closest half")
        self.assertEqual(check["same_as"], "task_1")
        self.assertFalse(check["create"])
        self.assertEqual(check["asked_again"]["alike"], ["task_1"])


# -- 2. the triggers -------------------------------------------------------------------------

class _Front:
    def __init__(self, title, text):
        self.title, self.text = title, text


class ATriggerNotAnsweredIsRecordedAsFailedTests(unittest.TestCase):
    TITLE = "Orchard survey - Mail"
    TEXT = "Ada Marlow - Invitation: orchard walk Mon 2:00 PM Yes No Maybe invite.ics"

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.watch = triggers.Triggers(heartbeat=self.app.heartbeat, bus=None,
                                       front=lambda: _Front(self.TITLE, self.TEXT))

    def _event(self, text):
        return {"application": "firefox.exe",
                "data": {"window_title": self.TITLE, "text": text, "handle": 0}}

    def _settled(self):
        self.watch.heard("listener.screen.text", self._event("opened"))
        self.watch.heard("listener.screen.text", self._event(self.TEXT))
        key = next(iter(self.watch._due))
        return self.watch._ready(now=self.watch._due[key]["at"])[0]

    def test_an_answer_not_whole_applies_nothing_says_so_and_is_not_asked_again(self) -> None:
        for name, said in NOT_WHOLE.items():
            with self.subTest(name):
                self.watch._seen.clear()
                self.watch._known.clear()
                self.watch._looked.clear()
                one = self._settled()
                self.app.heartbeat.models = _Models({"heartbeat_trigger": said})
                report = self.watch.run(one["rule"], one["window"], one["application"],
                                        fresh=one["fresh"])
                self.assertTrue(report["failed"])
                self.assertTrue(report["not_taken"])
                self.assertNotIn("notifications_scheduled", report)
                self.assertTrue(self.watch.history[0]["not_taken"])
                # The same lines, shown again: the same question is not asked again.
                self.watch.heard("listener.screen.text", self._event(self.TEXT))
                self.assertEqual(self.watch._due, {})


# -- 3. the notification lessons ---------------------------------------------------------------

class ALessonAnswerNotWholeIsRecordedAsFailedTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.heartbeat.core.calendar_nature import CalendarNature

        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.models = _Models()
        self.keeper = CalendarNature(self.app.db, models=self.models, config=self.app.config)

    def _record(self):
        self.keeper.record("delete", {"id": "task_orchard", "title": "Orchard walk with Ada Marlow",
                                      "description": "Monday at two."})
        self.keeper.wait()

    def _rows(self):
        return [dict(row) for row in self.app.db.query(
            "SELECT reasoned_at, why, lesson FROM calendar_actions")]

    def test_failed_moved_past_and_no_lesson_changed(self) -> None:
        from personalized_agent.heartbeat.core.calendar_nature import FAILED_WHY

        for name, said in NOT_WHOLE.items():
            with self.subTest(name):
                self.app.db.execute("DELETE FROM calendar_actions")
                self.models.answers["notification_lessons"] = (
                    said[0].replace("states", "why"), said[1])
                asked = len(self.models.asked)
                self._record()
                row, = self._rows()
                self.assertTrue(row["reasoned_at"], "moved past")
                self.assertTrue(row["why"].startswith(FAILED_WHY))
                self.assertFalse(row["lesson"])
                self.assertEqual(self.keeper.lessons(), [])
                self.assertEqual(len(self.models.asked), asked + 1, "asked once")

    def test_a_whole_answer_is_reasoned(self) -> None:
        self.models.answers["notification_lessons"] = (
            json.dumps({"why": "They walk the orchard alone.", "changes": []}), "end_turn")
        self._record()
        row, = self._rows()
        self.assertEqual(row["why"], "They walk the orchard alone.")


# -- 4. the state heartbeat ----------------------------------------------------------------------

class TheStateHeartbeatTakesOnlyWholeAnswersTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def _answering(self, said):
        self.beat.models = _Models(default=said)

    def _summarise(self, group, **patches):
        with mock.patch.object(self.beat, "_sample_for", return_value=[]), \
                mock.patch.object(self.beat, "_prompt", return_value="THE STATES"), \
                mock.patch.object(self.beat, "_show_the_prompt"), \
                mock.patch.object(self.beat, "_look", return_value=("0 matches", [])):
            return self.beat._summarise([], dict(group), "trace", self.beat.where("live"), {}, {})

    GROUP = {"id": "g1", "size": 3, "members": [],
             "described": {"title": "Orchard survey", "summary": "Counting the trees."}}

    def test_a_summary_not_whole_is_not_filled_with_the_old_name(self) -> None:
        for name, said in NOT_WHOLE.items():
            with self.subTest(name):
                self._answering((said[0].replace("states", "title"), said[1]))
                out = self._summarise(self.GROUP)
                self.assertTrue(out["not_taken"])
                self.assertNotIn("title", out)
                self.assertNotIn("record_id", out)

    def test_a_summary_still_asking_to_look_is_not_taken(self) -> None:
        self._answering((json.dumps({"need": {"keywords": ["orchard"], "ids": []}}), "end_turn"))
        out = self._summarise(self.GROUP)
        self.assertIn("still asking", out["not_taken"])
        self.assertNotIn("title", out)

    def test_a_failed_summary_is_recorded_on_the_task_and_moved_past(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept, Task

        kept = Kept(self.app.db)
        task = Task(id="task-survey", members=["s1", "s2", "s3"], summary_id="rec_1",
                    described={"title": "Orchard survey", "summary": "Counting the trees.",
                               "concrete": True, "waiting": True})
        kept.save([task])
        report = {}
        self.beat._failed_summary(task, {"not_taken": "the answer was empty"}, kept,
                                  self.beat.where("live"), report)
        again, = kept.by_ids(["task-survey"])
        self.assertEqual(again.described["why_failed"], "the answer was empty")
        self.assertTrue(again.described["failed_at"])
        self.assertNotIn("waiting", again.described)
        self.assertEqual(again.described["title"], "Orchard survey", "its name, untouched")
        self.assertEqual(again.summary_members, 3, "counted from here")
        self.assertEqual(report["failed"], [{"task": "task-survey",
                                             "why": "the answer was empty"}])

    def test_a_status_not_whole_is_recorded_and_keeps_the_users_changes(self) -> None:
        task = SimpleNamespace(id="task_orchard", described={"status": "Counting rows."},
                               profiled=False)
        asking = {"asked": "WHERE IT STANDS", "instruction": "SAY", "arrived": 1, "left": 0,
                  "todo": {"items": []}, "todo_scope": "live"}
        self._answering(NOT_WHOLE["JSON left open"])
        said = self.beat._status_asked({}, task, asking)
        self.assertTrue(said["not_taken"])
        landed = []
        with mock.patch.object(self.beat, "_land_todo",
                               lambda *a, **k: landed.append(k.get("failed"))), \
                mock.patch.object(self.beat, "_also_status") as marked:
            report = {}
            self.beat._keep_status(report, task, {}, said, self.beat.where("live"))
        self.assertEqual(landed, [True], "the user's changes were not seen")
        marked.assert_called_once_with("task_orchard")
        self.assertEqual(task.described["status"], "Counting rows.")
        self.assertTrue(task.described["why_status_failed"])
        self.assertTrue(report["status_calls"][0]["failed"])
        self.assertTrue(report["failed"])

    def test_a_project_question_not_whole_says_so(self) -> None:
        for name, said in NOT_WHOLE.items():
            with self.subTest(name):
                self._answering((said[0].replace("states", "status"), said[1]))
                out = self.beat._ask_project("SAY WHERE IT STANDS.", "the project", {})
                self.assertTrue(out["not_taken"])
                self.assertNotIn("status", out)

    def test_a_same_or_not_cut_off_is_not_a_no(self) -> None:
        self._answering(('{"same": false, "why": "different', "end_turn"))
        out = self.beat._ask_whether("SAME?", "A and B", {}, "merged part")
        self.assertTrue(out["not_taken"])
        self.assertFalse(out["same"])

    def test_a_merge_not_answered_is_not_split_and_is_moved_past(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept, Task

        kept = Kept(self.app.db)
        survey = Task(id="task-survey", members=["s1", "s2"],
                      described={"title": "Orchard survey", "concrete": True, "summary": "trees"},
                      retired_at="2026-10-04T00:00:00+00:00")
        jam = Task(id="task-jam", members=["s3", "s4"],
                   described={"title": "Plum jam", "concrete": True, "summary": "jam"},
                   retired_at="2026-10-04T00:00:00+00:00")
        merged = Task(id="task-merged", members=["s1", "s2", "s3", "s4"],
                      described={"title": "Orchard survey", "concrete": True},
                      born_of={"kind": "merged from", "from": ["task-survey", "task-jam"]})
        kept.save([survey, jam, merged])
        self._answering(("", "end_turn"))
        report = {}
        with mock.patch.object(self.beat, "_work_in", return_value=1e9):
            back = self.beat._undo_old_merges([merged], kept, report, self.beat.where("live"))
        self.assertEqual(back, [])
        again = kept.by_ids(["task-merged"])[0]
        self.assertEqual(sorted(again.members), ["s1", "s2", "s3", "s4"])
        self.assertTrue(again.born_of.get("rechecked"), "moved past")
        self.assertTrue(report["failed"])
        asked = len(self.beat.models.asked)
        self.beat._undo_old_merges([again], kept, {}, self.beat.where("live"))
        self.assertEqual(len(self.beat.models.asked), asked, "not asked again")


# -- 5. the plan of what is left -------------------------------------------------------------------

class APlanCutOffIsRecordedAsFailedTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept, Task

        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.hb = self.app.state_heartbeat
        self.kept = Kept(self.app.db, "live")
        row = Task(id="proj_orchard", number=7, seat=1)
        row.profile = {"scheme": tp.SCHEME, "last_seen": "2026-11-04T10:00:00+00:00"}
        row.described = {"title": "Orchard survey", "line": "A survey", "summary": "Counting",
                         "minutes": 60.0, "kind": "work", "plan_due": True,
                         "todo_changes": {"added": ["Count row 4"]}}
        self.kept.save([row])

    def _plan(self, stop):
        from personalized_agent.heartbeat.states import task_plan as plan
        from personalized_agent.heartbeat.states.project_round import ProjectRound

        self.hb.models = _Models(default=("", stop))
        with mock.patch.object(self.hb, "_going", return_value=True):
            round_ = ProjectRound(self.hb, self.hb.where("live"), {})
            self.assertTrue(plan.Planner(self.hb, round_).plan(self.kept.by_ids(["proj_orchard"])[0]))
        return round_.report, self.kept.by_ids(["proj_orchard"])[0].described

    def test_cut_off_it_is_moved_past_said_failed_and_the_users_changes_stay(self) -> None:
        from personalized_agent.heartbeat.states import task_plan as plan

        report, said = self._plan("max_tokens")
        self.assertFalse(said.get("plan_due"), "moved past")
        self.assertTrue(said.get("todo_at"))
        self.assertEqual(said.get("todo_changes"), {"added": ["Count row 4"]}, "not seen")
        self.assertIn("cut off", said["plan_why_failed"])
        self.assertTrue(said["plan_failed_at"])
        self.assertIn("cut off", report["plans"][0]["failed"])
        self.assertTrue(report["failed"])
        self.assertNotIn(self.kept.by_ids(["proj_orchard"])[0],
                         plan.Planner.due(self.kept.by_ids(["proj_orchard"])))

    def test_ended_whole_it_is_gone_through(self) -> None:
        _report, said = self._plan("end_turn")
        self.assertFalse(said.get("plan_due"))
        self.assertTrue(said.get("todo_at"))
        self.assertFalse(said.get("todo_changes"))
        self.assertNotIn("plan_why_failed", said)


# -- 6. the tasks and projects of a run --------------------------------------------------------------

def _rows_in(prompt):
    return re.findall(r"^(s\d+)\|", prompt, re.M)


class _Embed:
    def embed(self, texts, while_ok=None):
        return [[1.0, 0.0] if "orchard" in text.lower() else [0.0, 1.0] for text in texts]

    def loaded(self):
        import contextlib
        return contextlib.nullcontext()


class ARunAsksAShorterQuestionOrMovesPastTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.app.config.set("state_projects_enabled", True)
        self.beat = self.app.state_heartbeat
        self.beat.embedder = _Embed()
        self.calls = []

    def _state(self, title, hours_ago):
        from personalized_agent.agent.states.store import State

        return self.app.states.add(State(
            title=title, detail="", starts_at=utcnow() - timedelta(hours=hours_ago),
            ends_at=utcnow() - timedelta(hours=hours_ago) + timedelta(minutes=15),
            metadata={"minutes": 12, "kind": "work"}))

    def _models(self, writes_at_most, names_at_most=99, names=True, unreadable=False):
        test = self

        def answer(request):
            prompt = request.messages[0]["content"]
            if '"lines"' in request.system and "STEP 2" not in request.system:
                labels = [part.split()[0] for part in re.split(r"^== ", prompt, flags=re.M)[1:]]
                test.calls.append(("name", len(labels)))
                if not names:
                    return ("not JSON at all", "end_turn")
                if len(labels) > names_at_most:
                    return ('{"lines": {"P1": {"title": "Orch', "max_tokens")
                return (json.dumps({"lines": {label: {"title": f"Orchard survey {label}",
                                                      "line": "A made-up survey"}
                                              for label in labels}}), "end_turn")
            rows = _rows_in(prompt)
            test.calls.append(("write", len(rows)))
            if len(rows) > writes_at_most:
                if unreadable:
                    return ("I wrote the tasks down somewhere.", "end_turn")
                return ('{"lines": {}, "tasks": [{"title": "Orchard', "max_tokens")
            return (json.dumps({"lines": {}, "tasks": [
                {"title": "Count the orchard trees", "summary": "Counted.", "kind": "work",
                 "continues": "", "states": rows}]}), "end_turn")

        self.beat.models = _Models(default=answer)

    def _placed(self):
        return sorted(sid for task in tp.RunTasks(self.app.db, "live").window(24 * 30)
                      for sid in task.get("states") or [])

    def _writes(self):
        return [n for kind, n in self.calls if kind == "write"]

    def test_a_cut_stretch_is_written_in_halves(self) -> None:
        made = [self._state(f"They count orchard row {n}.", 3) for n in range(4)]
        self._models(writes_at_most=1)
        report = self.beat.run_once(trigger="manual_debug")
        self.assertTrue(report.get("round_complete"), report)
        self.assertEqual(self._writes()[:3], [4, 2, 1])
        self.assertEqual(self._placed(), sorted(str(getattr(one, "id", one)) for one in made))
        self.assertEqual(report["stretches"][-1].get("parts"), 4)
        self.assertNotIn("failed", report)

    def test_an_unreadable_stretch_is_asked_once_more_in_two_halves(self) -> None:
        for n in range(4):
            self._state(f"They count orchard row {n}.", 3)
        self._models(writes_at_most=2, unreadable=True)
        report = self.beat.run_once(trigger="manual_debug")
        self.assertEqual(self._writes()[:3], [4, 2, 2])
        self.assertEqual(len(self._placed()), 4)
        self.assertNotIn("failed", report)

    def test_still_not_whole_at_its_smallest_it_is_recorded_failed_and_moved_past(self) -> None:
        # A first run that wrote tasks: with none at all, every run is the scheme's first,
        # which reads the whole last week.
        self._state("They prune the plum trees.", 5)
        self._models(writes_at_most=9)
        self.beat.run_once(trigger="manual_debug")
        before = self._placed()
        made = [self._state(f"They count orchard row {n}.", 1) for n in range(2)]
        self.calls.clear()
        self._models(writes_at_most=0)
        report = self.beat.run_once(trigger="manual_debug")
        self.assertTrue(report.get("round_complete"), "moved past")
        self.assertEqual(self._placed(), before)
        failed = sorted(sid for one in report["failed"] if "states" in one
                        for sid in one["states"])
        self.assertEqual(failed, sorted(str(getattr(one, "id", one)) for one in made))
        self.assertTrue(all(one["why"] for one in report["failed"]))
        self.assertEqual([n for n in self._writes() if n], [2, 1, 1])
        self.calls.clear()
        self._models(writes_at_most=9)
        self.beat.run_once(trigger="manual_debug")
        self.assertEqual([n for n in self._writes() if n], [],
                         "the failed states are not asked again")

    def test_an_unreadable_naming_is_recorded_failed_and_moved_past(self) -> None:
        self._state("They count orchard row 1.", 3)
        self._models(writes_at_most=9, names=False)
        report = self.beat.run_once(trigger="manual_debug")
        from personalized_agent.heartbeat.states.tasks import Kept
        projects = [one for one in Kept(self.app.db).live()
                    if one.profile.get("scheme") == tp.SCHEME]
        self.assertTrue(projects)
        for one in projects:
            self.assertNotIn("name_due", one.described)
            self.assertTrue(one.described["why_name_failed"])
            self.assertTrue(one.described["name_failed_at"])
        self.assertTrue(any("named" in one for one in report["failed"]))

    def test_a_cut_naming_is_asked_in_halves(self) -> None:
        from personalized_agent.heartbeat.states.project_round import ProjectRound
        from personalized_agent.heartbeat.states.tasks import Kept, Task

        kept = Kept(self.app.db, "live")
        rows = []
        for n in range(3):
            row = Task(id=f"proj_{n}", number=n + 1, seat=n + 1)
            row.profile = {"scheme": tp.SCHEME}
            row.described = {"kind": "work", "minutes": 30.0,
                             "name_due": {"why": "new", "minutes": 30.0}}
            rows.append(row)
        kept.save(rows)
        self._models(writes_at_most=9, names_at_most=1)
        round_ = ProjectRound(self.beat, self.beat.where("live"), {})
        round_._name_what_is_new()
        self.assertEqual([n for kind, n in self.calls if kind == "name"], [3, 1, 2, 1, 1])
        named = [one for one in kept.live() if not (one.described or {}).get("name_due")]
        self.assertEqual(len(named), 3)
        self.assertFalse(any((one.described or {}).get("why_name_failed") for one in named))

    def test_a_relink_not_whole_changes_nothing_and_says_so(self) -> None:
        from personalized_agent.heartbeat.states.project_round import ProjectRound
        from personalized_agent.heartbeat.states.tasks import Kept, Task

        kept = Kept(self.app.db)
        tasks = tp.RunTasks(self.app.db)
        for pid, title, archived in (("p-old", "The orchard survey", True),
                                     ("p-new", "The orchard survey again", False)):
            kept.save([Task(id=pid, number=len(pid) + (1 if archived else 2),
                            profile={"scheme": tp.SCHEME},
                            described={"title": title, "line": "An orchard", "kind": "work",
                                       "in_english": {"title": title, "what_it_is": "An orchard"}},
                            archived_at="2026-01-10T00:00:00+00:00" if archived else "")])
            tasks.add([{"id": f"t-{pid}", "title": "Count trees", "kind": "work", "minutes": 60,
                        "project_id": pid, "ends_at": "2026-01-20T10:00:00+00:00"}])
        self.beat.models = _Models(default=('{"links": {"N1": {"same_as": "E1", "continues"',
                                            "end_turn"))
        round_ = ProjectRound(self.beat, SimpleNamespace(scope="live"), {})
        round_._named = {"p-new"}
        round_._made = {"p-new"}
        round_._relink()
        self.assertEqual(len(self.beat.models.asked), 1)
        self.assertTrue(round_.report["relinked"]["failed"])
        self.assertTrue(round_.report["failed"])
        old, = kept.by_ids(["p-old"])
        new, = kept.by_ids(["p-new"])
        self.assertTrue(old.archived_at)
        self.assertFalse(new.retired_at)


# -- 7. the English copies -------------------------------------------------------------------------

class ACutBatchOfCopiesIsAskedInHalvesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.app.config.set("display_language", "zh")
        at = datetime.now(timezone.utc).isoformat()
        for n, title in enumerate(("果园调查", "梅子果酱", "修剪苹果树")):
            self.app.db.execute(
                "INSERT INTO state_tasks (id, number, members, scope, described, created_at, "
                "updated_at) VALUES (?, ?, '[]', 'live', ?, ?, ?)",
                (f"t{n}", n + 1, json.dumps({"title": title}, ensure_ascii=False), at, at))
        self.sizes = []

    def _models(self, most):
        test = self

        def answer(request):
            said = json.loads(request.messages[0]["content"])
            test.sizes.append(len(said["items"]))
            if len(said["items"]) > most:
                return ('{"items": [{"id": "state_tasks:t0", "fields": {"title": "Orch',
                        "max_tokens")
            return (json.dumps({"items": [{"id": one["id"], "fields": {
                k: f"EN {k}" for k in one["fields"]}} for one in said["items"]]}), "end_turn")

        return _Models(default=answer)

    def _english(self):
        return [json.loads(row["described"]).get("in_english", {}).get("title")
                for row in self.app.db.query("SELECT described FROM state_tasks ORDER BY id")]

    def test_halved_until_answered_whole(self) -> None:
        from personalized_agent.heartbeat.states import english_copies

        done = english_copies.fill(self.app.db, self._models(1), self.app.config)
        self.assertEqual(self.sizes, [3, 1, 2, 1, 1])
        self.assertEqual(done["filled"], 3)
        self.assertEqual(done["calls"], 5)
        self.assertEqual(self._english(), ["EN title"] * 3)

    def test_cut_at_one_row_it_is_recorded_failed_and_moved_past(self) -> None:
        from personalized_agent.heartbeat.states import english_copies

        done = english_copies.fill(self.app.db, self._models(0), self.app.config)
        self.assertEqual(done["filled"], 0)
        self.assertTrue(done["not_taken"])
        self.assertEqual(self._english(), [None] * 3)
        said = json.loads(self.app.db.query_one(
            "SELECT described FROM state_tasks WHERE id = 't0'")["described"])
        self.assertIn("title", said["in_english"]["_failed"])
        self.assertTrue(said["in_english"]["_why_failed"])
        self.sizes.clear()
        english_copies.fill(self.app.db, self._models(9), self.app.config)
        self.assertEqual(self.sizes, [], "not asked again the same way")
        # Its words changed: a new question, asked.
        said["title"] = "果园调查（第二轮）"
        self.app.db.execute("UPDATE state_tasks SET described = ? WHERE id = 't0'",
                            (json.dumps(said, ensure_ascii=False),))
        english_copies.fill(self.app.db, self._models(9), self.app.config)
        self.assertEqual(self.sizes, [1])


if __name__ == "__main__":
    unittest.main()

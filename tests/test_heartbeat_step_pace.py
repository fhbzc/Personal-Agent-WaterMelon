"""EACH STEP AT ITS OWN PACE (the user, 2026-11-09: "每个step记录上一次做的时间，每个step有独立的duration,
超过这个duration才做"; the paces agreed: step one every round, notifications every 30 minutes,
people every 2 hours). A step run less often is given what it skipped. Everything here is made up."""

from __future__ import annotations

import json
import unittest
from datetime import timedelta
from types import SimpleNamespace

from support import TemporaryAgent

from personalized_agent.common.events.models import ListenerEvent, utcnow
from personalized_agent.heartbeat.core import heartbeat as beat


def _reply(text):
    return SimpleNamespace(text=text, stop_reason="end_turn", tool_calls=None, usage=None,
                           raw_content=text, model="fake", provider="fake")


class _Models:
    def __init__(self):
        self.asked = []

    def invoke(self, request, *args, **kwargs):
        self.asked.append(request)
        if request.task == "heartbeat:step_state":
            return _reply(json.dumps({"states": [{"title": "Counting the orchard's pear trees",
                                                  "detail": "In the survey sheet."}]}))
        return _reply('{"notifications": []}')

    def resolve(self, *a, **k):
        return SimpleNamespace(to_dict=lambda: {})

    def check_availability(self, *a, **k):
        return SimpleNamespace(available=True, to_dict=lambda: {}, state=None)


class StepPaceTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.beat = self.app.heartbeat
        self.beat.models = self.models = _Models()
        self.app.config.set("heartbeat_interval_seconds", 900)
        self.app.config.set(beat.SUMMARISED_UNTIL,
                            (utcnow() - timedelta(minutes=15)).isoformat(), internal=True)
        self._event("Orchard survey: the pear trees")

    def _event(self, text, minutes_ago=3):
        self.app.raw_store.add_event(ListenerEvent(
            event_type="screen.text", source="screen",
            timestamp=utcnow() - timedelta(minutes=minutes_ago),
            application="Code.exe", data={"text": text}))

    def _asked(self):
        out = [one.task for one in self.models.asked if one.task.startswith("heartbeat:step_")]
        self.models.asked.clear()
        return out

    def _ran_ago(self, step, minutes):
        kept = dict(self.app.config.get(beat.STEP_RAN, None) or {})
        one = dict(kept[step])
        one["at"] = (utcnow() - timedelta(minutes=minutes)).isoformat()
        one["until"] = (utcnow() - timedelta(minutes=minutes)).isoformat()
        kept[step] = one
        self.app.config.set(beat.STEP_RAN, kept, internal=True)

    def test_each_runs_when_its_time_has_come(self) -> None:
        self.beat.run_once(trigger="timer")
        first = self._asked()
        self.assertIn("heartbeat:step_people", first)
        self.assertIn("heartbeat:step_notify", first)
        self._event("Orchard survey: the plum trees", 1)
        report = self.beat.run_once(trigger="timer")
        self.assertEqual(self._asked(), ["heartbeat:step_state"], "the others are not due")
        self.assertEqual(set(report["steps_not_due"]), {"step_people", "step_notify"})
        # Thirty minutes on: the notifications step, not the people's.
        self._ran_ago("step_notify", 31)
        self._ran_ago("step_people", 31)
        self._event("Orchard survey: the cherry trees", 1)
        self.beat.run_once(trigger="timer")
        self.assertEqual(set(self._asked()), {"heartbeat:step_state", "heartbeat:step_notify"})
        self._ran_ago("step_people", 125)
        self._event("Orchard survey: the apple trees", 1)
        self.beat.run_once(trigger="timer")
        self.assertIn("heartbeat:step_people", self._asked())

    def test_by_hand_every_step_runs(self) -> None:
        self.beat.run_once(trigger="timer")
        self._asked()
        self._event("Orchard survey: the plum trees", 1)
        self.beat.run_once(trigger="manual_debug")
        self.assertIn("heartbeat:step_people", self._asked())

    def test_a_little_early_is_not_a_round_missed(self) -> None:
        self.beat.run_once(trigger="timer")
        self._ran_ago("step_notify", 28)
        self.assertTrue(self.beat._step_due("step_notify"), "within the slack")
        self._ran_ago("step_notify", 20)
        self.assertFalse(self.beat._step_due("step_notify"))

    def test_run_less_often_it_is_given_what_it_skipped(self) -> None:
        self.beat.run_once(trigger="timer")
        self._asked()
        self._ran_ago("step_notify", 40)
        # A state written meanwhile, by a round the notifications step sat out.
        from personalized_agent.common.events.models import new_id
        at = (utcnow() - timedelta(minutes=20)).isoformat()
        self.app.db.execute(
            "INSERT INTO states (id, title, detail, source, about, starts_at, ends_at, status, "
            "created_at, updated_at) VALUES (?, ?, '', 'agent', 'user', ?, ?, 'active', ?, ?)",
            (new_id("state"), "Promised Wren Halloway the yields by Friday", at, at, at, at))
        self._event("Orchard survey: the quince trees", 1)
        self.beat.run_once(trigger="timer")
        notify = [one for one in self.models.asked if one.task == "heartbeat:step_notify"]
        self.assertTrue(notify)
        self.assertIn("Promised Wren Halloway the yields by Friday",
                      json.dumps([m.get("content") for m in notify[0].messages], default=str,
                                 ensure_ascii=False))

    def test_a_pace_of_its_own_may_be_set(self) -> None:
        self.app.config.set("heartbeat_step_minutes", {"step_people": 0}, internal=True)
        self.beat.run_once(trigger="timer")
        self._asked()
        self._event("Orchard survey: the plum trees", 1)
        self.beat.run_once(trigger="timer")
        self.assertIn("heartbeat:step_people", self._asked())


if __name__ == "__main__":
    unittest.main()

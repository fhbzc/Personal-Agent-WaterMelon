"""Heartbeat behaviour, including the Data Heartbeat hard invariant (section 93)."""

from __future__ import annotations

import json
import threading
import pathlib
import time
import unittest
from datetime import datetime, timedelta, timezone

from support import ROOT, TemporaryAgent

from personalized_agent.heartbeat.checklist.store import DEFAULT_ITEMS, STEPS

from personalized_agent.common.events.models import ListenerEvent, utcnow


# STEP 2 IS PAUSED in the agent (PAUSED_STEPS): kept, not run. These tests
# are about the steps' machinery - step 2's included, so it can come back -
# and run with every step; StepTwoIsPausedTests checks the real setting.
import personalized_agent.heartbeat.core.heartbeat as _heartbeat_module

_PAUSED = _heartbeat_module.PAUSED_STEPS


def setUpModule() -> None:
    _heartbeat_module.PAUSED_STEPS = ()


def tearDownModule() -> None:
    _heartbeat_module.PAUSED_STEPS = _PAUSED
from personalized_agent.data_manage.library import (OTHER, STATES, USER_NATURE,
                                                    Record)
from personalized_agent.models.base import (
    AvailabilityState,
    FailureClass,
    ModelAvailability,
)


def unavailable(state: AvailabilityState, failure: FailureClass) -> ModelAvailability:
    return ModelAvailability(available=False, state=state, reason=f"simulated {state.value}",
                             authentication_valid=False, failure_class=failure)


class QuittingDoesNotWaitForARoundTests(unittest.TestCase):
    """Measured on a real quit: eight seconds to close.

        09:41:22.684  stopping
        09:41:22.686  state heartbeat stopped   (+0.002)
        09:41:27.690  data heartbeat stopped    (+5.004)
        09:41:27.691  agent heartbeat stopped   (+0.001)
        09:41:31.049  api stopped               (+3.358)

    The five seconds is a join timeout spent in full. The sleep was
    already woken on the way out - that was fixed once, and the comment
    on `stop` explains it - but a thread doing WORK is not asleep, and
    nothing a data round did ever looked to see whether it still
    mattered.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()


    def test_a_state_round_is_not_started_while_the_agent_is_closing(
            self) -> None:
        """It runs on the agent round's own thread.

        A state round is one model call per task - minutes of work on a
        busy folder - so starting one as the user quits would hold the
        way out for as long as it took.
        """
        agent_beat = self.app.heartbeat
        agent_beat._stop.set()
        said = agent_beat._state_round_if_due()
        self.assertFalse(said["run"], said.get("why"))
        self.assertIn("closing", said["why"])

    def test_no_listener_is_waited_out_on_the_way_to_the_door(self) -> None:
        """Measured on a real quit, after the instrumentation went in:

            shutdown took 9.211s: ListenerManager 8.020s, DataHeartbeat
            1.159s, ApiServer 0.030s
            listeners stopped in 8.018s: assistant 5.005s, application 3.011s

        Both spent their join timeouts exactly - five seconds and three -
        so neither thread returned. Neither was asleep either: they wait
        on an event and wake at once. They were inside a POLL, which here
        is a question put to the operating system and cannot be
        interrupted from outside.

        What is lost by not waiting is one sample of what was on screen.
        What was costing the user eight seconds was waiting for it.
        """
        import re

        from personalized_agent.listener.core import manager

        self.assertLessEqual(manager.STOPPING_GRACE_SECONDS, 1.0)
        root = pathlib.Path(manager.__file__).resolve().parent.parent
        offenders = []
        for path in sorted((root / "providers").glob("*.py")):
            for number, line in enumerate(
                    path.read_text(encoding="utf-8").splitlines(), start=1):
                found = re.search(r"\.join\(timeout=([0-9.]+)\)", line)
                if found and float(found.group(1)) > 1.0:
                    offenders.append(f"{path.name}:{number} waits "
                                     f"{found.group(1)}s")
        self.assertEqual(offenders, [],
                         "a listener still waits seconds for a poll nobody "
                         "will see: " + "; ".join(offenders))

    def test_and_the_api_does_not_wait_for_an_idle_browser_socket(
            self) -> None:
        """Handler threads are daemons and the server writes no state.

        Joining them on the way out means waiting for a socket that a
        page is holding open and not asking anything on.
        """
        from personalized_agent.api.server import ApiServer

        # A port of its own, free now: tests run several at once.
        import socket
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.app.config.set("api_port", port)
        api = ApiServer(self.app)
        api.start()
        try:
            self.assertFalse(api._httpd.block_on_close,
                             "the api joins its handler threads on close")
            began = time.perf_counter()
        finally:
            api.stop()
        self.assertLess(time.perf_counter() - began, 2.0,
                        "the api took too long to stop")


class AgentHeartbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_checklist_seeds_the_written_in_items(self) -> None:
        items = self.app.checklist.items()
        self.assertEqual(len(items), len(DEFAULT_ITEMS))
        self.assertIn("current state", items[0].text)

    def test_the_four_steps_cannot_be_changed(self) -> None:
        """Section 35, as amended: the procedure is fixed.

        A heartbeat whose steps can be rewritten by the thing running them can
        quietly stop doing the one thing it exists for, with no sign of it.
        """
        checklist = self.app.checklist
        for method in ("add", "update", "delete", "retire", "restore", "replace_all"):
            self.assertFalse(hasattr(checklist, method),
                             f"{method} would let the checklist be edited")
        self.assertEqual([i.text for i in checklist.items()], DEFAULT_ITEMS)

    def test_every_step_says_what_it_is_for(self) -> None:
        for step in STEPS:
            self.assertTrue(step.detail, f"{step.text} explains nothing")
            self.assertTrue(step.founding)

    def test_the_first_step_states_its_output_format(self) -> None:
        # Everything downstream reads the state calendar, so the step that
        # writes it has to say what it must contain.
        # A review reads a few minutes, so the period it can give a state
        # is the window - "long term" and "short term" were a pair of
        # labels it had no evidence to choose between.
        first = STEPS[0].detail
        for word in ("the window in front of it", "already on the calendar",
                     "never executed", "came from"):
            self.assertIn(word, first)

    def test_conversation_cannot_trigger_the_heartbeat(self) -> None:
        with self.assertRaises(PermissionError):
            self.app.heartbeat.run_once(trigger="user_message")

    def test_heartbeat_records_a_report(self) -> None:
        report = self.app.heartbeat.run_once(trigger="manual_debug")
        self.assertIn("checklist", report)
        self.assertIn("finished_at", report)
        self.assertEqual(len(report["checklist"]), len(DEFAULT_ITEMS))

    def test_the_window_is_read_and_folded_once_for_every_step(self) -> None:
        """The same stretch for every step: read once, each step given its
        own copy - not read again, a moment later, for each."""
        beat = self.app.heartbeat
        reads = []
        read = beat._activity_since
        beat._activity_since = lambda *a, **kw: reads.append(1) or read(*a, **kw)
        report = beat.run_once(trigger="manual_debug")
        steps = report.get("retrieval") or []
        self.assertGreater(len(steps), 1, "more than one step was given the window")
        self.assertEqual(len(reads), 1)
        # Each its own: one step's changes are not another's.
        self.assertEqual(len({id(one) for one in steps}), len(steps))

    def test_the_round_asks_whether_a_state_round_is_due(self) -> None:
        """Where the grouping is triggered from, and it SAYS so either way.

        The grouping is one model call per task - minutes on a busy folder
        - so it is not on a timer of its own; it is asked for when this
        round ends. "It did not run" and "it ran and found nothing" look
        identical from outside, so the decision goes in this report.
        """
        asked = []
        beat = self.app.state_heartbeat
        beat.run_if_due = lambda **kw: asked.append(kw) or {
            "run": False, "ran": False, "why": "0.1 hours since the last."}
        report = self.app.heartbeat.run_once(trigger="manual_debug")
        self.assertIn("state_round", report)
        self.assertEqual(report["state_round"]["why"],
                         "0.1 hours since the last.")
        self.assertEqual(asked, [{"trigger": "agent_heartbeat"}])

    def test_and_a_failing_state_round_is_not_this_round_failing(self) -> None:
        """The states are written by now; grouping them can wait."""
        def boom(**_kw):
            raise RuntimeError("the grouping fell over")

        self.app.state_heartbeat.run_if_due = boom
        report = self.app.heartbeat.run_once(trigger="manual_debug")
        self.assertIn("finished_at", report, "the agent round did not finish")
        self.assertIn("could not be asked", report["state_round"]["why"])


if __name__ == "__main__":
    unittest.main()


class StateCalendarTests(unittest.TestCase):
    """Section 34.1: what is true over a stretch of time.

    Not a task - it is shown and used as context, and never executed.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.states = self.app.states
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _add(self, title, hours_back=1, hours_on=1, **kw):
        from personalized_agent.agent.states.store import State

        return self.states.add(State(
            title=title,
            starts_at=self.now - timedelta(hours=hours_back),
            ends_at=self.now + timedelta(hours=hours_on), **kw))

    def test_a_state_covering_now_is_found_however_long_ago_it_started(self) -> None:
        # A state spanning the week is as true today as it was on Monday.
        self._add("this week work", hours_back=96, hours_on=96)
        self.assertIn("this week work", [s.title for s in self.states.covering()])

    def test_a_state_that_has_ended_does_not_cover_now(self) -> None:
        self._add("last month", hours_back=800, hours_on=-700)
        self.assertNotIn("last month", [s.title for s in self.states.covering()])

    def test_how_long_one_runs_is_read_off_the_period(self) -> None:
        """And nowhere else.

        There was a `horizon` beside the dates saying "short" or "long",
        which is the dates again in a word - so it was a second place for
        the two to disagree, and the word was what the eye went to.
        """
        self._add("all week", hours_back=96, hours_on=96)
        self._add("this hour", hours_back=1, hours_on=1)
        spans = {s.title: s.span_hours for s in self.states.covering()}
        self.assertGreater(spans["all week"], spans["this hour"])
        for state in self.states.covering():
            self.assertNotIn("horizon", state.to_dict())

    def test_the_line_the_agent_reads_does_not_open_with_a_horizon(self) -> None:
        """It began "(short term, the user)".

        The word is the span again, and it came first - so the line the
        agent reads led with the least of what it says.
        """
        self._add("something", hours_back=1, hours_on=1)
        line = [s.as_context() for s in self.states.covering()][0]
        self.assertNotIn("term,", line)
        self.assertIn("the user", line)

    def test_the_file_behind_a_state_is_not_filed_under_one_either(self) -> None:
        """A keyword is what a search matches on, and "short" is a word the
        agent no longer uses for anything."""
        state = self._add("something", hours_back=1, hours_on=1)
        record = self.app.library.get(state.id)
        self.assertIsNotNone(record)
        self.assertNotIn("short", record.keywords or [])
        self.assertNotIn("long", record.keywords or [])

    def test_the_files_written_before_are_brought_up_to_date(self) -> None:
        """Removing it from the code leaves it in everything already
        written, which is worse than either: the folder then holds two
        kinds of state file and only the old ones can be found by a word
        the agent has stopped using."""
        from personalized_agent.data_manage.library import STATES, Record

        state = self._add("an older state", hours_back=1, hours_on=1)
        stale = self.app.library.get(state.id)
        self.app.library.put(Record(
            folder=STATES, id=state.id, title=stale.title,
            summary="(short term, the user) an older state",
            body=stale.body, keywords=["active", "user", "agent", "short"],
            path=stale.path, source=stale.source))
        self.assertEqual(self.states.drop_horizons(), 1)
        fixed = self.app.library.get(state.id)
        self.assertNotIn("short", fixed.keywords or [])
        self.assertNotIn("term,", fixed.summary or "")

    def test_the_pass_does_not_search_the_library_once_per_state(self) -> None:
        """Finding a state's file by id searches every folder and every
        file in it. Once per state, on a real library, is thirteen hundred
        scans of twelve thousand files - and the application did not
        finish starting."""
        state = self._add("an older state", hours_back=1, hours_on=1)
        record = self.app.library.get(state.id)

        def refuse(_id):
            raise AssertionError("scanned the whole library for one state")

        self.app.library.get = refuse
        self.assertEqual(self.states.drop_horizons(), 0)
        self.assertIsNotNone(record)

    def test_and_a_file_that_never_had_one_is_left_alone(self) -> None:
        """A pass that rewrites everything is a pass nobody can run twice."""
        self._add("a newer state", hours_back=1, hours_on=1)
        self.assertEqual(self.states.drop_horizons(), 0)

    def test_a_state_says_where_it_came_from(self) -> None:
        from personalized_agent.agent.states.store import SOURCE_USER

        self._add("the user said so", source=SOURCE_USER)
        line = [s.as_context() for s in self.states.covering()][0]
        self.assertIn("the user said this", line)

    def test_the_agents_own_intentions_are_states_too(self) -> None:
        from personalized_agent.agent.states.store import ABOUT_AGENT

        self._add("draft a summary for them", about=ABOUT_AGENT)
        line = [s.as_context() for s in self.states.covering()][0]
        self.assertIn("the agent intends", line)

    def test_a_state_carries_the_period_it_covers(self) -> None:
        self._add("writing the report")
        line = [s.as_context() for s in self.states.covering()][0]
        self.assertIn(" to ", line, "the span must be stated")

    def test_extending_a_state_is_counted(self) -> None:
        # A state extended again and again is one that is not being finished.
        state = self._add("finish the migration")
        for _ in range(3):
            self.states.extend(state.id, self.now + timedelta(days=1), "not yet")
        self.assertEqual(self.states.get(state.id).extensions, 3)

    def test_a_finished_state_leaves_the_calendar(self) -> None:
        state = self._add("write the summary")
        self.states.close(state.id, reason="done")
        self.assertNotIn("write the summary", [s.title for s in self.states.covering()])
        self.assertEqual(self.states.get(state.id).metadata["closed_because"], "done")

    def test_states_that_have_run_out_are_offered_for_review(self) -> None:
        self._add("ended yesterday", hours_back=48, hours_on=-24)
        self.assertIn("ended yesterday",
                      [s.title for s in self.states.needing_review()])

    def test_a_state_is_never_executed(self) -> None:
        # The scheduler runs things; the calendar only shows them.
        for method in ("run", "execute", "fire", "trigger"):
            self.assertFalse(hasattr(self.states, method))


class HeartbeatWritesStatesTests(unittest.TestCase):
    """Section 35, as amended: the four steps, and what each produces."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _run(self, payload):
        class Reply:
            text = json.dumps(payload)

        self.app.heartbeat.models = type("M", (), {
            "invoke": lambda self, *a, **k: Reply(),
            "resolve": lambda self, *a, **k: type("R", (), {"to_dict": lambda s: {}})(),
            "check_availability": lambda self, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda s: {}, "state": None})()})()
        return self.app.heartbeat.run_once(trigger="manual_debug")

    def test_the_period_the_step_gives_is_the_period_written(self) -> None:
        """The period says everything a horizon used to.

        There is no label beside it any more: "short" and "long" were the
        span read off in a word, and a word beside the dates is a second
        place for the two to disagree.
        """
        now = datetime.now(timezone.utc)
        report = self._run({"states": [
            {"title": "the longer work", "detail": "what it was", "about": "user",
             "starts_at": (now - timedelta(hours=4)).isoformat(),
             "ends_at": (now + timedelta(hours=2)).isoformat()},
            {"title": "this hour", "detail": "what it is", "about": "user",
             "starts_at": now.isoformat(),
             "ends_at": (now + timedelta(hours=1)).isoformat()}]})
        by_title = {s["title"]: s for s in report["states_written"]}
        self.assertTrue(by_title["the longer work"]["starts_at"])
        self.assertTrue(by_title["this hour"]["ends_at"])
        for entry in report["states_written"]:
            self.assertNotIn("horizon", entry)

    def test_a_state_with_no_period_at_all_is_about_now(self) -> None:
        # The fallback, and the only thing a state that gives no dates can
        # honestly be taken to mean.
        report = self._run({"states": [
            {"title": "something", "detail": "with no dates", "about": "user"}]})
        written = self.app.states.recent(limit=1)[0]
        self.assertLessEqual(written.span_hours, 3)

    def test_the_step_is_not_asked_for_a_horizon(self) -> None:
        report = self._run({"states": []})
        prompt = report["steps"][0]["prompt"]
        self.assertNotIn('"horizon"', prompt)
        self.assertNotIn("short term", prompt)
        self.assertNotIn("long term", prompt)
        self.assertIn("There is no separate label to set", prompt)

    def test_step_two_writes_what_kind_of_person_the_user_is(self) -> None:
        self._run({"states": [{"title": "A long session", "detail": "d"}],
                   "user_nature": [{"title": "Works in long sessions",
                                    "content": "Returns for hours at a time.",
                                    "kind": "personal", "states": [1]}]})
        records = [r for r in self.app.library.records()
                   if r.folder == USER_NATURE]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].metadata["nature_kind"], "personal")

    def _pictures(self):
        from pathlib import Path
        from personalized_agent.common.events.models import ListenerEvent
        root = Path(self.app.library.root) / "references" / "screens" / "today"
        root.mkdir(parents=True, exist_ok=True)
        made = []
        for name in ("slide.jpg", "video.jpg"):
            path = root / name
            path.write_bytes(b"jpg")
            event = ListenerEvent(event_type="screen.shot", source="screenshot",
                                  timestamp=datetime.now(timezone.utc) - timedelta(minutes=1),
                                  data={"path": str(path), "window_title": name})
            self.app.raw_store.add_event(event)
            made.append((event, path))
        return made

    def test_step_one_links_materials_and_the_rest_of_the_pictures_go(self) -> None:
        (slide, slide_path), (_video, video_path) = self._pictures()
        report = self._run({"states": [{
            "title": "They are revising the FieldNotes slides.",
            "detail": "Reworking the deck.", "sources": ["the deck"],
            "materials": [slide.id]}]})
        state_id = report["states_written"][0]["id"]
        linked = self.app.db.query("SELECT event_id FROM state_materials "
                                   "WHERE state_id = ?", (state_id,))
        self.assertEqual([row["event_id"] for row in linked], [slide.id])
        self.assertTrue(slide_path.exists())
        self.assertFalse(video_path.exists(),
                         "a picture no state linked is gone when the run ends")
        self.assertEqual(report["pictures_dropped"]["deleted"], 1)

    def test_a_run_whose_step_one_failed_drops_nothing(self) -> None:
        (_slide, slide_path), (_video, video_path) = self._pictures()
        report = self._run({"not_states": []})
        self.assertNotIn("pictures_dropped", report)
        self.assertTrue(slide_path.exists() and video_path.exists())

    def test_a_nature_resting_on_no_state_or_of_no_kind_is_not_written(self) -> None:
        self._run({"states": [{"title": "A long session", "detail": "d"}],
                   "user_nature": [
                       {"title": "Rests on nothing", "content": "c",
                        "kind": "work", "states": []},
                       {"title": "Rests on a state that is not there",
                        "content": "c", "kind": "work", "states": [7]},
                       {"title": "Of no kind", "content": "c", "states": [1]},
                       {"title": "Work nature, which the stages write",
                        "content": "c", "kind": "work", "states": [1]},
                       {"title": "Both kinds at once", "content": "c",
                        "kind": "both", "states": [1]}]})
        records = [r for r in self.app.library.records()
                   if r.folder == USER_NATURE]
        self.assertEqual([r.title for r in records], ["Both kinds at once"])
        from personalized_agent.heartbeat.experience.store import ExperienceStore
        # Step 2 writes personal nature only: one said to be both keeps its
        # personal half; work nature comes from the stages of the work.
        self.assertEqual(ExperienceStore(self.app.db).nature_kinds(),
                         {records[0].id: "personal"})

    def test_one_run_cannot_rewrite_the_whole_calendar(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import MAX_STATES_PER_RUN

        report = self._run({"states": [{"title": f"state {i}", "detail": "d",
                                        "horizon": "short"}
                                       for i in range(40)]})
        self.assertLessEqual(len(report["states_written"]), MAX_STATES_PER_RUN)


class StatePeriodTests(unittest.TestCase):
    """Section 34.1, as amended: a state runs when it actually runs.

    Choosing between two fixed shapes made every long state exactly a week
    long and still running days from now, whatever it described - so a thing
    that had already finished sat in the calendar claiming to be the user's
    current situation.
    """

    def test_the_period_the_agent_states_is_the_period_used(self) -> None:
        from personalized_agent.agent.states.store import window_for

        starts, ends = window_for(starts_at="2026-09-01T09:00:00Z",
                                  ends_at="2026-09-02T18:00:00Z")
        self.assertEqual(starts.isoformat()[:16], "2026-09-01T09:00")
        self.assertEqual(ends.isoformat()[:16], "2026-09-02T18:00")

    def test_a_state_with_no_period_falls_back_to_about_now(self) -> None:
        """Which is the only thing it can honestly mean.

        There used to be two fallback shapes and a horizon to choose
        between them, and the long one claimed four days the agent had not
        been told anything about.
        """
        from personalized_agent.agent.states.store import window_for

        now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        starts, ends = window_for(now)
        self.assertLessEqual((ends - starts).total_seconds() / 3600, 3)
        self.assertLess(starts, now)
        self.assertGreater(ends, now)

    def test_a_start_with_no_end_does_not_become_a_week(self) -> None:
        from personalized_agent.agent.states.store import window_for

        now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        starts, ends = window_for(now, starts_at="2026-09-03T11:00:00Z")
        self.assertLessEqual((ends - starts).total_seconds() / 3600, 4)

    def test_a_nonsense_period_falls_back_rather_than_failing(self) -> None:
        from personalized_agent.agent.states.store import window_for

        starts, ends = window_for(starts_at="not a date", ends_at="also not")
        self.assertLess(starts, ends)

    def test_the_agent_is_told_what_is_not_a_state(self) -> None:
        # A completed fact written as a state is the failure this prevents.
        from personalized_agent.heartbeat.core.heartbeat import SYSTEM_PROMPT

        self.assertIn("Something that has already happened is not a state",
                      SYSTEM_PROMPT)
        self.assertIn("starts_at", SYSTEM_PROMPT)
        self.assertIn("ends_at", SYSTEM_PROMPT)

    def test_the_heartbeat_uses_the_period_it_was_given(self) -> None:
        agent = TemporaryAgent()
        try:
            written = agent.app.heartbeat._write_states("task_1", [{
                "title": "Reviewing the numbers", "detail": "the quarterly figures",
                "horizon": "long",
                "starts_at": "2026-09-01T09:00:00Z",
                "ends_at": "2026-09-02T18:00:00Z"}])
            state = agent.app.states.get(written[0]["id"])
            self.assertEqual(state.starts_at.isoformat()[:16], "2026-09-01T09:00")
            self.assertAlmostEqual(state.span_hours, 33.0, places=1)
        finally:
            agent.cleanup()


class InferenceTraceTests(unittest.TestCase):
    """Section 34.1, as amended: an inferred state carries its evidence.

    The agent describes the user's own situation back to them and can be wrong
    about it. A claim with no evidence is one the user can only accept or
    delete, and neither teaches anybody anything.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_evidence_is_kept_with_the_state(self) -> None:
        written = self.app.heartbeat._write_states("task_1", [{
            "title": "Writing the report", "detail": "Most of the week.",
            "horizon": "long",
            "evidence": ["11,560 lines in the release repo over five days",
                         "the report opened on four afternoons"],
            "reasoning": "Sustained work on one repository across days."}])
        state = self.app.states.get(written[0]["id"])
        self.assertEqual(len(state.metadata["evidence"]), 2)
        self.assertIn("11,560", state.metadata["evidence"][0])
        self.assertIn("Sustained work", state.metadata["reasoning"])

    def test_the_duration_is_asked_for_and_defined(self) -> None:
        """How long the activity actually went on, in minutes - required,
        and said to be the time at it, not its span."""
        from personalized_agent.heartbeat.core.heartbeat import STEP_PROMPTS

        asked = (STEP_PROMPTS["step_state"]["instruction"]
                 + STEP_PROMPTS["step_state"]["returns"])
        self.assertIn("THE DURATION OF THIS ACTIVITY, IN MINUTES. REQUIRED", asked)
        self.assertIn("not the span", asked)
        self.assertIn('"minutes": "REQUIRED', asked)

    def test_the_duration_is_kept(self) -> None:
        written = self.app.heartbeat._write_states("task_1", [{
            "title": "They are writing the report.", "detail": "The draft.",
            "minutes": 9, "starts_at": "2026-10-07T14:02:00+00:00",
            "ends_at": "2026-10-07T14:14:00+00:00"}], looked_at={
            "minutes": 15, "from": "2026-10-07T14:00:00+00:00",
            "to": "2026-10-07T14:15:00+00:00"})
        state = self.app.states.get(written[0]["id"])
        self.assertEqual(state.metadata["minutes"], 9)

    def test_a_missing_duration_is_its_stretch_inside_the_window(self) -> None:
        written = self.app.heartbeat._write_states("task_1", [{
            "title": "They are writing the report.", "detail": "The draft.",
            "starts_at": "2026-10-07T13:00:00+00:00",
            "ends_at": "2026-10-07T14:06:00+00:00"}], looked_at={
            "minutes": 15, "from": "2026-10-07T14:00:00+00:00",
            "to": "2026-10-07T14:15:00+00:00"})
        state = self.app.states.get(written[0]["id"])
        self.assertEqual(state.metadata["minutes"], 6)

    def test_the_agent_is_asked_for_evidence(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import SYSTEM_PROMPT

        self.assertIn("evidence", SYSTEM_PROMPT)
        self.assertIn("carries its evidence", SYSTEM_PROMPT)

    def test_the_trace_is_bounded(self) -> None:
        written = self.app.heartbeat._write_states("task_1", [{
            "title": "A state", "detail": "d", "horizon": "short",
            "evidence": [f"item {i}" for i in range(40)]}])
        state = self.app.states.get(written[0]["id"])
        self.assertLessEqual(len(state.metadata["evidence"]), 8)

    def test_a_state_without_a_detail_is_not_written(self) -> None:
        # Section 52.1: everything on the calendar says what it is.
        written = self.app.heartbeat._write_states("task_1", [
            {"title": "No detail here", "horizon": "short"}])
        self.assertEqual(written, [])

    def test_the_panel_shows_the_trace(self) -> None:
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(
            encoding="utf-8")
        self.assertIn("Why the agent believes this", script)
        # And says so when an inferred state has none, rather than showing
        # nothing where the reasoning should be.
        self.assertIn("None was recorded, so there is no way to check this", script)


class ChangingAnIntervalTakesEffectNowTests(unittest.TestCase):
    """Section 12, as amended: the next wake is what the setting is about.

    Both loops read their interval when they decide how long to sleep, so a
    change was picked up "on the next wake" - and for the agent that wake can
    be an hour away. Turning the hour down to five minutes did nothing for up
    to an hour, which reads as a setting that does not work, while the panel
    went on reporting the next run an hour out because that is when it had
    been scheduled.

    The sleep is interruptible instead. A change does not trigger a run: it
    makes the loop work out again when the run is due, counting from the same
    moment it was already counting from - so if the new interval has already
    elapsed, the run is due now.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _loops(self):
        return ((self.app.heartbeat, "heartbeat_interval_seconds"),)

    def test_shortening_it_brings_the_next_run_forward(self) -> None:
        for loop, key in self._loops():
            loop._watch_the_interval()
            self.app.config.set(key, 3600)
            waiting = threading.Thread(
                target=lambda l=loop: l._sleep_until_due(l._interval), daemon=True)
            waiting.start()
            deadline = time.time() + 5
            while loop._next_run_at is None and time.time() < deadline:
                time.sleep(0.02)
            self.assertGreater(loop._next_run_at - time.time(), 60, key)
            self.app.config.set(key, 60)
            # Due within a minute now, not within an hour.
            settled = time.time() + 5
            while time.time() < settled:
                if loop._next_run_at - time.time() < 120:
                    break
                time.sleep(0.02)
            self.assertLess(loop._next_run_at - time.time(), 120, key)
            loop._stop.set()
            loop._interval_changed.set()
            waiting.join(timeout=5)
            loop._stop.clear()

    def test_an_interval_already_elapsed_is_due_at_once(self) -> None:
        # The deadline is counted from when the wait began, so an interval
        # shortened past the time already spent waiting is due immediately -
        # rather than starting a fresh wait of the new length.
        loop = self.app.heartbeat
        loop._watch_the_interval()
        asked = [3600]
        done = []
        thread = threading.Thread(
            target=lambda: done.append(loop._sleep_until_due(lambda: asked[0])),
            daemon=True)
        thread.start()
        deadline = time.time() + 5
        while loop._next_run_at is None and time.time() < deadline:
            time.sleep(0.02)
        asked[0] = 0
        loop._interval_changed.set()
        thread.join(timeout=5)
        self.assertEqual(done, [True], "the run was not treated as due")

    def test_a_change_does_not_itself_run_the_heartbeat(self) -> None:
        # Editing how often something happens is not asking for it to happen.
        loop = self.app.heartbeat
        loop._watch_the_interval()
        self.app.config.set("heartbeat_interval_seconds", 3600)
        ran = []
        thread = threading.Thread(
            target=lambda: ran.append(loop._sleep_until_due(loop._interval)),
            daemon=True)
        thread.start()
        deadline = time.time() + 5
        while loop._next_run_at is None and time.time() < deadline:
            time.sleep(0.02)
        self.app.config.set("heartbeat_interval_seconds", 1800)
        time.sleep(0.3)
        self.assertEqual(ran, [], "it woke up and ran instead of re-timing")
        self.assertGreater(loop._next_run_at - time.time(), 600)
        loop._stop.set()
        loop._interval_changed.set()
        thread.join(timeout=5)

    def test_an_unrelated_setting_does_not_disturb_the_loop(self) -> None:
        loop = self.app.heartbeat
        loop._watch_the_interval()
        loop._interval_changed.clear()
        self.app.config.set("agent_name", "Not Aria")
        self.assertFalse(loop._interval_changed.is_set())

    def test_each_loop_watches_only_its_own(self) -> None:
        self.assertEqual(self.app.heartbeat.WAKES_ON,
                         ("heartbeat_interval_seconds", "heartbeat_enabled"))
        self.assertEqual(self.app.state_heartbeat.WAKES_ON,
                         ("state_heartbeat_every_hours", "state_heartbeat_enabled"))
        self.app.state_heartbeat._watch_the_interval()
        self.app.state_heartbeat._interval_changed.clear()
        self.app.config.set("heartbeat_interval_seconds", 120)
        self.assertFalse(self.app.state_heartbeat._interval_changed.is_set())

    def test_stopping_still_stops(self) -> None:
        loop = self.app.heartbeat
        self.app.config.set("heartbeat_interval_seconds", 3600)
        out = []
        thread = threading.Thread(
            target=lambda: out.append(loop._sleep_until_due(loop._interval)),
            daemon=True)
        thread.start()
        deadline = time.time() + 5
        while loop._next_run_at is None and time.time() < deadline:
            time.sleep(0.02)
        loop._stop.set()
        loop._interval_changed.set()
        thread.join(timeout=5)
        self.assertEqual(out, [False], "a stopped loop must not report a run due")
        loop._stop.clear()


class EverythingTheAgentAddsSaysWhenItWasAddedTests(unittest.TestCase):
    """A state, a trait and a scheduled task all carry the time they were
    written down - `created_at` on each of the three.

    It is not the same fact as any of the other times they carry. A state
    has a period, a task has an hour it runs at, and a trait is about a
    person rather than a moment; none of those says when the agent decided
    it. A state written this morning about a fortnight of work and one
    written a fortnight ago look alike without it, and they are very
    different claims about how long the agent has been watching.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.before = utcnow()

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _within_the_run(self, when) -> None:
        self.assertIsNotNone(when)
        self.assertGreaterEqual(when, self.before)
        self.assertLessEqual(when, utcnow() + timedelta(seconds=5))

    def test_a_state_says_when_it_was_added(self) -> None:
        from personalized_agent.agent.states.store import State

        state = self.app.states.add(State(
            title="A state", detail="d",
            starts_at=utcnow() - timedelta(days=14),
            ends_at=utcnow() + timedelta(hours=1)))
        self._within_the_run(state.created_at)
        # And it is not the period. The state covers a fortnight; it was
        # written a moment ago.
        self.assertGreater(state.created_at, state.starts_at)

    def test_and_the_reader_is_given_it(self) -> None:
        from personalized_agent.agent.states.store import State

        state = self.app.states.add(State(title="A state", detail="d"))
        self.assertIn("created_at", state.to_dict())
        # In the file as well as in the database, because the file is what
        # the user can open.
        record = self.app.library.get(state.id)
        self.assertIn("created_at:", record.to_text())

    def test_a_trait_says_when_it_was_added(self) -> None:
        from personalized_agent.data_manage.library import USER_NATURE, Record

        record = self.app.library.write(Record(
            folder=USER_NATURE, title="A trait", summary="s", body="b"))
        self._within_the_run(record.created_at)
        self.assertIn("created_at:", record.to_text())

    def test_a_scheduled_task_says_when_it_was_added(self) -> None:
        from personalized_agent.common.events.models import ScheduledTask

        task = self.app.scheduler_store.create(ScheduledTask(
            title="A task", scheduled_at=utcnow() + timedelta(hours=3)))
        self._within_the_run(task.created_at)
        # And it is not the hour it runs at.
        self.assertLess(task.created_at, task.scheduled_at)

    def test_and_it_survives_a_reload(self) -> None:
        """A time held only in the object that wrote it is not recorded."""
        from personalized_agent.common.events.models import ScheduledTask

        task = self.app.scheduler_store.create(ScheduledTask(
            title="A task", scheduled_at=utcnow() + timedelta(hours=3)))
        self.assertEqual(self.app.scheduler_store.get(task.id).created_at,
                         task.created_at)

    def test_and_a_state_keeps_the_one_it_was_written_with(self) -> None:
        """Changing a state is not adding it again."""
        from personalized_agent.agent.states.store import State

        state = self.app.states.add(State(
            title="A state", detail="d", ends_at=utcnow() + timedelta(hours=1)))
        added = state.created_at
        self.app.states.still_going(state.id, utcnow() + timedelta(hours=4),
                                    note="seen again")
        again = self.app.states.get(state.id)
        self.assertEqual(again.created_at, added)
        self.assertGreaterEqual(again.updated_at, added)


class StatesAreWrittenInEnglishTests(unittest.TestCase):
    """Whatever language the activity was in, and whatever language the
    rest of the agent writes in.

    Not a preference about the user: states are compared with one another -
    the review that groups them reads their words and their meaning - and a
    calendar half in one language and half in another is a calendar where
    the same situation written twice does not match itself.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.heartbeat = self.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _run(self):
        class Reply:
            text = json.dumps({"states": [], "actions": []})

        self.heartbeat.models = type("M", (), {
            "invoke": lambda s, *a, **k: Reply(),
            "resolve": lambda s, *a, **k: type(
                "R", (), {"to_dict": lambda x: {}})(),
            "check_availability": lambda s, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda x: {},
                "state": None})()})()
        report = self.heartbeat.run_once(trigger="manual_debug")
        return {step["id"]: step for step in report["steps"]}

    def _sent(self, display="zh"):
        """What the state step's call says, as the manager sends it."""
        self.app.config.set("display_language", display)
        asked = []

        class Reply:
            text = json.dumps({"states": [], "actions": []})

        self.heartbeat.models = type("M", (), {
            "invoke": lambda s, request, **k: (asked.append(request), Reply())[1],
            "resolve": lambda s, *a, **k: type(
                "R", (), {"to_dict": lambda x: {}})(),
            "check_availability": lambda s, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda x: {},
                "state": None})()})()
        self.heartbeat.run_once(trigger="manual_debug")
        self.assertTrue(asked, "the model was never asked")
        first = asked[0]
        return first, first.language.compose(first.system, self.app.config)

    def test_the_step_is_told_to_write_them_in_english(self) -> None:
        request, said = self._sent()
        self.assertEqual([one["rule"] for one in request.language.rules()],
                         ["display", "internal"])
        self.assertIn("the states", request.language.rules()[1]["what"])
        self.assertIn("write them in English", said)
        prompt = self._run()["step_state"]["prompt"]
        self.assertIn("ONE COMPLETE SENTENCE whose subject is THE USER", prompt)

    def test_and_why_rather_than_only_that(self) -> None:
        """An instruction with no reason is one a model drops the moment
        the material suggests otherwise."""
        self.assertIn("compared with one another", self._sent()[1])

    def test_quoted_material_keeps_its_own_wording(self) -> None:
        """A path, a page title, a line the user wrote in their own
        language: the sentence around it is English, not the quote."""
        self.assertIn("keeps its own wording", self._sent()[1])

    def test_the_display_language_does_not_contradict_it(self) -> None:
        """Every heartbeat prompt carries "write what the user will read in
        their language". Two instructions that disagree are answered by
        whichever the model reads last, which is not a way to decide
        anything - so the exception is stated where the rule is: after it,
        as an exception to it."""
        said = self._sent("zh")[1]
        self.assertLess(said.index("Write everything the user will read in Chinese"),
                        said.index("EXCEPTION, for the states"))

    def test_and_the_other_steps_are_not_given_the_exception(self) -> None:
        """What they write is read rather than compared, so it follows the
        display language like everything else - the people step's tags
        apart, which are found by."""
        from personalized_agent.heartbeat.core.heartbeat import STEP_PROMPTS

        with_states = [name for name, shape in STEP_PROMPTS.items()
                       if any("the states" in one["what"]
                              for one in shape["language"].rules())]
        self.assertEqual(with_states, ["step_state"])
        for name in ("step_notify", "step_todo"):
            self.assertEqual([one["rule"] for one in STEP_PROMPTS[name]["language"].rules()],
                             ["display"], name)


class StoppingDoesNotWaitOutTheIntervalTests(unittest.TestCase):
    """Quitting the agent took fifteen seconds and looked like a hang.

    Each loop sleeps on the event that says "your interval setting
    changed", with the whole interval as the timeout. `stop()` set a
    different event - so the thread stayed asleep, the join that follows
    spent its full five seconds failing, and three loops did that one
    after another. The threads are daemons, so the process did eventually
    go; what the user saw was fifteen seconds of nothing.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _stop_while_sleeping(self, beat, interval=3600):
        """Put the loop in the sleep it spends its life in, then stop it."""
        import threading
        import time

        woke = threading.Event()

        def sleep():
            beat._sleep_until_due(lambda: interval)
            woke.set()

        thread = threading.Thread(target=sleep, daemon=True)
        # What `stop` joins. The loop body is not run: the question is
        # whether a sleeping loop can be woken, and running one would ask
        # a model instead.
        beat._thread = thread
        thread.start()
        time.sleep(0.05)
        began = time.perf_counter()
        beat.stop()
        return time.perf_counter() - began, woke.wait(2)

    def test_the_review_stops_at_once(self) -> None:
        spent, woke = self._stop_while_sleeping(self.app.heartbeat)
        self.assertTrue(woke, "the loop slept through being stopped")
        self.assertLess(spent, 1.0, f"stopping took {spent:.1f}s")


    def test_and_so_does_the_one_that_groups_states(self) -> None:
        spent, woke = self._stop_while_sleeping(self.app.state_heartbeat)
        self.assertTrue(woke, "the loop slept through being stopped")
        self.assertLess(spent, 1.0, f"stopping took {spent:.1f}s")

    def test_a_loop_that_was_never_started_stops_anyway(self) -> None:
        """Quitting before the agent finished starting is an ordinary way
        to quit."""
        self.app.state_heartbeat.stop()
        self.app.heartbeat.stop()


class TheReviewHasThreeStepsTests(unittest.TestCase):
    """There was a fourth, and it judged what was on the calendar.

    It read every state whose end had passed and said finished, extended
    or retired. Nothing does that now: a state stops covering the present
    when its end goes by, and its status stays active for good.

    And the third decided whether there was anything useful to do, and
    could schedule any task, interrupt, or leave a note. It is replaced by
    one that only adds notifications: what to tell the user, and when.
    """

    def test_the_checklist_is_three(self) -> None:
        from personalized_agent.heartbeat.checklist.store import STEPS

        # Step 2 is the people now (heartbeat/connections/people.py); who
        # the user is is kept, paused (PAUSED_STEPS).
        # And step 4: the next steps the user finished, ticked - asked only
        # when there are open ones (test_state_todo).
        self.assertEqual([step.id for step in STEPS],
                         ["step_state", "step_people", "step_nature", "step_notify",
                          "step_todo"])
        self.assertEqual(STEPS[0].text, "Analyze the user's current state.")
        self.assertEqual(STEPS[3].text,
                         "Determine which notifications need to be added and "
                         "which need to be disabled.")

    def test_and_nothing_is_asked_what_to_do(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import (
            STEP_PROMPTS, SYSTEM_PROMPT)

        self.assertNotIn("step_help", STEP_PROMPTS)
        self.assertNotIn("ANYTHING USEFUL TO DO", SYSTEM_PROMPT)
        self.assertNotIn('"actions"', SYSTEM_PROMPT)

    def test_and_actions_in_a_reply_schedule_nothing(self) -> None:
        """Nothing reads the field, so nothing acts on it."""
        agent = TemporaryAgent()
        try:
            class Reply:
                text = json.dumps({
                    "states": [], "user_nature": [],
                    "actions": [{"type": "notify", "title": "Should not",
                                 "message": "be sent"}],
                    "message_to_user": "should not be sent either"})

            agent.app.heartbeat.models = type("M", (), {
                "invoke": lambda s, *a, **k: Reply(),
                "resolve": lambda s, *a, **k: type(
                    "R", (), {"to_dict": lambda x: {}})(),
                "check_availability": lambda s, *a, **k: type("A", (), {
                    "available": True, "to_dict": lambda x: {},
                    "state": None})()})()
            report = agent.app.heartbeat.run_once(trigger="manual_debug")
            self.assertNotIn("actions", report)
            self.assertNotIn("message_to_user", report)
            self.assertEqual(len(report.get("steps") or report["checklist"]), 4)
        finally:
            agent.cleanup()

    def test_and_nothing_is_asked_to_judge_a_state(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import (
            STEP_PROMPTS, SYSTEM_PROMPT)

        self.assertNotIn("step_review", STEP_PROMPTS)
        self.assertNotIn("state_review", SYSTEM_PROMPT)
        self.assertNotIn("JUDGE THE STATES", SYSTEM_PROMPT)

    def test_and_a_verdict_in_a_reply_does_nothing(self) -> None:
        """Nothing reads the field, so nothing acts on it - rather than
        acting on it halfway."""
        agent = TemporaryAgent()
        try:
            from personalized_agent.agent.states.store import ACTIVE, State

            state = agent.app.states.add(State(
                title="An old state",
                starts_at=utcnow() - timedelta(days=2),
                ends_at=utcnow() - timedelta(hours=1)))

            class Reply:
                text = json.dumps({"states": [], "actions": [],
                                   "state_review": [
                                       {"state_id": state.id,
                                        "verdict": "retire",
                                        "reason": "it is over"}]})

            agent.app.heartbeat.models = type("M", (), {
                "invoke": lambda s, *a, **k: Reply(),
                "resolve": lambda s, *a, **k: type(
                    "R", (), {"to_dict": lambda x: {}})(),
                "check_availability": lambda s, *a, **k: type("A", (), {
                    "available": True, "to_dict": lambda x: {},
                    "state": None})()})()
            report = agent.app.heartbeat.run_once(trigger="manual_debug")
            self.assertNotIn("states_reviewed", report)
            self.assertEqual(agent.app.states.get(state.id).status, ACTIVE)
        finally:
            agent.cleanup()


class OneCallPerStepTests(unittest.TestCase):
    """Section 37, as amended: four questions, asked four times.

    The four steps were four sections of one prompt answered in one call:
    one act of attention divided four ways, with four gatherings poured into
    a single context and told apart only by a label. Worse, it could not do
    the obvious thing - step 4 judges the states on the calendar, and step 1
    had just written some, in the same run, which step 4 could not be shown.

    Asked separately, each step gets its own question, its own context, and
    what the earlier steps concluded. The preamble is repeated four times and
    the input roughly doubles; that is the price of the other three things.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _steps(self):
        report = self.heartbeat.run_once(trigger="manual_debug")
        return report, {step["id"]: step for step in report.get("steps", [])}

    def _away(self):
        """A model that will not answer, the way the manager reports one."""
        from personalized_agent.models.base import ModelAvailability
        from personalized_agent.models.manager import ModelUnavailable

        resolved = self.agent.app.models.resolve("heartbeat")
        return ModelUnavailable(
            resolved, ModelAvailability(available=False, reason="no credit"))

    def test_every_step_gets_its_own_call(self) -> None:
        _report, steps = self._steps()
        self.assertEqual(len(steps), 4)
        for step in steps.values():
            self.assertTrue(step.get("prompt"), step["id"])
            self.assertTrue(step.get("answer") or step.get("blocked"), step["id"])

    #: What each prompt is about, in its own words. It used to be enough to
    #: look for "STEP 2 of 3", and a call is no longer told that it is one
    #: of a list - so a prompt is told apart by its subject, which is what a
    #: reader would have used anyway.
    SUBJECTS = {"step_state": "ANALYSE THE USER'S STATE",
                "step_nature": "WORK OUT WHAT THE USER IS LIKE"}

    def test_each_prompt_carries_its_own_question(self) -> None:
        _report, steps = self._steps()
        for name, subject in self.SUBJECTS.items():
            self.assertIn(subject, steps[name]["prompt"], name)

    def test_and_not_the_others(self) -> None:
        """The point of asking separately.

        One prompt holding every question is one act of attention divided
        that many ways, and the context of the others is noise to whichever
        is being answered.
        """
        _report, steps = self._steps()
        self.assertNotIn(self.SUBJECTS["step_nature"],
                         steps["step_state"]["prompt"])
        self.assertNotIn(self.SUBJECTS["step_state"],
                         steps["step_nature"]["prompt"])

    def test_a_later_step_is_told_what_the_earlier_ones_concluded(self) -> None:
        """What the single call could not do at all."""
        _report, steps = self._steps()
        # Given as findings rather than as the output of numbered steps:
        # what was established is usable, and which step established it is
        # not something a call that cannot see the list can act on.
        # And as material handed over rather than as something recalled:
        # every step is its own call, and one told "you already concluded"
        # is being told it remembers a conversation it was not in.
        self.assertNotIn("ALREADY BEEN WORKED OUT",
                         steps["step_state"]["prompt"])
        for name in ("step_nature",):
            self.assertIn("WHAT HAS ALREADY BEEN WORKED OUT FROM THIS "
                          "ACTIVITY", steps[name]["prompt"], name)
            self.assertNotIn("the previous step", steps[name]["prompt"], name)

    def test_the_step_after_the_first_is_given_what_it_concluded(self) -> None:
        """It loops, and a looping step was told nothing.

        The conclusions were passed as an empty list into the loop, so the
        one step that both follows another and gathers for itself was the
        only step in the run that did not know what had been decided.
        """
        _report, steps = self._steps()
        self.assertIn("WHAT HAS ALREADY BEEN WORKED OUT FROM THIS ACTIVITY",
                      steps["step_nature"]["prompt"])

    def test_the_state_step_is_told_it_is_about_the_user(self) -> None:
        """It is the user's calendar, and it said otherwise.

        The instruction invited states about the agent's own intentions -
        "what you intend to do for them is also a state" - so a calendar
        meant to describe the user's situation back to them filled up with
        the agent's plans, described as though they were the user's.
        """
        _report, steps = self._steps()
        prompt = steps["step_state"]["prompt"]
        self.assertIn("This is about the USER", prompt)
        self.assertNotIn("what you intend to do for them is also a state",
                         prompt)

    def test_and_not_to_write_a_state_that_is_already_running(self) -> None:
        """It is not shown the calendar at all, and says so.

        A reading of the evidence that starts from last hour's conclusions
        is a reading that mostly confirms them. The window is the evidence;
        the calendar is what earlier runs made of earlier windows.
        """
        _report, steps = self._steps()
        prompt = steps["step_state"]["prompt"]
        self.assertIn("COVER EVERYTHING IN THE WINDOW", prompt)
        self.assertIn("You are not shown what is already on the calendar",
                      prompt)
        # And nothing asks for an id it cannot see.
        self.assertNotIn("`continues`", prompt)
        self.assertNotIn("The states already running are shown to you",
                         prompt)

    def test_no_call_is_told_it_is_one_of_four(self) -> None:
        """A call does one job.

        Which number that job has in a list it cannot see is nothing it can
        use, and naming it invites answering the list instead of the job.
        """
        _report, steps = self._steps()
        for name, step in steps.items():
            prompt = step["prompt"]
            self.assertNotRegex(prompt, r"STEP \d OF \d", name)
            self.assertNotRegex(prompt, r"[Ss]tep \d", name)
            self.assertNotIn("four fixed steps", prompt, name)

    def test_the_raw_activity_goes_only_where_it_is_evidence(self) -> None:
        """Both questions - what the user is doing and who they are - are
        read off their activity, so both steps carry it as their floor."""
        _report, steps = self._steps()
        self.assertIn("WHAT THE USER DID", steps["step_state"]["prompt"])
        self.assertIn("WHAT THE USER DID", steps["step_nature"]["prompt"])

    def test_each_step_is_asked_for_its_own_shape_only(self) -> None:
        _report, steps = self._steps()
        self.assertIn("state_analysis", steps["step_state"]["prompt"])
        self.assertNotIn("state_analysis", steps["step_nature"]["prompt"])
        self.assertIn('"user_nature"', steps["step_nature"]["prompt"])

    def test_a_model_that_is_away_stops_the_run_rather_than_failing_four_times(self) -> None:
        """Asking three more times is three more failures and the same answer."""
        calls = []
        original = self.agent.app.models.invoke

        def refuse(request, role=""):
            calls.append(role)
            raise self._away()

        self.agent.app.models.invoke = refuse
        try:
            report = self.heartbeat.run_once(trigger="manual_debug")
        finally:
            self.agent.app.models.invoke = original
        self.assertTrue(report.get("blocked"))
        self.assertEqual(len(calls), 1, "asked once, not four times")

    def test_a_blocked_run_still_shows_what_every_step_gathered(self) -> None:
        def refuse(request, role=""):
            raise self._away()

        self.agent.app.models.invoke = refuse
        report = self.heartbeat.run_once(trigger="manual_debug")
        self.assertEqual(len(report.get("steps", [])), 4)
        self.assertTrue(all("trail" in step for step in report["steps"]))

    def test_the_polled_status_does_not_carry_the_prompts(self) -> None:
        """Four prompts and four answers, every few seconds, to a panel that
        wants to know a run happened."""
        report, _steps = self._steps()
        light = self.heartbeat.last_report
        self.assertTrue(any(step.get("prompt") for step in report["steps"]))
        for step in light["steps"]:
            self.assertNotIn("prompt", step)
            self.assertNotIn("answer", step)
            self.assertNotIn("context", step)
            self.assertIn("title", step)

    def test_what_a_step_concluded_is_a_line_not_its_whole_answer(self) -> None:
        # A later step given everything an earlier one returned is reading a
        # second prompt's worth of JSON to find one fact.
        said = self.heartbeat._what_it_concluded(
            "step_state", {"state_analysis": "Working on the listener.",
                           "states": [{"title": "Debugging"}]})
        self.assertIn("Debugging", said)
        self.assertLess(len(said), 900)

    def test_the_whole_instruction_is_still_readable_in_one_place(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import (
            PREAMBLE, STEP_PROMPTS, SYSTEM_PROMPT)

        self.assertIn(PREAMBLE, SYSTEM_PROMPT)
        for shape in STEP_PROMPTS.values():
            self.assertIn(shape["instruction"], SYSTEM_PROMPT)
            self.assertIn(shape["returns"], SYSTEM_PROMPT)


class AStateSaysWhyTheAgentBelievesItTests(unittest.TestCase):
    """Section 52.7: the reason was being written where nobody reads it.

    Every state the heartbeat wrote logged "no reason recorded for state ...
    created by agent" and stored the sentence that says it is a fault in
    whatever created it. The model *had* given a reason - the prompt asks for
    "how that evidence gets you to this state" and it answered - and the
    answer went into metadata, beside the evidence, rather than into the
    field that the calendar, the API and the Debugger all read.

    Read back afterwards, "no reason recorded" and "there was no reason" are
    the same sentence, which is exactly the confusion section 52.7 exists to
    prevent.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _write(self, **extra):
        entry = {"title": "Debugging the listener",
                 "detail": "Working through the filesystem provider.",
                 "horizon": "short",
                 "evidence": ["filesystem.py modified at 09:30"],
                 "reasoning": "the file was edited repeatedly this morning"}
        entry.update(extra)
        written = self.heartbeat._write_states("task_1", [entry])
        self.assertTrue(written, "the state was written")
        return self.agent.app.states.get(written[0]["id"])

    def test_the_reasoning_the_model_gave_is_the_reason(self) -> None:
        state = self._write()
        self.assertEqual(state.reason,
                         "the file was edited repeatedly this morning")

    def test_it_is_not_only_in_the_metadata(self) -> None:
        # Where it was, and where nothing that shows a state to the user looks.
        from personalized_agent.common.reason import NOT_RECORDED

        state = self._write()
        self.assertNotEqual(state.reason, NOT_RECORDED)

    def test_the_evidence_is_still_kept_beside_it(self) -> None:
        state = self._write()
        self.assertEqual(state.metadata.get("evidence"),
                         ["filesystem.py modified at 09:30"])

    def test_a_state_the_model_gave_no_reasoning_for_still_says_so(self) -> None:
        """The warning is right when there genuinely is nothing.

        It was wrong only because it fired on every state, including the ones
        the model had explained.
        """
        from personalized_agent.common.reason import NOT_RECORDED

        state = self._write(reasoning="")
        self.assertEqual(state.reason, NOT_RECORDED)


class TheLastRunSurvivesARefreshTests(unittest.TestCase):
    """Section 37, as amended: a panel that empties itself when reloaded.

    The run was held in the page. Reloading it threw away the only copy that
    still had the prompts and the gathered text in it - the thing the reader
    had opened the panel to look at - and the button had to be pressed again,
    which is four model calls to see what was already known.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_it_is_still_there_when_the_run_has_finished(self) -> None:
        self.heartbeat.run_once(trigger="manual_debug")
        state = self.heartbeat.progress()
        self.assertFalse(state["running"])
        self.assertEqual(len(state["steps"]), 4)

    def test_whole_with_what_each_step_was_given(self) -> None:
        self.heartbeat.run_once(trigger="manual_debug")
        state = self.heartbeat.progress()
        self.assertTrue(any(step.get("context") for step in state["steps"]))
        self.assertTrue(any(step.get("prompt") for step in state["steps"]))

    def test_before_anything_has_run_it_says_so_rather_than_failing(self) -> None:
        self.assertEqual(self.heartbeat.progress().get("running"), False)
        self.assertEqual(self.heartbeat.progress().get("steps", []), [])

    def test_the_polled_status_is_still_the_light_one(self) -> None:
        # Two readers, two needs: whoever asked for the run wants all of it,
        # and the status polled every few seconds does not.
        self.heartbeat.run_once(trigger="manual_debug")
        light = self.heartbeat.last_report
        for step in light["steps"]:
            self.assertNotIn("context", step)
        self.assertTrue(any(step.get("context")
                            for step in self.heartbeat.progress()["steps"]))


class TheStateStepDecidesWhatItNeedsTests(unittest.TestCase):
    """Section 37, as amended: step 1 gathers for itself.

    What to search the library for is the answer to the question step 1 is
    being asked, and it was being guessed beforehand by code that read the
    activity for names and searched for those - a fixed share of a fixed
    budget per folder, decided before anything was known about the question.

    So the step starts from what is happening - the last few minutes and the
    states running now - and asks for whatever else it decides it wants.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat
        self.asked = []

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _model(self, *rounds):
        """A model that makes the given calls, then answers."""
        from personalized_agent.models.base import ModelResponse, ToolCall

        replies = list(rounds)
        asked = self.asked

        def invoke(_self, request, **_kw):
            asked.append(request)
            if not request.tools or not replies:
                return ModelResponse(text=json.dumps(
                    {"state_analysis": "working", "states": []}))
            calls = replies.pop(0)
            return ModelResponse(
                text="", tool_calls=[
                    ToolCall(id=f"c{i}", name=name, arguments=args)
                    for i, (name, args) in enumerate(calls)])

        self.heartbeat.models = type("M", (), {
            "invoke": invoke,
            "resolve": lambda self, *a, **k: type("R", (), {"to_dict": lambda s: {}})(),
            "check_availability": lambda self, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda s: {}, "state": None})()})()


    def _step_one(self):
        return self.heartbeat.run_once(trigger="manual_debug")["steps"][0]

    # -- the floor ---------------------------------------------------------
    def test_it_starts_from_what_is_happening_rather_than_a_search(self) -> None:
        self._model()
        step = self._step_one()
        did = [row["did"] for row in step["trail"]]
        self.assertEqual(did[0], "floor")
        self.assertNotIn("plan", did)

    def test_the_floor_is_everything_since_the_last_review(self) -> None:
        """The window is the heartbeat's own interval.

        A review asks what is going on, and the honest window for that is
        since it last asked. A fixed five minutes on a fifteen-minute
        interval throws away two thirds of what happened, every time, and
        nothing in the answer says that it did.
        """
        self.agent.app.config.set("heartbeat_interval_seconds", 900)
        self._model()
        floor = self._step_one()["trail"][0]
        self.assertEqual(floor["minutes"], 15)
        self.assertIn("states", floor)

    def test_and_it_follows_the_interval_when_that_changes(self) -> None:
        self.agent.app.config.set("heartbeat_interval_seconds", 1800)
        self._model()
        self.assertEqual(self._step_one()["trail"][0]["minutes"], 30)

    def test_a_run_started_by_hand_uses_the_same_window(self) -> None:
        """It stands in for the run that would have happened.

        A one-off showing a different window would be showing a different
        thing from the one being debugged.
        """
        self.agent.app.config.set("heartbeat_interval_seconds", 900)
        self._model()
        self.assertEqual(self.heartbeat._window_minutes(), 15)

    def test_the_window_is_never_nothing(self) -> None:
        self.agent.app.config.set("heartbeat_interval_seconds", 0)
        self.assertGreaterEqual(self.heartbeat._window_minutes(), 1.0)

    def test_an_answer_needs_no_round_at_all(self) -> None:
        # A step that has enough says so by answering, and that is one call.
        self._model()
        self._step_one()
        self.assertEqual(len(self.asked), 4)

    # -- what it can ask for ----------------------------------------------
    def test_there_is_nothing_to_widen(self) -> None:
        """The window already covers the whole stretch since the last run.

        Widening it was a tool the step had while the floor was five
        minutes of a fifteen-minute gap. With the gap itself as the window
        there is nothing behind it that belongs to this review.
        """
        from personalized_agent.heartbeat.core.heartbeat import STATE_TOOLS

        self.assertEqual([spec.name for spec in STATE_TOOLS],
                         ["open_by_id"])


    # -- the loop ----------------------------------------------------------
    def test_the_trail_says_when_the_model_was_asked(self) -> None:
        """Otherwise the order cannot be read off it.

        `start, fold, search` looks like a search that happened before any
        call, and the only thing that explains those words rather than
        others is that a model chose them after being shown the floor.
        """
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        did = [row["did"] for row in self._step_one()["trail"]]
        self.assertEqual(did, ["floor", "fold", "asked", "open",
                               "asked", "answered"])

    def test_the_answer_on_the_trail_is_not_cut(self) -> None:
        """It was cut at four hundred characters.

        So the row meant to show what the step concluded showed the first
        sentence of it and stopped mid-word.
        """
        long_answer = ('{"state_analysis": "' + "a considered sentence. " * 60
                       + '", "states": []}')

        class Reply:
            text = long_answer

        self.heartbeat.models = type("M", (), {
            "invoke": lambda self, *a, **k: Reply(),
            "resolve": lambda self, *a, **k: type(
                "R", (), {"to_dict": lambda s: {}})(),
            "check_availability": lambda self, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda s: {},
                "state": None})()})()
        answered = [row for row in self._step_one()["trail"]
                    if row["did"] == "answered"][0]
        self.assertEqual(answered["said"], long_answer)

    def test_but_the_polled_copy_does_not_carry_it(self) -> None:
        # It is the whole answer again, on a status polled every few
        # seconds; whoever asked for the run already has it.
        self._model()
        self.heartbeat.run_once(trigger="manual_debug")
        for step in self.heartbeat.last_report["steps"]:
            for row in step.get("trail") or []:
                if row.get("did") == "answered":
                    self.assertNotIn("said", row)

    def test_and_which_round_each_was(self) -> None:
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        rounds = [row["round"] for row in self._step_one()["trail"]
                  if row["did"] in ("asked", "answered")]
        self.assertEqual(rounds, [1, 2, 2])

    def test_an_ask_carries_the_message_that_was_sent(self) -> None:
        """Not a description of it.

        "20 lines of activity, 146 states, 0 records" says how much went in
        and never what, and what was sent is the only thing that explains
        what came back.
        """
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        asked = [row for row in self._step_one()["trail"]
                 if row["did"] == "asked"]
        self.assertIn("ANALYSE THE USER'S STATE", asked[0]["sent"])
        self.assertIn("WHAT THE USER DID", asked[0]["sent"])

    def test_the_ask_carries_what_it_offered(self) -> None:
        """There is no separate call that asks whether to search.

        The message that asks for the answer carries the tools, and the
        model answers by using one or by not - so the offer is half of that
        ask. It travels in the request beside the messages rather than
        inside them, and a reader rebuilding the call from the message
        alone sees it being told it may ask for things, never what.
        """
        self._model()
        asked = [row for row in self._step_one()["trail"]
                 if row["did"] == "asked"]
        self.assertEqual([tool["name"] for tool in asked[0]["tools"]],
                         ["open_by_id"])

    def test_with_what_each_one_takes(self) -> None:
        self._model()
        tools = [row for row in self._step_one()["trail"]
                 if row["did"] == "asked"][0]["tools"]
        opening = next(t for t in tools if t["name"] == "open_by_id")
        self.assertIn("ids", opening["takes"]["properties"])
        self.assertIn("Read anything by its id", opening["description"])

    def test_and_the_message_says_it_may_decide(self) -> None:
        # The same message, because the decision and the answer are one
        # call: it either uses a tool or it does not.
        self._model()
        asked = [row for row in self._step_one()["trail"]
                 if row["did"] == "asked"]
        self.assertIn("YOU DECIDE WHAT YOU NEED TO SEE", asked[0]["sent"])

    def test_the_message_does_not_end_by_forbidding_the_tools(self) -> None:
        """It ended "Return ONLY this JSON object".

        That is a direct instruction not to call a tool, in the last and
        strongest position, contradicting the paragraph far above it that
        offers them - so the step read the nearest instruction and answered
        from its floor on the round where using a tool was the point.
        """
        self._model()
        sent = [row for row in self._step_one()["trail"]
                if row["did"] == "asked"][0]["sent"]
        ending = sent[-2000:]
        self.assertIn("CALL A TOOL", ending)
        self.assertIn("open_by_id", ending)
        self.assertNotIn("\nReturn ONLY this JSON object:", ending)

    def test_and_says_answering_is_the_other_option(self) -> None:
        self._model()
        sent = [row for row in self._step_one()["trail"]
                if row["did"] == "asked"][0]["sent"]
        self.assertIn("NOW DO ONE OF TWO THINGS", sent)
        self.assertIn("If you have enough, ANSWER", sent)

    def test_every_step_may_call_a_tool_or_answer(self) -> None:
        # Every step can look things up now - step 3 above all, which
        # should read a screen in full before disabling anything.
        self._model()
        self.heartbeat.run_once(trigger="manual_debug")
        report = self.heartbeat.progress()
        self.assertEqual(len(report["steps"]), 4)
        for step in report["steps"]:
            self.assertIn("If you have enough, ANSWER", step["prompt"])
            self.assertIn("open_by_id", step["prompt"])

    def test_and_the_standing_instruction_it_goes_with(self) -> None:
        # Sent on every round and in none of the messages, so a reader
        # reconstructing the call from the message alone is missing it.
        self._model()
        asked = [row for row in self._step_one()["trail"]
                 if row["did"] == "asked"]
        self.assertIn("background reasoning pass", asked[0]["system"])

    def test_a_later_round_carries_only_what_is_new(self) -> None:
        """The call holds every round before it as well.

        Printing the whole conversation on each row is the same text four
        times, so a row says what was added and how much it stands on.
        """
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        asked = [row for row in self._step_one()["trail"]
                 if row["did"] == "asked"]
        self.assertEqual(asked[0]["carried_over"], 0)
        self.assertEqual(asked[1]["carried_over"], 1)
        self.assertNotIn("ANALYSE THE USER'S STATE", asked[1]["sent"])
        self.assertIn("result of", asked[1]["sent"])

    def test_a_tool_result_reads_as_a_result(self) -> None:
        # Content is a string going out and a list of blocks coming back,
        # so printing it raw gives JSON with the interesting part inside.
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        asked = [row for row in self._step_one()["trail"]
                 if row["did"] == "asked"]
        self.assertIn("--- assistant ---", asked[1]["sent"])
        self.assertNotIn("tool_use_id", asked[1]["sent"])

    def test_every_size_is_counted_in_tokens(self) -> None:
        """Characters are a fact about a string.

        What is capped, paid for and worth deciding about is tokens, and
        nobody does that division in their head.
        """
        self._model()
        step = self._step_one()
        for name in ("context_tokens", "prompt_tokens"):
            self.assertIsInstance(step[name], int, name)
            self.assertGreater(step[name], 0, name)

    def test_the_polled_copy_does_not_carry_the_messages(self) -> None:
        # It is the whole prompt again, on every row, several times over.
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        self.heartbeat.run_once(trigger="manual_debug")
        for step in self.heartbeat.last_report["steps"]:
            for row in step.get("trail") or []:
                self.assertNotIn("sent", row)
                self.assertNotIn("system", row)

    def test_every_row_of_the_trail_says_what_it_cost(self) -> None:
        """A list of things that happened, with no way to weigh them.

        The folding and a model call read the same on a trail, so a reader
        looking for where a minute went had nowhere to look.
        """
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        for row in self._step_one()["trail"]:
            self.assertIn("ms", row, row["did"])
            self.assertIn("tokens", row, row["did"])

    def test_only_a_model_call_spends_tokens(self) -> None:
        # The floor is a database read, the folding is a loop and a search
        # is the index. None of them is a model, and the zero says so.
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        for row in self._step_one()["trail"]:
            if row["did"] in ("floor", "fold", "search", "open", "answered"):
                self.assertEqual(row["tokens"], 0, row["did"])

    def test_and_the_call_rows_carry_what_they_spent(self) -> None:
        from personalized_agent.models.base import (ModelResponse, ModelUsage,
                                                    ToolCall)

        replies = [[("open_by_id", {"ids": ["state_0123456789abcdef"]})]]

        def invoke(_self, request, **_kw):
            if not request.tools or not replies:
                return ModelResponse(
                    text='{"state_analysis": "x", "states": []}',
                    usage=ModelUsage(input_tokens=1500, output_tokens=40))
            calls = replies.pop(0)
            return ModelResponse(
                text="Looking that up.",
                usage=ModelUsage(input_tokens=1200, output_tokens=25),
                tool_calls=[ToolCall(id="c0", name=calls[0][0],
                                     arguments=calls[0][1])])

        self.heartbeat.models = type("M", (), {
            "invoke": invoke,
            "resolve": lambda s, *a, **k: type(
                "R", (), {"to_dict": lambda x: {}})(),
            "check_availability": lambda s, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda x: {},
                "state": None})()})()
        asked = [row for row in self._step_one()["trail"]
                 if row["did"] == "asked"]
        self.assertEqual([row["tokens"] for row in asked], [1225, 1540])


    def test_it_keeps_asking_until_it_says_it_has_enough(self) -> None:
        record_id = "rec_0f1e2d3c4b5a6978"
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})],
                    [("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        step = self._step_one()
        did = [row["did"] for row in step["trail"]]
        self.assertEqual(did, ["floor", "fold", "asked", "open",
                               "asked", "open", "asked", "answered"])
        self.assertIn("had enough", step["stopped_because"])

    def test_a_step_that_never_stops_is_stopped_and_says_so(self) -> None:
        """An answer given because the rounds ran out is not the same as one
        given because the step was satisfied, and the difference is worth
        more than the appearance of having finished."""
        self.agent.app.config.set("heartbeat_state_max_rounds", 2)
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})],
                    [("open_by_id", {"ids": ["state_0123456789abcdef"]})],
                    [("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        step = self._step_one()
        self.assertIn("still asking", step["stopped_because"])
        self.assertEqual(
            len([r for r in step["trail"] if r["did"] == "open"]), 2)

    def test_every_round_is_in_the_trail_as_it_happens(self) -> None:
        # The panel shows the step working rather than a minute of nothing.
        seen = []
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        original = self.heartbeat._run_state_tool

        def watched(floor, name, args, task_id, **kwargs):
            outcome = original(floor, name, args, task_id, **kwargs)
            seen.append(len(floor.trail))
            return outcome

        self.heartbeat._run_state_tool = watched
        self._step_one()
        # The floor, the folding and the call that asked for it are all
        # already there when the search runs.
        self.assertEqual(seen, [4])

    def test_a_model_with_no_tools_at_all_still_answers(self) -> None:
        # Not every provider has a tool protocol. The step answers from its
        # floor, which is a smaller answer than it could have given and not
        # a failure.
        class Reply:
            text = json.dumps({"state_analysis": "working", "states": []})

        self.heartbeat.models = type("M", (), {
            "invoke": lambda self, *a, **k: Reply(),
            "resolve": lambda self, *a, **k: type("R", (), {"to_dict": lambda s: {}})(),
            "check_availability": lambda self, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda s: {}, "state": None})()})()
        step = self._step_one()
        self.assertEqual(step["phase"], "answered")


class WhatWasOnTheScreenReachesTheStepTests(unittest.TestCase):
    """The step was reading application names, not what was in them.

    `content` - the reading of the window, which is what the page actually
    said - was dropped from every event on its way to the heartbeat. So a
    morning of reading a paper arrived as `firefox.exe`, and a step asked
    what the user is doing was answering from the name of the program.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _seen(self, text, **data):
        # `text`, which is what the screen reader writes. These wrote
        # `content`, the field the step read - so they passed for weeks
        # while no real reading of any window ever reached it.
        self.agent.app.raw_store.add_event(ListenerEvent(
            event_type="screen.text", source="screen", application="firefox.exe",
            data={"text": text, **data}))

    def test_each_part_of_the_context_says_what_it_is(self) -> None:
        """Labels name a section; they do not say what is in it.

        One block is an observation of the user, one is the agent's own
        earlier conclusions and one is what it looked up - read very
        differently, and headed as though they were the same kind of thing.
        """
        floor = self.heartbeat._state_floor("t")
        text = floor.context_text()
        self.assertIn("=== 1. WHAT THE USER DID", text)
        # Nothing is looked up beyond the window, so there is no such part.
        self.assertNotIn("WHAT YOU LOOKED UP", text)

    def test_and_the_numbering_has_no_gap_in_it(self) -> None:
        """The step that reads the window is given no states, so it has two
        sections and not three. A context that skips from 1 to 3 asks the
        reader - model or person - what happened to 2."""
        text = self.heartbeat._state_floor("t").context_text()
        self.assertNotIn("=== 3.", text)

    def test_the_step_that_reads_the_window_is_shown_no_states(self) -> None:
        """Not an empty section headed "what you already believe" either.

        A heading with nothing under it invites the model to account for
        the emptiness instead of reading what is in front of it.
        """
        from personalized_agent.agent.states.store import State

        self.agent.app.states.add(State(
            title="Something already running", detail="d",
            starts_at=utcnow() - timedelta(hours=1),
            ends_at=utcnow() + timedelta(hours=1)))
        floor = self.heartbeat._state_floor("t")
        self.assertEqual(floor.states, [])
        text = floor.context_text()
        self.assertNotIn("Something already running", text)
        self.assertNotIn("WHAT YOU ALREADY BELIEVE", text)

    def test_but_the_step_after_it_still_gets_its_own(self) -> None:
        """Which are not the running states either: they are what this
        review has just concluded."""
        floor = self.heartbeat._state_floor("t")
        floor.states_are = "WHAT THIS REVIEW JUST CONCLUDED"
        floor.states_about = "The states the previous step wrote."
        text = floor.context_text()
        self.assertIn("=== 2. WHAT THIS REVIEW JUST CONCLUDED", text)

    def test_the_activity_says_where_it_came_from(self) -> None:
        text = self.heartbeat._state_floor("t").context_text()
        self.assertIn("Recorded by the listener", text)
        self.assertIn("observation, not interpretation", text)

    def test_the_states_say_they_are_the_agents_own_conclusions(self) -> None:
        # Not evidence. They can be wrong, and a step that reads them as
        # observations is reasoning from its own output. Only the step that
        # is given states has the section at all.
        floor = self.heartbeat._state_floor("t")
        floor.states_are = "WHAT THIS REVIEW JUST CONCLUDED"
        floor.states_about = ("The states the previous step wrote - its "
                              "reading of what the user is doing.")
        self.assertIn("its reading of what the user is doing",
                      floor.context_text())


    def test_what_the_window_said_reaches_the_step(self) -> None:
        self._seen("A paper about retrieval-augmented generation")
        floor = self.heartbeat._state_floor("t")
        self.assertIn("retrieval-augmented generation", floor.context_text())

    def test_and_the_whole_address_rather_than_the_site(self) -> None:
        # `arxiv.org` says a paper; the rest says which.
        self._seen("A paper", url="https://arxiv.org/abs/2401.00001",
                   title="Retrieval-augmented generation")
        text = self.heartbeat._state_floor("t").context_text()
        self.assertIn("arxiv.org/abs/2401.00001", text)
        self.assertIn("Retrieval-augmented generation", text)

    def test_one_long_reading_is_cut_rather_than_carried_whole(self) -> None:
        self._seen("x" * 9000)
        floor = self.heartbeat._state_floor("t")
        # The reading is cut to its ceiling; the rest is the context's own
        # framing - the time, the interval, the key to the entries.
        self.assertLess(len(floor.context_text()),
                        self.heartbeat.READING_CEILING + 2500)

    def test_and_the_budget_reaches_every_reading(self) -> None:
        """A busy window is many readings.

        Spent newest-first, the step was shown the end of the window and
        nothing of how it got there; spent oldest-first, the reverse. Each
        reading gets an equal share instead, so the whole window is there,
        in the order it happened.
        """
        for index in range(80):
            self._seen(f"reading number {index} " + "y" * 800)
        text = self.heartbeat._state_floor("t").context_text()
        self.assertIn("reading number 0 ", text)
        self.assertIn("reading number 79 ", text)
        self.assertLess(text.index("reading number 0 "),
                        text.index("reading number 79 "))

    def test_a_reading_that_was_shortened_says_so(self) -> None:
        # Rather than looking like all there was.
        for index in range(80):
            self._seen(f"reading {index} " + "z" * 800)
        text = self.heartbeat._state_floor("t").context_text()
        self.assertIn("…", text)


class TheToolsAreWhatAProviderReadsTests(unittest.TestCase):
    """They were dictionaries that looked like tool specifications.

    Every test of the loop stubs `invoke`, so nothing on the way to a model
    ever read them - and a provider does, by attribute: `spec.name` for the
    list it writes into the prompt. The first real run ended in
    `'dict' object has no attribute 'name'`, with the tests green.
    """

    def test_they_are_the_type_the_model_layer_declares(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import STATE_TOOLS
        from personalized_agent.models.base import ToolSpec

        self.assertTrue(STATE_TOOLS)
        for spec in STATE_TOOLS:
            self.assertIsInstance(spec, ToolSpec)

    def test_a_provider_can_describe_them(self) -> None:
        # What every text-protocol provider does with request.tools, which
        # is the line that failed.
        from personalized_agent.heartbeat.core.heartbeat import STATE_TOOLS

        described = "\n".join(f"- {spec.name}: {spec.description}"
                              for spec in STATE_TOOLS)
        self.assertIn("open_by_id", described)
        self.assertNotIn("search_memory", described)

    def test_and_the_ones_with_a_schema_can_be_sent(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import STATE_TOOLS

        for spec in STATE_TOOLS:
            sent = spec.to_dict()
            self.assertEqual(sent["name"], spec.name)
            self.assertEqual(sent["input_schema"]["type"], "object")


    def test_the_step_is_offered_exactly_these(self) -> None:
        """And the request carries them, rather than something like them."""
        from personalized_agent.heartbeat.core.heartbeat import STATE_TOOLS

        agent = TemporaryAgent()
        try:
            seen = []

            def invoke(_self, request, **_kw):
                seen.append(request.tools)

                class Reply:
                    text = json.dumps({"state_analysis": "", "states": []})
                return Reply()

            agent.app.heartbeat.models = type("M", (), {
                "invoke": invoke,
                "resolve": lambda self, *a, **k: type(
                    "R", (), {"to_dict": lambda s: {}})(),
                "check_availability": lambda self, *a, **k: type("A", (), {
                    "available": True, "to_dict": lambda s: {},
                    "state": None})()})()
            agent.app.heartbeat.run_once(trigger="manual_debug")
            # Every step gathers for itself now, so every step is offered
            # them - and offered the same two, rather than something like
            # them assembled per step.
            self.assertEqual(len(seen), 4)
            for tools in seen:
                self.assertEqual(tools, STATE_TOOLS)
        finally:
            agent.cleanup()


class TheLoopBuildsAConversationAProviderAcceptsTests(unittest.TestCase):
    """The tests all stand in for the model, so none of them reach one.

    That is what let step 1 ship with its tools as plain dictionaries - a
    provider reads `spec.name` and every test stubbed `invoke` - and the
    same blindness covers the shape of the messages. Roles have to
    alternate: `request.messages` goes to the API as written.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat
        self.sent = []

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _model(self, *rounds):
        from personalized_agent.models.base import ModelResponse, ToolCall

        replies = list(rounds)
        sent = self.sent

        def invoke(_self, request, **_kw):
            sent.append(list(request.messages))
            if not request.tools or not replies:
                return ModelResponse(text=json.dumps(
                    {"state_analysis": "working", "states": []}))
            calls = replies.pop(0)
            return ModelResponse(text="", tool_calls=[
                ToolCall(id=f"c{i}", name=name, arguments=args)
                for i, (name, args) in enumerate(calls)])

        self.heartbeat.models = type("M", (), {
            "invoke": invoke,
            "resolve": lambda self, *a, **k: type(
                "R", (), {"to_dict": lambda s: {}})(),
            "check_availability": lambda self, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda s: {}, "state": None})()})()

    def test_the_roles_alternate(self) -> None:
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})],
                    [("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        self.heartbeat.run_once(trigger="manual_debug")
        last = self.sent[2]          # the third round of step 1
        roles = [m["role"] for m in last]
        self.assertEqual(roles, ["user", "assistant", "user",
                                 "assistant", "user"])

    def test_it_starts_with_the_user(self) -> None:
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        self.heartbeat.run_once(trigger="manual_debug")
        self.assertEqual(self.sent[1][0]["role"], "user")

    def test_a_round_answers_every_call_it_was_given(self) -> None:
        """One tool_result per tool_use, or the API refuses the turn."""
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]}),
                     ("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        self.heartbeat.run_once(trigger="manual_debug")
        blocks = self.sent[1][-1]["content"]
        results = [b for b in blocks if b.get("type") == "tool_result"]
        self.assertEqual([b["tool_use_id"] for b in results], ["c0", "c1"])

    def test_and_what_it_now_holds_travels_in_that_same_turn(self) -> None:
        # Not as a second user message after it.
        self._model([("open_by_id", {"ids": ["state_0123456789abcdef"]})])
        self.heartbeat.run_once(trigger="manual_debug")
        blocks = self.sent[1][-1]["content"]
        text = [b for b in blocks if b.get("type") == "text"]
        self.assertEqual(len(text), 1)
        self.assertIn("Your context now holds", text[0]["text"])


class TheSameThingFortyTimesIsOneLineTests(unittest.TestCase):
    """Structure the model would have to infer, worked out where it is free.

    An editor saving on a timer writes the same event forty times in a
    quarter of an hour; a window read every fifteen seconds writes sixty.
    None of that is forty or sixty facts, and the step was paying for every
    copy - measured, 6,556 tokens for a window that folds to 606.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat
        self.agent.app.config.set("heartbeat_interval_seconds", 900)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _saved(self, times, path="C:/work/search.py"):
        for _ in range(times):
            self.agent.app.raw_store.add_event(ListenerEvent(
                event_type="filesystem.modified", source="filesystem",
                application="sublime_text.exe", data={"path": path}))

    def _read(self, texts, title="A paper", url="https://arxiv.org/abs/1"):
        for text in texts:
            self.agent.app.raw_store.add_event(ListenerEvent(
                event_type="screen.text", source="screen",
                application="firefox.exe",
                data={"url": url, "title": title, "text": text}))

    def test_the_same_event_at_different_times_is_one_line(self) -> None:
        self._saved(40)
        floor = self.heartbeat._state_floor("t")
        self.assertEqual(len(floor.events), 1)
        self.assertEqual(floor.events[0]["count"], 40)

    def test_and_it_says_how_many_there_were(self) -> None:
        self._saved(40)
        text = self.heartbeat._state_floor("t").context_text()
        self.assertIn("x40", text)

    def test_and_when_each_of_them_happened(self) -> None:
        """Each time, not the stretch they span.

        "x3 over 02:20-02:21" is the same string for three clicks in four
        seconds and three visits over a minute, and those are a double-click
        and a person going back and forth.
        """
        self._saved(4)
        row = self.heartbeat._state_floor("t").trail[0]
        self.assertEqual(len(row["events"][0]["at_times"]), 4)
        self.assertRegex(row["events"][0]["at_times"][0], r"^\d\d:\d\d:\d\d$")

    def test_and_a_long_group_lists_some_and_counts_the_rest(self) -> None:
        self._saved(40)
        event = self.heartbeat._state_floor("t").trail[0]["events"][0]
        self.assertEqual(len(event["at_times"]), 12)
        self.assertEqual(event["times_left_out"], 28)

    def test_the_text_lists_them_too(self) -> None:
        self._saved(3)
        text = self.heartbeat._state_floor("t").context_text()
        self.assertIn("at ", text)
        # Each time, not a range - on the entry's own line, since the
        # context opens with a time and its offset ("20:19:28+01:00").
        entry = [line for line in text.splitlines() if line.startswith("x3")][0]
        self.assertNotRegex(entry, r"\d\d:\d\d-\d\d:\d\d")

    def test_different_files_do_not_merge(self) -> None:
        self._saved(5, "C:/work/search.py")
        self._saved(5, "C:/work/privacy.py")
        floor = self.heartbeat._state_floor("t")
        self.assertEqual(len(floor.events), 2)

    def test_nor_do_different_applications(self) -> None:
        self._saved(5)
        self._read(["a page"])
        self.assertEqual(len(self.heartbeat._state_floor("t").events), 2)

    def test_a_single_event_is_unchanged(self) -> None:
        self._saved(1)
        floor = self.heartbeat._state_floor("t")
        self.assertEqual(floor.events[0]["count"], 1)
        self.assertNotIn("x1", floor.context_text())

    def test_readings_of_one_page_keep_what_changed(self) -> None:
        """A page that was scrolled is not a page that was stared at.

        Folding that kept only the last would make those two identical,
        which is the one thing the readings are evidence about.
        """
        self._read([f"paragraph {i}" for i in range(6)])
        text = self.heartbeat._state_floor("t").context_text()
        self.assertEqual(text.count("read at "), 6)

    def test_and_all_of_them(self) -> None:
        """Each reading of a window is what was new on it, so a cap of three
        kept the end of the window and dropped the rest of it."""
        self._read([f"paragraph {i}" for i in range(20)])
        floor = self.heartbeat._state_floor("t")
        kept = floor.events[0]
        self.assertEqual(len(kept["data"].get("reads") or []), 20)
        self.assertNotIn("not shown", floor.context_text())

    def test_identical_readings_are_not_kept_twice(self) -> None:
        self._read(["the same paragraph"] * 10)
        floor = self.heartbeat._state_floor("t")
        self.assertEqual(floor.events[0]["data"].get("also_read"), None)

    def test_the_step_is_told_that_it_was_folded(self) -> None:
        """How busy the window was is part of the answer it is being asked for.

        A step told "2 events" that is looking at a fold of a hundred has
        been given the wrong idea of the afternoon.
        """
        self._saved(40)
        text = self.heartbeat._state_floor("t").context_text()
        self.assertIn("40 events", text)
        self.assertIn("merged into one line with a count", text)

    def test_and_the_trail_reports_both_numbers(self) -> None:
        # Folding that says only what was sent is a compression nobody can
        # check, and the ratio is the thing worth looking at.
        self._saved(40)
        row = self.heartbeat._state_floor("t").trail[0]
        self.assertEqual(row["events_seen"], 40)
        self.assertEqual(row["events_count"], 1)
        self.assertEqual(row["events_folded"], 39)

    def test_a_spinner_in_a_title_is_not_a_second_window(self) -> None:
        """Seen in the live feed, fifty seconds apart.

        One terminal, one session, two entries identical but for the
        character at the front: a spinner is a window saying it is still
        working, which is the one thing about it not worth a second row.
        """
        for glyph in "\u25d0\u2733\u25d0\u2733":
            self.agent.app.raw_store.add_event(ListenerEvent(
                event_type="screen.text", source="screen",
                application="WindowsTerminal.exe",
                data={"window_title": f"{glyph} Upload instruction"}))
        floor = self.heartbeat._state_floor("t")
        self.assertEqual(len(floor.events), 1)
        self.assertEqual(floor.events[0]["count"], 4)

    def test_nor_are_the_counters_that_go_with_them(self) -> None:
        for seconds in (7, 42, 105):
            self.agent.app.raw_store.add_event(ListenerEvent(
                event_type="screen.text", source="screen",
                application="WindowsTerminal.exe",
                data={"window_title": f"Claude Code ({seconds}s)"}))
        self.assertEqual(len(self.heartbeat._state_floor("t").events), 1)

    def test_but_a_different_window_is_still_a_different_window(self) -> None:
        """The same run held both, and only one pair was the same thing."""
        for title in ("\u25d0 Upload instruction",
                      "\u2733 Upload instruction",
                      "C:\\WINDOWS\\system32\\cmd.exe - python run.py"):
            self.agent.app.raw_store.add_event(ListenerEvent(
                event_type="screen.text", source="screen",
                application="WindowsTerminal.exe",
                data={"window_title": title}))
        self.assertEqual(len(self.heartbeat._state_floor("t").events), 2)

    def test_and_numbers_in_a_name_are_left_alone(self) -> None:
        # Stripping digits generally would merge "Chapter 3" into
        # "Chapter 4", which are two files and two afternoons.
        for chapter in (3, 4):
            self.agent.app.raw_store.add_event(ListenerEvent(
                event_type="screen.text", source="screen",
                application="WINWORD.EXE",
                data={"window_title": f"Chapter {chapter} - thesis.docx"}))
        self.assertEqual(len(self.heartbeat._state_floor("t").events), 2)

    def test_the_title_kept_is_the_one_that_was_seen(self) -> None:
        # Normalised for deciding what is the same thing, not for showing:
        # the step is told what the window actually said.
        for glyph in "\u25d0\u2733":
            self.agent.app.raw_store.add_event(ListenerEvent(
                event_type="screen.text", source="screen",
                application="WindowsTerminal.exe",
                data={"window_title": f"{glyph} Upload instruction"}))
        text = self.heartbeat._state_floor("t").context_text()
        self.assertIn("Upload instruction", text)
        self.assertRegex(text, r"[\u25d0\u2733] Upload instruction")

    def test_folding_is_a_step_of_its_own(self) -> None:
        self._saved(40)
        rows = [r for r in self.heartbeat._state_floor("t").trail
                if r["did"] == "fold"]
        self.assertEqual(len(rows), 1)

    def test_and_it_says_it_was_code_rather_than_a_model(self) -> None:
        """Every other row in this trail is somebody choosing something.

        A transform that runs the same way every time is a different kind
        of claim, and lumping it under either of the others misreports it.
        """
        self._saved(40)
        row = [r for r in self.heartbeat._state_floor("t").trail
               if r["did"] == "fold"][0]
        self.assertEqual(row["decided_by"], "code")

    def test_the_floor_says_it_was_fixed_and_names_the_setting(self) -> None:
        """Three kinds of row, told apart by when they were decided.

        The model chose, during this run; a setting decided it before the
        run; or code did it with no choice in it. "The agent" named the
        thing all three have in common - the model is part of the agent,
        and so is the code - so the label distinguished nothing, and gave
        a reader who thought the window was wrong nowhere to go.
        """
        self._saved(1)
        floor = [r for r in self.heartbeat._state_floor("t").trail
                 if r["did"] == "floor"][0]
        self.assertEqual(floor["decided_by"], "fixed")
        self.assertEqual(floor["setting"], "heartbeat_interval_seconds")

    def test_no_model_is_asked_anything_to_fold(self) -> None:
        # The claim, tested rather than asserted: building a floor makes no
        # model call at all.
        asked = []
        self.heartbeat.models = type("M", (), {
            "invoke": lambda self, *a, **k: asked.append(1),
            "resolve": lambda self, *a, **k: type(
                "R", (), {"to_dict": lambda s: {}})(),
            "check_availability": lambda self, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda s: {},
                "state": None})()})()
        self._saved(40)
        self._read([f"reading {i}" for i in range(6)])
        self.heartbeat._state_floor("t")
        self.assertEqual(asked, [])

    def test_the_input_is_shown_on_both_sides_of_it(self) -> None:
        """A compression that reports its own output cannot be checked."""
        self._saved(40)
        row = [r for r in self.heartbeat._state_floor("t").trail
               if r["did"] == "fold"][0]
        self.assertIn("filesystem.modified", row["before_text"])
        self.assertIn("x40", row["after_text"])
        self.assertNotIn("x40", row["before_text"])

    def test_and_what_it_cost_on_each_side(self) -> None:
        self._saved(40)
        row = [r for r in self.heartbeat._state_floor("t").trail
               if r["did"] == "fold"][0]
        self.assertEqual(row["before_events"], 40)
        self.assertEqual(row["after_events"], 1)
        self.assertGreater(row["before_tokens"], row["after_tokens"])

    def test_the_two_sides_are_the_same_rendering(self) -> None:
        """Or the comparison measures the rendering, not the folding."""
        self._read([f"reading {i}: a paragraph" for i in range(4)])
        row = [r for r in self.heartbeat._state_floor("t").trail
               if r["did"] == "fold"][0]
        for side in ("before_text", "after_text"):
            self.assertIn("title:", row[side])
            self.assertIn("url:", row[side])

    def test_a_window_with_nothing_to_fold_says_so_plainly(self) -> None:
        self._saved(1)
        row = [r for r in self.heartbeat._state_floor("t").trail
               if r["did"] == "fold"][0]
        self.assertEqual(row["before_events"], row["after_events"])

    def test_the_polled_copy_does_not_carry_either_side(self) -> None:
        # It is the whole feed twice, and the status is polled every few
        # seconds by the interface.
        self._saved(40)
        self.heartbeat.run_once(trigger="manual_debug")
        for step in self.heartbeat.last_report["steps"]:
            for row in step.get("trail") or []:
                self.assertNotIn("before_text", row)
                self.assertNotIn("after_text", row)

    def test_but_whoever_asked_for_the_run_gets_them(self) -> None:
        self._saved(40)
        self.heartbeat.run_once(trigger="manual_debug")
        rows = [r for step in self.heartbeat.progress()["steps"]
                for r in step.get("trail") or [] if r["did"] == "fold"]
        self.assertTrue(rows)
        self.assertTrue(rows[0]["before_text"])

    def test_it_is_worth_doing(self) -> None:
        """The measurement, kept as a test so it stays true."""
        from personalized_agent.data_manage.text import estimate_tokens

        # Forty saves of one file are one line...
        self._saved(40)
        saves = estimate_tokens(self.heartbeat._state_floor("t").context_text())
        self.assertLess(saves, 1500)
        # ...and readings, which are each different, are bounded by the
        # budget rather than by a count.
        self._read([f"reading {i} " + "paper text " * 60 for i in range(60)])
        both = estimate_tokens(self.heartbeat._state_floor("t").context_text())
        self.assertLess(both, saves + estimate_tokens(
            "x" * self.heartbeat.CONTENT_BUDGET) + 1500)


class TheDebuggerRunsTheRealHeartbeatTests(unittest.TestCase):
    """The panel is only worth having if it shows the thing itself.

    A debug path that assembled its own context, or asked its own prompts,
    would be a panel describing a heartbeat rather than showing one - and
    it would drift, quietly, in exactly the direction that makes it look
    fine.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_both_triggers_reach_the_same_method(self) -> None:
        import inspect
        from personalized_agent.api import routes

        source = inspect.getsource(routes)
        self.assertIn('heartbeat.run_once(trigger="manual_debug")', source)
        # The timer's run goes through _run_marked, which notes it under way
        # and tried - and reaches the same method.
        loop = inspect.getsource(type(self.heartbeat)._loop)
        self.assertIn("self._run_marked()", loop)
        marked = inspect.getsource(type(self.heartbeat)._run_marked)
        self.assertIn('run_once(trigger="timer")', marked)

    def test_and_the_trigger_only_labels_the_run(self) -> None:
        """Nothing branches on it.

        It is checked once - only the timer and the debugger may start a
        run, section 37 - and then written down. If it ever selects
        behaviour, the panel stops being evidence about the timer's runs.
        """
        import inspect

        source = inspect.getsource(type(self.heartbeat).run_once)
        for line in source.splitlines():
            if "trigger" not in line:
                continue
            body = line.strip()
            if body.startswith("#") or body.startswith("if trigger not in"):
                continue
            self.assertNotRegex(
                body, r"if .*trigger\s*==|if .*trigger\s*!=",
                f"run_once branches on the trigger: {body}")

    def test_the_two_runs_gather_the_same_way(self) -> None:
        windows = []
        original = self.heartbeat._state_floor

        def watched(task_id):
            floor = original(task_id)
            windows.append(floor.minutes)
            return floor

        self.heartbeat._state_floor = watched
        self.heartbeat.run_once(trigger="manual_debug")
        self.heartbeat.run_once(trigger="timer")
        # One window per run, read once and given to every step - the
        # same stretch for all of them.
        self.assertEqual(len(windows), 2)
        # The second goes on from where the first got to - the run by hand
        # stands in for the one that would have happened, so what it read
        # is not read again - which is moments ago: the shortest window.
        self.assertEqual(windows[1], 1.0)

    def test_and_produce_the_same_shape_of_report(self) -> None:
        by_hand = self.heartbeat.run_once(trigger="manual_debug")
        by_timer = self.heartbeat.run_once(trigger="timer")
        self.assertEqual(sorted(by_hand), sorted(by_timer))
        self.assertEqual([s["id"] for s in by_hand["steps"]],
                         [s["id"] for s in by_timer["steps"]])
        self.assertEqual(by_hand["trigger"], "manual_debug")
        self.assertEqual(by_timer["trigger"], "timer")


class WhatAStepCouldHaveDoneTests(unittest.TestCase):
    """An outcome shows what happened, and cannot be read for what did not.

    "Nothing" covers a step that decided against, a step with nothing to
    decide about, and a step that was never able to do the thing the reader
    had in mind. The third is the common one: somebody looking at step one
    for a scheduled task is looking at the wrong step, and an empty outcome
    does not tell them so.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _steps(self, payload=None):
        if payload is not None:
            class Reply:
                text = json.dumps(payload)
            self.heartbeat.models = type("M", (), {
                "invoke": lambda self, *a, **k: Reply(),
                "resolve": lambda self, *a, **k: type(
                    "R", (), {"to_dict": lambda s: {}})(),
                "check_availability": lambda self, *a, **k: type("A", (), {
                    "available": True, "to_dict": lambda s: {},
                    "state": None})()})()
        report = self.heartbeat.run_once(trigger="manual_debug")
        return {step["id"]: step for step in report["steps"]}

    def test_every_step_lists_what_it_is_able_to_do(self) -> None:
        steps = self._steps()
        for name in ("step_state", "step_nature"):
            self.assertTrue(steps[name]["could_do"], name)

    def test_the_state_step_can_only_write_states(self) -> None:
        """The question the user asked, answered on the step itself.

        Scheduling and notifying are another step's, and an empty outcome
        on this one has been read as "it decided not to schedule".
        """
        step = self._steps()["step_state"]
        self.assertEqual([row["what"] for row in step["could_do"]],
                         ["write a state on the calendar"])
        self.assertIn("notifications are step three", step["cannot"])

    def test_and_says_none_rather_than_leaving_it_out(self) -> None:
        step = self._steps({"states": [], "actions": []})["step_state"]
        self.assertEqual(step["could_do"][0]["count"], 0)

    def test_a_state_it_wrote_is_counted(self) -> None:
        step = self._steps({"states": [
            {"title": "Debugging the listener", "detail": "what it was",
             "horizon": "short", "about": "user"}]})["step_state"]
        self.assertEqual(step["could_do"][0]["count"], 1)

    def test_nothing_means_something_different_for_each_step(self) -> None:
        """One sentence was shown for every step.

        It was about the cost of interrupting, so a step that writes states
        explained itself by the cost of a notification it is not able to
        send.
        """
        steps = self._steps({"states": [], "actions": []})
        self.assertIn("No state was written", steps["step_state"]["nothing"])
        self.assertIn("written to memory", steps["step_nature"]["nothing"])

    def test_and_only_the_step_that_interrupts_talks_about_interrupting(self) -> None:
        steps = self._steps({"states": [], "actions": []})
        for name in ("step_state", "step_nature"):
            self.assertNotIn("interrupting", steps[name]["nothing"], name)

class WhatEachStepCostTests(unittest.TestCase):
    """The header read "1 memories, 41 ms", and 41 ms was the gathering.

    That is the cheap half. The minute somebody waits through is the model
    calls, and it was reported nowhere - nor was what the step spent, which
    is the number anybody deciding about the interval actually needs. A
    total for the run says a heartbeat is expensive and not which part.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _steps(self):
        report = self.heartbeat.run_once(trigger="manual_debug")
        return {step["id"]: step for step in report["steps"]}

    def test_every_step_says_how_many_tokens_it_spent(self) -> None:
        for name, step in self._steps().items():
            self.assertGreater(step["tokens_in"], 0, name)
            self.assertGreater(step["tokens_out"], 0, name)
            self.assertEqual(step["tokens_total"],
                             step["tokens_in"] + step["tokens_out"], name)

    def test_and_how_many_calls_it_made(self) -> None:
        for name, step in self._steps().items():
            self.assertGreaterEqual(step["calls"], 1, name)

    def test_and_how_long_the_whole_step_took(self) -> None:
        """Not just the gathering.

        They are different costs with different remedies, so both are
        kept: the gathering is milliseconds and the calls are the minute.
        """
        for name, step in self._steps().items():
            self.assertIn("elapsed_ms", step, name)
            self.assertIn("duration_ms", step, name)

    def test_the_state_step_counts_every_round(self) -> None:
        """It makes more than one call, and each carries the last again.

        Its cost is not one message but the sum of several growing ones,
        and charging it once would report the first and call it the total.
        """
        from personalized_agent.models.base import ModelResponse, ToolCall

        replies = [[("open_by_id", {"ids": ["state_0123456789abcdef"]})]]

        def invoke(_self, request, **_kw):
            if not request.tools or not replies:
                return ModelResponse(text='{"state_analysis": "x", "states": []}')
            calls = replies.pop(0)
            return ModelResponse(text="Looking that up.", tool_calls=[
                ToolCall(id="c0", name=calls[0][0], arguments=calls[0][1])])

        self.heartbeat.models = type("M", (), {
            "invoke": invoke,
            "resolve": lambda s, *a, **k: type(
                "R", (), {"to_dict": lambda x: {}})(),
            "check_availability": lambda s, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda x: {},
                "state": None})()})()
        self.assertEqual(self._steps()["step_state"]["calls"], 2)

    def test_a_blocked_step_is_still_timed(self) -> None:
        # A step that waited thirty seconds for a refusal took thirty
        # seconds, and a blank there reads as one that cost nothing.
        steps = self._steps()
        self.assertIsInstance(steps["step_state"]["elapsed_ms"], int)


class SomethingStillGoingIsSaidAgainTests(unittest.TestCase):
    """Two instructions that pulled against each other.

    "Write several states" and "do not write one that is already there",
    read together, mean "say nothing about anything you are already
    tracking" - so a window where the user carried on with three things
    they were already doing returned one state, for the one new thing.
    Seen in a real run: the analysis named four activities and wrote one,
    explaining that the other three were already on the calendar.

    Leaving them out is the thing that loses information: the calendar
    then says they ended when they did not, and the window says they never
    happened.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _running(self):
        from personalized_agent.agent.states.store import State

        return self.agent.app.states.add(State(
            title="They are getting the release out.",
            detail="The release branch.",
            starts_at=datetime.now(timezone.utc) - timedelta(hours=2),
            ends_at=datetime.now(timezone.utc) + timedelta(hours=1)))

    def _run(self, states):
        class Reply:
            text = json.dumps({"states": states})

        self.heartbeat.models = type("M", (), {
            "invoke": lambda s, *a, **k: Reply(),
            "resolve": lambda s, *a, **k: type(
                "R", (), {"to_dict": lambda x: {}})(),
            "check_availability": lambda s, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda x: {},
                "state": None})()})()
        return self.heartbeat.run_once(trigger="manual_debug")

    def test_a_continuation_does_not_become_a_second_state(self) -> None:
        running = self._running()
        self._run([{"title": "They are still getting the release out.",
                    "detail": "Same work.", "continues": running.id,
                    "minutes": 12}])
        titles = [s.title for s in self.agent.app.states.recent(limit=10)]
        self.assertEqual(titles.count("They are getting the release out."), 1)
        self.assertEqual(len(titles), 1)

    def test_but_it_moves_the_end_of_the_one_it_continues(self) -> None:
        running = self._running()
        later = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        self._run([{"title": "Still going.", "detail": "Same work.",
                    "continues": running.id,
                    "starts_at": running.starts_at.isoformat(),
                    "ends_at": later}])
        moved = self.agent.app.states.get(running.id)
        self.assertGreater(moved.ends_at, running.ends_at)

    def test_and_is_not_counted_as_an_extension(self) -> None:
        """Every review sees everything on the calendar.

        Counting those as extensions would make "extended eleven times"
        mean "the agent looked eleven times", and the signal that exists
        to find a state nobody ever finishes is gone.
        """
        running = self._running()
        self._run([{"title": "Still going.", "detail": "Same work.",
                    "continues": running.id}])
        self.assertEqual(self.agent.app.states.get(running.id).extensions, 0)

    def test_and_the_sighting_is_recorded(self) -> None:
        running = self._running()
        self._run([{"title": "They are still on the release.",
                    "detail": "Same work.", "continues": running.id}])
        seen = self.agent.app.states.get(running.id).metadata.get("seen_again")
        self.assertIn("They are still on the release.", seen)

    def test_the_run_says_which_were_continuations(self) -> None:
        running = self._running()
        report = self._run([
            {"title": "Still going.", "detail": "Same.", "continues": running.id},
            {"title": "They are also watching videos.", "detail": "New."}])
        marks = [entry.get("continued", False)
                 for entry in report["states_written"]]
        self.assertEqual(marks, [True, False])

    def test_an_id_that_does_not_exist_is_written_as_new(self) -> None:
        # Rather than dropped, which would lose the state entirely over a
        # mistyped id.
        self._run([{"title": "They are doing something new.",
                    "detail": "New.", "continues": "state_nope"}])
        titles = [s.title for s in self.agent.app.states.recent(limit=10)]
        self.assertIn("They are doing something new.", titles)


class AStateIsACardTests(unittest.TestCase):
    """A state said what it was and never what it rested on or how long.

    Two states covering a window are different claims if one took forty
    minutes and the other took two, and the calendar showed them the same
    way. And "a browser tab" is not something anybody can go and look at:
    a reader who disagrees with a state needs the page, not a description
    of the page.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    STATE = {
        "title": "Working on the survey-weights replication",
        "detail": "Rebuilding the SWR replication package.",
        "minutes": 38, "horizon": "short", "about": "user",
        "sources": ["C:/work/survey_weights_rebuild/run.py",
                    "https://arxiv.org/abs/2401.00001"],
        "evidence": ["an editor on run.py"],
        "reasoning": "both were open at once",
    }

    def _run(self, states):
        class Reply:
            text = json.dumps({"states": states})

        self.heartbeat.models = type("M", (), {
            "invoke": lambda s, *a, **k: Reply(),
            "resolve": lambda s, *a, **k: type(
                "R", (), {"to_dict": lambda x: {}})(),
            "check_availability": lambda s, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda x: {},
                "state": None})()})()
        return self.heartbeat.run_once(trigger="manual_debug")

    def _written(self):
        return self.agent.app.states.recent(limit=5)

    def test_a_state_keeps_how_much_of_the_window_it_took(self) -> None:
        self._run([self.STATE])
        self.assertEqual(self._written()[0].metadata["minutes"], 38)

    def test_and_what_it_was_built_from(self) -> None:
        self._run([self.STATE])
        sources = self._written()[0].metadata["sources"]
        self.assertIn("https://arxiv.org/abs/2401.00001", sources)
        self.assertIn("C:/work/survey_weights_rebuild/run.py", sources)

    def test_the_sources_are_not_the_evidence(self) -> None:
        """They answer different questions.

        The evidence is the argument for believing the state; the sources
        are the things themselves, which is what somebody who disagrees
        with it has to go and look at.
        """
        self._run([self.STATE])
        meta = self._written()[0].metadata
        self.assertNotEqual(meta["sources"], meta["evidence"])

    def test_the_run_reports_the_state_and_not_a_tally_of_it(self) -> None:
        """It said `"sources": 2`, which is a number about a list nobody
        could then read. Somebody looking at what a review concluded wants
        the file and the page; a count of them is the one part of a state
        that cannot be checked."""
        written = self._run([self.STATE])["states_written"][0]
        self.assertEqual(written["minutes"], 38)
        self.assertIn("https://arxiv.org/abs/2401.00001", written["sources"])
        self.assertIn("an editor on run.py", written["evidence"])
        self.assertEqual(written["reasoning"], "both were open at once")
        self.assertEqual(written["detail"], self.STATE["detail"])

    def test_a_state_with_no_minutes_is_written_anyway(self) -> None:
        # The number is the model's reading of its own evidence, so it is
        # worth having and not worth refusing a state over.
        entry = {key: value for key, value in self.STATE.items()
                 if key != "minutes"}
        self._run([entry])
        self.assertEqual(self._written()[0].metadata.get("minutes"), None)

    def test_and_nonsense_in_the_field_does_not_break_the_run(self) -> None:
        self._run([{**self.STATE, "minutes": "about forty"}])
        self.assertTrue(self._written())

    def test_several_states_are_written_from_one_window(self) -> None:
        """A window of activity is usually two or three things.

        One state for a morning is a state that describes nothing.
        """
        report = self._run([
            self.STATE,
            {**self.STATE, "title": "Watching videos on YouTube",
             "minutes": 12, "sources": ["https://www.youtube.com/watch?v=Ex4m1a0001A"]}])
        self.assertEqual(len(report["states_written"]), 2)


class AStateSaysWhatStretchItWasReadFromTests(unittest.TestCase):
    """A state said it took forty minutes and never out of how long.

    The same forty minutes read out of a fifteen-minute window and out of
    a four-hour one are different claims, and the state carried no way to
    tell them apart. The range is a fact about the run rather than a
    reading of anything, so the script stamps it and the model is never
    asked for it - a number the model copies back is a number that can
    come back wrong.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.heartbeat = self.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    STATE = {"title": "They are rebuilding the replication package.",
             "detail": "Working through the scripts.", "minutes": 9,
             "sources": ["C:/work/run.py"], "evidence": ["the editor on it"],
             "reasoning": "one file for most of the window"}

    def _activity(self, count=3):
        for index in range(count):
            self.app.raw_store.add_event(ListenerEvent(
                event_type="filesystem.modified", source="filesystem",
                timestamp=utcnow(),
                data={"path": f"C:/work/file{index}.py"}))

    def _run(self, states=None):
        class Reply:
            text = json.dumps({"states": states if states is not None
                               else [AStateSaysWhatStretchItWasReadFromTests.STATE]})

        self.heartbeat.models = type("M", (), {
            "invoke": lambda s, *a, **k: Reply(),
            "resolve": lambda s, *a, **k: type(
                "R", (), {"to_dict": lambda x: {}})(),
            "check_availability": lambda s, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda x: {},
                "state": None})()})()
        return self.heartbeat.run_once(trigger="manual_debug")

    def _written(self):
        return self.app.states.recent(limit=5)[0]

    def test_a_state_carries_the_stretch_the_review_read(self) -> None:
        self._activity()
        self._run()
        self.assertIn("looked_at", self._written().metadata)

    def test_the_stretch_has_both_ends(self) -> None:
        """Bounds with one end are not a range."""
        self._activity()
        self._run()
        seen = self._written().metadata["looked_at"]
        self.assertTrue(seen["from"], "no start of the window")
        self.assertTrue(seen["to"], "no end of the window")

    def test_it_says_which_event_to_which_event(self) -> None:
        """The clock says what was asked for; these say what was there.

        A window is a request. Which events actually fell inside it is a
        different fact, and the one somebody weighing the state later
        needs.
        """
        self._activity(3)
        self._run()
        seen = self._written().metadata["looked_at"]
        self.assertTrue(seen["first_event"])
        self.assertTrue(seen["last_event"])
        self.assertLessEqual(seen["first_event"], seen["last_event"])

    def test_it_says_how_much_was_in_it(self) -> None:
        """Two events in a quarter of an hour and three hundred are not
        the same evidence, and the bounds alone cannot tell them apart."""
        self._activity(4)
        self._run()
        self.assertGreaterEqual(self._written().metadata["looked_at"]["events"], 4)

    def test_the_window_is_the_heartbeat_interval(self) -> None:
        self._activity()
        self._run()
        self.assertEqual(self._written().metadata["looked_at"]["minutes"],
                         self.heartbeat._window_minutes())

    def test_the_model_is_never_asked_for_it(self) -> None:
        """It is stamped, not requested.

        A number the model repeats back is a number it can get wrong, and
        this one is already known to the code that writes the state.
        """
        from personalized_agent.heartbeat.core.heartbeat import STEP_PROMPTS

        shape = STEP_PROMPTS["step_state"]
        said = shape["returns"] + shape["instruction"]
        for word in ("looked_at", "first_event", "last_event"):
            self.assertNotIn(word, said,
                             f"the prompt asks the model for {word}")

    def test_being_at_the_desk_is_not_a_state(self) -> None:
        """Coming back, stepping away and the screen locking say whether
        the user was there - and a calendar of them grouped into a "task"
        of watching the user's presence."""
        from personalized_agent.heartbeat.core.heartbeat import STEP_PROMPTS

        said = STEP_PROMPTS["step_state"]["instruction"]
        self.assertIn("BEING AT THE DESK IS NOT A STATE", said)
        self.assertIn("Back at the desk", said)
        self.assertIn("goes in `state_analysis` and nowhere else", said)

    def test_what_the_model_says_about_it_is_ignored(self) -> None:
        """Even when it volunteers one."""
        self._activity()
        self._run([{**self.STATE,
                    "looked_at": {"from": "1999-01-01T00:00:00+00:00"}}])
        seen = self._written().metadata["looked_at"]
        self.assertNotIn("1999", str(seen["from"]))

    def test_the_report_of_what_was_written_carries_it_too(self) -> None:
        """So the panel can say it without going and finding the state."""
        self._activity()
        report = self._run()
        seen = report["states_written"][0]["looked_at"]
        self.assertTrue(seen["first_event"])
        self.assertEqual(seen["minutes"], self.heartbeat._window_minutes())

    def test_an_empty_window_still_says_what_it_covered(self) -> None:
        """Nothing recorded is a finding about the stretch, not an absence
        of one. A state written from a quiet window is a weaker claim, and
        it can only be read that way if the quiet is written down."""
        self._run()
        seen = self._written().metadata["looked_at"]
        self.assertTrue(seen["from"])
        self.assertEqual(seen["events"], 0)


class AnOutcomeNobodyHasYetIsNotNothingTests(unittest.TestCase):
    """The panel read an empty outcome as a conclusion.

    What each step wrote is recorded after all four have answered - so
    between step one's own answer and the end of the run it had answered
    and had nothing on it, and that was shown as "Nothing came of this
    step". They are opposite readings of the same empty dict, and the
    panel had no way to tell them apart.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.heartbeat = self.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _run(self, answer):
        class Reply:
            text = json.dumps(answer)

        self.heartbeat.models = type("M", (), {
            "invoke": lambda s, *a, **k: Reply(),
            "resolve": lambda s, *a, **k: type(
                "R", (), {"to_dict": lambda x: {}})(),
            "check_availability": lambda s, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda x: {},
                "state": None})()})()
        return self.heartbeat.run_once(trigger="manual_debug")

    def test_a_step_says_whether_its_outcome_is_known(self) -> None:
        report = self._run({"states": [
            {"title": "They are reading the listener code.",
             "detail": "filesystem.py, most of the window."}]})
        for step in report["steps"]:
            self.assertTrue(step["outcome_known"],
                            f"{step['id']} finished without saying so")

    def test_and_it_is_false_before_the_run_gets_there(self) -> None:
        """Which is the state every step is in while the run is watched."""
        from personalized_agent.heartbeat.core.heartbeat import StateFloor

        floor = StateFloor(minutes=15, events=[], states=[])
        item = type("I", (), {"id": "step_state", "text": "t", "detail": "d"})()
        given = self.heartbeat._steps_given([(item, floor)])
        self.assertFalse(given[0]["outcome_known"])


class TheStateStepIsToldWhatAStateLooksLikeTests(unittest.TestCase):
    """Opening a file is an event; what it was opened for is a state."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _prompt(self):
        return self.heartbeat.run_once(
            trigger="manual_debug")["steps"][0]["prompt"]

    def test_it_asks_for_several(self) -> None:
        self.assertIn("WRITE SEVERAL STATES", self._prompt())

    def test_and_for_at_least_one_when_there_was_activity(self) -> None:
        """Returning nothing says the window was empty.

        It puts an empty stretch on the calendar for a stretch that was
        not empty, and whatever reads it back later reads it as nothing
        having happened.
        """
        self.assertIn("AT LEAST ONE, UNLESS NOBODY WAS THERE", self._prompt())

    def test_but_an_idle_window_may_write_none(self) -> None:
        # The machine locked, or background events with nobody there.
        prompt = self._prompt()
        self.assertIn("really is idle", prompt)
        self.assertIn("Then write no state", prompt)

    def test_it_shows_three_worked_examples(self) -> None:
        """The shape is not left to be guessed at.

        One project across three applications, one stretch of something
        that is not work at all, and one brief but distinct third thing -
        all from the one window, because that is what it will be given.
        """
        prompt = self._prompt()
        self.assertIn("THREE WORKED EXAMPLES", prompt)
        self.assertIn("They are rebuilding the SWR replication package", prompt)
        self.assertIn("They are watching videos on YouTube", prompt)
        self.assertIn("They are getting the release out", prompt)

    def test_a_title_is_a_whole_sentence(self) -> None:
        """It is the line the user sees with nothing around it.

        "Replication work" and "YouTube" are labels: they name a subject
        and say nothing about it.
        """
        prompt = self._prompt()
        self.assertIn("THE TITLE IS ONE COMPLETE SENTENCE", prompt)
        self.assertIn("ONE COMPLETE SENTENCE whose subject is "
                      "THE USER", prompt)

    def test_and_its_subject_is_the_user(self) -> None:
        """Every state on this calendar is a claim about their situation.

        Not about the file, not about the window, and not about what the
        agent means to do - a thing, a window and an intention, none of
        which is the person the calendar is about.
        """
        prompt = self._prompt()
        self.assertIn("ITS SUBJECT IS THE USER", prompt)
        self.assertIn('"search.py is being edited"', prompt)
        self.assertIn('"Firefox is open"', prompt)

    def test_and_it_is_no_longer_asked_whose_state_it_is(self) -> None:
        # There is only one answer left, so it is not a question.
        self.assertNotIn('"about": "user|agent"', self._prompt())

    def test_and_every_example_title_is_one(self) -> None:
        # An example that is a phrase teaches that a phrase will do.
        prompt = self._prompt()
        for title in ("They are rebuilding the SWR replication package",
                      "They are watching videos on YouTube",
                      "They are getting the release out."):
            self.assertIn(title, prompt, title)

    def test_the_examples_fit_the_window_they_are_from(self) -> None:
        """A state dated across three days on fifteen minutes of evidence.

        The examples taught that: one spanned a week, and two claimed more
        minutes than the window has. An example is the shape the answer
        takes, so an example that does not fit the window teaches that the
        window is not the limit.
        """
        prompt = self._prompt()
        self.assertIn("THE PERIOD YOU CAN SEE IS THIS WINDOW", prompt)
        self.assertIn("All three are from one fifteen-minute window", prompt)
        self.assertNotIn('"minutes": 41', prompt)
        self.assertNotIn("2026-03-06T18:00:00Z", prompt)

    def test_and_a_longer_thing_is_written_for_this_window(self) -> None:
        """One window at a time, as this window saw it.

        It used to be told to mark a continuation with the running state's
        id. It is no longer shown the running states, so an instruction to
        name one would be an instruction it cannot follow - and a prompt
        that asks for something the context does not contain is answered
        by invention.
        """
        prompt = self._prompt()
        self.assertIn("the same situation seen again", prompt)
        self.assertIn("A long state is made of those, one window at a time",
                      prompt)
        self.assertNotIn("continues", prompt)

    def test_and_says_why_the_calendar_is_not_in_front_of_it(self) -> None:
        """An absence with no reason reads as an oversight."""
        prompt = self._prompt()
        self.assertIn("You are not shown what is already on the calendar",
                      prompt)
        self.assertIn("confirming", prompt)

    def test_and_the_examples_carry_every_field(self) -> None:
        # An example missing the fields it is meant to demonstrate is a
        # demonstration that they are optional.
        prompt = self._prompt()
        for field in ('"minutes"', '"sources"', '"evidence"', '"reasoning"',
                      '"starts_at"'):
            self.assertIn(field, prompt, field)

    def test_the_preamble_says_nothing_about_how_it_is_run(self) -> None:
        """"You run on a timer, not in a conversation" told the model
        something it cannot use. Every call is its own call with its own
        context; what schedules it is not a fact it can act on."""
        from personalized_agent.heartbeat.core.heartbeat import PREAMBLE

        self.assertNotIn("in a conversation", PREAMBLE)
        self.assertNotIn("on a timer", PREAMBLE)
        self.assertIn("Answer that job and nothing else", PREAMBLE)

    def test_and_none_of_them_declares_a_horizon(self) -> None:
        self.assertNotIn('"horizon"', self._prompt())

    def test_and_shows_what_the_right_altitude_looks_like(self) -> None:
        prompt = self._prompt()
        self.assertIn("They are watching videos on YouTube", prompt)
        self.assertIn("survey-weights replication", prompt)

    def test_and_what_the_wrong_altitude_looks_like(self) -> None:
        # The line is easier to see from both sides of it.
        prompt = self._prompt()
        self.assertIn("that is an event", prompt)
        self.assertIn("that is every state at once", prompt)

    def test_it_asks_for_the_minutes_and_the_sources(self) -> None:
        # Matched on fragments that do not cross a line break: the
        # instruction is wrapped prose, not one long line.
        prompt = self._prompt()
        self.assertIn("THE DURATION OF THIS ACTIVITY, IN MINUTES. REQUIRED", prompt)
        self.assertIn("every file, page or window this state rests on", prompt)

    def test_and_says_the_detail_is_not_the_time(self) -> None:
        # Repeating the period in prose means two places to be wrong.
        self.assertIn("The detail does NOT say how long it", self._prompt())


class TheConfidenceScaleHasSevenPointsTests(unittest.TestCase):
    """It ran 1 to 7 and explained five of them.

    A scale that names 7, 5, 4, 2 and 1 is a scale whose 6 and 3 mean
    whatever the reader decides they mean - and the two it left out are
    the two either side of the middle, which is where most honest answers
    land.
    """

    def _instruction(self):
        from personalized_agent.heartbeat.core.heartbeat import STEP_PROMPTS

        return STEP_PROMPTS["step_nature"]["instruction"]

    def test_every_point_on_it_is_explained(self) -> None:
        said = self._instruction()
        for number in range(1, 8):
            self.assertIn(f"  {number} - ", said, f"no {number} on the scale")

    def test_and_they_run_from_seven_down_to_one(self) -> None:
        said = self._instruction()
        places = [said.index(f"  {number} - ") for number in range(7, 0, -1)]
        self.assertEqual(places, sorted(places), "the scale is out of order")

    def test_and_the_record_takes_any_of_them(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import _confidence

        for number in range(1, 8):
            self.assertEqual(_confidence(number), number)


class TheNatureStepGathersForItselfTests(unittest.TestCase):
    """It was handed a context built from a query written in advance.

    The query came from the names in the activity, which is the right
    material for "what is happening" and the wrong material for "who is
    this" - a habit is the same afternoon four times, and the fourth one
    is not in this window. So it reads the same window the step before it
    read, plus what that step concluded from it, and goes looking for the
    rest.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.heartbeat = self.agent.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _steps(self, states=()):
        class Reply:
            text = json.dumps({"states": list(states),
                               "user_nature": [{"title": "Works late",
                                                "content": "Often.",
                                                "kind": "personal",
                                                "states": [1]}]})

        self.heartbeat.models = type("M", (), {
            "invoke": lambda s, *a, **k: Reply(),
            "resolve": lambda s, *a, **k: type(
                "R", (), {"to_dict": lambda x: {}})(),
            "check_availability": lambda s, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda x: {},
                "state": None})()})()
        report = self.heartbeat.run_once(trigger="manual_debug")
        return {step["id"]: step for step in report["steps"]}

    def test_it_starts_from_a_floor_like_the_step_before_it(self) -> None:
        step = self._steps()["step_nature"]
        self.assertEqual(step["trail"][0]["did"], "floor")

    def test_and_reads_the_same_window_of_activity(self) -> None:
        step = self._steps()["step_nature"]
        self.assertIn("WHAT THE USER DID", step["prompt"])

    def test_and_is_given_the_states_the_step_before_it_wrote(self) -> None:
        """Not the states already running.

        Those are what was true before this review; these are what it has
        just decided is true now, which is the thing being generalised.
        """
        step = self._steps([
            {"title": "They are rebuilding the replication package.",
             "detail": "Working through the scripts.",
             "about": "user"}])["step_nature"]
        self.assertIn("STATES ALREADY WRITTEN FROM THIS ACTIVITY",
                      step["context"])
        self.assertIn("rebuilding the replication package", step["context"])
        # Said as what they are, not as who wrote them. This step is a
        # fresh call: it has no memory of the one before it, and a label
        # that tells it what it "just concluded" describes a conversation
        # it was not in.
        self.assertNotIn("previous step", step["context"])

    def test_its_opening_row_says_what_it_actually_started_with(self) -> None:
        """The row is written when the floor is built.

        These states are not known then - they are what the step before is
        about to answer - so it carried the running states instead, which
        is a different set and contradicted its own context two lines
        further down the panel.
        """
        step = self._steps([
            {"title": "They are rebuilding the replication package.",
             "detail": "Working through the scripts.",
             "about": "user"}])["step_nature"]
        floor = [row for row in step["trail"] if row["did"] == "floor"][0]
        self.assertEqual(floor["states"], 1)
        self.assertEqual(floor["states_started"][0]["title"],
                         "They are rebuilding the replication package.")


    def test_and_is_told_to_write_nothing_rather_than_guess(self) -> None:
        step = self._steps()["step_nature"]
        self.assertIn("Write nothing rather than write a habit you have seen",
                      step["prompt"])

    def test_and_that_an_empty_list_is_a_good_answer(self) -> None:
        """Most windows say nothing new about who somebody is, and a step
        that thinks it must write something will write a restatement."""
        step = self._steps()["step_nature"]
        self.assertIn("AN EMPTY LIST IS A GOOD ANSWER", step["prompt"])
        self.assertIn('{"user_nature": []}', step["prompt"])
        self.assertNotIn("mark it a 1 or a 2", step["prompt"])
        # Allowed, not expected: how often a window has something worth
        # recording is not something the step can be told in advance.
        self.assertNotIn("most windows should", step["prompt"])

    NATURE = {
        "title": "They work in long uninterrupted sittings, late in the evening.",
        "content": "Sessions run past midnight and rarely break.",
        "confidence": 5,
        "evidence": ["four sessions past midnight this week"],
        "reasoning": "the same shape four times is a rhythm, not a night",
        "kind": "personal",
        "states": [1],
    }

    def _nature(self, entry):
        class Reply:
            # A state for it to rest on: a nature resting on none is not
            # written.
            text = json.dumps({"states": [{"title": "A late session",
                                           "detail": "d"}],
                               "user_nature": [entry]})

        self.heartbeat.models = type("M", (), {
            "invoke": lambda s, *a, **k: Reply(),
            "resolve": lambda s, *a, **k: type(
                "R", (), {"to_dict": lambda x: {}})(),
            "check_availability": lambda s, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda x: {},
                "state": None})()})()
        self.heartbeat.run_once(trigger="manual_debug")
        return [r for r in self.agent.app.library.records()
                if r.folder == USER_NATURE][0]

    def test_a_trait_says_how_sure_the_agent_is(self) -> None:
        """A range, not a flag.

        "I watched this hold for a month" and "this looked like a pattern
        once" are both worth writing down and are not the same claim - and
        the second written as the first is something the agent will act on
        and keep acting on.
        """
        self.assertIn("Confidence: 5 of 7", self._nature(self.NATURE).body)

    def test_and_what_it_rests_on_and_how_it_got_there(self) -> None:
        # The same three things a state carries, for the same reason: a
        # claim about a person they never made, which they cannot argue
        # with unless they can see its basis.
        body = self._nature(self.NATURE).body
        self.assertIn("four sessions past midnight this week", body)
        self.assertIn("a rhythm, not a night", body)

    def test_an_unsaid_confidence_is_the_middle(self) -> None:
        entry = {k: v for k, v in self.NATURE.items() if k != "confidence"}
        self.assertIn("Confidence: 4 of 7", self._nature(entry).body)

    def test_and_one_off_the_scale_is_brought_onto_it(self) -> None:
        self.assertIn("Confidence: 7 of 7",
                      self._nature({**self.NATURE, "confidence": 99}).body)
        self.assertIn("Confidence: 1 of 7",
                      self._nature({**self.NATURE, "confidence": -4}).body)

    def test_nonsense_in_the_field_does_not_lose_the_trait(self) -> None:
        record = self._nature({**self.NATURE, "confidence": "quite sure"})
        self.assertIn("Confidence: 4 of 7", record.body)

    def test_the_prompt_asks_for_all_of_it(self) -> None:
        prompt = next(step for step in self.heartbeat.run_once(
            trigger="manual_debug")["steps"] if step["id"] == "step_nature")["prompt"]
        self.assertIn("EVERY RECORD SAYS HOW SURE YOU ARE", prompt)
        self.assertIn('"confidence": 4', prompt)
        self.assertIn("ONE COMPLETE SENTENCE whose subject is the user",
                      prompt)
        self.assertIn("a short paragraph", prompt)

    def test_it_still_writes_what_it_finds(self) -> None:
        # With a state for it to rest on: one resting on none is not written.
        self._steps(states=[{"title": "A late session", "detail": "d"}])
        folders = [record.folder for record in self.agent.app.library.records()]
        self.assertIn(USER_NATURE, folders)


class TheReviewReadsWhatEachPageWasAboutTests(unittest.TestCase):
    """The review was given titles: "Inbox", "New chat", a video's name.
    A title says which page and not what was on it. Every place the agent
    heartbeat reads recent activity now carries the page's keywords beside
    its title - the same ones the Overview shows, with no model."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        pages = {
            "Inbox - Outlook — Mozilla Firefox":
                "The journal office asks for the revised manuscript. The revised "
                "manuscript is due Friday; the reviewer wants the manuscript "
                "figures redrawn.",
            "New chat - ChatGPT — Mozilla Firefox":
                "How do I bootstrap a confidence interval in pandas? The "
                "bootstrap resamples rows; pandas sample with replace.",
            "Weather — Mozilla Firefox":
                "Sunny tomorrow with light wind and a warm afternoon.",
        }
        for title, text in pages.items():
            self.app.raw_store.add_event(ListenerEvent(
                event_type="screen.text", source="screen",
                application="firefox.exe", timestamp=utcnow(),
                data={"window_title": title, "text": text}))
        # A focus event: a title and nothing else of its own.
        self.app.raw_store.add_event(ListenerEvent(
            event_type="application.focused", source="application",
            application="firefox.exe", timestamp=utcnow(),
            data={"window_title": "Inbox - Outlook — Mozilla Firefox"}))

    def tearDown(self) -> None:
        self.agent.cleanup()


class AWindowWhoseTitleNeverChangesTests(unittest.TestCase):
    """A terminal named after the morning's problem was reported all
    evening as that problem: no reading of any window reached the step -
    it looked for `content`, the screen keeps `text` - and the keywords
    were one set per title for the whole day. Now the step reads the
    text, the keywords are of the moment, and an old title is said to be
    a name."""

    TITLE = "Upload instruction wc error - Terminal"

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        now = utcnow()
        said = [
            (now - timedelta(hours=5),
             "wc reports an error on the upload instruction file. The wc "
             "command fails; wc counts lines; the upload instruction is "
             "missing a newline, so wc errors."),
            (now - timedelta(minutes=3),
             "The usage chart shows a share of the hour. Each hour's share "
             "is minutes over sixty; the lock screen hour is time away from "
             "the chart, so the chart share drops."),
        ]
        for when, text in said:
            self.app.raw_store.add_event(ListenerEvent(
                event_type="screen.text", source="screen",
                application="WindowsTerminal.exe", timestamp=when,
                data={"window_title": self.TITLE, "text": text}))
        self.app.raw_store.add_event(ListenerEvent(
            event_type="application.focused", source="application",
            application="WindowsTerminal.exe", timestamp=now,
            data={"window_title": self.TITLE}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_step_reads_the_text_of_now_and_is_told_the_title_is_old(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import StateFloor

        window = self.app.heartbeat._activity_since(15)
        shown = StateFloor(minutes=15, events=window, states=[]).events_text()
        self.assertIn("share of the hour", shown,
                      "the screen's text did not reach the step")
        self.assertIn("is the window's name", shown,
                      "a five-hour-old title was not said to be a name")
        focused = [e for e in window if e["event_type"] == "window"][0]
        words = focused["data"].get("keywords") or []
        self.assertIn("chart", words, "the keywords were not of the moment")
        self.assertNotIn("wc", words,
                         "the keywords came from five hours earlier")


class EveryReadingOfTheWindowTests(unittest.TestCase):
    """A window read seven times in fifteen minutes was shown as its newest
    three, 700 characters each - the end of the window presented as all of
    it. Every reading is shown now, oldest first, and when they do not fit
    each is shortened alike rather than the oldest being dropped."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _read(self, count: int, size: int) -> None:
        now = utcnow()
        for index in range(count):
            self.app.raw_store.add_event(ListenerEvent(
                event_type="screen.text", source="screen",
                application="WindowsTerminal.exe",
                timestamp=now - timedelta(minutes=count - index),
                data={"window_title": "A session",
                      "text": f"reading {index} " + "x" * size}))

    def test_all_of_them_oldest_first(self) -> None:
        self._read(7, 1500)
        window = self.app.heartbeat._activity_since(15)
        reads = [r for e in window for r in (e["data"].get("reads") or [])]
        self.assertEqual([r["text"].split()[1] for r in reads],
                         [str(index) for index in range(7)])
        self.assertFalse(any(r["text"].endswith("…") for r in reads),
                         "readings that fit were shortened")

    def test_when_they_do_not_fit_each_is_shortened_alike(self) -> None:
        beat = self.app.heartbeat
        self._read(20, 3900)
        window = beat._activity_since(30)
        reads = [r for e in window for r in (e["data"].get("reads") or [])]
        self.assertEqual(len(reads), 20, "a reading was dropped")
        lengths = {len(r["text"]) for r in reads}
        self.assertLessEqual(max(lengths) - min(lengths), 1, lengths)
        self.assertLessEqual(sum(len(r["text"]) for r in reads),
                             beat.CONTENT_BUDGET + len(reads))


class StepThreeAddsNotificationsTests(unittest.TestCase):
    """What notifications need to be added: read from the same activity as
    step one and the states it has just written, and each one put on the
    calendar as a task that pops up at its time - a reminder, or a notice.
    Every step is also told how often it is asked."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.raw_store.add_event(ListenerEvent(
            event_type="screen.text", source="screen",
            application="Outlook.exe",
            data={"window_title": "Inbox",
                  "text": "The revised manuscript is due Friday at 17:00."}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _run(self, notifications, verdict=None):
        soon = (utcnow() + timedelta(hours=2)).isoformat()
        self.checks_asked = []
        checks_asked = self.checks_asked

        class Verdict:
            text = (verdict if isinstance(verdict, str)
                    else json.dumps(verdict or {"same_as": None,
                                                "create_anyway": False,
                                                "why": "different"}))

        class Reply:
            text = json.dumps({
                "state_analysis": "Reading mail.",
                "states": [{"title": "Revising the manuscript",
                            "detail": "due Friday", "horizon": "short",
                            "about": "user"}],
                "user_nature": [],
                # Every one says why: one without a reason is not added.
                "notifications": [{"why": "seen in the activity",
                                   **(dict(n, at=n.get("at", soon))
                                      if "at" not in n or n["at"] else n)}
                                  for n in notifications]})

        def invoke(s, request, *a, **k):
            # The duplicate check is its own question, with its own answer.
            if str(getattr(request, "system", "")).startswith(
                    "IS THIS NOTIFICATION ALREADY SCHEDULED"):
                checks_asked.append(request.messages[0]["content"])
                return Verdict()
            return Reply()

        self.app.heartbeat.models = type("M", (), {
            "invoke": invoke,
            "resolve": lambda s, *a, **k: type(
                "R", (), {"to_dict": lambda x: {}})(),
            "check_availability": lambda s, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda x: {},
                "state": None})()})()
        return self.app.heartbeat.run_once(trigger="manual_debug")

    def _waiting(self):
        return [task for task in self.app.scheduler.store.list(status="pending")
                if task.execution_type == "notification"]

    def test_it_is_given_the_activity_the_states_and_what_is_scheduled(self) -> None:
        report = self._run([])
        steps = {step["id"]: step for step in report["steps"]}
        prompt = steps["step_notify"]["prompt"]
        self.assertIn("revised manuscript is due Friday", prompt)
        self.assertIn("Revising the manuscript", prompt)
        self.assertIn("NOTIFICATIONS ALREADY SCHEDULED", prompt)
        self.assertNotIn("NOTIFICATIONS ALREADY SCHEDULED",
                         steps["step_state"]["prompt"])

    def test_every_step_is_told_how_often_it_is_asked(self) -> None:
        every = self.app.heartbeat._window_minutes()
        report = self._run([])
        for step in report["steps"]:
            self.assertIn(f"YOU ARE ASKED THIS EVERY {every:g} MINUTES",
                          step["prompt"], step["id"])

    def test_each_one_goes_on_the_calendar_as_its_own_kind(self) -> None:
        report = self._run([
            {"type": "reminder", "title": "Send the revised manuscript",
             "message": "It is due Friday at 17:00.", "why": "the mail"},
            {"type": "notice", "title": "Manuscript due tomorrow",
             "message": "The deadline is tomorrow at 17:00."}])
        self.assertEqual([row["ok"] for row in
                          report["notifications_scheduled"]], [True, True])
        kinds = {task.title: task.task_payload.get("template")
                 for task in self._waiting()}
        self.assertEqual(kinds["Send the revised manuscript"], "reminder")
        self.assertEqual(kinds["Manuscript due tomorrow"], "passive")
        for task in self._waiting():
            self.assertTrue(task.task_payload.get("notify_user"))
            self.assertGreater(task.scheduled_at, utcnow())

    def test_what_it_cannot_schedule_is_refused_and_said(self) -> None:
        self._run([{"type": "reminder", "title": "Send it",
                    "message": "Due Friday."}])
        report = self._run([
            {"type": "reminder", "title": "Send it", "message": "Due Friday."},
            {"type": "reminder", "title": "", "message": "No title."},
            {"type": "notice", "title": "No time", "message": "x", "at": ""}],
            verdict={"same_as": "it", "create_anyway": False, "why": "same"})
        errors = [row.get("error") for row in report["notifications_scheduled"]]
        self.assertIn("already scheduled as", errors[0])
        self.assertIn("title", errors[1])
        self.assertIn("time", errors[2])
        self.assertEqual(len(self._waiting()), 1)

    def test_a_notice_pops_up_as_a_notice(self) -> None:
        self._run([{"type": "notice", "title": "Manuscript due tomorrow",
                    "message": "The deadline is tomorrow at 17:00."}])
        task = self._waiting()[0]
        self.app.scheduler.run_task(task)
        rows = self.app.db.query(
            "SELECT metadata FROM notifications WHERE title = ?",
            ("Manuscript due tomorrow",))
        self.assertTrue(rows, "nothing popped up")
        self.assertEqual(json.loads(rows[-1]["metadata"])["template"], "passive")

    # -- the check against what is already there ---------------------------
    def _first(self):
        self._run([{"type": "reminder", "title": "Send the revised manuscript",
                    "message": "It is due Friday at 17:00."}])

    def test_a_repeat_nearby_is_shown_to_the_model_and_not_created(self) -> None:
        self._first()
        report = self._run(
            [{"type": "reminder", "title": "Send your revised manuscript",
              "message": "The revised manuscript is due Friday at 17:00."}],
            verdict={"same_as": "x", "create_anyway": False,
                     "why": "the same reminder, reworded"})
        self.assertEqual(len(self.checks_asked), 1)
        asked = self.checks_asked[0]
        self.assertIn("NEW:", asked)
        self.assertIn("Send your revised manuscript", asked)
        self.assertIn("ALREADY SCHEDULED NEARBY", asked)
        self.assertIn("Send the revised manuscript", asked)
        row = report["notifications_scheduled"][0]
        self.assertFalse(row["ok"])
        self.assertIn("already scheduled as", row["error"])
        self.assertEqual(len(self._waiting()), 1)
        self.assertEqual(len(report["notification_checks"]), 1)

    def test_but_the_model_may_insist_and_says_why(self) -> None:
        self._first()
        report = self._run(
            [{"type": "reminder", "title": "Send the revised manuscript",
              "message": "It is due Friday at 17:00.", "why": "the mail"}],
            verdict={"same_as": "x", "create_anyway": True,
                     "why": "the editor wrote again with a new deadline"})
        self.assertTrue(report["notifications_scheduled"][0]["ok"])
        self.assertEqual(len(self._waiting()), 2)
        made = [task for task in self._waiting() if "created although"
                in (task.reason or "")]
        self.assertEqual(len(made), 1)
        self.assertIn("new deadline", made[0].reason)

    def test_alike_but_far_apart_in_time_is_not_a_repeat(self) -> None:
        """A reminder three days ahead and one two days ahead are both meant."""
        self._first()
        later = (utcnow() + timedelta(days=2)).isoformat()
        report = self._run(
            [{"type": "reminder", "title": "Send the revised manuscript",
              "message": "It is due Friday at 17:00.", "at": later}])
        self.assertEqual(self.checks_asked, [])
        self.assertTrue(report["notifications_scheduled"][0]["ok"])

    def test_a_check_that_cannot_be_read_does_not_create(self) -> None:
        """The doubt was that the user would be interrupted twice for one
        thing, and an answer nobody can read has not removed it."""
        self._first()
        report = self._run(
            [{"type": "reminder", "title": "Send the revised manuscript",
              "message": "It is due Friday at 17:00."}],
            verdict="I think these are probably the same, yes.")
        row = report["notifications_scheduled"][0]
        self.assertFalse(row["ok"])
        self.assertIn("could not be read", row["error"])
        self.assertEqual(len(self._waiting()), 1)

    def test_the_event_says_which_step_made_it(self) -> None:
        self._first()
        task = self._waiting()[0]
        self.assertIn("agent heartbeat, step 3",
                      task.metadata.get("created_by_step", ""))
        self.assertEqual(task.created_by, "heartbeat")


class OneWindowIsOneEntryTests(unittest.TestCase):
    """Switching to a window, switching away and reading it were three
    entries, and only the reading had any of what was in it. They are one
    entry now: each stretch the window was in front, and what was read."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        now = utcnow()
        title = "Notes - Editor"
        for kind, minutes_ago, data in (
                ("application.focused", 12, {}),
                ("screen.text", 11, {"text": "Draft of the rebuttal letter."}),
                ("application.focus_ended", 10, {"duration_seconds": 120}),
                ("application.focused", 4, {})):
            self.app.raw_store.add_event(ListenerEvent(
                event_type=kind, source="test", application="editor.exe",
                timestamp=now - timedelta(minutes=minutes_ago),
                data={"window_title": title, **data}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_three_kinds_of_event_are_one_window(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import StateFloor

        window = self.app.heartbeat._activity_since(15)
        self.assertEqual([e["event_type"] for e in window], ["window"])
        entry = window[0]
        self.assertEqual(len(entry["spans"]), 1)
        self.assertEqual(round(entry["spans"][0][2]), 120)
        self.assertTrue(entry.get("in_front_since"),
                        "the switch back with no end is still in front")
        shown = StateFloor(minutes=15, events=window, states=[]).context_text()
        self.assertIn("editor.exe  window  in front 2 times  read 1 time",
                      shown)
        self.assertIn("(2m 00s)", shown)
        self.assertIn("-now", shown)
        self.assertIn("Draft of the rebuttal letter.", shown)

    def test_the_prompt_says_what_each_kind_of_entry_is(self) -> None:
        floor = self.app.heartbeat._state_floor("t")
        text = floor.context_text()
        self.assertIn("What each entry is:", text)
        for kind in ("window -", "browser.navigation", "assistant.message",
                     "filesystem.opened"):
            self.assertIn(kind, text)


class StepsTwoAndThreeAreShownExamplesTests(unittest.TestCase):
    """What is worth writing is easier to show than to define, and so is
    what is not - a state written as a trait, a duplicate reworded."""

    def test_both_carry_examples_of_each_side(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import STEP_PROMPTS

        for name in ("step_nature", "step_notify"):
            text = STEP_PROMPTS[name]["instruction"]
            self.assertIn("EXAMPLES. Worth", text, name)
            self.assertIn("Not worth", text, name)
        self.assertIn('"type": "notice"',
                      STEP_PROMPTS["step_notify"]["instruction"])
        self.assertIn('"type": "reminder"',
                      STEP_PROMPTS["step_notify"]["instruction"])
        self.assertIn('"confidence": 3',
                      STEP_PROMPTS["step_nature"]["instruction"])


class EveryTimeIsTheUsersTests(unittest.TestCase):
    """Every time the heartbeat showed was UTC cut to its clock: the model
    read "01:30" for half past eight in the evening, on a day that was
    already tomorrow. Every prompt opens with now, in the user's timezone,
    and every time in it is in that timezone."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.raw_store.add_event(ListenerEvent(
            event_type="filesystem.modified", source="filesystem",
            application="editor.exe", timestamp=utcnow() - timedelta(minutes=3),
            data={"path": "C:/work/draft.md"}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_context_opens_with_now_and_the_zone(self) -> None:
        here = datetime.now().astimezone()
        text = self.app.heartbeat._state_floor("t").context_text()
        self.assertTrue(text.startswith("NOW: "), text[:80])
        self.assertIn(here.strftime("%A %d %B %Y"), text)
        self.assertIn(here.tzname() or "", text)

    def test_an_events_time_is_the_users(self) -> None:
        moment = utcnow() - timedelta(minutes=3)
        text = self.app.heartbeat._state_floor("t").context_text()
        local = moment.astimezone().strftime("%H:%M")
        self.assertIn(local, text)
        if moment.astimezone().utcoffset():
            self.assertNotIn(moment.strftime("%H:%M:%S"), text.split(
                "=== 1.")[1].split("What each entry is")[1])

    def test_what_the_model_writes_carries_an_offset(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import STEP_PROMPTS

        for name in ("step_state", "step_notify"):
            prompt = STEP_PROMPTS[name]["instruction"] + STEP_PROMPTS[name]["returns"]
            self.assertIn("UTC offset", prompt, name)
            self.assertNotIn("ISO-8601 UTC", prompt, name)


class AnythingCanBeReadByItsIdTests(unittest.TestCase):
    """Every step can read anything whose id it sees, exactly and in full;
    the id's beginning says what it is and where it is kept."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.heartbeat = self.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _reading(self, text="Message sent. Undo  View message"):
        return self.app.raw_store.add_event(ListenerEvent(
            event_type="screen.text", source="screen", application="firefox.exe",
            data={"window_title": "Gmail", "text": text}))

    def test_each_kind_is_read_from_where_it_lives(self) -> None:
        from personalized_agent.agent.states.store import State
        from personalized_agent.common.events.models import ScheduledTask

        event = self._reading("x" * 9000)
        state = self.app.states.add(State(title="Replying to Prof. Moreau"))
        task = self.app.scheduler.create_task(ScheduledTask(
            title="Finish your reply", scheduled_at=utcnow() + timedelta(hours=3),
            execution_type="notification", task_payload={"message": "m"}))
        found = self.heartbeat._open_by_ids(
            [event.id, state.id, task.id, "state_0000000000000000", "nope"])
        # Whole: a screen reading is not cut short.
        self.assertEqual(found[event.id]["data"]["text"], "x" * 9000)
        self.assertEqual(found[state.id]["title"], "Replying to Prof. Moreau")
        self.assertEqual(found[task.id]["title"], "Finish your reply")
        self.assertIn("scheduled task", found[task.id]["is"])
        self.assertIn("error", found["state_0000000000000000"])
        self.assertIn("event_", found["nope"]["error"])


    def test_every_activity_line_names_the_events_it_stands_for(self) -> None:
        event = self._reading()
        floor = self.heartbeat._state_floor("heartbeat_test")
        self.assertIn(event.id, floor.context_text())

    def test_every_step_is_offered_it(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import STATE_TOOLS

        self.assertIn("open_by_id", [tool.name for tool in STATE_TOOLS])


class StepThreeDisablesWhatIsAlreadyDoneTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.heartbeat = self.app.heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _notification(self, title="Finish your reply to Prof. Moreau"):
        from personalized_agent.common.events.models import ScheduledTask

        return self.app.scheduler.create_task(ScheduledTask(
            title=title, description="Finish the reply you started.",
            scheduled_at=utcnow() + timedelta(hours=3),
            execution_type="notification", reason="a draft was left unsent",
            task_payload={"message": "m", "template": "reminder"}))

    def test_it_is_shown_every_waiting_one_with_its_id_and_reason(self) -> None:
        task = self._notification()
        lines = "\n".join(self.heartbeat._notifications_ahead(with_checks=True))
        self.assertIn(task.id, lines)
        self.assertIn("made because: a draft was left unsent", lines)

    def test_a_done_one_is_closed_with_its_reason_and_never_fires(self) -> None:
        task = self._notification()
        self.heartbeat._notifications_ahead(with_checks=True)
        done = self.heartbeat._close_notifications("heartbeat_test", [
            {"id": task.id, "why": "The reply was sent at 15:02.",
             "evidence": ["event_1234abcd5678ef90"]}])
        self.assertTrue(done[0]["ok"], done)
        kept = self.app.scheduler.store.get(task.id)
        self.assertEqual(kept.status, "closed")
        self.assertEqual(kept.metadata["closed_reason"],
                         "The reply was sent at 15:02.")
        self.assertIn("step 3", kept.metadata["closed_by"])
        # Still on the calendar, and never run.
        self.assertIn(task.id, [one.id for one in self.app.scheduler.store.list(
            start=utcnow(), end=utcnow() + timedelta(days=1))])
        self.assertNotIn(task.id, [one.id for one in self.app.scheduler.store.due(
            utcnow() + timedelta(days=1))] if hasattr(
                self.app.scheduler.store, "due") else [])

    def test_it_closes_only_what_it_was_shown_and_still_waiting(self) -> None:
        shown = self._notification()
        self.heartbeat._notifications_ahead(with_checks=True)
        unseen = self._notification("Something listed after")
        done = self.heartbeat._close_notifications("heartbeat_test", [
            {"id": unseen.id, "why": "done"},
            {"id": "task_made_up", "why": "done"},
            {"id": shown.id, "why": ""}])
        self.assertFalse(any(row["ok"] for row in done))
        self.assertEqual(self.app.scheduler.store.get(unseen.id).status, "pending")
        self.assertEqual(self.app.scheduler.store.get(shown.id).status, "pending")

    def test_new_details_fill_a_notification_in_rather_than_another(self) -> None:
        """A meeting's notification, and then its link arrives - and it
        moves to an hour later: the one notification carries both."""
        task = self._notification("Meeting with Prof. Tan")
        self.heartbeat._notifications_ahead(with_checks=True)
        later = (task.scheduled_at + timedelta(hours=1)).isoformat()
        done = self.heartbeat._update_notifications("heartbeat_test", [
            {"id": task.id, "why": "The invitation email gives the Zoom link and "
                                   "moves it an hour later.",
             "details": {"link": "https://zoom.us/j/123", "place": "Zoom"},
             "at": later, "evidence": ["event_1234abcd5678ef90"]}])
        self.assertTrue(done[0]["ok"], done)
        self.assertEqual(done[0]["changed"], ["description", "details", "scheduled_at"])
        kept = self.app.scheduler.store.get(task.id)
        self.assertEqual(kept.status, "pending")
        # What it said still holds; the link is in what the user sees.
        self.assertIn("Finish the reply you started.", kept.description)
        self.assertIn("link: https://zoom.us/j/123", kept.description)
        self.assertEqual(kept.task_payload["message"], kept.description)
        self.assertEqual(kept.metadata["details"]["link"], "https://zoom.us/j/123")
        self.assertEqual(kept.scheduled_at.isoformat(), later)
        update = kept.metadata["updates"][-1]
        self.assertEqual(update["before"]["message"], "Finish the reply you started.")
        self.assertIn("zoom.us", update["after"]["message"])
        self.assertIn("step 3", update["by"])
        # Listed to the next look with its details - and nothing new is no update.
        lines = "\n".join(self.heartbeat._notifications_ahead(with_checks=True))
        self.assertIn("details: link: https://zoom.us/j/123", lines)
        again = self.heartbeat._update_notifications("heartbeat_test", [
            {"id": task.id, "why": "the same link", "details": {"link": "https://zoom.us/j/123"}}])
        self.assertEqual(again[0]["error"], "nothing new in it")

    def test_it_updates_only_what_it_was_shown_still_waiting_and_with_a_reason(self) -> None:
        shown = self._notification()
        self.heartbeat._notifications_ahead(with_checks=True)
        unseen = self._notification("Something listed after")
        done = self.heartbeat._update_notifications("heartbeat_test", [
            {"id": unseen.id, "why": "new", "message": "x"},
            {"id": "task_made_up", "why": "new", "message": "x"},
            {"id": shown.id, "why": "", "message": "x"}])
        self.assertFalse(any(row["ok"] for row in done))
        self.assertEqual(self.app.scheduler.store.get(shown.id).description,
                         "Finish the reply you started.")
        from personalized_agent.heartbeat.core.heartbeat import STEP_PROMPTS
        self.assertIn("AND COMPLETE WHAT HAS NEW DETAILS",
                      STEP_PROMPTS["step_notify"]["instruction"])
        self.assertIn('"updated"', STEP_PROMPTS["step_notify"]["returns"])

    def test_step_3_adds_and_disables_and_does_not_repeat(self) -> None:
        """One step: what to add and what is done, from the states, the
        activity and every active notification from now on."""
        from personalized_agent.heartbeat.core.heartbeat import STEP_PROMPTS

        said = STEP_PROMPTS["step_notify"]["instruction"]
        returns = STEP_PROMPTS["step_notify"]["returns"]
        self.assertIn("DO NOT ADD WHAT IS ALREADY THERE", said)
        self.assertIn("AND DISABLE WHAT IS ALREADY DONE", said)
        self.assertIn("Reading is not replying", said)
        # And it can read more of its window, by id, for both halves.
        self.assertIn("YOU CAN READ MORE OF THIS WINDOW", said)
        self.assertNotIn("search_memory", said)
        self.assertIn("open_by_id", said)
        self.assertIn('"notifications"', returns)
        self.assertIn('"closed"', returns)
        self.assertNotIn("step_close", STEP_PROMPTS)

    def test_what_has_already_popped_up_is_not_listed(self) -> None:
        from personalized_agent.common.events.models import ScheduledTask

        past = self.app.scheduler.create_task(ScheduledTask(
            title="Earlier", scheduled_at=utcnow() - timedelta(minutes=20),
            execution_type="notification", task_payload={"message": "m"}))
        ahead = self._notification()
        lines = "\n".join(self.heartbeat._notifications_ahead(with_checks=True))
        self.assertIn(ahead.id, lines)
        self.assertNotIn(past.id, lines)


class WhatEachStepWritesSaysWhoWroteItTests(unittest.TestCase):
    """Every state, nature record and notification names the model that
    wrote it; and a nature record is linked to the states it rests on."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.raw_store.add_event(ListenerEvent(
            event_type="assistant.message", source="assistant",
            application="VS Code",
            data={"role": "user", "text": "rebuild the replication package"}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _run(self, payload):
        class Reply:
            text = json.dumps(payload)
            model = "claude-test-1"
            provider = "anthropic"
            tool_calls = []
            usage = None

        self.app.heartbeat.models = type("M", (), {
            "invoke": lambda self, *a, **k: Reply(),
            "resolve": lambda self, *a, **k: type("R", (), {"to_dict": lambda s: {}})(),
            "check_availability": lambda self, *a, **k: type("A", (), {
                "available": True, "to_dict": lambda s: {}, "state": None})()})()
        return self.app.heartbeat.run_once(trigger="manual_debug")

    def test_states_and_nature_name_their_model_and_are_linked(self) -> None:
        from personalized_agent.data_manage.library import USER_NATURE

        now = utcnow()
        report = self._run({
            "states": [
                {"title": "Rebuilding the replication package",
                 "detail": "Working through the scripts.",
                 "starts_at": now.isoformat(),
                 "ends_at": (now + timedelta(minutes=10)).isoformat()},
                {"title": "Reading the news", "detail": "A quick look.",
                 "starts_at": now.isoformat(),
                 "ends_at": (now + timedelta(minutes=5)).isoformat()}],
            "user_nature": [
                {"title": "The user checks figures against the paper.",
                 "content": "They verify before they trust.",
                 "confidence": 5, "evidence": ["the scripts"],
                 "reasoning": "seen here", "states": [1, "[state 9]"],
                 "kind": "personal"}]})
        written = report["states_written"]
        self.assertEqual([card["asked_as"] for card in written], [1, 2])
        state = self.app.states.get(written[0]["id"])
        self.assertEqual(state.metadata["written_by"]["model"], "claude-test-1")
        records = self.app.library.records(USER_NATURE)
        mine = [r for r in records if r.title.startswith("The user checks")]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].metadata.get("written_by"),
                         "claude-test-1 (anthropic)")
        links = self.app.db.query("SELECT state_id, record_id FROM nature_links")
        # State 1 is linked; "state 9" does not exist and is not.
        self.assertEqual([(row["state_id"], row["record_id"]) for row in links],
                         [(written[0]["id"], mine[0].id)])
        # And each step's row on the panel says so too.
        steps = {step["id"]: step for step in report["steps"]}
        self.assertEqual(steps["step_state"]["written_by"]["model"],
                         "claude-test-1")

    def test_step_2_is_asked_for_the_states_and_sees_them_numbered(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import STEP_PROMPTS

        self.assertIn('"states"', STEP_PROMPTS["step_nature"]["returns"])
        now = utcnow()
        report = self._run({"states": [
            {"title": "Rebuilding the replication package", "detail": "Scripts.",
             "starts_at": now.isoformat(),
             "ends_at": (now + timedelta(minutes=10)).isoformat()}]})
        steps = {step["id"]: step for step in report["steps"]}
        self.assertIn("[state 1, not written yet]", steps["step_nature"]["prompt"])



class StepTwoIsPausedTests(unittest.TestCase):
    """Who the user is is learned from their lessons now: the heartbeat's
    step 2 is kept and not run - no call for it, nothing written."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        _heartbeat_module.PAUSED_STEPS = _PAUSED

    def tearDown(self) -> None:
        _heartbeat_module.PAUSED_STEPS = ()
        self.agent.cleanup()

    def test_the_agent_does_not_run_step_two(self) -> None:
        self.assertIn("step_nature", _PAUSED)
        asked = []

        class Model:
            def invoke(self, request, role=""):
                asked.append(request.system or "")
                reply = type("R", (), {})()
                reply.text = json.dumps({"states": [], "user_nature": [{
                    "title": "The user likes tea.", "detail": "d", "kind": "personal"}],
                    "notifications": []})
                reply.tool_calls, reply.usage, reply.model, reply.provider = [], None, "m", "p"
                reply.raw_content = reply.text
                return reply

        self.app.heartbeat.models = Model()
        self.app.raw_store.add_event(ListenerEvent(
            event_type="window.focus", source="window", application="Editor",
            data={"window_title": "notes.md"}))
        report = self.app.heartbeat.run_once(trigger="manual_debug")
        self.assertEqual(report.get("paused_steps"), ["step_nature"])
        self.assertFalse(any("WORK OUT WHAT THE USER IS LIKE" in one for one in asked))
        self.assertNotIn("nature_recorded", report)
        self.assertEqual(list(self.app.library.files(USER_NATURE)), [])

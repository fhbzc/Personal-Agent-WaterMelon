"""Agent Core workflow, tool surface and the local HTTP API."""

from __future__ import annotations

import json
import socket
import unittest
import urllib.error
import urllib.request
from datetime import timedelta

from support import ROOT, TemporaryAgent, keyed

from personalized_agent.common.events.models import utcnow


class AgentCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_message_produces_an_answer_and_a_conversation(self) -> None:
        result = self.app.agent.handle_message("what am I working on?")
        self.assertTrue(result["answer"])
        self.assertTrue(result["conversation_id"])
        messages = self.app.conversations.messages(result["conversation_id"])
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])

    def test_instruction_and_response_are_recorded_as_raw_edits(self) -> None:
        self.app.agent.handle_message("remember that I like short answers")
        events = {e.event_type for e in self.app.raw_store.recent_edits()}
        self.assertIn("agent.instruction_received", events)
        # Nothing is gathered before it answers, so nothing is recorded as
        # retrieved.
        self.assertNotIn("agent.context_retrieved", events)
        self.assertIn("agent.response", events)


    def test_the_chat_runs_no_python(self) -> None:
        """The user's choice: running code is the work runs' job, isolated by
        the operating system; the chat is not offered it."""
        names = {spec.name for spec in self.app.agent.tools.specs}
        self.assertNotIn("run_python", names)

    def test_a_scheduled_run_cannot_schedule(self) -> None:
        """A task that runs the agent does not make more tasks: the tool is
        not offered to it, while the chat still has it."""
        offered = []
        invoke = self.app.models.invoke

        def watching(request, *args, **kwargs):
            if request.task == "agent_instruction":
                offered.append({spec.name for spec in request.tools or []})
            return invoke(request, *args, **kwargs)

        self.app.models.invoke = watching
        self.app.agent.scheduled_runner("check Northbank Invest",
                                        {"task_id": "t1", "origin": "scheduler"})
        self.app.agent.run_instruction("what is on today?")
        self.assertNotIn("schedule_task", offered[0])
        self.assertIn("schedule_task", offered[-1])

    def test_write_file_tool_refuses_paths_outside_the_workspace(self) -> None:
        outcome = self.app.agent.tools.call(
            "write_file", {"path": str(self.agent.dir / "escape.txt"), "content": "x"}
        )
        self.assertEqual(outcome["error"], "security_violation")
        self.assertFalse((self.agent.dir / "escape.txt").exists())

    def test_write_and_read_file_tools_work_inside_the_workspace(self) -> None:
        self.app.agent.tools.call("write_file", {"path": "documents/a.txt", "content": "hello"})
        outcome = self.app.agent.tools.call("read_file", {"path": "documents/a.txt"})
        self.assertEqual(outcome["content"], "hello")

    def test_schedule_task_tool_creates_a_real_scheduler_entry(self) -> None:
        when = (utcnow() + timedelta(hours=3)).isoformat()
        # Section 52.1: a task carries a title and a detail, or is not made.
        outcome = self.app.agent.tools.call(
            # Section 52.2: and whether it interrupts, which is not optional.
            "schedule_task", {"title": "Call the supplier back",
                              "description": "They asked for a decision on the quote.",
                              "notify": "notify",
                              "scheduled_at": when}
        )
        self.assertIn("task_id", outcome)
        self.assertIsNotNone(self.app.scheduler_store.get(outcome["task_id"]))


    def test_model_unavailable_produces_a_visible_message(self) -> None:
        from personalized_agent.models.base import (
            AvailabilityState,
            FailureClass,
            ModelAvailability,
        )

        # Not a lost sign-in: that one the application asks for itself, once
        # (test_one_message.py).
        blocked = ModelAvailability(available=False,
                                    state=AvailabilityState.QUOTA_EXHAUSTED,
                                    reason="credit used up",
                                    failure_class=FailureClass.USER_ACTION_REQUIRED)
        self.app.models.check_availability = lambda role="primary", force=False: blocked
        result = self.app.agent.handle_message("hello")
        self.assertIn("unavailable", result["answer"])
        self.assertTrue(self.app.notifications.recent())


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.port = _free_port()
        self.app.config.set("api_port", self.port)
        from personalized_agent.api.server import ApiServer

        self.server = ApiServer(self.app)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self) -> None:
        self.server.stop()
        self.agent.cleanup()

    def get(self, path: str):
        with urllib.request.urlopen(keyed(self.app, f"{self.base}{path}"), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def post(self, path: str, payload: dict):
        request = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(keyed(self.app, request), timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_projects_are_learned_from_the_api(self) -> None:
        """Learning is started from the page, project by project; there is
        one experience, and nothing clears what it learned."""
        said = self.get("/api/experience")
        self.assertNotIn("test", said)
        self.assertNotIn("scope", said)
        started = self.post("/api/experience/learn", {"units": ["task-nowhere"]})
        self.assertEqual(started["started"], [])
        for unit in said["units"]:
            self.assertIn("tokens", unit["learning"])

    def test_experience_nature_returns_every_lesson_as_an_example(self) -> None:
        """A nature, opened from the page: each lesson it was learned from or
        linked to is one of its examples, newest first, with its history."""
        from personalized_agent.heartbeat.experience.stage_natures import write_nature
        experience = self.app.state_heartbeat.experience
        store = experience.store
        stages = [store.add_stage({"task_id": "task-a", "kind": "evaluation",
                                   "summary": f"lesson {n}", "states": [],
                                   "began": f"2026-09-0{n}T10:00:00+00:00",
                                   "ended": f"2026-09-0{n}T11:00:00+00:00"})
                  for n in range(1, 6)]
        self.assertTrue(write_nature(
            self.app.library, store,
            {"title": "Reads every figure on paper", "aspect": "evaluation",
             "content": "prints them",
             # A real case: the user's own words (an example with neither
             # words nor a place it can be opened is not kept).
             "example": {"said": "print it, I can't judge it on screen",
                         "what": "the first, printed"}},
            [stages[0]["id"], stages[1]["id"]], {"model": "m", "provider": "p"}))
        record = next(r for r in self.app.library.records("user_nature")
                      if r.title == "Reads every figure on paper")
        for stage in stages[2:]:
            store.link_stage_nature(stage["id"], record.id)
        said = self.get(f"/api/experience/nature?id={record.id}")
        self.assertEqual(said["examples_total"], 5)
        self.assertEqual([e["id"] for e in said["examples"]],
                         [s["id"] for s in reversed(stages)])
        self.assertEqual(said["examples"][-1]["what"], "the first, printed")
        self.assertEqual(said["examples"][-1]["said"], "print it, I can't judge it on screen")
        for field in ("title", "what", "said", "label", "summary", "project", "task", "began",
                      "ended", "deliverable", "locations"):
            self.assertIn(field, said["examples"][0])
        self.assertEqual(said["history"], [])

    def test_an_unchanged_answer_is_not_sent_again(self) -> None:
        """A page following a round's report - megabytes, four times a
        second - is told when what it has is still the answer."""
        with urllib.request.urlopen(keyed(self.app, f"{self.base}/api/state_heartbeat/progress"),
                                    timeout=30) as response:
            tag = response.headers["ETag"]
            first = response.read()
        self.assertTrue(tag)
        request = urllib.request.Request(
            f"{self.base}/api/state_heartbeat/progress",
            headers={"If-None-Match": tag})
        with self.assertRaises(urllib.error.HTTPError) as said:
            urllib.request.urlopen(keyed(self.app, request), timeout=30)
        self.assertEqual(said.exception.code, 304)
        self.assertEqual(said.exception.read(), b"")
        # A tag that is not the answer's gets the whole answer.
        request = urllib.request.Request(
            f"{self.base}/api/state_heartbeat/progress",
            headers={"If-None-Match": '"stale"'})
        with urllib.request.urlopen(keyed(self.app, request), timeout=30) as response:
            self.assertEqual(response.read(), first)

    def test_status_endpoint(self) -> None:
        status = self.get("/api/status")
        self.assertIn("platform", status)
        self.assertIn("library", status)

    def test_static_ui_is_served(self) -> None:
        with urllib.request.urlopen(f"{self.base}/", timeout=10) as response:
            body = response.read().decode("utf-8")
        from personalized_agent.version import APP_NAME
        self.assertIn(f"<title>{APP_NAME}</title>", body)

    def test_settings_roundtrip(self) -> None:
        payload = self.get("/api/settings")
        self.assertIn("schema", payload)
        self.post("/api/settings", {"values": {"font_size": 18}})
        self.assertEqual(self.get("/api/settings")["values"]["font_size"], 18)

    def test_invalid_setting_is_rejected(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/settings", {"values": {"log_level": "NOPE"}})
        self.assertEqual(ctx.exception.code, 400)

    def test_calendar_task_lifecycle_through_the_api(self) -> None:
        when = (utcnow() + timedelta(days=1)).isoformat()
        created = self.post("/api/tasks", {"title": "review", "scheduled_at": when})
        # Section 52.2: silence is right for what the agent queues for itself,
        # but a reminder the user sat down and made is one they want to be
        # reminded by, so the form's default follows what they asked for.
        self.assertTrue(created["task_payload"]["notify_user"])
        # And a quiet one is a choice the form can still make.
        quiet = self.post("/api/tasks", {"title": "quiet note", "scheduled_at": when,
                                         "notify": False})
        self.assertFalse(quiet["task_payload"]["notify_user"])
        # Only notifications go on the calendar: nothing there executes
        # anything, so asking for another kind is refused, and nothing is made.
        with self.assertRaises(urllib.error.HTTPError) as refused:
            self.post("/api/tasks", {"title": "quiet check", "scheduled_at": when,
                                     "execution_type": "model_task"})
        self.assertEqual(refused.exception.code, 400)
        self.assertNotIn("quiet check",
                         [t.title for t in self.app.scheduler_store.list(limit=50)])
        listed = self.get("/api/tasks")
        self.assertIn(created["id"], [t["id"] for t in listed["tasks"]])
        self.post("/api/tasks/action", {"id": created["id"], "action": "disable"})
        self.assertEqual(self.app.scheduler_store.get(created["id"]).status, "disabled")

    def test_nothing_waits_to_be_allowed(self) -> None:
        """No event asks first: one that says it should runs at its time, like
        any other, and nothing is left waiting for a "run now"."""
        from personalized_agent.common.events.models import ScheduledTask
        task = self.app.scheduler.create_task(ScheduledTask(
            title="Tidy the exports", description="Deletes old exports.",
            scheduled_at=utcnow(), execution_type="notification",
            task_payload={"approval": "ask_first", "message": "Tidy the exports",
                          "notify_user": False}))
        self.assertNotIn("approval", task.task_payload)
        self.app.scheduler.tick()
        self.assertEqual(self.app.scheduler_store.get(task.id).status, "completed")
        self.assertEqual(self.app.questions.pending(), [])

    def test_a_notification_asks_nothing(self) -> None:
        self.app.notifications.notify("Stretch", "A short break.",
                                      metadata={"task_id": "t1"})
        self.assertEqual(self.app.questions.pending(), [])

    def test_library_endpoints(self) -> None:
        """A record is read by its id - there is no listing to browse, and
        nothing searches the library."""
        from personalized_agent.data_manage.library import USER_FEEDBACK, Record
        record_id = self.app.library.write(Record(
            folder=USER_FEEDBACK, title="note", summary="s", body="body",
            source="test")).id
        for gone in ("/api/library", "/api/library/intro?folder=user_nature"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(gone)
            self.assertEqual(ctx.exception.code, 404)
        detail = self.get(f"/api/library/record?id={record_id}&folder=user_feedback")
        self.assertEqual(detail["title"], "note")
        self.assertEqual(detail["body"], "body")
        self.assertEqual(self.get(f"/api/library/record?id={record_id}")["title"], "note")


    def test_ingest_requires_authentication(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/listener/ingest",
                      {"event_type": "integration.test", "data": {}})
        self.assertEqual(ctx.exception.code, 401)

    def test_unknown_route_is_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/does-not-exist")
        self.assertEqual(ctx.exception.code, 404)

    def test_foreign_host_header_is_refused(self) -> None:
        request = urllib.request.Request(f"{self.base}/api/status",
                                         headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(ctx.exception.code, 403)

    def test_debugger_snapshot_responds_promptly(self) -> None:
        """This endpoint aggregates provider state; it must not block on the network."""
        import time

        started = time.perf_counter()
        self.get("/api/debugger")
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 8.0, f"/api/debugger took {elapsed:.1f}s")

    def test_debugger_snapshot_shape(self) -> None:
        snapshot = self.get("/api/debugger")
        for key in ("model_metrics", "system_metrics", "agent_metrics"):
            self.assertIn(key, snapshot)
        self.assertIn("totals", snapshot["model_metrics"])

    def test_models_endpoint_never_returns_credentials(self) -> None:
        self.app.credentials.set("anthropic.api_key", "sk-ant-secret-value")
        payload = json.dumps(self.get("/api/models"))
        self.assertNotIn("sk-ant-secret-value", payload)


if __name__ == "__main__":
    unittest.main()


class AgentProgressTests(unittest.TestCase):
    """Section 15.1: what the agent is doing while someone waits on it.

    A request that takes twenty seconds and says only "thinking" is
    indistinguishable from one that has hung.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.observability = self.app.observability

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_stages_of_a_turn_are_recorded_as_they_happen(self) -> None:
        self.app.agent.handle_message("hello")
        finished = list(self.observability._recent_tasks)[0]
        stages = [step["stage"] for step in finished.get("steps", [])]
        self.assertNotIn("Looking through what I know", stages)
        self.assertIn("Thinking", stages)
        self.assertIn("Writing the answer", stages)
        self.assertEqual(stages, sorted(stages, key=lambda s: stages.index(s)),
                         "the stages are in the order they happened")

    def test_progress_is_visible_while_the_turn_is_still_running(self) -> None:
        seen = []

        def watch(task_id, name, arguments):
            # Inside a tool call the turn is demonstrably mid-flight.
            state = self.observability.active_progress(kind="user")
            if state:
                seen.extend(step["stage"] for step in state["steps"])
            return {"ok": True}

        self.app.agent._execute_tool = watch
        self.observability.task_started("task_x", "user", "a question")
        self.observability.task_step("task_x", "Thinking", "the model")
        state = self.observability.active_progress(kind="user")
        self.assertTrue(state["steps"], "a running task reports its stages")
        self.assertEqual(state["steps"][-1]["stage"], "Thinking")

    def test_a_finished_stage_records_how_long_it_took(self) -> None:
        self.observability.task_started("task_y", "user", "a question")
        self.observability.task_step("task_y", "first", "")
        self.observability.task_step("task_y", "second", "")
        steps = self.observability.active_progress()["steps"]
        self.assertIn("finished_at", steps[0], "a stage that gave way is finished")
        self.assertNotIn("finished_at", steps[1], "the current stage has not")

    def test_a_background_heartbeat_is_not_shown_to_someone_waiting(self) -> None:
        # Otherwise a person watching their own question suddenly sees the
        # agent doing something they did not ask for.
        self.observability.task_started("task_z", "heartbeat", "periodic work")
        self.observability.task_step("task_z", "Thinking", "")
        self.assertIsNone(self.observability.active_progress(kind="user"))
        self.assertIsNotNone(self.observability.active_progress(kind="heartbeat"))

    def test_nothing_running_reports_nothing(self) -> None:
        self.assertIsNone(self.observability.active_progress(kind="user"))

    def test_the_step_list_cannot_grow_without_bound(self) -> None:
        from personalized_agent.common.observability.metrics import MAX_STEPS

        self.observability.task_started("task_w", "user", "a long one")
        for index in range(MAX_STEPS + 40):
            self.observability.task_step("task_w", f"step {index}", "")
        steps = self.observability.active_progress()["steps"]
        self.assertLessEqual(len(steps), MAX_STEPS)
        self.assertEqual(steps[-1]["stage"], f"step {MAX_STEPS + 39}",
                         "the most recent stages are the ones kept")

    def test_a_step_for_a_task_that_is_not_running_is_ignored(self) -> None:
        self.observability.task_started("task_a", "user", "a question")
        self.observability.task_step("task_b", "from somewhere else", "")
        self.assertEqual(self.observability.active_progress()["steps"], [])



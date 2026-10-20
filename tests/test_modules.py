"""The modules (common/modules.py): what runs by itself, each of which can be
turned off to spend fewer tokens - its steps not run, its clock not ticking -
and every model call counted under its module."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from support import TemporaryAgent

from personalized_agent.common import modules


class WhichModuleTests(unittest.TestCase):

    def test_each_call_to_its_module(self) -> None:
        self.assertEqual(modules.module_of("heartbeat:step_state"), "core")
        self.assertEqual(modules.module_of("heartbeat:step_people"), "connections")
        self.assertEqual(modules.module_of("heartbeat:step_notify"), "calendar")
        self.assertEqual(modules.module_of("heartbeat_trigger"), "calendar")
        self.assertEqual(modules.module_of("heartbeat:step_todo"), "tasks")
        self.assertEqual(modules.module_of("state_heartbeat"), "tasks")
        self.assertEqual(modules.module_of("connections"), "recommend")
        self.assertEqual(modules.module_of("reading"), "recommend")
        self.assertEqual(modules.module_of("lessons_work"), "experience")
        # Natures grouped beside a round run by themselves; a task learned
        # into stages, and natures merged, the user starts.
        self.assertEqual(modules.module_of("experience"), "experience")
        self.assertEqual(modules.module_of("experience_stages"), "experience_manual")
        self.assertEqual(modules.module_of("experience_merge"), "experience_manual")
        self.assertEqual(modules.module_of("lessons_connections"), "connections")
        self.assertEqual(modules.module_of("heartbeat"), "heartbeat (before modules)")

    def test_nothing_spends_unseen_under_other(self) -> None:
        # The data heartbeat and knowledge library, removed from the
        # application: Other - their calls stay in the chart's month.
        for task in ("data_heartbeat", "library_compaction", "library_relabel"):
            self.assertEqual(modules.module_of(task), "other", task)
        # A task in no module, by its own name - even run by the user.
        self.assertEqual(modules.module_of("brand_new_step"), "task:brand_new_step")
        self.assertEqual(modules.module_of("brand_new_step", by_user=True),
                         "task:brand_new_step")
        self.assertEqual(modules.module_name("task:brand_new_step"),
                         "Not in a module: brand_new_step")
        self.assertEqual(modules.module_of(""), "other")

    def test_what_the_user_started_each_kind_apart(self) -> None:
        for task, key in (("work_run", "manual_work"), ("agent_instruction", "manual_chat"),
                          ("chat_followup", "manual_chat"), ("scheduled_task", "manual_scheduled"),
                          ("task_completion", "manual_scheduled"),
                          ("lessons_package", "manual_lessons"), ("diagnostic", "manual_other")):
            self.assertEqual(modules.module_of(task), key, task)
            self.assertTrue(modules.is_manual(key))
        self.assertEqual(modules.module_name("manual_chat"), "Started by you: Chat")

    def test_a_function_run_with_a_button_is_apart(self) -> None:
        self.assertEqual(modules.module_of("connections", by_user=True), "recommend:by_you")
        self.assertTrue(modules.is_manual("recommend:by_you"))
        self.assertEqual(modules.module_name("recommend:by_you"),
                         "Newspaper (started by you)")
        # What the user starts anyway is not "by you" twice.
        self.assertEqual(modules.module_of("work_run", by_user=True), "manual_work")

    def test_each_says_what_turning_it_off_means(self) -> None:
        for one in modules.MODULES:
            self.assertTrue(one["what"] and one["off"], one["key"])
        # Passive and active looking after contacts, said apart.
        self.assertIn("on your screen", modules.BY_KEY["connections"]["what"])
        self.assertIn("on the web is Newspaper's", modules.BY_KEY["connections"]["what"])
        self.assertIn("contacts looked up on the web", modules.BY_KEY["recommend"]["what"])
        self.assertIn("turn it off too", modules.BY_KEY["connections"]["off"])


class TurnedOffTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _off(self, key):
        self.app.config.set(modules.BY_KEY[key]["setting"], False)

    def test_a_heartbeat_step_of_a_module_off_is_not_run(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import _step_on
        for step in ("step_state", "step_people", "step_notify"):
            self.assertTrue(_step_on(self.app.config, step))
        self._off("calendar")
        self._off("connections")
        self._off("tasks")
        self.assertTrue(_step_on(self.app.config, "step_state"), "the core is always on")
        self.assertFalse(_step_on(self.app.config, "step_notify"))
        self.assertFalse(_step_on(self.app.config, "step_people"))
        # And calendar management's triggers.
        self.assertFalse(self.app.triggers.enabled())

    def test_each_heartbeat_step_is_counted_under_its_own_function(self) -> None:
        """Every step's calls carry the step's own tag - one tag for all four
        put every call under the core, and the functions near nothing."""
        self.app.heartbeat.run_once(trigger="manual_debug")
        tags = {row["task"] for row in self.app.db.query(
            "SELECT DISTINCT task FROM model_calls WHERE task LIKE 'heartbeat:%'")}
        self.assertTrue(tags, "the round asked a model")
        self.assertGreater(len(tags), 1, f"one tag for every step: {tags}")
        self.assertTrue({"heartbeat:step_notify", "heartbeat:step_people"} & tags, tags)
        self.assertEqual({modules.module_of(tag) for tag in tags} - {"core", "calendar",
                                                                     "connections", "tasks"},
                         set())

    def test_task_organisation_off_no_state_round(self) -> None:
        self._off("tasks")
        said = self.app.state_heartbeat.due_now()
        self.assertFalse(said["run"])
        self.assertIn("Task organisation is turned off", said["why"])

    def test_each_kind_of_lesson_its_modules(self) -> None:
        engine = self.app.lessons
        engine.record("notifications", "calact_1", "The user deleted this event.",
                      action="delete")
        engine.record("connections", "cact_1", "The user deleted Ana.", action="delete")
        self.assertEqual(set(engine.due()) & {"notifications", "connections"},
                         {"notifications", "connections"})
        self._off("calendar")
        self.assertNotIn("notifications", engine.due())
        self.assertIn("connections", engine.due())
        self._off("connections")
        self.assertNotIn("connections", engine.due())

    def test_recommendation_off_no_issue_and_no_reading(self) -> None:
        tz = datetime.now().astimezone().tzinfo
        at_nine = datetime.now(tz).replace(hour=9, minute=0)
        self.assertTrue(self.app.issue_clock.due(at_nine))
        self._off("recommend")
        self.assertFalse(self.app.issue_clock.due(at_nine))

    def test_the_page_says_each_and_what_it_spent(self) -> None:
        from personalized_agent.api.routes import build_routes
        now = datetime.now(timezone.utc).isoformat()
        for task, tokens in (("heartbeat:step_notify", 1000), ("heartbeat_trigger", 500),
                             ("state_heartbeat", 300), ("work_run", 7000),
                             ("heartbeat", 100), ("experience_stages", 2000)):
            self.app.db.execute(
                "INSERT INTO model_calls (id, created_at, provider, model, task, input_tokens, "
                "output_tokens, total_tokens, latency_ms, ok, estimated_cost) VALUES "
                "(?, ?, 'p', 'm', ?, ?, 0, ?, 1, 1, 0.01)",
                (f"call_{task}", now, task, tokens, tokens))
        self._off("experience")

        class _Req:
            def int_param(self, key, default):
                return default

        said = build_routes(self.app)[("GET", "/api/modules")](_Req())
        self.assertEqual(said["hours"], 72)
        by_key = {one["key"]: one for one in said["modules"]}
        self.assertEqual(by_key["calendar"]["spent"]["total_tokens"], 1500)
        # Its share of the cost: of what every function spent by itself
        # (0.02 of 0.04), and of everything, the user's own work too (of 0.06).
        self.assertEqual(by_key["calendar"]["spent"]["of_automatic"], 50.0)
        self.assertEqual(by_key["calendar"]["spent"]["of_all"], 33.3)
        # What the user started: a work run and a task learned into stages,
        # together - neither a function's share.
        self.assertEqual(said["manual"]["of_all"], 33.4)
        self.assertEqual(said["manual"]["total_tokens"], 9000)
        # The heartbeat from before its steps were counted apart, shown too.
        self.assertEqual([(one["name"], one["of_automatic"]) for one in said["unassigned"]],
                         [("heartbeat (before modules)", 25.0)])
        self.assertEqual(said["manual"]["of_automatic"], 0.0)
        self.assertEqual(by_key["tasks"]["spent"]["total_tokens"], 300)

        self.assertFalse(by_key["experience"]["enabled"])
        self.assertTrue(by_key["calendar"]["enabled"])
        self.assertEqual(said["core"]["name"], "Activity and states")


class ByTheUserTests(unittest.TestCase):
    """A function's run started with a button: its calls the user's - in the
    log, and not in the function's own share."""

    def setUp(self) -> None:
        from personalized_agent.common.observability import started
        self.started = started
        started.clear()
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.started.clear()
        self.agent.cleanup()

    def test_a_window_while_the_run_goes(self) -> None:
        going = [True]
        self.started.STARTING, before = 0.0, self.started.STARTING
        try:
            self.started.while_running(("connections", "reading"), lambda: going[0])
            self.assertTrue(self.started.by_user("connections"))
            self.assertTrue(self.started.by_user("reading"))
            self.assertFalse(self.started.by_user("state_heartbeat"))
            going[0] = False
            self.assertFalse(self.started.by_user("connections"), "closed with the run")
            with self.started.now(("heartbeat:",)):
                self.assertTrue(self.started.by_user("heartbeat:step_notify"))
            self.assertFalse(self.started.by_user("heartbeat:step_notify"))
        finally:
            self.started.STARTING = before

    def test_the_call_records_it_and_the_share_leaves_it_out(self) -> None:
        from personalized_agent.models.base import ModelRequest
        with self.started.now(("connections",)):
            try:
                self.app.models.invoke(ModelRequest(messages=[{"role": "user",
                                                               "content": "hi"}],
                                                    task="connections"), role="primary")
            except Exception:
                self.skipTest("no model to call here")
        row = self.app.db.query_one("SELECT by_user FROM model_calls WHERE task = 'connections'")
        self.assertEqual(int(row["by_user"]), 1)
        spent = modules.shares(self.app.db, 72)
        self.assertIn("recommend:by_you", spent)
        self.assertEqual(spent["recommend:by_you"]["of_automatic"], 0.0)
        said = modules.described(self.app.config, spent)
        self.assertIn("Newspaper (started by you)",
                      [one["name"] for one in said["manual_parts"]])


class TheChartByModuleTests(unittest.TestCase):
    """The Overview's token chart, by module: each module its own line, and
    still after the page refreshes - the refresh used to set the colours
    back to the kinds of work, and every module was drawn as "Other"."""

    def setUp(self) -> None:
        from test_panels import _javascript_engine
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_modules_stay_modules_after_a_refresh(self) -> None:
        from test_connections_panel import _render
        today = datetime.now().date().isoformat()
        by_task = {f"task_{n}": {} for n in range(9)}
        rows = [{"day": today, "task": task, "module": module, "calls": 1,
                 "input_tokens": 10, "output_tokens": 10, "total_tokens": tokens,
                 "estimated_cost": 0.01}
                for task, module, tokens in (("work_run", "manual", 900),
                                             ("state_heartbeat", "tasks", 300),
                                             ("reading", "recommend", 200),
                                             ("heartbeat:step_notify", "calendar", 100))]
        stub = {"/api/debugger": {"model_metrics": {"by_task": by_task,
                                                    "tokens_over_time": []}},
                "/api/debugger/tokens_by_day": {"days": [today], "rows": rows},
                "/api/modules": modules.described(None, {})}
        steps = ("const pick=document.querySelector('#dbg-tokens-group');"
                 "pick.value='module';pick.dispatchEvent(new Event('change'));await wait(300);"
                 "await loadTokenUsage();await wait(300);"
                 "out.legend=text(document.querySelector('#dbg-tokens-legend'));")
        result = _render(self.engine, stub, steps)
        self.assertEqual(result["err"], "")
        legend = result["out"]["legend"]
        for name in ("Started by you", "Task organisation", "Newspaper",
                     "Calendar management"):
            self.assertIn(name, legend)
        self.assertNotIn("Other", legend)


if __name__ == "__main__":
    unittest.main()

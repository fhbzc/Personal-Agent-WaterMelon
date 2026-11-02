"""ONE THING, MORE THAN ONE WAY (the user, 2026-11-01): a message to
someone may go by Slack or by email - up to 3 ways, how many the agent's to
choose, each complete with its own how and check; made one at a time, and
each opened as soon as it is handed over, while the next is made. Everyone
here is made up."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent.work import handed
from personalized_agent.agent.work.runner import Run

BY_SLACK = {"kind": "message", "title": "Tell Mira the date moved", "app": "Slack",
            "to": "Mira Holt", "text": "hey, moved to Fri 10am", "how": "Paste it, send it.",
            "check": ["The day"], "choice": "Tell Mira the date moved", "way": "By Slack"}
BY_EMAIL = {"kind": "email", "title": "Tell Mira the date moved", "to": ["mira@example.org"],
            "subject": "The date moved", "body": "Hi Mira,\n\nIt is Friday at 10 now.",
            "how": "Open it, read it, send it.", "check": ["The day"],
            "choice": "Tell Mira the date moved", "way": "By email"}


class _Ask(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def require(self, key):
        return self[key]

    def int_param(self, key, default=0):
        return int(self.get(key, default))


class WaysTests(unittest.TestCase):

    def test_a_way_is_kept_with_its_thing_and_said_so(self) -> None:
        item = handed.checked(BY_SLACK, lambda p: None)
        self.assertEqual((item["choice"], item["way"]), ("Tell Mira the date moved", "By Slack"))
        self.assertTrue(handed.shown(item).startswith("ONE WAY (By Slack) OF: Tell Mira"))
        self.assertEqual(handed.note(item)["title"], "Tell Mira the date moved - By Slack")
        plain = handed.checked(dict(BY_SLACK, choice="", way=""), lambda p: None)
        self.assertNotIn("choice", plain)
        self.assertIn("MAKE THEM ONE AT A TIME", handed.HAND_OVER_RULES)

    def _run(self, items=()):
        saved = []
        run = SimpleNamespace(plan={"handed": list(items)}, _event=lambda *a, **k: None,
                              owner=SimpleNamespace(save=lambda r: saved.append(1)),
                              _way_checks=lambda item: (True, []))
        return run, saved

    def test_at_most_three_each_ready_and_kept_at_once(self) -> None:
        run, saved = self._run()
        for n in range(3):
            said = Run._hand_over(run, handed.checked(
                dict(BY_SLACK, title=f"Way {n}", way=f"Way {n}"), lambda p: None))
            self.assertIsNone(said)
        self.assertEqual(len(saved), 3, "each kept at once - to open while the next is made")
        self.assertTrue(all(one["ready"] for one in run.plan["handed"]))
        fourth = Run._hand_over(run, handed.checked(dict(BY_SLACK, title="Way 3"),
                                                    lambda p: None))
        self.assertIn("at most 3 ways", fourth["error"])
        again = Run._hand_over(run, handed.checked(dict(BY_SLACK, title="Way 1",
                                                        text="changed"), lambda p: None))
        self.assertIsNone(again, "one of the three handed over again")
        self.assertEqual(len(run.plan["handed"]), 3)

    def test_one_the_user_changed_is_left_alone(self) -> None:
        item = dict(handed.checked(BY_SLACK, lambda p: None), edited_at="2026-11-01T10:00")
        run, _saved = self._run([item])
        said = Run._hand_over(run, handed.checked(dict(BY_SLACK, text="mine now"),
                                                  lambda p: None))
        self.assertIn("the user has changed this one", said["error"])
        self.assertEqual(run.plan["handed"][0]["text"], "hey, moved to Fri 10am")

    def test_a_way_the_checks_did_not_pass_is_kept_but_not_opened(self) -> None:
        run, saved = self._run()
        run._way_checks = lambda item: (False, ["[privacy check] a home address in it"])
        said = Run._hand_over(run, handed.checked(BY_SLACK, lambda p: None))
        self.assertIn("a home address in it", said["error"])
        self.assertIn("hand this way over again", said["error"])
        self.assertFalse(run.plan["handed"][0]["ready"], "it opens once the run is done")
        self.assertEqual(saved, [])
        run._way_checks = lambda item: (True, [])
        self.assertIsNone(Run._hand_over(run, handed.checked(BY_SLACK, lambda p: None)))
        self.assertTrue(run.plan["handed"][0]["ready"])
        self.assertEqual(len(run.plan["handed"]), 1, "fixed in place")

    def test_the_tool_says_what_the_run_refused(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools
        tools = WorkTools.__new__(WorkTools)
        tools.records_db = tools.people = None
        tools._voice_looked = []
        tools.on_hand_over = lambda item: {"error": "at most 3 ways of one thing"}
        tools.runtime = mock.Mock()
        tools.runtime.policy.write_root, tools.runtime.policy.extra_writable = ".", []
        self.assertEqual(tools.give_to_user(dict(BY_SLACK)),
                         {"error": "at most 3 ways of one thing"})


class CheckedAsSoonAsItIsDoneTests(unittest.TestCase):
    """The user, 2026-11-01: "隐私检查什么的是每个方案做好就检查" - the final
    checks on each way as it is handed over, before it opens."""

    def _run(self, answers, variant="", on=True):
        asked = []

        def ask(system, messages, tools, **how):
            asked.append((how.get("who"), system, messages[0]["content"]))
            return answers[how.get("who")]
        return SimpleNamespace(
            _checks_on=lambda: on, _going=lambda work=True, quiet=False: True,
            instruction="tell Mira the date moved", plan={"contract": {"goal": "Mira knows"}},
            _branch_words=lambda: [], variant=variant, checks_folder=Path("checks"),
            _event=lambda *a, **k: None, _note=lambda *a, **k: None, _ask=ask,
            _testing_tools=lambda: None, _at_once=lambda asks: [one() for one in asks]), asked

    PASSED = {"passed": True, "checked": ["the message"], "problems": []}

    def test_each_check_reads_this_way_alone_and_passes_it(self) -> None:
        run, asked = self._run({"quality": self.PASSED, "privacy": self.PASSED})
        item = handed.checked(BY_SLACK, lambda p: None)
        self.assertEqual(Run._way_checks(run, item), (True, []))
        self.assertEqual([who for who, _s, _c in asked], ["quality", "privacy"])
        for _who, _system, content in asked:
            self.assertIn("hey, moved to Fri 10am", content)
            self.assertIn("ONE WAY OF A THING", content)

    def test_what_a_check_found_is_said_back(self) -> None:
        run, _asked = self._run({"privacy": {"passed": False, "checked": ["the message"],
                                             "problems": [{"say": "a phone number in it",
                                                           "severity": "high"}]}},
                                variant="quick")
        passed, found = Run._way_checks(run, handed.checked(BY_SLACK, lambda p: None))
        self.assertFalse(passed)
        self.assertEqual(found, ["[privacy check] a phone number in it"])

    def test_nothing_read_back_has_not_passed(self) -> None:
        run, _asked = self._run({"privacy": {}}, variant="quick")
        self.assertFalse(Run._way_checks(run, handed.checked(BY_SLACK, lambda p: None))[0])

    def test_where_the_checks_are_off_nothing_is_asked(self) -> None:
        run, asked = self._run({}, on=False)
        self.assertEqual(Run._way_checks(run, handed.checked(BY_SLACK, lambda p: None)),
                         (True, []))
        self.assertEqual(asked, [])


class OpenedWhileTheNextIsMadeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        ready = dict(handed.checked(BY_EMAIL, lambda p: None), ready=True)
        plain = handed.checked(dict(BY_EMAIL, title="Notes", choice="", way=""), lambda p: None)
        self.app.db.execute(
            "INSERT INTO work_runs (id, unit_id, unit_name, folder, status, instruction, plan, "
            "log, final, started_at, ended_at, updated_at) VALUES ('run_w', 'task-w', 'Mira', "
            "'', 'running', 'tell her', ?, '[]', '', '2026-11-01T09:00:00', '', "
            "'2026-11-01T09:10:00')", (json.dumps({"handed": [ready, plain]}),))

        class _Going:
            def alive(self):
                return True
        self.app.work._running["run_w"] = _Going()
        from personalized_agent.api.routes import build_routes
        self.routes = build_routes(self.app)

    def tearDown(self) -> None:
        self.app.work._running.pop("run_w", None)
        self.agent.cleanup()

    def test_a_way_handed_over_opens_and_saves_while_the_work_goes_on(self) -> None:
        with mock.patch("personalized_agent.platform_layer.clipboard.copy", return_value=True), \
                mock.patch("webbrowser.open", return_value=True), \
                mock.patch("os.startfile", create=True):
            said = self.routes[("POST", "/api/work/handed/open")](_Ask(run="run_w", n=0))
        self.assertTrue(said.get("opened"), said)
        saved = self.app.work.save_handed("run_w", 0, "Hi Mira,\n\nFriday, 10.")
        self.assertEqual(saved["body"], "Hi Mira,\n\nFriday, 10.")

    def test_anything_else_still_waits_for_the_end(self) -> None:
        from personalized_agent.api.errors import ApiError
        with self.assertRaises(ApiError):
            self.routes[("POST", "/api/work/handed/open")](_Ask(run="run_w", n=1))
        self.assertEqual(self.app.work.save_handed("run_w", 1, "x"), {"error": "going"})


if __name__ == "__main__":
    unittest.main()

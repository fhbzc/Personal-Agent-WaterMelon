"""NO WORK PANEL (the user, 2026-11-06): work is followed and opened from To do and Work assigned,
and what was made is always there to open. Made-up work."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import support  # noqa: F401
from support import ROOT, TemporaryAgent

from personalized_agent.agent.work.runner import Run

WEB = ROOT / "personalized_agent" / "api" / "web"


class ThePageTests(unittest.TestCase):
    def test_no_way_to_it_is_left(self) -> None:
        page = (WEB / "index.html").read_text(encoding="utf-8")
        script = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertNotIn('data-tab="experience"', page)
        for words in (">See it in Work<", "Follow it in Work", "See the latest try in Work",
                      'startsWith("work=")', "see it in Work"):
            self.assertNotIn(words, script, words)


class WhatThereIsToOpenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.drafts = self.agent.app.drafts

    def _made(self, run, readable=lambda _run, path: Path(path)):
        store = SimpleNamespace(workspace=lambda _unit: {})
        app = SimpleNamespace(work=SimpleNamespace(readable_by=readable), state_heartbeat=SimpleNamespace(
            experience=SimpleNamespace(store=store)))
        return type(self.drafts)._made_by(SimpleNamespace(app=app), run, "u1")

    def test_what_it_handed_over_first(self) -> None:
        said = self._made({"id": "r1", "folder": "/f", "plan": {"handed": [
            {"kind": "email", "title": "Reply to the club"}]}})
        self.assertEqual(said["items"][0]["kind"], "email")

    def test_else_the_deliverable(self) -> None:
        said = self._made({"id": "r1", "folder": "/f", "plan": {"deck": {"deliverables": [
            {"location": "/f/report.docx"}]}}})
        self.assertEqual(said["result"], "/f/report.docx")

    def test_else_its_folder(self) -> None:
        self.assertEqual(self._made({"id": "r1", "folder": "/f", "plan": {}})["result"], "/f")

    def test_only_a_folder_gone_leaves_nothing(self) -> None:
        said = self._made({"id": "r1", "folder": "/f", "plan": {}}, lambda _run, _path: None)
        self.assertEqual(said, {"result": ""})


class TheReportIsTheAnswerTests(unittest.TestCase):
    def _ended(self, **plan):
        run = SimpleNamespace(plan={"last_report": "The tide table is correct.", **plan},
                              unit={"name": "Check the tide table"})
        Run._hand_over_its_report(run)
        return run.plan.get("handed")

    def test_with_nothing_else_to_open(self) -> None:
        item = self._ended()[0]
        self.assertEqual((item["kind"], item["title"], item["text"]),
                         ("answer", "Check the tide table", "The tide table is correct."))

    def test_not_over_what_was_handed_over(self) -> None:
        self.assertEqual(self._ended(handed=[{"kind": "email"}]), [{"kind": "email"}])

    def test_not_where_it_made_files(self) -> None:
        self.assertIsNone(self._ended(changed_files=[__file__]))

    def test_a_report_of_nothing_is_nothing(self) -> None:
        self.assertIsNone(self._ended(last_report=""))


COMMAND = {"id": "act_1", "kind": "action", "title": "Run a command outside the sandbox",
           "what": "tide --update", "why": "It needs the new tables"}
MODEL = {"id": "model_1", "kind": "model", "title": "The model: usage limit reached", "what": ""}


class WorkStatusAsksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.pending = [COMMAND]
        self.states = [{"run": "r1", "unit": "u1", "debug": False, "state": "waiting_for_you"}]
        self.app.work.live = lambda _run, _since, calls=0: {"pending": list(self.pending)}
        self.app.work.run_states = lambda: list(self.states)
        self.going = [{"state": "ongoing", "run": "r1", "unit": "u1"}]

    def test_it_says_what_it_waits_for_with_what_answers_it(self) -> None:
        notes, actions = self.app._work_asks(self.going)
        self.assertIn("Run a command outside the sandbox: tide --update", notes[0])
        self.assertIn("It needs the new tables", notes[1])
        self.assertEqual([one["label"] for one in actions], ["Approve", "Deny"])
        self.assertEqual(actions[0]["path"], "/api/work/approve")
        self.assertEqual(actions[0]["body"], {"run": "r1", "request": "act_1", "allow": True})
        self.assertIs(actions[1]["body"]["allow"], False)
        self.assertTrue(all(one["changes"] for one in actions), "read again, answered")

    def test_a_model_that_needs_them_has_its_own_answers(self) -> None:
        self.pending = [MODEL]
        labels = [one["label"] for one in self.app._work_asks(self.going)[1]]
        self.assertEqual(labels, ["Try again now", "End the run"])

    def test_on_the_row_of_the_list_beside_the_avatar(self) -> None:
        drafted = {"id": "d1", "text": "Fictional errand: tides", "detail": "", "at": "", "source": "add",
                   "work": {"versions": self.going, "next": 2}}
        self.app.drafts.assigned = lambda: [drafted]
        self.app.drafts.of_todos = lambda: {}
        rows = [row for part in self.app.quick_sections("todo") for row in part["rows"]]
        self.assertIn("tide --update", rows[0]["note"])
        self.assertEqual([one["label"] for one in rows[0]["actions"]][:2], ["Approve", "Deny"])

    def test_answered_it_goes_on_and_asks_nothing_more(self) -> None:
        from personalized_agent.api.routes import build_routes
        answered = []
        self.app.work.approve = lambda run, request, allow: answered.append(
            (run, request, allow)) or {"id": request}
        action = self.app._work_asks(self.going)[1][0]
        body = action["body"]
        request = SimpleNamespace(require=body.__getitem__, get=body.get)
        build_routes(self.app)[("POST", action["path"])](request)
        self.assertEqual(answered, [("r1", "act_1", True)])
        self.pending = []
        self.assertEqual(self.app._work_asks(self.going), ([], []))

    def test_a_version_not_going_is_not_asked(self) -> None:
        self.assertEqual(self.app._work_asks([{"state": "finished", "run": "r1"}]), ([], []))

    def test_told_once_and_it_pops(self) -> None:
        self.app.tell_work_waiting()
        self.app.tell_work_waiting()
        said = self.app.notifications.recent()
        self.assertEqual(len(said), 1)
        self.assertEqual(said[0]["kind"], "approval")
        self.assertIn("tide --update", said[0]["message"])
        self.assertEqual(self.app.notifications.waiting_count(), 0, "not kept in the list")

    def test_a_new_thing_asked_is_told_too(self) -> None:
        self.app.tell_work_waiting()
        self.pending = [dict(COMMAND, id="act_2", what="tide --again")]
        self.app.tell_work_waiting()
        self.assertEqual(len(self.app.notifications.recent()), 2)

    def test_a_debuggers_run_and_one_going_on_tell_nothing(self) -> None:
        self.states = [{"run": "r1", "debug": True, "state": "waiting_for_you"},
                       {"run": "r2", "debug": False, "state": "going"}]
        self.app.tell_work_waiting()
        self.assertEqual(self.app.notifications.recent(), [])


class DraftsKeptTests(unittest.TestCase):
    def test_listed_in_work_assigned_and_not_while_being_written(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        drafts = agent.app.drafts
        kept = drafts.create(text="Fictional errand: a page", source="add")
        other = drafts.create(text="Fictional errand: a note", source="add")
        agent.app.db.execute("UPDATE work_drafts SET status = 'ready', options = ? WHERE id = ?",
                             (json.dumps({"saved": True}), kept["id"]))
        agent.app.db.execute("UPDATE work_drafts SET status = 'ready' WHERE id = ?", (other["id"],))
        listed = drafts.assigned()
        self.assertEqual([one["id"] for one in listed], [kept["id"]])
        self.assertEqual(listed[0]["work"]["versions"][0]["state"], "drafting")


if __name__ == "__main__":
    unittest.main()

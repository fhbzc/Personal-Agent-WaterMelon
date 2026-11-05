"""WHAT IS STILL OPEN AND THE WORK IT BELONGS TO (the user, 2026-11-04): the state
heartbeat links notifications, to-dos and work assigned to tasks and projects -
several, or none; clicking a project shows them; each says what it is part of;
a model reading one is told. Everything here is made up."""

from __future__ import annotations

import json
import unittest
import urllib.request
from types import SimpleNamespace

import support  # noqa: F401
from support import keyed
from test_the_users_say import _WithAProject

from personalized_agent.agent.feedback.todo_tools import add_tools
from personalized_agent.agent.work.delegate import FRONT_NOTE, instruction_of
from personalized_agent.heartbeat.states.project_round import ProjectRound


class _Model:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def invoke(self, request, role=""):
        self.asked.append(request)
        return SimpleNamespace(text=json.dumps(self.answer), usage=None)


class ItemLinksTests(_WithAProject):

    def setUp(self) -> None:
        super().setUp()
        self.links = self.app.item_links

    def _todo(self, text="Measure the tide gauge"):
        return self.app.todos.add(text)["id"]

    def _link(self, answer):
        model = _Model(answer)
        self.hb.models = model
        run = ProjectRound(self.hb, self.hb.where("live"), {})
        run._link_what_is_open()
        return model, run.report

    def test_nothing_open_asks_no_model(self) -> None:
        model, _report = self._link({})
        self.assertEqual(model.asked, [])

    def test_one_call_says_what_each_belongs_to_several_or_none(self) -> None:
        first, second = self._todo(), self._todo("Buy milk")
        model, report = self._link({"links": {"I1": {}, "I2": {"tasks": ["T1", "T2"],
                                                               "projects": ["P1"]}}})
        self.assertEqual(len(model.asked), 1)
        sent = model.asked[0].messages[0]["content"]
        self.assertIn("Tide tables", sent)
        self.assertIn("Measure the tide gauge", sent)
        # The newest first.
        self.assertEqual(self.links.of("todo", second), [])
        self.assertEqual({one["kind"] for one in self.links.of("todo", first)}, {"task", "project"})
        self.assertEqual(len(self.links.of("todo", first)), 3)
        self.assertEqual(report["linked"], 2)

    def test_a_label_made_up_is_passed_over_and_what_is_not_answered_stands(self) -> None:
        todo = self._todo()
        self._link({"links": {"I1": {"projects": ["P1"]}}})
        self._link({"links": {"I9": {"projects": ["P1"]}, "I1x": {}}})
        self.assertEqual([one["id"] for one in self.links.of("todo", todo)], ["proj_a"])
        self._link({"links": {"I1": {"tasks": ["T99"], "projects": ["P77"]}}})
        self.assertEqual(self.links.of("todo", todo), [], "answered, with nothing that exists")

    def test_a_run_does_not_fail_for_it(self) -> None:
        self._todo()

        class Broken:
            def invoke(self, *_args, **_kwargs):
                raise RuntimeError("no model")
        self.hb.models = Broken()
        ProjectRound(self.hb, self.hb.where("live"), {})._link_what_is_open()

    def test_read_as_the_work_is_now(self) -> None:
        todo = self._todo()
        self.links.put("todo", todo, ["t1"], ["proj_a"])
        self.hb.rename_task("t1", "Check the tide gauge")
        self.assertIn("Check the tide gauge", [one["title"] for one in self.links.of("todo", todo)])
        self.hb.delete_task("t1")
        self.assertEqual([one["id"] for one in self.links.of("todo", todo)], ["proj_a"])
        self.hb.delete_project("proj_a")
        self.assertEqual(self.links.of("todo", todo), [])

    def test_clicking_a_project_shows_what_is_open_under_it(self) -> None:
        by_task, by_project, elsewhere = self._todo("One"), self._todo("Two"), self._todo("Three")
        said = self.app.notifications.notify("High water", "Gauge at noon", kind="proactive")
        self.links.put("todo", by_task, ["t1"], [])
        self.links.put("todo", by_project, [], ["proj_a"])
        self.links.put("notification", said.id, ["t2"], ["proj_a"])
        project = self.hb.projects_picture()["projects"][0]
        shown = {(one["kind"], one["text"]) for one in project["open_items"]}
        self.assertEqual(shown, {("todo", "One"), ("todo", "Two"), ("notification", "High water")})
        self.assertEqual(len(project["open_items"]), 3, "once, though linked twice")
        self.assertTrue(elsewhere)

    def test_one_done_is_no_longer_open_under_it(self) -> None:
        todo = self._todo()
        self.links.put("todo", todo, ["t1"], [])
        self.app.todos.tick(todo)
        self.assertEqual(self.hb.projects_picture()["projects"][0]["open_items"], [])

    # -- a model reading one is told -----------------------------------------------------
    def test_the_chats_to_do_list(self) -> None:
        todo = self._todo()
        self.links.put("todo", todo, [], ["proj_a"])
        tools = {}
        add_tools(SimpleNamespace(add=lambda spec, run: tools.update({spec.name: run})),
                  self.app.todos, self.links)
        self.assertIn("Tide tables", tools["todo_list"]({})["todos"][0]["part_of"])

    def test_the_lesson_from_an_answer(self) -> None:
        said = self.app.notifications.notify("High water", "Gauge at noon", kind="proactive")
        self.links.put("notification", said.id, [], ["proj_a"])
        self.app.learn_answer(self.app.notifications.get(said.id), "got_it")
        self.assertIn("Part of the user's work: Tide tables", self.lessons()[-1]["summary"])

    def test_work_started_from_a_to_do(self) -> None:
        todo = self._todo()
        self.links.put("todo", todo, [], ["proj_a"])
        draft = self.app.drafts.create(text="Measure", source="todo", options={"todo": todo},
                                       collect=False)
        context = self.app.drafts.collect(draft)
        self.assertIn("Tide tables", context["part_of"])
        told = instruction_of(dict(draft, context=context), {})
        self.assertIn("Part of the user's work: Tide tables", told)


class WhatThePageIsGivenTests(_WithAProject):

    def setUp(self) -> None:
        super().setUp()
        from personalized_agent.api.server import ApiServer
        from test_agent_and_api import _free_port
        self.port = _free_port()
        self.app.config.set("api_port", self.port)
        self.server = ApiServer(self.app)
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        super().tearDown()

    def get(self, path):
        url = keyed(self.app, f"http://127.0.0.1:{self.port}{path}")
        with urllib.request.urlopen(url, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_each_says_what_it_is_part_of(self) -> None:
        todo = self.app.todos.add("Measure the tide gauge")["id"]
        said = self.app.notifications.notify("High water", "Gauge at noon", kind="proactive")
        draft = self.app.drafts.create(text="Plot the tides", source="selection", collect=False)
        self.app.item_links.put("todo", todo, ["t1"], ["proj_a"])
        self.app.item_links.put("notification", said.id, [], ["proj_a"])
        self.app.item_links.put("assigned", draft["id"], ["t2"], [])
        listed = self.get("/api/todos")["todos"][0]
        self.assertEqual([one["title"] for one in listed["associated"]], ["Task t1", "Tide tables"])
        shown = self.get("/api/notifications?unread=true")
        self.assertEqual(shown["notifications"][0]["associated"][0]["title"], "Tide tables")
        self.assertEqual(shown["blocked"], "")
        for one in self.get("/api/work/assigned")["assigned"]:
            self.assertIn("associated", one)


class WhatIsInFrontTests(_WithAProject):
    """Delegated directly: the screen goes with it, said to be only that."""

    def test_the_model_is_told_it_may_have_nothing_to_do_with_it(self) -> None:
        front = {"application": "chrome.exe", "title": "Tide tables - notes"}
        draft = self.app.drafts.create(text="Book the train", source="shortcut",
                                       options={"front": front}, collect=False)
        context = self.app.drafts.collect(draft)
        self.assertEqual(context["front_screen"]["title"], "Tide tables - notes")
        self.assertIn("may have nothing to do with it", context["front_screen"]["note"])
        told = instruction_of(dict(draft, context=context), {})
        self.assertIn(FRONT_NOTE, told)
        self.assertIn("Tide tables - notes", told)

    def test_with_nothing_in_front_nothing_is_said(self) -> None:
        draft = self.app.drafts.create(text="Book the train", source="shortcut", collect=False)
        self.assertNotIn("front_screen", self.app.drafts.collect(draft))


if __name__ == "__main__":
    unittest.main()

"""The next steps on what is at the top: a project, or a task in no project.

The state heartbeat writes a short to-do list when it describes one - what
is still to do on it now, not what it is for - and the user may add a step
or delete one from the page. The next call about it is shown the list and
what the user changed, once, and writes the list again by four rules: follow
the user where it can, drop what is done, add what is new, and keep the
words of a step that is not done yet so that it stays the same step.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import TemporaryAgent, keyed

from personalized_agent.heartbeat.states import todo
from personalized_agent.heartbeat.states.tasks import (
    Kept, Project, Projects, Task)


def _reply(body):
    return type("R", (), {
        "text": json.dumps(body),
        "usage": type("U", (), {"input_tokens": 2, "output_tokens": 30,
                                "cached_input_tokens": 0,
                                "cache_written_tokens": 0})()})()


class _Model:
    """A model that answers each kind of question with its own body, and
    remembers what it was sent. `during` runs inside a call - which is
    where the user's edit lands when they make one while a call is out."""

    def __init__(self, project=None, status=None, summary=None, during=None):
        self.sent = []
        self.project = project
        self.status = status
        self.summary = summary
        self.during = during

    def invoke(self, request, **kwargs):
        system = str(getattr(request, "system", "") or "")
        message = request.messages[0]["content"]
        self.sent.append({"system": system, "message": message})
        if self.during is not None:
            during, self.during = self.during, None
            during()
        if system.startswith("SAY WHERE THIS PROJECT"):
            return _reply(self.project or {})
        if system.startswith("SAY WHERE THIS TASK"):
            return _reply(self.status or {})
        if system.startswith("SUMMARISE") and self.summary is not None:
            return _reply(self.summary)
        return _reply({})

    def resolve(self, *a, **k):
        return type("R", (), {"to_dict": lambda s: {}})()

    def check_availability(self, *a, **k):
        return type("A", (), {"available": True, "to_dict": lambda s: {},
                              "state": None})()


class _Refusing(_Model):
    def invoke(self, request, **kwargs):
        raise RuntimeError("the sign-in has expired")


def _worked(task_id, **described):
    """A task with six hours of work in it - enough to be drawn."""
    from datetime import datetime, timedelta, timezone

    task = Task(id=task_id, described=described)
    task.start_profile()
    when = datetime.now(timezone.utc) - timedelta(hours=6)
    for index in range(6):
        moment = (when + timedelta(hours=index)).isoformat()
        task.take(f"{task_id}-{index}", [1.0] + [0.0] * 7, (), moment, moment)
    return task


def _texts(described):
    return [one["text"] for one in described.get("todo") or []]


class TheListByItselfTests(unittest.TestCase):
    """What the four rules come to, on the list alone."""

    def _held(self):
        return {"todo": [
            {"id": "todo-a", "text": "Fix the fetch step", "by": "agent",
             "since": "2026-10-01T00:00:00+00:00"},
            {"id": "todo-b", "text": "Check figure 2", "by": "agent",
             "since": "2026-10-01T00:00:00+00:00"}]}

    def test_a_step_with_the_same_words_is_the_same_step(self) -> None:
        held = self._held()
        shown = todo.snapshot(held)
        after = todo.answered(held, shown, ["Check figure 2",
                                            "Write to the editor"])
        steps = after["todo"]
        self.assertEqual([one["text"] for one in steps],
                         ["Check figure 2", "Write to the editor"])
        # Kept: its id, who wrote it and since when.
        self.assertEqual(steps[0]["id"], "todo-b")
        self.assertEqual(steps[0]["since"], "2026-10-01T00:00:00+00:00")
        # New: an id of its own, written by the model.
        self.assertNotIn(steps[1]["id"], ("todo-a", "todo-b"))
        self.assertEqual(steps[1]["by"], "agent")
        # And the step that is done is gone.
        self.assertNotIn("Fix the fetch step", [one["text"] for one in steps])

    def test_the_users_changes_are_kept_until_a_call_has_seen_them(self) -> None:
        held = self._held()
        held.update(todo.added(held, "Email the editor"))
        held.update(todo.removed(held, "todo-a"))
        self.assertEqual(held["todo_changes"],
                         {"added": ["Email the editor"],
                          "removed": ["Fix the fetch step"], "finished": [], "agent_finished": []})
        # Shown at once: the added step is in the list, by the user, and
        # the deleted one is not.
        self.assertEqual(_texts(held), ["Check figure 2", "Email the editor"])
        self.assertEqual(held["todo"][-1]["by"], "user")
        self.assertTrue(todo.pending(held))
        # The model kept the user's step; it stays the user's.
        shown = todo.snapshot(held)
        after = todo.answered(held, shown, ["Check figure 2",
                                            "Email the editor"])
        self.assertEqual(after["todo"][1]["by"], "user")
        self.assertEqual(after["todo_changes"], {"added": [], "removed": [],
                          "finished": [], "agent_finished": []})
        self.assertFalse(todo.pending(after))

    def test_added_then_deleted_before_anything_saw_it_is_no_change(self) -> None:
        held = self._held()
        held.update(todo.added(held, "Email the editor"))
        mine = held["todo"][-1]["id"]
        held.update(todo.removed(held, mine))
        self.assertEqual(held["todo_changes"], {"added": [], "removed": [],
                          "finished": [], "agent_finished": []})
        # And deleted then put back, likewise.
        held.update(todo.removed(held, "todo-a"))
        held.update(todo.added(held, "Fix the fetch step"))
        self.assertEqual(held["todo_changes"], {"added": [], "removed": [],
                          "finished": [], "agent_finished": []})

    def test_an_answer_without_a_list_keeps_the_list_and_clears_what_it_saw(
            self) -> None:
        held = self._held()
        held.update(todo.added(held, "Email the editor"))
        shown = todo.snapshot(held)
        after = todo.answered(held, shown, todo.read_answer({"status": "x"}))
        self.assertEqual(_texts(after), _texts(held))
        self.assertEqual(after["todo_changes"], {"added": [], "removed": [],
                          "finished": [], "agent_finished": []})

    def test_a_call_that_failed_clears_nothing(self) -> None:
        held = self._held()
        held.update(todo.added(held, "Email the editor"))
        shown = todo.snapshot(held)
        after = todo.answered(held, shown, None, failed=True)
        self.assertEqual(after["todo_changes"]["added"], ["Email the editor"])

    def test_a_change_made_while_the_call_was_out_reaches_the_next_one(
            self) -> None:
        held = self._held()
        held.update(todo.added(held, "Email the editor"))
        shown = todo.snapshot(held)
        held["todo_asking"] = {"added": shown["added"],
                               "removed": shown["removed"]}
        # While the call is out: one more step added, and the one it is
        # being shown deleted again.
        held.update(todo.added(held, "Book the room"))
        mine = next(one["id"] for one in held["todo"]
                    if one["text"] == "Email the editor")
        held.update(todo.removed(held, mine))
        after = todo.answered(held, shown, ["Check figure 2",
                                            "Email the editor"])
        # What it was shown is gone; what it was not is still there.
        self.assertEqual(after["todo_changes"],
                         {"added": ["Book the room"],
                          "removed": ["Email the editor"], "finished": [], "agent_finished": []})
        # And laid over its answer, so the page does not undo the user.
        self.assertEqual(_texts(after), ["Check figure 2", "Book the room"])

    def test_a_step_marked_finished_is_ticked_then_left_out(self) -> None:
        held = self._held()
        held.update(todo.finished(held, "todo-a"))
        # Still in the list, ticked, until the next answer.
        self.assertEqual(_texts(held), ["Fix the fetch step", "Check figure 2"])
        self.assertTrue(held["todo"][0]["done"])
        self.assertEqual(held["todo_changes"]["finished"],
                         ["Fix the fetch step"])
        self.assertTrue(todo.pending(held))
        shown = todo.snapshot(held)
        told = todo.told(shown)
        self.assertIn("- Fix the fetch step (the user marked this finished)",
                      told)
        self.assertIn("marked finished:\n- Fix the fetch step", told)
        # The model follows rule 2.
        after = todo.answered(held, shown, ["Check figure 2"])
        self.assertEqual(_texts(after), ["Check figure 2"])
        self.assertEqual(after["todo_changes"]["finished"], [])
        # An answer with no list drops it as well: the user said it is done.
        after = todo.answered(held, shown, None)
        self.assertEqual(_texts(after), ["Check figure 2"])
        self.assertFalse(todo.pending(after))
        # And a call that failed keeps it, ticked and still to be told.
        after = todo.answered(held, shown, None, failed=True)
        self.assertEqual(after["todo_changes"]["finished"],
                         ["Fix the fetch step"])

    def test_a_step_finished_while_the_call_was_out_stays_ticked(self) -> None:
        held = self._held()
        shown = todo.snapshot(held)
        held["todo_asking"] = {"added": [], "removed": [], "finished": []}
        held.update(todo.finished(held, "todo-b"))
        after = todo.answered(held, shown, ["Fix the fetch step",
                                            "Check figure 2"])
        self.assertTrue(after["todo"][1].get("done"))
        self.assertEqual(after["todo_changes"]["finished"], ["Check figure 2"])

    def test_the_answer_is_read_as_a_list_of_steps(self) -> None:
        self.assertIsNone(todo.read_answer({"status": "x"}))
        self.assertEqual(todo.read_answer({"todo": []}), [])
        self.assertEqual(todo.read_answer({"todo": [" a  b ", "A B", "c"]}),
                         ["a b", "c"])


class TheProjectsListTests(unittest.TestCase):
    """A project is always at the top, so its status call writes its list."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_summaries_enabled", False, internal=True)
        kept = Kept(self.app.db)
        made = []
        for index, what in enumerate(("Fix the heartbeat",
                                      "Write the release notes")):
            task = _worked(f"task-{index}", title=what,
                           status="The user is on it.", urgency=5,
                           importance=3)
            task.seat = index
            made.append(task)
        kept.save(made)
        self.tasks = made
        self.project = Project(
            id="project-live-one", name="The agent release", why="one program",
            tasks=[task.id for task in made],
            described={"named_size": 2, "status": "The user is releasing it.",
                       "summarised": [task.id for task in made],
                       "todo": [{"id": "todo-a", "text": "Tag the release",
                                 "by": "agent", "since": "2026-10-01"},
                                {"id": "todo-b", "text": "Write the notes",
                                 "by": "agent", "since": "2026-10-01"}]})
        Projects(self.app.db).save(self.project)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _stored(self):
        return Projects(self.app.db).all()[0].described

    def _round(self, models, moved=None):
        self.app.config.set("state_round_tried", "[]", internal=True)
        self.beat.models = models
        report = {"moved": moved or {}}
        self.beat._bring_projects_up_to_date(report, self.beat.where("live"))
        return report

    def test_nothing_changed_means_nothing_asked(self) -> None:
        models = _Model(project={"status": "s", "name": "n", "why": "w"})
        self._round(models)
        self.assertEqual(models.sent, [])

    def test_the_users_change_is_asked_about_next_round(self) -> None:
        said = self.beat.change_todo(self.project.id, add="Email the editor")
        self.assertEqual(said["kind"], "project")
        self.assertIn("Email the editor", [one["text"] for one in said["todo"]])
        said = self.beat.change_todo(self.project.id, remove="todo-b")
        self.assertEqual([one["text"] for one in said["todo"]],
                         ["Tag the release", "Email the editor"])
        models = _Model(project={
            "status": "The user is releasing it.", "name": "The agent release",
            "why": "w", "todo": ["Tag the release", "Email the editor",
                                 "Upload the installer"]})
        report = self._round(models)
        # ASKED, though none of its tasks moved: the user changed its list.
        self.assertEqual(len(models.sent), 1)
        system, message = models.sent[0]["system"], models.sent[0]["message"]
        # The list so far, and what the user changed since, each a line.
        self.assertIn("THE NEXT STEPS SO FAR:\n- Tag the release\n"
                      "- Email the editor", message)
        self.assertIn("WHAT CHANGED SINCE:\nthe user added:\n- Email the "
                      "editor\nthe user removed:\n- Write the notes", message)
        # The four rules, and that these are next steps, not goals.
        self.assertIn("marked finished:\n  (nothing)", message)
        for rule in ("FOLLOW THE USER'S CHANGES AS FAR AS YOU CAN",
                     "or the user marked it",
                     "A STEP THAT IS ALREADY DONE IS LEFT OUT",
                     "NEW THINGS TO DO ARE ADDED",
                     "A STEP NOT YET DONE KEEPS EXACTLY THE SAME WORDS",
                     "NEXT STEPS, NOT LONG-TERM GOALS", "todo_note"):
            self.assertIn(rule, system)
        self.assertIn('"todo"', system)
        # The contract it had is all still there.
        for field in ('"status"', '"name"', '"why"'):
            self.assertIn(field, system)
        stored = self._stored()
        self.assertEqual(_texts(stored), ["Tag the release",
                                          "Email the editor",
                                          "Upload the installer"])
        self.assertEqual(stored["todo"][0]["id"], "todo-a")
        self.assertEqual(stored["todo"][1]["by"], "user")
        self.assertEqual(stored["todo"][2]["by"], "agent")
        self.assertEqual(stored["todo_changes"], {"added": [], "removed": [],
                          "finished": [], "agent_finished": []})
        self.assertNotIn("todo_asking", stored)
        # The page reads the projects off the report.
        self.assertEqual([one["text"] for one in report["projects"][0]["todo"]],
                         _texts(stored))
        # SHOWN ONCE, THEN GONE: the round after is not asked at all, and
        # when something moves it is told nothing was changed.
        self._round(models)
        self.assertEqual(len(models.sent), 1)
        self._round(models, moved={self.tasks[0].id: {"gained": ["s"],
                                                      "lost": []}})
        self.assertEqual(len(models.sent), 2)
        self.assertNotIn("removed:\n- Write the notes",
                         models.sent[1]["message"])
        self.assertIn("nothing - the list has not changed since it was written",
                      models.sent[1]["message"])

    def test_an_answer_without_a_list_still_clears_the_changes(self) -> None:
        self.beat.change_todo(self.project.id, add="Email the editor")
        models = _Model(project={"status": "The user is releasing it.",
                                 "name": "The agent release", "why": "w"})
        self._round(models)
        stored = self._stored()
        self.assertEqual(_texts(stored), ["Tag the release", "Write the notes",
                                          "Email the editor"])
        self.assertEqual(stored["todo_changes"], {"added": [], "removed": [],
                          "finished": [], "agent_finished": []})

    def test_a_call_that_never_got_through_keeps_the_changes(self) -> None:
        self.beat.change_todo(self.project.id, add="Email the editor")
        self._round(_Refusing())
        stored = self._stored()
        self.assertEqual(stored["todo_changes"]["added"], ["Email the editor"])
        self.assertTrue(self.beat._todo_due("state_projects", "live",
                                            self.project.id))

    def test_a_change_made_while_the_call_is_out_survives_it(self) -> None:
        self.beat.change_todo(self.project.id, add="Email the editor")
        models = _Model(
            project={"status": "s", "name": "The agent release", "why": "w",
                     "todo": ["Tag the release", "Email the editor"]},
            during=lambda: self.beat.change_todo(self.project.id,
                                                 add="Book the room"))
        self._round(models)
        stored = self._stored()
        self.assertEqual(stored["todo_changes"],
                         {"added": ["Book the room"], "removed": [],
                          "finished": [], "agent_finished": []})
        self.assertEqual(_texts(stored), ["Tag the release",
                                          "Email the editor", "Book the room"])
        # And it is what the next round is asked about.
        self._round(models)
        self.assertEqual(len(models.sent), 2)
        self.assertIn("added:\n- Book the room", models.sent[1]["message"])
        self.assertNotIn("- Email the editor\nremoved",
                         models.sent[1]["message"])

    def test_a_round_that_saves_the_project_does_not_undo_an_edit(self) -> None:
        """The round holds its copy for minutes; the row is the truth."""
        stale = Projects(self.app.db).all()[0]
        self.beat.change_todo(self.project.id, add="Email the editor")
        stale.why = "still one program"
        Projects(self.app.db).save(stale)
        stored = Projects(self.app.db).all()[0]
        self.assertEqual(stored.why, "still one program")
        self.assertIn("Email the editor", _texts(stored.described))

    def test_the_page_gets_the_list_whichever_way_it_is_drawn(self) -> None:
        self.beat.change_todo(self.project.id, add="Email the editor")
        self.beat._last_report = {}
        self.beat._progress = {}
        stored = self.beat.the_picture()
        drawn = (stored.get("projects") or [{}])[0]
        self.assertIn("Email the editor", [one["text"] for one in drawn["todo"]])
        self.assertEqual(drawn["todo_changes"]["added"], ["Email the editor"])
        report = {"plan": {"nodes": [{"id": "task_1", "task_id": "task-0"},
                                     {"id": "task_2", "task_id": "task-1"}],
                           "edges": []},
                  "layers": [{"id": "task_1", "task_id": "task-0"},
                             {"id": "task_2", "task_id": "task-1"}],
                  "finished_at": "2026-10-04T00:00:00+00:00"}
        self.beat._last_report = report
        self.beat._progress = report
        drawn = (self.beat.the_picture().get("projects") or [{}])[0]
        self.assertIn("Email the editor", [one["text"] for one in drawn["todo"]])


class ATopTasksListTests(unittest.TestCase):
    """A task in no project keeps a list; a task in one does not."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_summaries_enabled", False, internal=True)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _a_task(self, task_id="task-top", **described):
        task = _worked(task_id, **dict({
            "title": "Rebuild the replication package",
            "status": "The user has not started the figures.",
            "where": "survey-weights/", "urgency": 5, "importance": 4,
            "todo": [{"id": "todo-a", "text": "Fix the fetch step",
                      "by": "agent", "since": "2026-10-01"},
                     {"id": "todo-b", "text": "Check figure 2", "by": "agent",
                      "since": "2026-10-01"}]}, **described))
        task.summary_id = "rec_1"
        Kept(self.app.db).save([task])
        return task

    def _ask_status(self, task):
        """One status call, as a round makes it for a kept summary."""
        report = {"moved": {}, "layers": [{"id": "task_1",
                                           "task_id": task.id}]}
        summary = {"described": dict(task.described)}
        self.beat._kept_now = Kept(self.app.db)
        self.beat._project_of = {}
        self.beat._bring_status_up_to_date(
            report, task, 0, [], "trace", self.beat.where("live"), summary)
        return report, summary

    def test_the_users_change_brings_a_status_call_with_the_list(self) -> None:
        task = self._a_task()
        self.beat.models = models = _Model(status={
            "changed": False, "why": "nothing moved", "urgency": 5,
            "importance": 4, "todo": ["Check figure 2", "Email the editor"],
            "todo_note": ""})
        # Nothing changed, nothing moved: not asked.
        self._ask_status(task)
        self.assertEqual(models.sent, [])
        self.beat.change_todo(task.id, add="Email the editor")
        task = Kept(self.app.db).by_ids([task.id])[0]
        report, summary = self._ask_status(task)
        self.assertEqual(len(models.sent), 1)
        system, message = models.sent[0]["system"], models.sent[0]["message"]
        self.assertTrue(system.startswith("SAY WHERE THIS TASK STANDS"))
        self.assertIn("A STEP NOT YET DONE KEEPS EXACTLY THE SAME WORDS", system)
        self.assertIn("THE NEXT STEPS SO FAR:\n- Fix the fetch step\n"
                      "- Check figure 2\n- Email the editor", message)
        self.assertIn("added:\n- Email the editor", message)
        # What the status call always asked is all still there.
        for field in ('"status"', '"changed"', '"urgency"', '"importance"',
                      '"weighed"'):
            self.assertIn(field, system)
        stored = Kept(self.app.db).by_ids([task.id])[0].described
        self.assertEqual(_texts(stored), ["Check figure 2", "Email the editor"])
        self.assertEqual(stored["todo"][0]["id"], "todo-b")
        self.assertEqual(stored["todo"][1]["by"], "user")
        self.assertFalse(todo.pending(stored))
        # The status it did not change is as it was.
        self.assertEqual(stored["status"],
                         "The user has not started the figures.")
        # And the card's copy has the new list too.
        self.assertEqual(_texts(summary["described"]),
                         ["Check figure 2", "Email the editor"])

    def test_a_task_in_a_project_has_no_list_of_its_own(self) -> None:
        task = self._a_task()
        Projects(self.app.db).save(Project(id="project-live-x", name="x",
                                           tasks=[task.id]))
        with self.assertRaises(ValueError):
            self.beat.change_todo(task.id, add="Email the editor")
        # Its status call, when states move, is not shown a list.
        self.beat.models = models = _Model(status={"changed": False,
                                                   "why": "w"})
        self.beat._project_of = {task.id: "project-live-x"}
        asking = self.beat._status_question(
            {}, task, {"id": "task_1"}, [], {"gained": [], "lost": []},
            todo_scope=None, todo_due=False)
        self.assertNotIn("asked", asking)

    def test_an_unknown_id_is_not_found(self) -> None:
        with self.assertRaises(LookupError):
            self.beat.change_todo("task-nobody", add="x")
        task = self._a_task()
        with self.assertRaises(LookupError):
            self.beat.change_todo(task.id, remove="todo-nobody")

    def test_the_page_gets_a_top_tasks_list_both_ways(self) -> None:
        one = self._a_task("task-one")
        self._a_task("task-two", title="Answer the reviewer")
        self.beat.change_todo(one.id, add="Email the editor")
        self.beat._last_report = {}
        self.beat._progress = {}
        picture = self.beat.the_picture()
        self.assertTrue(picture.get("from_store"))
        layer = next(row for row in picture["layers"] if row["id"] == one.id)
        self.assertIn("Email the editor",
                      _texts(layer["summary"]["described"]))
        # From a round's report - and from the drawing kept from one - the
        # list comes from the row, not from the round's older copy.
        report = {"plan": {"nodes": [
                      {"id": "task_1", "task_id": one.id, "title": "a"},
                      {"id": "task_2", "task_id": "task-two", "title": "b"}],
                      "edges": []},
                  "layers": [{"id": "task_1", "task_id": one.id, "summary": {
                                 "title": "a", "described": {"todo": []}}},
                             {"id": "task_2", "task_id": "task-two",
                              "summary": {"title": "b"}}],
                  "finished_at": "2026-10-04T00:00:00+00:00"}
        self.beat._last_report = report
        self.beat._progress = report
        picture = self.beat.the_picture()
        self.assertFalse(picture.get("from_store"))
        layer = next(row for row in picture["layers"] if row["id"] == "task_1")
        self.assertIn("Email the editor",
                      _texts(layer["summary"]["described"]))
        self.beat._keep_the_drawing(report)
        self.beat._last_report = {}
        self.beat._progress = {}
        self.beat._kept_drawing = None
        self.beat.change_todo(one.id, add="Book the room")
        held = self.beat._the_kept_drawing()
        self.assertTrue(held.get("layers"))
        picture = self.beat._the_picture_now()
        self.assertFalse(picture.get("from_store"))
        layer = next(row for row in picture["layers"] if row["id"] == "task_1")
        self.assertIn("Book the room", _texts(layer["summary"]["described"]))

    def test_a_summary_of_a_top_task_writes_its_list(self) -> None:
        """A whole round, in the sandbox: the summary call of a task in no
        project is shown its list and the rules, and what it says is kept;
        the user's change then brings a status call though nothing moved."""
        self.app.config.set("state_summaries_enabled", True, internal=True)
        self.app.config.set("state_sandbox_max_states", 160)
        body = {"title": "Rebuild the replication package.",
                "summary": "The one thing these were.", "concrete": True,
                "concrete_why": "a named thing", "reasoning": "r",
                "evidence": [], "where": "The user works in it.",
                "goal": "The user wants it done.",
                "doing": "The user is re-running it.",
                "status": "The user has not started the figures.",
                "todo": ["Fix the fetch step", "Check figure 2"]}
        self.beat.models = models = _Model(summary=body, status={
            "changed": False, "why": "nothing moved",
            "todo": ["Check figure 2", "Email the editor"]})
        self.beat.make_up_states(how_many=160, days=20, seed=71)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(report.get("error"), report.get("error"))
        summaries = [one for one in models.sent
                     if one["system"].startswith("SUMMARISE")]
        self.assertTrue(summaries)
        self.assertIn("THE NEXT STEPS SO FAR:", summaries[0]["message"])
        self.assertIn("(none yet - this is the first list for it)",
                      summaries[0]["message"])
        self.assertIn("NEXT STEPS, NOT LONG-TERM GOALS",
                      summaries[0]["system"])
        tasks = [one for one in Kept(self.app.db, "sandbox").live()
                 if one.described.get("todo")]
        self.assertTrue(tasks, "no task kept the list it was given")
        task = tasks[0]
        self.assertEqual(_texts(task.described),
                         ["Fix the fetch step", "Check figure 2"])
        kept_id = task.described["todo"][1]["id"]
        # The user's edit, on a sandbox task.
        said = self.beat.change_todo(task.id, add="Email the editor")
        self.assertEqual(said["scope"], "sandbox")
        # The next round, with nothing new in it.
        models.sent.clear()
        again = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(again.get("error"), again.get("error"))
        asked = [one for one in models.sent
                 if "added:\n- Email the editor" in one["message"]]
        self.assertEqual(len(asked), 1, [one["system"][:30]
                                         for one in models.sent])
        after = Kept(self.app.db, "sandbox").by_ids([task.id])[0].described
        self.assertEqual(_texts(after), ["Check figure 2", "Email the editor"])
        self.assertEqual(after["todo"][0]["id"], kept_id)
        self.assertFalse(todo.pending(after))


class TheEndpointTests(unittest.TestCase):
    """POST /api/state_heartbeat/todo, over the real server."""

    def setUp(self) -> None:
        import socket

        self.agent = TemporaryAgent()
        self.app = self.agent.app
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.app.config.set("api_port", port)
        from personalized_agent.api.server import ApiServer
        self.server = ApiServer(self.app)
        self.server.start()
        self.base = f"http://127.0.0.1:{port}"
        top = Task(id="task-top", described={"title": "Top", "todo": [
            {"id": "todo-a", "text": "Fix the fetch step", "by": "agent",
             "since": "2026-10-01"}]})
        inside = Task(id="task-inside", described={"title": "Inside"})
        Kept(self.app.db).save([top, inside])
        Projects(self.app.db).save(Project(id="project-live-p", name="P",
                                           tasks=["task-inside"]))

    def tearDown(self) -> None:
        self.server.stop()
        self.agent.cleanup()

    def _post(self, body):
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"{self.base}/api/state_heartbeat/todo", method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(keyed(self.app, request), timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_add_and_remove(self) -> None:
        status, said = self._post({"id": "task-top", "add": "Email the editor"})
        self.assertEqual(status, 200, said)
        self.assertEqual([one["text"] for one in said["todo"]],
                         ["Fix the fetch step", "Email the editor"])
        self.assertEqual(said["todo"][1]["by"], "user")
        self.assertEqual(said["todo_changes"]["added"], ["Email the editor"])
        mine = said["todo"][1]["id"]
        status, said = self._post({"id": "task-top", "remove": "todo-a"})
        self.assertEqual(status, 200, said)
        self.assertEqual([one["text"] for one in said["todo"]],
                         ["Email the editor"])
        self.assertEqual(said["todo_changes"]["removed"], ["Fix the fetch step"])
        status, said = self._post({"id": "task-top", "finish": mine})
        self.assertEqual(status, 200, said)
        self.assertTrue(said["todo"][0]["done"])
        self.assertEqual(said["todo_changes"]["finished"],
                         ["Email the editor"])
        status, said = self._post({"id": "project-live-p", "add": "Ship it"})
        self.assertEqual(status, 200, said)
        self.assertEqual(said["kind"], "project")

    def test_unknown_and_not_at_the_top(self) -> None:
        self.assertEqual(self._post({"id": "task-nobody", "add": "x"})[0], 404)
        self.assertEqual(self._post({"id": "task-top", "remove": "nope"})[0],
                         404)
        self.assertEqual(self._post({"id": "task-top", "finish": "nope"})[0],
                         404)
        self.assertEqual(self._post({"id": "task-inside", "add": "x"})[0], 400)
        self.assertEqual(self._post({"id": "task-top"})[0], 400)


class TheStepsAreInThePictureTests(unittest.TestCase):
    """In a real browser: clicking a project, or a task in no project, opens
    its next steps under it IN THE PICTURE - each with a box to tick it off
    and a cross to delete it, which asks first - and a "+" adds one. The
    card lists them, read only. What is open stays open through a redraw.
    """

    def setUp(self) -> None:
        from test_panels import _javascript_engine

        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _in_the_page(self, body: str, held: dict) -> dict:
        from test_panels import ROOT

        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        harness = (
            "<style>" + (web / "styles.css").read_text(encoding="utf-8")
            + "</style>"
            "<script>window.onerror=(m)=>{window.__err=String(m);};</script>"
            # THE ENDPOINT, played by the page: every POST is recorded, and
            # answered with the list a server would keep.
            "<script>window.__posts=[];window.fetch=(path,opts)=>{"
            "if(opts&&opts.method==='POST'){const b=JSON.parse(opts.body);"
            "window.__posts.push({path:path,body:b});"
            "let todo=window.__list.slice();"
            "if(b.add)todo.push({id:'todo-new'+todo.length,text:b.add,"
            "  by:'user'});"
            "if(b.remove)todo=todo.filter(o=>o.id!==b.remove);"
            "if(b.finish)todo=todo.map(o=>o.id===b.finish"
            "  ?Object.assign({},o,{done:true}):o);"
            "window.__list=todo;"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve({id:b.id,todo:todo,todo_changes:"
            "{added:b.add?[b.add]:[],removed:[]},todo_note:''})});}"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "json:()=>Promise.resolve({})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8")
            + "</script>"
            "<script>stateBeat.recentDays=0;</script>"
            + "<script>" + ";".join(
                f"const {name} = {json.dumps(value)}"
                for name, value in held.items()) + ";</script>"
            "<script>window.addEventListener('load',()=>{" + body
            + "});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            done = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu",
                 "--no-first-run", "--window-size=1400,900",
                 f"--user-data-dir={work}/profile",
                 "--virtual-time-budget=9000", "--dump-dom", probe.as_uri()],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=300)
        found = re.search(r"<title>(.*?)</title>", done.stdout or "", re.S)
        self.assertTrue(found, "the page never finished rendering")
        return json.loads(found.group(1).replace("&quot;", '"')
                          .replace("&amp;", "&"))

    def _picture(self):
        def node(i, task_id, title):
            return {"id": f"task_{i}", "task_id": task_id, "title": title,
                    "size": 5, "count": 9, "phase": "active",
                    "began": "2026-09-20T09:00:00+00:00",
                    "ended": f"2026-10-0{i}T09:00:00+00:00",
                    "summary": {"title": title, "urgency": 5}}
        steps = [{"id": "todo-a", "text": "Tag the release", "by": "agent"},
                 {"id": "todo-b", "text": "Email the editor", "by": "user"}]
        nodes = [node(1, "a", "Fix the heartbeat"),
                 node(2, "b", "Rebuild the package"),
                 node(3, "c", "Write the release notes")]
        layers = [dict(n, states=[]) for n in nodes]
        layers[1] = dict(layers[1], summary={
            "title": "Rebuild the package", "described": {
                "status": "The user is on it.",
                "todo": [{"id": "todo-t", "text": "Fix the fetch step",
                          "by": "agent"}]}})
        return {"plan": {"nodes": nodes, "edges": []}, "layers": layers,
                "projects": [{"id": "p1", "name": "The agent release",
                              "why": "one program's release",
                              "status": "The user is getting it out.",
                              "tasks": ["a", "c"], "urgency": 6,
                              "importance": 5, "count": 18, "todo": steps,
                              "todo_changes": {"added": ["Email the editor"],
                                               "removed": [], "finished": []},
                              "began": "2026-09-20T09:00:00+00:00",
                              "ended": "2026-10-03T09:00:00+00:00",
                              "stretches": []}],
                "running": False, "due": {}}

    def test_a_projects_steps_open_in_the_picture_and_are_changed_there(
            self) -> None:
        verdict = self._in_the_page(STEPS_IN_THE_PICTURE,
                                    {"pic": self._picture()})
        self.assertEqual(verdict["err"], "", verdict["err"])
        out = verdict["out"]
        self.assertIsNone(out["before"], "steps were open before a click")
        # THE VIEW DOES NOT MOVE when something in the picture is clicked.
        self.assertGreater(out["scrollBefore"], 0, "the page did not scroll")
        self.assertEqual(out["scrollAfterProject"], out["scrollBefore"])
        self.assertEqual(out["scrollAfterTask"], out["scrollBefore"])
        self.assertEqual(out["open"], ["Tag the release",
                                       "Email the editor · yours"])
        self.assertNotEqual(out["moved"], f"translateY({out['home']}px)",
                            "the rows below did not make room")
        self.assertEqual(out["card"], ["Tag the release", "Email the editor"])
        self.assertEqual(out["cardEdits"], 0, "the card can still edit")
        self.assertEqual(out["afterRedraw"], out["open"],
                         "a redraw shut the steps")
        self.assertTrue(out["focused"], "the new step's box was not focused")
        self.assertEqual(out["added"], ["Tag the release",
                                        "Email the editor · yours",
                                        "Upload the installer · yours"])
        self.assertEqual(out["finished"][0], "Tag the release [done]")
        self.assertTrue(out["asked"], "deleting did not ask first")
        self.assertEqual(out["postsWhileAsking"], 2,
                         "it deleted before the answer")
        self.assertFalse(out["askedAfterNo"])
        self.assertIn("Email the editor · yours", out["afterNo"])
        self.assertNotIn("Email the editor · yours", out["removed"])
        self.assertEqual(out["posts"], [
            ["/api/state_heartbeat/todo",
             {"id": "p1", "add": "Upload the installer"}],
            ["/api/state_heartbeat/todo", {"id": "p1", "finish": "todo-a"}],
            ["/api/state_heartbeat/todo", {"id": "p1", "remove": "todo-b"}]])
        self.assertIsNone(out["shut"], "a second click did not shut it")
        self.assertEqual(out["task"], ["Fix the fetch step"])
        self.assertFalse(out["insideHasList"],
                         "a task inside a project was given its own list")


#: The page script for the test above: open a project's steps by clicking
#: it, then add, finish and delete one, reading the picture after each.
STEPS_IN_THE_PICTURE = """
let err=window.__err||'';let out={};
const host=()=>document.getElementById('ov-tasks');
const group=(id)=>host().querySelector(
  `.sb-tt-step-group[data-sb-steps-for="${id}"]`);
const said=(id)=>group(id)?[...group(id).querySelectorAll('.sb-tt-step')]
  .map(g=>(g.querySelector('.sb-tt-step-words').textContent+((g.querySelector('.sb-tt-step-by')||{}).textContent||''))
    .replace(/\\s+/g,' ').trim()
    +(g.classList.contains('sb-tt-step-done')?' [done]':'')):null;
const click=(el)=>el.dispatchEvent(new MouseEvent('click',{bubbles:true}));
const wait=()=>new Promise(r=>setTimeout(r,60));
(async()=>{try{
  window.__list=pic.projects[0].todo.slice();
  drawWhatIsRunning(pic);
  // A page long enough to scroll, scrolled part of the way down: a click
  // in the picture must leave it exactly there.
  document.body.style.paddingBottom='4000px';
  window.scrollTo(0,240);
  out.scrollBefore=window.scrollY;
  out.before=said('p1');
  const disc=host().querySelector('[data-sb-project] .sb-tt-project-disc');
  const at={bubbles:true,button:0,pointerId:1};
  disc.dispatchEvent(new PointerEvent('pointerdown',at));
  disc.dispatchEvent(new PointerEvent('pointerup',at));
  click(disc);
  await wait();
  out.scrollAfterProject=window.scrollY;
  out.open=said('p1');
  const below=host().querySelector('[data-sb-task="task_2"]');
  out.moved=below.style.transform;
  out.home=below.dataset.sbAt;
  const card=host().querySelector('[data-sb-card]');
  out.card=[...card.querySelectorAll('.sb-next-text')].map(e=>e.textContent);
  out.cardEdits=card.querySelectorAll('input,[data-sb-step-delete]').length;
  drawWhatIsRunning(JSON.parse(JSON.stringify(stateBeat.whole)));
  out.afterRedraw=said('p1');
  click(host().querySelector('[data-sb-step-plus="p1"]'));
  const box=host().querySelector('[data-sb-next-input="p1"]');
  out.focused=document.activeElement===box;
  box.value='Upload the installer';
  box.dispatchEvent(new Event('input',{bubbles:true}));
  click(host().querySelector('[data-sb-step-add="p1"]'));
  await wait();
  out.added=said('p1');
  click(host().querySelector('[data-sb-step-finish="todo-a"]'));
  await wait();
  out.finished=said('p1');
  click(host().querySelector('[data-sb-step-delete="todo-b"]'));
  out.asked=!!host().querySelector('.sb-tt-step-ask');
  out.postsWhileAsking=window.__posts.length;
  click(host().querySelector('[data-sb-step-no="todo-b"]'));
  out.afterNo=said('p1');
  out.askedAfterNo=!!host().querySelector('.sb-tt-step-ask');
  click(host().querySelector('[data-sb-step-delete="todo-b"]'));
  click(host().querySelector('[data-sb-step-yes="todo-b"]'));
  await wait();
  out.removed=said('p1');
  out.posts=window.__posts.map(p=>[p.path,p.body]);
  click(host().querySelector('[data-sb-project] .sb-tt-project-disc'));
  out.shut=said('p1');
  click(host().querySelector('[data-sb-task="task_2"]'));
  await wait();
  out.scrollAfterTask=window.scrollY;
  out.task=said('b');
  const inside=document.createElement('div');
  inside.innerHTML=sbTaskCard(pic,'task_1');
  out.insideHasList=!!inside.querySelector('[data-sb-next]');
}catch(e){err=String(e&&e.stack?e.stack:e);}
document.title=JSON.stringify({err:err,out:out});})();
"""


if __name__ == "__main__":
    unittest.main()


class ATickCanBeTakenBackTests(unittest.TestCase):
    """A step marked finished by mistake is un-ticked with the same click."""

    def test_untick_before_any_call_is_as_if_never_ticked(self) -> None:
        from personalized_agent.heartbeat.states import todo
        described = todo.added({}, "Deploy the site")
        step = described["todo"][0]
        described.update(todo.finished(described, step["id"]))
        self.assertTrue(described["todo"][0].get("done"))
        described.update(todo.unfinished(described, step["id"]))
        self.assertFalse(described["todo"][0].get("done"))
        self.assertEqual(described["todo_changes"]["finished"], [])

    def test_untick_while_a_call_reads_the_tick_keeps_the_step(self) -> None:
        from personalized_agent.heartbeat.states import todo
        described = todo.added({}, "Deploy the site")
        step = described["todo"][0]
        described.update(todo.finished(described, step["id"]))
        described["todo_asking"] = {"finished": ["Deploy the site"]}
        described.update(todo.unfinished(described, step["id"]))
        self.assertIn("Deploy the site", described["todo_changes"]["added"])


class TheAgentsTicksTests(unittest.TestCase):
    """Each agent heartbeat ticks the steps it sees the user finish - with
    why; the user can take a tick back; the next list is written from the
    ticks and the user's own changes."""

    def _held(self):
        return {"todo": [
            {"id": "todo-a", "text": "Send the draft to Tan", "by": "agent",
             "since": "2026-10-01T00:00:00+00:00"},
            {"id": "todo-b", "text": "Book the room", "by": "agent",
             "since": "2026-10-01T00:00:00+00:00"}]}

    def test_a_tick_says_who_and_why_and_is_told_to_the_next_list(self) -> None:
        held = self._held()
        held.update(todo.ticked(held, "todo-a", "the sent mail to Tan is in Outlook",
                                ["event_1"]))
        step = held["todo"][0]
        self.assertEqual((step["done"], step["done_by"]), (True, "agent"))
        self.assertEqual(step["done_why"], "the sent mail to Tan is in Outlook")
        self.assertEqual(step["done_evidence"], ["event_1"])
        self.assertEqual(held["todo_changes"]["agent_finished"], ["Send the draft to Tan"])
        self.assertTrue(todo.pending(held), "the next state round is due")
        shown = todo.snapshot(held)
        told = todo.told(shown)
        self.assertIn("(the agent heartbeat marked this finished: the sent mail", told)
        self.assertIn("the agent heartbeat marked finished", told)
        self.assertIn("because: the sent mail to Tan is in Outlook", told)
        # Rule 2: the next list leaves it out, and what it was told is cleared.
        after = todo.answered(held, shown, ["Book the room"])
        self.assertEqual(_texts(after), ["Book the room"])
        self.assertEqual(after["todo_changes"]["agent_finished"], [])

    def test_the_user_takes_a_tick_back(self) -> None:
        held = self._held()
        held.update(todo.ticked(held, "todo-a", "seen sent", []))
        held.update(todo.unfinished(held, "todo-a"))
        step = held["todo"][0]
        self.assertFalse(step.get("done"))
        self.assertNotIn("done_by", step)
        self.assertEqual(held["todo_changes"]["agent_finished"], [])
        self.assertFalse(todo.pending(held), "as if it never happened")
        # Taken back while a call was reading it: said as one to keep.
        held.update(todo.ticked(held, "todo-a", "seen sent", []))
        held["todo_asking"] = {"added": [], "removed": [], "finished": [],
                               "agent_finished": ["Send the draft to Tan"]}
        held.update(todo.unfinished(held, "todo-a"))
        self.assertEqual(held["todo_changes"]["added"], ["Send the draft to Tan"])

    def test_a_step_the_user_ticked_is_not_ticked_again(self) -> None:
        held = self._held()
        held.update(todo.finished(held, "todo-a"))
        held.update(todo.ticked(held, "todo-a", "seen", []))
        self.assertNotIn("done_by", held["todo"][0])
        self.assertEqual(held["todo_changes"]["agent_finished"], [])


class TheAgentHeartbeatsStepTests(unittest.TestCase):
    """The agent heartbeat's fourth step: the open next steps of the work at
    the top, read; the ones its answer says are done, ticked."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        kept = Kept(self.app.db)
        inside = _worked("task-in", title="Part of the release", status="on it",
                         urgency=3, importance=3)
        inside.described["todo"] = [{"id": "todo-old", "text": "An old list's step",
                                     "by": "agent", "since": "2026-10-01"}]
        alone = _worked("task-alone", title="Reply to Tan", status="drafting",
                        urgency=3, importance=3)
        alone.described["todo"] = [{"id": "todo-c", "text": "Send the reply",
                                    "by": "agent", "since": "2026-10-01"}]
        kept.save([inside, alone])
        Projects(self.app.db).save(Project(
            id="project-live-one", name="The agent release", why="one program",
            tasks=["task-in"],
            described={"todo": [{"id": "todo-a", "text": "Tag the release", "by": "agent",
                                 "since": "2026-10-01"},
                                {"id": "todo-b", "text": "Write the notes", "by": "agent",
                                 "since": "2026-10-01", "done": True}]}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_it_reads_the_open_steps_at_the_top_and_ticks_what_it_was_told(self) -> None:
        beat = self.app.heartbeat
        lines = beat._open_todos()
        joined = "\n".join(lines)
        self.assertIn("[todo-a] Tag the release (of: The agent release)", joined)
        self.assertIn("[todo-c] Send the reply (of: Reply to Tan)", joined)
        self.assertNotIn("todo-b", joined, "already ticked")
        self.assertNotIn("todo-old", joined, "a task inside a project has no list")
        ticked = beat._tick_todos([
            {"id": "todo-a", "why": "the v2.1 tag was pushed", "evidence": ["event_9"]},
            {"id": "todo-zzz", "why": "never shown"},
            {"id": "todo-c"}])                     # no reason: not ticked
        self.assertEqual([one["id"] for one in ticked], ["todo-a"])
        stored = Projects(self.app.db).all()[0].described
        step = next(one for one in stored["todo"] if one["id"] == "todo-a")
        self.assertEqual((step["done_by"], step["done_why"]),
                         ("agent", "the v2.1 tag was pushed"))
        self.assertTrue(todo.pending(stored), "the project's next round is due")

    def test_it_is_a_step_of_every_heartbeat(self) -> None:
        from personalized_agent.heartbeat.core.heartbeat import STEP_PROMPTS, SYSTEM_PROMPT
        self.assertIn("step_todo", [one.id for one in self.app.checklist.snapshot()])
        self.assertIn("todo_done", STEP_PROMPTS["step_todo"]["returns"])
        self.assertIn("DECIDE WHICH OF THE USER'S NEXT STEPS ARE NOW DONE", SYSTEM_PROMPT)


class AHeartbeatTicksWhatItSawTests(unittest.TestCase):
    """A whole agent heartbeat run: the fourth step is given the open next
    steps, answers, and the step it named is ticked by the agent."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        Projects(self.app.db).save(Project(
            id="project-live-one", name="The agent release", why="one program",
            tasks=[], described={"todo": [{"id": "todo-a", "text": "Tag the release",
                                           "by": "agent", "since": "2026-10-01"}]}))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_run_ticks_the_step_it_saw_done(self) -> None:
        from personalized_agent.models.base import ModelResponse, ModelUsage
        seen = []

        class _Models:
            def invoke(self, request, role=""):
                said = json.dumps(request.messages, ensure_ascii=False) + (request.system or "")
                if "DECIDE WHICH OF THE USER'S NEXT STEPS ARE NOW DONE" in said:
                    seen.append(said)
                    return ModelResponse(text=json.dumps({"todo_done": [
                        {"id": "todo-a", "why": "the v2.1 tag was pushed",
                         "evidence": []}]}), usage=ModelUsage(10, 5))
                return ModelResponse(text="{}", usage=ModelUsage(10, 5))

        self.app.heartbeat.models = _Models()
        report = self.app.heartbeat.run_once(trigger="manual_debug")
        self.assertTrue(seen, "the fourth step was asked")
        self.assertIn("[todo-a] Tag the release (of: The agent release)", seen[0])
        self.assertEqual([one["id"] for one in report.get("todos_ticked") or []], ["todo-a"])
        step = Projects(self.app.db).all()[0].described["todo"][0]
        self.assertEqual((step["done"], step["done_by"]), (True, "agent"))

"""What a Work debugger way is given nothing of: the folders left out of its
project - the agent's own among them, always - in what was learned, and in
the user's records it searches (a state by the files it rests on, a task
and a stage by their states'; of the rest, no word that names one)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from personalized_agent.agent.work import context_search
from personalized_agent.agent.work.debug_projects import DebugProjects
from personalized_agent.common.paths import app_code_root
from personalized_agent.data_manage.storage.database import Database
from personalized_agent.learning.leave_out import LeaveOut

ORCHARD = "/home/sam/orchard-tools"
LETTERS = "/home/sam/letters"
AT = "2026-01-05T10:00:00+00:00"


class TheRecordsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.directory.name) / "agent.sqlite")

    def tearDown(self) -> None:
        self.db.close() if hasattr(self.db, "close") else None
        self.directory.cleanup()

    def _state(self, ident: str, title: str, *files: str) -> None:
        self.db.execute(
            "INSERT INTO states (id, title, detail, source, about, starts_at, ends_at, status, "
            "created_at, updated_at, metadata) VALUES (?, ?, '', 'agent', 'user', ?, '', "
            "'active', ?, ?, ?)",
            (ident, title, AT, AT, AT, json.dumps({"sources": list(files)})))

    def _task(self, ident: str, title: str, members: list) -> None:
        self.db.execute(
            "INSERT INTO state_tasks (id, number, members, described, created_at, updated_at) "
            "VALUES (?, 1, ?, ?, ?, ?)",
            (ident, json.dumps(members), json.dumps({"title": title, "summary": title}), AT, AT))

    def test_what_rests_on_a_folder_left_out_is_not_given(self) -> None:
        self._state("s1", "Fixed the pruning script", f"{ORCHARD}/prune.py")
        self._state("s2", "Wrote the harvest plan", f"{ORCHARD}/plan.md", f"{LETTERS}/a.txt")
        self._state("s3", "Answered a letter from the bakery", f"{LETTERS}/bakery.txt")
        self._task("t1", "The orchard scripts", ["s1", "s2"])
        self._task("t2", "Letters", ["s3"])
        out = LeaveOut([ORCHARD])
        found = {one["id"] for one in context_search.records(self.db, out)}
        self.assertEqual(found, {"s3", "t2"}, "half or more of its places there: out whole")
        everything = {one["id"] for one in context_search.records(self.db)}
        self.assertEqual(everything, {"s1", "s2", "s3", "t1", "t2"}, "no leave-out: all of it")

    def test_of_the_rest_the_words_that_name_one_are_taken_out(self) -> None:
        self._state("s1", "Letters, and a look at orchard_tools/notes.md",
                    f"{LETTERS}/a.txt", f"{LETTERS}/b.txt")
        self._state("s2", "Planned the week in orchard-tools")       # rests on nothing
        self._state("s3", "Planned the week")
        said = {one["id"]: one for one in context_search.records(self.db, LeaveOut([ORCHARD]))}
        self.assertEqual(set(said), {"s1", "s3"}, "resting on nothing, it names one: out")
        self.assertNotIn("orchard", said["s1"]["title"].lower())
        self.assertIn("[left out]", said["s1"]["title"])


    def test_a_project_worked_in_a_folder_left_out_is_not_given(self) -> None:
        self._state("s1", "Answered a letter", f"{LETTERS}/a.txt")
        self.db.execute(
            "INSERT INTO state_tasks (id, number, members, described, created_at, updated_at) "
            "VALUES ('p-orchard', 1, '[]', ?, ?, ?)",
            (json.dumps({"title": "The orchard tools", "kind": "work",
                         "directories": [ORCHARD.replace("-", "_"), f"{ORCHARD}/tests"]}), AT, AT))
        found = {one["id"] for one in context_search.records(self.db, LeaveOut([ORCHARD]))}
        self.assertEqual(found, {"s1"}, "a project's own row, by the folders it is worked in")

    def test_what_was_learned_of_such_a_project_is_left_out_whole(self) -> None:
        self.db.execute(
            "INSERT INTO state_tasks (id, number, members, described, created_at, updated_at) "
            "VALUES ('p-orchard', 1, '[]', ?, ?, ?)",
            (json.dumps({"directories": [ORCHARD.replace("-", "_")]}), AT, AT))

        class _Store:
            db = self.db

            def units(self):
                return [{"id": "work:o", "kind": "work", "key": "p-orchard", "folders": []},
                        {"id": "work:l", "kind": "work", "key": "p-letters", "folders": []}]

            def cases(self):
                return [{"id": "C-1", "unit_id": "work:o", "locations": []},
                        {"id": "C-2", "unit_id": "work:l", "locations": []}]

            def judgments(self):
                return [{"id": "J-1", "case_id": "C-1", "refs": []}]
        cases, judgments = LeaveOut([ORCHARD]).parts_out(_Store())
        self.assertEqual((cases, judgments), ({"C-1"}, {"J-1"}))


class TheWordsTests(unittest.TestCase):

    def test_a_name_that_is_a_word_first_is_not_taken_for_the_folder(self) -> None:
        out = LeaveOut(["/home/sam/docs"])
        self.assertFalse(out.named_in("Read the docs again"))
        self.assertTrue(out.named_in("Read /home/sam/docs/intro.md again"))
        self.assertTrue(LeaveOut([ORCHARD]).named_in(r"C:\x and \home\sam\orchard_tools\a.py"))


class TheAgentsOwnFolderTests(unittest.TestCase):

    def test_always_left_out_and_shown_as_the_agents(self) -> None:
        projects = DebugProjects.__new__(DebugProjects)
        row = {"folder": "/home/sam/debug/hedges", "leave_out": json.dumps([LETTERS])}
        self.assertEqual(projects.leave_out_folders(row),
                         ["/home/sam/debug/hedges", str(app_code_root()), LETTERS])


if __name__ == "__main__":
    unittest.main()

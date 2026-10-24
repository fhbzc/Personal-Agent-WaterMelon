"""A RECORD IS LEFT OUT BY THE PATHS AND SESSIONS IT RESTS ON (try 4,
2026-10-24): a test of an earlier try rested on one file of it and many
pages and windows, and was given to a debugger way that searched the user's
records; stages of the agent's own work rested on no state, and their own
record named the repository and the conversation they were in. The pages
and window titles do not count; a stage rests on its own references and
work's locations too; a path is read without the punctuation after it, and
a session by the start of its id."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from personalized_agent.agent.work import context_search
from personalized_agent.data_manage.storage.database import Database
from personalized_agent.learning.leave_out import LeaveOut

TRIES = "/home/sam/trials"
ORCHARD = "/home/sam/orchard-tools"
AT = "2026-01-05T10:00:00+00:00"
SESSION = "5d2c7e91-0b6a-4c1e-9f3d-2a7b8c9d0e1f"


class TheRecordsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.directory.name) / "agent.sqlite")

    def tearDown(self) -> None:
        self.db.close() if hasattr(self.db, "close") else None
        self.directory.cleanup()

    def _state(self, ident: str, title: str, *sources: str) -> None:
        self.db.execute(
            "INSERT INTO states (id, title, detail, source, about, starts_at, ends_at, status, "
            "created_at, updated_at, metadata) VALUES (?, ?, '', 'agent', 'user', ?, '', "
            "'active', ?, ?, ?)",
            (ident, title, AT, AT, AT, json.dumps({"sources": list(sources)})))

    def _stage(self, ident: str, summary: str, work: dict, deliverable: dict) -> None:
        work = dict(work, keywords=["harvest", "plan"])
        self.db.execute(
            "INSERT INTO experience_stages (id, scope, task_id, kind, medium, summary, deliverable, "
            "evaluation, states, began, ended, written_by, notes, created_at, updated_at, work) "
            "VALUES (?, 'live', 't', 'execution', 'code', ?, ?, '{}', '[]', ?, ?, '{}', '{}', ?, ?, ?)",
            (ident, summary, json.dumps(deliverable), AT, AT, AT, AT, json.dumps(work)))

    def _found(self, *folders, sessions=()) -> set:
        out = LeaveOut(list(folders))
        out.sessions = set(sessions)
        return {one["id"] for one in context_search.records(self.db, out)}

    def test_pages_and_windows_do_not_hide_a_file_left_out(self) -> None:
        self._state("s1", "Tried the two sites by hand", f"{TRIES}/C/worker.py",
                    "http://127.0.0.1:8642/tasks/1", "http://127.0.0.1:5000/tasks/1",
                    "Start your worker - Mozilla Firefox", "Donate - Mozilla Firefox")
        self._state("s2", "Read the news", "https://example.org/a", "News - Mozilla Firefox")
        self.assertEqual(self._found(TRIES), {"s2"})

    def test_a_path_with_the_punctuation_after_it(self) -> None:
        self._state("s1", "Released the tools", f"{ORCHARD})")
        self.assertEqual(self._found(ORCHARD), set())

    def test_a_stage_by_its_own_references_and_work(self) -> None:
        self._stage("g1", "Fixed the planner", {"input": [{"what": "the ask", "location": ""}]},
                    {"references": [{"location": f"{ORCHARD} @ e4a90f2c1d7b", "what": "the commit"}]})
        self._stage("g2", "Talked the design through",
                    {"input": [{"what": "the talk", "location": f"conversation session {SESSION[:8]}, "
                                                                "2026-01-05T10:00Z"}]}, {})
        self._stage("g3", "Wrote the letters", {}, {"references": [
            {"location": "/home/sam/letters/a.md", "what": "the letter"}]})
        self.assertEqual(self._found(ORCHARD, sessions=[SESSION]), {"g3"})
        self.assertEqual(self._found(), {"g1", "g2", "g3"}, "no leave-out: all of it")


if __name__ == "__main__":
    unittest.main()

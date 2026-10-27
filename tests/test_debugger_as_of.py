"""A TRY OF THE WORK DEBUGGER IS GIVEN THE USER AS THEY WERE WHEN IT BEGAN
(the user, 2026-10-25): its records and what was learned of their work come
from a copy made then - nothing written since, a state that ended after it,
a case or a reason changed since - for every way of it; real work from the
database as it is. What the user took away since stays away; the copy is
made again when the try begins again; the local index finds no file changed
after it."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from support import TemporaryAgent

from personalized_agent.agent.work import as_of, context_search
from personalized_agent.agent.work.runner import Run
from personalized_agent.agent.work.tools import WorkTools
from personalized_agent.data_manage.storage.database import Database
from personalized_agent.learning.store import LearningStore

START = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)
BEFORE = (START - timedelta(days=2)).isoformat()
AFTER = (START + timedelta(hours=3)).isoformat()
REF = "dbg_orchard~2"


def _at(moment: datetime) -> str:
    return moment.isoformat()


class _Live(unittest.TestCase):

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.live = Database(Path(self.directory.name) / "database" / "agent.db")
        self.store = LearningStore(self.live)

    def tearDown(self) -> None:
        self.live.close()
        if hasattr(self, "copy") and self.copy is not None:
            self.copy.close()
        self.directory.cleanup()

    def state(self, ident: str, title: str, starts: str, ends: str = "",
              created: str = "") -> None:
        self.live.execute(
            "INSERT INTO states (id, title, detail, source, about, starts_at, ends_at, status, "
            "created_at, updated_at, metadata) VALUES (?, ?, '', 'agent', 'user', ?, ?, "
            "'active', ?, ?, '{}')", (ident, title, starts, ends, created or starts,
                                      created or starts))

    def task(self, ident: str, title: str, updated: str) -> None:
        self.live.execute(
            "INSERT INTO state_tasks (id, number, members, described, created_at, updated_at) "
            "VALUES (?, 1, '[]', ?, ?, ?)",
            (ident, json.dumps({"title": title, "summary": title,
                                "directories": ["/home/sam/orchard"]}), BEFORE, updated))

    def learned(self) -> None:
        """A unit; an old case, a case changed after the start; judgments
        before and after; a reason before, one after; their links."""
        self.store._put("learn_units", {"id": "place:orchard", "kind": "place",
                                        "key": "/home/sam/orchard", "title": "orchard",
                                        "state": "active", "updated_at": AFTER})
        for ident, created, updated in (("C-old", BEFORE, BEFORE), ("C-late", BEFORE, AFTER),
                                        ("C-new", AFTER, AFTER)):
            self.store._put("learn_cases", {"id": ident, "unit_id": "place:orchard",
                                            "title": f"case {ident}", "alias_of": "",
                                            "created_at": created, "updated_at": updated})
        for ident, case, created in (("J-old", "C-old", BEFORE), ("J-new", "C-old", AFTER),
                                     ("J-gone", "C-late", BEFORE)):
            self.store._put("learn_judgments", {"id": ident, "case_id": case,
                                                "direction": f"judged {ident}",
                                                "created_at": created})
        for ident, created in (("R-old", BEFORE), ("R-new", AFTER)):
            self.store._put("learn_reasons", {"id": ident, "statement": f"reason {ident}",
                                              "state": "active", "created_at": created,
                                              "updated_at": created})
        for reason, judgment in (("R-old", "J-old"), ("R-old", "J-new"), ("R-new", "J-old")):
            self.live.execute("INSERT INTO learn_links (reason_id, judgment_id, role, "
                              "predicted, why, at) VALUES (?, ?, 'supports', 0, '', ?)",
                              (reason, judgment, BEFORE))

    def made(self, *started: str) -> Database:
        self.copy = as_of.for_try(self.live, REF, list(started) or [_at(START)])
        self.assertIsNotNone(self.copy)
        return self.copy

    def ids(self, db: Database, table: str):
        return {row["id"] for row in db.query(f"SELECT id FROM {table}")}


class TheCopyTests(_Live):

    def test_nothing_written_after_the_try_began(self) -> None:
        self.state("s-old", "Pruned the pear trees", BEFORE, BEFORE)
        self.state("s-across", "Read the review of the ways", BEFORE, AFTER)
        self.state("s-new", "Said which way is better", AFTER, AFTER)
        self.task("t-old", "The orchard", BEFORE)
        self.task("t-late", "The orchard, reviewed", AFTER)
        self.learned()
        copy = self.made()
        self.assertEqual(self.ids(copy, "states"), {"s-old"},
                         "one that ended after it, or began after it: not given")
        late = json.loads(copy.query_one("SELECT described FROM state_tasks WHERE id = 't-late'")
                          ["described"])
        self.assertEqual(late, {"directories": ["/home/sam/orchard"]},
                         "a project rewritten since: its folders, none of its words")
        self.assertEqual(self.ids(copy, "learn_cases"), {"C-old"},
                         "a case changed since, or made since: not given")
        self.assertEqual(self.ids(copy, "learn_judgments"), {"J-old"},
                         "made since, or of a case not given: not given")
        self.assertEqual(self.ids(copy, "learn_reasons"), {"R-old"})
        links = {(row["reason_id"], row["judgment_id"])
                 for row in copy.query("SELECT * FROM learn_links")}
        self.assertEqual(links, {("R-old", "J-old")})
        self.assertEqual(as_of.cutoff_of(copy), START)

    def test_the_records_searched_are_the_copys(self) -> None:
        self.state("s-old", "Pruned the pear trees", BEFORE, BEFORE)
        self.state("s-new", "Pruned the pear trees again", AFTER, AFTER)
        found = {one["id"] for one in context_search.records(self.made())}
        self.assertEqual(found, {"s-old"})
        everything = {one["id"] for one in context_search.records(self.live)}
        self.assertEqual(everything, {"s-old", "s-new"}, "real work: all of it")

    def test_one_copy_a_try_cut_at_its_first_start(self) -> None:
        self.state("s-old", "Pruned the pear trees", BEFORE, BEFORE)
        first = self.made(_at(START + timedelta(minutes=5)), _at(START))
        self.assertEqual(as_of.cutoff_of(first), START, "the earliest of its ways' starts")
        first.close()
        # Written since: a way going again after a restart is given the same.
        self.state("s-between", "Planted a row", BEFORE, BEFORE)
        again = self.made(_at(START + timedelta(hours=9)))
        self.assertEqual(self.ids(again, "states"), {"s-old"}, "nothing added")
        self.assertEqual(as_of.cutoff_of(again), START)

    def test_what_the_user_took_away_stays_away(self) -> None:
        self.learned()
        self.state("s-old", "Pruned the pear trees", BEFORE, BEFORE)
        self.made().close()
        self.live.execute("DELETE FROM learn_cases WHERE id = 'C-old'")
        self.live.execute("DELETE FROM states WHERE id = 's-old'")
        self.live.execute("UPDATE learn_units SET state = 'inactive'")
        copy = self.made()
        self.assertEqual(self.ids(copy, "learn_cases"), set())
        self.assertEqual(self.ids(copy, "learn_judgments"), set(), "theirs with them")
        self.assertEqual(self.ids(copy, "states"), set())
        self.assertEqual(copy.query_one("SELECT state FROM learn_units")["state"], "inactive",
                         "a unit held back as it is now")

    def test_made_again_when_the_try_begins_again(self) -> None:
        self.state("s-old", "Pruned the pear trees", BEFORE, BEFORE)
        self.state("s-new", "Grafted the apples", AFTER, AFTER)
        self.made().close()
        as_of.forget(self.live, [REF])
        self.assertFalse(as_of.path_for(self.live, REF).exists())
        copy = self.made(_at(START + timedelta(days=1)))
        self.assertEqual(self.ids(copy, "states"), {"s-old", "s-new"})

    def test_it_lies_with_the_agents_database(self) -> None:
        self.made()
        self.assertEqual(as_of.path_for(self.live, REF).parent,
                         Path(self.live.path).parent / as_of.FOLDER)

    def test_times_with_an_offset_or_a_day(self) -> None:
        self.assertEqual(as_of.when("2026-03-02T04:00:00-05:00"), START)
        self.assertEqual(as_of.when("2026-03-02T09:00:00Z"), START)
        self.assertEqual(as_of.when("2026-03-02").date(), START.date())
        self.assertIsNone(as_of.when(""))


class WhichWayIsGivenItTests(_Live):

    def run_of(self, debug):
        return SimpleNamespace(owner=SimpleNamespace(db=self.live),
                               unit={"id": "u", "debug": debug} if debug else {"id": "u"},
                               started_at=_at(START))

    def test_real_work_reads_the_database_as_it_is(self) -> None:
        self.assertIs(Run._user_db(self.run_of(None)), self.live)

    def test_every_way_of_a_try_the_copy(self) -> None:
        self.state("s-new", "Said which way is better", AFTER, AFTER)
        for variant in ("single", "noctx", "full"):
            one = self.run_of({"project": REF, "variant": variant})
            copy = Run._user_db(one)
            self.assertIsNot(copy, self.live, variant)
            self.assertEqual(self.ids(copy, "states"), set(), variant)
            copy.close()

    def test_no_copy_nothing_of_the_user(self) -> None:
        one = SimpleNamespace(owner=SimpleNamespace(db=SimpleNamespace(query=lambda *a: [])),
                              unit={"debug": {"project": REF, "variant": "full"}},
                              started_at=_at(START))
        self.assertIsNone(Run._user_db(one), "a database not in a file: no copy, nothing")


class TheIndexTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        app = self.agent.app
        unit = {"id": "unit_as_of", "name": "A pledge form", "kind": "task"}
        self.folder = Path(app.work.folder_of(unit))
        runtime = app.work._runtime(unit, self.folder)
        runtime.mkdir(self.folder)
        self.tools = WorkTools(runtime)
        self.old = self.folder / "old_notes.md"
        self.new = self.folder / "new_notes.md"
        for one in (self.old, self.new):
            one.write_text("pear trees\n", encoding="utf-8")
        long_ago = time.time() - 30 * 86400
        os.utime(self.old, (long_ago, long_ago))

        class _Index:
            def search(index, query, allowed, limit=8):
                return [{"path": str(one), "found_in": "name"}
                        for one in (self.old, self.new) if allowed(str(one))]
        self.tools.files_index = _Index()

    def tearDown(self) -> None:
        self.agent.cleanup()

    def found(self):
        return {Path(one["path"]).name for one in
                self.tools.search_files({"query": "pear trees"})["found"]}

    def test_a_file_changed_after_the_try_began_is_not_found(self) -> None:
        self.assertEqual(self.found(), {"old_notes.md", "new_notes.md"}, "real work: both")
        self.tools.as_of = datetime.now(timezone.utc) - timedelta(days=1)
        self.assertEqual(self.found(), {"old_notes.md"})


if __name__ == "__main__":
    unittest.main()

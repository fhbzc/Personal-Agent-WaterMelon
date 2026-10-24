"""THE LOCAL INDEX, KEPT SMALL AND ON THE LEARNING'S CLOCK (the user,
2026-10-24): measured on one machine it was 2.4 GB, and every start of the
application walked 128,000 files again. Now each folder is walked once and
only once, a JSON file is read to 200 KB, a conversation record is kept as
what was said and done, a change of those rules reads again only what is
kept differently, the file is made small again, and it is brought up to
date at the start of every learning update - never at the start."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

import support  # noqa: F401

from personalized_agent.index import rules, service
from personalized_agent.index.build import build, outermost
from personalized_agent.index.store import IndexStore, fts5_available
from personalized_agent.index.talk import talk_text


def _line(**entry) -> str:
    return json.dumps(entry) + "\n"


CLAUDE_RECORD = "".join([
    _line(type="user", message={"role": "user", "content":
          "Make the seed planner print a table.<system-reminder>The tool's own note."
          "</system-reminder>"}),
    _line(type="user", isMeta=True, message={"role": "user", "content": "Caveat from the tool."}),
    _line(type="assistant", message={"role": "assistant", "content": [
        {"type": "thinking", "thinking": "Weighing the columns quietly."},
        {"type": "text", "text": "I will add a table to the planner."},
        {"type": "tool_use", "name": "Edit", "input": {
            "file_path": "/home/sam/seeds/plan.py", "old_string": "print(rows)",
            "new_string": "for row in rows:\n    print(row)"}},
        {"type": "tool_use", "name": "Bash", "input": {
            "command": "SEED_TOKEN=abc123 python plan.py", "description": "Run the planner"}},
        {"type": "tool_use", "name": "Grep", "input": {"pattern": "sow_date", "path": "/home/sam/seeds"}}]}),
    _line(type="user", toolUseResult={}, message={"role": "user", "content": [
        {"type": "tool_result", "content": "Traceback: the whole output", "is_error": True}]}),
    _line(type="attachment", attachment={"text": "an attachment"}),
])

CODEX_RECORD = "".join([
    _line(type="session_meta", payload={"id": "s1", "cwd": "/home/sam/seeds"}),
    _line(type="response_item", payload={"type": "message", "role": "developer", "content": [
        {"type": "input_text", "text": "<permissions instructions>sandbox</permissions instructions>"}]}),
    _line(type="response_item", payload={"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "<environment_context>cwd</environment_context>"},
        {"type": "input_text", "text": "Sort the seeds by sowing week."}]}),
    _line(type="response_item", payload={"type": "reasoning", "summary": []}),
    _line(type="response_item", payload={"type": "function_call", "name": "shell", "arguments":
          json.dumps({"command": ["bash", "-lc", "cat plan.py"], "workdir": "/home/sam/seeds"})}),
    _line(type="response_item", payload={"type": "custom_tool_call", "name": "apply_patch", "input":
          "*** Begin Patch\n*** Update File: plan.py\n@@\n-a\n+b\n*** End Patch"}),
    _line(type="response_item", payload={"type": "function_call_output", "output": "lots of output"}),
    _line(type="response_item", payload={"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": "Sorted by sowing week."}]}),
    _line(type="event_msg", payload={"type": "agent_message", "message": "Sorted by sowing week."}),
])


class AConversationRecordTests(unittest.TestCase):

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _record(self, name: str, text: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_what_was_said_and_done_not_what_came_back(self) -> None:
        text = talk_text(self._record("one.jsonl", CLAUDE_RECORD))
        self.assertEqual(text.splitlines(), [
            "user: Make the seed planner print a table.",
            "assistant: I will add a table to the planner.",
            "  [Edit] /home/sam/seeds/plan.py (+2 -1)",
            "  [Bash] Run the planner",
            '  [Grep] in /home/sam/seeds "sow_date"',
            "  [failed]"])
        for never in ("tool's own note", "Caveat", "Weighing", "abc123", "Traceback",
                      "attachment"):
            self.assertNotIn(never, text)

    def test_a_subagent_keeps_its_replies_not_its_prompt(self) -> None:
        text = talk_text(self._record("s/subagents/agent-1.jsonl", CLAUDE_RECORD))
        self.assertNotIn("user:", text)
        self.assertIn("assistant: I will add a table to the planner.", text)

    def test_codex_too(self) -> None:
        text = talk_text(self._record("codex.jsonl", CODEX_RECORD))
        self.assertEqual(text.splitlines(), [
            "user: Sort the seeds by sowing week.",
            "  [shell] in /home/sam/seeds",
            "  [apply_patch] plan.py",
            "assistant: Sorted by sowing week."])

    def test_bounded_by_what_is_kept_not_by_the_file(self) -> None:
        """A record of 1.3 GB kept under 8 MB: the longest records are the
        user's main work, and are read."""
        record = self._record("long.jsonl", CLAUDE_RECORD + _line(
            type="progress", data="x" * 400_000) * 3)
        self.assertGreater(record.stat().st_size, 1_000_000)
        self.assertEqual(rules.how_kept(record, record.stat(), sessions=True), "talk")
        self.assertLess(len(talk_text(record)), 400)
        self.assertIsNone(talk_text(record, most=100), "past the most kept: its name")

    def test_kept_as_talk_and_json_to_200_kb(self) -> None:
        record = self._record("r.jsonl", CLAUDE_RECORD)
        self.assertEqual(rules.how_kept(record, record.stat(), sessions=True), "talk")
        small = self._record("small.json", '{"a": 1}' + " " * 150_000)
        big = self._record("big.json", '{"a": 1}' + " " * 250_000)
        self.assertEqual(rules.how_kept(small, small.stat()), "text")
        self.assertEqual(rules.how_kept(big, big.stat()), "name")


@unittest.skipUnless(fts5_available(), "this Python's SQLite has no FTS5")
class EachFolderOnceTests(unittest.TestCase):

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.work = Path(self.directory.name)
        self.outer = self.work / "orchard"
        self.inner = self.outer / "pears"
        self.inner.mkdir(parents=True)
        (self.outer / "apples.md").write_text("Apples ripen in autumn.\n", encoding="utf-8")
        (self.inner / "pears.md").write_text("Pears keep in the cellar.\n", encoding="utf-8")
        self.store = IndexStore(self.work / "files.db")

    def tearDown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    def test_a_folder_inside_another_is_walked_with_it(self) -> None:
        roots = [{"path": str(self.inner)}, {"path": str(self.outer)}]
        walked, within = outermost(roots)
        self.assertEqual([one["path"] for one in walked], [str(self.outer)])
        self.assertEqual(within, {str(self.inner): str(self.outer)})
        first = build(self.store, roots)
        self.assertEqual(first["read"], 2, "each file read once")
        again = build(self.store, roots)
        self.assertEqual((again["read"], again["unchanged"]), (0, 2), "and not again")
        states = {one["path"]: one["state"] for one in self.store.roots()}
        self.assertEqual(states, {str(self.outer): "done", str(self.inner): "within"})
        self.assertTrue(self.store.search("cellar"))

    def test_kept_before_under_the_inner_folder_not_read_again(self) -> None:
        build(self.store, [{"path": str(self.inner)}])
        said = build(self.store, [{"path": str(self.inner)}, {"path": str(self.outer)}])
        self.assertEqual((said["read"], said["unchanged"]), (1, 1), "only apples.md is new")
        roots = {row["root"] for row in self.store.con.execute("SELECT root FROM files")}
        self.assertEqual(roots, {str(self.outer)})

    def test_rules_changed_reads_again_only_what_is_kept_differently(self) -> None:
        big = self.outer / "results.json"
        big.write_text(json.dumps({"rows": list(range(60_000))}), encoding="utf-8")
        self.assertGreater(big.stat().st_size, rules.MOST_JSON_BYTES)
        build(self.store, [{"path": str(self.outer)}])
        # As an index built by the old rules left it: the JSON read whole.
        row = self.store.con.execute("SELECT id FROM files WHERE path = ?", (str(big),)).fetchone()
        self.store.con.execute("UPDATE files SET kind = 'text' WHERE id = ?", (row[0],))
        self.store.set_meta("rules", "1")
        self.store.commit()
        said = build(self.store, [{"path": str(self.outer)}])
        self.assertEqual((said["kept_name"], said["unchanged"]), (1, 2))
        self.assertEqual(self.store.meta("rules"), str(rules.RULES_VERSION))
        self.assertEqual(build(self.store, [{"path": str(self.outer)}])["unchanged"], 3)

    def test_the_file_made_small_again(self) -> None:
        for n in range(300):
            (self.outer / f"note{n}.txt").write_text(f"word{n} " * 400, encoding="utf-8")
        build(self.store, [{"path": str(self.outer)}])
        self.assertFalse(self.store.tidy()["vacuumed"], "no room to give back")
        for n in range(300):
            (self.outer / f"note{n}.txt").unlink()
        build(self.store, [{"path": str(self.outer)}])
        said = self.store.tidy()
        self.assertTrue(said["vacuumed"])
        self.assertLess(said["pages_after"], said["pages"])


@unittest.skipUnless(fts5_available(), "this Python's SQLite has no FTS5")
class ItsOwnBarTests(unittest.TestCase):
    """The Learning panel's bar for the index, beside the learning's: an
    update stood at 0% while the index was built (the user, 2026-10-24)."""

    def test_the_builder_says_how_far_it_is(self) -> None:
        from personalized_agent.index import build as building
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "barn"
            root.mkdir()
            for n in range(5):
                (root / f"hay{n}.txt").write_text("bale", encoding="utf-8")
            store = IndexStore(Path(folder) / "files.db")
            try:
                heard = []
                build(store, [{"path": str(root)}], told=heard.append)
                self.assertEqual(heard[0], {"expected": 0, "expected_all": False, "walked": 0},
                                 "never built: how many is not known")
                heard = []
                with unittest.mock.patch.object(building, "TELL_EVERY", 2):
                    build(store, [{"path": str(root)}], told=heard.append)
                self.assertEqual(heard[0], {"expected": 5, "expected_all": True, "walked": 0})
                self.assertEqual([one["walked"] for one in heard if "walked" in one][1:4],
                                 [0, 2, 4])
                self.assertEqual(heard[-1]["walked"], 5)
            finally:
                store.close()

    def test_shown_with_its_share_or_only_that_it_goes(self) -> None:
        class Child:
            def poll(self):
                return None
        index = service.LocalIndex.__new__(service.LocalIndex)
        index._child = Child()
        index._progress = {"expected": 200, "expected_all": True, "walked": 50,
                           "root": "/home/sam/barn"}
        said = index.shown()
        self.assertEqual((said["share"], said["root"], said["expected"]),
                         (0.25, "/home/sam/barn", 200))
        index._progress = {"expected": 120, "expected_all": False, "walked": 50}
        self.assertIsNone(index.shown()["share"], "a folder never built: not known")
        index._progress = {"expected": 200, "expected_all": True, "walked": 400, "tidying": True}
        said = index.shown()
        self.assertEqual((said["share"], said["tidying"]), (0.99, True))

    def test_the_panel_draws_both(self) -> None:
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        script = (web / "app.js").read_text(encoding="utf-8")
        self.assertIn("function learningIndexHtml(s)", script)
        self.assertIn('aria-label="How far the local index is"', script)
        self.assertIn("${learningIndexHtml(s)}${lines.join(\"\")}", script)


class OnTheLearningsClockTests(unittest.TestCase):

    def test_no_build_at_the_start_nor_every_few_hours(self) -> None:
        self.assertFalse(hasattr(service, "FIRST_AFTER_SECONDS"))
        self.assertFalse(hasattr(service, "EVERY_SECONDS"))

    def test_the_learnings_switch(self) -> None:
        class Learning:
            on = False

            def auto_on(self):
                return self.on

        class App:
            learning = Learning()
            config = {}
        index = service.LocalIndex.__new__(service.LocalIndex)
        index.app = App()
        self.assertFalse(index.enabled())
        App.learning.on = True
        self.assertTrue(index.enabled())

    def test_brought_up_to_date_at_the_start_of_an_update_once(self) -> None:
        from personalized_agent.learning.service import Learning, Stop
        with tempfile.TemporaryDirectory() as folder:
            from personalized_agent.data_manage.storage.database import Database
            db = Database(Path(folder) / "agent.sqlite")

            class Config(dict):
                def set(self, key, value, **_kw):
                    self[key] = value
            learning = Learning(db, Config(), None, Path(folder) / "data")
            built = []
            learning.before_update = lambda: built.append(time.time())
            # Never the user's own records: none here.
            learning.places.sessions = lambda *args, **kwargs: []

            def plan(*_args):
                raise Stop()
            learning._plan = plan
            update_id = learning.store.save_update({"cutoff": "2026-01-05T10:00:00+00:00",
                                                    "by": "you", "state": "running", "plan": {}})
            with self.assertRaises(Stop):
                learning._go(update_id)
            self.assertEqual(len(built), 1)
            self.assertTrue(learning.store.update(update_id)["plan"]["indexed"])
            with self.assertRaises(Stop):
                learning._go(update_id)
            self.assertEqual(len(built), 1, "carried on: not built again")
            if hasattr(db, "close"):
                db.close()


if __name__ == "__main__":
    unittest.main()

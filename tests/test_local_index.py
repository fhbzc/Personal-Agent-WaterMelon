"""THE LOCAL INDEX: the user's own files, searchable by their words - built
in a process of its own at the lowest priority, reading again only what
changed, and searched only for what the asker may read. No model is called.
Fixtures are made up; nothing of the user's is read."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import TemporaryAgent

from personalized_agent.index import rules
from personalized_agent.index.build import build, pieces_of
from personalized_agent.index.store import IndexStore, fts5_available
from personalized_agent.index.tokens import query, words


class TheWordsTests(unittest.TestCase):

    def test_latin_words_and_pairs_of_cjk(self) -> None:
        self.assertEqual(words("Rebuild the Garden_planner v2!"),
                         ["rebuild", "the", "garden_planner", "v2"])
        self.assertEqual(words("浇水计划"), ["浇水", "水计", "计划"])
        self.assertEqual(words("水"), ["水"])
        self.assertEqual(words("x " + "a" * 60), [], "a letter alone, and no hashes")
        # What is asked is written the same way, each word quoted.
        self.assertEqual(query("计划 OR drop"), '"计划" "or" "drop"')
        self.assertEqual(query("计划 drop", any_word=True), '"计划" OR "drop"')


class TheRulesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="pa-index-rules-"))

    def _file(self, name, data=b"hello words\n"):
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_what_is_read_named_or_left(self) -> None:
        kept = lambda path: rules.how_kept(path, path.stat())
        self.assertEqual(kept(self._file("notes.md")), "text")
        self.assertEqual(kept(self._file("photo.png", b"\x89PNG\0\0")), "name")
        self.assertEqual(kept(self._file("README", b"plain words")), "text")
        self.assertEqual(kept(self._file("blob", b"\0\0\0binary")), "name")
        # Named like a secret: never read.
        self.assertEqual(kept(self._file("api_key.txt")), "name")
        self.assertEqual(kept(self._file(".env")), "")
        # A data file past its size: its name.
        self.assertEqual(kept(self._file("big.csv", b"a,b\n" * 900_000)), "name")
        # Conversation records are read whole, to a larger size.
        record = self._file("session.jsonl", b'{"text": "hi"}\n' * 300_000)
        self.assertEqual(kept(record), "name")
        self.assertEqual(rules.how_kept(record, record.stat(), sessions=True), "text")

    def test_folders_never_gone_into(self) -> None:
        for name in (".git", "node_modules", "__pycache__", ".venv", ".ssh", "build"):
            self.assertTrue(rules.skipped_folder(self.folder / name), name)
        venv = self.folder / "myenv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = x")
        self.assertTrue(rules.skipped_folder(venv))
        # A folder of the user's called "library" is theirs.
        self.assertFalse(rules.skipped_folder(self.folder / "library"))
        self.assertFalse(rules.skipped_folder(self.folder / "notes"))
        # The places it may never go.
        self.assertTrue(rules.skipped_folder(self.folder / "data" / "sub",
                                             never=[self.folder / "data"]))

    def test_a_file_only_in_the_cloud_is_not_read(self) -> None:
        class Info:
            st_file_attributes = 0x00400000
        self.assertTrue(rules.cloud_only(Info(), "report.docx"))
        self.assertTrue(rules.cloud_only(type("I", (), {})(), ".report.docx.icloud"))
        self.assertFalse(rules.cloud_only(type("I", (), {"st_file_attributes": 0})(),
                                          "report.docx"))


@unittest.skipUnless(fts5_available(), "this Python's SQLite has no FTS5")
class BuildingAndSearchingTests(unittest.TestCase):

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="pa-index-"))
        self.folder = self.work / "garden"
        (self.folder / "src").mkdir(parents=True)
        (self.folder / "src" / "plan.py").write_text(
            "def watering_schedule():\n    return 'twice a week'\n", encoding="utf-8")
        (self.folder / "notes.md").write_text("# 浇水计划\nThe tomatoes need shade.\n",
                                              encoding="utf-8")
        (self.folder / "sunflower.png").write_bytes(b"\x89PNG\0\0\0")
        (self.folder / "node_modules").mkdir()
        (self.folder / "node_modules" / "lib.js").write_text("watering_schedule", encoding="utf-8")
        self.store = IndexStore(self.work / "files.db")

    def tearDown(self) -> None:
        self.store.close()

    def _build(self, **kw):
        return build(self.store, [{"path": str(self.folder)}], **kw)

    def test_found_by_its_words_its_name_and_in_chinese(self) -> None:
        said = self._build()
        self.assertEqual((said["read"], said["kept_name"]), (2, 1))
        found = self.store.search("watering schedule")
        self.assertEqual([Path(one["path"]).name for one in found][:1], ["plan.py"])
        self.assertEqual(found[0]["line"], 1)
        # Not in the folders it never goes into.
        self.assertFalse(any("node_modules" in one["path"] for one in found))
        self.assertEqual(Path(self.store.search("计划")[0]["path"]).name, "notes.md")
        # A picture, by its name.
        named = self.store.search("sunflower")
        self.assertEqual((Path(named[0]["path"]).name, named[0]["found_in"]),
                         ("sunflower.png", "name"))
        # Nothing holds all the words: any of them.
        self.assertTrue(self.store.search("tomatoes zebraquokka"))

    def test_only_what_changed_is_read_again_and_what_is_gone_forgotten(self) -> None:
        self._build()
        again = self._build()
        self.assertEqual((again["read"], again["unchanged"]), (0, 3))
        plan = self.folder / "src" / "plan.py"
        plan.write_text("def pruning():\n    pass\n", encoding="utf-8")
        os.utime(plan, (time.time() + 5, time.time() + 5))
        (self.folder / "notes.md").unlink()
        third = self._build()
        self.assertEqual((third["read"], third["forgotten"]), (1, 1))
        self.assertFalse(self.store.search("watering schedule"))
        self.assertTrue(self.store.search("pruning"))
        self.assertFalse(self.store.search("tomatoes"))

    def test_a_stop_keeps_what_was_read(self) -> None:
        calls = []

        def going():
            calls.append(1)
            return len(calls) < 2
        said = self._build(going=going)
        self.assertTrue(said["stopped"])
        self.assertEqual(self.store.roots()[0]["state"], "building")
        rest = self._build()
        self.assertEqual(self.store.roots()[0]["state"], "done")
        self.assertEqual(rest["read"] + rest["kept_name"] + rest["unchanged"], 3)

    def test_pieces_say_where_they_start(self) -> None:
        text = "\n".join(f"line {n}" for n in range(1, 91))
        self.assertEqual([one[0] for one in pieces_of(text)], [1, 41, 81])


@unittest.skipUnless(fts5_available(), "this Python's SQLite has no FTS5")
class TheApplicationsIndexTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.folder = Path(self.agent.dir) / "mine"
        self.folder.mkdir()
        (self.folder / "recipe.txt").write_text("Bake the sourdough at dawn.\n", encoding="utf-8")
        self.secret = Path(self.agent.dir) / "elsewhere"
        self.secret.mkdir()

    def tearDown(self) -> None:
        self.app.local_index.halt()
        self.agent.cleanup()

    def test_the_folders_a_build_in_its_own_process_and_a_search(self) -> None:
        """Its folders are WHERE THE LEARNING READS (learning/places.py),
        listed in Work's Learning panel - the index's own route no longer
        takes a folder."""
        from personalized_agent.api.errors import ApiError
        from personalized_agent.api.routes import build_routes
        routes = build_routes(self.app)

        class Req:
            def __init__(self, body):
                self.body = body

            def get(self, key, default=None):
                return self.body.get(key, default)

            def require(self, key):
                if self.body.get(key) in (None, ""):
                    raise ApiError(f"missing parameter: {key}")
                return self.body[key]
        for action in ("add", "remove", "sessions"):
            with self.assertRaises(ApiError, msg=action):
                routes[("POST", "/api/index")](Req({"action": action,
                                                    "path": str(self.folder)}))
        said = routes[("POST", "/api/learning")](Req({"action": "add",
                                                      "path": str(self.folder)}))
        self.assertEqual([one["path"] for one in said["places"]], [str(self.folder)])
        index = self.app.local_index
        self.assertEqual([(one["path"], one["why"]) for one in index.roots()],
                         [(str(self.folder), "added")])
        # The application's own data is never read.
        with self.assertRaises(ApiError):
            routes[("POST", "/api/learning")](Req({"action": "add",
                                                   "path": str(self.app.paths.data_dir)}))
        self.assertEqual(index.never(), [str(self.app.paths.data_dir)])
        index.build_now(wait=True)
        status = routes[("GET", "/api/index")](Req({}))
        self.assertFalse(status["building"])
        self.assertEqual(status["folders"][0]["state"], "done")
        self.assertEqual(status["files"], 1)
        # At the lowest priority the system has.
        self.assertIn("priority", index._progress)
        self.assertNotIn("not lowered", index._progress["priority"])
        found = index.search("sourdough")
        self.assertEqual(Path(found[0]["path"]).name, "recipe.txt")
        self.assertIn("Bake the sourdough", found[0]["text"])
        # Only what the asker may read.
        self.assertEqual(index.search("sourdough", allowed=lambda path: False), [])
        # Removed from the learning's places: forgotten at the next build.
        routes[("POST", "/api/learning")](Req({"action": "remove", "path": str(self.folder)}))
        index.build_now(wait=True)
        self.assertEqual(index.search("sourdough"), [])

    def test_the_conversation_records_when_listed_for_learning(self) -> None:
        """The records of the user's coding agents are indexed when they are
        one of the learning's places - the old switch of the index's own
        (index_sessions) no longer counts."""
        from unittest import mock
        from personalized_agent.learning import records as learning_records
        records = Path(self.agent.dir) / "claude_projects"
        records.mkdir()
        with mock.patch.object(learning_records, "claude_sessions_root", lambda: records):
            self.app.config.set("index_sessions", True)
            self.assertEqual(self.app.local_index.roots(), [])
            self.app.learning.places.add("", "claude")
            self.assertEqual([(one["path"], one["sessions"]) for one in
                              self.app.local_index.roots()], [(str(records), True)])

    def test_the_tool_gives_only_what_the_agent_may_read(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools

        class Index:
            def search(self, query, allowed, limit):
                paths = [str(Path(self_folder) / "recipe.txt"),
                         str(self.app_data / "settings" / "settings.json")]
                return [{"path": one, "line": 1, "text": "x"} for one in paths if allowed(one)]
        self_folder = self.folder
        Index.app_data = self.app.paths.data_dir
        tools = WorkTools(self.app.runtime)
        self.assertNotIn("search_files", [one.name for one in tools.specs()])
        tools.files_index = Index()
        self.assertIn("search_files", [one.name for one in tools.specs()])
        said = tools.call("search_files", {"query": "sourdough"})
        self.assertEqual([Path(one["path"]).name for one in said["found"]], ["recipe.txt"])


class TheSettingsCardTests(unittest.TestCase):
    """The Settings card "Search your files" is gone: the folders searched
    are the learning's places, listed in Work's Learning panel."""

    def test_no_card_in_settings_the_places_in_work(self) -> None:
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        html = " ".join((web / "index.html").read_text(encoding="utf-8").split())
        script = (web / "app.js").read_text(encoding="utf-8")
        for one in ('id="file-search-card"', 'id="file-search-on"', 'id="file-search-add"',
                    'id="file-search-sessions"', 'id="file-search-build"'):
            self.assertNotIn(one, html)
        self.assertNotIn("loadFileSearch", script)
        self.assertNotIn("renderFileSearch", script)
        self.assertNotIn('"/api/index"', script)
        self.assertIn('id="xp-learn"', html)
        self.assertIn('api.get("/api/learning")', script)


if __name__ == "__main__":
    unittest.main()

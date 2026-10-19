"""PACKAGE ALL LESSONS and LOAD LESSONS (heartbeat/lessons_package.py): every
lesson packed into one folder updated with only what is new, a zip of it, and
a package merged into another computer's lessons - newer kept, nothing lost."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from support import TemporaryAgent

from personalized_agent.data_manage.library import USER_NATURE, Record
from personalized_agent.heartbeat.core.calendar_nature import lesson_text
from personalized_agent.heartbeat.experience.store import ExperienceStore
from personalized_agent.heartbeat.lessons_package import (
    COPY_AT_MOST, IMPORTED_FOLDER, PACKAGE_FOLDER, carried_scope, ref_key)

ROOT = Path(__file__).resolve().parents[1]
SECRET = b"SECRET-KEY-CONTENT-never-packaged"


class _Usage:
    input_tokens = 100
    cached_input_tokens = 5
    cache_written_tokens = 0
    output_tokens = 20


class _Summariser:
    """A model that summarises: what it is asked is kept, to be read."""

    def __init__(self) -> None:
        self.asked = []

    def invoke(self, request, role=""):
        content = request.messages[0]["content"]
        self.asked.append({"system": request.system, "content": content,
                           "task": request.task})

        class Reply:
            pass

        reply = Reply()
        reply.text = ("SUMMARY: rows of measurements, the columns the paper's "
                      "table was computed from")
        reply.usage = _Usage()
        reply.model, reply.provider, reply.tool_calls = "fake", "test", []
        return reply


def _files(folder: Path) -> dict:
    return {str(path.relative_to(folder)): path.stat().st_mtime_ns
            for path in folder.rglob("*") if path.is_file()}


class _Seeded(unittest.TestCase):
    """An agent with lessons of every kind, and references of every kind."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.files = Path(tempfile.mkdtemp(prefix="pa-lessons-files-")).resolve()
        cls.small = cls.files / "referee_notes.txt"
        cls.small.write_text("The referee asked for seven points.\n", encoding="utf-8")
        cls.compressible = cls.files / "run_log.csv"
        line = b"step,loss,accuracy,0.1234,0.9876,the same line again\n"
        with open(cls.compressible, "wb") as handle:
            handle.write(line * (COPY_AT_MOST // len(line) + 2000))
        cls.incompressible = cls.files / "measurements.bin"
        with open(cls.incompressible, "wb") as handle:
            for _ in range(21):
                handle.write(os.urandom(1024 * 1024))
        cls.missing = cls.files / "draft_gone.pdf"
        cls.secret = cls.files / "id_rsa"
        cls.secret.write_bytes(SECRET)
        cls.url = "https://example.org/the-paper"

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.files, ignore_errors=True)

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.model = _Summariser()
        self.app.lessons_package.models = self.model
        self._seed(self.app)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _seed(self, app) -> None:
        live = ExperienceStore(app.db)
        # The agent's own copy of the small file, as materials keep them.
        copies = Path(app.library.root) / "references"
        copies.mkdir(parents=True, exist_ok=True)
        self.copy = copies / "referee_notes.text.txt"
        self.copy.write_text("The referee asked for seven points. (copy)", encoding="utf-8")
        protected = app.paths.credentials_dir / "api_key.txt"
        protected.write_bytes(SECRET)
        live.add_card({"id": "card_root", "unit_id": "task-a", "task_id": "task-a",
                       "title": "The reply to the referees",
                       "statement": "Every point answered",
                       "refs": [{"id": "r1", "location": str(self.small),
                                 "what": "the referee's notes"}]})
        live.add_stage({
            "id": "stage_one", "task_id": "task-a", "kind": "evaluation",
            "summary": "The user checked the measurements against the paper's table",
            "card_id": "card_root", "began": "2026-09-01T10:00:00+00:00",
            "work": {"references": [
                {"id": "r1", "location": str(self.small), "what": "the referee's notes",
                 "copies": {"text": str(self.copy)}},
                {"id": "r2", "location": str(self.compressible), "what": "the run log"},
                {"id": "r3", "location": str(self.incompressible),
                 "what": "the measurements the table was computed from"},
                {"id": "r4", "location": str(self.missing), "what": "the draft"},
                {"id": "r5", "location": self.url, "what": "the paper online"},
                {"id": "r6", "location": str(self.secret), "what": "a key file"},
                {"id": "r7", "location": str(protected), "what": "the app's key"}]}})
        live.add_stage({"id": "stage_two", "task_id": "task-a", "kind": "plan",
                        "summary": "Planned the reply", "card_id": "card_root",
                        "began": "2026-09-01T09:00:00+00:00"})
        live.add_edge("stage_one", "stage_two", "continues")
        record = app.library.write(Record(
            folder=USER_NATURE, title="Checks every number against its source",
            summary="Checks every number", source="experience",
            body="Kind: work nature.\n\nChecks every number.\n\n"
                 f"For example: the table checked\n  - {self.incompressible} (the data)\n\n"
                 "From the stages: stage_one",
            metadata={"examples": json.dumps([{
                "stage": "stage_one", "said": "check the totals against the data",
                "what": "the table checked", "references": [
                    {"id": "r3", "location": str(self.incompressible),
                     "what": "the data behind the table"}]}]),
                "stages": json.dumps(["stage_one"]), "aspect": "evaluation"}))
        self.nature_id = record.id
        live.set_nature_kind(record.id, "work")
        live.link_stage_nature("stage_one", record.id)
        live.save_pattern({"id": "pattern_a", "layer": "pattern",
                           "statement": "Checks numbers", "members": [record.id],
                           "support": 1, "nature_kind": "work"})
        live.save_pattern({"id": "principle_a", "layer": "principle",
                           "statement": "Rigour", "members": ["pattern_a"],
                           "support": 1, "nature_kind": "work"})
        live.save_learning("task-a", until="2026-09-02T00:00:00+00:00", done=1,
                           learned_at="2026-09-02T00:00:00+00:00")
        # A work project and its own tree.
        live.set_workspace("work_abc", "", "work", "My work project")
        work = ExperienceStore(app.db, "work:work_abc")
        work.add_card({"id": "card_work", "unit_id": "work_abc", "task_id": "work_abc",
                       "title": "The work's goal"})
        work.add_stage({"id": "stage_work", "task_id": "work_abc", "kind": "execution",
                        "summary": "The agent built it", "card_id": "card_work"})
        # The Experience debugger's: never carried.
        ExperienceStore(app.db, "work:dbg_0123:full").add_stage(
            {"id": "stage_dbg", "task_id": "dbg_0123:full", "kind": "plan",
             "summary": "a debugger way"})
        # A notification lesson.
        action = {"id": "calact_one", "acted_at": "2026-09-03T08:00:00+00:00",
                  "action": "delete", "task_id": "t1",
                  "event": json.dumps({"task": {"title": "Stretch reminder"}}),
                  "reasoned_at": "2026-09-03T08:01:00+00:00",
                  "why": "too frequent", "lesson": "Do not remind more than twice a day"}
        app.db.execute("INSERT INTO calendar_actions (id, acted_at, action, task_id, "
                       "event, reasoned_at, why, lesson) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       tuple(action.values()))
        app.db.execute("INSERT INTO calendar_nature (id, created_at, kind, content, "
                       "action_id) VALUES ('calnat_one', ?, 'entry', ?, 'calact_one')",
                       (action["reasoned_at"], lesson_text(action, action["lesson"])))

    def _package(self, app=None) -> dict:
        return (app or self.app).lessons_package.package()

    def _manifest(self, app=None) -> dict:
        folder = (app or self.app).lessons_package.folder
        latest = json.loads((folder / "latest.json").read_text(encoding="utf-8"))
        return json.loads((folder / latest["manifest"]).read_text(encoding="utf-8"))


class PackagingTests(_Seeded):

    def test_everything_is_packaged_with_its_references(self) -> None:
        stats = self._package()
        folder = self.app.lessons_package.folder
        self.assertEqual(folder, self.app.paths.workspace_dir / PACKAGE_FOLDER)
        for name in ("package.json", "latest.json", "history.json"):
            self.assertTrue((folder / name).is_file(), name)
        self.assertTrue(any((folder / "objects").rglob("*")))
        self.assertTrue(any((folder / "summaries").rglob("*.md")))
        manifest = self._manifest()
        self.assertEqual(manifest["format"], "personalized-agent-lessons")
        self.assertIn("experience_stages", manifest["schema"]["tables"])
        stages = {one["key"] for one in manifest["rows"]["stage"]}
        self.assertEqual(stages, {"stage_one", "stage_two", "stage_work"})
        self.assertNotIn("stage_dbg", json.dumps(manifest))
        self.assertIn("work_abc", {one["key"] for one in manifest["rows"]["workspace"]})
        self.assertIn(self.nature_id, {one["key"] for one in manifest["rows"]["nature"]})
        self.assertEqual({one["key"] for one in manifest["rows"]["calendar_action"]},
                         {"calact_one"})
        self.assertEqual(len(manifest["links"]["experience_edges"]), 1)
        self.assertEqual(len(manifest["links"]["calendar_nature"]), 1)
        # The zip: next to the folder, of the folder.
        zip_path = Path(stats["zip_path"])
        self.assertEqual(zip_path.parent, self.app.paths.workspace_dir)
        self.assertTrue(zip_path.name.startswith("lessons_"))
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
        self.assertIn(f"{PACKAGE_FOLDER}/latest.json", names)
        self.assertTrue(all(one.startswith(PACKAGE_FOLDER + "/") for one in names))
        # References: numbered, each stored as it can be.
        refs = {one["location"]: one for one in manifest["references"]}
        for one in refs.values():
            self.assertRegex(one["id"], r"^ref_\d{6}$")
        stored = lambda location: {f["role"]: f["stored"] for f in refs[location]["files"]}
        self.assertEqual(stored(str(self.small))["original"], "copied")
        self.assertEqual(stored(str(self.small))["copy_text"], "copied")
        self.assertEqual(stored(str(self.compressible))["original"], "compressed")
        self.assertEqual(stored(str(self.incompressible))["original"], "summarised")
        self.assertEqual(stored(str(self.missing))["original"], "missing")
        self.assertEqual(stored(str(self.secret))["original"], "excluded")
        self.assertEqual(refs[self.url]["is"], "url")
        self.assertEqual(refs[self.url]["files"], [])
        compressed = next(f for f in refs[str(self.compressible)]["files"])
        self.assertLessEqual((self.app.lessons_package.folder / compressed["object"])
                             .stat().st_size, COPY_AT_MOST)
        # The summary was asked with what the reference was cited for.
        self.assertEqual(len(self.model.asked), 2, "one summary per context")
        said = "\n".join(one["content"] for one in self.model.asked)
        self.assertIn("checked the measurements against the paper's table", said)
        self.assertIn("the measurements the table was computed from", said)
        self.assertIn("check the totals against the data", said)
        self.assertIn("the data behind the table", said)
        self.assertTrue(all(one["task"] == "lessons_package" for one in self.model.asked))
        self.assertIn("AS IT MATTERS FOR THAT REFERENCE",
                      " ".join(self.model.asked[0]["system"].split()))
        # The stats.
        self.assertEqual(stats["calls"], 2)
        self.assertEqual(stats["tokens_in"], 210)
        self.assertEqual(stats["tokens_in_cached"], 10)
        self.assertEqual(stats["tokens_out"], 40)
        refs_said = stats["references"]
        self.assertEqual(refs_said["summarised"], 2)
        self.assertEqual(refs_said["compressed"], 1)
        self.assertEqual(refs_said["missing"], 1)
        self.assertEqual(refs_said["excluded"], 2)
        self.assertGreater(stats["bytes_added"], 0)
        self.assertGreater(stats["package_bytes"], 0)
        self.assertGreater(stats["zip_bytes"], 0)
        self.assertEqual(stats["items"]["stage"]["new"], 3)
        described = self.app.lessons_package.describe()
        self.assertEqual(described["last"]["stamp"], stats["stamp"])
        self.assertEqual(len(described["history"]), 1)

    def test_no_credential_and_nothing_protected_is_packaged(self) -> None:
        stats = self._package()
        for path in self.app.lessons_package.folder.rglob("*"):
            if path.is_file():
                self.assertNotIn(SECRET, path.read_bytes(), path)
        with zipfile.ZipFile(stats["zip_path"]) as archive:
            for name in archive.namelist():
                self.assertNotIn(SECRET, archive.read(name), name)
        refs = {one["location"]: one for one in self._manifest()["references"]}
        protected = str(self.app.paths.credentials_dir / "api_key.txt")
        self.assertEqual(refs[protected]["files"][0]["why"],
                         "the application's protected state")

    def test_again_with_nothing_changed_redoes_nothing(self) -> None:
        first = self._package()
        folder = self.app.lessons_package.folder
        before = _files(folder / "objects")
        before.update({"s/" + k: v for k, v in _files(folder / "summaries").items()})
        asked = len(self.model.asked)
        second = self._package()
        self.assertEqual(len(self.model.asked), asked, "no model call")
        self.assertEqual(second["calls"], 0)
        refs = second["references"]
        self.assertEqual((refs["copied"], refs["compressed"], refs["summarised"]), (0, 0, 0))
        self.assertGreater(refs["reused"], 0)
        for kind, counts in second["items"].items():
            self.assertEqual(counts["unchanged"], counts["total"], kind)
        after = _files(folder / "objects")
        after.update({"s/" + k: v for k, v in _files(folder / "summaries").items()})
        self.assertEqual(before, after, "nothing copied, compressed or summarised again")
        self.assertNotEqual(first["manifest"], second["manifest"])
        self.assertTrue((folder / first["manifest"]).is_file(), "earlier manifests kept")
        self.assertEqual(len(self.app.lessons_package.describe()["history"]), 2)

    def test_only_what_changed_is_processed(self) -> None:
        first = self._package()
        folder = self.app.lessons_package.folder
        objects = _files(folder / "objects")
        manifest_bytes = (folder / first["manifest"]).read_bytes()
        live = ExperienceStore(self.app.db)
        live.correct_stage("stage_two", {"summary": "Planned the reply, point by point"},
                           "the user said so")
        added = self.files / f"new_figure_{os.getpid()}.txt"
        added.write_text("a figure's caption", encoding="utf-8")
        live.add_stage({"id": "stage_three", "task_id": "task-a", "kind": "execution",
                        "summary": "Made the figure", "work": {"references": [
                            {"id": "r1", "location": str(added), "what": "the figure"}]}})
        second = self._package()
        self.assertEqual(second["calls"], 0)
        self.assertEqual(second["items"]["stage"]["new"], 1)
        self.assertEqual(second["items"]["stage"]["updated"], 1)
        self.assertEqual(second["items"]["stage"]["unchanged"], 2)
        self.assertEqual(second["references"]["copied"], 1, "only the new file")
        later = _files(folder / "objects")
        for name, stamp in objects.items():
            self.assertEqual(later[name], stamp, f"{name} was touched")
        self.assertEqual(len(later), len(objects) + 1)
        self.assertEqual((folder / first["manifest"]).read_bytes(), manifest_bytes)
        refs = {one["location"]: one["id"] for one in self._manifest()["references"]}
        old = {one["location"]: one["id"] for one in
               json.loads(manifest_bytes.decode("utf-8"))["references"]}
        for location, number in old.items():
            self.assertEqual(refs[location], number, "numbers are kept")
        self.assertGreater(int(refs[str(added)][4:]), max(int(n[4:]) for n in old.values()))
        added.unlink()

    def test_a_cancelled_packaging_leaves_the_last_one(self) -> None:
        first = self._package()
        package = self.app.lessons_package
        package._cancel.set()
        from personalized_agent.heartbeat.lessons_package import Cancelled
        with self.assertRaises(Cancelled):
            package.package()
        package._cancel.clear()
        latest = json.loads((package.folder / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["manifest"], first["manifest"])

    def test_in_the_background_with_progress(self) -> None:
        package = self.app.lessons_package
        self.assertTrue(package.start_package())
        self.assertFalse(package.start_package(), "one at a time")
        package.wait(300)
        self.assertFalse(package.running())
        self.assertEqual(package.last_error, "")
        self.assertIsNotNone(package.describe()["last"])

    def test_scopes_carried(self) -> None:
        self.assertTrue(carried_scope("live"))
        self.assertTrue(carried_scope("work:work_abc"))
        self.assertFalse(carried_scope("work:dbg_0123:full"))
        self.assertFalse(carried_scope("test"))
        self.assertEqual(ref_key("C:\\Users\\X\\a.txt"), ref_key("c:/users/x/a.txt"))


class LoadingTests(_Seeded):

    def setUp(self) -> None:
        super().setUp()
        self.stats = self._package()
        self.other = TemporaryAgent()

    def tearDown(self) -> None:
        self.other.cleanup()
        super().tearDown()

    def _stage(self, app, stage_id):
        return ExperienceStore(app.db).stage(stage_id) or \
            ExperienceStore(app.db, "work:work_abc").stage(stage_id)

    def test_a_package_loads_into_a_fresh_agent(self) -> None:
        app = self.other.app
        report = app.lessons_package.load(self.stats["zip_path"])
        self.assertEqual(report["kinds"]["stage"]["added"], 3)
        self.assertIsNone(ExperienceStore(app.db, "work:dbg_0123:full").stage("stage_dbg"))
        self.assertIsNotNone(ExperienceStore(app.db, "work:work_abc").stage("stage_work"))
        self.assertIsNotNone(ExperienceStore(app.db).workspace("work_abc"))
        self.assertEqual({p["id"] for p in ExperienceStore(app.db).patterns()},
                         {"pattern_a", "principle_a"})
        self.assertEqual(len(ExperienceStore(app.db).edges()), 1)
        self.assertEqual(ExperienceStore(app.db).nature_kinds(), {self.nature_id: "work"})
        self.assertTrue(app.db.query("SELECT 1 FROM stage_terms WHERE stage_id = 'stage_one'"),
                        "a loaded stage is indexed here")
        imported = Path(app.library.root) / IMPORTED_FOLDER
        refs = {one["id"]: one for one in self._stage(app, "stage_one")["work"]["references"]}
        small = refs["r1"]
        self.assertEqual(small["originally_at"], str(self.small))
        self.assertTrue(small["location"].startswith(str(imported)))
        self.assertEqual(Path(small["location"]).read_text(encoding="utf-8"),
                         self.small.read_text(encoding="utf-8"))
        self.assertTrue(small["copies"]["text"].startswith(str(imported)))
        self.assertEqual(small["access"], "local")
        compressed = refs["r2"]
        self.assertEqual(Path(compressed["location"]).stat().st_size,
                         self.compressible.stat().st_size)
        summarised = refs["r3"]
        self.assertTrue(summarised["summarised"])
        self.assertIn("SUMMARY:", summarised["summary"])
        self.assertIn("SUMMARY:", Path(summarised["copies"]["text"]).read_text(encoding="utf-8"))
        self.assertEqual(summarised["originally_at"], str(self.incompressible))
        self.assertTrue(refs["r4"]["missing_when_packaged"])
        self.assertEqual(refs["r4"]["location"], str(self.missing))
        self.assertEqual(refs["r5"]["location"], self.url)
        card = ExperienceStore(app.db).card("card_root")
        self.assertTrue(card["refs"][0]["location"].startswith(str(imported)))
        # The nature, its example pointing at its summary here.
        nature = app.library.get(self.nature_id, USER_NATURE)
        self.assertIsNotNone(nature)
        example = json.loads(nature.metadata["examples"])[0]["references"][0]
        self.assertEqual(example["originally_at"], str(self.incompressible))
        self.assertIn("SUMMARY:", example["summary"])
        self.assertIn(f"(originally at {self.incompressible})", nature.body)
        # The notification lesson, in the running text here.
        self.assertIn("Do not remind more than twice a day", app.calendar_nature.text())
        self.assertEqual(report["calendar"]["lessons_added"], 1)
        self.assertEqual(app.lessons_package.describe()["last_load"]["package_id"],
                         report["package_id"])

    def test_loading_again_changes_nothing(self) -> None:
        app = self.other.app
        app.lessons_package.load(self.stats["zip_path"])
        before = app.db.query("SELECT id, updated_at, work FROM experience_stages ORDER BY id")
        files = _files(Path(app.library.root))
        text = app.calendar_nature.text()
        again = app.lessons_package.load(self.stats["zip_path"])
        self.assertEqual(again["totals"]["added"], 0)
        self.assertEqual(again["totals"]["updated"], 0)
        self.assertEqual(again["references"]["files_copied"], 0)
        self.assertEqual(again["calendar"]["lessons_added"], 0)
        self.assertEqual(before, app.db.query(
            "SELECT id, updated_at, work FROM experience_stages ORDER BY id"))
        self.assertEqual(files, _files(Path(app.library.root)))
        self.assertEqual(text, app.calendar_nature.text())

    def test_merged_with_lessons_already_here_newer_kept(self) -> None:
        app = self.other.app
        here = ExperienceStore(app.db)
        here.add_stage({"id": "stage_mine", "task_id": "task-z", "kind": "goal",
                        "summary": "my own lesson"})
        here.add_stage({"id": "stage_two", "task_id": "task-a", "kind": "plan",
                        "summary": "this computer's newer version"})
        app.db.execute("UPDATE experience_stages SET updated_at = '2999-01-01T00:00:00+00:00' "
                       "WHERE id = 'stage_two'")
        here.add_stage({"id": "stage_one", "task_id": "task-a", "kind": "evaluation",
                        "summary": "an older version"})
        app.db.execute("UPDATE experience_stages SET updated_at = '2000-01-01T00:00:00+00:00' "
                       "WHERE id = 'stage_one'")
        app.db.execute("INSERT INTO calendar_actions (id, acted_at, action, task_id, event, "
                       "reasoned_at, why, lesson) VALUES ('calact_mine', "
                       "'2026-09-05T00:00:00+00:00', 'add', '', '{}', "
                       "'2026-09-05T00:01:00+00:00', 'wanted', 'Remind me of lunch')")
        # Loaded from the package's folder this time, not its zip.
        report = app.lessons_package.load(self.app.lessons_package.folder)
        self.assertEqual(here.stage("stage_mine")["summary"], "my own lesson")
        self.assertEqual(here.stage("stage_two")["summary"],
                         "this computer's newer version")
        self.assertEqual(here.stage("stage_one")["summary"],
                         "The user checked the measurements against the paper's table")
        self.assertEqual(report["kinds"]["stage"]["updated"], 1)
        self.assertEqual(report["kinds"]["stage"]["skipped"], 1)
        self.assertEqual({row["id"] for row in app.db.query("SELECT id FROM calendar_actions")},
                         {"calact_one", "calact_mine"})

    def test_through_the_routes(self) -> None:
        from personalized_agent.api.routes import build_routes
        from personalized_agent.api.server import Request
        routes = build_routes(self.other.app)

        def call(method, path, body=None):
            return routes[(method, path)](Request(method, path, {}, body or {}, {}))

        state = call("GET", "/api/lessons/package")
        self.assertIn("history", state)
        self.assertFalse(call("POST", "/api/lessons/load")["started"],
                         "no dialog under the tests: nothing chosen")
        started = call("POST", "/api/lessons/load", {"path": self.stats["zip_path"]})
        self.assertTrue(started["started"])
        self.other.app.lessons_package.wait(300)
        report = call("GET", "/api/lessons/package")["last_load"]
        self.assertEqual(report["kinds"]["stage"]["added"], 3)


def _javascript_engine():
    from test_panels import _javascript_engine as find
    return find()


class LessonsPackageOverviewPanelTests(unittest.TestCase):
    """The Overview's Lessons package card, drawn in a real browser from
    stubbed answers."""

    STATE = {
        "folder": "C:/root/workspace/lesson_package", "exists": True,
        "running": False, "progress": {}, "last_error": "",
        "last": {"stamp": "20261010-120000-000", "finished_at": "2026-10-10T12:00:05+00:00",
                 "seconds": 4.2, "seconds_total": 5.5, "zip_seconds": 1.3,
                 "calls": 2, "tokens_in": 210, "tokens_in_cached": 10, "tokens_out": 40,
                 "items": {"stage": {"total": 3, "new": 1, "updated": 1, "unchanged": 1}},
                 "references": {"references": 7, "files": 8, "copied": 2, "compressed": 1,
                                "summarised": 2, "reused": 0, "missing": 1, "excluded": 2,
                                "urls": 1},
                 "bytes_added": 2048, "package_bytes": 4096,
                 "zip": "lessons_20261010-120000-000.zip", "zip_bytes": 3072},
        "history": [{"finished_at": "2026-10-10T12:00:05+00:00", "seconds_total": 5.5,
                     "calls": 2, "tokens_in": 210, "tokens_out": 40,
                     "bytes_added": 2048, "zip_bytes": 3072}],
        "zips": [], "manifest": "manifests/manifest_x.json", "package_id": "pkg_1",
        "last_load": {"path": "D:/carried/lessons.zip", "package_id": "pkg_other",
                      "machine": "LAPTOP", "finished_at": "2026-10-10T13:00:00+00:00",
                      "totals": {"added": 5, "updated": 1, "skipped": 2},
                      "kinds": {"stage": {"added": 3, "updated": 1, "skipped": 0}},
                      "references": {"files_copied": 4, "summaries": 1},
                      "calendar": {"lessons_added": 1}, "conflicts": []},
    }

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, state: dict, steps: str = "") -> dict:
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        script = (web / "app.js").read_text(encoding="utf-8")
        stub = {"/api/lessons/package": state}
        harness = (
            "<script>window.__posted=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posted.push(p);"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "headers:{get:()=>null},"
            "json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + script + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';"
            "const wait=()=>new Promise(r=>setTimeout(r,60));"
            "try{await loadOverview();await wait();" + steps + "}catch(e){err=e.message;}"
            "const t=(s)=>(document.querySelector(s)||{}).textContent||'';"
            "document.title=JSON.stringify({error:err,"
            "last:t('#ov-lp-last').replace(/\\s+/g,' '),"
            "report:t('#ov-lp-report').replace(/\\s+/g,' '),"
            "history:t('#ov-lp-history').replace(/\\s+/g,' '),"
            "progress:t('#ov-lp-progress'),"
            "packageDisabled:document.querySelector('#ov-lp-package').disabled,"
            "loadDisabled:document.querySelector('#ov-lp-load').disabled,"
            "cancelHidden:document.querySelector('#ov-lp-cancel').hidden,"
            "buttons:[...document.querySelectorAll('#ov-lessons-card button')]"
            ".map(b=>b.textContent.trim()),posted:window.__posted});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=5000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=300)
            dom = completed.stdout or ""
        match = re.search(r"<title>(.*?)</title>", dom, re.S)
        self.assertIsNotNone(match, "the page never finished rendering")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["error"], "")
        return result

    def test_the_card_shows_the_last_packaging_and_the_last_load(self) -> None:
        result = self._run(self.STATE)
        self.assertIn("Package all lessons", result["buttons"])
        self.assertIn("Load lessons", result["buttons"])
        self.assertIn("Open package folder", result["buttons"])
        self.assertIn("2 model calls", result["last"])
        self.assertIn("tokens in 210", result["last"])
        self.assertIn("out 40", result["last"])
        self.assertIn("2 summarised", result["last"])
        self.assertIn("lessons_20261010-120000-000.zip", result["last"])
        self.assertIn("5.5 s", result["last"])
        self.assertIn("5 added, 1 updated, 2 skipped", result["report"])
        self.assertIn("LAPTOP", result["report"])
        self.assertIn("Packagings so far (1)", result["history"])
        self.assertFalse(result["packageDisabled"])
        self.assertTrue(result["cancelHidden"])

    def test_while_packaging_the_buttons_wait_and_progress_shows(self) -> None:
        state = dict(self.STATE, running=True,
                     progress={"kind": "packaging", "phase": "the references",
                               "done": 3, "total": 7})
        result = self._run(state)
        self.assertTrue(result["packageDisabled"])
        self.assertTrue(result["loadDisabled"])
        self.assertFalse(result["cancelHidden"])
        self.assertIn("Packaging: the references (3 of 7)", result["progress"])

    def test_the_button_starts_a_packaging(self) -> None:
        result = self._run(self.STATE, "document.querySelector('#ov-lp-package').click();"
                                       "await wait();")
        self.assertIn("/api/lessons/package", result["posted"])


class LessonCardsAreCarriedTests(unittest.TestCase):
    """The lesson cards go with the rest of the lessons - every version - and
    none resting on web content is ever loaded, whatever a package says."""

    def setUp(self) -> None:
        self.here, self.there = TemporaryAgent(), TemporaryAgent()

    def tearDown(self) -> None:
        self.here.cleanup()
        self.there.cleanup()

    def test_packaged_and_loaded_never_one_resting_on_the_web(self) -> None:
        from personalized_agent.heartbeat.experience.lesson_cards import LessonCards
        cards = LessonCards(self.here.app.db)
        first = cards.write("Which charting library does the user use?", "Altair.",
                            [{"type": "user_code", "ref": "plot.py"}])["card"]
        second = cards.supersede(first["id"], answer="Altair, one theme.")["card"]
        # One written past the rules, as a package from elsewhere might hold.
        self.here.app.db.execute(
            "INSERT INTO lesson_cards (id, scope, lineage, question, answer, evidence, tier, "
            "created_at, updated_at) VALUES ('lc_web', 'live', 'lc_web', 'Which is best?', "
            "'Whatever the page says.', '[{\"type\": \"web\"}]', 'user', '2026-10-18', "
            "'2026-10-18')")
        stats = self.here.app.lessons_package.package()
        report = self.there.app.lessons_package.load(stats["zip_path"])
        there = LessonCards(self.there.app.db)
        self.assertEqual(sorted(one["version"] for one in there.versions(second["id"])), [1, 2])
        self.assertIsNone(there.get("lc_web"))
        self.assertEqual(report["kinds"]["lesson_card"]["added"], 2)
        self.assertTrue(any("lc_web" in one and "web" in one for one in report["conflicts"]))


if __name__ == "__main__":
    unittest.main()

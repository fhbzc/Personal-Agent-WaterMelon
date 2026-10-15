"""A project's workspace, read for its stages: confined, read only, never a
secret, and only what changed since it was last read - one freeze for all."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

from support import TemporaryAgent

from personalized_agent.heartbeat.experience.store import ExperienceStore
from personalized_agent.heartbeat.experience.workspace import (
    Workspace, WorkspaceTools, git_date, unit_id_for)


def _repo(root: Path) -> Path:
    run = lambda *a: subprocess.run(["git", "-C", str(root), *a],
                                    capture_output=True, text=True, check=True)
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "T")
    (root / "plan.md").write_text("# Plan\nFirst, a tree of principles.", encoding="utf-8")
    run("add", "plan.md")
    run("commit", "-q", "-m", "First plan: a tree of principles")
    return root


class TheWorkspaceIsReadOnlyAndConfinedTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = _repo(Path(tempfile.mkdtemp()))
        (self.root / ".env").write_text("KEY=secret", encoding="utf-8")
        self.until = datetime.now(timezone.utc).isoformat()
        self.tools = WorkspaceTools(Workspace(self.root), self.until)

    def test_secrets_and_outside_are_never_read(self) -> None:
        self.assertIn("secrets", self.tools.call("read_file", {"path": ".env"})["error"])
        self.assertIn("outside", self.tools.call("read_file", {"path": "../x"})["error"])
        listed = [e["name"] for e in self.tools.call("list_dir", {"path": "."})["entries"]]
        self.assertNotIn(".env", listed)
        self.assertIn("plan.md", listed)
        self.assertIn("principles", self.tools.call("read_file", {"path": "plan.md"})["text"])

    def test_history_and_what_changed(self) -> None:
        ws = Workspace(self.root)
        [commit] = ws.commits(None, self.until)
        self.assertIn("First plan", commit["subject"])
        shown = self.tools.call("git_show", {"commit": commit["commit"]})
        self.assertIn("a tree of principles", shown["text"])
        self.assertEqual(git_date("2026-10-05"), "2026-10-05 00:00:00")
        old = self.root / "plan.md"
        long_ago = time.time() - 86400
        os.utime(old, (long_ago, long_ago))
        (self.root / "new.md").write_text("new", encoding="utf-8")
        # Windows stamps a file from a coarser clock than Python reads, a
        # few milliseconds ahead: one written at a cutoff is the next
        # pass's, never lost.
        time.sleep(0.1)
        since = datetime.fromtimestamp(time.time() - 3600, timezone.utc).isoformat()
        until = datetime.now(timezone.utc).isoformat()
        changed = [one["path"] for one in ws.files_changed(since, until)]
        self.assertEqual(changed, ["new.md"])
        self.assertEqual(sorted(one["path"] for one in ws.files_changed(None, until)),
                         ["new.md", "plan.md"])

    def test_the_folders_conversations_are_the_users_words(self) -> None:
        home = Path(tempfile.mkdtemp())
        import re
        folder = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(self.root))
        folder.mkdir(parents=True)
        rows = [{"timestamp": "2026-10-01T10:00:00Z", "type": "user",
                 "message": {"role": "user", "content": "两层就够了"}},
                {"timestamp": "2026-10-01T10:01:00Z", "type": "user",
                 "message": {"role": "user",
                             "content": "Another Claude session sent a message: x"}},
                {"timestamp": "2026-10-01T10:02:00Z", "type": "assistant",
                 "message": {"role": "assistant",
                             "content": [{"type": "text", "text": "Two layers it is."}]}}]
        (folder / "abcdef12-0000.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        with unittest.mock.patch.object(Path, "home", return_value=home):
            said = Workspace(self.root).said_in_conversations(None, "2026-10-02")
        self.assertEqual([(s["role"], s["text"]) for s in said],
                         [("user", "两层就够了"), ("assistant", "Two layers it is.")])


class _Model:
    """Reads one file, then answers with dated stages and the project."""

    def __init__(self):
        self.asked = []

    def invoke(self, request, role=""):
        self.asked.append(request)
        out = type("R", (), {})()
        out.model, out.provider, out.usage, out.raw_content = "m", "p", None, ""
        if len(self.asked) == 1:
            call = type("C", (), {"id": "c1", "name": "read_file",
                                  "arguments": {"path": "plan.md"}})()
            out.text, out.tool_calls = "", [call]
            return out
        out.tool_calls = []
        out.text = json.dumps({
            "new_stages": [{"ref": "n1", "kind": "plan", "medium": "document",
                            "card": "root", "summary": "Planned the tree.",
                            "goal": "a tree of principles", "steps": ["the user wrote the plan"],
                            "began": "2026-09-01T10:00:00Z", "ended": "2026-09-01T11:00:00Z",
                            "states": [],
                            "references": [{"location": "plan.md", "what": "the plan"}]}],
            "project": {"title": "The tree", "goal": "learn how the user works",
                        "status": "planned"}})
        return out


class AFolderIsAProjectTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.xp = self.app.state_heartbeat.experience
        self.root = _repo(Path(tempfile.mkdtemp()))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_added_read_from_the_beginning_then_only_what_changed(self) -> None:
        held = self.xp.add_directory(str(self.root))
        unit_id = unit_id_for(self.root)
        self.assertEqual(held["unit_id"], unit_id)
        [unit] = self.xp.directory_units()
        writer = self.xp.stages_writer()
        writer.models = model = _Model()
        said = self.xp.workspace_pass(writer, unit)
        # The first reading: the whole history, and the folder's own words.
        first = model.asked[0].messages[0]["content"]
        self.assertIn("THE FIRST READING OF THIS FOLDER", first)
        self.assertIn("First plan: a tree of principles", first)
        self.assertEqual(said["stages"], 1)
        # What it was given, whole: the message, the round of reading with
        # what came back, and the answer.
        [prompt] = ExperienceStore(self.app.db).prompts(unit_id)
        self.assertEqual(prompt["kind"], "workspace")
        self.assertIn("WORKSPACE", prompt["system"])
        self.assertIn("THE FIRST READING OF THIS FOLDER", prompt["messages"][0]["content"])
        self.assertEqual(prompt["messages"][1]["tool_calls"][0]["name"], "read_file")
        self.assertIn("a tree of principles", prompt["messages"][2]["content"][0]["content"])
        self.assertIn("Planned the tree.", prompt["answer"])
        [stage] = ExperienceStore(self.app.db).stages([unit_id])
        self.assertTrue(stage["began"].startswith("2026-09-01"))
        self.assertEqual(Path(stage["work"]["references"][0]["location"]),
                         (self.root / "plan.md").resolve())
        record = ExperienceStore(self.app.db).workspace(unit_id)
        self.assertTrue(record["processed_until"])
        self.assertEqual(record["summary"]["title"], "The tree")
        self.assertEqual(self.xp.directory_units()[0]["name"], "The tree")
        # Read to the cutoff: nothing more until the cutoff moves.
        self.assertIsNone(self.xp.workspace_pass(writer, self.xp.directory_units()[0]))
        # The next pass reads only what changed after it.
        self.app.config.set("experience_stages_pass", {}, internal=True)
        time.sleep(0.05)
        (self.root / "draft.md").write_text("a draft", encoding="utf-8")
        time.sleep(0.1)
        writer.models = model = _Model()
        self.xp.workspace_pass(writer, self.xp.directory_units()[0])
        later = model.asked[0].messages[0]["content"]
        self.assertNotIn("THE FIRST READING", later)
        self.assertIn("draft.md", later)
        self.assertNotIn("plan.md\n", later.split("FILES CHANGED SINCE")[1])

    def test_a_reading_cut_short_keeps_the_heartbeats_cutoff(self) -> None:
        store = ExperienceStore(self.app.db)
        store.set_workspace("task-x", str(self.root), "detected", "X")
        unit = {"id": "task-x", "kind": "task", "name": "X", "tasks": []}

        class _Writer:
            def units(self, live, projects):
                return [unit]

            def pending(self, one):
                return []

        cutoff = self.xp._stage_pass(open_one=True)["until"]
        # Its states are in stages, its workspace read only part of the way.
        store.set_processed("task-x", "2026-09-02T00:00:00+00:00")
        self.assertFalse(self.xp._close_stage_pass_if_done(_Writer(), [], []))
        self.assertEqual(self.xp._stage_pass()["until"], cutoff, "the same cutoff")
        self.assertEqual(self.xp.standing().get("pass_until"), cutoff)
        # Read to the cutoff: the pass closes.
        store.set_processed("task-x", cutoff)
        self.assertTrue(self.xp._close_stage_pass_if_done(_Writer(), [], []))
        # Closed, it keeps its cutoff and says it is done.
        self.assertEqual(self.xp._stage_pass(), {"until": cutoff, "done": True})

    def test_listed_with_the_projects(self) -> None:
        self.xp.add_directory(str(self.root))
        kinds = [one["kind"] for one in self.xp.units()]
        self.assertIn("directory", kinds)
        with self.assertRaises(ValueError):
            self.xp.add_directory(str(self.root / "not-there"))


class SeveralFoldersAreOneProjectTests(unittest.TestCase):

    def test_read_as_one(self) -> None:
        from personalized_agent.heartbeat.experience.workspace import open_workspace
        one = _repo(Path(tempfile.mkdtemp()))
        two = Path(tempfile.mkdtemp())
        (two / "notes.md").write_text("notes", encoding="utf-8")
        ws = open_workspace([one, two])
        until = datetime.now(timezone.utc).isoformat()
        tools = WorkspaceTools(ws, until)
        self.assertEqual(len(tools.call("list_dir", {"path": "."})["folders"]), 2)
        read = tools.call("read_file", {"path": f"{two.name}/notes.md"})
        self.assertEqual(read["text"], "notes")
        self.assertIn("principles", tools.call("read_file", {"path": "plan.md"})["text"])
        self.assertIn("outside", tools.call("read_file", {"path": "C:/Windows/win.ini"})["error"])
        self.assertTrue(ws.is_repository())
        self.assertEqual(len(ws.commits(None, until)), 1)


class _PartsModel(_Model):
    """Reads nothing; answers the first time with part of the history."""

    def invoke(self, request, role=""):
        self.asked.append(request)
        out = type("R", (), {})()
        out.model, out.provider, out.usage, out.raw_content = "m", "p", None, ""
        out.tool_calls = []
        if "experience stage natures" in str(getattr(request, "metadata", {})):
            ids = __import__("re").findall(r"\[(stage_[0-9a-f]+)\]",
                                           request.messages[0]["content"])
            out.text = json.dumps({"natures": [{
                "stages": ids[:1], "kind": "work",
                "title": "The user plans a tree before building it.",
                "content": "c", "confidence": 4, "evidence": ["the plan"],
                # A case needs the user's own words (or a place to open).
                "example": {"said": "plan the tree first", "what": "the plan"}}]})
            return out
        first = sum(1 for one in self.asked
                    if "experience stage natures" not in str(one.metadata)) == 1
        out.text = json.dumps({
            "new_stages": [{"ref": "n1", "kind": "plan", "card": "root",
                            "summary": "part", "states": [],
                            "began": "2026-09-01T10:00:00Z",
                            "ended": "2026-09-01T11:00:00Z",
                            # What the later part shows, written with it.
                            "natures": [] if first else [{
                                "aspect": "evaluation",
                                "title": "The user checks a tree by building it.",
                                "content": "c", "confidence": 4,
                                "example": {"said": "build it and run it",
                                            "what": "the tree, built and run"}}]}],
            **({"covered_until": "2026-09-02T00:00:00+00:00"} if first else {}),
            "project": {"title": "T", "goal": "g", "status": "s"}})
        return out


class _LongAnswerModel(_PartsModel):
    """Its first answer from the folder runs past the length limit; asked
    for fewer stages, it answers - unless `always`."""

    def __init__(self, always=False):
        super().__init__()
        self.always = always

    def invoke(self, request, role=""):
        if "experience stage natures" in str(getattr(request, "metadata", {})):
            return super().invoke(request, role)
        last = request.messages[-1]["content"]
        retried = "AT MOST SIX stages" in (last if isinstance(last, str) else json.dumps(last))
        if self.always or not retried:
            self.asked.append(request)
            out = type("R", (), {})()
            out.model, out.provider, out.usage, out.raw_content = "m", "p", None, ""
            out.tool_calls, out.stop_reason = [], "max_tokens"
            out.text = '{"new_stages": [{"ref": "n1", "kind": "pl'
            return out
        return super().invoke(request, role)


class AFolderAnswerCutOffTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.xp = self.app.state_heartbeat.experience
        self.root = _repo(Path(tempfile.mkdtemp()))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _process(self, unit_id):
        self.assertTrue(self.xp.process_project(unit_id))
        deadline = time.time() + 30
        while self.xp.project_progress[unit_id].get("running") and time.time() < deadline:
            time.sleep(0.05)
        return self.xp.project_progress[unit_id]

    def test_it_is_asked_once_more_for_fewer_stages(self) -> None:
        unit = self.xp.create_project("Long history", [str(self.root)])["unit_id"]
        self.xp.models = model = _LongAnswerModel()
        progress = self._process(unit)
        self.assertNotIn("error", progress)
        self.assertTrue(ExperienceStore(self.app.db).stages([unit]))
        self.assertTrue(any("AT MOST SIX stages" in json.dumps(one.messages[-1]["content"])
                            for one in model.asked))

    def test_cut_off_again_leaves_the_folder_unread_and_says_so(self) -> None:
        unit = self.xp.create_project("Too long", [str(self.root)])["unit_id"]
        self.xp.models = _LongAnswerModel(always=True)
        progress = self._process(unit)
        self.assertIn("cut off", progress.get("error", ""))
        store = ExperienceStore(self.app.db)
        self.assertEqual(store.stages([unit]), [])
        self.assertEqual(store.workspace(unit)["processed_until"], "")


class AProjectTheUserMadeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.xp = self.app.state_heartbeat.experience
        self.root = _repo(Path(tempfile.mkdtemp()))

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _process(self, unit_id):
        self.assertTrue(self.xp.process_project(unit_id))
        deadline = time.time() + 30
        while self.xp.project_progress[unit_id].get("running") and time.time() < deadline:
            time.sleep(0.05)
        return self.xp.project_progress[unit_id]

    def test_process_reads_in_parts_then_writes_natures(self) -> None:
        held = self.xp.create_project("My old project", [str(self.root)])
        unit = held["unit_id"]
        self.xp.models = model = _PartsModel()
        progress = self._process(unit)
        self.assertNotIn("error", progress)
        # Two passes: the first covered part of the history, the second the rest.
        stages = ExperienceStore(self.app.db).stages([unit])
        self.assertEqual(len(stages), 2)
        record = ExperienceStore(self.app.db).workspace(unit)
        self.assertEqual(record["processed_until"], progress["until"])
        self.assertEqual(record["name"], "My old project")
        # What the stages show, linked to them.
        self.assertEqual(progress["natures"], 1)
        linked = ExperienceStore(self.app.db).natures_of_stages([s["id"] for s in stages])
        self.assertEqual(sum(len(v) for v in linked.values()), 1)
        self.assertEqual(ExperienceStore(self.app.db).nature_kinds()[
            next(iter(linked.values()))[0]], "work")
        # Not read by a round: only when asked.
        before = len(model.asked)
        self.app.config.set("experience_stages_pass", {}, internal=True)
        # The patterns' own thread is not what this is about.
        with unittest.mock.patch.object(self.xp, "start_patterns", return_value=False):
            self.xp.after_round(budget=4)
        self.assertEqual(len(model.asked), before)

    def test_a_run_cut_short_is_resumed_under_its_own_cutoff(self) -> None:
        held = self.xp.create_project("Cut short", [str(self.root)])
        unit = held["unit_id"]
        store = ExperienceStore(self.app.db)

        class _CutModel(_PartsModel):
            """Fails the second reading, once."""
            failed = False

            def invoke(self, request, role=""):
                stage_calls = sum(1 for one in self.asked
                                  if "experience stage natures" not in str(one.metadata))
                if stage_calls == 1 and not _CutModel.failed                         and "experience stage natures" not in str(request.metadata):
                    _CutModel.failed = True
                    raise RuntimeError("the connection dropped")
                return super().invoke(request, role)

        self.xp.models = _CutModel()
        first = self._process(unit)
        self.assertTrue(first.get("unfinished"))
        record = store.workspace(unit)
        self.assertEqual(record["processing_until"], first["until"])
        self.assertEqual(record["processed_until"], "2026-09-02T00:00:00+00:00")
        self.assertEqual(len(store.stages([unit])), 1)

        # The agent quit and came back: nothing in memory, the cutoff kept.
        self.xp.project_progress.clear()
        time.sleep(1.1)
        self.assertTrue(self.xp.process_project(unit, resume=True))
        deadline = time.time() + 30
        while self.xp.project_progress[unit].get("running") and time.time() < deadline:
            time.sleep(0.05)
        second = self.xp.project_progress[unit]
        self.assertEqual(second["until"], first["until"], "the same freeze")
        self.assertTrue(second["resumed"])
        self.assertNotIn("error", second)
        record = store.workspace(unit)
        self.assertEqual(record["processing_until"], "", "finished: nothing to resume")
        self.assertEqual(record["processed_until"], first["until"])
        self.assertEqual(len(store.stages([unit])), 2)
        self.assertGreaterEqual(second["natures"], 1)

    def test_several_folders_and_a_change_reads_again(self) -> None:
        other = Path(tempfile.mkdtemp())
        held = self.xp.create_project("", [str(self.root), str(other)])
        store = ExperienceStore(self.app.db)
        self.assertEqual(len(store.workspace(held["unit_id"])["paths"]), 2)
        store.set_processed(held["unit_id"], "2026-10-01T00:00:00+00:00")
        store.set_paths(held["unit_id"], [str(self.root)])
        self.assertEqual(store.workspace(held["unit_id"])["processed_until"], "")
        with self.assertRaises(ValueError):
            self.xp.create_project("x", [str(self.root / "missing")])


class WhichFoldersAreAProjectsTests(unittest.TestCase):
    """Every local place the work touched, lifted to the root of its project,
    containers and folders other work also uses left out - git or not."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.xp = self.app.state_heartbeat.experience
        # Long form: the places the tasks keep are, and a folder in Windows's
        # short form is the same folder spelt differently.
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.research = self.home / "Research"
        self.package = self.research / "survey" / "replication"
        (self.package / "scripts").mkdir(parents=True)
        (self.package / ".claude").mkdir()
        (self.package / "scripts" / ".claude").mkdir()
        (self.research / ".claude").mkdir()
        (self.home / "work" / "shared_project").mkdir(parents=True)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _unit(self, unit_id, places):
        from personalized_agent.heartbeat.states.tasks import Task
        task = Task(id=unit_id, members=[], described={"title": unit_id})
        task.profile = {"weight": 1.0, "places": {
            str(path).replace("\\", "/").lower().replace("-", "_"): share
            for path, share in places.items()}}
        return {"id": unit_id, "kind": "task", "name": unit_id, "tasks": [task]}

    def test_the_package_not_its_container_nor_its_scripts(self) -> None:
        with unittest.mock.patch.object(Path, "home", return_value=self.home):
            unit = self._unit("swr", {self.research: 0.30,
                                      self.research / "survey": 0.20,
                                      self.package: 0.20,
                                      self.package / "scripts" / "run.py": 0.10,
                                      self.home / "Dropbox": 0.5})
            self.assertEqual([Path(one) for one in self.xp._detect_folders(unit)],
                             [self.package.resolve()])

    def test_a_folder_other_work_also_uses_is_not_this_works(self) -> None:
        with unittest.mock.patch.object(Path, "home", return_value=self.home):
            shared = self.home / "work" / "shared_project"
            mine = self._unit("mine", {shared: 0.6})
            others = [self._unit(f"o{n}", {shared: 0.4}) for n in range(3)]
            every = [mine] + others
            self.assertEqual(self.xp._detect_folders(mine, every), [],
                             "used by every other piece of work: generic")
            alone = [mine, self._unit("x", {self.home / "elsewhere": 0.9})]
            self.assertEqual([Path(one) for one in self.xp._detect_folders(mine, alone)],
                             [shared.resolve()])


class OldReadingIsCutTests(unittest.TestCase):

    def test_only_the_last_rounds_stay_whole(self) -> None:
        from personalized_agent.heartbeat.experience.stages import _shrink_old_results
        messages = [{"role": "user", "content": "the start"}]
        for n in range(6):
            messages.append({"role": "assistant", "content": "reading"})
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": str(n), "content": "x" * 5000}]})
        _shrink_old_results(messages, keep=4)
        sizes = [len(m["content"][0]["content"]) for m in messages
                 if isinstance(m.get("content"), list)]
        self.assertTrue(all(size < 1700 for size in sizes[:2]))
        self.assertTrue(all(size == 5000 for size in sizes[2:]))


class OnlyTheLastPromptsAreKeptTests(unittest.TestCase):

    def test_six(self) -> None:
        agent = TemporaryAgent()
        try:
            store = ExperienceStore(agent.app.db)
            for n in range(9):
                store.save_prompt("u", "states", "system", [{"role": "user",
                                                             "content": f"m{n}"}], "a")
            kept = store.prompts("u")
            self.assertEqual(len(kept), 6)
            self.assertEqual(kept[0]["messages"][0]["content"], "m8")
        finally:
            agent.cleanup()

"""The Experience debugger: one piece of work done three ways side by side -
one model on its own, builder and reviewer with no experience, and the whole
system - each in its own subfolder of the folder the user chose, sealed from
the other two, unlimited unless told otherwise, and never reaching the
user's learned experience."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from support import TemporaryAgent
from test_work_runs import _TwoAgents


class _Request:
    def __init__(self, body):
        self.body = body

    def get(self, key, default=None):
        return self.body.get(key, default)

    def require(self, key):
        from personalized_agent.api.errors import ApiError
        if self.body.get(key) in (None, ""):
            raise ApiError(f"missing parameter: {key}")
        return self.body[key]

    def int_param(self, key, default):
        return int(self.body.get(key, default))


def _until(condition, seconds=60.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


class _Models(_TwoAgents):
    """The two agents of a builder-reviewer run - and the single model: it
    writes alone.md with a tool, then says it is done, with its report. With
    `gate`, every run's first call waits until the test lets it go."""

    def __init__(self, gate=False, alone_turns=1, **kwargs):
        super().__init__(**kwargs)
        self.gate = threading.Event()
        if not gate:
            self.gate.set()
        self.waiting = 0
        self.alone_turns = alone_turns
        self.alone_answers = 0
        self.lock = threading.Lock()

    def invoke(self, request, role=""):
        with self.lock:
            self.waiting += 1
        self.gate.wait(60)
        with self.lock:
            self.waiting -= 1
        if "on your own" in (request.system or ""):
            from personalized_agent.models.base import ToolCall
            last = request.messages[-1]["content"]
            # The README of what was made, at the end: answered apart, not
            # one of the turns counted.
            if isinstance(last, str) and "WRITE THE README" in last:
                return self._reply(json.dumps({"readme": "# alone.md\nOpen it."}))
            with self.lock:
                self.asked.append(request)
            if isinstance(last, str) and not last.startswith("GO ON"):
                if self.alone_answers == 0:
                    return self._reply("", [ToolCall(id="a1", name="write_file", arguments={
                        "path": "alone.md", "content": "made alone"})])
            self.alone_answers += 1
            done = self.alone_answers >= self.alone_turns
            return self._reply(json.dumps({"done": done, "report": f"wrote alone.md ({self.alone_answers})",
                                           "next": "" if done else "look again"}))
        return super().invoke(request, role)


class _DebuggerBase(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.work.open_folder = lambda folder: None
        from personalized_agent.api.routes import build_routes
        self.routes = build_routes(self.app)
        # Inside the test's own readable folder: what a run's code could read
        # but for the seal.
        self.chosen = Path(self.agent.dir) / "bench"
        self.chosen.mkdir()

    def tearDown(self) -> None:
        for run in list(self.app.work._running.values()):
            run.stop_now = True
            run.release(True)
        models = self.app.work.models
        if hasattr(models, "gate"):
            models.gate.set()
        _until(lambda: not any(r.alive() for r in self.app.work._running.values()), 60)
        self.agent.cleanup()

    def _get(self, path, body=None):
        return self.routes[("GET", path)](_Request(body or {}))

    def _post(self, path, body):
        return self.routes[("POST", path)](_Request(body))

    def _project(self, **extra):
        said = self._post("/api/debugger/experience/projects", dict({
            "action": "create", "name": "Notes", "description": "a short note, plainly",
            "folder": str(self.chosen)}, **extra))
        return said["project"]

    def _start(self, project, variant, **extra):
        return self._post("/api/debugger/experience/start",
                          dict({"project": project["id"], "variant": variant,
                                "instruction": "write the notes"}, **extra))

    def _run_row(self, project, variant):
        runs = self._get("/api/debugger/experience/runs",
                         {"project": project["id"], "variant": variant})["runs"]
        return runs[0] if runs else None

    def _finished(self, project, variant, seconds=90):
        ok = _until(lambda: (self._run_row(project, variant) or {}).get("alive") is False
                    and self._run_row(project, variant)["status"] != "running", seconds)
        row = self._run_row(project, variant)
        self.assertTrue(ok, row and row["status"])
        return row

    def _live_rows(self):
        out = {}
        for table in ("experience_stages", "experience_cards", "experience_edges",
                      "experience_threads", "experience_marks", "experience_workspaces",
                      "experience_prompts", "experience_patterns"):
            out[table] = [dict(row) for row in self.app.db.query(
                f"SELECT * FROM {table} WHERE scope = 'live' ORDER BY rowid")]
        return out


class AProjectAndItsFoldersTests(_DebuggerBase):

    def test_three_subfolders_are_made_and_it_is_not_in_experience(self) -> None:
        project = self._project()
        self.assertTrue(project["id"].startswith("dbg_"))
        # In the project's first try: tries sit side by side in its folder.
        for name in ("A", "B", "C"):
            self.assertTrue((self.chosen / "try_1" / name).is_dir(), name)
        self.assertEqual(project["tries"], [1])
        self.assertEqual([one["variant"] for one in project["variants"]],
                         ["single", "noctx", "full"])
        listed = self._get("/api/debugger/experience/projects")["projects"]
        self.assertEqual([one["name"] for one in listed], ["Notes"])
        # Never one of the user's projects.
        units = self._get("/api/experience")["units"]
        self.assertFalse(any(str(one["id"]).startswith("dbg_") for one in units), units)
        self.assertFalse(any(one["name"] == "Notes" for one in units))
        changed = self._post("/api/debugger/experience/projects", {
            "action": "update", "id": project["id"], "instruction": "write it shorter"})
        self.assertEqual(changed["project"]["instruction"], "write it shorter")
        # Removed: its records, not its files.
        (self.chosen / "try_1" / "C" / "kept.txt").write_text("mine")
        self._post("/api/debugger/experience/projects", {"action": "remove", "id": project["id"]})
        self.assertEqual(self._get("/api/debugger/experience/projects")["projects"], [])
        self.assertTrue((self.chosen / "try_1" / "C" / "kept.txt").is_file())

    def test_some_folders_are_refused(self) -> None:
        from personalized_agent.api.errors import ApiError
        for folder in (str(Path.home()), Path.home().anchor,
                       str(self.app.policy.protected_paths[0]),
                       str(self.chosen / "not-there")):
            with self.assertRaises(ApiError, msg=folder):
                self._project(folder=folder)
        self._project()
        with self.assertRaises(ApiError):         # another project's folder
            self._project(folder=str(self.chosen / "try_1" / "A"))


class TheInstructionIsGivenAtStartTests(_DebuggerBase):
    """A project is made without an instruction; each way is given one as it
    starts, and the next is offered the words the last began with."""

    def test_made_without_one_started_with_one(self) -> None:
        from personalized_agent.api.errors import ApiError
        project = self._project()
        self.assertEqual(project["instruction"], "")
        with self.assertRaises(ApiError):
            self._start(project, "single", instruction="   ")
        self.app.work.models = _Models()
        self._start(project, "single", instruction="write the short notes")
        shown = self._get("/api/debugger/experience/projects")["projects"][0]
        self.assertEqual(shown["instruction"], "write the short notes")
        self._finished(project, "single")
        # The next way, given nothing: the same words as the last.
        self._start(project, "noctx", instruction="")
        row = self._run_row(project, "noctx")
        self.assertEqual(row["instruction"], "write the short notes")


class EachWayWritesAndReadsOnlyItsOwnTests(_DebuggerBase):

    def _tools(self, project, variant):
        from personalized_agent.agent.work.debug_projects import DebugProjects
        from personalized_agent.agent.work.tools import WorkTools
        unit = DebugProjects(self.app.work).unit(project["id"], variant)
        runtime = self.app.work._runtime(unit, self.app.work.folder_of(unit))
        return unit, runtime, WorkTools(runtime)

    def test_its_policy_and_its_tools(self) -> None:
        project = self._project()
        folders = {"single": self.chosen / "try_1" / "A",
                   "noctx": self.chosen / "try_1" / "B", "full": self.chosen / "try_1" / "C"}
        for name, folder in folders.items():
            (folder / "secret.txt").write_text(f"made by {name}")
        (self.chosen / "shared.txt").write_text("the user's")
        for variant, own in folders.items():
            unit, runtime, tools = self._tools(project, variant)
            others = [one for key, one in folders.items() if key != variant]
            policy = runtime.policy
            # Writable: its own subfolder, and nothing else at all.
            self.assertEqual(Path(policy.write_root).resolve(), own.resolve())
            self.assertEqual(list(policy.extra_writable), [])
            # The other two ways sealed - among what else a way may not read
            # (test_debugger_reads) - and never its own.
            sealed = {str(p) for p in policy.sealed_paths}
            self.assertTrue({str(p.resolve()) for p in others} <= sealed, sealed)
            self.assertNotIn(str(own.resolve()), sealed)
            said = tools.call("write_file", {"path": "mine.md", "content": "ok"})
            self.assertNotIn("error", said, said)
            self.assertTrue((own / "mine.md").is_file())
            for target in (self.chosen / "parent.md", *[one / "x.md" for one in others]):
                said = tools.call("write_file", {"path": str(target), "content": "no"})
                self.assertEqual(said.get("error"), "security_violation", (variant, target, said))
                self.assertFalse(target.exists())
            # Read: anything but the other two ways' folders.
            self.assertEqual(tools.call("read_file", {"path": str(self.chosen / "shared.txt")})
                             ["text"], "the user's")
            for other in others:
                said = tools.call("read_file", {"path": str(other / "secret.txt")})
                self.assertEqual(said.get("error"), "security_violation", said)
                self.assertIn("closed to this work", said["message"])
                said = tools.call("list_dir", {"path": str(other)})
                self.assertEqual(said.get("error"), "security_violation", said)
                said = tools.call("find_files", {"root": str(other), "pattern": "*"})
                self.assertEqual(said.get("error"), "security_violation", said)
            # The try's folder is listed - without the other ways in it.
            listed = [one["name"] for one in tools.call("list_dir", {"path": str(own.parent)})
                      ["entries"]]
            self.assertIn(own.name, listed)
            self.assertIn("shared.txt", [one["name"] for one in tools.call(
                "list_dir", {"path": str(self.chosen)})["entries"]])
            for other in others:
                self.assertNotIn(other.name, listed)
            found = tools.call("find_files", {"root": str(self.chosen), "pattern": "**/*.txt"})
            self.assertFalse(any("made by" in Path(one).read_text() and
                                 Path(one).parent != own for one in found["files"]), found)
            # Code it runs cannot open them either.
            said = tools.call("run_python", {"code": (
                "import os\n"
                f"for path in [r'{others[0] / 'secret.txt'}', r'{others[1] / 'secret.txt'}']:\n"
                "    try:\n"
                "        print('READ', open(path).read())\n"
                "    except Exception as exc:\n"
                "        print('refused:', type(exc).__name__)\n"
                f"print('MINE', open(r'{own / 'secret.txt'}').read())\n")})
            out = str(said.get("stdout"))
            self.assertNotIn("READ", out, said)
            self.assertIn(f"MINE made by {variant}", out, said)

    def test_the_operating_system_keeps_them_apart_too(self) -> None:
        """Past the guard - `_io.FileIO` is not wrapped - the AppContainer
        alone: the other ways' folders are sealed from this child, while the
        parent folder, under the same readable root, is open to it."""
        from personalized_agent.runtime.isolation import isolation_status
        if isolation_status().get("backend") != "appcontainer":
            self.skipTest("no AppContainer isolation here")
        project = self._project()
        (self.chosen / "try_1" / "B" / "secret.txt").write_text("theirs")
        (self.chosen / "shared.txt").write_text("the user's")
        _unit, _runtime, tools = self._tools(project, "single")
        said = tools.call("run_python", {"code": (
            "import _io\n"
            f"for path in [r'{self.chosen / 'shared.txt'}', "
            f"r'{self.chosen / 'try_1' / 'B' / 'secret.txt'}']:\n"
            "    try:\n"
            "        print('READ', _io.FileIO(path).read().decode())\n"
            "    except Exception as exc:\n"
            "        print('refused:', type(exc).__name__)\n")})
        out = str(said.get("stdout"))
        self.assertIn("READ the user's", out, said)
        self.assertNotIn("READ theirs", out, said)
        self.assertIn("refused: PermissionError", out, said)


class TheSingleModelTests(_DebuggerBase):

    def test_it_works_alone_until_it_says_it_is_done(self) -> None:
        models = _Models(alone_turns=2)
        self.app.work.models = models
        project = self._project()
        started = self._start(project, "single")
        # Unlimited unless told otherwise.
        self.assertEqual(started["limits"], {"minutes": 0, "tokens_in": 0, "tokens_out": 0,
                                             "calls": 0})
        run = self._finished(project, "single")
        self.assertEqual(run["status"], "done", run["log"][-6:])
        # Its model calls, for the chart: every one the single model's.
        self.assertEqual({one["who"] for one in run["calls"]}, {"single"})
        self.assertEqual(len(run["calls"]), run["plan"]["total"]["calls"])
        folder = self.chosen / "try_1" / "A"
        self.assertEqual((folder / "alone.md").read_text(), "made alone")
        # No reviewer, no contract, no cards, no stages.
        self.assertEqual(models.reviewed_rounds, 0)
        self.assertFalse(any("You review a work agent" in r.system for r in models.asked))
        self.assertFalse(run["plan"].get("contract"))
        self.assertIsNone(run["contract_in_force"])
        scope = f"work:{project['id']}:single"
        for table in ("experience_stages", "experience_cards"):
            self.assertEqual(self.app.db.query(
                f"SELECT COUNT(*) AS n FROM {table} WHERE scope = ?", (scope,))[0]["n"], 0)
        # The same tools as the work agent, but no cards or stages to open.
        tools = [s.name for s in models.asked[0].tools or []]
        for name in ("read_file", "write_file", "run_python", "web_search", "fetch_url"):
            self.assertIn(name, tools)
        self.assertNotIn("open_card", tools)
        self.assertNotIn("search_stages", tools)
        # What the user gave: the instruction and the description.
        first = models.asked[0].messages[0]["content"]
        self.assertIn("write the notes", first)
        self.assertIn("a short note, plainly", first)
        self.assertNotIn("Single model", first)
        # Its report is the run's final word; a version after each turn.
        self.assertEqual(run["final"]["answer"], "wrote alone.md (2)")
        self.assertEqual([one["progress"] for one in run["plan"]["rounds"]],
                         ["went on", "done"])
        self.assertGreaterEqual(run["versions"]["count"], 2)
        self.assertTrue(run["extendable"])
        # Continued with words: they join the task, as said.
        models.alone_answers = 5
        said = self._post("/api/debugger/experience/continue", {
            "project": project["id"], "variant": "single", "run": run["id"],
            "words": "add a title"})
        self.assertNotIn("error", said)
        run = self._finished(project, "single")
        self.assertEqual(run["status"], "done")
        self.assertIn("add a title", json.dumps(models.asked[-1].messages))


class BuilderAndReviewerTests(_DebuggerBase):

    def setUp(self) -> None:
        super().setUp()
        from personalized_agent.heartbeat.experience.store import ExperienceStore
        self.past = ExperienceStore(self.app.db).add_stage({
            "task_id": "task-past", "kind": "plan",
            "summary": "planned how to write the notes, ZEBRAQUOKKA style", "states": [],
            "began": "2026-01-01T00:00:00", "ended": "2026-01-01T00:00:00"})
        # The index of the user's stages is made once, by whoever searches
        # first: made now, so what follows is compared with it in place.
        from personalized_agent.heartbeat.experience.retrieve import Retriever
        Retriever(self.app.db, self.app.library).find({"text": "notes"})

    def _reviewer(self, models):
        return [r for r in models.asked if "You review a work agent" in r.system]

    def test_no_context_the_reviewer_gets_none_of_the_users_experience(self) -> None:
        models = _Models()
        self.app.work.models = models
        project = self._project()
        before = self._live_rows()
        self._start(project, "noctx")
        run = self._finished(project, "noctx")
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertTrue(run["plan"]["contract"]["goal"])
        reviewer = self._reviewer(models)
        self.assertTrue(reviewer)
        for request in reviewer:
            said = request.system + json.dumps(request.messages, default=str)
            for words in ("ZEBRAQUOKKA", self.past["id"], "HOW THE USER HAS WORKED",
                          "past stages", "natures", "patterns and principles",
                          "past work supports", "the way the user likes",
                          "check|experience|own judgement"):
                self.assertNotIn(words, said)
            names = [s.name for s in request.tools or []]
            # No search of the user's own record; the web, as the full way's
            # - but for what it must know before it writes criteria, asked
            # with no web, as the full way's is.
            for local in ("search_stages", "search_records", "open_stage"):
                self.assertNotIn(local, names)
            if "web_search" not in names:
                self.assertIn("BEFORE YOU WRITE THE CRITERIA OF",
                              json.dumps(request.messages, default=str))
            # THE SAME WORK AS THE FULL WAY'S, only without the context
            # (2026-10-18): what was asked comes first, nothing added to it.
            self.assertIn("WHAT THEY ASKED COMES FIRST: the core promises", request.system)
            self.assertIn("never add\n    work they did not ask for", request.system)
        # The web, as the full way's reviewer has it.
        self.assertTrue(any("web_search" in [s.name for s in r.tools or []] for r in reviewer))
        self.assertTrue(any("NO RECORD OF THE USER'S PAST WORK" in json.dumps(r.messages)
                            for r in reviewer))
        # Nothing written to the user's experience.
        self.assertEqual(self._live_rows(), before)
        self.assertGreater(self.app.db.query(
            "SELECT COUNT(*) AS n FROM experience_stages WHERE scope = ?",
            (f"work:{project['id']}:noctx",))[0]["n"], 0)
        # ITS OWN TREE, for the debugger to show how its stages evolved - as
        # /api/work/tree gives a work's - and the other ways' their own.
        tree = self._get("/api/debugger/experience/tree",
                         {"project": project["id"], "variant": "noctx"})
        self.assertTrue(tree["cards"])
        self.assertTrue(tree["stages"])
        self.assertEqual({one["id"] for one in tree["stages"]},
                         {row["id"] for row in self.app.db.query(
                             "SELECT id FROM experience_stages WHERE scope = ?",
                             (f"work:{project['id']}:noctx",))})
        other = self._get("/api/debugger/experience/tree",
                          {"project": project["id"], "variant": "full"})
        self.assertEqual(other["stages"], [])
        from personalized_agent.api.routes import ApiError
        with self.assertRaises(ApiError):
            self._get("/api/debugger/experience/tree", {"project": "dbg_none", "variant": "noctx"})

    def test_full_reads_the_users_experience_and_writes_none(self) -> None:
        models = _Models()
        self.app.work.models = models
        project = self._project()
        before = self._live_rows()
        self._start(project, "full")
        run = self._finished(project, "full")
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertTrue(any("ZEBRAQUOKKA" in json.dumps(r.messages, default=str)
                            for r in self._reviewer(models)))
        for request in models.asked:
            if "You review a work agent" not in request.system:
                self.assertNotIn("ZEBRAQUOKKA", json.dumps(request.messages, default=str))
        self.assertEqual(self._live_rows(), before)
        # Nor recorded as the agent's activity, which the heartbeats read.
        events = [e for e in self.app.raw_store.recent_events(limit=200)
                  if (e.data or {}).get("by") == "agent"]
        self.assertEqual(events, [])


class SideBySideTests(_DebuggerBase):

    def test_two_ways_at_once_and_the_users_own_rule_unchanged(self) -> None:
        models = _Models(gate=True)
        self.app.work.models = models
        project = self._project()
        one = self._start(project, "single")
        two = self._start(project, "noctx")
        self.assertNotEqual(one["run"], two["run"])
        self.assertTrue(_until(lambda: models.waiting >= 2, 30))
        rows = {v: self._run_row(project, v) for v in ("single", "noctx")}
        self.assertTrue(all(row["alive"] for row in rows.values()), rows)
        # One run at a time of each way.
        from personalized_agent.api.errors import ApiError
        with self.assertRaises(ApiError):
            self._start(project, "single")
        # The user's own work starts beside them - and its one-at-a-time rule
        # holds among its own runs.
        experience = self.app.state_heartbeat.experience
        unit = experience.unit(experience.store.create_work_project("Mine")["unit_id"])
        mine = self.app.work.start(unit, "write mine")
        self.assertNotIn("error", mine, mine)
        other = experience.unit(experience.store.create_work_project("Also mine")["unit_id"])
        self.assertIn("already going", self.app.work.start(other, "write it")["error"])
        # Each way followed, stopped, by its run alone.
        live = self._get("/api/work/live", {"run": one["run"]})
        self.assertEqual(live["run"], one["run"])
        self.assertEqual(self._post("/api/work/pause", {"run": two["run"]}), {"paused": True})
        self.assertEqual(self._post("/api/work/resume", {"run": two["run"]}), {"paused": False})
        models.gate.set()
        for variant in ("single", "noctx"):
            self.assertEqual(self._finished(project, variant)["status"], "done")


class TheLogFolderTests(_DebuggerBase):

    def test_open_logs_opens_a_ways_own_folder_and_nothing_else(self) -> None:
        from personalized_agent.api.errors import ApiError
        opened = []
        self.app.work.open_folder = opened.append
        project = self._project()
        unit_id = f"{project['id']}:full"
        for run_id, folder, unit in (("run_dbg", self.chosen / "try_1" / "C", unit_id),
                                     ("run_out", self.chosen, unit_id),
                                     ("run_odd", self.chosen / "try_1" / "B", "task-x")):
            self.app.db.execute(
                "INSERT INTO work_runs (id, unit_id, unit_name, folder, status, "
                "started_at, updated_at) VALUES (?, ?, 'x', ?, 'done', '2026-10-01', "
                "'2026-10-01')", (run_id, unit, str(folder)))
        said = self._post("/api/work/open_logs", {"run": "run_dbg"})
        self.assertEqual(Path(said["opened"]), self.chosen / "try_1" / "C" / "run_logs")
        self.assertEqual(opened, [self.chosen / "try_1" / "C" / "run_logs"])
        for run_id in ("run_out", "run_odd"):
            with self.assertRaises(ApiError):
                self._post("/api/work/open_logs", {"run": run_id})


def _link(link: Path, target: Path) -> str:
    """A link to a folder, made as this machine can: a symbolic link, or on
    Windows without the right to make one a junction. "" when neither."""
    import os
    import subprocess
    import sys
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        pass
    if sys.platform.startswith("win"):
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True)
        if made.returncode == 0 and link.exists():
            return "junction"
    return ""


class ClearingAWayTests(_DebuggerBase):
    """CLEAR, a way at a time: everything that way wrote - in its own folder
    and nowhere else - gone, and its runs forgotten; its folder stays, empty,
    and the other two ways and the project are as they were."""

    def _clear(self, project, variant):
        return self._post("/api/debugger/experience/projects",
                          {"action": "clear", "id": project["id"], "variant": variant})

    def test_clearing_one_way_leaves_the_other_two_and_the_users_own(self) -> None:
        import os
        import stat
        self.app.work.models = _Models()
        project = self._project()
        for variant in ("single", "noctx"):
            self._start(project, variant)
            self.assertEqual(self._finished(project, variant)["status"], "done")
        single = self.chosen / "try_1" / "A"
        noctx = self.chosen / "try_1" / "B"
        full = self.chosen / "try_1" / "C"
        self.assertTrue((single / "alone.md").is_file())
        self.assertTrue((noctx / "notes.md").is_file())
        (full / "c_made.txt").write_text("the full way's")
        # The user's own files, beside the three: their input.
        (self.chosen / "input.csv").write_text("the user's own")
        (self.chosen / "theirs").mkdir()
        (self.chosen / "theirs" / "notes.txt").write_text("also theirs")
        # Whatever else way B wrote: deep folders, versions, a read-only file.
        deep = noctx / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "made.py").write_text("print(1)")
        (noctx / ".versions").mkdir(exist_ok=True)
        (noctx / ".versions" / "blob").write_bytes(b"\x00" * 10)
        locked = noctx / "read_only.txt"
        locked.write_text("cannot be written")
        os.chmod(locked, stat.S_IREAD)
        single_files = sorted(p.relative_to(single) for p in single.rglob("*"))
        said = self._clear(project, "noctx")
        self.assertEqual(said["cleared"], f"{project['id']}:noctx")
        self.assertEqual(said["runs"], 1)
        self.assertGreaterEqual(said["files"], 4)
        self.assertGreaterEqual(said["folders"], 4)
        self.assertEqual(said["not_removed"], [])
        # B: there, and empty; its runs and its tree gone.
        self.assertTrue(noctx.is_dir())
        self.assertEqual(list(noctx.iterdir()), [])
        self.assertEqual(self._get("/api/debugger/experience/runs",
                                   {"project": project["id"], "variant": "noctx"})["runs"], [])
        self.assertEqual(self.app.db.query(
            "SELECT COUNT(*) AS n FROM experience_cards WHERE scope = ?",
            (f"work:{project['id']}:noctx",))[0]["n"], 0)
        # A and C as they were - their files and A's run.
        self.assertEqual(sorted(p.relative_to(single) for p in single.rglob("*")), single_files)
        self.assertEqual((full / "c_made.txt").read_text(), "the full way's")
        self.assertIsNotNone(self._run_row(project, "single"))
        # The user's own files, and the project.
        self.assertEqual((self.chosen / "input.csv").read_text(), "the user's own")
        self.assertEqual((self.chosen / "theirs" / "notes.txt").read_text(), "also theirs")
        listed = self._get("/api/debugger/experience/projects")["projects"]
        self.assertEqual([one["id"] for one in listed], [project["id"]])
        # And B starts again, from nothing.
        self._start(project, "noctx")
        self.assertEqual(self._finished(project, "noctx")["status"], "done")

    def test_refused_while_that_way_goes_and_not_for_another(self) -> None:
        from personalized_agent.api.errors import ApiError
        models = _Models(gate=True)
        self.app.work.models = models
        project = self._project()
        (self.chosen / "try_1" / "B" / "b_kept.txt").write_text("still here")
        (self.chosen / "try_1" / "A" / "a_made.txt").write_text("A's")
        self._start(project, "noctx")
        self.assertTrue(_until(lambda: models.waiting >= 1, 30))
        # B goes, A is idle: B is refused...
        with self.assertRaises(ApiError) as caught:
            self._clear(project, "noctx")
        self.assertIn("going", str(caught.exception))
        self.assertTrue((self.chosen / "try_1" / "B" / "b_kept.txt").is_file())
        self.assertIsNotNone(self._run_row(project, "noctx"))
        # ...and A, beside it, is cleared.
        said = self._clear(project, "single")
        self.assertEqual(said["files"], 1)
        self.assertEqual(list((self.chosen / "try_1" / "A").iterdir()), [])
        self.assertTrue((self.chosen / "try_1" / "B" / "b_kept.txt").is_file())
        self.assertTrue(self._run_row(project, "noctx")["alive"])
        models.gate.set()
        self._finished(project, "noctx")
        for body in ({"action": "clear", "id": "dbg_none", "variant": "single"},
                     {"action": "clear", "id": project["id"], "variant": "fourth"},
                     {"action": "clear", "id": project["id"]}):
            with self.assertRaises(ApiError):
                self._post("/api/debugger/experience/projects", body)

    def test_clear_all_empties_the_three_and_keeps_the_project_and_the_users_own(self) -> None:
        self.app.work.models = _Models()
        project = self._project()
        self._start(project, "single")
        self.assertEqual(self._finished(project, "single")["status"], "done")
        (self.chosen / "try_1" / "B" / "b_made.txt").write_text("B's")
        (self.chosen / "try_1" / "C" / "deep").mkdir()
        (self.chosen / "try_1" / "C" / "deep" / "c_made.txt").write_text("C's")
        (self.chosen / "input.csv").write_text("the user's own")
        said = self._post("/api/debugger/experience/projects",
                          {"action": "clear_all", "id": project["id"]})
        self.assertEqual(said["cleared_all"], project["id"])
        self.assertEqual(said["runs"], 1)
        self.assertGreaterEqual(said["files"], 3)
        self.assertEqual(set(said["ways"]), {"single", "noctx", "full"})
        for folder in ("A", "B", "C"):
            self.assertTrue((self.chosen / "try_1" / folder).is_dir())
            self.assertEqual(list((self.chosen / "try_1" / folder).iterdir()), [], folder)
        self.assertIsNone(self._run_row(project, "single"))
        self.assertEqual((self.chosen / "input.csv").read_text(), "the user's own")
        listed = self._get("/api/debugger/experience/projects")["projects"]
        self.assertEqual([one["id"] for one in listed], [project["id"]])

    def test_clear_all_is_refused_while_any_way_goes_and_clears_nothing(self) -> None:
        from personalized_agent.api.errors import ApiError
        models = _Models(gate=True)
        self.app.work.models = models
        project = self._project()
        (self.chosen / "try_1" / "A" / "a_kept.txt").write_text("A's")
        self._start(project, "noctx")
        self.assertTrue(_until(lambda: models.waiting >= 1, 30))
        with self.assertRaises(ApiError) as caught:
            self._post("/api/debugger/experience/projects",
                       {"action": "clear_all", "id": project["id"]})
        self.assertIn("going", str(caught.exception))
        self.assertTrue((self.chosen / "try_1" / "A" / "a_kept.txt").is_file())
        self.assertIsNotNone(self._run_row(project, "noctx"))
        models.gate.set()
        self._finished(project, "noctx")

    def test_clear_returns_at_once_and_the_old_files_go_behind_it(self) -> None:
        """A way with its packages installed is tens of thousands of files,
        and deleting them took a clear half a minute: where the folder's
        seal goes with it, it is set aside and deleted in the background,
        a fresh one in its place - and its code can write there again."""
        from personalized_agent.agent.work import debug_projects as dp
        from personalized_agent.runtime import isolation
        project = self._project()
        way = self.chosen / "try_1" / "B"
        for n in range(300):
            folder = way / ".packages" / f"pkg{n // 30}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"m{n}.py").write_text("x = 1")
        backend = isolation.isolation_status().get("backend")
        if backend == "appcontainer":
            # Its code has written there - the folder granted - before.
            done = isolation.run_isolated(
                [sys.executable, "-I", "-c", "open('before.txt', 'w').write('x')"],
                cwd=way, writable=[way], network="none", timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
        said = self._post("/api/debugger/experience/projects",
                          {"action": "clear", "id": project["id"], "variant": "noctx"})
        self.assertGreaterEqual(said["files"], 300)
        self.assertEqual(list(way.iterdir()), [])
        dp.wait_deleted(60)
        self.assertEqual([one.name for one in self.chosen.iterdir()
                          if dp.ASIDE_MARK in one.name], [], "the old folder deleted")
        if backend == "appcontainer":
            done = isolation.run_isolated(
                [sys.executable, "-I", "-c", "open('after.txt', 'w').write('y')"],
                cwd=way, writable=[way], network="none", timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual((way / "after.txt").read_text(), "y")

    def test_two_clears_at_once_are_one_after_the_other(self) -> None:
        """A second click, or Clear all over a Clear: two clears of one way
        at the same moment. They had raced for the folder - one put the old
        folder back over the other's fresh one - and two deleters went at
        the same old files, each saying the other's work could not be done.
        Now the second waits for the first, and nothing is said."""
        import threading
        from personalized_agent.agent.work import debug_projects as dp
        project = self._project()
        way = self.chosen / "try_1" / "C"
        for n in range(200):
            folder = way / "work" / f"part{n // 20}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"f{n}.txt").write_text("x")
        said = []
        with self.assertNoLogs("personalized_agent.agent.work.debug_projects", "WARNING"):
            threads = [threading.Thread(target=lambda: said.append(self._post(
                "/api/debugger/experience/projects",
                {"action": "clear", "id": project["id"], "variant": "full"})))
                for _ in range(2)]
            threads.append(threading.Thread(target=lambda: said.append(self._post(
                "/api/debugger/experience/projects",
                {"action": "clear_all", "id": project["id"]}))))
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(120)
            dp.wait_deleted(60)
        self.assertEqual([one.get("error") for one in said], [None, None, None], said)
        self.assertEqual(sum(int(one.get("files") or 0) for one in said), 200)
        self.assertTrue(way.is_dir())
        self.assertEqual(list(way.iterdir()), [])
        self.assertEqual([one.name for one in self.chosen.iterdir()
                          if dp.ASIDE_MARK in one.name], [], "every old folder deleted")

    def test_a_link_inside_is_removed_and_what_it_leads_to_is_left(self) -> None:
        import os
        project = self._project()
        target = Path(self.agent.dir) / "elsewhere"
        target.mkdir()
        (target / "precious.txt").write_text("not the project's")
        link = self.chosen / "try_1" / "A" / "into_elsewhere"
        kind = _link(link, target)
        if not kind:
            self.skipTest("this machine can make neither a symbolic link nor a junction")
        self.assertTrue((link / "precious.txt").is_file())
        said = self._clear(project, "single")
        self.assertEqual(said["links"], 1, said)
        self.assertFalse(os.path.lexists(link), kind)
        self.assertEqual((target / "precious.txt").read_text(), "not the project's")
        self.assertEqual(list((self.chosen / "try_1" / "A").iterdir()), [])

    def test_a_ways_folder_that_is_a_link_is_left_alone(self) -> None:
        import os
        import shutil as _shutil
        project = self._project()
        target = Path(self.agent.dir) / "somewhere"
        target.mkdir()
        (target / "precious.txt").write_text("not the project's")
        way = self.chosen / "try_1" / "B"
        _shutil.rmtree(way)
        if not _link(way, target):
            self.skipTest("this machine can make neither a symbolic link nor a junction")
        said = self._clear(project, "noctx")
        self.assertEqual([Path(one["path"]) for one in said["left_alone"]], [way])
        self.assertTrue((target / "precious.txt").is_file())
        self.assertTrue(os.path.lexists(way))


if __name__ == "__main__":
    unittest.main()


class StartingAWayTests(_DebuggerBase):
    """Packages install without asking unless "ask" is chosen; and Start
    answers at once - the run logs are sealed on the run's own thread, not
    on the request (a second way's Start waited behind the first's seal)."""

    def test_packages_are_auto_unless_asked(self) -> None:
        self.app.work.models = _Models(gate=True)
        project = self._project()
        self._start(project, "single")
        self._start(project, "noctx", packages="ask")
        self.assertEqual(self._run_row(project, "single")["plan"]["packages"]["mode"], "auto")
        self.assertEqual(self._run_row(project, "noctx")["plan"]["packages"]["mode"], "ask")

    def test_the_logs_are_sealed_on_the_runs_thread(self) -> None:
        import threading
        from unittest import mock
        threads = []
        # Made first: making a project seals its three folders, once, there.
        project = self._project()
        with mock.patch("personalized_agent.runtime.isolation.seal_from_containers",
                        side_effect=lambda paths: threads.append(
                            threading.current_thread().name)):
            self.app.work.models = _Models(gate=True)
            self.app.work.sandbox_enabled = True
            said = self._start(project, "single")
            _until(lambda: said["run"] in threads, 30)
        self.assertNotIn(threading.current_thread().name, threads)
        self.assertIn(said["run"], threads)


class TheReadoutListsWhatIsGoingTests(_DebuggerBase):
    """A run goes on with its panel closed - the debugger's too - and the
    status readout lists every one going."""

    def test_a_going_way_is_in_the_agent_state_and_its_readout(self) -> None:
        from personalized_agent.ui.desktop import _work_lines
        models = _Models(gate=True)
        self.app.work.models = models
        project = self._project()
        said = self._start(project, "full")
        self.assertTrue(_until(lambda: models.waiting >= 1, 30))
        going = self.app.agent_state()["work"]
        self.assertEqual([one["id"] for one in going], [said["run"]])
        self.assertEqual(going[0]["name"], "Notes")
        self.assertEqual(going[0]["way"], "Full")
        self.assertTrue(going[0]["debugger"])
        lines = _work_lines(going)
        self.assertEqual(lines[0], "Work going: 1")
        self.assertIn("Notes · Full", lines[1])
        self.assertIn(" in / ", lines[1])

    def test_nothing_going_says_nothing(self) -> None:
        from personalized_agent.ui.desktop import _work_lines
        self.assertEqual(self.app.agent_state()["work"], [])
        self.assertEqual(_work_lines([]), [])


class NeutralFolderNamesTests(unittest.TestCase):
    """The ways' folders are named for nothing - A, B, C - since every call
    tells a way where it writes; a project made under the old names keeps
    them."""

    def test_letters_and_the_old_names_kept(self) -> None:
        from personalized_agent.agent.work.debug_projects import (
            VARIANT_FOLDERS, variant_folder)
        self.assertEqual(sorted(VARIANT_FOLDERS.values()), ["A", "B", "C"])
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(variant_folder(root, "full"), root / "C")
            (root / "3_full").mkdir()
            self.assertEqual(variant_folder(root, "full"), root / "3_full")
            (root / "C").mkdir()
            self.assertEqual(variant_folder(root, "full"), root / "C")

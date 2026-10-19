"""Versions of a work run: saved at each key point - the files and where the
run stood - kept once per content, and continued from any of them: an older
one rolls the files and the state back and starts a branch, the later line
kept. A file the user changed in a granted folder is theirs, and left."""

from __future__ import annotations

import json
import shutil
import subprocess
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
        return self.body[key]

    def int_param(self, key, default):
        return int(self.body.get(key, default))


class _Fails:
    """A model that is never reached: a continued run fails at once, leaving
    the folder and the state as the rollback made them."""

    def __init__(self, gate=None):
        self.gate = gate

    def invoke(self, request, role=""):
        if self.gate is not None:
            self.gate.wait(30)
        raise RuntimeError("no model in this test")


class _WritesGranted(_TwoAgents):
    """Each round also writes two files in a granted folder: out.txt and
    b.txt, saying which round wrote them."""

    def __init__(self, granted, **kwargs):
        super().__init__(**kwargs)
        self.granted = Path(granted)
        self.writes = 0

    def invoke(self, request, role=""):
        from personalized_agent.models.base import ToolCall
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        last = request.messages[-1]["content"]
        if "You review a work agent" not in request.system and "WORK ONE ROUND" in first \
                and isinstance(last, str) and "FAILED THE CHECKS" not in last \
                and "THE RUN IS PAUSING HERE" not in last:
            self.writes += 1
            self.asked.append(request)
            said = f"round {self.writes}"
            return self._reply("", [
                ToolCall(id="c1", name="write_file", arguments={"path": "notes.md",
                                                                "content": "hello"}),
                ToolCall(id="c2", name="write_file", arguments={
                    "path": str(self.granted / "out.txt"), "content": said}),
                ToolCall(id="c3", name="write_file", arguments={
                    "path": str(self.granted / "b.txt"), "content": said})])
        return super().invoke(request, role)


class _RunsBase(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        # A project of the agents', so the routes find it as well.
        experience = self.app.state_heartbeat.experience
        made = experience.store.create_work_project("Write notes")
        self.unit = experience.unit(made["unit_id"])
        from personalized_agent.api.routes import build_routes
        self.routes = build_routes(self.app)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _wait(self, run_id=None):
        for _ in range(600):
            runs = self.app.work.runs(self.unit["id"])
            if runs and not any(one["alive"] for one in runs) \
                    and runs[0]["status"] not in ("running", "waiting for you"):
                break
            time.sleep(0.1)
        return self.app.work.run(run_id) if run_id else \
            self.app.work.runs(self.unit["id"])[0]

    def _run(self, models, limits=None):
        self.app.work.models = models
        started = self.app.work.start(self.unit, "write the notes", limits=limits)
        self.assertNotIn("error", started, started)
        return self._wait(started["run"]), Path(started["folder"])

    def _continue(self, run_id, models, from_version="", limits=None):
        self.app.work.models = models
        said = self.app.work.continue_run(run_id, self.unit,
                                          limits or {"calls": 0, "minutes": 0, "tokens": 0},
                                          from_version=from_version)
        self.assertNotIn("error", said, said)
        return said, self._wait(run_id)

    def _versions(self, run_id):
        return self.app.work.versions(run_id)

    def _manifest(self, folder, run_id, version_id):
        from personalized_agent.agent.work.versions import VersionStore
        return VersionStore(folder / "runs" / run_id).manifest(version_id)


class VersionsAreSavedAtEachKeyPointTests(_RunsBase):

    def test_a_finished_run_has_one_at_each_key_point(self) -> None:
        run, folder = self._run(_TwoAgents())
        self.assertEqual(run["status"], "done", run["log"][-6:])
        tree = self._versions(run["id"])
        kinds = [one["kind"] for one in tree["versions"]]
        # A VERSION IS A CHECKPOINT (the user's decision, 2026-10-18): the
        # contract approved, the full review the reviewer asked for when the
        # notes were done, and the run done - no round, review or deck of
        # its own.
        self.assertEqual(kinds, ["contract", "full review", "finished"])
        # One line, each after the one before it; the last is the head.
        self.assertEqual([one["parent"] for one in tree["versions"]], ["", "v1", "v2"])
        self.assertEqual({one["branch"] for one in tree["versions"]}, {"main"})
        self.assertEqual(tree["head"], "v3")
        last = tree["versions"][-1]["summary"]
        self.assertEqual((last["rounds"], last["cards_done"], last["deck"]), (1, 1, True))
        # In the store inside the run's own folder, and nowhere else.
        store = folder / "runs" / run["id"] / ".versions"
        self.assertTrue((store / "index.json").is_file())
        self.assertTrue((store / "versions" / "v3.json").is_file())
        # What each holds: the contract version has the contract and not
        # yet the notes; the finished one has everything.
        first = self._manifest(folder, run["id"], "v1")["files"]
        self.assertIn(f"runs/{run['id']}/contract.json", first)
        self.assertNotIn("notes.md", first)
        whole = self._manifest(folder, run["id"], "v3")["files"]
        for name in ("notes.md", f"runs/{run['id']}/presentation.html",
                     f"runs/{run['id']}/report.md"):
            self.assertIn(name, whole)
        self.assertFalse(any("/.versions/" in path for path in whole))
        # The runs list carries them in brief - and, of a run going, how
        # many still have their slides being drawn.
        self.assertEqual(self.app.work.runs(self.unit["id"])[0]["versions"],
                         {"count": 3, "head": "v3", "branch": "main", "drawing": 0})
        # The checkpoint the run went from last, in its plan.
        self.assertEqual(run["plan"]["checkpoint"]["version"], "v2")

    def test_only_a_checkpoint_is_a_version(self) -> None:
        from personalized_agent.agent.work.runner import NOT_FINISHED
        # Rounds the reviewer asked no full review of keep none.
        models = _TwoAgents(rounds_to_complete=3)
        run, _folder = self._run(models)
        kinds = [one["kind"] for one in self._versions(run["id"])["versions"]]
        self.assertEqual(kinds, ["contract", "full review", "finished"])
        self.assertEqual(self._versions(run["id"])["versions"][1]["round"], 3)
        self.assertEqual(models.full_reviews, 1)
        # A full review that does not pass keeps none, and the work goes on
        # - to the next one, which does.
        self.agent.cleanup()
        self.setUp()
        run, _folder = self._run(_TwoAgents(full_fails=1))
        self.assertEqual(run["status"], "done", run["log"][-6:])
        versions = self._versions(run["id"])["versions"]
        self.assertEqual([one["kind"] for one in versions], ["contract", "full review", "finished"])
        self.assertEqual([one["passed"] for one in run["plan"]["full_reviews"]], [False, True])
        self.assertEqual(versions[1]["round"], 2)
        # Paused at a limit: no version - it goes on from where it stopped,
        # its to-do list in its plan.
        self.agent.cleanup()
        self.setUp()
        run, _folder = self._run(_TwoAgents(), limits={"calls": 7, "minutes": 0, "tokens": 0})
        self.assertEqual(run["status"], NOT_FINISHED)
        tree = self._versions(run["id"])
        self.assertEqual([one["kind"] for one in tree["versions"]], ["contract"])
        self.assertTrue(run["plan"]["todo"]["next"])

    def test_how_a_session_ended_names_its_version(self) -> None:
        from personalized_agent.agent.work.runner import (BLOCKED, DONE, NOT_FINISHED,
                                                          Run)
        from personalized_agent.agent.work.runner import FULL, SINGLE
        run = Run.__new__(Run)
        saved = []
        run._version = lambda kind, label, message=None: saved.append(
            (kind, label, message["title"]))
        # The single model keeps each end: it has no review, no checkpoint.
        run.variant = SINGLE
        for status, reason in ((DONE, ""), (NOT_FINISHED, "stopped"),
                               (NOT_FINISHED, "the call limit"),
                               (NOT_FINISHED, "the time limit"), (NOT_FINISHED, ""),
                               (BLOCKED, "")):
            run.status, run.stop_reason = status, reason
            run._end_version()
        self.assertEqual([kind for kind, _label, _title in saved],
                         ["finished", "stopped", "limit reached", "limit reached",
                          "paused", "ended"])
        # Named for how it ended, as a commit's title.
        self.assertEqual([title for _kind, _label, title in saved][:3],
                         ["Finish: done", "Stop: stopped", "Pause: the call limit"])
        # A builder and a reviewer keep only the end that is a checkpoint.
        saved.clear()
        run.variant = FULL
        for status, reason in ((DONE, ""), (NOT_FINISHED, "stopped"),
                               (NOT_FINISHED, "the call limit"), (BLOCKED, "")):
            run.status, run.stop_reason = status, reason
            run._end_version()
        self.assertEqual([kind for kind, _label, _title in saved], ["finished"])


class AFileIsKeptOnceTests(unittest.TestCase):

    def test_the_same_content_is_one_object(self) -> None:
        from personalized_agent.agent.work.versions import VersionStore
        with tempfile.TemporaryDirectory() as work:
            store = VersionStore(Path(work) / "run")
            one = store.put(b"hello")
            self.assertEqual(store.put(b"hello"), one)
            objects = [p for p in (store.root / "objects").rglob("*") if p.is_file()]
            self.assertEqual(len(objects), 1)
            self.assertEqual(store.get(one), b"hello")
            # A damaged object is never handed back.
            objects[0].write_bytes(b"jello")
            from personalized_agent.agent.work.versions import VersionError
            with self.assertRaises(VersionError):
                store.get(one)

    def test_a_run_keeps_an_unchanged_file_once(self) -> None:
        agent = TemporaryAgent()
        try:
            app = agent.app
            app.work.models = _TwoAgents()
            unit = {"id": "task-ver002", "name": "Dedupe", "kind": "task", "why": ""}
            started = app.work.start(unit, "write the notes")
            for _ in range(600):
                runs = app.work.runs(unit["id"])
                if runs and not runs[0]["alive"] and runs[0]["status"] != "running":
                    break
                time.sleep(0.1)
            from personalized_agent.agent.work.versions import VersionStore, sha_of_bytes
            store = VersionStore(Path(started["folder"]) / "runs" / started["run"])
            held = [store.manifest(f"v{n}")["files"].get("notes.md") for n in range(2, 4)]
            self.assertTrue(all(held))
            self.assertEqual({one["sha"] for one in held}, {sha_of_bytes(b"hello")})
            copies = [p for p in (store.root / "objects").rglob("*")
                      if p.is_file() and p.read_bytes() == b"hello"]
            self.assertEqual(len(copies), 1, "notes.md is kept once, in two versions")
        finally:
            agent.cleanup()


class ContinuingFromAnOlderVersionTests(_RunsBase):

    def test_rollback_restores_files_exactly_and_the_state(self) -> None:
        run, folder = self._run(_TwoAgents())
        run_dir = folder / "runs" / run["id"]
        contract = (run_dir / "contract.json").read_bytes()
        # After the run: a file added to the work's folder, the notes
        # changed - and what a version never touches: another run's records
        # and this run's packages.
        (folder / "later.txt").write_text("added after")
        (folder / "notes.md").write_text("changed")
        other = folder / "runs" / "run_other" / "x.txt"
        other.parent.mkdir(parents=True)
        other.write_text("another run's")
        package = run_dir / ".packages" / "pkg.py"
        package.parent.mkdir(parents=True)
        package.write_text("x = 1")
        said, after = self._continue(run["id"], _Fails(), from_version="v1")
        back = said["rolled_back"]
        self.assertEqual((back["version"], back["branch"]), ("v1", "branch 2"))
        # The files are the contract version's, exactly.
        self.assertFalse((folder / "notes.md").exists())
        self.assertFalse((folder / "later.txt").exists())
        self.assertFalse((run_dir / "presentation.html").exists())
        self.assertFalse((run_dir / "report.md").exists())
        self.assertEqual((run_dir / "contract.json").read_bytes(), contract)
        self.assertEqual(other.read_text(), "another run's")
        self.assertEqual(package.read_text(), "x = 1")
        # And the state: no rounds, no deck; the card open again, its
        # criteria kept; the tree's stages as they were.
        plan = after["plan"]
        self.assertEqual(plan["rounds"], [])
        self.assertNotIn("deck", plan)
        self.assertEqual(plan["contract"]["goal"], "notes.md says hello")
        tree = self.app.work.tree(self.unit)
        notes = [c for c in tree["cards"] if c["title"] == "The notes"][0]
        self.assertEqual(notes["status"], "open")
        self.assertEqual([one["text"] for one in notes["checked_by"]["criteria"]],
                         ["notes.md says hello"])
        self.assertEqual([s["kind"] for s in tree["stages"]], ["goal", "plan"])
        # What it spent is not undone: both sessions are counted.
        self.assertEqual(len(plan["sessions"]), 2)
        self.assertTrue(any(one["what"] == "rolled back" for one in after["log"]))

    def test_a_pause_goes_on_from_where_it_stopped(self) -> None:
        # A pause is no checkpoint: no version of it - continued, the run
        # goes on from where it stopped, its files as they are.
        run, folder = self._run(_TwoAgents(), limits={"calls": 8, "minutes": 0, "tokens": 0})
        self.assertEqual(self._versions(run["id"])["head"], "v1")
        run_dir = folder / "runs" / run["id"]
        self.assertTrue((run_dir / "todo.md").is_file())
        said, done = self._continue(run["id"], _TwoAgents())
        self.assertNotIn("rolled_back", said)
        self.assertEqual(done["status"], "done", done["log"][-6:])
        self.assertTrue(done["plan"]["deck"]["finished"])
        self.assertEqual(len(done["plan"]["sessions"]), 2)

    def test_a_branch_keeps_the_other_line(self) -> None:
        run, folder = self._run(_TwoAgents())
        before = self._versions(run["id"])["versions"]
        # Continued from the contract version - a finished run too - it is
        # worked again, on a branch of its own.
        _said, again = self._continue(run["id"], _TwoAgents(), from_version="v1")
        self.assertEqual(again["status"], "done", again["log"][-6:])
        tree = self._versions(run["id"])
        self.assertEqual(tree["versions"][:3], before, "the first line is kept as it was")
        new = tree["versions"][3:]
        self.assertTrue(new)
        self.assertEqual(new[0]["parent"], "v1")
        self.assertEqual({one["branch"] for one in new}, {"branch 2"})
        self.assertEqual(tree["head"], new[-1]["id"])
        self.assertEqual(tree["branches"], ["main", "branch 2"])
        # And the old line can be gone back to: its tip carries it on.
        _said, back = self._continue(run["id"], _Fails(), from_version="v3")
        self.assertEqual(back["plan"]["rolled_back"]["branch"], "main")
        self.assertTrue((folder / "notes.md").exists())
        self.assertTrue(back["plan"]["deck"]["finished"])

    def test_a_finished_run_from_its_latest_is_not_continued(self) -> None:
        from personalized_agent.api.errors import ApiError
        run, _folder = self._run(_TwoAgents())
        with self.assertRaises(ApiError):
            self.routes[("POST", "/api/work/continue")](_Request({"run": run["id"]}))
        with self.assertRaises(ApiError):
            self.routes[("POST", "/api/work/continue")](
                _Request({"run": run["id"], "from_version": "v3"}))
        with self.assertRaises(ApiError):
            self.routes[("POST", "/api/work/continue")](
                _Request({"run": run["id"], "from_version": "v99"}))

    def test_a_run_that_is_going_is_not_rolled_back(self) -> None:
        from personalized_agent.api.errors import ApiError
        run, folder = self._run(_TwoAgents())
        gate = threading.Event()
        try:
            self.app.work.models = _Fails(gate)
            said = self.app.work.continue_run(run["id"], self.unit, {},
                                              from_version="v2")
            self.assertNotIn("error", said, said)
            with self.assertRaises(ApiError) as caught:
                self.routes[("POST", "/api/work/continue")](
                    _Request({"run": run["id"], "from_version": "v1"}))
            self.assertEqual(caught.exception.status, 409)
            # Nothing was rolled back: the notes are still there.
            self.assertTrue((folder / "notes.md").exists())
        finally:
            gate.set()
            self._wait(run["id"])


class GrantedFoldersOnRollbackTests(_RunsBase):

    def setUp(self) -> None:
        super().setUp()
        self.granted = Path(tempfile.mkdtemp(prefix="pa-granted-ver-"))
        (self.granted / "mine.txt").write_text("the user's own")
        said = self.app.work.grant(self.unit["id"], str(self.granted))
        self.assertNotIn("error", said, said)

    def tearDown(self) -> None:
        shutil.rmtree(self.granted, ignore_errors=True)
        super().tearDown()

    def test_only_what_is_still_as_the_agent_left_it_is_put_back(self) -> None:
        run, _folder = self._run(_WritesGranted(self.granted, rounds_to_complete=2,
                                                full_review="always"))
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertEqual((self.granted / "out.txt").read_text(), "round 2")
        versions = self._versions(run["id"])["versions"]
        first_round = [one for one in versions if one["kind"] == "full review"][0]["id"]
        detail = self.app.work.version(run["id"], first_round)
        self.assertEqual(sorted(Path(one["path"]).name for one in detail["granted"]),
                         ["b.txt", "out.txt"])
        # The user's own file is never in a version.
        self.assertFalse(any(Path(one["path"]).name == "mine.txt"
                             for one in detail["granted"]))
        # The user changes b.txt; then the run is rolled back to round 1.
        (self.granted / "b.txt").write_text("the user's edit")
        said, _after = self._continue(run["id"], _Fails(), from_version=first_round)
        self.assertEqual((self.granted / "out.txt").read_text(), "round 1")
        self.assertEqual((self.granted / "b.txt").read_text(), "the user's edit")
        self.assertEqual(said["rolled_back"]["kept"],
                         [f"kept your version of {(self.granted / 'b.txt').resolve()}"])
        self.assertEqual((self.granted / "mine.txt").read_text(), "the user's own")

    def test_a_file_the_agent_wrote_after_the_version_is_not_deleted(self) -> None:
        run, _folder = self._run(_WritesGranted(self.granted))
        # The contract version holds no file of the granted folder: rolled
        # back to it, the agent's later files there stay - nothing in a
        # granted folder is deleted.
        said, _after = self._continue(run["id"], _Fails(), from_version="v1")
        self.assertTrue((self.granted / "out.txt").exists())
        self.assertEqual(sorted(Path(one).name for one in said["rolled_back"]["left"]),
                         ["b.txt", "out.txt"])


class TheVersionsApiTests(_RunsBase):

    def test_the_tree_one_version_and_its_files(self) -> None:
        from personalized_agent.api.errors import ApiError
        run, folder = self._run(_TwoAgents())
        tree = self.routes[("GET", "/api/work/versions")](_Request({"run": run["id"]}))
        self.assertEqual(set(tree), {"run", "head", "branch", "branches", "versions",
                                     "alive", "status"})
        one = tree["versions"][1]
        # And what it did, as a commit says it; a round's number and what the
        # reviewer found of it, apart.
        self.assertEqual(set(one), {"id", "n", "parent", "branch", "label", "kind", "at",
                                    "files", "granted", "summary", "message", "round",
                                    "progress", "full_review"})
        self.assertEqual(set(one["message"]), {"title", "body", "files"})
        # Its slides made or not, and the run's tokens so far, in and out.
        self.assertEqual(set(one["summary"]) - {"slides_why"},
                         {"rounds", "cards", "cards_done", "todo", "deck", "phase",
                          "status", "slides", "tokens_in", "tokens_out", "for_user"})
        # Slides not made say why.
        if one["summary"]["slides"] == "not made":
            self.assertTrue(one["summary"]["slides_why"])
        detail = self.routes[("GET", "/api/work/version")](
            _Request({"run": run["id"], "id": "v3"}))
        self.assertTrue(detail["head"])
        self.assertTrue(detail["deck"])
        self.assertEqual(detail["goal"], "notes.md says hello")
        self.assertIn("notes.md", [f["path"] for f in detail["files"]])
        self.assertIn("todo", detail)
        with self.assertRaises(ApiError):
            self.routes[("GET", "/api/work/version")](_Request({"run": run["id"],
                                                                "id": "../index"}))
        with self.assertRaises(ApiError):
            self.routes[("GET", "/api/work/presentation")](
                _Request({"run": run["id"], "version": "v1"}))
        # A figure as a version holds it.
        figure = folder / "plot.png"
        figure.write_bytes(b"\x89PNG\r\n")
        from personalized_agent.agent.work.versions import VersionStore
        held = self.app.work.version_file(run["id"], "v3", str(figure))
        self.assertIsNone(held, "made after the version: not in it")
        served = self.routes[("GET", "/api/work/presentation")](
            _Request({"run": run["id"], "version": "v3"}))
        self.assertIn("Key deliverables", served.body.decode("utf-8"))
        self.assertTrue(VersionStore(folder / "runs" / run["id"]).index()["versions"])

    def test_the_agent_cannot_write_or_delete_the_versions(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools
        from personalized_agent.runtime.sandbox import SandboxRuntime
        folder = self.app.work.folder_for("Locked", "task-lock02")
        runtime = SandboxRuntime(self.app.work.policy_for(folder), platform=self.app.platform)
        tools = WorkTools(runtime)
        store = folder / "runs" / "run_1" / ".versions"
        (store / "versions").mkdir(parents=True)
        (store / "index.json").write_text("{}")
        tools.locked = [store]
        for path in ("runs/run_1/.versions/index.json",
                     "runs/run_1/.versions/versions/v1.json"):
            self.assertIn("locked", tools.call("write_file", {"path": path,
                                                              "content": "x"})["error"])
        self.assertIn("locked", tools.call("delete_file",
                                           {"path": "runs/run_1/.versions"})["error"])
        self.assertEqual((store / "index.json").read_text(), "{}")


class _Commits(_TwoAgents):
    """Two rounds: the first writes notes.md and scratch.md, the second
    changes notes.md, deletes scratch.md and adds extra.md - each round's
    answer with a commit saying what it changed (unless `commit` is off)."""

    def __init__(self, commit=True, **kwargs):
        # A full review each round: each a checkpoint, a version.
        super().__init__(rounds_to_complete=2, full_review="always", **kwargs)
        self.commit = commit

    def _summary(self, first):
        said = super()._summary(first)
        if self.commit:
            said["commit"] = {"title": f"write the greeting, take {self.summaries}.",
                              "body": [f"notes.md: hello {self.summaries}", "  ", 7]}
        return said

    def invoke(self, request, role=""):
        from personalized_agent.models.base import ToolCall
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        last = request.messages[-1]["content"]
        if ("You review a work agent" not in request.system and "WORK ONE ROUND" in first
                and isinstance(last, str) and "FAILED THE CHECKS" not in last
                and "THE RUN IS PAUSING HERE" not in last):
            self.asked.append(request)
            if self.summaries == 0:
                calls = [ToolCall(id="w1", name="write_file", arguments={
                             "path": "notes.md", "content": "hello"}),
                         ToolCall(id="w2", name="write_file", arguments={
                             "path": "scratch.md", "content": "scratch"})]
            else:
                calls = [ToolCall(id="w3", name="write_file", arguments={
                             "path": "notes.md", "content": "hello again"}),
                         ToolCall(id="w4", name="delete_file", arguments={"path": "scratch.md"}),
                         ToolCall(id="w5", name="write_file", arguments={
                             "path": "extra.md", "content": "more"})]
            return self._reply("", calls)
        return super().invoke(request, role)


class EachVersionSaysWhatItDidTests(_RunsBase):
    """A version is named as a commit is: a title saying what it changed - a
    round's from the commit the builder wrote with it, or from the round's
    own words - a few lines of the changes, and the files added, modified
    and deleted since its parent; the round and the reviewer's word apart."""

    def test_rounds_named_by_their_commits_with_the_files_they_changed(self) -> None:
        run, _folder = self._run(_Commits())
        self.assertEqual(run["status"], "done", run["log"][-6:])
        versions = self._versions(run["id"])["versions"]
        kinds = [one["kind"] for one in versions]
        self.assertEqual(kinds[:3], ["contract", "full review", "full review"])
        contract, first, second = versions[:3]
        self.assertEqual(contract["message"]["title"], "Contract: notes.md says hello")
        self.assertIn("deliverable: The notes", contract["message"]["body"])
        # The builder's commit, made a title: one line, upper case first, no
        # full stop; the label is the title.
        self.assertEqual(first["message"]["title"], "Write the greeting, take 1")
        self.assertEqual(first["label"], first["message"]["title"])
        self.assertEqual(first["message"]["body"], ["notes.md: hello 1", "7"])
        self.assertEqual((first["round"], first["progress"]), (1, "advanced"))
        self.assertEqual((second["round"], second["progress"]), (2, "advanced"))
        self.assertNotIn("advanced", first["message"]["title"])
        # The files, against the version before.
        self.assertIn("notes.md", first["message"]["files"]["added"])
        self.assertIn("scratch.md", first["message"]["files"]["added"])
        changed = second["message"]["files"]
        self.assertEqual(changed["deleted"], ["scratch.md"])
        self.assertIn("notes.md", changed["modified"])
        self.assertIn("extra.md", changed["added"])
        self.assertEqual(changed["counts"]["deleted"], 1)
        self.assertEqual(changed["counts"]["added"], len(changed["added"]))
        # The other kinds, named for what they are.
        titles = {one["kind"]: one["message"]["title"] for one in versions}
        self.assertEqual(titles["finished"], "Finish: done")
        self.assertNotIn("final review", titles)
        self.assertNotIn("deck", titles)
        # And from the API, the list and one version.
        listed = self.routes[("GET", "/api/work/versions")](_Request({"run": run["id"]}))
        self.assertEqual(listed["versions"][1]["message"]["title"], "Write the greeting, take 1")
        one = self.routes[("GET", "/api/work/version")](
            _Request({"run": run["id"], "id": second["id"]}))
        self.assertEqual(one["message"]["files"]["deleted"], ["scratch.md"])
        self.assertEqual((one["round"], one["progress"]), (2, "advanced"))

    def test_without_a_commit_the_rounds_own_words(self) -> None:
        run, _folder = self._run(_Commits(commit=False))
        self.assertEqual(run["status"], "done", run["log"][-6:])
        first = self._versions(run["id"])["versions"][1]
        self.assertEqual(first["kind"], "full review")
        self.assertEqual(first["message"]["title"], "Wrote notes.md")
        self.assertEqual(first["message"]["body"], ["notes.md says hello"])

    def test_a_version_saved_before_messages_still_lists(self) -> None:
        run, folder = self._run(_TwoAgents())
        from personalized_agent.agent.work.versions import VersionStore
        store = VersionStore(folder / "runs" / run["id"])
        index = store.index()
        for one in index["versions"]:
            one.pop("message", None)
        (store.root / "index.json").write_text(json.dumps(index), encoding="utf-8")
        manifest = store.manifest("v1")
        manifest.pop("message")
        manifest["label"] = "The contract approved"
        (store.root / "versions" / "v1.json").write_text(json.dumps(manifest), encoding="utf-8")
        listed = self._versions(run["id"])["versions"]
        self.assertTrue(all(one["message"]["title"] == one["label"] for one in listed))
        self.assertEqual(self.app.work.version(run["id"], "v1")["message"],
                         {"title": "The contract approved", "body": [], "files": None})


class ThePanelShowsVersionsTests(unittest.TestCase):
    """The run's Versions: the tree with its branch and head, View showing a
    version's files and to-do list, and Continue from here setting Continue
    to post from_version."""

    def setUp(self) -> None:
        from test_panels import _javascript_engine
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_the_tree_and_continue_from_here(self) -> None:
        import html
        import re
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        run = {"id": "run_2", "folder": "C:/w/agent_work/x", "alive": False,
               "status": "done", "continuable": False,
               "started_at": "2026-10-08T12:00:00", "instruction": "write the notes",
               "final": {}, "log": [], "plan": {"packages": {"mode": "ask", "requests": []}},
               "versions": {"count": 4, "head": "v4", "branch": "branch 2"}}

        def version(vid, parent, branch, kind, label):
            return {"id": vid, "n": int(vid[1:]), "parent": parent, "branch": branch,
                    "kind": kind, "label": label, "at": "2026-10-08T12:0%s:00" % vid[1:],
                    "files": 3, "granted": 0,
                    "summary": {"rounds": 1, "cards": 2, "cards_done": 1, "todo": 2,
                                "deck": kind == "finished", "phase": "rounds",
                                "status": "running"}}
        tree = {"run": "run_2", "head": "v4", "branch": "branch 2",
                "branches": ["main", "branch 2"], "alive": False, "status": "done",
                "versions": [version("v1", "", "main", "contract", "The contract approved"),
                             version("v2", "v1", "main", "round", "Round 1: The notes"),
                             version("v3", "v2", "main", "finished", "done"),
                             version("v4", "v1", "branch 2", "round", "Round 1 again")]}
        detail = {"id": "v2", "label": "Round 1: The notes", "kind": "round",
                  "branch": "main", "parent": "v1", "at": "2026-10-08T12:02:00",
                  "head": False, "goal": "notes.md says hello", "phase": "rounds",
                  "files": [{"path": "notes.md", "size": 5},
                            {"path": "runs/run_2/contract.json", "size": 300}],
                  "granted": [], "todo": {"next": ["write the summary"]}, "deck": True}
        stub = {"/api/work/runs": {"runs": [run]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {"minutes": 45, "tokens": 1000000,
                                                  "calls": 200}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/work/versions": tree, "/api/work/version": detail,
                "/api/work/continue": {"run": "run_2", "rolled_back": {
                    "version": "v1", "branch": "branch 3", "kept": []}}}
        steps = (
            "experienceState.unit='task-ver';await xpLoadRuns();"
            "out.continueBefore=!!document.querySelector('[data-xp-continue=\"run_2\"]');"
            "document.querySelector('[data-xp-versions=\"run_2\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "out.rows=[...document.querySelectorAll('.xp-version')].map(li=>"
            "  li.dataset.xpVersionRow+':'+li.style.marginLeft+':'"
            "  +li.classList.contains('xp-version-head'));"
            "out.headText=document.querySelector('.xp-version-head').textContent.replace(/\\s+/g,' ');"
            "out.v2Text=document.querySelector('[data-xp-version-row=\"v2\"]').textContent.replace(/\\s+/g,' ');"
            "out.fromButtons=[...document.querySelectorAll('[data-xp-version-from]')]"
            "  .map(b=>b.dataset.xpVersionFrom);"
            "document.querySelector('[data-xp-version-view=\"v2\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "out.view=document.querySelector('.xp-version-view').textContent.replace(/\\s+/g,' ');"
            "out.gets=window.__gets.filter(g=>g.includes('version'));"
            "document.querySelector('[data-xp-version-from=\"v1\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "out.form=document.querySelector('.xp-continue-row').textContent.replace(/\\s+/g,' ');"
            "out.stillOpen=document.querySelector('.xp-versions').open;"
            "document.querySelector('[data-xp-continue=\"run_2\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "out.posts=window.__posts;"
            "out.continueAfter=!!document.querySelector('[data-xp-continue=\"run_2\"]');"
            # A version's slides not made say why; a contract's say there are none.
            "const box=document.createElement('div');"
            "box.innerHTML=xpVersionView({id:'run_2'},{id:'v5',kind:'round',summary:{},"
            "slides:{status:'not made',why:'its answer was not valid <b>JSON</b>'}});"
            "out.notMade=box.textContent.replace(/\s+/g,' ');out.notMadeBold=!!box.querySelector('b');"
            "box.innerHTML=xpVersionView({id:'run_2'},{id:'v1',kind:'contract',summary:{},"
            "slides:{status:'skipped'}});out.skipped=box.textContent.replace(/\s+/g,' ');")
        harness = (
            "<script>window.__posts=[];window.__gets=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posts.push({p,body:JSON.parse(o.body||'{}')});"
            "else window.__gets.push(String(u));"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';const out={};"
            "const wait=()=>new Promise(r=>setTimeout(r,80));"
            "try{" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err,out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "the page never finished rendering")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        # A finished run offers no plain Continue.
        self.assertFalse(out["continueBefore"])
        # The tree: the branch indented under the version it began from,
        # right after it; the head marked.
        self.assertEqual(out["rows"], ["v1:0em:false", "v4:1.4em:true",
                                       "v2:0em:false", "v3:0em:false"])
        self.assertIn("head - Continue goes on from here", out["headText"])
        self.assertIn("round advanced", out["v2Text"])
        self.assertIn("1/2 cards done", out["v2Text"])
        self.assertIn("2 to do", out["v2Text"])
        # Continue from here on every version but the head of a finished run.
        self.assertEqual(out["fromButtons"], ["v1", "v2", "v3"])
        self.assertIn("notes.md", out["view"])
        self.assertIn("write the summary", out["view"])
        self.assertIn("notes.md says hello", out["view"])
        # Saved before versions had slides: said so, and where it stood in a
        # line - its goal and its to-do list - not a dump of its record.
        self.assertIn("No update slides for this version", out["view"])
        self.assertIn("Show the results deck as it was then", out["view"])
        self.assertTrue(any("/api/work/version?run=run_2&id=v2" in g for g in out["gets"]))
        # Picked, Continue says where it goes on from - and posts it.
        self.assertIn("from version v1", out["form"])
        self.assertIn("new branch", out["form"])
        self.assertTrue(out["stillOpen"])
        posts = [one for one in out["posts"] if one["p"] == "/api/work/continue"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["body"]["from_version"], "v1")
        self.assertEqual(posts[0]["body"]["run"], "run_2")
        self.assertIn("The slides of this version were not made - its answer was not valid "
                      "<b>JSON</b>", out["notMade"])
        self.assertFalse(out["notMadeBold"])
        self.assertIn("No slides for the contract's version - there is no work to show yet",
                      out["skipped"])
        self.assertIn("limits", posts[0]["body"])
        # Posted, the choice is spent: a finished run again offers no Continue.
        self.assertFalse(out["continueAfter"])



class ALooseFileInRunsTests(unittest.TestCase):
    """The work left a file loose in runs/ - a server's log, which a builder
    wrote there - and a rollback to a version holding it was refused whole:
    "another run's records are not restored". Another run's records are its
    folder under runs/; a loose file is the work's, and comes back with it."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        experience = self.app.state_heartbeat.experience
        made = experience.store.create_work_project("Loose")
        self.unit = experience.unit(made["unit_id"])
        self.folder = self.app.work.folder_of(self.unit)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.policy = self.app.work._runtime(self.unit, self.folder).policy

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_kept_and_given_back_and_another_runs_left_alone(self) -> None:
        from personalized_agent.agent.work.versions import (VersionStore, keep_files,
                                                            restore_files)
        run_dir = self.folder / "runs" / "run_mine"
        run_dir.mkdir(parents=True)
        store = VersionStore(run_dir, self.policy)
        (self.folder / "notes.md").write_text("v1")
        (self.folder / "runs" / "round7_server.log").write_text("served")
        other = self.folder / "runs" / "run_other"
        other.mkdir()
        (other / "record.json").write_text("theirs")
        files, _skipped = keep_files(store, self.folder, "run_mine")
        self.assertIn("runs/round7_server.log", files)
        self.assertNotIn("runs/run_other/record.json", files)
        manifest = {"files": files, "skipped": []}
        (self.folder / "notes.md").write_text("v2")
        (self.folder / "runs" / "round7_server.log").unlink()
        said = restore_files(store, manifest, self.folder, "run_mine", self.policy)
        self.assertEqual(said["refused"], [])
        self.assertEqual((self.folder / "notes.md").read_text(), "v1")
        self.assertEqual((self.folder / "runs" / "round7_server.log").read_text(), "served")
        self.assertEqual((other / "record.json").read_text(), "theirs")
        # A path no version may write - another run's - is left and said,
        # and the rest is still given back.
        manifest["files"] = dict(files, **{"runs/run_other/record.json": files["notes.md"]})
        (self.folder / "notes.md").write_text("v3")
        said = restore_files(store, manifest, self.folder, "run_mine", self.policy)
        self.assertEqual(len(said["refused"]), 1)
        self.assertIn("another run's records", said["refused"][0])
        self.assertEqual((self.folder / "notes.md").read_text(), "v1")
        self.assertEqual((other / "record.json").read_text(), "theirs")

if __name__ == "__main__":
    unittest.main()

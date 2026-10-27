"""RESUME POINTS (the user, 2026-10-24): after every round the reviewer
finds advanced, the run's files and where it stood are kept - the latest
only - so that a run cut off (the application closed) goes on from there
and loses at most the round it was in. No version: not listed, no slides,
no checkpoint of the full review. What nothing holds any more is removed
from the store, never the work's own files."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from test_work_runs import _TwoAgents
from test_work_versions import _Fails, _RunsBase

from personalized_agent.agent.work import versions as versions_module
from personalized_agent.agent.work.versions import VersionStore


class AResumePointAfterEachRoundThatAdvancedTests(_RunsBase):

    def test_the_latest_kept_and_left_behind_by_a_version(self) -> None:
        run, folder = self._run(_TwoAgents(rounds_to_complete=3))
        self.assertEqual(run["status"], "done", run["log"][-6:])
        saved = [one for one in run["log"] if one.get("what") == "resume point saved"]
        self.assertEqual([one.get("round") for one in saved], [1, 2],
                         "after the rounds that advanced, not after the one a full review kept")
        tree = self._versions(run["id"])
        self.assertEqual([one["kind"] for one in tree["versions"]],
                         ["contract", "full review", "finished"], "no version of its own")
        store = VersionStore(folder / "runs" / run["id"])
        self.assertEqual(store.resume_points(), {}, "a version saved since leaves it behind")

    def test_cut_off_goes_on_from_the_latest_round_that_advanced(self) -> None:
        from personalized_agent.agent.work.runner import INTERRUPTED
        run, folder = self._run(_TwoAgents(rounds_to_complete=99, full_review="never"),
                                limits={"calls": 14, "minutes": 0, "tokens": 0})
        advanced = [one for one in run["plan"].get("rounds") or []
                    if one.get("progress") == "advanced"]
        self.assertTrue(advanced, run["log"][-6:])
        store = VersionStore(folder / "runs" / run["id"])
        point = store.resume_point()
        self.assertIsNotNone(point)
        self.assertEqual(point["round"], advanced[-1]["n"])
        # The application closed in the middle of the next round: its half
        # written changes in the folder, the run marked interrupted.
        (folder / "notes.md").write_text("half of a round", encoding="utf-8")
        (folder / "stray.md").write_text("never reviewed", encoding="utf-8")
        self.app.db.execute("UPDATE work_runs SET status = ? WHERE id = ?",
                            (INTERRUPTED, run["id"]))
        entry = self.app.work.runs(self.unit["id"])[0]
        self.assertEqual(entry["milestone"]["resume"], True)
        self.assertIn(f"after round {advanced[-1]['n']}", entry["milestone"]["label"])
        said, _after = self._continue(run["id"], _Fails())
        rolled = said["rolled_back"]
        self.assertTrue(rolled["resume_point"])
        self.assertEqual(rolled["round"], advanced[-1]["n"])
        self.assertEqual((folder / "notes.md").read_text(encoding="utf-8"), "hello")
        self.assertFalse((folder / "stray.md").exists())
        self.assertEqual(self._versions(run["id"])["head"], "v1", "the head and its line stay")



class AProgressPointEveryTenMinutesOfWorkTests(_RunsBase):
    """PROGRESS POINTS (the user, 2026-10-26): every ten minutes of the run's
    own time, in the middle of a round too - a round takes about 27 minutes -
    the latest only, not a version, shown as not checked."""

    def _points_at_every_tool(self, models, **kwargs):
        from personalized_agent.agent.work import runner as runner_module
        with mock.patch.object(runner_module, "PROGRESS_POINT_MINUTES", 0):
            return self._run(models, **kwargs)

    def test_in_the_middle_of_a_round_after_a_tool_of_the_work_agents(self) -> None:
        run, folder = self._points_at_every_tool(
            _TwoAgents(rounds_to_complete=99, full_review="never"),
            limits={"calls": 14, "minutes": 0, "tokens": 0})
        saved = [one for one in run["log"] if one.get("what") == "resume point saved"]
        midway = [one for one in saved if str(one.get("label", "")).startswith("in round ")]
        self.assertTrue(midway, run["log"][-8:])
        self.assertRegex(midway[0]["label"], r"^in round 1, at \d\d:\d\d$")
        progress = self._versions(run["id"])["progress"]
        self.assertIsNotNone(progress, "shown beside the versions")
        self.assertEqual(set(progress), {"label", "at", "round", "in_round", "files"})
        kinds = [one["kind"] for one in self._versions(run["id"])["versions"]]
        self.assertNotIn("progress", kinds, "not a version")
        # SAID IN THE LIVE SESSION as each is saved (the user, 2026-10-27).
        events = self.app.work.live(run["id"])["events"]
        saved = [one for one in events if one["kind"] == "saved"]
        self.assertTrue(saved, [one["kind"] for one in events][-12:])
        self.assertTrue(saved[0]["text"].startswith("Saved a progress point - "), saved[0]["text"])

    def test_kept_off_the_works_thread(self) -> None:
        # LIGHT (the user, 2026-10-27): its files are kept by a thread of
        # their own - the tool that asked for it returns at once.
        from personalized_agent.agent.work import runner as runner_module
        import threading as _threading
        started = _threading.Event()
        release = _threading.Event()

        def slow_keep(*args, **kwargs):
            started.set()
            release.wait(10)
            return {}, []
        run, _folder = self._run(_TwoAgents(rounds_to_complete=99, full_review="never"),
                                 limits={"calls": 2, "minutes": 0, "tokens": 0})
        live = runner_module.Run.__new__(runner_module.Run)
        live.alone, live.id, live.plan, live.status, live.final = False, "run_x", {}, "running", {}
        live.instruction, live.folder = "", Path(tempfile.mkdtemp(prefix="point-"))
        live.versions = mock.Mock()
        live.versions.index.return_value = {"head": "v1"}
        live.versions.put_json.return_value = "0" * 64
        live.versions.save_resume.return_value = {"label": "in round 1, at 10:00"}
        live.owner = mock.Mock()
        live.runtime = mock.Mock()
        live.scope = "s"
        live._minutes = lambda: 0.0
        notes = []
        live._note = lambda what, **said: notes.append(what)
        with mock.patch.object(runner_module, "keep_files", slow_keep),                 mock.patch.object(runner_module, "keep_tree", return_value={}),                 mock.patch.object(runner_module, "keep_granted", return_value={}):
            began = time.monotonic()
            live._save_point("in round 1, at 10:00", {"round": 0, "in_round": 1}, behind=True)
            self.assertLess(time.monotonic() - began, 1, "it did not wait for the files")
            self.assertTrue(started.wait(5))
            # One at a time: a second, while the first is kept, is not begun.
            live._save_point("in round 1, at 10:10", {"round": 0, "in_round": 1}, behind=True)
            release.set()
            live._saving.join(10)
        self.assertEqual(notes, ["resume point saved"])
        live.versions.save_resume.assert_called_once()

    def test_not_every_tool_but_every_ten_minutes(self) -> None:
        run, _folder = self._run(_TwoAgents(rounds_to_complete=99, full_review="never"),
                                 limits={"calls": 14, "minutes": 0, "tokens": 0})
        midway = [one for one in run["log"] if one.get("what") == "resume point saved"
                  and str(one.get("label", "")).startswith("in round ")]
        self.assertEqual(midway, [], "a test run lasts seconds, not ten minutes")

    def _cut_off_midway(self, run, folder, did):
        """The latest point made one in the middle of the next round."""
        store = VersionStore(folder / "runs" / run["id"])
        point = store.resume_point()
        state = store.get_json(point["state"])
        done = int(state["plan"].get("round") or 0)
        state["plan"]["resumed_mid_round"] = {"round": done + 1, "did": did, "tools": len(did)}
        store.save_resume(f"in round {done + 1}, at 10:00", point["files"], {}, [],
                          store.put_json(state), {"round": done, "in_round": done + 1})
        return done

    def test_cut_off_goes_on_from_it_and_is_told_what_it_had_done(self) -> None:
        from personalized_agent.agent.work.runner import INTERRUPTED
        run, folder = self._run(_TwoAgents(rounds_to_complete=99, full_review="never"),
                                limits={"calls": 14, "minutes": 0, "tokens": 0})
        done = self._cut_off_midway(run, folder, ["read_file(notes.md)", "write_file(notes.md)"])
        self.app.db.execute("UPDATE work_runs SET status = ? WHERE id = ?",
                            (INTERRUPTED, run["id"]))
        entry = self.app.work.runs(self.unit["id"])[0]
        self.assertEqual(entry["milestone"]["label"], f"in round {done + 1}, at 10:00")
        agents = _TwoAgents(rounds_to_complete=1)
        said, after = self._continue(run["id"], agents)
        self.assertTrue(said["rolled_back"]["resume_point"])
        # Each turn of a round carries the round's first message again.
        told = {str(request.messages[0]["content"]) for request in agents.asked
                if "THIS ROUND WAS CUT OFF IN ITS MIDDLE" in str(request.messages[0]["content"])}
        self.assertEqual(len(told), 1, "told once, in the round it was cut off in")
        self.assertIn("write_file(notes.md)", told.pop())
        self.assertEqual(min(one["n"] for one in after["plan"]["rounds"] if one["n"] > done),
                         done + 1, "that round, begun again")

    def test_a_stopped_run_continued_from_it_by_choice(self) -> None:
        run, folder = self._run(_TwoAgents(rounds_to_complete=99, full_review="never"),
                                limits={"calls": 14, "minutes": 0, "tokens": 0})
        self._cut_off_midway(run, folder, ["write_file(notes.md)"])
        (folder / "stray.md").write_text("after the point", encoding="utf-8")
        said, _after = self._continue(run["id"], _Fails(), from_version="progress")
        self.assertTrue(said["rolled_back"]["resume_point"])
        self.assertFalse((folder / "stray.md").exists())

    def test_none_says_so(self) -> None:
        run, _folder = self._run(_TwoAgents())
        self.app.work.models = _Fails()
        said = self.app.work.continue_run(run["id"], self.unit, {"calls": 0, "minutes": 0, "tokens": 0},
                                          from_version="progress")
        self.assertIn("no progress point", said.get("error", ""))



class _EndsInRoundTwo(_TwoAgents):
    """Round 1 goes as usual; in round 2's work, the call ends - the user
    stopping the run at once while it goes, or the model failing."""

    def __init__(self, works, at_once):
        super().__init__(rounds_to_complete=99, full_review="never")
        self.works, self.at_once, self.rounds_seen = works, at_once, 0

    def invoke(self, request, role=""):
        # The work agent's first call after round 1 was reviewed.
        if ("You review a work agent" not in request.system
                and self.reviewed_rounds >= 1 and len(request.messages) == 1):
            run_id = (request.metadata or {}).get("run")
            if self.at_once:
                self.works.stop(run_id, now=True)
                raise RuntimeError("the call was ended: the run was stopped")
            raise RuntimeError("the provider went away")
        return super().invoke(request, role)


class WhatDidNotStopCleanlyGoesBackTests(_RunsBase):
    """THE USER'S RULE (2026-10-26): a run that stopped cleanly - wrapped up at
    Stop or at a limit - goes on from where it stopped; one that did not -
    stopped at once, failed, or cut off by the application closing - goes
    back to its last save point, and what came after is given up."""

    def _ends_in_round_two(self, at_once):
        run, folder = self._run(_EndsInRoundTwo(self.app.work, at_once),
                                limits={"calls": 0, "minutes": 0, "tokens": 0})
        point = VersionStore(folder / "runs" / run["id"]).resume_point()
        self.assertIsNotNone(point, run["log"][-6:])
        return run, folder, point

    def test_stopped_at_once_in_a_call_is_not_failed_and_goes_back(self) -> None:
        run, folder, point = self._ends_in_round_two(at_once=True)
        self.assertEqual(run["status"], "stopped at once", run["log"][-4:])
        entry = self.app.work.runs(self.unit["id"])[0]
        self.assertTrue(entry["continuable"], "it can be resumed")
        (folder / "stray.md").write_text("after the save point", encoding="utf-8")
        said, _after = self._continue(run["id"], _Fails())
        self.assertTrue(said["rolled_back"]["resume_point"])
        self.assertEqual(said["rolled_back"]["round"], point["round"])
        self.assertFalse((folder / "stray.md").exists(), "what came after is given up")

    def test_failed_goes_back_too(self) -> None:
        run, folder, point = self._ends_in_round_two(at_once=False)
        self.assertEqual(run["status"], "failed", run["log"][-4:])
        self.assertTrue(self.app.work.runs(self.unit["id"])[0]["continuable"])
        said, _after = self._continue(run["id"], _Fails())
        self.assertTrue(said["rolled_back"]["resume_point"])

    def test_a_clean_stop_goes_on_from_where_it_stopped(self) -> None:
        from personalized_agent.agent.work.runner import GOES_BACK, NOT_FINISHED
        self.assertNotIn(NOT_FINISHED, GOES_BACK)
        run, folder = self._run(_TwoAgents(rounds_to_complete=99, full_review="never"),
                                limits={"calls": 14, "minutes": 0, "tokens": 0})
        self.assertEqual(run["status"], NOT_FINISHED, "a limit wraps up")
        (folder / "kept.md").write_text("after the save point", encoding="utf-8")
        said, _after = self._continue(run["id"], _Fails())
        self.assertNotIn("rolled_back", said)
        self.assertTrue((folder / "kept.md").exists(), "nothing given up")


class TheStoreKeepsOnlyWhatIsHeldTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="resume-points-"))
        self.store = VersionStore(self.dir)

    def _files(self, **contents):
        return {name: {"sha": self.store.put(text.encode("utf-8")), "size": len(text)}
                for name, text in contents.items()}

    def test_an_old_resume_points_own_copies_removed_a_versions_kept(self) -> None:
        version_files = self._files(**{"plan.py": "first"})
        state = self.store.put_json({"plan": {"round": 0}, "tree": {}})
        self.store.save("contract", "The contract", version_files, {}, [], state, {})
        round_five = self._files(**{"plan.py": "round five"})
        state5 = self.store.put_json({"plan": {"round": 5}, "tree": {}})
        # Collected at every resume point here: in a run, at most each half
        # hour (COLLECT_EVERY_SECONDS) - a progress point is kept light.
        with mock.patch.object(versions_module, "COLLECT_GRACE_SECONDS", -1),                 mock.patch.object(versions_module, "COLLECT_EVERY_SECONDS", 0):
            self.store.save_resume("after round 5", round_five, {}, [], state5, {"round": 5})
            round_six = self._files(**{"plan.py": "round six"})
            state6 = self.store.put_json({"plan": {"round": 6}, "tree": {}})
            self.store.save_resume("after round 6", round_six, {}, [], state6, {"round": 6})
        exists = lambda sha: self.store._object(sha).is_file()
        self.assertFalse(exists(round_five["plan.py"]["sha"]), "no one holds it any more")
        self.assertFalse(exists(state5))
        self.assertTrue(exists(version_files["plan.py"]["sha"]), "the version's copy stays")
        self.assertTrue(exists(round_six["plan.py"]["sha"]))
        self.assertEqual(self.store.resume_point()["round"], 6)

    def test_light_collected_at_most_each_half_hour(self) -> None:
        state = self.store.put_json({"plan": {}, "tree": {}})
        self.store.save("contract", "The contract", {}, {}, [], state, {})
        with mock.patch.object(self.store, "collect", wraps=self.store.collect) as collected:
            for n in range(3):
                self.store.save_resume(f"in round {n}", {}, {}, [], state, {"round": n})
        self.assertEqual(collected.call_count, 1, "the first, then not for half an hour")

    def test_a_file_unchanged_is_kept_without_reading_it_or_the_store(self) -> None:
        folder = self.dir / "work"
        folder.mkdir()
        (folder / "a.txt").write_text("one", encoding="utf-8")
        files, _ = versions_module.keep_files(self.store, folder, "run_x")
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("read")),                 mock.patch.object(Path, "is_file", side_effect=AssertionError("looked")):
            again, _ = versions_module.keep_files(self.store, folder, "run_x")
        self.assertEqual(again, files)

    def test_nothing_new_and_nothing_with_an_unreadable_version_removed(self) -> None:
        state = self.store.put_json({"plan": {}, "tree": {}})
        self.store.save("contract", "The contract", {}, {}, [], state, {})
        loose = self.store.put(b"just written for slides")
        self.assertEqual(self.store.collect(), 0, "within its grace")
        self.assertTrue(self.store._object(loose).is_file())
        old = time.time() - 4000
        os.utime(self.store._object(loose), (old, old))
        (self.store.root / "versions" / "v1.json").write_text("{damaged", encoding="utf-8")
        self.assertEqual(self.store.collect(), 0, "a version that cannot be read: nothing removed")
        (self.store.root / "versions" / "v1.json").unlink()
        self.assertEqual(self.store.collect(), 1)

    def test_left_behind_when_the_head_moves(self) -> None:
        state = self.store.put_json({"plan": {}, "tree": {}})
        self.store.save("contract", "The contract", {}, {}, [], state, {})
        self.store.save_resume("after round 1", {}, {}, [], state, {"round": 1})
        self.assertIsNotNone(self.store.resume_point())
        self.store.save("full review", "Round 2", {}, {}, [], state, {})
        self.assertIsNone(self.store.resume_point())
        self.store.save_resume("after round 3", {}, {}, [], state, {"round": 3})
        self.store.move_head("v1")
        self.assertIsNone(self.store.resume_point(), "continued from an older version")
        self.assertEqual(json.loads((self.store.root / "index.json").read_text())["head"], "v1")


if __name__ == "__main__":
    unittest.main()

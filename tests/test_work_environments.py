"""INSTALLED ENVIRONMENTS LEFT OUT OF EVERY WALK, AND WHAT CODE CHANGED
HEARD AS IT CHANGES (the user, 2026-10-29): an environment in a work's
folder - told by what it holds, never by a name a work might use for its
own - is left out of what code changed, of versions and progress points,
of the tests' copy; a rollback leaves it as it is and says what changed in
it; the reviewers are told it is there."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from personalized_agent.agent.work import environments as envs
from personalized_agent.agent.work.changes import WATCHING, Watch


def _venv(root: Path, packages=("numpy-1.26.0", "six-1.16.0"), files=30) -> Path:
    """A virtual environment as venv makes one (fictional packages)."""
    root.mkdir(parents=True)
    (root / "pyvenv.cfg").write_text("home = C:/Python312\n", encoding="utf-8")
    site = root / "Lib" / "site-packages"
    site.mkdir(parents=True)
    for one in packages:
        (site / f"{one}.dist-info").mkdir()
        package = site / one.split("-")[0]
        package.mkdir()
        for n in range(files):
            (package / f"m{n}.py").write_text("x = 1\n", encoding="utf-8")
    return root


class _Folder(unittest.TestCase):

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="envs-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.folder, ignore_errors=True))
        (self.folder / "analysis.py").write_text("print(1)\n", encoding="utf-8")
        # A folder of the work's own named like an environment: kept.
        (self.folder / "env").mkdir()
        (self.folder / "env" / "settings.toml").write_text("a = 1\n", encoding="utf-8")
        _venv(self.folder / "package" / "tryout" / "venv1")


class TellingThemTests(_Folder):

    def test_by_what_they_hold_never_by_a_name_a_work_uses(self) -> None:
        self.assertTrue(envs.is_environment(self.folder / "package" / "tryout" / "venv1"))
        self.assertTrue(envs.is_environment(self.folder / "x" / "site-packages"))
        self.assertFalse(envs.is_environment(self.folder / "env"), "a work's own 'env'")
        self.assertFalse(envs.is_environment(self.folder / "build"))

    def test_found_with_their_packages_never_walked_into(self) -> None:
        found = envs.found_in(self.folder)
        self.assertEqual(found, [{"path": "package/tryout/venv1",
                                  "packages": ["numpy-1.26.0", "six-1.16.0"], "count": 2}])
        self.assertIn("package/tryout/venv1 (2 packages installed)", envs.said(found))

    def test_a_list_of_files_shows_them(self) -> None:
        files = ["a.py", "t/venv1/pyvenv.cfg", "t/venv1/Lib/site-packages/six/m.py",
                 "c/conda-meta/history", "c/lib/x.py", "deep/site-packages/q.py"]
        roots = envs.roots_in(files)
        self.assertEqual(roots, {"t/venv1", "c"})
        self.assertEqual([one for one in files if envs.inside(one, roots)], files[1:])


class TheWalksLeaveThemOutTests(_Folder):

    def test_versions_and_the_tests_key(self) -> None:
        from personalized_agent.agent.work.tools import _files_under
        from personalized_agent.agent.work.versions import work_files
        kept, _skipped = work_files(self.folder, "run_x")
        self.assertEqual(sorted(kept), ["analysis.py", "env/settings.toml"])
        under = sorted(p.relative_to(self.folder).as_posix() for p in _files_under(self.folder))
        self.assertEqual(under, ["analysis.py", "env/settings.toml"])

    def test_a_rollback_leaves_them_as_they_are_and_says_what_changed(self) -> None:
        from personalized_agent.agent.work.versions import (VersionStore, environments_of,
                                                            keep_files, restore_files)
        store = VersionStore(self.folder / "runs" / "run_x" / ".versions")
        files, skipped = keep_files(store, self.folder, "run_x")
        state = store.put_json({"plan": {}, "tree": {}})
        store.save("contract", "The contract", files, {}, skipped, state, {},
                   details={"environments": environments_of(self.folder, "run_x")})
        self.assertNotIn("environments", store.index()["versions"][0],
                         "not on its line in the tree")
        # Since: the work changed, and a package installed in the environment.
        (self.folder / "analysis.py").write_text("print(2)\n", encoding="utf-8")
        site = self.folder / "package" / "tryout" / "venv1" / "Lib" / "site-packages"
        (site / "kelp-0.1.dist-info").mkdir()
        policy = mock.Mock(write_root=str(self.folder), extra_writable=[], write_roots=None)
        with mock.patch("personalized_agent.agent.work.versions._work_target",
                        side_effect=lambda folder, run_id, relative, policy, roots:
                        Path(folder) / relative):
            done = restore_files(store, store.manifest("v1"), self.folder, "run_x", policy)
        self.assertEqual((self.folder / "analysis.py").read_text(encoding="utf-8"), "print(1)\n")
        self.assertTrue((site / "kelp-0.1.dist-info").is_dir(), "left as it is")
        self.assertTrue((site / "six" / "m0.py").is_file())
        self.assertEqual(done["environments"], [{
            "path": "package/tryout/venv1", "packages": 3, "left_as_is": True,
            "added_since": ["kelp-0.1"], "gone_since": []}])

    def test_a_version_from_before_holding_them_does_not_write_them_back(self) -> None:
        from personalized_agent.agent.work.versions import VersionStore, restore_files
        store = VersionStore(self.folder / "runs" / "run_x" / ".versions")
        sha = store.put(b"x = 1\n")
        files = {"analysis.py": {"sha": store.put(b"print(1)\n"), "size": 9},
                 "old/venv9/pyvenv.cfg": {"sha": sha, "size": 6},
                 "old/venv9/Lib/site-packages/six/m0.py": {"sha": sha, "size": 6}}
        state = store.put_json({"plan": {}, "tree": {}})
        store.save("contract", "Before", files, {}, [], state, {})
        policy = mock.Mock(write_root=str(self.folder), extra_writable=[], write_roots=None)
        with mock.patch("personalized_agent.agent.work.versions._work_target",
                        side_effect=lambda folder, run_id, relative, policy, roots:
                        Path(folder) / relative):
            restore_files(store, store.manifest("v1"), self.folder, "run_x", policy)
        self.assertFalse((self.folder / "old").exists(), "not written back")


class WhatCodeChangedTests(_Folder):

    def _tools(self):
        from personalized_agent.agent.work.tools import WorkTools
        tools = WorkTools.__new__(WorkTools)
        tools.runtime = mock.Mock()
        tools.runtime.policy.write_root = str(self.folder)
        tools.runtime.policy.write_roots = None
        return tools

    @unittest.skipUnless(WATCHING, "no watch here")
    def test_heard_while_it_runs_environments_and_scratch_left_out(self) -> None:
        tools = self._tools()
        before = tools._changes_begin()
        self.assertIsInstance(before, Watch, "watched, not walked")
        with mock.patch.object(tools, "_look_over", side_effect=AssertionError("walked")):
            (self.folder / "result.csv").write_text("a,b\n", encoding="utf-8")
            (self.folder / "analysis.py").unlink()
            venv = self.folder / "package" / "tryout" / "venv1" / "Lib" / "site-packages"
            (venv / "six" / "m0.py").write_text("x = 2\n", encoding="utf-8")
            (self.folder / ".agent_runtime").mkdir()
            (self.folder / ".agent_runtime" / "scratch.txt").write_text("s", encoding="utf-8")
            time.sleep(0.2)
            touched = tools._changes_end(before)
        names = sorted(Path(one).relative_to(self.folder).as_posix() for one in touched)
        self.assertEqual(names, ["analysis.py", "result.csv"])

    def test_without_a_watch_walked_environments_left_out(self) -> None:
        tools = self._tools()
        with mock.patch("personalized_agent.agent.work.changes.WATCHING", False):
            before = tools._changes_begin()
        self.assertIsInstance(before, dict)
        self.assertEqual(sorted(Path(p).relative_to(self.folder).as_posix() for p in before),
                         ["analysis.py", "env/settings.toml"])
        (self.folder / "result.csv").write_text("a,b\n", encoding="utf-8")
        touched = tools._changes_end(before)
        self.assertEqual([Path(one).name for one in touched], ["result.csv"])


class TheBuilderIsToldWhereTheyGoTests(unittest.TestCase):

    def test_in_its_rules(self) -> None:
        from personalized_agent.agent.work.runner import ENVIRONMENT_RULES
        self.assertIn("NEVER IN WHAT THE WORK DELIVERS", ENVIRONMENT_RULES)
        self.assertIn(".agent_runtime/envs", ENVIRONMENT_RULES)


if __name__ == "__main__":
    unittest.main()

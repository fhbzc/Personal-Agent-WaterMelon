"""THE WORK FASTER, ITS JUDGEMENT UNTOUCHED (the user, 2026-10-24):

- edit_file changes part of a file - each old text found exactly once, all
  the edits or none - instead of the whole file written again;
- run_tests gives the result of tests that already ran when EXACTLY the same
  tests would run on exactly the same files - every byte of every file, the
  same arguments, packages and Python - and runs them otherwise, or when
  asked to (fresh);
- the reading tools run side by side, at most four, and one at a time while
  the computer is busy; everything else alone;
- what was learned for the next cards' criteria is looked up while the
  reviewer reviews the round, and used only if the card is still what it was.
"""

from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent.work import parallel
from personalized_agent.agent.work.parallel import ToolGate
from personalized_agent.agent.work.runner import Run, _side_by_side
from personalized_agent.agent.work.tools import TESTS_CODE, TestRuns, WorkTools
from personalized_agent.models.base import ToolCall
from personalized_agent.runtime.sandbox import ScriptResult


class _WithTools(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        unit = {"id": "unit_speed", "name": "A pledge form", "kind": "task"}
        self.folder = Path(self.app.work.folder_of(unit))
        self.runtime = self.app.work._runtime(unit, self.folder)
        self.runtime.mkdir(self.folder)
        self.tools = WorkTools(self.runtime)

    def tearDown(self) -> None:
        self.agent.cleanup()


class EditFileTests(_WithTools):

    def test_part_changed_in_place(self) -> None:
        path = self.folder / "form.py"
        path.write_text("def total(a, b):\n    return a - b\n\nNAME = 'form'\n", encoding="utf-8")
        said = self.tools.call("edit_file", {"path": str(path), "edits": [
            {"old_text": "return a - b", "new_text": "return a + b"},
            {"old_text": "NAME = 'form'", "new_text": "NAME = 'pledge form'"}]})
        self.assertEqual(said.get("edits"), 2, said)
        self.assertEqual(path.read_text(encoding="utf-8"),
                         "def total(a, b):\n    return a + b\n\nNAME = 'pledge form'\n")

    def test_all_or_none(self) -> None:
        path = self.folder / "form.py"
        before = "x = 1\nx = 1\ny = 2\n"
        path.write_text(before, encoding="utf-8")
        twice = self.tools.call("edit_file", {"path": str(path), "edits": [
            {"old_text": "y = 2", "new_text": "y = 3"},
            {"old_text": "x = 1", "new_text": "x = 5"}]})
        self.assertIn("found 2 times", twice["error"])
        missing = self.tools.call("edit_file", {"path": str(path), "edits": [
            {"old_text": "z = 9", "new_text": "z = 0"}]})
        self.assertIn("found 0 times", missing["error"])
        self.assertEqual(path.read_text(encoding="utf-8"), before, "nothing was changed")

    def test_windows_line_ends_kept(self) -> None:
        path = self.folder / "notes.txt"
        path.write_bytes(b"first\r\nsecond\r\n")
        self.tools.call("edit_file", {"path": str(path), "edits": [
            {"old_text": "first\nsecond", "new_text": "one\ntwo"}]})
        self.assertEqual(path.read_bytes(), b"one\r\ntwo\r\n")

    def test_only_where_it_may_write(self) -> None:
        outside = Path(self.agent.root) / "elsewhere.txt"
        outside.write_text("keep me", encoding="utf-8")
        said = self.tools.call("edit_file", {"path": str(outside), "edits": [
            {"old_text": "keep", "new_text": "lose"}]})
        self.assertTrue(said.get("error") or said.get("boundary"), said)
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep me")
        self.assertIn("edit_file", {one.name for one in self.tools.specs(writing=True)})
        self.assertNotIn("edit_file", {one.name for one in self.tools.specs(writing=False)})


class RunTestsTests(_WithTools):

    def setUp(self) -> None:
        super().setUp()
        (self.folder / "test_form.py").write_text("def test_one():\n    assert 1\n",
                                                  encoding="utf-8")
        self.runs = []

        def ran(code, timeout=0, sys_path=None):
            self.runs.append(code)
            return ScriptResult(ok=True, returncode=0, stdout="1 passed", stderr="",
                                isolation="test")
        self.tools.runtime.run_python_code = ran
        self.tools.label = "the work agent"

    def test_the_same_tests_on_the_same_files_run_once(self) -> None:
        first = self.tools.call("run_tests", {"folder": str(self.folder), "args": ["-q"]})
        self.assertEqual(first["stdout"], "1 passed")
        self.assertNotIn("reused", first)
        again = self.tools.call("run_tests", {"folder": str(self.folder), "args": ["-q"]})
        self.assertEqual(len(self.runs), 1, "not run again")
        self.assertEqual(again["reused"]["by"], "the work agent")
        # Said first, before what the run printed: it is not a new run, and
        # the tests can be run again.
        self.assertEqual(next(iter(again)), "not_a_new_run")
        self.assertIn("NOT A NEW RUN", again["not_a_new_run"])
        self.assertIn("the work agent", again["not_a_new_run"])
        self.assertIn("fresh=true", again["not_a_new_run"])
        self.assertEqual(again["stdout"], "1 passed")
        # The reviewer, sharing the run's record, is given it too.
        reviewer = WorkTools(self.runtime)
        reviewer.test_runs = self.tools.test_runs
        reviewer.runtime.run_python_code = self.tools.runtime.run_python_code
        self.assertIn("reused", reviewer.call("run_tests", {"folder": str(self.folder),
                                                            "args": ["-q"]}))
        self.assertEqual(len(self.runs), 1)

    def test_run_again_when_anything_differs(self) -> None:
        self.tools.call("run_tests", {"folder": str(self.folder)})
        # One byte of one file.
        (self.folder / "test_form.py").write_text("def test_one():\n    assert 2\n",
                                                  encoding="utf-8")
        self.assertNotIn("reused", self.tools.call("run_tests", {"folder": str(self.folder)}))
        # A file added - a conftest, a data file.
        (self.folder / "data.csv").write_text("a,b\n", encoding="utf-8")
        self.tools.call("run_tests", {"folder": str(self.folder)})
        # Other arguments.
        self.tools.call("run_tests", {"folder": str(self.folder), "args": ["-x"]})
        # Asked to.
        self.tools.call("run_tests", {"folder": str(self.folder), "args": ["-x"], "fresh": True})
        self.assertEqual(len(self.runs), 5)

    def test_printing_alone_does_not_make_tests_different(self) -> None:
        first = self.tools.call("run_tests", {"folder": str(self.folder),
                                              "args": ["-q", "-p", "no:cacheprovider"]})
        self.assertEqual(first["ran_with"][:3], ["-q", "-rA", "--durations=50"],
                         "the whole report, whoever asks")
        self.assertIn("-rA", self.runs[0])
        for printing in (["-q", "--durations=10"], ["-rA", "--durations", "5"], ["-vv"],
                         ["--tb=short", "-rfE"], []):
            again = self.tools.call("run_tests", {"folder": str(self.folder), "args": printing})
            self.assertIn("reused", again, printing)
        self.assertEqual(len(self.runs), 1)
        # What chooses or runs the tests is told apart.
        for choosing in (["-x"], ["-k", "pledge"], ["test_form.py"], ["-s"]):
            self.assertNotIn("reused", self.tools.call(
                "run_tests", {"folder": str(self.folder), "args": choosing}), choosing)
        self.assertEqual(len(self.runs), 5)

    def test_failures_shown_as_asked(self) -> None:
        self.tools.runtime.run_python_code = lambda code, timeout=0, sys_path=None: (
            self.runs.append(code) or ScriptResult(ok=False, returncode=1,
                                                   stdout="1 failed", stderr="",
                                                   isolation="test"))
        folder = str(self.folder)
        self.tools.call("run_tests", {"folder": folder, "args": ["--tb=short"]})
        self.assertIn("--tb=short", self.runs[0])
        # The same failures shown the same way: given again - other printing too.
        self.assertIn("reused", self.tools.call("run_tests", {"folder": folder,
                                                              "args": ["--tb", "short", "-v"]}))
        # Asked with more of a failure: run, so it is shown.
        self.assertNotIn("reused", self.tools.call("run_tests", {"folder": folder,
                                                                 "args": ["--tb=long"]}))
        self.assertNotIn("reused", self.tools.call("run_tests", {"folder": folder,
                                                                 "args": ["--tb=long", "-l"]}))
        self.assertIn("-l", self.runs[-1])
        self.assertIn("reused", self.tools.call("run_tests", {"folder": folder,
                                                              "args": ["--showlocals",
                                                                       "--tb=long"]}))
        self.assertEqual(len(self.runs), 3)

    def test_a_pass_is_given_whatever_failures_would_show(self) -> None:
        folder = str(self.folder)
        self.tools.call("run_tests", {"folder": folder})
        for asked in (["--tb=long"], ["-l"], ["--tb=no"]):
            self.assertIn("reused", self.tools.call("run_tests", {"folder": folder,
                                                                  "args": asked}), asked)
        self.assertEqual(len(self.runs), 1)

    def test_a_run_cut_off_is_not_given_again(self) -> None:
        self.tools.runtime.run_python_code = lambda code, timeout=0, sys_path=None: (
            self.runs.append(code) or ScriptResult(ok=False, returncode=-1, stdout="",
                                                   stderr="", isolation="test",
                                                   timed_out=True))
        self.tools.call("run_tests", {"folder": str(self.folder)})
        self.tools.call("run_tests", {"folder": str(self.folder)})
        self.assertEqual(len(self.runs), 2)

    def test_the_key_reads_content_not_times(self) -> None:
        path = self.folder / "test_form.py"
        key = TestRuns.key(self.folder, [], None)
        later = time.time() + 100
        import os
        os.utime(path, (later, later))
        self.assertEqual(TestRuns.key(self.folder, [], None), key, "touched, the same")
        # Caches and the runtime's scratch are not the work.
        (self.folder / "__pycache__").mkdir()
        (self.folder / "__pycache__" / "x.pyc").write_bytes(b"\0")
        self.assertEqual(TestRuns.key(self.folder, [], None), key)
        # A run's own folder - its state, the reviewer's checks - changes as
        # the run goes: not the work (try 6).
        own = self.folder / "runs" / "run_0123456789abcdef" / "checks"
        own.mkdir(parents=True)
        (own / "scratch.db").write_bytes(b"changes all the time")
        self.assertEqual(TestRuns.key(self.folder, [], None), key)
        # The agent's own folder of that name, elsewhere, is the work.
        (self.folder / "runs" / "smoke").mkdir()
        (self.folder / "runs" / "smoke" / "check.py").write_text("x = 1\n", encoding="utf-8")
        self.assertNotEqual(TestRuns.key(self.folder, [], None), key)

    def test_the_copy_leaves_a_runs_own_folder_out(self) -> None:
        import subprocess
        import sys
        import tempfile
        src = Path(tempfile.mkdtemp(prefix="pa-copy-"))
        (src / "runs" / "run_0123456789abcdef").mkdir(parents=True)
        (src / "runs" / "run_0123456789abcdef" / "state.json").write_text("{}", encoding="utf-8")
        (src / "runs" / "smoke").mkdir()
        (src / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
        dst = src.parent / (src.name + "-copy")
        code = TESTS_CODE.format(src=str(src), dst=str(dst), args=repr(["-q", "--co"]),
                                 skip=repr(("__pycache__",)))
        subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=120)
        self.assertTrue((dst / "test_x.py").is_file())
        self.assertTrue((dst / "runs" / "smoke").is_dir())
        self.assertFalse((dst / "runs" / "run_0123456789abcdef").exists())

    def test_the_code_it_runs_is_python(self) -> None:
        compile(TESTS_CODE.format(src="a", dst="b", args=repr(["-q"]), skip=repr(("x",))),
                "tests", "exec")


class SideBySideTests(unittest.TestCase):

    def test_reading_tools_together_others_alone(self) -> None:
        calls = [ToolCall(str(n), name, {}) for n, name in enumerate(
            ["read_file", "read_file", "list_dir", "write_file", "read_file",
             "read_file", "read_file", "read_file", "read_file", "run_python"])]
        groups = [[one.name for one in batch] for batch in _side_by_side(calls)]
        self.assertEqual(groups, [["read_file", "read_file", "list_dir"], ["write_file"],
                                  ["read_file"] * 4, ["read_file"], ["run_python"]])

    def _most_at_once(self, gate: ToolGate, names) -> int:
        going, most, lock = [0], [0], threading.Lock()

        def one(name):
            with gate.hold(name):
                with lock:
                    going[0] += 1
                    most[0] = max(most[0], going[0])
                time.sleep(0.8)
                with lock:
                    going[0] -= 1
        threads = [threading.Thread(target=one, args=(name,)) for name in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return most[0]

    def test_within_the_computers_limits(self) -> None:
        self.assertEqual(self._most_at_once(ToolGate(busy=lambda: False), ["read_file"] * 6), 4)
        self.assertEqual(self._most_at_once(ToolGate(busy=lambda: True), ["read_file"] * 3), 1)
        self.assertEqual(self._most_at_once(ToolGate(busy=lambda: False),
                                            ["write_file", "run_python", "write_file"]), 1)
        # A write never beside a read: at most the two reads together.
        self.assertLessEqual(self._most_at_once(ToolGate(busy=lambda: False),
                                                ["read_file", "write_file", "read_file"]), 2)

    def test_busy_as_the_governor_counts_it(self) -> None:
        class Machine:
            def __init__(self, cpu, free):
                self.cpu, self.free = cpu, free

            def cpu_percent(self, interval=None):
                return self.cpu

            def virtual_memory(self):
                return mock.Mock(total=100, available=self.free)
        for cpu, free, busy in ((90.0, 50, True), (20.0, 5, True), (20.0, 50, False)):
            parallel._looked["at"] = 0.0
            with mock.patch.object(parallel, "_psutil", return_value=Machine(cpu, free)):
                self.assertEqual(parallel.machine_busy(), busy)
        parallel._looked["at"] = 0.0

    def test_the_bridge_runs_reading_tools_side_by_side(self) -> None:
        from personalized_agent.models.base import ToolSpec
        from personalized_agent.models.providers.mcp_bridge import ToolBridge
        specs = [ToolSpec("read_file", "", {}, read_only=True), ToolSpec("write_file", "", {})]
        going, most, lock = [0], [0], threading.Lock()

        def runner(name, arguments):
            with lock:
                going[0] += 1
                most[0] = max(most[0], going[0])
            time.sleep(0.15)
            with lock:
                going[0] -= 1
            return {"ok": True}
        bridge = ToolBridge(specs, runner)
        listed = bridge._answer({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {one["name"]: one for one in listed["result"]["tools"]}
        self.assertTrue(tools["read_file"]["annotations"]["readOnlyHint"])
        self.assertNotIn("annotations", tools["write_file"])
        threads = [threading.Thread(target=bridge._call, args=("read_file", {}))
                   for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(most[0], 3)
        most[0] = 0
        threads = [threading.Thread(target=bridge._call, args=("write_file", {}))
                   for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(most[0], 1)


class _Run:
    """What `_what_to_know` asks of a run - its record kept by the real
    methods."""
    experience_given = Run.experience_given
    keep_experience_given = Run.keep_experience_given
    learned_given = Run.learned_given
    _what_to_know = Run._what_to_know
    _log_questions = Run._log_questions
    _ahead_still_holds = Run._ahead_still_holds

    def __init__(self, app, finder):
        self.owner = app.work
        self.id = "run_ahead"
        self.finder = finder
        self.review_tools = None
        self.instruction = "Build the pledge form"
        self.debug = False
        self._facets = {"purpose": ["purpose:the round's"]}
        self._asked = {1: "the round's question"}
        self.notes = []

    def _description_text(self):
        return ""

    def _description(self):
        return ""

    def _learned_ask(self, *args):
        return {}

    def _note(self, what, **details):
        self.notes.append(what)


class _Finder:
    def word_list(self):
        return "THE WORD LIST:\n  pledge\n"

    def job_facets(self, terms):
        return {"deliverable": ["deliverable:form"]}

    def answer(self, job, facets, questions, ask=None, had=None, explain=False):
        return {"text": "WHAT WAS LEARNED", "found": {"reasons": ["r1"], "cases": []}}


class _Review:
    attached = False
    plain = False
    rules_given = False

    def __init__(self):
        self.given = {"reasons": set(), "cases": set(), "index": set()}

    def once(self, text, after):
        return text

    def forget(self, *args):
        pass

    def took(self, found):
        pass

    def ask(self, content, tools=None, rounds=0):
        return {"questions": [{"q": "Who signs the pledge?", "kind": "requirement"}],
                "terms": {}}


class LookedUpAheadTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _card(self, statement="Who may sign, and how"):
        return {"id": "card_2", "title": "The signers", "statement": statement, "type": "task"}

    def test_ahead_leaves_the_review_going_alone_then_is_used(self) -> None:
        run = _Run(self.app, _Finder())
        from personalized_agent.agent.work.runner import _card_shown
        card = self._card()
        run._what_to_know(_Review(), "THIS CARD", _card_shown(card), card, ahead=True)
        # What the round's review is using, as it was.
        self.assertEqual(run._facets, {"purpose": ["purpose:the round's"]})
        self.assertEqual(run._asked, {1: "the round's question"})
        kept = run.experience_given("criteria:card_2")
        self.assertIn("WHAT WAS LEARNED", kept["text"])
        self.assertEqual(kept["ahead_for"], _card_shown(card))
        # The next round writes its criteria: no question asked again, the
        # questions logged ahead its answers' own.
        run._ahead_still_holds(card)

        class Never(_Review):
            def ask(self, *args, **kwargs):
                raise AssertionError("asked again")
        self.assertIn("WHAT WAS LEARNED", run._what_to_know(Never(), "THIS CARD",
                                                            _card_shown(card), card))
        self.assertEqual(run._facets, {"deliverable": ["deliverable:form"]})
        self.assertEqual(list(run._asked), [1])
        self.assertNotEqual(run._asked[1], "the round's question")

    def test_a_card_changed_since_is_looked_up_again(self) -> None:
        run = _Run(self.app, _Finder())
        from personalized_agent.agent.work.runner import _card_shown
        card = self._card()
        run._what_to_know(_Review(), "THIS CARD", _card_shown(card), card, ahead=True)
        changed = self._card("Who may sign, how, and by when")
        run._ahead_still_holds(changed)
        self.assertIsNone(run.experience_given("criteria:card_2"))
        self.assertIn("looked up again", run.notes)


if __name__ == "__main__":
    unittest.main()

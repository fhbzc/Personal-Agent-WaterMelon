"""Two things a real debugger run showed.

THE RUN LOGS ARE NOT THE AGENTS' TO READ. A project's `run_logs/` - every
run's transcript, prompts and all, and its live session - was only kept
from being written: all three ways of a debugger project listed it and read
its README, and the single model read its own whole transcript into its
context. Now no agent of any run reads, lists or writes it: the work tools
refuse it and leave it out of what they list, the guard refuses it inside
code a run runs, and the operating system keeps that code out of it - on
Windows the folder is sealed from every container as soon as it is made,
while the application, writing as the user, and the user keep theirs.

A MODEL CALL THAT TIMED OUT IS MADE AGAIN - after a short wait, then
longer ones, for as long as it lasts - each failed attempt recorded in the
transcript as a call of its own and said in the live session; the run goes
on once it answers. A FAILURE ONLY THE USER CAN MEND - a sign-in - and A
USAGE LIMIT are waited on instead: the run says it waits for you, why and
where to mend it, and goes on when you say to try again or once the model
answers again by itself; ended by you, it makes no other call.
"""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from unittest import mock

from support import TemporaryAgent
from test_work_runs import _TwoAgents

from personalized_agent.agent.work import runner as runner_module
from personalized_agent.agent.work.versions import LOGS_DIR
from personalized_agent.models.base import (AvailabilityState, FailureClass,
                                            ModelAvailability, ModelInvocationError)
from personalized_agent.runtime import isolation


def _appcontainer() -> bool:
    return isolation.isolation_status().get("backend") == "appcontainer"


class _RunBase(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.unit = {"id": "task-logs01", "name": "Write notes", "kind": "task", "why": ""}

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _wait(self):
        for _ in range(900):
            runs = self.app.work.runs(self.unit["id"])
            if runs and not runs[0]["alive"] and runs[0]["status"] != "running":
                break
            time.sleep(0.1)
        return self.app.work.runs(self.unit["id"])[0]

    def _run(self, models, instruction="write the notes"):
        self.app.work.models = models
        started = self.app.work.start(self.unit, instruction)
        self.assertNotIn("error", started, started)
        return self._wait(), Path(started["folder"])

    def _start(self, models, instruction="write the notes"):
        self.app.work.models = models
        started = self.app.work.start(self.unit, instruction)
        self.assertNotIn("error", started, started)
        return started["run"], Path(started["folder"])

    def _until(self, check, seconds=60.0):
        end = time.time() + seconds
        while time.time() < end:
            said = check()
            if said:
                return said
            time.sleep(0.05)
        self.fail("it did not happen in time")

    @staticmethod
    def _logs_text(folder: Path) -> str:
        return "\n".join(one.read_text(encoding="utf-8", errors="replace")
                         for one in (folder / LOGS_DIR).rglob("*") if one.is_file())


class TheRunLogsAreClosedTests(_RunBase):

    def _tools(self, folder):
        from personalized_agent.agent.work.tools import WorkTools
        runtime = self.app.work._runtime(self.unit, folder)
        return runtime, WorkTools(runtime)

    def test_no_tool_reads_lists_or_finds_them(self) -> None:
        run, folder = self._run(_TwoAgents())
        self.assertEqual(run["status"], "done", run["log"][-4:])
        logs = folder / LOGS_DIR
        readme = logs / "README.txt"
        self.assertTrue(readme.is_file())
        transcripts = list(logs.rglob("transcript-*.txt"))
        self.assertTrue(transcripts)
        runtime, tools = self._tools(folder)
        self.assertIn(logs.resolve(), runtime.policy.closed_paths)
        for path in (readme, transcripts[0]):
            said = tools.call("read_file", {"path": str(path)})
            self.assertEqual(said.get("error"), "security_violation", said)
            self.assertIn("run logs - the record of what happened", said["message"])
        said = tools.call("list_dir", {"path": str(logs)})
        self.assertEqual(said.get("error"), "security_violation", said)
        listed = [one["name"] for one in tools.call("list_dir", {"path": str(folder)})
                  ["entries"]]
        self.assertNotIn(LOGS_DIR, listed)
        self.assertIn("notes.md", listed)
        found = tools.call("find_files", {"root": str(folder), "pattern": "**/*.txt"})
        self.assertFalse([one for one in found["files"] if LOGS_DIR in one], found)
        # Writing them was refused already, and still is.
        said = tools.call("write_file", {"path": f"{LOGS_DIR}/mine.txt", "content": "x"})
        self.assertIn("run logs", str(said.get("error")), said)
        self.assertFalse((logs / "mine.txt").exists())

    def test_code_a_run_runs_cannot_open_them(self) -> None:
        _run, folder = self._run(_TwoAgents())
        readme = folder / LOGS_DIR / "README.txt"
        _runtime, tools = self._tools(folder)
        said = tools.call("run_python", {"code": (
            "import os\n"
            f"for act in (lambda: open(r'{readme}').read(),\n"
            f"            lambda: os.listdir(r'{readme.parent}'),\n"
            f"            lambda: open(r'{readme.parent / 'x.txt'}', 'w').write('x')):\n"
            "    try:\n"
            "        print('DID', act())\n"
            "    except Exception as exc:\n"
            "        print('refused:', exc)\n")})
        out = str(said.get("stdout"))
        self.assertNotIn("DID", out, said)
        self.assertEqual(out.count("the project's run logs"), 3, said)
        self.assertFalse((readme.parent / "x.txt").exists())

    def test_the_operating_system_keeps_them_closed(self) -> None:
        """Past the guard - `_io.FileIO` is not wrapped - the AppContainer
        alone; a project's logs made before this rule are sealed by its next
        run; the user, and the application as the user, keep their access."""
        if not _appcontainer():
            self.skipTest("no AppContainer isolation here")
        folder = self.app.work.folder_for(self.unit["name"], self.unit["id"])
        old = folder / LOGS_DIR
        old.mkdir(parents=True)
        (old / "earlier.txt").write_text("from before")
        api = isolation._AppContainer.api()
        self.assertFalse(api.is_protected(str(old)))
        _run, folder = self._run(_TwoAgents())
        logs = folder / LOGS_DIR
        self.assertTrue(api.is_protected(str(logs)))
        _runtime, tools = self._tools(folder)
        said = tools.call("run_python", {"code": (
            "import _io\n"
            f"for act in (lambda: _io.FileIO(r'{logs / 'earlier.txt'}').read(),\n"
            f"            lambda: _io.FileIO(r'{logs / 'planted.txt'}', 'w').write(b'x')):\n"
            "    try:\n"
            "        print('DID', act())\n"
            "    except Exception as exc:\n"
            "        print('refused:', type(exc).__name__)\n"
            "print('BESIDE', _io.FileIO('notes.md').read())\n")})
        out = str(said.get("stdout"))
        self.assertNotIn("DID", out, said)
        self.assertEqual(out.count("refused: PermissionError"), 2, said)
        self.assertIn("BESIDE b'hello'", out, said)    # the rest of the folder is its own
        self.assertFalse((logs / "planted.txt").exists())
        # The user - this process - reads, lists and writes them as before.
        self.assertIn("earlier.txt", os.listdir(logs))
        self.assertEqual((logs / "earlier.txt").read_text(), "from before")
        (logs / "user.txt").write_text("mine")
        (logs / "user.txt").unlink()
        self.assertTrue(list(logs.rglob("events.jsonl")))   # written by the application

    def test_where_a_run_from_before_kept_its_own_is_closed_too(self) -> None:
        folder = self.app.work.folder_for(self.unit["name"], self.unit["id"])
        old = folder / "runs" / "run_old" / ".session"
        old.mkdir(parents=True)
        (old / "events.jsonl").write_text("{}\n")
        runtime, tools = self._tools(folder)
        self.assertIn(old.resolve(), runtime.policy.closed_paths)
        said = tools.call("read_file", {"path": str(old / "events.jsonl")})
        self.assertEqual(said.get("error"), "security_violation", said)

    def test_a_debugger_way_and_pip_are_closed_out_too(self) -> None:
        from personalized_agent.agent.work.packages import Installer
        chosen = Path(self.agent.dir) / "bench"
        unit = {"id": "dbg_x:single", "name": "N", "debug": {
            "project": "dbg_x", "variant": "single", "folder": str(chosen / "A"),
            "sealed": [str(chosen / "B"), str(chosen / "C")]}}
        runtime = self.app.work._runtime(unit, chosen / "A")
        self.assertIn((chosen / "A" / LOGS_DIR).resolve(), runtime.policy.closed_paths)
        pip = Installer(runtime.policy).pip_policy(chosen / "A" / "runs" / "r")
        self.assertEqual(pip.closed_paths, runtime.policy.closed_paths)
        env = runtime._child_environment(None)
        self.assertIn(str((chosen / "A" / LOGS_DIR).resolve()), env["AGENT_CLOSED_PATHS"])


class _TimesOut:
    """The two agents - but the first `times` calls (or every one) do not
    answer: the provider's own timeout."""

    def __init__(self, times=1, error=None):
        self.inner = _TwoAgents()
        self.times = times
        self.failed = 0
        self.error = error

    @property
    def asked(self):
        return self.inner.asked

    def invoke(self, request, role=""):
        if self.times is None or self.failed < self.times:
            self.failed += 1
            raise self.error or ModelInvocationError(
                "Claude Code did not answer within 300s", ModelAvailability(
                    available=False, state=AvailabilityState.PROVIDER_UNAVAILABLE,
                    reason="Claude Code did not answer within 300s",
                    failure_class=FailureClass.TEMPORARY))
        return self.inner.invoke(request, role)


def _signed_out():
    return ModelInvocationError("the sign-in has expired", ModelAvailability(
        available=False, state=AvailabilityState.AUTHENTICATION_REQUIRED,
        reason="the sign-in has expired",
        failure_class=FailureClass.USER_ACTION_REQUIRED))


class WhatAFailureAsksAndWhenALimitResetsTests(unittest.TestCase):

    def test_what_a_failure_asks(self) -> None:
        trouble = runner_module.model_trouble

        def failed(state, failure, reason):
            return ModelInvocationError(reason, ModelAvailability(
                False, state, reason=reason, failure_class=failure))
        self.assertEqual(trouble(failed(AvailabilityState.PROVIDER_UNAVAILABLE,
                                        FailureClass.TEMPORARY,
                                        "Claude Code did not answer within 300s")), "temporary")
        self.assertEqual(trouble(failed(AvailabilityState.RATE_LIMITED, FailureClass.TEMPORARY,
                                        "the provider is rate limiting this credential")),
                         "temporary")
        self.assertEqual(trouble(failed(AvailabilityState.RATE_LIMITED, FailureClass.TEMPORARY,
                                        "You've hit your session limit · resets 11:50pm")),
                         "limit")
        self.assertEqual(trouble(failed(AvailabilityState.QUOTA_EXHAUSTED,
                                        FailureClass.USER_ACTION_REQUIRED,
                                        "usage limit reached")), "limit")
        self.assertEqual(trouble(failed(AvailabilityState.QUOTA_EXHAUSTED,
                                        FailureClass.USER_ACTION_REQUIRED,
                                        "Your credit balance is too low")), "yours")
        self.assertEqual(trouble(_signed_out()), "yours")
        self.assertIsNone(trouble(ValueError("did not answer within 300s")))
        self.assertIsNone(trouble(failed(AvailabilityState.UNKNOWN_ERROR, FailureClass.UNKNOWN,
                                         "something else")))

    def test_when_a_limit_resets(self) -> None:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        resets = runner_module.limit_resets_at
        now = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc).timestamp()
        lisbon = datetime.fromtimestamp(
            resets("You've hit your session limit · resets 11:50pm (Europe/Lisbon)", now),
            ZoneInfo("Europe/Lisbon"))
        self.assertEqual((lisbon.day, lisbon.hour, lisbon.minute), (11, 23, 50))
        self.assertEqual(resets("Try again in 2 hours 5 minutes.", now), now + 7500)
        self.assertEqual(resets("try again in 1h 30m", now), now + 5400)
        self.assertEqual(resets("usage limit reached|1791763200", now), 1791763200.0)
        shanghai = datetime.fromtimestamp(resets("resets Oct 12, 3pm (Asia/Shanghai)", now),
                                          ZoneInfo("Asia/Shanghai"))
        self.assertEqual((shanghai.month, shanghai.day, shanghai.hour), (10, 12, 15))
        # Earlier today than now: tomorrow.
        early = datetime.fromtimestamp(resets("resets 1:15am (UTC)", now), timezone.utc)
        self.assertEqual((early.day, early.hour, early.minute), (12, 1, 15))
        for unsaid in ("usage limit reached", "the sign-in has expired", "resets soon",
                       "usage limit reached|1600000000"):
            self.assertIsNone(resets(unsaid, now), unsaid)


class AModelCallIsTriedAgainTests(_RunBase):

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(runner_module, "CALL_RETRY_WAITS", (0.0, 0.0))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_once_timed_out_then_answered_the_run_goes_on(self) -> None:
        models = _TimesOut(times=1)
        run, folder = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-4:])
        text = self._logs_text(folder)
        self.assertEqual(text.count("(the call failed:"), 1, text[-2000:])
        self.assertIn("did not answer within 300s", text)
        self.assertIn("model call timed out - trying again in 0s (attempt 2)", text)

    def test_always_timed_out_it_keeps_trying_until_stopped(self) -> None:
        models = _TimesOut(times=None)
        with mock.patch.object(runner_module, "CALL_WAIT_LONGER", (0.05,)):
            run_id, folder = self._start(models)
            # Past the first waits, and on with the longer ones: never failed.
            self._until(lambda: models.failed >= 6)
            going = self._until(lambda: (lambda g: g if g and "trying again" in g[0]["activity"]
                                         else None)(self.app.work.run_states()))
            self.assertEqual([one["state"] for one in going], ["going"], going)
            self.app.work.stop(run_id)
            run = self._wait()
        self.assertEqual(run["status"], "not finished", run["log"][-4:])
        failed = models.failed
        text = self._logs_text(folder)
        # Stopped while it waited: no other call - not even for the slides.
        self.assertEqual(text.count("(the call failed:"), failed, text[-2000:])
        self.assertIn("trying again in 0s (attempt 6)", text)
        self.assertNotIn("Failed:", text)

    def test_failing_for_an_hour_it_waits_for_you(self) -> None:
        models = _TimesOut(times=None)
        with mock.patch.object(runner_module, "CALL_WAIT_LONGER", (0.05,)), \
                mock.patch.object(runner_module, "TEMPORARY_CAP_SECONDS", 0.6):
            run_id, folder = self._start(models)
            session = self._until(lambda: (lambda s: s if s["pending"] else None)(
                self.app.work.live(run_id)))
        self.assertEqual(session["state"], "waiting_for_you")
        ask, = session["pending"]
        self.assertEqual(ask["trouble"], "stuck")
        self.assertIn("has not answered for", ask["title"])
        self.assertEqual(ask["next_look"], "")
        # No more tries by itself, however long it waits.
        tried = models.failed
        time.sleep(1.5)
        self.assertEqual(models.failed, tried)
        self.assertEqual(self.app.work.run_states()[0]["state"], "waiting_for_you")
        # Try again now: once, and failing, it waits for you again - the same wait.
        self.app.work.approve(run_id, ask["id"], True)
        self._until(lambda: models.failed == tried + 1)
        session = self._until(lambda: (lambda s: s if s["pending"] and s["pending"][0]["looks"] == 1
                                       else None)(self.app.work.live(run_id)))
        self.assertEqual(session["pending"][0]["id"], ask["id"])
        time.sleep(0.5)
        self.assertEqual(models.failed, tried + 1)
        self.app.work.approve(run_id, ask["id"], False)
        run = self._wait()
        self.assertEqual(run["status"], "not finished", run["log"][-4:])
        self.assertEqual(models.failed, tried + 1)
        self.assertIn("has not answered for", self._logs_text(folder))

    def test_what_only_you_can_mend_waits_for_you(self) -> None:
        models = _TimesOut(times=None, error=_signed_out())
        run_id, folder = self._start(models)
        session = self._until(lambda: (lambda s: s if s["state"] == "waiting_for_you"
                                       else None)(self.app.work.live(run_id)))
        self.assertEqual(session["status"], "waiting for you")
        ask, = session["pending"]
        self.assertEqual((ask["kind"], ask["trouble"]), ("model", "yours"))
        self.assertIn("sign in again", ask["title"])
        self.assertEqual(ask["fix"]["action"], "sign_in")
        self.assertEqual(ask["what"], "the sign-in has expired")
        self.assertTrue(ask["next_look"])
        # Not made again on its own, at once.
        self.assertEqual(models.failed, 1)
        going = self.app.work.run_states()
        self.assertEqual([(one["state"], one["waiting_on"]) for one in going],
                         [("waiting_for_you", "model")])
        # Try again now: made once more, and still waiting - the same wait.
        self.assertEqual(self.app.work.approve(run_id, ask["id"], True)["status"], "pending")
        self._until(lambda: models.failed == 2)
        session = self._until(lambda: (lambda s: s if s["pending"] and s["pending"][0]["looks"] == 1
                                       else None)(self.app.work.live(run_id)))
        self.assertEqual(session["pending"][0]["id"], ask["id"])
        self.assertEqual(session["state"], "waiting_for_you")
        # End the run: it ends not finished, and no other call is made.
        self.assertEqual(self.app.work.approve(run_id, ask["id"], False)["status"], "ended")
        run = self._wait()
        self.assertEqual(run["status"], "not finished", run["log"][-4:])
        self.assertEqual(models.failed, 2)
        text = self._logs_text(folder)
        self.assertEqual(text.count("(the call failed:"), 2)
        self.assertIn("You ended the run while the model needed you", text)
        self.assertNotIn("Failed:", text)
        waits = run["plan"]["model_waits"]
        self.assertEqual([one["status"] for one in waits], ["ended"])

    def test_mended_it_goes_on_by_itself(self) -> None:
        models = _TimesOut(times=1, error=_signed_out())
        with mock.patch.object(runner_module, "YOURS_LOOK_SECONDS", (0.3,)), \
                mock.patch.object(runner_module, "LOOK_AT_LEAST", 0.0):
            run, _folder = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-4:])
        waits = run["plan"]["model_waits"]
        self.assertEqual([(one["status"], one["note"]) for one in waits],
                         [("continued", "the model answered again")])

    def test_a_usage_limit_waits_until_it_resets(self) -> None:
        models = _TimesOut(times=1, error=ModelInvocationError(
            "You've hit your session limit - try again in 2 seconds", ModelAvailability(
                available=False, state=AvailabilityState.RATE_LIMITED,
                reason="You've hit your session limit - try again in 2 seconds",
                failure_class=FailureClass.TEMPORARY)))
        with mock.patch.object(runner_module, "LIMIT_RESET_GRACE", 0.0), \
                mock.patch.object(runner_module, "LOOK_AT_LEAST", 0.0):
            run_id, _folder = self._start(models)
            session = self._until(lambda: (lambda s: s if s["pending"] else None)(
                self.app.work.live(run_id)))
            self.assertEqual(session["state"], "waiting_for_you")
            ask, = session["pending"]
            self.assertEqual(ask["trouble"], "limit")
            self.assertIn("usage limit", ask["title"])
            self.assertTrue(ask["resets_at"])
            run = self._wait()
        self.assertEqual(run["status"], "done", run["log"][-4:])
        self.assertEqual(models.failed, 1)
        self.assertEqual([one["status"] for one in run["plan"]["model_waits"]], ["continued"])

    def test_which_failures_are_tried_again(self) -> None:
        from personalized_agent.models.manager import ModelUnavailable
        retry = runner_module.retryable_failure
        temporary = ModelAvailability(False, AvailabilityState.RATE_LIMITED,
                                      failure_class=FailureClass.TEMPORARY)
        self.assertTrue(retry(ModelInvocationError("busy", temporary)))
        resolved = mock.Mock(label="claude_code/opus")
        self.assertTrue(retry(ModelUnavailable(resolved, ModelAvailability(
            False, AvailabilityState.PROVIDER_UNAVAILABLE,
            reason="Claude Code did not answer within 300s"))))
        self.assertFalse(retry(ModelUnavailable(resolved, ModelAvailability(
            False, AvailabilityState.CREDENTIAL_INVALID, reason="bad key"))))
        self.assertFalse(retry(ModelInvocationError("could not run claude", ModelAvailability(
            False, AvailabilityState.PROVIDER_UNAVAILABLE,
            failure_class=FailureClass.USER_ACTION_REQUIRED))))
        self.assertFalse(retry(ValueError("did not answer within 300s")))
        # A client that started without its input (Claude Code waits three
        # seconds for it): the next try has it.
        self.assertTrue(retry(ModelInvocationError(
            "Warning: no stdin data received in 3s, proceeding without it.",
            ModelAvailability(False, AvailabilityState.UNKNOWN_ERROR,
                              failure_class=FailureClass.UNKNOWN))))


if __name__ == "__main__":
    unittest.main()

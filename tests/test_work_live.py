"""The live session of a work run: what the two agents do, as they do it -
paused at a safe point and resumed exactly there, stopped while paused,
settled as interrupted when the application closes, and asking for
permission inline - and the panel that shows it."""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from support import TemporaryAgent
from test_work_packages import _FakeInstaller, _WantsPackages
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


class _Gated(_TwoAgents):
    """The two agents, with the first model call of the first round held at
    a gate: the test pauses the run there, then lets the call answer - a
    write_file - so the run reaches the safe point before that tool."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.reached = threading.Event()
        self.go = threading.Event()
        self.gated_at = -1
        #: Each call's messages as they were when it was made - the list
        #: itself goes on growing.
        self.seen = []

    def invoke(self, request, role=""):
        self.seen.append(json.loads(json.dumps(request.messages, default=str)))
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        if self.gated_at < 0 and "You review a work agent" not in request.system \
                and "WORK ONE ROUND" in first:
            self.gated_at = len(self.asked)
            self.reached.set()
            self.go.wait(30)
        return super().invoke(request, role)


def _until(condition, seconds=60.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


class _LiveBase(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        experience = self.app.state_heartbeat.experience
        made = experience.store.create_work_project("Write notes")
        self.unit = experience.unit(made["unit_id"])
        from personalized_agent.api.routes import build_routes
        self.routes = build_routes(self.app)
        self.held = []

    def tearDown(self) -> None:
        for run in list(self.app.work._running.values()):
            if run in self.held:
                continue          # "the application closed" on it: left held
            run.stop_now = True
            run.release(True)
        _until(lambda: not any(r.alive() for r in self.app.work._running.values()
                               if r not in self.held), 60)
        self.agent.cleanup()

    def _start(self, models, packages=None):
        self.app.work.models = models
        said = self.app.work.start(self.unit, "write the notes", packages=packages)
        self.assertNotIn("error", said, said)
        return said, self.app.work._running[said["run"]]

    def _row(self, run_id):
        return next(one for one in self.app.work.runs(self.unit["id"]) if one["id"] == run_id)

    def _finished(self, run_id, seconds=60):
        self.assertTrue(_until(lambda: not self._row(run_id)["alive"], seconds),
                        self._row(run_id)["status"])
        return self._row(run_id)

    def _paused_at_the_tool(self, models):
        """Started, paused while the round's first model call is in flight,
        and held at the safe point before the tool that call asked for."""
        said, run = self._start(models)
        self.assertTrue(models.reached.wait(30), "the round never began")
        self.assertEqual(self.routes[("POST", "/api/work/pause")](
            _Request({"run": said["run"]})), {"paused": True})
        models.go.set()
        self.assertTrue(_until(lambda: run._held_since > 0), "never held")
        return said, run

    def _get(self, path, body):
        return self.routes[("GET", path)](_Request(body))

    def _post(self, path, body):
        return self.routes[("POST", path)](_Request(body))


class PauseAndResumeTests(_LiveBase):

    def test_pause_holds_at_the_next_safe_point_and_resume_goes_on_there(self) -> None:
        models = _Gated()
        said, run = self._paused_at_the_tool(models)
        folder = Path(said["folder"])
        calls = len(models.asked)
        time.sleep(1.5)
        # Held: no model call, and the tool the answer asked for not run.
        self.assertEqual(len(models.asked), calls)
        self.assertFalse((folder / "notes.md").exists())
        row = self._row(said["run"])
        self.assertEqual(row["status"], "paused")
        self.assertTrue(row["paused"])
        live = self._get("/api/work/live", {"run": said["run"]})
        self.assertEqual(live["state"], "paused")
        self.assertTrue(live["progress"]["held"])
        self.assertIn("before write_file", live["progress"]["activity"])
        self.assertTrue(any(e["kind"] == "pause" and e["who"] == "you" for e in live["events"]))
        # PAUSED TIME IS NOT THE RUN'S: an hour gone, all of it held, and a
        # 30-minute limit is not reached.
        run.began -= 3600
        run._held_since -= 3599
        run.limits = dict(run.limits, minutes=30)
        self.assertTrue(run._going(work=True))
        self.assertLess(run._minutes(), 5)
        self.assertEqual(self._post("/api/work/resume", {"run": said["run"]}),
                         {"paused": False})
        row = self._finished(said["run"])
        self.assertEqual(row["status"], "done", row["log"][-6:])
        self.assertEqual((folder / "notes.md").read_text(), "hello")
        # The same round, the same conversation: the call after the pause
        # carries the tool's result on from the held exchange, and no new
        # round was begun for it.
        gated = models.seen[models.gated_at]
        after = models.seen[models.gated_at + 1]
        self.assertEqual(len(gated), 1)
        self.assertEqual(after[0], gated[0])
        self.assertEqual(after[1]["role"], "assistant")
        self.assertIsInstance(after[-1]["content"], list)
        self.assertEqual(after[-1]["content"][0]["type"], "tool_result")
        self.assertIn("written", after[-1]["content"][0]["content"])
        rounds = [seen for r, seen in zip(models.asked, models.seen)
                  if "You review a work agent" not in r.system
                  and "WORK ONE ROUND" in str(seen[0]["content"]) and len(seen) == 1]
        self.assertEqual(len(rounds), 1)
        self.assertEqual([one["n"] for one in row["plan"]["rounds"]], [1])
        session = row["plan"]["sessions"][-1]
        self.assertGreaterEqual(session["paused_minutes"], 59)
        self.assertLess(session["minutes"], 5)
        report = (folder / "runs" / said["run"] / "report.md").read_text(encoding="utf-8")
        self.assertIn("paused by you", report)

    def test_stop_while_paused_ends_the_run(self) -> None:
        from personalized_agent.agent.work.runner import NOT_FINISHED
        models = _Gated()
        said, _run = self._paused_at_the_tool(models)
        self.assertEqual(self._post("/api/work/stop", {"run": said["run"]}),
                         {"stopping": True})
        row = self._finished(said["run"])
        self.assertEqual(row["status"], NOT_FINISHED)
        self.assertEqual(row["final"]["reason"], "stopped")
        # The tool it was held before was never run.
        self.assertFalse((Path(said["folder"]) / "notes.md").exists())
        live = self._get("/api/work/live", {"run": said["run"]})
        self.assertEqual(live["state"], "ended")
        self.assertTrue(any(e["kind"] == "stop" for e in live["events"]))

    def test_stop_now_while_paused_ends_it_at_once(self) -> None:
        models = _Gated()
        said, _run = self._paused_at_the_tool(models)
        calls = len(models.asked)
        self.app.work.stop(said["run"], now=True)
        self._finished(said["run"], seconds=20)
        self.assertEqual(len(models.asked), calls, "stopped now: no model call after")

    def test_a_run_not_going_cannot_be_paused(self) -> None:
        from personalized_agent.api.errors import ApiError
        with self.assertRaises(ApiError) as caught:
            self._post("/api/work/pause", {"run": "run_nope"})
        self.assertEqual(caught.exception.status, 409)


class TheLiveEndpointTests(_LiveBase):

    def test_events_since_a_number_and_where_the_run_stands(self) -> None:
        self.app.work.models = _TwoAgents()
        said = self.app.work.start(self.unit, "write the notes")
        row = self._finished(said["run"])
        self.assertEqual(row["status"], "done")
        live = self._get("/api/work/live", {"run": said["run"], "since": 0})
        self.assertEqual(set(live), {"run", "events", "last", "state", "status", "pending",
                                     "requests", "progress", "resumes_from", "contract",
                                     "calls"})
        # The contract in force, in a line, for the session's head.
        self.assertEqual(live["contract"], {"goal": "notes.md says hello", "branch": "main",
                                            "since": "v1", "done": 1, "total": 1,
                                            "amended": False})
        events = live["events"]
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
        for one in events:
            self.assertTrue({"seq", "at", "who", "kind", "text"} <= set(one), one)
        kinds = {(e["who"], e["kind"]) for e in events}
        for expected in (("you", "start"), ("worker", "turn"), ("reviewer", "turn"),
                         ("worker", "tool"), ("reviewer", "verdict"), ("run", "gate"),
                         ("run", "milestone"), ("run", "version"), ("run", "end")):
            self.assertIn(expected, kinds)
        tool = next(e for e in events if e["kind"] == "tool")
        self.assertIn("write_file(notes.md)", tool["text"])
        self.assertEqual(tool["detail"]["result"], "written")
        stages = [e["detail"]["stage"] for e in events if e["kind"] == "milestone"
                  and (e.get("detail") or {}).get("stage")]
        # Last, the README of what was made (runner.py, the deliverable's own).
        self.assertEqual(stages, ["contract", "round 1", "final review", "deck", "readme"])
        progress = live["progress"]
        self.assertEqual(progress["rounds_done"], 1)
        self.assertEqual((progress["cards"], progress["cards_done"]), (1, 1))
        self.assertGreater(progress["used"]["calls"], 0)
        self.assertEqual(progress["limits"], {"minutes": 0, "tokens_in": 0,
                                              "tokens_out": 0, "calls": 0})
        self.assertFalse(progress["sound"])
        # Its model calls, every one, for the chart.
        self.assertEqual(len(live["calls"]), progress["used"]["calls"])
        self.assertEqual(live["state"], "ended")
        # Only what came after.
        later = self._get("/api/work/live", {"run": said["run"], "since": events[-3]["seq"]})
        self.assertEqual([e["seq"] for e in later["events"]],
                         [e["seq"] for e in events[-2:]])
        self.assertEqual(later["last"], events[-1]["seq"])
        # Kept in the run's folder, outside its versions, and read back the
        # same by an application started again.
        logs = next((Path(said["folder"]) / "run_logs").glob(f"*_{said['run']}"))
        kept = logs / "events.jsonl"
        self.assertTrue(kept.is_file())
        from personalized_agent.agent.work.runner import WorkRuns
        again = WorkRuns(self.app.db, self.app.policy, self.app.models, None)
        reread = again.live(said["run"], 0)
        self.assertEqual([e["seq"] for e in reread["events"]], [e["seq"] for e in events])
        self.assertEqual(reread["state"], "ended")
        manifest = self.app.work.version(said["run"], self.app.work.versions(
            said["run"])["head"])
        self.assertFalse(any(".session" in one["path"] or one["path"].startswith("run_logs")
                             for one in manifest["files"]))
        from personalized_agent.api.errors import ApiError
        with self.assertRaises(ApiError):
            self._get("/api/work/live", {"run": "run_nope"})

    def test_the_agent_cannot_write_the_session(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools
        from personalized_agent.runtime.sandbox import SandboxRuntime
        folder = self.app.work.folder_for("Locked", "task-live01")
        tools = WorkTools(SandboxRuntime(self.app.work.policy_for(folder),
                                         platform=self.app.platform))
        session = folder / "runs" / "run_1" / ".session"
        session.mkdir(parents=True)
        (session / "events.jsonl").write_text("{}\n")
        tools.locked = [session]
        said = tools.call("write_file", {"path": "runs/run_1/.session/events.jsonl",
                                         "content": "x"})
        self.assertIn("live session", said["error"])
        self.assertEqual((session / "events.jsonl").read_text(), "{}\n")
        # Where a run keeps it now: the project's run logs.
        logs = folder / "run_logs" / "2026-10-09_14-32-05_run_2"
        logs.mkdir(parents=True)
        (logs / "events.jsonl").write_text("{}\n")
        tools.locked = [folder / "run_logs"]
        said = tools.call("write_file", {"path": f"run_logs/{logs.name}/events.jsonl",
                                         "content": "x"})
        self.assertIn("the project's run logs", said["error"])
        self.assertEqual((logs / "events.jsonl").read_text(), "{}\n")


class AskingInTheSessionTests(_LiveBase):

    def test_a_request_is_pending_and_in_the_events_and_approve_goes_on(self) -> None:
        self.app.work.installer_for = lambda policy: _FakeInstaller()
        said, _run = self._start(_WantsPackages(), packages="ask")
        self.assertTrue(_until(lambda: self._get("/api/work/live", {"run": said["run"]})
                               ["state"] == "waiting_for_you"))
        live = self._get("/api/work/live", {"run": said["run"]})
        self.assertEqual(len(live["pending"]), 1)
        ask = live["pending"][0]
        self.assertEqual(ask["kind"], "packages")
        self.assertEqual(ask["items"], ["six", "numpy>=1.26"])
        self.assertEqual(ask["why"], "to read the data files")
        asked = [e for e in live["events"] if e["kind"] == "permission"]
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0]["detail"]["request"], ask["id"])
        self.assertEqual(live["requests"][ask["id"]]["status"], "pending")
        self.assertIn("waiting for you", live["progress"]["activity"])
        self._post("/api/work/approve", {"run": said["run"], "request": ask["id"],
                                         "allow": True})
        row = self._finished(said["run"])
        self.assertEqual(row["status"], "done")
        live = self._get("/api/work/live", {"run": said["run"]})
        self.assertEqual(live["pending"], [])
        self.assertEqual(live["requests"][ask["id"]]["status"], "installed")
        answers = [e for e in live["events"] if e["kind"] == "answer"]
        self.assertEqual([(e["who"], e["detail"]["status"]) for e in answers],
                         [("you", "approved")])
        self.assertTrue(any(e["kind"] == "install" and "six 1.17.0" in e["text"]
                            for e in live["events"]))


class TheApplicationClosedTests(_LiveBase):

    def test_a_paused_run_is_interrupted_and_continues_from_its_last_milestone(self) -> None:
        from personalized_agent.agent.work.runner import INTERRUPTED, WorkRuns
        models = _Gated()
        said, run = self._paused_at_the_tool(models)
        # The application closes: the run held where it was - never let go
        # here - and its memory gone. Started again, it is interrupted.
        self.held.append(run)
        self.assertEqual(self.app.db.query("SELECT status FROM work_runs WHERE id = ?",
                                           (said["run"],))[0]["status"], "paused")
        again = WorkRuns(self.app.db, self.app.policy, _TwoAgents(),
                         self.app.state_heartbeat.experience, platform=self.app.platform)
        row = again.runs(self.unit["id"])[0]
        self.assertEqual(row["status"], INTERRUPTED)
        self.assertTrue(row["continuable"])
        # The version's label is its title, as a commit's: what it did.
        self.assertEqual(row["milestone"], {"version": "v1",
                                            "label": "Contract: notes.md says hello"})
        live = again.live(said["run"], 0)
        self.assertEqual(live["state"], "interrupted")
        self.assertEqual(live["resumes_from"]["version"], "v1")
        self.assertTrue(any(e["kind"] == "pause" for e in live["events"]))
        # Something written after the milestone: put back as it was then.
        late = Path(said["folder"]) / "after-the-milestone.txt"
        late.write_text("made after v1")
        logs = next((Path(said["folder"]) / "run_logs").glob(f"*_{said['run']}"))
        transcript = (logs / "transcript-001.txt").read_text(encoding="utf-8")
        continued = again.continue_run(said["run"], self.unit,
                                       {"calls": 0, "minutes": 0, "tokens": 0})
        self.assertNotIn("error", continued, continued)
        self.assertEqual(continued["from_milestone"], "v1")
        self.assertEqual(continued["rolled_back"]["version"], "v1")
        self.assertFalse(late.exists())
        # The rollback leaves the logs as they were - written after v1, and
        # still every word of them there.
        self.assertTrue((logs / "transcript-001.txt").read_text(encoding="utf-8")
                        .startswith(transcript))
        self.assertTrue((logs.parent / "README.txt").is_file())
        self.assertTrue(_until(lambda: not again.runs(self.unit["id"])[0]["alive"]))
        row = again.runs(self.unit["id"])[0]
        self.assertEqual(row["status"], "done", row["log"][-6:])
        # Its session goes on, numbered after what came before.
        seqs = [e["seq"] for e in again.live(said["run"], 0)["events"]]
        self.assertEqual(seqs, sorted(set(seqs)))
        self.assertGreater(seqs[-1], live["last"])


class WhatIsLearnedMeanwhileTests(_LiveBase):
    """Learning a project while a run works on it: a task, and its stages,
    learned after the run began are found by the run's searches of its
    project as soon as they are written."""

    def test_a_task_learned_after_the_start_is_searched(self) -> None:
        from unittest import mock
        from types import SimpleNamespace
        from personalized_agent.agent.work import runner as runner_module
        models = _Gated()
        said, run = self._start(models)
        self.assertTrue(models.reached.wait(30), "the round never began")
        try:
            before = list(run.project_tasks)
            experience = self.app.state_heartbeat.experience
            unit = dict(experience.full_unit(self.unit["id"]))
            unit["tasks"] = [*(unit.get("tasks") or []),
                             SimpleNamespace(id="task_learned_later", members=[], described={})]
            with mock.patch.object(experience, "full_unit", return_value=unit), \
                    mock.patch.object(runner_module, "LIVE_FRESH_SECONDS", 0.0):
                self.assertIn("task_learned_later", run.project_tasks)
                seen = {}

                def find(profile):
                    seen.update(profile)
                    return {}
                run.retriever = SimpleNamespace(find=find)
                run._find_in_project("anything", "")
                self.assertIn("task_learned_later", seen["only_tasks"])
            self.assertNotIn("task_learned_later", before)
        finally:
            models.go.set()


class TheSessionViewTests(unittest.TestCase):
    """The live session in the panel: the progress bars, the transcript, a
    request with Approve and Deny posted, Pause and Resume posted, and
    following it never scrolls the page."""

    def setUp(self) -> None:
        from test_panels import _javascript_engine
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_the_session_view(self) -> None:
        import html
        import re
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        run = {"id": "run_1", "folder": "C:/w/agent_work/x", "alive": True,
               "status": "waiting for you", "continuable": False, "paused": False,
               "started_at": "2026-10-08T12:00:00", "instruction": "read the data",
               "final": {}, "log": [], "versions": {"count": 1, "head": "v1"},
               "plan": {"packages": {"mode": "ask", "requests": [
                   {"id": "req_1", "packages": ["six"], "why": "tables",
                    "status": "pending"}]}}}

        def event(seq, who, kind, text, **detail):
            return {"seq": seq, "at": "2026-10-08T12:00:%02d+00:00" % (seq % 60),
                    "who": who, "kind": kind, "text": text,
                    **({"detail": detail} if detail else {})}
        events = [event(1, "you", "start", "You asked: read the data"),
                  event(2, "run", "milestone", "The contract", stage="contract"),
                  event(3, "worker", "turn", "Calls 1 tool: read_file", tokens=1200),
                  event(4, "worker", "tool", "read_file(data.csv) → 120 characters",
                        ok=True),
                  event(5, "worker", "permission", "Asks to install six - why: tables",
                        request="req_1")]
        live = {"run": "run_1", "events": events, "last": 5, "state": "waiting_for_you",
                "status": "waiting for you", "resumes_from": None,
                "requests": {"req_1": {"status": "pending", "note": "", "kind": "packages"}},
                "pending": [{"id": "req_1", "kind": "packages", "items": ["six"],
                             "what": "six", "why": "tables", "note": "From PyPI",
                             "status": "pending"}],
                "progress": {"stage": "round 2", "phase": "rounds", "round": 1,
                             "rounds_done": 1, "cards": 4, "cards_done": 1,
                             "used": {"minutes": 12.5, "tokens": 250000, "calls": 50},
                             "limits": {"minutes": 45, "tokens": 1000000, "calls": 0},
                             "paused_minutes": 0, "activity": "waiting for you: install six",
                             "pause_asked": False, "held": False, "stopping": False}}
        stub = {"/api/work/runs": {"runs": [run]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {"minutes": 45, "tokens": 1000000,
                                                  "calls": 200}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/work/live": live}
        more = [event(n, "reviewer" if n % 2 else "worker", "turn", f"line {n}")
                for n in range(6, 66)]
        steps = (
            "activeTab='experience';"
            "document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.id==='experience'));"
            "document.body.style.minHeight='4000px';"
            "experienceState.unit='task-live';await xpLoadRuns();await wait(1300);"
            "const box=document.querySelector('[data-xp-live-body=\"run_1\"]');"
            "out.open=document.querySelector('.xp-live').open;"
            "out.state=box.querySelector('.xp-live-state').textContent.trim();"
            "out.head=box.querySelector('.xp-live-head').textContent.replace(/\\s+/g,' ');"
            "out.bars=[...box.querySelectorAll('.xp-live-bar')].map(b=>"
            "  b.textContent.replace(/\\s+/g,' ').trim()+'|'+b.querySelector('.xp-live-bar-fill').style.width);"
            "out.doing=box.querySelector('.xp-live-doing-text').textContent;"
            "out.rows=[...box.querySelectorAll('.xp-live-ev')].map(li=>"
            "  li.querySelector('.xp-live-who').textContent+':'+li.querySelector('.xp-live-text').textContent);"
            "out.answer=box.querySelector('[data-xp-live-req=\"req_1\"]').textContent;"
            "out.ask=box.querySelector('.xp-live-asks .xp-package-ask').textContent.replace(/\\s+/g,' ');"
            "out.gets=window.__gets.filter(g=>g.includes('/api/work/live')).length;"
            "box.querySelector('[data-xp-allow=\"1\"]').dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "await wait(200);"
            "box.querySelector('.xp-live-asks [data-xp-allow=\"0\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait(200);"
            # Answered: the request shows its answer, and is asked no more.
            "STUB['/api/work/live']=Object.assign({},STUB['/api/work/live'],{events:[],"
            "  last:5,state:'going',pending:[],requests:{req_1:{status:'approved',note:''}},"
            "  progress:Object.assign({},STUB['/api/work/live'].progress,{activity:'worker: thinking'})});"
            "await wait(1300);"
            "out.answered=box.querySelector('[data-xp-live-req=\"req_1\"]').textContent;"
            "out.asksAfter=box.querySelectorAll('.xp-live-asks .xp-package-ask').length;"
            # Pause and Stop are beside the project's name, not in the line.
            "out.pauseInLine=!!box.querySelector('[data-xp-live-pause]');"
            "const title=document.querySelector('#xp-title-run');"
            "out.pauseButton=!!title.querySelector('[data-xp-live-pause]');"
            "out.stopBeside=!!title.querySelector('[data-xp-stop=\"run_1\"]');"
            "title.querySelector('[data-xp-live-pause]').dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "await wait(200);"
            "STUB['/api/work/live']=Object.assign({},STUB['/api/work/live'],{state:'paused',"
            "  progress:Object.assign({},STUB['/api/work/live'].progress,{pause_asked:true,"
            "  held:true,activity:'paused before write_file'})});"
            "await wait(1300);"
            "out.pausedState=box.querySelector('.xp-live-state').textContent.trim();"
            "out.pausedDoing=box.querySelector('.xp-live-doing-text').textContent;"
            "out.spinHidden=box.querySelector('.xp-live-spin').hidden;"
            "out.titleState=title.querySelector('.xp-live-state').textContent.trim();"
            "title.querySelector('[data-xp-live-resume]').dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "await wait(200);"
            # Following never scrolls the page; the transcript follows only
            # while you are at its bottom.
            "STUB['/api/work/live']=Object.assign({},STUB['/api/work/live'],{state:'going',"
            "  events:MORE.slice(0,30),last:35});"
            "window.scrollTo(0,300);const y=window.scrollY;"
            "const scroller=box.querySelector('.xp-live-scroll');"
            "scroller.scrollTop=scroller.scrollHeight;"
            "await wait(1300);"
            "out.pageY=[y,window.scrollY];"
            "out.scrollable=scroller.scrollHeight-scroller.clientHeight;"
            "out.followed=scroller.scrollHeight-scroller.scrollTop-scroller.clientHeight<24;"
            "scroller.scrollTop=0;"
            "STUB['/api/work/live']=Object.assign({},STUB['/api/work/live'],{events:MORE.slice(30),last:65});"
            "await wait(1300);"
            "out.keptUp=scroller.scrollTop;out.pageY2=window.scrollY;"
            "out.count=box.querySelectorAll('.xp-live-ev').length;"
            "out.posts=window.__posts;")
        harness = (
            # The page's own styles - the transcript's box scrolls by them.
            "<style>" + (web / "styles.css").read_text(encoding="utf-8") + "</style>"
            "<script>window.__posts=[];window.__gets=[];const STUB=" + json.dumps(stub) + ";"
            "const MORE=" + json.dumps(more) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posts.push({p,body:JSON.parse(o.body||'{}')});"
            "else window.__gets.push(String(u));"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';const out={};"
            "const wait=(ms)=>new Promise(r=>setTimeout(r,ms||80));"
            "try{" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err,out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=20000",
                 "--window-size=1200,800", "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "the page never finished rendering")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        # Open by default for a run that is going, and followed.
        self.assertTrue(out["open"])
        self.assertGreaterEqual(out["gets"], 1)
        self.assertEqual(out["state"], "waiting for you")
        self.assertIn("round 2", out["head"])
        self.assertIn("1/4 cards done", out["head"])
        self.assertEqual(out["bars"][0], "minutes 12.5 / 45 min|27.8%")
        self.assertTrue(out["bars"][1].startswith("tokens 250,000 / 1,000,000"), out["bars"])
        self.assertIn("(no limit)", out["bars"][2])
        self.assertEqual(out["doing"], "waiting for you: install six")
        self.assertEqual(out["rows"][:2], ["you:You asked: read the data",
                                           "run:The contract"])
        self.assertIn("worker:read_file(data.csv) → 120 characters", out["rows"])
        self.assertIn("waiting for your answer", out["answer"])
        self.assertIn("The agent asks to install six", out["ask"])
        self.assertIn("Why: tables", out["ask"])
        posts = out["posts"]
        self.assertEqual(posts[0], {"p": "/api/work/approve", "body": {
            "run": "run_1", "request": "req_1", "allow": True}})
        self.assertEqual(posts[1], {"p": "/api/work/approve", "body": {
            "run": "run_1", "request": "req_1", "allow": False}})
        self.assertIn("approved", out["answered"])
        self.assertEqual(out["asksAfter"], 0)
        self.assertTrue(out["pauseButton"])
        self.assertTrue(out["stopBeside"])
        self.assertFalse(out["pauseInLine"])
        self.assertEqual(posts[2], {"p": "/api/work/pause", "body": {"run": "run_1"}})
        self.assertEqual(out["pausedState"], "paused")
        self.assertEqual(out["titleState"], "paused")
        self.assertEqual(out["pausedDoing"], "paused before write_file")
        self.assertTrue(out["spinHidden"])
        self.assertEqual(posts[3], {"p": "/api/work/resume", "body": {"run": "run_1"}})
        # The page did not move; the transcript followed while at its
        # bottom, and stayed where it was put once scrolled up.
        self.assertEqual(out["pageY"][0], 300)
        self.assertEqual(out["pageY"][0], out["pageY"][1])
        self.assertEqual(out["pageY2"], out["pageY"][0])
        self.assertGreater(out["scrollable"], 0, "the transcript scrolls in its own box")
        self.assertTrue(out["followed"])
        self.assertEqual(out["keptUp"], 0)
        self.assertEqual(out["count"], 65)


if __name__ == "__main__":
    unittest.main()


class ARunWaitingOnItsModelOnThePageTests(unittest.TestCase):
    """A run waiting on its model, on the page: the card in its session -
    what failed, when the limit resets, where to mend it, Try again now and
    End the run - and, wherever you are, its state beside the project in
    the list and how many runs wait for you on the tabs."""

    def setUp(self) -> None:
        from test_panels import _javascript_engine
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_the_card_and_the_badges(self) -> None:
        import html
        import re
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        run = {"id": "run_1", "folder": "C:/w/agent_work/x", "alive": True,
               "status": "waiting for you", "continuable": False, "paused": False,
               "started_at": "2026-10-08T12:00:00", "instruction": "read the data",
               "final": {}, "log": [], "versions": {"count": 1, "head": "v1"}, "plan": {}}
        ask = {"id": "model_1", "kind": "model", "trouble": "limit",
               "title": "Claude Code: usage limit reached",
               "what": "You have hit your session limit - resets 11:50pm",
               "provider": "claude_code", "provider_name": "Claude Code",
               "state": "rate_limited", "status": "pending",
               "resets_at": "2099-01-01T05:50:00+00:00",
               "next_look": "2099-01-01T05:51:00+00:00", "looks": 2,
               "fix": {"action": "settings", "provider": "claude_code",
                       "label": "Choose another model in Settings"},
               "note": "It goes on by itself a minute after the limit resets."}
        live = {"run": "run_1", "events": [{
                    "seq": 1, "at": "2026-10-08T12:00:01+00:00", "who": "run",
                    "kind": "permission", "text": "The usage limit is reached",
                    "detail": {"request": "model_1"}}],
                "last": 1, "state": "waiting_for_you", "status": "waiting for you",
                "resumes_from": None,
                "requests": {"model_1": {"status": "pending", "note": "", "kind": "model"}},
                "pending": [ask],
                "progress": {"stage": "round 2", "phase": "rounds", "round": 1,
                             "rounds_done": 1, "cards": 4, "cards_done": 1,
                             "used": {"minutes": 1, "tokens": 10, "calls": 1},
                             "limits": {"minutes": 45, "tokens": 1000000, "calls": 0},
                             "paused_minutes": 0,
                             "activity": "waiting for you: the usage limit",
                             "pause_asked": False, "held": False, "stopping": False}}
        going = {"going": [
            {"run": "run_1", "unit": "task-live", "debug": False, "state": "waiting_for_you",
             "waiting_on": "model", "why": "the usage limit - it resets at 23:50",
             "activity": ""},
            {"run": "run_2", "unit": "dbg", "debug": True, "state": "waiting_for_you",
             "waiting_on": "model", "why": "the model", "activity": ""},
            {"run": "run_3", "unit": "dbg", "debug": True, "state": "going",
             "waiting_on": "", "why": "", "activity": ""}]}
        stub = {"/api/work/runs": {"runs": [run]},
                "/api/work/folders": {"folders": [], "warning": ""},
                "/api/work/limits": {"defaults": {"minutes": 45, "tokens": 1000000,
                                                  "calls": 200}},
                "/api/work/tree": {"cards": [], "stages": [], "edges": []},
                "/api/work/live": live, "/api/work/going": going}
        steps = (
            "activeTab='experience';"
            "document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.id==='experience'));"
            "experienceState.units=[{id:'task-live',name:'Live work',kind:'task',stages:3},"
            "  {id:'other',name:'Other',kind:'task',stages:1}];xpDrawUnits();"
            "await wait(1800);"
            "out.badges=[...document.querySelectorAll('#xp-units .xp-unit-run')].map(s=>s.textContent.trim());"
            "out.badgeTitle=document.querySelector('#xp-units .xp-unit-run .xp-live-state').title;"
            "out.tabs=[...document.querySelectorAll('#tabs .tab-wait')].map(s=>"
            "  s.closest('button').dataset.tab+':'+s.textContent);"
            "experienceState.unit='task-live';await xpLoadRuns();await wait(1300);"
            "const box=document.querySelector('[data-xp-live-body=\"run_1\"]');"
            "const card=box.querySelector('.xp-live-asks .xp-model-ask');"
            "out.card=card.textContent.replace(/\\s+/g,' ').trim();"
            "out.buttons=[...card.querySelectorAll('button')].map(b=>b.textContent.trim());"
            "out.answer=box.querySelector('[data-xp-live-req=\"model_1\"]').textContent;"
            "card.querySelector('[data-xp-allow=\"1\"]').dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "await wait(300);"
            "box.querySelector('.xp-model-ask [data-xp-allow=\"0\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait(300);"
            "box.querySelector('.xp-model-ask [data-xp-model-fix]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait(500);"
            "out.settings=document.querySelector('#tabs button[data-tab=\"settings\"]').classList.contains('active');"
            "out.posts=window.__posts;")
        harness = (
            "<style>" + (web / "styles.css").read_text(encoding="utf-8") + "</style>"
            "<script>window.__posts=[];const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u,o)=>{const p=String(u).split('?')[0];"
            "if(o&&o.method==='POST')window.__posts.push({p,body:JSON.parse(o.body||'{}')});"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';const out={};"
            "const wait=(ms)=>new Promise(r=>setTimeout(r,ms||80));"
            "try{" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err,out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=20000",
                 "--window-size=1200,800", "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "the page never finished rendering")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "", result["err"])
        out = result["out"]
        # Beside the project: waiting for you, and on what; the other, nothing.
        self.assertEqual(out["badges"], ["waiting for you: the model", ""])
        self.assertIn("resets at 23:50", out["badgeTitle"])
        # On the tabs: one of yours waits, one of the debugger's.
        self.assertEqual(sorted(out["tabs"]), ["experience:1 waiting", "xdebug:1 waiting"])
        self.assertIn("Claude Code: usage limit reached", out["card"])
        self.assertIn("You have hit your session limit", out["card"])
        self.assertIn("Resets at", out["card"])
        self.assertIn("Looks again by itself at", out["card"])
        self.assertIn("looked 2 times so far", out["card"])
        self.assertEqual(out["buttons"], ["Choose another model in Settings", "Try again now",
                                          "End the run"])
        self.assertIn("waiting for your answer", out["answer"])
        self.assertEqual(out["posts"][0], {"p": "/api/work/approve", "body": {
            "run": "run_1", "request": "model_1", "allow": True}})
        self.assertEqual(out["posts"][1], {"p": "/api/work/approve", "body": {
            "run": "run_1", "request": "model_1", "allow": False}})
        self.assertTrue(out["settings"])

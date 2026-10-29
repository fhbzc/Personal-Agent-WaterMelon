"""A BUILDER CALL HANDED OVER BEFORE IT GROWS LONG (the user, 2026-10-28):
past CALL_MOST_STEPS tool steps or CALL_MOST_MINUTES minutes, the work
agent writes a handover note, and a fresh call goes on from it - the
brief as it is now, the note, the files the round wrote. Natively called
tools are counted and stopped the same way; the note is kept with a
progress point, and a round gone on from that point goes on from it."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from test_work_runs import _TwoAgents
from test_work_versions import _RunsBase

from personalized_agent.agent.work import runner as runner_module
from personalized_agent.agent.work.versions import VersionStore

NOTE = {"card": "", "done": ["notes.md begun"], "next": ["finish the greeting in notes.md"],
        "files": ["notes.md - the greeting, half written"], "problems": [],
        "checks": ["read notes.md: it says hel"]}


class _LongBuilder(_TwoAgents):
    """A work agent that would go on calling tools: each call of a round
    writes until it is asked for its note - a call that goes on from a
    note ends its round after `then` tools."""

    def __init__(self, then=2, **kwargs):
        kwargs.setdefault("full_review", "never")
        super().__init__(**kwargs)
        self.then = then
        self.notes_asked = 0
        self.firsts = []

    def invoke(self, request, role=""):
        from personalized_agent.models.base import ToolCall
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        last = request.messages[-1]["content"]
        said = last if isinstance(last, str) else json.dumps(last)
        if "You review a work agent" in request.system or "WORK ONE ROUND" not in first:
            return super().invoke(request, role)
        self.asked.append(request)
        if len(request.messages) == 1:
            self.firsts.append(first)
        if "THIS CALL HAS GONE ON LONG ENOUGH" in said:
            self.notes_asked += 1
            self.assertNoTools(request)
            return self._reply(json.dumps({"handover": NOTE}))
        if "FAILED THE CHECKS" in said or "THE RUN IS PAUSING HERE" in said:
            return self._reply(json.dumps(self._summary(first)))
        tools_so_far = sum(1 for one in request.messages if one["role"] == "assistant")
        if "GOES ON FROM AN EARLIER CALL" in first and tools_so_far >= self.then:
            return self._reply(json.dumps(self._summary(first)))
        return self._reply("", [ToolCall(id=f"c{tools_so_far}", name="write_file",
                                         arguments={"path": "notes.md", "content": "hello"})])

    @staticmethod
    def assertNoTools(request):
        assert not request.tools, "the note is asked for with no tools"


class AtItsCapACallHandsOverTests(_RunsBase):

    def _capped(self, steps=3, handovers=None):
        patches = [mock.patch.object(runner_module, "CALL_MOST_STEPS", steps)]
        if handovers is not None:
            patches.append(mock.patch.object(runner_module, "HANDOVERS_PER_ROUND", handovers))
        for one in patches:
            one.start()
            self.addCleanup(one.stop)

    def test_the_note_asked_for_and_a_fresh_call_goes_on_from_it(self) -> None:
        self._capped(steps=3)
        models = _LongBuilder()
        run, folder = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertEqual(models.notes_asked, 1)
        handed = [one for one in run["log"] if one.get("what") == "builder call handed over"]
        self.assertEqual(len(handed), 1, run["log"][-10:])
        self.assertEqual(handed[0]["steps"], 3, "the cap counted its tool steps")
        self.assertEqual(handed[0]["next"], ["finish the greeting in notes.md"])
        # The fresh call: one message - the brief, the note, the files the
        # round wrote, the round's ask - nothing of the call before.
        fresh = [one for one in models.firsts if "GOES ON FROM AN EARLIER CALL" in one]
        self.assertEqual(len(fresh), 1)
        self.assertIn("call 2 of this round", fresh[0])
        self.assertIn("finish the greeting in notes.md", fresh[0])
        self.assertIn("notes.md", fresh[0].split("The files the round wrote so far:")[1])
        self.assertIn("WORK ONE ROUND", fresh[0])
        self.assertNotIn("THIS CALL HAS GONE ON LONG ENOUGH", fresh[0])
        # Said in the live session.
        events = self.app.work.live(run["id"])["events"]
        self.assertTrue(any(one["kind"] == "handover" for one in events))
        # Over once the round is: not left in the plan.
        self.assertNotIn("handover", run["plan"])

    def test_past_the_handovers_a_round_has_the_last_call_finishes(self) -> None:
        self._capped(steps=2, handovers=1)
        models = _LongBuilder(then=4)
        run, _folder = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertEqual(models.notes_asked, 1, "the second call was not capped")

    def test_uncapped_without_its_cap(self) -> None:
        run, _folder = self._run(_TwoAgents(full_review="never"))
        self.assertFalse([one for one in run["log"]
                          if one.get("what") == "builder call handed over"])

    def test_a_round_gone_on_from_a_progress_point_goes_on_from_its_note(self) -> None:
        from personalized_agent.agent.work.runner import INTERRUPTED
        run, folder = self._run(_TwoAgents(rounds_to_complete=99, full_review="never"),
                                limits={"calls": 14, "minutes": 0, "tokens": 0})
        store = VersionStore(folder / "runs" / run["id"])
        point = store.resume_point()
        state = store.get_json(point["state"])
        done = int(state["plan"].get("round") or 0)
        state["plan"]["handover"] = {"round": done + 1, "calls": 2, "note": NOTE,
                                     "written": [str(folder / "notes.md")]}
        store.save_resume(f"in round {done + 1}, at 10:00", point["files"], {}, [],
                          store.put_json(state), {"round": done, "in_round": done + 1})
        self.app.db.execute("UPDATE work_runs SET status = ? WHERE id = ?",
                            (INTERRUPTED, run["id"]))
        agents = _TwoAgents(rounds_to_complete=1)
        said, _after = self._continue(run["id"], agents)
        self.assertTrue(said["rolled_back"]["resume_point"])
        told = {str(request.messages[0]["content"]) for request in agents.asked
                if "GOES ON FROM AN EARLIER CALL" in str(request.messages[0]["content"])}
        self.assertEqual(len(told), 1, "told once, in the round it was handed over in")
        text = told.pop()
        self.assertIn("call 3 of this round", text)
        self.assertIn("finish the greeting in notes.md", text)


class TheMinutesAreTheModelsOwnTests(_RunsBase):
    """THE CAP'S MINUTES ARE THE MODEL'S OWN (the user, 2026-10-29): tools
    running long do not hand a call over; the model taking long does."""

    def _cap(self, minutes):
        for name, value in (("CALL_MOST_MINUTES", minutes), ("CALL_MOST_STEPS", 1000)):
            patcher = mock.patch.object(runner_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_tools_running_long_do_not_hand_it_over(self) -> None:
        import time as _time
        self._cap(0.02)                       # 1.2 s of the model's own
        models = _LongBuilder(then=3)
        real = runner_module._run_calls

        def slow(tools, batch):
            _time.sleep(0.6)                  # code running: 1.8 s over three
            return real(tools, batch)
        with mock.patch.object(runner_module, "_run_calls", slow):
            models.calls_without_note = 3
            run, _folder = self._run(_Finishes(models))
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertEqual(models.notes_asked, 0, "its tools' time is not the model's")

    def test_the_model_taking_long_does(self) -> None:
        import time as _time
        self._cap(0.02)
        models = _LongBuilder(then=2)
        think = models.invoke

        def slow(request, role=""):
            if "WORK ONE ROUND" in json.dumps(request.messages[0]["content"]) \
                    and "You review a work agent" not in request.system:
                _time.sleep(0.7)              # the model thinking
            return think(request, role)
        models.invoke = slow
        run, _folder = self._run(models)
        self.assertEqual(run["status"], "done", run["log"][-6:])
        self.assertGreaterEqual(models.notes_asked, 1)
        handed = [one for one in run["log"] if one.get("what") == "builder call handed over"]
        self.assertGreaterEqual(handed[0]["model_minutes"], 0.0)
        self.assertIn("of the model's own", json.dumps(self.app.work.live(run["id"])["events"]))


class _Finishes:
    """A builder that ends its round after a few tools, in its first call."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def invoke(self, request, role=""):
        from personalized_agent.models.base import ToolCall
        first = request.messages[0]["content"]
        first = first if isinstance(first, str) else json.dumps(first)
        if "You review a work agent" in request.system or "WORK ONE ROUND" not in first:
            return self.inner.invoke(request, role)
        last = request.messages[-1]["content"]
        said = last if isinstance(last, str) else json.dumps(last)
        if "THIS CALL HAS GONE ON LONG ENOUGH" in said:
            return self.inner.invoke(request, role)
        if "FAILED THE CHECKS" in said or "THE RUN IS PAUSING HERE" in said:
            return self.inner.invoke(request, role)
        done = sum(1 for one in request.messages if one["role"] == "assistant")
        if done >= self.inner.calls_without_note:
            return self.inner._reply(json.dumps(self.inner._summary(first)))
        return self.inner._reply("", [ToolCall(id=f"c{done}", name="write_file",
                                               arguments={"path": "notes.md", "content": "hello"})])


class NativeToolsStopAtTheCapTests(unittest.TestCase):

    def test_a_tool_past_the_cap_asks_for_the_note(self) -> None:
        from personalized_agent.agent.work.runner import Run
        ran = []
        run = SimpleNamespace(
            stop_now=False, stopping=False, activity="",
            _hold=lambda *_: None, _going=lambda **_: True,
            owner=SimpleNamespace(save=lambda r: None),
            NATIVE_CALLS_PER_ROUND=Run.NATIVE_CALLS_PER_ROUND)
        tools = SimpleNamespace(call=lambda name, args: ran.append(name) or {"ok": True})
        specs = [SimpleNamespace(name="write_file")]
        steps = {"n": 0, "asked": False}
        cap = 2

        def capped():
            return not steps["asked"] and steps["n"] >= cap

        def hand_over():
            steps["asked"] = True
            return "THIS CALL HAS GONE ON LONG ENOUGH - the note"
        go = Run._tool_runner(run, tools, specs, "worker", True, False, rounds_left=10,
                              steps=steps, capped=capped, hand_over=hand_over)
        self.assertEqual(go("write_file", {}), {"ok": True})
        self.assertEqual(go("write_file", {}), {"ok": True})
        self.assertEqual(steps["n"], 2, "counted with the run's own loop's")
        said = go("write_file", {})
        self.assertIn("not run: THIS CALL HAS GONE ON LONG ENOUGH", said["error"])
        # And every one after it, until the call answers.
        self.assertIn("LONG ENOUGH", go("write_file", {})["error"])
        self.assertEqual(len(ran), 2)

    def test_the_note_asks_for_what_a_fresh_call_needs(self) -> None:
        from personalized_agent.agent.work.runner import HAND_OVER
        text = HAND_OVER.format(steps=40, minutes=15)
        start = text.index("{")
        note = json.loads(text[start:])["handover"]
        self.assertEqual(set(note), {"card", "done", "next", "files", "problems", "checks"})


if __name__ == "__main__":
    unittest.main()

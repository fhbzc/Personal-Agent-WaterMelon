"""RESUME WITHOUT A HITCH, THE TOKENS IN EVERY BUILD, AND THE OLD PROGRESS
POINTS SHOWN (the user, 2026-10-27).

- A run in a process of its own read what it was told twice a second, and
  the page, asking at once, was answered as before: Resume drawn again. It
  reads ten times a second now, says at once what it did, and the agent
  waits (a second at most) to hear it.
- The Overview's Token usage asked the Debugger's routes, which a released
  build does not serve: it was empty for everyone.
- A progress point kept before its own line existed shows as one now.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401

from personalized_agent.agent.work import child

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"


class TheChildSaysItAtOnceTests(unittest.TestCase):

    def _child_run(self, folder: Path) -> child.ChildRun:
        run = child.ChildRun.__new__(child.ChildRun)
        run.dir = folder
        run.process = None
        return run

    def test_the_agent_waits_to_hear_it_done(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            run = self._child_run(folder)
            child._write_json(folder / child.SNAPSHOT, {"alive": True})

            def obey_soon() -> None:
                # The child, a moment later: what it was told, done and said.
                time.sleep(0.2)
                line = (folder / child.CONTROL).read_text(encoding="utf-8").splitlines()[-1]
                child._write_json(folder / child.SNAPSHOT,
                                  {"alive": True, "obeyed": [json.loads(line)["id"]]})
            threading.Thread(target=obey_soon).start()
            started = time.monotonic()
            self.assertTrue(run.resume())
            waited = time.monotonic() - started
        self.assertGreaterEqual(waited, 0.15, "waited for the child")
        self.assertLess(waited, child.HEARD_WITHIN, "and no longer than it took")

    def test_not_heard_it_goes_on_after_a_second(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            run = self._child_run(folder)
            child._write_json(folder / child.SNAPSHOT, {"alive": True})
            with mock.patch.object(child, "HEARD_WITHIN", 0.2):
                started = time.monotonic()
                self.assertTrue(run.pause())
                self.assertLess(time.monotonic() - started, 1.0)

    def test_the_child_reads_often_and_writes_what_it_did_at_once(self) -> None:
        self.assertLessEqual(child.ORDERS_EVERY, 0.1)
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            done = threading.Event()
            fake = SimpleNamespace(alive=lambda: not done.is_set(),
                                   resume=mock.Mock(), pause=mock.Mock())
            with mock.patch.object(child, "snapshot", return_value={"alive": True}), \
                    mock.patch.object(child, "BEAT_SECONDS", 60.0):
                # The beat is a minute: only having done something writes now.
                follower = threading.Thread(target=child._follow, args=(fake, folder, 0))
                follower.start()
                time.sleep(0.15)
                (folder / child.CONTROL).write_text(json.dumps(
                    {"id": "order1", "do": "resume", "why": ""}) + "\n", encoding="utf-8")
                heard = None
                until = time.monotonic() + 2
                while time.monotonic() < until:
                    said = child.read_json(folder / child.SNAPSHOT) or {}
                    if "order1" in (said.get("obeyed") or []):
                        heard = time.monotonic()
                        break
                    time.sleep(0.02)
                done.set()
                follower.join(5)
        self.assertIsNotNone(heard, "said at once, not at the next beat")
        fake.resume.assert_called_once()


class TokensInEveryBuildTests(unittest.TestCase):

    def test_served_without_the_debugger(self) -> None:
        from personalized_agent.api.routes import build_routes
        metrics = {"tokens_over_time": [{"timestamp": "t", "total_tokens": 5}],
                   "measured_since": "s", "by_task": {"chat": {}}, "totals": {},
                   "recent_calls": [{"anything": "not sent"}]}
        app = SimpleNamespace(
            observability=SimpleNamespace(snapshot=lambda: {"model_metrics": metrics}),
            model_calls=SimpleNamespace(by_day=lambda days, minutes_east:
                                        {"days": [days], "rows": [minutes_east]}))
        with mock.patch("personalized_agent.api.routes.debugger_available", return_value=False):
            routes = build_routes(app)
        self.assertFalse(any(path.startswith("/api/debugger") for _m, path in routes))
        said = routes[("GET", "/api/tokens")](SimpleNamespace())
        self.assertEqual(said["tokens_over_time"], metrics["tokens_over_time"])
        self.assertNotIn("recent_calls", said, "only what the chart draws")
        request = SimpleNamespace(int_param=lambda name, default: {"days": 7, "east": -300}[name])
        self.assertEqual(routes[("GET", "/api/tokens/by_day")](request),
                         {"days": [7], "rows": [-300]})

    def test_the_overview_asks_them(self) -> None:
        source = (WEB / "app.js").read_text(encoding="utf-8")
        load = source[source.index("async function loadTokenUsage"):]
        load = load[:load.index("\n}\n")]
        self.assertIn('api.get("/api/tokens")', load)
        self.assertNotIn("/api/debugger", load)
        days = source[source.index("async function loadTokenDays"):]
        days = days[:days.index("\n}\n")]
        self.assertIn("/api/tokens/by_day", days)


class TheOldProgressPointsTests(unittest.TestCase):

    def _row(self, event):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is needed")
        source = (WEB / "app.js").read_text(encoding="utf-8")
        start = source.index("function xpLiveRow(")
        row = source[start:source.index("\n}\n", start) + 3]
        script = ("const XP_LIVE_WHO = {run: 'Run'};"
                  "function escapeHtml(s) { return String(s); }"
                  + row + "process.stdout.write(xpLiveRow(" + json.dumps(event) + "));")
        done = subprocess.run([node, "-e", script], capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_a_bare_note_shows_as_a_saved_point(self) -> None:
        html = self._row({"seq": 1, "at": "2026-10-27T16:10:02+00:00", "who": "run",
                          "kind": "note", "text": "resume point saved (round 3)"})
        self.assertIn("xp-live-kind-saved", html)
        self.assertIn("Saved a progress point", html)

    def test_another_note_stays_a_note(self) -> None:
        html = self._row({"seq": 2, "at": "2026-10-27T16:10:02+00:00", "who": "run",
                          "kind": "note", "text": "contract reviewed"})
        self.assertIn("xp-live-kind-note", html)

    def test_the_button_says_what_is_being_done(self) -> None:
        source = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('clicked.textContent = pauseAt ? "Pausing…" : "Resuming…";', source)


if __name__ == "__main__":
    unittest.main()

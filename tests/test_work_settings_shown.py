"""TWO PLACES FOR SETTINGS (the user, 2026-10-27): a run's ACTIVE SPEC on a
card at the top of its live session - changed there, sent to it at once - and
Work's settings for the NEXT start or Continue, never sent to a run that
goes. And Work's line of the going run, as the run is now."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    return source[start:source.index("\n}\n", start) + 3]


class TheRunsOwnSpecTests(unittest.TestCase):

    def _record(self, run, progress=None):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is needed")
        source = (WEB / "app.js").read_text(encoding="utf-8")
        script = (_function(source, "xpRecordSpec") + "process.stdout.write(JSON.stringify("
                  "xpRecordSpec(" + json.dumps(run) + "," + json.dumps(progress) + ")));")
        done = subprocess.run([node, "-e", script], capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_from_its_record_when_its_process_says_none(self) -> None:
        run = {"id": "run_1", "plan": {"way": "quick", "limits": {"minutes": 30},
               "packages": {"mode": "ask"}, "unconfined": True,
               "sessions": [{"limits": {"minutes": 45}, "sound": True}]}}
        self.assertEqual(self._record(run), {"way": "quick", "limits": {"minutes": 45},
                                             "packages": "ask", "packages_chosen": False,
                                             "sandbox": False, "sound": True})

    def test_the_card_sends_to_the_run_and_works_settings_do_not(self) -> None:
        source = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('closest("[data-xp-active-spec]")', source)
        self.assertIn('await api.post("/api/work/settings", { run: runId, limits,', source)
        # Work's own settings post nowhere while a run goes.
        self.assertNotIn("XP_SETTINGS_NOW", source)
        self.assertNotIn("xpGoingSpec", source)
        self.assertIn("XP_SETTINGS_NEXT", source)
        # The card is not drawn again under the user's hand.
        self.assertIn('document.activeElement.closest(".xp-active-spec")', source)


class WorksLineIsAsTheRunIsNowTests(unittest.TestCase):
    """The user, 2026-10-27: resumed from the to-do, and Work still showed
    Resume - its line was drawn from what the panel last loaded, and the
    panel stopped asking while another tab was shown."""

    def test_the_line_takes_the_runs_going_now(self) -> None:
        source = (WEB / "app.js").read_text(encoding="utf-8")
        line = _function(source, "xpDrawTitleRun")
        self.assertIn("(workGoing.byUnit || {})[going.unit_id]", line)
        self.assertIn("now && now.run === going.id", line)
        watch = source[source.index("async function watchWorkGoing"):]
        watch = watch[:watch.index("\n}\n")]
        self.assertIn("xpDrawTitleRun(titled)", watch)

    def test_the_panel_keeps_asking_on_another_tab(self) -> None:
        source = (WEB / "app.js").read_text(encoding="utf-8")
        timer = _function(source, "xpRunsTimer")
        self.assertIn("else xpRunsTimer(true);", timer)


if __name__ == "__main__":
    unittest.main()

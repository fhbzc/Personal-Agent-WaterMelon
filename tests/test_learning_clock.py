"""THE OLD Work learning clock (heartbeat/experience/auto.py): at the times
chosen, the projects that changed and are ticked; Start and Update held
while it runs; a run the application's closing cut short carried on when it
starts again. Kept, but no longer started by the application nor shown on
the page - the learning started again (learning/, 2026-10-21): these test
the class itself."""

from __future__ import annotations

import os
import time
import unittest
from datetime import datetime, timedelta

from support import TemporaryAgent
from test_experience_projects import _Request

from personalized_agent.heartbeat.experience.auto import LAST, RUN


class _ClockBase(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.experience = self.app.state_heartbeat.experience
        self.clock = self.app.learning_clock
        from personalized_agent.api.routes import build_routes
        self.routes = build_routes(self.app)
        # Nothing is really learned: what was asked for is written down.
        self.asked = []
        real = self.experience.learn

        def learn(units, auto=False):
            if not auto and self.experience.auto_running():
                return real(units, auto=auto)
            self.asked.append((list(units), auto))
            return list(units)
        self.experience.learn = learn

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _project(self, name):
        folder = self.agent.dir / name.lower().replace(" ", "-")
        folder.mkdir()
        (folder / "notes.txt").write_text("the first notes", encoding="utf-8")
        said = self.routes[("POST", "/api/experience/projects")](_Request(
            {"action": "create", "name": name, "paths": [str(folder)]}))
        return said["project"]["unit_id"], folder

    def _read_to(self, unit, when):
        store = self.experience.store
        store.db.execute("UPDATE experience_workspaces SET processed_until = ? "
                         "WHERE unit_id = ?", (when, store._key(unit)))


class WhenItIsDueTests(_ClockBase):

    def test_once_for_each_time_that_has_passed(self) -> None:
        self.clock.set_times("03:00")
        now = datetime.now().astimezone().replace(hour=4, minute=0)
        self.assertTrue(self.clock.due(now))
        self.clock.start_run(now)
        self.assertFalse(self.clock.due(now))
        self.assertFalse(self.clock.due(now + timedelta(hours=20)))
        self.assertTrue(self.clock.due(now + timedelta(days=1)))

    def test_no_time_no_run(self) -> None:
        self.clock.set_times("")
        self.assertFalse(self.clock.due())


class WhatIsUpdatedTests(_ClockBase):

    def test_a_ticked_project_whose_folder_changed(self) -> None:
        changed, folder = self._project("Garden planner")
        still, _other = self._project("Pond survey")
        later = datetime.now().astimezone() + timedelta(minutes=5)
        self._read_to(still, later.isoformat())
        # Read before the notes were written: changed since.
        self._read_to(changed, "2026-01-01T00:00:00+00:00")
        off, _third = self._project("Bird count")
        self._read_to(off, "2026-01-01T00:00:00+00:00")
        self.clock.set_opted(off, False)
        self.assertEqual(self.clock.wanted(), [changed])
        self.assertFalse([u for u in self.experience.units() if u["id"] == off][0]["auto"])

    def test_the_run_learns_them_as_the_button_would(self) -> None:
        unit, _folder = self._project("Garden planner")
        self._read_to(unit, "2026-01-01T00:00:00+00:00")
        self.clock.set_times("03:00")
        self.clock.start_run(datetime.now().astimezone().replace(hour=4, minute=0))
        self.assertEqual(self.asked, [([unit], True)])
        self.assertTrue(self.clock.running())


class OneRunAtATimeTests(_ClockBase):

    def test_the_buttons_wait_while_it_runs(self) -> None:
        self.app.config.set(RUN, {"units": ["proj_x"], "finished": [],
                                  "started": "2026-10-17T03:00:00+00:00"}, internal=True)
        self.clock._taken.add("proj_x")
        self.experience.learning_progress["proj_x"] = {"running": True}
        try:
            said = self.routes[("POST", "/api/experience/learn")](_Request({"units": ["proj_y"]}))
        finally:
            self.experience.learning_progress.pop("proj_x", None)
        self.assertIn("error", said)
        self.assertEqual(self.asked, [])

    def test_cut_short_by_closing_it_carries_on_at_the_next_start(self) -> None:
        unit, _folder = self._project("Garden planner")
        self.app.config.set(RUN, {"units": [unit], "finished": [],
                                  "started": "2026-10-17T03:00:00+00:00"}, internal=True)
        # While the application closes, nothing is read as finished.
        self.experience._stopping = True
        self.clock.follow()
        self.assertEqual(self.asked, [])
        self.assertTrue(self.clock.running())
        # Started again: the run's project is taken on again, by itself.
        self.experience._stopping = False
        self.clock.follow()
        self.assertEqual(self.asked, [([unit], True)])
        # Its learning ended: the run is over, and the buttons work again.
        self.clock.follow()
        self.assertFalse(self.clock.running())
        self.assertFalse(self.experience.auto_running())

    def test_its_work_natures_are_grouped_when_it_ends(self) -> None:
        from unittest import mock
        from personalized_agent.data_manage.library import USER_NATURE, Record
        unit, _folder = self._project("Garden planner")
        self.app.config.set(RUN, {"units": [unit], "finished": [],
                                  "started": "2000-01-01T00:00:00+00:00"}, internal=True)
        self.clock.follow()
        self.app.library.write(Record(
            folder=USER_NATURE, title="Checks the beds by hand", summary="checks by hand",
            body="checks by hand", source="experience",
            metadata={"nature_kind": "work"}))
        with mock.patch.object(self.experience, "start_patterns") as grouped:
            self.clock.follow()
        self.assertFalse(self.clock.running())
        grouped.assert_called_once()
        self.assertTrue(self.experience._regroup_now)
        self.assertEqual(self.app.config.get("experience_auto_done")["regrouped"], 1)

    def test_no_new_natures_no_grouping(self) -> None:
        from unittest import mock
        unit, _folder = self._project("Garden planner")
        self.app.config.set(RUN, {"units": [unit], "finished": [],
                                  "started": "2999-01-01T00:00:00+00:00"}, internal=True)
        self.clock.follow()
        with mock.patch.object(self.experience, "start_patterns") as grouped:
            self.clock.follow()
        grouped.assert_not_called()

    def test_the_page_no_longer_shows_it(self) -> None:
        """Its per-project ticks and its readout are gone from Work: the
        learning's own panel (learning/) is in their place."""
        from pathlib import Path
        script = (Path(__file__).resolve().parents[1] / "personalized_agent" / "api"
                  / "web" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("data-xp-auto-unit", script)
        self.assertNotIn("Automatic learning is running", script)
        self.assertNotIn("/api/experience/auto", script)


if __name__ == "__main__":
    unittest.main()

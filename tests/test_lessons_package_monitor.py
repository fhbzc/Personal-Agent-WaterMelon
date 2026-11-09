"""THE MONITOR CARRIED WITH THE LESSONS (the user, 2026-11-07): what the application gives is the
default, what the user adds or changes is theirs - packed, and merged on another computer."""

from __future__ import annotations

import time
import unittest

import support  # noqa: F401
from support import TemporaryAgent


class MonitorCarriedTests(unittest.TestCase):

    def setUp(self) -> None:
        self.here = TemporaryAgent()
        self.there = TemporaryAgent()
        self.addCleanup(self.here.cleanup)
        self.addCleanup(self.there.cleanup)

    def _builtin(self, app, key):
        found = [one for one in app.monitor.store.list() if one["builtin"] == key]
        self.assertLessEqual(len(found), 1, f"one {key}, not two")
        return found[0] if found else None

    def _carry(self):
        stats = self.here.app.lessons_package.package()
        return self.there.app.lessons_package.load(stats["zip_path"])

    def test_what_the_user_made_of_it_goes_with_them(self) -> None:
        store = self.here.app.monitor.store
        store.edit(self._builtin(self.here.app, "old_project")["id"], {"cooldown_minutes": 12},
                   "user", "fewer looks")
        store.delete(self._builtin(self.here.app, "leisure")["id"], "user", "rests alone")
        mine, _w = store.add({"name": "Tide tables updated", "group": "other", "purpose": "x",
                              "when": {"event": "back"},
                              "then": [{"do": "notify", "title": "Tides", "message": "New tables"}]},
                             "user", "my own")
        quiet = store.quiet_rules()[0]
        store.remove_quiet(quiet["id"], "user", "not this one")
        time.sleep(0.01)
        report = self._carry()
        there = self.there.app
        self.assertEqual(self._builtin(there, "old_project")["cooldown_minutes"], 12,
                         "the one the user changed, over the default there")
        self.assertIsNone(self._builtin(there, "leisure"), "deleted here, deleted there")
        self.assertIn(mine, [one["id"] for one in there.monitor.store.list()])
        self.assertNotIn(quiet["data"]["name"] if "data" in quiet else quiet.get("name"),
                         [one.get("name") or (one.get("data") or {}).get("name")
                          for one in there.monitor.store.quiet_rules()])
        self.assertIn("monitor_activator", report["kinds"])
        # Started again there: the change carried says it is the user's, so the default stays off it.
        there.monitor._write_builtins()
        self.assertEqual(self._builtin(there, "old_project")["cooldown_minutes"], 12)
        self.assertIsNone(self._builtin(there, "leisure"), "and not written again")

    def test_what_the_user_changed_there_stays_over_a_default_from_here(self) -> None:
        there = self.there.app
        there.monitor.store.edit(self._builtin(there, "old_project")["id"],
                                 {"cooldown_minutes": 45}, "user", "theirs there")
        self._carry()
        self.assertEqual(self._builtin(there, "old_project")["cooldown_minutes"], 45)

    def test_loading_again_changes_nothing(self) -> None:
        self._carry()
        before = [(one["id"], one["cooldown_minutes"]) for one in self.there.app.monitor.store.list()]
        report = self._carry()
        after = [(one["id"], one["cooldown_minutes"]) for one in self.there.app.monitor.store.list()]
        self.assertEqual(before, after)
        self.assertEqual(report["kinds"]["monitor_activator"].get("added", 0), 0)


if __name__ == "__main__":
    unittest.main()

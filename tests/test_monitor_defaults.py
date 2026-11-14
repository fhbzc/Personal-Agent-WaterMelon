"""WHAT THE APPLICATION GIVES IS THE DEFAULT; WHAT THE USER ADDS OR CHANGES IS THEIRS (the user,
2026-11-07). A built-in Activator whose default changed follows it where no one changed it; one
taken out of the application goes only where no one changed it."""

from __future__ import annotations

import unittest

import support  # noqa: F401
from support import TemporaryAgent


class DefaultsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.monitor = self.agent.app.monitor
        self.store = self.monitor.store

    def _old_project(self):
        return next(one for one in self.store.list() if one["builtin"] == "old_project")

    def _as_given(self, **changed):
        from personalized_agent.heartbeat.monitor.builtins import builtins
        out = []
        for one in builtins():
            if one["builtin"] == "old_project":
                one = dict(one, **changed)
            out.append(one)
        return out

    def test_a_changed_default_reaches_a_copy_no_one_changed(self) -> None:
        self.assertEqual(self._old_project()["cooldown_minutes"], 5)
        self.assertEqual(self.store.refresh(self._as_given(cooldown_minutes=30)), 1)
        self.assertEqual(self._old_project()["cooldown_minutes"], 30)
        why = self.store.changes(self._old_project()["id"])[0]
        self.assertEqual((why["by"], why["op"]), ("built in", "edit"))
        self.assertEqual(self.store.refresh(self._as_given(cooldown_minutes=30)), 0, "said once")

    def test_fixed_it_follows_the_application_and_only_its_switch_is_theirs(self) -> None:
        """2026-11-13: "现在设置的几个monitor都是protected, 不允许改" - switched off by the user, it still follows the
        application's word, and stays off."""
        one = self._old_project()
        self.store.switch(one["id"], False)
        self.store.rename(one["id"], "Old work")
        self.assertEqual(self.store.refresh(self._as_given(cooldown_minutes=30)), 1)
        kept = self._old_project()
        self.assertEqual((kept["cooldown_minutes"], kept["enabled"], kept["display_name"]),
                         (30, False, "Old work"))

    def test_taken_out_of_the_application_only_where_no_one_changed_it(self) -> None:
        mine, _w = self.store.add({"name": "New work begins", "group": "recommend", "purpose": "x",
                                   "when": {"event": "back"},
                                   "then": [{"do": "ask_model", "handler": "recommend", "brief": "x"}]},
                                  "built in", "built in", builtin="new_work")
        theirs, _w = self.store.add({"name": "New work begins", "group": "recommend", "purpose": "x",
                                     "when": {"event": "back"},
                                     "then": [{"do": "ask_model", "handler": "recommend", "brief": "x"}]},
                                    "built in", "built in", builtin="new_work")
        self.store.edit(theirs, {"cooldown_minutes": 90}, "model", "the user asked for fewer")
        self.assertEqual(self.store.retire("new_work", "taken out"), 1)
        kept = [one["id"] for one in self.store.list()]
        self.assertNotIn(mine, kept)
        self.assertIn(theirs, kept, "changed by the model for the user: theirs to keep")

    def test_on_each_start_the_defaults_are_followed(self) -> None:
        one = self._old_project()
        self.agent.app.db.execute("UPDATE monitor_activators SET data = json_set(data, "
                                  "'$.cooldown_minutes', 30) WHERE id = ?", (one["id"],))
        self.assertEqual(self._old_project()["cooldown_minutes"], 30)
        self.monitor._write_builtins()
        self.assertEqual(self._old_project()["cooldown_minutes"], 5,
                         "written when it was 30, the application now says 5")


class HeldOnceTests(unittest.TestCase):
    """The user, 2026-11-07: the check is right, it need not be written down every minute."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.monitor = self.agent.app.monitor

    def _rows(self, ident):
        return self.monitor.db.query("SELECT action, outcome FROM monitor_firings WHERE "
                                     "activator_id = ? ORDER BY at", (ident,))

    def test_held_the_same_way_it_is_written_once_with_why(self) -> None:
        from datetime import datetime, timezone
        from unittest import mock
        one = next(a for a in self.monitor.store.list() if a["builtin"] == "evening_closing")
        reasons = iter(["the evening report is done today"] * 5 + ["not now - the user said so"])
        with mock.patch.object(self.monitor, "_held", side_effect=lambda *a: next(reasons)):
            for _minute in range(6):
                said = self.monitor.fire(one, 1.0, ["the evening"], "tick",
                                         now=datetime.now(timezone.utc))
                self.assertTrue(said["held"])
        rows = self._rows(one["id"])
        self.assertEqual(len(rows), 2, "once for each reason, not each minute")
        self.assertIn("done today", rows[0]["outcome"])
        self.assertIn("the user said so", rows[1]["outcome"])


class InTheirLanguageTests(unittest.TestCase):
    """The user, 2026-11-09: "monitor的description打开好像没有中文翻译"."""

    def test_every_built_in_ones_name_and_description_has_chinese(self) -> None:
        from personalized_agent.common.interface_words import translated
        from personalized_agent.heartbeat.monitor.builtins import builtins
        for one in builtins():
            for said in (one.get("name"), " ".join(str(one.get("purpose") or "").split())):
                self.assertNotEqual(translated(said, "zh"), said, said)


if __name__ == "__main__":
    unittest.main()

"""EVERY NOTIFICATION THROUGH THE MONITOR (the user, 2026-11-13: "所有的通知必须在monitor里，收到monitor的统一调配"; what is
left: recommendations, the calendar, the report, risks, the day ahead and closing up - "其它都取消" - and the
application itself, "系统类的可以保留"; "While presenting, In a meeting 这两个silence对全部monitor都有效").

Nothing outside the Monitor makes a notification; what the rest of the application says goes through
its Activator, switched off or set to wait by the user, held under a silence. Everything here is made up."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import support  # noqa: F401
from support import TemporaryAgent

PACKAGE = Path(__file__).resolve().parents[1] / "personalized_agent"
#: The Monitor's own, and the endpoint itself.
ALLOWED = {PACKAGE / "heartbeat" / "monitor" / "engine.py", PACKAGE / "common" / "notifications.py"}


class NothingAroundItTests(unittest.TestCase):

    def test_no_notification_made_but_by_the_monitor(self) -> None:
        found = []
        for path in PACKAGE.rglob("*.py"):
            if path in ALLOWED:
                continue
            code = path.read_text(encoding="utf-8")
            for match in re.finditer(r"notifications\.notify\s*\(|notify\s*=\s*self\.notifications\.notify\b", code):
                found.append(f"{path.relative_to(PACKAGE)}:{code[:match.start()].count(chr(10)) + 1}")
        self.assertEqual(found, [], "a notification made around the Monitor")

    def test_the_agent_has_no_tool_to_interrupt(self) -> None:
        code = (PACKAGE / "agent" / "core" / "tools.py").read_text(encoding="utf-8")
        self.assertNotIn('ToolSpec("notify_user"', code)


class TheMonitorSaysItTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.monitor = self.app.monitor

    def _row(self, builtin: str) -> dict:
        row = self.app.db.query_one("SELECT id FROM monitor_activators WHERE builtin = ? AND "
                                    "COALESCE(deleted_at, '') = ''", (builtin,))
        return self.monitor.store.get(row["id"])

    def test_what_is_left_and_what_is_gone(self) -> None:
        kept = {row["builtin"] for row in self.app.db.query(
            "SELECT builtin FROM monitor_activators WHERE COALESCE(deleted_at, '') = ''")}
        for one in ("calendar_reminder", "summary", "system", "morning", "evening_closing", "spam_risk"):
            self.assertIn(one, kept)
        self.assertNotIn("evening", kept, "the evening report by the hours: gone")

    def test_a_reminder_said_through_its_activator_its_switch_and_its_silence(self) -> None:
        sent = self.monitor.relay("calendar_reminder", "Water the beds", "At 18:00")
        self.assertIsNotNone(sent)
        self.assertEqual(sent.metadata["monitor"]["group"], "calendar")
        self.assertEqual(sent.metadata["level"], "pop")
        firing = self.app.db.query_one("SELECT how, acted FROM monitor_firings WHERE id = ?",
                                       (sent.metadata["monitor"]["firing"],))
        self.assertEqual((firing["how"], firing["acted"]), ("relay", 1), "kept as a firing")
        # Under a silence: in the list, nothing popped.
        self.monitor._quieted = lambda: "In a meeting"
        held = self.monitor.relay("calendar_reminder", "Call the nursery", "At 18:30")
        self.assertEqual(held.metadata["level"], "collapse")
        self.assertEqual(held.metadata["held_by"], "In a meeting")
        self.assertFalse(held.metadata["through"])
        del self.monitor._quieted
        # Switched off by the user: nothing.
        self.monitor.store.switch(self._row("calendar_reminder")["id"], False)
        self.assertIsNone(self.monitor.relay("calendar_reminder", "Buy seeds", "At 19:00"))

    def test_the_application_itself_its_own_switch_alone(self) -> None:
        from personalized_agent.common import modules
        modules.set_enabled(self.app.config, "monitor", False) if hasattr(modules, "set_enabled") else \
            self.app.config.set("modules_disabled", ["monitor"])
        said = self.app.notifications.system("Sign in to the model again", "Its sign-in ran out.")
        self.assertIsNotNone(said, "said even with the Monitor's other work off")
        self.assertEqual(said.metadata["monitor"]["group"], "system")
        waiting = self.app.notifications.system("Updated", "To 9.9", kind="info", level="collapse")
        self.assertEqual(waiting.metadata["level"], "collapse", "rather waiting, as it asked")

    def test_a_risk_is_held_by_a_silence_too(self) -> None:
        context = {"activator": {"id": self._row("spam_risk")["id"], "name": "Spam", "group": "risk"}}
        self.monitor._quieted = lambda: "While presenting"
        said = self.monitor.say("This looks like a scam", "Be careful", context, "monfire_x")
        self.assertTrue(said["said"])
        import json
        made = self.app.notifications.get(said["notification_id"])
        metadata = made["metadata"] if isinstance(made["metadata"], dict) else json.loads(made["metadata"])
        self.assertEqual(metadata["level"], "collapse")
        self.assertFalse(metadata["through"], "no risk through a silence")


if __name__ == "__main__":
    unittest.main()

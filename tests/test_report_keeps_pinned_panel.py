"""2026-11-17: "我刚刚是点了你的report, 打开窗口之后，pin的sidebar直接collapse了" - the report's window opened, a pinned
side panel stays; one not pinned is put away, not to stand in front of it."""
import unittest
from types import SimpleNamespace

import support  # noqa: F401

from personalized_agent.ui.desktop import DesktopAgent


class TheReportKeepsAPinnedPanel(unittest.TestCase):
    def _agent(self, pinned: bool):
        hidden, opened = [], []
        agent = SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: pinned if key == "side_panel_pinned" else default),
                                open_url=lambda fragment: opened.append(fragment), hide_notes_panel=lambda: hidden.append(1))
        return agent, hidden, opened

    def test_pinned_it_stays(self) -> None:
        agent, hidden, opened = self._agent(True)
        self.assertTrue(DesktopAgent.open_report_window(agent))
        self.assertEqual(opened, ["/#report"])
        self.assertEqual(hidden, [], "pinned: not put away")

    def test_not_pinned_it_is_put_away(self) -> None:
        agent, hidden, _opened = self._agent(False)
        DesktopAgent.open_report_window(agent)
        self.assertEqual(hidden, [1])


if __name__ == "__main__":
    unittest.main()

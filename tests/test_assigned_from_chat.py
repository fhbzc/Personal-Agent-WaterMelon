"""WORK THE CHAT'S AGENT HANDED OVER, IN ONE FOLDED CATEGORY UNDER WORK ASSIGNED (the user, 2026-11-08:
"work assigned 把所有agent加的工作都collapose在一个category里，用户可以expand看"; "基于chat agent加的工作").
Everything here is made up."""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

import support  # noqa: F401

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    depth, at = 0, source.index("{", start)
    for i in range(at, len(source)):
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        if depth == 0:
            return source[start:i + 1]
    raise ValueError(name)


@unittest.skipUnless(shutil.which("node"), "node is not here")
class FromTheChatTests(unittest.TestCase):

    def _run(self, opened: bool) -> str:
        source = (WEB / "app.js").read_text("utf-8")
        script = ("const escapeHtml=(s)=>String(s);"
                  f"let ovChatOpened={'true' if opened else 'false'};"
                  + _function(source, "ovChatCategory")
                  + "const items=" + json.dumps([{"id": "d1", "text": "Tide report"},
                                                 {"id": "d2", "text": "Harbour notes"}]) + ";"
                  "process.stdout.write(ovChatCategory(items,(one)=>'<li>'+one.text+'</li>'));"
                  "process.stdout.write('|'+ovChatCategory([],(one)=>'x'));")
        return subprocess.run(["node", "-e", script], capture_output=True, text=True,
                              encoding="utf-8", timeout=60).stdout

    def test_folded_until_opened_its_count_on_its_heading(self) -> None:
        folded = self._run(False)
        self.assertIn("Handed over from the chat", folded)
        self.assertIn('<span class="ov-group-count">2</span>', folded)
        self.assertIn("ov-group-folded", folded)
        self.assertNotIn("Tide report", folded, "folded: none of it shown")
        self.assertTrue(folded.endswith("|"), "none from the chat: no heading")
        opened = self._run(True)
        self.assertIn("Tide report", opened)
        self.assertIn("Harbour notes", opened)
        self.assertNotIn("ov-group-folded", opened)

    def test_only_the_chats_go_in_it(self) -> None:
        source = (WEB / "app.js").read_text("utf-8")
        box = _function(source, "ovAssignedBox")
        self.assertIn('ovAssigned.filter((one) => one.source === "chat")', box)
        self.assertIn('ovGroupedItems("assigned", yours, drawn) + ovChatCategory(fromChat, drawn)', box)


if __name__ == "__main__":
    unittest.main()

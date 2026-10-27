"""A RUN'S RESULT, TO OPEN (the user, 2026-10-27: not the log folder alone):
what it handed over - an email, an event, a file, a folder, text to copy -
and what it made: the deliverables its deck lists, else its folder."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from test_work_runs import _TwoAgents
from test_work_versions import _RunsBase

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    return source[start:source.index("\n}\n", start) + 3]


class WhatItMadeTests(_RunsBase):

    def test_each_run_lists_what_it_made_to_open(self) -> None:
        run, folder = self._run(_TwoAgents())
        entry = next(one for one in self.app.work.runs(self.unit["id"]) if one["id"] == run["id"])
        results = entry["results"]
        self.assertTrue(results, "at least the folder it worked in")
        for one in results:
            self.assertEqual(set(one), {"path", "name", "kind", "work_folder"})
            self.assertIsNotNone(self.app.work.readable_by(run["id"], one["path"]))
        self.assertEqual(sum(one["work_folder"] for one in results), 1)
        self.assertEqual(len({one["path"] for one in results}), len(results), "each once")


class TheRowTests(unittest.TestCase):

    def _row(self, run):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is needed")
        source = (WEB / "app.js").read_text(encoding="utf-8")
        script = ("const escapeHtml=s=>String(s);const xpHandedItems={};"
                  "const XP_HANDED_KIND={email:'Email',event:'Event',text:'Text to paste',"
                  "file:'File',folder:'Folder',answer:'Answer'};"
                  + _function(source, "xpResultsRow")
                  + "process.stdout.write(xpResultsRow(" + json.dumps(run) + ").replace(/\s+/g,' '));")
        done = subprocess.run([node, "-e", script], capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_what_it_handed_over_and_what_it_made(self) -> None:
        row = self._row({"id": "run_1", "handed": [
            {"kind": "email", "title": "Reply to the club"}, {"kind": "event", "title": "Meeting"},
            {"kind": "answer", "title": "The number"}, {"kind": "folder", "title": "Site"}],
            "results": [{"path": "C:/w/report.pdf", "name": "report.pdf", "kind": "file",
                         "work_folder": False},
                        {"path": "C:/w", "name": "w", "kind": "folder", "work_folder": True}]})
        for words in ("The result:", "Open the email", "Add the event", "Copy the answer",
                      "Open the folder", "Open <code>report.pdf</code>",
                      "Open the work folder <code>w</code>"):
            self.assertIn(words, row)
        self.assertIn('data-xp-handed-open="run_1:0"', row)
        self.assertIn('data-xp-result-path="C:/w/report.pdf"', row)

    def test_nothing_to_open_no_row(self) -> None:
        self.assertEqual(self._row({"id": "run_1"}), "")


if __name__ == "__main__":
    unittest.main()

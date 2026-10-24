"""The user's past work as references for a run's deliverables: the
candidates (where the deliverables of the cases most like the job are, those
left out never among them), what the call is shown of each, and what reaches
the work agent - at most two a deliverable, each there, readable and in no
folder left out."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from personalized_agent.learning import references
from personalized_agent.learning.leave_out import LeaveOut


class _Finder:
    def __init__(self, cases, left_out=()):
        self.cases = cases
        self.leave_out = LeaveOut(list(left_out))

    def similar_cases(self, facets):
        return list(self.cases)


class ReferencesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.orchard = self.root / "orchard-site"
        (self.orchard / "src").mkdir(parents=True)
        (self.orchard / "README.md").write_text("# Orchard\n\n## Install\n\n## Run\n", encoding="utf-8")
        (self.orchard / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
        (self.root / "draft.md").write_text("an early draft", encoding="utf-8")
        self.secret = self.root / "sealed"
        self.secret.mkdir()
        (self.secret / "notes.md").write_text("closed", encoding="utf-8")
        self.letters = self.root / "letters"
        self.letters.mkdir()
        (self.letters / "report.md").write_text("left out", encoding="utf-8")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _readable(self, path: str) -> bool:
        return not Path(path).resolve().is_relative_to(self.secret.resolve())

    def test_candidates_newest_final_readable_and_none_left_out(self) -> None:
        cases = [{"id": "C-1", "title": "Orchard site", "locations": [
                     {"path": str(self.root / "draft.md"), "what": "draft", "at": "2026-02-01"},
                     {"path": str(self.orchard / "README.md"), "what": "final version", "at": "2026-01-01"},
                     {"path": str(self.orchard), "what": "code folder", "at": "2026-01-02"},
                     {"path": str(self.root / "gone.md"), "what": "final version", "at": "2026-03-01"}]},
                 {"id": "C-2", "title": "Sealed notes", "locations": [
                     {"path": str(self.secret / "notes.md"), "what": "notes", "at": "2026-01-01"}]},
                 {"id": "C-3", "title": "Letters", "locations": [
                     {"path": str(self.letters / "report.md"), "what": "report", "at": "2026-01-01"}]}]
        found = references.candidates(_Finder(cases, [self.letters]), {}, self._readable)
        paths = [one["path"] for one in found]
        self.assertNotIn(str(self.root / "gone.md"), paths, "not on disk")
        self.assertNotIn(str(self.secret / "notes.md"), paths, "not readable by the work agent")
        self.assertNotIn(str(self.letters / "report.md"), paths, "in a folder left out")
        self.assertLess(paths.index(str(self.orchard)), paths.index(str(self.root / "draft.md")),
                        "a final version before a draft")
        self.assertLessEqual(len([p for p in found if p["case"] == "C-1"]), references.PER_CASE)

    def test_shown_by_its_first_lines_a_folder_by_its_first_levels(self) -> None:
        self.assertIn("## Install", references.excerpt(str(self.orchard / "README.md")))
        tree = references.excerpt(str(self.orchard))
        self.assertIn("src/", tree)
        self.assertIn("  app.py", tree)
        text = references.content([{"id": "card_1", "title": "README", "statement": "How to run it"}],
                                  [{"path": str(self.orchard / "README.md"), "what": "final version",
                                    "at": "2026-01-01", "case": "C-1", "project": "Orchard site"}])
        self.assertIn("card_1: README", text)
        self.assertIn("## Install", text)

    def test_what_reaches_the_work_agent_is_checked(self) -> None:
        cards = [{"id": "card_1"}, {"id": "card_2"}]
        said = {"cards": {
            "card_1": [{"path": str(self.orchard / "README.md"), "take": "Its sections."},
                       {"path": str(self.root / "gone.md"), "take": "Not there."},
                       {"path": str(self.secret / "notes.md"), "take": "Not readable."},
                       {"path": str(self.letters / "report.md"), "take": "Left out."},
                       {"path": str(self.orchard), "take": ""},
                       {"path": str(self.root / "draft.md"), "take": "Its tone."},
                       {"path": str(self.orchard / "src" / "app.py"), "take": "A third one."}],
            "card_2": []}}
        kept = references.checked(said, cards, self._readable, LeaveOut([str(self.letters)]))
        self.assertEqual(kept["card_1"], [
            {"path": str(self.orchard / "README.md"), "take": "Its sections."},
            {"path": str(self.root / "draft.md"), "take": "Its tone."}])
        self.assertEqual(kept["card_2"], [], "none, when none fits")
        self.assertEqual(references.shown(kept["card_1"])[0],
                         f"    look at: {self.orchard / 'README.md'} - Its sections.")


if __name__ == "__main__":
    unittest.main()

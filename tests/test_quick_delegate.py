"""The shortcut's quick Delegate work box: the agent's own window, there at
once - a title and a description, the folders and files, Create - and the
page opened only then, on the piece of work."""

from __future__ import annotations

import unittest
from unittest import mock

from support import TemporaryAgent

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAVE_TK = True
except Exception:                                  # pragma: no cover - no display
    HAVE_TK = False


class _Drafts:
    def __init__(self):
        self.made, self.composed = [], []

    def create(self, **kw):
        self.made.append(kw)
        return {"id": "draft_quick1"}

    def compose(self, draft_id, instruction="", title=""):
        self.composed.append((draft_id, instruction, title))
        return {"draft": {"id": draft_id}}


class _App:
    def __init__(self):
        self.drafts = _Drafts()


@unittest.skipUnless(HAVE_TK, "no display for Tk here")
class QuickBoxTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.ui.quick_delegate import QuickDelegate
        self.root = tk.Tk()
        self.root.withdraw()
        self.opened, self.posted = [], []
        self.app = _App()
        self.box = QuickDelegate(self.root, self.app, self.opened.append, lambda text: text,
                                 self.posted.append)

    def tearDown(self) -> None:
        self.box.close()
        self.root.destroy()

    def _run_posted(self) -> None:
        import time
        for _ in range(100):
            if self.posted:
                break
            time.sleep(0.02)
        while self.posted:
            self.posted.pop(0)()

    def test_title_and_description_handed_over_then_the_page(self) -> None:
        self.box.show()
        self.root.update()
        self.box.title.insert(0, "Tidy the seed catalogue")
        self.box.text.insert("1.0", "Sort the seeds by sowing month.")
        self.box.folders.append("C:/garden/seeds")
        self.box.submit()
        self._run_posted()
        self.assertEqual(self.app.drafts.made[0]["folders"], ["C:/garden/seeds"])
        self.assertEqual(self.app.drafts.made[0]["source"], "shortcut")
        self.assertEqual(self.app.drafts.composed,
                         [("draft_quick1", "Sort the seeds by sowing month.",
                           "Tidy the seed catalogue")])
        self.assertEqual(self.opened, ["/?solo=delegate#delegate=draft_quick1"])
        self.assertIsNone(self.box.window, "the quick box closes once the page is opened")

    def test_nothing_written_is_not_handed_over(self) -> None:
        self.box.show()
        self.root.update()
        self.box.submit()
        self.assertEqual(self.app.drafts.made, [])
        self.assertTrue(self.box.note.cget("text"))

    def test_pressed_again_one_box(self) -> None:
        self.box.show()
        first = self.box.window
        self.box.show()
        self.assertIs(self.box.window, first)


class TheTitleIsTheWorksTests(unittest.TestCase):
    """A title the user gave is said to the planner and is the plan's own."""

    def test_given_title_kept_over_the_planners(self) -> None:
        from personalized_agent.agent.work import delegate
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        drafts = agent.app.drafts
        draft = drafts.create(source="shortcut", write_first=True)
        with mock.patch.object(drafts, "_collect_apart"):
            said = drafts.compose(draft["id"], "Sort the seeds by sowing month.",
                                  title="Tidy the seed catalogue")
        options = said["draft"]["options"]
        self.assertEqual(options["title"], "Tidy the seed catalogue")
        self.assertTrue(options["instruction"].startswith("Tidy the seed catalogue\n\n"))
        planned = delegate.plan_of({"title": "Seed catalogue cleanup", "goal": "g"})
        with mock.patch.object(drafts, "collect", return_value={}), \
                mock.patch.object(drafts, "plan", return_value=planned):
            drafts.collect_and_plan(draft["id"])
        self.assertEqual(drafts.get(draft["id"])["plan"]["title"], "Tidy the seed catalogue")


if __name__ == "__main__":
    unittest.main()

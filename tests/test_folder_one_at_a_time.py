"""ONE PIECE OF WORK AT A TIME IN A FOLDER (the user, 2026-10-26).

A to-do may be handed over again and again, each version a project of its
own - and "one run at a time" was by project: two versions given the same
folder each changed it at once, the later over the earlier. Now, while work
is going in a folder - paused too, it will go on - no other work starts in
it, nor in a folder inside it or around it: a start the user asks for is
refused saying which work is in it; one that starts by itself - waiting for
room, waiting for its plan - waits for the folder, and goes once it is
free."""

from __future__ import annotations

import time
import unittest
from pathlib import Path

from support import TemporaryAgent
from test_delegate import PLAN, _Models


class _Going:
    """A run of other work, going - or not."""

    def __init__(self, unit, run_id="run_going", alive=True):
        self.unit, self.id, self._alive, self.debug = unit, run_id, alive, False

    def alive(self):
        return self._alive


class OneAtATimeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.work = self.app.work
        self.shared = Path(self.agent.dir) / "club notes"
        (self.shared / "minutes").mkdir(parents=True)
        self.other = Path(self.agent.dir) / "elsewhere"
        self.other.mkdir()
        self.v1 = {"id": "work_v1", "name": "Reply to the club", "kind": "new project"}
        self.v2 = {"id": "work_v2", "name": "Reply to the club (V2)", "kind": "new project"}
        for unit in (self.v1, self.v2):
            self.work.grant(unit["id"], str(self.shared))

    def tearDown(self) -> None:
        self.work._running.clear()
        self.work.waiting.clear()
        self.agent.cleanup()

    def test_while_one_works_in_a_folder_no_other_starts_in_it(self) -> None:
        self.assertIsNone(self.work.folder_in_use([str(self.shared)]))
        self.work._running["run_going"] = _Going(self.v1)
        held = self.work.folder_in_use([str(self.shared)], self.v2["id"])
        self.assertEqual((held["by"], held["run"]), ("Reply to the club", "run_going"))
        # Inside it, or around it, the same.
        self.assertIsNotNone(self.work.folder_in_use([str(self.shared / "minutes")]))
        self.assertIsNotNone(self.work.folder_in_use([str(self.agent.dir)]))
        self.assertIsNone(self.work.folder_in_use([str(self.other)]))
        # Its own run is the one-run-a-work rule's, not this one's.
        self.assertIsNone(self.work.folder_in_use([str(self.shared)], self.v1["id"]))
        self.work.in_subprocess = True
        said = self.work._busy(self.v2)
        self.assertIn("is being worked on by \"Reply to the club\"", said)
        # Ended: free again.
        self.work._running["run_going"]._alive = False
        self.assertEqual(self.work._busy(self.v2), "")

    def test_a_start_waiting_for_room_waits_for_its_folder_the_others_go_on(self) -> None:
        self.work._running["run_going"] = _Going(self.v1)
        third = {"id": "work_v3", "name": "Elsewhere", "kind": "new project"}
        self.work.grant(third["id"], str(self.other))
        self.work.waiting[:] = [{"kind": "start", "unit": self.v2, "args": {}},
                                {"kind": "start", "unit": third, "args": {}}]
        launched = []
        self.work._room_for = lambda head=False: ""
        self.work._launch = lambda kind, unit, args: launched.append(unit["id"]) or {"run": "r"}
        self.work.start_waiting()
        self.assertEqual(launched, ["work_v3"], "the one whose folder is free went")
        self.assertEqual([one["unit"]["id"] for one in self.work.waiting], ["work_v2"])
        self.assertIn("waiting for the folder", self.work.waiting[0]["why"])
        # The folder free: it goes.
        self.work._running["run_going"]._alive = False
        self.work.start_waiting()
        self.assertEqual(launched, ["work_v3", "work_v2"])

    def test_handed_over_the_user_is_told_a_start_by_itself_waits(self) -> None:
        self.app.models = _Models(PLAN)
        self.work.start = lambda unit, instruction, **kw: {"run": "run_new"}
        self.work._running["run_going"] = _Going(self.v1)
        drafts = self.app.drafts
        # Asked for: refused at once, saying which work is in it - no project made.
        draft = drafts.create(text="Reply again", collect=False)
        drafts.collect_and_plan(draft["id"])
        said = drafts.start(draft["id"], folders=[str(self.shared)])
        self.assertIn("is being worked on by", said["error"])
        self.assertEqual(drafts.get(draft["id"])["unit_id"], "")
        # Started before its plan: waits for the folder, and says so.
        drafts._when_ready.__func__.__defaults__ = (0.05, 30 * 60.0)
        self.addCleanup(setattr, drafts._when_ready.__func__, "__defaults__", (1.0, 30 * 60.0))
        draft = drafts.create(text="Reply again", source="todo",
                              options={"todo": "t1"}, collect=False)
        drafts.start_apart(draft["id"], plan={"way": "auto"}, folders=[str(self.shared)])
        drafts.collect_and_plan(draft["id"])
        time.sleep(0.5)
        kept = drafts.get(draft["id"])
        self.assertEqual(kept["status"], "ready")
        self.assertIn("waiting for the folder", kept["options"]["start_when_ready"]["held"])
        self.assertIn("waiting for the folder", drafts.of_todos()["t1"]["versions"][0]["held"])
        self.work._running["run_going"]._alive = False
        for _ in range(300):
            if drafts.get(draft["id"])["status"] == "started":
                break
            time.sleep(0.05)
        self.assertEqual(drafts.get(draft["id"])["status"], "started")

    def test_the_box_asks_whether_its_folders_are_free(self) -> None:
        from personalized_agent.api.routes import build_routes

        class _Ask(dict):
            def get(self, key, default=None):
                return super().get(key, default)

            def require(self, key):
                return self[key]

        route = build_routes(self.app)[("POST", "/api/work/folders/held")]
        self.assertIsNone(route(_Ask(paths=[str(self.shared)]))["held"])
        self.work._running["run_going"] = _Going(self.v1)
        said = route(_Ask(paths=[str(self.shared / "minutes")]))
        self.assertEqual(said["held"]["by"], "Reply to the club")
        self.assertIn("one piece of work at a time in a folder", said["said"])
        # A project's own folders count, as Add work starts in them.
        self.assertIsNotNone(route(_Ask(paths=[], unit=self.v2["id"]))["held"])


if __name__ == "__main__":
    unittest.main()

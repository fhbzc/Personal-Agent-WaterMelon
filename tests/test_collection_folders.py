"""THE NEWSPAPER'S COLLECTION IN FOLDERS (the user, 2026-11-03, from PR #8):
folders in folders; a kept story in one or none; a folder deleted only when
empty, and never put inside itself. Every story here is made up."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from personalized_agent.data_manage.storage.database import Database
from personalized_agent.heartbeat.connections import recommend as rec


class FoldersTests(unittest.TestCase):

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._dir.name) / "agent.db")
        for n in (1, 2):
            self.db.execute("INSERT INTO rec_collection (id, collected_at, issue_id, story_id, "
                            "story) VALUES (?, ?, 'i1', ?, '{}')",
                            (f"c{n}", f"2026-11-0{n}T08:00:00", f"s{n}"))

    def tearDown(self) -> None:
        self.db.close()
        self._dir.cleanup()

    def test_folders_in_folders_and_stories_moved_in_and_out(self) -> None:
        garden = rec.edit_folder(self.db, "create", name="Garden")["id"]
        tomatoes = rec.edit_folder(self.db, "create", name="Tomatoes", parent_id=garden)["id"]
        rec.move_kept(self.db, "s1", tomatoes)
        kept = {one["story_id"]: one["folder_id"] for one in rec.collection(self.db)["items"]}
        self.assertEqual(kept, {"s1": tomatoes, "s2": ""})
        self.assertEqual([(one["name"], one["parent_id"]) for one in rec.collection(self.db)["folders"]],
                         [("Garden", ""), ("Tomatoes", garden)])
        rec.move_kept(self.db, "s1", "")
        self.assertEqual(rec.collection(self.db)["items"][1]["folder_id"], "")

    def test_renamed_and_moved_never_into_itself(self) -> None:
        garden = rec.edit_folder(self.db, "create", name="Garden")["id"]
        beds = rec.edit_folder(self.db, "create", name="Beds", parent_id=garden)["id"]
        with self.assertRaises(ValueError):
            rec.edit_folder(self.db, "edit", garden, name="Garden", parent_id=beds)
        rec.edit_folder(self.db, "edit", beds, name="Raised beds", parent_id="")
        self.assertEqual({one["name"]: one["parent_id"] for one in rec.collection_folders(self.db)},
                         {"Garden": "", "Raised beds": ""})
        with self.assertRaises(ValueError):
            rec.edit_folder(self.db, "create", name="garden")     # one of that name here

    def test_deleted_only_when_empty(self) -> None:
        garden = rec.edit_folder(self.db, "create", name="Garden")["id"]
        rec.move_kept(self.db, "s2", garden)
        with self.assertRaises(ValueError):
            rec.edit_folder(self.db, "delete", garden)
        rec.move_kept(self.db, "s2", "")
        rec.edit_folder(self.db, "delete", garden)
        self.assertEqual(rec.collection_folders(self.db), [])

    def test_a_story_taken_out_of_the_collection_does_not_keep_a_folder_full(self) -> None:
        garden = rec.edit_folder(self.db, "create", name="Garden")["id"]
        rec.move_kept(self.db, "s1", garden)
        rec.uncollect(self.db, "s1")
        rec.edit_folder(self.db, "delete", garden)
        with self.assertRaises(KeyError):
            rec.move_kept(self.db, "s1", "")


if __name__ == "__main__":
    unittest.main()

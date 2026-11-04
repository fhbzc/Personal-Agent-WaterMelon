"""THE USER'S WECHAT REMARK IS THEIR NAME FOR SOMEONE (the user, 2026-11-03: a
contact showed the person's WeChat nickname, not the user's remark). The
model is told the remark gives the main name, and may rename a contact
listed under another (`rename_to`) - the old name kept among the others; a
name the user set themselves is never changed. No model is called; the
people are made up."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from personalized_agent.data_manage.storage.database import Database
from personalized_agent.heartbeat.connections import people
from personalized_agent.heartbeat.connections.store import ConnectionsStore


class RemarkNameTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="remark-"))
        self.db = Database(self.dir / "agent.db")
        self.store = ConnectionsStore(self.db, self.dir / "connections")

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_told_that_the_remark_gives_the_main_name(self) -> None:
        self.assertIn("THE USER'S OWN REMARK", people.INSTRUCTION)
        self.assertIn("THEIR MAIN NAME COMES FROM IT", people.INSTRUCTION)
        self.assertIn('"rename_to"', people.INSTRUCTION)
        self.assertIn("rename_to", people.RETURNS)

    def test_renamed_by_the_models_decision_the_old_name_kept(self) -> None:
        person = self.store.add("Sunny Bun", by="agent")      # first seen in a group
        said = people.apply(self.store, [{
            "action": "update", "person_id": person["id"], "name": "Ada Quist",
            "rename_to": "Ada Quist", "why": "the user's remark for her is Ada Quist"}])
        self.assertEqual(said[0]["renamed"], {"from": "Sunny Bun", "to": "Ada Quist"})
        kept = self.store.get(person["id"])
        self.assertEqual(kept["name"], "Ada Quist")
        self.assertIn("Sunny Bun", kept["aliases"])

    def test_without_rename_to_a_new_name_is_only_another_name(self) -> None:
        person = self.store.add("Sunny Bun", by="agent")
        people.apply(self.store, [{"action": "update", "person_id": person["id"],
                                   "name": "Ada Quist"}])
        kept = self.store.get(person["id"])
        self.assertEqual(kept["name"], "Sunny Bun")
        self.assertIn("Ada Quist", kept["aliases"])

    def test_a_name_the_user_set_is_never_changed(self) -> None:
        person = self.store.add("Sunny Bun", by="agent")
        self.store.edit(person["id"], by="user", name="Ada Q.")
        said = people.apply(self.store, [{
            "action": "update", "person_id": person["id"], "name": "Ada Quist",
            "rename_to": "Ada Quist", "why": "the remark"}])
        self.assertEqual(said[0]["rename_refused"], "the user set this name themselves")
        self.assertEqual(self.store.get(person["id"])["name"], "Ada Q.")


if __name__ == "__main__":
    unittest.main()

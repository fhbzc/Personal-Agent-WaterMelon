"""ONE ENTRY THAT IS MORE THAN ONE PERSON, AND WHAT CHANGED THE CONTACTS: the
people step is told that names which cannot be one person's are someone
else, and may split an entry; every change made from a model's answer is
kept with what the model was asked and answered (the Connection debugger).
No model is called; the people are made up."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from personalized_agent.data_manage.storage.database import Database
from personalized_agent.heartbeat.connections import people
from personalized_agent.heartbeat.connections.store import ConnectionsStore

ROOT = Path(__file__).resolve().parents[1]


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="split-"))
        self.db = Database(self.dir / "agent.db")
        self.store = ConnectionsStore(self.db, self.dir / "connections")

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def mixed(self):
        """An entry that took two people's names: a co-author, and a
        professor met by email."""
        person = self.store.add("Mira Halden", by="agent", aliases=["M Halden"])
        self.store.edit(person["id"], by="agent", aliases=["Tomas Brekke", "Prof. Brekke"])
        self.store.add_account(person["id"], "email", "tbrekke@example.edu")
        self.store.add_account(person["id"], "scholar", "Mira Halden")
        self.store.add_activity(person["id"], "Replied to the user's talk request",
                                "2026-10-21T19:14:00+00:00", "the user's activity")
        self.store.add_activity(person["id"], "Co-authored the diversity paper",
                                "2026-10-12T10:00:00+00:00", "the user's activity")
        return self.store.get(person["id"])


class ThePromptTests(_Base):

    def test_told_that_different_names_are_someone_else_and_that_it_may_split(self) -> None:
        self.assertIn("A DIFFERENT PERSON IS NOT AN UPDATE", people.INSTRUCTION)
        self.assertIn('"split"', people.INSTRUCTION)
        self.assertIn("never give one person's id to another", people.INSTRUCTION)
        self.assertIn("split", people.RETURNS)

    def test_each_piece_of_activity_is_listed_with_its_id(self) -> None:
        person = self.mixed()
        listed = people.context(self.store, "Mira Halden")
        for one in self.store.activity(person["id"]):
            self.assertIn(f"[{one['id']}]", listed)
        self.assertIn("Tomas Brekke", listed)


class SplitTests(_Base):

    def test_the_other_person_takes_their_names_accounts_and_activity(self) -> None:
        person = self.mixed()
        reply = [one for one in self.store.activity(person["id"])
                 if one["what"].startswith("Replied")][0]
        said = people.apply(self.store, [{
            "action": "split", "person_id": person["id"],
            "why": "Brekke is a professor met by email, not the co-author",
            "parts": [{"name": "Tomas Brekke", "aliases": ["Prof. Brekke"],
                       "accounts": ["tbrekke@example.edu"], "activity": [reply["id"]]}]}])
        self.assertTrue(said[0]["ok"], said)
        kept = self.store.get(person["id"])
        self.assertEqual(kept["aliases"], ["M Halden"])
        other = self.store.find("Tomas Brekke")
        self.assertIsNotNone(other)
        self.assertNotEqual(other["id"], person["id"])
        self.assertEqual([one["handle"] for one in self.store.accounts(other["id"])],
                         ["tbrekke@example.edu"])
        self.assertEqual([one["id"] for one in self.store.activity(other["id"])], [reply["id"]])
        self.assertEqual(len(self.store.activity(person["id"])), 1)
        self.assertEqual(self.store.find("Prof. Brekke")["id"], other["id"])
        actions = [one["action"] for one in self.store.changes()]
        self.assertIn("split", actions)

    def test_never_an_entry_the_user_made_one(self) -> None:
        one = self.store.add("Mira Halden", by="user")
        two = self.store.add("M. Halden", by="user")
        self.store.merge(one["id"], two["id"], by="user", reason="the same person")
        said = people.apply(self.store, [{
            "action": "split", "person_id": one["id"], "why": "looks like two",
            "parts": [{"name": "M. Halden"}]}])
        self.assertFalse(said[0].get("ok"))
        self.assertIn("the user made these one person", said[0].get("error", ""))

    def test_a_split_needs_its_why_and_the_people_in_it(self) -> None:
        person = self.mixed()
        self.assertIn("why", people.apply(self.store, [{
            "action": "split", "person_id": person["id"],
            "parts": [{"name": "Tomas Brekke"}]}])[0]["error"])
        self.assertIn("other people", people.apply(self.store, [{
            "action": "split", "person_id": person["id"], "why": "two people"}])[0]["error"])


class WhatChangedTheContactsTests(_Base):

    def test_a_change_is_kept_with_the_call_it_came_from_whole(self) -> None:
        prompt = "FIND THE PEOPLE IN THIS WINDOW\n" + "x" * (ConnectionsStore.HEAD_CHARS + 500)
        call = self.store.begin_call("agent heartbeat: the people step", prompt,
                                     '{"people": []}', window="the activity up to noon")
        self.store.add("Ilse Varga", by="agent", reason="seen in the user's activity")
        self.store.end_call(call, [{"name": "Ilse Varga", "ok": True}])
        self.store.add("Noor Aziz", by="user")
        changes = self.store.changes()
        self.assertEqual([one["person_name"] for one in changes], ["Noor Aziz", "Ilse Varga"])
        self.assertIsNone(changes[0]["call"], "the user's own change came from no call")
        kept = changes[1]["call"]
        self.assertEqual(kept["source"], "agent heartbeat: the people step")
        self.assertEqual(kept["prompt_chars"], len(prompt))
        whole = self.store.call_whole(call)
        self.assertEqual(whole["prompt"], prompt, "the whole prompt, from its file")
        self.assertEqual(whole["result"][0]["name"], "Ilse Varga")
        self.assertTrue(Path(whole["archive"]).is_file())

    def test_the_debugger_tab_is_there_and_goes_from_an_installed_build(self) -> None:
        page = (ROOT / "personalized_agent" / "api" / "web" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('data-tab="connectiondebug"', page)
        self.assertIn('id="connectiondebug"', page)
        self.assertIn("/api/debugger/connections", script)
        self.assertIn('"connectiondebug"', script.split("EVERY DEBUGGER")[1][:400])


if __name__ == "__main__":
    unittest.main()

"""WHO IT IS, NOT WHAT THEY ARE CALLED (the user, 2026-10-31: a notification
said 学姐 where the WeChat chat's own name said who; a task said "the junior
students"). A WeChat chat's name is kept with its events - each chat a window
of its own; each one-to-one chat is linked to the one contact it is with,
and the messages WeChat marks as the user's own are kept with that contact;
the steps that write states and notifications are told who is in the window,
to name people as the user's contacts name them; and the work that writes to
someone is given how the user writes to them. Everyone here is made up."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from support import TemporaryAgent

from personalized_agent.heartbeat.connections import people as P
from personalized_agent.heartbeat.connections.store import ConnectionsStore
from personalized_agent.heartbeat.core import heartbeat as hb


def _message(text, role):
    return {"text": text, "sender_role": role, "content_status": "visible_chat_bubble",
            "sender_basis": "right_bubble" if role == "self" else "left_bubble",
            "attribution_status": "inferred_from_ui", "message_time": None,
            "bbox": [.6, .2, .2, .05]}


class TheChatsNameTests(unittest.TestCase):

    def test_kept_with_its_event(self) -> None:
        from personalized_agent.data_manage.storage.database import Database
        from personalized_agent.data_manage.storage.raw_store import RawStore
        from personalized_agent.common.events.models import ListenerEvent
        with tempfile.TemporaryDirectory() as work:
            db = Database(Path(work) / "db.sqlite")
            try:
                raw = RawStore(db)
                capture = {"peer_name": "Mei Lantern", "peer_avatar": "0123456789abcdef:4,6,9",
                           "scope_id": "visit1", "conversation_type": "direct",
                           "layout_status": "recognized", "layout_reason": "",
                           "frame_hash": "pixels", "captured_at": datetime.now(timezone.utc).isoformat(),
                           "messages": [_message("Are you free on Friday?", "other"),
                                        _message("Yes, thank you!", "self")],
                           "unresolved_text": ""}
                raw.add_event(ListenerEvent(event_type="screen.text", source="screen",
                                            application="WeChat",
                                            data={"text": "raw", "wechat_capture": capture}))
                data = raw.recent_events()[0].data
                self.assertEqual(data["wechat_chat"], "Mei Lantern")
                self.assertEqual(data["wechat_chat_kind"], "direct")
            finally:
                db.close()

    def test_each_chat_is_a_window_of_its_own_said_by_its_name(self) -> None:
        one = {"event_type": "screen.text", "application": "WeChat",
               "data": {"window_title": "微信", "wechat_chat": "Mei Lantern"}}
        two = {"event_type": "screen.text", "application": "WeChat",
               "data": {"window_title": "微信", "wechat_chat": "Rowan Quill"}}
        self.assertNotEqual(hb._signature(one), hb._signature(two))
        self.assertEqual(hb._window_of(one)[1], "WeChat chat Mei Lantern")
        rules = " ".join(hb.__dict__.get("STATE_PROMPT", "").split()) or " ".join(
            open(hb.__file__, encoding="utf-8").read().split())
        self.assertIn("NAME PEOPLE AS THE USER'S CONTACTS NAME THEM", rules)
        self.assertIn("A WeChat chat's name (\"wechat chat\") is not such a title", rules)


class WhoIsInTheWindowTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.store: ConnectionsStore = self.app.connections
        self.mei = self.store.add("Wang Mei", relationship="classmate", aliases=["Mei"],
                                  org="Northmoor Institute")
        self.store.add_account(self.mei["id"], platform="WeChat", handle="Mei Lantern")
        self.rowan = self.store.add("Rowan Quill", relationship="student")
        self.store.add("Rowan Quill", relationship="neighbour")     # the same name twice

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_chat_is_its_one_contacts(self) -> None:
        self.assertEqual(P.contact_of_chat(self.store, "Mei Lantern")["id"], self.mei["id"],
                         "by its WeChat account")
        self.assertEqual(P.contact_of_chat(self.store, "Mei")["id"], self.mei["id"],
                         "by another name")
        self.assertIsNone(P.contact_of_chat(self.store, "Rowan Quill"), "two of that name: none")
        self.assertIsNone(P.contact_of_chat(self.store, "Nobody Known"))

    def test_the_steps_are_told_who_it_is(self) -> None:
        events = [{"data": {"wechat_chat": "Mei Lantern", "content": "学姐 asked about the poster"}}]
        said = P.named(self.store, events, "学姐 asked about the poster - Mei Lantern")
        self.assertIn('the WeChat chat "Mei Lantern" is with: [', said)
        self.assertIn("Wang Mei", said)
        self.assertIn("to the user: classmate", said)

    def test_each_chat_linked_and_the_users_own_words_kept(self) -> None:
        db = self.app.db
        now = datetime.now(timezone.utc).isoformat()
        db.execute("INSERT INTO listener_wechat_conversations (id, account_id, person_id, peer_name, "
                   "header_key, peer_avatar, kind, identity_status, identity_basis, created_at, "
                   "last_seen_at) VALUES ('conv_1', 'acct', NULL, 'Mei Lantern', 'mei lantern', NULL, "
                   "'direct', 'x', '[]', ?, ?)", (now, now))
        for n, (text, role) in enumerate([("Could you look at my poster?", "other"),
                                          ("Of course! Send it over, 学姐.", "self"),
                                          ("Thank you so much!", "self")]):
            db.execute("INSERT INTO listener_wechat_messages (id, conversation_id, sender_role, text, "
                       "content_status, message_time, first_seen_at, last_seen_at, payload) VALUES "
                       "(?, 'conv_1', ?, ?, 'visible_chat_bubble', NULL, ?, ?, '{}')",
                       (f"msg_{n}", role, text, f"2026-10-31T10:0{n}:00+00:00", now))
        said = P.link_wechat(self.store)
        self.assertEqual(said, {"linked": 1, "kept": 2})
        self.assertEqual(db.query_one("SELECT person_id FROM listener_wechat_conversations "
                                      "WHERE id = 'conv_1'")["person_id"], self.mei["id"])
        kept = [one["text"] for one in self.store.writing(self.mei["id"])]
        self.assertEqual(kept, ["Of course! Send it over, 学姐.", "Thank you so much!"],
                         "the user's own words alone, oldest first")
        self.assertEqual(P.link_wechat(self.store), {"linked": 0, "kept": 0}, "once each")
        self.assertIn("Thank you so much!",
                      [one["text"] for one in self.store.get(self.mei["id"], with_details=True)
                       ["writing"]])

    def test_the_users_words_seen_on_the_screen_are_kept_and_can_be_let_go(self) -> None:
        P.apply(self.store, [{"action": "update", "person_id": self.rowan["id"],
                              "name": "Rowan Quill", "user_wrote": ["Dear Rowan, thanks for the draft."]}])
        [kept] = self.store.writing(self.rowan["id"])
        self.assertEqual(kept["text"], "Dear Rowan, thanks for the draft.")
        self.assertTrue(self.store.forget_writing(kept["id"]))
        self.assertEqual(self.store.writing(self.rowan["id"]), [])

    def test_a_word_for_how_they_stand_is_not_a_name(self) -> None:
        # The user has two 学姐: the word is how they are called, never a name.
        P.apply(self.store, [{"action": "update", "person_id": self.mei["id"], "name": "Wang Mei",
                              "aliases": ["学姐", "Meimei"], "called": ["my senior"]}])
        mei = self.store.get(self.mei["id"])
        self.assertIn("Meimei", mei["aliases"])
        self.assertNotIn("学姐", mei["aliases"])
        self.assertEqual(mei["called"], ["学姐", "my senior"])
        other = self.store.add("Li Hua", relationship="classmate", aliases=["学姐"])  # from before
        hay = "学姐 sent the slides"
        self.assertFalse(P._named_in(self.store.get(self.mei["id"]), hay))
        self.assertFalse(P._named_in(other, hay), "an old alias of the kind no longer matches")
        self.assertIsNone(P.contact_of_chat(self.store, "学姐"), "nor names a chat")
        said = P.named(self.store, [{"data": {"wechat_chat": "Mei Lantern"}}], hay)
        self.assertIn("the user calls them 学姐, my senior", said)
        self.assertNotIn("Li Hua", said)

    def test_only_the_latest_few_kept(self) -> None:
        for n in range(ConnectionsStore.MOST_WRITING + 3):
            self.store.add_writing(self.mei["id"], f"message {n}", at=f"2026-10-{10 + n:02d}T09:00:00")
        kept = [one["text"] for one in self.store.writing(self.mei["id"])]
        self.assertEqual(len(kept), ConnectionsStore.MOST_WRITING)
        self.assertEqual(kept[-1], f"message {ConnectionsStore.MOST_WRITING + 2}")

    def test_the_people_step_is_told_to_keep_them(self) -> None:
        self.assertIn("user_wrote", P.RETURNS)
        self.assertIn("A WeChat chat's name", P.INSTRUCTION)

    def test_the_work_looks_people_up_in_the_contacts(self) -> None:
        from types import SimpleNamespace
        from personalized_agent.agent.work.tools import WorkTools
        self.store.add_writing(self.mei["id"], "Thank you, 学姐!", channel="WeChat")
        tools = SimpleNamespace(people=self.store)
        found = WorkTools.search_people(tools, {"query": "Northmoor"})["people"]
        self.assertEqual([one["name"] for one in found], ["Wang Mei"])
        self.assertIn("classmate", found[0]["to_the_user"])
        whole = WorkTools.open_person(tools, {"id": self.mei["id"]})
        self.assertEqual([one["text"] for one in whole["writing"]], ["Thank you, 学姐!"])
        self.assertNotIn("actions", whole)
        me = self.store.me()
        self.assertIn("error", WorkTools.open_person(tools, {"id": me["id"]}), "never the user")
        self.assertIn("error", WorkTools.search_people(SimpleNamespace(people=None), {}))

    def test_a_contact_the_work_names_is_who_it_is_for_by_default(self) -> None:
        from test_delegate import PLAN, _Models
        self.app.models = _Models(dict(PLAN, people=[]))       # the plan named no one
        draft = self.app.drafts.create(text="Ask Wang Mei whether the poster is ready",
                                       collect=False)
        self.app.drafts.collect_and_plan(draft["id"])
        people = self.app.drafts.get(draft["id"])["context"]["people"]
        self.assertEqual([one["name"] for one in people], ["Wang Mei"])
        self.assertIn("Northmoor Institute", people[0]["contact"]["who"])

    def test_work_written_to_them_is_given_how_the_user_writes(self) -> None:
        from test_delegate import PLAN, _Models
        from personalized_agent.agent.work.delegate import instruction_of
        self.store.add_writing(self.mei["id"], "Of course! Send it over, 学姐.", channel="WeChat",
                               at="2026-10-31T10:01:00+00:00")
        self.app.models = _Models(dict(PLAN, people=["Wang Mei"]))
        draft = self.app.drafts.create(text="Reply to Mei about the poster", collect=False)
        self.app.drafts.collect_and_plan(draft["id"])
        draft = self.app.drafts.get(draft["id"])
        said = instruction_of(draft, draft["plan"], [])
        self.assertIn("HOW THE USER WRITES TO THEM", said)
        self.assertIn("Of course! Send it over, 学姐.", said)


if __name__ == "__main__":
    unittest.main()

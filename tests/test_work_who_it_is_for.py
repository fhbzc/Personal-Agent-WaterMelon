"""WHO IT IS FOR (the user, 2026-10-31: "我怎么给这个人写邮件，他是什么人，
我是什么人，我之前是怎么写的，这些不都是related info吗"). The plan's one
call names the people a piece of work is for; each is looked up - who they
are, as the contacts have them, and what passed between them and the user,
the latest last - and the work is told it, with who the user is, and to
write as the user writes to that person. Everyone here is made up."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from support import TemporaryAgent
from test_delegate import PLAN, _Models

from personalized_agent.agent.work import runner as rn
from personalized_agent.agent.work.delegate import instruction_of


def _state(app, sid, title, detail, at, source="agent", about="user"):
    app.db.execute(
        "INSERT INTO states (id, title, detail, source, about, starts_at, ends_at, status, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
        (sid, title, detail, source, about, at, at, at, at))


class WhoItIsForTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.models = _Models(dict(PLAN, people=["Ada Quillfeather"]))
        ada = self.app.connections.add(
            "Ada Quillfeather", relationship="mentor", aliases=["Prof. Quillfeather"],
            affiliations=[{"org": "Northmoor Institute", "role": "professor"}],
            summary="Runs the Lantern Lab on star maps.")
        self.app.connections.add_account(ada["id"], platform="email",
                                         handle="ada@northmoor.example")
        me = self.app.connections.me()
        self.app.connections.edit(me["id"], summary="A doctoral student who studies star maps.")
        _state(self.app, "st_1", "Ada asked about a talk",
               "Prof. Quillfeather wrote that the lab is busy in November and asked about "
               "December.", "2026-10-30T09:00:00+00:00")
        _state(self.app, "st_2", "The user replied to Quillfeather",
               "The user wrote: \"Yes, I should be free in December. Thank you so much for "
               "checking!\"", "2026-10-31T18:00:00+00:00")
        _state(self.app, "st_3", "Lunch", "Nothing about anyone.", "2026-10-31T12:00:00+00:00")
        # A first name alone is too many people's; the agent's own doing is not
        # what passed between them.
        _state(self.app, "st_4", "Chatting with Ada from the choir", "About the concert.",
               "2026-10-31T13:00:00+00:00")
        _state(self.app, "st_5", "The agent drafted the note to Ada Quillfeather", "A draft.",
               "2026-10-31T14:00:00+00:00", about="agent")

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _drafted(self):
        draft = self.app.drafts.create(text="Write to Ada about the talk", collect=False)
        self.app.drafts.collect_and_plan(draft["id"])
        return self.app.drafts.get(draft["id"])

    def test_the_plan_names_them_and_they_are_looked_up(self) -> None:
        context = self._drafted()["context"]
        [person] = context["people"]
        self.assertEqual(person["name"], "Ada Quillfeather")
        self.assertIn("Northmoor Institute", person["contact"]["who"])
        self.assertIn("mentor", person["contact"]["relation"])
        # What passed between them, the latest last; nothing that names no one.
        self.assertEqual([one["title"] for one in person["exchanges"]],
                         ["Ada asked about a talk", "The user replied to Quillfeather"])
        self.assertIn("A doctoral student", context["me"]["who"])

    def test_the_work_is_told_it_and_to_write_as_the_user_does(self) -> None:
        draft = self._drafted()
        said = instruction_of(draft, draft["plan"], [])
        self.assertIn("WHO IT IS FOR: Ada Quillfeather", said)
        self.assertIn("Northmoor Institute", said)
        self.assertIn("Thank you so much for checking!", said, "the user's own words, whole")
        self.assertLess(said.index("Ada asked about a talk"),
                        said.index("The user replied to Quillfeather"))
        self.assertIn("WHO THE USER IS: ", said)
        rules = " ".join(rn.WRITING_RULES.split())
        self.assertIn("FOLLOW THE USER'S TONE: write as the user writes to that person", rules)
        self.assertIn("not what the task said when it was drafted", rules)

    def test_written_as_the_next_message_of_the_exchange_in_the_users_tone(self) -> None:
        """The user, 2026-11-02, of a reply drafted in a thread: "your kind offer
        不是我会说的话", "我之前已经提过是什么工作了，后面略写就行了" - "要模型在写的
        时候，理解整个context, 并且知道context里的话之前已经说过了 ... follow the
        tone". Every work agent is told, the quick way and the thorough way."""
        rules = " ".join(rn.WRITING_RULES.split())
        for words in ("UNDERSTAND THE WHOLE EXCHANGE FIRST",
                      "and do not say it again: a reply is read under what came before",
                      "No phrase the user does not use with them",
                      "shows how they write, never what to say here",
                      "cut each sentence that tells them what they already know"):
            self.assertIn(words, rules)
        source = (Path(rn.__file__)).read_text(encoding="utf-8")
        self.assertIn('+ "\\n\\n" + WRITING_RULES', source, "in every work agent's system")
        monitor = " ".join(rn.MONITOR_SYSTEM.split())
        self.assertIn("tells the person again what was already said between them", monitor)
        draft = self._drafted()
        if draft["context"].get("found"):
            self.assertIn("What the user wrote to OTHER people among them shows how they write",
                          instruction_of(draft, draft["plan"], []))

    def test_a_contact_called_by_a_first_name_is_not_one_inside_another_name(self) -> None:
        """A reply to Prof. Ben Thistlewood was told about a WeChat contact "Ben"."""
        self.app.connections.add("Ben", relationship="investor",
                                 summary="Met over WeChat.")
        self.app.models = _Models(dict(PLAN, people=[]))
        for text, told in (("Write to Prof. Benjamin Thistlewood; 'contact Ben Thistlewood' "
                            "is on the calendar", False),
                           ("Ask Ben about the deck", True)):
            draft = self.app.drafts.create(text=text, collect=False)
            self.app.drafts.collect_and_plan(draft["id"])
            names = [one["name"] for one in self.app.drafts.get(draft["id"])["context"]["people"]]
            self.assertEqual("Ben" in names, told, text)

    def test_two_of_the_same_name_are_not_told_apart_by_guessing(self) -> None:
        self.app.connections.add("Ada Quillfeather", relationship="neighbour",
                                 summary="Lives next door.")
        draft = self._drafted()
        [person] = draft["context"]["people"]
        self.assertEqual(person["contact"]["several"], 2)
        self.assertIn("2 of the user's contacts are called this",
                      instruction_of(draft, draft["plan"], []))

    def test_the_address_tells_who_it_is_and_each_record_says_how_it_was_found(self) -> None:
        # Another Ada Quillfeather - the same name: told apart by the address.
        self.app.connections.add("Ada Quillfeather", relationship="neighbour",
                                 summary="Lives next door.")
        self.app.models = _Models(dict(PLAN, people=["Ada Quillfeather <ADA@northmoor.example>"]))
        _state(self.app, "st_6", "An email came in",
               'From ada@northmoor.example: "Could you send the slides beforehand?"',
               "2026-10-31T19:00:00+00:00")
        draft = self._drafted()
        [person] = draft["context"]["people"]
        self.assertEqual(person["name"], "Ada Quillfeather")
        self.assertIn("Northmoor Institute", person["contact"]["who"], "the one at that address")
        self.assertEqual(person["addresses"], ["ada@northmoor.example"])
        how = {one["title"]: one["matched"] for one in person["exchanges"]}
        self.assertEqual(how["An email came in"], "email address ada@northmoor.example")
        self.assertEqual(how["The user replied to Quillfeather"], "family name Quillfeather")
        said = instruction_of(draft, draft["plan"], [])
        self.assertIn("WHO IT IS FOR: Ada Quillfeather <ada@northmoor.example>", said)
        self.assertIn("[family name Quillfeather] The user replied to Quillfeather", said)
        self.assertIn("may be someone else of that name", said)

    def test_one_not_among_the_contacts_is_looked_up_publicly_first(self) -> None:
        self.app.models = _Models(dict(PLAN, people=["Oskar Brightwater"]))
        _state(self.app, "st_7", "Oskar Brightwater wrote about the review",
               "He asked for the report by Friday.", "2026-10-31T19:00:00+00:00")
        draft = self._drafted()
        said = instruction_of(draft, draft["plan"], [])
        self.assertIn("WHO IT IS FOR: Oskar Brightwater", said)
        self.assertIn("not among the user's contacts: before you write, find who they are "
                      "from public pages", said)

    def test_for_no_one_nothing_is_added(self) -> None:
        self.app.models = _Models(dict(PLAN, people=[]))
        draft = self._drafted()
        self.assertEqual(draft["context"]["people"], [])
        self.assertNotIn("WHO IT IS FOR", instruction_of(draft, draft["plan"], []))

    def test_the_plans_one_call_asks_for_them(self) -> None:
        from personalized_agent.agent.work.delegate import PLAN_PROMPT, PLAN_PROMPT_QUICK
        for prompt in (PLAN_PROMPT, PLAN_PROMPT_QUICK):
            self.assertIn('"people"', prompt)
        self._drafted()
        plans = [one for one in self.app.models.asked
                 if one.system in (PLAN_PROMPT, PLAN_PROMPT_QUICK)]
        self.assertEqual(len(plans), 1)
        self.assertEqual(json.loads(json.dumps(PLAN))["title"], "Summarise the meeting")


if __name__ == "__main__":
    unittest.main()

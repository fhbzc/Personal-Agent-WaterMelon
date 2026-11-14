"""THE AGENT SUGGESTS, THE USER CLOSES (the user, 2026-11-09: "允许模型suggest代办已做完，模型不能直接删
to do, 但是suggest之后，那个to do会出现一个mark as done按钮 ... override之后下一次要suggest finished必须
必用户上次override之后有明显progress"; "包括chat, 现在也要有把to do 建议删掉的功能"; "已有的这几条旧待办
按现在的规则重新命名"; "你在做这些management的时候，都要把相关context给他").

Everyone here is made up."""

from __future__ import annotations

import json
import unittest
from datetime import timedelta
from types import SimpleNamespace

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.common.events.models import new_id, utcnow
from personalized_agent.heartbeat.core import related
from personalized_agent.heartbeat.core.todo_check import TodoCheck


def _state(db, title, detail="", at=None, about="user", source="agent"):
    at = (at or utcnow()).isoformat()
    ident = new_id("state")
    db.execute("INSERT INTO states (id, title, detail, source, about, starts_at, ends_at, status, "
               "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
               (ident, title, detail, source, about, at, at, at, at))
    return ident


def _person(db, name, called=(), summary="", tags=()):
    ident = new_id("person")
    now = utcnow().isoformat()
    db.execute("INSERT INTO connection_people (id, name, called, summary, tags, created_at, "
               "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
               (ident, name, json.dumps(list(called), ensure_ascii=False), summary,
                json.dumps(list(tags), ensure_ascii=False), now, now))
    return ident


class _Models:
    """`answer` for the look itself; `words` for the search words (by the thing's id)."""

    def __init__(self, answer, words=None):
        self.answer, self.words, self.asked = answer, dict(words or {}), []

    def invoke(self, request, role="primary"):
        self.asked.append(request)
        if request.task.endswith("_words"):
            things = json.loads(request.messages[0]["content"])["things"]
            text = {"things": [{"id": one["id"], "words": self.words.get(one["id"], [])}
                               for one in things]}
        else:
            text = self.answer(request) if callable(self.answer) else self.answer
        return SimpleNamespace(text=json.dumps(text), stop_reason="end_turn")

    def looks(self):
        return [one for one in self.asked if one.task == "todo_check"]


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.db, self.todos = self.app.db, self.app.todos

    def tearDown(self) -> None:
        self.agent.cleanup()


class TheStoreTests(_Base):

    def test_suggested_never_done_and_turned_down(self) -> None:
        one = self.todos.add("Send the orchard plan to Wren Halloway")
        said = self.todos.suggest(one["id"], "The plan was sent at 10:02.", ["state_1"])
        self.assertEqual((said["suggested_kind"], said["done_at"], said["deleted_at"]), ("done", "", ""))
        self.assertEqual(json.loads(said["suggested_from"]), ["state_1"])
        self.assertIn(one["id"], [row["id"] for row in self.todos.open()], "still open")
        no = self.todos.decline(one["id"], "not yet")
        self.assertEqual((no["suggested_at"], no["suggested_kind"]), ("", ""))
        self.assertTrue(no["declined_at"])
        gone = self.todos.suggest(one["id"], "no longer wanted", kind="remove")
        self.assertEqual(gone["suggested_kind"], "remove")

    def test_renamed_once_its_first_words_kept(self) -> None:
        one = self.todos.add("Message 学姐 to set a time")
        self.todos.rename(one["id"], "Message Ilse Marrow to set a time")
        again = self.todos.rename(one["id"], "Message Ilse M. to set a time")
        self.assertEqual(again["renamed_from"], "Message 学姐 to set a time", "the first words kept")


class WhatIsRelatedTests(_Base):

    def test_the_rare_words_find_it_and_the_named_person_first(self) -> None:
        for n in range(12):
            _state(self.db, f"The user is reading WeChat chats, round {n}")
        hit = _state(self.db, "The user published the orchard-yield metric on Quillhub",
                     "Its page went online with the replication files.")
        ilse = _person(self.db, "Ilse Marrow", called=["学姐"], summary="startup advice, flying home")
        _person(self.db, "Tobin Fairweather", summary="reads WeChat chats with the user every day")
        found = related.activity(self.db, "Put the orchard-yield metric online")
        self.assertEqual(found[0]["id"], hit)
        people = related.people(self.db, "Message 学姐 to set a time for the startup review")
        self.assertEqual(people[0]["name"], "Ilse Marrow")
        self.assertTrue(ilse)

    def test_one_seen_often_is_not_meant_more(self) -> None:
        busy = _person(self.db, "Tobin Fairweather")
        now = utcnow().isoformat()
        for n in range(15):
            self.db.execute("INSERT INTO connection_activity (id, person_id, at, what, updated_at) "
                            "VALUES (?, ?, ?, ?, ?)", (new_id("cev"), busy, now,
                                                       f"chats about the orchard plan {n}", now))
        _person(self.db, "Wren Halloway")
        people = related.people(self.db, "Send the orchard plan to Wren Halloway")
        self.assertEqual(people[0]["name"], "Wren Halloway", "named beats seen often")


class TheHeartbeatsLookTests(_Base):

    def _check(self, answer, words=None):
        models = _Models(answer, words)
        return TodoCheck(self.db, self.todos, lambda: models, self.app.config), models

    def test_looks_done_on_what_it_was_shown_and_suggested_only(self) -> None:
        one = self.todos.add("Send the orchard plan to Wren Halloway")
        shown = _state(self.db, "The user sent the orchard plan to Wren Halloway",
                       "Sent with the yields attached.")
        check, models = self._check({"done": [{"id": one["id"], "why": "It was sent.",
                                               "shown_by": [shown]},
                                              {"id": "todo_nope", "why": "x", "shown_by": [shown]}]})
        said = check.run()
        self.assertEqual(said["suggested"], [one["id"]])
        kept = self.todos.get(one["id"])
        self.assertEqual((kept["suggested_kind"], kept["done_at"]), ("done", ""), "never done by it")
        handed = json.loads(models.looks()[0].messages[0]["content"])["open_todos"][0]
        self.assertIn("activity_about_it", handed)
        self.assertIn("people_who_may_be_meant", handed)

    def test_nothing_it_was_not_shown_and_turned_down_needs_progress_since(self) -> None:
        one = self.todos.add("Send the orchard plan to Wren Halloway")
        old = _state(self.db, "The user sent the orchard plan to Wren Halloway",
                     at=utcnow() - timedelta(hours=1))
        self.todos.decline(one["id"], "not yet")
        check, _models = self._check({"done": [{"id": one["id"], "why": "sent", "shown_by": [old]},
                                               ]})
        self.assertEqual(check.run()["suggested"], [], "a line from before they said no")
        self.db.execute("DELETE FROM kv WHERE key = 'todo_check.at'")
        later = _state(self.db, "The user sent the orchard plan to Wren Halloway again, final",
                       at=utcnow() + timedelta(minutes=5))
        check, _models = self._check({"done": [{"id": one["id"], "why": "sent", "shown_by": [later]}]})
        self.assertEqual(check.run()["suggested"], [one["id"]], "clear progress since")

    def test_renamed_only_to_a_name_it_was_shown(self) -> None:
        one = self.todos.add("Message 学姐 to set a time for the startup review")
        _person(self.db, "Ilse Marrow", called=["学姐"])
        check, _models = self._check({"rename": [
            {"id": one["id"], "text": "Message Ilse Marrow to set a time for the startup review"}]})
        self.assertEqual(check.run()["renamed"], [one["id"]])
        check, _models = self._check({"rename": [{"id": one["id"], "text": "Message Someone Else"}]})
        self.db.execute("DELETE FROM kv WHERE key = 'todo_check.at'")
        self.assertEqual(check.run().get("renamed", []), [], "once, and only to a name shown")

    def test_only_the_to_dos_with_something_new_are_sent_again(self) -> None:
        self.todos.add("Send the orchard plan to Wren Halloway")
        check, models = self._check({})
        check.run()
        self.assertEqual(len(models.looks()), 1)
        check.run()
        self.assertEqual(len(models.looks()), 1, "nothing new about it: not sent again, no model")
        _state(self.db, "The user drafted the orchard plan for Wren Halloway",
               at=utcnow() + timedelta(minutes=2))
        check.run()
        self.assertEqual(len(models.looks()), 2)
        words = [one for one in models.asked if one.task == "todo_check_words"]
        self.assertEqual(len(words), 1, "its search words written once, kept while it is the same")

    def test_found_by_the_agents_own_search_words(self) -> None:
        one = self.todos.add("Go see Prof. W. Halloway")
        for n in range(3):
            _state(self.db, f"The user is reading the news, part {n}")
        shown = _state(self.db, "The user booked coffee at Larchwood in Millbrook", "Room 4.12.")
        check, models = self._check({"done": []}, words={one["id"]: ["Larchwood", "Millbrook", "咖啡"]})
        check.run()
        handed = json.loads(models.looks()[0].messages[0]["content"])["open_todos"][0]
        self.assertIn(shown, [line["id"] for line in handed["activity_about_it"]],
                      "no word of its own, found by the words the agent wrote for it")

    def test_its_words_in_both_languages_english_first_then_translated(self) -> None:
        """The records are in more than one language (much of the chat in Chinese): the English
        rule names the English list alone, and the words come back from both."""
        asked = []

        class Models:
            def invoke(self, request, role="primary"):
                asked.append(request)
                return SimpleNamespace(text=json.dumps({"things": [
                    {"id": "t1", "english": ["meeting", "report", "Larchwood"],
                     "as_written": ["见面", "报告", "larchwood"]}]}), stop_reason="end_turn")

        said = related.search_words(Models(), [{"id": "t1", "text": "Meet about the report"}],
                                    "todo_check_words")
        self.assertEqual(said["t1"], ["见面", "报告", "larchwood", "meeting", "report"],
                         "the records' own words first, each word once")
        rules = asked[0].language.rules()
        self.assertEqual([one["rule"] for one in rules], ["internal"])
        self.assertIn('"english" list', rules[0]["what"])
        self.assertIn("as_written", asked[0].system)

    def test_due_every_three_hours_on_something_read(self) -> None:
        check, _models = self._check({})
        self.assertFalse(check.due(), "nothing read")
        _state(self.db, "The user is planning the orchard")
        self.assertTrue(check.due())
        check.run()
        _state(self.db, "The user is pruning the orchard plan")
        self.assertFalse(check.due(), "within three hours")
        self.assertTrue(check.due(utcnow() + timedelta(hours=3, minutes=1)))


class OnTheListTests(_Base):

    def test_the_overview_and_the_avatars_panel_offer_it(self) -> None:
        from personalized_agent.api.routes import build_routes
        one = self.todos.add("Send the orchard plan to Wren Halloway")
        self.todos.suggest(one["id"], "It was sent at 10:02.")
        row = self.app.quick_sections("todo")[0]["rows"][0]
        keys = [action["key"] for action in row["actions"]][:2]
        self.assertEqual(keys, ["suggested", "decline"], "first, one press to close it")
        self.assertEqual(row["actions"][0]["path"], "/api/todos/done")
        self.assertIn("It was sent at 10:02.", row["note"])

        class _Request:
            def __init__(self, **values):
                self.body = values

            def get(self, key, default=None):
                return self.body.get(key, default)

            def require(self, key):
                return self.body[key]
        route = build_routes(self.app)[("POST", "/api/todos/decline")]
        said = route(_Request(id=one["id"]))
        self.assertTrue(said["todo"]["declined_at"])
        self.assertEqual(said["todo"]["suggested_at"], "")

    def test_the_page_draws_its_two_buttons(self) -> None:
        source = (support.ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text("utf-8")
        start = source.index("function ovTodoSuggested(one) {")
        body = source[start:source.index("\n}\n", start)]
        for word in ("Mark as done", "Not done", "Remove", "Keep", "ov-todo-decline"):
            self.assertIn(word, body)
        self.assertIn("ovTodoSuggested(one)", source[source.index("function ovTodoBox()"):])

    def test_the_chat_suggests_on_the_to_do_itself(self) -> None:
        one = self.todos.add("Send the orchard plan to Wren Halloway")
        tools = self.app.agent.tools
        said = tools.call("todo_suggest_remove", {"id": one["id"], "why": "They said it is not wanted."})
        self.assertEqual(said["suggested"], one["id"])
        self.assertEqual(self.todos.get(one["id"])["suggested_kind"], "remove")
        self.assertIn("error", tools.call("todo_suggest_done", {"id": one["id"]}), "says why")
        self.assertIsNone(self.todos.get(one["id"])["deleted_at"] or None, "never removed by it")


class AfterAToDoTests(_Base):

    def test_the_look_is_given_what_was_said_and_why_each_was_made(self) -> None:
        from personalized_agent.agent.feedback.after_todo import AfterTodo
        from personalized_agent.common.events.models import ScheduledTask
        when = utcnow() + timedelta(days=2)
        self.app.scheduler.create_task(ScheduledTask(
            title="Meet Wren Halloway", description="At the orchard", scheduled_at=when,
            execution_type="notification", task_payload={"notify_user": False},
            reason="The user asked for it in the chat."))
        todo = self.todos.add("Go see Prof. Wren Halloway - Monday 3:30pm")
        conversation = self.app.conversations.ensure(None)
        # As it was said (2026-11-09): not one word of the to-do in it.
        self.app.conversations.add_message(conversation, "user", "从to do 里删掉，就calendar就行了")
        models = _Models({"close": [], "finish": []})
        look = AfterTodo(self.db, lambda: models, self.app.scheduler, self.todos)
        look.run(todo, "removed")
        asked = [one for one in models.asked if one.task == "after_todo"][0]
        handed = json.loads(asked.messages[0]["content"])
        self.assertIn("what_was_found", handed)
        just = handed["what_was_found"]["the_chat_just_before"]
        self.assertEqual([line["said"] for line in just], ["从to do 里删掉，就calendar就行了"])
        self.assertTrue([one for one in models.asked if one.task == "after_todo_words"])
        self.assertEqual(handed["reminders_waiting"][0]["why_made"], "The user asked for it in the chat.")
        self.assertIn("the calendar's", asked.system)

    def test_taken_off_on_the_chats_word_its_why_says_what_stays(self) -> None:
        from personalized_agent.agent.feedback.after_todo import AfterTodo
        from personalized_agent.common.events.models import ScheduledTask
        self.app.scheduler.create_task(ScheduledTask(
            title="Meet Wren Halloway", description="Monday 3:30pm",
            scheduled_at=utcnow() + timedelta(days=2), execution_type="notification",
            task_payload={"notify_user": False}, reason="The user asked for it in the chat."))
        todo = self.todos.add("Go see Prof. Wren Halloway - Monday 3:30pm")
        self.app.agent.tools.call("todo_suggest_remove", {
            "id": todo["id"], "why": "Only off the list: the meeting stays on the calendar."})
        models = _Models({"close": [], "finish": []})
        AfterTodo(self.db, lambda: models, self.app.scheduler, self.todos).run(
            self.todos.get(todo["id"]), "removed")
        asked = [one for one in models.asked if one.task == "after_todo"][0]
        handed = json.loads(asked.messages[0]["content"])
        self.assertEqual(handed["the_to_do"]["the_agents_suggestion"],
                         {"kind": "remove", "why": "Only off the list: the meeting stays on the calendar."})
        self.assertIn("what it says is to stay, stays", asked.system)


if __name__ == "__main__":
    unittest.main()

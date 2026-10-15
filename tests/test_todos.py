"""Add to to-do: a notification stopped and kept on the to-do list the
Overview shows beside the Summary task's progress bars - ticked by the user,
or by the agent's to-do step when it sees one done."""

from __future__ import annotations

import unittest

from support import TemporaryAgent

from personalized_agent.agent.feedback.templates import (BAD_LABEL, GOT_IT, TO_TODO,
                                                         TO_TODO_LABEL, options_for)


class TheButtonsSayWhatHappensTests(unittest.TestCase):

    def test_each_says_whether_and_when_it_comes_back(self) -> None:
        reminder = {one.key: one.label for one in options_for("reminder")}
        self.assertEqual(reminder["acknowledged"], GOT_IT)
        self.assertIn("no more notifications", GOT_IT)
        self.assertEqual(reminder["delay"], "Remind me in 15 minutes", "the default, said")
        self.assertEqual({one.key: one.label for one in options_for("reminder", 1800)}["delay"],
                         "Remind me in 30 minutes")
        self.assertIn("listed in To do on the Overview", TO_TODO_LABEL)
        self.assertIn("fewer similar ones", BAD_LABEL)
        for template in ("reminder", "permission", "passive"):
            self.assertIn(TO_TODO, [one.key for one in options_for(template)], template)


class AddToTodoTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _notify(self, title="Send the draft", message="The reply you said you would send."):
        return self.app.notifications.notify(title, message, force=True)

    def test_a_notification_onto_the_list_and_off_the_screen(self) -> None:
        shown = self._notify()
        ident = shown["id"] if isinstance(shown, dict) else shown.id
        todo = self.app.notification_to_todo(ident)
        self.assertEqual(todo["text"], "Send the draft")
        self.assertEqual(todo["notification_id"], ident)
        self.assertTrue(self.app.notifications.get(ident).get("read")
                        or self.app.notifications.get(ident).get("read_at"))
        # Twice is once.
        self.assertEqual(self.app.notification_to_todo(ident)["id"], todo["id"])
        self.assertEqual([one["id"] for one in self.app.todos.shown()], [todo["id"]])

    def test_ticked_unticked_and_deleted(self) -> None:
        one = self.app.todos.add("Book the room")
        self.app.todos.tick(one["id"])
        self.assertEqual(self.app.todos.get(one["id"])["done_by"], "user")
        self.assertEqual(self.app.todos.open(), [])
        self.app.todos.tick(one["id"], done=False)
        self.assertEqual(len(self.app.todos.open()), 1)
        self.assertTrue(self.app.todos.delete(one["id"]))
        self.assertEqual(self.app.todos.shown(), [])

    def test_only_the_user_removes_one_the_agent_never_ticks_it(self) -> None:
        one = self.app.todos.add("Book the room", "for Friday's review")
        beat = self.app.heartbeat
        lines = beat._open_todos()
        self.assertFalse(any(one["id"] in line for line in lines), lines)
        ticked = beat._tick_todos([{"id": one["id"], "why": "it looked booked",
                                    "evidence": []}])
        self.assertEqual(ticked, [])
        self.assertEqual(self.app.todos.get(one["id"])["done_at"], "")

    def test_the_routes(self) -> None:
        from personalized_agent.api.routes import build_routes

        class _Ask(dict):
            def get(self, key, default=None):
                return super().get(key, default)

            def require(self, key):
                return self[key]

            def int_param(self, key, default=0):
                return int(self.get(key, default))

        routes = build_routes(self.app)
        shown = self._notify()
        ident = shown["id"] if isinstance(shown, dict) else shown.id
        said = routes[("POST", "/api/notifications/todo")](_Ask(id=ident))
        listed = routes[("GET", "/api/todos")](_Ask())["todos"]
        self.assertEqual([one["id"] for one in listed], [said["todo"]["id"]])
        routes[("POST", "/api/todos/tick")](_Ask(id=said["todo"]["id"], done=True))
        self.assertTrue(self.app.todos.get(said["todo"]["id"])["done_at"])
        routes[("POST", "/api/todos/delete")](_Ask(id=said["todo"]["id"]))
        self.assertEqual(routes[("GET", "/api/todos")](_Ask())["todos"], [])
        self.assertEqual(routes[("GET", "/api/notifications")](_Ask())["remind_later_minutes"],
                         30)


if __name__ == "__main__":
    unittest.main()

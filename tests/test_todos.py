"""Add to to-do: a notification stopped and kept on the to-do list the
Overview shows beside the Summary task's progress bars - ticked by the user,
or by the agent's to-do step when it sees one done."""

from __future__ import annotations

import unittest
from unittest import mock

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
        # Nothing of the agent's ticks anything any more: the step that ticked
        # the tasks' next steps is gone (section 37, as amended 2026-10-18).
        one = self.app.todos.add("Book the room", "for Friday's review")
        from personalized_agent.heartbeat.checklist.store import STEPS
        self.assertNotIn("step_todo", [step.id for step in STEPS])
        self.assertFalse(hasattr(self.app.heartbeat, "_tick_todos"))
        self.assertEqual(self.app.todos.get(one["id"])["done_at"], "")

    def test_the_next_version_counts_the_removed(self) -> None:
        """The user, 2026-11-01: V4 and V5 listed, V1-V3 removed - "Delegate
        work" said V3; the next is V6."""
        import json as _json
        for n, (version, removed) in enumerate([(1, True), (2, True), (3, True), (4, False),
                                                 (5, False)]):
            self.app.db.execute(
                "INSERT INTO work_drafts (id, created_at, updated_at, source, status, options) "
                "VALUES (?, ?, ?, 'todo', 'started', ?)",
                (f"draft_{n}", f"2026-10-2{n}T09:00:00", f"2026-10-2{n}T09:00:00",
                 _json.dumps({"todo": "todo_fern", "version": version, "removed": removed})))
        came = self.app.drafts.of_todos()["todo_fern"]
        self.assertEqual([one["version"] for one in came["versions"]], [4, 5])
        self.assertEqual(came["next"], 6)
        self.assertEqual(self.app.drafts._next_version("todo_fern"), 6, "the same the server starts")

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
        # One of the user's own, added by hand (the user, 2026-11-01).
        mine = routes[("POST", "/api/todos/add")](_Ask(text="  Water the ferns ",
                                                       detail="the big pot too"))["todo"]
        self.assertEqual((mine["text"], mine["detail"], mine["notification_id"]),
                         ("Water the ferns", "the big pot too", ""))
        self.assertEqual([one["id"] for one in routes[("GET", "/api/todos")](_Ask())["todos"]],
                         [mine["id"]])
        from personalized_agent.api.errors import ApiError
        with self.assertRaises(ApiError):
            routes[("POST", "/api/todos/add")](_Ask(text="   "))
        # Its words changed (the user, 2026-11-04): grouped again at once, and a lesson.
        with mock.patch.object(self.app.list_groups, "notice") as notice, \
                mock.patch.object(self.app.lessons, "the_user_did") as lesson:
            said = routes[("POST", "/api/todos/edit")](_Ask(id=mine["id"], text=" Water the palms ",
                                                            detail=""))["todo"]
            self.assertEqual((said["text"], said["detail"]), ("Water the palms", ""))
            notice.assert_called_once()
            self.assertEqual(notice.call_args.args[0], "todo")
            self.assertEqual(lesson.call_args.args[0], "edited_todo")
            self.assertIn("Water the ferns", lesson.call_args.args[1])
            self.assertIn("Water the palms", lesson.call_args.args[1])
            routes[("POST", "/api/todos/edit")](_Ask(id=mine["id"], text="Water the palms", detail=""))
            notice.assert_called_once()                  # nothing changed, nothing to do
        with self.assertRaises(ApiError):
            routes[("POST", "/api/todos/edit")](_Ask(id=mine["id"], text="  "))
        with self.assertRaises(ApiError) as missing:
            routes[("POST", "/api/todos/edit")](_Ask(id="nope", text="x"))
        self.assertEqual(missing.exception.status, 404)
        self.assertEqual(self.app.todos.get(mine["id"])["text"], "Water the palms")
        self.assertEqual(routes[("GET", "/api/notifications")](_Ask())["remind_later_minutes"],
                         30)


if __name__ == "__main__":
    unittest.main()

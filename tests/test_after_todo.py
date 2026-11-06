"""Marked done or removed, a to-do goes, and the model looks at what it leaves behind. Everything
here is made up."""

from __future__ import annotations

import json
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.common.events.models import ScheduledTask, utcnow


class _Model:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def invoke(self, request, role=""):
        self.asked.append((request, role))
        return SimpleNamespace(text=json.dumps(self.answer))


class _Request:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def require(self, key):
        return self.values[key]


class Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.after = self.app.after_todo

    def _reminder(self, title="Write to Sam to set a day"):
        return self.app.scheduler.create_task(ScheduledTask(
            title=title, description=title, scheduled_at=utcnow() + timedelta(days=1),
            execution_type="notification", task_payload={"notify_user": True},
            created_by="heartbeat", reason="made up"))

    def _step(self, steps):
        self.app.db.execute(
            "INSERT INTO state_tasks (id, number, members, created_at, updated_at, scope, described) "
            "VALUES ('p-1', 1, '[]', '', '', 'live', ?)",
            (json.dumps({"title": "A made-up project", "todo": steps}),))

    def _said(self, answer):
        model = _Model(answer)
        self.after._models = lambda: model
        return model


class TheLookTests(Base):

    def test_nothing_waiting_no_model_is_asked(self) -> None:
        model = self._said({})
        self.assertFalse(self.after.run({"text": "x"}, "done")["asked"])
        self.assertEqual(model.asked, [])

    def test_it_is_told_what_is_waiting_and_what_went(self) -> None:
        task = self._reminder()
        self._step([{"id": "s1", "text": "Book the room"}])
        self.app.todos.add("Buy tea")
        model = self._said({})
        self.after.run({"text": "Write to Sam", "detail": "about lunch"}, "removed")
        request, role = model.asked[0]
        said = json.loads(request.messages[0]["content"])
        self.assertEqual((said["what_the_user_did"], said["the_to_do"]["text"], role),
                         ("removed", "Write to Sam", "heartbeat"))
        self.assertEqual([one["id"] for one in said["reminders_waiting"]], [task.id])
        self.assertEqual(said["open_steps"][0]["step"], "s1")
        self.assertEqual(said["the_rest_of_their_list"], ["Buy tea"])

    def test_a_reminder_it_names_is_closed_and_says_why(self) -> None:
        task = self._reminder()
        self._said({"close": [{"id": task.id, "why": "It is written."}]})
        self.assertEqual(self.after.run({"text": "Write to Sam"}, "done")["closed"], [task.id])
        kept = self.app.scheduler.store.get(task.id)
        self.assertEqual((kept.status, kept.metadata["closed_reason"]), ("closed", "It is written."))
        self.assertIn("done", kept.metadata["closed_by"])

    def test_one_it_was_not_shown_or_with_no_reason_stays(self) -> None:
        task = self._reminder()
        self._said({"close": [{"id": "made-up", "why": "x"}, {"id": task.id, "why": ""}]})
        self.assertEqual(self.after.run({"text": "x"}, "done")["closed"], [])
        self.assertEqual(self.app.scheduler.store.get(task.id).status, "pending")

    def test_a_step_is_ticked_saying_why_and_its_reminder_goes(self) -> None:
        task = self._reminder()
        self._step([{"id": "s1", "text": "Write to Sam", "reminder": task.id}])
        self._said({"finish": [{"work": "p-1", "step": "s1", "why": "The same thing."}]})
        self.assertEqual(self.after.run({"text": "Write to Sam"}, "done")["finished"], ["s1"])
        said = json.loads(self.app.db.query_one("SELECT described FROM state_tasks WHERE id='p-1'")["described"])
        self.assertEqual((said["todo"][0]["done_by"], said["todo"][0]["done_why"]), ("agent", "The same thing."))
        self.assertEqual(self.app.scheduler.store.get(task.id).status, "closed")

    def test_a_tick_the_user_took_back_is_not_shown(self) -> None:
        self._step([{"id": "s1", "text": "Write", "open_by_user": True}])
        self._said({"finish": [{"work": "p-1", "step": "s1", "why": "x"}]})
        self.assertEqual(self.after.run({"text": "x"}, "done"), {"asked": False, "closed": [], "finished": []})

    def test_the_users_list_is_never_touched(self) -> None:
        other = self.app.todos.add("Write to Sam again")
        task = self._reminder()
        self._said({"close": [{"id": other["id"], "why": "x"}],
                    "finish": [{"work": "todo", "step": other["id"], "why": "x"}]})
        self.after.run({"text": "Write to Sam"}, "done")
        self.assertEqual(self.app.todos.get(other["id"])["done_at"], "")
        self.assertEqual(self.app.todos.get(other["id"])["deleted_at"], "")
        self.assertEqual(self.app.scheduler.store.get(task.id).status, "pending")

    def test_a_reply_that_is_not_json_changes_nothing(self) -> None:
        task = self._reminder()
        model = _Model({})
        model.invoke = lambda request, role="": SimpleNamespace(text="I think nothing.")
        self.after._models = lambda: model
        self.assertEqual(self.after.run({"text": "x"}, "done")["closed"], [])
        self.assertEqual(self.app.scheduler.store.get(task.id).status, "pending")


class TheListTests(Base):

    def _route(self, path, **values):
        from personalized_agent.api.routes import build_routes
        return build_routes(self.app)[("POST", path)](_Request(**values))

    def setUp(self) -> None:
        super().setUp()
        self.after.check = mock.Mock()
        self.app.lessons.the_user_did = mock.Mock()
        self.todo = self.app.todos.add("Write to Sam", "about lunch")

    def test_marked_done_it_goes_at_once_and_the_model_looks(self) -> None:
        self._route("/api/todos/done", id=self.todo["id"])
        self.assertEqual(self.app.todos.shown(), [], "off the list")
        kept = self.app.todos.get(self.todo["id"])
        self.assertEqual(kept["done_by"], "user")
        self.after.check.assert_called_once()
        self.assertEqual(self.after.check.call_args.args[1], "done")
        self.assertEqual(self.app.lessons.the_user_did.call_args.args[0], "done_todo")

    def test_removed_the_model_looks_too(self) -> None:
        self._route("/api/todos/delete", id=self.todo["id"])
        self.assertEqual(self.after.check.call_args.args[1], "removed")
        self.assertEqual(self.after.check.call_args.args[0]["text"], "Write to Sam")
        self.assertEqual(self.app.lessons.the_user_did.call_args.args[0], "removed_todo")

    def test_one_that_is_not_there_says_so(self) -> None:
        from personalized_agent.api.routes import ApiError
        with self.assertRaises(ApiError):
            self._route("/api/todos/done", id="made-up")
        self.after.check.assert_not_called()


class SteadyAvatarTests(unittest.TestCase):

    def _canvas(self):
        from PIL import Image, ImageDraw
        canvas = Image.new("RGB", (200, 200), (237, 237, 237))
        pen = ImageDraw.Draw(canvas)
        pen.rectangle((60, 60, 131, 131), fill=(40, 90, 160))
        pen.ellipse((75, 70, 120, 115), fill=(230, 190, 120))
        return canvas

    def test_two_pixels_off_it_is_still_near_where_the_old_one_is_not(self) -> None:
        from personalized_agent.platform_layer.wechat_items import _steady_avatar
        canvas = self._canvas()
        a, b = _steady_avatar(canvas, [60, 60, 72, 72]), _steady_avatar(canvas, [62, 61, 72, 72])
        self.assertLessEqual((int(a.split(":")[0], 16) ^ int(b.split(":")[0], 16)).bit_count(), 6)

    def test_part_of_one_is_not_taken(self) -> None:
        from personalized_agent.platform_layer.wechat_items import _steady_avatar
        self.assertIsNone(_steady_avatar(self._canvas(), [60, 78, 72, 54]))

    def test_it_changes_nothing_that_decides(self) -> None:
        import inspect
        from personalized_agent.data_manage.storage import wechat_store
        self.assertNotIn("peer_avatar_steady", inspect.getsource(wechat_store))


if __name__ == "__main__":
    unittest.main()

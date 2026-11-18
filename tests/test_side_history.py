"""THE SIDE PANEL'S HISTORY, SEARCH AND REACTIONS (api/routes.py; the user, 2026-11-17: "默认显示的是到上上个summary
point 为止的history, 如果用户继续上翻会继续load之前的" - "要支持搜索功能，可以做exact match ... (类似wechat的搜索机制)" -
"agent的每个回复，有一个快速emoji功能"). Everything here is made up."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from support import TemporaryAgent

from personalized_agent.api.routes import build_routes


def _asked(**said):
    return SimpleNamespace(get=lambda name, default=None: said.get(name, default), body=said, query={})


class HistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.routes = build_routes(self.app)
        self.side = self.app.side_conversation()
        self.said = []
        stamp = iter(f"2026-11-0{day}T{hour:02d}:00:00+00:00" for day in range(1, 10) for hour in range(24))

        def add(role, words):
            one = self.app.conversations.add_message(self.side, role, words)
            at = next(stamp)
            self.app.db.execute("UPDATE messages SET created_at = ? WHERE id = ?", (at, one["id"]))
            self.said.append(dict(one, created_at=at))
            return self.said[-1]
        for n in range(30):
            add("user" if n % 2 == 0 else "assistant", f"line {n} about beans" if n == 7 else f"line {n}")
        # Two summary points: through line 9, then through line 19.
        for through in (9, 19):
            mark = self.app.conversations.add_message(self.side, "system", "", metadata={
                "origin": "compacted", "at": self.said[through]["created_at"], "count": 10, "summary": f"to {through}"})
            self.app.db.execute("UPDATE messages SET created_at = ? WHERE id = ?", (next(stamp), mark["id"]))

    def history(self, **said):
        return self.routes[("GET", "/api/side/conversation")](_asked(**said))

    def test_from_the_point_before_last_then_a_point_at_a_time(self) -> None:
        first = self.history()
        words = [one["content"] for one in first["messages"] if one["role"] != "system"]
        self.assertEqual(words[0], "line 10", "from the summary point before last")
        self.assertEqual(words[-1], "line 29")
        self.assertTrue(first["earlier"])
        self.assertEqual([one["metadata"]["summary"] for one in first["messages"] if one["role"] == "system"],
                         ["to 19"], "its summary marks where they stand among it")
        more = self.history(before=first["from"])
        self.assertEqual([one["content"] for one in more["messages"] if one["role"] != "system"],
                         [f"line {n}" if n != 7 else "line 7 about beans" for n in range(10)])
        self.assertFalse(more["earlier"], "nothing before it")

    def test_found_and_shown_where_it_was_said(self) -> None:
        found = self.routes[("GET", "/api/side/search")](_asked(q="ABOUT BEANS"))["hits"]
        self.assertEqual([one["id"] for one in found], [self.said[7]["id"]], "exactly, any case")
        self.assertEqual(found[0]["found"].lower(), "about beans")
        self.assertEqual(self.routes[("GET", "/api/side/search")](_asked(q="nothing like it"))["hits"], [])
        first = self.history()
        around = self.history(around=self.said[7]["id"], before=first["from"])
        words = [one["content"] for one in around["messages"] if one["role"] != "system"]
        self.assertIn("line 7 about beans", words)
        self.assertEqual(words[-1], "line 9", "up to what is shown already")


class AskAboutWhatWasToldTests(unittest.TestCase):
    def test_a_notification_asked_about_by_its_message(self) -> None:
        """2026-11-17: "recommended content 好像不能ask about?"."""
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        made = app.notifications.notify("A bean guide", "How to keep beans dry. https://example.org/beans",
                                        kind="proactive")
        told = [one for one in app.conversations.messages(app.side_conversation(), limit=None)
                if (one.get("metadata") or {}).get("notification_id") == made.id]
        self.assertTrue(told, "the notification said in the side panel's conversation")
        with mock.patch.object(app.agent, "follow_up", return_value={"id": "fu1"}) as asked:
            said = build_routes(app)[("POST", "/api/chat/followup")](
                _asked(note=made.id, about="keep beans dry", question="how dry?"))
        self.assertEqual(said, {"id": "fu1"})
        self.assertEqual(asked.call_args[0][0], told[-1]["id"])


class ReactionTests(unittest.TestCase):
    def test_put_on_taken_off_and_told_after(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        routes = build_routes(app)
        side = app.side_conversation()
        said = app.conversations.add_message(side, "assistant", "The seed club is at 3:30.")
        react = routes[("POST", "/api/side/react")]
        self.assertTrue(react(_asked(id=said["id"], emoji="\U0001F44D", on=True))["ok"])
        self.assertEqual(app.conversations.message(said["id"])["metadata"]["reactions"], ["\U0001F44D"])
        from personalized_agent.agent.chat_pictures import told
        self.assertIn("The user reacted to this with: \U0001F44D (thumbs up)",
                      told(app.conversations.message(said["id"])))
        react(_asked(id=said["id"], emoji="\U0001F44D", on=False))
        self.assertEqual(app.conversations.message(said["id"])["metadata"]["reactions"], [])
        self.assertIn("error", react(_asked(id=said["id"], emoji="x", on=True)))
        # Bad content, its own press beside the emoji (2026-11-17: "要在emoji旁边加一个快速的bad content").
        react(_asked(id=said["id"], bad=True, on=True))
        self.assertIn("The user marked this: bad content", told(app.conversations.message(said["id"])))

    def test_on_a_chat_reply_learned_from_at_once(self) -> None:
        """2026-11-17: "这个一点反应就立刻学习" - not left to the quiet summary."""
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        side = app.side_conversation()
        app.conversations.add_message(side, "user", "when is the seed club?")
        said = app.conversations.add_message(side, "assistant", "The seed club is at 3:30.")
        react = build_routes(app)[("POST", "/api/side/react")]
        with mock.patch.object(app.lessons, "record", return_value="ex1") as kept:
            react(_asked(id=said["id"], emoji="\U0001F914", on=True))
            react(_asked(id=said["id"], bad=True, on=True))
            react(_asked(id=said["id"], bad=True, on=False))
        self.assertEqual([one.kwargs["action"] for one in kept.call_args_list], ["reaction", "bad_content"],
                         "each put on, at once; one taken off is not")
        self.assertIn("when is the seed club?", kept.call_args_list[0][0][2])
        self.assertIn("thinking", kept.call_args_list[0].kwargs["reason"])

    def test_on_a_notification_emoji_and_bad_content_are_learned_from(self) -> None:
        """2026-11-17: "bad content, emoji都是要被作为学习的对象"."""
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        made = app.notifications.notify("A bean guide", "https://example.org/beans", kind="proactive")
        react = build_routes(app)[("POST", "/api/side/react")]
        with mock.patch.object(app, "learn_answer") as learned:
            react(_asked(note=made.id, emoji="\U0001F602", on=True))
            self.assertIn("laughing", learned.call_args.kwargs["meaning"])
            react(_asked(note=made.id, bad=True, on=True))
            self.assertIn("bad content", learned.call_args.kwargs["meaning"])
        meta = app.notifications.get(made.id)["metadata"]
        self.assertEqual((meta["reactions"], meta["bad_content"]), (["\U0001F602"], True))


if __name__ == "__main__":
    unittest.main()

"""ANSWERING ANOTHER PERSON'S AGENT (society/interactive.py; the user, 2026-11-15):
- "回复agent的时候，agent全部的tool都要能accessible" / "你发出前反正也是要过一个reviewer, 检查privacy什么的": every tool of the
  chat at hand - what reads runs at once; what changes or sends anything is noted, done once the review lets it
  through, else held for the user's approval;
- "允许agent对对方的身份进行核实（包括online search等)": their public page given, the web at hand;
- "这个task的安排要根据对方和用户的关系酌情安排": what waits for the user told at once only when who they are calls for it.
Fictional throughout."""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

import support  # noqa: F401

from personalized_agent.agent.core.tools import ToolRegistry
from personalized_agent.models.base import ToolSpec
from personalized_agent.society import interactive as soc_interactive
from personalized_agent.society.interactive import Interactive

ROOT = Path(__file__).resolve().parents[1]


class _Approvals:
    def __init__(self):
        self.held = []

    def hold(self, source, kind, title, payload, why="", detail="", due=""):
        self.held.append((source, kind, title, payload, why))
        return "ap_1"


class _Monitor:
    def __init__(self):
        self.said = []

    def relay(self, builtin, title, message, **said):
        self.said.append((builtin, title, message, said))


class AnsweringWithEveryToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ran = []
        registry = ToolRegistry()
        schema = {"type": "object", "properties": {}}
        registry.add(ToolSpec("list_scheduled_tasks", "the calendar", schema),
                     lambda args: self.ran.append("list") or {"tasks": ["Tea, Friday 4pm"]})
        registry.add(ToolSpec("search_todos", "the to-dos", schema, read_only=True),
                     lambda args: self.ran.append("todos") or {"todos": []})
        registry.add(ToolSpec("schedule_task", "puts a thing on the calendar", schema),
                     lambda args: self.ran.append(("schedule", args)) or {"task_id": "t1"})
        registry.add(ToolSpec("society_remember_who", "the user's word only", schema),
                     lambda args: self.ran.append("remember") or {})
        self.approvals, self.monitor = _Approvals(), _Monitor()
        app = SimpleNamespace(agent=SimpleNamespace(tools=registry), approvals=self.approvals, monitor=self.monitor,
                              connections=None, config=None)
        society = SimpleNamespace(app=app, contact=lambda peer: {"name": "Rowan Pike"})
        self.answering = Interactive(society)
        self.answering._asked_to_do = []

    def test_what_reads_runs_and_what_changes_is_only_noted(self) -> None:
        self.assertEqual(self.answering._a_tool("list_scheduled_tasks", {}), {"tasks": ["Tea, Friday 4pm"]})
        self.assertEqual(self.answering._a_tool("search_todos", {}), {"todos": []})
        said = self.answering._a_tool("schedule_task", {"title": "Tea with Rowan"})
        self.assertIn("noted", said)
        self.assertEqual(self.ran, ["list", "todos"], "nothing changed yet")
        self.assertEqual(self.answering._asked_to_do, [{"tool": "schedule_task", "arguments": {"title": "Tea with Rowan"}}])
        self.assertIn("error", self.answering._a_tool("society_remember_who", {}), "the user's word only")
        names = {one.name for one in self.answering._registry_specs()}
        self.assertNotIn("society_remember_who", names)
        self.assertIn("schedule_task", names)

    def test_done_once_the_review_lets_it_through_else_held_for_the_user(self) -> None:
        action = {"kind": "tool", "tool": "schedule_task", "arguments": {"title": "Tea with Rowan"}}
        message = {"id": "sm_1", "peer_id": "pa-AAAA-BBBB"}
        done = self.answering._tool_action(message, action, {"ok": True}, True)
        self.assertTrue(done.startswith("Done by your agent"))
        self.assertIn(("schedule", {"title": "Tea with Rowan"}), self.ran)
        held = self.answering._tool_action(message, action, {"ok": False, "why": "it gives away the user's day"}, False)
        self.assertTrue(held.startswith("Waiting for your approval"))
        [(source, kind, title, payload, why)] = self.approvals.held
        self.assertEqual((source, kind, payload["tool"]), ("society_reply", "event_tool", "schedule_task"))
        self.assertIn("gives away the user's day", why)
        listed = self.answering._actions({"tool_actions": [{"tool": "schedule_task", "arguments": {}}]}, "reply",
                                         {"peer_id": "pa-AAAA-BBBB", "subject": "Tea"})
        self.assertEqual([one["kind"] for one in listed], ["tool"], "the review sees it with the answer")

    def test_told_at_once_only_when_who_they_are_calls_for_it(self) -> None:
        last = {"id": "sm_2", "peer_id": "pa-AAAA-BBBB"}
        self.answering._tell_if_now(last, {"urgency": "report", "urgency_why": "an acquaintance, nothing pressing"})
        self.assertEqual(self.monitor.said, [])
        self.answering._tell_if_now(last, {"urgency": "now", "urgency_why": "her sister, asking about tonight"})
        [(builtin, title, message, said)] = self.monitor.said
        self.assertEqual(builtin, "deadline")
        self.assertIn("Rowan Pike", title)
        self.assertIn("tonight", message)

    def test_answered_a_minute_after_the_last_of_them_not_half_an_hour(self) -> None:
        # The user, 2026-11-15: "为什么这里没有被我的agent读" - it waited for a round every 30 minutes.
        from personalized_agent.society import service
        self.assertEqual((service.POLL_SECONDS, service.SETTLE_SECONDS), (60, 60))
        answered = []
        waiting = {"pa-OLD0-0000": [{"id": "a", "created_at": "2026-11-20T10:00:00+00:00"}],
                   "pa-NEW0-0000": [{"id": "b", "created_at": "2026-11-20T10:05:00+00:00"}]}
        self.answering.society.waiting = lambda: waiting
        self.answering.society.group_of = lambda peer: "read"
        self.answering.society.mark_handled = lambda ids, read=True: answered.extend(ids)
        self.answering.answer_waiting("2026-11-20T10:04:30+00:00")
        self.assertEqual(answered, ["a"], "one who wrote within the minute: more may be on the way")

    def test_the_open_thread_is_not_moved_under_the_user(self) -> None:
        # The user, 2026-11-15: "你现在这个draft好像会自动刷新view的位置".
        page = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text("utf-8")
        self.assertIn('#society-main [data-draft-subject]:focus', page, "a draft in hand: not drawn again")
        self.assertIn("text.value !== String(kept.text", page, "a draft changed and not saved: not drawn again")
        self.assertIn("list.scrollTop = listAt && !listAt.atEnd ? listAt.top : list.scrollHeight", page)

    def test_the_prompt_says_so(self) -> None:
        system = soc_interactive.SYSTEM
        self.assertIn("YOUR TOOLS ARE ALL AT HAND", system)
        self.assertIn("CHECK WHO THEY ARE", system)
        self.assertIn("web_search", system)
        self.assertIn('"urgency": "now | report"', system)
        self.assertIn("never with {user}'s private details", system)


if __name__ == "__main__":
    unittest.main()

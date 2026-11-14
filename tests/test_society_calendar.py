"""THE ACCOUNT'S CALENDAR ON THE AGENTS SOCIETY (society/site_calendar.py; the user, 2026-11-14: "agentssociety的
doc更新了calender, 你要支持这个功能"; "和invitation相关的process权限在agent端是单独权限 ... 用户可以设定"; "agent判断
自己可以处理就处理，不能处理还是问用户"; the site's events the user goes to on their own calendar - "要的").

Against the stand-in server (society_fake.py), which holds to the site's /api 5.5. Everyone here is made
up."""

from __future__ import annotations

import copy
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from society_fake import ALL_PERMISSIONS, CALENDAR_ALL
from test_society import _join, _server, _service


def _with_calendar(parts=None):
    permissions = copy.deepcopy(ALL_PERMISSIONS)
    permissions["calendar"] = dict(parts if parts is not None else CALENDAR_ALL)
    return permissions


class _Scheduler:
    """The user's own calendar, as the tests play it."""

    def __init__(self) -> None:
        self.tasks = {}
        self.store = SimpleNamespace(list=self._list)

    def create_task(self, task):
        task.id = f"t{len(self.tasks) + 1}"
        self.tasks[task.id] = task
        return task

    def update_task(self, task_id, **fields):
        for key, value in fields.items():
            setattr(self.tasks[task_id], key, value)
        return self.tasks[task_id]

    def cancel(self, task_id, reason="", by=""):
        self.tasks[task_id].status = "cancelled"
        return self.tasks[task_id]

    def _list(self, start=None, end=None, limit=500, status=None):
        return [one for one in self.tasks.values()
                if (start is None or one.scheduled_at >= start) and (end is None or one.scheduled_at <= end)]


class _Model:
    """The builder's answer to an invitation, and the reviewer's verdict."""

    def __init__(self, answer="accepted") -> None:
        self.answer, self.asked, self.reviewed = answer, [], []
        self.review = {"ok": True, "changes": [], "block": [], "risk": "none", "why": "Fine."}

    def invoke(self, request, role="primary"):
        task = getattr(request, "task", "")
        if task.endswith("_review"):
            self.reviewed.append(request)
            return SimpleNamespace(text=json.dumps(self.review))
        if task == "society_calendar_answer":
            self.asked.append(request)
            return SimpleNamespace(text=json.dumps({"answer": self.answer, "why": "She is free then.",
                                                    "for_user": ""}))
        return SimpleNamespace(text=json.dumps({"keywords": ["garden"]}))


class _WithCalendar(unittest.TestCase):
    parts = None

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        here = Path(self._dir.name)
        self.transport, self.server = _server()
        self.mira = _service(here / "mira", self.transport, {"name": "Mira Holt"})
        self.ada = _service(here / "ada", self.transport, {"name": "Ada Quist"})
        _join(self.server, self.mira, "Mira Holt", permissions=_with_calendar(self.parts))
        _join(self.server, self.ada, "Ada Quist", permissions=_with_calendar())
        self.mira.refresh()
        app = self.mira.app
        app.models = self.model = _Model()
        app.scheduler = self.scheduler = _Scheduler()
        from personalized_agent.agent.approvals import Approvals
        app.approvals = self.approvals = Approvals(app)
        calendar = self.mira.site_calendar
        self.approvals.carriers.update({
            "society_invitation": lambda payload: calendar.respond(payload["event"], "accepted", by="user"),
            "society_calendar_action": calendar.carry})
        self.approvals.on_no.update({
            "society_invitation": lambda payload: calendar.respond(payload["event"], "declined", by="user")})
        self.calendar = calendar

    def tearDown(self) -> None:
        for one in (self.mira, self.ada):
            one.db.close()
        self._dir.cleanup()

    def _invite(self, title="Seed swap", hours=26) -> str:
        start = time.time() + hours * 3600
        return self.server.make_event_as(self.ada.account_id, title, start, start + 5400,
                                         [self.mira.account_id], location="Ivo's garden")

    def _held(self):
        return self.mira.db.query("SELECT * FROM approvals_held WHERE status = 'open'")

    def _kinds(self):
        from personalized_agent.agent.reports import did_since
        return [one["kind"] for one in did_since(self.mira.db, "")]


class AnInvitationIsNoMessageTests(_WithCalendar):
    def test_kept_from_the_inbox_never_answered_as_a_message(self) -> None:
        ident = self._invite()
        said = self.mira.collect()
        self.assertEqual(said["messages"], 0)
        self.assertEqual(self.mira.db.query("SELECT * FROM society_messages"), [],
                         "not a thread for the agent to write back in")
        [note] = self.mira.db.query("SELECT * FROM society_calendar_notes")
        self.assertEqual(note["event_id"], ident)
        self.calendar.read()
        [waiting] = self.calendar.shown()["invitations"]
        self.assertEqual((waiting["id"], waiting["organizer_name"]), (ident, "Ada Quist"))


class TheAgentJudgesTests(_WithCalendar):
    """"agent判断自己可以处理就处理，不能处理还是问用户" - the default."""

    def test_what_it_can_tell_it_answers_once_reviewed_and_it_goes_on_her_calendar(self) -> None:
        ident = self._invite()
        self.calendar.read()
        self.assertEqual(self.calendar.mode(), "judge", "the agent judges, by default")
        self.assertEqual(self.calendar.decide_waiting(), 1)
        self.assertEqual(self.server.answer_of(self.mira.account_id, ident), "accepted")
        system = self.model.asked[-1].system
        for words in ("YOU ARE THE PERSONAL AGENT OF Mira Holt", "ONLY WHEN YOU\nCAN TELL",
                      'answer "ask"'):
            self.assertIn(words, system)
        handed = json.loads(self.model.asked[-1].messages[0]["content"])
        self.assertEqual(handed["who_invites"]["name"], "Ada Quist")
        self.assertIn("your_users_calendar_then", handed)
        self.assertEqual(len(self.model.reviewed), 1, "reviewed before it was sent")
        self.assertIn("society_calendar_answered", self._kinds())
        # On her own calendar, at its time - once.
        [task] = self.scheduler.tasks.values()
        self.assertEqual(task.metadata["society_event"], ident)
        self.assertEqual(task.title, "Seed swap")
        self.calendar.read()
        self.assertEqual(len(self.scheduler.tasks), 1)
        # Each invitation looked at once.
        asked = len(self.model.asked)
        self.calendar.decide_waiting()
        self.assertEqual(len(self.model.asked), asked)

    def test_what_it_cannot_tell_is_asked_of_her_and_her_answer_is_sent(self) -> None:
        first, second = self._invite("Seed swap"), self._invite("Dinner with the club", hours=50)
        self.calendar.read()
        self.model.answer = "ask"
        self.calendar.decide_waiting()
        self.assertEqual(self.server.answer_of(self.mira.account_id, first), "needsAction", "nothing sent")
        held = {row["title"].split(": ", 1)[1].split(" - ")[0]: row for row in self._held()}
        self.assertEqual(set(held), {"Seed swap", "Dinner with the club"})
        self.assertTrue(all(row["kind"] == "society_invitation" for row in held.values()))
        items = {one["id"]: one for one in self.approvals.items()}
        one = items[held["Seed swap"]["id"]]
        self.assertEqual((one["yes"], one["no"], one["batch"]), ("Accept", "Decline", False))
        # Her yes: accepted, and on her calendar; her no: declined - sent, not "left as it was".
        self.approvals.decide("held:" + held["Seed swap"]["id"], True)
        self.approvals.decide("held:" + held["Dinner with the club"]["id"], False)
        self.assertEqual(self.server.answer_of(self.mira.account_id, first), "accepted")
        self.assertEqual(self.server.answer_of(self.mira.account_id, second), "declined")
        self.assertEqual([task.title for task in self.scheduler.tasks.values()], ["Seed swap"])
        from personalized_agent.agent.approvals import done_said
        self.assertIn("the one who invited you is told",
                      done_said({"kind": "society_invitation", "title": "Dinner"}, "no"))

    def test_held_back_by_its_review_it_is_asked_not_sent(self) -> None:
        ident = self._invite()
        self.calendar.read()
        self.model.review = {"ok": False, "changes": [], "block": [], "risk": "high",
                             "why": "Unsure she wants to go."}
        self.calendar.decide_waiting()
        self.assertEqual(self.server.answer_of(self.mira.account_id, ident), "needsAction")
        [held] = self._held()
        self.assertIn("held back by its review", held["why"])


class HerSettingTests(_WithCalendar):
    def test_ask_holds_every_one_and_read_answers_nothing(self) -> None:
        self.calendar.set_mode("ask")
        ident = self._invite()
        self.calendar.read()
        self.calendar.decide_waiting()
        self.assertEqual(self.model.asked, [], "not judged: asked of her")
        self.assertEqual([row["kind"] for row in self._held()], ["society_invitation"])
        self.calendar.set_mode("read")
        other = self._invite("Harbour walk", hours=30)
        self.calendar.read()
        self.calendar.decide_waiting()
        self.assertEqual(self.server.answer_of(self.mira.account_id, other), "needsAction")
        self.assertEqual(len(self._held()), 1, "read only: nothing asked either")
        self.assertEqual(self.server.answer_of(self.mira.account_id, ident), "needsAction")
        with self.assertRaises(ValueError):
            self.calendar.set_mode("draft")

    def test_what_the_agent_does_on_the_calendar_goes_by_her_setting(self) -> None:
        when = datetime.fromtimestamp(time.time() + 3 * 86400, timezone.utc).isoformat()
        asked = {"title": "Seed sorting", "start": when, "attendees": [self.ada.account_id]}
        # Judged, and its review lets it through: made, Ada invited.
        said = self.calendar.agent_act("create", dict(asked), why="Mira asked for it", said_by="agent")
        self.assertTrue(said.get("done"), said)
        [made] = self.server.cal_made
        self.assertEqual(self.server.answer_of(self.ada.account_id, made), "needsAction")
        self.assertIn("society_calendar_made", self._kinds())
        # Not let through: held for her yes - then made.
        self.model.review = {"ok": False, "changes": [], "block": [], "risk": "high", "why": "Unsure."}
        said = self.calendar.agent_act("create", dict(asked, title="Bulb planting"), why="x", said_by="agent")
        self.assertIn("held", said)
        [held] = self._held()
        self.assertEqual(held["kind"], "society_calendar_action")
        self.approvals.decide("held:" + held["id"], True)
        self.assertEqual(len(self.server.cal_made), 2)
        # Ask: held without a review; read: not at all.
        self.calendar.set_mode("ask")
        self.model.review = {"ok": True, "changes": [], "block": [], "risk": "none", "why": "Fine."}
        reviews = len(self.model.reviewed)
        self.assertIn("held", self.calendar.agent_act("cancel", {"id": made}, why="x"))
        self.assertEqual(len(self.model.reviewed), reviews)
        self.calendar.set_mode("read")
        self.assertIn("read only", self.calendar.agent_act("cancel", {"id": made}, why="x")["error"])
        self.assertIn("not 'delete'", self.calendar.agent_act("delete", {"id": made})["error"])


class HerOwnCalendarFollowsTests(_WithCalendar):
    def test_moved_there_moved_here_cancelled_there_cancelled_here(self) -> None:
        ident = self._invite()
        self.calendar.read()
        self.calendar.respond(ident, "accepted")
        [task] = self.scheduler.tasks.values()
        later = time.time() + 40 * 3600
        self.server.move_event_as(self.ada.account_id, ident, later, later + 3600)
        self.calendar.read()
        self.assertEqual(int(task.scheduled_at.timestamp()), int(later))
        self.assertIn("calendar_moved", self._kinds())
        self.server.cancel_event_as(self.ada.account_id, ident)
        self.calendar.read()
        self.assertEqual(task.status, "cancelled")
        self.assertIn("calendar_cancelled", self._kinds())
        # One she declines goes off it as well.
        other = self._invite("Harbour walk", hours=30)
        self.calendar.read()
        self.calendar.respond(other, "accepted")
        self.calendar.respond(other, "declined")
        self.assertEqual([one.status for one in self.scheduler.tasks.values()].count("cancelled"), 2)


class AsTheKeyPermitsTests(_WithCalendar):
    parts = {"read": False, "write": False, "invite": False, "respond": False}

    def test_nothing_asked_of_the_site_it_may_not_do(self) -> None:
        self._invite()
        self.calendar.read()
        self.assertEqual(self.calendar.shown()["invitations"], [])
        self.assertFalse(any(path.startswith("/v2/calendar") for path, _status in self.server.asked))
        with self.assertRaises(PermissionError):
            self.calendar.respond("ev_none", "accepted")
        self.assertIn("calendar.write", self.calendar.agent_act(
            "create", {"title": "x", "start": "2026-12-01T10:00:00Z"})["error"])
        self.assertNotIn("answer calendar invitations", self.mira.permitted())


class ThePanelTests(unittest.TestCase):
    def test_wired(self) -> None:
        from support import ROOT
        web = ROOT / "personalized_agent" / "api" / "web"
        page = (web / "index.html").read_text("utf-8")
        script = (web / "app.js").read_text("utf-8")
        routes = (ROOT / "personalized_agent" / "api" / "routes.py").read_text("utf-8")
        self.assertIn('data-society-view="calendar"', page)
        self.assertIn('id="society-view-calendar"', page)
        for words in ("/api/society/calendar/mode", "/api/society/calendar/respond", "/api/society/calendar/do"):
            self.assertIn(words, script)
            self.assertIn(words, routes)
        table = json.loads((web / "lang" / "zh.json").read_text("utf-8"))
        from personalized_agent.agent import approvals
        for words in (approvals.HELD_ASKS["society_invitation"], approvals.HELD_ASKS["society_calendar_action"],
                      approvals.DONE_DECLINED, "Accept", "Decline"):
            self.assertIn(words, table, words)


if __name__ == "__main__":
    unittest.main()

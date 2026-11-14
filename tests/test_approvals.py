"""FOR THE USER'S APPROVAL, TOGETHER, AT THEIR TIMES (the user, 2026-11-12: "所有给user approve的信息，
要作为一个summary, 比如早中晚给user summarize和一起approve (open approve一次显示所有需要approve的)。
什么时候给user summarize看你要和user协调(default现在是早中晚3次)"). agent/approvals.py."""

from __future__ import annotations

import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personalized_agent.agent import approvals as ap  # noqa: E402
from personalized_agent.data_manage.storage.database import Database  # noqa: E402


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value, **_kwargs):
        self[key] = value


class _Notes:
    def __init__(self):
        self.said = []

    def notify(self, title, message, **kwargs):
        self.said.append((title, message, kwargs))
        return {"id": f"n{len(self.said)}"}


class _Todos:
    def __init__(self):
        self.rows = {"t1": {"id": "t1", "text": "Send the survey", "suggested_at": "2026-11-12T08:00",
                            "suggested_kind": "done", "suggested_why": "Sent at 9."},
                     "t2": {"id": "t2", "text": "Old idea", "suggested_at": "2026-11-12T08:01",
                            "suggested_kind": "remove", "suggested_why": "Not wanted."},
                     "t3": {"id": "t3", "text": "Nothing suggested"}}
        self.done, self.deleted, self.declined = [], [], []

    def open(self):
        return list(self.rows.values())

    def get(self, ident):
        return self.rows.get(ident)

    def tick(self, ident, done=True, by="user"):
        self.done.append(ident)
        return self.rows.get(ident)

    def delete(self, ident):
        self.deleted.append(ident)
        return self.rows.pop(ident, None) is not None

    def decline(self, ident, why=""):
        self.declined.append(ident)
        self.rows[ident].pop("suggested_at", None)
        return self.rows[ident]


class ApprovalsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._dir.cleanup)
        self.gone = []
        self.app = SimpleNamespace(db=Database(Path(self._dir.name) / "agent.db"),
                                   config=_Config(), notifications=_Notes(), todos=_Todos(),
                                   todo_gone=lambda one, how, action: self.gone.append(how))
        self.addCleanup(self.app.db.close)
        # TOLD THROUGH THE MONITOR (2026-11-13: "所有的通知必须在monitor里"): by its "Your report".
        self.relayed = []
        self.app.monitor = SimpleNamespace(relay=lambda builtin, title, message, **said: (
            self.relayed.append(builtin), self.app.notifications.said.append((title, message, said)))[-1]
            or {"id": "n"})
        self.approvals = ap.Approvals(self.app)
        self.carried = []
        self.approvals.carriers["todo"] = self.carried.append

    def test_everything_waiting_gathered_and_answered_together(self) -> None:
        self.approvals.hold("society_reply", "todo", "Ada: a to-do, \"Pay the deposit\"",
                            {"action": {"text": "Pay the deposit"}}, why="It spends money.")
        items = self.approvals.items()
        self.assertEqual(sorted(one["source"] for one in items), ["held", "todo", "todo"])
        held = next(one for one in items if one["source"] == "held")
        self.assertEqual((held["why"], held["batch"]), ("It spends money.", True))
        said = self.approvals.approve_all()
        self.assertTrue(all(one["done"] for one in said))
        self.assertEqual(self.carried, [{"action": {"text": "Pay the deposit"}}])
        self.assertEqual((self.app.todos.done, sorted(self.app.todos.deleted)), (["t1"], ["t1", "t2"]))
        self.assertEqual(sorted(self.gone), ["done", "removed"])
        self.assertEqual(self.approvals.items(), [])

    def test_a_no_is_a_no(self) -> None:
        ident = self.approvals.hold("trigger", "todo", "x", {"action": {}})
        self.assertTrue(self.approvals.decide(f"held:{ident}", False)["done"])
        self.assertEqual(self.carried, [])
        self.assertTrue(self.approvals.decide("todo:t1", False)["done"])
        self.assertEqual(self.app.todos.declined, ["t1"])
        self.assertFalse(self.approvals.decide("held:no-such", True)["done"])

    def test_the_same_thing_held_again_is_one_thing(self) -> None:
        """2026-11-13: "这些task item 不是duplicate 了吗" - a project round held a reminder, wrote its why
        again and was held again."""
        title = "Garden plan: remind of “Order the seeds” at 2026-11-16 09:00"
        first = self.approvals.hold("project_round", "project_set_reminder", title,
                                    {"args": {"why": "first"}}, why="Seeds sell out.")
        again = self.approvals.hold("project_round", "project_set_reminder", title,
                                    {"args": {"why": "again"}}, why="Seeds sell out by spring.")
        self.assertEqual(again, first, "the one already waiting, not a second")
        held = [one for one in self.approvals.items() if one["source"] == "held"]
        self.assertEqual([one["why"] for one in held], ["Seeds sell out by spring."], "its newer why")
        other = self.approvals.hold("project_round", "project_set_reminder",
                                    title.replace("09:00", "10:00"), {})
        self.assertNotEqual(other, first, "another time is another thing")
        # Twins already kept (held before this): shown once, settled together.
        self.app.db.execute("INSERT INTO approvals_held (id, at, source, kind, title, detail, why, due, "
                            "payload) VALUES ('ap_twin', '9999', 'project_round', 'project_set_reminder', "
                            "?, '', '', '', '{}')", (title,))
        held = [one for one in self.approvals.items() if one["source"] == "held"]
        self.assertEqual(sorted(one["id"] for one in held), sorted([first, other]))
        self.assertTrue(self.approvals.decide(f"held:{first}", False)["done"])
        row = self.app.db.query_one("SELECT status FROM approvals_held WHERE id = 'ap_twin'")
        self.assertEqual(row["status"], "declined")

    def test_a_carrier_that_fails_is_said_and_kept_failed(self) -> None:
        def fail(_payload):
            raise ValueError("it may be done already")
        self.approvals.carriers["todo"] = fail
        ident = self.approvals.hold("trigger", "todo", "x", {})
        said = self.approvals.decide(f"held:{ident}", True)
        self.assertEqual((said["done"], said["said"]), (False, "it may be done already"))
        row = self.app.db.query_one("SELECT status FROM approvals_held WHERE id = ?", (ident,))
        self.assertEqual(row["status"], "failed")

    def test_morning_noon_and_evening_by_default_and_told_once_each(self) -> None:
        self.assertEqual(self.approvals.times(), ["08:00", "12:00", "21:00"])
        day = datetime(2026, 11, 12, 7, 0).astimezone()
        self.assertFalse(self.approvals.look(day), "the first look only marks where it is")
        self.assertTrue(self.approvals.look(day.replace(hour=11)), "08:00 missed: told when back")
        self.assertFalse(self.approvals.look(day.replace(hour=11, minute=30)))
        self.assertTrue(self.approvals.look(day.replace(hour=12, minute=1)))
        title, message, kwargs = self.app.notifications.said[-1]
        self.assertEqual(self.relayed[-1], "summary", "through the Monitor's own")
        self.assertEqual(title, "For your approval: 2")
        self.assertIn("- Send the survey", message)
        self.assertEqual((kwargs["kind"], kwargs["metadata"]), ("approval", {"approvals": True}))
        self.assertFalse(self.approvals.look(day.replace(hour=12, minute=30)), "told once")

    def test_a_time_added_after_it_passed_counts_from_tomorrow(self) -> None:
        """2026-11-12: "为什么21.23会有一个summary, 不是21.00的定时工作吗" - 21:00 added at 21:18."""
        day = datetime(2026, 11, 12, 20, 0).astimezone()
        self.assertFalse(self.approvals.look(day))
        self.app.config.set("approval_summary_times", "08:00, 12:00, 21:00")
        self.approvals.times_changed(day.replace(hour=21, minute=18))
        self.assertFalse(self.approvals.look(day.replace(hour=21, minute=19)), "not told as missed")
        self.assertTrue(self.approvals.look(day.replace(day=13, hour=8, minute=1)), "from the next one")
        told = []
        self.app.reports = SimpleNamespace(tell=lambda for_time=None: told.append(for_time) or {"id": "i"})
        self.assertTrue(self.approvals.look(day.replace(day=13, hour=12, minute=2)))
        self.assertEqual(told[0].strftime("%H:%M"), "12:00", "the report named by its time")

    def test_nothing_waiting_nothing_told(self) -> None:
        self.app.todos.rows.clear()
        day = datetime(2026, 11, 12, 7, 0).astimezone()
        self.approvals.look(day)
        self.assertFalse(self.approvals.look(day.replace(hour=8, minute=5)))
        self.assertEqual(self.app.notifications.said, [])

    def test_what_cannot_wait_is_due_before_the_next_time(self) -> None:
        now = datetime(2026, 11, 12, 9, 0).astimezone()
        self.assertTrue(self.approvals.urgent("2026-11-12 11:00", now))
        self.assertFalse(self.approvals.urgent("2026-11-12 13:00", now))
        self.assertFalse(self.approvals.urgent("", now))

    def test_the_times_settled_in_settings_or_the_chat(self) -> None:
        from personalized_agent.agent.core.tools import ToolRegistry
        registry = ToolRegistry()
        ap.add_tools(registry, self.approvals, self.app.config)
        self.assertEqual(registry.call("approval_summary_times", {})["times"],
                         ["08:00", "12:00", "21:00"])
        said = registry.call("approval_summary_times", {"times": "9:00 and 17.30"})
        self.assertEqual(said["times"], ["09:00", "17:30"])
        self.assertEqual(self.app.config[ap.TIMES], "09:00, 17:30")
        upcoming = self.approvals.next_time(datetime(2026, 11, 12, 18, 0).astimezone())
        self.assertEqual((upcoming.hour, upcoming.minute, upcoming.day), (9, 0, 13))
        waiting = registry.call("approvals_waiting", {})["waiting_for_approval"]
        self.assertEqual(len(waiting), 2)


class AWorksAskTests(unittest.TestCase):
    """2026-11-12: "让主 agent 给这类请求带上所属工作的截止时间， 带上ddl"."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._dir.cleanup)
        self.notes = _Notes()
        self.app = SimpleNamespace(
            db=Database(Path(self._dir.name) / "agent.db"), config=_Config(), notifications=self.notes,
            work=SimpleNamespace(run_states=lambda: [{"run": "r1", "state": "waiting_for_you"}]),
            _asks_of=lambda run: [{"id": "q1", "kind": "approval", "title": "Run outside the sandbox",
                                   "what": "git push", "why": "to publish the page"}],
            run_deadline=lambda run: "2026-11-13 09:00")
        self.addCleanup(self.app.db.close)

    def test_the_ask_carries_its_works_deadline(self) -> None:
        [item] = ap.Approvals(self.app).items()
        self.assertEqual((item["source"], item["due"], item["title"]),
                         ("work", "2026-11-13 09:00", "Run outside the sandbox: git push"))

    def test_due_before_the_summary_it_is_told_at_once_else_it_waits(self) -> None:
        from personalized_agent.app import AgentApplication
        told = {}
        for due, urgent in (("2026-11-13 09:00", True), ("", False)):
            fake = SimpleNamespace(
                work=SimpleNamespace(run_states=lambda: [{"run": "r1", "state": "waiting_for_you"}]),
                _asks_of=self.app._asks_of, run_deadline=lambda run, due=due: due,
                approvals=SimpleNamespace(urgent=lambda when, urgent=urgent: urgent),
                notifications=_Notes(), config=_Config())
            fake.relayed = []
            # THROUGH THE MONITOR'S "DUE SOON, NOT DONE YET" (2026-11-14: "这个快要到期的提示也要的").
            fake.monitor = SimpleNamespace(relay=lambda builtin, title, message, fake=fake, **said: (
                fake.relayed.append(builtin), fake.notifications.said.append((title, message, said))))
            AgentApplication.tell_work_waiting(fake)
            told[due] = fake.notifications.said
            if urgent:
                self.assertEqual(fake.relayed, ["deadline"])
        [(_title, message, kwargs)] = told["2026-11-13 09:00"]
        self.assertIn("Due: 2026-11-13 09:00", message)
        self.assertEqual(kwargs["kind"], "approval")
        self.assertEqual(told[""], [], "no deadline: it waits for the summary")


class _Agenda:
    def __init__(self, things):
        self.rows = {one["id"]: one for one in things}

    def things(self):
        return list(self.rows.values())


class ThePlansThingsTests(unittest.TestCase):
    """2026-11-12: what the agent made, shown; only what truly waits; Already done; a reply in words;
    what is done already found by the agent itself - none of it a lesson of the user's."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._dir.cleanup)
        things = [{"id": "a_deploy", "title": "Deploy the commits", "detail": "",
                   "triage": {"who": "delegable", "deadline": "2026-11-20 09:00"}},
                  {"id": "a_own", "title": "Sort the survey files", "detail": "",
                   "triage": {"who": "delegable"}},
                  {"id": "a_ok", "title": "Send the report", "detail": "",
                   "triage": {"who": "approve"}}]
        runs = {"r_deploy": {"plan": {"handed": [{"kind": "text", "title": "Release notes",
                                                  "text": "Score bars, author links."}]}},
                "r_own": {"plan": {"handed": [{"kind": "file", "title": "Sorted", "path": "x"}]}},
                "r_ok": {"plan": {"handed": [{"kind": "email", "title": "Report", "to": ["a@b.c"],
                                              "subject": "Report", "body": "Here it is."}]}}}
        rows = [{"id": "a_deploy", "title": "Deploy the commits", "state": "ready", "run": "r_deploy"},
                {"id": "a_own", "title": "Sort the survey files", "state": "ready", "run": "r_own"},
                {"id": "a_ok", "title": "Send the report", "state": "ready", "run": "r_ok"}]
        self.feedback = []
        self.replies = []
        self.app = SimpleNamespace(
            db=Database(Path(self._dir.name) / "agent.db"), config=_Config(), notifications=_Notes(),
            agenda=_Agenda(things), agenda_approvals=lambda: {"approval": {"rows": rows}},
            work=SimpleNamespace(run=lambda run_id: runs.get(run_id)),
            _agenda_own_plan=lambda run_id: "1. `git push`" if run_id == "r_deploy" else "",
            agenda_work_complete=lambda run_id: run_id == "r_own",
            agenda_feedback=SimpleNamespace(apply=lambda ident, action, by="": self.feedback.append(
                (ident, action, by)) or {"id": ident}),
            agenda_reply=lambda ident, text: self.replies.append((ident, text)) or {"id": ident},
            agenda_executor=SimpleNamespace(started_for=lambda ident: "draft"),
            lessons=SimpleNamespace(the_user_did=lambda action, said: self.replies.append((action, said))))
        self.addCleanup(self.app.db.close)
        self.approvals = ap.Approvals(self.app)

    def test_only_what_truly_waits_with_what_the_agent_made(self) -> None:
        items = {one["id"]: one for one in self.approvals.items()}
        self.assertEqual(sorted(items), ["a_deploy", "a_ok"], "its own, done on its own: not asked")
        deploy = items["a_deploy"]
        self.assertIn("What the agent made:\nTEXT - Release notes\nScore bars", deploy["detail"])
        self.assertIn("What it will do once you approve:\n1. `git push`", deploy["detail"])
        self.assertTrue(deploy["already"] and deploy["replies"])
        self.assertIn("EMAIL - Report", items["a_ok"]["detail"])
        # EACH DELIVERABLE TO OPEN AND CHECK (2026-11-12): its place, how it is used, what to check.
        [made] = items["a_deploy"]["deliverables"]
        self.assertEqual((made["n"], made["kind"], made["title"]), (0, "text", "Release notes"))
        self.assertEqual(items["a_deploy"]["run"], "r_deploy")

    def test_what_only_the_user_can_send_is_said_apart(self) -> None:
        """2026-11-12: the agent has no way to send WeChat, email or a form: said apart; carried out, the
        answer is "I sent it"."""
        from personalized_agent.agent.approvals import Approvals
        material = {"plan": "1. Send the three lines on WeChat\n2. Add the call", "outgoing": [],
                    "yours": ['the message "Three times" to Tess in WeChat']}
        asks = Approvals._agenda_asks(material, False)
        self.assertEqual(asks["steps"], ["Send the three lines on WeChat", "Add the call"])
        self.assertEqual(asks["yours"], ['the message "Three times" to Tess in WeChat'])
        carried = Approvals._agenda_asks(material, False, carried=True)
        self.assertIn("then say I sent it", carried["lead"])
        self.assertEqual(carried["steps"], ['the message "Three times" to Tess in WeChat'])

    def test_nothing_for_the_agent_once_approved_is_the_users_to_do_not_to_approve(self) -> None:
        """2026-11-13: "如果approve之后agent什么也做不了，就不要approve作为快捷按钮, 而是应该变成i have done it。
        agent也不是waiting for my approval, 而是waiting for my action"."""
        from personalized_agent.agent.approvals import Approvals
        yours = ['the email "Re: dates" to tess@example.org']
        for plan in ("NOTHING", "**NOTHING**", "1. Nothing more. I have no tool to send email."):
            asks = Approvals._agenda_asks({"plan": plan, "outgoing": yours, "yours": yours}, False)
            self.assertIn("these are yours to do", asks["lead"], plan)
            self.assertEqual(asks["steps"], yours)
        self.assertFalse(Approvals.nothing_after({"plan": "1. Put the call on the calendar"}))
        self.assertFalse(Approvals.nothing_after({"plan": "1. Nothing to install\n2. Run `make`"}))

    def test_what_approving_does_is_said(self) -> None:
        """2026-11-12: "你让我approve的是什么，你得写清楚 ... 后一步是需要我approve的内容，你得标注"."""
        items = {one["id"]: one for one in self.approvals.items()}
        self.assertEqual(items["a_deploy"]["asks"], {"lead": "Once you approve, your agent will",
                                                     "steps": ["`git push`"]})
        self.assertEqual(items["a_ok"]["asks"]["steps"], ['the email "Report" to a@b.c'])
        self.assertIn("yours to send", items["a_ok"]["asks"]["lead"])
        ident = self.approvals.hold("trigger", "add_a_calendar_reminder", "Walk, Monday 14:00", {})
        held = next(one for one in self.approvals.items() if one["id"] == ident)
        self.assertEqual(held["asks"], {"lead": "Approving puts this reminder on your calendar",
                                        "steps": ["Walk, Monday 14:00"]})

    def test_approved_its_plan_carried_out_else_as_it_was_never_an_unrun_plan(self) -> None:
        said = []
        self.app.agenda_approved = lambda ident, how: said.append((ident, how)) or (
            None if how == "go" else {"id": ident})
        self.app.drafts = SimpleNamespace(get=lambda draft_id: {"run_id": "r_ok"})
        self.assertTrue(self.approvals.decide("agenda:a_ok", True)["done"])
        self.assertEqual(said, [("a_ok", "go"), ("a_ok", "as_it_was")], "no plan: approved as it was")
        self.app.drafts = SimpleNamespace(get=lambda draft_id: {"run_id": "r_deploy"})
        said.clear()
        result = self.approvals.decide("agenda:a_deploy", True)
        self.assertFalse(result["done"])
        self.assertIn("could not be carried out", result["said"])
        self.assertEqual(said, [("a_deploy", "go")], "a plan not carried out is never taken for done")

    def test_the_whole_task_done_not_a_draft_waiting(self) -> None:
        """2026-11-12: "做完要的是整个task做完 ... 只是把草稿写好，还没发要我批准，不能算做完"."""
        from personalized_agent.app import AgentApplication
        runs = {"r_msg": {"plan": {"handed": [{"kind": "message", "title": "To Ada"}]}},
                "r_file": {"plan": {"handed": [{"kind": "file", "title": "Sorted", "path": "x"}]}},
                "r_ask": {"plan": {"handed": []}}}
        fake = SimpleNamespace(
            UNSENT_KINDS=AgentApplication.UNSENT_KINDS,
            _agenda_own_plan=lambda run_id: "1. send it" if run_id == "r_plan" else "",
            work=SimpleNamespace(run=lambda run_id: runs.get(run_id)),
            society=SimpleNamespace(work_asks=lambda run_id: [{"sent_at": ""}] if run_id == "r_ask" else []))
        complete = AgentApplication.agenda_work_complete
        self.assertFalse(complete(fake, "r_msg"), "a message drafted, not sent")
        self.assertFalse(complete(fake, "r_plan"), "what it would still do once approved")
        self.assertFalse(complete(fake, "r_ask"), "its message to another agent still a draft")
        self.assertFalse(complete(fake, ""))
        self.assertTrue(complete(fake, "r_file"), "the whole task done")

    def test_already_done_and_a_reply_in_words(self) -> None:
        self.assertTrue(self.approvals.decide("agenda:a_deploy", False, already=True)["done"])
        self.assertEqual(self.feedback, [("a_deploy", "done", "user")])
        self.assertTrue(self.approvals.decide("agenda:a_ok", False, "Send it to Ada too")["done"])
        self.assertIn(("a_ok", "Send it to Ada too"), self.replies)
        self.assertTrue(any(one[0] == "approval_reply" for one in self.replies), "a lesson of theirs")

    def test_what_is_done_already_found_by_the_agent_itself(self) -> None:
        self.app.db.execute("INSERT INTO kv (key, value) VALUES (?, ?)",
                            ("agenda.words.a_deploy", '{"words": ["deploy"]}'))
        from personalized_agent.heartbeat.core import related
        found = {"activity_about_it": [{"id": "state_9", "at": "2026-11-11", "text": "pushed"}],
                 "said_in_the_chat": [], "people_who_may_be_meant": []}
        answers = iter(['{"done": [{"id": "a_deploy", "why": "You deployed it on the 11th.", '
                        '"shown_by": ["state_9"]}, {"id": "a_ok", "why": "made up", '
                        '"shown_by": ["state_404"]}]}'])
        self.app.models = SimpleNamespace(invoke=lambda request, role="": SimpleNamespace(
            text=next(answers)))
        original = related.context
        related.context = lambda *a, **k: found
        try:
            let_go = self.approvals.check_done(force=True)
        finally:
            related.context = original
        self.assertEqual([(one["title"], one["why"]) for one in let_go],
                         [("Deploy the commits", "You deployed it on the 11th.")])
        self.assertEqual(self.feedback, [("a_deploy", "done", "agent_found_done")],
                         "what it rests on must be what it was shown")
        self.assertEqual(self.approvals.let_go()[0]["title"], "Deploy the commits")
        self.assertEqual(self.approvals.check_done(), [], "not again so soon")

    def test_the_users_own_things_found_done_in_the_same_look_with_what_shows_it(self) -> None:
        """2026-11-13: "这个可以agent 自己detect 完成，为什么要我label" - in the same look as what waits for
        approval; done only on a line shown, and that line said in the report."""
        import json as _json
        from personalized_agent.agent.reports import did_since
        from personalized_agent.heartbeat.core import related
        self.app.agenda.rows["a_mine"] = {"id": "a_mine", "title": "Test the build on a Mac", "detail": "",
                                          "triage": {"who": "human", "agent_part": "List what changed"}}
        self.app.agenda.rows["a_later"] = {"id": "a_later", "title": "Read the survey", "detail": "",
                                           "triage": {"who": "human"}}
        found = {"activity_about_it": [{"id": "state_7", "title": "Terminal",
                                        "detail": "5979 tests in 5.9 min on the Mac: OK"}],
                 "said_in_the_chat": []}
        asked = []

        def invoke(request, role=""):
            asked.append(_json.loads(request.messages[0]["content"]))
            return SimpleNamespace(text=_json.dumps({"done": [
                {"id": "a_mine", "why": "You ran the build's tests on the Mac.", "shown_by": ["state_7"]},
                {"id": "a_later", "why": "made up", "shown_by": ["state_404"]}]}))
        self.app.models = SimpleNamespace(invoke=invoke)
        with mock.patch.object(related, "context", return_value=found):
            self.approvals.check_done(force=True)
        self.assertEqual(len(asked), 1, "one look for all of them")
        kinds = {one["id"]: one["kind"] for one in asked[0]["things"]}
        self.assertEqual((kinds["a_mine"], kinds["a_deploy"]), ("theirs", "approval"))
        self.assertIn(("a_mine", "done", "agent_found_done"), self.feedback)
        self.assertNotIn(("a_later", "done", "agent_found_done"), self.feedback, "no line shown: left")
        [said] = [one for one in did_since(self.app.db, "") if one["kind"] == "found_done"]
        self.assertEqual(said["title"], "Found done by your agent: Test the build on a Mac")
        self.assertIn("5979 tests in 5.9 min on the Mac", said["detail"], "what it rests on, said")

    def test_what_came_of_finished_work_told_as_done_by_the_agent_when_nothing_is_left(self) -> None:
        """2026-11-13: "这些已经做好，如果不需要我做，而是只是通知下我，就说done by agents, 其它的是need your look" -
        such things had been shown nowhere."""
        from personalized_agent.agent.reports import did_since
        from personalized_agent.app import AgentApplication
        things = [{"id": "w_told", "title": "Check the bee survey upload", "from": "work", "ref": "d1",
                   "triage": {"who": "delegable"}},
                  {"id": "w_decide", "title": "Tell the club your three decisions", "from": "work", "ref": "d1",
                   "triage": {"who": "approve"}},
                  {"id": "w_send", "title": "Look at the reply to Tess", "from": "work", "ref": "d2",
                   "triage": {"who": "approve"}},
                  {"id": "w_going", "title": "Look at the orchard draft", "from": "work", "ref": "d3",
                   "triage": {"who": "approve"}},
                  {"id": "p_mine", "title": "Read it", "triage": {"who": "human"}}]
        drafts = {"d1": {"run_id": "r1"}, "d2": {"run_id": "r2"}, "d3": {"run_id": "r3"}}
        runs = {"r1": {"status": "done"}, "r2": {"status": "done"}, "r3": {"status": "running"}}
        fake = SimpleNamespace(
            db=self.app.db, config=None, agenda=SimpleNamespace(things=lambda: things),
            drafts=SimpleNamespace(get=drafts.get), work=SimpleNamespace(run=runs.get, run_going=lambda r: r == "r3"),
            agenda_work_complete=lambda run_id: run_id == "r1",       # r2 still has a message to send
            agenda_feedback=SimpleNamespace(apply=lambda ident, action, by="": self.feedback.append(
                (ident, action, by)) or {"id": ident}),
            agenda_executor=SimpleNamespace(started_for=lambda ident: ""))
        self.assertEqual(AgentApplication.work_born_done(fake), ["w_told"],
                         "for the user's look - a decision - is never closed as done, however finished")
        self.assertEqual(self.feedback, [("w_told", "done", "agent_did_it")])
        [said] = [one for one in did_since(self.app.db, "") if one["kind"] == "work_done"]
        self.assertEqual((said["title"], said["ref"]), ("Done by your agent: Check the bee survey upload", "w_told"))
        # The rest: the work it came of is what the approvals panel shows it by - in Needs your look.
        self.assertEqual(AgentApplication._plan_draft(fake, things[2]), "d2")
        self.assertEqual(AgentApplication._plan_draft(fake, things[4]), "")

    def test_taking_off_what_is_done_is_told_not_asked(self) -> None:
        """2026-11-12: "如果只是让我确认一下做的对不对，不应该是need approve, 而是单纯的知会我一下"."""
        from personalized_agent.heartbeat.agenda.plan import Agenda
        things = [{"id": "ag1", "title": "Invite Corwin"}, {"id": "ag2", "title": "Old survey form"}]
        plan = {"clean_up": [{"item": "ag1", "why": "the call happened"},
                             {"item": "ag2", "why": "the survey is over"}]}
        review = {"ok": False, "changes": [{"what": "keep the Old survey form - it is still due",
                                            "because": []}, {"what": "move the writing block"}]}
        self.assertEqual(Agenda._named_by_review(plan, review, things), {"ag2"},
                         "only what the review named waits")
        self.assertEqual(Agenda._named_by_review(plan, {"ok": True}, things), set())
        self.app.agenda_approvals = lambda: {"approval": {"rows": []}}
        carried = []
        self.approvals.carriers["agenda_clean_up"] = carried.append
        self.approvals.hold("agenda", "agenda_clean_up", "Take off: Invite Corwin",
                            {"item": "ag1", "why": "the call happened"})
        self.approvals.hold("agenda", "agenda_clean_up", "Take off: Old survey form",
                            {"item": "ag2", "why": "over", "named": True})
        let_go = self.approvals.check_done(force=True)
        self.assertEqual([one["title"] for one in let_go], ["Take off: Invite Corwin"])
        self.assertEqual(carried, [{"item": "ag1", "why": "the call happened"}])
        self.assertEqual([one["title"] for one in self.approvals.items() if one["source"] == "held"],
                         ["Take off: Old survey form"], "what its review named still waits")

    def test_not_taken_off_is_never_said_done_it_waits_for_the_user(self) -> None:
        """2026-11-13: "Let go of as done already" told twice of a thing still on the plate - the cleaner takes
        off only what was sorted gone, and said nothing."""
        self.app.agenda_approvals = lambda: {"approval": {"rows": []}}
        carried = []

        def carry(payload):
            carried.append(payload)
            if not payload.get("by_user"):
                raise ValueError("it was not taken off your plate")
        self.approvals.carriers["agenda_clean_up"] = carry
        ident = self.approvals.hold("agenda", "agenda_clean_up", "Take off: Look at the bee survey",
                                    {"item": "ag7", "why": "the meeting has passed"})
        self.assertEqual(self.approvals.check_done(force=True), [], "not let go of as done")
        [held] = [one for one in self.approvals.items() if one["source"] == "held"]
        self.assertEqual(held["id"], ident, "it waits for the user's yes")
        self.assertTrue(self.approvals.decide(held["key"], True)["done"])
        self.assertTrue(carried[-1]["by_user"], "the user's yes takes it off whatever it was sorted as")
        from personalized_agent.app import AgentApplication
        import inspect
        self.assertIn("raise ValueError", inspect.getsource(AgentApplication._clean_up_carry))

    def test_not_known_to_be_over_is_a_check_set_with_its_id_and_done_at_its_time(self) -> None:
        """2026-11-12: "checks tonight这种应该视为agent task 没完成，不需要批准，告知就行"; then "agent应该honestly
        汇报他的事情，如果他要要查，就必须要查。或者这样，所有他做的事情必须以event的形式说明，比如加了什么notification(加上
        id之类), 而不能只是描述"."""
        import json as _json
        from personalized_agent.agent.checks import Checks
        from personalized_agent.agent.reports import Report, did_since
        from personalized_agent.heartbeat.agenda import plan as planning
        self.assertIn('"to_check"', planning.BUILDER_SYSTEM)
        self.assertIn("Its time having passed is not enough", planning.BUILDER_SYSTEM)
        self.assertIn("PROMISE NOTHING THE CODE DOES NOT DO", planning.BUILDER_SYSTEM)
        closed = []
        answers = []

        class Models:
            def invoke(self, request, role=""):
                return SimpleNamespace(text=answers.pop(0), content=answers and "" or "",
                                       ok=True, error=None)
        self.app.agenda = SimpleNamespace(things=lambda: [{"id": "ag9", "title": "Lanternfish chat at 12:00",
                                                           "detail": ""}])
        self.app.agenda_feedback = SimpleNamespace(apply=lambda ident, how, by="": closed.append(
            (ident, how, by)) or {"id": ident})
        self.app.checks = Checks(self.app)
        agenda = SimpleNamespace(approvals=SimpleNamespace(app=self.app, db=self.approvals.db))
        things = [{"id": "ag9", "title": "Lanternfish chat at 12:00"}]
        plan = {"to_check": [{"item": "ag9", "why": "look for the call in the calendar and chats",
                              "when": "21:00"}, {"item": "not_on_the_plate"}]}
        planning.Agenda._tell_to_check(agenda, plan, things)
        planning.Agenda._tell_to_check(agenda, plan, things)
        waiting = self.app.checks.waiting()
        self.assertEqual(len(waiting), 1, "one check, set once")
        ident = waiting[0]["id"]
        self.assertTrue(ident.startswith("ck_"))
        told = [one for one in did_since(self.approvals.db, "") if one["kind"] == "check_set"]
        self.assertEqual([one["ref"] for one in told], [ident], "told as set, with its id")
        self.assertIn(ident, told[0]["title"])
        record = Report(SimpleNamespace(db=self.approvals.db)).done("")[0]["record"]
        self.assertEqual(record, {"what": "check_set", "id": ident}, "the report shows what it is and its id")
        self.assertFalse([one for one in self.approvals.items() if one["source"] == "held"], "never asked")
        # AT ITS TIME, DONE - what it rests on must be lines it was shown.
        self.app.db.execute("UPDATE agent_checks SET due_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
                            (ident,))
        from personalized_agent.heartbeat.core import related
        found = {"activity_about_it": [{"id": "a1", "text": "WeChat call with Ysolde, 12:00-12:40"}]}
        with mock.patch.object(related, "context", return_value=found):
            self.app.models = Models()
            answers.append(_json.dumps({"result": "happened", "why": "The call is in the chat log.",
                                        "shown_by": ["a1"]}))
            self.assertEqual(self.app.checks.run_due(background=False), [ident])
        self.assertEqual(closed, [("ag9", "done", "agent_found_done")])
        done = [one for one in did_since(self.approvals.db, "") if one["kind"] == "check_done"]
        self.assertEqual(done[0]["ref"], ident)
        self.assertIn("it happened - marked done", done[0]["title"])
        self.assertEqual(self.app.checks.waiting(), [])
        self.assertEqual(self.app.checks.run_due(background=False), [], "done once")

    def test_i_sent_it_the_model_works_out_a_real_follow_up(self) -> None:
        """2026-11-12: "这个要变成真的follow-up"; "这个我点我已发出之后，应该让模型生成跟进的plan (比如在wechat上发出
        之后，模型会去检查我的message, 然后记录一下要follow up check)" - not by words looked for."""
        import json as _json
        from personalized_agent.agent.checks import Checks
        from personalized_agent.agent.reports import did_since
        from personalized_agent.heartbeat.agenda.execute import CARRIED_OUT
        plan = ("1. Send the three lines to Tess on WeChat\n2. When she replies on WeChat with a slot, put "
                "the call on the calendar at her slot\n")
        kv, closed, went, said_in_thread = {}, [], [], []
        self.app.agenda_executor = SimpleNamespace(_kv=lambda key: kv.get(key, ""),
                                                   _set=lambda key, value: kv.__setitem__(key, value),
                                                   started_for=lambda ident: "")
        self.app.agenda = SimpleNamespace(things=lambda: [{"id": "ag5", "title": "Three times to talk",
                                                           "detail": ""}])
        self.app.work = SimpleNamespace(run=lambda run: {"plan": {"approved_plan": plan, "handed": []},
                                                         "final": {"answer": "Drafted the three lines."}})
        self.app.agenda_approved = lambda ident, how: closed.append((ident, how)) or {"id": ident}
        self.app.agenda_follow_up_go = lambda run, ident, came, steps: went.append((run, came, steps)) or run
        self.app.agenda_sent_again = lambda ident, run: kv.__setitem__(CARRIED_OUT + ident, run)
        self.app.events = SimpleNamespace(say_about=lambda ref, text: said_in_thread.append((ref, text)))
        answers, asked = [], []

        class Models:
            def invoke(self, request, role=""):
                asked.append(request.task)
                return SimpleNamespace(text=answers.pop(0), content="", ok=True, error=None)
        self.app.models = Models()
        self.app.checks = Checks(self.app)
        checks = self.app.checks
        checks.plan_follow_up = lambda item, title, run: Checks.plan_follow_up(checks, item, title, run,
                                                                               background=False)
        answers.append(_json.dumps({
            "follow_up": True, "why": "", "wait_for": "Tess's reply in your WeChat chat with her, picking a slot",
            "sent_check": "your WeChat chat with Tess shows the three lines",
            "steps": ["Put the call on the calendar at the slot she picks"], "every_minutes": 90,
            "for_days": 5}))
        kv[CARRIED_OUT + "ag5"] = "run_5"
        said = self.approvals.decide("agenda:ag5", True)
        self.assertTrue(said["done"])
        self.assertIn("working out what to follow up on", said["reply"])
        self.assertEqual(asked, ["agent_follow_up_plan"], "a model works it out")
        self.assertEqual(closed, [], "not closed: its follow-up waits")
        follow = checks.waiting()[0]
        self.assertTrue(follow["id"].startswith("fu_"))
        watch = _json.loads(follow["watch"])
        self.assertEqual((watch["every"], watch["days"]), (90, 5))
        self.assertIn("WeChat chat with Tess", watch["sent_check"])
        told = [one for one in did_since(self.app.db, "") if one["kind"] == "follow_up_set"]
        self.assertEqual(told[0]["ref"], follow["id"])
        self.assertEqual(said_in_thread[-1][0], "agenda:ag5")
        self.assertIn(follow["id"], said_in_thread[-1][1], "said in the event's thread, with its id")
        # Each look: the message seen sent first; then the answer.
        from personalized_agent.heartbeat.core import related
        lines = {"activity_about_it": [{"id": "w1", "text": "you: three lines"}, {"id": "w2", "text": "Tess: Fri"}]}
        self.app.db.execute("UPDATE agent_checks SET due_at = '2000-01-01T00:00:00+00:00'")
        with mock.patch.object(related, "context", return_value=lines):
            answers.append(_json.dumps({"sent_seen": True, "sent_shown_by": ["w1"], "result": "not_yet"}))
            checks.run_due(background=False)
            seen = [one for one in did_since(self.app.db, "") if one["kind"] == "follow_up_seen"]
            self.assertEqual(len(seen), 1, "your message seen sent, said once")
            self.app.db.execute("UPDATE agent_checks SET due_at = '2000-01-01T00:00:00+00:00'")
            answers.append(_json.dumps({"sent_seen": True, "sent_shown_by": ["w1"], "result": "came",
                                        "what": "Tess picked Friday 10:00.", "shown_by": ["w2"]}))
            checks.run_due(background=False)
        self.assertEqual(len([one for one in did_since(self.app.db, "") if one["kind"] == "follow_up_seen"]), 1)
        self.assertEqual(went, [("run_5", "Tess picked Friday 10:00.",
                                 ["Put the call on the calendar at the slot she picks"])])
        self.assertEqual(checks.waiting(), [])
        # Nothing follows: the model says so - done.
        answers.append(_json.dumps({"follow_up": False, "why": "The plan ends with the message."}))
        kv[CARRIED_OUT + "ag5"] = "run_6"
        self.approvals.decide("agenda:ag5", True)
        self.assertEqual(closed, [("ag5", "as_it_was")])
        # Not worked out: "I sent it" waits again.
        answers.append("no json")
        kv[CARRIED_OUT + "ag5"] = "run_7"
        self.approvals.decide("agenda:ag5", True)
        self.assertEqual(kv[CARRIED_OUT + "ag5"], "run_7", "asked again")
        self.assertIn("say I sent it again", said_in_thread[-1][1])

    def test_not_seen_sent_is_noted_and_taken_on_the_users_word(self) -> None:
        """2026-11-12: "如果user说了他发了，agent能确认最好，不能确认可以记一下没确认，但是user说了做好了，然后继续做后面的
        monitor"."""
        import json as _json
        from personalized_agent.agent.checks import Checks
        from personalized_agent.agent.reports import did_since
        from personalized_agent.heartbeat.core import related
        answers = []

        class Models:
            def invoke(self, request, role=""):
                return SimpleNamespace(text=answers.pop(0), content="", ok=True, error=None)
        self.app.models = Models()
        self.app.agenda = SimpleNamespace(things=lambda: [{"id": "ag5", "title": "Three times", "detail": ""}])
        checks = Checks(self.app)
        checks.follow_up("ag5", "Three times", "run_5", ["put the call on the calendar"], "Tess's reply")
        lines = {"activity_about_it": [{"id": "w1", "text": "something else"}]}
        for seen in (False, False, True):
            self.app.db.execute("UPDATE agent_checks SET due_at = '2000-01-01T00:00:00+00:00'")
            with mock.patch.object(related, "context", return_value=dict(lines)):
                answers.append(_json.dumps({"sent_seen": seen, "sent_shown_by": ["w1"] if seen else [],
                                            "result": "not_yet"}))
                checks.run_due(background=False)
        kinds = [one["kind"] for one in did_since(self.app.db, "")]
        self.assertEqual(kinds.count("follow_up_unconfirmed"), 1, "noted once, as not confirmed")
        self.assertEqual(kinds.count("follow_up_seen"), 1, "seen later: said so")
        self.assertEqual(len(checks.waiting()), 1, "the watching goes on")

    def test_a_follow_up_looks_before_each_report_unless_its_deadline_is_tight(self) -> None:
        """2026-11-12: "根据task 类型看跟进时间。比如如果你的ddl不紧，你就在下次summary前看一眼就行"."""
        from datetime import datetime as _dt, timedelta as _td
        from personalized_agent.agent.checks import BEFORE_REPORT_MINUTES, Checks
        from personalized_agent.agent.reports import did_since
        self.app.approvals = self.approvals          # its times: 08:00, 12:00, 21:00
        self.app.agenda = SimpleNamespace(things=lambda: [{"id": "ag5", "title": "Three times", "detail": "",
                                                           "triage": {"deadline": "2026-11-30"}}])
        checks = Checks(self.app)
        now = _dt(2026, 11, 12, 9, 0).astimezone()
        made = checks.follow_up("ag5", "Three times", "run_5", ["put the call on the calendar"], "Tess's reply",
                                now=now, looks="before_reports")
        due = _dt.fromisoformat(made["due_at"]).astimezone()
        self.assertEqual(due, now.replace(hour=12) - _td(minutes=BEFORE_REPORT_MINUTES),
                         "a look shortly before the next report")
        told = [one for one in did_since(self.app.db, "") if one["kind"] == "follow_up_set"][0]
        self.assertIn("before each of your reports", told["title"])
        timing = checks._timing("ag5")
        self.assertEqual((timing["deadline"], timing["report_times"]), ("2026-11-30", ["08:00", "12:00", "21:00"]))
        self.assertEqual(len(timing["next_reports"]), 3, "the model told the next reports")
        self.app.db.execute("DELETE FROM agent_checks")
        tight = checks.follow_up("ag5", "Three times", "run_5", ["call"], every=45, now=now, looks="every")
        self.assertEqual(_dt.fromisoformat(tight["due_at"]).astimezone(), now + _td(minutes=45))

    def test_a_follow_up_with_no_answer_in_a_week_says_so(self) -> None:
        from datetime import timedelta as _td
        from personalized_agent.agent import checks as checking
        from personalized_agent.agent.reports import did_since
        self.app.agenda = SimpleNamespace(things=lambda: [{"id": "ag5", "title": "Three times", "detail": ""}])
        self.app.models = None
        checks = checking.Checks(self.app)
        long_ago = checking._now() - _td(days=8)
        checks.follow_up("ag5", "Three times", "run_5", ["put the call on the calendar"], now=long_ago)
        self.app.db.execute("UPDATE agent_checks SET due_at = '2000-01-01T00:00:00+00:00'")
        checks.run_due(background=False)
        done = [one for one in did_since(self.app.db, "") if one["kind"] == "follow_up_done"]
        self.assertIn("no answer found in 7 days - it stays on your plate", done[0]["title"])

    def test_a_check_not_shown_says_so_and_one_that_cannot_be_done_is_tried_again(self) -> None:
        import json as _json
        from personalized_agent.agent.checks import MOST_TRIES, Checks
        from personalized_agent.agent.reports import did_since
        answers = []

        class Models:
            def invoke(self, request, role=""):
                return SimpleNamespace(text=answers.pop(0), content="", ok=True, error=None)
        self.app.agenda = SimpleNamespace(things=lambda: [{"id": "ag9", "title": "Call", "detail": ""}])
        self.app.agenda_feedback = SimpleNamespace(apply=lambda *a, **k: self.fail("not shown: not closed"))
        self.app.models = Models()
        checks = Checks(self.app)
        one = checks.schedule("ag9", "Call", "look in the chats", "")
        self.app.db.execute("UPDATE agent_checks SET due_at = '2000-01-01T00:00:00+00:00'")
        from personalized_agent.heartbeat.core import related
        with mock.patch.object(related, "context", return_value={"activity_about_it": []}):
            answers.append(_json.dumps({"result": "happened", "why": "it did", "shown_by": ["made-up"]}))
            checks.run_due(background=False)
        said = [x for x in did_since(self.app.db, "") if x["kind"] == "check_done"][0]
        self.assertIn("nothing shows it happened - it stays on your plate", said["title"],
                      "a claim resting on no line it was shown is not taken")
        two = checks.schedule("ag9", "Call", "again", "")
        self.assertIsNone(two, "not set again so soon")
        self.app.db.execute("DELETE FROM agent_checks")
        checks.schedule("ag9", "Call", "look", "")
        for _ in range(MOST_TRIES):
            self.app.db.execute("UPDATE agent_checks SET due_at = '2000-01-01T00:00:00+00:00'")
            with mock.patch.object(related, "context", return_value={}):
                answers.append("not json at all")
                checks.run_due(background=False)
        failed = [x for x in did_since(self.app.db, "") if x["kind"] == "check_failed"]
        self.assertEqual(len(failed), 1, "told as failed after its tries")
        self.assertIsNotNone(one)
        # NOT SHOWN, WITH THE REPORT THERE: A QUESTION IN "NEEDS YOUR LOOK", not a thing done (the user,
        # 2026-11-13: "如果是让我confirm这个是否做了，应该是needs your look").
        self.app.db.execute("DELETE FROM agent_checks")
        self.app.approvals = self.approvals
        closed = []
        self.app.agenda_feedback = SimpleNamespace(apply=lambda *a, **k: closed.append((a, k)))
        self.approvals.carriers["check_happened"] = lambda payload: self.app.agenda_feedback.apply(
            payload["item"], "done", by="user")
        asked = checks.schedule("ag9", "Call", "look in the chats", "", now=datetime.now(timezone.utc)
                                + timedelta(days=2))
        self.app.db.execute("UPDATE agent_checks SET due_at = '2000-01-01T00:00:00+00:00'")
        before = len([x for x in did_since(self.app.db, "") if x["kind"] == "check_done"])
        with mock.patch.object(related, "context", return_value={"activity_about_it": []}):
            answers.append(_json.dumps({"result": "not_shown", "why": "No call in the log.", "shown_by": []}))
            checks.run_due(background=False)
        self.assertEqual(len([x for x in did_since(self.app.db, "") if x["kind"] == "check_done"]), before,
                         "not a thing done by the agent")
        [held] = [x for x in self.approvals.items() if x["source"] == "held"]
        self.assertEqual((held["title"], held["yes"], held["no"], held["batch"]),
                         ("Did it happen? Call", "It happened", "It didn't", False),
                         "a question of fact, never answered by approve all")
        self.assertIn(asked["id"], held["detail"])
        self.assertTrue(self.approvals.decide(held["key"], True)["done"])
        self.assertEqual(closed, [(("ag9", "done"), {"by": "user"})], "its yes marks it done")
        import inspect
        from personalized_agent.app import AgentApplication
        self.assertIn('"check_happened"', inspect.getsource(AgentApplication._approval_carriers))

    def test_answered_after_it_was_settled_says_how_not_an_error(self) -> None:
        """2026-11-12: "That could not be done: no such thing waiting ... 这个no such thing waiting应该是
        indicate什么bug" - a page shown before the agent let the thing go."""
        ident = self.approvals.hold("agenda", "agenda_clean_up", "Take off: Old call", {"item": "ag1"})
        self.approvals._settle(ident, "done_by_agent")
        said = self.approvals.decide("held:" + ident, True)
        self.assertFalse(said["done"])
        self.assertIn("Your agent found it done and took it off already", said["settled"])
        self.assertNotEqual(said["said"], "no such thing waiting")
        from personalized_agent.agent.events import Events
        lines = []
        fake = SimpleNamespace(say=lambda event, who, text, item="": lines.append((who, text)),
                               WATCH_KEY="w.", db=self.approvals.db)
        result = Events.answer(fake, "ev1", "Approve", lambda: said, "Taken off: Old call.")
        self.assertTrue(result["done"], "no error shown")
        self.assertEqual(lines, [("you", "Approve"), ("agent", said["settled"])])

    def test_what_a_review_held_whose_time_is_past_is_let_go_of(self) -> None:
        self.app.agenda_approvals = lambda: {"approval": {"rows": []}}
        self.approvals.hold("trigger", "add_a_calendar_reminder", "Old walk", {}, due="2020-01-01 10:00")
        let_go = self.approvals.check_done(force=True)
        self.assertEqual([one["title"] for one in let_go], ["Old walk"])
        self.assertEqual([one for one in self.approvals.items() if one["source"] == "held"], [])

    def test_the_agenda_does_not_learn_what_the_agent_found(self) -> None:
        source = (ROOT / "personalized_agent" / "heartbeat" / "agenda" / "act.py").read_text("utf-8")
        self.assertIn('agent = str(by).startswith("agent")', source)


class TheRunsAskAnsweredInWordsTests(unittest.TestCase):
    def test_denied_with_the_users_words_told_to_the_run(self) -> None:
        from personalized_agent.agent.work import runner
        run = runner.Run.__new__(runner.Run)
        run.plan = {"packages": {"requests": []}, "approvals": [
            {"id": "q1", "status": runner.PENDING}], "model_waits": []}
        run._answer_lock, run._model_lock = __import__("threading").Lock(), __import__("threading").Lock()
        run._answers = {"q1": __import__("threading").Event()}
        said = run.answer("q1", False, "Use the staging server instead.")
        self.assertEqual((said["status"], said["note"]),
                         (runner.DENIED, "the user said: Use the staging server instead."))


class TheApplicationTests(unittest.TestCase):
    """What pops one by one now waits for the summary - by the code's own words."""

    def test_wired(self) -> None:
        source = (ROOT / "personalized_agent" / "app.py").read_text("utf-8")
        for words in ("self.approvals = Approvals(self)", '("Approvals", self.approvals.start)',
                      "if self._agenda_waits(metadata):",
                      'if ask.get("kind") != "model" and not (due and self.approvals.urgent(due)):',
                      'if which == "approvals":'):
            self.assertIn(words, source)

    def test_the_agendas_call_waits_unless_due_before_the_summary(self) -> None:
        from personalized_agent.app import AgentApplication
        soon = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
        later = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
        fake = SimpleNamespace(
            approvals=SimpleNamespace(urgent=lambda due: ap.Approvals.urgent(
                SimpleNamespace(next_time=lambda now=None: datetime.now().astimezone()
                                + timedelta(hours=2)), due)),
            agenda=SimpleNamespace(things=lambda: [
                {"id": "a1", "triage": {"deadline": later}},
                {"id": "a2", "triage": {"deadline": soon}}]))
        waits = AgentApplication._agenda_waits
        self.assertTrue(waits(fake, {"agenda": {"approve": ["a1"]}}))
        self.assertFalse(waits(fake, {"agenda": {"approve": ["a1", "a2"]}}), "one cannot wait")
        self.assertFalse(waits(fake, {"agenda": {}}), "not a call for approval: as before")


if __name__ == "__main__":
    unittest.main()

"""THE REPORT BY EVENTS (agent/events.py; the user, 2026-11-12: "以事件为单位，一个事件可以对应多个to do,
event之类的 ... 对每个时间有一个快速回复，或者聊天框 ... 所谓的人要approve在display上和做完的没区别")."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personalized_agent.agent.events import Events  # noqa: E402
from personalized_agent.data_manage.storage.database import Database  # noqa: E402


class _Models:
    def __init__(self, answers):
        self.answers, self.asked = dict(answers), []

    def invoke(self, request, role=""):
        self.asked.append(request)
        said = self.answers.get(request.task)
        return SimpleNamespace(text=said(request) if callable(said) else said or "{}")


class _Agent:
    """The chat agent as the tests play it: a conversation store, and an answer."""

    def __init__(self, db):
        self.db, self.turns = db, []
        self.conversations = SimpleNamespace(create=self._create, messages=self._messages)
        self.said = {}

    def _create(self, title=""):
        ident = f"conv{len(self.said) + 1}"
        self.said[ident] = []
        return ident

    def _messages(self, conversation, limit=None):
        return list(self.said.get(conversation, []))

    def handle_message(self, text, conversation, front=""):
        self.turns.append((text, conversation, front))
        self.said[conversation] += [{"role": "user", "content": text},
                                    {"role": "assistant", "content": "Done - I moved it to Friday."}]


APPROVE = [{"key": "agenda:a1", "title": "Message Ada to set the review", "why": "drafted",
            "due": "2026-11-20 18:00", "batch": True, "source": "agenda"}]
DONE = [{"key": "did:d1", "title": "Wrote to Ada: review time", "why": "she asked", "at": "x",
         "source": "society"}]
MINE = [{"key": "todo:t1", "title": "Read the survey draft", "why": "", "due": "",
         "source": "plan"}]


class EventsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._dir.cleanup)
        self.db = Database(Path(self._dir.name) / "agent.db")
        self.addCleanup(self.db.close)

    def _events(self, answers):
        app = SimpleNamespace(db=self.db, models=_Models(answers))
        app.agent = _Agent(self.db)
        self.app = app
        return Events(app)

    def test_the_same_project_put_together_as_one_event(self) -> None:
        """2026-11-13: "这个人的project有好多个，整理的时候应该merge"; "按项目，合作"."""
        from personalized_agent.agent.reports import Report, _ready, did_since
        _ready(self.db)
        events = self._events({})
        reports_ev = events._new_event("Ada's two reports")
        reply_ev = events._new_event("Reply to Ada about the index")
        other_ev = events._new_event("Garden plan")
        empty_ev = events._new_event("Ada's two reports")            # made twice at once, left empty
        for key, event in (("mine:a1", reports_ev), ("mine:a2", reply_ev), ("mine:a3", reply_ev),
                           ("mine:g1", other_ev)):
            self.db.execute("INSERT INTO report_event_items (key, event_id, title, added_at, conversation_id) "
                            "VALUES (?, ?, ?, 'x', ?)", (key, event, key, "conv_" + key))
        self.db.execute("UPDATE report_events SET conversation_id = 'conv_ev' WHERE id = ?", (reply_ev,))
        self.app.models = _Models({"report_merge": json.dumps({"merge": [
            {"into": reports_ev, "from": [reply_ev, "ev_unknown"], "name": "Ada's index project",
             "why": "The same project with Ada."},
            {"into": other_ev, "from": [reports_ev], "name": "no", "why": "a second claim on one"}]})})
        done = events.merge_open()
        self.assertEqual([(one["into"], one["from"]) for one in done], [(reports_ev, [reply_ev])])
        moved = {row["key"]: row["event_id"] for row in self.db.query("SELECT key, event_id FROM report_event_items")}
        self.assertEqual(moved, {"mine:a1": reports_ev, "mine:a2": reports_ev, "mine:a3": reports_ev,
                                 "mine:g1": other_ev})
        kept = self.db.query_one("SELECT * FROM report_events WHERE id = ?", (reports_ev,))
        self.assertEqual((kept["title"], kept["conversation_id"]), ("Ada's index project", "conv_ev"))
        folded = self.db.query_one("SELECT * FROM report_events WHERE id = ?", (reply_ev,))
        self.assertTrue(folded["closed_at"])
        self.assertEqual(events.merged_into(reply_ev), reports_ev)
        self.assertTrue(self.db.query_one("SELECT closed_at FROM report_events WHERE id = ?",
                                          (empty_ev,))["closed_at"], "the empty one closed")
        said = [one for one in did_since(self.db, "") if one["kind"] == "events_merged"]
        self.assertIn("Ada's index project", said[0]["title"])
        # Carried into the one it became - its things, their own conversations kept by key.
        report = Report.__new__(Report)
        report.db, report.app = self.db, self.app
        before = {"yours": [{"id": reply_ev, "title": "Reply to Ada about the index",
                             "items": [{"key": "mine:a2", "kind": "mine", "title": "a2"}]}]}
        now = {"yours": [{"id": reports_ev, "title": "Ada's index project",
                          "items": [{"key": "mine:a1", "kind": "mine", "title": "a1"}]}]}
        carried = report._carried(before, now, events.merged_into)
        self.assertEqual([one["id"] for one in carried["yours"]], [reports_ev])
        self.assertEqual(sorted(one["key"] for one in carried["yours"][0]["items"]), ["mine:a1", "mine:a2"])

    def test_what_is_still_the_users_keeps_the_event_with_them_whatever_was_judged(self) -> None:
        """2026-11-13: "为啥这个是在done by your agent 列表里，这个不是agents还在prepare吗"."""
        events = self._events({})
        mine = [{"key": "todo:s1", "title": "Seminar brief", "why": "", "due": "", "source": "plan"}]
        tabs = events.tabs([], [], mine)
        [event] = tabs["yours"]
        self.db.execute("UPDATE report_events SET status = 'done' WHERE id = ?", (event["id"],))
        tabs = events.tabs([], [], mine)
        self.assertEqual([one["id"] for one in tabs["yours"]], [event["id"]], "judged done, still theirs")
        self.assertEqual(tabs["done"], [])

    def test_its_time_gone_by_the_agents_part_is_not_being_prepared(self) -> None:
        from personalized_agent.agent.reports import Report
        report = Report.__new__(Report)
        report.db = self.db
        thing = {"id": "s1", "title": "Seminar", "triage": {"who": "human", "agent_part": "Write a brief",
                                                            "deadline": "2026-11-12 13:00"}}
        report.app = SimpleNamespace(config=None, agenda=SimpleNamespace(things=lambda: [thing], latest=lambda: None),
                                     agenda_executor=SimpleNamespace(started_for=lambda ident: "", _kv=lambda key: ""))
        report._looked = None
        prep = report._prep_now({"kind": "mine", "ref": "todo:s1"})
        self.assertEqual(prep["state"], "ready")
        self.assertIn("time has passed", prep["note"])

    def test_kept_up_to_date_by_itself_the_whole_look_every_two_hours(self) -> None:
        """2026-11-13: "report上的定时变成通知时间"; "改成每2小时一次整理吧"."""
        from personalized_agent.agent import reports as rp
        _ = self._events({})
        report = rp.Report.__new__(rp.Report)
        report.db, report.app, report.making = self.db, self.app, ""
        rp._ready(self.db)
        made, signed = [], ["a"]
        report._signature = lambda: signed[0]
        report.make_in_background = lambda why, full=True: made.append(full) or True
        report._set(rp.SIGNED_KEY, "a")
        base = time.time()
        self.assertFalse(report.keep_current(now=base), "nothing changed: nothing made")
        signed[0] = "b"
        self.assertFalse(report.keep_current(now=base), "changed: it settles first")
        self.assertFalse(report.keep_current(now=base + rp.SETTLE_SECONDS - 1))
        self.assertTrue(report.keep_current(now=base + rp.SETTLE_SECONDS))
        self.assertEqual(made, [True], "no whole look yet: this one is")
        report._set(rp.FULL_KEY, rp._now())
        self.assertFalse(report.keep_current(now=time.time()))
        self.assertTrue(report.keep_current(now=time.time() + rp.SETTLE_SECONDS))
        self.assertEqual(made, [True, False], "the whole look within two hours: only what is new")
        self.assertEqual(rp.FULL_EVERY_SECONDS, 2 * 3600)

    def test_entries_joined_to_events_and_shown_in_three_tabs(self) -> None:
        def join(request):
            new = json.loads(request.messages[0]["content"])["new_entries"]
            return json.dumps({"join": [
                {"key": one["key"], "new": "The project review with Ada"
                 if "Ada" in one["title"] else "The survey"} for one in new]})
        events = self._events({"report_events": join,
                               "report_order": '{"order": []}'})
        tabs = events.tabs(APPROVE, DONE, MINE)
        [review] = tabs["needs_you"]
        self.assertEqual(review["title"], "The project review with Ada")
        self.assertEqual([one["kind"] for one in review["items"]], ["approve", "done"],
                         "what waits and what was done, one event - waiting first")
        self.assertEqual(review["items"][0]["ref"], "agenda:a1", "answered by its own key")
        self.assertEqual(review["due"], "2026-11-20 18:00")
        [survey] = tabs["yours"]
        self.assertEqual(survey["title"], "The survey")
        self.assertEqual(tabs["done"], [])
        # Kept across days: the same entries, the same events, nothing asked again.
        asked = len(self.app.models.asked)
        again = events.tabs(APPROVE, DONE, MINE)
        self.assertEqual(again["needs_you"][0]["id"], review["id"])
        self.assertEqual([one.task for one in self.app.models.asked[asked:]],
                         [], "joined already, and the order kept")
        # Answered, the event is done: what the agent did is left.
        self.assertEqual(events.tabs([], DONE, MINE)["done"][0]["id"], review["id"])

    def test_its_progress_looked_at_again_and_kept_to_the_next_report(self) -> None:
        """2026-11-12: "在下一个summary的时候会对当前还有的event的状态查询并且进行更新 ... 之前的和agent的
        interation记录什么都是保存的"."""
        def progress(request):
            events = json.loads(request.messages[0]["content"])["events"]
            return json.dumps({"events": [{"id": one["id"], "status": "yours",
                                           "progress": "Ada agreed; you send the room booking."}
                                          for one in events]})
        events = self._events({"report_progress": progress})
        tabs = events.tabs(APPROVE, [], [])
        event = tabs["needs_you"][0]
        events.reply(event["id"], "Book the small room.", event["items"])
        for _wait in range(50):
            if not events.thread(event["id"])["going"]:
                break
            time.sleep(0.05)
        row = self.db.query_one("SELECT progress FROM report_events WHERE id = ?", (event["id"],))
        self.assertEqual(row["progress"], "Done - I moved it to Friday.", "its progress at once")
        # The approval answered since: nothing new in it - the event stays, its thread with it.
        again = events.update_progress(events.tabs([], [], []))
        [kept] = again["yours"]
        self.assertEqual((kept["id"], kept["progress"]),
                         (event["id"], "Ada agreed; you send the room booking."))
        self.assertEqual(len(events.thread(event["id"])["messages"]), 2, "its conversation kept")
        asked = [one for one in self.app.models.asked if one.task == "report_progress"][-1]
        handed = json.loads(asked.messages[0]["content"])["events"][0]
        self.assertEqual(handed["conversation"][0]["text"], "Book the small room.")

    def test_a_reply_under_a_thing_goes_to_its_own_work_said_as_a_chat(self) -> None:
        """2026-11-12: "应该是一个满工具的chat基于work的context进行回答（可以类似为工作run?)"."""
        events = self._events({})
        self.app.agent.conversations.add_message = lambda conv, role, content, metadata=None:             self.app.agent.said.setdefault(conv, []).append({"role": role, "content": content})
        event = events.tabs(APPROVE, [], [])["needs_you"][0]
        asked = []
        said = events.reply_to_work(event["id"], "Make it Tuesday instead.", "agenda", "run_7",
                                    lambda: asked.append(1) or {"done": True})
        self.assertTrue(said["done"])
        self.assertEqual(asked, [1], "given to the thing's own work")
        thread = events.thread(event["id"])["messages"]
        self.assertEqual(thread[0], {"who": "you", "text": "Make it Tuesday instead.", "at": ""})
        self.assertIn("doing it again in its own work", thread[1]["text"])
        events.work_done("run_7", "Lanternfish chat", ["EMAIL - Reply to Ysolde"])
        last = events.thread(event["id"])["messages"][-1]["text"]
        self.assertIn("Done again as you said: Lanternfish chat.", last)
        self.assertIn("EMAIL - Reply to Ysolde", last)
        events.work_done("run_7", "again", [])
        self.assertEqual(len(events.thread(event["id"])["messages"]), 3, "told once")
        failed = events.reply_to_work(event["id"], "Hm", "agenda", "", lambda: {"done": False,
                                                                                "said": "gone"})
        self.assertFalse(failed["done"])

    def test_a_quick_answer_is_a_message_and_the_agent_answers_what_it_did(self) -> None:
        """2026-11-12: "你不要answered since, 每个answer之后都应该是用chat形式的回复 ... 你那个快速按钮只是让我
        快速发个chat而已，不是特殊的东西"."""
        from personalized_agent.agent.approvals import done_said
        events = self._events({})
        self.app.agent.conversations.add_message = lambda conv, role, content, metadata=None:             self.app.agent.said.setdefault(conv, []).append({"role": role, "content": content})
        event = events.tabs(APPROVE, [], [])["needs_you"][0]
        item = {"title": "Take off: Meet Ada on Monday",
                "asks": {"lead": "Approving takes it off your plate", "steps": []}}
        said = events.answer(event["id"], "Approve", lambda: {"done": True}, done_said(item, "yes"))
        self.assertTrue(said["done"])
        thread = [(one["who"], one["text"]) for one in events.thread(event["id"])["messages"]]
        self.assertEqual(thread, [("you", "Approve"),
                                  ("agent", "Taken off your plate: Take off: Meet Ada on Monday.")])
        failed = events.answer(event["id"], "Already done", lambda: {"done": False, "said": "gone"},
                               done_said(item, "already"))
        self.assertFalse(failed["done"])
        self.assertEqual(events.thread(event["id"])["messages"][-1]["text"], "That could not be done: gone.")
        planned = {"title": "Chat", "asks": {"lead": "Once you approve, your agent will",
                                             "steps": ["Send the email", "Put it on the calendar"]}}
        self.assertIn("Send the email; Put it on the calendar", done_said(planned, "yes"))
        self.assertIn("not done", done_said(item, "no"))

    def test_each_thing_its_own_conversation(self) -> None:
        """2026-11-13: "我不是说一个item有整个chat, 而是每个to do (比如这个approve)有单独的chat"."""
        events = self._events({})
        self.app.agent.conversations.add_message = lambda conv, role, content, metadata=None:             self.app.agent.said.setdefault(conv, []).append({"role": role, "content": content})
        two = APPROVE + [{"key": "agenda:a2", "title": "Book the room", "why": "", "due": "",
                          "batch": True, "source": "agenda"}]
        events._ask_join = lambda open_events, entries: {}      # each its own event, then joined below
        tabs = events.tabs(two, [], MINE)
        first = next(one for tab in tabs.values() for one in tab if one["items"])
        # Both things in one event, as a model would join them.
        for one in ("approve:agenda:a2", "mine:t1"):
            self.db.execute("UPDATE report_event_items SET event_id = ? WHERE key = ?", (first["id"], one))
        event = first["id"]
        events.answer(event, "Approve", lambda: {"done": True}, "Done.", item="approve:agenda:a1")
        events.answer(event, "I already did this", lambda: {"done": True}, "Noted.", item="mine:t1")
        self.assertEqual([one["text"] for one in events.thread(event, "approve:agenda:a1")["messages"]],
                         ["Approve", "Done."])
        self.assertEqual([one["text"] for one in events.thread(event, "mine:t1")["messages"]],
                         ["I already did this", "Noted."])
        self.assertEqual(events.thread(event)["messages"], [], "nothing said in the event's own")
        made = {row["key"]: row["conversation_id"] for row in self.db.query(
            "SELECT key, conversation_id FROM report_event_items")}
        self.assertNotEqual(made["mine:t1"], made["approve:agenda:a1"], "each its own")
        self.assertEqual(made["approve:agenda:a2"], "", "none until something is said in it")
        conversation = self.db.query_one("SELECT conversation_id FROM report_event_items WHERE key = ?",
                                         ("mine:t1",))["conversation_id"]
        self.assertEqual(events.of_conversation(conversation)["item"], "mine:t1",
                         "its acts reviewed as the event's")
        # Said by the agent about one thing: in that thing's own.
        self.assertTrue(events.say_about("agenda:a2", "Booked."))
        self.assertEqual(events.thread(event, "approve:agenda:a2")["messages"][-1]["text"], "Booked.")
        # The words under it, to the chat agent - in its own conversation.
        events.reply(event, "Make it Tuesday.", [], about="Book the room", item="approve:agenda:a2")
        for _ in range(50):
            if not events.thread(event, "approve:agenda:a2")["going"]:
                break
            time.sleep(0.05)
        turn = self.app.agent.turns[-1]
        self.assertEqual(turn[1], self.db.query_one(
            "SELECT conversation_id FROM report_event_items WHERE key = ?",
            ("approve:agenda:a2",))["conversation_id"])
        with self.assertRaises(ValueError):
            events.thread(event, "mine:nothing")

    def test_its_own_sentences_in_the_display_language(self) -> None:
        """2026-11-12: "要求agent用display language 显示，除非用户另有要求"."""
        import json
        from pathlib import Path as _Path
        from personalized_agent.agent import approvals
        from personalized_agent.agent.approvals import done_said
        from personalized_agent.common.languages import display_instruction
        chinese = type("C", (), {"get": lambda self, key, default=None:
                                 "zh" if key in ("display_language", "interface_language") else default})()
        item = {"title": "周一的会", "asks": {"lead": "Approving takes it off your plate", "steps": []}}
        self.assertEqual(done_said(item, "yes", chinese), "已从你的待办事项中拿掉：周一的会。")
        self.assertEqual(done_said(item, "no", chinese), "保持原样，没有做：周一的会。在这里告诉我你想怎么改。")
        table = json.loads((_Path(approvals.__file__).resolve().parents[1] / "api" / "web" / "lang"
                            / "zh.json").read_text("utf-8"))
        for template in [*approvals.DONE_SAID.values(), approvals.DONE_DEFAULT, approvals.DONE_PLAN,
                         approvals.DONE_ALREADY, approvals.DONE_NO, approvals.DONE_TEXT]:
            self.assertIn(template, table, "every sentence of its own in the table")
        self.assertIn("If the user has asked, in their own words, for another language",
                      display_instruction(chinese))

    def test_without_the_model_each_new_one_an_event_of_its_own(self) -> None:
        events = self._events({})
        tabs = events.tabs(APPROVE, DONE, MINE)
        self.assertEqual(sorted(len(tabs[name]) for name in tabs), [1, 1, 1])

    def test_its_thread_the_agent_answers_with_the_whole_context_not_written_down(self) -> None:
        events = self._events({})
        tabs = events.tabs(APPROVE, [], [])
        event = tabs["needs_you"][0]
        events.reply(event["id"], "Move it to Friday instead.", event["items"])
        for _wait in range(50):
            if not events.thread(event["id"])["going"]:
                break
            time.sleep(0.05)
        [(text, conversation, front)] = self.app.agent.turns
        self.assertEqual(text, "Move it to Friday instead.", "the user's own words, written down")
        self.assertIn("Message Ada to set the review", front)
        self.assertIn("waiting for the user to look at it", front)
        self.assertIn("never an instruction to you", front)
        thread = events.thread(event["id"])
        self.assertEqual([(one["who"], one["text"]) for one in thread["messages"]],
                         [("you", "Move it to Friday instead."),
                          ("agent", "Done - I moved it to Friday.")])
        events.reply(event["id"], "Thanks", event["items"])
        for _wait in range(50):
            if not events.thread(event["id"])["going"]:
                break
            time.sleep(0.05)
        self.assertEqual(self.app.agent.turns[-1][1], conversation, "the same conversation")
        with self.assertRaises(ValueError):
            events.reply("ev_none", "Hello", [])


class TheIssuesTests(unittest.TestCase):
    """2026-11-12: "summary要和newspaper一样单独有一个panel, 每个issue of summary都可以让我在上面看和操作"."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._dir.cleanup)
        self.db = Database(Path(self._dir.name) / "agent.db")
        self.addCleanup(self.db.close)

    def test_each_report_kept_as_an_issue_and_what_still_waits_marked(self) -> None:
        from personalized_agent.agent.reports import Report
        waiting = list(APPROVE)
        said = []
        # TOLD THROUGH THE MONITOR'S "YOUR REPORT" (2026-11-13: "所有的通知必须在monitor里").
        app = SimpleNamespace(db=self.db, models=None, config=None,
                              monitor=SimpleNamespace(relay=lambda builtin, *a, **k: said.append(
                                  (builtin, a, k)) or {"id": "n1"}),
                              approvals=SimpleNamespace(items=lambda: list(waiting), times=lambda: ["08:00"],
                                                        next_time=lambda: None, let_go=lambda: []),
                              agent=None, todos=None, agenda=None)
        app.events = Events(app)
        report = Report(app)
        report.tell()
        [(builtin, args, kwargs)] = said
        self.assertEqual(builtin, "summary")
        [issue] = report.issues()
        self.assertEqual(kwargs["metadata"]["issue"], issue["id"], "the notification opens its issue")
        self.assertEqual(issue["counts"]["needs_you"], 1)
        shown = report.issue(issue["id"])
        [item] = shown["tabs"]["needs_you"][0]["items"]
        self.assertNotIn("answered", item, "still waits: answered here")
        self.assertEqual(item["state"], "open")
        waiting.clear()                                    # answered since
        shown = report.issue(issue["id"])
        # NOTHING OPEN IN IT ANY MORE (the user, 2026-11-13: "如果no longer on, 应该把这个状态标为done之类的"):
        # with what is done, the thing marked so, and how.
        # ... AND GONE FROM THE REPORT, ITS EVENT CLOSED (the user, 2026-11-13: "显示done了就从事件列表里删除").
        self.assertEqual(shown["tabs"]["needs_you"], [])
        self.assertEqual(shown["tabs"].get("done") or [], [])
        self.assertEqual(self.db.query_one("SELECT COUNT(*) AS n FROM report_events WHERE closed_at = ''")["n"], 0)
        with self.assertRaises(ValueError):
            report.issue("issue_none")

    def test_ok_on_a_thing_done_by_the_agent_and_it_is_not_shown_again(self) -> None:
        """2026-11-13: "done by agents, 需要快速按钮有ok, ok了之后就不再出现done by agents里"; and each event's last
        active time, for "每个panel支持按照last active time 排序"."""
        from personalized_agent.agent.reports import Report, did, did_since, seen
        app = SimpleNamespace(db=self.db, models=None, config=None,
                              notifications=SimpleNamespace(notify=lambda *a, **k: {"id": "n1"}),
                              approvals=SimpleNamespace(items=lambda: [], times=lambda: [],
                                                        next_time=lambda: None, let_go=lambda: []),
                              agent=None, todos=None, agenda=None)
        app.events = Events(app)
        report = Report(app)
        did(self.db, "work_done", "Done by your agent: sort the seed list", "", source="agenda", ref="a1")
        report.make_issue("x")
        [event] = report.latest_issue()["tabs"]["done"]
        [item] = event["items"]
        self.assertTrue(event["active_at"], "when it was last active")
        self.assertTrue(seen(self.db, item["ref"].partition(":")[2]))
        self.assertEqual(report.latest_issue()["tabs"]["done"], [], "said OK to: gone")
        self.assertEqual([one["id"] for one in did_since(self.db, "", unseen=True)], [], "and not made again")
        self.assertFalse(seen(self.db, "did_none"))
        # OK FOR ALL OF THEM (2026-11-13: "要有ok for all 的选项").
        from personalized_agent.api.routes import build_routes
        did(self.db, "check_set", "Set check ck_1", "", source="plan", ref="ck_1")
        did(self.db, "check_set", "Set check ck_2", "", source="plan", ref="ck_2")
        first, second = [one["id"] for one in did_since(self.db, "", unseen=True)]
        routes = build_routes(SimpleNamespace(db=self.db, config=None))
        said = routes[("POST", "/api/report/ok_all")](SimpleNamespace(
            get=lambda key, default=None: {"keys": [f"did:{first}", f"did:{second}", "held:x"]}.get(key, default)))
        self.assertEqual(said, {"seen": 2})
        self.assertEqual(did_since(self.db, "", unseen=True), [])

    def test_only_the_latest_issue_kept_what_is_not_over_carried_into_it(self) -> None:
        """2026-11-13: "report不要多个issue, 而是只保留most recent issue, 然后上个issue的event没结束继续保留到这个
        （并且保留我的conversation)"."""
        from personalized_agent.agent.reports import Report
        first = {"key": "agenda:a1", "title": "Message Ada to set the review", "why": "", "due": "",
                 "batch": True, "source": "agenda"}
        second = dict(first, key="agenda:a2", title="Book the room for the review")
        waiting = [first, second]
        app = SimpleNamespace(db=self.db, models=None, config=None,
                              notifications=SimpleNamespace(notify=lambda *a, **k: {"id": "n1"}),
                              approvals=SimpleNamespace(items=lambda: list(waiting), times=lambda: [],
                                                        next_time=lambda: None, let_go=lambda: []),
                              agent=None, todos=None, agenda=None)
        app.events = Events(app)
        # Both in one event, the first with its own conversation.
        event = app.events._new_event("The review")
        for key, conversation in (("approve:agenda:a1", "conv_a1"), ("approve:agenda:a2", "")):
            self.db.execute("INSERT INTO report_event_items (key, event_id, title, added_at, conversation_id) "
                            "VALUES (?, ?, 'x', 'x', ?)", (key, event, conversation))
        report = Report(app)
        report.tell()
        waiting.remove(first)                              # the first answered; the event goes on
        report.tell()
        [issue] = report.issues()                          # only the latest kept
        shown = report.issue(issue["id"])
        [held] = shown["tabs"]["needs_you"]
        self.assertEqual(held["id"], event)
        states = {one["key"]: one["state"] for one in held["items"]}
        self.assertEqual(states, {"approve:agenda:a1": "done", "approve:agenda:a2": "open"},
                         "what was answered in it carried on, with what is done")
        row = self.db.query_one("SELECT conversation_id FROM report_event_items WHERE key = ?",
                                ("approve:agenda:a1",))
        self.assertEqual(row["conversation_id"], "conv_a1", "its conversation kept with it")
        # Over: not carried into the next one.
        waiting.clear()
        report.tell()
        [issue] = report.issues()
        shown = report.issue(issue["id"]) if issue else {"tabs": {}}
        self.assertEqual(shown["tabs"].get("needs_you") or [], [])

    def test_a_thing_of_the_users_done_since_is_marked_done(self) -> None:
        from personalized_agent.agent.reports import Report
        gone = {"t1": {"id": "t1", "deleted_at": "x"}, "t2": {"id": "t2", "deleted_at": "", "done_at": ""}}
        report = Report.__new__(Report)
        report.app = SimpleNamespace(config=None, todos=SimpleNamespace(get=gone.get))
        plate = {"a1": {"status": "closed"}, "a2": {"status": "open"}}
        report.db = SimpleNamespace(query_one=lambda sql, args: plate.get(args[0]))
        state = lambda ref: Report._state_now(report, {"kind": "mine", "ref": ref})[0]  # noqa: E731
        self.assertEqual([state("todo:a1"), state("todo:a2"), state("todo:t1"), state("todo:t2"),
                          state("todo:none")], ["done", "open", "done", "open", "done"])
        self.assertEqual(Report._state_now(report, {"kind": "done"})[0], "done")


class TheReportTests(unittest.TestCase):
    def test_wired(self) -> None:
        source = (ROOT / "personalized_agent" / "app.py").read_text("utf-8")
        self.assertIn("self.events = Events(self)", source)
        self.assertIn('self.agenda_feedback.apply(ident, "done", by="agent_did_it")', source)
        routes = (ROOT / "personalized_agent" / "api" / "routes.py").read_text("utf-8")
        for words in ('"/api/report"', '"/api/report/event"', '"/api/report/event/reply"'):
            self.assertIn(words, routes)



class YoursToDoTests(unittest.TestCase):
    """The user, 2026-11-13: "yours to do 这个也要提供回复，并且用chat形式（比如我会跟agent说我已经做了，或者让agent
    帮我做某些事情等)。另外这个yours to do 有的时候会有agent给我做好的东西，也要有link让我打开"."""

    def test_what_the_agent_made_for_a_thing_of_theirs_is_with_it(self) -> None:
        from personalized_agent.agent.reports import Report
        made = [{"n": 0, "kind": "file", "title": "Survey notes", "how": "", "check": [], "where": "x.md"}]
        app = SimpleNamespace(
            agenda=SimpleNamespace(things=lambda: [{"id": "a1", "title": "Read the survey draft",
                                                    "triage": {"who": "human"}}],
                                   latest=lambda: None),
            agenda_executor=SimpleNamespace(started_for=lambda ident: "run_7" if ident == "a1" else ""),
            todos=SimpleNamespace(open=lambda: [{"id": "t2", "text": "Book the room"},
                                                {"id": "t3", "text": "Water the beds"}]),
            drafts=SimpleNamespace(of_todos=lambda: {"t2": {"versions": [{"run": "run_1"},
                                                                         {"run": "run_2"}]}}),
            approvals=SimpleNamespace(_material=lambda run: {"deliverables": made if run in (
                "run_7", "run_2") else []}))
        report = Report.__new__(Report)
        report.app = app
        said = {one["key"]: one for one in Report.to_do(report)}
        self.assertEqual((said["todo:a1"]["run"], said["todo:a1"]["deliverables"]), ("run_7", made))
        self.assertEqual(said["todo:t2"]["run"], "run_2", "the latest version's")
        self.assertNotIn("run", said["todo:t3"], "nothing made: no link")

    def test_the_plan_keeps_the_works_draft_its_run_is_the_drafts_and_done_work_has_its_files(self) -> None:
        """The plan keeps the draft a thing was handed to, not its run (a draft's id taken for a run's,
        nothing it made was found); and 2026-11-13: "done by agent ... 如果有文件也要让我看 ... 让我看是让我看看
        完整版，而不是需要我批准"."""
        import tempfile
        from personalized_agent.agent.reports import Report, _ready, did
        from personalized_agent.data_manage.storage.database import Database
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        db = Database(Path(folder.name) / "agent.db")
        self.addCleanup(db.close)
        _ready(db)
        made = [{"n": 0, "kind": "file", "title": "Survey notes", "how": "", "check": [], "where": "x.md"}]
        report = Report.__new__(Report)
        report.db = db
        report.app = SimpleNamespace(
            agenda_executor=SimpleNamespace(started_for=lambda ident: {"a1": "draft_3"}.get(ident, "")),
            drafts=SimpleNamespace(get=lambda ident: {"draft_3": {"run_id": "run_9"}}.get(ident)),
            approvals=SimpleNamespace(_material=lambda run: {"deliverables": made if run == "run_9" else []}),
            config=None)
        self.assertEqual(report._plan_run("a1"), "run_9")
        self.assertEqual(report._plan_run("a2"), "")
        did(db, "work_done", "Done: sort the survey notes", "", source="agenda", ref="a1")
        did(db, "check_set", "Set check ck_1", "", source="plan", ref="ck_1")
        shown = {one["record"]["what"]: one for one in report.done(since="")}
        self.assertEqual((shown["work_done"]["run"], shown["work_done"]["deliverables"]), ("run_9", made))
        self.assertNotIn("deliverables", shown["check_set"])

    def test_drawn_with_a_reply_and_what_was_made_to_open(self) -> None:
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("no node here")
        from test_chat_debugger_ui import ESCAPE
        from test_overview_todo_scroll import _function
        source = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        start = source.index("const REPORT_KINDS = ")
        consts = source[start:source.index("function reportDue", start)]
        item = {"key": "mine:a1", "ref": "todo:a1", "kind": "mine", "title": "Read the survey draft",
                "run": "run_7", "deliverables": [{"n": 0, "kind": "file", "title": "Survey notes"},
                                                 {"n": 1, "kind": "answer", "title": "Already sent",
                                                  "text": "Nothing to send - it went out on 8 Nov."}]}
        script = ("const window=globalThis;" + ESCAPE + "const localStamp = (x) => x;" + consts
                  + "reportState.open.add('mine:a1#1');"
                  + _function(source, "reportDue") + _function(source, "reportSaid")
                  + _function(source, "reportItemChat") + _function(source, "reportPreparing")
                  + _function(source, "reportPrepWhen") + _function(source, "reportEntry")
                  + "process.stdout.write(reportEntry(" + json.dumps(item) + ", 'ev_1'));")
        ran = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        drawn = ran.stdout
        self.assertIn('data-report-say="ev_1"', drawn, "a reply under it, into the event's chat")
        self.assertIn('data-ref="todo:a1"', drawn)
        self.assertIn("say you did it", drawn)
        self.assertIn('data-report-deliver="run_7"', drawn, "what the agent made, to open")
        self.assertIn("Your agent made these for it:", drawn)
        self.assertNotIn("data-report-yes", drawn, "nothing to approve")
        # Quick replies too (2026-11-13: "这个也要有快速回复（比如i already did this)").
        self.assertIn('data-report-mine-done="todo:a1"', drawn)
        self.assertIn('data-report-mine-do="todo:a1"', drawn)
        # 2026-11-13: "这种先让我expand, 如果我要open email/file/folder有一个单独的button open": an answer read in
        # place, no Open; the file opened with its own button.
        self.assertIn("Nothing to send - it went out on 8 Nov.", drawn)
        self.assertNotIn('data-n="1"', drawn, "no Open for an answer")
        self.assertIn('data-n="0"', drawn)

    def test_by_how_they_stand_each_a_category_folded_by_its_heading(self) -> None:
        """2026-11-13: "根据状态应该有一个分类，允许collapose"; "needs your work 也是一个category, 也是可以collapose的
        (default是展开的)"; the event's own conversation "不需要显示，现在没这个了"."""
        import shutil
        import subprocess
        if not shutil.which("node"):
            self.skipTest("no node here")
        from test_chat_debugger_ui import ESCAPE
        from test_overview_todo_scroll import _function
        source = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        start = source.index("const REPORT_KINDS = ")
        consts = source[start:source.index("function reportDue", start)]
        event = {"id": "ev_1", "thread": {"count": 4}, "items": [
            {"key": "mine:a1", "ref": "todo:a1", "kind": "mine", "title": "Open one", "state": "open"},
            {"key": "mine:a2", "ref": "todo:a2", "kind": "mine", "title": "Gone one", "state": "done",
             "state_said": "No longer on your plate - done or settled."}]}
        script = ("const window=globalThis;" + ESCAPE + "const localStamp = (x) => x;" + consts
                  + "".join(_function(source, name) for name in (
                      "reportDue", "reportSaid", "reportItemChat", "reportPreparing", "reportPrepWhen",
                      "reportPrepEarliest", "reportDoneKeys", "reportEntry", "reportThread",
                      "reportEntriesByState"))
                  + "const ev = " + json.dumps(event) + ";"
                  + "const first = reportEntriesByState(ev) + reportThread(ev);"
                  + "reportState.open.add('ready:ev_1'); reportState.open.add('settled:ev_1');"
                  + "const then = reportEntriesByState(ev);"
                  # the agent's part not done yet: folded at first, the earliest time on its heading
                  + "const prep = {id: 'ev_3', items: [{key: 'mine:p1', ref: 'todo:p1', kind: 'mine', state: 'open',"
                  + " title: 'Read the draft', prep: {state: 'preparing', what: 'Summarise it', status: 'planned',"
                  + " when: '14:25-14:55'}}, {key: 'approve:x', ref: 'held:x', kind: 'approve', state: 'open',"
                  + " title: 'Send the note'}]};"
                  + "const prepFirst = reportEntriesByState(prep);"
                  + "reportState.open.add('preparing:ev_3');"
                  + "const prepOpen = reportEntriesByState(prep);"
                  # all done: shown at first, and its heading folds it all the same
                  + "const allDone = {id: 'ev_2', items: [ev.items[1]]};"
                  + "const doneFirst = reportEntriesByState(allDone);"
                  + "reportState.open.add('settled:ev_2');"
                  + "const doneThen = reportEntriesByState(allDone);"
                  + "process.stdout.write(JSON.stringify({first, then, doneFirst, doneThen, prepFirst, prepOpen}));")
        ran = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        drawn = json.loads(ran.stdout)
        self.assertIn("Needs your work - ready (1)", drawn["first"])
        self.assertIn("Open one", drawn["first"], "open by default")
        self.assertIn("Done or no longer on (1)", drawn["first"])
        self.assertNotIn("Gone one", drawn["first"], "folded by default")
        self.assertNotIn("whole event", drawn["first"], "the event's own conversation not shown")
        self.assertNotIn("Open one", drawn["then"], "folded by its heading")
        self.assertIn("Gone one", drawn["then"])
        self.assertNotIn("I already did this", drawn["then"].split("Gone one")[1], "done: no quick answers")
        self.assertIn("Gone one", drawn["doneFirst"], "nothing else left: shown at first")
        self.assertIn("Done or no longer on (1)", drawn["doneThen"])
        self.assertNotIn("Gone one", drawn["doneThen"], "2026-11-13: \"这个要可以collapse\"")
        # 2026-11-13: "needs your look是agent做绝大多数" - apart from "needs your work"; the agent still
        # preparing its part: folded, the count and the earliest time on its heading.
        self.assertIn("Needs your look (1)", drawn["prepFirst"])
        self.assertIn("Needs your work - your agent is preparing (1)", drawn["prepFirst"])
        self.assertIn("from 14:25", drawn["prepFirst"])
        self.assertNotIn("Read the draft", drawn["prepFirst"], "folded at first")
        self.assertIn("Summarise it", drawn["prepOpen"])
        self.assertIn("today 14:25-14:55", drawn["prepOpen"])
        self.assertIn("data-report-prep-now", drawn["prepOpen"], "told to do it now")

    def test_i_already_did_this_marks_it_done_and_says_so_in_its_thread(self) -> None:
        from support import TemporaryAgent
        from personalized_agent.api.routes import build_routes
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        todo = app.todos.add("Book the meeting room")
        self.assertEqual(app.yours_done(todo["id"]), {"done": True})
        self.assertTrue(app.todos.get(todo["id"])["deleted_at"], "done, and gone from the list")
        self.assertNotIn(todo["id"], [one["id"] for one in app.todos.open()])
        self.assertIn("settled", app.yours_done(todo["id"]), "again: said it was settled already")
        # Through the report: the quick word said in the thread, and done.
        other = app.todos.add("Water the beds")
        item = {"key": "mine:" + other["id"], "ref": "todo:" + other["id"], "kind": "mine",
                "title": "Water the beds"}
        event = {"id": "ev_1", "title": "The garden", "items": [item]}
        app.reports.latest_issue = lambda: {"tabs": {"yours": [event]}}
        said = []
        app.events.answer = lambda ev, words, decide, done, **_k: said.append((ev, words, decide(), done))
        body = {"id": "ev_1", "ref": item["ref"], "how": "already"}
        build_routes(app)[("POST", "/api/report/event/answer")](
            SimpleNamespace(require=body.__getitem__, get=body.get))
        [(ev, words, decided, done)] = said
        self.assertEqual((ev, words, decided), ("ev_1", "I already did this", {"done": True}))
        self.assertIn("Water the beds", done)
        self.assertTrue(app.todos.get(other["id"])["done_at"])

if __name__ == "__main__":
    unittest.main()

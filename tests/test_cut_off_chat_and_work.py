"""AN ANSWER CUT OFF IS NOT AN ANSWER - in chat, work, the lists, the society and the
scheduler (the user, 2026-11-07: "完整计划开始").

A model's answer that stopped at its output limit (stop_reason "max_tokens", or JSON left
open), an empty one and one that is not JSON: nothing derived from it is saved, nothing is
marked done or handled, and a reply shown to the user that was cut says so. Everything here
is made up."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from support import TemporaryAgent

from personalized_agent.agent.core import agent_core
from personalized_agent.models.base import ModelResponse

CUT_NOTE = "(The answer stopped at the length limit.)"
CUT_JSON = '{"reply": "Ada Marlow can join the Harbour survey on Fri'
BROKEN_JSON = '{"reply": "yes", "full_agent": "run",, }'

#: The three ways an answer is not one: (text, stop_reason).
NOT_ANSWERS = {"cut by its word": ('{"groups": []}', "max_tokens"),
               "JSON left open": (CUT_JSON, "end_turn"),
               "empty": ("", "end_turn"),
               "broken JSON": (BROKEN_JSON, "end_turn")}


def _answer(text, stop="end_turn"):
    return ModelResponse(text=text, stop_reason=stop)


# -- chat: the reply, the follow-up, the summary of the conversation so far -------------------
class ChatReplyTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.config.set("display_language", "en")
        self.app.config.set("chat_two_speed", False)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _model(self, text, stop):
        def invoke(request, role="primary"):
            return _answer(text, stop)
        self.app.models.invoke = invoke

    def test_a_reply_cut_off_is_kept_and_says_so(self) -> None:
        self._model("The Harbour survey has three parts: the north quay, the", "max_tokens")
        result = self.app.agent.handle_message("What is in the Harbour survey?")
        self.assertTrue(result["answer"].startswith("The Harbour survey has three parts"))
        self.assertTrue(result["answer"].endswith(CUT_NOTE))
        self.assertTrue(result["cut"])
        saved = self.app.conversations.messages(result["conversation_id"], limit=None)[-1]
        self.assertTrue(saved["content"].endswith(CUT_NOTE), "the kept reply says so too")

    def test_a_whole_reply_has_no_note(self) -> None:
        self._model("The Harbour survey has three parts.", "end_turn")
        result = self.app.agent.handle_message("What is in the Harbour survey?")
        self.assertEqual(result["answer"], "The Harbour survey has three parts.")
        self.assertFalse(result["cut"])

    def test_the_note_in_the_display_language(self) -> None:
        self.app.config.set("display_language", "zh")
        self._model("港口调查有三个部分：北码头、", "length")
        result = self.app.agent.handle_message("港口调查里有什么？")
        self.assertIn("长度上限", result["answer"])
        self.assertNotIn(CUT_NOTE, result["answer"])

    def test_a_follow_up_cut_off_says_so(self) -> None:
        self._model("Ada Marlow leads it.", "end_turn")
        first = self.app.agent.handle_message("Who leads the Harbour survey?")
        self._model("Ada Marlow has led surveys of the", "max_tokens")
        said = self.app.agent.follow_up(first["message_id"], "Ada Marlow", "who is she?")
        self.assertTrue(said["answer"].startswith("Ada Marlow has led surveys of the"))
        self.assertTrue(said["answer"].endswith(CUT_NOTE))


class ChatSummaryOfTheConversationTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _long_conversation(self, turns=20):
        conversation_id = self.app.conversations.create()
        size = agent_core.HISTORY_BUDGET_CHARS // 12
        for index in range(turns):
            self.app.conversations.add_message(conversation_id, "user",
                                               f"Question {index}: " + "q" * size)
            self.app.conversations.add_message(conversation_id, "assistant",
                                               f"Answer {index}: " + "a" * size)
        return conversation_id

    def _summarising(self, *said):
        """The summaries the model gives, in turn: (text, stop_reason) each, the last again."""
        said = list(said)

        def invoke(request, role="primary"):
            if request.task == "chat_history_summary":
                invoke.asked.append(request.messages[0]["content"])
                return _answer(*(said.pop(0) if len(said) > 1 else said[0]))
            invoke.turn = request
            return _answer("the answer")
        invoke.asked = []
        self.app.models.invoke = invoke
        return invoke

    def test_a_summary_cut_off_is_asked_again_of_less_and_kept_so(self) -> None:
        conversation_id = self._long_conversation()
        invoke = self._summarising(("The user asked about the Harbour survey and", "max_tokens"),
                                   ("SUMMARY: the first half, on the Harbour survey.", "end_turn"))
        result = self.app.agent.handle_message("And the next one?", conversation_id)
        self.assertEqual(result["answer"], "the answer")
        self.assertEqual(len(invoke.asked), 2, "asked once more, never the same way")
        self.assertLess(len(invoke.asked[1]), len(invoke.asked[0]))
        self.assertIn("Question 0:", invoke.asked[1])
        kept = self.app.conversations.summary(conversation_id)
        self.assertEqual(kept["summary"], "SUMMARY: the first half, on the Harbour survey.")
        self.assertNotIn("The user asked about the Harbour survey and", invoke.turn.system)
        contents = [one["content"] for one in invoke.turn.messages]
        self.assertTrue(contents[0].startswith(f"Question {kept['covered'] // 2}:"),
                        "what the shorter summary does not cover is given word for word")

    def test_a_summary_cut_off_at_its_shortest_too_is_failed_and_not_kept(self) -> None:
        conversation_id = self._long_conversation()
        invoke = self._summarising(("The user asked about the Harbour survey and", "max_tokens"))
        self.app.agent.handle_message("And the next one?", conversation_id)
        self.assertEqual(len(invoke.asked), 2, "once shorter, then given up")
        self.assertIsNone(self.app.conversations.summary(conversation_id))
        self.assertIn("could not be summarised", invoke.turn.system)

    def test_a_summary_cut_off_leaves_the_one_before_it(self) -> None:
        conversation_id = self._long_conversation()
        self._summarising(("SUMMARY: the Harbour survey, by Ada Marlow.", "end_turn"))
        self.app.agent.handle_message("Next?", conversation_id)
        first = self.app.conversations.summary(conversation_id)
        self.assertEqual(first["summary"], "SUMMARY: the Harbour survey, by Ada Marlow.")
        size = agent_core.HISTORY_BUDGET_CHARS // 6
        for index in range(6):
            self.app.conversations.add_message(conversation_id, "user",
                                               f"More {index}: " + "m" * size)
            self.app.conversations.add_message(conversation_id, "assistant", "ok")
        invoke = self._summarising(("SUMMARY: the Harbour survey, by Ada Marlow, and th",
                                    "max_tokens"))
        self.app.agent.handle_message("Still there?", conversation_id)
        kept = self.app.conversations.summary(conversation_id)
        self.assertEqual((kept["summary"], kept["covered"]), (first["summary"], first["covered"]))
        self.assertIn(first["summary"], invoke.turn.system, "the old summary is still given")


# -- the quick reply ------------------------------------------------------------------------
class QuickReplyTests(unittest.TestCase):

    def _quick(self, text, stop="end_turn"):
        from personalized_agent.agent.two_speed import TwoSpeed
        models = SimpleNamespace(invoke=lambda request, role="": _answer(text, stop))
        agent = SimpleNamespace(_system_prompt=lambda: "You are Melon.")
        return TwoSpeed(agent, None, models, {})._quick("Hello", [], [])

    def test_a_quick_reply_that_cannot_be_read_shows_nothing_and_the_full_agent_answers(
            self) -> None:
        for text in ('{"reply": "Hello! The Harbour survey is', "", BROKEN_JSON):
            with self.subTest(text=text):
                self.assertEqual(self._quick(text), {"reply": "", "run": True})
        self.assertEqual(self._quick("Hello there, I will look into it"),
                         {"reply": "Hello there, I will look into it", "run": True},
                         "words alone, whole, are a reply as before")
        self.assertEqual(self._quick("Hello there, I will look", "max_tokens"),
                         {"reply": "", "run": True})

    def test_a_quick_reply_cut_by_its_word_shows_nothing(self) -> None:
        self.assertEqual(self._quick('{"reply": "Hi", "full_agent": "stop"}', "max_tokens"),
                         {"reply": "", "run": True})

    def test_a_whole_quick_reply_is_shown(self) -> None:
        self.assertEqual(self._quick('{"reply": "Hi Ada.", "full_agent": "stop"}'),
                         {"reply": "Hi Ada.", "run": False})


# -- the lists in groups --------------------------------------------------------------------
class ListGroupsTests(unittest.TestCase):

    ITEMS = [{"id": "t1", "text": "Survey the north quay"},
             {"id": "t2", "text": "Survey the south quay"},
             {"id": "t3", "text": "Call Ada Marlow"}]

    def setUp(self) -> None:
        from personalized_agent.agent.feedback.list_groups import ListGroups
        from personalized_agent.data_manage.storage.database import Database
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Database(Path(self._dir.name) / "agent.db")
        self.said = (json.dumps({"groups": [{"name": "Harbour survey", "items": ["t1", "t2"]},
                                            {"name": "Calls", "items": ["t3"]}]}), "end_turn")
        self.asked = []

        def invoke(request, role=""):
            self.asked.append(request)
            return _answer(*self.said)
        self.groups = ListGroups(self.db, lambda: SimpleNamespace(invoke=invoke),
                                 {"todo": lambda: self.ITEMS, "assigned": lambda: []})

    def tearDown(self) -> None:
        for timer in list(self.groups._timers.values()):
            timer.cancel()
        self.db.close()
        self._dir.cleanup()

    def test_an_answer_that_is_not_one_keeps_the_users_groups_and_moves_on(self) -> None:
        self.groups.group("todo")
        before = self.groups._kept("todo")
        for n, (why, said) in enumerate(NOT_ANSWERS.items()):
            with self.subTest(why=why):
                self.said = said
                items = self.ITEMS + [{"id": f"n{n}", "text": f"Book boat {n}"}]
                self.groups._items["todo"] = lambda items=items: items
                result = self.groups.group("todo")
                self.assertTrue(result.get("not_grouped"))
                kept = self.groups._kept("todo")
                self.assertEqual(kept["groups"], before["groups"],
                                 "not every item put in Others")
                self.assertIn(f"n{n}", kept["seen"], "what was seen saved: not asked again")
                view = self.groups.view("todo", items)
                self.assertEqual(view["new"], [f"n{n}"], "the new item shown as new")
                self.assertNotIn("todo", self.groups._timers)

    def test_not_asked_again_the_same_way_but_when_the_list_changes(self) -> None:
        self.said = NOT_ANSWERS["JSON left open"]
        self.groups.group("todo")
        self.groups.notice("todo", self.ITEMS)
        self.assertNotIn("todo", self.groups._timers, "the same list: not asked again")
        self.groups.notice("todo", self.ITEMS + [{"id": "t5", "text": "Measure the tide"}])
        self.assertIn("todo", self.groups._timers, "a change: grouped again")


# -- who a reply is for, on a mail page ------------------------------------------------------
class SamePersonTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.db = self.agent.app.db

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_an_answer_that_is_not_one_is_failed_and_not_asked_again(self) -> None:
        from personalized_agent.agent.work import handed
        for n, (why, (text, stop)) in enumerate(NOT_ANSWERS.items()):
            with self.subTest(why=why):
                asked = []
                models = SimpleNamespace(invoke=lambda request, role="", t=text, s=stop:
                                         asked.append(request) or _answer(t, s))
                who = [f"ada{n}@marlow.example"]
                threads = {"a": ["Ada Marlow"], "b": ["A. Marlow"]}
                said = handed._the_same_person(self.db, who, threads,
                                               handed.person_judge(models))
                self.assertEqual(said, {"a": "failed", "b": "failed"}, "never a match")
                cut = why in ("cut by its word", "JSON left open")
                self.assertEqual(len(asked), 3 if cut else 1,
                                 "cut off: asked again in halves; else not asked again")
                asked.clear()
                handed._the_same_person(self.db, who, threads, handed.person_judge(models))
                self.assertEqual(asked, [], "kept as failed: not asked the same way again")

    def test_a_cut_answer_asked_in_halves_takes_what_the_halves_answer(self) -> None:
        from personalized_agent.agent.work import handed

        def invoke(request, role=""):
            labels = list(json.loads(request.messages[0]["content"])["threads"])
            if len(labels) > 1:
                return _answer('{"T1": "yes", "T2": "n', "end_turn")
            return _answer(json.dumps({labels[0]: "yes" if labels[0] == "T1" else "no"}))
        said = handed._the_same_person(self.db, ["ada@marlow.example"],
                                       {"a": ["Ada Marlow"], "b": ["Theo Brand"]},
                                       handed.person_judge(SimpleNamespace(invoke=invoke)))
        self.assertEqual(said, {"a": "yes", "b": "no"})

    def test_a_whole_answer_is_kept(self) -> None:
        from personalized_agent.agent.work import handed
        models = SimpleNamespace(invoke=lambda request, role="": _answer('{"T1": "yes"}'))
        said = handed._the_same_person(self.db, ["ada@marlow.example"],
                                       {"a": ["Ada Marlow"]}, handed.person_judge(models))
        self.assertEqual(said, {"a": "yes"})
        self.assertIsNotNone(self.db.query_one(
            "SELECT 1 FROM kv WHERE key LIKE 'reply.same_person.%'"))


# -- the society: messages answered by the user's agent --------------------------------------
class SocietyTests(unittest.TestCase):

    def setUp(self) -> None:
        from test_society import _Model, _Scheduler, _server, _service
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        here = Path(self._dir.name)
        self.transport, self.mailer = _server()
        self.mira = _service(here / "mira", self.transport, {"name": "Mira Holt"})
        self.ada = _service(here / "ada", self.transport, {"name": "Ada Quist"})
        self.said = ("", "end_turn")
        outer = self

        class _Cut(_Model):
            def invoke(self, request, role="primary"):
                if getattr(request, "task", "") == "society_reply":
                    self.asked.append(request)
                    return _answer(*outer.said)
                return super().invoke(request, role)
        self.mira.app.models = self.model = _Cut()
        self.mira.app.scheduler = self.calendar = _Scheduler()
        for one, email in ((self.mira, "mira@example.org"), (self.ada, "ada@example.org")):
            one.publish(listed=False)
            one.email_start(email)
            one.email_confirm(self.mailer.last_code(email))
        self.mira.link(self.ada.identity.id, "p_ada")
        self.mira.interactive.received = lambda message: None

    def tearDown(self) -> None:
        for one in (self.mira, self.ada):
            one.db.close()
        self._dir.cleanup()

    def _ask(self, text):
        draft = self.ada.write_draft(self.mira.identity.id, text, "Friday", written_by="user")
        self.ada.send_draft(draft["id"], draft["hash"])
        self.mira.collect()
        return [one for one in self.mira.conversation(self.ada.identity.id)
                if one["status"] == "received"][-1]

    def _notes(self):
        return [one["text"] for one in self.mira.conversation(self.ada.identity.id)
                if one["status"] == "note"]

    def test_reply_group_an_answer_that_is_not_one_is_recorded_as_not_answered(self) -> None:
        from personalized_agent.common.interface_words import shown
        from personalized_agent.society.interactive import NOT_ANSWERED_NOTE
        for why, said in NOT_ANSWERS.items():
            with self.subTest(why=why):
                self._ask(f"Is Friday good for the Harbour survey? ({why})")
                self.said = said
                asked = len(self.model.asked)
                self.mira.interactive.answer_waiting()
                self.assertEqual(len(self.model.asked), asked + 1)
                self.assertEqual(self.mira.waiting(), {}, "moved on: not asked the same again")
                self.assertEqual(self._notes()[-1], shown(NOT_ANSWERED_NOTE,
                                                          self.mira.app.config))
                self.assertFalse(any("no answer was wanted" in one for one in self._notes()))
                self.mira.interactive.answer_waiting()
                self.assertEqual(len(self.model.asked), asked + 1)

    def test_instant_an_answer_that_is_not_one_sends_the_sorry_and_says_why(self) -> None:
        from personalized_agent.common.interface_words import shown
        from personalized_agent.society.interactive import NOT_TAKEN_NOTE
        self.mira.put_in_group("instant", peer=self.ada.identity.id)
        self.said = ('{"reply": "Yes", "events": [{"title": "Harbour survey walk", "at_lo',
                     "end_turn")
        self.mira.interactive.answer_now(self._ask("Can you put a survey walk on Friday?"),
                                         quick_seconds=5)
        self.assertEqual(self.calendar.made, [])
        self.assertIn(shown(NOT_TAKEN_NOTE, self.mira.app.config), self._notes()[-1],
                      "the events asked for not dropped silently")


# -- work: the agent's answer in a run -------------------------------------------------------
class WorkAnswerTests(unittest.TestCase):

    def _run(self, answers):
        from personalized_agent.agent.work import runner as rn
        run = rn.Run.__new__(rn.Run)
        run.sent, run.notes = [], []

        def call(who, request):
            run.sent.append(json.loads(json.dumps(request.messages, default=str)))
            return answers.pop(0)
        run.__dict__.update(
            id="run-harbour", stage_now="", activity="", stop_now=False, stopping=False,
            owner=SimpleNamespace(save=lambda one: None), _hold=lambda *a, **k: None,
            _going=lambda **k: True, _spent=lambda: 0, _call=call,
            _turn=lambda *a, **k: None, _paused_seconds=lambda: 0.0,
            _note=lambda what, **k: run.notes.append(what))
        return run

    def _tools(self):
        return SimpleNamespace(specs=lambda writing=False: [], guard=None)

    def test_a_cut_answer_said_to_the_model_in_its_next_turn(self) -> None:
        from personalized_agent.agent.work import runner as rn
        run = self._run([_answer('{"done": true, "report": "Surveyed the north qu'),
                         _answer('{"done": true, "report": "Surveyed both quays."}')])
        messages = [{"role": "user", "content": "Survey the harbour."}]
        said = run._ask("system", messages, self._tools(), writing=True)
        self.assertFalse(rn.said_something(said), "its bookkeeping alone is not an answer")
        self.assertTrue(run._last_cut)
        self.assertIn("answer cut off", run.notes)
        messages.append({"role": "user", "content": "GO ON"})
        said = run._ask("system", messages, self._tools(), writing=True)
        self.assertEqual(run.sent[-1][-1]["content"], rn.CUT_TO_MODEL + "\n\nGO ON")
        self.assertEqual(said["report"], "Surveyed both quays.")
        self.assertFalse(run._last_cut)
        messages.append({"role": "user", "content": "GO ON"})
        run._call = lambda who, request: run.sent.append(list(request.messages)) or _answer("{}")
        run._ask("system", messages, self._tools(), writing=True)
        self.assertEqual(run.sent[-1][-1]["content"], "GO ON", "said once")

    def test_cut_by_its_word_too(self) -> None:
        run = self._run([_answer('{"done": true, "report": "Surveyed."}', "max_tokens")])
        run._ask("system", [{"role": "user", "content": "go"}], self._tools(), writing=True)
        self.assertTrue(run._last_cut)

    def test_the_gate_reads_bookkeeping_alone_as_no_summary(self) -> None:
        run = self._run([_answer('{"card": "c1", "claims": [{"claim": "the north q')])
        run._contract_intact = lambda: []
        said = run._ask("system", [{"role": "user", "content": "go"}], self._tools(),
                        writing=True)
        problems = run._gate(said)
        self.assertEqual(len(problems), 1)
        self.assertIn("stopped at the output limit", problems[0])

    def test_a_cut_turn_is_never_taken_as_done(self) -> None:
        from personalized_agent.agent.work import runner as rn
        run = self._run([])

        def ask(system, messages, tools, **kwargs):
            run._local_state().cut = True
            messages.append({"role": "assistant", "content": "{...}"})
            return {"done": True, "report": "Surveyed the north", "written_by": {}}
        run.__dict__.update(
            plan={}, variant=rn.QUICK, monitor=None, _worker_tools=0, stop_reason="",
            _held_for_monitor=0, tools=self._tools(), _ask=ask, _system=lambda: "system",
            _stage=lambda *a, **k: None, _event=lambda *a, **k: None,
            _version=lambda *a, **k: None, _monitored=lambda text: text)
        ended = run._alone_turns([{"role": "user", "content": "Survey the harbour."}])
        self.assertEqual(ended, "stalled", "never complete on a cut answer")
        self.assertEqual({one["progress"] for one in run.plan["rounds"]}, {"cut off"})
        self.assertIn("[cut off at the output limit]", run.plan["last_report"])


# -- the scheduler: a model task, and an event completed behind the user ----------------------
class SchedulerTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.config.set("display_language", "en")

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _task(self, **over):
        from datetime import timedelta
        from personalized_agent.common.events.models import ScheduledTask, utcnow
        fields = dict(title="Harbour survey notes", scheduled_at=utcnow() + timedelta(days=1),
                      execution_type="notification", metadata={"created_by": "calendar_ui"})
        fields.update(over)
        return self.app.scheduler.create_task(ScheduledTask(**fields))

    def test_a_model_task_cut_off_still_ran_and_says_it_was_cut(self) -> None:
        sent = []
        executor = self.app.executor
        executor.models = SimpleNamespace(invoke=lambda request, role="": _answer(
            "The notes for Ada Marlow: the north quay is", "max_tokens"))
        executor.notifications = SimpleNamespace(
            notify=lambda title, message, **kwargs: sent.append(message))
        task = SimpleNamespace(id="t-harbour", title="Harbour survey notes",
                               task_payload={"prompt": "Write the notes", "notify_user": True},
                               description="", metadata={}, created_by="user")
        outcome = executor._model_task(task)
        self.assertTrue(outcome.ok)
        self.assertTrue(outcome.result.endswith(CUT_NOTE))
        self.assertTrue(sent and sent[0].endswith(CUT_NOTE))
        self.assertTrue(outcome.metadata.get("cut"))

    def test_a_whole_model_task_has_no_note(self) -> None:
        executor = self.app.executor
        executor.models = SimpleNamespace(invoke=lambda request, role="": _answer("Done."))
        task = SimpleNamespace(id="t-harbour", title="Notes", task_payload={"prompt": "x"},
                               description="", metadata={})
        self.assertEqual(executor._model_task(task).result, "Done.")

    def test_completion_cut_off_says_so_and_fills_nothing(self) -> None:
        from personalized_agent.scheduler.core import completion
        completer = self.app.task_completer
        task = self._task()
        completer.models.invoke = lambda request, role=None: _answer(
            '{"description": "Notes for the Harbour survey, for Ada Mar')
        completer.complete(task.id)
        stored = self.app.scheduler_store.get(task.id)
        self.assertEqual(completion.state_of(stored), completion.FAILED)
        self.assertIn("cut off at the output limit",
                      stored.metadata[completion.COMPLETION]["note"])
        self.assertFalse(stored.description)

    def test_completion_unreadable_is_nothing_usable(self) -> None:
        from personalized_agent.scheduler.core import completion
        completer = self.app.task_completer
        task = self._task()
        completer.models.invoke = lambda request, role=None: _answer("")
        completer.complete(task.id)
        stored = self.app.scheduler_store.get(task.id)
        self.assertEqual(stored.metadata[completion.COMPLETION]["note"],
                         "the model gave nothing usable")


# -- delegated work: the plan --------------------------------------------------------------
class PlanTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.app.config.set("display_language", "en")

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _planned(self, text, stop="end_turn"):
        def invoke(request, role=""):
            if request.task == "work_draft":
                return _answer(text, stop)
            return _answer("{}")
        self.app.models = SimpleNamespace(invoke=invoke)
        draft = self.app.drafts.create(text="Write up the Harbour survey", collect=False)
        self.app.drafts.collect_and_plan(draft["id"])
        return self.app.drafts.get(draft["id"])["plan"]

    def test_a_plan_cut_off_says_it_could_not_be_written(self) -> None:
        from personalized_agent.agent.work.delegate import PLAN_CUT
        plan = self._planned('{"title": "Harbour survey write-up", "understanding": "A rep')
        self.assertEqual(plan["understanding"], PLAN_CUT)
        self.assertIn("cut off", plan["not_written"])

    def test_a_plan_unreadable_says_so(self) -> None:
        from personalized_agent.agent.work.delegate import PLAN_UNREAD
        for text in ("", "I would survey the harbour."):
            with self.subTest(text=text):
                self.assertEqual(self._planned(text)["understanding"], PLAN_UNREAD)

    def test_a_whole_plan_is_as_written(self) -> None:
        plan = self._planned(json.dumps({"title": "Harbour survey write-up",
                                         "understanding": "A report of the survey."}))
        self.assertEqual(plan["understanding"], "A report of the survey.")
        self.assertNotIn("not_written", plan)


# -- a summary document of chat's answers ---------------------------------------------------
class ChatSummaryDocumentTests(unittest.TestCase):

    def _summarise(self, text, stop, style="plain"):
        from personalized_agent.agent.chat_summary import summarise
        models = SimpleNamespace(invoke=lambda request, role="": _answer(text, stop),
                                 config={"display_language": "en"})
        return summarise(models, ["The Harbour survey has three parts."], style=style)

    def test_plain_cut_off_says_so(self) -> None:
        said = self._summarise("Harbour survey\n\nThree parts: the north", "max_tokens")
        self.assertTrue(said["summary"].endswith(CUT_NOTE))
        self.assertTrue(said["cut"])

    def test_plain_whole_has_no_note(self) -> None:
        said = self._summarise("Harbour survey\n\nThree parts.", "end_turn")
        self.assertEqual(said["summary"], "Harbour survey\n\nThree parts.")

    def test_enriched_left_open_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._summarise('{"title": "Harbour", "blocks": [{"type": "paragraph", "te',
                            "end_turn", style="enriched")


if __name__ == "__main__":
    unittest.main()

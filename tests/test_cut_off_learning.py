"""AN ANSWER CUT OFF IS NOT AN ANSWER, AND IS NOT ASKED AGAIN THE SAME WAY (the user, 2026-11-07:
"完整计划开始"; and then: never the same thing asked again the same way).

A model's answer stopped at its output limit - by its word (`stop_reason`), or with its JSON left
open - an empty one, or one that cannot be read, was read as {} and taken as "nothing to do":
what it was about was marked read, done, seen or judged, with nothing said. Here, at each place
of the heartbeat that learns from an answer: a batch cut off is asked again in halves; a part still
not answered at its smallest is recorded as failed - in the report and on the item - and moved
past; nothing is taken from it, and nothing good is overwritten by it. All data is made up.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.models.base import ModelResponse, ModelUsage

CUT = "max_tokens"


def _content(request) -> str:
    content = request.messages[0]["content"]
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


class _Fake:
    """A model whose answer is a function of what it is asked: text, or (text, stop_reason)."""

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def invoke(self, request, role=""):
        self.asked.append(request)
        said = self.answer(request)
        text, stop = said if isinstance(said, tuple) else (said, "end_turn")
        if not isinstance(text, str):
            text = json.dumps(text)
        return ModelResponse(text=text, stop_reason=stop, usage=ModelUsage(1, 1),
                             model="test-model", provider="test")

    def asked_for(self, words: str) -> int:
        return sum(1 for one in self.asked
                   if words in _content(one) or words in str(getattr(one, "system", "")))


# -- the Monitor's step ----------------------------------------------------------------------
class TheMonitorsStepTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.monitor = self.app.monitor
        self.decider = self.app.monitor_decider

    def _hits(self, n):
        from personalized_agent.heartbeat.monitor import decide
        one = self.monitor.store.list()[0]
        ids = [self.monitor._queue(one, 3, {"activator": {"name": "A garden sale"},
                                            "why": ["made up"],
                                            "window": {"title": f"Seed list {k}"}},
                                   "", datetime.now(timezone.utc))["queued"] for k in range(n)]
        return ids, decide.STEP_TASK

    def _decision(self, hit):
        row = self.app.db.query_one("SELECT judged_at, decision FROM monitor_hits WHERE id = ?",
                                    (hit,))
        return row["judged_at"], json.loads(row["decision"] or "{}")

    def test_a_batch_cut_off_is_asked_again_in_halves(self) -> None:
        ids, task = self._hits(2)

        def answer(request):
            if request.task != task:
                return "{}"
            shown = [one for one in ids if one in _content(request)]
            if len(shown) > 1:
                return '{"decisions": [{"hit": "' + shown[0] + '", "notify": fal', CUT
            return {"decisions": [{"hit": shown[0], "notify": False, "why": "nothing in it"}]}
        self.decider.models = model = _Fake(answer)
        report = self.decider.step(self.monitor)
        self.assertEqual(sum(1 for one in model.asked if one.task == task), 3)
        self.assertEqual(report["judged"], 2)
        self.assertNotIn("failed", report)
        for hit in ids:
            self.assertEqual(self._decision(hit)[1]["why"], "nothing in it")

    def test_unreadable_at_its_smallest_it_is_judged_failed_and_not_asked_again(self) -> None:
        ids, task = self._hits(1)
        self.decider.models = model = _Fake(
            lambda request: "I could not decide." if request.task == task else "{}")
        report = self.decider.step(self.monitor)
        self.assertEqual(report["judged"], 0)
        self.assertEqual(report["failed"][0]["why"], "the answer could not be read")
        judged_at, decision = self._decision(ids[0])
        self.assertTrue(judged_at)
        self.assertEqual(decision, {"notify": False, "failed": "the answer could not be read"})
        self.assertIn("skipped", self.decider.step(self.monitor))
        self.assertEqual(sum(1 for one in model.asked if one.task == task), 1)

    def test_json_left_open_is_cut_off_though_the_model_said_it_ended(self) -> None:
        ids, task = self._hits(1)
        self.decider.models = model = _Fake(
            lambda request: ('{"decisions": [{"hit": "' + ids[0] + '", "notify": true, "tit'
                             if request.task == task else "{}"))
        report = self.decider.step(self.monitor)
        self.assertEqual(report["failed"][0]["why"], "the answer was cut off at the output limit")
        self.assertEqual(self._decision(ids[0])[1].get("failed"),
                         "the answer was cut off at the output limit")
        self.assertEqual(sum(1 for one in model.asked if one.task == task), 1,
                         "one hit: nothing to halve, not asked again")


# -- the Monitor's single calls ----------------------------------------------------------------
class OneCallOfTheMonitorTests(unittest.TestCase):

    def _decider(self):
        from personalized_agent.heartbeat.monitor import decide
        decider = object.__new__(decide.Decider)
        decider.config = {"monitor_recommend_search": False}
        decider.tasks = decider.experience = decider.reserve = None
        decider.signals = SimpleNamespace(store=SimpleNamespace(since=lambda _at: []),
                                          habits_text=lambda: "")
        decider.lessons_for = lambda query: ""
        return decide, decider

    def _monitor(self):
        kept = {}
        return kept, SimpleNamespace(_kv=lambda key: kept.get(key, ""), _set_kv=kept.__setitem__,
                                     say=mock.Mock(return_value={"said": True}),
                                     notifications=None, questions=None,
                                     recommended_today=lambda: 0)

    def test_back_to_an_old_project_cut_off_sends_nothing_and_the_window_is_looked_at(self) -> None:
        decide, decider = self._decider()
        from personalized_agent.heartbeat.monitor import signals as sg
        kept, monitor = self._monitor()
        signals = {"project_id": "p-orchard", "application": "Code.exe", "title": "notes.txt"}
        with mock.patch.object(decider, "_project_brief", return_value="Name: Orchard"), \
                mock.patch.object(decider, "_screen_now", return_value=("the screen", [])), \
                mock.patch.object(decider, "_ask", return_value=decide._said(
                    '{"same": true, "title": "Back at the orch', CUT)) as asked:
            out = decider.old_project(monitor, {"name": "Old project"}, {"signals": signals}, "f1")
        asked.assert_called_once()
        monitor.say.assert_not_called()
        self.assertFalse(out["sent"])
        self.assertEqual(out["failed"], "the answer was cut off at the output limit")
        self.assertNotEqual(out["why"], "the model said it is not that project")
        self.assertTrue(kept.get(sg.OLD_LOOKED.format(sg.old_window_key("Code.exe", "notes.txt"))),
                        "not asked again the same way")

    def test_a_recommendation_with_an_empty_answer_says_it_failed(self) -> None:
        decide, decider = self._decider()
        _kept, monitor = self._monitor()
        with mock.patch.object(decider, "_recent_said", return_value=[]), \
                mock.patch.object(decider, "_contacts_in", return_value=[]), \
                mock.patch.object(decider, "_ask", return_value=decide._said("", "end_turn")):
            out = decider.recommend(monitor, {"name": "Leisure"}, {}, {"signals": {}}, "f1")
        monitor.say.assert_not_called()
        self.assertEqual(out["failed"], "the answer was empty")
        self.assertNotIn("said nothing", out["why"])

    def test_an_evening_report_cut_off_is_not_sent_nor_written_again_that_day(self) -> None:
        decide, decider = self._decider()
        kept, monitor = self._monitor()
        with mock.patch.object(decider, "_ask", return_value=decide._said(
                "Today you worked on the orchard survey and", CUT)) as asked:
            out = decider.write_evening(monitor, {}, "f1")
            self.assertEqual(out["failed"], "the answer was cut off at the output limit")
            monitor.say.assert_not_called()
            day = datetime.now(timezone.utc).astimezone().date().isoformat()
            self.assertEqual(kept[decider._evening_key(day)], decide.EVENING_FAILED)
            one = {"id": "act_evening", "name": "The evening report", "group": "rest"}
            self.assertIn("skipped", decider.evening(monitor, one, {}, "f2", at_once=True))
        asked.assert_called_once()


# -- the reserve of recommendations -------------------------------------------------------------
class TheReserveTests(unittest.TestCase):

    def test_a_look_cut_off_drops_what_waited_with_why_and_is_not_made_again_at_once(self) -> None:
        from personalized_agent.heartbeat.monitor import reserve as rs
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        app.config.set("monitor_recommend_search", False)
        ident = app.reserve.add("Harbour tide tables", "https://tides.example.org/harbour")
        app.monitor_decider.models = model = _Fake(
            lambda request: ('{"items": [{"from": "' + ident + '", "title": "Harbour', CUT)
            if request.task == rs.PREPARE_TASK else "{}")
        self.assertEqual(app.reserve.prepare(app.monitor_decider), 0)
        item = app.reserve.get(ident)
        self.assertEqual(item["status"], rs.DROPPED)
        self.assertIn("cut off", item["why"])
        failed = json.loads(app.reserve._kept(rs.LAST_FAILED))
        self.assertEqual(failed["dropped"], [ident])
        self.assertIn("cut off", failed["why"])
        self.assertFalse(app.reserve.due(), "the clock moved on: not looked again at once")
        self.assertEqual(sum(1 for one in model.asked if one.task == rs.PREPARE_TASK), 1)


# -- the reading log -----------------------------------------------------------------------------
class TheReadingLogTests(unittest.TestCase):

    def test_a_batch_cut_off_is_halved_and_a_piece_still_unread_is_recorded_and_passed(self) -> None:
        from personalized_agent.common.events.models import ListenerEvent
        from personalized_agent.heartbeat.reading.log import UNTIL
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app

        def answer(request):
            prompt = _content(request)
            ids = re.findall(r"^\[(p\d+)\] ([^\n<(]*)", prompt, re.M)
            if "keep: true when" in prompt:
                return {"items": [{"id": ident, "keep": True, "fetch": False}
                                  for ident, _title in ids]}
            if len(ids) > 1:
                return '{"pieces": [{"id": "p0", "kind": "arti', CUT
            if "soup" in ids[0][1]:
                return "Sorry, I lost my place."
            return {"pieces": [{"id": ids[0][0], "kind": "article", "topics": ["physics"],
                                "entities": [], "points": ["a new code"], "summary": "Qubits"}]}
        log = app.reading
        log.models = model = _Fake(answer)
        log.embedder = SimpleNamespace(embed=lambda texts, while_ok=None: [[1.0, 0.0]
                                                                         for _ in texts])
        log.fetch = lambda url: {}
        at = datetime.now(timezone.utc) - timedelta(hours=1)
        app.config.set(UNTIL, (at - timedelta(minutes=1)).isoformat(), internal=True)
        for minutes, url, title in ((0, "https://news.example.org/qubits", "Qubits in a row"),
                                    (5, "https://recipes.example.com/soup", "A winter soup")):
            app.raw_store.add_event(ListenerEvent(
                event_type="browser.navigation", source="test", application="chrome",
                data={"url": url, "title": title}, timestamp=at + timedelta(minutes=minutes)))
        report = log.catch_up()
        self.assertEqual(report["written"], 1)
        self.assertEqual([one["title"] for one in report["failed"]], ["A winter soup"])
        self.assertEqual(report["failed"][0]["why"], "the answer could not be read")
        self.assertEqual([one["title"] for one in log.pieces()], ["Qubits in a row"])
        self.assertGreater(app.config.get(UNTIL), (at + timedelta(minutes=4)).isoformat(),
                           "the mark passed the piece that failed")
        writing = model.asked_for("pieces")
        self.assertEqual(writing, 3, "the pair, then each half")
        log.catch_up()
        self.assertEqual(model.asked_for("pieces"), writing, "not asked again the same way")


# -- the lessons ----------------------------------------------------------------------------------
class TheLessonsTests(unittest.TestCase):

    def test_records_cut_off_are_halved_and_one_unread_at_its_smallest_is_kept_failed(self) -> None:
        from personalized_agent.heartbeat.lessons.engine import LessonEngine
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        first = {}

        def answer(request):
            prompt = _content(request)
            if "give each one the features" not in prompt:
                return "{}"
            ids = re.findall(r"\[(evi_[0-9a-f]+)\]", prompt)
            if len(ids) > 1:
                return '{"features": {"' + ids[0] + '": {"source": "wech', CUT
            if ids[0] == first["id"]:
                return {"features": {ids[0]: {"source": "wechat", "keywords": ["meeting"]}}}
            return "I would rather not."
        engine = LessonEngine(app.db, _Fake(answer), app.config)
        first["id"] = engine.record("notifications", "calact_1", "The user deleted a made-up "
                                    "talk arranged in chat", action="delete")
        second = engine.record("notifications", "calact_2", "The user deleted another made-up "
                               "talk", action="delete")
        rows = [dict(row) for row in app.db.query(
            "SELECT * FROM lesson_evidence WHERE kind = 'notifications' ORDER BY at, rowid")]
        engine._ingest("notifications", rows)
        features = {row["id"]: json.loads(row["features"]) for row in app.db.query(
            "SELECT id, features FROM lesson_evidence WHERE kind = 'notifications'")}
        self.assertEqual(features[first["id"]]["keywords"], ["meeting"])
        self.assertEqual(features[second]["failed"], "the answer could not be read")
        asked = engine.models.asked_for("give each one the features")
        self.assertEqual(asked, 3)
        engine.learn("notifications")
        self.assertEqual(engine.models.asked_for("give each one the features"), asked,
                         "moved past: not asked again")


# -- the experience: natures, joins, principles -----------------------------------------------------
class TheExperienceTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.heartbeat.experience.store import ExperienceStore
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.store = ExperienceStore(self.app.db)

    def _stage(self, task, summary, day):
        return self.store.add_stage({
            "task_id": task, "kind": "evaluation", "summary": summary, "states": [],
            "began": f"2026-09-{day:02d}T10:00:00+00:00",
            "ended": f"2026-09-{day:02d}T11:00:00+00:00",
            "evaluation": {"verdict": "rejected", "words": ["shorter"]}, "work": {}})

    def test_stages_cut_off_are_halved_and_marked_failed_at_their_smallest(self) -> None:
        from personalized_agent.heartbeat.experience.stage_natures import StageNatures
        stages = [self._stage("task-a", "the user cut the made-up abstract", 1),
                  self._stage("task-a", "the user cut the made-up summary", 2)]
        model = _Fake(lambda request: ('{"natures": [{"stages": ["', CUT))
        report = StageNatures(self.app.db, self.app.library, model, "live").write(stages, 10)
        self.assertEqual(len(model.asked), 3, "the pair, then each one")
        self.assertEqual(len(report["failed"]), 2)
        self.assertEqual(report["natures"], 0)
        self.assertEqual(self.store.stages_without_natures(), [], "moved past")

    def test_a_join_cut_off_makes_no_edge_is_marked_and_is_not_asked_again(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Project, Projects
        self._stage("task-a", "the made-up first part", 1)
        self._stage("task-b", "the made-up second part", 2)
        project = Project(id="project-1", name="P", tasks=["task-a", "task-b"])
        Projects(self.app.db).save(project)
        writer = self.app.state_heartbeat.experience.stages_writer()
        writer.models = model = _Fake(lambda request: ('{"edges": [{"from": "', CUT))
        done = writer.joins([project])
        self.assertTrue(all(one.get("failed") for one in done))
        self.assertEqual(self.store.edges(), [])
        asked = len(model.asked)
        self.assertGreater(asked, 0)
        writer.joins([project])
        self.assertEqual(len(model.asked), asked)

    def test_new_patterns_whose_placing_failed_are_not_put_under_by_vector(self) -> None:
        from personalized_agent.heartbeat.experience.patterns import PatternBuilder
        model = _Fake(lambda request: ('{"placed": {"', CUT)
                      if "THE NEW PATTERNS" in _content(request) else "{}")
        builder = PatternBuilder(self.app.db, self.app.library, None, model)
        made = [builder.store.save_pattern({"layer": "pattern", "members": [f"rec-{n}"],
                                            "nature_kind": "work",
                                            "statement": f"made-up pattern {n}"})
                for n in range(3)]
        builder.store.save_pattern({"layer": "principle", "members": [made[0]["id"]],
                                    "nature_kind": "work", "statement": "a made-up principle"})
        report = {"calls": 0}
        builder._principles(made, {}, 10, report)
        self.assertEqual(model.asked_for("THE NEW PATTERNS"), 3, "the pair, then each one")
        self.assertEqual(sorted(one for part in report["failed"] for one in part["patterns"]),
                         sorted(one["id"] for one in made[1:]))
        principle, = builder.store.patterns("principle", kind="work")
        self.assertEqual(principle["members"], [made[0]["id"]], "nothing placed by vector")
        again = {"calls": 0}
        builder._principles(made, {}, 10, again)
        self.assertEqual(model.asked_for("THE NEW PATTERNS"), 3, "not asked again the same way")
        self.assertEqual(sorted(again["held_back"]), sorted(one["id"] for one in made[1:]))


# -- Connections and the newspaper ----------------------------------------------------------------
class TheConnectionsTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.runtime.web_guard import WebGuard
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.store = self.app.connections
        self.WebGuard = WebGuard

    def _round(self, model):
        from personalized_agent.heartbeat.connections.round import ConnectionsRound
        round_ = ConnectionsRound(self.store, model, self.app.db, search=lambda q: {},
                                  fetch=lambda u: {})
        round_.guard = self.WebGuard()
        return round_

    @staticmethod
    def _used():
        return dict(calls=0, web=0, tokens_in=0, tokens_out=0)

    def test_names_tidied_from_a_cut_answer_merge_nothing_and_are_not_asked_again(self) -> None:
        self.store.add("A", by="user", org="NFU")
        self.store.add("B", by="agent", org="Northfield University")
        model = _Fake(lambda request: ('{"groups": [{"kind": "org", "keep": "NF', CUT))
        round_ = self._round(model)
        said = round_._tidy_names(self._used())
        self.assertEqual(said, {"failed": "the answer was cut off at the output limit"})
        self.assertEqual(sorted(one["name"] for one in self.store.names_in_use("org")
                                if one["name"] in ("NFU", "Northfield University")),
                         ["NFU", "Northfield University"], "nothing merged")
        self.assertIn("skipped", round_._tidy_names(self._used()))
        self.assertEqual(len(model.asked), 1)

    def test_your_look_up_cut_off_is_said_to_have_failed_and_not_made_again_at_once(self) -> None:
        me = self.store.me()
        self.store.add_profile(me["id"], "https://me.example")
        model = _Fake(lambda request: ("I looked at your page and found", CUT))
        round_ = self._round(model)
        said = round_._look_me_up(self._used(), lambda: True)
        self.assertEqual(said["failed"], "the answer was cut off at the output limit")
        self.assertEqual(round_._look_me_up(self._used(), lambda: True),
                         {"skipped": "looked up lately"})
        self.assertEqual(len(model.asked), 1)

    def test_a_summary_of_changes_cut_off_folds_nothing_of_its_part(self) -> None:
        from personalized_agent.heartbeat.connections import round as rd
        actions = [{"id": f"act-{n}", "at": f"2026-10-0{n}T10:00:00", "action": "delete",
                    "person_name": f"Made-up person {n}", "reason": "", "after": {}}
                   for n in (1, 2)]
        folded, meta = [], {}
        store = SimpleNamespace(
            lessons=None, overflowing_actions=lambda: [one for one in actions
                                                       if one not in sum(folded, [])],
            lessons_summary=lambda: {"text": ""},
            fold_actions=lambda part, text: folded.append(list(part)),
            meta=lambda key, value=None: meta.__setitem__(key, value) or value
            if value is not None else meta.get(key, ""))

        def answer(request):
            content = _content(request)
            if "Made-up person 1" in content and "Made-up person 2" in content:
                return "The user deleted", CUT
            if "Made-up person 1" in content:
                return "They delete people they never met."
            return "They also", CUT
        model = _Fake(answer)
        round_ = rd.ConnectionsRound(store, model, self.app.db, search=lambda q: {},
                                     fetch=lambda u: {})
        round_.guard = self.WebGuard()
        round_._fold_lessons(self._used())
        self.assertEqual(folded, [[actions[0]]], "only the half answered whole is folded")
        self.assertTrue(meta.get(rd.FOLD_FAILED))
        asked = len(model.asked)
        self.assertEqual(asked, 3)
        round_._fold_lessons(self._used())
        self.assertEqual(len(model.asked), asked, "not asked again the same way")


# -- a lesson package's summary of a file -------------------------------------------------------------
class ALessonPackageSummaryTests(unittest.TestCase):

    def test_a_summary_cut_off_is_not_kept_as_the_files_summary(self) -> None:
        from personalized_agent.heartbeat import lessons_package as lp
        folder = Path(tempfile.mkdtemp(prefix="pa-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        source = folder / "made_up_notes.txt"
        source.write_text("Made-up notes about an orchard survey.\n" * 20, encoding="utf-8")
        packing = object.__new__(lp._Packing)
        packing.folder = folder / "package"
        packing.used = {"calls": 0, "tokens_in": 0, "tokens_out": 0}
        packing.refs_stats = {"reused": 0}
        packing.owner = SimpleNamespace(models=_Fake(
            lambda request: ("# The notes\nThey are about an orch", CUT)), _check=lambda: None)
        stored = {"sha256": "ab" * 32, "path": str(source), "size": source.stat().st_size,
                  "mtime_ns": 0}
        said = packing._summary(stored, {"location": str(source)},
                                [{"owner": "stage", "id": "s1", "summary": "made up"}])
        self.assertIsNone(said)
        self.assertEqual(packing.refs_stats["summary_failed"], 1)
        self.assertFalse((packing.folder / "summaries").exists(), "nothing written for good")


# -- the learning's calls -------------------------------------------------------------------------------
class TheLearningsCallsTests(unittest.TestCase):

    def test_a_cut_or_unreadable_answer_says_it_failed(self) -> None:
        from personalized_agent.learning.ask import Asker
        for text, stop, said in (
                ('{"items": [{"a": 1}', "end_turn",
                 {"_failed": "the answer was cut off at the output limit", "_cut": True}),
                ('{"items": []}', CUT,
                 {"_failed": "the answer was cut off at the output limit", "_cut": True}),
                ("Nothing here.", "end_turn", {"_failed": "the answer could not be read"}),
                ("", "end_turn", {"_failed": "the answer was empty"}),
                ('{"items": []}', "end_turn", {"items": []}),
                ("{}", "end_turn", {})):
            with self.subTest(text=text, stop=stop):
                asker = Asker(_Fake(lambda request, t=text, s=stop: (t, s)))
                self.assertEqual(asker("interests", "system", "made-up content"), said)


if __name__ == "__main__":
    unittest.main()

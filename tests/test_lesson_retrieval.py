"""THE LESSONS RETRIEVED BY WHAT A MOMENT IS ABOUT (the user, 2026-11-07): no quota - every
type ranked together, the asked types a bonus, a floor below which nothing is given; candidate
rules retrieved at a discount and said to be candidates, retired ones never; compared in
English; every caller asking with the content, not the situation; every retrieval recorded
where the Debugger is built in. Everything here is made up."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.heartbeat.lessons import retrieve as rt
from personalized_agent.heartbeat.lessons.retrieve import LessonRetriever

#: What the made-up embedder can tell apart: a text is like another by the topics both name.
TOPICS = ("otter", "kelp", "tide", "lantern", "violin", "tea", "meeting", "ferry", "glacier")


class _Embedder:
    """By meaning, as the tests play it: one dimension per topic named."""

    def available(self):
        return True

    def embed(self, texts):
        return [[1.0 if topic in str(text).lower() else 0.0 for topic in TOPICS] + [0.001]
                for text in texts]


class _Model:
    def __init__(self, answer="The user reads about sea otters in kelp forests."):
        self.answer, self.asked = answer, []

    def invoke(self, request, role="primary"):
        self.asked.append(request)
        return SimpleNamespace(text=self.answer, tool_calls=[], usage=None)


class _Stop(Exception):
    """Raised by a fake once it has seen what it was asked with."""


def _on():
    return mock.patch("personalized_agent.build_flags.debugger_available", lambda: True)


def _off():
    return mock.patch("personalized_agent.build_flags.debugger_available", lambda: False)


class Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.db = self.app.db
        self.engine = self.app.lessons
        for table in ("lesson_rules", "lesson_evidence", "lesson_links", "lesson_retrievals"):
            self.db.execute(f"DELETE FROM {table}")
        self.app.lesson_retriever.embedder = None         # no model to load in these tests
        self.retriever = LessonRetriever(self.engine, _Embedder())

    def _rule(self, kind, statement, state="candidate", cases=0):
        ident = self.engine._new_rule(kind, {"statement": statement}, "x", state=state)
        if state == "active":
            self.db.execute("UPDATE lesson_rules SET confidence = 0.9 WHERE id = ?", (ident,))
        for n in range(cases):
            self.db.execute("INSERT INTO lesson_links (evidence_id, rule_id, relation, at) "
                            "VALUES (?, ?, 'supports', 'x')", (f"e{ident}{n}", ident))
        return ident

    def _example(self, kind, summary, english=""):
        ident = self.engine.record(kind, "s", summary, action="got_it")
        self.db.execute("UPDATE lesson_evidence SET english = ? WHERE id = ?", (english, ident))
        return ident

    def _records(self):
        return [dict(row) for row in self.db.query("SELECT * FROM lesson_retrievals ORDER BY at")]


class NoQuotaTests(Base):

    def test_the_asked_kind_is_not_given_when_it_is_unrelated(self) -> None:
        for n in range(4):
            self._rule("notifications", f"Offer tea in the afternoon, way {n}", "active")
        other = self._rule("connections", "An otter keeper's mail matters", "active")
        found = self.retriever.retrieve(("notifications",), "otters in the harbour", rules=4)
        self.assertEqual([one["id"] for one in found["rules"]], [other],
                         "no half of the room kept for its own kind")

    def test_the_asked_kind_counts_more(self) -> None:
        mine = self._rule("notifications", "Say a word about the otter", "active")
        theirs = self._rule("connections", "Ask the otter keeper", "active")
        found = self.retriever.retrieve(("notifications",), "the otter", rules=2)
        self.assertEqual([one["id"] for one in found["rules"]], [mine, theirs])
        self.assertAlmostEqual(found["rules"][0]["score"],
                               rt.OWN_BONUS * found["rules"][1]["score"], places=3)
        self.assertEqual([one["id"] for one in self.retriever.retrieve(
            ("notifications",), "the otter", rules=1)["rules"]], [mine])

    def test_below_the_floor_fewer_or_none(self) -> None:
        # Nine topics in the moment, one in each lesson: a third alike - under the floor
        # alone, over it with the bonus.
        mine = self._rule("notifications", "When otters show, say so", "active")
        self._rule("connections", "Otter keepers are friends", "active")
        moment = " ".join(TOPICS)
        found = self.retriever.retrieve(("notifications",), moment, rules=8)
        self.assertEqual([one["id"] for one in found["rules"]], [mine], "fewer")
        none = self.retriever.retrieve(("notifications",), "nothing alike", rules=8)
        self.assertEqual((none["rules"], none["examples"]), ([], []), "none")
        self.assertEqual(self.retriever.text(("notifications",), "nothing alike at all"), "")

    def test_by_words_with_its_own_floor(self) -> None:
        self.retriever.embedder = None
        kept = self._rule("notifications", "During a ferry crossing, hold every reminder", "active")
        self._rule("notifications", "Offer tea after long meetings", "active")
        found = self.retriever.retrieve(("notifications",), "on the ferry crossing to the island")
        self.assertFalse(found["by_meaning"])
        self.assertEqual([one["id"] for one in found["rules"]], [kept])
        self.assertTrue(all(one["score"] >= rt.WORDS_FLOOR for one in found["rules"]))


class CandidatesTests(Base):

    def test_candidates_come_at_a_discount_and_are_said_to_be(self) -> None:
        active = self._rule("notifications", "Tell them of the otter", "active")
        candidate = self._rule("notifications", "Mention the otter pups", cases=1)
        found = self.retriever.retrieve(("notifications",), "otter", rules=4)
        scores = {one["id"]: one["score"] for one in found["rules"]}
        self.assertAlmostEqual(scores[candidate], rt.CANDIDATE_SHARE * scores[active], places=3)
        said = self.retriever.text(("notifications",), "otter", rules=4)
        line = [one for one in said.splitlines() if candidate in one][0]
        self.assertIn("a candidate, from 1 case", line)
        self.assertNotIn("a candidate", [one for one in said.splitlines() if active in one][0])

    def test_the_works_candidates_too(self) -> None:
        work = self._rule("work", "Check the tide tables before the field day")
        said = self.retriever.text(("notifications",), "tide", rules=4)
        self.assertIn(work, said)
        self.assertIn("from work: Check the tide tables", said)
        self.assertIn("a candidate, from 0 cases", said)

    def test_retired_and_rejected_never(self) -> None:
        for state in ("retired", "rejected"):
            self._rule("notifications", f"The otter, {state}", state)
            self._rule("work", f"The otter at work, {state}", state)
        self.assertEqual(self.retriever.retrieve(("notifications",), "otter")["rules"], [])


class InEnglishTests(Base):

    def test_a_moment_not_in_english_is_said_in_english_first(self) -> None:
        self._rule("notifications", "When they read about otters, offer a fact", "active")
        model = _Model()
        self.retriever.models = model
        found = self.retriever.retrieve(("notifications",), "海獭在海藻林里", rewrite_task="t1")
        self.assertEqual(len(model.asked), 1)
        self.assertEqual(model.asked[0].task, "t1")
        self.assertEqual(found["query"], "The user reads about sea otters in kelp forests.")
        self.assertEqual(found["moment"], "海獭在海藻林里")
        self.assertEqual(len(found["rules"]), 1)
        self.retriever.retrieve(("notifications",), "海獭在海藻林里", rewrite_task="t1")
        self.assertEqual(len(model.asked), 1, "the same moment: remembered")
        self.retriever.retrieve(("notifications",), "otters in the kelp", rewrite_task="t1")
        self.assertEqual(len(model.asked), 1, "English already: not asked")

    def test_examples_by_their_english_copy_and_how_many_have_none(self) -> None:
        by_english = self._example("notifications", "某个总结", english="A fact about otters")
        self._example("notifications", "Nothing in English here about tea")
        found = self.retriever.retrieve(("notifications",), "otter", examples=4)
        self.assertEqual([one["id"] for one in found["examples"]], [by_english])
        self.assertEqual(found["without_english"], 1)
        self.assertIn("A fact about otters", LessonRetriever._said({"english": "A fact about otters",
                                                                   "summary": "x", "reason": "不要"}))
        self.assertNotIn("不要", LessonRetriever._said({"english": "A", "reason": "不要"}))


class TheQueryIsTheContentTests(Base):
    """Each caller asks with what the thing is about - never the Activator's name or purpose,
    the project's name, the mode or the hour."""

    CONTEXT = {"activator": {"id": "act_x", "name": "Afternoon lantern nudge",
                             "purpose": "suggest a violin break", "group": "rhythm"},
               "window": {"title": "Kelp forests of the north", "application": "browser",
                          "where": "page", "new_text": "Sea otters keep urchins in check"},
               "signals": {"project": "Glacier survey", "mode": "resting", "hour": 15},
               "why": []}
    SITUATION = ("lantern", "violin", "Glacier survey", "resting", "15")

    def _catch(self, decider):
        seen = []

        def lessons_for(query):
            seen.append((query, rt._SITE.get()))
            raise _Stop
        decider.lessons_for = lessons_for
        return seen

    def _content_only(self, query):
        for word in self.SITUATION:
            self.assertNotIn(word, query)

    def test_a_recommendation(self) -> None:
        decider = self.app.monitor_decider
        seen = self._catch(decider)
        with self.assertRaises(_Stop):
            decider.recommend(self.app.monitor, dict(self.CONTEXT["activator"], then=[]), {},
                              json.loads(json.dumps(self.CONTEXT)), "f1")
        query, site = seen[0]
        self.assertIn("Sea otters keep urchins", query)
        self.assertIn("Kelp forests of the north", query)
        self._content_only(query)
        self.assertEqual(site, "monitor recommend")

    def test_back_to_an_old_project(self) -> None:
        self.db.execute(
            "INSERT INTO state_projects (id, name, described, created_at, updated_at) "
            "VALUES ('project_k', 'Glacier survey', ?, 'x', 'x')",
            (json.dumps({"what_it_is": "A count of otters along the coast",
                         "summary": "Two reefs counted, one left"}),))
        decider = self.app.monitor_decider
        seen = self._catch(decider)
        context = dict(self.CONTEXT, signals={"project_id": "project_k", "project": "Glacier survey",
                                              "application": "browser", "title": "x"})
        with self.assertRaises(_Stop):
            decider.old_project(self.app.monitor, self.CONTEXT["activator"], context, "f2")
        query, site = seen[0]
        self.assertEqual(query, "A count of otters along the coast Two reefs counted, one left")
        self.assertEqual(site, "monitor old_project")

    def test_the_monitors_step(self) -> None:
        decider = self.app.monitor_decider
        seen = self._catch(decider)
        hit = {"id": "h1", "context": json.loads(json.dumps(self.CONTEXT)), "score": 0.5,
               "at": "2026-03-01T10:00:00+00:00"}
        monitor = SimpleNamespace(pending_hits=lambda: [hit], _kv=lambda key: "",
                                  bad_ones=lambda most: [],
                                  store=SimpleNamespace(for_the_model=lambda: []))
        with self.assertRaises(_Stop):
            decider.step(monitor)
        self.assertIn("Sea otters keep urchins", seen[0][0])
        self._content_only(seen[0][0])
        self.assertEqual(seen[0][1], "monitor step")
        # Nothing queued: what the bad notifications said.
        bad = {"at": "2026-03-01T10:00:00+00:00", "activator_id": "act_x", "said": "",
               "context": {"title": "Ferry times", "message": "The ferry leaves at noon",
                           "activator": self.CONTEXT["activator"]}}
        monitor.pending_hits, monitor.bad_ones = (lambda: []), (lambda most: [bad])
        with self.assertRaises(_Stop):
            decider.step(monitor)
        self.assertEqual(seen[1][0], "Ferry times The ferry leaves at noon")

    def test_the_reserve(self) -> None:
        seen = []

        def lessons_for(query):
            seen.append((query, rt._SITE.get()))
            raise _Stop
        decider = SimpleNamespace(_tasks=lambda: ["Mapping kelp beds for the survey"],
                                  lessons_for=lessons_for)
        with self.assertRaises(_Stop):
            self.app.reserve.prepare(decider, datetime.now(timezone.utc))
        self.assertEqual(seen[0], ("Mapping kelp beds for the survey", "reserve prepare"))
        decider._tasks = lambda: []
        with self.assertRaises(_Stop):
            self.app.reserve.prepare(decider, datetime.now(timezone.utc))
        self.assertEqual(seen[1][0], "", "nothing to go by: no made-up words")

    def test_the_evidence_for_a_change(self) -> None:
        from personalized_agent.heartbeat.monitor.evidence import Evidence
        asked = []
        retriever = SimpleNamespace(retrieve=lambda *a, **k: asked.append((a, k)) or
                                    {"rules": [], "examples": []})
        monitor = SimpleNamespace(store=SimpleNamespace(get=lambda ident: {
            "name": "Afternoon lantern nudge", "purpose": "say when the tide turns",
            "when": {}}), db=SimpleNamespace(query=lambda *a: []))
        Evidence(monitor, lambda: retriever).look("act_x", "too many tide reminders")
        args, kwargs = asked[0]
        self.assertEqual(args[1], "too many tide reminders say when the tide turns")
        self.assertNotIn("lantern", args[1])
        self.assertEqual(kwargs["site"], "monitor evidence")
        self.assertTrue(kwargs["rewrite_task"])

    def test_a_newspaper_page(self) -> None:
        asked = []
        round_ = self.app.connections_round
        round_.retriever = SimpleNamespace(text=lambda *a, **k: asked.append((a, k)) or "")
        round_._lessons_for({"title": "Kelp · Otters", "leisure": True, "key": "p",
                             "subjects": [{"title": "Kelp"}, {"title": "Otters"}],
                             "detail": ["Urchin barrens spread south"]})
        args, kwargs = asked[0]
        self.assertEqual(args[1], "Kelp · Otters Kelp Otters Urchin barrens spread south")
        self.assertEqual(kwargs["site"], "connections page")
        self.assertTrue(kwargs["rewrite_task"])

    def test_the_contacts_and_a_site_for_each_caller(self) -> None:
        asked = []
        store = self.app.connections
        store.retriever = SimpleNamespace(text=lambda *a, **k: asked.append((a, k)) or "")
        store.lessons_text("Writing the otter survey report", site="connections plan")
        store.lessons_text("Reading about ferries")
        self.assertEqual(asked[0][0][1], "Writing the otter survey report")
        self.assertEqual(asked[0][1]["site"], "connections plan")
        self.assertEqual(asked[1][1]["site"], "connections test_the_contacts_and_a_site_for_each_caller")

    def test_a_list_grouped(self) -> None:
        from personalized_agent.agent.feedback.list_groups import ListGroups
        items = [{"id": "t1", "text": "Count the otters"}, {"id": "t2", "text": "Tune the violin"}]
        model = SimpleNamespace(invoke=lambda request, role="primary": SimpleNamespace(
            text=json.dumps({"groups": [{"name": "All", "items": ["t1", "t2"]}]})))
        groups = ListGroups(self.db, lambda: model, {"todo": lambda: items, "assigned": lambda: []},
                            allowed=lambda: True)
        asked = []
        groups.retriever = SimpleNamespace(the_users_own=lambda *a, **k: asked.append((a, k)) or "")
        groups.group("todo")
        args, kwargs = asked[0]
        self.assertEqual(args[0], "Count the otters Tune the violin")
        self.assertEqual(kwargs["site"], "list grouping todo")


class AtAMomentToSpeakTests(Base):
    """The user, 2026-11-07: of the reasons, attention and motives bear on whether to speak; a
    rule is found by its own examples too; coming back, the query is never the title alone."""

    def _reasons(self, *aspects):
        self.retriever._learned_reasons = lambda: [
            {"id": f"R-{aspect}", "kind": "why they judge so", "state": "active",
             "aspect": aspect, "statement": "The otter matters to them", "confidence": 0.85,
             "pinned": False} for aspect in aspects]

    def test_a_reason_of_the_work_counts_less_at_a_moment_to_speak(self) -> None:
        self._reasons("attention", "output")
        found = {one["id"]: one["score"] for one in self.retriever.retrieve(
            ("notifications", "general"), "the otter", rules=4)["rules"]}
        self.assertAlmostEqual(found["R-output"], rt.OTHER_ASPECT_SHARE * found["R-attention"],
                               places=3)
        found = {one["id"]: one["score"] for one in self.retriever.retrieve(
            ("general",), "the otter", rules=4)["rules"]}
        self.assertAlmostEqual(found["R-output"], found["R-attention"], places=3,
                               msg="elsewhere, as they are")

    def test_a_rule_is_found_by_its_own_examples(self) -> None:
        rule = self._rule("notifications", "When something was asked, check it is not done",
                          "active")
        self.assertEqual(self.retriever.retrieve(("notifications",), "the otter")["rules"], [])
        example = self._example("notifications", "Reminded to send the otter count", "")
        self.db.execute("INSERT INTO lesson_links (evidence_id, rule_id, relation, at) "
                        "VALUES (?, ?, 'supports', 'x')", (example, rule))
        found = self.retriever.retrieve(("notifications",), "the otter")
        self.assertEqual([one["id"] for one in found["rules"]], [rule])

    def test_coming_back_the_query_is_what_they_were_doing_not_the_title_alone(self) -> None:
        self.db.execute(
            "INSERT INTO states (id, title, detail, source, about, starts_at, ends_at, status, "
            "reason, created_at, updated_at, metadata) VALUES ('state_t', 'Counting kelp "
            "otters', 'the harbour tally', 'agent', 'user', '2026-01-01T10:00:00+00:00', "
            "'2026-01-01T11:00:00+00:00', 'active', '', 'x', 'x', '{}')")
        decider = self.app.monitor_decider
        seen = self._catch(decider)
        context = {"activator": {"id": "act_b", "name": "Back after an absence",
                                 "purpose": "where they left off", "group": "recommend"},
                   "window": {}, "signals": {"title": "WeChat", "application": "WeChat.exe"},
                   "why": []}
        with self.assertRaises(_Stop):
            decider.recommend(self.app.monitor, dict(context["activator"], then=[]), {},
                              context, "f1")
        query = seen[0][0]
        self.assertIn("WeChat", query)
        self.assertIn("Counting kelp otters", query)

    _catch = TheQueryIsTheContentTests._catch


class RecordedTests(Base):

    def test_every_retrieval_kept_where_the_debugger_is(self) -> None:
        rule = self._rule("notifications", "Tell them of the otter", "active")
        with _on():
            said = self.retriever.text(("notifications",), "otter", site="a test site")
            self.retriever.retrieve(("notifications",), "nothing alike", site="another")
        kept = self._records()
        self.assertEqual([one["site"] for one in kept], ["a test site", "another"])
        first = kept[0]
        self.assertEqual((first["query"], first["query_english"], first["by_meaning"]),
                         ("otter", "otter", 1))
        results = json.loads(first["results"])
        self.assertEqual([(one["id"], one["kind"], one["type"], one["state"]) for one in results],
                         [(rule, "notifications", "rule", "active")])
        self.assertGreater(results[0]["score"], rt.MEANING_FLOOR)
        self.assertEqual(first["chars_given"], len(said))
        self.assertEqual(json.loads(kept[1]["results"]), [], "none given is recorded too")

    def test_nothing_kept_without_the_debugger(self) -> None:
        self._rule("notifications", "Tell them of the otter", "active")
        with _off():
            self.assertIn("otter", self.retriever.text(("notifications",), "otter", site="s"))
        self.assertEqual(self._records(), [])

    def test_a_site_said_around_the_call_or_found(self) -> None:
        self._rule("notifications", "Tell them of the otter", "active")
        self.app.lesson_retriever.embedder = _Embedder()
        with _on():
            with rt.retrieval_site("around"):
                self.app.calendar_nature.relevant_text("otter")
            self.app.calendar_nature.relevant_text("otter")
            self.app.agent.tools.call("lesson_find", {"moment": "otter"})
        self.assertEqual([one["site"] for one in self._records()],
                         ["around", "test_lesson_retrieval.test_a_site_said_around_the_call_or_found",
                          "chat lesson_find"])

    def test_read_in_the_debugger_newest_first_by_site(self) -> None:
        from personalized_agent.api.routes import build_routes
        rule = self._rule("notifications", "Tell them of the otter", "active")
        with _on():
            self.retriever.retrieve(("notifications",), "otter", site="one")
            self.retriever.retrieve(("notifications",), "the otter again", site="two")

        class _Request:
            def __init__(self, **values):
                self.values = values

            def get(self, key, default=None):
                return self.values.get(key, default)

            def int_param(self, key, default):
                return int(self.values.get(key, default))
        route = build_routes(self.app)[("GET", "/api/debugger/lessons/retrievals")]
        every = route(_Request())
        self.assertEqual([one["site"] for one in every["retrievals"]], ["two", "one"])
        self.assertEqual({one["site"] for one in every["sites"]}, {"one", "two"})
        one = route(_Request(site="one"))["retrievals"]
        self.assertEqual([r["query"] for r in one], ["otter"])
        self.assertEqual(one[0]["results"][0]["id"], rule)
        self.assertEqual(one[0]["results"][0]["said"], "Tell them of the otter")


if __name__ == "__main__":
    unittest.main()

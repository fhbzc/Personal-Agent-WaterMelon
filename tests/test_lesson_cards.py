"""LESSON CARDS: one answered question each, with the evidence it rests on -
never resting on the web, never edited (a new version supersedes), found by
its question. No model is called; the cards are made up."""

from __future__ import annotations

import unittest

from support import TemporaryAgent

from personalized_agent.heartbeat.experience.lesson_cards import (
    LessonCards, facets_of, tier_of, topics_of)


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.cards = LessonCards(self.agent.app.db)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _write(self, question, answer="Use it.", evidence=None, project="garden", **kw):
        return self.cards.write(question, answer,
                                evidence if evidence is not None else
                                [{"type": "user_code", "ref": "src/plot.py", "said": "import altair"}],
                                context={"project": project, "card": "c1"}, **kw)


class WritingTests(_Base):

    def test_kept_as_a_candidate_with_its_tier_facets_and_topics(self) -> None:
        said = self._write("Which charting library does the user use for figures?",
                           "Altair, with one theme for every figure.",
                           evidence=[{"type": "user_code", "ref": "src/plot.py", "said": "import altair"},
                                     {"type": "user_code", "ref": "requirements.txt"}],
                           topics=["Charts", "charts", " Figure  style "])
        self.assertTrue(said["kept"])
        card = said["card"]
        self.assertEqual((card["status"], card["tier"], card["version"]), ("candidate", "user", 1))
        self.assertEqual(card["lineage"], card["id"])
        self.assertEqual(card["facets"], {"files": ["py", "txt"], "stack": ["python"],
                                          "project": "garden"})
        self.assertEqual(card["topics"], ["charts", "figure style"])

    def test_what_rests_on_the_web_even_partly_is_never_kept(self) -> None:
        said = self._write("Which charting library is best?", evidence=[
            {"type": "user_said", "said": "I like clean charts"},
            {"type": "web", "ref": "https://example.org/charts"}])
        self.assertFalse(said["kept"])
        self.assertIn("web", said["why"])
        self.assertEqual(self.cards.cards(), [])

    def test_only_the_models_own_knowledge_is_an_assumption(self) -> None:
        self.assertEqual(tier_of([{"type": "model"}]), "assumption")
        self.assertEqual(tier_of([]), "assumption")
        self.assertEqual(tier_of([{"type": "model"}, {"type": "user_decision"}]), "user")
        said = self._write("How long may a page take to load?", evidence=[
            {"type": "something else"}])
        self.assertEqual(said["card"]["evidence"][0]["type"], "model")
        self.assertFalse(self._write("", "x")["kept"])
        self.assertFalse(self._write("A question?", "")["kept"])

    def test_never_edited_a_new_version_supersedes(self) -> None:
        first = self._write("Which charting library does the user use?")["card"]
        second = self.cards.supersede(first["id"], answer="Altair; matplotlib for papers.")["card"]
        self.assertEqual((second["lineage"], second["version"], second["supersedes"]),
                         (first["id"], 2, first["id"]))
        self.assertEqual(self.cards.get(first["id"])["status"], "superseded")
        self.assertEqual(self.cards.get(first["id"])["answer"], "Use it.", "the old words stay")
        self.assertEqual([one["version"] for one in self.cards.versions(first["id"])], [1, 2])
        self.assertEqual([one["id"] for one in self.cards.cards()], [second["id"]])
        # A superseded version is not changed again.
        self.assertFalse(self.cards.supersede(first["id"], answer="x")["kept"])
        self.assertTrue(self.cards.mark(second["id"], "retired"))
        self.assertEqual(self.cards.cards(), [])
        self.assertFalse(self.cards.mark(second["id"], "kept"))


class FindingTests(_Base):

    def test_found_by_its_question_the_same_project_first(self) -> None:
        charts = self._write("Which charting library does the user use for figures?",
                             project="garden")["card"]
        other = self._write("Which charting library does the user use for figures?",
                            project="bakery")["card"]
        self._write("How are data files named in the archive?")
        found = self.cards.find("What library should draw the charts?", project="bakery")
        self.assertEqual([one["id"] for one in found], [other["id"], charts["id"]])
        self.assertIn("same project", found[0]["why"])
        self.assertTrue(found[0]["why"][0].startswith("question: "))
        self.assertEqual(self.cards.find("the and of what"), [], "no word that tells")
        self.assertEqual(self.cards.find("telescope mirrors"), [])
        self.assertEqual(self.cards.asked_before("charting library", limit=1),
                         ["Which charting library does the user use for figures?"])

    def test_in_chinese_too(self) -> None:
        card = self._write("用户的数据文件怎么命名？")["card"]
        self.assertEqual([one["id"] for one in self.cards.find("数据文件的命名")], [card["id"]])

    def test_what_it_was_used_for_is_recorded(self) -> None:
        card = self._write("Which charting library does the user use?")["card"]
        self.cards.used(card["id"], run_id="run_1", work_card="card_3",
                        decision="figures drawn with Altair", files=["src/fig.py"])
        uses = self.cards.uses(card["id"])
        self.assertEqual([(one["run_id"], one["work_card"], one["files"]) for one in uses],
                         [("run_1", "card_3", ["src/fig.py"])])

    def test_a_scope_of_its_own(self) -> None:
        self._write("Which charting library does the user use?")
        apart = LessonCards(self.agent.app.db, scope="work:dbg_x:full")
        self.assertEqual(apart.cards(), [])
        self.assertEqual(apart.find("charting library"), [])


class TheFacetsTests(unittest.TestCase):

    def test_by_code_from_the_evidence(self) -> None:
        self.assertEqual(facets_of([{"ref": "C:\\work\\site\\package.json"},
                                    {"ref": "https://example.org/a.html"},
                                    {"ref": "notes"}]),
                         {"files": ["json"], "stack": ["node"]})
        self.assertEqual(topics_of([None, "", "A" * 60]), ["a" * 40])


class ShownInTheLessonsDebuggerTests(_Base):

    def test_the_route_and_the_card(self) -> None:
        from pathlib import Path
        from personalized_agent.api.routes import build_routes
        card = self._write("Which charting library does the user use?")["card"]
        self.cards.used(card["id"], run_id="run_1")
        said = build_routes(self.agent.app)[("GET", "/api/lessons/cards")](None)
        self.assertEqual([(one["id"], one["uses"], one["versions"]) for one in said["cards"]],
                         [(card["id"], 1, 1)])
        web = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
        html = (web / "index.html").read_text(encoding="utf-8")
        script = (web / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="lesson-cards"', html)
        self.assertIn('api.get("/api/lessons/cards")', script)
        # The question, the answer and the evidence are never translated.
        self.assertIn(".lesson-card-words", (web / "i18n.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

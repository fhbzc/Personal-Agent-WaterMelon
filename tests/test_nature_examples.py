"""A work nature gathers REAL examples: what the user actually said and did,
pointing at real files and pages. A trait seen again is one more example of
its nature, not a new nature; its words stand until its examples have
changed a lot; and the natures already written as separate ones for one
trait can be merged - in a dry run first."""

from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from support import TemporaryAgent, keyed

from personalized_agent.heartbeat.experience.store import ExperienceStore

ROOT = Path(__file__).resolve().parents[1]


class _Models:
    """A model whose answer depends on what it is asked - and which says
    what it was asked."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = []

    def invoke(self, request, role=""):
        self.asked.append(request)
        text = self.answer(request) if callable(self.answer) else self.answer

        class Reply:
            pass

        reply = Reply()
        reply.text = json.dumps(text) if not isinstance(text, str) else text
        reply.model, reply.provider, reply.usage = "test-model", "test", None
        reply.tool_calls = []
        return reply


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.experience = self.app.state_heartbeat.experience
        self.store = ExperienceStore(self.app.db)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _stage(self, summary, day, references=(), words=()):
        return self.store.add_stage({
            "task_id": "task-a", "kind": "evaluation", "summary": summary,
            "states": [], "began": f"2026-09-{day:02d}T10:00:00+00:00",
            "ended": f"2026-09-{day:02d}T11:00:00+00:00",
            "evaluation": {"verdict": "rejected", "words": list(words)},
            "work": {"references": [
                {"id": f"r{n + 1}", "location": where, "what": f"place {n + 1}"}
                for n, where in enumerate(references)]}})

    def _natures(self):
        from personalized_agent.heartbeat.experience.patterns import nature_records
        return {r.id: r for r in nature_records(self.app.library)}

    def _writer(self, models=None):
        from personalized_agent.heartbeat.experience.stage_natures import StageNatures
        writer = StageNatures(self.app.db, self.app.library, models, "live")
        return writer


class ATraitSeenAgainIsAnExampleTests(_Base):

    def test_same_as_adds_an_example_and_no_new_nature(self) -> None:
        from personalized_agent.heartbeat.experience.stage_natures import examples_of
        first = self._stage("the user sent the referee reply back", 1,
                            ["D:/paper/reply_r1.docx"], ["quote each point in full"])
        writer = self._writer(_Models({"natures": [{
            "stages": [first["id"]], "aspect": "output",
            "title": "The user wants each referee point quoted in full above its reply.",
            "content": "c", "confidence": 5,
            "example": {"said": "quote each point in full",
                        "what": "the reply to the first referee",
                        "references": ["r1"]}}]}))
        self.assertEqual(writer.write([first], budget=5)["natures"], 1)
        (record,) = self._natures().values()

        later = self._stage("the user asked again that each referee point be quoted "
                            "in full in the reply", 9,
                            ["D:/paper/reply_r2.docx", "D:/paper/table3.xlsx",
                             "https://journal.example/ms/42"],
                            ["again - every point, in full, above the answer"])

        def answer(request):
            asked = request.messages[0]["content"]
            # The known nature is shown, with its id and its examples.
            self.assertIn("KNOWN NATURES", asked)
            self.assertIn(f"[{record.id}] output · 1 example", asked)
            return {"natures": [{
                "same_as": record.id, "stages": [later["id"]],
                "example": {"said": "again - every point, in full, above the answer",
                            "what": "the reply to the second referee",
                            "references": ["r1", "r2", "r3"]}}]}

        writer = self._writer(_Models(answer))
        said = writer.write([later], budget=5)
        self.assertEqual((said["natures"], said["examples"]), (0, 1))
        natures = self._natures()
        self.assertEqual(list(natures), [record.id])
        examples = examples_of(natures[record.id])
        self.assertEqual([e["stage"] for e in examples], [first["id"], later["id"]])
        newest = examples[-1]
        self.assertEqual(newest["said"], "again - every point, in full, above the answer")
        self.assertEqual(newest["what"], "the reply to the second referee")
        # Every place it touched, not one.
        self.assertEqual([r["location"] for r in newest["references"]],
                         ["D:/paper/reply_r2.docx", "D:/paper/table3.xlsx",
                          "https://journal.example/ms/42"])
        self.assertEqual(newest["at"][:10], "2026-09-09")
        self.assertEqual(newest["task_id"], "task-a")
        # The lesson is linked to the nature it showed again.
        self.assertEqual(self.store.natures_of_stages([later["id"]])[later["id"]],
                         [record.id])
        # Its words stand - one more case is not a reason to reword it - and
        # the first example is still where it always was.
        kept = natures[record.id]
        self.assertEqual(kept.title, record.title)
        self.assertNotIn("history", kept.metadata)
        self.assertIn("the reply to the first referee",
                      kept.metadata["example"])
        self.assertIn("The user said: \u201cagain - every point", kept.body)
        self.assertIn("  - https://journal.example/ms/42 (place 3)", kept.body)
        # And the nature, opened, shows each case with the user's words.
        opened = self.experience.nature(record.id)
        self.assertEqual(opened["examples_total"], 2)
        top = opened["examples"][0]
        self.assertEqual(top["id"], later["id"])
        self.assertEqual(top["said"], "again - every point, in full, above the answer")
        self.assertEqual(top["label"], "the reply to the second referee")
        self.assertIn("D:/paper/table3.xlsx", [p["location"] for p in top["locations"]])

    def test_a_same_as_naming_a_nature_not_shown_is_not_kept(self) -> None:
        stage = self._stage("a stage", 2, ["C:/w/a.txt"])
        writer = self._writer(_Models({"natures": [{
            "same_as": "rec_not_shown", "stages": [stage["id"]],
            "example": {"said": "x", "references": ["r1"]}}]}))
        said = writer.write([stage], budget=5)
        self.assertEqual((said["natures"], said["examples"]), (0, 0))
        self.assertEqual(self._natures(), {})


class AnExampleIsARealCaseTests(_Base):

    def test_an_example_with_neither_words_nor_references_is_dropped(self) -> None:
        from personalized_agent.heartbeat.experience.stage_natures import make_example
        refs = [{"id": "r1", "location": "C:/w/fig.pdf", "what": "the figure"}]
        self.assertIsNone(make_example({"what": "the figure, redrawn"}, "s", refs))
        # A reference named that the stage does not have is not a place.
        self.assertIsNone(make_example({"what": "x", "references": ["r9"]}, "s", refs))
        self.assertIsNone(make_example("just a description", "s", refs))
        self.assertIsNotNone(make_example({"said": "too busy"}, "s", refs))
        self.assertIsNotNone(make_example({"references": ["r1"]}, "s", refs))
        stage = self._stage("a figure sent back", 3, ["C:/w/fig.pdf"])
        writer = self._writer(_Models({"natures": [{
            "stages": [stage["id"]], "aspect": "evaluation",
            "title": "The user sends back a figure whose point does not read.",
            "content": "c", "example": {"what": "the figure"}}]}))
        self.assertEqual(writer.write([stage], budget=5)["natures"], 0)
        self.assertEqual(self._natures(), {})

    def test_many_references_are_kept(self) -> None:
        from personalized_agent.heartbeat.experience.stage_natures import examples_of
        places = [f"C:/paper/section_{n}.tex" for n in range(1, 8)]
        stage = self._stage("the user rewrote the introduction", 4, places,
                            ["say the finding first"])
        writer = self._writer(_Models({"natures": [{
            "stages": [stage["id"]], "aspect": "output",
            "title": "The user wants each section to open with its finding.",
            "content": "c",
            "example": {"said": ["say the finding first", "then the method"],
                        "what": "the introduction, rewritten",
                        "references": [f"r{n}" for n in range(1, 8)]}}]}))
        self.assertEqual(writer.write([stage], budget=5)["natures"], 1)
        (record,) = self._natures().values()
        (example,) = examples_of(record)
        self.assertEqual([r["location"] for r in example["references"]], places)
        self.assertEqual(example["said"], "say the finding first\nthen the method")
        self.assertEqual(record.metadata["worded_examples"], "1")


class TheWordingStaysStableTests(_Base):

    def test_it_is_reworded_only_past_the_threshold_with_history(self) -> None:
        from personalized_agent.heartbeat.experience.stage_natures import (
            REWORD_LEAST, REWORD_SHARE, add_example, examples_of, history_of,
            make_example, wants_rewording, write_nature)
        self.assertEqual((REWORD_SHARE, REWORD_LEAST), (0.3, 2))
        self.assertFalse(wants_rewording(2, 1))      # one new: not enough
        self.assertTrue(wants_rewording(3, 1))       # two new of three
        self.assertFalse(wants_rewording(12, 10))    # two new of twelve: 17%
        self.assertTrue(wants_rewording(15, 10))     # five of fifteen: 33%
        stages = [self._stage(f"lesson {n}", n, [f"C:/w/{n}.pdf"], [f"words {n}"])
                  for n in range(1, 6)]
        self.assertEqual(write_nature(
            self.app.library, self.store,
            {"title": "Judges a figure by printing it", "aspect": "evaluation",
             "content": "prints it",
             "example": {"said": "words 1", "what": "case 1", "references": ["r1"]}},
            [stages[0]["id"]], {"model": "m", "provider": "p"},
            stages[0]["work"]["references"]), "new")
        asked = []

        def reword(record, examples):
            asked.append(len(examples))
            return {"title": f"Judges every figure on paper ({len(examples)})",
                    "content": "prints them all before saying yes"}

        def record():
            return next(iter(self._natures().values()))

        def add(n):
            stage = stages[n - 1]
            example = make_example({"said": f"words {n}", "what": f"case {n}",
                                    "references": ["r1"]}, stage["id"],
                                   stage["work"]["references"], stage)
            return add_example(self.app.library, self.store, record(), example,
                               [stage["id"]], reword=reword)

        add(2)                                      # 1 new of 2
        self.assertEqual(asked, [])
        self.assertEqual(record().title, "Judges a figure by printing it")
        add(3)                                      # 2 new of 3: worded again
        self.assertEqual(asked, [3])
        now = record()
        self.assertEqual(now.title, "Judges every figure on paper (3)")
        self.assertEqual(now.summary, "prints them all before saying yes")
        self.assertEqual(now.metadata["worded_examples"], "3")
        (earlier,) = history_of(now)
        self.assertEqual(earlier["title"], "Judges a figure by printing it")
        self.assertIn("worded again from its 3 examples", earlier["why"])
        add(4)                                      # 1 new of 4
        add(5)                                      # 2 new of 5 - 40%: again
        self.assertEqual(asked, [3, 5])
        last = record()
        self.assertEqual(len(history_of(last)), 2)
        self.assertEqual([e["said"] for e in examples_of(last)],
                         [f"words {n}" for n in range(1, 6)])
        # Every lesson is linked to it, and its text has every case.
        for stage in stages:
            self.assertEqual(self.store.natures_of_stages([stage["id"]])[stage["id"]],
                             [last.id])
        self.assertEqual(last.body.count("For example:"), 5)


class MergingTheSameNaturesTests(_Base):

    def _setup(self):
        from personalized_agent.data_manage.library import USER_NATURE, Record
        from personalized_agent.heartbeat.experience.stage_natures import write_nature
        made = []
        for n, title in enumerate(["The user wants each referee point quoted in full.",
                                   "The user wants each referee point quoted in full.",
                                   "The user judges a chart on the live page in dark mode."]):
            stage = self._stage(f"lesson {n}", n + 1, [f"D:/paper/{n}.docx"], [f"said {n}"])
            self.assertEqual(write_nature(
                self.app.library, self.store,
                {"title": title, "aspect": "output", "content": f"content {n}",
                 "example": {"said": f"said {n}", "what": f"case {n}",
                             "references": ["r1"]}},
                [stage["id"]], {"model": "m", "provider": "p"},
                stage["work"]["references"]), "new")
            made.append(next(r for r in self._natures().values()
                             if r.id not in {m.id for m in made}))
            # MADE IN ORDER, each at a time of its own. The clock on Windows
            # moves every ~16 ms, so two natures written in one tick had the
            # same time, and "the earliest is kept" fell to their random ids:
            # these tests, which keep made[0], failed one run in five.
            from personalized_agent.common.events.models import utcnow
            written = made[-1].created_at
            while utcnow() <= written:
                import time
                time.sleep(0.002)
        # The second is linked to a state too, from before lessons.
        self.app.db.execute("INSERT INTO nature_links (state_id, record_id, run_id, "
                            "created_at) VALUES ('state_1', ?, '', 'now')", (made[1].id,))
        pattern = self.store.save_pattern({
            "layer": "pattern", "members": [m.id for m in made], "support": 3,
            "statement": "Replies and charts", "nature_kind": "work",
            "written_by": {"summarised": [m.id for m in made], "named_size": 3}})
        other = self.store.save_pattern({
            "layer": "pattern", "members": [made[1].id], "support": 1,
            "statement": "The other kind too", "nature_kind": "work"})
        principle = self.store.save_pattern({
            "layer": "principle", "members": [pattern["id"], other["id"]],
            "support": 4, "statement": "How they judge", "nature_kind": "work"})
        return made, pattern, other, principle

    def test_the_preview_finds_a_set_and_changes_nothing(self) -> None:
        made, pattern, _other, _principle = self._setup()
        files = {p: p.read_text(encoding="utf-8")
                 for p in Path(made[0].path).parent.glob("*.md")}
        said = self.experience.merge_same_natures(dry_run=True)
        self.assertEqual(said["mode"], "similarity")
        self.assertEqual(said["calls"], 0)
        (one,) = said["sets"]
        self.assertEqual((one["pattern"], one["keep"], one["merge"]),
                         (pattern["id"], made[0].id, [made[1].id]))
        self.assertEqual((said["natures"], said["after"]), (3, 2))
        self.assertEqual(files, {p: p.read_text(encoding="utf-8")
                                 for p in Path(made[0].path).parent.glob("*.md")})
        # By the model, a pattern to a call.
        self.experience.models = _Models({"same": [
            {"ids": [made[1].id, made[0].id], "title": "Quotes every point",
             "content": "each referee point above its answer", "why": "one trait"}]})
        said = self.experience.merge_same_natures(dry_run=True, mode="model")
        # One call: the other pattern has one nature, nothing to compare.
        self.assertEqual(said["calls"], 1)
        self.assertEqual(said["sets"][0]["keep"], made[0].id)
        self.assertEqual(said["sets"][0]["title"], "Quotes every point")
        asked = self.experience.models.asked[0].messages[0]["content"]
        self.assertIn("The user said: \u201csaid 0\u201d", asked)

    def test_apply_merges_examples_links_and_patterns(self) -> None:
        from personalized_agent.heartbeat.experience.stage_natures import (
            examples_of, history_of)
        made, pattern, other, principle = self._setup()
        self.experience.models = _Models({"same": [
            {"ids": [made[0].id, made[1].id], "title": "Quotes every point",
             "content": "each referee point above its answer", "why": "one trait"}]})
        self.experience.merge_same_natures(dry_run=True, mode="model",
                                           patterns=[pattern["id"]])
        backup = Path(self.agent.root) / "merge-backup"
        said = self.experience.merge_same_natures(dry_run=False, backup=backup)
        self.assertEqual((said["sets"], said["merged"]), (1, 1))
        natures = self._natures()
        self.assertEqual(set(natures), {made[0].id, made[2].id})
        self.assertFalse(Path(made[1].path).exists())
        self.assertEqual(len(list(backup.glob("*.md"))), 2)
        kept = natures[made[0].id]
        self.assertEqual(kept.title, "Quotes every point")
        self.assertEqual([e["said"] for e in examples_of(kept)], ["said 0", "said 1"])
        self.assertEqual(kept.metadata["worded_examples"], "2")
        (earlier,) = history_of(kept)
        self.assertEqual(earlier["merged_from"], [made[1].id])
        self.assertEqual(earlier["merged"][0]["title"], made[1].title)
        self.assertEqual(earlier["title"], made[0].title)
        # Its lessons and states are the kept nature's now.
        stage_of = {e["stage"] for e in examples_of(kept)}
        for stage_id in stage_of:
            self.assertEqual(self.store.natures_of_stages([stage_id])[stage_id],
                             [made[0].id])
        self.assertEqual([r["record_id"] for r in self.app.db.query(
            "SELECT record_id FROM nature_links WHERE state_id = 'state_1'")],
            [made[0].id])
        self.assertNotIn(made[1].id, self.store.nature_kinds())
        # Every pattern names the kept one instead, once; the support follows.
        rows = {p["id"]: p for p in self.store.patterns()}
        self.assertEqual(rows[pattern["id"]]["members"], [made[0].id, made[2].id])
        self.assertEqual(rows[pattern["id"]]["support"], 2)
        self.assertEqual(rows[pattern["id"]]["written_by"]["summarised"],
                         [made[0].id, made[2].id])
        self.assertEqual(rows[other["id"]]["members"], [made[0].id])
        self.assertEqual(rows[principle["id"]]["support"], 3)
        opened = self.experience.nature(made[0].id)
        self.assertEqual(opened["examples_total"], 2)
        self.assertEqual(opened["history"][0]["merged_from"],
                         [{"id": made[1].id, "title": made[1].title}])
        # Nothing is left to merge.
        self.assertEqual(self.experience.merge_same_natures(dry_run=True)["sets"], [])

    def test_found_by_clustering_then_worded_from_all_its_examples(self) -> None:
        """A set found by similarity has no wording of its own: merging it
        asks the model once to say the trait from every example it holds."""
        made, pattern, _other, _principle = self._setup()
        said = self.experience.merge_same_natures(dry_run=True)
        self.assertEqual(said["sets"][0]["title"], "")
        # The one the preview keeps: the earliest made.
        keep = said["sets"][0]["keep"]
        self.assertEqual(keep, made[0].id)
        self.experience.models = _Models(
            {"title": "The user wants each referee point quoted above its answer.",
             "content": "Seen in both replies."})
        self.experience.merge_same_natures(dry_run=False)
        kept = self._natures()[keep]
        self.assertEqual(kept.title,
                         "The user wants each referee point quoted above its answer.")
        (asked,) = self.experience.models.asked
        self.assertIn("SAY THIS NATURE AGAIN", asked.system)
        self.assertIn("said 0", asked.messages[0]["content"])
        self.assertIn("said 1", asked.messages[0]["content"])

    def test_a_failed_apply_changes_nothing(self) -> None:
        made, pattern, _other, _principle = self._setup()
        texts = {m.path: Path(m.path).read_text(encoding="utf-8") for m in made}
        before = self.app.db.query("SELECT * FROM stage_natures ORDER BY stage_id")
        patterns = self.store.patterns()
        self.experience.merge_same_natures(dry_run=True)
        with mock.patch("personalized_agent.heartbeat.experience.nature_merge.os.remove",
                        side_effect=OSError("held open")):
            with self.assertRaises(OSError):
                self.experience.merge_same_natures(dry_run=False)
        self.assertEqual(texts, {p: Path(p).read_text(encoding="utf-8") for p in texts})
        self.assertEqual(before, self.app.db.query(
            "SELECT * FROM stage_natures ORDER BY stage_id"))
        self.assertEqual(patterns, self.store.patterns())
        self.assertIn(made[1].id, self.store.nature_kinds())

    def test_a_set_that_is_not_one_is_refused(self) -> None:
        made, _pattern, _other, _principle = self._setup()
        with self.assertRaises(ValueError):
            self.experience.merge_same_natures(
                dry_run=False, sets=[{"keep": made[0].id, "merge": ["rec_gone"]}])
        with self.assertRaises(ValueError):
            self.experience.merge_same_natures(dry_run=False, sets=[
                {"keep": made[0].id, "merge": [made[1].id]},
                {"keep": made[2].id, "merge": [made[1].id]}])
        self.assertEqual(len(self._natures()), 3)


class TheMergeIsOfferedOverTheApiTests(MergingTheSameNaturesTests):
    """GET merge_preview is the dry run; POST merge applies it."""

    def setUp(self) -> None:
        super().setUp()
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.app.config.set("api_port", port)
        from personalized_agent.api.server import ApiServer
        self.server = ApiServer(self.app)
        self.server.start()
        self.base = f"http://127.0.0.1:{port}"

    def tearDown(self) -> None:
        self.server.stop()
        super().tearDown()

    def _call(self, path, payload=None):
        import urllib.request
        request = urllib.request.Request(
            f"{self.base}{path}", method="GET" if payload is None else "POST",
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(keyed(self.app, request), timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_preview_then_merge(self) -> None:
        import urllib.error
        made, _pattern, _other, _principle = self._setup()
        with self.assertRaises(urllib.error.HTTPError) as refused:
            self._call("/api/experience/natures/merge", {})
        self.assertEqual(refused.exception.code, 409)
        said = self._call("/api/experience/natures/merge_preview")
        self.assertEqual([s["merge"] for s in said["sets"]], [[made[1].id]])
        self.assertEqual(len(self._natures()), 3)
        said = self._call("/api/experience/natures/merge", {})
        self.assertEqual(said["merged"], 1)
        self.assertTrue(Path(said["backup"]).is_dir())
        self.assertEqual(set(self._natures()), {made[0].id, made[2].id})

    # The service's own tests are not run again here.
    test_the_preview_finds_a_set_and_changes_nothing = None
    test_apply_merges_examples_links_and_patterns = None
    test_a_failed_apply_changes_nothing = None
    test_a_set_that_is_not_one_is_refused = None


def _engine():
    from test_panels import _javascript_engine
    return _javascript_engine()


class TheExamplesAreShownWithTheUsersWordsTests(unittest.TestCase):
    """A nature, clicked in How you work, shows each example with the
    user's own words quoted, its short label and every place it touched -
    newest first, the count in the header; its row says how many."""

    def setUp(self) -> None:
        self.kind, self.engine = _engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _run(self, nature) -> dict:
        now = datetime.now(timezone.utc)
        tree = {"kinds": [{"kind": "work", "principles": [{
            "id": "pr_1", "statement": "Judges on the real thing",
            "last_active": (now - timedelta(days=1)).isoformat(),
            "stretches": [[(now - timedelta(days=3)).isoformat(),
                           (now - timedelta(days=1)).isoformat()]],
            "patterns": [{"id": "p1", "statement": "Replies to referees",
                          "last_active": (now - timedelta(days=1)).isoformat(),
                          "members": [{"id": "rec_9", "examples": 3,
                                       "title": "Quotes each referee point"}]}]}],
            "unplaced": []}]}
        stub = {"/api/experience/nature": nature}
        steps = (
            "ovState.recentDays=0;renderPrinciples(TREE);"
            "document.querySelector('#ov-principles [data-ov-principle=\"pr_1\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));"
            "const card=document.getElementById('ov-principle-card');"
            "card.querySelector('details[data-ov-pattern=\"p1\"]').open=true;await wait();"
            "out.row=card.querySelector('details[data-ov-pattern=\"p1\"] li')"
            "  .textContent.replace(/\\s+/g,' ');"
            "card.querySelector('[data-ov-nature=\"rec_9\"]')"
            "  .dispatchEvent(new MouseEvent('click',{bubbles:true}));await wait();"
            "const box=card.querySelector('[data-xp-nature-detail=\"rec_9\"]');"
            "out.text=box.textContent.replace(/\\s+/g,' ');"
            "out.quotes=[...box.querySelectorAll('.xp-nx-said blockquote')]"
            "  .map(q=>q.textContent);"
            "out.rows=[...box.querySelectorAll('.xp-nature-example-row')].map(r=>({"
            "  id:r.dataset.xpExample,"
            "  places:[...r.querySelectorAll('.xp-nx-places code')].map(c=>c.textContent),"
            "  label:(r.querySelector('.xp-nx-label')||{}).textContent||''}));")
        web = ROOT / "personalized_agent" / "api" / "web"
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (web / "index.html").read_text(encoding="utf-8"))
        harness = (
            "<style>" + (web / "styles.css").read_text(encoding="utf-8") + "</style>"
            "<script>window.__asked=[];const STUB=" + json.dumps(stub) + ";"
            "const TREE=" + json.dumps(tree) + ";"
            "window.fetch=(u)=>{const p=String(u).split('?')[0];window.__asked.push(String(u));"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (web / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';const out={};"
            "const wait=()=>new Promise(r=>setTimeout(r,60));"
            "try{" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err,out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 "--window-size=1400,900",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "the page never rendered")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "", result["err"])
        return result["out"]

    def test_each_example_shows_the_users_words_and_every_place(self) -> None:
        def case(n, said, places):
            return {"source": "stage", "id": f"stage_{n}", "kind": "evaluation",
                    "title": f"Lesson {n}", "what": f"the reply, round {n}",
                    "label": f"the reply, round {n}", "said": said,
                    "summary": "", "project": "The paper", "task": "Reply",
                    "began": f"2026-09-0{n}T10:00:00+00:00",
                    "ended": f"2026-09-0{n}T11:00:00+00:00", "deliverable": "",
                    "own_example": True,
                    "locations": [{"location": one, "what": ""} for one in places]}
        nature = {"id": "rec_9", "title": "Quotes each referee point",
                  "summary": "each point above its answer", "body": "b",
                  "source": "experience", "created_at": "2026-09-01T10:00:00+00:00",
                  "pattern": {"id": "p1", "statement": "Replies to referees"},
                  "principle": {}, "lessons": [], "history": [],
                  "examples": [
                      case(3, "every point, in full\nabove the answer",
                           ["D:/paper/reply_r2.docx", "D:/paper/table3.xlsx",
                            "https://journal.example/ms/42"]),
                      case(2, "quote them", ["D:/paper/reply_r1.docx"]),
                      case(1, "", ["D:/paper/notes.md", "D:/paper/reply_0.docx"])],
                  "examples_total": 3}
        out = self._run(nature)
        self.assertIn("3 examples", out["row"])
        self.assertIn("Examples (3):", out["text"])
        # Newest first, each with its words quoted and its label.
        self.assertEqual([r["id"] for r in out["rows"]], ["stage_3", "stage_2", "stage_1"])
        self.assertEqual(out["quotes"], ["\u201cevery point, in full\u201d",
                                         "\u201cabove the answer\u201d",
                                         "\u201cquote them\u201d"])
        self.assertIn("For example: the reply, round 3", out["rows"][0]["label"])
        # Every place, not one.
        self.assertEqual(out["rows"][0]["places"],
                         ["D:/paper/reply_r2.docx", "D:/paper/table3.xlsx",
                          "https://journal.example/ms/42"])
        self.assertEqual(out["rows"][2]["places"],
                         ["D:/paper/notes.md", "D:/paper/reply_0.docx"])
        self.assertIn("You said:", out["text"])


if __name__ == "__main__":
    unittest.main()

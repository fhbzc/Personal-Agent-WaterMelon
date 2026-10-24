"""LEARNING (personalized_agent/learning/): the shared words, what is never
sent or kept, the agents' records, the places, whether a reason's condition
covers a job, reading a unit into cases and judgments, the reasons and how
they are revised, the update's clock, and what a reviewer is given. No model
is called: each model answer is made up here, and so is everything read -
fictional projects (a grant on coastal erosion, a restored motorcycle)."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from personalized_agent.data_manage.storage.database import Database
from personalized_agent.learning import records
from personalized_agent.learning.find import Finder
from personalized_agent.learning.match import coverage, fits_its_cases
from personalized_agent.learning.places import Places
from personalized_agent.learning.reading import UnitReader
from personalized_agent.learning.reasons import Reasons
from personalized_agent.learning.scrub import scrub
from personalized_agent.learning.service import LAST_SLOT, Learning
from personalized_agent.learning.store import LearningStore
from personalized_agent.learning.vocab import Vocabulary

#: These tests make their made-up projects in the system's folder for passing
#: files, which learning never reads: here it is a folder of its own instead.
_PASSING = Path(tempfile.gettempdir()) / "made-up-passing-files"
_temp_roots = mock.patch.object(records, "_temp_roots", lambda: [str(_PASSING)])


def setUpModule() -> None:
    _temp_roots.start()


def tearDownModule() -> None:
    _temp_roots.stop()


class _Config:
    """Settings in memory: what a test sets, else the default asked for."""

    def __init__(self, **values) -> None:
        self.values = dict(values)

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value, internal=False):
        self.values[key] = value
        return value

    def all(self):
        return dict(self.values)


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="learning-"))
        self.db = Database(self.dir / "agent.db")
        self.store = LearningStore(self.db)
        self.vocab = Vocabulary(self.db)
        self.vocab.ensure_fixed()

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def case(self, title, **facets):
        return self.store.save_case({"unit_id": "u1", "title": title, "facets": facets})

    def judgment(self, case_id, obj="object:amount of text", direction="less", **more):
        row = {"case_id": case_id, "aspect": "output", "object": obj, "direction": direction,
               "strength": "changed", "at": "2026-05-01", "state": "new"}
        row.update(more)
        return self.store.save_judgment(row)


class _Answers:
    """A model that answers each call with the next of its answers - and
    keeps what it was asked."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.asked = []

    def __call__(self, kind, system, content, max_tokens=4000, what=""):
        self.asked.append((kind, system, content))
        return self.answers.pop(0) if self.answers else {}


class TheSharedWordsTests(_Base):

    def test_a_term_added_is_on_trial_until_a_second_case_uses_it(self) -> None:
        tag = self.vocab.add("object", "accent colour on figures", "object:colour",
                             "one colour picks out the point")
        self.assertEqual(tag, "object:accent colour on figures")
        self.assertEqual(self.vocab.by_id()[tag]["state"], "trial")
        self.vocab.used([tag], "C-1")
        self.assertEqual(self.vocab.by_id()[tag]["state"], "trial")
        self.vocab.used([tag], "C-2")
        self.assertEqual(self.vocab.by_id()[tag]["state"], "kept")

    def test_no_term_without_a_parent_of_its_own_dimension(self) -> None:
        self.assertEqual(self.vocab.add("object", "anything", "deliverable:slides"), "")
        self.assertEqual(self.vocab.add("object", "anything", "object:no such"), "")

    def test_how_terms_stand_to_one_another(self) -> None:
        self.assertEqual(self.vocab.relation("deliverable:slides", "deliverable:slides"), "same")
        self.assertEqual(self.vocab.relation("deliverable:slides", "deliverable:visual material"),
                         "under")
        self.assertEqual(self.vocab.relation("deliverable:slides", "deliverable:poster"), "sibling")
        self.assertEqual(self.vocab.relation("deliverable:slides", "deliverable:email"), "")

    def test_a_merged_term_reads_as_what_it_became(self) -> None:
        one = self.vocab.add("object", "slide words", "object:amount of text")
        two = self.vocab.add("object", "text per slide", "object:amount of text")
        self.assertTrue(self.vocab.merge(one, two))
        self.assertEqual(self.vocab.resolve(one), two)
        self.assertFalse(self.vocab.merge("object:colour", two), "a fixed term never goes")

    def test_a_trial_term_is_folded_back_only_after_its_time_and_its_chances(self) -> None:
        tag = self.vocab.add("object", "rare thing", "object:colour")
        old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        self.db.execute("UPDATE learn_terms SET created_at = ? WHERE id = ?", (old, tag))
        self.assertEqual(self.vocab.prune(first_learning=False), [], "too few chances yet")
        case_id = self.case("A grant talk")
        for _ in range(100):
            self.judgment(case_id, obj="object:colour")
        self.assertEqual(self.vocab.prune(first_learning=True), [],
                         "never while a place is read the first time")
        self.assertEqual(self.vocab.prune(first_learning=False), [tag])
        self.assertEqual(self.vocab.resolve(tag), "object:colour")


class WhatIsNeverSentTests(unittest.TestCase):

    def test_keys_and_passwords_go_and_paths_and_commits_stay(self) -> None:
        text = ("password = hunter2hunter2\nAPI_KEY=abcdef0123456789\n"
                "token: sk-abcdefghijklmnop1234\n"
                "C:/Users/someone/grant-2025/talk/defense_final.pptx at commit "
                "3f9a1c2b7d4e5f60718293a4b5c6d7e8f9012345")
        clean, removed = scrub(text)
        self.assertGreaterEqual(removed, 3)
        self.assertNotIn("hunter2hunter2", clean)
        self.assertNotIn("abcdef0123456789", clean)
        self.assertNotIn("sk-abcdefghijklmnop1234", clean)
        self.assertIn("grant-2025/talk/defense_final.pptx", clean)
        self.assertIn("3f9a1c2b7d4e5f60718293a4b5c6d7e8f9012345", clean)


class TheAgentsRecordsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="records-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, name, rows):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        return path

    def test_claude_code_the_users_words_the_agents_edits_and_what_it_read(self) -> None:
        work = str(self.dir / "coastal-grant")
        path = self._write("claude/proj/s1.jsonl", [
            {"type": "user", "timestamp": "2026-05-01T10:00:00Z", "cwd": work,
             "message": {"role": "user", "content": "Make the budget slide a table."}},
            {"type": "user", "timestamp": "2026-05-01T10:00:01Z", "cwd": work,
             "message": {"role": "user", "content": "<system-reminder>not the user</system-reminder>"}},
            {"type": "assistant", "timestamp": "2026-05-01T10:01:00Z", "cwd": work,
             "message": {"role": "assistant", "content": [
                 {"type": "text", "text": "Done."},
                 {"type": "tool_use", "name": "Edit", "input": {
                     "file_path": work + "/talk/notes.md", "old_string": "a", "new_string": "| item | cost |"}},
                 {"type": "tool_use", "name": "Read", "input": {"file_path": work + "/budget.xlsx"}}]}},
            {"type": "user", "timestamp": "2026-05-01T10:01:01Z", "cwd": work,
             "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
        ])
        session = records.read_session(path)
        self.assertEqual(session.tool, "claude")
        self.assertEqual(session.cwd, work)
        said = [turn.text for turn in session.turns if turn.role == "user"]
        self.assertEqual(said, ["Make the budget slide a table."])
        self.assertEqual(len(session.edits), 1)
        self.assertIn("| item | cost |", session.edits[0].added)
        self.assertTrue(any(one.endswith("budget.xlsx") for one in session.touched))

    def test_codex_its_directory_its_words_and_a_patch(self) -> None:
        work = str(self.dir / "motorcycle")
        patch = ("*** Begin Patch\n*** Update File: parts.md\n@@\n-old\n+carburettor rebuilt\n"
                 "*** End Patch")
        path = self._write("codex/2026/05/01/rollout-x.jsonl", [
            {"timestamp": "2026-05-01T09:00:00Z", "type": "session_meta",
             "payload": {"id": "abc", "cwd": work}},
            {"timestamp": "2026-05-01T09:00:01Z", "type": "response_item",
             "payload": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": "List the parts still missing."}]}},
            {"timestamp": "2026-05-01T09:00:02Z", "type": "response_item",
             "payload": {"type": "custom_tool_call", "name": "apply_patch", "input": patch}},
        ])
        session = records.read_session(path)
        self.assertEqual(session.tool, "codex")
        self.assertEqual(session.cwd, work)
        self.assertEqual([turn.text for turn in session.turns], ["List the parts still missing."])
        self.assertEqual(len(session.edits), 1)
        self.assertTrue(session.edits[0].file.endswith("parts.md"))
        self.assertEqual(session.edits[0].added, "carburettor rebuilt")
        self.assertFalse(session.by_program)

    def test_what_a_program_started_is_told_apart(self) -> None:
        work = str(self.dir / "motorcycle")
        person = self._write("claude/p/a.jsonl", [
            {"type": "user", "timestamp": "2026-05-01T10:00:00Z", "cwd": work,
             "entrypoint": "cli", "message": {"role": "user", "content": "Tidy the parts list."}}])
        program = self._write("claude/p/b.jsonl", [
            {"type": "user", "timestamp": "2026-05-01T10:00:00Z", "cwd": work,
             "entrypoint": "sdk-cli", "message": {"role": "user", "content": "Group these states."}}])
        exec_run = self._write("codex/2026/05/01/rollout-e.jsonl", [
            {"timestamp": "2026-05-01T09:00:00Z", "type": "session_meta",
             "payload": {"id": "e", "cwd": work, "originator": "codex_exec", "source": "exec"}}])
        tui = self._write("codex/2026/05/01/rollout-t.jsonl", [
            {"timestamp": "2026-05-01T09:00:00Z", "type": "session_meta",
             "payload": {"id": "t", "cwd": work, "originator": "codex-tui", "source": "vscode"}}])
        self.assertFalse(records.read_session(person).by_program)
        self.assertTrue(records.read_session(program).by_program)
        self.assertTrue(records.read_session(exec_run).by_program)
        self.assertFalse(records.read_session(tui).by_program)


class ThePlacesTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="places-"))
        self.app_data = self.dir / "agent-data"
        self.app_data.mkdir()
        self.project = self.dir / "work" / "coastal-grant"
        (self.project / "talk").mkdir(parents=True)
        (self.project / "README.md").write_text("A grant on coastal erosion.", encoding="utf-8")
        self.config = _Config()
        self.places = Places(self.config, never=[self.app_data])

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_folder_is_listed_once_and_the_apps_own_never(self) -> None:
        self.places.add(self.project)
        self.places.add(self.project)
        self.assertEqual(len(self.places.listed()), 1)
        with self.assertRaises(ValueError):
            self.places.add(self.app_data)
        with self.assertRaises(ValueError):
            self.places.add(self.dir / "no such folder")

    def test_a_record_folder_brings_the_project_its_conversations_worked_in(self) -> None:
        session = records.Session(path="s1.jsonl", tool="claude", id="s1",
                                  cwd=str(self.project / "talk"),
                                  touched=[str(self.project / "talk" / "slides.md")])
        own = records.Session(path="s2.jsonl", tool="claude", id="s2",
                              cwd=str(self.app_data / "runs"))
        with mock.patch.object(records, "all_sessions", lambda tools=(): [session, own]), \
                mock.patch.object(records, "claude_sessions_root", lambda: self.dir):
            self.places.add(self.dir, "claude")
            found = self.places.found_folders()
        self.assertEqual([Path(one["path"]) for one in found], [self.project],
                         "its project's root, by its README - never the app's own runs")
        self.assertEqual(found[0]["via"], ["claude"])
        self.places.set_excluded(self.project, True)
        with mock.patch.object(records, "all_sessions", lambda tools=(): [session]):
            self.assertEqual(self.places.found_folders(), [], "left out")

    def test_never_a_conversation_a_program_started_nor_one_in_passing_files(self) -> None:
        mine = records.Session(path="s1.jsonl", tool="claude", id="s1",
                               cwd=str(self.project))
        by_program = records.Session(path="s2.jsonl", tool="claude", id="s2",
                                     cwd=str(self.project), by_program=True)
        passing = records.Session(path="s3.jsonl", tool="claude", id="s3",
                                  cwd=str(_PASSING / "pa-copy" / "root" / "workspace"))
        with mock.patch.object(records, "all_sessions",
                               lambda tools=(): [mine, by_program, passing]):
            self.assertEqual([one.id for one in self.places.sessions()], ["s1"])

    def test_a_folder_brings_the_conversations_that_worked_in_it(self) -> None:
        inside = records.Session(path="a", tool="codex", id="a", cwd=str(self.dir),
                                 touched=[str(self.project / "README.md")])
        outside = records.Session(path="b", tool="codex", id="b", cwd=str(self.dir / "elsewhere"))
        found = self.places.sessions_in(self.project, [inside, outside])
        self.assertEqual([one.id for one in found], ["a"])


class CoverageTests(_Base):

    def test_covered_near_unknown_outside_and_excepted(self) -> None:
        terms = self.vocab.by_id()
        condition = {"dims": {"deliverable": ["deliverable:visual material"],
                              "presentation": ["presentation:presented in person"]},
                     "excluded": {"purpose": ["purpose:examine together"]}}
        job = {"deliverable": ["deliverable:slides"],
               "presentation": ["presentation:presented in person"],
               "purpose": ["purpose:persuade or defend"]}
        self.assertEqual(coverage(condition, job, self.vocab, terms)[0], "covered")
        self.assertEqual(coverage(condition, dict(job, purpose=["purpose:examine together"]),
                                  self.vocab, terms)[0], "outside")
        self.assertEqual(coverage(condition, {"deliverable": ["deliverable:slides"]},
                                  self.vocab, terms)[0], "unknown")
        narrow = {"dims": {"deliverable": ["deliverable:slides"]}}
        self.assertEqual(coverage(narrow, {"deliverable": ["deliverable:poster"]},
                                  self.vocab, terms)[0], "near")
        self.assertEqual(coverage(narrow, {"deliverable": ["deliverable:email"]},
                                  self.vocab, terms)[0], "outside")

    def test_a_condition_is_widened_to_hold_for_its_own_cases(self) -> None:
        terms = self.vocab.by_id()
        condition = {"dims": {"deliverable": ["deliverable:slides"]}}
        widened = fits_its_cases(condition, [{"deliverable": ["deliverable:slides"]},
                                             {"deliverable": ["deliverable:poster"]}],
                                 self.vocab, terms)
        self.assertEqual(widened["dims"]["deliverable"], ["deliverable:visual material"])


class ReadingTests(_Base):

    def setUp(self) -> None:
        super().setUp()
        self.folder = self.dir / "coastal-grant"
        self.folder.mkdir()
        (self.folder / "notes.md").write_text("# Talk\nthree paragraphs of text\n", encoding="utf-8")
        self.unit = {"id": "place:1", "kind": "place", "key": str(self.folder),
                     "title": "Coastal grant", "folders": [str(self.folder)], "marks": {}}
        self.store.save_unit(self.unit)

    def _reader(self, ask):
        return UnitReader(self.db, ask, self.dir / "copies", Places(_Config()), never=[])

    def test_only_the_users_judgments_kept_their_words_only_where_shown(self) -> None:
        answer = {
            "cases": [{"ref": "new1", "title": "Grant defence slides",
                       "facets": {"deliverable": ["deliverable:slides"],
                                  "audience": ["reviewers"]},
                       "looks_like": "Twelve pages, one figure each.",
                       "locations": [{"path": str(self.folder / "notes.md"), "what": "notes"}]}],
            "judgments": [
                {"case": "new1", "aspect": "output", "object": "object:amount of text",
                 "direction": "one sentence a page", "said": "three paragraphs",
                 "by": "user", "strength": "changed", "at": "2026-05-01"},
                {"case": "new1", "aspect": "output", "object": "object:colour",
                 "direction": "blue", "by": "agent", "strength": "accepted"},
                {"case": "new1", "aspect": "habit", "object": "object:tools used",
                 "direction": "outline first", "said": "words never shown",
                 "by": "user", "strength": "written", "refs": ["notes.txt"]}],
            "new_terms": []}
        reader = self._reader(_Answers(answer))
        pieces, marks = reader.pending(self.unit, datetime.now(timezone.utc).isoformat(), [])
        report = reader.read(self.unit, pieces, marks)
        self.assertEqual(report["judgments"], 2)
        self.assertEqual(report["left_agent"], 1)
        case = self.store.cases("place:1")[0]
        self.assertEqual(case["facets"]["deliverable"], ["deliverable:slides"])
        self.assertEqual(case["facets"]["audience"], ["audience:reviewers"])
        rows = {row["object"]: row for row in self.store.judgments_of(case["id"])}
        self.assertEqual(rows["object:amount of text"]["said"], "three paragraphs")
        habit = rows["object:tools used"]
        self.assertEqual(habit["said"], "", "a quote not in what was read is not the user's")
        self.assertNotEqual(habit["strength"], "written", "written only on a rule file")

    def test_a_change_the_agent_made_is_marked_as_the_agents(self) -> None:
        reader = self._reader(_Answers({"cases": [], "judgments": []}))
        pieces, marks = reader.pending(self.unit, datetime.now(timezone.utc).isoformat(), [])
        reader.read(self.unit, pieces, marks)
        self.unit = self.store.unit("place:1")
        self.unit["first_read"] = "2026-05-01"
        self.store.save_unit(self.unit)
        (self.folder / "notes.md").write_text(
            "# Talk\none sentence, by the user\n| item | cost |\n", encoding="utf-8")
        edit = records.Session(path="s", tool="claude", id="s", cwd=str(self.folder),
                               edits=[records.Edit("2026-05-02T00:00:00Z",
                                                   str(self.folder / "notes.md"),
                                                   "| item | cost |")])
        with mock.patch.object(Places, "sessions_in", lambda self, folder, sessions=None: [edit]):
            pieces, _marks = reader.pending(self.unit, datetime.now(timezone.utc).isoformat(), [edit])
        changed = "\n".join(piece.text for piece in pieces)
        self.assertIn("+[AGENT] | item | cost |", changed)
        self.assertIn("+[USER] one sentence, by the user", changed)

    # -- what counts as a rule the user wrote, and as what an agent noted --------
    def _strengths(self, judgments, sessions=()):
        """Read the unit once, the model answering with these judgments;
        the strength kept for each, by what it says."""
        answer = {"cases": [{"ref": "new1", "title": "Coastal grant code"}],
                  "judgments": [dict({"case": "new1", "aspect": "habit",
                                      "object": "object:steps of the work", "by": "user",
                                      "at": "2026-05-01"}, **one) for one in judgments]}
        reader = self._reader(_Answers(answer))
        with mock.patch.object(Places, "sessions_in",
                               lambda self, folder, given=None: list(sessions)):
            pieces, marks = reader.pending(self.unit, datetime.now(timezone.utc).isoformat(),
                                           list(sessions))
            reader.read(self.unit, pieces, marks)
        case = self.store.cases("place:1")[0]
        return {row["direction"]: row["strength"] for row in self.store.judgments_of(case["id"])}

    def _stamp(self, minutes):
        return (datetime.now(timezone.utc) - timedelta(days=1)
                + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_a_readme_is_no_rules_file(self) -> None:
        (self.folder / "README.md").write_text("Always use tabs.\n", encoding="utf-8")
        said = self._strengths([{"direction": "tabs", "said": "Always use tabs.",
                                 "strength": "written", "refs": ["README.md"]}])
        self.assertEqual(said["tabs"], "accepted")

    def test_what_init_made_is_not_the_users_and_a_line_they_added_is(self) -> None:
        made = "Use pytest for the tests.\nKeep functions short.\n"
        (self.folder / "CLAUDE.md").write_text(made + "Never push without asking.\n",
                                               encoding="utf-8")
        init = records.Session(path=str(self.dir / "s.jsonl"), tool="claude", id="s",
                               cwd=str(self.folder), commands=[(self._stamp(0), "/init")],
                               edits=[records.Edit(self._stamp(1), str(self.folder / "CLAUDE.md"),
                                                   made)])
        said = self._strengths([
            {"direction": "short functions", "said": "Keep functions short.",
             "strength": "written", "refs": ["CLAUDE.md"]},
            {"direction": "ask before pushing", "said": "Never push without asking.",
             "strength": "written", "refs": ["CLAUDE.md"]}], [init])
        self.assertEqual(said["short functions"], "accepted", "the agent's /init wrote it")
        self.assertEqual(said["ask before pushing"], "written", "the user added it")

    def test_a_rules_file_no_one_is_known_to_have_written_is_not_the_users(self) -> None:
        (self.folder / "AGENTS.md").write_text("Answer in English.\n", encoding="utf-8")
        said = self._strengths([{"direction": "english", "said": "Answer in English.",
                                 "strength": "written", "refs": ["AGENTS.md"]}])
        self.assertEqual(said["english"], "accepted")

    def test_a_memory_is_what_the_agent_noted_only_with_the_users_words_it_came_from(self) -> None:
        project = self.dir / "claude" / "coastal"
        memory = project / "memory" / "slides.md"
        memory.parent.mkdir(parents=True)
        memory.write_text("No emojis in slides.\n", encoding="utf-8")
        bare = records.Session(path=str(project / "s1.jsonl"), tool="claude", id="s1",
                               cwd=str(self.folder))
        said = self._strengths([{"direction": "no emojis", "strength": "noted",
                                 "refs": [str(memory)]}], [bare])
        self.assertEqual(said["no emojis"], "approved", "where it came from is not found")
        self.tearDown()
        self.setUp()
        project = self.dir / "claude" / "coastal"
        memory = project / "memory" / "slides.md"
        memory.parent.mkdir(parents=True)
        memory.write_text("No emojis in slides.\n", encoding="utf-8")
        told = records.Session(
            path=str(project / "s1.jsonl"), tool="claude", id="s1", cwd=str(self.folder),
            turns=[records.Turn(self._stamp(0), "user", "Please never put emojis in my slides.")],
            edits=[records.Edit(self._stamp(1), str(memory), "No emojis in slides.")])
        said = self._strengths([{"direction": "no emojis", "strength": "noted",
                                 "refs": [str(memory)]}], [told])
        self.assertEqual(said["no emojis"], "noted")

    def test_looking_ahead_keeps_no_copy(self) -> None:
        reader = self._reader(_Answers())
        reader.dry = True
        reader.pending(self.unit, datetime.now(timezone.utc).isoformat(), [])
        self.assertFalse((self.dir / "copies").exists() and any((self.dir / "copies").iterdir()))


class ReasonsTests(_Base):

    def _reason(self, judgments, **more):
        row = {"statement": "When you present in person, the audience should listen to you.",
               "source": "stated", "aspect": "output", "state": "active",
               "condition": {"dims": {"presentation": ["presentation:presented in person"]},
                             "text": "presented in person"},
               "objects": ["object:amount of text"], "predictions": [], "history": []}
        row.update(more)
        rid = self.store.save_reason(row)
        for jid in judgments:
            self.store.link(rid, jid, "supports")
        return rid

    def test_a_judgment_explained_is_linked_and_one_explained_by_none_goes_to_the_pool(self) -> None:
        case = self.case("Defence slides", presentation=["presentation:presented in person"])
        old = self.judgment(case)
        rid = self._reason([old])
        new, lone = self.judgment(case), self.judgment(case, obj="object:colour", direction="grey")
        ask = _Answers({"judgments": [{"id": new, "explained_by": [{"reason": rid, "predicted": True}]},
                                      {"id": lone}]}, {"judgments": [{"id": lone}]})
        Reasons(self.db, ask).explain([self.store.judgment(new), self.store.judgment(lone)])
        self.assertEqual(self.store.judgment(new)["state"], "linked")
        self.assertEqual(self.store.judgment(lone)["state"], "pool")
        link = [one for one in self.store.links_of_reason(rid) if one["judgment_id"] == new][0]
        self.assertTrue(link["predicted"])

    def test_a_rewrite_that_loses_the_users_own_words_is_refused_and_then_a_rival(self) -> None:
        case = self.case("Lecture slides", presentation=["presentation:presented in person"])
        said = self.judgment(case, said="No paragraphs, I'll say it when I present")
        rid = self._reason([said])
        conflict = self.judgment(case, direction="more", at="2026-06-01")
        rewrite = {"choice": "c", "why": "narrower",
                   "rewrite": {"statement": "Reviewers want little text.", "aspect": "output",
                               "condition": {"dims": {}, "text": "for reviewers"},
                               "objects": ["object:amount of text"], "predictions": [],
                               "fails_when": "never for students"}}
        verdicts = {"judgments": {said: "no", conflict: "yes"}}
        ask = _Answers(rewrite, verdicts, rewrite, verdicts)
        done = Reasons(self.db, ask).revise(rid, [conflict])
        self.assertEqual(done, "rival")
        reason = self.store.reason(rid)
        self.assertEqual(reason["state"], "competing")
        self.assertTrue(reason["rival"])
        self.assertIn("the user's own words", ask.asked[2][1], "told why it was refused")

    def test_a_change_over_time_cannot_set_aside_what_came_after_it(self) -> None:
        case = self.case("Lecture slides", presentation=["presentation:presented in person"])
        early = self.judgment(case, at="2025-01-01")
        late = self.judgment(case, at="2026-03-01")
        rid = self._reason([early, late], source="inferred")
        conflict = self.judgment(case, direction="more", at="2026-06-01")
        answer = {"choice": "e", "change_at": "2026-02-01", "set_aside": [early, late],
                  "rewrite": {"statement": "Now full text.", "aspect": "output",
                              "condition": {"text": "always"}, "objects": [],
                              "predictions": [], "fails_when": "a talk"}}
        reasons = Reasons(self.db, _Answers())
        ok, why = reasons._rewrite(self.store.reason(rid), [self.store.judgment(conflict)],
                                   answer, "e", "changed")
        self.assertFalse(ok)
        self.assertIn(late, why)

    def test_a_new_reason_needs_two_judgments_or_the_users_word_and_a_way_to_fail(self) -> None:
        case = self.case("Report", deliverable=["deliverable:report"])
        one, two = self.judgment(case, state="pool"), self.judgment(case, state="pool")
        reason = {"statement": "Reviewers must find the point in seconds.", "aspect": "output",
                  "condition": {"dims": {}, "text": "read by reviewers"},
                  "objects": ["object:structure and order"], "predictions": []}
        ask = _Answers({"reasons": [dict(reason, explains=[one])]},
                       {"reasons": [dict(reason, explains=[one, two])]},
                       {"reasons": [dict(reason, explains=[one, two], fails_when="a novel")]})
        reasons = Reasons(self.db, ask)
        reasons.abduce()
        self.assertEqual(self.store.reasons(), [], "one judgment, not the user's own word")
        reasons.abduce()
        self.assertEqual(self.store.reasons(), [], "no way to fail: not kept")
        reasons.abduce()
        kept = self.store.reasons()
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["source"], "inferred")

    def test_active_on_a_written_rule_two_changed_cases_or_three_let_stand(self) -> None:
        enough = Reasons._enough
        self.assertTrue(enough([{"strength": "written", "case_id": "a"}]))
        self.assertFalse(enough([{"strength": "changed", "case_id": "a"}]))
        self.assertTrue(enough([{"strength": "changed", "case_id": "a"},
                                {"strength": "approved", "case_id": "b"}]))
        self.assertFalse(enough([{"strength": "accepted", "case_id": c} for c in "ab"]))
        self.assertTrue(enough([{"strength": "accepted", "case_id": c} for c in "abc"]))


class TheClockTests(_Base):

    def setUp(self) -> None:
        super().setUp()
        self.config = _Config()
        self.learning = Learning(self.db, self.config, None, self.dir / "data")
        self.started = []
        self.learning._start = lambda update_id: self.started.append(update_id)
        # Started is going: the clock does not start it again.
        self.learning.running = lambda: bool(self.started)

    def _at(self, hour, minute=0):
        return datetime.now().astimezone().replace(hour=hour, minute=minute, second=0,
                                                   microsecond=0)

    def test_nothing_by_itself_while_off_or_with_no_time(self) -> None:
        self.learning.look(self._at(23))
        self.config.set("learning_times", "03:00")
        self.learning.look(self._at(23))
        self.assertEqual(self.started, [], "off at first")
        self.config.set("learning_auto_on", True)
        self.config.set("learning_times", "")
        self.learning.look(self._at(23))
        self.assertEqual(self.started, [])

    def test_a_missed_time_starts_one_update_cut_off_at_that_time(self) -> None:
        self.config.set("learning_auto_on", True)
        self.config.set("learning_times", "03:00, 14:00")
        self.learning.look(self._at(20))
        self.assertEqual(len(self.started), 1)
        update = self.store.update(self.started[0])
        self.assertEqual(update["by"], "schedule")
        self.assertEqual(datetime.fromisoformat(update["cutoff"]),
                         self._at(14).astimezone(timezone.utc))
        self.learning.look(self._at(20, 30))
        self.assertEqual(len(self.started), 1, "the same time is not started twice")

    def test_a_time_passing_while_an_update_is_open_is_skipped_and_said(self) -> None:
        self.config.set("learning_auto_on", True)
        self.config.set("learning_times", "03:00, 14:00")
        self.learning.look(self._at(4))
        self.assertEqual(len(self.started), 1)
        self.learning.look(self._at(15))
        self.assertEqual(len(self.started), 1, "no second update while one is open")
        update = self.store.update(self.started[0])
        self.assertEqual(len(update["skipped"]), 1)
        self.assertTrue(self.config.get(LAST_SLOT))

    def test_update_now_pause_and_continue(self) -> None:
        self.learning.update_now()
        with self.assertRaises(ValueError):
            self.learning.update_now()
        update = self.store.open_update()
        self.assertEqual(update["by"], "you")
        self.learning.pause()
        self.assertEqual(self.store.open_update()["state"], "paused")
        self.learning.resume()
        self.assertEqual(self.store.open_update()["state"], "running")

    def test_setting_the_clock_does_not_count_times_already_passed_as_missed(self) -> None:
        self.learning.set_auto(on=True, times="00:00")
        self.learning.look()
        self.assertEqual(self.started, [])


class WhatTheReviewerIsGivenTests(_Base):

    def _reason(self, statement, source, supports, **more):
        row = {"statement": statement, "source": source, "aspect": "output", "state": "active",
               "condition": {"dims": {"deliverable": ["deliverable:visual material"]},
                             "text": "visual material"},
               "objects": ["object:amount of text"], "predictions": [], "history": [],
               "support": float(len(supports))}
        row.update(more)
        rid = self.store.save_reason(row)
        for jid in supports:
            self.store.link(rid, jid, "supports")
        return rid

    def test_stated_first_inferred_on_two_observations_outside_never(self) -> None:
        slides = self.case("Defence slides", deliverable=["deliverable:slides"])
        poster = self.case("Conference poster", deliverable=["deliverable:poster"])
        one, two, three = self.judgment(slides), self.judgment(poster), self.judgment(slides)
        stated = self._reason("Listen, not read.", "stated", [one])
        inferred = self._reason("Reviewers have little time.", "inferred", [two, three])
        thin = self._reason("Thin evidence.", "inferred", [one])
        email = self._reason("Short emails.", "stated", [one],
                             condition={"dims": {"deliverable": ["deliverable:email"]}})
        found = Finder(self.db).answer("slides for a grant defence",
                                       {"deliverable": ["deliverable:slides"]},
                                       [{"q": "How much text a slide?", "kind": "output"}])
        given = found["found"]["reasons"]
        self.assertEqual(given[0], stated)
        self.assertIn(inferred, given)
        self.assertNotIn(thin, given)
        self.assertNotIn(email, given)
        self.assertIn("said by the user", found["text"])
        self.assertIn("inferred, confidence", found["text"])
        self.assertEqual(found["found"]["cases"][0], slides)

    def test_a_reason_the_model_says_does_not_hold_is_left_out(self) -> None:
        slides = self.case("Defence slides", deliverable=["deliverable:slides"])
        rid = self._reason("Listen, not read.", "stated", [self.judgment(slides)])
        said = Finder(self.db).answer("slides", {"deliverable": ["deliverable:slides"]}, [],
                                      ask=lambda system, content, most: {
                                          rid: {"holds": "no", "why": "a handout"}})
        self.assertEqual(said["found"]["reasons"], [])

    def test_each_question_its_few_reasons_and_the_unasked_expected(self) -> None:
        from personalized_agent.learning import find as find_module
        slides = self.case("Defence slides", deliverable=["deliverable:slides"])
        rids = [self._reason(f"Reason {n}.", "stated", [self.judgment(slides)]) for n in range(7)]
        questions = [{"q": "How much text a slide?", "kind": "output"},
                     {"q": "Which tool are the slides made in?", "kind": "fact"}]
        verdicts = {rid: {"holds": "yes", "answers": {"1": 3 - n % 2}} for n, rid in enumerate(rids[:5])}
        verdicts[rids[5]] = {"holds": "yes", "answers": {}, "expected": True}
        verdicts[rids[6]] = {"holds": "yes", "answers": {}}
        seen = []
        said = Finder(self.db).answer(
            "slides", {"deliverable": ["deliverable:slides"]}, questions,
            ask=lambda system, content, most: seen.append(content) or {"reasons": verdicts})
        self.assertIn("THE REVIEWER'S QUESTIONS:", seen[0])
        self.assertEqual(len(said["found"]["by_question"]["1"]), find_module.PER_QUESTION)
        self.assertEqual(said["found"]["by_question"]["1"][0], rids[0], "the best answer first")
        self.assertEqual(said["found"]["expected"], [rids[5]])
        self.assertNotIn(rids[6], said["found"]["reasons"], "answering nothing, expected by no one")
        self.assertIn("== QUESTION 2 (fact)", said["text"])
        self.assertIn("No reason learned answers this", said["text"])
        self.assertIn("FOR THE FACTS OF THIS PROJECT", said["text"])
        self.assertLessEqual(said["text"].count("    e.g. "), len(said["found"]["reasons"]),
                             "one example a reason")

    def test_long_cases_their_judgments_picked_for_the_questions(self) -> None:
        from personalized_agent.learning import find as find_module
        slides = self.case("Defence slides", deliverable=["deliverable:slides"])
        jids = [self.judgment(slides, direction=f"a long change, {n} " + "x" * 600) for n in range(80)]
        rid = self._reason("Listen, not read.", "stated", jids[:1])
        seen = []
        said = Finder(self.db).answer(
            "slides", {"deliverable": ["deliverable:slides"]}, [{"q": "How much text?", "kind": "output"}],
            ask=lambda system, content, most: seen.append(content) or {
                "reasons": {rid: {"holds": "yes", "answers": {"1": 3}}},
                "cases": {slides: [jids[7], jids[3], "J-not-of-it"]}})
        self.assertNotIn("CASES' JUDGMENTS", seen[0])
        self.assertIn("CASES' JUDGMENTS", seen[1], "picked in a second call")
        self.assertEqual(said["found"]["judgments_picked"], {slides: [jids[7], jids[3]]})
        self.assertLess(said["text"].index(jids[7]), said["text"].index(jids[3]), "most first")
        self.assertNotIn(jids[20], said["text"])
        self.assertIn(f"(78 more of the user's judgments in it: open_case {slides})", said["text"])
        self.assertLessEqual(len(said["text"]), find_module.PACKAGE_CHARS)

    def test_reasons_that_do_not_fit_are_left_out_whole_the_cases_stay(self) -> None:
        from personalized_agent.learning import find as find_module
        slides = self.case("Defence slides", deliverable=["deliverable:slides"])
        jid = self.judgment(slides)
        rids = [self._reason(f"Reason {n}: " + "y" * 3000, "stated", [jid]) for n in range(30)]
        questions = [{"q": f"Question {n}?", "kind": "output"} for n in range(12)]
        said = Finder(self.db).answer(
            "slides", {"deliverable": ["deliverable:slides"]}, questions,
            ask=lambda system, content, most: {"reasons": {
                rid: {"holds": "yes", "answers": {str(1 + n % 12): 3}} for n, rid in enumerate(rids)}})
        self.assertNotIn("(cut:", said["text"])
        self.assertLessEqual(len(said["text"]), find_module.PACKAGE_CHARS)
        self.assertIn(f"CASE {slides}", said["text"])
        self.assertLess(len(said["found"]["reasons"]), 30)

    def test_a_case_in_full_once_and_its_reason_page_by_page(self) -> None:
        slides = self.case("Defence slides", deliverable=["deliverable:slides"])
        jids = [self.judgment(slides) for _ in range(20)]
        rid = self._reason("Listen, not read.", "stated", jids)
        finder = Finder(self.db)
        self.assertIn("the user's judgments in it", finder.open_case(slides))
        first, second = finder.open_reason(rid, 1), finder.open_reason(rid, 2)
        self.assertIn("(more: page 2)", first)
        self.assertNotIn("(more:", second)


class LearningAgainTests(_Base):
    """Once: what was learned is backed up whole and emptied - the places
    listed and left out stay - and the next update learns from nothing."""

    def test_backed_up_emptied_and_only_once(self) -> None:
        from personalized_agent.learning.fresh import AGAIN, learn_again
        case = self.case("Grant defence slides", deliverable=["deliverable:slides"])
        self.judgment(case)
        self.store.save_unit({"id": "place:1", "kind": "place", "key": str(self.dir / "grant"),
                              "title": "Coastal grant", "folders": [], "marks": {}})
        self.store.save_update({"cutoff": "2026-05-01T00:00:00+00:00", "by": "you",
                                "state": "paused", "plan": {"units": []}})
        copies = self.dir / "learning" / "copies"
        copies.mkdir(parents=True)
        (copies / "a.txt").write_text("a copy of a document", encoding="utf-8")
        config = _Config(learning_places=[{"path": "D:/grant", "kind": "folder"}])

        said = learn_again(self.db, config, self.dir)
        backup = Path(said["backup"])
        self.assertTrue((backup / "agent.db").is_file())
        self.assertTrue((backup / "copies" / "a.txt").is_file(), "the copies go with it")
        self.assertFalse(copies.exists())
        for table in ("learn_cases", "learn_judgments", "learn_units", "learn_updates"):
            self.assertEqual(self.db.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"], 0, table)
        import sqlite3
        kept = sqlite3.connect(str(backup / "agent.db"))
        self.assertEqual(kept.execute("SELECT COUNT(*) FROM learn_cases").fetchone()[0], 1)
        kept.close()
        # The fixed words are there again; the places stay.
        self.assertTrue(Vocabulary(self.db).fixed_listing(("deliverable",)))
        self.assertTrue(self.db.query_one("SELECT COUNT(*) AS n FROM learn_terms")["n"])
        self.assertEqual(config.get("learning_places"), [{"path": "D:/grant", "kind": "folder"}])
        self.assertTrue(config.get(AGAIN))
        # Once only.
        self.store.save_update({"cutoff": "2026-05-02T00:00:00+00:00", "by": "you",
                                "state": "done", "plan": {}})
        self.assertEqual(learn_again(self.db, config, self.dir), {})
        self.assertEqual(self.db.query_one("SELECT COUNT(*) AS n FROM learn_updates")["n"], 1)

    def test_nothing_learned_nothing_backed_up(self) -> None:
        from personalized_agent.learning.fresh import AGAIN, learn_again
        config = _Config()
        self.assertEqual(learn_again(self.db, config, self.dir), {})
        self.assertFalse((self.dir / "backups").exists())
        self.assertTrue(config.get(AGAIN))


class TheNewspapersSwitchTests(unittest.TestCase):

    def test_no_issue_by_itself_unless_switched_on(self) -> None:
        from personalized_agent.heartbeat.connections.schedule import IssueClock

        class _Store:
            def briefing(self):
                return {}

        config = _Config(recommend_times="08:00")
        clock = IssueClock(config, mock.Mock(running=lambda: False), _Store())
        late = datetime.now().astimezone().replace(hour=23, minute=0)
        self.assertFalse(clock.due(late))
        config.set("recommend_auto_on", True)
        self.assertTrue(clock.due(late))

    def test_an_install_that_made_issues_keeps_making_them(self) -> None:
        from personalized_agent.heartbeat.connections.schedule import carry_over
        config = _Config()
        carry_over(config, made_before=True)
        self.assertTrue(config.get("recommend_auto_on"))
        self.assertEqual(config.get("recommend_times"), "08:00")
        fresh = _Config()
        carry_over(fresh, made_before=False)
        self.assertFalse(fresh.get("recommend_auto_on", False))


if __name__ == "__main__":
    unittest.main()

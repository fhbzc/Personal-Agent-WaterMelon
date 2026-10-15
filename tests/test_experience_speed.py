"""The experience's readings made faster - and still the same readings.

Each test holds a kept or narrowed reading to what the full one gives, and
to what it gives after the thing it was read from has changed."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from personalized_agent.data_manage.library import USER_NATURE, Record
from personalized_agent.data_manage.library.store import DataLibrary
from personalized_agent.data_manage.storage.database import Database
from personalized_agent.heartbeat.experience import index as index_module
from personalized_agent.heartbeat.experience import workspace as workspace_module
from personalized_agent.heartbeat.experience.index import (
    StageIndex, _found_by_now, found_by)
from personalized_agent.heartbeat.experience.patterns import (
    PatternBuilder, folder_records, nature_records)
from personalized_agent.heartbeat.experience.store import ExperienceStore
from personalized_agent.heartbeat.experience.workspace import (
    Workspace, WorkspaceTools, _from_the_harness, _text_of)


class _Temp(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="exp-speed-"))

    def tearDown(self) -> None:
        db = getattr(self, "db", None)
        if db is not None:
            db.close()
        shutil.rmtree(self.dir, ignore_errors=True)


def _said_as_it_was(paths, since, until, most=400):
    """The conversation reading as it was before it was kept: every file
    parsed, every time."""
    out = []
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    stamp = str(row.get("timestamp") or "")
                    if not stamp or stamp > until or (since and stamp <= since):
                        continue
                    message = row.get("message") or {}
                    role = message.get("role") or row.get("type")
                    text = _text_of(message.get("content"))
                    if role not in ("user", "assistant") or not text.strip():
                        continue
                    if role == "user" and _from_the_harness(text):
                        continue
                    out.append({"at": stamp[:19], "session": path.stem[:8],
                                "role": role, "text": text[:1500]})
        except OSError:
            continue
    out.sort(key=lambda one: one["at"])
    return out[-most:]


class ConversationsAreParsedOnceTests(_Temp):

    def _line(self, at, role, text):
        return json.dumps({"timestamp": at, "type": role,
                           "message": {"role": role, "content": text}}) + "\n"

    def _workspace(self, paths):
        workspace = Workspace(self.dir)
        workspace.conversations = lambda: list(paths)
        return workspace

    def test_the_kept_reading_is_the_full_one_and_follows_the_file(self) -> None:
        one = self.dir / "aaaaaaaa-1.jsonl"
        two = self.dir / "bbbbbbbb-2.jsonl"
        one.write_text(
            self._line("2026-10-01T10:00:00.000Z", "user", "make the figure")
            + "not json\n"
            + self._line("2026-10-01T10:01:00.000Z", "assistant",
                         [{"type": "text", "text": "made it"}])
            + json.dumps({"type": "summary", "summary": "x"}) + "\n"
            + self._line("2026-10-02T09:00:00.000Z", "user", "x" * 4000),
            encoding="utf-8")
        two.write_text(
            self._line("2026-10-01T10:00:30.000Z", "user", "and the table")
            + self._line("2026-10-03T00:00:00.000Z", "assistant", "   "),
            encoding="utf-8")
        workspace = self._workspace([one, two])
        for since, until in (("", "9999"), ("2026-10-01T10:00:30", "9999"),
                             ("", "2026-10-01T10:01"), ("2026-10-02", "2026-10-03")):
            self.assertEqual(workspace.said_in_conversations(since or None, until),
                             _said_as_it_was([one, two], since or None, until))
        # Read again, nothing is parsed again.
        loads = []
        real = workspace_module.json.loads
        workspace_module.json.loads = lambda text: (loads.append(1), real(text))[1]
        try:
            workspace.said_in_conversations(None, "9999")
            self.assertEqual(loads, [])
            # A session still being written: read again, whole.
            time.sleep(0.01)
            with open(two, "a", encoding="utf-8") as handle:
                handle.write(self._line("2026-10-04T00:00:00.000Z", "user", "one more"))
            said = workspace.said_in_conversations(None, "9999")
        finally:
            workspace_module.json.loads = real
        self.assertEqual(said, _said_as_it_was([one, two], None, "9999"))
        self.assertEqual(said[-1]["text"], "one more")
        self.assertTrue(loads)

    def test_the_conversation_tool_gives_what_it_gave(self) -> None:
        one = self.dir / "cccccccc-3.jsonl"
        one.write_text("".join(self._line(f"2026-10-01T10:{n:02d}:00.000Z",
                                          "user" if n % 2 else "assistant",
                                          f"turn {n} about the stage")
                               for n in range(60)), encoding="utf-8")
        tools = WorkspaceTools(self._workspace([one]), "2026-10-01T10:50")
        said = tools.call("conversation", {"containing": "STAGE"})
        expected = _said_as_it_was([one], None, "2026-10-01T10:50", most=2000)
        self.assertEqual(said, {"turns": expected[-120:],
                                "more_before": max(0, len(expected) - 120)})

    def test_a_line_that_is_not_a_turn_fails_the_reading_as_it_did(self) -> None:
        one = self.dir / "dddddddd-4.jsonl"
        one.write_text(json.dumps({"timestamp": "2026-10-05T00:00:00Z",
                                   "message": "a string"}) + "\n"
                       + self._line("2026-10-01T00:00:00.000Z", "user", "hello"),
                       encoding="utf-8")
        workspace = self._workspace([one])
        # Outside the window it was never looked at...
        self.assertEqual(workspace.said_in_conversations(None, "2026-10-02"),
                         _said_as_it_was([one], None, "2026-10-02"))
        # ...inside it, it failed - and still does.
        with self.assertRaises(AttributeError):
            _said_as_it_was([one], None, "9999")
        with self.assertRaises(AttributeError):
            workspace.said_in_conversations(None, "9999")


class NatureRecordsAreReadOnceTests(_Temp):

    def setUp(self) -> None:
        super().setUp()
        self.library = DataLibrary(self.dir / "data")
        self.library.ensure()

    def _write(self, title, **extra):
        return self.library.write(Record(folder=USER_NATURE, title=title,
                                         summary=title, body=title, **extra))

    def _same(self):
        def said(records):
            return [(r.id, r.title, r.summary, r.path, r.source, r.level,
                     r.created_at) for r in records]
        self.assertEqual(said(folder_records(self.library, USER_NATURE)),
                         said(self.library.records(USER_NATURE)))

    def test_the_kept_reading_is_the_librarys_and_follows_its_files(self) -> None:
        for n in range(5):
            self._write(f"the user checks figure {n}")
        self._write("a summary of others", source="compaction")
        self._same()
        self.assertEqual(len(nature_records(self.library)), 5)
        reads = []
        real = self.library.read
        self.library.read = lambda path: (reads.append(path), real(path))[1]
        folder_records(self.library, USER_NATURE)
        self.assertEqual(reads, [])
        # A new record, and one rewritten in place, are read; the rest not.
        self._write("a new one")
        first = next(r for r in self.library.records(USER_NATURE)
                     if r.title == "the user checks figure 0")
        first.title = "rewritten in place, and longer than it was"
        self.library.put(first)
        reads.clear()
        folder_records(self.library, USER_NATURE)
        self.assertEqual(len(reads), 2)
        self._same()
        reads.clear()
        folder_records(self.library, USER_NATURE)
        self.assertEqual(len(reads), 0)
        # And one taken away is gone.
        os.remove(first.path)
        self._same()


class FoundByIsKeptUntilTheTermsChangeTests(_Temp):

    def setUp(self) -> None:
        super().setUp()
        self.db = Database(self.dir / "agent.db")
        self.store = ExperienceStore(self.db)
        self.index = StageIndex(self.db, "live")
        self.stages = [self.store.add_stage({
            "task_id": "t", "kind": "execution", "medium": "document",
            "summary": f"rewrote the robustness section {n} of the paper",
            "work": {"references": [{"id": "r1", "location":
                                     f"D:/papers/trade{n}/main.tex"}]},
            "states": [], "began": f"2026-09-0{n + 1}", "ended": ""})
            for n in range(4)]
        self.ids = [stage["id"] for stage in self.stages]

    def test_kept_and_worked_out_again_agree(self) -> None:
        self.assertEqual(found_by(self.index, self.ids),
                         _found_by_now(self.index, self.ids))
        # Asked again, the same - and from what was kept.
        asked = []
        real = self.index.terms
        self.index.terms = lambda *a, **k: (asked.append(1), real(*a, **k))[1]
        self.assertEqual(found_by(self.index, self.ids[:2]),
                         _found_by_now(StageIndex(self.db, "live"), self.ids[:2]))
        self.assertEqual(asked, [])
        self.index.terms = real
        # A new stage changes how rare every word is: worked out again.
        self.store.add_stage({"task_id": "t", "kind": "plan",
                              "summary": "robustness robustness plan",
                              "states": [], "began": "2026-09-09", "ended": ""})
        self.assertEqual(found_by(self.index, self.ids),
                         _found_by_now(self.index, self.ids))
        # A correction that leaves as many terms, under the same row ids.
        self.store.correct_stage(self.ids[-1], {"summary": "rewrote the "
                                                "abstract section 3 of the essay"},
                                 "a test")
        self.assertEqual(found_by(self.index, self.ids),
                         _found_by_now(self.index, self.ids))
        # Written around this code, straight into the table.
        self.db.execute("DELETE FROM stage_terms WHERE stage_id = ?", (self.ids[0],))
        self.assertEqual(found_by(self.index, self.ids),
                         _found_by_now(self.index, self.ids))
        self.assertNotIn(self.ids[0], found_by(self.index, self.ids))

    def test_a_search_reads_only_the_terms_it_compares_by(self) -> None:
        whole = self.index.terms(self.ids)
        words = ["robustness", "paper", "nothing"]
        narrow = self.index.terms(self.ids[1:], only=words, places=True)
        expected = {}
        for stage_id in self.ids[1:]:
            kept = {term: weight for term, weight in whole.get(stage_id, {}).items()
                    if term in words or term.startswith("env:")}
            if kept:
                expected[stage_id] = kept
        self.assertEqual(narrow, expected)
        self.assertTrue(any(t.startswith("env:dir:") for t in narrow[self.ids[1]]))

    def test_the_counts_are_the_stages(self) -> None:
        self.assertEqual(self.store.stage_count(), len(self.store.stages()))
        self.assertEqual(self.store.stage_count(["t", "t", "u"]),
                         len(self.store.stages(["t", "t", "u"])))
        self.assertEqual(self.store.stage_count([]), 0)


class ThePatternsStartTheModelOnlyForAMissingVectorTests(_Temp):

    def test_kept_vectors_need_no_model(self) -> None:
        db = self.db = Database(self.dir / "agent.db")
        library = DataLibrary(self.dir / "data")
        library.ensure()
        records = [library.write(Record(folder=USER_NATURE, title=f"checks {n}",
                                        summary="s", body="b",
                                        metadata={"nature_kind": "work"}))
                   for n in range(4)]

        class Kept:
            """Every vector kept: `available()` would start the model."""

            def available(self):
                raise AssertionError("the model was started")

            def loaded(self):
                return contextlib.nullcontext(self)

            def read_states(self, texts):
                return {one.id: [1.0, 0.0] for one in texts}

            def pairwise(self, order, vectors, floor=0.0):
                return [(i, j, 1.0) for i in range(len(order))
                        for j in range(i + 1, len(order))]

        said = PatternBuilder(db, library, Kept(), None).build()
        self.assertEqual(said["records"], len(records))
        self.assertEqual(said["new_patterns"], 1)

        class Broken(Kept):
            def read_states(self, texts):
                return None

        self.assertEqual(PatternBuilder(db, library, Broken(), None).build(),
                         {"calls": 0, "patterns": 0, "principles": 0, "named": 0,
                          "appended": 0, "new_patterns": 0,
                          "skipped": "no embedder"})


if __name__ == "__main__":
    unittest.main()

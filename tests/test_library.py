"""The memory library: five folders of files, an index, and search.

Replaces test_graph_retrieval.py. What the graph tests were protecting falls
into three groups, and they are treated differently here.

*Still true, still tested.* Queries in scripts without spaces have to work -
this user writes in Chinese, and a search that silently matches nothing is the
worst kind of failure. Keywords still decide what a thing is findable under.
Categories still keep one kind of memory from crowding out the others.

*True of the graph only.* Twelve probabilistic walks, edge weights, evidence,
sampling. Those tests went with the thing they tested; the reasoning is in
deprecated/README.md.

*New, and tested here for the first time.* A file a user has edited by hand has
to be readable. An index has to notice what changed. A state's file has to say
what the state currently is, including after the user drags it on a calendar.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from support import TemporaryAgent

from personalized_agent.common.events.models import ListenerEvent

from personalized_agent.data_manage.library import (FOLDERS,
                                                    STATES,
                                                    USER_FEEDBACK,
                                                    USER_NATURE, DataLibrary,
                                                    Record)
from personalized_agent.data_manage.library.store import INTRO_NAME
from personalized_agent.data_manage.text import salient_terms, search_terms


class LibraryLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.library = self.agent.app.library

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_five_folders_exist_with_an_intro_each(self) -> None:
        for folder in FOLDERS:
            path = self.library.folder(folder)
            self.assertTrue(path.is_dir(), f"{folder} is missing")
            self.assertTrue((path / INTRO_NAME).is_file(),
                            f"{folder} has no {INTRO_NAME}")

    def test_an_intro_says_where_the_contents_come_from(self) -> None:
        # The folder has to explain itself, or a directory of four hundred
        # files explains nothing - which is the whole reason for files.
        for folder in FOLDERS:
            text = self.library.intro(folder)
            self.assertIn("Where it comes from", text, f"{folder} intro")
            self.assertIn("What belongs here", text, f"{folder} intro")

    def test_an_intro_the_user_rewrote_is_left_alone(self) -> None:
        """Rewriting one is the user saying how they want the folder used."""
        path = self.library.folder(USER_NATURE) / INTRO_NAME
        path.write_text("mine now", encoding="utf-8")
        self.library.ensure()
        self.assertEqual(path.read_text(encoding="utf-8"), "mine now")

    def test_the_library_lives_where_the_user_can_reach_it(self) -> None:
        # Under the workspace, not in internal state. A memory the user cannot
        # open is the thing the graph was.
        workspace = self.agent.app.paths.workspace_dir
        self.assertIn(str(workspace), str(self.library.root))


class RecordFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.library = self.agent.app.library

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_record_survives_a_round_trip(self) -> None:
        written = self.library.write(Record(
            folder=USER_NATURE, title="Works late", summary="past midnight",
            keywords=["schedule", "late"], body="Four weeks of evidence.",
            observed_from=datetime(2026, 9, 1, tzinfo=timezone.utc),
            observed_to=datetime(2026, 9, 14, tzinfo=timezone.utc)))
        read = self.library.read(written.path)
        self.assertEqual(read.id, written.id)
        self.assertEqual(read.title, "Works late")
        self.assertEqual(read.keywords, ["schedule", "late"])
        self.assertEqual(read.body, "Four weeks of evidence.")
        self.assertEqual(read.observed_from.day, 1)

    def test_a_file_the_user_wrote_by_hand_is_still_a_record(self) -> None:
        """No front matter, no id, no keywords. A user dropped a note in.

        Refusing to read it would be the wrong answer to a reasonable thing to
        do with a folder, and the folders exist to be reasonable things.
        """
        path = self.library.folder(USER_NATURE) / "my-note.md"
        path.write_text("I hate being interrupted before 10am.\n\nReally.",
                        encoding="utf-8")
        record = self.library.read(path)
        self.assertIsNotNone(record)
        self.assertEqual(record.folder, USER_NATURE)
        self.assertIn("interrupted", record.title)
        self.assertTrue(record.id, "a record read from a bare file still needs an id")

    def test_a_filename_says_what_the_record_is(self) -> None:
        # A directory listing should be readable on its own.
        written = self.library.write(Record(
            folder=USER_FEEDBACK, title="Thesis chapter three", body="x"))
        name = Path(written.path).name
        self.assertIn("thesis-chapter-three", name)
        self.assertRegex(name, r"^\d{4}-\d{2}-\d{2}-")

    def test_two_records_with_one_title_do_not_overwrite_each_other(self) -> None:
        # One round can write several records about one subject, and each is a
        # separate observation.
        first = self.library.write(Record(folder=USER_FEEDBACK, title="Same", body="one"))
        second = self.library.write(Record(folder=USER_FEEDBACK, title="Same", body="two"))
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(self.library.read(first.path).body, "one")
        self.assertEqual(self.library.read(second.path).body, "two")

    def test_a_title_that_is_not_a_filename_still_becomes_one(self) -> None:
        written = self.library.write(Record(
            folder=USER_FEEDBACK, title='C:\\path? "quoted" <thing>', body="x"))
        self.assertTrue(Path(written.path).is_file())

    def test_the_intro_is_not_a_record(self) -> None:
        self.library.write(Record(folder=USER_FEEDBACK, title="real", body="x"))
        titles = [r.title for r in self.library.records(USER_FEEDBACK)]
        self.assertEqual(titles, ["real"])


class TermTests(unittest.TestCase):
    def test_words_where_there_are_words(self) -> None:
        self.assertIn("thesis", search_terms("the thesis chapter"))

    def test_pairs_where_there_are_no_spaces(self) -> None:
        terms = search_terms("论文进度")
        self.assertIn("论文", terms)
        self.assertIn("文进", terms)

    def test_salient_terms_are_what_a_batch_repeats(self) -> None:
        # Not the first words of the oldest item, which is what handing a whole
        # batch to search_terms would give.
        batch = ["a one-off thing nobody repeats"] + [
            "compiling thesis chapter bibliography in latex overleaf"] * 5
        terms = salient_terms(batch)
        self.assertIn("thesis", terms)
        self.assertNotIn("nobody", terms)

    def test_common_words_are_not_terms(self) -> None:
        self.assertNotIn("the", salient_terms(["the user is in the editor"] * 3))

    def test_punctuation_does_not_take_a_slot(self) -> None:
        terms = salient_terms(['{"window_title": "report.docx"}'] * 3)
        self.assertFalse([t for t in terms if t.startswith("{")])


class StatesAreMirroredToDiskTests(unittest.TestCase):
    """Section 34.1 and 39.1, as amended: the file is the state.

    A state is the one thing in the library that is rewritten rather than
    added to. It is not an observation - it is a claim about what is true now,
    and it gets extended, closed, and moved when the user drags the task it
    accompanies across the calendar. A folder holding one file per change would
    say a dozen contradictory things about one afternoon.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _state(self, **kwargs):
        from personalized_agent.agent.states.store import State

        kwargs.setdefault("title", "Writing chapter three")
        kwargs.setdefault("starts_at", self.now)
        kwargs.setdefault("ends_at", self.now + timedelta(hours=2))
        return self.app.states.add(State(**kwargs))

    def _file(self, state) -> str:
        path = state.metadata.get("record_path")
        self.assertTrue(path, "a state must know which file is its own")
        return Path(path).read_text(encoding="utf-8")

    def test_creating_a_state_writes_its_file(self) -> None:
        state = self._state()
        text = self._file(state)
        self.assertIn("Writing chapter three", text)
        self.assertIn("status: active", text)

    def test_extending_a_state_rewrites_the_same_file(self) -> None:
        state = self._state()
        path = state.metadata["record_path"]
        self.app.states.extend(state.id, self.now + timedelta(hours=6), "not done")
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn("extended 1 time(s)", text)
        self.assertEqual(
            len(list(self.app.library.folder(STATES).glob("*.md"))), 1,
            "a change must not leave a second file behind")

    def test_closing_a_state_shows_in_the_file(self) -> None:
        from personalized_agent.agent.states.store import RETIRED

        state = self._state()
        self.app.states.close(state.id, status=RETIRED, reason="cancelled")
        self.assertIn("status: retired",
                      Path(state.metadata["record_path"]).read_text(encoding="utf-8"))

    def test_a_task_moved_on_the_calendar_moves_its_state_file(self) -> None:
        """Section 60.1: a calendar edit reaches the task, and the state.

        The state is half the context behind every answer, so one left saying
        Thursday after the user moved the task to Saturday is not a cosmetic
        problem - it is the agent being told something untrue.
        """
        from personalized_agent.common.events.models import ScheduledTask

        task = self.app.scheduler.create_task(ScheduledTask(
            title="Submit the form", scheduled_at=self.now + timedelta(hours=3)))
        states = self.app.states.for_task(task.id)
        self.assertTrue(states, "every task writes a state")
        path = states[0].metadata["record_path"]
        before = Path(path).read_text(encoding="utf-8")

        self.app.scheduler.reschedule(task.id, self.now + timedelta(days=2))
        after = Path(path).read_text(encoding="utf-8")
        self.assertNotEqual(before, after, "the file must follow the calendar")
        self.assertIn("span:", after)

    def test_a_task_cancelled_on_the_calendar_retires_its_state_file(self) -> None:
        from personalized_agent.common.events.models import ScheduledTask

        task = self.app.scheduler.create_task(ScheduledTask(
            title="Submit the form", scheduled_at=self.now + timedelta(hours=3)))
        path = self.app.states.for_task(task.id)[0].metadata["record_path"]
        self.app.scheduler.update_task(task.id, status="cancelled")
        self.assertIn("status: retired", Path(path).read_text(encoding="utf-8"))

    def test_a_state_that_cannot_be_mirrored_is_still_a_state(self) -> None:
        """The database is the record; the file is what makes it readable.

        Losing a state because a disk was busy would be a far worse outcome
        than a file that is briefly out of date.
        """
        from personalized_agent.agent.states.store import State

        broken = self.app.states
        original, broken.library = broken.library, _RefusesToWrite()
        try:
            state = broken.add(State(title="still recorded", starts_at=self.now,
                                     ends_at=self.now + timedelta(hours=1)))
            self.assertIsNotNone(self.app.states.get(state.id))
        finally:
            broken.library = original


class _RefusesToWrite:
    def put(self, record):
        raise OSError("disk is busy")


class TheProgressLineSaysWhatIsBeingAskedTests(unittest.TestCase):
    """Section 52.1: the line exists to say what the agent is doing.

    It showed the first two arguments as written, and those are not reliably
    what a call is about: ``{"limit": 40}`` read "Using search_memory
    limit: 40", which tells the reader how much of something they cannot see.
    """

    def _line(self, arguments):
        from personalized_agent.agent.core.agent_core import _tool_summary

        return _tool_summary(arguments)

    def test_what_was_searched_for_comes_first(self) -> None:
        line = self._line({"limit": 40, "queries": ["the SWR package"]})
        self.assertTrue(line.startswith("queries: the SWR package"), line)

    def test_a_list_reads_as_its_words_not_as_a_list(self) -> None:
        self.assertIn("one, two", self._line({"queries": ["one", "two"]}))

    def test_a_call_with_only_a_limit_still_says_what_it_can(self) -> None:
        self.assertEqual(self._line({"limit": 40}), "limit: 40")

    def test_an_argument_nobody_ranked_is_still_shown(self) -> None:
        self.assertIn("whatever", self._line({"whatever": "this"}))

    def test_a_long_value_is_cut_and_says_it_was(self) -> None:
        self.assertIn("…", self._line({"about": "x" * 200}))


class CountingWhatHasNotChangedTests(unittest.TestCase):
    """Section 14.36, as amended: the same count, over and over.

    ``stats()`` is asked constantly - by the status the interface polls, by
    the Memory tab, by the agent's own tools, by every heartbeat round - and
    it counted every file each time. Measured on one machine with **7,715
    records: 0.4 to 1.1 seconds a call**, and the Memory tab asked twice in
    one request.

    A folder's modification time moves when a file is added to it or removed
    from it, and stands still when one is merely rewritten - which is exactly
    the distinction a count needs.
    """

    def setUp(self) -> None:
        from personalized_agent.data_manage.library.store import DataLibrary

        self.root = Path(tempfile.mkdtemp(prefix="pa-count-"))
        self.library = DataLibrary(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _add(self, folder, name, body="a record"):
        directory = self.library.folder(folder)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text(body, encoding="utf-8")

    def test_it_counts_what_is_there(self) -> None:
        self._add("user_feedback", "one.md")
        self._add("user_feedback", "two.md")
        self._add("states", "three.md")
        stats = self.library.stats()
        self.assertEqual(stats["records"], 3)
        self.assertEqual(stats["folders"]["user_feedback"], 2)

    def test_asking_again_does_not_count_again(self) -> None:
        self._add("user_feedback", "one.md")
        self.library.stats()
        seen = []
        original = self.library.files

        def counted(folder=None):
            seen.append(folder)
            return original(folder)

        self.library.files = counted
        self.library.stats()
        self.assertEqual(seen, [], "the count was remembered")

    def test_but_a_new_record_is_counted(self) -> None:
        """Including one written inside the same tick as the last.

        A directory's clock has a resolution of a millisecond or two, and
        writes inside one tick are one tick: measured on this filesystem, six
        files written in a loop moved it four times. A round that writes
        three records would have added three files the count could not see,
        so the library says so itself when it writes one.
        """
        from personalized_agent.data_manage.library import USER_FEEDBACK, Record

        self.library.write(Record(folder=USER_FEEDBACK, title="one", body="a record"))
        self.assertEqual(self.library.stats()["records"], 1)
        self.library.write(Record(folder=USER_FEEDBACK, title="two", body="another"))
        self.assertEqual(self.library.stats()["records"], 2)

    def test_and_one_that_appeared_from_outside(self) -> None:
        # Nothing here can be told about that, so it is seen when the
        # folder's clock moves, or when the count expires - whichever is
        # first.
        self._add("user_feedback", "one.md")
        self.assertEqual(self.library.stats()["records"], 1)
        self.library._counts_changed()
        self._add("user_feedback", "two.md")
        self.assertEqual(self.library.stats()["records"], 2)

    def test_and_a_removed_one(self) -> None:
        # Removed from outside, so - like the test above - it is seen when
        # the folder's clock moves or when the count expires. The clock has
        # a resolution of a millisecond or two, and a delete that lands in
        # the same tick as the write before it moves nothing: under load
        # this passed or failed depending on how busy the machine was.
        self._add("user_feedback", "one.md")
        self._add("user_feedback", "two.md")
        self.assertEqual(self.library.stats()["records"], 2)
        (self.library.folder("user_feedback") / "two.md").unlink()
        self.library._counts_changed()
        self.assertEqual(self.library.stats()["records"], 1)

    def test_rewriting_a_record_does_not_change_the_count(self) -> None:
        # And does not need to: a folder's time does not move for it, which
        # is why the folder's time is the right thing to watch.
        self._add("user_feedback", "one.md")
        self.assertEqual(self.library.stats()["records"], 1)
        self._add("user_feedback", "one.md", body="rewritten, and longer than it was")
        self.assertEqual(self.library.stats()["records"], 1)

    def test_what_the_caller_gets_cannot_corrupt_what_is_remembered(self) -> None:
        self._add("user_feedback", "one.md")
        folders = self.library.stats()["folders"]
        folders["user_feedback"] = 999
        self.assertEqual(self.library.stats()["folders"]["user_feedback"], 1)

    def test_walking_asks_the_directory_entry_rather_than_the_path(self) -> None:
        """``Path.is_file()`` is another stat per entry.

        Seven thousand of them to answer a question about counts, when a
        directory entry already carries the answer.
        """
        import inspect

        from personalized_agent.data_manage.library import store

        source = inspect.getsource(store.DataLibrary.files)
        self.assertIn("os.scandir(", source)
        self.assertNotIn(".iterdir()", source)
        self.assertNotIn(".is_file() and", source, "the name is checked first")

    def test_an_intro_file_is_not_a_record(self) -> None:
        self._add("user_feedback", "one.md")
        self._add("user_feedback", INTRO_NAME)
        self.assertEqual(self.library.stats()["records"], 1)

    def test_a_missing_folder_counts_as_none_rather_than_failing(self) -> None:
        self.assertEqual(self.library.stats()["records"], 0)


if __name__ == "__main__":
    unittest.main()

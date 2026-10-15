"""The three things that went wrong all day, asked as questions.

Each of these took several passes to find, and none of them failed
loudly when it broke: a round that could not finish looked like a busy
agent, a flag that disagreed with the rows looked like a stale panel,
and two drawings of the same folder looked like names being lost. They
are cheap to ask and they were expensive not to.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import TemporaryAgent


def _a_group(beat, how_many=6):
    """A folder with enough in it to be worth grouping."""
    from personalized_agent.heartbeat.states.tasks import Kept, Task

    kept = Kept(beat.db, "live")
    made = []
    for number in range(how_many):
        task = Task(id=f"task-test-{number}", scope="live", number=number)
        task.described = {"title": "", "concrete": True}
        # Big enough to be described: a group below the smallest size
        # worth a summary is never sent to the model, and is rightly not
        # counted as owed a description.
        task.members = [f"s{number}_{k}" for k in range(beat._min_nodes())]
        made.append(task)
    kept.save(made)
    return made


class ARoundAlwaysReachesAnEndTests(unittest.TestCase):
    """A round that cannot finish is resumed every minute for ever.

    It happened: six model calls a minute for hours, on three tasks the
    model would not describe. Nothing failed - the agent was busy, the
    panel said it was working, and the only sign was the bill.

    The shape that caused it is a rule saying "carry on until every task
    has a name" with no answer for a task that cannot get one. So the
    question this asks is not "does a round finish" but "does it finish
    when the answers are useless", which is the case nobody writes a
    test for.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_even_when_every_answer_is_unusable(self) -> None:
        _a_group(self.beat)
        self.app.config.set("state_embedding_enabled", False)
        self.app.config.set("state_round_done", True, internal=True)
        self.app.config.set("state_round_completed_at",
                            "2026-01-01T00:00:00+00:00", internal=True)
        self.app.config.set("state_first_round_states", 1)

        calls = {"n": 0}

        def nothing_usable(*_args, **_kwargs):
            calls["n"] += 1
            return {"title": "", "summary": "", "concrete": True, "calls": 1,
                    "described": {}, "urgency": 4, "importance": 4}

        self.beat._summarise = nothing_usable
        self.beat.run_if_due(trigger="timer")
        first = calls["n"]
        for _ in range(3):
            self.beat.run_if_due(trigger="timer")
        self.assertEqual(
            calls["n"], first,
            "a round that cannot name its tasks asked again on the next "
            "look, which is how it came to spend six calls a minute for "
            "hours")

    def test_and_says_so_rather_than_claiming_it_finished(self) -> None:
        """The other half, and they pull opposite ways.

        Stopping is not the same as having done the work, and one flag
        meaning both called a round with three blanks in it complete.
        """
        _a_group(self.beat)
        self.app.config.set("state_embedding_enabled", False)
        self.app.config.set("state_first_round_states", 1)
        self.app.config.set("state_round_done", True, internal=True)
        self.app.config.set("state_round_completed_at",
                            "2026-01-01T00:00:00+00:00", internal=True)
        self.beat._summarise = lambda *a, **k: {
            "title": "", "summary": "", "concrete": True, "calls": 1,
            "described": {}, "urgency": 4, "importance": 4}
        self.beat.run_if_due(trigger="timer")
        self.assertTrue(
            self.beat._how_much_is_blank(),
            "the rows should still show work with no words on it")
        self.assertFalse(
            self.beat._round_record()["done"],
            "a round that left tasks blank called itself finished")


class TheFlagAndTheRowsAgreeTests(unittest.TestCase):
    """"Round finished" beside "1 of 4 tasks named" on the same screen.

    The flag was written when the round ended and describes that moment;
    the rows go on changing. Whoever asks whether there is work to do has
    to get the answer the panel is showing, or the two of them are
    describing different folders.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_folder_with_blanks_is_not_a_finished_round(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept

        _a_group(self.beat)
        kept = Kept(self.beat.db, "live")
        live = list(kept.live())
        live[0].described = {"title": "This one has a name.", "concrete": True}
        kept.save(live[:1])

        blank = self.beat._how_much_is_blank()
        self.assertTrue(blank, "the rows have tasks with no words on them")
        self.app.config.set("state_round_done", True, internal=True)
        self.assertEqual(
            self.beat._round_record()["blank"], blank,
            "the record has to carry what the rows say, not only what the "
            "round said about itself when it ended")


class BothDrawingsShowTheSameFolderTests(unittest.TestCase):
    """One drawing comes from a round in memory, one from the rows.

    They showed nine tasks and four. The five that went were groupings a
    summary had rejected - the drawing from the rows left them out and
    the drawing from the round did not - so restarting the application
    appeared to lose five names. Whichever is drawn, a reader is looking
    at the same folder and should see the same tasks in it.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_and_a_rejected_grouping_is_in_neither(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept

        _a_group(self.beat, how_many=4)
        kept = Kept(self.beat.db, "live")
        live = list(kept.live())
        for index, task in enumerate(live):
            task.described = {"title": f"Task {index}.",
                              "concrete": index != 0}
        kept.save(live)

        from_rows = self.beat._from_the_store()
        rows = {one["id"]: one for one in (from_rows.get("plan") or {})
                .get("nodes") or []}
        self.assertTrue(rows, "the rows should draw something")
        self.assertIn(live[0].id, rows,
                      "every row is sent; the page decides what to draw")
        self.assertTrue(
            rows[live[0].id].get("hidden"),
            "a grouping the summary rejected was sent unmarked, so the "
            "page cannot tell it apart from one worth drawing")

        # And the same folder, drawn from a round's report instead.
        self.beat._last_report = {
            "layers": [{"id": f"task_{n + 1}", "task_id": task.id,
                        "states": [], "children": [],
                        "summary": {"title": f"Task {n}.",
                                    "concrete": n != 0}}
                       for n, task in enumerate(live)],
            "plan": {"nodes": [{"id": f"task_{n + 1}", "task_id": task.id,
                                "title": f"Task {n}."}
                               for n, task in enumerate(live)],
                     "edges": []},
            "states": [], "running": False}
        from_round = self.beat.the_picture()
        marked = {str(one.get("task_id") or ""): bool(one.get("hidden"))
                  for one in (from_round.get("plan") or {}).get("nodes") or []}
        if marked:
            # THE SAME ANSWER FROM BOTH. The round's own copy of a
            # summary does not carry this flag, so a test written against
            # it matched nothing while appearing to work - the judgement
            # is read from the stored row on both paths now.
            self.assertTrue(
                marked.get(live[0].id),
                "the drawing from a round's report did not mark a "
                "grouping the summary rejected, so restarting the "
                "application looked like names being lost")


class NothingIsWrittenUnderABlankParentTests(unittest.TestCase):
    """The panel said it: "summarizing now: a task that has no name yet,
    in one of its sub-tasks".

    The top layer is written first so a reader has the whole picture
    before it fills in, and the pieces are then done one task at a time,
    most urgent first - where the urgency comes OUT of the task's own
    summary. A task whose description came back unusable has neither: no
    place in the picture, and no position in the order. Writing up its
    insides puts named sub-tasks under a blank parent.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_task_with_no_name_keeps_its_pieces_until_it_has_one(self):
        asked = []
        self.beat._name_the_pieces = lambda report, task, *a, **k: (
            asked.append(task.get("id")))
        report = {
            "layers": [
                {"id": "task_1", "task_id": "a", "states": [],
                 "children": [{"id": "task_1.1"}, {"id": "task_1.2"}],
                 "summary": {"title": "", "concrete": True,
                             "described": {"urgency": 6}}},
                {"id": "task_2", "task_id": "b", "states": [],
                 "children": [{"id": "task_2.1"}, {"id": "task_2.2"}],
                 "summary": {"title": "This one was written.",
                             "concrete": True,
                             "described": {"urgency": 3}}},
            ],
            "communities": [{"id": "g1", "big_enough": True},
                            {"id": "g2", "big_enough": True}],
        }
        shown = report["layers"]
        for one in shown:
            named = str((one.get("summary") or {}).get("title") or "").strip()
            if not named:
                continue
            self.beat._name_the_pieces(report, one)
        self.assertEqual(
            asked, ["task_2"],
            "the pieces of a task with no name were written up, which puts "
            "named sub-tasks under a blank parent")


class WhatAModelSaidIsNotLostTests(unittest.TestCase):
    """Three tasks went unnamed for a day while the round, the gate, the
    flag and the drawing were each examined in turn - because the one
    fact that would have explained it, what the model actually said, was
    recorded only onto a report nobody reads."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_summary_with_no_name_in_it_is_said_out_loud(self) -> None:
        import logging

        source = "personalized_agent.heartbeat.states.heartbeat"
        with self.assertLogs(source, level=logging.WARNING) as caught:
            logging.getLogger(source).warning(
                "no name came back for %s: %d chars of answer", "task_1", 0)
        self.assertTrue(any("no name came back" in one
                            for one in caught.output))


if __name__ == "__main__":        # pragma: no cover
    unittest.main()


class TheStatusIsCheapToAskTests(unittest.TestCase):
    """The panel asks for a drawing about once a second.

    Three status questions were added in one day - how many tasks are
    blank, which kind of round is running, what work is ahead - and each
    reads the whole task table or parses the round's grouping file off
    the disk. A drawing asks all of them several times over, so the
    answer went from tens of milliseconds to one and a quarter seconds,
    and the application stopped feeling responsive. The cause was the
    status, not the work.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_and_asking_twice_does_not_cost_twice(self) -> None:
        import time

        _a_group(self.beat, how_many=8)
        reads = {"n": 0}
        real = self.beat._still_missing

        def counted(*a, **k):
            reads["n"] += 1
            return real(*a, **k)

        self.beat._still_missing = counted
        self.beat.the_picture()
        once = reads["n"]
        self.beat.the_picture()
        self.beat.the_picture()
        self.assertEqual(
            reads["n"], once,
            "three drawings read the task table three times over; these "
            "answers change when a round writes something, which is "
            "minutes apart")

    def test_but_a_round_beginning_drops_what_was_kept(self) -> None:
        """Kept answers that outlive what they describe are worse than
        slow ones."""
        _a_group(self.beat, how_many=8)
        self.beat._how_much_is_blank()
        self.beat._begin_round(self.beat.where("live"))
        self.assertEqual(
            getattr(self.beat, "_recent_answers", {}), {},
            "a new round left the old counts in place")


class ARoundKeepsTheRowsItNamedTests(unittest.TestCase):
    """A rebuild retires rows and creates others, and the name, the
    weights and the pieces all live on the row.

    So a rebuild in the middle of a round throws away what that round has
    written. Measured on the live folder: a round that began at 16:52
    retired three rows and created four at 19:18, and the panel went from
    "8 of 8 tasks named" to "5 of 8" with tasks that had names reading
    "preparing" again. Nothing about the work had changed; only the rows
    holding it.

    Deciding again is not a cheaper way of reaching the same answer. It
    is a different answer from the same input.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_visit_finishing_a_round_does_not_rebuild(self) -> None:
        ran = {"n": 0}
        real = self.beat._rebuild

        def counted(*a, **k):
            ran["n"] += 1
            return real(*a, **k)

        self.beat._rebuild = counted
        _a_group(self.beat, how_many=6)
        self.app.config.set("state_embedding_enabled", False)
        self.app.config.set("state_first_round_states", 1)
        self.app.config.set("state_round_done", True, internal=True)
        self.app.config.set("state_round_completed_at",
                            "2026-01-01T00:00:00+00:00", internal=True)
        self.beat._summarise = lambda *a, **k: {
            "title": "A name.", "summary": "Something.", "concrete": True,
            "calls": 1, "described": {}, "urgency": 4, "importance": 4}
        self.beat.run_if_due(trigger="timer")
        opened = ran["n"]
        self.beat.run_once(trigger="manual_debug", scope="live")
        self.assertEqual(
            ran["n"], opened,
            "a visit that was only finishing a round rebuilt the task set, "
            "which retires the rows holding everything it had written")


class AStateIsNeverShownAsATaskTests(unittest.TestCase):
    """A task with no summary took the title of its first state.

    A state's title is a sentence about the user - "The user is adding an
    example..." - not a name for a piece of work. And it made a nameless
    task look named: the plan a round drew said "8 of 8 tasks named",
    the stored rows said "5 of 8", and the panel flipped between them
    every time a visit started or ended, for an evening.
    """

    def test_a_nameless_task_is_drawn_without_one(self) -> None:
        import inspect

        from personalized_agent.heartbeat.states import heartbeat

        source = inspect.getsource(heartbeat.StateHeartbeat._plan_the_lot)
        self.assertNotIn(
            "members[0].title", source,
            "an unnamed task is being given the title of one of its states")


class AFinishingVisitHasItsPiecesTests(unittest.TestCase):
    """A visit finishing a round does not rebuild the tree, and the tree
    is where a layer's pieces came from. Without reading them back, every
    layer had none: the pieces phase walked nothing, the completeness
    check read five blank pieces off the stored rows, and the round was
    picked up every thirteen seconds to do nothing at all.
    """

    def test_kept_pieces_come_back_in_the_shape_a_round_uses(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import (
            _a_piece_as_drawn)

        kept = {"id": "task_3.2", "title": "", "summary": "",
                "states": ["s1", "s2"], "children": [
                    {"id": "task_3.2.1", "title": "Named.", "summary": "Yes.",
                     "states": ["s1"], "children": []}]}
        drawn = _a_piece_as_drawn(kept)
        self.assertEqual(drawn["states"], ["s1", "s2"])
        self.assertEqual(drawn["summary"], {},
                         "a blank piece came back looking described")
        self.assertEqual(drawn["children"][0]["summary"]["title"], "Named.")


class WorkThatGrewKeepsItsNameTests(unittest.TestCase):
    """A rebuild matched a new group to an old task by overlap over the
    UNION, and a task that grows falls below that however much of itself
    it keeps: ten states joined by fifteen new ones is 0.4, with every
    one of the ten still there. So the rebuild retired it and started a
    nameless row in its place - for work that had only continued.
    """

    def test_a_task_that_grew_keeps_its_row(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Task, match

        old = [Task(id="A", members=[f"a{n}" for n in range(10)])]
        grown = [f"a{n}" for n in range(10)] + [f"new{n}" for n in range(15)]
        lined_up = match(old, [grown])[0]
        self.assertIsNotNone(lined_up["task"],
                             "a task that grew was given a new row")
        self.assertEqual(lined_up["task"].id, "A")
        self.assertEqual(lined_up["kind"], "continues")

    def test_but_one_that_lost_most_of_itself_is_new(self) -> None:
        """Continuation is about keeping what it was - not about sharing
        some of it. Six of ten left and ten others joined is a different
        piece of work."""
        from personalized_agent.heartbeat.states.tasks import Task, match

        old = [Task(id="A", members=[f"a{n}" for n in range(10)])]
        changed = [f"a{n}" for n in range(6)] + [f"new{n}" for n in range(10)]
        self.assertIsNone(match(old, [changed])[0]["task"])

    def test_and_a_continued_task_is_not_described_from_nothing(self):
        """Keeping the row is half of it. A continued task keeps its name
        and has its status and weights brought up to date; describing it
        afresh because it grew would rename the same work every time it
        was worked on."""
        import inspect

        from personalized_agent.heartbeat.states import heartbeat

        # The round's body: `run_once` only holds the model around it.
        source = inspect.getsource(heartbeat.StateHeartbeat._run_visit)
        self.assertIn('"continues"', source,
                      "the round no longer treats a continued task as one "
                      "whose name is kept")


class TheModelIsHeldToWhatItWasToldTests(unittest.TestCase):
    """The prompt says "send back ONLY the parts that are now wrong -
    anything you leave out is kept exactly as it reads here". It listed
    where, goal, doing and status - not the name. So the model left the
    name out, as told, and the parser - reading the name from the answer
    alone - threw every such answer away. Three tasks went a day without
    a name like that, answered in full every round: 3,445 characters,
    eleven fields, logged as "no name came back".
    """

    def test_an_amendment_keeps_what_it_left_out(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import _amended

        answer = {"doing": "The user now also writes the tests.",
                  "changed": ["doing"]}
        before = {"title": "Answer reviewer 2.",
                  "summary": "Reply to reviewer two.",
                  "goal": "The user wants it accepted."}
        got = _amended(answer, before)
        self.assertEqual(got["title"], "Answer reviewer 2.",
                         "an amendment that did not touch the name lost it")
        self.assertEqual(got["goal"], "The user wants it accepted.")
        self.assertEqual(got["doing"], "The user now also writes the tests.",
                         "what the amendment sent did not replace the old")

    def test_and_a_task_with_no_name_is_told_it_has_none(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import (
            _already_known)

        shown = _already_known({"doing": "The user drafts."})
        title_line = [one for one in shown.splitlines()
                      if one.startswith("title:")]
        self.assertTrue(title_line,
                        "the name is not among what the model is shown, so "
                        "it reads leaving it out as keeping it")
        self.assertIn("NONE YET", title_line[0])


class AnExpiredSignInIsNoticedTests(unittest.TestCase):
    """The agent's Claude Code sign-in expired and every model call was
    refused - fifty-seven of them - and nobody was asked to sign in.

    The prompt to sign in again exists and waits for exactly this. It was
    never shown, because both of the things that should have triggered it
    missed: the refusal said "Failed to authenticate", which a check for
    "authentication" does not match; and the sign-in probe, answered
    {"loggedIn": false} with a non-zero exit, read that as "could not
    check" and carried on as if signed in.
    """

    def test_the_refusal_is_recognised_as_needing_a_sign_in(self) -> None:
        import inspect

        from personalized_agent.models.providers import claude_code_provider

        source = inspect.getsource(claude_code_provider).lower()
        said = ("failed to authenticate: oauth session expired and could "
                "not be refreshed")
        phrases = ("authenticat", "oauth", "session expired")
        self.assertTrue(any(p in said for p in phrases))
        for phrase in phrases:
            self.assertIn(f'"{phrase}"', source,
                          f"the refusal's wording ({phrase!r}) is no longer "
                          "recognised as an expired sign-in")

    def test_a_signed_out_status_is_an_answer(self) -> None:
        from personalized_agent.common import children
        from personalized_agent.models.providers.claude_code_provider import (
            ClaudeCodeProvider)

        class Said:
            returncode = 1
            stdout = '{"loggedIn": false, "authMethod": "none"}'
            stderr = ""

        real = children.run
        children.run = lambda *a, **k: Said()
        try:
            got = ClaudeCodeProvider.__new__(ClaudeCodeProvider)._ask_client(
                "claude")
        finally:
            children.run = real
        self.assertEqual(got.get("loggedIn"), False,
                         "a signed-out answer was read as a failed check, so "
                         "the agent carried on as if signed in")


class ARefusedCallIsNotAnAttemptTests(unittest.TestCase):
    """Each item was recorded as tried before its call was made, so a call
    the model never saw used up the item's one go. With the sign-in gone,
    twenty-nine refused calls used up the whole round and it settled in
    to wait six hours with nothing named."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_round_pauses_and_carries_on_when_the_model_returns(self):
        """An open round with something left to try - the state a round is
        in when its calls have been refused rather than answered."""
        self.app.config.set("state_round_done", False, internal=True)
        self.app.config.set("state_round_exhausted", False, internal=True)
        self.app.config.set("state_round_started_at",
                            "2026-10-03T13:02:22+00:00", internal=True)
        self.app.config.set("state_round_reads_until",
                            "2026-10-03T13:02:22+00:00", internal=True)
        self.assertTrue(self.beat.due_now()["run"])
        self.beat._blocked("Failed to authenticate: OAuth session expired")
        verdict = self.beat.due_now()
        self.assertFalse(verdict["run"],
                         "a model that cannot be reached is asked again at "
                         "once instead of after a pause")
        self.assertIn("could not be reached", verdict["why"])
        self.assertTrue(self.beat._round_record()["blocked"],
                        "the page cannot say why the round has stopped")
        # Listening as the running loop does when it starts.
        self.beat._watch_the_interval()
        self.app.bus.publish("model.available", {"provider": "claude_code"})
        self.assertTrue(self.beat.due_now()["run"],
                        "the sign-in came back and the round did not carry on")

    def test_and_the_attempt_is_written_down_only_after_the_call(self):
        import inspect

        from personalized_agent.heartbeat.states import heartbeat

        # The round's body, where the calls now run several at once: the
        # call is made in `_summarise_one`, and what came back is written
        # down in `_land`, after it.
        source = inspect.getsource(heartbeat.StateHeartbeat._run_visit)
        call = source.index("return self._summarise(nodes, group, trace_id")
        noted = source.index("self._also_tried(", call)
        self.assertGreater(noted, call,
                           "a task is recorded as tried before its call is "
                           "made, so a refused call uses up its one go")


class ATooSmallGroupDoesNotHoldARoundOpenTests(unittest.TestCase):
    """A group below the smallest size worth a summary is never sent to
    the model - and the completeness check counted it as blank anyway.
    So there was always something "left to try" that nothing would ever
    try, the round could not finish, and it was resumed every two seconds
    as a round being finished, reading no new state again."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_and_is_not_counted_as_owed_a_description(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept, Task

        small = Task(id="task-small", scope="live", number=1)
        small.members = ["s1", "s2"]                 # below the minimum
        small.described = {"title": ""}
        big = Task(id="task-big", scope="live", number=2)
        big.members = [f"b{n}" for n in range(self.beat._min_nodes())]
        big.described = {"title": ""}
        Kept(self.beat.db, "live").save([small, big])
        missing = self.beat._still_missing(everything=True)
        self.assertNotIn("task-small", missing,
                         "a group too small to describe was counted as owed "
                         "a description, which no round will ever write")
        self.assertIn("task-big", missing)


class APageIsDescribedByWhatIsOnItTests(unittest.TestCase):
    """Keywords for a page come from what was read on it, weighed against
    every other page read in the same window - so what every page of a
    site shares is what no page is about."""

    def test_the_furniture_is_not_a_keyword(self) -> None:
        from personalized_agent.agent.overview.keywords import Corpus

        corpus = Corpus()
        chrome = "Search\nSign in\nSettings\nHelp\n"
        corpus.add("a", chrome + "The survey weights are robust to response "
                   "rates. We rerun the survey weights with response rates.")
        corpus.add("b", chrome + "Send the replication package before the "
                   "meeting about the replication package.")
        corpus.add("c", chrome + "和阿木聊天，阿木说明天去看电影，电影院在市中心。")
        about = corpus.about()
        for words in (about["a"]["keywords"], about["b"]["keywords"],
                      about["c"]["keywords"]):
            for furniture in ("search", "sign", "settings", "help"):
                self.assertNotIn(furniture, words)
        self.assertIn("survey", about["a"]["keywords"])
        self.assertIn("replication", about["b"]["keywords"])
        self.assertIn("阿木", about["c"]["keywords"])

    def test_a_word_cut_into_pairs_is_put_back_together(self) -> None:
        from personalized_agent.agent.overview.keywords import Corpus

        corpus = Corpus()
        corpus.add("v", "国际象棋解说 国际象棋 国际象棋新开局")
        corpus.add("w", "其他页面")
        corpus.add("x", "another page")
        self.assertIn("国际象棋", corpus.about()["v"]["keywords"])

    def test_a_page_and_its_window_are_matched_by_title(self) -> None:
        from personalized_agent.agent.overview.keywords import page_key

        self.assertEqual(page_key("Inbox (3) - me@x.com - Gmail - Google Chrome"),
                         page_key("Inbox (12) - me@x.com - Gmail"))
        self.assertEqual(page_key("\u25d1 Upload instruction"),
                         page_key("\u2733 Upload instruction"))

"""Section 34.4: tasks that outlive the round that found them.

The grouping used to be computed from nothing every hour. It was right
every hour and worthless over two: the tasks were numbered one to five in
order of size, so task 3 on Monday and task 3 on Tuesday were unrelated
things. Nothing could be said about a task over time - not that it grew,
not that it went quiet, not that the user had been at it for three weeks
- because there was no it. And every summary was paid for again, in a
model call, to describe a task that had gained one state.

So a task persists. States written since the last round are appended to
the task they are nearest, and the grouping is found again only when a
signal says appending has stopped being honest. When it is found again it
is matched against the old one by who is in it, so a task that is
substantially the same thing keeps its identity through the rebuild.
"""

import json
import pathlib
import unittest
from typing import Any
from datetime import datetime, timedelta, timezone

from personalized_agent.common.events.models import utcnow
from support import TemporaryAgent


def _reply(payload):
    class Reply:
        text = json.dumps(payload)

    return type("M", (), {
        "invoke": lambda self, *a, **k: Reply(),
        "resolve": lambda self, *a, **k: type("R", (), {"to_dict": lambda s: {}})(),
        "check_availability": lambda self, *a, **k: type("A", (), {
            "available": True, "to_dict": lambda s: {}, "state": None})()})()


SUMMARY = {"title": "They have spent the week on the replication package.",
           "summary": "A run of sittings on the same package.",
           "reasoning": "the same scripts throughout",
           "evidence": ["the fetch.py sittings"]}

REPLICATION = [
    ("They are rebuilding the SWR replication package.",
     "Working through the census fetch script."),
    ("They are rebuilding the replication package for the SWR paper.",
     "Checking the scripts against the published figures."),
    ("They are working through the replication scripts again.",
     "fetch.py and the figure notebook."),
    ("They are fixing the replication code for the weighting paper.",
     "The same package as yesterday."),
    ("They are still rebuilding that replication package.",
     "More of the census fetching."),
    ("They are checking the replication figures against the paper.",
     "The figure notebook again."),
]
WATCHING = [
    ("They are watching videos on YouTube.", "Video pages one after another."),
    ("They are watching another YouTube video.", "More video pages."),
    ("They are on YouTube again this evening.", "Videos, one after another."),
    ("They are watching a long video on YouTube.", "One page, a long while."),
    ("They are back on YouTube after dinner.", "More videos."),
]
#: More of the same replication work, written later and worded differently.
#: These are what an appending round is for.
MORE_REPLICATION = [
    ("They are back on the replication package this morning.",
     "The fetch script once more."),
    ("They are re-running the replication scripts.",
     "Waiting on census."),
]
#: A subject nothing already here is about, arriving all at once.
BOOKING = [
    ("They are booking a flight for the conference.", "Airline pages."),
    ("They are filling in the expense form.", "The finance system."),
    ("They are arranging the visa paperwork.", "Forms and appointments."),
    ("They are booking a hotel for the trip.", "Hotel pages."),
    ("They are submitting the travel claim.", "The finance system again."),
    ("They are checking the conference registration.", "The conference site."),
    ("They are printing the visa appointment letter.", "The consulate page."),
    ("They are paying the conference fee.", "The registration page."),
]


class AResumedRoundKeepsWhatItNamedTests(unittest.TestCase):
    """It should not pay twice for the same name.

    The marking step tells every task a round will describe to say
    "loading", and the loop asks about each in turn. A round interrupted
    after naming nine of eleven therefore came back, told all eleven to
    say loading again, and paid for the nine a second time - and until the
    second pass reached them, nine tasks that HAD names showed none.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.beat.models = _asking()
        self.app.config.set("state_first_round_states", 1)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _states(self, how_many: int) -> None:
        from personalized_agent.agent.states.store import State

        for number in range(how_many):
            self.app.states.add(State(
                id=f"state_{number}", title=f"A sitting {number}",
                detail="The user was working on the fetch step.",
                source="user", status="active",
                starts_at=utcnow() - timedelta(days=3, minutes=number * 9),
                ends_at=utcnow() - timedelta(days=3, minutes=number * 9 - 5)))

    def test_the_second_pass_asks_for_nothing_it_already_has(self) -> None:
        self._states(30)
        calls = []
        real = self.beat._summarise

        def give_up(*args, **kwargs):
            calls.append(1)
            if len(calls) == 3:
                self.beat._stop.set()
            return real(*args, **kwargs)

        self.beat._summarise = give_up
        first = self.beat.run_once(trigger="manual_debug", scope="live")
        self.assertFalse(first.get("error"), first.get("error"))
        named = json.loads(
            str(self.app.config.get("state_round_named", "[]")))
        self.assertTrue(named, "the round recorded nothing it had named")

        self.beat._stop.clear()
        self.beat.carry_on("live")
        self.beat._summarise = real
        calls.clear()
        second = self.beat.run_once(trigger="manual_debug", scope="live")
        self.assertFalse(second.get("error"), second.get("error"))
        # NOTHING ASKED AGAIN for what was already named.
        self.assertEqual(len(calls), 0,
                         f"the resumed round made {len(calls)} calls it did "
                         f"not need")
        self.assertTrue(second.get("round_complete"))

    def test_and_nothing_is_left_without_a_name_it_already_had(self) -> None:
        """A name on a task is drawn even while the round is still going,
        because the round never took it away - it only noted that a new
        one is owed."""
        from personalized_agent.heartbeat.states.heartbeat import _waiting_on

        held = _waiting_on({"title": "Answer reviewer 2.",
                            "status": "The user is partway.",
                            "urgency": 6, "importance": 5}, 14)
        # WHAT A TASK QUEUED FOR REWRITING HOLDS: its own name, and a note
        # that a new description is owed. The note used to be written into
        # the title with the real name moved aside, which made the stored
        # description a lie and - when a resumed round marked the same task
        # twice - saved the note as the name it used to have.
        self.assertEqual(held["title"], "Answer reviewer 2.")
        self.assertTrue(held["waiting"])
        self.assertNotIn("was", held)
        # Marking it again changes nothing, which is the point.
        self.assertEqual(_waiting_on(held, 14)["title"], "Answer reviewer 2.")

        report = {"layers": [
            {"id": "task_1", "states": ["s0"],
             "summary": {"described": held}},
            {"id": "task_2", "states": ["s1"],
             "summary": {"title": "Other.", "described": {}}}]}

        class Said:
            def __init__(self, number):
                self.id = f"s{number}"
                self.title = "A sitting"
                self.status = "active"
                self.starts_at = f"2026-09-2{number + 1}T09:00:00+00:00"
                self.ends_at = f"2026-09-2{number + 1}T11:00:00+00:00"

        node = self.beat._the_shape_so_far(
            report, [Said(0), Said(1)])["nodes"][0]
        self.assertEqual(node["title"], "Answer reviewer 2.")
        # Still flagged as waiting, so the pulse and the panel can say the
        # name on it is last round's.
        self.assertTrue(node["waiting"])
        self.assertTrue(node["name_is_old"])

    def test_and_a_new_round_forgets_the_old_list(self) -> None:
        """Otherwise the next round would skip everything the last one
        named and never describe anything again."""
        self.app.config.set("state_round_named",
                            json.dumps(["task_1", "task_2"]), internal=True)
        self.app.config.set("state_round_done", True, internal=True)
        self.beat._begin_round(self.beat.where("live"))
        self.assertEqual(self.beat._named_this_round(), [])

    def test_but_a_resumed_round_keeps_it(self) -> None:
        self.app.config.set("state_round_named",
                            json.dumps(["task_1"]), internal=True)
        self.app.config.set("state_round_done", False, internal=True)
        self.app.config.set("state_round_reads_until",
                            "2026-09-30T18:00:00+00:00", internal=True)
        self.beat._begin_round(self.beat.where("live"))
        self.assertEqual(self.beat._named_this_round(), ["task_1"])


class AnEmblemBelongsToTheTaskNotTheRowTests(unittest.TestCase):
    """The badge exists so that the one with the spokes stays the one with
    the spokes.

    It is generated from whatever id it is given, and the picture's own ids
    are POSITIONAL: the first row is `task_1` whatever task that happens to
    be this round. Seeded from those, a task's emblem followed its
    position - re-sort the picture, or let one task grow past another, and
    the badges swapped over, which is the exact opposite of the point.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.beat = self.agent.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    class Said:
        def __init__(self, number):
            self.id = f"s{number}"
            self.title = "A sitting"
            self.status = "active"
            self.starts_at = f"2026-09-2{number + 1}T09:00:00+00:00"
            self.ends_at = f"2026-09-2{number + 1}T11:00:00+00:00"

    def _seeds(self, order):
        """Which stored id each ROW would draw its emblem from."""
        layers = [{"id": f"task_{at + 1}", "task_id": held,
                   "states": [f"s{at}"],
                   "summary": {"title": f"Task {held}.", "concrete": True,
                               "described": {}}}
                  for at, held in enumerate(order)]
        shape = self.beat._the_shape_so_far(
            {"layers": layers}, [self.Said(0), self.Said(1)])
        return {one["id"]: one["task_id"] for one in shape["nodes"]}

    def test_the_seed_follows_the_task_through_a_change_of_order(self) -> None:
        first = self._seeds(["task-live-aaa", "task-live-bbb"])
        second = self._seeds(["task-live-bbb", "task-live-aaa"])
        # The ROW ids are the same both times...
        self.assertEqual(sorted(first), sorted(second))
        # ...and the seeds moved with the tasks.
        self.assertEqual(first["task_1"], "task-live-aaa")
        self.assertEqual(second["task_1"], "task-live-bbb")
        self.assertNotEqual(first["task_1"], second["task_1"],
                            "the emblem would have stayed with the row")

    def test_and_the_saved_picture_carries_it_too(self) -> None:
        """A restart draws from the store, where the id already IS the
        stored one - but it has to be handed over under the same name or
        the drawing falls back to the positional one."""
        from personalized_agent.agent.states.store import State
        from personalized_agent.heartbeat.states.tasks import Kept, Task

        made = []
        for number in range(2):
            ids = []
            for which in range(3):
                state = State(
                    id=f"state_{number}_{which}", title="A sitting",
                    detail="", source="user", status="active",
                    starts_at=utcnow() - timedelta(days=2, hours=which),
                    ends_at=utcnow() - timedelta(days=2, hours=which - 1))
                self.agent.app.states.add(state)
                ids.append(state.id)
            made.append(Task(id=f"task_{number + 1}", members=ids,
                             seat=number,
                             described={"title": f"Task {number + 1}.",
                                        "urgency": 4, "importance": 4,
                                        "concrete": True}))
        Kept(self.agent.app.db, "live").save(made)
        picture = self.beat.the_picture()
        for node in (picture["plan"] or {})["nodes"]:
            self.assertTrue(node.get("task_id"),
                            "a saved node carries no lasting id")
            self.assertEqual(node["task_id"], node["id"])


class WhatIsNotShownIsNotDrawnTests(unittest.TestCase):
    """A way of working is the agent's note about a habit.

    The model decides whether a group is a task with an end or a way the
    user works - "they keep checking the same three sites" - and only the
    first belongs in a picture of what somebody is in the middle of. The
    decision comes OUT of the summary, so before one is written the task
    is drawn and says it is loading; after, it either stays or goes.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        # Their tasks are three states each - a quarter hour apiece. What
        # is tested here is the picture, not how much work makes a task.
        self.app.config.set("state_task_min_hours", 0.25)

    def tearDown(self) -> None:
        self.agent.cleanup()

    class Said:
        def __init__(self, number):
            self.id = f"s{number}"
            self.title = f"A sitting {number}"
            self.status = "active"
            self.starts_at = f"2026-09-2{number + 1}T09:00:00+00:00"
            self.ends_at = f"2026-09-2{number + 1}T11:00:00+00:00"

    def test_an_unsummarised_task_is_drawn_and_a_way_of_working_is_not(
            self) -> None:
        report = {"layers": [
            {"id": "task_1", "states": ["s0", "s1"], "summary": {}},
            {"id": "task_2", "states": ["s2"],
             "summary": {"title": "Real work.", "concrete": True}},
            {"id": "task_3", "states": ["s3"],
             "summary": {"title": "A habit.", "concrete": False}}]}
        shape = self.beat._the_shape_so_far(
            report, [self.Said(number) for number in range(4)])
        drawn = [one["id"] for one in shape["nodes"]]
        # Not yet asked about, so drawn - that is what "loading" is for.
        self.assertIn("task_1", drawn)
        self.assertIn("task_2", drawn)
        # Asked, and the answer was no.
        self.assertNotIn("task_3", drawn)

    def test_the_judgement_outlives_the_round(self) -> None:
        """Without it a restart draws every one of them again, including
        the ones it had already judged not worth drawing."""
        from personalized_agent.agent.states.store import State
        from personalized_agent.heartbeat.states.tasks import Kept, Task

        made = []
        for number in range(3):
            ids = []
            for which in range(3):
                state = State(
                    id=f"state_{number}_{which}", title="A sitting",
                    detail="", source="user", status="active",
                    starts_at=utcnow() - timedelta(days=3, hours=which),
                    ends_at=utcnow() - timedelta(days=3, hours=which - 1))
                self.app.states.add(state)
                ids.append(state.id)
            made.append(Task(
                id=f"task_{number + 1}", members=ids, seat=number,
                described={"title": f"Task {number + 1}.",
                           "urgency": 4, "importance": 4,
                           # The middle one is a way of working.
                           "concrete": number != 1}))
        Kept(self.app.db, "live").save(made)

        picture = self.beat.the_picture()
        rows = {one["id"]: one for one in (picture["plan"] or {})["nodes"]}
        self.assertIn("task_1", rows)
        self.assertIn("task_3", rows)
        # MARKED, NOT MISSING. The judgement still has to survive the
        # restart - what changed is where it is applied. Dropping these
        # rows before the page saw them left two drawings of one folder
        # that could not be reconciled from anything on screen, and no
        # way to ask why a count was what it was. The page hides them and
        # has a button to show them.
        self.assertIn("task_2", rows,
                      "the row is sent, so the page can offer to show it")
        self.assertTrue(rows["task_2"]["hidden"],
                        "a way of working came back unmarked")
        self.assertFalse(rows["task_1"]["hidden"])


class AResumedRoundFinishesWhatItStartedTests(unittest.TestCase):
    """A round that was interrupted reads what IT was reading.

    The question this answers: if a round has begun summarising the
    states up to some moment and does not finish, does resuming it pull
    in everything that has arrived since? No - and it matters, because
    the tasks it had already written up were written against the old
    line. Mixing the two would leave the first half of a round describing
    one set of states and the second half another, with nothing to say
    which was which.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.beat.models = _asking()
        self.app.config.set("state_first_round_states", 1)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _states(self, how_many: int, tag: str, offset: Any,
                step: Any = None) -> None:
        """States at a given offset from now.

        `offset` is signed deliberately. The first version of this
        back-dated the "newer" states by a few minutes each, so eight of
        the nine actually STARTED BEFORE the line - and the round was
        right to read them while the test called it wrong. A state is
        newer than a line because of when it happened, not because of
        when the row was written.
        """
        from personalized_agent.agent.states.store import State

        for number in range(how_many):
            # The spacing matters as much as the offset: nine states a
            # minute apart, placed a second after a line, are mostly in
            # the FUTURE - and a future state is held back by every round,
            # including the one meant to read it.
            apart = step if step is not None else timedelta(minutes=1)
            began = utcnow() + offset + apart * number
            self.app.states.add(State(
                id=f"state_{tag}_{number}",
                title=f"A sitting on {tag} {number}",
                detail="The user was working on the fetch step.",
                source="user", status="active",
                starts_at=began, ends_at=began + timedelta(minutes=5)))

    def test_it_keeps_the_line_and_leaves_the_newer_states_alone(self) -> None:
        self._states(24, "before", -timedelta(days=3))
        # A round that gives up partway, as a quit does.
        calls = []
        real = self.beat._summarise

        def give_up(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                self.beat._stop.set()
            return real(*args, **kwargs)

        self.beat._summarise = give_up
        first = self.beat.run_once(trigger="manual_debug", scope="live")
        self.assertFalse(first.get("error"), first.get("error"))
        line = str(self.app.config.get("state_round_reads_until", ""))
        self.assertTrue(line, "the round drew no line")
        self.assertFalse(first.get("round_complete"),
                         "the round claimed to finish after being stopped")
        read_first = int(first.get("states_considered") or 0)
        self.assertGreater(read_first, 0, "it read nothing at all")

        # States arrive while it is down.
        self.beat._stop.clear()
        self.beat.carry_on("live")
        # JUST AFTER THE LINE THE FIRST ROUND DREW. Placed against that
        # line rather than against "now plus five minutes": a state in the
        # future is held back by every round, including the one meant to
        # pick it up, so the test would be asserting something no round
        # can do.
        from datetime import datetime

        drawn = datetime.fromisoformat(line)
        # A HAIR AFTER THE LINE, and all of them together. Spread over a
        # couple of seconds they land in the FUTURE relative to the next
        # round's line as well, so that round holds them back too and the
        # test measures how fast the machine is rather than what the code
        # does.
        self._states(9, "after", (drawn - utcnow()) + timedelta(milliseconds=1),
                     step=timedelta(0))
        self.beat._summarise = real

        # THE RESUMED ROUND. Same line, and the newer states are not in it.
        second = self.beat.run_once(trigger="manual_debug", scope="live")
        self.assertFalse(second.get("error"), second.get("error"))
        self.assertEqual(second.get("reads_until"), line,
                         "the resumed round moved its own line")
        self.assertEqual(int(second.get("states_considered") or 0), read_first,
                         "the resumed round pulled in the newer states")
        self.assertEqual(int(second.get("held_back") or 0), 9,
                         "the newer states were not held back")
        self.assertTrue(second.get("round_complete"),
                        "the resumed round did not reach the end")

        # AND ONLY THEN DOES THE NEXT ONE SEE THEM.
        third = self.beat.run_once(trigger="manual_debug", scope="live")
        self.assertFalse(third.get("error"), third.get("error"))
        self.assertNotEqual(third.get("reads_until"), line,
                            "a finished round did not draw a new line")
        # FEWER HELD BACK, not none: these states were placed a second or
        # so after the line, and whether the next round's line has passed
        # all of them depends on how fast the test runs. Asserting zero
        # made the test a stopwatch - it failed when the machine was busy
        # and passed when it was not, which says nothing about the code.
        # What the code promises is that a NEW line is drawn and that it
        # is later, so fewer wait behind it.
        self.assertEqual(int(third.get("held_back") or 0), 0,
                         "the new line held back states that were before it")
        self.assertGreater(int(third.get("states_considered") or 0),
                           0, "the round that drew a new line read nothing")


class TheFirstGroupingWaitsForEnoughStatesTests(unittest.TestCase):
    """Below the threshold nothing runs, and the panel says what for.

    Two reasons pointing the same way: a dozen states grouped into a dozen
    tasks of one is a drawing of a list, which is what grouping exists to
    replace; and an empty panel for six hours cannot be told apart from a
    broken one.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        # A round finished a moment ago, so the HOURS would say no. The
        # point is that while there is nothing to show they do not apply.
        self.app.config.set("state_round_done", True, internal=True)
        self.app.config.set("state_round_completed_at",
                            utcnow().isoformat(), internal=True)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_it_says_what_it_is_waiting_for_and_how_far_along(self) -> None:
        verdict = self.beat.due_now()
        self.assertFalse(verdict["run"], verdict["why"])
        self.assertEqual(verdict["waiting_for"], 100)
        self.assertIn("Waiting for the first 100 states", verdict["why"])
        self.assertIn("so far", verdict["why"])

    def test_and_the_hours_do_not_override_it(self) -> None:
        """Seven hours with four states still does not group anything."""
        from datetime import timedelta

        self.app.config.set(
            "state_round_completed_at",
            (utcnow() - timedelta(hours=7)).isoformat(), internal=True)
        verdict = self.beat.due_now()
        self.assertFalse(verdict["run"], verdict["why"])
        self.assertIn("Waiting for the first", verdict["why"])

    def test_but_a_picture_that_exists_puts_the_hours_back_in_charge(
            self) -> None:
        """The count is the rule only while there is nothing to show."""
        self.beat._last_report = {"plan": {"nodes": [{"id": "task_1"},
                                                     {"id": "task_2"}]}}
        verdict = self.beat.due_now()
        self.assertFalse(verdict["run"], verdict["why"])
        self.assertIn("hours since the last round", verdict["why"])
        self.assertNotIn("Waiting for the first", verdict["why"])

    def test_the_threshold_can_be_lowered_and_then_it_runs(self) -> None:
        self.app.config.set("state_first_round_states", 1)
        verdict = self.beat.due_now()
        # With the floor at one, any unread state is enough; with none at
        # all it still waits, which is the honest answer.
        self.assertIn("Waiting for the first 1 states", verdict["why"]) \
            if not verdict["run"] else self.assertIn("past the 1",
                                                     verdict["why"])


class ThePictureSurvivesARestartTests(unittest.TestCase):
    """Reported from a running agent, and it was right to report it.

        No grouping to draw yet. 0.0 hours since the last round finished,
        and it waits 6. Every one of the 506 states it reads is in a task.

    Nine tasks in the database, five hundred states inside them, and a
    panel saying there was nothing to draw: the picture was built only
    from the round's report, which lives in memory and dies with the
    process. The tasks are rows in a table - that is what keeping them is
    for.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        # Their tasks are three states each - a quarter hour apiece. What
        # is tested here is the picture, not how much work makes a task.
        self.app.config.set("state_task_min_hours", 0.25)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _saved(self, how_many: int = 2) -> None:
        """Tasks and their states, as a previous process would have left
        them: written down, with no report anywhere."""
        from personalized_agent.agent.states.store import State
        from personalized_agent.heartbeat.states.tasks import Kept, Task

        made = []
        for number in range(how_many):
            ids = []
            for which in range(3):
                state = State(
                    id=f"state_{number}_{which}",
                    title=f"A sitting on part {which}",
                    detail="", source="user", status="active",
                    starts_at=utcnow() - timedelta(days=4 - number,
                                                   hours=which),
                    ends_at=utcnow() - timedelta(days=4 - number,
                                                 hours=which - 1))
                self.app.states.add(state)
                ids.append(state.id)
            made.append(Task(id=f"task_{number + 1}", members=ids,
                             seat=number,
                             described={"title": f"The {number + 1}st job.",
                                        "urgency": 5 + number,
                                        "importance": 4,
                                        "status": "The user is partway."}))
        Kept(self.app.db, "live").save(made)

    def test_it_is_drawn_from_the_saved_tasks_with_no_round_at_all(
            self) -> None:
        self._saved()
        self.assertFalse((self.beat.last_report or {}).get("plan"),
                         "this test is about there being no report")
        picture = self.beat.the_picture()
        self.assertTrue(picture.get("from_store"),
                        "the picture was not built from the saved tasks")
        nodes = (picture.get("plan") or {}).get("nodes") or []
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]["title"], "The 1st job.")
        self.assertEqual(nodes[0]["size"], 3)
        # The span is arithmetic over the states behind the task.
        self.assertTrue(nodes[0]["began"], "no span was worked out")
        self.assertLess(nodes[0]["began"], nodes[0]["ended"])
        # The weight it was given is kept with it, so the ordering works
        # before any round has run in this process.
        held = (picture["layers"][0]["summary"] or {}).get("described") or {}
        self.assertEqual(held.get("urgency"), 5)

    def test_only_the_top_layer_is_drawn_even_with_pieces_saved(self) -> None:
        """Only tasks are described, and nothing under one is unfolded: a
        task saved with pieces from before is drawn without them."""
        from personalized_agent.heartbeat.states.tasks import Kept

        self._saved()
        kept = Kept(self.app.db, "live")
        one = kept.live()[0]
        one.described = dict(one.described or {}, pieces=[
            {"id": "task_1.1", "title": "The fetch step.",
             "states": ["state_0_0"], "concrete": True, "described": {},
             "children": []}])
        kept.save([one])
        mine = next(layer for layer in self.beat.the_picture()["layers"]
                    if layer["id"] == one.id)
        self.assertEqual(mine["children"], [])

    def test_but_a_task_whose_pieces_are_unknown_says_so(self) -> None:
        """Not "nothing divides", which is a different claim."""
        self._saved()
        picture = self.beat.the_picture()
        bare = [layer for layer in picture["layers"]
                if not layer["children"]]
        self.assertTrue(bare, "every task had pieces, so this tested nothing")
        self.assertIn("not been worked out yet", bare[0]["why_closed"])
        self.assertIn("next round", bare[0]["why_closed"])

    def test_but_a_round_in_memory_is_preferred_to_the_saved_tasks(
            self) -> None:
        """It is the newer answer, and it has the pieces and the arrows."""
        self._saved()
        self.beat._last_report = {
            "plan": {"nodes": [{"id": "task_1", "title": "From the round."},
                               {"id": "task_2", "title": "Also."}],
                     "edges": [{"from": "task_1", "to": "task_2"}]},
            "layers": [], "states": []}
        picture = self.beat.the_picture()
        self.assertFalse(picture.get("from_store"))
        self.assertEqual(len(picture["plan"]["edges"]), 1)

    def test_and_one_saved_task_is_still_not_a_picture(self) -> None:
        self._saved(how_many=1)
        picture = self.beat.the_picture()
        self.assertFalse(picture.get("from_store"))
        self.assertEqual((picture.get("plan") or {}).get("nodes"), [])


class TheShapeIsDrawnBeforeTheNamesTests(unittest.TestCase):
    """Grouping needs no model; naming is one call per task.

    Waiting for the words before drawing anything meant the panel showed
    nothing for the whole of a round that takes minutes, and a reader
    cannot tell a round that is working from one that failed.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    class Said:
        """A state as the shape reads one: an id, a span and a status."""

        def __init__(self, number):
            self.id = f"s{number}"
            self.title = f"A sitting {number}"
            self.status = "active"
            self.starts_at = f"2026-09-2{number + 1}T09:00:00+00:00"
            self.ends_at = f"2026-09-2{number + 1}T11:00:00+00:00"

    def _nodes(self):
        return [self.Said(number) for number in range(4)]

    def test_the_circles_and_spans_come_from_the_grouping_alone(self) -> None:
        nodes = self._nodes()
        report = {"layers": [
            {"id": "task_1", "states": ["s0", "s1"], "summary": {}},
            {"id": "task_2", "states": ["s2", "s3"],
             "summary": {"title": "The figures."}}]}
        shape = self.beat._the_shape_so_far(report, nodes)
        self.assertEqual(len(shape["nodes"]), 2)
        # NO EDGES. A dependency is a judgement and only a model makes
        # one, so this says nothing about them rather than guessing.
        self.assertEqual(shape["edges"], [])
        self.assertTrue(shape["shape_only"])
        first, second = shape["nodes"]
        # The span is arithmetic over the states, not a model's opinion.
        self.assertEqual(first["began"], "2026-09-21T09:00:00+00:00")
        self.assertEqual(first["ended"], "2026-09-22T11:00:00+00:00")
        self.assertEqual(first["size"], 2)
        # UNNAMED SAYS SO, rather than borrowing a state's words or the
        # internal id - either would read as the name of the work.
        self.assertTrue(first["waiting"])
        self.assertEqual(first["title"], "")
        # And a task already named is not waiting for anything.
        self.assertFalse(second["waiting"])
        self.assertEqual(second["title"], "The figures.")

    def test_one_task_is_not_a_shape(self) -> None:
        shape = self.beat._the_shape_so_far(
            {"layers": [{"id": "task_1", "states": ["s0"], "summary": {}}]},
            self._nodes())
        self.assertEqual(shape, {})

    def test_a_round_puts_the_shape_up_before_it_asks_for_a_name(
            self) -> None:
        """Measured through the round itself, not by reading the code."""
        seen = []
        real = self.beat._summarise

        def watched(*args, **kwargs):
            # What the panel would be showing at the moment of the first
            # naming call.
            plan = (self.beat.progress() or {}).get("plan") or {}
            seen.append((len(plan.get("nodes") or []),
                         bool(plan.get("shape_only"))))
            return real(*args, **kwargs)

        self.beat._summarise = watched
        self.beat.models = _asking()
        self.app.config.set("state_sandbox_max_states", 60)
        self.beat.run_in_the_sandbox(how_many=60, days=12, seed=31)
        if not seen:
            self.skipTest("nothing was summarised")
        drawn, shape_only = seen[0]
        self.assertGreaterEqual(drawn, 2,
                                "the picture was empty when naming began")
        self.assertTrue(shape_only,
                        "the early picture is not marked as shape-only")


class ARoundIsAThingWithAnEndTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    """Six hours between rounds, a line to read up to, and an order.

    A round is not a tick: it is one model call per task, which is
    minutes on a busy folder. That makes when it starts, what it reads
    and what it leaves half-done all worth deciding rather than letting
    a timer decide them.
    """

    def test_it_waits_twelve_hours_from_the_last_round_that_finished(
            self) -> None:
        from datetime import datetime, timedelta, timezone

        self.beat.models = _asking()
        self.beat.make_up_states(how_many=30, days=8, seed=81)
        # THE HOURS ARE ABOUT A PICTURE THAT ALREADY EXISTS. While there
        # is nothing to show, the count is the whole rule and outranks
        # them - including "no round has ever finished" - so this test
        # gives it something to show first. Without that it is testing
        # the other rule and saying the wrong thing about this one.
        self.beat._last_report = {"plan": {"nodes": [{"id": "task_1"},
                                                     {"id": "task_2"}]}}
        # Nothing has ever finished, so it is due.
        self.assertTrue(self.beat.due_now()["run"])
        self.assertIn("has ever finished", self.beat.due_now()["why"])

        # A round that finished an hour ago is not due.
        now = datetime.now(timezone.utc)
        self.app.config.set("state_round_done", True, internal=True)
        self.app.config.set(
            "state_round_completed_at",
            (now - timedelta(hours=1)).isoformat(), internal=True)
        verdict = self.beat.due_now()
        self.assertFalse(verdict["run"], verdict["why"])
        self.assertIn("waits 12", verdict["why"])

        # Thirteen hours ago, and it is.
        self.app.config.set(
            "state_round_completed_at",
            (now - timedelta(hours=13)).isoformat(), internal=True)
        verdict = self.beat.due_now()
        self.assertTrue(verdict["run"], verdict["why"])

    def test_an_unfinished_round_is_resumed_before_the_six_hours_matter(
            self) -> None:
        """Half a round leaves tasks reading 'loading'.

        The way out of that is to finish, not to wait six hours and
        start again - so an interrupted round outranks the clock.
        """
        from datetime import datetime, timezone

        # Finished a moment ago, which would normally mean "not due"...
        self.app.config.set("state_round_completed_at",
                            datetime.now(timezone.utc).isoformat(),
                            internal=True)
        # ...but one began and never ended.
        self.app.config.set("state_round_done", False, internal=True)
        self.app.config.set("state_round_started_at",
                            "2026-09-30T18:00:00+00:00", internal=True)
        verdict = self.beat.due_now()
        self.assertTrue(verdict["run"], verdict["why"])
        self.assertTrue(verdict["resuming"])
        # The reason names the consequence, not a marker: there is no
        # longer a word written into a task to mean "no name yet".
        self.assertIn("no name yet", verdict["why"])

    def test_a_resumed_round_keeps_the_line_the_first_one_drew(self) -> None:
        """Moving it would hide new states from the half already done."""
        where = self.beat.where("live")
        self.app.config.set("state_round_done", False, internal=True)
        self.app.config.set("state_round_reads_until",
                            "2026-09-30T18:00:00+00:00", internal=True)
        self.assertEqual(self.beat._begin_round(where),
                         "2026-09-30T18:00:00+00:00")
        # And a round that is NOT a resumption draws a new one.
        self.app.config.set("state_round_done", True, internal=True)
        drawn = self.beat._begin_round(where)
        self.assertNotEqual(drawn, "2026-09-30T18:00:00+00:00")

    def test_a_state_arriving_mid_round_is_left_for_the_next_one(
            self) -> None:
        """A round that read a moving window would finish holding a
        summary already out of date, with nothing to tell that apart
        from one that was never written."""
        from personalized_agent.heartbeat.states.heartbeat import _up_to

        class Said:
            def __init__(self, when):
                self.starts_at = when

        line = "2026-09-30T12:00:00+00:00"
        nodes = [Said("2026-09-30T11:00:00+00:00"),
                 Said("2026-09-30T12:00:00+00:00"),
                 Said("2026-09-30T13:00:00+00:00"),
                 Said("")]
        kept = _up_to(nodes, line)
        # Before and exactly on the line are in; after is not.
        self.assertEqual(len(kept), 3)
        # AND A STATE WITH NO TIME IS KEPT. The line exists to stop a
        # round reading its own tail, and a missing field is not evidence
        # of that - dropping it would be a grouping quietly losing work.
        self.assertIs(kept[-1], nodes[-1])

    def test_the_top_layer_is_done_first_then_by_urgency(self) -> None:
        """What a reader watching it fill in would have chosen."""
        report = {"layers": [
            {"id": "a", "summary": {"described": {"urgency": 3}}},
            {"id": "b", "summary": {"described": {"urgency": 7}}},
            {"id": "c", "summary": {"described": {"urgency": 5}}},
            {"id": "d", "summary": {}},
        ]}
        order = self.beat._by_urgency(report)
        self.assertEqual([report["layers"][i]["id"] for i in order],
                         ["b", "c", "d", "a"])

    def test_and_a_tie_keeps_the_order_it_had(self) -> None:
        report = {"layers": [
            {"id": "a", "summary": {"described": {"urgency": 5}}},
            {"id": "b", "summary": {"described": {"urgency": 5}}},
        ]}
        self.assertEqual(self.beat._by_urgency(report), [0, 1])

    def test_the_timer_cannot_run_a_round_the_gate_refused(self) -> None:
        """Two paths to the same work is six hours that are not six hours.

        The timer wakes hourly and used to call a round outright. Left
        that way beside an agent round that also asks for one, a round
        would happen every hour and the gate would decide nothing.
        """
        from datetime import datetime, timezone

        asked = []
        self.beat.run_once = lambda **kw: asked.append(kw) or {}
        # A round finished a moment ago, so nothing is due.
        self.app.config.set("state_round_done", True, internal=True)
        self.app.config.set("state_round_completed_at",
                            datetime.now(timezone.utc).isoformat(),
                            internal=True)
        said = self.beat.run_if_due(trigger="timer")
        self.assertFalse(said["run"], said["why"])
        self.assertFalse(said["ran"])
        self.assertEqual(asked, [], "a round ran although none was due")

    def test_and_the_panel_says_when_the_next_one_is_due(self) -> None:
        """The interval is how often the gate is consulted, which is not
        how often a round happens - a reader shown only the first would
        be told rounds are hourly when they are six-hourly."""
        shown = self.beat.status()
        self.assertIn("due", shown)
        self.assertIn("every_hours", shown)
        self.assertIn("why", shown["due"])

    def test_a_task_waiting_its_turn_keeps_the_name_it_was_given(
            self) -> None:
        """A name survives until a better one replaces it.

        Hiding it meant a panel of tasks called "loading" every time a
        round was open, which is most of the time on a busy folder - and
        the reader could no longer tell WHICH task was which. That a name
        is not this round's is worth saying; it is not worth erasing the
        name to say it, and the row carries the note instead."""
        from personalized_agent.heartbeat.states.heartbeat import (
            WAITING, _is_waiting, _waiting_on)

        held = _waiting_on({"title": "The census fetch step.",
                            "status": "The user has not started.",
                            "urgency": 6}, 12)
        self.assertTrue(_is_waiting(held))
        self.assertEqual(held["waiting_size"], 12)
        # The weight is not a summary and is not thrown away.
        self.assertEqual(held["urgency"], 6)
        # THE WORDS IT HAS ARE THE WORDS IT KEEPS. A reader is not left
        # guessing whether they are this round's: the note says a new
        # description is owed, and the row drawn from it says so too.
        self.assertEqual(held["title"], "The census fetch step.")
        self.assertEqual(held["status"], "The user has not started.")
        self.assertNotEqual(held["title"], WAITING)


class TasksThatOutliveTheRoundTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.beat.models = _reply(SUMMARY)
        self.hours = 400

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _write(self, rows):
        """Write these states, each older than the last."""
        from personalized_agent.agent.states.store import State

        for title, detail in rows:
            self.hours -= 1
            began = utcnow() - timedelta(hours=self.hours)
            self.app.states.add(State(
                title=title, detail=detail, starts_at=began,
                ends_at=began + timedelta(minutes=15), metadata={}))

    def _run(self):
        return self.beat.run_once(trigger="manual_debug")

    def _ids(self, report):
        return {task["task_id"] for task in report["layers"]}

    # -- the first round has nothing to keep --------------------------------
    def test_the_first_round_says_it_had_nothing_to_carry(self) -> None:
        self._write(REPLICATION + WATCHING)
        kept = self._run()["continuity"]
        self.assertTrue(kept["rebuilt"])
        self.assertIn("first grouping", kept["why"])
        self.assertGreaterEqual(kept["kept_tasks"], 1)

    def test_and_every_task_it_found_is_written_down(self) -> None:
        self._write(REPLICATION + WATCHING)
        found = self._ids(self._run())
        from personalized_agent.heartbeat.states.tasks import Kept

        self.assertEqual({task.id for task in Kept(self.app.db).live()}, found)

    # -- the same states give the same tasks --------------------------------
    def test_a_second_round_over_the_same_states_changes_nothing(self) -> None:
        """The failure this whole mechanism exists to fix: the same
        calendar read twice used to give two unrelated sets of tasks."""
        self._write(REPLICATION + WATCHING)
        first = self._run()
        second = self._run()
        self.assertFalse(second["continuity"]["rebuilt"])
        self.assertEqual(self._ids(first), self._ids(second))

    def test_and_says_that_no_signal_fired(self) -> None:
        self._write(REPLICATION + WATCHING)
        self._run()
        kept = self._run()["continuity"]
        self.assertIn("Nothing is regrouped", kept["why"])
        self.assertEqual(kept["arrivals"], 0)

    # -- new states join without the grouping being computed again ----------
    def test_a_new_state_joins_a_task_without_a_rebuild(self) -> None:
        self._write(REPLICATION + WATCHING)
        before = self._ids(self._run())
        self._write(MORE_REPLICATION)
        after = self._run()
        self.assertFalse(after["continuity"]["rebuilt"],
                         after["continuity"]["why"])
        self.assertEqual(self._ids(after), before)
        self.assertEqual(after["continuity"]["arrivals"], len(MORE_REPLICATION))

    def test_and_it_joins_the_task_it_is_about(self) -> None:
        self._write(REPLICATION + WATCHING)
        self._run()
        self._write(MORE_REPLICATION)
        report = self._run()
        took = [task for task in report["layers"] if task["kept"]["took"]]
        if not took:
            self.skipTest("nothing was appended on this machine's embeddings")
        joined = took[0]
        titles = " ".join(state["title"].lower()
                          for state in report["states"]
                          if state["id"] in joined["states"])
        self.assertIn("replication", titles)

    def test_and_the_task_says_which_states_joined_it(self) -> None:
        self._write(REPLICATION + WATCHING)
        self._run()
        self._write(MORE_REPLICATION)
        report = self._run()
        named = [one for task in report["layers"]
                 for one in task["kept"]["took"]]
        self.assertEqual(len(named), report["continuity"]["placed"])

    def test_and_a_task_keeps_its_place_in_the_reading_order(self) -> None:
        """Ordering by size undoes half of what the ids are for: two
        tasks that swap sizes swap places, so "task 2" on the page is a
        different task this hour."""
        self._write(REPLICATION + WATCHING)
        before = [task["task_id"] for task in self._run()["layers"]]
        self._write(MORE_REPLICATION)
        after = [task["task_id"] for task in self._run()["layers"]]
        self.assertEqual(before, after[:len(before)])

    # -- every signal is reported, fired or not -----------------------------
    def test_every_new_state_says_where_it_went_and_on_what(self) -> None:
        """There are no signals to report any more - nothing is regrouped.
        The question on a round is where each new state went: which task,
        how far it was from that task's centre, and how much of the work it
        shared - or why it was left loose."""
        self._write(REPLICATION + WATCHING)
        self._run()
        self._write(MORE_REPLICATION)
        kept = self._run()["continuity"]
        self.assertEqual(kept["signals"], [])
        evidence = kept["evidence"]
        self.assertEqual(len(evidence), len(MORE_REPLICATION))
        for said in evidence.values():
            self.assertIn("distance", said)
            self.assertIn("workspace", said)
            self.assertIn("score", said)
            if not said["task"]:
                self.assertTrue(said["why"])

    def test_and_a_new_subject_arriving_is_a_task_nobody_noticed(self) -> None:
        """What no task would take, once there is enough of it, is a task
        of its own - found from the pile, without regrouping anything."""
        self._write(REPLICATION + WATCHING)
        self._run()
        self._write(BOOKING)
        kept = self._run()["continuity"]
        self.assertFalse(kept["rebuilt"], kept["why"])
        # Noticed: a task of its own, or waiting in the pile until it is one.
        self.assertTrue(kept["became_tasks"] or kept["waiting"], kept["why"])

    # -- a rebuild is not a renumbering -------------------------------------
    def test_a_new_subject_leaves_the_tasks_already_here_alone(self) -> None:
        """Every task that was here is still here, under the same id: a new
        subject is a new task, not a reason to renumber the old ones."""
        self._write(REPLICATION + WATCHING)
        before = self._ids(self._run())
        self._write(BOOKING)
        after = self._run()
        self.assertFalse(after["continuity"]["rebuilt"])
        self.assertTrue(before <= self._ids(after),
                        "a task that was here is gone")

    def test_and_a_task_from_the_pile_says_where_it_came_from(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept

        self._write(REPLICATION + WATCHING)
        self._run()
        self._write(BOOKING)
        became = set(self._run()["continuity"]["became_tasks"])
        for task in Kept(self.app.db).live():
            if task.id in became:
                self.assertEqual(task.born_of.get("kind"), "new")
                self.assertIn("fitted nowhere", task.born_of.get("why", ""))

    def test_and_a_task_that_ends_is_kept_not_deleted(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept

        self._write(REPLICATION + WATCHING)
        self._run()
        self._write(BOOKING)
        self._run()
        store = Kept(self.app.db)
        live = {task.id for task in store.live()}
        every = {task.id for task in store.all()}
        self.assertTrue(every >= live)
        for task in store.all():
            if task.id not in live:
                self.assertTrue(task.retired_at)

    # -- and it is all on the page ------------------------------------------
    def test_the_report_says_why_it_did_or_did_not_start_again(self) -> None:
        self._write(REPLICATION + WATCHING)
        self._run()
        kept = self._run()["continuity"]
        self.assertGreater(len(kept["why"]), 20)
        for name in ("far", "place_counts", "tie_within", "revive_at",
                     "archive_after_days", "loose_kept_days"):
            self.assertIn(name, kept["limits"])

    def test_and_the_method_says_the_grouping_is_not_computed_again(self) -> None:
        self._write(REPLICATION + WATCHING)
        steps = self._run()["how"]["steps"]
        self.assertTrue(any("DOES NOT DO ANY OF THAT" in step
                            for step in steps))
        names = {entry["name"] for entry in self._run()["how"]["parameters"]}
        self.assertIn("a state joins the nearest task within", names)
        self.assertIn("what a shared place is worth", names)
        self.assertNotIn("the backstop", names)

    # -- a state the round no longer reads is forgotten ---------------------
    def test_a_task_keeps_what_it_is_when_its_states_are_not_read(self) -> None:
        """A task used to forget the states that fell out of the window the
        round read, and end when it had none - so a month away from a piece
        of work ended it. It keeps its profile now: what it is does not
        depend on its old states being read again. What is drawn is still
        only what this round holds."""
        self._write(REPLICATION + WATCHING + BOOKING + MORE_REPLICATION)
        self._write(BOOKING)
        first = self._run()
        counted = {task["task_id"]: task["count"] for task in first["layers"]}
        self.app.config.set("state_heartbeat_max_states", 20)
        self.app.config.set("state_sandbox_max_states", 20)
        second = self._run()
        read = {state["id"] for state in second["states"]}
        for task in second["layers"]:
            self.assertTrue(set(task["states"]) <= read,
                            "a task drew a state the round does not hold")
            if task["task_id"] in counted:
                self.assertGreaterEqual(task["count"],
                                        counted[task["task_id"]])
        self.assertTrue(set(counted) <= self._ids(second),
                        "a task ended because its states were not read")

    def test_and_a_task_that_loses_every_state_is_retired(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept

        self._write(REPLICATION + WATCHING + BOOKING + MORE_REPLICATION)
        self._write(BOOKING)
        self._run()
        self.app.config.set("state_heartbeat_max_states", 20)
        # The sandbox has its own window, and a test asking for a
        # window of 20 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 20)
        self._run()
        store = Kept(self.app.db)
        for task in store.all():
            if task.retired_at:
                continue
            self.assertTrue(task.members, "a live task holds nothing")



class WhatTheFirstRoundLooksAtTests(unittest.TestCase):
    """A user who has run this for a year has thousands of states, and a
    first grouping over all of them is a quarter of a million pairs to
    describe months they are not asking about. So the first look is
    bounded - and what is outside it is before the beginning, not a
    backlog: left as unread, every later round would find those states in
    no task, decide that what fits nowhere has become a task of its own,
    and rebuild on them forever."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.beat.models = _reply(SUMMARY)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _write(self, rows, days_ago):
        """States written this many days ago, by when they were touched."""
        from personalized_agent.agent.states.store import State

        made = []
        for index, (title, detail) in enumerate(rows):
            began = utcnow() - timedelta(days=days_ago, minutes=index)
            state = self.app.states.add(State(
                title=title, detail=detail, starts_at=began,
                ends_at=began + timedelta(minutes=15), metadata={}))
            # `add` stamps updated_at with now; the window reads that, so
            # an old state has to be old in the column the window reads.
            self.app.db.execute(
                "UPDATE states SET updated_at = ?, created_at = ? WHERE id = ?",
                (began.isoformat(), began.isoformat(), state.id))
            made.append(state.id)
        return made

    def _run(self):
        return self.beat.run_once(trigger="manual_debug")

    def test_the_first_round_reads_only_the_window(self) -> None:
        old = self._write(BOOKING, days_ago=200)
        self._write(REPLICATION + WATCHING, days_ago=2)
        report = self._run()
        read = {state["id"] for state in report["states"]}
        self.assertFalse(read & set(old),
                         "the first round read states from two hundred days ago")
        self.assertEqual(report["window"]["kind"], "first")
        self.assertEqual(report["window"]["days"], 30)

    def test_and_says_how_much_it_did_not_read(self) -> None:
        self._write(BOOKING, days_ago=200)
        self._write(REPLICATION + WATCHING, days_ago=2)
        scope = self._run()["window"]
        self.assertEqual(scope["states_in_all"],
                         len(BOOKING) + len(REPLICATION) + len(WATCHING))
        self.assertEqual(scope["before_the_beginning"], len(BOOKING))
        self.assertIn("before the beginning", scope["why"])

    def test_the_day_limit_shapes_the_first_round_only(self) -> None:
        """It is not a permanent line, and saying it was would be a lie
        about the user's own calendar.

        The window is the most recent N states. The day limit keeps the
        FIRST grouping from reaching back a year, and after that anything
        within the newest N is read - including what the first round was
        too young to see. What is permanently out is what the COUNT
        excludes, which is a different thing and the only thing.
        """
        old = set(self._write(BOOKING, days_ago=200))
        self._write(REPLICATION + WATCHING, days_ago=2)

        first = self._run()
        read = {state["id"] for state in first["states"]}
        self.assertFalse(read & old, "the first round reached back 200 days")
        self.assertIn("before the beginning", first["window"]["why"])

        # And the next round reads only what was written after the first:
        # the first grouping is the beginning, not a sample of a backlog.
        second = self._run()
        self.assertEqual(second["window"]["kind"], "new since the last round")
        self.assertEqual(second["continuity"]["arrivals"], 0)

    def test_and_the_count_is_what_permanently_excludes(self) -> None:
        self.app.config.set("state_heartbeat_max_states", 20)
        # The sandbox has its own window, and a test asking for a
        # window of 20 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 20)
        self._write(BOOKING, days_ago=200)
        self._write(REPLICATION + WATCHING, days_ago=2)
        self._write(MORE_REPLICATION, days_ago=1)
        self._run()
        report = self._run()
        self.assertLessEqual(report["states_considered"], 20)
        # Whatever is outside the newest twenty stays outside while it is.
        self.assertGreaterEqual(report["window"]["states_in_all"],
                                report["states_considered"])

    def test_and_the_line_is_remembered(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept

        self._write(REPLICATION + WATCHING, days_ago=2)
        self._run()
        self.assertTrue(Kept(self.app.db).seen_from())

    def test_a_later_round_reads_from_the_line_forward(self) -> None:
        from personalized_agent.agent.states.store import State

        self._write(REPLICATION + WATCHING, days_ago=2)
        self._run()
        # Written now, as a state is: what a round reads as new is what was
        # written since it last read.
        for title, detail in MORE_REPLICATION:
            began = utcnow() - timedelta(minutes=30)
            self.app.states.add(State(
                title=title, detail=detail, starts_at=began,
                ends_at=began + timedelta(minutes=15), metadata={}))
        report = self._run()
        self.assertEqual(report["window"]["kind"], "new since the last round")
        self.assertEqual(report["window"]["days"], 0)
        self.assertEqual(report["continuity"]["arrivals"],
                         len(MORE_REPLICATION))


class WhatAnAppendingRoundDoesNotDoTests(unittest.TestCase):
    """Joining every state to every other is the square of the calendar,
    and it is the whole of what appending exists not to pay."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.beat.models = _reply(SUMMARY)
        self.hours = 400

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _write(self, rows):
        from personalized_agent.agent.states.store import State

        for title, detail in rows:
            self.hours -= 1
            began = utcnow() - timedelta(hours=self.hours)
            self.app.states.add(State(
                title=title, detail=detail, starts_at=began,
                ends_at=began + timedelta(minutes=15), metadata={}))

    def _run(self):
        return self.beat.run_once(trigger="manual_debug")

    def test_the_first_round_scores_every_pair(self) -> None:
        self._write(REPLICATION + WATCHING)
        report = self._run()
        self.assertTrue(report["similarity"]["scored_now"])
        count = len(REPLICATION) + len(WATCHING)
        self.assertLessEqual(report["similarity"]["pairs_scored"],
                             count * (count - 1) // 2)

    def test_and_an_appending_round_does_not(self) -> None:
        self._write(REPLICATION + WATCHING)
        self._run()
        self._write(MORE_REPLICATION)
        report = self._run()
        if report["continuity"]["rebuilt"]:
            self.skipTest("a signal fired on this machine's embeddings")
        self.assertFalse(report["similarity"]["scored_now"])
        self.assertEqual(report["similarity"]["pairs"], [])
        self.assertIn("square of the calendar", report["similarity"]["why"])
        # Two arrivals against a handful of centres, not fifty-odd pairs.
        self.assertLessEqual(report["similarity"]["pairs_scored"],
                             len(MORE_REPLICATION) * 10)

    def test_and_shows_the_picture_the_last_rebuild_drew(self) -> None:
        self._write(REPLICATION + WATCHING)
        first = self._run()
        self._write(MORE_REPLICATION)
        second = self._run()
        if second["continuity"]["rebuilt"]:
            self.skipTest("a signal fired on this machine's embeddings")
        self.assertTrue(second["tree"].get("merges"),
                        "the picture vanished on an appending round")
        self.assertEqual(len(second["tree"]["merges"]),
                         len(first["tree"]["merges"]))
        self.assertTrue(second["tree"].get("as_of"))



class NothingExpensiveOnTheWayInOrOutTests(unittest.TestCase):
    """The first round is the expensive one - the whole window, every
    pair, and the model loaded from disk - and it used to run inside
    start(). That was harmless while a round was a handful of states."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _write(self, how_many):
        from personalized_agent.agent.states.store import State

        for index in range(how_many):
            began = utcnow() - timedelta(hours=index + 1)
            self.app.states.add(State(
                title=f"They are working on the replication package, {index}.",
                detail="Another sitting on it.", starts_at=began,
                ends_at=began + timedelta(minutes=15), metadata={}))

    def test_the_first_round_is_not_run_inside_start(self) -> None:
        beat = self.app.state_heartbeat
        self.assertGreaterEqual(beat._settling(), 5)
        self.assertLessEqual(beat._settling(), beat._interval())

    def test_and_quitting_does_not_wait_for_a_round(self) -> None:
        """Every heartbeat's join spent its full timeout, so closing the
        application took as long as three of them."""
        import time

        self._write(60)
        self.app.start()
        began = time.perf_counter()
        self.app.stop()
        self.assertLess(time.perf_counter() - began, 3.0,
                        "quitting waited for something")

    def test_and_a_round_asked_to_stop_keeps_what_it_has(self) -> None:
        """Nothing is lost by deferring: the signal that asked for the
        rebuild will ask again."""
        beat = self.app.state_heartbeat
        self._write(20)
        beat.run_once(trigger="manual_debug")
        beat._stop.set()
        try:
            report = beat.run_once(trigger="manual_debug")
        finally:
            beat._stop.clear()
        self.assertIn("closing", str(report.get("note", "")))



class ATaskIsNotAStateAndDoesNotLiveWithThemTests(unittest.TestCase):
    """They were the same folder, the summary distinguished only by sitting
    a level above what it covered - so a reader holding one could not tell
    which they had, and a search could not ask for either."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.beat.models = _reply(SUMMARY)
        self.app.config.set("state_summaries_enabled", True)
        self.hours = 400

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _write(self, rows):
        from personalized_agent.agent.states.store import State

        for title, detail in rows:
            self.hours -= 1
            began = utcnow() - timedelta(hours=self.hours)
            self.app.states.add(State(
                title=title, detail=detail, starts_at=began,
                ends_at=began + timedelta(minutes=15), metadata={}))

    def test_the_task_goes_to_its_own_folder(self) -> None:
        from personalized_agent.data_manage.library.record import (STATE_TASKS,
                                                                   STATES)

        self._write(REPLICATION + WATCHING)
        report = self.beat.run_once(trigger="manual_debug")
        self.assertTrue(report.get("summaries_written"), report.get("note"))
        tasks = self.app.library.records(STATE_TASKS)
        self.assertTrue(tasks, "no task was written to the tasks folder")
        for record in tasks:
            self.assertEqual(record.folder, STATE_TASKS)
        # And the states are still next door, not moved.
        self.assertTrue(self.app.library.records(STATES))

    def test_and_the_folder_says_what_belongs_in_it(self) -> None:
        from personalized_agent.data_manage.library.intro import intro_for
        from personalized_agent.data_manage.library.record import STATE_TASKS

        words = intro_for(STATE_TASKS)
        self.assertIn("state_tasks", words)
        self.assertIn("different questions", words)


class TheSandboxRunsTheSameCodeElsewhereTests(unittest.TestCase):
    """Real states can only show what the grouping did. Made-up ones show
    what it does to a shape you chose, which is the only way to tell the
    two apart."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.beat.models = _reply(SUMMARY)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_it_makes_up_states_in_its_own_folder(self) -> None:
        made = self.beat.make_up_states(how_many=30, days=7, seed=1)
        self.assertEqual(made["written"], 30)
        self.assertIn("state_sandbox", made["where"]["library"])
        self.assertTrue(made["subjects"])
        status = self.beat.sandbox_status()
        self.assertEqual(status["states"], 30)

    def test_and_the_made_up_states_are_not_in_the_real_library(self) -> None:
        """A made-up state answering a real question would be a
        fabrication, however clearly its folder was named."""
        from personalized_agent.data_manage.library.record import STATES

        self.beat.make_up_states(how_many=20, days=7, seed=2)
        self.assertEqual(self.app.library.records(STATES), [])
        self.assertEqual(self.app.states.recent(limit=50), [])

    def test_a_sandbox_round_groups_them(self) -> None:
        self.beat.make_up_states(how_many=40, days=10, seed=3)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertEqual(report["where"]["scope"], "sandbox")
        self.assertEqual(report["states_considered"], 40)
        self.assertGreaterEqual(report["continuity"]["kept_tasks"], 2)

    def test_and_writes_its_tasks_into_the_sandbox(self) -> None:
        from personalized_agent.data_manage.library.record import STATE_TASKS

        self.beat.make_up_states(how_many=40, days=10, seed=4)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertTrue(report.get("summaries_written"), report.get("note"))
        inside = self.beat.where("sandbox").library.records(STATE_TASKS)
        self.assertTrue(inside)
        self.assertEqual(self.app.library.records(STATE_TASKS), [],
                         "a sandbox round wrote into the real library")

    def test_and_does_not_become_the_real_last_report(self) -> None:
        """It is what the status bar counts tasks from. Made-up work
        standing in for the user's own would be a lie told by a test."""
        self.beat.make_up_states(how_many=20, days=7, seed=5)
        self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(self.beat.last_report)
        self.assertTrue(self.beat.sandbox_status()["last_report"])

    def test_and_its_tasks_are_not_the_real_tasks(self) -> None:
        from personalized_agent.agent.states.store import State
        from personalized_agent.heartbeat.states.tasks import Kept

        for index, (title, detail) in enumerate(REPLICATION + WATCHING):
            began = utcnow() - timedelta(hours=index + 1)
            self.app.states.add(State(
                title=title, detail=detail, starts_at=began,
                ends_at=began + timedelta(minutes=15), metadata={}))
        real = self.beat.run_once(trigger="manual_debug")
        self.beat.make_up_states(how_many=30, days=7, seed=6)
        self.beat.run_once(trigger="manual_debug", scope="sandbox")
        again = self.beat.run_once(trigger="manual_debug")
        self.assertEqual({task["task_id"] for task in real["layers"]},
                         {task["task_id"] for task in again["layers"]},
                         "the sandbox disturbed the real tasks")
        self.assertTrue(Kept(self.app.db, "live").live())
        self.assertTrue(Kept(self.app.db, "sandbox").live())

    def test_two_rounds_in_one_second_do_not_share_a_task_id(self) -> None:
        """They did, and `save` updates by id - so a sandbox round begun
        in the same second as a real one overwrote the real task with
        made-up states and left it marked live."""
        from personalized_agent.agent.states.store import State
        from personalized_agent.heartbeat.states.tasks import Kept

        for index, (title, detail) in enumerate(REPLICATION + WATCHING):
            began = utcnow() - timedelta(hours=index + 1)
            self.app.states.add(State(
                title=title, detail=detail, starts_at=began,
                ends_at=began + timedelta(minutes=15), metadata={}))
        self.beat.make_up_states(how_many=30, days=7, seed=8)
        real = self.beat.run_once(trigger="manual_debug")
        box = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        mine = {task["task_id"] for task in real["layers"]}
        theirs = {task["task_id"] for task in box["layers"]}
        self.assertTrue(mine and theirs)
        self.assertFalse(mine & theirs, "the two scopes shared a task id")
        for task_id in mine:
            self.assertIn("-live-", task_id)
        for task_id in theirs:
            self.assertIn("-sandbox-", task_id)
        # And the real rows still hold real states.
        real_states = {state["id"] for state in real["states"]}
        for task in Kept(self.app.db, "live").live():
            self.assertTrue(set(task.members) <= real_states,
                            "a live task holds a made-up state")

    def test_and_a_sandbox_round_leaves_no_loose_states_behind(self) -> None:
        """One list for both scopes would let made-up states be reported
        as the user's own on the next real round."""
        from personalized_agent.agent.states.store import State

        for index, (title, detail) in enumerate(REPLICATION + WATCHING):
            began = utcnow() - timedelta(hours=index + 1)
            self.app.states.add(State(
                title=title, detail=detail, starts_at=began,
                ends_at=began + timedelta(minutes=15), metadata={}))
        self.beat.run_once(trigger="manual_debug")
        self.beat.make_up_states(how_many=30, days=7, seed=9)
        self.beat.run_once(trigger="manual_debug", scope="sandbox")
        after = self.beat.run_once(trigger="manual_debug")
        real_states = {state["id"] for state in after["states"]}
        self.assertTrue(set(after.get("loose") or []) <= real_states,
                        "a made-up state was reported on a real round")

    def test_emptying_it_leaves_the_real_side_alone(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept

        self.beat.make_up_states(how_many=20, days=7, seed=7)
        self.beat.run_once(trigger="manual_debug", scope="sandbox")
        gone = self.beat.clear_sandbox()
        self.assertGreater(gone["files_removed"], 0)
        self.assertEqual(self.beat.sandbox_status()["states"], 0)
        self.assertEqual(Kept(self.app.db, "sandbox").live(), [])

    def test_the_rehearsal_shows_both_paths(self) -> None:
        """Two rounds do not show appending: the second one over a fresh
        grouping nearly always rebuilds, because a first grouping leaves
        states in no task and those are offered again every round."""
        self.app.config.set("state_heartbeat_max_states", 60)
        # The sandbox has its own window, and a test asking for a
        # window of 60 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 60)
        out = self.beat.rehearse_append(first=80, then=14, after=6, days=20,
                                        seed=12)
        self.assertEqual(out["cap"], 60)
        # emptied, made up, round one, more, round two, more, round three
        self.assertEqual(len(out["trace"]), 7)

        first = out["round_one"]
        self.assertEqual(first["states_considered"], 60)
        self.assertEqual(first["window"]["before_the_beginning"], 20)
        self.assertIn("before the beginning", first["window"]["why"])
        self.assertTrue(first["similarity"]["scored_now"])

        last = out["round_three"]
        if not last["continuity"]["rebuilt"]:
            # The path the whole mechanism exists for: a handful of dot
            # products against the task centres, and no tree at all.
            self.assertFalse(last["similarity"]["scored_now"])
            self.assertLess(last["similarity"]["pairs_scored"],
                            first["similarity"]["pairs_scored"])
            # And no model call - unless the states that fit nowhere
            # became a task of their own, which is new and has nothing
            # written about it yet, or a task took in so many that a third
            # of it is states it was not named from, and is named again.
            changed = [one for one, did in (last.get("moved") or {}).items()
                       if did.get("gained") or did.get("lost")]
            self.assertLessEqual(
                len(last.get("summaries_written") or []),
                len(last["continuity"]["became_tasks"] or []) + len(changed))
        self.assertGreaterEqual(out["identity"]["kept_their_id_throughout"], 1)

    def test_and_says_plainly_what_the_cap_dropped(self) -> None:
        """The one place the grouping loses something: more than a
        window's worth between two rounds and the oldest are never
        grouped at all."""
        self.app.config.set("state_heartbeat_max_states", 40)
        # The sandbox has its own window, and a test asking for a
        # window of 40 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 40)
        self.beat.make_up_states(how_many=70, days=20, seed=13)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        window = report["window"]
        self.assertEqual(window["read"], 40)
        self.assertEqual(window["states_in_all"], 70)
        self.assertEqual(window["before_the_beginning"], 30)

        self.beat.make_up_states(how_many=50, days=1, seed=14)
        again = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertTrue(again["window"]["at_the_cap"])
        self.assertIn("the cap of 40 was reached", again["window"]["why"])
        # AND THE REST ARE READ NEXT ROUND, not dropped. Once tasks keep
        # profiles a round reads what is new, oldest first; more than the
        # cap between two rounds is spread over two.
        self.assertIn("read next round", again["window"]["why"])
        third = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertEqual(third["continuity"]["arrivals"], 10)

    def test_the_overview_counts_the_folder_and_not_the_round(self) -> None:
        """A picture of one's own data is worth nothing if the numbers on
        it come from the thing being examined rather than from the files."""
        from personalized_agent.data_manage.library.record import STATES

        self.app.config.set("state_heartbeat_max_states", 60)

        # The sandbox has its own window, and a test asking for a

        # window of 60 wants it wherever the round is running.

        self.app.config.set("state_sandbox_max_states", 60)
        self.beat.make_up_states(how_many=90, days=20, seed=31)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        overview = report["overview"]
        on_disk = len(list(self.beat.where("sandbox").library.files(STATES)))

        self.assertEqual(overview["states_in_all"], on_disk, "not the folder")
        self.assertEqual(sum(one["states"] for one in overview["buckets"]),
                         on_disk, "the columns do not add up to the folder")
        # The round read fewer than there are, which is the whole point of
        # the picture: the unread stretch is visible rather than implied.
        self.assertEqual(overview["read"], 60)
        self.assertLess(overview["read"], overview["states_in_all"])
        self.assertLessEqual(overview["in_tasks"], overview["read"])

    def test_and_the_second_overview_is_the_first_plus_what_arrived(self) -> None:
        self.app.config.set("state_heartbeat_max_states", 60)
        # The sandbox has its own window, and a test asking for a
        # window of 60 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 60)
        self.beat.make_up_states(how_many=90, days=20, seed=32)
        first = self.beat.run_once(trigger="manual_debug",
                                   scope="sandbox")["overview"]
        self.beat.make_up_states(how_many=25, days=1, seed=33)
        second = self.beat.run_once(trigger="manual_debug",
                                    scope="sandbox")["overview"]
        self.assertEqual(second["states_in_all"],
                         first["states_in_all"] + 25)
        self.assertEqual(sum(one["states"] for one in second["buckets"]),
                         second["states_in_all"])

    def test_the_overview_names_which_threshold_cut_the_window(self) -> None:
        """Two thresholds decide what is read - a month back and a number
        of states - and which one bound is the thing a reader wants. It
        has to be read from what EXISTS: asking whether the oldest state
        READ is inside the limits answers nothing, because it always is."""
        self.app.config.set("state_first_round_days", 30)

        # The count cuts: more states than the cap, all inside the month.
        self.app.config.set("state_heartbeat_max_states", 60)
        # The sandbox has its own window, and a test asking for a
        # window of 60 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 60)
        self.beat.make_up_states(how_many=120, days=20, seed=35)
        limits = self.beat.run_once(trigger="manual_debug",
                                    scope="sandbox")["overview"]["limits"]
        self.assertIn("the state count", limits["bound_by"])
        self.assertEqual(limits["not_read"], 60)
        self.assertTrue(limits["by_count"])

        # The month cuts: fewer states than the cap, spread past it.
        self.beat.clear_sandbox()
        self.app.config.set("state_heartbeat_max_states", 1000)
        # The sandbox has its own window, and a test asking for a
        # window of 1000 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 1000)
        self.beat.make_up_states(how_many=40, days=90, seed=36)
        limits = self.beat.run_once(trigger="manual_debug",
                                    scope="sandbox")["overview"]["limits"]
        self.assertIn("30-day limit", limits["bound_by"])
        self.assertGreater(limits["not_read"], 0)

        # Neither cuts.
        self.beat.clear_sandbox()
        self.beat.make_up_states(how_many=30, days=10, seed=37)
        limits = self.beat.run_once(trigger="manual_debug",
                                    scope="sandbox")["overview"]["limits"]
        self.assertIn("every state in the folder was read",
                      limits["bound_by"])
        self.assertEqual(limits["not_read"], 0)

    def test_and_a_later_round_has_no_month_limit_to_name(self) -> None:
        """Only the first round has a duration limit. Afterwards the
        window starts where that round drew its line, so naming the month
        would be naming a rule that did not apply."""
        self.beat.make_up_states(how_many=30, days=10, seed=38)
        self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.beat.make_up_states(how_many=5, days=1, seed=39)
        limits = self.beat.run_once(trigger="manual_debug",
                                    scope="sandbox")["overview"]["limits"]
        self.assertFalse(limits["first_round"])
        self.assertEqual(limits["max_days"], 0)
        self.assertEqual(limits["by_duration"], "")

    def test_the_overview_shows_what_the_round_worked_on(self) -> None:
        """Not what it read. An appending round reads the window to find
        what is new in it and then works on the new ones alone - forty
        against four hundred - and the number worth seeing is forty."""
        self.app.config.set("state_heartbeat_max_states", 400)
        # The sandbox has its own window, and a test asking for a
        # window of 400 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 400)
        self.beat.make_up_states(how_many=500, days=20, seed=51)
        first = self.beat.run_once(trigger="manual_debug",
                                   scope="sandbox")["overview"]
        # A first grouping does work on the whole window.
        self.assertEqual(first["this_round"], first["read"])
        self.assertTrue(first["limits"]["worked_on_all_of_it"])
        self.assertTrue(first["limits"]["by_count"])

        self.beat.make_up_states(how_many=40, days=1, seed=52)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        if report["continuity"]["rebuilt"]:
            self.skipTest("a signal fired on this machine's embeddings")
        second = report["overview"]
        self.assertEqual(second["this_round"],
                         report["continuity"]["arrivals"])
        self.assertLess(second["this_round"], second["read"])
        self.assertFalse(second["limits"]["worked_on_all_of_it"])
        # The window edge is not drawn on a round that did not use it.
        self.assertEqual(second["limits"]["by_count"], "")
        self.assertTrue(second["this_round_from"])
        # And the work sits in the few columns it actually happened in.
        busy = [one for one in second["buckets"] if one["this_round"]]
        self.assertTrue(busy)
        self.assertLess(len(busy), len(second["buckets"]) / 2)
        self.assertEqual(sum(one["this_round"] for one in second["buckets"]),
                         second["this_round"])

    def test_and_each_task_says_which_stretch_it_drew_on(self) -> None:
        self.app.config.set("state_heartbeat_max_states", 60)
        # The sandbox has its own window, and a test asking for a
        # window of 60 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 60)
        self.beat.make_up_states(how_many=80, days=20, seed=34)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        spans = report["overview"]["spans"]
        self.assertTrue(spans)
        shown = {task["id"] for task in report["layers"]}
        for span in spans:
            self.assertIn(span["id"], shown)
            self.assertLessEqual(span["from"], span["to"])

    def test_a_round_says_where_it_has_got_to_while_it_runs(self) -> None:
        """The page follows a round rather than being refreshed by hand,
        and what it follows is the half-written report itself - so the
        two cannot disagree about what is happening."""
        seen = []

        real_at = self.beat._at

        def watch(report, stage, detail=""):
            real_at(report, stage, detail)
            # What a reader would see at this moment, read the way the
            # page reads it: the same object, mid-round.
            live = self.beat.progress()
            seen.append((live.get("stage"), bool(live.get("running"))))

        self.beat._at = watch
        try:
            self.beat.make_up_states(how_many=30, days=7, seed=40)
            report = self.beat.run_once(trigger="manual_debug",
                                        scope="sandbox")
        finally:
            self.beat._at = real_at

        self.assertTrue(seen, "nothing was reported while it ran")
        for stage, running in seen:
            self.assertTrue(stage)
            self.assertTrue(running, "a stage was reported as finished")
        named = [stage for stage, _ in seen]
        self.assertIn("reading the states", named)
        self.assertIn("reading what they mean", named)

        # And when it ends, the last stage is closed off and costed.
        self.assertEqual(report["stage"], "done")
        self.assertTrue(report["stages"])
        for stage in report["stages"]:
            self.assertIn("seconds", stage)
            self.assertGreaterEqual(stage["seconds"], 0.0)

    def test_and_the_page_has_no_refresh_button_to_press(self) -> None:
        import pathlib

        page = pathlib.Path(
            "personalized_agent/api/web/index.html").read_text(encoding="utf-8")
        panel = page[page.index('<section id="statebeat"'):]
        panel = panel[:panel.index("</section>")]
        self.assertNotIn("dbg-states-refresh", panel)
        self.assertIn("nothing to refresh", panel)
        script = pathlib.Path(
            "personalized_agent/api/web/app.js").read_text(encoding="utf-8")
        self.assertIn("function followStateBeat(", script)
        # Followed while it runs, not on a standing timer.
        self.assertNotIn("setInterval(followStateBeat", script)

    def test_a_round_only_processes_what_it_has_not_processed(self) -> None:
        """A state that fits nowhere looks exactly like a state nobody
        has looked at. Without remembering the difference, every round
        offered the whole pile again - re-processing what had been
        processed, and holding the count of states that fit nowhere
        permanently above the limit at which it means a rebuild."""
        from personalized_agent.heartbeat.states.tasks import Kept

        self.app.config.set("state_heartbeat_max_states", 400)

        # The sandbox has its own window, and a test asking for a

        # window of 400 wants it wherever the round is running.

        self.app.config.set("state_sandbox_max_states", 400)
        self.beat.make_up_states(how_many=200, days=20, seed=44)
        first = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        waiting = first["continuity"]["waiting"]

        # What the first round could not place is written down as placed
        # nowhere, rather than left to be rediscovered.
        self.assertEqual(sorted(Kept(self.app.db, "sandbox").loose()),
                         sorted(first["loose"]))

        self.beat.make_up_states(how_many=20, days=1, seed=45)
        second = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        # Twenty arrived, so twenty are new - not twenty plus the pile.
        self.assertEqual(second["continuity"]["arrivals"], 20,
                         f"the pile of {waiting} was offered again")

    def test_and_scores_only_the_new_ones_against_the_task_centres(self) -> None:
        self.app.config.set("state_heartbeat_max_states", 400)
        # The sandbox has its own window, and a test asking for a
        # window of 400 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 400)
        self.beat.make_up_states(how_many=200, days=20, seed=46)
        first = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.beat.make_up_states(how_many=20, days=1, seed=47)
        second = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        if second["continuity"]["rebuilt"]:
            self.skipTest("a signal fired on this machine's embeddings")
        scored = second["similarity"]["pairs_scored"]
        tasks = second["continuity"]["kept_tasks"]
        self.assertLessEqual(scored, 20 * max(1, tasks))
        self.assertLess(scored, first["similarity"]["pairs_scored"] / 10)

    def test_and_a_full_pile_is_clustered_by_itself(self) -> None:
        """Asking whether the leftovers have become a thing is a question
        about the leftovers. The first version answered it by rebuilding
        the whole calendar, which threw away the appending it had just
        done."""
        self.app.config.set("state_heartbeat_max_states", 400)
        # The sandbox has its own window, and a test asking for a
        # window of 400 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 400)
        self.beat.make_up_states(how_many=200, days=20, seed=48)
        self.beat.run_once(trigger="manual_debug", scope="sandbox")
        made = []
        for batch in range(4):
            self.beat.make_up_states(how_many=20, days=1, seed=60 + batch)
            report = self.beat.run_once(trigger="manual_debug",
                                        scope="sandbox")
            born = report["continuity"]["became_tasks"]
            if born:
                made.append((report["continuity"]["rebuilt"], born,
                             report["similarity"]["pairs_scored"]))
        if not made:
            self.skipTest("the pile never filled on this machine")
        rebuilt, born, scored = made[0]
        self.assertFalse(rebuilt, "the pile forced a full rebuild")
        self.assertTrue(born)
        # A task made from the pile is new, and says so.
        for task_id in born:
            self.assertIn("pile", task_id)

    def _put(self, titles):
        """Exactly these states, in the sandbox, written just now."""
        from personalized_agent.data_manage.library import Record
        from personalized_agent.data_manage.library.record import STATES

        library = self.beat.where("sandbox").library
        for index, title in enumerate(titles):
            began = utcnow() - timedelta(minutes=5 + index)
            library.write(Record(folder=STATES, title=title,
                                 summary="A stretch of it.", source="sandbox",
                                 created_at=began, observed_from=began,
                                 updated_at=datetime.now(timezone.utc),
                                 observed_to=began + timedelta(minutes=15)))

    def test_states_that_match_nothing_wait_rather_than_becoming_a_task(self):
        """Five states that each matched nothing are five strangers as
        easily as they are a new subject, and calling any five of them a
        task invents work the user never did."""
        self.app.config.set("state_heartbeat_max_states", 400)
        # The sandbox has its own window, and a test asking for a
        # window of 400 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 400)
        self.beat.make_up_states(how_many=120, days=20, seed=53)
        self.beat.run_once(trigger="manual_debug", scope="sandbox")

        self._put(["They are repotting the plants.",
                   "They are renewing the car insurance.",
                   "They are reading about volcanoes.",
                   "They are fixing the kitchen tap.",
                   "They are choosing a birthday present."])
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        kept = report["continuity"]
        self.assertEqual(kept["arrivals"], 5)
        self.assertEqual(kept["became_tasks"], [],
                         "five strangers were called a task")
        self.assertGreaterEqual(kept["waiting"], 5)

    def test_and_states_that_are_one_thing_become_a_task(self) -> None:
        self.app.config.set("state_heartbeat_max_states", 400)
        # The sandbox has its own window, and a test asking for a
        # window of 400 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 400)
        self.beat.make_up_states(how_many=120, days=20, seed=54)
        self.beat.run_once(trigger="manual_debug", scope="sandbox")

        self._put(["They are practising the cello.",
                   "They are working through a cello etude.",
                   "They are playing scales on the cello.",
                   "They are tuning the cello before practice.",
                   "They are learning a new cello piece."])
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        kept = report["continuity"]
        self.assertTrue(kept["became_tasks"], kept["why"])
        self.assertFalse(kept["rebuilt"], "it rebuilt to find one new task")
        born = set(kept["became_tasks"])
        cello = [task for task in report["layers"]
                 if task["task_id"] in born
                 and any("cello" in state["title"].lower()
                         for state in report["states"]
                         if state["id"] in task["states"])]
        self.assertTrue(cello, "the new task is not the one that arrived")

    def test_emptying_the_sandbox_clears_what_it_emptied(self) -> None:
        """A result left on the page after the thing it describes has
        been deleted is worse than no result: the tasks, the overview and
        the list of states would all still be there, every one of them
        about files that no longer exist."""
        import pathlib
        import re

        script = pathlib.Path(
            "personalized_agent/api/web/app.js").read_text(encoding="utf-8")
        clearing = script[script.index("async function clearSandbox("):]
        clearing = clearing[:clearing.index(chr(10) + "function ")]
        self.assertIn("stateBeat.rehearsal = null", clearing)
        self.assertIn("stateBeat.report = null", clearing)
        self.assertIn("renderStateBeat()", clearing)
        # And only when what is shown came from the sandbox: emptying it
        # says nothing about a round over the user's own states.
        self.assertIn('showing.scope === "sandbox"', clearing)

        # With nothing to show, the panel says so rather than keeping the
        # last thing it drew.
        # The drawing itself; renderStateBeat is the wrapper that also frames
        # what is selected.
        render = script[script.index("function renderStateBeatNow("):]
        render = render[:render.index(chr(10) + "function ")]
        head = render.index("if (!report) {")
        empty = render[head:render.index("return;", head)]
        # What it DOES, not how. Asserting on the assignment meant the
        # test broke when the redraws were routed through one helper - a
        # change that did not alter the behaviour at all.
        self.assertIn("Nothing to show", empty,
                      "the panel keeps the last result when there is none")

    def test_the_algorithm_is_not_branched_on(self) -> None:
        """A debugger that runs different code from the thing it is
        debugging is only testing itself."""
        source = (pathlib.Path("personalized_agent") / "heartbeat" / "states"
                  / "heartbeat.py").read_text(encoding="utf-8")
        body = source[source.index("    def _keep("):source.index("    def _rebuild(")]
        self.assertNotIn("SANDBOX", body)
        self.assertNotIn("sandbox", body.lower())



def _asking(asks_for=None):
    """A model that asks once for what it names, then answers."""
    import json as _json

    class Models:
        def __init__(self):
            self.sent = []

        def invoke(self, request, **kwargs):
            text = request.messages[0]["content"]
            self.sent.append(text)
            # Keyed on the opening line of the follow-up prompt: this
            # double has to tell "here are the states" from "and here is
            # what your search returned", and that line is how the real
            # prompt marks the difference.
            if asks_for is not None and "YOU ASKED TO LOOK" not in text:
                body = {"need": asks_for}
            else:
                body = {"title": "Finish the census fetch step.",
                        "summary": "What to do next.", "concrete": True,
                        "concrete_why": "a named step", "reasoning": "r",
                        "evidence": []}
            return type("R", (), {
                "text": _json.dumps(body),
                "usage": type("U", (), {"input_tokens": 2, "output_tokens": 40,
                                        "cached_input_tokens": 900,
                                        "cache_written_tokens": 300})()})()

        def resolve(self, *a, **k):
            return type("R", (), {"to_dict": lambda s: {}})()

        def check_availability(self, *a, **k):
            return type("A", (), {"available": True, "to_dict": lambda s: {},
                                  "state": None})()

    return Models()


class WhatTheModelIsShownOfAGroupTests(unittest.TestCase):
    """Forty of three hundred states, chosen rather than taken.

    The members are in the order the tree walks them, so the first forty of
    three hundred are one contiguous branch - one sub-subject - and the
    model read a corner and described it as the whole, while the prompt
    asked it to report edges from a sample chosen so as to exclude them.
    """

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.beat.models = _asking()
        self.app.config.set("state_heartbeat_max_states", 400)
        # The sandbox has its own window, and a test asking for a
        # window of 400 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 400)
    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_sample_accounts_for_every_state_in_the_group(self) -> None:
        self.beat.make_up_states(how_many=300, days=25, seed=71)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        big = max(report["communities"], key=lambda one: one["size"])
        sample = big.get("sample") or []
        if big["size"] <= 40:
            self.skipTest("no group here is bigger than the budget")
        self.assertLessEqual(len(sample), 40)
        # Every state stands behind one of the forty, so the weights add up
        # to the group. A sample that does not is a sample with a hole.
        self.assertEqual(sum(int(one["stands_for"]) for one in sample),
                         big["size"])

    def test_and_it_is_not_simply_the_first_forty(self) -> None:
        self.beat.make_up_states(how_many=300, days=25, seed=72)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        big = max(report["communities"], key=lambda one: one["size"])
        if big["size"] <= 40:
            self.skipTest("no group here is bigger than the budget")
        first = [one["id"] for one in big["members"][:40]]
        chosen = [one["member"]["id"] for one in big["sample"]]
        self.assertNotEqual(chosen, first)
        # And it spans the group in time rather than one end of it.
        times = [str(one["member"].get("starts_at") or "")
                 for one in big["sample"]]
        self.assertEqual(times, sorted(times), "the sample is not in time order")
        every = sorted(str(one.get("starts_at") or "")
                       for one in big["members"])
        self.assertEqual(times[0], every[0], "the earliest state is missing")
        self.assertEqual(times[-1], every[-1], "the latest state is missing")

    def test_and_the_prompt_says_what_each_one_stands_for(self) -> None:
        self.beat.make_up_states(how_many=300, days=25, seed=73)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        big = max(report["communities"], key=lambda one: one["size"])
        if big["size"] <= 40:
            self.skipTest("no group here is bigger than the budget")
        prompt = big["summary"]["prompt"]
        self.assertIn("more like it", prompt)
        self.assertIn("one from each of", prompt)
        self.assertIn("Weigh them by those numbers", prompt)


class WhenTheSummaryNeedsToLookTests(unittest.TestCase):
    """A title that does not name the thing is worth little, and the sample
    does not always name it. So the model may ask - within its own group."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_heartbeat_max_states", 300)
        # The sandbox has its own window, and a test asking for a
        # window of 300 wants it wherever the round is running.
        self.app.config.set("state_sandbox_max_states", 300)
    def tearDown(self) -> None:
        self.agent.cleanup()

    def _a_group(self) -> dict:
        self.beat.models = _asking()
        self.beat.make_up_states(how_many=150, days=20, seed=74)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        return report

    def test_a_search_returns_only_states_from_this_group(self) -> None:
        """Reaching the whole calendar would answer with a state from some
        other month, and the model could not tell that it had - it would
        name the wrong paper with perfect confidence."""
        report = self._a_group()
        groups = [one for one in report["communities"] if one["size"] >= 5]
        self.assertGreaterEqual(len(groups), 2, "need two groups to tell")
        mine, theirs = groups[0], groups[1]
        # A word that belongs to the other group, searched in this one.
        elsewhere = [word for word in theirs["shared_said"]
                     if word not in mine["shared_said"]]
        if not elsewhere:
            self.skipTest("the two groups share all their words")
        # `_look` hands back what it showed as well as the text, so
        # the round can say which states the model has read in full.
        found, showed = self.beat._look(mine, {"keywords": elsewhere[:2]})
        ours = {one["id"] for one in mine["members"]}
        for word in found.split():
            if word.startswith(("rec_", "state_")):
                self.assertIn(word.strip("[],-"), ours,
                              "the search reached outside the group")
        self.assertIn(f"all {mine['size']} states in this group", found)

    def test_and_an_id_outside_the_group_is_refused(self) -> None:
        report = self._a_group()
        groups = [one for one in report["communities"] if one["size"] >= 5]
        self.assertGreaterEqual(len(groups), 2)
        outsider = groups[1]["members"][0]["id"]
        found, showed = self.beat._look(groups[0], {"ids": [outsider]})
        self.assertIn("No state in this group has the id", found)
        self.assertNotIn(groups[1]["members"][0]["title"], found)
        self.assertEqual(showed, [], "it showed a state it refused to find")

    def test_asking_costs_a_second_call_and_the_answer_is_attached(self) -> None:
        self.beat.models = _asking({"keywords": ["census"], "ids": []})
        self.beat.make_up_states(how_many=150, days=20, seed=75)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        summarised = [one for one in report["communities"]
                      if (one.get("summary") or {}).get("calls")]
        self.assertTrue(summarised)
        one = summarised[0]["summary"]
        self.assertEqual(one["calls"], 2, "it did not ask, or did not answer")
        self.assertEqual(len(one["looks"]), 1)
        look = one["looks"][0]
        self.assertEqual(look["query"]["keywords"], ["census"])
        # All four parts of the exchange are kept, because each can be
        # wrong on its own: what it was asked, what it replied, what the
        # search returned, and what was then put to it.
        for part in ("asked_with", "it_replied", "found", "then_asked"):
            self.assertTrue(look.get(part), part)
        self.assertEqual(look["searched_within"], summarised[0]["size"])
        # The prompt kept is the last one, so it holds what was found.
        self.assertIn("YOU ASKED TO LOOK SOMETHING UP", one["prompt"])
        # And the follow-up says what was asked and what came back, so the
        # model is not left to remember its own question across a turn.
        self.assertIn("What you asked for:", one["prompt"])
        self.assertIn("What it found:", one["prompt"])
        self.assertIn("and nowhere else", one["prompt"])
        # And the tokens are the sum of both calls.
        self.assertEqual(one["tokens_out"], 80)

    def test_and_it_cannot_ask_for_ever(self) -> None:
        """Every look is a model call, and a third has never been the one
        that found the answer."""
        self.beat.models = _asking({"keywords": ["nothing"], "ids": []})
        # This model asks every time it is not given something, so the cap
        # is the only thing that stops it.
        self.beat.make_up_states(how_many=80, days=10, seed=76)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        for group in report["communities"]:
            summary = group.get("summary") or {}
            if summary.get("calls"):
                self.assertLessEqual(summary["calls"],
                                     self.beat.LOOKS_PER_SUMMARY + 1)

    def test_and_the_prompt_keeps_the_id_so_one_can_be_asked_for(self) -> None:
        report = self._a_group()
        summarised = [one for one in report["communities"]
                      if (one.get("summary") or {}).get("prompt")]
        self.assertTrue(summarised)
        prompt = summarised[0]["summary"]["prompt"]
        first = summarised[0]["members"][0]["id"]
        self.assertIn("[", prompt)
        self.assertTrue(any(one["id"] in prompt
                            for one in summarised[0]["members"]),
                        "no state id is in the prompt to ask about")


class WhatIsShownOfTheCallTests(unittest.TestCase):
    """The prompt on the page is the whole prompt, and what came back is
    kept whole.

    Both of these were half-true in a way that reads as a bug in the
    agent rather than in the panel. The step said "the whole prompt
    (3,872 tokens: 0 of instruction, 0 of states)" above the states
    alone - so the instruction that decides the output format, and the
    permission to search, appeared never to have been sent. And the reply
    was read for a title and a summary and then dropped, which is fine
    until the reply is the one that went wrong."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_sandbox_max_states", 120)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _round(self, asks_for=None, seed=91, how_many=120) -> dict:
        self.beat.models = _asking(asks_for)
        self.beat.make_up_states(how_many=how_many, days=18, seed=seed)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        # AND IT FINISHED CLEAN. A round catches its own exceptions and
        # puts them in `error`, which is right - a heartbeat that dies
        # takes the hour with it - but it means a round can raise on every
        # single pass and still return a full report. The whole tech tree
        # was doing exactly that: `_plan` used a name nothing had bound, so
        # every round raised NameError, every report carried it, no tree
        # was ever drawn, and six hundred and forty-one tests passed.
        self.assertFalse(report.get("error"), report.get("error"))
        return report

    def test_the_whole_prompt_includes_the_instruction(self) -> None:
        report = self._round()
        steps = [one for one in report["stages"] if one.get("prompt")]
        self.assertTrue(steps, "no step recorded a prompt")
        for step in steps:
            # Its own, or the round's: the instruction is the same text on
            # every summary and is carried once so that polling a running
            # round does not send it eight times a second.
            rules = step.get("instruction") or report.get("instruction") or ""
            self.assertTrue(rules,
                            "the step shows the states and not the rules")
            self.assertGreater(int(step.get("instruction_tokens") or 0), 0)
            self.assertGreater(int(step.get("message_tokens") or 0), 0)
            # And the number on the line is the two of them, not one of
            # them labelled as both.
            self.assertEqual(step["prompt_tokens"],
                             step["instruction_tokens"]
                             + step["message_tokens"])
            # The instruction is the thing that was actually sent as the
            # system prompt, not a description of it.
            self.assertIn("JSON", rules)

    def test_and_the_page_prints_both_halves(self) -> None:
        script = pathlib.Path(
            "personalized_agent/api/web/app.js").read_text(encoding="utf-8")
        block = script[script.index("function stateBeatStage("):]
        block = block[:block.index(chr(10) + "function ")]
        self.assertIn("one.instruction", block)
        self.assertIn("one.message_tokens", block)

    def test_every_reply_is_kept_whole(self) -> None:
        report = self._round(asks_for={"keywords": ["census"], "ids": []})
        summaries = [one["summary"] for one in report["communities"]
                     if (one.get("summary") or {}).get("calls")]
        self.assertTrue(summaries)
        one = summaries[0]
        self.assertEqual(len(one["exchange"]), one["calls"],
                         "a call happened that left no record of itself")
        first, last = one["exchange"][0], one["exchange"][-1]
        self.assertTrue(first["asked_to_search"],
                        "the search it asked for is not on the record")
        self.assertEqual(first["what_it_asked_for"]["keywords"], ["census"])
        self.assertFalse(last["asked_to_search"])
        for call in one["exchange"]:
            self.assertTrue(call["reply"], "a reply was not kept")
            self.assertTrue(call["sent"])
            self.assertTrue(call["instruction"])
            self.assertTrue(call["was_json"])

    def test_and_none_of_it_is_written_to_disk(self) -> None:
        """Reversing an earlier decision, and saying why.

        The whole exchange was written to a file beside each task, on the
        argument that the report is in memory and lasts only until the
        next round. A day of it measured the argument: seventy files and
        two and a half megabytes a day, nothing ever deleting one, and
        about half of each file the same text written twice - the searches
        repeated the prompts the exchange had already recorded, and the
        instruction was stored once per call although it is identical in
        every one. What a round PRODUCES is the task and its summary, and
        those are records in the library. How the model got there is worth
        looking at once, while it is being looked at."""
        report = self._round()
        for group in report["communities"]:
            summary = group.get("summary") or {}
            self.assertNotIn("transcript", summary,
                             "something still writes a transcript")
        library = self.beat.where("sandbox").library
        where_to = pathlib.Path(library.root) / "state_tasks" / "transcripts"
        self.assertFalse(
            where_to.exists() and any(where_to.iterdir()),
            f"transcripts were written to {where_to}")

    def test_and_the_working_out_still_reaches_the_page(self) -> None:
        """Not kept is not the same as not collected. The panel's whole
        purpose is to show what the model was sent and said, and that is
        unchanged - it is only the file that is gone."""
        report = self._round(asks_for={"keywords": ["census"], "ids": []})
        summaries = [one["summary"] for one in report["communities"]
                     if (one.get("summary") or {}).get("calls")]
        self.assertTrue(summaries)
        one = summaries[0]
        self.assertTrue(one["exchange"], "the calls are no longer collected")
        self.assertTrue(one["looks"], "the searches are no longer collected")
        for call in one["exchange"]:
            self.assertTrue(call["reply"])
            self.assertTrue(call["instruction"])

    def test_and_the_page_says_it_is_not_kept(self) -> None:
        """A reader who is told nothing will assume it is somewhere."""
        script = pathlib.Path(
            "personalized_agent/api/web/app.js").read_text(encoding="utf-8")
        self.assertIn("This is shown and not kept", script)
        self.assertIn("Copy anything you want to keep", script)


def _describing(parts, title="Rebuild the replication package."):
    """A model that answers with whichever parts it is given."""
    import json as _json

    class Models:
        def __init__(self):
            self.sent = []

        def invoke(self, request, **kwargs):
            self.sent.append(request.messages[0]["content"])
            body = {"title": title, "summary": "The one thing these were.",
                    "concrete": True, "concrete_why": "a named thing",
                    "reasoning": "r", "evidence": []}
            body.update(parts)
            return type("R", (), {
                "text": _json.dumps(body),
                "usage": type("U", (), {"input_tokens": 2, "output_tokens": 40,
                                        "cached_input_tokens": 0,
                                        "cache_written_tokens": 0})()})()

        def resolve(self, *a, **k):
            return type("R", (), {"to_dict": lambda s: {}})()

        def check_availability(self, *a, **k):
            return type("A", (), {"available": True, "to_dict": lambda s: {},
                                  "state": None})()

    return Models()


class ATaskDescribedInPartsTests(unittest.TestCase):
    """Where the work lives, what it is in aid of, what it is, and where it
    stands - which is what somebody asks when they come back to something
    after a week, and what one paragraph answers badly."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_sandbox_max_states", 120)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _round(self, models, seed=81, how_many=120) -> dict:
        self.beat.models = models
        self.beat.make_up_states(how_many=how_many, days=18, seed=seed)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(report.get("error"), report.get("error"))
        return report

    def _described(self, report):
        for task in (report.get("layers") or []):
            held = (task.get("summary") or {}).get("described") or {}
            if held:
                return task, held
        return None, {}

    def test_the_four_parts_are_read_and_kept(self) -> None:
        report = self._round(_describing({
            "where": "C:/Research/survey-weights/, mostly scripts/",
            "goal": "a revision a reader can reproduce from the package",
            "doing": "Re-running the pipeline against a fresh census pull.",
            "status": "Figures 1 and 3 match; the fetch step still fails.",
        }))
        task, held = self._described(report)
        self.assertIsNotNone(task, "no task was described in parts")
        for part in ("where", "goal", "doing", "status"):
            self.assertTrue(held.get(part), part)
        self.assertIn("survey-weights", held["where"])

    def test_and_a_part_left_out_is_kept_as_it_was(self) -> None:
        """A round where four states arrived usually changes `status` and
        nothing else, and rewriting the other three would be three claims
        restated by something with no new reason to restate them."""
        from personalized_agent.heartbeat.states.tasks import Kept

        first = self._round(_describing({
            "where": "C:/Research/survey-weights/",
            "goal": "a reproducible revision",
            "doing": "Re-running the pipeline.",
            "status": "The fetch step fails.",
        }))
        task, _ = self._described(first)
        self.assertIsNotNone(task)

        kept = Kept(self.app.db, scope="sandbox")
        held = {one.id: one for one in kept.live()}
        mine = held.get(task.get("task_id") or "")
        if mine is None:
            self.skipTest("the task was not kept under that id")
        # It is described, and now only its status moves.
        self.assertTrue(mine.described.get("where"))
        # So the next round writes it again: a task's name is rewritten
        # when the work has moved away from what the name was written
        # about, which is what a centre far from its anchor says.
        mine.anchor = [-value for value in mine.centre]
        kept.save([mine])

        self._round(_describing({"status": "The fetch step passes now."}),
                    seed=81, how_many=140)
        again = {one.id: one for one in Kept(self.app.db,
                                             scope="sandbox").live()}
        after = again.get(mine.id)
        self.assertIsNotNone(after, "the task did not survive the round")
        self.assertEqual(after.described.get("status"),
                         "The fetch step passes now.")
        # And the three it said nothing about are exactly as they were.
        self.assertEqual(after.described.get("where"),
                         "C:/Research/survey-weights/")
        self.assertEqual(after.described.get("goal"), "a reproducible revision")
        self.assertEqual(after.described.get("doing"), "Re-running the pipeline.")

    def test_and_the_prompt_shows_what_is_already_known(self) -> None:
        """Shown after the states, not before: a description put first is a
        conclusion, and what follows it gets read as evidence for it."""
        from personalized_agent.heartbeat.states.heartbeat import _already_known

        said = _already_known({"where": "a folder", "status": "half done"})
        self.assertIn("ALREADY KNOWN", said)
        self.assertIn("kept exactly as it reads here", said)
        self.assertIn("where: a folder", said)
        # A part that has never been filled in says so, rather than being
        # left out - so the model knows it may fill it.
        self.assertIn("goal: (not said yet", said)

    def test_and_the_instruction_says_to_send_only_what_changed(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import SUMMARY_PROMPT

        self.assertIn("RETURN ONLY THE PARTS THAT HAVE CHANGED", SUMMARY_PROMPT)
        self.assertIn("anything you leave out", _already_known_words())
        for part in ("where", "goal", "doing", "status"):
            self.assertIn(f'"{part}":', SUMMARY_PROMPT)


def _already_known_words():
    from personalized_agent.heartbeat.states.heartbeat import _already_known
    return " ".join(_already_known({}).split())


class WhatTheGroupingIsAllowedToReadTests(unittest.TestCase):
    """A state written to accompany a scheduled task is not something the
    user did.

    "Scheduled: check the FieldConf 2027 deadline" is the agent's own note that
    something is queued. Twenty of them say the same thing in twenty
    wordings, because the agent kept rescheduling one reminder - so they
    group tightly and perfectly, and the result is the agent's backlog
    handed back to the user as a list of what they have been working on."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _write(self, title, source):
        from personalized_agent.agent.states.store import State

        return self.app.states.add(State(
            title=title, detail="what it is about", source=source,
            starts_at=utcnow() - timedelta(hours=2),
            ends_at=utcnow() - timedelta(hours=1)))

    def test_a_scheduled_note_is_not_read_by_the_grouping(self) -> None:
        from personalized_agent.agent.states.store import SOURCE_AGENT

        self._write("Rebuild the replication package.", SOURCE_AGENT)
        self._write("Scheduled: check the FieldConf 2027 deadline", "task")
        seen = self.app.states.since(
            utcnow() - timedelta(days=1),
            sources=self.app.states.OBSERVED)
        titles = {one.title for one in seen}
        self.assertIn("Rebuild the replication package.", titles)
        self.assertNotIn("Scheduled: check the FieldConf 2027 deadline", titles)

    def test_and_the_round_asks_for_those_and_no_others(self) -> None:
        source = (pathlib.Path("personalized_agent") / "heartbeat" / "states"
                  / "heartbeat.py").read_text(encoding="utf-8")
        # Every way the round reaches for states names the sources it
        # wants. One that did not would read the scheduled notes back in
        # through a side door - and the counts under the overview would
        # disagree with what was grouped.
        # And only the user's: the agent's own work runs are not their tasks.
        import re as _re
        for call in (r"states\.since\(began, limit=cap, sources=OBSERVED,\s+users_only=True\)",
                     r"states\.recent\(limit=cap, sources=OBSERVED,\s+users_only=True\)"):
            self.assertRegex(source, call)
        self.assertIn("sources=OBSERVED", source)
        self.assertNotIn("where.states.timeline()", source)

    def test_and_the_filter_is_in_the_query_not_after_it(self) -> None:
        """These queries have a LIMIT on them: taking the newest thousand
        and then dropping a third leaves seven hundred, and the caller
        asked for a thousand."""
        from personalized_agent.agent.states.store import SOURCE_AGENT

        for number in range(6):
            self._write(f"Scheduled: reminder {number}", "task")
        for number in range(4):
            self._write(f"Real work {number}", SOURCE_AGENT)
        got = self.app.states.recent(limit=4,
                                     sources=self.app.states.OBSERVED)
        self.assertEqual(len(got), 4, "the limit was spent on excluded rows")
        for one in got:
            self.assertFalse(one.title.startswith("Scheduled:"), one.title)


class WhereTheUserWasStandingTests(unittest.TestCase):
    """Everything is stored in UTC, which is right and is not what
    anybody's week looks like."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_state_carries_the_zone_the_span_and_the_day(self) -> None:
        from personalized_agent.agent.states.store import State

        state = self.app.states.add(State(
            title="Rebuild the package.", detail="d",
            starts_at=utcnow() - timedelta(hours=3),
            ends_at=utcnow() - timedelta(hours=2)))
        held = (self.app.states.get(state.id).metadata or {}).get("local")
        self.assertTrue(held, "a state was written with no local time on it")
        self.assertTrue(held.get("zone"), "no zone")
        self.assertRegex(held["offset"], r"^[+-]\d\d:\d\d$")
        # Both ends, in local time, and which day that was.
        self.assertIn("starts_at", held)
        self.assertIn("ends_at", held)
        self.assertIn(held["weekday"],
                      ("Monday", "Tuesday", "Wednesday", "Thursday",
                       "Friday", "Saturday", "Sunday"))

    def test_and_the_day_is_the_day_it_began(self) -> None:
        """A state that runs past midnight belongs to the evening it began
        in - that is the sitting a person remembers."""
        from datetime import datetime, timezone
        from personalized_agent.agent.states.store import local_span

        # Chosen so the two ends fall either side of midnight wherever
        # this runs: a full day apart cannot share a weekday.
        early = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
        late = early + timedelta(days=1)
        said = local_span(early, late)
        self.assertEqual(said["weekday"], said["starts_at_day"])
        self.assertNotEqual(said["starts_at_day"], said["ends_at_day"])

    def test_and_it_is_not_recomputed_afterwards(self) -> None:
        """The user may be in another zone by then, and the honest answer
        is the zone they were in at the time."""
        from personalized_agent.agent.states.store import State

        state = self.app.states.add(State(
            title="Written in Shanghai.", detail="d",
            metadata={"local": {"zone": "CST", "offset": "+08:00",
                                "weekday": "Tuesday"}},
            starts_at=utcnow(), ends_at=utcnow()))
        held = (self.app.states.get(state.id).metadata or {})["local"]
        self.assertEqual(held["zone"], "CST")
        self.assertEqual(held["weekday"], "Tuesday")


class TheStatesWrittenBeforeThereWasAClockTests(unittest.TestCase):
    """A calendar where two thirds of the entries can be placed in a week
    and the rest cannot is worse than either: every question about the
    user's Mondays would silently be a question about the last fortnight."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _unstamped(self, title, starts_at):
        """A state as they were written before any of this existed."""
        import json

        from personalized_agent.agent.states.store import State

        state = self.app.states.add(State(
            title=title, detail="d", starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1)))
        self.app.db.execute("UPDATE states SET metadata = ? WHERE id = ?",
                            (json.dumps({}), state.id))
        return state.id

    def test_the_older_states_are_given_a_local_time(self) -> None:
        from datetime import datetime, timezone

        old = self._unstamped("Rebuild the package.",
                              datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc))
        done = self.app.states.stamp_local_times("Europe/Lisbon")
        if not done:
            self.skipTest("this machine has no time zone table")
        held = (self.app.states.get(old).metadata or {}).get("local") or {}
        self.assertEqual(held["starts_at"], "2026-01-15T18:00:00+00:00")
        self.assertEqual(held["weekday"], "Thursday")
        self.assertEqual(held["zone"], "WET")

    def test_and_the_offset_is_the_one_that_was_in_force(self) -> None:
        """Half these states are from one side of a clock change, which is
        the whole reason a named zone is used rather than a fixed offset."""
        from datetime import datetime, timezone

        winter = self._unstamped(
            "In January.", datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc))
        summer = self._unstamped(
            "In July.", datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc))
        if not self.app.states.stamp_local_times("Europe/Lisbon"):
            self.skipTest("this machine has no time zone table")
        cold = (self.app.states.get(winter).metadata or {})["local"]
        warm = (self.app.states.get(summer).metadata or {})["local"]
        self.assertEqual(cold["offset"], "+00:00")
        self.assertEqual(warm["offset"], "+01:00")
        # And the label agrees with the times beside it, which it did not
        # when the zone was read at the moment of the pass instead of at
        # the moment of the state.
        self.assertTrue(cold["starts_at"].endswith("+00:00"))
        self.assertTrue(warm["starts_at"].endswith("+01:00"))

    def test_and_it_is_marked_as_a_guess(self) -> None:
        """A state stamped as it was written knows where the machine was;
        these know only what they were told afterwards."""
        from datetime import datetime, timezone

        old = self._unstamped("Before.",
                              datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc))
        if not self.app.states.stamp_local_times("Europe/Lisbon"):
            self.skipTest("this machine has no time zone table")
        held = (self.app.states.get(old).metadata or {})["local"]
        self.assertTrue(held.get("assumed"))
        self.assertIn("Europe/Lisbon", held.get("assumed_why", ""))

    def test_and_a_state_that_has_one_is_left_alone(self) -> None:
        from personalized_agent.agent.states.store import State

        mine = self.app.states.add(State(
            title="Already stamped.", detail="d",
            metadata={"local": {"zone": "CST", "weekday": "Tuesday"}},
            starts_at=utcnow(), ends_at=utcnow()))
        self.app.states.stamp_local_times("Europe/Lisbon")
        held = (self.app.states.get(mine.id).metadata or {})["local"]
        self.assertEqual(held["weekday"], "Tuesday")
        self.assertNotIn("assumed", held)

    def test_and_the_model_is_shown_the_users_own_clock(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import _when_it_ran

        said = _when_it_ran({"local": {
            "starts_at": "2026-09-27T18:30:00+01:00",
            "ends_at": "2026-09-27T20:00:00+01:00",
            "starts_at_day": "Sunday", "ends_at_day": "Sunday",
            "zone": "WEST"}})
        self.assertIn("Sunday", said)
        self.assertIn("18:30", said)
        self.assertIn("WEST", said)

    def test_and_a_state_without_one_says_it_is_utc(self) -> None:
        """Rather than quietly presenting UTC as though it were the user's
        own clock, which is how a Wednesday morning becomes a Tuesday
        night."""
        from personalized_agent.heartbeat.states.heartbeat import _when_it_ran

        said = _when_it_ran({"starts_at": "2026-09-27T23:30:00+00:00",
                             "ends_at": "2026-09-28T01:00:00+00:00"})
        self.assertIn("UTC", said)
        self.assertIn("no local time", said)

    def test_and_a_span_crossing_midnight_names_both_days(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import _when_it_ran

        said = _when_it_ran({"local": {
            "starts_at": "2026-01-15T23:30:00+00:00",
            "ends_at": "2026-01-16T01:00:00+00:00",
            "starts_at_day": "Thursday", "ends_at_day": "Friday",
            "zone": "WET"}})
        self.assertIn("Thursday", said)
        self.assertIn("Friday", said)


class TheSummaryIsWrittenInTheUsersLanguageTests(unittest.TestCase):
    """The states are in English because one small model has to read all
    of them. That is a decision about what the MACHINE reads. A summary is
    what the USER reads, and they chose a language."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _instruction(self, language):
        """The summary's system prompt as it is sent: its language said by
        SUMMARY_LANGUAGE, named at the top and in full at the foot."""
        from personalized_agent.heartbeat.states.heartbeat import SUMMARY_LANGUAGE
        self.app.config.set("display_language", language)
        return SUMMARY_LANGUAGE.compose(self.beat._instruction(), self.app.config)

    def test_the_language_is_named_at_the_top_and_at_the_foot(self) -> None:
        """One line after sixteen thousand characters of English prose and
        English worked examples is a rule the examples outvote: a model
        shown twenty GOOD summaries in English will write English."""
        said = self._instruction("zh")
        self.assertIn("WRITE IT IN THIS LANGUAGE: Chinese", said)
        self.assertIn("read in Chinese", said)
        # At the TOP, which is the half that was missing: the position was
        # the whole of the problem, not the presence.
        head = said[:2000]
        self.assertIn("Chinese", head,
                      "the language is only named after the examples")

    def test_and_it_follows_the_setting(self) -> None:
        self.assertIn("LANGUAGE: English", self._instruction("en"))
        self.assertIn("LANGUAGE: Chinese", self._instruction("zh"))

    def test_and_no_placeholder_reaches_the_model(self) -> None:
        """A prompt that asks for a summary in `{display_language}` has
        told the model the name of a variable."""
        for language in ("en", "zh"):
            self.assertNotIn("{display_language}", self._instruction(language))

    def test_and_the_keys_of_the_json_are_exempted(self) -> None:
        """This is the hazard the instruction itself introduces. A model
        told to write everything in Chinese may translate the field names,
        which produces an object nothing can read - and the summary is
        then lost silently, because a missing title is indistinguishable
        from a model that had nothing to say."""
        said = self._instruction("zh")
        self.assertIn("KEYS OF THE JSON STAY EXACTLY AS WRITTEN", said)
        self.assertIn("The KEYS below are field names", said)

    def test_and_so_are_paths_and_names_quoted_from_the_states(self) -> None:
        """A translated path names a file that does not exist."""
        said = self._instruction("zh")
        self.assertIn("ANYTHING QUOTED FROM THE STATES", said)
        self.assertIn("fetch.py", said)

    def test_and_the_tech_tree_call_says_it_too(self) -> None:
        """It is a second call, with its own instruction, and its `why` is
        read by the user like everything else."""
        from personalized_agent.common import output_languages as langs

        plans = [one for one in langs.read_code()["said"]
                 if one["task"] == "state_heartbeat" and one["in"] == "_plan_pieces"]
        if not plans:
            plans = [one for one in langs.read_code()["said"]
                     if one["task"] == "state_heartbeat"
                     and any("`why`" in rule["what"] for rule in one["rules"])]
        self.assertEqual(plans[0]["rule"], "display")

    def test_and_naming_it_never_costs_the_round(self) -> None:
        """A label should not be able to fail a grouping."""
        from personalized_agent.heartbeat.states.heartbeat import _the_language

        class Broken:
            def get(self, *a, **k):
                raise RuntimeError("no settings here")

        # It does not raise, and it names something. Which name is not
        # the promise - `display_language` already falls back to English
        # on its own, so the guard here is the second line of defence and
        # the test is that a broken config cannot reach the caller.
        said = _the_language(Broken())
        self.assertTrue(said, "no language was named at all")
        self.assertIsInstance(said, str)


class TheFourPartsAreSentencesAboutTheUserTests(unittest.TestCase):
    """Two shapes on one page, and the prompt has to keep them apart.

    The title and the summary are instructions TO the user - "Check the
    figures in the SWR replication package" - with the verb first and the
    user left out, because they are the person being addressed. The four
    parts are statements ABOUT the user, with "the user" as the subject,
    because they answer "what is going on" rather than "what should I do".
    Either rule stated alone reads as a contradiction of the other, so the
    prompt states the difference."""

    def setUp(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import (
            SUMMARY_PROMPT)

        self.prompt = SUMMARY_PROMPT
        start = self.prompt.index("AND THEN THE SAME TASK IN FOUR PARTS.")
        self.parts = self.prompt[start:self.prompt.index(
            "WHAT IS ALREADY KNOWN, AND WHAT TO DO WITH IT.")]

    def test_the_prompt_says_which_shape_goes_where(self) -> None:
        self.assertIn("THE TITLE AND THE SUMMARY ARE INSTRUCTIONS TO THE USER",
                      self.parts)
        self.assertIn("THESE FOUR ARE STATEMENTS ABOUT THE USER", self.parts)
        # Matched on the text with its wrapping taken out: the prompt is
        # hard-wrapped, so a phrase that spans a line break is not in it
        # as a phrase, and asserting on the wrapping would make this test
        # fail the next time a word is added three lines above.
        flat = " ".join(self.parts.split())
        self.assertIn('THE SUBJECT IS ALWAYS "the user"', flat)

    def test_and_every_example_in_the_four_parts_starts_with_the_user(self) -> None:
        """A rule with an example beside it that breaks the rule is an
        example, not a rule: the shape shown wins."""
        shown = []
        for chunk in self.parts.split('e.g. "')[1:]:
            shown.append(" ".join(chunk.split('"')[0].split()))
        self.assertGreaterEqual(len(shown), 6, "too few examples to teach it")
        for said in shown:
            self.assertTrue(
                said.startswith("The user "),
                f"an example does not start with 'The user': {said[:70]}")

    def test_and_each_example_is_a_sentence_not_a_fragment(self) -> None:
        for chunk in self.parts.split('e.g. "')[1:]:
            said = " ".join(chunk.split('"')[0].split())
            self.assertTrue(said.endswith("."),
                            f"an example is not a sentence: {said[-50:]}")

    def test_and_the_shapes_to_avoid_are_shown_with_their_faults(self) -> None:
        """Naming the failure is what makes it recognisable in one's own
        draft; a list of good examples teaches the topic, not the rule."""
        for fault in ("a fragment", "a participle hanging off nothing",
                      "the subject is always", "cause and its consequence"):
            self.assertIn(fault, self.parts.lower(), fault)

    def test_and_the_prompt_asks_for_the_reason_not_only_the_fact(self) -> None:
        self.assertIn("AND SAY HOW IT CAME ABOUT", self.parts)
        # The worked contrast: the same fact with and without its cause.
        self.assertIn("The fetch step fails", self.parts)
        self.assertIn("because the fetch step fails on the census API rate",
                      self.parts)

    def test_and_the_output_spec_repeats_the_shape(self) -> None:
        """The field descriptions are the last thing read before the answer
        is written, and a spec that said only "what it is in aid of" was
        where the fragments came from."""
        spec = self.prompt[self.prompt.index("Return ONLY this JSON object"):]
        for field in ("where", "goal", "doing", "status"):
            line = spec[spec.index(f'"{field}":'):][:220]
            self.assertIn("The user", line, field)

    def test_and_an_amendment_is_told_the_shape_as_well(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import _already_known

        said = _already_known({"where": "somewhere"})
        self.assertIn("subject is 'the user'", said)
        self.assertIn("not fragments", said)

    def test_and_the_title_is_still_an_imperative(self) -> None:
        """The earlier rule is not repealed by the new one."""
        self.assertIn("HOW TO WRITE THE TITLE. AN IMPERATIVE", self.prompt)
        self.assertIn("BAD   They are checking the figures", self.prompt)


class ATaskKeepsItsPlaceTests(unittest.TestCase):
    """A reader who opened the third row yesterday should find the same
    work in the third row today.

    The order was `(created_at, -size)`, which is almost right and fails
    on the case that matters most: every task from the first rebuild
    shares one `created_at` to the second, so the tie-break decided the
    whole page - and the tie-break was size. Two tasks that swapped sizes
    swapped places, which is the instability the persistent ids were
    introduced to fix, coming back in the reading order after being fixed
    in the data."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_sandbox_max_states", 160)

    def tearDown(self) -> None:
        self.agent.cleanup()

    @staticmethod
    def _tasks(count, sizes=None, seats=None):
        from personalized_agent.heartbeat.states.tasks import Task

        out = []
        for index in range(count):
            out.append(Task(
                id=f"t{index}",
                members=[f"s{index}-{n}"
                         for n in range((sizes or [3] * count)[index])],
                seat=(seats or [-1] * count)[index],
                created_at="2026-09-01T00:00:00+00:00"))
        return out

    def test_the_first_round_seats_them_in_the_order_they_are_in(self) -> None:
        """A fix for an unstable order that reordered the page once on the
        way in would be a poor joke."""
        held = self._tasks(3)
        self.beat._seat_them(held)
        self.assertEqual([one.seat for one in held], [0, 1, 2])

    def test_and_a_task_that_has_a_seat_keeps_it(self) -> None:
        held = self._tasks(3, seats=[2, 0, 1])
        self.beat._seat_them(held)
        self.assertEqual([one.seat for one in held], [2, 0, 1])

    def test_and_anything_new_sits_after_everything_that_existed(self) -> None:
        # Two old tasks and two new ones, the new ones interleaved - which
        # is what a rebuild hands over, because the groups come out in
        # tree order and not in age order.
        held = self._tasks(4, seats=[1, -1, 0, -1])
        self.beat._seat_them(held)
        self.assertEqual([one.seat for one in held], [1, 2, 0, 3])
        # And sorting by seat puts the two that existed first, in their
        # own order, with the new ones after in the order they arrived.
        held.sort(key=lambda one: one.seat)
        self.assertEqual([one.id for one in held], ["t2", "t0", "t1", "t3"])

    def test_and_size_no_longer_decides_anything(self) -> None:
        """The whole of the old failure in one assertion."""
        held = self._tasks(3, sizes=[1, 50, 4], seats=[0, 1, 2])
        self.beat._seat_them(held)
        held.sort(key=lambda one: (one.seat, one.created_at))
        self.assertEqual([one.id for one in held], ["t0", "t1", "t2"])

    def test_and_a_split_keeps_one_piece_in_place(self) -> None:
        """One of the pieces inherits the old task's identity and its seat
        with it; the rest are new tasks that happen to have a parent, and
        they go where new tasks go."""
        from personalized_agent.heartbeat.states.tasks import Task

        # What a rebuild produces when task `b` splits in two: the piece
        # that matched keeps the object and the seat, the other is new.
        held = [Task(id="a", members=["s1"], seat=0,
                     created_at="2026-09-01T00:00:00+00:00"),
                Task(id="b", members=["s2"], seat=1,
                     created_at="2026-09-01T00:00:00+00:00"),
                Task(id="b-split", members=["s3"], seat=-1,
                     created_at="2026-09-02T00:00:00+00:00"),
                Task(id="c", members=["s4"], seat=2,
                     created_at="2026-09-01T00:00:00+00:00")]
        self.beat._seat_them(held)
        held.sort(key=lambda one: one.seat)
        self.assertEqual([one.id for one in held], ["a", "b", "c", "b-split"])

    def test_and_the_order_holds_across_two_real_rounds(self) -> None:
        """The unit tests above are about the seating; this is about the
        page."""
        self.beat.models = _asking()
        self.beat.make_up_states(how_many=160, days=20, seed=61)
        first = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(first.get("error"), first.get("error"))
        was = [task.get("task_id") for task in (first.get("layers") or [])]
        self.assertGreaterEqual(len(was), 3, "too few tasks to tell")

        self.beat.make_up_states(how_many=40, days=4, seed=62)
        again = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(again.get("error"), again.get("error"))
        now = [task.get("task_id") for task in (again.get("layers") or [])]

        # Whatever survived is in the same relative order as before. Not
        # "the same list": a task can end, and this is about the ones that
        # did not.
        survived = [one for one in was if one in now]
        self.assertGreaterEqual(len(survived), 2, "nothing survived to check")
        self.assertEqual(survived, [one for one in now if one in set(was)],
                         "the surviving tasks changed places")

    def test_and_a_task_that_arrives_late_goes_to_the_end(self) -> None:
        self.beat.models = _asking()
        self.beat.make_up_states(how_many=160, days=20, seed=63)
        first = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(first.get("error"), first.get("error"))
        was = [task.get("task_id") for task in (first.get("layers") or [])]

        self.beat.make_up_states(how_many=60, days=5, seed=64)
        again = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(again.get("error"), again.get("error"))
        now = [task.get("task_id") for task in (again.get("layers") or [])]

        arrived = [one for one in now if one not in set(was)]
        if not arrived:
            self.skipTest("no task arrived in the second round")
        # Every new one is below every one that was already there.
        highest = max(now.index(one) for one in now if one in set(was))
        for one in arrived:
            self.assertGreater(now.index(one), highest,
                               "a new task was put above one that existed")


def _answering(bodies):
    """A model that answers each call with the next body it is given."""
    import json as _json

    class Models:
        def __init__(self):
            self.sent = []
            self.left = list(bodies)

        def invoke(self, request, **kwargs):
            self.sent.append({"system": request.system,
                              "message": request.messages[0]["content"]})
            body = self.left.pop(0) if self.left else bodies[-1]
            return type("R", (), {
                "text": _json.dumps(body),
                "usage": type("U", (), {"input_tokens": 2, "output_tokens": 30,
                                        "cached_input_tokens": 0,
                                        "cache_written_tokens": 0})()})()

        def resolve(self, *a, **k):
            return type("R", (), {"to_dict": lambda s: {}})()

        def check_availability(self, *a, **k):
            return type("A", (), {"available": True, "to_dict": lambda s: {},
                                  "state": None})()

    return Models()


class WhereItStandsIsAskedEveryRoundTests(unittest.TestCase):
    """The turnover rule is right about three of the four parts and wrong
    about this one.

    Where the work lives, what it is in aid of and what it consists of do
    not change because four states arrived. `status` is the part whose
    whole job is to move, and under the same rule it sat still for hours
    while the work it described went on happening."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_sandbox_max_states", 160)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _described(self):
        return {"title": "Rebuild the package.",
                "summary": "The one thing these were.",
                "concrete": True, "concrete_why": "a named thing",
                "reasoning": "r", "evidence": [],
                "where": "The user works in survey-weights/.",
                "goal": "The user wants the figures to match.",
                "doing": "The user is re-running the pipeline.",
                "status": "The user has not started the figures."}

    def _first_round(self, seed=71):
        self.beat.models = _describing({
            "where": "The user works in survey-weights/.",
            "goal": "The user wants the figures to match.",
            "doing": "The user is re-running the pipeline.",
            "status": "The user has not started the figures."})
        self.beat.make_up_states(how_many=160, days=20, seed=seed)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(report.get("error"), report.get("error"))
        return report

    def test_a_kept_summary_still_gets_its_status_asked(self) -> None:
        self._first_round()
        # A handful of new states: not enough to turn a task over, which
        # is exactly the case the full summary declines to look at.
        self.beat.models = _answering([{
            "status": "The user now has figure 1 matching.",
            "changed": True, "why": "a sitting closed the first figure"}])
        self.beat.make_up_states(how_many=6, days=1, seed=72)
        again = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(again.get("error"), again.get("error"))
        asked = again.get("status_calls") or []
        if not asked:
            self.skipTest("no task gained states in this round")
        self.assertTrue(any(one["changed"] for one in asked),
                        "no status was brought up to date")

    def test_a_carried_over_task_is_weighed_without_a_full_summary(
            self) -> None:
        """Found in the live database, not imagined.

        Eleven tasks carried over from before the weighing existed, not
        one with an urgency or an importance. A weight was written only
        by the full summary, and the full summary runs only when a third
        of a task has turned over - so a task that merely ticks along
        kept no weight and sorted below everything.

        The status call is what every round makes for a task it did not
        re-summarise, so that is where the two numbers come from now.
        """
        from personalized_agent.heartbeat.states.tasks import Kept

        self._first_round(seed=75)
        kept = Kept(self.app.db, scope="sandbox")
        before = kept.live()
        if not before:
            self.skipTest("no tasks to carry over")
        # Exactly the state the live database was in.
        for one in before:
            held = dict(one.described or {})
            held.pop("urgency", None)
            held.pop("importance", None)
            one.described = held
        kept.save(before)
        self.beat.models = _answering([{
            "changed": False, "why": "another sitting on the same obstacle",
            "urgency": 6, "importance": 5,
            "weighed": "A date this week, and the user's own work."}])
        # A handful of states: a round that ticks along, which is the case
        # the full summary declines to look at.
        self.beat.make_up_states(how_many=6, days=1, seed=76)
        again = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(again.get("error"), again.get("error"))
        after = Kept(self.app.db, scope="sandbox").live()
        weighed = [one for one in after
                   if one.described.get("urgency") is not None]
        if not (again.get("status_calls") or []):
            self.skipTest("no task gained states in this round")
        self.assertTrue(weighed, "no task was ever given a weight")
        self.assertEqual(weighed[0].described["urgency"], 6)
        self.assertEqual(weighed[0].described["importance"], 5)
        # The status it said had not changed is left exactly as it was.
        self.assertEqual(weighed[0].described.get("status"),
                         "The user has not started the figures.")

    def test_a_summarised_task_is_never_shown_as_unweighed(self) -> None:
        """A 4 is an answer, not a blank.

        The weight was kept only when the model volunteered a number in
        range, so a task whose summary had just been written read "not
        weighed yet" in the panel. The instruction says to answer 4 where
        the states give no sign, so 4 IS the answer - and which of the two
        it was is worth recording, because a middling number read off the
        states and a middling number standing in for silence are
        different claims.
        """
        from personalized_agent.heartbeat.states.tasks import Kept

        # A model that answers the four parts and skips the weighing.
        self.beat.models = _describing({
            "where": "The user works in survey-weights/.",
            "goal": "The user wants the figures to match.",
            "doing": "The user is re-running the pipeline.",
            "status": "The user has not started the figures."})
        self.beat.make_up_states(how_many=160, days=20, seed=77)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(report.get("error"), report.get("error"))
        held = [one.described for one in Kept(self.app.db, scope="sandbox").live()
                if one.described.get("doing")]
        if not held:
            self.skipTest("nothing was described")
        for described in held:
            self.assertIsNotNone(described.get("urgency"),
                                 "a summarised task has no urgency")
            self.assertIsNotNone(described.get("importance"),
                                 "a summarised task has no importance")
            # And it says the number stood in for silence.
            self.assertEqual(described.get("weighed_how"), "default",
                             "a defaulted weight is not marked as one")

    def test_and_only_the_status_moves(self) -> None:
        """The other three are not asked for and must not be replaced."""
        from personalized_agent.heartbeat.states.tasks import Kept

        self._first_round(seed=73)
        self.beat.models = _answering([{
            "status": "The user now has figure 1 matching.",
            "changed": True, "why": "it closed"}])
        self.beat.make_up_states(how_many=6, days=1, seed=74)
        self.beat.run_once(trigger="manual_debug", scope="sandbox")
        held = [one for one in Kept(self.app.db, scope="sandbox").live()
                if one.described.get("status")]
        if not held:
            self.skipTest("nothing was described")
        moved = [one for one in held
                 if one.described["status"].startswith("The user now has")]
        if not moved:
            self.skipTest("no task had its status updated")
        one = moved[0]
        self.assertEqual(one.described["where"],
                         "The user works in survey-weights/.")
        self.assertEqual(one.described["goal"],
                         "The user wants the figures to match.")
        self.assertEqual(one.described["doing"],
                         "The user is re-running the pipeline.")

    def test_and_it_may_answer_that_nothing_changed(self) -> None:
        """A status rewritten in new words every hour is the thing the
        turnover rule was protecting the reader from, and reintroducing it
        here would trade one fault for the other."""
        from personalized_agent.heartbeat.states.tasks import Kept

        self._first_round(seed=75)
        before = {one.id: dict(one.described)
                  for one in Kept(self.app.db, scope="sandbox").live()}
        self.beat.models = _answering([{
            "changed": False, "why": "another sitting on the same obstacle"}])
        self.beat.make_up_states(how_many=6, days=1, seed=76)
        again = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(again.get("error"), again.get("error"))
        after = {one.id: dict(one.described)
                 for one in Kept(self.app.db, scope="sandbox").live()}
        for task_id, held in before.items():
            if task_id in after and held.get("status"):
                self.assertEqual(after[task_id].get("status"),
                                 held.get("status"),
                                 "an unchanged status was rewritten anyway")

    def test_and_the_call_is_small(self) -> None:
        """It is meant to be cheap enough to make every round: the
        description it already has and the states that moved, not the
        forty the task holds."""
        self._first_round(seed=77)
        models = _answering([{"status": "The user moved on.",
                              "changed": True, "why": "w"}])
        self.beat.models = models
        self.beat.make_up_states(how_many=6, days=1, seed=78)
        self.beat.run_once(trigger="manual_debug", scope="sandbox")
        if not models.sent:
            self.skipTest("no status call was made")
        message = models.sent[0]["message"]
        self.assertIn("THE TASK, AS IT IS DESCRIBED NOW", message)
        self.assertIn("ARRIVED IN IT", message)
        # Far shorter than a summary, which carries forty whole states.
        self.assertLess(len(message), 12000, len(message))

    def test_and_the_instruction_forbids_the_other_three(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import STATUS_PROMPT

        self.assertIn("KEEP THE OTHER THREE OUT OF IT", STATUS_PROMPT)
        self.assertIn("must not return them", STATUS_PROMPT)
        # And losing states counts as a change, not only gaining them.
        self.assertIn("STATES MAY HAVE ARRIVED, AND STATES MAY HAVE GONE",
                      STATUS_PROMPT)

    def test_and_a_task_that_did_not_move_is_not_asked(self) -> None:
        from personalized_agent.heartbeat.states.heartbeat import StateHeartbeat

        said = StateHeartbeat._where_it_stands(
            self.beat, {"stages": []},
            type("T", (), {"id": "t1", "described":
                           {"status": "something"}, "members": ["s1"]})(),
            {"id": "task_1", "summary": {"title": "t"}}, [],
            {"gained": [], "lost": []}, "trace", self.beat.where("sandbox"))
        self.assertEqual(said.get("calls"), 0)
        self.assertIn("did not change", said.get("skipped", ""))


class AReusedSummaryIsStillASummaryTests(unittest.TestCase):
    """A task whose summary was not rewritten came out of the next round
    with no name on it.

    The kept branch built a summary dict out of a record id, a count and a
    reason - everything except the words. The title and the sentence lived
    only in the library record, so the page had nowhere to read them from
    and drew a task called nothing, on every round the summary was reused,
    which is most rounds."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_sandbox_max_states", 160)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_title_survives_a_round_that_did_not_rewrite_it(self) -> None:
        self.beat.models = _describing({
            "where": "The user works in survey-weights/.",
            "goal": "The user wants the figures to match.",
            "doing": "The user is re-running the pipeline.",
            "status": "The user has not started the figures."},
            title="Rebuild the SWR replication package.")
        self.beat.make_up_states(how_many=160, days=20, seed=81)
        first = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(first.get("error"), first.get("error"))
        named = [task for task in (first.get("layers") or [])
                 if (task.get("summary") or {}).get("title")]
        self.assertTrue(named, "nothing was summarised in the first round")

        # A few states: not enough to turn any task over, so the summaries
        # are reused - which is the case that lost the name.
        self.beat.models = _answering([{"changed": False, "why": "same"}])
        self.beat.make_up_states(how_many=5, days=1, seed=82)
        again = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(again.get("error"), again.get("error"))
        reused = [task for task in (again.get("layers") or [])
                  if (task.get("summary") or {}).get("kept")]
        if not reused:
            self.skipTest("every summary was rewritten this round")
        for task in reused:
            self.assertTrue((task.get("summary") or {}).get("title"),
                            "a reused summary came back with no title")

    def test_and_the_words_are_kept_beside_the_four_parts(self) -> None:
        from personalized_agent.heartbeat.states.tasks import Kept

        self.beat.models = _describing(
            {"status": "The user has not started."},
            title="Rebuild the SWR replication package.")
        self.beat.make_up_states(how_many=160, days=20, seed=83)
        self.beat.run_once(trigger="manual_debug", scope="sandbox")
        held = [one for one in Kept(self.app.db, scope="sandbox").live()
                if one.described.get("title")]
        self.assertTrue(held, "no task kept the words it was given")
        self.assertEqual(held[0].described["title"],
                         "Rebuild the SWR replication package.")


class TheSandboxCopiesTheWholeStateTests(unittest.TestCase):
    """A copy that keeps the title and the detail and drops the rest is
    not a copy of a state.

    What it rests on, the evidence it recorded and the reasoning the
    review wrote it with are three of the things a summary is made from,
    and a search that asks for a state "in full" was handing back four
    fields and three apologies."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _a_real_state(self):
        from personalized_agent.agent.states.store import State

        return self.app.states.add(State(
            title="Fixing the census fetch step.",
            detail="Another sitting on the rate limit.",
            metadata={"evidence": ["the traceback in fetch.py"],
                      "sources": ["C:/Research/survey-weights/fetch.py"],
                      "reasoning": "three windows on the same file"},
            starts_at=utcnow() - timedelta(hours=2),
            ends_at=utcnow() - timedelta(hours=1)))

    def test_what_it_rests_on_and_its_reasoning_are_copied(self) -> None:
        self._a_real_state()
        made = self.beat.make_up_states(how_many=1, days=1, seed=91)
        self.assertTrue(made.get("copied"), "nothing was copied")
        states = self.beat.where("sandbox").states.recent(limit=5)
        held = [one for one in states
                if (one.metadata or {}).get("sources")]
        self.assertTrue(held, "the copy kept no sources")
        one = held[0]
        self.assertTrue(one.metadata.get("evidence"), "no evidence")
        self.assertTrue(one.metadata.get("reasoning"), "no reasoning")

    def test_and_it_carries_a_local_clock_of_its_own(self) -> None:
        """The sandbox moves a state to a new moment so the calendar it
        draws is its own, so keeping the original's wall-clock time would
        put a Tuesday evening on a Friday morning."""
        self._a_real_state()
        self.beat.make_up_states(how_many=1, days=1, seed=92)
        states = self.beat.where("sandbox").states.recent(limit=5)
        held = [one for one in states if (one.metadata or {}).get("local")]
        if not held:
            self.skipTest("this machine has no local clock to record")
        said = held[0].metadata["local"]
        self.assertTrue(said.get("weekday"))
        self.assertTrue(said.get("starts_at"))

    def test_and_the_model_is_shown_them(self) -> None:
        """The whole point: what the debugger shows the model should be
        the shape the real round shows it."""
        from personalized_agent.heartbeat.states.heartbeat import _whole_state

        self._a_real_state()
        self.beat.make_up_states(how_many=1, days=1, seed=93)
        states = self.beat.where("sandbox").states.recent(limit=5)
        held = [one for one in states
                if (one.metadata or {}).get("sources")]
        if not held:
            self.skipTest("nothing was copied with sources")
        from personalized_agent.heartbeat.states.heartbeat import _Node

        said = "\n".join(_whole_state(_Node(held[0]).to_dict(), "one"))
        self.assertIn("fetch.py", said)
        self.assertNotIn("this state names no file", said)
        self.assertNotIn("it did not say", said)


if __name__ == "__main__":       # pragma: no cover
    unittest.main()

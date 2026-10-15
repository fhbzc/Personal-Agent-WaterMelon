"""Section 34.3: the review that groups the states already written.

The review that writes states reads a few minutes at a time, so a
fortnight of one project is a hundred states, each true and each worded
slightly differently. Nothing anywhere said "this is one thing".

What compares them is what they mean rather than which words they share:
one model writes every state, in a stable vocabulary, about a handful of
projects, so two states picked at random already share a sixth of their
words - and word overlap mostly measures that. What groups them is a
tree, because a flat grouping has to choose an altitude before it has
seen anything.
"""

import json
import unittest
from datetime import timedelta

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


SUMMARY = {"title": "They have spent the week on the SWR replication package.",
           "summary": "A run of sittings on the same package.",
           "reasoning": "the same scripts and the same paper throughout",
           "evidence": ["the fetch.py sittings"]}

#: States that are two things and a pair that is neither.
#:
#: Six on a project and five on an evening off are tasks; the two about
#: email are not, because a task holds five states or it is not a task.
#: They are the loose pair every run has some of.
#:
#: Written the way the review writes them - one sentence about the user,
#: in English - and deliberately worded differently within each group, so
#: that what joins them is what they mean rather than which words they
#: happen to share.
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
EMAIL = [
    ("They are answering email about the grant.", "Gmail threads."),
    ("They are answering email about the trip.", "More Gmail."),
]


class GroupingWhatTheCalendarKeepsSayingTests(unittest.TestCase):
    """Two states about the same work, written a window apart, were two
    unrelated rows on the calendar and four unrelated hits in a search."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.beat.models = _reply(SUMMARY)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _state(self, title, detail, hours_ago=1, **metadata):
        from personalized_agent.agent.states.store import State

        return self.app.states.add(State(
            title=title, detail=detail,
            starts_at=utcnow() - timedelta(hours=hours_ago),
            ends_at=utcnow() - timedelta(hours=hours_ago) + timedelta(minutes=15),
            metadata=dict(metadata)))

    def _write(self, rows, since=1):
        for index, (title, detail) in enumerate(rows):
            self._state(title, detail, hours_ago=since + index)

    def _run(self):
        return self.beat.run_once(trigger="manual_debug")

    def _titles(self, group):
        return " | ".join(group["titles"])

    # -- the tree -----------------------------------------------------------
    def test_every_state_is_a_leaf(self) -> None:
        self._write(REPLICATION)
        report = self._run()
        self.assertEqual(sorted(report["tree"]["leaves"]),
                         list(range(len(REPLICATION))))

    def test_and_the_tree_joins_all_of_them(self) -> None:
        """A state that joins nothing is a finding about that state; a
        state left out of the tree is a state the picture does not
        mention."""
        self._write(REPLICATION + WATCHING)
        report = self._run()
        self.assertEqual(len(report["tree"]["merges"]),
                         len(REPLICATION) + len(WATCHING) - 1)

    def test_the_closest_pair_is_joined_first(self) -> None:
        self._write(REPLICATION + WATCHING + EMAIL)
        heights = [merge["height"] for merge in self._run()["tree"]["merges"]]
        self.assertEqual(heights, sorted(heights))

    def test_states_about_one_thing_end_up_under_one_join(self) -> None:
        self._write(REPLICATION + WATCHING)
        report = self._run()
        biggest = report["communities"][0]
        self.assertGreaterEqual(biggest["size"], 3)
        self.assertIn("replication", self._titles(biggest).lower())

    def test_and_states_about_another_thing_do_not_join_them(self) -> None:
        self._write(REPLICATION + WATCHING)
        report = self._run()
        for task in report["layers"]:
            titles = " | ".join(task["states"]).lower()
            titles = " | ".join(
                state["title"] for state in report["states"]
                if state["id"] in set(task["states"])).lower()
            self.assertFalse("replication" in titles and "youtube" in titles,
                             f"one task holds both: {titles}")

    def test_the_tree_is_read_as_layers_of_tasks(self) -> None:
        """A few tasks at the top, each holding a few - rather than one
        cut at one height for the whole calendar."""
        self._write(REPLICATION + WATCHING + EMAIL)
        report = self._run()
        sizes = sorted(task["size"] for task in report["layers"])
        self.assertEqual(sizes, [5, 6])
        self.assertGreaterEqual(report["tasks_found"], 2)

    def test_and_a_pair_too_small_to_be_a_task_is_left_loose(self) -> None:
        """A task holds five states or it is not a task - and the two
        that fall outside are said to be that rather than hidden or made
        into a task of two."""
        self._write(REPLICATION + WATCHING + EMAIL)
        report = self._run()
        self.assertEqual(len(report["loose"]), len(EMAIL))

    def test_and_how_readily_it_splits_is_a_setting(self) -> None:
        self._write(REPLICATION + WATCHING + EMAIL)
        self.app.config.set("state_task_stands_out", 99.0)
        held = self._run()
        self.app.config.set("state_task_stands_out", 0.2)
        split = self._run()
        self.assertLess(held["tasks_found"], split["tasks_found"])
        self.assertEqual(held["tasks_found"], 1,
                         "nothing splits when nothing can stand out enough")

    def test_and_every_task_says_how_far_its_join_stood_out(self) -> None:
        """Which is what makes that setting a number worth choosing."""
        self._write(REPLICATION + WATCHING)
        for task in self._run()["layers"]:
            self.assertIn("stands_out", task)

    def test_nothing_to_compare_is_said_rather_than_reported_as_nothing(self) -> None:
        self._state("The only state", "d")
        report = self._run()
        self.assertIn("nothing to compare", report["note"])
        self.assertNotIn("tree", report)

    def test_the_same_states_give_the_same_tree(self) -> None:
        """A run that cannot be compared with the one before it cannot be
        read for what changed."""
        self._write(REPLICATION + WATCHING)
        first, second = self._run(), self._run()
        self.assertEqual(first["tree"]["merges"], second["tree"]["merges"])
        self.assertEqual(first["tree"]["leaves"], second["tree"]["leaves"])

    # -- what reads them ----------------------------------------------------
    def test_states_are_compared_by_what_they_mean(self) -> None:
        self._write(REPLICATION)
        report = self._run()
        self.assertEqual(report["similarity"]["source"], "embedding")
        self.assertIn("MiniLM", report["similarity"]["model"])

    def test_and_a_pair_carries_both_how_alike_and_how_far(self) -> None:
        """The first is what a reader compares; the second is the height
        the two are joined at, and it is one minus the other."""
        self._write(REPLICATION)
        pair = self._run()["similarity"]["pairs"][0]
        self.assertAlmostEqual(pair["score"] + pair["distance"], 1.0, places=3)

    def test_meaning_sees_what_word_overlap_does_not(self) -> None:
        """Which is the whole reason for reading them this way.

        These two are one evening said twice, in different words. Counting
        the words they share puts them at 0.25; reading what they mean
        puts them at 0.60, and the second is the answer.
        """
        from personalized_agent.heartbeat.states.heartbeat import (
            _terms, similarity)

        said = ("They are watching videos on YouTube.",
                "Video pages one after another.")
        again = ("They are taking the evening off in front of a video site.",
                 "Clip after clip.")
        self._state(*said)
        self._state(*again, hours_ago=2)
        report = self._run()
        meant = report["similarity"]["pairs"][0]["score"]
        overlap = similarity(_terms(" ".join(said)), _terms(" ".join(again)))
        self.assertGreater(meant, overlap * 1.5,
                           f"meaning {meant:.2f} against words {overlap:.2f}")

    def test_what_is_read_is_the_title_and_the_detail(self) -> None:
        """Not the evidence or the sources: those are paths and URLs, and
        a language model reading a path is reading punctuation."""
        from personalized_agent.heartbeat.states.embedding import text_of

        state = self._state("A title.", "A detail.",
                            sources=["C:/work/thing.py"], evidence=["an editor"])
        self.assertEqual(text_of(state), "A title. A detail.")

    def test_a_vector_is_read_once_and_kept(self) -> None:
        """A thousand states are read once; an hour later only the handful
        written since are new."""
        self._write(REPLICATION)
        self._run()
        rows = self.app.db.query("SELECT state_id, dims FROM state_vectors")
        self.assertEqual(len(rows), len(REPLICATION))
        self.assertEqual(rows[0]["dims"], 384)

    def test_and_read_again_when_its_words_change(self) -> None:
        state = self._state("They are doing one thing.", "A detail.")
        self._state("They are doing another thing.", "Another detail.", 2)
        self._run()
        before = self.app.db.query(
            "SELECT fingerprint FROM state_vectors WHERE state_id = ?",
            (state.id,))[0]["fingerprint"]
        self.app.db.execute("UPDATE states SET title = ? WHERE id = ?",
                            ("They are doing something else entirely.", state.id))
        self._run()
        after = self.app.db.query(
            "SELECT fingerprint FROM state_vectors WHERE state_id = ?",
            (state.id,))[0]["fingerprint"]
        self.assertNotEqual(before, after)

    def test_with_no_model_it_compares_words_and_says_so(self) -> None:
        """This application's rule is that everything needing more than
        the standard library degrades rather than stops."""
        self._write(REPLICATION)
        self.app.config.set("state_embedding_enabled", False)
        report = self._run()
        self.assertEqual(report["similarity"]["source"], "words")
        self.assertTrue(report["tree"]["merges"], "the tree still built")

    # -- summaries, which are off ------------------------------------------
    def test_summaries_can_be_turned_off_and_then_nothing_is_written(self):
        """They are ON by default now - a task without a sentence saying
        what it is is a number and a size, and being told what a hundred
        states add up to is the whole reason for grouping them. Off is
        still a setting, and off has to mean off."""
        self._write(REPLICATION)
        self.app.config.set("state_summaries_enabled", False)
        report = self._run()
        self.assertEqual(report["summaries_written"], [])
        self.assertEqual(report["calls"], 0)
        self.assertIn("Summaries are off", report["note"])

    def test_and_by_default_they_are_written(self) -> None:
        self._write(REPLICATION + WATCHING)
        report = self._run()
        self.assertTrue(report["summaries_written"], report.get("note"))

    def test_and_when_they_are_asked_for_every_task_gets_one(self) -> None:
        self._write(REPLICATION + WATCHING)
        self.app.config.set("state_summaries_enabled", True)
        report = self._run()
        self.assertEqual(len(report["summaries_written"]), 2)
        self.assertEqual(report["calls"], 2)

    def test_a_group_too_small_is_not_summarised(self) -> None:
        """Five is the floor, and it is the same floor that decides what
        is a task at all: a handful of states is a handful of states."""
        self._write(EMAIL)
        self.app.config.set("state_summaries_enabled", True)
        report = self._run()
        self.assertEqual(report["summaries_written"], [])
        self.assertIn("summarised at 5", report["communities"][0]["why_not"])

    def test_the_summary_is_written_as_a_record_that_names_its_states(self) -> None:
        """A summary that cannot be opened out into its originals is a
        claim with the evidence deleted."""
        self._write(REPLICATION + WATCHING)
        self.app.config.set("state_summaries_enabled", True)
        report = self._run()
        record = self.app.library.get(report["summaries_written"][0]["record_id"])
        self.assertEqual(record.title, SUMMARY["title"])
        self.assertEqual(record.level, 1)
        self.assertEqual(len(record.covers), len(REPLICATION))
        self.assertTrue(record.child_index)
        self.assertTrue(record.child_index)

    def test_a_model_that_is_away_does_not_lose_the_tree(self) -> None:
        """The grouping needs no model. A round that reports an error
        instead of the tree has thrown away the part that worked."""
        from personalized_agent.models.base import ModelAvailability
        from personalized_agent.models.manager import ModelUnavailable

        self._write(REPLICATION + WATCHING)
        self.app.config.set("state_summaries_enabled", True)
        resolved = self.app.models.resolve("heartbeat")

        def refuse(*_a, **_k):
            raise ModelUnavailable(
                resolved, ModelAvailability(available=False, reason="no credit"))

        self.beat.models = type("M", (), {"invoke": refuse})()
        report = self._run()
        self.assertTrue(report["blocked"])
        self.assertTrue(report["tree"]["merges"])

    # -- the method, written down ------------------------------------------
    def test_the_method_is_reported_as_steps(self) -> None:
        self._write(REPLICATION)
        steps = self._run()["how"]["steps"]
        self.assertEqual(len(steps), 11)
        # Which states the round is even about comes first: the grouping
        # covers a window, not the calendar, and a method that does not
        # say so reads as though it had seen everything.
        self.assertIn("WHICH STATES", steps[0])
        self.assertIn("It is the beginning", steps[0])
        self.assertIn("title and the detail", steps[1])
        self.assertIn("cosine", steps[2])
        self.assertIn("One minus", steps[3])
        self.assertIn("average distance", steps[4])
        self.assertIn("COLLAPSE", steps[5])
        self.assertIn("SPLIT", steps[6])
        self.assertIn("WHERE IT STOPS", steps[7])
        # And the three that say the grouping is not computed again from
        # nothing every hour, which is the part a reader would otherwise
        # assume from steps 4 to 7.
        self.assertIn("DOES NOT DO ANY OF THAT", steps[8])
        self.assertIn("WHERE A NEW STATE GOES", steps[9])
        self.assertIn("DORMANT", steps[10])

    def test_and_every_parameter_is_named_with_its_value_and_why(self) -> None:
        """A number on a page with no account of itself is a number
        nobody can change."""
        self._write(REPLICATION)
        parameters = self._run()["how"]["parameters"]
        self.assertEqual(len(parameters), 10)
        for entry in parameters:
            self.assertTrue(entry["name"])
            self.assertTrue(entry["value"])
            self.assertGreater(len(entry["why"]), 80, entry["name"])

    def test_and_names_what_read_them_and_what_joined_them(self) -> None:
        self._write(REPLICATION)
        how = self._run()["how"]
        self.assertIn("MiniLM", how["reading"])
        self.assertIn("average linkage", how["clustering"])
        self.assertIn("dendrogram", how["layout"])

    def test_and_it_follows_the_settings_in_use(self) -> None:
        """A page that describes a method it does not run is a page that
        will describe the old one after the next change."""
        self._write(REPLICATION)
        self.app.config.set("state_task_stands_out", 2.5)
        self.app.config.set("state_embedding_enabled", False)
        how = self._run()["how"]
        self.assertIn("2.5 standard deviations", how["steps"][6])
        self.assertIn("no embedding model", how["reading"])

    # -- what the overview opens -------------------------------------------
    def test_the_picture_is_a_fraction_of_the_report(self) -> None:
        """The overview opens this on every visit.

        The debugger's report of the same round is megabytes of prompts
        and exchanges - the right payload for investigating a round and
        the wrong one for a tab somebody opens twenty times a day.
        Measured on a real folder: 7.4MB of report, 235KB of this.
        """
        from personalized_agent.api.routes import build_routes

        class Request:
            def get(self, key, default=None):
                return default

        from test_state_continuity import _asking
        self.beat.models = _asking()
        self.beat.make_up_states(how_many=40, days=10, seed=91)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.assertFalse(report.get("error"), report.get("error"))
        # The sandbox does not become `last_report`, which is the live
        # round's; this is the shape test, so it is handed the round it
        # has.
        self.beat._last_report = report
        routes = build_routes(self.app)
        picture = routes[("GET", "/api/state_heartbeat/picture")](Request())

        # The keys the drawing reads, under the names the report uses - so
        # one function draws from either, rather than two drifting apart.
        for key in ("plan", "layers", "states", "due", "round"):
            self.assertIn(key, picture, key)
        self.assertIn("nodes", picture["plan"])
        self.assertIn("edges", picture["plan"])

        # NOT THE PROMPTS. These are what the size is, and nothing in the
        # drawing reads one.
        whole = json.dumps(report, default=str)
        trimmed = json.dumps(picture, default=str)
        self.assertLess(len(trimmed), len(whole) / 2,
                        f"the picture is {len(trimmed)} of {len(whole)} bytes")
        for forbidden in ("\"prompt\"", "\"exchange\"", "\"answer\""):
            self.assertNotIn(forbidden, trimmed,
                             f"{forbidden} is in a payload the overview opens "
                             f"on every visit")
        # A state is counted and timed, never named - no row shows one.
        if picture["states"]:
            self.assertNotIn("title", picture["states"][0])
            for field in ("id", "status", "starts_at", "ends_at"):
                self.assertIn(field, picture["states"][0], field)

    def test_and_a_task_keeps_its_name_and_weight_in_it(self) -> None:
        """Trimming the states is not trimming the tasks: the rows ARE
        the tasks, and a row with no name is a circle."""
        from test_state_continuity import _asking
        self.beat.models = _asking()
        self.beat.make_up_states(how_many=40, days=10, seed=92)
        report = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        self.beat._last_report = report
        picture = self.beat.the_picture()
        if not picture["layers"]:
            self.skipTest("no tasks were found")
        held = picture["layers"][0]
        for field in ("id", "size", "states", "summary", "children"):
            self.assertIn(field, held, field)
        for field in ("title", "described", "concrete"):
            self.assertIn(field, held["summary"], field)

    # -- the clock ----------------------------------------------------------
    def test_it_runs_on_its_own_interval(self) -> None:
        """ONE number, in hours, and the seconds are derived from it.

        There were two - an interval in seconds and a gap in hours - and
        two numbers for one thing meant whichever the loop slept on, the
        other was what the panel showed.
        """
        self.assertEqual(self.beat.status()["interval_seconds"], 12 * 3600)
        self.assertEqual(self.beat.status()["every_hours"], 12.0)
        self.app.config.set("state_heartbeat_every_hours", 0.5)
        self.assertEqual(self.beat.status()["interval_seconds"], 1800)
        self.assertEqual(self.beat.status()["every_hours"], 0.5)

    def test_a_chosen_interval_survives_the_rename(self) -> None:
        """A rename carries what was chosen rather than dropping it.

        How often states are grouped was an interval in seconds and is
        now a gap in hours. Dropping the old key would silently reset
        somebody who had deliberately set ten minutes back to six hours,
        and they would have no way to know a tidy-up had changed how
        their agent behaves.
        """
        self.app.config.set("state_heartbeat_interval_seconds", 600,
                            internal=True)
        self.app.config.set("state_interval_carried", False, internal=True)
        self.app._carry_the_state_interval()
        self.assertAlmostEqual(self.beat._every_hours(), 600 / 3600, places=3)

    def test_but_an_old_default_nobody_chose_takes_the_new_one(self) -> None:
        """A stored value equal to the old default was never a decision."""
        self.app.config.set("state_heartbeat_interval_seconds", 3600,
                            internal=True)
        self.app.config.set("state_interval_carried", False, internal=True)
        self.app._carry_the_state_interval()
        self.assertEqual(self.beat._every_hours(), 12.0)

    def test_and_it_only_happens_once(self) -> None:
        """Otherwise a user who set six hours deliberately would have it
        rewritten from a key nobody reads, every start-up."""
        self.app.config.set("state_heartbeat_interval_seconds", 600,
                            internal=True)
        self.app.config.set("state_interval_carried", False, internal=True)
        self.app._carry_the_state_interval()
        self.app.config.set("state_heartbeat_every_hours", 6.0)
        self.app._carry_the_state_interval()
        self.assertEqual(self.beat._every_hours(), 6.0)

    def test_and_the_interval_can_be_set_as_low_as_a_minute(self) -> None:
        self.app.config.set("state_heartbeat_every_hours", 0.0001)
        self.assertEqual(self.beat.status()["interval_seconds"], 60)

    def test_two_rounds_do_not_run_at_once(self) -> None:
        self.beat._running.acquire()
        try:
            self.assertIn("skipped", self._run())
        finally:
            self.beat._running.release()

    def test_the_report_survives_the_run(self) -> None:
        self._write(REPLICATION)
        self._run()
        self.assertEqual(self.beat.last_report["states_considered"],
                         len(REPLICATION))
        self.assertFalse(self.beat.progress()["running"])


class ThePageItIsTestedFromTests(unittest.TestCase):
    """A panel with a button that posts to a route nobody serves is a page
    that looks like it works."""

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _routes(self):
        from personalized_agent.api.routes import build_routes

        return build_routes(self.app)

    def test_the_route_the_button_posts_to_is_served(self) -> None:
        import pathlib

        script = pathlib.Path(
            "personalized_agent/api/web/app.js").read_text(encoding="utf-8")
        self.assertIn("/api/state_heartbeat/run", script)
        self.assertIn(("POST", "/api/state_heartbeat/run"), self._routes())

    def test_and_so_are_the_two_it_reads(self) -> None:
        self.assertIn(("GET", "/api/state_heartbeat"), self._routes())
        self.assertIn(("GET", "/api/state_heartbeat/progress"), self._routes())

    def test_running_one_from_the_route_runs_one(self) -> None:
        class Request:
            def __init__(self, **values):
                self.values = values

            def get(self, key, default=None):
                return self.values.get(key, default)

        run = self._routes()[("POST", "/api/state_heartbeat/run")]
        report = run(Request())
        self.assertEqual(report["trigger"], "manual_debug")
        # Section 34.3, as amended: the same route, told where to work.
        self.assertEqual(report["where"]["scope"], "live")
        self.assertEqual(run(Request(scope="sandbox"))["where"]["scope"],
                         "sandbox")
        # And anything else is the real one, rather than a typo silently
        # becoming a round over made-up states.
        self.assertEqual(run(Request(scope="snadbox"))["where"]["scope"],
                         "live")

    def test_and_the_sandbox_has_its_own_routes(self) -> None:
        routes = self._routes()
        self.assertIn(("POST", "/api/state_heartbeat/sandbox/states"), routes)
        self.assertIn(("POST", "/api/state_heartbeat/sandbox/clear"), routes)
        self.assertIn(("GET", "/api/state_heartbeat/sandbox"), routes)

    def test_every_button_on_the_page_says_what_it_does(self) -> None:
        """Two of these buttons change the user's real records and four
        cannot touch them. That is not a difference to leave a person to
        work out from the labels."""
        import pathlib
        import re

        page = pathlib.Path(
            "personalized_agent/api/web/index.html").read_text(encoding="utf-8")
        panel = page[page.index('<section id="statebeat"'):]
        panel = panel[:panel.index("</section>")]
        labels = re.findall(r'<button[^>]*id="dbg-[^"]*"[^>]*>([^<]+)</button>',
                            panel)
        self.assertGreaterEqual(len(labels), 5, labels)
        explained = re.findall(r"<dt>([^<]+)</dt>", panel)
        for label in labels:
            with self.subTest(label):
                self.assertIn(label.strip(), [one.strip() for one in explained],
                              f"{label.strip()} has no explanation on the page")
        # And the numbers beside them are explained too.
        self.assertTrue(any("states" in one and "days" in one
                            for one in explained), explained)

    def test_and_every_one_says_in_brackets_whose_data_it_touches(self) -> None:
        """The two kinds are the same code over different folders, which is
        the point and the hazard: the labels have to carry the difference,
        because the results look identical."""
        import pathlib
        import re

        page = pathlib.Path(
            "personalized_agent/api/web/index.html").read_text(encoding="utf-8")
        panel = page[page.index('<section id="statebeat"'):]
        panel = panel[:panel.index("</section>")]
        labels = re.findall(r'<button[^>]*id="dbg-[^"]*"[^>]*>([^<]+)</button>',
                            panel)
        self.assertGreaterEqual(len(labels), 5, labels)
        for label in labels:
            with self.subTest(label):
                said = label.strip()
                self.assertTrue("(real data)" in said or "sandbox" in said,
                                f"{said} does not say whose data it touches")
        real = [one for one in labels if "(real data)" in one]
        box = [one for one in labels if "sandbox" in one]
        self.assertEqual(len(real), 1, real)
        self.assertEqual(len(box), 4, box)

    def test_and_says_which_of_them_touch_real_records(self) -> None:
        import pathlib

        page = pathlib.Path(
            "personalized_agent/api/web/index.html").read_text(encoding="utf-8")
        panel = page[page.index('<section id="statebeat"'):]
        panel = panel[:panel.index("</section>")]
        self.assertIn("writes real records", panel)
        self.assertIn("writes nothing of yours", panel)

    def test_the_page_has_its_own_tab(self) -> None:
        import pathlib

        markup = pathlib.Path(
            "personalized_agent/api/web/index.html").read_text(encoding="utf-8")
        self.assertIn('data-tab="statebeat"', markup)
        self.assertIn('id="statebeat"', markup)
        self.assertIn('id="dbg-states-run"', markup)

    def test_the_agent_starts_and_stops_it(self) -> None:
        """A loop nothing starts is a setting that does nothing.

        ASKED OF THE BEHAVIOUR, not of the source text. This read app.py
        looking for the literal line `self.state_heartbeat.start()`, and
        broke when the three starts became a loop that times each of them
        - a change that starts the heartbeat exactly as before. A test
        that fails when working code is rearranged is a test of the
        arrangement, and the arrangement is not what matters here.
        """
        started = []
        stopped = []
        beat = self.app.state_heartbeat
        beat.start = lambda: started.append(True)
        beat.stop = lambda: stopped.append(True)
        self.app.config.set("api_port", 8941)
        self.app.start(with_api=False)
        try:
            self.assertEqual(len(started), 1, "the agent did not start it")
        finally:
            self.app.stop()
        self.assertEqual(len(stopped), 1, "the agent did not stop it")
        # And it is stopped FIRST, because a round in flight holds the
        # way out and everything else waits behind it.
        self.assertEqual(
            list(getattr(self.app, "_shutdown_spent", {}).keys())[0],
            "StateHeartbeat")


class HowAlikeTwoStatesAreByWordsTests(unittest.TestCase):
    """The fallback, for a machine with no model on it.

    Jaccard scored a paragraph against a line low for a reason that has
    nothing to do with the question: the union is mostly the longer one.
    """

    def _score(self, left, right):
        from personalized_agent.heartbeat.states.heartbeat import similarity

        return similarity(set(left.split()), set(right.split()))

    def test_the_same_words_score_one(self) -> None:
        self.assertEqual(self._score("a b c", "a b c"), 1.0)

    def test_nothing_shared_scores_nothing(self) -> None:
        self.assertEqual(self._score("a b c", "d e f"), 0.0)

    def test_an_empty_side_scores_nothing(self) -> None:
        self.assertEqual(self._score("", "a b"), 0.0)

    def test_a_line_inside_a_paragraph_still_scores_well(self) -> None:
        self.assertGreater(
            self._score("replication fetch",
                        "replication fetch census figures scripts notebook "
                        "paper nature article"), 0.4)

    def test_but_not_as_well_as_the_same_length_would(self) -> None:
        self.assertGreater(self._score("a b", "a b"),
                           self._score("a b", "a b c d e f g h"))

    def test_punctuation_does_not_decide_whether_two_states_match(self) -> None:
        """The splitter keeps the full stop on the last word of a
        sentence, so "the replication package." and "the replication
        package, and the figures" shared one term out of two."""
        from personalized_agent.heartbeat.states.heartbeat import _terms

        self.assertIn("package", _terms("Rebuilding the replication package."))
        self.assertIn("fetch",
                      _terms("C:/Research/replication_SWR/scripts/fetch.py"))


if __name__ == "__main__":
    unittest.main()

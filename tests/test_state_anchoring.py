"""Section 34.3, as amended: what a state was open on, before anything is scored.

The grouping reads what a state SAYS, which is the model's paraphrase of a
quarter of an hour. A path is not a paraphrase - it is the same string
every time or it is absent - so two states naming one file are on one
piece of work whatever either of them says about it.

Every test here is about a way that idea goes wrong. They are not
hypothetical: each one is a fault this code had, found by running it over
a real calendar rather than by reading it.
"""

import unittest

from personalized_agent.heartbeat.states import workspace as W


class WhatWasOpenTests(unittest.TestCase):
    """Reading the object of the work out of what a state recorded."""

    def test_a_path_and_a_url_are_read(self) -> None:
        got = W.read(["C:/Research/survey-weights/fetch.py"],
                     ["opened https://openreview.net/forum?id=abc123"])
        self.assertIn("c:/research/survey-weights/fetch.py", got["files"])
        self.assertIn("openreview.net/forum?id=abc123", got["urls"])

    def test_and_the_same_page_in_two_browsers_is_one_key(self) -> None:
        """The application is the vehicle. A page is the same page in
        Firefox and in Chrome, and keying on the browser would merge
        Gmail with YouTube for both being Firefox while splitting one
        paper across an editor and a terminal."""
        one = W.read(["Harbour towns at dusk - YouTube - Mozilla Firefox"],
                     ["https://www.youtube.com/watch?v=Ex4m1a0001A&t=9"])
        two = W.read(["Harbour towns at dusk - YouTube - Google Chrome"],
                     ["http://youtube.com/watch?v=Ex4m1a0001A"])
        self.assertEqual(one["urls"], two["urls"])
        self.assertNotEqual(one["apps"], two["apps"])

    def test_and_an_application_never_identifies_anything(self) -> None:
        got = W.read(["Weixin.exe window: Photos and Videos"], [])
        self.assertIn("weixin.exe", got["apps"])
        self.assertNotIn("weixin.exe", W.keys(got))

    def test_and_a_path_to_an_executable_is_a_tool(self) -> None:
        """`C:/Windows/System32/cmd.exe` was the second commonest
        workspace on a real calendar before this."""
        got = W.read([], ["ran C:/WINDOWS/system32/cmd.exe - python run.py"])
        self.assertEqual(got["files"], [])
        self.assertIn("cmd.exe", got["apps"])

    def test_and_assistant_message_is_not_a_dot_me_domain(self) -> None:
        """It was, in a hundred and one states, which made it the
        commonest workspace on the calendar."""
        got = W.read([], ["assistant.message 2026-09-20T16:46:36Z: 'hello'"])
        self.assertEqual(got["urls"], [])

    def test_and_the_s_of_https_is_not_a_drive_letter(self) -> None:
        got = W.read([], ["opened https://chatgpt.com/c/abc"])
        self.assertEqual(got["files"], [])
        self.assertIn("chatgpt.com/c/abc", got["urls"])

    def test_and_a_search_engine_is_a_way_through(self) -> None:
        """Everybody's path to everywhere. Keying work on it would put a
        morning of unrelated lookups in one task."""
        got = W.read([], ["searched https://www.google.com/search?q=fieldconf"])
        self.assertEqual(W.keys(got), [])

    def test_and_a_state_that_names_nothing_comes_back_empty(self) -> None:
        got = W.read([], ["the user was thinking"])
        self.assertEqual(W.identifying(got, W.tools_in([got])), [])

    def test_and_a_bare_name_is_demoted_by_the_corpus_not_by_a_list(self) -> None:
        """"Personalized Agent" is a repository page and "Mozilla Firefox"
        is a browser, and both are two capitalised words: nothing in the
        shape of a bare window title tells them apart.

        What tells them apart is that one of them also turns up as the
        application half of other titles. So the corpus answers it, and
        there is no list to keep - which means that with no corpus a bare
        name is kept, as the weakest kind of identity there is. That is
        safe on its own terms: a key naming one state anchors nothing."""
        alone = W.read(["Aria"], [])
        self.assertEqual(W.identifying(alone, W.tools_in([alone])), ["aria"])

        # Shown the same name as an application elsewhere, it goes.
        corpus = [alone, W.read(["Notes - Aria"], []),
                  W.read([], ["ran aria.exe"])]
        self.assertEqual(
            W.identifying(alone, W.tools_in(corpus)), [])


class WhatAnchorsTests(unittest.TestCase):
    """Joining states that were open on the same thing."""

    @staticmethod
    def _places(*rows):
        return [W.read(list(row), []) for row in rows]

    def test_two_states_on_one_file_are_one_anchor(self) -> None:
        seen = self._places(["C:/Research/AA/BB/fetch.py"],
                            ["C:/Research/AA/BB/fetch.py"],
                            ["C:/Elsewhere/other.py"])
        out = W.anchor(seen)
        joined = [one for one in out["anchors"] if len(one["members"]) > 1]
        self.assertEqual(len(joined), 1)
        self.assertEqual(sorted(joined[0]["members"]), [0, 1])

    def test_and_states_that_name_nothing_never_anchor(self) -> None:
        """Absence is not agreement. Letting two states with no evidence
        look alike is what made the earlier two-measure design a
        structural zero."""
        seen = self._places(["Aria"], ["WeChat"], ["Mozilla Firefox"])
        out = W.anchor(seen)
        self.assertTrue(all(len(one["members"]) == 1
                            for one in out["anchors"]))

    def test_and_an_anchor_never_chains(self) -> None:
        """A shared a file with B, B shared a page with C, and on a real
        calendar three unrelated months went into one anchor of a hundred
        and forty-seven states with NOTHING in common across it."""
        seen = self._places(
            ["C:/one/a.py"],                      # shares a.py with 1
            ["C:/one/a.py", "C:/two/b.py"],       # the link
            ["C:/two/b.py"],                      # shares b.py with 1
        )
        out = W.anchor(seen)
        for one in out["anchors"]:
            if len(one["members"]) < 2:
                continue
            # Every anchor names the thing its members were open on.
            self.assertTrue(one["on"], "an anchor with nothing in common")
            self.assertLess(len(one["members"]), 3,
                            "the three chained into one")

    def test_and_an_anchor_is_capped_at_the_size_of_a_task(self) -> None:
        """An anchored group merges at distance nought, which takes it out
        of the splitter's reach - so an anchor of two hundred states would
        be one task nothing could divide."""
        seen = self._places(*[["C:/one/a.py"]] * 300)
        out = W.anchor(seen)
        biggest = max(len(one["members"]) for one in out["anchors"])
        self.assertEqual(biggest, 1,
                         "a key on three hundred states anchored anyway")

    def test_and_a_small_window_can_still_anchor(self) -> None:
        """A tenth of twelve is one, so without a floor no key could be
        shared by two states and anchor at all."""
        seen = self._places(*([["C:/one/a.py"]] * 4 + [["C:/two/b.py"]] * 4))
        out = W.anchor(seen)
        joined = [one for one in out["anchors"] if len(one["members"]) > 1]
        self.assertEqual(len(joined), 2)


class WhatPullsTests(unittest.TestCase):
    """A shared folder, which decides nothing."""

    def test_a_shared_parent_pulls_and_does_not_join(self) -> None:
        near = W.pull(["c:/research/aa/bb/one.py"], ["c:/research/aa/cc/two.py"])
        self.assertGreater(near, 0.0)
        self.assertLess(near, 1.0)

    def test_and_the_same_file_is_whole_agreement(self) -> None:
        self.assertEqual(W.pull(["c:/a/b.py"], ["c:/a/b.py"]), 1.0)

    def test_and_a_deeper_share_pulls_harder(self) -> None:
        """Sharing three levels of four is close; sharing one of five is
        a shelf."""
        close = W.pull(["c:/a/b/c/one.py"], ["c:/a/b/c/two.py"])
        far = W.pull(["c:/a/b/c/d/one.py"], ["c:/a/zz/yy/xx/two.py"])
        self.assertGreater(close, far)

    def test_and_naming_nothing_pulls_nothing(self) -> None:
        self.assertEqual(W.pull([], ["c:/a/b.py"]), 0.0)
        self.assertEqual(W.pull([], []), 0.0)


class WhatRareWordsAnchorTests(unittest.TestCase):
    """The second kind, for work that names no file and no page."""

    def test_states_sharing_several_rare_words_are_one_anchor(self) -> None:
        said = [
            ["fieldnotes", "coauthor", "openreview", "invitation"],
            ["fieldnotes", "coauthor", "openreview", "deadline"],
            ["youtube", "city", "planning", "zoning"],
            ["youtube", "city", "planning", "video"],
        ] + [["ordinary", "words", f"number{n}"] for n in range(8)]
        out = W.anchor_by_words(said)
        joined = [sorted(one["members"]) for one in out["anchors"]]
        self.assertIn([0, 1], joined)
        self.assertIn([2, 3], joined)

    def test_and_one_word_in_common_is_a_coincidence(self) -> None:
        """Two states each mentioning Lisbon once may be a trip and a
        restaurant."""
        said = ([["lisbon", "visa", "letter"], ["lisbon", "pastry", "shop"]]
                + [["other", f"word{n}", "here"] for n in range(8)])
        out = W.anchor_by_words(said)
        self.assertEqual([one for one in out["anchors"]
                          if len(one["members"]) > 1], [])

    def test_and_a_group_with_no_common_word_is_refused(self) -> None:
        """A chain rather than a subject, and there is no honest way to
        pick which of its links was the real one."""
        said = [
            ["alpha", "beta", "gamma"],
            ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"],
            ["delta", "epsilon", "zeta"],
        ] + [["filler", f"w{n}", "text"] for n in range(8)]
        out = W.anchor_by_words(said)
        for one in out["anchors"]:
            self.assertTrue(one["on"], "an anchor with no word in common")


if __name__ == "__main__":       # pragma: no cover
    unittest.main()


class TheDebuggerDoesNotWaitOnTheRealRoundTests(unittest.TestCase):
    """Two symptoms, one bug.

    There was one lock for both scopes, so an hourly round over the
    user's own states blocked every press in the debugger. That alone
    would only be a wait - what made it a puzzle is that the debugger
    did not say so. `clear_sandbox` asks whatever is running to stop
    before it deletes anything, which is right when a person presses the
    button; `run_once` takes that request back, but only once it has the
    lock, and it never got there. So the flag was still set when the
    rehearsal looked, and the rehearsal reported that the sandbox had
    been emptied underneath it - two true statements, neither of them
    the reason.
    """

    def setUp(self) -> None:
        from support import TemporaryAgent

        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.beat = self.app.state_heartbeat
        self.app.config.set("state_sandbox_max_states", 40)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _a_model(self):
        import sys

        sys.path.insert(0, "tests")
        from test_state_continuity import _asking

        return _asking()

    def test_a_rehearsal_runs_while_the_real_round_is_in_flight(self) -> None:
        self.beat.models = self._a_model()
        self.beat._running.acquire()          # the hourly round
        try:
            out = self.beat.rehearse_append(first=50, then=8, after=6,
                                            days=10, seed=7)
        finally:
            self.beat._running.release()
        self.assertFalse(out.get("given_up"), out.get("why"))
        self.assertFalse(out.get("skipped"), out.get("skipped"))
        for which in ("round_one", "round_two", "round_three"):
            self.assertIn(which, out)

    def test_and_two_sandbox_rounds_still_wait_for_each_other(self) -> None:
        """Separate from the real one, not free of each other: two
        rounds over the same folder would read files the other is
        writing."""
        self.beat.models = self._a_model()
        held = self.beat._running_in("sandbox")
        held.acquire()
        try:
            out = self.beat.run_once(trigger="manual_debug", scope="sandbox")
        finally:
            held.release()
        self.assertIn("already running", str(out.get("skipped")))

    def test_and_a_round_that_never_began_is_not_one_that_gave_up(self) -> None:
        """The two have different answers: one is wait and try again, the
        other is that the data went away."""
        self.beat.models = self._a_model()
        held = self.beat._running_in("sandbox")
        held.acquire()
        try:
            out = self.beat.rehearse_append(first=20, then=4, after=4,
                                            days=5, seed=3)
        finally:
            held.release()
        self.assertFalse(out.get("given_up"))
        self.assertIn("did not start", str(out.get("why")))
        self.assertNotIn("emptied", str(out.get("why")))

    def test_and_emptying_it_does_not_stop_the_rehearsal_that_did_it(self) -> None:
        """`clear_sandbox` asks whatever is running to stop. A rehearsal
        empties the sandbox itself, as its first step, and is not an
        interruption of itself."""
        self.beat.models = self._a_model()
        self.beat.clear_sandbox()
        self.assertFalse(self.beat._going(self.beat.where("sandbox")))
        out = self.beat.rehearse_append(first=20, then=4, after=4,
                                        days=5, seed=3)
        self.assertFalse(out.get("given_up"), out.get("why"))
        self.assertIn("round_one", out)

"""AN ANSWER CUT OFF IS NOT AN ANSWER (the user, 2026-11-07): read as nothing, it was taken for
"nothing to do" and what it was about let go. The common part: how an answer is told whole,
cut off or unreadable; halves for a batch too big; one output limit for every call."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import support  # noqa: F401

from personalized_agent.models import answers
from personalized_agent.models.manager import ModelManager


def said(text, stop="end_turn"):
    return SimpleNamespace(text=text, stop_reason=stop)


class ReadTests(unittest.TestCase):

    def test_whole_and_readable(self) -> None:
        got = answers.read(said('{"states": []}'), wants=["states"])
        self.assertTrue(got.ok, "an empty list is an answer: nothing was going on")
        self.assertEqual(got.data, {"states": []})

    def test_cut_off_by_the_models_word(self) -> None:
        got = answers.read(said('{"states": []}', stop="max_tokens"))
        self.assertFalse(got.ok)
        self.assertTrue(got.cut)
        self.assertIn("cut off", got.why_not)
        self.assertTrue(answers.read(said('{"a": 1}', stop="length")).cut)

    def test_cut_off_by_json_left_open(self) -> None:
        """Claude Code and Codex nearly always say end_turn: the JSON tells it."""
        got = answers.read(said('{"states": [{"title": "Reading the survey", "detail": "half'))
        self.assertTrue(got.cut)
        self.assertFalse(got.ok)

    def test_empty_unreadable_or_without_what_is_wanted(self) -> None:
        self.assertEqual(answers.read(said("")).why_not, "the answer was empty")
        self.assertEqual(answers.read(said("I could not decide.")).why_not,
                         "the answer could not be read")
        self.assertFalse(answers.read(said('{"notes": []}'), wants=["states"]).ok)
        self.assertFalse(answers.read(said("I could not decide.")).cut)

    def test_fenced_and_with_words_around(self) -> None:
        self.assertEqual(answers.read(said('Here:\n```json\n{"a": {"b": 2}}\n```')).data,
                         {"a": {"b": 2}})
        self.assertEqual(answers.read(said('So {"a": "}"} it is.')).data, {"a": "}"})

    def test_text_and_whether_it_was_cut(self) -> None:
        self.assertEqual(answers.read_text(said("Half a sent", stop="max_tokens")),
                         ("Half a sent", True))


class InPartsTests(unittest.TestCase):

    def test_whole_when_it_fits(self) -> None:
        asked = []

        def ask(items):
            asked.append(list(items))
            return answers.read(said('{"ok": true}'))
        parts = answers.in_parts(list(range(8)), ask)
        self.assertEqual(asked, [list(range(8))])
        self.assertEqual(len(parts), 1)

    def test_cut_off_asked_again_in_halves_down_to_what_fits(self) -> None:
        asked = []

        def ask(items):
            asked.append(len(items))
            return answers.read(said('{"ok": true}', stop="max_tokens" if len(items) > 2 else "end_turn"))
        parts = answers.in_parts(list(range(8)), ask)
        self.assertEqual(asked, [8, 4, 2, 2, 4, 2, 2])
        self.assertTrue(all(answer.ok for _part, answer in parts))
        self.assertEqual(sum(len(part) for part, _answer in parts), 8)

    def test_unreadable_is_not_halved_and_the_smallest_is_given_back(self) -> None:
        asked = []

        def ask(items):
            asked.append(len(items))
            return answers.read(said("no idea"))
        parts = answers.in_parts(list(range(6)), ask)
        self.assertEqual(asked, [6], "unreadable, not too long: halves would not help")
        self.assertFalse(parts[0][1].ok)

        def always_cut(items):
            return answers.read(said('{"a": 1}', stop="max_tokens"))
        parts = answers.in_parts([1, 2, 3], always_cut)
        self.assertEqual([len(part) for part, _answer in parts], [1, 1, 1])
        self.assertTrue(all(not answer.ok for _part, answer in parts))


class OneLimitTests(unittest.TestCase):

    def _manager(self, setting=20000):
        return SimpleNamespace(config=SimpleNamespace(
            get=lambda key, default=None: setting if key == "max_output_tokens" else default))

    def _provider(self, most=None):
        return SimpleNamespace(list_models=lambda: [SimpleNamespace(id="m", max_output_tokens=most)])

    def test_every_call_at_least_the_setting(self) -> None:
        limit = ModelManager._output_limit(self._manager(), self._provider(), "m", 300)
        self.assertEqual(limit, 20000, "a call that asked for 300 is given the setting")
        self.assertEqual(ModelManager._output_limit(self._manager(), self._provider(), "m", 64000),
                         64000, "one that asked for more keeps it")

    def test_held_to_what_the_model_can_write(self) -> None:
        self.assertEqual(ModelManager._output_limit(self._manager(), self._provider(8192), "m", 300),
                         8192)
        self.assertEqual(ModelManager._output_limit(self._manager(), self._provider(), "other", 300),
                         20000, "a model the catalog does not list: the setting")

    def test_a_value_kept_from_before_is_lifted(self) -> None:
        from personalized_agent.common.config.schema import SCHEMA_BY_KEY, coerce
        self.assertEqual(coerce("max_output_tokens", 4096), 20000)
        self.assertEqual(SCHEMA_BY_KEY["max_output_tokens"].default, 20000)


if __name__ == "__main__":
    unittest.main()

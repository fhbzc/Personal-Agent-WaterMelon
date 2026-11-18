"""THE SMALL MODEL (models/manager.py SMALL_MODELS; the user, 2026-11-17: "小模型的default改成对应版本的弱模型的low effort
模式（比如haiku)") - the chat's quick reply, the side watch and co-play."""

from __future__ import annotations

import unittest

from support import TemporaryAgent

from personalized_agent.models import manager as mm


class SmallModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.models = self.agent.app.agent.models
        self.config = self.agent.app.config

    def test_each_model_on_the_list_as_its_providers_weaker_one(self) -> None:
        self.config.set("model_chain", [{"provider": "claude_code", "model": "claude-opus-5-5"},
                                        {"provider": "anthropic", "model": "claude-sonnet-5-5"}], internal=True)
        small = [one.label for one in self.models.chain("fast_reply")]
        self.assertEqual(small[:2], ["claude_code/claude-haiku-5-5", "anthropic/claude-haiku-5-5"], "in the list's order")
        self.assertEqual(small[2:], ["claude_code/claude-opus-5-5", "anthropic/claude-sonnet-5-5"],
                         "then the list as it is, should a weaker one not answer")
        self.assertEqual([one.label for one in self.models.chain("primary")],
                         ["claude_code/claude-opus-5-5", "anthropic/claude-sonnet-5-5"], "the rest as they are")

    def test_a_gpt_subscription_has_its_weaker_one_and_the_users_choice_wins(self) -> None:
        """2026-11-17: "如果用gpt subscription, 对应的小模型也要设置"."""
        self.assertEqual(mm.small_model_of("codex", "gpt-6.1-sol"), "gpt-6.1-terra")
        self.assertEqual(mm.small_model_of("codex", "gpt-6-astra"), "gpt-6-luna")
        self.assertEqual(mm.small_model_of("codex", "gpt-5.6-sol"), "gpt-5.6-luna")
        self.assertEqual(mm.small_model_of("codex", "default"), "gpt-6-luna")
        self.assertEqual(mm.small_model_of("openai_compatible", "some-model"), "some-model", "none known: itself")
        self.config.set("chat_fast_model", "anthropic/claude-sonnet-5-5", internal=True)
        resolved = self.models.resolve("fast_reply")
        self.assertEqual((resolved.label, resolved.is_override), ("anthropic/claude-sonnet-5-5", True))

    def test_chosen_in_settings_default_at_first(self) -> None:
        """2026-11-17: "setting里显示小模型并且允许用户设置，一开始是default"."""
        self.models.set_chain([{"provider": "claude_code", "model": "claude-opus-5-5"}])
        [row] = self.models.describe()["chain"]
        self.assertEqual((row["small"], row["small_default"]), ("", "claude-haiku-5-5"), "default at first")
        self.models.set_chain([{"provider": "claude_code", "model": "claude-opus-5-5", "small": "claude-sonnet-5-5"}])
        self.assertEqual(self.models.chain("fast_reply")[0].model_id, "claude-sonnet-5-5", "as chosen")
        self.assertEqual(self.models.describe()["chain"][0]["small"], "claude-sonnet-5-5")
        self.models.set_chain([{"provider": "claude_code", "model": "claude-opus-5-5", "small": "default"}])
        self.assertEqual(self.models.chain("fast_reply")[0].model_id, "claude-haiku-5-5", "default again")

    def test_its_effort_chosen_else_the_least(self) -> None:
        """2026-11-17: "small model的effort也是可以选的"."""
        from unittest import mock
        self.models.set_chain([{"provider": "claude_code", "model": "claude-opus-5-5"}])
        with mock.patch.object(self.models, "efforts_of", return_value=["low", "medium", "high"]):
            self.models.set_chain([{"provider": "claude_code", "model": "claude-opus-5-5", "small_effort": "medium"}])
        self.assertEqual(self.models.small_effort_for("claude_code", "claude-haiku-5-5"), "medium")
        self.assertEqual(self.models.describe()["chain"][0]["small_effort"], "medium")
        self.models.set_chain([{"provider": "claude_code", "model": "claude-opus-5-5"}])
        self.assertEqual(self.models.small_effort_for("claude_code", "claude-haiku-5-5"), "", "none: the least")

    def test_the_watchers_ask_it(self) -> None:
        from personalized_agent.heartbeat.monitor import co_play, heads_up
        self.assertEqual((heads_up.ROLE, co_play.ROLE), ("fast_reply", "fast_reply"))
        self.assertIn("claude_code", mm.SMALL_MODELS)


if __name__ == "__main__":
    unittest.main()

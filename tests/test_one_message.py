"""One problem, one message (section 88.1): a sign-in that stopped working is
asked for by the application itself ("Sign in to ... again"), so the chat
and the heartbeat, meeting the same failure, do not each say it again. A
failure the user must fix that is not a sign-in is still said."""

from __future__ import annotations

import unittest

from support import TemporaryAgent

from personalized_agent.models.base import AvailabilityState, FailureClass, ModelAvailability
from personalized_agent.models.manager import ModelUnavailable, ResolvedModel


class OneProblemOneMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _model_fails(self, state: AvailabilityState) -> None:
        def invoke(request, role="primary"):
            raise ModelUnavailable(
                ResolvedModel(role, "anthropic", "claude-test", False),
                ModelAvailability(available=False, state=state, reason="it said no",
                                  failure_class=FailureClass.USER_ACTION_REQUIRED))
        self.app.models.invoke = invoke

    def _titles(self):
        return [row["title"] for row in self.app.notifications.recent(50)]

    def test_a_lost_sign_in_is_not_said_again_by_the_chat(self) -> None:
        for state in (AvailabilityState.AUTHENTICATION_REQUIRED,
                      AvailabilityState.CREDENTIAL_INVALID):
            self._model_fails(state)
            said = self.app.agent.handle_message("hello")
            self.assertIn("unavailable", said["answer"])   # the reply still says why
            self.assertNotIn("Model unavailable", self._titles())

    def test_another_problem_the_user_must_fix_is_still_said(self) -> None:
        self._model_fails(AvailabilityState.QUOTA_EXHAUSTED)
        self.app.agent.handle_message("hello")
        self.assertIn("Model unavailable", self._titles())

    def test_the_sign_in_states_are_the_ones_the_application_asks_for(self) -> None:
        from personalized_agent.models.base import SIGN_IN_STATES
        from personalized_agent.models.manager import _STATE_EVENTS
        asked = {state for state, topic in _STATE_EVENTS.items()
                 if topic in ("model.authentication_required", "model.authentication_failed")}
        self.assertEqual(set(SIGN_IN_STATES), asked)


if __name__ == "__main__":
    unittest.main()

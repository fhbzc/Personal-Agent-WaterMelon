"""THE MODELS, IN ORDER: each call goes to the first model that works. One
that cannot answer as things stand - a sign-in expired, a key refused,
credit or a usage limit used up, a model not offered - passes the call to
the next; the first is used again once it works. A call that only timed out
is not passed on: it is raised, to be tried again. When none can answer,
the first one's failure is raised - it is the one the user put first.

The list is the user's (Settings > General > Models), its first kept as the
primary model too; from before there was one, it is the primary model and,
when it was turned on, the fallback."""

from __future__ import annotations

import unittest

from support import TemporaryAgent

from personalized_agent.models.base import (AuthMethod, AuthStatus, AvailabilityState,
                                            CredentialState, FailureClass,
                                            ModelAvailability, ModelDescriptor,
                                            ModelInvocationError, ModelProvider,
                                            ModelRequest, ModelResponse, ModelUsage)
from personalized_agent.models.manager import ModelUnavailable


class _Scripted(ModelProvider):
    """A provider that answers, or fails as `fails` says, and counts calls."""

    def __init__(self, provider_id, kind="api"):
        self.provider_id = provider_id
        self.display_name = provider_id.title()
        self.kind = kind
        self.fails = None
        self.calls = 0

    def list_models(self):
        return [ModelDescriptor("m1", "M1")]

    def get_auth_methods(self):
        return [AuthMethod.API_KEY]

    def get_auth_status(self):
        return AuthStatus(CredentialState.AUTHENTICATED, AuthMethod.API_KEY)

    def authenticate(self, config):
        return self.get_auth_status()

    def logout(self):
        return self.get_auth_status()

    def check_availability(self, model):
        return ModelAvailability(True, AvailabilityState.AVAILABLE)

    def invoke(self, request, model):
        self.calls += 1
        if self.fails is not None:
            state, failure, reason = self.fails
            raise ModelInvocationError(reason, ModelAvailability(
                False, state, reason=reason, failure_class=failure))
        return ModelResponse(text=f"from {self.provider_id}", usage=ModelUsage(1, 1),
                             model=model, provider=self.provider_id)


EXPIRED = (AvailabilityState.AUTHENTICATION_REQUIRED, FailureClass.USER_ACTION_REQUIRED,
           "the sign-in has expired")
LIMIT = (AvailabilityState.RATE_LIMITED, FailureClass.TEMPORARY,
         "You've hit your session limit · resets 11:50pm")
TIMEOUT = (AvailabilityState.PROVIDER_UNAVAILABLE, FailureClass.TEMPORARY,
           "did not answer within 300s")


class TheModelsInOrderTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.models = self.agent.app.models
        self.first = _Scripted("first", kind="client")
        self.second = _Scripted("second")
        self.third = _Scripted("third")
        for one in (self.first, self.second, self.third):
            self.models.register(one)
        self.models.set_chain([{"provider": "first", "model": "m1"},
                               {"provider": "second", "model": "m1"},
                               {"provider": "third", "model": "m1"}])

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _ask(self):
        return self.models.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}]))

    def test_the_first_answers_and_is_the_primary(self) -> None:
        self.assertEqual(self._ask().text, "from first")
        self.assertEqual(self.agent.app.config.get("primary_provider"), "first")
        self.assertEqual(self.models.resolve("primary").label, "first/m1")
        said = self.models.describe()
        self.assertEqual([one["label"] for one in said["chain"]],
                         ["first/m1", "second/m1", "third/m1"])
        self.assertEqual(said["answered"], "first/m1")
        self.assertEqual(said["chain"][0]["kind"], "client")

    def test_an_expired_sign_in_passes_to_the_next(self) -> None:
        self.first.fails = EXPIRED
        self.assertEqual(self._ask().text, "from second")
        self.assertEqual(self.models.answered["primary"], "second/m1")
        # Known for a while to need the user: not asked again, passed on.
        self.assertEqual(self._ask().text, "from second")
        self.assertEqual(self.first.calls, 1)
        # Mended: the first again.
        self.first.fails = None
        self.models.invalidate()
        self.assertEqual(self._ask().text, "from first")

    def test_a_usage_limit_passes_on_and_a_timeout_does_not(self) -> None:
        self.first.fails = LIMIT
        self.assertEqual(self._ask().text, "from second")
        self.models.invalidate()
        self.first.fails = TIMEOUT
        with self.assertRaises(ModelUnavailable) as caught:
            self._ask()
        self.assertIn("did not answer", str(caught.exception))
        self.assertEqual(self.second.calls, 1, "a timeout is tried again, not passed on")

    def test_none_can_answer_the_first_ones_failure(self) -> None:
        for one in (self.first, self.second, self.third):
            one.fails = EXPIRED
        with self.assertRaises(ModelUnavailable) as caught:
            self._ask()
        self.assertEqual(caught.exception.resolved.provider_id, "first")
        # A later one only timed out: that is what is said - try again.
        self.models.invalidate()
        self.third.fails = TIMEOUT
        with self.assertRaises(ModelUnavailable) as caught:
            self._ask()
        self.assertEqual(caught.exception.resolved.provider_id, "third")

    def test_each_model_thinks_as_hard_as_chosen_for_it(self) -> None:
        self.first.reasoning_options = lambda model: {"efforts": ["default", "low", "high"]}
        seen = []
        answer = self.first.invoke
        self.first.invoke = lambda request, model: (seen.append(request.reasoning_effort),
                                                    answer(request, model))[1]
        kept = self.models.set_chain([{"provider": "first", "model": "m1", "effort": "high"},
                                      {"provider": "second", "model": "m1"}])
        self.assertEqual(kept[0], {"provider": "first", "model": "m1", "effort": "high"})
        self._ask()
        self.assertEqual(seen, ["high"])
        said = self.models.describe()["chain"]
        self.assertEqual((said[0]["effort"], said[0]["efforts"]), ("high", ["low", "high"]))
        # A level the model does not take is refused, not sent.
        with self.assertRaises(ValueError):
            self.models.set_chain([{"provider": "first", "model": "m1", "effort": "max"}])
        # "default" is the model's own: nothing kept, nothing sent.
        kept = self.models.set_chain([{"provider": "first", "model": "m1", "effort": "default"}])
        self.assertNotIn("effort", kept[0])

    def test_the_list_is_checked_as_it_is_kept(self) -> None:
        with self.assertRaises(ValueError):
            self.models.set_chain([{"provider": "nobody", "model": "m1"}])
        with self.assertRaises(ValueError):
            self.models.set_chain([{"provider": "first", "model": ""}])
        kept = self.models.set_chain([{"provider": "second", "model": "m1"},
                                      {"provider": "second", "model": "m1"}])
        self.assertEqual(kept, [{"provider": "second", "model": "m1"}])

    def test_from_before_the_list(self) -> None:
        config = self.agent.app.config
        config.set("model_chain", [])
        config.set("primary_provider", "first")
        config.set("primary_model", "m1")
        config.set("model_fallback_enabled", True)
        config.set("model_fallback_target", "second/m1")
        self.assertEqual([one.label for one in self.models.chain()], ["first/m1", "second/m1"])
        self.first.fails = EXPIRED
        self.assertEqual(self._ask().text, "from second")

    def test_the_route_keeps_the_order(self) -> None:
        from personalized_agent.api.errors import ApiError
        from personalized_agent.api.routes import build_routes
        routes = build_routes(self.agent.app)

        class Request:
            def __init__(self, body):
                self.body = body

            def get(self, key, default=None):
                return self.body.get(key, default)

        said = routes[("POST", "/api/models/chain")](Request({"chain": [
            {"provider": "third", "model": "m1"}, {"provider": "first", "model": "m1"}]}))
        self.assertEqual([one["label"] for one in said["chain"]], ["third/m1", "first/m1"])
        # Emptied is allowed (the last model can be removed); no list is not.
        self.assertEqual(routes[("POST", "/api/models/chain")](Request({"chain": []}))["chain"], [])
        with self.assertRaises(ApiError):
            routes[("POST", "/api/models/chain")](Request({}))


if __name__ == "__main__":
    unittest.main()

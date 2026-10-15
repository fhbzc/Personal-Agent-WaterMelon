"""A stale Codex picker list must not deny the account a newer model.

Ported from PR #4 (by daniela-miaut). Codex is started through the children
wrapper here, so that is where its process is caught.
"""

import json
import os
import subprocess
import unittest
from unittest.mock import patch

from support import TemporaryAgent
from personalized_agent.api.routes import build_routes
from personalized_agent.api.server import Request
from personalized_agent.models.base import AvailabilityState, ModelInvocationError, ModelRequest
from personalized_agent.models.manager import ROLES, ModelManager

RUN = "personalized_agent.common.children.run"


class CodexCatalogAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.provider = self.app.models.provider("codex")
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(self.agent.dir)})
        self.env.start()
        self.cache = self.agent.dir / "models_cache.json"
        self.cache.write_text(json.dumps({"models": [
            {"slug": "gpt-5.6-luna", "visibility": "list", "default_reasoning_level": "medium",
             "supported_reasoning_levels": [{"effort": "low"}, {"effort": "medium"}]},
        ]}), encoding="utf-8")
        self.provider.executable = lambda: "codex"
        self.provider._auth_status = lambda force=False: {"logged_in": True}
        self.app.config.set("primary_provider", "codex")
        self.app.config.set("primary_model", "gpt-6-luna")

    def tearDown(self):
        self.env.stop()
        self.agent.cleanup()

    def test_absence_from_the_cache_does_not_block_any_inherited_role(self):
        self.assertNotIn("gpt-6-luna", [m.id for m in self.provider.list_models()])
        for role in ROLES:
            with self.subTest(role):
                self.assertTrue(self.app.models.check_availability(role).available)
        self.assertTrue(self.app.models.model_belongs_to_provider("codex", "gpt-6.1-sol"))

    def test_stale_catalog_does_not_replace_the_model_on_startup(self):
        manager = ModelManager(self.app.config, self.app.credentials)
        self.assertEqual(manager.resolve().model_id, "gpt-6-luna")
        self.assertEqual(self.app.config.get("primary_model"), "gpt-6-luna")
        self.assertEqual(manager.reconcile_primary(), {})

    def test_the_new_model_is_sent_to_codex_instead_of_denied_locally(self):
        output = '{"type":"item.completed","item":{"type":"agent_message","text":"Luna works"}}'
        with patch(RUN, return_value=subprocess.CompletedProcess([], 0, output, "")) as run:
            response = self.app.models.invoke(ModelRequest([{"role": "user", "content": "hello"}]))
        self.assertEqual(response.text, "Luna works")
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--model") + 1], "gpt-6-luna")

    def test_a_partial_or_missing_cache_keeps_the_last_usable_catalog(self):
        before = self.provider.list_models()
        for payload in ('{"models": [', '{"models": []}'):
            with self.subTest(payload):
                self.cache.write_text(payload, encoding="utf-8")
                self.assertEqual(self.provider.list_models(), before)
        self.cache.unlink()
        self.assertEqual(self.provider.list_models(), before)
        self.assertEqual(self.provider.reasoning_options("gpt-5.6-luna")["efforts"],
                         ["default", "low", "medium"])
        self.assertTrue(self.app.models.check_availability().available)

    def test_obvious_provider_mismatches_are_still_rejected(self):
        for model in ("offline-1", "claude-opus-5", "opus", "gpt-6-luna extra",
                      "gpt-6-luna/other"):
            with self.subTest(model):
                self.assertFalse(self.provider.accepts_model_id(model))
        self.app.config.set("primary_model", "offline-1")
        self.assertEqual(self.app.models.check_availability().state,
                         AvailabilityState.MODEL_UNAVAILABLE)

    def test_real_codex_failures_are_still_reported(self):
        with patch(RUN, return_value=subprocess.CompletedProcess(
                [], 1, "", "This model is unavailable to your account")):
            with self.assertRaises(ModelInvocationError):
                self.provider.invoke(ModelRequest([]), "gpt-6-luna")

    def test_refresh_discards_old_availability_without_changing_the_selection(self):
        before = self.app.config.all()
        req = Request("POST", "/api/models/codex/refresh", {}, {}, {})
        routes = build_routes(self.app)
        self.app.models.check_availability()
        self.assertTrue(self.app.models._availability)
        with patch.object(self.provider, "refresh_models",
                          return_value=self.provider.list_models()):
            routes[(req.method, req.path)](req)
        self.assertFalse(self.app.models._availability)
        self.assertEqual(self.app.config.all(), before)


if __name__ == "__main__":
    unittest.main()

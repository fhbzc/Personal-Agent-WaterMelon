"""Which language each model call writes in - said in one way only,
`language=in_display(...) + in_english(...)`, and read off the source by the
Language debugger (common/output_languages.py), beside what was sent."""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from support import TemporaryAgent
from test_connections_panel import _render
from test_panels import _javascript_engine

from personalized_agent.common import output_languages as langs
from personalized_agent.common.languages import (EVERYTHING_READ, display_instruction,
                                                 in_display, in_english)


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class ALanguageIsSaidOnceTests(unittest.TestCase):

    def test_the_words_the_model_is_given(self) -> None:
        config = _Config(display_language="zh")
        said = (in_display() + in_english("the states")).compose("THE PROMPT.", config)
        head, rest = said.split("\n\n", 1)
        self.assertIn("WRITE IT IN THIS LANGUAGE: Chinese", head, "named at the top")
        self.assertIn("THE PROMPT.", rest)
        self.assertIn(display_instruction(config), said, "and in full at the foot")
        self.assertIn("EXCEPTION, for the states: write them in English", said)
        self.assertIn("keeps its own wording", said)

    def test_english_alone(self) -> None:
        said = in_english("the keywords").compose("P", _Config(display_language="zh"))
        self.assertNotIn("Chinese", said)
        self.assertIn("Write the keywords in English", said)

    def test_what_it_covers_is_said_and_recorded(self) -> None:
        language = in_display("the statement") + in_english("its keywords")
        self.assertIn("That is: the statement.", language.instruction(_Config()))
        self.assertEqual(language.rules(), [{"rule": "display", "what": "the statement"},
                                            {"rule": "internal", "what": "its keywords"}])
        self.assertEqual(in_display().rules()[0]["what"], EVERYTHING_READ)


class ReadOffTheSourceTests(unittest.TestCase):

    def test_every_model_call_in_the_code_says_its_language(self) -> None:
        code = langs.read_code()
        self.assertGreater(len(code["said"]), 30, "the code was read")
        self.assertEqual(code["unspecified"], [],
                         "a model call that does not say which language it writes in")
        self.assertTrue(all(one["task"] for one in code["said"]),
                        [one["where"] for one in code["said"] if not one["task"]])

    def test_the_states_english_recommendations_display(self) -> None:
        said = {one["task"]: one for one in langs.read_code()["said"]}
        self.assertEqual(said["heartbeat:step_state"]["rule"], langs.MIXED)
        self.assertEqual(said["reading"]["rule"], langs.INTERNAL)
        pages = [one for one in langs.read_code()["said"] if one["task"] == "connections"
                 and "heading" in json.dumps(one["rules"])]
        # What the user reads in theirs; each story's English copy in English.
        self.assertEqual(pages[0]["rule"], langs.MIXED)

    def test_a_change_in_the_code_is_seen_at_once(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "one.py").write_text(textwrap.dedent('''
            from personalized_agent.common.languages import in_display, in_english
            LANGUAGE = in_display("the title") + in_english("the tags")

            def ask(models, language=None):
                return models.invoke(ModelRequest(task="one_task", language=language))

            def first(models):
                return ask(models, language=LANGUAGE)

            def second(models):
                return ask(models)

            def third(models):
                return models.invoke(ModelRequest(task="bare", messages=[]))

            PROMPT = "Write the answer in English, please."
            '''), encoding="utf-8")
        code = langs.read_code(root)
        said = code["said"][0]
        self.assertEqual((said["task"], said["rule"]), ("one_task", langs.MIXED))
        self.assertEqual(sorted((one["task"], one["in"]) for one in code["unspecified"]),
                         [("bare", "third"), ("one_task", "second")])
        self.assertEqual([one["where"] for one in code["inline"]], ["one.py:17"])


class WhatWasSentTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_each_call_records_its_language(self) -> None:
        self.app.model_calls.record(provider="p", model="m", task="reading", input_tokens=1,
                                    output_tokens=1, latency_ms=1,
                                    language=json.dumps(in_english("x").rules()))
        self.app.model_calls.record(provider="p", model="m", task="old", input_tokens=1,
                                    output_tokens=1, latency_ms=1)
        sent = {one["task"]: one for one in langs.read_sent(self.app.db)}
        self.assertEqual(sent["reading"]["rule"], langs.INTERNAL)
        self.assertEqual(sent["old"]["rule"], langs.UNSPECIFIED)

    def test_the_manager_sends_and_records_it(self) -> None:
        from personalized_agent.models.base import ModelRequest
        sent = []
        manager = self.app.models
        provider = manager.provider("offline")
        original = provider.invoke
        provider.invoke = lambda request, *a, **k: (sent.append(request.system),
                                                    original(request, *a, **k))[1]
        self.app.config.set("display_language", "zh")
        try:
            manager.invoke(ModelRequest(messages=[{"role": "user", "content": "hi"}],
                                        system="THE PROMPT.", task="chat_followup",
                                        language=in_display()), role="primary")
        finally:
            provider.invoke = original
        if not sent:
            self.skipTest("the offline provider is not the one resolved here")
        self.assertTrue(sent[0].startswith("WRITE IT IN THIS LANGUAGE: Chinese"))
        self.assertIn("THE PROMPT.", sent[0])
        row = self.app.db.query_one("SELECT language FROM model_calls WHERE "
                                    "task = 'chat_followup'")
        self.assertEqual(json.loads(row["language"])[0]["rule"], "display")

    def test_the_route(self) -> None:
        from personalized_agent.api.routes import build_routes
        self.app.config.set("display_language", "zh")
        said = build_routes(self.app)[("GET", "/api/debugger/languages")](None)
        self.assertIn("Chinese", said["display_language"])
        self.assertEqual(said["groups"]["unspecified"], [])
        self.assertIn("sent", said)


class AProjectIsATopLevelTaskTests(unittest.TestCase):
    """A project is the aggregate of top-level tasks, the same to the user
    and the program: its name and status in the display language, as theirs."""

    def test_its_questions_say_the_display_language(self) -> None:
        from personalized_agent.models.base import ModelResponse
        agent = TemporaryAgent()
        try:
            asked = []

            class _Models:
                def invoke(self, request, role=""):
                    asked.append(request.language)
                    return ModelResponse(text='{"status": "ok"}')

            beat = agent.app.state_heartbeat
            beat.models = _Models()
            beat._ask_project("SAY WHERE IT STANDS.", "the project", {})
            self.assertEqual(asked[0].rules()[0]["rule"], "display")
        finally:
            agent.cleanup()


class ThePanelTests(unittest.TestCase):

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_each_kind_its_table_and_what_was_sent(self) -> None:
        said = langs.described("Chinese (zh)")
        said["sent"] = [{"task": "reading", "rules": in_english("x").rules(),
                         "rule": "internal", "calls": 4, "last": "2026-10-14T08:00:00+00:00"},
                        {"task": "old", "rules": [], "rule": "unspecified", "calls": 2,
                         "last": "2026-10-13T08:00:00+00:00"}]
        steps = ("document.querySelector('#tabs button[data-tab=\"languagedebug\"]').click();"
                 "await loadLanguages();await wait();"
                 "const host=document.querySelector('#lang-debug');"
                 "out.text=text(host);out.shown=host.getBoundingClientRect().height>0;")
        result = _render(self.engine, {"/api/debugger/languages": said}, steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertTrue(out["shown"])
        for words in ("Your display language: Chinese (zh)",
                      "In English (the agent's internal language)", "In your display language",
                      "Partly each", "Not specified (0)", "heartbeat:step_state",
                      "Still said inside a prompt", "What was sent, the last 7 days",
                      "1 kind of call went out with none"):
            self.assertIn(words, out["text"])


if __name__ == "__main__":
    unittest.main()

"""References an agent can open: whether it can, a local original, a copy of
what was seen - and the copies no stage cites any more, cleared away."""

from __future__ import annotations

import unittest

from personalized_agent.heartbeat.experience.references import access_of


class WhetherItCanBeOpenedTests(unittest.TestCase):

    def test_access(self) -> None:
        self.assertEqual(access_of({"location": __file__}), "local")
        self.assertEqual(access_of({"location": r"C:\nowhere\gone.pptx"}),
                         "screen-only")
        self.assertEqual(access_of({"location": "https://arxiv.org/abs/2401.1"}),
                         "public")
        self.assertEqual(access_of({"location": "https://docs.google.com/presentation/d/x"}),
                         "sign-in")
        self.assertEqual(access_of({"location": "Overleaf project 'Fieldnotes'"}),
                         "sign-in")
        self.assertEqual(access_of({"location": "ChatGPT conversation 'x'"}),
                         "sign-in")
        self.assertEqual(access_of({"kind": "email", "location": "'Hello' (to x)"}),
                         "sign-in")
        self.assertEqual(access_of({"location": "http://127.0.0.1:8756/api"}),
                         "sign-in")

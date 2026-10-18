"""Starting at sign-in while another copy of the program already does: the
Settings card says which copy holds it, and cannot be saved with the switch on
(platform_layer/autostart.py). Unticked, it saves."""

from __future__ import annotations

import json
import unittest

from test_connections_panel import _render
from test_panels import _javascript_engine

HELD = "D:\\Elsewhere\\PersonalizedAgent.exe"
FIELD = {"key": "start_at_login", "label": "Start when I sign in", "type": "bool",
         "section": "General", "description": "Start the agent when you sign in.",
         "default": True}


def _data(on: bool, held: str) -> str:
    return json.dumps({"values": {"start_at_login": on, "agent_root": ""},
                       "schema": [FIELD], "start_at_login_held_by": held,
                       "choices": {}, "choice_labels": {}, "choice_notes": {},
                       "user_defaults": {}})


STEPS = ("renderSettings({data});await wait();"
         "const host=document.querySelector('#settings-form-startup');"
         "const note=host.querySelector('[data-start-held]');"
         "out.shown=!!note&&!note.hidden;out.says=text(note);"
         "settingsChanged(host.querySelector('.setting-row'),'start_at_login',true,'Start');"
         "out.saveOn=!host.querySelector('[data-settings-save]').disabled;"
         "settingsChanged(host.querySelector('.setting-row'),'start_at_login',false,'Start');"
         "out.saveOff=!host.querySelector('[data-settings-save]').disabled;"
         "out.shownOff=!note.hidden;")


class AnotherCopyHoldsItTests(unittest.TestCase):

    def setUp(self) -> None:
        kind, self.engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_said_and_not_saved_until_unticked(self) -> None:
        result = _render(self.engine, {}, STEPS.replace("{data}", _data(True, HELD)))
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertTrue(out["shown"])
        self.assertIn("already starts when you sign in", out["says"])
        self.assertIn(HELD, out["says"])
        self.assertFalse(out["saveOn"], "saved on while another copy holds it")
        self.assertTrue(out["saveOff"], "unticked, it cannot be saved")
        self.assertFalse(out["shownOff"])

    def test_nothing_said_when_no_other_copy_holds_it(self) -> None:
        result = _render(self.engine, {}, STEPS.replace("{data}", _data(True, "")))
        self.assertEqual(result["err"], "")
        self.assertFalse(result["out"]["shown"])
        self.assertTrue(result["out"]["saveOn"])


if __name__ == "__main__":
    unittest.main()

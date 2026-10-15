"""The tabs: the user's first, then the Debugger and every debugger after it
- none of them in an installed build. And the installer: it asks which
language first, and the program starts in it - unless the user chose one."""

from __future__ import annotations

import re
import shutil
import tempfile
import unittest
from pathlib import Path

from test_connections_panel import _render
from test_panels import _javascript_engine

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "personalized_agent" / "api" / "web"


def _tabs() -> list:
    page = (WEB / "index.html").read_text(encoding="utf-8")
    nav = page[page.index('<nav id="tabs"'):page.index("</nav>")]
    return re.findall(r'<button data-tab="([^"]+)">([^<]+)</button>', nav)


class TheTabsTests(unittest.TestCase):

    def test_every_debugger_after_the_debugger(self) -> None:
        names = [key for key, _label in _tabs()]
        first = names.index("debugger")
        for key, label in _tabs():
            if "debugger" in label.lower() and key != "debugger":
                self.assertGreater(names.index(key), first, label)
        self.assertTrue(all("debugger" in label.lower() for _k, label in _tabs()[first:]))
        self.assertIn(("statebeat", "State heartbeat debugger"), _tabs())

    def test_an_installed_build_has_none_of_them(self) -> None:
        kind, engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")
        result = _render(engine, {"/api/status": {"debugger_built_in": False}},
                         "await applyDebuggerAvailability();await wait();"
                         "out.tabs=[...document.querySelectorAll('#tabs button')]"
                         ".map(b=>b.textContent.trim());")
        self.assertEqual(result["err"], "")
        self.assertEqual(result["out"]["tabs"], ["Overview", "Customized Recommendation",
                                                 "Calendar", "Connections", "Experience",
                                                 "Lessons", "Chat", "Settings"])


class TheInstallerTests(unittest.TestCase):

    def test_it_asks_which_of_the_five_languages(self) -> None:
        script = (ROOT / "build" / "windows" / "installer.iss").read_bytes()
        self.assertTrue(script.startswith(b"\xef\xbb\xbf"), "UTF-8, for its own words")
        text = script.decode("utf-8-sig")
        self.assertIn("ShowLanguageDialog=yes", text)
        from personalized_agent.common.languages import DISPLAY_CODES
        named = re.findall(r'^Name: "(\w+)"; MessagesFile', text, re.M)
        self.assertEqual(sorted(named), sorted(DISPLAY_CODES))
        self.assertIn('--display-language {language}', text)
        self.assertTrue((ROOT / "build" / "windows" / "ChineseSimplified.isl").exists())

    def test_the_program_starts_in_it_unless_the_user_chose(self) -> None:
        from personalized_agent.__main__ import main
        from personalized_agent.common.config.store import ConfigStore
        from personalized_agent.common.paths import AppPaths, resolve_root
        root = Path(tempfile.mkdtemp())
        try:
            def config():
                paths = AppPaths.create(resolve_root(root))
                return ConfigStore(paths.data_dir, path=paths.settings_file)

            self.assertEqual(main(["--root", str(root), "--display-language", "ja"]), 0)
            self.assertEqual(config().get("display_language"), "ja")
            self.assertNotIn("display_language", config().user_defaults(),
                             "the installer's, not the user's choice")
            config().set("display_language", "zh")         # chosen in Settings
            main(["--root", str(root), "--display-language", "en"])
            self.assertEqual(config().get("display_language"), "zh", "a reinstall keeps it")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

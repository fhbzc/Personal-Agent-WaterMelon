"""The interface in the display language, from fixed tables (HANDOFF.md 7a).

One table for each language the screens can be shown in, every one of the
interface's texts in each; nothing translated on the fly. The page reads
them (api/web/i18n.js), and so do the desktop's menus, the notification's
buttons and the avatar's hover panel (common/interface_words.py). A text
the sources or the server can show that is not in every table fails here -
which is how a new button is not left in English."""

from __future__ import annotations

import html
import importlib.util
import json
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from support import TemporaryAgent
from test_panels import _javascript_engine

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "personalized_agent" / "api" / "web"
SLOT = re.compile(r"\{\d+\}")


def _tool():
    spec = importlib.util.spec_from_file_location("interface_words_tool",
                                                  ROOT / "scripts" / "interface_words.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = _tool()


class _Ask(dict):
    """A request with nothing in it, as a route reads one."""

    def int_param(self, key, default=0):
        return int(self.get(key, default))

    def require(self, key):
        return self[key]


class TheTablesTests(unittest.TestCase):

    def test_one_table_for_every_display_language_but_english(self) -> None:
        from personalized_agent.common.languages import DISPLAY_CODES
        self.assertEqual(sorted(TOOL.LANGUAGES), sorted(c for c in DISPLAY_CODES if c != "en"))
        for code in TOOL.LANGUAGES:
            self.assertTrue((WEB / "lang" / f"{code}.json").is_file(), code)

    def test_every_table_holds_every_text_and_adds_no_place(self) -> None:
        tables = TOOL.tables()
        keys = set(tables["zh"])
        self.assertGreater(len(keys), 2000)
        for code, table in tables.items():
            with self.subTest(language=code):
                self.assertEqual(set(table), keys, "every text, in every table")
                for english, said in table.items():
                    self.assertIsInstance(said, str)
                    # A place the English has not would be filled with nothing.
                    self.assertLessEqual(set(SLOT.findall(said)), set(SLOT.findall(english)),
                                         (english, said))

    def test_nothing_of_a_person_in_them(self) -> None:
        for code, table in TOOL.tables().items():
            for english in table:
                self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.\w+|[A-Za-z]:\\Users", english),
                                  (code, english))

    def test_every_text_the_sources_can_show_is_in_every_table(self) -> None:
        texts = TOOL.source_texts()
        missing = TOOL.missing(texts)
        self.assertEqual(missing, [], "run python scripts/interface_words.py and put "
                         "these in every table (HANDOFF.md 7a): "
                         + json.dumps({t: texts[t] for t in missing[:20]}, ensure_ascii=False))

    def test_every_setting_the_server_explains_is_in_every_table(self) -> None:
        agent = TemporaryAgent()
        try:
            from personalized_agent.api.routes import build_routes
            settings = build_routes(agent.app)[("GET", "/api/settings")](_Ask())
        finally:
            agent.cleanup()
        texts = TOOL.server_texts(settings)
        self.assertGreater(len(texts), 100)
        self.assertEqual(TOOL.missing(texts), [])


class TheDesktopsWordsTests(unittest.TestCase):

    def test_the_menus_and_buttons_in_each_language(self) -> None:
        from personalized_agent.common import interface_words as words
        for code in TOOL.LANGUAGES:
            with self.subTest(language=code):
                for text in ("Quit", "Hide avatar", "Show avatar", "Delegate work",
                             "Got it (no more notifications)"):
                    self.assertNotEqual(words.translated(text, code), text)
                # A number in it: the entry with a place.
                said = words.translated("Remind me in 30 minutes", code)
                self.assertIn("30", said)
                self.assertNotEqual(said, "Remind me in 30 minutes")
        self.assertEqual(words.translated("Quit", "en"), "Quit")
        self.assertEqual(words.translated("Send the revised draft", "zh"), "Send the revised draft")

    def test_the_hover_panel_put_together_in_the_language(self) -> None:
        from personalized_agent.common import interface_words as words
        from personalized_agent.ui.desktop import _schedule_line

        def fill(key, *values):
            return words.fill(key, "zh", *values)
        line = _schedule_line(fill("State heartbeat"),
                              {"running": True, "last_run_seconds_ago": 300,
                               "next_run_in_seconds": 600}, fill)
        self.assertNotRegex(line, r"[A-Za-z]{3}")
        self.assertIn("5", line)
        self.assertIn("10", line)
        # In English, as it always read.
        self.assertEqual(_schedule_line("Heartbeat", {"enabled": False}), "Heartbeat: off")

    def test_a_long_text_with_many_places_is_matched_at_once(self) -> None:
        from personalized_agent.common import interface_words as words
        started = time.monotonic()
        words.translated("x " * 400 + "and nothing of ours", "de")
        self.assertLess(time.monotonic() - started, 1.0)


class ThePageTests(unittest.TestCase):

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def _render(self, steps: str) -> dict:
        page = ("<!doctype html><html><body>"
                "<button id=b>Quit</button>"
                "<div class=msg-text>Quit</div>"
                "<textarea id=t placeholder=\"Say what you want done, as you would to a colleague\">"
                "Quit</textarea><p id=p></p></body></html>")
        table = json.loads((WEB / "lang" / "de.json").read_text(encoding="utf-8"))
        harness = (
            "<script>const TABLE=" + json.dumps(table) + ";"
            "window.fetch=(u)=>Promise.resolve({ok:true,json:()=>Promise.resolve("
            "String(u).startsWith('/lang/')?TABLE:{values:{display_language:'de'}})});</script>"
            "<script>" + (WEB / "i18n.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';const out={};"
            "const wait=(ms)=>new Promise(r=>setTimeout(r,ms||150));"
            "try{await wait(300);" + steps + "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err,out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page.replace("</body>", harness + "</body>"), encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=5000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "the page never finished rendering")
        return json.loads(html.unescape(match.group(1)))

    def test_the_application_s_words_and_not_the_user_s(self) -> None:
        steps = (
            "out.button=document.getElementById('b').textContent;"
            "out.mine=document.querySelector('.msg-text').textContent;"
            "out.typed=document.getElementById('t').value;"
            "out.placeholder=document.getElementById('t').placeholder;"
            # Drawn later, with a number in it, and a place filled with one
            # of the table's own texts.
            "document.getElementById('p').textContent='every 2 minutes';await wait();"
            "out.every=document.getElementById('p').textContent;"
            "const t0=performance.now();"
            "out.long=window.interfaceLanguage.lookUp('x '.repeat(400)+'and nothing of ours')===undefined;"
            "out.ms=performance.now()-t0;"
            "out.lang=document.documentElement.lang;"
            "out.date=new Date(2026,9,15).toLocaleDateString(undefined,{month:'long'});")
        result = self._render(steps)
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertEqual(out["button"], "Beenden")
        self.assertEqual(out["mine"], "Quit")
        self.assertEqual(out["typed"], "Quit")
        self.assertNotIn("colleague", out["placeholder"])
        self.assertNotIn("minutes", out["every"])
        self.assertIn("2", out["every"])
        self.assertTrue(out["long"])
        self.assertLess(out["ms"], 500)
        self.assertEqual(out["lang"], "de-DE")
        self.assertEqual(out["date"], "Oktober")


if __name__ == "__main__":
    unittest.main()

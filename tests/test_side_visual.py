"""The picture beside the answer (the user, 2026-11-16: the side panel's second column, "model觉得有必要画图的才画").

Asked from the side panel, the full agent may end its answer with one ```view block; the page draws it
(side-visual.js). The quick reply is never told of it; neither is a message from anywhere else.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest

from support import ROOT, TemporaryAgent

from personalized_agent.agent import two_speed as ts

WEB = ROOT / "personalized_agent" / "api" / "web"


class OnlyTheFullAgentOfTheSidePanelIsToldTests(unittest.TestCase):
    def _sent(self, visual: bool):
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        speed = agent.app.two_speed
        quick_fronts, jobs = [], []
        speed._quick = lambda text, earlier, files, front="", seen=None: (
            quick_fronts.append(front) or {"reply": "On it.", "run": True})
        speed._begin = lambda conversation, job: jobs.append(job)
        speed.send("How many hours went on the garden this week?", visual=visual)
        return quick_fronts, jobs

    def test_from_the_side_panel_the_full_agent_may_draw(self) -> None:
        quick, jobs = self._sent(visual=True)
        self.assertIn("```view", jobs[0]["front"])
        self.assertTrue(all("```view" not in front for front in quick), "never the quick reply")

    def test_from_anywhere_else_nothing_is_said_of_pictures(self) -> None:
        _quick, jobs = self._sent(visual=False)
        self.assertNotIn("```view", jobs[0]["front"])

    def test_the_route_says_where_it_came_from(self) -> None:
        import inspect

        from personalized_agent.api import routes
        said = inspect.getsource(routes)
        self.assertIn('side = request.get("panel") == "side"', said)
        self.assertIn("visual=True, quick=quick", said)          # the side panel's own turns
        notes = (WEB / "notes.js").read_text(encoding="utf-8")
        self.assertIn('panel: "side"', notes)

    def test_the_note_names_only_kinds_the_page_draws(self) -> None:
        page = (WEB / "side-visual.js").read_text(encoding="utf-8")
        for kind in ("bar", "line", "table", "timeline", "stat"):
            self.assertIn(f'"{kind}"', ts.VISUAL_NOTE)
            self.assertIn(f'"{kind}"', page)


@unittest.skipUnless(shutil.which("node"), "no node here")
class KeptAtTheEndTests(unittest.TestCase):
    """2026-11-18: "你这个现在chat没有拉到很底下，刚刚那个looking into it都没有显示出来"."""

    def test_what_grows_after_it_was_at_the_end_keeps_it_there(self) -> None:
        notes = (WEB / "notes.js").read_text(encoding="utf-8")
        start = notes.index("function sideToEnd()")
        body = notes[start:notes.index("/* One line said", start)]
        self.assertIn("sideChat.atEnd = true;", body, "taken to the end: kept there")
        self.assertIn("new ResizeObserver(", body, "as what is in it grows")
        self.assertIn("keep.observe(SIDE_LOG);", body)
        self.assertIn("scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 48", body,
                      "scrolled up by the user: left where it is read")


class TheWorldKeepsItsKeysTests(unittest.TestCase):
    """2026-11-18: "还是刚刚的问题，一开始不能移动"."""

    def test_the_panel_coming_in_gives_the_keys_to_the_open_world(self) -> None:
        notes = (WEB / "notes.js").read_text(encoding="utf-8")
        home = notes[notes.index("function sideKeysHome()"):notes.index("function sideKept()")]
        self.assertIn("play.on && play.canvas ? play.canvas", home)
        self.assertNotIn('setTimeout(() => $("#side-input")?.focus', notes, "never the chat's box over the open world")
        self.assertIn("sideKeysHome();\n  }, 30));", notes)
        page = (WEB / "play.js").read_text(encoding="utf-8")
        self.assertIn("playBlocked(play.me.x + dx, play.me.y + dy))) playUnstuck();", page, "never opened boxed in")
        # 2026-11-18: "要把你继续监控着（对debugger模式生效)": its first seconds told to the log, in the Debugger's mode only.
        self.assertIn('api.post("/api/play/note"', page)
        routes = (ROOT / "personalized_agent" / "api" / "routes.py").read_text(encoding="utf-8")
        note = routes[routes.index('@route("POST", "/api/play/note")'):routes.index('@route("POST", "/api/play/showing")')]
        self.assertIn('debugger_available() and bool(app.config.get("debugger_enabled", True))', note)
        # 2026-11-18: "你现在椅子sit之后不能起身了": stood up where it sat down from.
        self.assertIn("play.me.x = was.fromX;", page)
        self.assertIn("if (playBlocked(play.me.x, play.me.y)) playUnstuck();", page)
        # 2026-11-19: "你现在chat的ask about 在切换进play之后还留在屏幕上": put away as the world opens.
        opened = page[page.index("async function playOpen()"):page.index("playFirst.at = performance.now();")]
        self.assertIn('document.querySelectorAll(".side-ask, #fu-pick").forEach((one) => one.remove());', opened)
        # 2026-11-19: "在play view下不能右键弹出copy的panel".
        menu = (WEB / "context-menu.js").read_text(encoding="utf-8")
        self.assertIn('if (event.target.closest && event.target.closest(".side-play")) { event.preventDefault(); close(); return; }', menu)


class ThePageReadsItTests(unittest.TestCase):
    def run_js(self, body: str):
        source = (WEB / "side-visual.js").read_text(encoding="utf-8")
        ran = subprocess.run(["node", "-e", source + "\n" + body], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        return json.loads(ran.stdout)

    def test_the_block_is_taken_out_of_the_words_and_read(self) -> None:
        said = self.run_js(
            "const a = sideViewOf('Six hours.\\n\\n```view\\n{\"kind\": \"stat\", \"value\": \"6 h\"}\\n```');"
            "const b = sideViewOf('Just words.');"
            "const c = sideViewOf('Bad.\\n```view\\n{not json}\\n```');"
            "const d = sideViewOf('Odd.\\n```view\\n{\"kind\": \"pie\"}\\n```');"
            "process.stdout.write(JSON.stringify([a, b, c.view, d.view, c.words.includes('```')]));")
        self.assertEqual(said[0], {"words": "Six hours.", "view": {"kind": "stat", "value": "6 h"}})
        self.assertEqual(said[1], {"words": "Just words.", "view": None})
        self.assertEqual(said[2:], [None, None, True], "unreadable or unknown: no picture, the words whole")

    def test_an_axis_has_round_steps_from_zero_past_the_most(self) -> None:
        said = self.run_js("process.stdout.write(JSON.stringify([sideTicks(0, 14), sideTicks(0, 0.7), "
                           "sideTicks(-3, 9)]));")
        for ticks, most in zip(said, (14, 0.7, 9)):
            self.assertLessEqual(len(ticks), 7)
            self.assertGreaterEqual(ticks[-1], most)
            steps = {round(b - a, 9) for a, b in zip(ticks, ticks[1:])}
            self.assertEqual(len(steps), 1, ticks)
        self.assertLessEqual(said[2][0], -3)

    def test_a_ring_holds_what_its_middle_is_inside_of(self) -> None:
        """2026-11-16: "这个图用户是可以用类似画图的方法edit (比如画圈之类的), 然后model可以把用户edit的内容作为input"."""
        said = self.run_js(
            "const part = (x, y, said) => ({box: {left: x, top: y, width: 0, height: 0}, said});"
            "const parts = [part(100, 200, 'Garden'), part(168, 150, 'Thesis'), part(236, 230, 'Admin')];"
            "const area = {left: 50, top: 100};"                                   # the layer, scrolled
            "const one = sideHeld(parts, [{cx: 50, cy: 100, rx: 30, ry: 30}], area);"
            "const two = sideHeld(parts, [{cx: 50, cy: 100, rx: 30, ry: 30}, {cx: 186, cy: 130, rx: 20, ry: 20},"
            "  {cx: 50, cy: 100, rx: 40, ry: 40}], area);"
            "const none = sideHeld(parts, [{cx: 400, cy: 400, rx: 10, ry: 10}], area);"
            "process.stdout.write(JSON.stringify([one, two, none]));")
        self.assertEqual(said[0], ["Garden"])
        self.assertEqual(said[1], ["Garden", "Admin"], "each once, in the order marked")
        self.assertEqual(said[2], [])

    def test_what_is_marked_goes_with_the_next_message_and_is_shown(self) -> None:
        notes = (WEB / "notes.js").read_text(encoding="utf-8")
        send = notes[notes.index("async function sideSend"):notes.index("/* A full turn told")]
        send = send[send.index("WHAT THE USER MARKED"):]          # the chat's own way, past a reply to one waiting
        self.assertLess(send.index("sideMarksSaid()"), send.index('sideSay("you", text || " ")'),
                        "the marks in the words shown: what the agent is told is what the user sees")
        self.assertIn("sideMarksClear()", send)

    def test_numbers_are_written_as_people_write_them(self) -> None:
        said = self.run_js("process.stdout.write(JSON.stringify([sideNumber(6.5, 'h'), sideNumber(1234.6), "
                           "sideNumber(0.123456), sideNumber('n/a')]));")
        self.assertEqual(said[0], "6.5 h")
        self.assertIn(said[1], ("1,235", "1235"))
        self.assertEqual(said[2], "0.123")
        self.assertEqual(said[3], "n/a")


if __name__ == "__main__":
    unittest.main()

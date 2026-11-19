"""The small screen at the top of the side panel (side-board.js; the user, 2026-11-17: "点开之后会打开一个小屏幕（在sidebar
的上方 ... 覆盖在chat上"; "image界面可以允许模型画图，也允许我做screenshot ... 我可以edit, 然后模型会看到这个"; "本质上只需
要一个按钮"; "告诉chat模型，生成图片的时候调用图片panel显示"). Everything here is made up."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import ROOT, TemporaryAgent

from personalized_agent.agent import two_speed as ts

WEB = ROOT / "personalized_agent" / "api" / "web"


class TheScreenNowTests(unittest.TestCase):

    def _routes(self, pictures):
        from personalized_agent.api.routes import build_routes
        return build_routes(SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: default),
                                            front_pictures=pictures))

    def test_taken_as_a_messages_picture_is_and_the_next_message_takes_its_own(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "shot.jpg"
            path.write_bytes(b"\xff\xd8 a picture")
            pictures = SimpleNamespace(taken=[], used=False, why="")
            pictures.take = lambda words=True: pictures.taken.append(words)
            pictures.latest = lambda wait=0: {"path": str(path), "title": "x"}
            with mock.patch("personalized_agent.common.privacy_folders.why_no_picture", return_value=""):
                said = self._routes(pictures)[("GET", "/api/side/screen_now")](SimpleNamespace(get=lambda *_a: None))
        self.assertEqual(said.body, b"\xff\xd8 a picture")
        self.assertEqual(pictures.taken, [False], "taken, its words not read")
        self.assertTrue(pictures.used, "the next message takes the screen afresh")

    def test_none_in_private_mode(self) -> None:
        from personalized_agent.api.routes import ApiError
        pictures = SimpleNamespace(take=mock.Mock(), latest=mock.Mock(), used=False, why="")
        with mock.patch("personalized_agent.common.privacy_folders.why_no_picture", return_value="private mode is on"):
            with self.assertRaises(ApiError):
                self._routes(pictures)[("GET", "/api/side/screen_now")](SimpleNamespace(get=lambda *_a: None))
        pictures.take.assert_not_called()

    def test_a_message_with_the_small_screens_picture_takes_the_screen_as_well(self) -> None:
        """2026-11-17: "同时把2个图发给chat, 一个是主屏幕的一个是小窗口的，chat自己判断怎么处理"."""
        import inspect
        from personalized_agent.api import routes
        self.assertNotIn("own_picture", inspect.getsource(routes))
        notes = (WEB / "notes.js").read_text(encoding="utf-8")
        self.assertNotIn("own_picture", notes)
        self.assertIn("sideBoardForMessage()", notes)


class APartOfTheScreenCutOutTests(unittest.TestCase):
    """2026-11-17: "能不能我点一个快捷键，我能把某个矩形框的内容拖到image里面" - and "一张图片，我直接拖，而不是下载什么的"."""

    def test_a_drag_marks_a_part_a_press_marks_none(self) -> None:
        from personalized_agent.ui import snip
        self.assertEqual(snip.box_of((40, 30), (10, 90), (100, 100)), (10, 30, 40, 90), "any way it is dragged")
        self.assertEqual(snip.box_of((97, 97), (500, 500), (100, 100)), None, "off the screen, too small within it")
        self.assertEqual(snip.box_of((80, 80), (500, 500), (200, 200)), (80, 80, 200, 200), "kept within the screen")
        self.assertIsNone(snip.box_of((5, 5), (8, 9), (100, 100)), "a press")
        self.assertEqual(snip.command(Path("o.png"))[-3:], ["-m", "personalized_agent.ui.snip", "o.png"])

    def test_its_keys_and_the_packaged_application_run_it(self) -> None:
        import inspect

        from personalized_agent import __main__ as main
        from personalized_agent.platform_layer import hotkeys
        from personalized_agent.ui.desktop import DesktopAgent
        self.assertEqual(hotkeys.BY_KEY["board_snip"]["default"], "Ctrl+Alt+S")
        self.assertIn('"board_snip": self._snip_by_shortcut', inspect.getsource(DesktopAgent._start_shortcuts))
        self.assertIn('args_in[0] == "--snip"', inspect.getsource(main.main))
        said = inspect.getsource(DesktopAgent._snip_into_the_board)
        self.assertIn("why_no_picture(self.config)", said, "nothing of the screen while private")
        self.assertIn('side.tell("board", cut=ident)', said)

    def _routes(self, app):
        from personalized_agent.api.routes import build_routes
        return build_routes(app)

    def test_a_cut_waits_for_the_panel_and_is_taken_once(self) -> None:
        from personalized_agent.api.routes import ApiError
        app = SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: default),
                              board_inbox={"cut_1": b"PNG one"})
        routes = self._routes(app)
        self.assertEqual(routes[("GET", "/api/side/board_inbox")](SimpleNamespace(get=lambda *_a: None)), {"cuts": ["cut_1"]})
        ask = SimpleNamespace(require=lambda key: "cut_1", get=lambda *_a: None)
        self.assertEqual(routes[("GET", "/api/side/board_cut")](ask).body, b"PNG one")
        with self.assertRaises(ApiError):
            routes[("GET", "/api/side/board_cut")](ask)

    def test_a_picture_off_a_page_is_fetched_only_from_the_web(self) -> None:
        from personalized_agent.api.routes import ApiError
        routes = self._routes(SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: default)))
        for url in ("file:///C:/secret.png", "javascript:alert(1)", "data:image/png;base64,AAAA"):
            with self.assertRaises(ApiError):
                routes[("POST", "/api/side/fetch_picture")](SimpleNamespace(require=lambda key, url=url: url,
                                                                            get=lambda *_a: None))


class OneButtonAndTheModelToldTests(unittest.TestCase):

    def test_opened_by_the_user_the_keys_are_in_the_box_not_on_the_search(self) -> None:
        """2026-11-17: "第一次按钮expand, 焦点会在search上(search按钮显示文字)" - activated, the page's first button had
        the keys."""
        import inspect
        from personalized_agent.ui.desktop import DesktopAgent
        self.assertIn("focus=not quietly", inspect.getsource(DesktopAgent.open_notifications))
        notes = (WEB / "notes.js").read_text(encoding="utf-8")
        # The keys to the chat's box - or, the Play view open, to the world (sideKeysHome, 2026-11-18).
        self.assertIn('now.id === "side-search-open") sideKeysHome();', notes)
        home = notes[notes.index("function sideKeysHome()"):notes.index("function sideKept()")]
        self.assertIn('(world || $("#side-input"))?.focus', home)

    def test_one_button_on_the_edge(self) -> None:
        notes = (WEB / "notes.js").read_text(encoding="utf-8")
        self.assertIn('const SIDE_DRAWERS = { picture: "Small screen" };', notes)
        self.assertIn('<script src="/side-board.js"></script>', (WEB / "notes.html").read_text(encoding="utf-8"))

    def test_the_chat_model_draws_in_the_picture_panel_and_hears_the_marks(self) -> None:
        self.assertIn("PICTURE PANEL", ts.VISUAL_NOTE)
        self.assertIn("[On the picture ..., I marked: ...]", ts.VISUAL_NOTE)
        self.assertIn("```view", ts.VISUAL_NOTE)
        self.assertIn('"kind": "marks"', ts.VISUAL_NOTE, "it may draw on a picture already there")
        self.assertIn("NEVER CHANGE A PICTURE IN PLACE", ts.VISUAL_NOTE, "a copy, the user's or its own")
        self.assertIn('"kind": "name"', ts.VISUAL_NOTE, "and names any")
        page = (WEB / "side-visual.js").read_text(encoding="utf-8")
        self.assertIn('SIDE_VIEW_EDITS = new Set(["marks", "name"])', page)


def _chrome():
    from test_panels import _javascript_engine
    kind, engine = _javascript_engine()
    return engine if kind == "chrome" else None


@unittest.skipUnless(_chrome(), "no browser here to draw in")
class DrawnOnAndSentTests(unittest.TestCase):

    def test_a_picture_drawn_on_goes_as_a_png_once_and_marks_on_the_agents_chart_in_words(self) -> None:
        notes = (WEB / "notes.js").read_text(encoding="utf-8").replace("\r\n", "\n")
        escape = notes[notes.index("function escapeHtml"):notes.index("\n}\n", notes.index("function escapeHtml")) + 3]
        page = """<!doctype html><html class="notes-solo"><head><meta charset="utf-8"><style>%s</style></head>
<body class="notes-solo"><section id="notes-page"><header class="notes-head">Your agent</header></section>
<script>
const PAGE_ZONE = "UTC";
const posted = [];
const api = { post: async (path, body) => { posted.push([path, body.name, (body.data || "").length > 100]);
  return { id: "f1", picture: true, name: body.name }; } };
function toast() {}
%s
</script>
<script>%s</script>
<script>%s</script>
<script>
const c = document.createElement('canvas'); c.width = 800; c.height = 450;
const g = c.getContext('2d'); g.fillStyle = '#d9dee8'; g.fillRect(0, 0, 800, 450);
(async () => {
  const out = {};
  // A whiteboard first: nothing to send till drawn on; drawn on, a white picture with the drawing.
  sideBoardOpen();
  out.firstBlank = sideBoard.base.kind;
  out.blankUntouched = (await sideBoardForMessage()).ids.length;
  // As wide as the stage; its drawing in its own pixels.
  const frame = document.querySelector('#side-board .side-board-frame');
  const stage = document.querySelector('#side-board .side-board-stage');
  out.fills = frame.clientWidth >= stage.clientWidth - 24;
  sideBoard.shapes.push({ kind: 'pen', colour: '#3e8ef7', points: [[10, 30], [120, 30]] });
  sideBoard.dirty = true;
  const white = sideBoardPicture();
  const at = (x, y) => Array.from(white.getContext('2d').getImageData(x, y, 1, 1).data).slice(0, 3);
  out.white = { twice: white.width === frame.clientWidth * 2 && white.height === frame.clientHeight * 2,
                paper: at(white.width - 10, white.height - 10), line: at(120, 60) };
  out.whiteSent = await sideBoardForMessage();
  posted.length = 0;
  // PAINT'S TOOLS: the eraser takes a stroke it passes over, Ctrl+Z gives it back; Clear, undone too; a whiteboard
  // turned right about its middle.
  const strokes = sideBoard.shapes.length;
  sideBoardRemember();
  const took = sideBoardErase([60, 30]);
  const afterErase = sideBoard.shapes.length;
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true }));
  const afterUndo = sideBoard.shapes.length;
  sideBoardClear();
  const afterClear = sideBoard.shapes.length;
  sideBoardUndo();
  const frameNow = sideBoardSizeNow();
  sideBoard.shapes.push({ kind: 'ellipse', colour: '#000', size: 8, points: [[frameNow[0] / 2, frameNow[1] / 2 - 10], [frameNow[0] / 2 + 20, frameNow[1] / 2]] });
  await sideBoardRotate();
  const turned = sideBoard.shapes[sideBoard.shapes.length - 1].points[0];
  out.paint = { strokes, took, afterErase, afterUndo, afterClear, back: sideBoard.shapes.length,
                turned: [Math.round(turned[0] - frameNow[0] / 2), Math.round(turned[1] - frameNow[1] / 2)],
                bar: [...document.querySelectorAll('#side-board .side-board-tools > button')].map((b) => b.dataset.board || b.dataset.boardTool) };
  sideBoardBase({ kind: 'image', url: c.toDataURL('image/png'), screen: true });
  for (let i = 0; i < 50 && !document.querySelector('#side-board .side-board-base img').naturalWidth; i++) {
    await new Promise((r) => setTimeout(r, 100));
  }
  sideBoard.shapes.push({ kind: 'box', colour: '#e5484d', points: [[0.2, 0.2], [0.6, 0.7]] });
  sideBoard.dirty = true;
  const cc = sideBoardPicture();
  const cx = cc.getContext('2d');
  out.size = [cc.width, cc.height];
  out.edge = Array.from(cx.getImageData(Math.round(800 * 0.2), Math.round(450 * 0.45), 1, 1).data).slice(0, 3);
  const sent = await sideBoardForMessage();
  out.sent = { ids: sent.ids, words: sent.words, posted: [...posted] };
  out.again = (await sideBoardForMessage()).ids.length;
  sideBoardShowView({ kind: 'bar', title: 'Hours', x: ['Mon', 'Tue', 'Wed'], series: [{ name: 'work', values: [3, 8, 5] }] });
  const card = document.querySelector('#side-board .side-viz-card');
  const area = document.querySelector('#side-board .side-board-frame').getBoundingClientRect();
  const mid = sideParts(card, sideBoard.base.view)[1].box;
  const fx = (mid.left - area.left) / area.width, fy = (mid.top - area.top) / area.height;
  sideBoard.shapes.push({ kind: 'box', colour: '#e5484d', points: [[fx - 0.04, fy - 0.06], [fx + 0.04, fy + 0.06]] });
  sideBoard.dirty = true;
  out.chart = await sideBoardForMessage();
  const pagesBefore = sideBoard.pages.length;
  sideBoardShowView({ kind: 'bar', title: 'Hours', x: ['Mon', 'Tue', 'Wed'], series: [{ name: 'work', values: [3, 8, 5] }] });
  out.pages = { before: pagesBefore, again: sideBoard.pages.length, strip: document.querySelectorAll('#side-board .side-board-page').length,
                agent: document.querySelectorAll('#side-board .side-board-page-agent').length };
  // THE AGENT'S, NEVER IN PLACE: its picture drawn again a new page, the old kept; marks on a copy of the user's,
  // first and shown; names for any.
  const board1 = sideBoard.pages.find((p) => p.base.kind === 'blank' && p.shapes.length);
  sideBoardShowView({ kind: 'stat', name: 'total', title: 'Total', value: 10, label: 'hours' });
  const first = sideBoard.page;
  sideBoardShowView({ kind: 'stat', name: 'total', title: 'Total', value: 12, label: 'hours' });
  const redrawn = { kept: sideBoard.pages.includes(first) && first.base.view.value === 10,
                    newFirst: sideBoard.pages[0] === sideBoard.page && sideBoard.page !== first, name: sideBoard.page.name };
  const mineBefore = JSON.stringify(board1.shapes);
  const count = sideBoard.pages.length;
  const marks = { kind: 'marks', page: board1.id, name: 'Where it fails', marks: [{ shape: 'box', at: [0.1, 0.1, 0.5, 0.5] },
    { shape: 'text', at: [0.2, 0.7], text: 'here' }, { shape: 'arrow', from: [0, 0], to: [2, 2] }] };
  sideBoardShowView(marks);
  const copy = sideBoard.page;
  sideBoardShowView(marks);                      // its mark in the chat pressed again: the same copy
  const added = copy.shapes.filter((s) => s.agent);
  sideBoardShowView({ kind: 'name', page: board1.id, name: 'Login sketch' });
  out.agent = { redrawn, untouched: JSON.stringify(board1.shapes) === mineBefore, copy: copy !== board1,
                once: sideBoard.pages.length === count + 1 && sideBoard.page === copy, first: sideBoard.pages[0] === copy,
                kept: copy.shapes.length === board1.shapes.length + 3, added: added.length, name: copy.name,
                ownColour: added.every((s) => s.colour === SIDE_BOARD_AGENT_COLOUR), inPixels: added[0].points[1][0] > 1,
                named: board1.name, byName: sideBoardFind('login sketch') === board1,
                said: sideBoardWhatItIs(copy), saidMine: sideBoardWhatItIs(board1) };
  // A picture file dragged onto it: in it, to draw on - not the box's attachment as well.
  let boxGotIt = 0;
  document.addEventListener('drop', () => { boxGotIt += 1; });
  const file = await new Promise((r) => c.toBlob((b) => r(new File([b], 'cat.png', { type: 'image/png' })), 'image/png'));
  const carried = new DataTransfer();
  carried.items.add(file);
  document.getElementById('side-board').dispatchEvent(new DragEvent('drop', { dataTransfer: carried, bubbles: true, cancelable: true }));
  out.dropped = { kind: sideBoard.base.kind, dropped: Boolean(sideBoard.base.dropped), own: Boolean(sideBoard.base.own),
                  blob: String(sideBoard.base.url).startsWith('blob:'), boxGotIt };
  document.title = JSON.stringify(out);
})().catch((err) => { document.title = JSON.stringify({ error: String(err && err.stack || err) }); });
</script></body></html>""" % ((WEB / "styles.css").read_text(encoding="utf-8"), escape,
                              (WEB / "side-visual.js").read_text(encoding="utf-8"),
                              (WEB / "side-board.js").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "board.html"
            path.write_text(page, encoding="utf-8")
            done = subprocess.run([_chrome(), "--headless=new", "--disable-extensions", "--disable-gpu", "--no-first-run",
                                   f"--user-data-dir={work}/profile", "--virtual-time-budget=6000",
                                   "--window-size=440,900", "--dump-dom", path.as_uri()],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        found = re.search(r"<title>(.*?)</title>", done.stdout, re.S)
        self.assertIsNotNone(found, (done.stdout[-600:], done.stderr[-1500:]))
        said = json.loads(found.group(1).replace("&quot;", '"').replace("&amp;", "&"))
        self.assertNotIn("error", said, said.get("error"))
        paint = said["paint"]
        self.assertTrue(paint["took"])
        self.assertEqual(paint["afterErase"], paint["strokes"] - 1, "the stroke the eraser passed over, taken")
        self.assertEqual(paint["afterUndo"], paint["strokes"], "Ctrl+Z gives it back")
        self.assertEqual(paint["afterClear"], 0)
        self.assertEqual(paint["back"], paint["strokes"] + 1, "Clear undone; and the ellipse added")
        self.assertEqual(paint["turned"], [10, 0], "a quarter turn right about the middle")
        self.assertEqual(paint["bar"], ["pages", "new", "snip", "clear", "shape", "pen", "eraser", "text", "rotate", "colour"],
                         "as Paint's: the strip, New, Clear; Shapes, the pen, the eraser, words, Rotate; the colour")
        self.assertEqual(said["firstBlank"], "blank", "a whiteboard by default")
        self.assertEqual(said["blankUntouched"], 0, "nothing drawn: nothing sent")
        self.assertTrue(said["fills"], "the whiteboard as wide as the stage")
        self.assertTrue(said["white"]["twice"], "sent at twice its size on the screen")
        self.assertEqual(said["white"]["paper"], [255, 255, 255])
        self.assertLess(said["white"]["line"][0], 120, "the line where it was drawn, in the board's own pixels")
        self.assertEqual(said["whiteSent"]["ids"], ["f1"])
        self.assertRegex(said["whiteSent"]["words"], r"from the small screen \(small screen picture p\w+\): a whiteboard, with what I drew on it")
        self.assertEqual(said["size"], [800, 450], "as large as the picture itself")
        self.assertGreater(said["edge"][0], 180, "the box drawn into it")
        self.assertLess(said["edge"][1], 140)
        self.assertEqual(said["sent"]["ids"], ["f1"])
        self.assertEqual(said["sent"]["posted"], [["/api/chat/upload", "screen-drawn-on.png", True]])
        self.assertIn("): a screenshot of my screen I took at", said["sent"]["words"])
        self.assertIn("with what I drew on it", said["sent"]["words"])
        self.assertIn("my screen as it is now", said["sent"]["words"], "told apart from the screen now, sent as well")
        self.assertEqual(said["again"], 1, "open, it goes with every message - beside the screen now")
        self.assertEqual(said["chart"]["ids"], [], "the agent's drawing: words, it knows the drawing")
        self.assertRegex(said["chart"]["words"], r'^\[On the picture "Hours" \(small screen picture p\w+\), I marked: Tue \(work 8\)\]$')
        self.assertEqual(said["pages"]["before"], said["pages"]["again"], "the agent's drawing shown again: its own page")
        self.assertEqual(said["pages"]["strip"], said["pages"]["again"], "each page in the strip")
        self.assertEqual(said["pages"]["agent"], 1, "the agent's marked as its")
        agent = said["agent"]
        self.assertEqual(agent["redrawn"], {"kept": True, "newFirst": True, "name": "total"},
                         "its picture drawn again: a new one, first - the earlier kept")
        self.assertTrue(agent["untouched"], "the user's picture never changed")
        self.assertTrue(agent["copy"] and agent["once"] and agent["first"], "a copy, first, made once")
        self.assertTrue(agent["kept"], "the copy has what the user drew and the agent's marks")
        self.assertEqual((agent["added"], agent["name"]), (3, "Where it fails"))
        self.assertTrue(agent["ownColour"] and agent["inPixels"])
        self.assertEqual(agent["named"], "Login sketch", "a name for the user's picture - the picture untouched")
        self.assertTrue(agent["byName"], "found by its name too")
        self.assertIn("the marks you drew on it", agent["said"])
        self.assertIn('named "Login sketch"', agent["saidMine"])
        self.assertEqual(said["dropped"], {"kind": "image", "dropped": True, "own": True, "blob": True, "boxGotIt": 0})


if __name__ == "__main__":
    unittest.main()


class AScreenshotButtonTests(unittest.TestCase):
    """2026-11-17: "我的image panel 要加个截屏按钮"."""

    def test_the_bar_has_it_and_it_runs_the_shortcuts_cut_out(self) -> None:
        import inspect
        source = (WEB / "side-board.js").read_text(encoding="utf-8")
        self.assertIn("""button("snip", "Screenshot: the whole screen, or a part of it", ' aria-haspopup="true"')""", source)
        self.assertIn('api.post("/api/side/board_snip"', source)
        # 2026-11-18: "screen shot有两个选项，一个是截取整个屏幕，一个是截取一个部分的".
        self.assertIn('data-board-snip="whole"', source)
        self.assertIn('data-board-snip="part"', source)
        self.assertIn('if (cut.dataset.boardSnip === "whole") sideBoardScreen();', source)
        # 2026-11-18: "你的new就是单独创建一个新的" - a blank page at once, no menu; the screen is the screenshot's.
        self.assertIn('button("new", "New page")', source)
        self.assertNotIn('data-board-new="screen"', source)
        from personalized_agent.ui.desktop import DesktopAgent
        self.assertIn("self.app.snip_into_board = lambda: DesktopAgent._snip_by_shortcut(self)",
                      inspect.getsource(DesktopAgent))

    def test_without_the_desktop_it_says_so(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        from personalized_agent.api.routes import build_routes
        routes = build_routes(agent.app)
        self.assertIn("error", routes[("POST", "/api/side/board_snip")]({}))
        asked = []
        agent.app.snip_into_board = lambda: asked.append(1)
        self.assertEqual(routes[("POST", "/api/side/board_snip")]({}), {"started": True})
        self.assertEqual(asked, [1])


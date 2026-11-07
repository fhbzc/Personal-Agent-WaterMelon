"""A link in a reply is a link: `[words](address)`, as a model writes one, was shown as written in
the chat box and the chat page, and could not be pressed."""

from __future__ import annotations

import gc
import html
import json
import re
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest import mock

from support import ROOT
from test_chat_debugger_ui import _browser, _run_browser_probe

from personalized_agent.ui import chat_bubble as cb
from personalized_agent.ui.avatar_buttons import QuickPanel
from personalized_agent.ui.chat_bubble import links_in
from personalized_agent.ui.desktop import BUBBLE_LOOK, DesktopAgent

WEB = ROOT / "personalized_agent" / "api" / "web"


class ReadingThemTests(unittest.TestCase):

    def test_as_a_model_writes_one(self) -> None:
        self.assertEqual(links_in("See [the filing](https://example.org/a.pdf) today."),
                         [("See ", ""), ("the filing", "https://example.org/a.pdf"), (" today.", "")])

    def test_an_address_alone_without_what_ends_the_sentence(self) -> None:
        self.assertEqual(links_in("At https://example.org/a. Next"),
                         [("At ", ""), ("https://example.org/a", "https://example.org/a"),
                          (".", ""), (" Next", "")])
        self.assertEqual(links_in("(https://example.org/a)"),
                         [("(", ""), ("https://example.org/a", "https://example.org/a"), (")", "")])

    def test_chinese_after_an_address_is_not_part_of_it(self) -> None:
        self.assertEqual(links_in("见 https://example.org/a。然后"),
                         [("见 ", ""), ("https://example.org/a", "https://example.org/a"), ("。然后", "")])

    def test_brackets_in_an_address_stay_in_it(self) -> None:
        self.assertEqual(links_in("[w](https://example.org/a_(b))"),
                         [("w", "https://example.org/a_(b)")])

    def test_only_web_pages(self) -> None:
        for text in ("[run](javascript:alert(1))", "[a file](file:///c:/a.txt)", "plain [words] (here)"):
            self.assertEqual(links_in(text), [(text, "")])

    def test_an_address_without_its_beginning(self) -> None:
        text = "Join on Google Meet: meet.google.com/abc-defg-hij."
        self.assertEqual(links_in(text), [
            ("Join on Google Meet: ", ""),
            ("meet.google.com/abc-defg-hij", "https://meet.google.com/abc-defg-hij"), (".", "")])
        self.assertEqual(links_in("加入 zoom.us/j/123。然后"),
                         [("加入 ", ""), ("zoom.us/j/123", "https://zoom.us/j/123"), ("。然后", "")])

    def test_what_only_looks_like_one_is_not(self) -> None:
        for text in ("docs/design-doc.md", "see notes.txt/old", "tests/support.py", "version 1.2/3",
                     "ada@example.org/a", "/srv/example.org/a", "example.org", "a.b"):
            self.assertEqual(links_in(text), [(text, "")], text)

    def test_no_words_lost(self) -> None:
        text = "a [b](https://x.org) c https://y.org, d"
        said = "".join(words for words, _address in links_in(text))
        self.assertEqual(said, "a b c https://y.org, d")


class InTheChatBoxTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.bubble = cb.ChatBubble(self.root, name="Fern", family="Segoe UI", size=10,
                                    send=lambda text, c: {"answer": ""}, post=lambda fn: fn(),
                                    open_full=lambda _c: None, words=lambda text: text)
        self.bubble.show()
        self.root.update()

    def tearDown(self) -> None:
        self.bubble.close()
        self.root.destroy()
        del self.bubble, self.root
        gc.collect()

    def _pressed(self) -> list:
        opened = []
        with mock.patch.object(cb.threading, "Thread", lambda target, args, **_more: SimpleNamespace(
                start=lambda: opened.append(args[0]))):
            self.bubble._open_link("https://example.org/a")
        return opened

    def test_its_words_show_and_it_opens(self) -> None:
        self.bubble._write_reply("See [the filing](https://example.org/a) today", "")
        said = self.bubble.said.get("1.0", "end-1c")
        self.assertIn("See the filing today", said)
        self.assertNotIn("https://", said)
        self.assertEqual(self._pressed(), ["https://example.org/a"])

    def test_the_end_of_selecting_words_opens_nothing(self) -> None:
        self.bubble._write_reply("See [the filing](https://example.org/a)", "")
        self.bubble.said.tag_add("sel", "1.0", "1.3")
        self.assertEqual(self._pressed(), [])

    def test_in_a_fold_its_title_stays_above_and_it_stays_folded(self) -> None:
        fold = {"turn": "t1"}
        self.bubble._fold_begin(fold)
        self.bubble._fold_done(fold, {"answer": "More at [this](https://example.org/a) page"})
        said = self.bubble.said
        self.assertLess(said.index(f"{fold['head']}.first"), said.index(f"{fold['body']}.first"))
        link = next(one for one in said.tag_names() if one.startswith("link"))
        self.assertIn(fold["body"], said.tag_names(f"{link}.first"), "folded with what it is in")


class InANotificationTests(unittest.TestCase):
    """The user, 2026-11-06: a call's address in a notification, to be pressed."""

    SAID = "Your meeting runs at 3. Join on Google Meet: meet.google.com/abc-defg-hij"
    ADDRESS = "https://meet.google.com/abc-defg-hij"

    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self._gone)
        self.bound = {}
        keep = mock.patch.object(tk.Text, "tag_bind", lambda _box, tag, sequence, press:
                                 self.bound.__setitem__((tag, sequence), press))
        keep.start()
        self.addCleanup(keep.stop)

    def _gone(self) -> None:
        self.root.destroy()
        del self.root
        gc.collect()

    def _pressed(self, box: tk.Text) -> list:
        link = next(one for one in box.tag_names() if one.startswith("link"))
        self.assertEqual(box.get(f"{link}.first", f"{link}.last"), "meet.google.com/abc-defg-hij")
        opened = []
        with mock.patch.object(cb.threading, "Thread", lambda target, args, **_more: SimpleNamespace(
                start=lambda: opened.append(args[0]))):
            self.bound[(link, "<ButtonRelease-1>")](None)
        return opened

    def test_in_its_bubble(self) -> None:
        look = dict(BUBBLE_LOOK[False])
        desk = SimpleNamespace(_words=lambda text: text)
        box = DesktopAgent._copyable(desk, self.root, self.SAID, ("Segoe UI", 10), "#ffffff", look, 300)
        self.assertEqual(box.get("1.0", "end-1c"), self.SAID)
        self.assertEqual(box.cget("state"), "disabled")
        self.assertEqual(self._pressed(box), [self.ADDRESS])

    def test_in_the_waiting_list(self) -> None:
        panel = QuickPanel(self.root, "Waiting", lambda: [], lambda fn: fn(), lambda text: text,
                           ("Segoe UI", 10), "Nothing is waiting.")
        self.addCleanup(panel.close)
        panel._build()
        panel._got([{"name": "", "rows": [{"id": "n", "title": "Your meeting", "detail": self.SAID,
                                           "actions": []}]}])
        self.assertIn(self.SAID, panel.list.get("1.0", "end"))
        self.assertEqual(self._pressed(panel.list), [self.ADDRESS])


class InTheChatPageTests(unittest.TestCase):

    def test_a_link_and_nothing_written_as_markup(self) -> None:
        engine = _browser()
        if not engine:
            self.skipTest("a browser is needed to draw it")
        source = (WEB / "chat.js").read_text(encoding="utf-8")
        start = source.index("const CHAT_LINK")
        written = source[start:source.index("function appendMessage(", start)]
        text = ("See [the <b>filing</b>](https://example.org/a) and [run](javascript:alert(1)) at "
                "https://b.org. Or meet.google.com/abc-defg-hij.")
        script = (written + "window.addEventListener('load',()=>{const box=document.createElement('div');"
                  "writeWithLinks(box," + json.dumps(text) + ");"
                  "const links=[...box.querySelectorAll('a')];"
                  "const pre=document.createElement('pre');pre.id='out';"
                  "pre.textContent=JSON.stringify({text:box.textContent,bold:box.querySelectorAll('b').length,"
                  "links:links.map(a=>[a.textContent,a.getAttribute('href'),a.target,a.rel])});"
                  "document.body.prepend(pre);});")
        page = "<!doctype html><html><head><meta charset='utf-8'></head><body><script>" + script + "</script></body></html>"
        stdout, stderr = _run_browser_probe(engine, page)
        found = re.search(r'<pre id="out">(.*?)</pre>', stdout, re.S)
        self.assertIsNotNone(found, stderr[-2000:])
        out = json.loads(html.unescape(found.group(1)))
        self.assertEqual(out["bold"], 0, "words are never markup")
        self.assertEqual(out["links"][0], ["the <b>filing</b>", "https://example.org/a", "_blank",
                                           "noopener noreferrer"])
        self.assertEqual(out["links"][1][1], "https://b.org")
        self.assertEqual(out["links"][2][:2], ["meet.google.com/abc-defg-hij",
                                               "https://meet.google.com/abc-defg-hij"])
        self.assertEqual(len(out["links"]), 3, "a web page's or none")
        self.assertIn("[run](javascript:alert(1))", out["text"])


class WhenAndWhatTests(unittest.TestCase):
    """The user, 2026-11-05: what a stroke says under the pointer, written out on the project."""

    def test_each_task_its_time_what_it_was_and_how_long(self) -> None:
        engine = _browser()
        if not engine:
            self.skipTest("a browser is needed to draw it")
        source = (WEB / "app.js").read_text(encoding="utf-8")

        def function(name: str) -> str:
            start = source.index(f"function {name}(")
            return source[start:source.index("\n}\n", start) + 3]
        tasks = [{"id": "t1", "title": "Plotted the <curve>", "minutes": 90,
                  "starts_at": "2026-11-04T14:10:00", "ends_at": "2026-11-04T15:40:00"},
                 {"id": "t2", "title": "Sent the tables", "minutes": 120,
                  "starts_at": "2026-11-03T23:00:00", "ends_at": "2026-11-04T01:00:00"}]
        script = ("const escapeHtml=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');"
                  "const pjUserSay=()=>'';" + function("pjHours") + function("pjWhen")
                  + function("pjStretch") + function("pjTaskList")
                  + "window.addEventListener('load',()=>{const box=document.createElement('div');"
                  "box.innerHTML=pjTaskList({id:'p',tasks:" + json.dumps(tasks) + "});"
                  "const pre=document.createElement('pre');pre.id='out';"
                  "pre.textContent=JSON.stringify({head:box.querySelector('.event-label').textContent,"
                  "fold:box.querySelectorAll('details[open][data-sb-open=\"pj-tasks-p\"]').length,"
                  "days:[...box.querySelectorAll('.pj-day')].map(li=>li.textContent),"
                  "rows:[...box.querySelectorAll('.pj-stretch')].map(li=>["
                  "li.querySelector('.pj-when').textContent,li.querySelector('.pj-what').textContent,"
                  "li.querySelector('.muted').textContent])});document.body.prepend(pre);});")
        page = ("<!doctype html><html><head><meta charset='utf-8'></head><body><script>" + script
                + "</script></body></html>")
        stdout, stderr = _run_browser_probe(engine, page)
        found = re.search(r'<pre id="out">(.*?)</pre>', stdout, re.S)
        self.assertIsNotNone(found, stderr[-2000:])
        out = json.loads(html.unescape(found.group(1)))
        self.assertEqual(out["head"], "What was done when")
        self.assertEqual(out["fold"], 1, "open at first, and it can be shut")
        self.assertEqual(out["days"], ["Nov 4", "Nov 3"], "each day once, newest first")
        first, second = out["rows"]
        self.assertEqual(first[1], "Plotted the <curve>", "the newest first")
        self.assertNotIn("Nov", first[0], "its day is on the line above it")
        self.assertEqual(second[0].count("Nov"), 1, "into another day: that day said")
        self.assertTrue(first[2] and second[2], "how long")


class StillOpenTests(unittest.TestCase):
    """The user, 2026-11-05: like with like, and a way to where each one is."""

    def _in_a_page(self, ending: str) -> dict:
        engine = _browser()
        if not engine:
            self.skipTest("a browser is needed to draw it")
        source = (WEB / "app.js").read_text(encoding="utf-8")

        def function(name: str, word: str = "function") -> str:
            start = source.index(f"{word} {name}(")
            return source[start:source.index("\n}\n", start) + 3]
        items = [{"kind": "notification", "id": "n1", "text": "Fictional reminder"},
                 {"kind": "todo", "id": "t1", "text": "Repot the <fern>", "detail": "the big pot"},
                 {"kind": "todo", "id": "t2", "text": "Buy tea"},
                 {"kind": "assigned", "id": "a1", "text": "A summary"}]
        script = ("const escapeHtml=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;');"
                  "const PJ_OPEN_KINDS={notification:'Notification',todo:'To do',assigned:'Work assigned'};"
                  "const opened=[],read=[],told=[];const toast=w=>told.push(w);"
                  "const openTask=id=>opened.push(id);const openPopover=(_a,words)=>read.push(words);"
                  "const startOfDay=d=>d;const calendarState={tasks:[{id:'k1'}]};"
                  "const loadCalendar=async()=>{};"
                  "const api={get:async()=>({notifications:["
                  "{id:'n1',title:'From the calendar',created_at:'2026-11-04T10:00:00',"
                  "metadata:JSON.stringify({task_id:'k1'})},"
                  "{id:'n2',title:'Only <words>',message:'To read',metadata:{}},"
                  "{id:'n3',title:'Its entry has gone',message:'Still to read',"
                  "created_at:'2026-11-04T10:00:00',metadata:{task_id:'away'}}]})};"
                  + function("pjOpenItems") + function("pjGoToNotification", "async function")
                  + function("pjGoTo", "async function")
                  + "window.addEventListener('load',async()=>{const box=document.getElementById('box');"
                  "box.innerHTML=pjOpenItems({open_items:" + json.dumps(items) + "});" + ending
                  + "const pre=document.createElement('pre');pre.id='out';"
                  "pre.textContent=JSON.stringify(out);document.body.prepend(pre);});")
        page = ("<!doctype html><html><head><meta charset='utf-8'></head><body><div id='ov-tasks-card'>"
                "<aside id='ov-todo'><p data-todo='t1'>x</p></aside><aside id='ov-assigned'></aside></div>"
                "<div id='box'></div><script>" + script + "</script></body></html>")
        stdout, stderr = _run_browser_probe(engine, page)
        found = re.search(r'<pre id="out">(.*?)</pre>', stdout, re.S)
        self.assertIsNotNone(found, stderr[-2000:])
        return json.loads(html.unescape(found.group(1)))

    def test_like_with_like_each_with_its_way_there(self) -> None:
        out = self._in_a_page(
            "const out={kinds:[...box.querySelectorAll('.pj-open-kind')].map(one=>"
            "one.textContent.replace(/\\s+/g,' ').trim()),"
            "goes:[...box.querySelectorAll('[data-pj-go]')].map(b=>[b.dataset.pjGoKind,b.dataset.pjGo,"
            "b.textContent]),text:box.querySelector('.pj-open-text').textContent};")
        self.assertEqual(out["kinds"], ["To do 2", "Work assigned 1", "Notification 1"])
        self.assertEqual([one[:2] for one in out["goes"]],
                         [["todo", "t1"], ["todo", "t2"], ["assigned", "a1"], ["notification", "n1"]])
        self.assertTrue(all(one[2] == "Go to" for one in out["goes"]))
        self.assertIn("Repot the <fern>", out["text"])

    def test_a_notification_is_read_never_answered(self) -> None:
        """The card with its buttons could only be put off or closed: the user wanted to read it."""
        out = self._in_a_page(
            "await pjGoTo('notification','n1');await pjGoTo('notification','n2');"
            "await pjGoTo('notification','n3');await pjGoTo('notification','gone');"
            "const out={opened,read,told};")
        self.assertEqual(out["opened"], ["k1"], "its entry on the calendar")
        self.assertEqual(len(out["read"]), 2, "no entry, or none left: its words")
        self.assertIn("Only &lt;words>", out["read"][0])
        self.assertIn("Still to read", out["read"][1])
        self.assertEqual(out["told"], ["This notification is no longer waiting"])

    def test_it_goes_to_its_line_or_its_list(self) -> None:
        out = self._in_a_page(
            "await pjGoTo('todo','t1');await pjGoTo('todo','t2');await pjGoTo('assigned','a1');"
            "const marked=s=>document.querySelector(s).classList.contains('xp-ref-flash');"
            "const out={line:marked('[data-todo=t1]'),list:marked('#ov-todo'),"
            "assigned:marked('#ov-assigned')};")
        self.assertTrue(out["line"], "its line")
        self.assertTrue(out["list"], "not on the page: its list")
        self.assertTrue(out["assigned"])


class TheLookTests(unittest.TestCase):

    def test_the_list_folds_away_and_says_so(self) -> None:
        engine = _browser()
        if not engine:
            self.skipTest("a browser is needed for it")
        source = (WEB / "app.js").read_text(encoding="utf-8")
        start = source.index("/* THE PANELS' LIST FOLDS AWAY")
        script = (source[start:source.index("function settingsPendingIn(", start)]
                  + "window.addEventListener('load',()=>{});const b=document.getElementById('side-toggle');"
                  "const look=()=>[document.documentElement.classList.contains('side-closed'),"
                  "b.getAttribute('aria-label'),b.getAttribute('aria-expanded')];"
                  "const out=[look()];b.click();out.push(look());b.click();out.push(look());"
                  "const pre=document.createElement('pre');pre.id='out';"
                  "pre.textContent=JSON.stringify(out);document.body.prepend(pre);")
        page = ("<!doctype html><html><head><meta charset='utf-8'></head><body>"
                "<button id='side-toggle'></button><script>" + script + "</script></body></html>")
        stdout, stderr = _run_browser_probe(engine, page)
        found = re.search(r'<pre id="out">(.*?)</pre>', stdout, re.S)
        self.assertIsNotNone(found, stderr[-2000:])
        self.assertEqual(json.loads(html.unescape(found.group(1))),
                         [[False, "Close the sidebar", "true"], [True, "Open the sidebar", "false"],
                          [False, "Close the sidebar", "true"]])

    def test_the_main_list_alone_is_styled_as_the_main_list(self) -> None:
        """`nav button` in look.css took the chat's list too: its ⋯ as wide as the row, and no
        conversation's name left to read. The Newspaper's and Society's are `nav`s as well."""
        look = (WEB / "look.css").read_text(encoding="utf-8")
        words = re.sub(r"/\*.*?\*/", "", look, flags=re.S)
        self.assertIsNone(re.search(r"(^|[\s,>])nav[\s.,:{]", words, re.M), "a bare `nav` selector")


if __name__ == "__main__":
    unittest.main()

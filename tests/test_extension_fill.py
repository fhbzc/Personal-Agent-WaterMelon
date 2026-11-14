"""A PAGE'S BOXES FILLED BY THE EXTENSION - NOTHING PRESSED, BY CODE (the user, 2026-11-08: "提交、发送、
删除这类按钮，是始终都由你自己点 ... 这个要由代码保证"). The filling script is held word by word to what
it may do; an order is the agent's alone, taken once; and on a real page it fills the boxes, never a
password or the user's own words, and the page's own submit is stopped. Everything here is made up."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from personalized_agent.platform_layer import browser_extension as extension
from personalized_agent.platform_layer import native_host

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "resources" / "browser_extension"
#: The extension's sources are in the private repository alone: the public one runs what needs none.
HAS_SOURCE = (WEB / "fill.js").is_file()
FILL = (WEB / "fill.js").read_text("utf-8") if HAS_SOURCE else ""
SOURCES = "the extension's sources are in the private repository alone"

#: What the filling script may never hold: a press, a submit, a key or a pointer, a page left, a word sent.
FORBIDDEN = [r"\.click\s*\(", r"\.submit\s*\(", r"requestSubmit", r"\bKeyboardEvent\b",
             r"\bPointerEvent\b", r"\bTouchEvent\b", r"\bInputEvent\b", r"\bfetch\s*\(", r"XMLHttpRequest",
             r"sendBeacon", r"\blocation\s*=", r"location\.(assign|replace|reload)", r"location\.href\s*=",
             r"history\.(pushState|replaceState|back|go)", r"\.focus\s*\(", r"window\.open\s*\(",
             r"execCommand", r"\.form\b", r"\bnew\s+(Custom)?Event\s*\(\s*['\"](?!input['\"]|change['\"])"]


@unittest.skipUnless(HAS_SOURCE, SOURCES)
class WhatItMayDoTests(unittest.TestCase):

    def test_nothing_that_presses_submits_goes_or_sends(self) -> None:
        code = re.sub(r"/\*.*?\*/", "", FILL, flags=re.S)          # its comments say what it never does
        code = re.sub(r"//[^\n]*", "", code)
        for pattern in FORBIDDEN:
            self.assertIsNone(re.search(pattern, code), f"fill.js holds {pattern!r}")
        # A PRESS IN ONE PLACE ONLY (the user, 2026-11-13: "可以动单选和网页自制的下拉框 ... 只选选项，绝不按按钮"):
        # `choose`, which looks first that it is an option or a radio, inside no button or link.
        start = code.index("function choose(el)")
        chooser = code[start:code.index("\n  }\n", start)]
        self.assertEqual(code.count("MouseEvent"), chooser.count("MouseEvent"), "a press only in choose")
        self.assertIn('role !== "option"', chooser)
        self.assertIn('el.closest("a, button, [type=submit], [role=button], [role=link]")', chooser)
        self.assertLess(chooser.index("return false"), chooser.index("MouseEvent"), "looked at first")
        events = re.findall(r"new\s+Event\s*\(\s*['\"](\w+)['\"]", code)
        self.assertEqual(set(events), {"input", "change"}, "only that its words changed")
        # ITS FILES NEVER PUT INTO THE PAGE (the user, 2026-11-13: "所有附件不上传").
        self.assertNotIn("DataTransfer", code)
        self.assertNotIn(".files", code)
        self.assertIn('addEventListener("submit"', code, "the page's own submit stopped a while")

    def test_only_the_agents_order_starts_it(self) -> None:
        background = (WEB / "background.js").read_text("utf-8")
        at = background.index('message.type === "fill-ask"')
        path = background[at:at + 600]
        self.assertIn('onLine({ type: "fill-ask", url: message.url })', path)
        self.assertNotIn("executeScript", path)
        external = background[background.index("onMessageExternal.addListener"):]
        self.assertNotIn("fill", external[:900], "no page, not even the agent's own, orders a fill")
        built = extension.manifest("chromium")
        self.assertIn("fill.js", built["content_scripts"][0]["js"])
        self.assertEqual(built["incognito"], "not_allowed")
        self.assertIn("fill.js", extension.FILES)


class TheOrderTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="fill-orders-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_taken_once_by_its_own_site_and_let_go_of_when_old(self) -> None:
        parts = [{"field": "Your name", "text": "Ada"}]
        self.assertTrue(extension.leave_fill_order(self.dir, "https://forms.example.org/apply", parts))
        self.assertIsNone(extension.take_fill_order(self.dir, "https://elsewhere.example.com/x"))
        said = extension.take_fill_order(self.dir, "https://forms.example.org/apply?step=2")
        self.assertEqual(said["parts"], parts)
        self.assertIsNone(extension.take_fill_order(self.dir, "https://forms.example.org/apply"), "once")
        extension.leave_fill_order(self.dir, "https://forms.example.org/apply", parts)
        with mock.patch("time.time", return_value=time.time() + extension.FILL_ORDER_SECONDS + 5):
            self.assertIsNone(extension.take_fill_order(self.dir, "https://forms.example.org/apply"))
        self.assertEqual(list((extension.folder(self.dir) / "fill").glob("*.json")), [], "let go of")
        self.assertEqual(extension.leave_fill_order(self.dir, "not a page", parts), "")
        # Its files, given to the page to say - never to put in (2026-11-13).
        extension.leave_fill_order(self.dir, "https://forms.example.org/apply", parts,
                                   [{"field": "Resume", "name": "cv.pdf", "path": "C:/w/cv.pdf"}])
        said = extension.take_fill_order(self.dir, "https://forms.example.org/apply")
        self.assertEqual(said["attach"], [{"field": "Resume", "name": "cv.pdf", "path": "C:/w/cv.pdf"}])

    def test_the_host_answers_a_page_from_the_orders_kept_until_one_fills_it(self) -> None:
        """A side agent's note, 2026-11-08: a site's sign-in page, opened first, took the order and
        filled none - the form after the sign-in was given nothing."""
        extension.leave_fill_order(self.dir, "https://forms.example.org/apply",
                                   [{"field": "Review", "text": "Clear and kind."}])
        ask = lambda url: native_host.answer({"type": "fill-ask", "url": url}, self.dir,
                                             lambda *a, **k: None)
        signing_in = ask("https://forms.example.org/login")
        self.assertEqual(signing_in["order"]["parts"][0]["field"], "Review")
        # It filled nothing: it says nothing - the form after it is given the order still.
        form = ask("https://forms.example.org/apply")
        self.assertEqual(form["order"]["id"], signing_in["order"]["id"])
        done = native_host.answer({"type": "fill-done", "id": form["order"]["id"]}, self.dir,
                                  lambda *a, **k: None)
        self.assertTrue(done["ok"])
        self.assertNotIn("order", ask("https://forms.example.org/apply"), "filled: let go of")
        self.assertNotIn("order", ask("https://x.example/"))
        self.assertFalse(extension.finish_fill_order(self.dir, "../../etc"), "only an order's own id")


def _chrome():
    from test_panels import _javascript_engine
    kind, engine = _javascript_engine()
    return engine if kind == "chrome" else None


@unittest.skipUnless(HAS_SOURCE, SOURCES)
@unittest.skipUnless(_chrome(), "no browser here to fill a page in")
class OnARealPageTests(unittest.TestCase):

    def test_its_boxes_filled_never_a_password_nor_the_users_words_and_no_submit(self) -> None:
        order = {"parts": [{"field": "Your name", "text": "Ada Quill"},
                           {"field": "Review", "text": "Clear and kind."},
                           {"field": "Rating", "text": "Good"},
                           {"field": "Password", "text": "hunter2"},
                           {"field": "Affiliation", "text": "Harbour Institute"},
                           {"field": "Shoe size", "text": "41"}]}
        page = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<form id="f" onsubmit="document.body.dataset.submitted='yes'">
  <label for="name">Your name</label> <input id="name">
  <label>Review <textarea name="review"></textarea></label>
  <label for="rating">Rating</label> <select id="rating"><option>Bad</option><option>Good</option></select>
  <label for="pw">Password</label> <input id="pw" type="password">
  <label for="aff">Affiliation</label> <input id="aff" value="My own words">
  <button type="submit">Send</button>
</form>
<script>
  // A PAGE THAT SUBMITS BY ITSELF as soon as a box changes: what the guard is for.
  document.getElementById('name').addEventListener('change', () =>
    document.getElementById('f').requestSubmit());
  globalThis.browser = {runtime: {lastError: null, sendMessage: (m, answer) => answer(%s)}};
</script>
<script>%s</script>
<script>
  setTimeout(() => {
    const v = (id) => document.getElementById(id).value;
    document.title = JSON.stringify({name: v('name'), review: document.querySelector('textarea').value,
      rating: v('rating'), pw: v('pw'), aff: v('aff'), submitted: document.body.dataset.submitted || 'no',
      note: (document.querySelector('[role=status]') || {}).textContent || ''});
  }, 2500);
</script></body></html>""" % (json.dumps({"order": order}), FILL)
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "form.html"
            path.write_text(page, encoding="utf-8")
            done = subprocess.run([_chrome(), "--headless=new", "--disable-extensions", "--disable-gpu", "--no-first-run",
                                   f"--user-data-dir={work}/profile", "--virtual-time-budget=6000",
                                   "--dump-dom", path.as_uri()],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=120)
        said = json.loads(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
                          .replace("&quot;", '"').replace("&amp;", "&"))
        self.assertEqual(said["name"], "Ada Quill")
        self.assertEqual(said["review"], "Clear and kind.")
        self.assertEqual(said["rating"], "Good")
        self.assertEqual(said["pw"], "", "never a password")
        self.assertEqual(said["aff"], "My own words", "never over the user's own words")
        self.assertEqual(said["submitted"], "no", "the page's own submit stopped")
        self.assertIn("Filled by your agent: 3 boxes", said["note"])
        self.assertIn("submit it yourself", said["note"])
        self.assertIn("Shoe size", said["note"], "what found no box, said to copy")


@unittest.skipUnless(HAS_SOURCE, SOURCES)
@unittest.skipUnless(_chrome(), "no browser here to fill a page in")
class ChoicesAndFilesTests(unittest.TestCase):
    """2026-11-13: "可以动单选和网页自制的下拉框" (only an option chosen, never a button); "所有附件不上传，但是
    instruction告诉我传什么"."""

    def test_a_radio_and_a_pages_own_list_chosen_a_link_never_files_told_no_submit(self) -> None:
        order = {"parts": [{"field": "Full name", "text": "Ada Quill"},
                           {"field": "Are you authorized to work here?", "text": "No"},
                           {"field": "Will you relocate?", "text": "Yes"},
                           {"field": "Sponsorship", "text": "Maybe"}],
                 "attach": [{"field": "Resume", "name": "cv.pdf", "path": "C:/work/cv.pdf"}]}
        page = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<form id="f" onsubmit="document.body.dataset.submitted='yes'; return false">
  <label for="name">Full name</label> <input id="name">
  <fieldset><legend>Are you authorized to work here?</legend>
    <label><input type="radio" name="auth" value="y"> Yes</label>
    <label><input type="radio" name="auth" value="n"> No</label></fieldset>
  <label id="rl">Will you relocate?</label>
  <input id="reloc" role="combobox" aria-labelledby="rl" aria-controls="list1"><ul id="list1" role="listbox"></ul>
  <input type="hidden" id="reloc_value">
  <label id="sl">Sponsorship</label>
  <input id="spon" role="combobox" aria-labelledby="sl" aria-controls="list2"><div id="list2" role="listbox"></div>
  <input type="hidden" id="spon_value">
  <label for="cv">Resume</label> <input id="cv" type="file">
  <button type="submit" id="go">Submit application</button>
</form>
<script>
  // A LIST OF THE PAGE'S OWN MAKING: typed into, it offers its options; one chosen by a press on it.
  document.getElementById('reloc').addEventListener('input', () => {
    const list = document.getElementById('list1');
    list.innerHTML = '<li role="option">Yes</li><li role="option">No</li>';
    for (const one of list.children) one.addEventListener('click', () => {
      document.getElementById('reloc_value').value = one.textContent; list.innerHTML = ''; });
  });
  // ITS OPTION A LINK THAT WOULD SUBMIT: never pressed.
  document.getElementById('spon').addEventListener('input', () => {
    const list = document.getElementById('list2');
    list.innerHTML = '<a href="#" role="option" id="maybe">Maybe</a>';
    document.getElementById('maybe').addEventListener('click', () => {
      document.getElementById('spon_value').value = 'Maybe'; document.getElementById('f').requestSubmit(); });
  });
  document.getElementById('go').addEventListener('click', () => { document.body.dataset.pressed = 'yes'; });
  globalThis.browser = {runtime: {lastError: null, sendMessage: (m, answer) => answer && answer(%s)}};
</script>
<script>%s</script>
<script>
  setTimeout(() => {
    const v = (id) => document.getElementById(id).value;
    const auth = (document.querySelector('input[name=auth]:checked') || {}).value || '';
    document.title = JSON.stringify({name: v('name'), auth, reloc: v('reloc_value'), spon: v('spon_value'),
      cv: document.getElementById('cv').files.length, submitted: document.body.dataset.submitted || 'no',
      pressed: document.body.dataset.pressed || 'no',
      note: (document.querySelector('[role=status]') || {}).textContent || ''});
  }, 4500);
</script></body></html>""" % (json.dumps({"order": order}), FILL)
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "apply.html"
            path.write_text(page, encoding="utf-8")
            done = subprocess.run([_chrome(), "--headless=new", "--disable-extensions", "--disable-gpu", "--no-first-run",
                                   f"--user-data-dir={work}/profile", "--virtual-time-budget=9000",
                                   "--dump-dom", path.as_uri()],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=120)
        said = json.loads(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
                          .replace("&quot;", '"').replace("&amp;", "&"))
        self.assertEqual(said["name"], "Ada Quill")
        self.assertEqual(said["auth"], "n", "the radio chosen")
        self.assertEqual(said["reloc"], "Yes", "the page's own list: its option chosen")
        self.assertEqual(said["spon"], "", "an option that is a link: never pressed")
        self.assertEqual((said["submitted"], said["pressed"]), ("no", "no"), "nothing submitted, no button")
        self.assertEqual(said["cv"], 0, "no file put in")
        self.assertIn("Attach these yourself", said["note"])
        self.assertIn("Resume: cv.pdf", said["note"])
        self.assertIn("Sponsorship", said["note"], "what was not chosen, said")


@unittest.skipUnless(HAS_SOURCE, SOURCES)
@unittest.skipUnless(_chrome(), "no browser here to fill a page in")
class ASignInPageFirstTests(unittest.TestCase):

    def test_a_page_with_none_of_its_boxes_fills_nothing_says_nothing_and_keeps_it(self) -> None:
        order = {"id": "abc123", "parts": [{"field": "Review", "text": "Clear and kind."},
                                           {"field": "Summary", "text": "Ferns."}]}
        page = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<form><label for="em">Email</label> <input id="em" type="email">
<label for="pw">Password</label> <input id="pw" type="password"><button>Sign in</button></form>
<script>
  const told = [];
  globalThis.browser = {runtime: {lastError: null, sendMessage: (m, answer) => {
    told.push(m.type); if (answer) answer(%s); }}};
</script>
<script>%s</script>
<script>setTimeout(() => { document.title = JSON.stringify({told,
  note: !!document.querySelector('[role=status]'), em: document.getElementById('em').value}); }, 2500);
</script></body></html>""" % (json.dumps({"order": order}), FILL)
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "login.html"
            path.write_text(page, encoding="utf-8")
            done = subprocess.run([_chrome(), "--headless=new", "--disable-extensions", "--disable-gpu", "--no-first-run",
                                   f"--user-data-dir={work}/profile", "--virtual-time-budget=6000",
                                   "--dump-dom", path.as_uri()],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=120)
        said = json.loads(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
                          .replace("&quot;", '"').replace("&amp;", "&"))
        self.assertEqual(said["told"], ["fill-ask"], "asked - and no fill-done: the order kept")
        self.assertFalse(said["note"], "nothing said on a page that fills none of it")
        self.assertEqual(said["em"], "")


if __name__ == "__main__":
    unittest.main()

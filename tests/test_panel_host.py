"""THE SIDE PANEL IN WEBVIEW2, IN A PROCESS OF ITS OWN (platform_layer/panel_host.py; the user, 2026-11-14: "那你试
一下WebView2吧"; "接进"; and started before it is wanted - "这个做"). Measured that day: Chrome's app mode 1.7-2.1 s
to open the panel, WebView2 already running 0.30-0.37 s; a panel made hidden beforehand drew its list 0.1 s
after it was shown.

The host is played here by a small stand-in speaking its protocol - no WebView2 needed; the real one was run
against a stand-in server that day. Everything here is made up."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401

from personalized_agent.platform_layer import app_window as aw
from personalized_agent.platform_layer import panel_host as ph

#: The host's protocol, played: ready; each "open" written down and answered - "fail" in its url refused.
STAND_IN = r'''
import json, sys
kept = sys.argv[1]
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    asked = json.loads(line)
    if asked.get("op") == "quit":
        break
    with open(kept, "a", encoding="utf-8") as out:
        out.write(line)
    if asked.get("op") == "focus":
        print(json.dumps({"focused": len(asked.get("titles") or [])}), flush=True)
    elif "fail" in asked.get("url", ""):
        print(json.dumps({"error": "RuntimeError: no WebView2 here"}), flush=True)
    else:
        print(json.dumps({"opened": True}), flush=True)
'''
SILENT = "import sys, time\ntime.sleep(30)\n"


class TheHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._dir.cleanup)
        self.here = Path(self._dir.name)
        self.kept = self.here / "asked.jsonl"

    def _host(self, script: str = STAND_IN) -> ph.PanelHost:
        host = ph.PanelHost(self.here / "store")
        command = [sys.executable, "-c", script, str(self.kept)]
        patches = [mock.patch.object(ph, "host_command", lambda storage: command),
                   mock.patch.object(ph.PanelHost, "usable", lambda self: not self.failed)]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)
        self.addCleanup(host.stop)
        return host

    def test_started_once_asked_to_open_and_stopped(self) -> None:
        host = self._host()
        self.assertTrue(host.start())
        child = host._child
        self.assertTrue(host.start(), "started once")
        self.assertIs(host._child, child)
        self.assertTrue(host.open("http://here/notes.html?placed=1", "Waiting notifications", 460, 900,
                                  -32000, 0, hidden=True))
        [asked] = [json.loads(line) for line in self.kept.read_text("utf-8").splitlines()]
        self.assertEqual(asked, {"op": "open", "url": "http://here/notes.html?placed=1",
                                 "title": "Waiting notifications", "width": 460, "height": 900,
                                 "x": -32000, "y": 0, "hidden": True})
        # What it cannot open: False - the desktop opens it the browser's way.
        self.assertFalse(host.open("http://here/fail", "Waiting notifications", 460, 900))
        host.stop()
        self.assertFalse(host.running())
        self.assertEqual(child.wait(timeout=5), 0, "told to quit, it ended by itself")

    def test_its_page_given_the_keys_by_the_host(self) -> None:
        """2026-11-17: "那个快捷键之后，焦点应该放到chat栏（这样我可以直接输入)" - done where the window is."""
        host = self._host()
        self.assertTrue(host.start())
        with mock.patch("ctypes.windll", create=True) as windll:
            self.assertTrue(host.focus({"Waiting notifications"}))
        windll.user32.AllowSetForegroundWindow.assert_called_once_with(host._child.pid)
        [asked] = [json.loads(line) for line in self.kept.read_text("utf-8").splitlines()]
        self.assertEqual(asked, {"op": "focus", "titles": ["Waiting notifications"]})
        self.assertEqual(ph.focus_panel({"Nothing open"}), 0, "no WebView2 window here: none")

    def test_slow_the_first_time_started_again_once(self) -> None:
        """2026-11-17: "现在怎么没有sidebar了" - once it did not say it was ready in time; and what it wrote was lost."""
        slow_once = ("import json, sys, time, pathlib\n"
                     "mark = pathlib.Path(sys.argv[1] + '.once')\n"
                     "if not mark.exists():\n"
                     "    mark.write_text('x'); sys.stderr.write('slow to start\\n'); sys.stderr.flush(); time.sleep(30)\n"
                     "print(json.dumps({'ready': True}), flush=True)\n"
                     "for line in sys.stdin:\n"
                     "    if json.loads(line).get('op') == 'quit': break\n")
        host = self._host(slow_once)
        with mock.patch.object(ph, "START_SECONDS", 2.0):
            self.assertTrue(host.start(), "started again, ready")
        host.stop()
        said = (self.here / "logs" / "panel_host.log").read_text("utf-8")
        self.assertIn("slow to start", said, "what it wrote kept by the other logs")

    def test_one_that_never_says_it_is_ready_is_let_go_and_not_asked_again(self) -> None:
        host = self._host(SILENT)
        with mock.patch.object(ph, "START_SECONDS", 1.0):
            self.assertFalse(host.start())
        self.assertEqual(host.failed, "it did not start")
        self.assertFalse(host.running())
        self.assertFalse(host.open("http://here/notes.html", "x", 460, 900), "the browser's way from now on")

    def test_started_after_a_while_then_the_panel_made_hidden(self) -> None:
        host = self._host()
        made = []
        host.start_later(0.05, then=lambda: made.append(host.running()))
        for _ in range(100):
            if made:
                break
            import time
            time.sleep(0.05)
        self.assertEqual(made, [True])

    def test_where_it_cannot_be_nothing_is_started(self) -> None:
        host = ph.PanelHost(self.here / "store")
        with mock.patch.object(ph, "pywebview_present", return_value=False):
            self.assertFalse(host.usable())
            self.assertFalse(host.start())
        with mock.patch.dict("os.environ", {"PA_NO_DIALOGS": "1"}):
            self.assertFalse(host.usable(), "never under the tests")

    def test_how_it_is_started(self) -> None:
        store = Path("store")
        self.assertEqual(ph.host_command(store)[1:], ["-m", "personalized_agent.platform_layer.panel_host", "store"])
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(ph.host_command(store), [sys.executable, ph.FLAG, "store"])
        source = (Path(ph.__file__).resolve().parents[1] / "__main__.py").read_text("utf-8")
        self.assertIn('args_in[0] == "--panel-host"', source)
        self.assertEqual(ph.serve(""), 2, "no store, no host")

    def test_started_where_its_module_is_found_whatever_folder_the_application_is_in(self) -> None:
        # Started at sign-in the application is in C:\Windows\System32, where "-m" finds no package.
        elsewhere = self.here / "elsewhere"
        elsewhere.mkdir()
        bare = {**os.environ, "PYTHONPATH": ""}
        found = [sys.executable, "-c", "import personalized_agent.platform_layer.panel_host"]
        self.assertNotEqual(subprocess.run(found, cwd=elsewhere, capture_output=True, env=bare).returncode, 0)
        self.assertEqual(subprocess.run(found, cwd=ph.host_folder(), capture_output=True, env=bare).returncode, 0)
        asked = {}

        def popen(command, **kwargs):
            asked.update(kwargs)
            raise OSError("not started here")
        host = ph.PanelHost(self.here / "store")
        with mock.patch.object(ph.PanelHost, "usable", lambda self: True),                 mock.patch.object(ph.subprocess, "Popen", popen):
            self.assertFalse(host.start())
        self.assertEqual(asked.get("cwd"), ph.host_folder())
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertIsNone(ph.host_folder(), "the packaged application starts itself, wherever it is")


class TheSharedWindowsFunctionsTests(unittest.TestCase):
    """THE AVATAR TUCKED, THE PANEL STILL COMES (the user, 2026-11-15: "我现在logo是在侧边，是这个的原因吗"): the
    tuck set `windll.user32.GetMonitorInfoW` to take its own structure alone, and every other caller's failed -
    the side panel's, which then stayed off every screen."""

    def test_no_shared_function_is_made_to_take_one_structure_alone(self) -> None:
        import re
        source = Path(ph.__file__).resolve().parents[1]
        bad = [f"{path.relative_to(source)}:{n}" for path in source.rglob("*.py")
               for n, line in enumerate(path.read_text("utf-8").splitlines(), 1)
               if re.search(r"(user32|kernel32|gdi32|shcore)\.\w+\.argtypes\s*=.*POINTER\((?!wintypes\.)", line)]
        # Windows' own (wintypes.RECT, wintypes.MSG) are one class for every caller; a module's own is not.
        self.assertEqual(bad, [], "a structure of its own is passed as c_void_p: every module shares windll")

    @unittest.skipUnless(sys.platform == "win32", "Windows' own screens")
    def test_the_panels_screen_read_after_the_avatar_was_tucked(self) -> None:
        from personalized_agent.ui import avatar_tuck, chat_bubble  # noqa: F401 - each sets its own types
        avatar_tuck._windows(100, 100)
        (left, top, right, bottom), dpi = aw._cursor_work_area()
        self.assertGreater(right, left)
        self.assertGreater(dpi, 0)


class TheDesktopTests(unittest.TestCase):
    def test_its_windows_are_the_agents_own_and_opened_by_it_first(self) -> None:
        self.assertTrue(aw._ours("Chrome_WidgetWin_1"))
        self.assertTrue(aw._ours("WindowsForms10.Window.8.app.0.390be75_r18_ad1"))
        self.assertFalse(aw._ours("Notepad"))
        host = mock.Mock()
        host.open.return_value = False
        with mock.patch.object(aw, "_no_windows_here", return_value=False), \
                mock.patch.object(aw.sys, "platform", "win32"), \
                mock.patch.object(aw, "place_side_window") as placed:
            self.assertFalse(aw.open_hosted_side_window(host, "http://here/x", (460, 900), "T"))
            placed.assert_not_called()
            host.open.return_value = True
            self.assertTrue(aw.open_hosted_side_window(host, "http://here/x", (460, 900), "T", {"T"}))
            self.assertEqual(host.open.call_args[0], ("http://here/x", "T", 460, 900) + aw.OFF_SCREEN)
            self.assertEqual(placed.call_args[0], ((460, 900), {"T"}))
            self.assertFalse(aw.open_hosted_side_window(None, "http://here/x", (460, 900), "T"))

    def test_the_desktop_opens_it_in_webview2_first_and_the_browser_only_when_it_cannot(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        desk = SimpleNamespace(app=SimpleNamespace(sign_in_url=lambda path: "http://here" + path,
                                                   paths=SimpleNamespace(data_dir=Path("data")), config=None),
                               NOTES_TITLE=DesktopAgent.NOTES_TITLE, NOTES_PATH=DesktopAgent.NOTES_PATH,
                               _off_this_thread=lambda what, name="": what(), _notes_on_a_mac=lambda: False,
                               panel_host=object())
        desk._notes_titles = lambda: DesktopAgent._notes_titles(desk)
        # Never the real screen's state: a film the user is watching is not this test's.
        no_film = mock.patch.object(DesktopAgent, "_film_in_front", return_value=False)
        no_film.start()
        self.addCleanup(no_film.stop)
        with mock.patch.object(aw, "slide_in_titled", return_value=False), \
                mock.patch.object(aw, "open_hosted_side_window", return_value=True) as hosted, \
                mock.patch.object(aw, "open_side_window") as browser:
            DesktopAgent.open_notifications(desk)
        self.assertEqual(hosted.call_args[0][1], "http://here/notes.html?placed=1")
        browser.assert_not_called()
        with mock.patch.object(aw, "slide_in_titled", return_value=False), \
                mock.patch.object(aw, "open_hosted_side_window", return_value=False), \
                mock.patch.object(aw, "open_side_window", return_value=True) as browser:
            DesktopAgent.open_notifications(desk)
        browser.assert_called_once()

    def test_made_hidden_beforehand_unless_one_is_there(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        host = mock.Mock()
        desk = SimpleNamespace(app=SimpleNamespace(sign_in_url=lambda path: "http://here" + path, config=None),
                               NOTES_TITLE=DesktopAgent.NOTES_TITLE, NOTES_PATH=DesktopAgent.NOTES_PATH,
                               panel_host=host)
        desk._notes_titles = lambda: DesktopAgent._notes_titles(desk)
        with mock.patch.object(aw, "_windows_page", return_value=None):
            DesktopAgent._prepare_notes(desk)
        self.assertEqual(host.open.call_args[1], {"hidden": True})
        self.assertEqual(host.open.call_args[0][:2], ("http://here/notes.html?placed=1", DesktopAgent.NOTES_TITLE))
        host.reset_mock()
        with mock.patch.object(aw, "_windows_page", return_value=1234):
            DesktopAgent._prepare_notes(desk)
        host.open.assert_not_called()


class TheShellToldWhenItIsActivatedTests(unittest.TestCase):
    """2026-11-17: "sidebar有焦点的时候，你的任务栏会变暗" - only while pinned, a bar of the screen that never said it
    was activated."""

    @unittest.skipUnless(sys.platform == "win32", "Windows' own")
    def test_activated_it_says_so_as_a_bar_must(self) -> None:
        import ctypes
        from unittest import mock

        from personalized_agent.platform_layer import panel_host
        with mock.patch.object(ctypes.windll.shell32, "SHAppBarMessage") as said:
            panel_host.appbar_activated(4242)
        message, data = said.call_args[0]
        self.assertEqual(message, panel_host.ABM_ACTIVATE)
        import inspect
        self.assertIn("tell_the_shell_when_active(shown)", inspect.getsource(panel_host.serve))


if __name__ == "__main__":
    unittest.main()

"""TWO PRIVACY PROTECTIONS (the user, 2026-11-12; common/privacy_folders.py).

Privacy folders and files: the monitor keeps only the outermost name and never
reads inside; no search reads them; while one of their files is open no picture
of the screen is taken at all (not one painted over) and the chat says why.
Private mode, from the avatar's menu: nothing recorded until it is turned off,
the watermelon red meanwhile. Fixtures are made up."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import support  # noqa: F401 - the package on the path, as for every test here

from personalized_agent.common import privacy_folders
from personalized_agent.common.events.models import ListenerEvent


class _Config:
    """Just enough of the settings for the filter and the providers."""

    def __init__(self, values: Dict[str, Any] = None) -> None:
        self.values = dict(values or {})

    def get(self, key, default=None):
        return self.values.get(key, default)

    def lines(self, key):
        raw = self.values.get(key, "")
        if isinstance(raw, (list, tuple)):
            return [str(item) for item in raw if str(item).strip()]
        return [line.strip() for line in str(raw).splitlines() if line.strip()]

    def set(self, key, value, internal=False):
        self.values[key] = value


class _Raw:
    def __init__(self, path, event_type="modified", dest=None):
        self.src_path = str(path)
        self.dest_path = str(dest) if dest else ""
        self.event_type = event_type
        self.is_directory = False


class _Store:
    def __init__(self):
        self.events = []

    def add_event(self, event):
        self.events.append(event)
        return event


class _Folders(unittest.TestCase):
    """A made-up home: a privacy folder with a file and a folder in it, a privacy file
    beside it, and a file of the user's that is not private."""

    def setUp(self) -> None:
        privacy_folders.forget()
        self.dir = Path(tempfile.mkdtemp(prefix="pa-privacy-")).resolve()
        self.secret = self.dir / "Lantern Diary"
        (self.secret / "letters-to-orchid").mkdir(parents=True)
        (self.secret / "harbour-plan.md").write_text("the harbour plan, in detail",
                                                     encoding="utf-8")
        (self.secret / "letters-to-orchid" / "first.txt").write_text("dear orchid",
                                                                     encoding="utf-8")
        self.lone = self.dir / "tax-summary.csv"
        self.lone.write_text("a,b\n1,2\n", encoding="utf-8")
        self.open_file = self.dir / "garden-notes.md"
        self.open_file.write_text("tomatoes need shade", encoding="utf-8")
        self.config = _Config({
            "listener_enabled": True,
            "listener_filesystem_enabled": True,
            "listener_screen_enabled": True,
            "listener_application_enabled": True,
            "listener_screenshots_enabled": True,
            "listener_only_while_present": False,
            "listener_only_what_the_user_did": False,
            "listener_privacy_paths": f"{self.secret}\n{self.lone}",
        })
        self.addCleanup(privacy_folders.forget)
        self.addCleanup(shutil.rmtree, self.dir, True)


class WhichPathsAndWindowsTests(_Folders):

    def test_a_folder_and_a_file_and_what_is_in_the_folder(self) -> None:
        cover = lambda path: privacy_folders.covering(path, self.config)
        self.assertEqual(cover(self.secret / "harbour-plan.md"), self.secret)
        self.assertEqual(cover(self.secret / "letters-to-orchid" / "first.txt"), self.secret)
        self.assertEqual(cover(self.secret), self.secret)
        self.assertEqual(cover(self.lone), self.lone)
        self.assertIsNone(cover(self.open_file))
        self.assertIsNone(cover(self.dir))
        # Not a folder whose name only begins the same.
        self.assertIsNone(cover(self.dir / "Lantern Diary2" / "x.md"))

    def test_a_window_by_its_title(self) -> None:
        said = lambda title: privacy_folders.window_is_private("", title, self.config)
        self.assertEqual(said("harbour-plan.md - Notepad"), self.secret)
        self.assertEqual(said("Lantern Diary - File Explorer"), self.secret)
        self.assertEqual(said(f"{self.secret}\\letters-to-orchid - Explorer"), self.secret)
        self.assertEqual(said("letters-to-orchid"), self.secret)
        self.assertEqual(said("tax-summary.csv - Excel"), self.lone)
        # The name inside a longer word, another file, a short folder's name: not private.
        self.assertIsNone(said("garden-notes.md - Notepad"))
        self.assertIsNone(said("xharbour-plan.mdx - Notepad"))
        self.assertIsNone(said(""))

    def test_none_added_none_private(self) -> None:
        empty = _Config({"listener_privacy_paths": ""})
        self.assertIsNone(privacy_folders.covering(self.lone, empty))
        self.assertIsNone(privacy_folders.private_file_open(empty, titles=["anything"]))
        self.assertEqual(privacy_folders.why_no_picture(empty), "")


class WhatTheMonitorKeepsTests(_Folders):

    def _manager(self):
        from personalized_agent.listener.core.manager import ListenerManager
        from personalized_agent.listener.providers.filesystem import FilesystemProvider
        store = _Store()
        manager = ListenerManager(config=self.config, raw_store=store)
        provider = FilesystemProvider(self.config)
        manager.register(provider)
        return manager, provider, store

    def test_a_file_in_one_is_kept_as_the_folders_name_and_never_opened(self) -> None:
        from personalized_agent.listener.providers import filesystem
        manager, provider, store = self._manager()
        opened: List[str] = []
        real_open = open

        def counting_open(path, *args, **kwargs):
            opened.append(os.path.basename(str(path)))
            return real_open(path, *args, **kwargs)

        filesystem.open = counting_open
        try:
            provider.handle_raw(_Raw(self.secret / "harbour-plan.md"))
            provider.handle_raw(_Raw(self.secret / "letters-to-orchid" / "first.txt"))
            provider.handle_raw(_Raw(self.lone))
            provider.handle_raw(_Raw(self.open_file))
            provider.flush_held_events()
        finally:
            del filesystem.open
        kept = {e.data["path"]: e.data for e in store.events}
        self.assertEqual(opened, ["garden-notes.md"], "nothing in a privacy folder opened")
        self.assertEqual(set(kept), {str(self.secret), str(self.lone), str(self.open_file)})
        for path in (str(self.secret), str(self.lone)):
            self.assertNotIn("content", kept[path])
            self.assertTrue(kept[path]["privacy_folder"])
        self.assertEqual(kept[str(self.open_file)]["content"], "tomatoes need shade")
        every = repr([e.data for e in store.events])
        for inner in ("harbour-plan", "letters-to-orchid", "first.txt", "dear orchid"):
            self.assertNotIn(inner, every)

    def test_a_move_inside_it_is_the_folder_alone(self) -> None:
        manager, provider, store = self._manager()
        provider.handle_raw(_Raw(self.secret / "harbour-plan.md", "moved",
                                 dest=self.secret / "letters-to-orchid" / "plan.md"))
        provider.flush_held_events()
        self.assertTrue(store.events)
        for event in store.events:
            for key in ("path", "source_path", "destination_path"):
                if key in event.data:
                    self.assertEqual(event.data[key], str(self.secret))

    def test_a_window_showing_one_is_not_read_and_its_title_is_the_folders_name(self) -> None:
        from personalized_agent.listener.core import filtered
        from personalized_agent.listener.core.manager import ListenerManager
        filtered.reset()
        store = _Store()
        manager = ListenerManager(config=self.config, raw_store=store)
        read = ListenerEvent(event_type="screen.text", source="screen", application="notepad.exe",
                             data={"window_title": "harbour-plan.md - Notepad",
                                   "text": "the harbour plan, in detail"})
        manager._on_event(read)
        self.assertEqual(store.events, [])
        self.assertEqual(filtered.counts().get("privacy_folder"), 1)
        focus = ListenerEvent(event_type="application.focused", source="application",
                              application="notepad.exe",
                              data={"window_title": "harbour-plan.md - Notepad"})
        manager._on_event(focus)
        self.assertEqual([e.data["window_title"] for e in store.events], ["Lantern Diary"])
        other = ListenerEvent(event_type="screen.text", source="screen", application="notepad.exe",
                              data={"window_title": "garden-notes.md - Notepad",
                                    "text": "tomatoes need shade"})
        manager._on_event(other)
        self.assertEqual(len(store.events), 2, "another window is read as before")


class PrivateModeTests(_Folders):

    def test_nothing_is_recorded_and_the_sources_stop(self) -> None:
        from personalized_agent.listener.core import filtered
        from personalized_agent.listener.core.manager import ListenerManager
        from personalized_agent.listener.core.provider import ListenerProvider, PluginManifest

        class Source(ListenerProvider):
            provider_id = "made-up"
            category = "application"
            started = stopped = 0

            def start(self):
                Source.started += 1

            def stop(self):
                Source.stopped += 1

            def get_status(self):
                return {}

            def supported_event_types(self):
                return ["application.focused"]

            def get_capabilities(self):
                return {}

            def manifest(self):
                return PluginManifest(plugin_id="made-up", version="1",
                                      supported_platforms=["windows"],
                                      supported_event_types=[], required_permissions=[],
                                      configuration_schema={}, description="")

        filtered.reset()
        store = _Store()
        manager = ListenerManager(config=self.config, raw_store=store)
        source = Source()
        manager.register(source)
        manager.start()
        self.assertEqual(Source.started, 1)
        self.config.set("listener_private_mode", True)
        manager.apply_settings()
        self.assertEqual(Source.stopped, 1, "stopped while private")
        manager._on_event(ListenerEvent(event_type="application.focused", source="made-up",
                                        application="notepad.exe",
                                        data={"window_title": "garden-notes.md"}))
        self.assertEqual(store.events, [])
        self.assertEqual(filtered.counts().get("private_mode"), 1)
        self.assertFalse(manager.privacy.monitoring_enabled())
        self.config.set("listener_private_mode", False)
        manager.apply_settings()
        self.assertEqual(Source.started, 2, "on again once it is turned off")
        manager._on_event(ListenerEvent(event_type="application.focused", source="made-up",
                                        application="notepad.exe",
                                        data={"window_title": "garden-notes.md"}))
        self.assertEqual(len(store.events), 1)

    def test_the_watermelon_is_red(self) -> None:
        from PIL import Image
        from personalized_agent.ui.desktop import DesktopAgent
        frame = Image.new("RGBA", (3, 1))
        frame.putpixel((0, 0), (40, 160, 60, 255))     # the rind
        frame.putpixel((1, 0), (230, 50, 60, 255))     # the flesh
        frame.putpixel((2, 0), (255, 255, 255, 255))   # an eye
        red = DesktopAgent._in_private_colours(object(), frame)
        rind = red.getpixel((0, 0))
        self.assertGreater(rind[0], 150)
        self.assertLess(rind[1], 60)
        self.assertEqual(red.getpixel((1, 0)), (230, 50, 60, 255))
        self.assertEqual(red.getpixel((2, 0)), (255, 255, 255, 255))

    def test_the_flat_watermelons_teal_leaves_are_red_too(self) -> None:
        """2026-11-12: "logo的叶子也要变成红色" - its leaves, their dark half and veins bluer than green."""
        from PIL import Image
        from personalized_agent.agent.character import watermelon_flat as flat
        from personalized_agent.ui.desktop import DesktopAgent
        colours = [flat.LEAF, flat.LEAF_DARK, flat.LEAF_VEIN, flat.OUTLINE, flat.BASE, flat.STRIPE,
                   flat.IRIS, flat.BLUSH, flat.TEAR, flat.CREAM]
        frame = Image.new("RGBA", (len(colours), 1))
        for x, colour in enumerate(colours):
            frame.putpixel((x, 0), colour + (255,))
        red = DesktopAgent._in_private_colours(object(), frame)
        for x in range(6):                                  # the leaves, the rind, its stripes
            r, g, b, _a = red.getpixel((x, 0))
            self.assertTrue(r > g + 30 and r > b + 30, (colours[x], (r, g, b)))
        for x in range(6, len(colours)):                    # its eyes, cheeks, a tear, white
            self.assertEqual(red.getpixel((x, 0)), colours[x] + (255,), colours[x])


class APrivateWebPageTests(_Folders):
    """2026-11-12: "你的private mode 要follow https://agentssociety.org/ 的protocol"; then "我只排除这个
    窗口（所以你那个protected directory里，也可以允许我加url, 这个url下的所有网址都不会view和截图，和现在这个一样)":
    a page whose site signals (agent-private-mode), or at a private address, is left out alone - as a
    privacy folder's file: not read, nothing kept, no picture while it is open; the rest recorded."""

    TITLE = "[Private] Inbox - Agents Society - Google Chrome"

    def setUp(self) -> None:
        super().setUp()
        self.titles: List[str] = []
        privacy_folders.site_titles = lambda: list(self.titles)
        self.addCleanup(setattr, privacy_folders, "site_titles", lambda: [])
        self.config.set("listener_privacy_paths",
                        f"{self.secret}\nhttps://www.harbour-notes.example/private")

    def test_its_window_alone_left_out_private_mode_untouched(self) -> None:
        self.titles.append(self.TITLE)
        privacy_folders.forget()
        self.assertFalse(privacy_folders.private_mode(self.config), "private mode stays the user's")
        self.assertEqual(privacy_folders.why_no_picture(self.config),
                         privacy_folders.NO_PICTURE_PRIVATE_PAGE, "no picture while it is open")
        self.assertTrue(privacy_folders.withheld(privacy_folders.NO_PICTURE_PRIVATE_PAGE))
        self.assertTrue(privacy_folders.window_not_to_read("chrome.exe", self.TITLE))
        self.assertFalse(privacy_folders.window_not_to_read("notepad.exe", "garden-notes.md"))
        self.titles.clear()
        privacy_folders.forget()
        self.assertEqual(privacy_folders.why_no_picture(self.config), "", "over when it is closed")
        self.assertFalse(privacy_folders.site_signal("Notes [Private] - Editor"),
                         "only a title starting with it")

    def test_the_addresses_are_not_folders_and_cover_what_is_under_them(self) -> None:
        self.assertEqual(privacy_folders.roots(self.config), [self.secret])
        private = lambda url: privacy_folders.address_is_private(url, self.config)  # noqa: E731
        self.assertTrue(private("https://harbour-notes.example/private"))
        self.assertTrue(private("http://www.harbour-notes.example/Private/day-2?x=1"))
        self.assertFalse(private("https://harbour-notes.example/privateer"))
        self.assertFalse(private("https://harbour-notes.example/public"))
        self.assertFalse(private("https://other.example/private"))
        self.assertTrue(privacy_folders.address_in("https://mail.lantern.example/a", ["https://lantern.example"]),
                        "a site given whole: its subdomains too")

    def test_nothing_of_it_kept_the_rest_recorded_and_its_window_known_after(self) -> None:
        from personalized_agent.listener.core.privacy import PrivacyFilter
        privacy = PrivacyFilter(config=self.config)
        signalled = ListenerEvent(event_type="application.focused", source="made-up",
                                  application="chrome.exe", data={"window_title": self.TITLE})
        self.assertEqual(privacy.refusal(signalled, "application"), "private_page")
        visited = ListenerEvent(event_type="browser.visited", source="browser", application="chrome",
                                data={"title": "Harbour diary", "url": "https://harbour-notes.example/private/1"})
        self.assertEqual(privacy.refusal(visited, "browser"), "private_page")
        later = ListenerEvent(event_type="application.focused", source="made-up", application="chrome.exe",
                              data={"window_title": "Harbour diary - Google Chrome"})
        self.assertEqual(privacy.refusal(later, "application"), "private_page",
                         "its window known by its title once seen")
        self.titles.append("Harbour diary - Google Chrome")
        privacy_folders._site.update(at=0.0)
        self.assertEqual(privacy_folders.why_no_picture(self.config), privacy_folders.NO_PICTURE_PRIVATE_PAGE)
        other = ListenerEvent(event_type="application.focused", source="made-up",
                              application="notepad.exe", data={"window_title": "garden-notes.md"})
        self.assertIsNone(privacy.refusal(other, "application"), "the rest recorded as ever")
        self.assertTrue(privacy.category_enabled("application"))

    def test_a_page_signals_by_its_header_meta_or_attribute(self) -> None:
        signals = privacy_folders.page_signals
        self.assertTrue(signals({"Agent-Private-Mode": "on"}, ""))
        self.assertTrue(signals({}, '<head><meta name="agent-private-mode" content="on"></head>'))
        self.assertTrue(signals({}, "<meta content='on' name='agent-private-mode'>"))
        self.assertTrue(signals({}, '<html lang="en" data-agent-private-mode="on">'))
        self.assertFalse(signals({"Agent-Private-Mode": "off"}, "<html><title>x</title>"))
        self.assertFalse(signals(None, '<meta name="agent-private-mode" content="off">'))

    def test_the_agents_web_read_returns_nothing_of_it(self) -> None:
        from personalized_agent.agent.work import tools

        class Answer:
            headers = {"Content-Type": "text/html", "Agent-Private-Mode": "on"}

            def read(self, _n):
                return b"<html><title>[Private] Inbox</title><body>a letter</body></html>"

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch.object(tools.urllib.request, "urlopen", return_value=Answer()):
            said = tools.fetch("https://example.org/inbox")
        self.assertNotIn("text", said)
        self.assertIn("private mode", said["error"])
        privacy_folders.use(self.config)
        self.addCleanup(privacy_folders.use, None)
        with mock.patch.object(tools.urllib.request, "urlopen") as opened:
            said = tools.fetch("https://harbour-notes.example/private/2")
        opened.assert_not_called()
        self.assertIn("private web addresses", said["error"])

    def test_the_extension_keeps_nothing_of_it(self) -> None:
        import json
        from personalized_agent.platform_layer import browser_extension
        browser_extension.keep_visible(self.dir, "Garden - Chrome", "tomatoes")
        browser_extension.keep_visible(self.dir, "[Private] Inbox", "a letter")
        kept = json.loads((browser_extension.folder(self.dir) / "visible.json").read_text("utf-8"))
        self.assertEqual((kept["title"], kept["text"]), ("", ""), "and what was kept before, let go")
        privacy_folders.use(self.config, self.dir)
        self.addCleanup(privacy_folders.use, None)
        browser_extension.keep_visible(self.dir, "Harbour diary", "my diary",
                                       "https://harbour-notes.example/private/3")
        kept = json.loads((browser_extension.folder(self.dir) / "visible.json").read_text("utf-8"))
        self.assertEqual(kept["text"], "", "a page at a private address: nothing of it kept")
        privacy_folders._pages.clear()
        self.assertTrue(privacy_folders.is_private_page_title("Harbour diary - Google Chrome"),
                        "its title told by the extension's line")
        root = Path(browser_extension.__file__).resolve().parents[1] / "resources" / "browser_extension"
        for name in ("visible.js", "fill.js", "background.js"):
            self.assertIn("agentPrivateMode", (root / name).read_text("utf-8"), name)


class NoPictureAtAllTests(_Folders):

    def test_why_none_is_taken(self) -> None:
        with mock.patch.object(privacy_folders, "_windows_titles",
                               return_value=["harbour-plan.md - Notepad"]), \
                mock.patch.object(privacy_folders, "_command_lines", return_value=[]):
            self.assertEqual(privacy_folders.why_no_picture(self.config),
                             privacy_folders.NO_PICTURE_PRIVACY_FOLDER)
        privacy_folders.forget()
        with mock.patch.object(privacy_folders, "_windows_titles",
                               return_value=["garden-notes.md - Notepad"]), \
                mock.patch.object(privacy_folders, "_command_lines",
                                  return_value=["\0".join(["--open", str(self.lone)])]):
            # Passed to a program on its command line: open too.
            self.assertEqual(privacy_folders.why_no_picture(self.config),
                             privacy_folders.NO_PICTURE_PRIVACY_FOLDER)
        privacy_folders.forget()
        with mock.patch.object(privacy_folders, "_windows_titles",
                               return_value=["garden-notes.md - Notepad"]), \
                mock.patch.object(privacy_folders, "_command_lines", return_value=[]):
            self.assertEqual(privacy_folders.why_no_picture(self.config), "")
            self.config.set("listener_private_mode", True)
            self.assertEqual(privacy_folders.why_no_picture(self.config),
                             privacy_folders.NO_PICTURE_PRIVATE_MODE)
        self.assertTrue(privacy_folders.withheld(privacy_folders.NO_PICTURE_PRIVATE_MODE))
        self.assertFalse(privacy_folders.withheld("the window in front is one never read"))

    def test_the_chats_picture_is_not_taken_and_says_why(self) -> None:
        from personalized_agent.agent.front_picture import FrontPictures
        grabbed = []

        class Activity:
            def user_screen_image(self, **_kw):
                grabbed.append("screen")

            def user_window_image(self, **_kw):
                grabbed.append("window")

        pictures = FrontPictures(self.config, Activity(), self.dir / "shots",
                                 front=lambda: {"title": "garden-notes.md"})
        with mock.patch.object(privacy_folders, "_windows_titles",
                               return_value=["harbour-plan.md - Notepad"]), \
                mock.patch.object(privacy_folders, "_command_lines", return_value=[]):
            pictures.take(words=False)
            shot = pictures.for_a_message(title="garden-notes.md", wait=0.5)
        self.assertEqual(grabbed, [], "nothing taken - not one painted over")
        self.assertEqual(shot, {})
        self.assertEqual(pictures.why, privacy_folders.NO_PICTURE_PRIVACY_FOLDER)
        self.assertIsNone(pictures.preview(wait=0.1))
        privacy_folders.forget()
        self.config.set("listener_private_mode", True)
        pictures.take(words=False)
        self.assertEqual((grabbed, pictures.why), ([], privacy_folders.NO_PICTURE_PRIVATE_MODE))

    def test_the_monitors_picture_is_not_taken(self) -> None:
        from personalized_agent.listener.core import filtered
        from personalized_agent.listener.providers.screenshot import ScreenshotProvider
        filtered.reset()

        class Activity:
            def seconds_since_input(self):
                return 1.0

        provider = ScreenshotProvider(self.config, platform=type("P", (), {
            "activity": Activity()})(), folder=self.dir / "screens")
        with mock.patch.object(privacy_folders, "_windows_titles",
                               return_value=["garden-notes.md - Notepad"]), \
                mock.patch.object(privacy_folders, "_command_lines", return_value=[]):
            self.assertTrue(provider._allowed("notepad.exe", "garden-notes.md", 1))
        privacy_folders.forget()
        with mock.patch.object(privacy_folders, "_windows_titles",
                               return_value=["garden-notes.md - Notepad",
                                             "harbour-plan.md - Notepad"]), \
                mock.patch.object(privacy_folders, "_command_lines", return_value=[]):
            self.assertFalse(provider._allowed("notepad.exe", "garden-notes.md", 1))
            self.assertFalse(provider._allowed("notepad.exe", "garden-notes.md", 1))
        self.assertEqual(filtered.counts().get("privacy_folder"), 1, "said once a window")

    def test_the_chat_is_told_nothing_of_the_screen(self) -> None:
        from personalized_agent.agent.front_screen import front_now

        class Db:
            def query(self, *_a):
                raise AssertionError("nothing read of what was recorded")
        self.config.set("listener_private_mode", True)
        self.assertEqual(front_now(Db(), self.config), {})


class TheAgentsNeverReadItTests(_Folders):

    def setUp(self) -> None:
        super().setUp()
        privacy_folders.use(self.config)
        self.addCleanup(privacy_folders.use, None)

    def test_the_file_tools_refuse_it(self) -> None:
        from personalized_agent.runtime.filesystem import validate_read_path
        from personalized_agent.runtime.permissions import SecurityViolation, WorkspacePolicy
        workspace = self.dir / "workspace"
        workspace.mkdir()
        policy = WorkspacePolicy.create(workspace, readable_paths=[self.dir])
        policy.read_anywhere = True
        for path in (self.secret / "harbour-plan.md", self.secret, self.lone):
            with self.assertRaises(SecurityViolation) as caught:
                validate_read_path(str(path), policy)
            self.assertEqual(caught.exception.boundary, "privacy_folder")
        self.assertEqual(validate_read_path(str(self.open_file), policy), self.open_file)

    def test_the_code_a_run_runs_is_refused_it_and_does_not_list_it(self) -> None:
        from personalized_agent.runtime import guard
        before = list(guard._private)
        guard._private = [guard._resolve(str(self.secret)), guard._resolve(str(self.lone))]
        try:
            with self.assertRaises(guard.SandboxViolation):
                guard._check_read(str(self.secret / "harbour-plan.md"), "open")
            with self.assertRaises(guard.SandboxViolation):
                guard._check_read(str(self.lone), "open")
            guard._check_read(str(self.open_file), "open")
            hidden = guard._hidden_roots()
            self.assertIn(guard._normcase(guard._resolve(str(self.secret))), hidden)
        finally:
            guard._private = before
        self.assertIn(guard.PRIVATE_ENV, guard._POLICY_NAMES)

    def test_the_local_index_never_goes_in(self) -> None:
        from personalized_agent.index.build import build
        from personalized_agent.index.store import IndexStore, fts5_available
        if not fts5_available():
            self.skipTest("this Python's SQLite has no FTS5")
        store = IndexStore(self.dir / "files.db")
        try:
            build(store, [{"path": str(self.dir)}], never=privacy_folders.roots(self.config))
            kept = set(store.known_under(str(self.dir)))
            self.assertIn(str(self.open_file), kept)
            self.assertFalse([one for one in kept if privacy_folders.is_private(one, self.config)])
            self.assertFalse(store.search("harbour"))
            # A folder walked that is itself private: nothing kept of it.
            build(store, [{"path": str(self.secret)}], never=privacy_folders.roots(self.config))
            self.assertEqual(store.known_under(str(self.secret)), {})
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()

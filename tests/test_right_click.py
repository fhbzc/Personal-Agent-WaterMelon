"""RIGHT-CLICK, DELEGATE WORK (the user, 2026-11-01: "在任何一个网页，程序，我可
以直接右键，delegate work ... 或者选中一行字，context就是这个网址，尤其是这个字";
"对全平台，全工具支持"; "跳出来的delegate panel要显示当前点击的context").

What was right-clicked, kept as it was clicked; the browser extension and its
host; the box opened on it. Never the real registry, the real browsers'
folders or the clipboard: a fake registry and folders of the tests' own.
Every page and person here is made up."""

from __future__ import annotations

import io
import json
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from support import TemporaryAgent

from personalized_agent.agent.work import handover, picked
from personalized_agent.agent.work.delegate import instruction_of
from personalized_agent.platform_layer import browser_extension as extension
from personalized_agent.platform_layer import native_host

#: THE EXTENSION'S SOURCES are in the private repository alone (the user,
#: 2026-11-02): where they are not, the tests that read them are skipped.
HAS_SOURCE = extension.source().is_dir()

WEB = Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
SELECTED = {"where": "browser", "app": "Google Chrome", "url": "https://example.org/garden",
            "title": "Garden club - spring plans", "text": "Plant the tomatoes by May 10.",
            "around": "Spring plans. Plant the tomatoes by May 10. Bring gloves.",
            "page": "Garden club. Spring plans. Plant the tomatoes by May 10."}


class PickedTests(unittest.TestCase):

    def test_kept_as_it_was_clicked_and_its_kind_from_what_it_holds(self) -> None:
        one = picked.picked(SELECTED)
        self.assertEqual(one["kind"], "selection")
        self.assertEqual(one["text"], "Plant the tomatoes by May 10.")
        self.assertEqual(picked.picked({"link": "https://example.org/a"})["kind"], "link")
        self.assertEqual(picked.picked({"src": "https://example.org/a.png"})["kind"], "image")
        self.assertEqual(picked.picked({"url": "https://example.org"})["kind"], "page")
        self.assertEqual(picked.picked({"where": "nowhere"})["where"], "editor")

    def test_no_script_for_an_address_and_each_part_cut_to_its_length(self) -> None:
        one = picked.picked({"url": "javascript:alert(1)", "text": "x" * 50000, "line": "12"})
        self.assertNotIn("url", one)
        self.assertEqual(len(one["text"]), picked.TEXT_CHARS)
        self.assertEqual(one["line"], 12)

    def test_a_folder_right_clicked_is_a_folder(self) -> None:
        with tempfile.TemporaryDirectory() as there:
            one = picked.picked({"where": "files", "file": there})
            self.assertEqual(one["kind"], "folder")
            self.assertEqual(picked.as_draft(one)["folders"], [str(Path(there))])

    def test_the_work_it_makes_the_selected_text_first(self) -> None:
        made = picked.as_draft(picked.picked(SELECTED))
        self.assertEqual(made["text"], "Plant the tomatoes by May 10.")
        self.assertEqual(made["window"]["url"], "https://example.org/garden")
        self.assertIn("Bring gloves", made["window"]["text"], "the words around it")
        self.assertEqual(made["window"]["picked"]["kind"], "selection")
        told = "\n".join(picked.told(made["window"]["picked"]))
        self.assertIn("THE SELECTED TEXT - what the work is about above all", told)
        self.assertIn("its address: https://example.org/garden", told)

    def test_the_run_is_told_what_was_right_clicked(self) -> None:
        draft = {"text": "Plant the tomatoes by May 10.",
                 "window": picked.as_draft(picked.picked(SELECTED))["window"],
                 "options": {"instruction": "put it in my calendar"}}
        said = instruction_of(draft, {})
        self.assertIn("WHAT THE USER RIGHT-CLICKED: selected text, in Google Chrome", said)
        self.assertNotIn("WHAT THE USER HANDED OVER:", said, "said once, as it was clicked")


class NoteTests(unittest.TestCase):

    def test_a_note_of_its_own_taken_once(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            ident = handover.hand_over_picked(Path(data), SELECTED)
            taken = handover.take(Path(data), ident)
            self.assertEqual(taken["picked"]["title"], "Garden club - spring plans")
            self.assertIsNone(handover.take(Path(data), ident))


class TheBoxOpensOnItTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        from personalized_agent.api.routes import build_routes
        self.routes = build_routes(self.app)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_inbox_makes_a_work_of_what_was_right_clicked(self) -> None:
        from test_handed_ways import _Ask
        ident = handover.hand_over_picked(self.app.data_dir, SELECTED)
        draft = self.routes[("POST", "/api/work/drafts/inbox")](_Ask(id=ident))["draft"]
        self.assertEqual(draft["text"], "Plant the tomatoes by May 10.")
        self.assertEqual(draft["source"], "picked")
        self.assertEqual(draft["window"]["picked"]["url"], "https://example.org/garden")
        self.assertEqual(draft["status"], "writing", "the instruction is the user's first")


def _message(said) -> bytes:
    body = json.dumps(said).encode("utf-8")
    return struct.pack("=I", len(body)) + body


def _answers(raw: bytes):
    out, at = [], 0
    while at < len(raw):
        size = struct.unpack("=I", raw[at:at + 4])[0]
        out.append(json.loads(raw[at + 4:at + 4 + size]))
        at += 4 + size
    return out


class NativeHostTests(unittest.TestCase):

    def test_ping_and_a_delegate_kept_and_opened_apart(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            inbound = io.BytesIO(_message({"type": "ping"})
                                 + _message({"type": "delegate", "picked": SELECTED})
                                 + _message({"type": "something else"}))
            outbound = io.BytesIO()
            with mock.patch.object(native_host, "open_apart") as opened:
                self.assertEqual(native_host.serve(Path(data), ["agent"], inbound, outbound), 0)
            said = _answers(outbound.getvalue())
            self.assertTrue(said[0]["ok"] and said[0]["version"])
            self.assertEqual(said[1], {"ok": True})
            self.assertFalse(said[2]["ok"])
            ident = opened.call_args[0][1]
            self.assertEqual(opened.call_args[0][0], ["agent"])
            self.assertEqual(opened.call_args[0][2], "delegate")
            self.assertEqual(handover.take(Path(data), ident)["picked"]["kind"], "selection")

    def test_learn_by_the_icons_menu_and_a_browser_that_says_it_is_here(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            inbound = io.BytesIO(_message({"type": "here", "browser": "Brave"})
                                 + _message({"type": "learn", "picked": dict(
                                     SELECTED, app="Microsoft Edge")}))
            outbound = io.BytesIO()
            with mock.patch.object(native_host, "open_apart") as opened:
                native_host.serve(Path(data), ["agent"], inbound, outbound)
            self.assertEqual(_answers(outbound.getvalue()), [{"ok": True}, {"ok": True}])
            self.assertEqual(opened.call_args[0][2], "learn")
            self.assertEqual(extension.seen_processes(Path(data)), {"brave.exe", "msedge.exe"})

    def test_opened_apart_the_learn_box_by_its_own_flag(self) -> None:
        with mock.patch.object(native_host.subprocess, "Popen") as started:
            native_host.open_apart(["agent"], "abc123", "learn")
        self.assertEqual(started.call_args[0][0], ["agent", "--learn-note", "abc123"])

    def test_the_box_opened_by_the_agent_running_not_from_the_browsers_job(self) -> None:
        """The user, 2026-11-02: "弹出的委托面板是全黑的" - a window started from
        the host was ended with it, by the browser."""
        from personalized_agent import __main__ as entry
        with tempfile.TemporaryDirectory() as data:
            inbound = io.BytesIO(_message({"type": "delegate", "picked": SELECTED}))
            with mock.patch.object(entry, "_ask_agent_to_open", return_value=True) as asked, \
                    mock.patch.object(native_host, "open_apart") as apart, \
                    mock.patch("personalized_agent.common.paths.resolve_root",
                               return_value=Path(data)), \
                    mock.patch.object(native_host, "streams",
                                      return_value=(inbound, io.BytesIO())):
                entry._native_host(["--root", data])
            self.assertTrue(asked.call_args[0][1].startswith("/?solo=delegate&delegate_inbox="))
            apart.assert_not_called()
            inbound = io.BytesIO(_message({"type": "delegate", "picked": SELECTED}))
            with mock.patch.object(entry, "_ask_agent_to_open", return_value=False), \
                    mock.patch.object(native_host, "open_apart") as apart, \
                    mock.patch("personalized_agent.common.paths.resolve_root",
                               return_value=Path(data)), \
                    mock.patch.object(native_host, "streams",
                                      return_value=(inbound, io.BytesIO())):
                entry._native_host(["--root", data])
            apart.assert_called_once()                # no agent running: apart

    def test_the_agent_opens_a_notes_box_alone(self) -> None:
        agent = TemporaryAgent()
        try:
            from personalized_agent.api.errors import ApiError
            from personalized_agent.api.routes import build_routes
            from test_handed_ways import _Ask
            routes = build_routes(agent.app)
            with mock.patch("personalized_agent.platform_layer.app_window.open_page") as opened:
                said = routes[("POST", "/api/open_box")](
                    _Ask(fragment="/?solo=delegate&delegate_inbox=abc123"))
                for bad in ("https://example.org", "/?solo=delegate&delegate_inbox=../x",
                            "/#settings"):
                    with self.assertRaises(ApiError):
                        routes[("POST", "/api/open_box")](_Ask(fragment=bad))
            self.assertEqual(said, {"opened": True})
        finally:
            agent.cleanup()

    def test_the_browser_opens_the_box_at_once_on_a_note_it_names(self) -> None:
        """The user, 2026-11-02: the box "弹出来要卡一下" - the extension opens
        the box at the click on a note it names; the host keeps the note
        under that name and only says the box's address, signed in."""
        with tempfile.TemporaryDirectory() as data:
            asked = []

            def opener(ident, which, give=False):
                asked.append((ident, which, give))
                return "http://127.0.0.1:8756/api/access/sign-in?pa_sign_in=x" if give else ""

            said = native_host.answer({"type": "delegate", "picked": SELECTED, "opens": True,
                                       "id": "a1b2c3d4e5f60718"}, Path(data), opener)
            self.assertEqual(said["url"][:22], "http://127.0.0.1:8756/")
            self.assertEqual(asked, [("a1b2c3d4e5f60718", "delegate", True)])
            self.assertEqual(handover.take(Path(data), "a1b2c3d4e5f60718")["picked"]["text"],
                             SELECTED["text"])
            # A name not of letters and digits is not taken: one of its own.
            native_host.answer({"type": "delegate", "picked": SELECTED, "opens": True,
                                "id": "../../x"}, Path(data), opener)
            self.assertTrue(asked[-1][0].isalnum() and asked[-1][0] != "../../x")

    def test_the_box_waits_a_moment_for_its_note(self) -> None:
        import threading
        with tempfile.TemporaryDirectory() as data:
            self.assertIsNone(handover.take(Path(data), "notyet1234", wait=0.2))
            later = threading.Timer(0.3, lambda: handover.hand_over_picked(
                Path(data), SELECTED, "comeslater1"))
            later.start()
            said = handover.take(Path(data), "comeslater1", wait=5)
            later.join()
            self.assertEqual(said["picked"]["url"], SELECTED["url"])

    def test_the_agent_says_the_boxs_address_without_opening_it(self) -> None:
        agent = TemporaryAgent()
        try:
            from personalized_agent.api.routes import build_routes
            from test_handed_ways import _Ask
            routes = build_routes(agent.app)
            with mock.patch("personalized_agent.platform_layer.app_window.open_page") as page:
                said = routes[("POST", "/api/open_box")](
                    _Ask(fragment="/?solo=delegate&delegate_inbox=abc12345", give=True))
            page.assert_not_called()
            self.assertIn("/api/access/sign-in?", said["url"])
            self.assertIn("delegate_inbox%3Dabc12345", said["url"])
        finally:
            agent.cleanup()

    def test_a_message_too_large_is_refused(self) -> None:
        inbound = io.BytesIO(struct.pack("=I", native_host.MOST_BYTES + 1))
        outbound = io.BytesIO()
        self.assertEqual(native_host.serve(Path("."), ["agent"], inbound, outbound), 1)
        self.assertFalse(_answers(outbound.getvalue())[0]["ok"])

    def test_the_browser_starts_it_with_its_own_arguments(self) -> None:
        from personalized_agent import __main__ as entry
        with mock.patch.object(entry, "_native_host", return_value=0) as host:
            self.assertEqual(entry.main(["--root", "R", "--native-host",
                                         "chrome-extension://abc/"]), 0)
        self.assertEqual(host.call_args[0][0], ["--root", "R"])
        self.assertEqual(entry._root_of(["--root", "R"]), Path("R"))


class _Registry:
    def __init__(self) -> None:
        self.keys = {}

    def set(self, key, value):
        self.keys[key] = value

    def get(self, key):
        return self.keys.get(key)

    def delete(self, key):
        self.keys.pop(key, None)


class ExtensionTests(unittest.TestCase):

    def setUp(self) -> None:
        self._data = tempfile.TemporaryDirectory()
        self.data = Path(self._data.name)
        # Never this computer's own browsers.
        homes = mock.patch.object(extension, "_browser_homes", return_value=[])
        self.homes = homes.start()
        self.addCleanup(homes.stop)

    def tearDown(self) -> None:
        self._data.cleanup()

    def _heard(self, *names: str) -> None:
        extension.folder(self.data).mkdir(parents=True, exist_ok=True)
        (extension.folder(self.data) / "store_seen.json").write_text(
            json.dumps({name: time.time() for name in names}), encoding="utf-8")

    def test_removed_from_its_browser_it_is_not_installed(self) -> None:
        """A removed extension says nothing: its folder is looked for."""
        self._heard("Google Chrome", "Firefox")
        browser = self.data / "browser"
        self.homes.return_value = [browser]
        store = browser / "Default" / "Extensions" / extension.STORE_IDS[0]
        store.mkdir(parents=True)
        (browser / "p" / "extensions").mkdir(parents=True)
        (browser / "p" / "extensions" / f"{extension.FIREFOX_ID}.xpi").write_text("x")
        rows = {one["key"]: one for one in extension.browsers(self.data)}
        self.assertEqual(rows["chrome"]["state"], "installed")
        self.assertEqual(rows["firefox"]["state"], "installed")
        store.rmdir()
        (browser / "p" / "extensions" / f"{extension.FIREFOX_ID}.xpi").unlink()
        rows = {one["key"]: one for one in extension.browsers(self.data)}
        self.assertEqual(rows["chrome"]["state"], "absent")
        self.assertEqual(rows["firefox"]["state"], "absent")

    def test_no_folders_to_look_in_and_its_word_stands(self) -> None:
        self._heard("Google Chrome")
        rows = {one["key"]: one for one in extension.browsers(self.data)}
        self.assertEqual(rows["chrome"]["state"], "installed")

    def test_its_id_is_fixed_by_its_key(self) -> None:
        self.assertEqual(extension.CHROMIUM_ID, "ppndbddcegnoelabjaacahmkcnmodddp")

    @unittest.skipUnless(HAS_SOURCE, "the extension's sources are in the private repository alone")
    def test_its_folders_one_for_chromium_one_for_firefox(self) -> None:
        from PIL import Image
        folders = extension.prepare(self.data, "0.2.16", Image.new("RGBA", (128, 128)))
        chromium = json.loads((Path(folders["chromium"]) / "manifest.json").read_text("utf-8"))
        firefox = json.loads((Path(folders["firefox"]) / "manifest.json").read_text("utf-8"))
        self.assertEqual(chromium["background"], {"service_worker": "background.js"})
        self.assertEqual(chromium["key"], extension.KEY)
        self.assertEqual(firefox["background"], {"scripts": ["background.js"]})
        self.assertEqual(firefox["browser_specific_settings"]["gecko"]["id"],
                         extension.FIREFOX_ID)
        # Its own version, not the agent's (the user, 2026-11-02).
        self.assertEqual(chromium["version"], extension.EXTENSION_VERSION)
        self.assertRegex(extension.EXTENSION_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertIn("nativeMessaging", chromium["permissions"])
        # A script in every page, for what can be seen of it (the user, 2026-11-05).
        for one in (chromium, firefox):
            self.assertEqual(one["content_scripts"][0]["js"], ["visible.js"])
            self.assertNotIn("<all_urls>", one["content_scripts"][0]["matches"])
            self.assertNotIn("host_permissions", one)
        for flavor in folders.values():
            for name in ("background.js", "help.html", "help.js", "icons/128.png",
                         "_locales/en/messages.json", "_locales/zh_CN/messages.json"):
                self.assertTrue((Path(flavor) / name).exists(), name)
        words = json.loads((Path(folders["chromium"]) / "_locales/zh_CN/messages.json")
                           .read_text("utf-8"))
        self.assertEqual(words["delegate"]["message"], "委托工作")
        self.assertEqual(set(extension.WORDS), {"en", "zh"})
        for flavor in ("chromium", "firefox"):
            self.assertEqual(extension.manifest(flavor)["incognito"], "not_allowed")
        for words in extension.WORDS.values():
            self.assertEqual(set(words), set(extension.WORDS["en"]))
        # THE BOX AT ONCE (the user, 2026-11-02): only the agent's own page
        # may ask the extension for its sign-in; Firefox's opening window.
        self.assertEqual(chromium["externally_connectable"]["matches"],
                         ["http://127.0.0.1/*", "http://localhost/*"])
        for name in ("opening.html", "opening.js"):
            self.assertTrue((Path(folders["firefox"]) / name).exists(), name)

    @unittest.skipUnless(HAS_SOURCE, "the extension's sources are in the private repository alone")
    def test_the_stores_packages(self) -> None:
        """The user, 2026-11-02: "在chrome和firefox上都release 这个extension"."""
        import zipfile
        from unittest import mock
        from PIL import Image
        made = extension.package(self.data / "out", "0.2.20", Image.new("RGBA", (128, 128)))
        with zipfile.ZipFile(made["chromium"]) as box:
            chromium = json.loads(box.read("manifest.json"))
            names = set(box.namelist())
        self.assertNotIn("key", chromium, "a store takes no key: it gives its own id")
        self.assertIn("background.js", names)
        self.assertIn("icons/128.png", names)
        with zipfile.ZipFile(made["firefox"]) as box:
            firefox = json.loads(box.read("manifest.json"))
        gecko = firefox["browser_specific_settings"]["gecko"]
        self.assertEqual(gecko["id"], extension.FIREFOX_ID)
        self.assertEqual(gecko["data_collection_permissions"], {"required": ["none"]},
                         "nothing collected: what is right-clicked goes to the agent here")
        self.assertFalse((self.data / "out" / "staging").exists(), "no copy left behind")
        # The store's own id answered by the host, beside the unpacked one's.
        with mock.patch.object(extension, "STORE_IDS", ("a" * 32,)):
            host = extension.host_manifest("chromium", Path("launch.cmd"))
        self.assertEqual(host["allowed_origins"],
                         [f"chrome-extension://{extension.CHROMIUM_ID}/",
                          f"chrome-extension://{'a' * 32}/"])

    @unittest.skipUnless(HAS_SOURCE, "the extension's sources are in the private repository alone")
    def test_the_box_is_opened_at_the_click_waiting_for_nothing(self) -> None:
        """Measured in a real browser (2026-11-02): reading the extension's
        storage took Edge three seconds; the host's start half a second. The
        window is asked for before anything is awaited but where the right-
        click's window is, and the host is started at the click."""
        code = (extension.source() / "background.js").read_text(encoding="utf-8")
        clicked = code[code.index("async function delegateClicked("):]
        before = clicked[:clicked.index("popup(")]
        self.assertNotIn("storage", before)
        self.assertEqual(before.count("await"), 1)            # boxBounds alone
        self.assertLess(clicked.index("startHost()"), clicked.index("popup("))
        self.assertLess(clicked.index("popup("), clicked.index("await pageWords("))
        self.assertIn("delegate_inbox=${id}", clicked)
        self.assertNotIn("sendNativeMessage", clicked)
        page = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn('runtime.sendMessage(FROM_EXTENSION.ext, { type: "sign-in"', page)

    def test_windows_the_users_part_of_the_registry_named_and_taken_out(self) -> None:
        registry = _Registry()
        said = extension.install(self.data, ["C:/A/agent.exe", "--root", "C:/A 1"], "0.2.16",
                                 platform="win32", registry=registry)
        self.assertIn("Google Chrome", said["connected"])
        self.assertIn("Firefox", said["connected"])
        key = r"Software\Google\Chrome\NativeMessagingHosts" + "\\" + extension.HOST
        host = json.loads(Path(registry.keys[key]).read_text("utf-8"))
        self.assertEqual(host["allowed_origins"],
                         [f"chrome-extension://{one}/"
                          for one in (extension.CHROMIUM_ID, *extension.STORE_IDS)])
        launcher = Path(host["path"]).read_text("utf-8")
        self.assertIn('"C:/A 1" --native-host %*', launcher)
        firefox = json.loads(Path(registry.keys[r"Software\Mozilla\NativeMessagingHosts"
                                                + "\\" + extension.HOST]).read_text("utf-8"))
        self.assertEqual(firefox["allowed_extensions"], [extension.FIREFOX_ID])
        extension.remove(self.data, platform="win32", registry=registry)
        self.assertEqual(registry.keys, {})
        self.assertFalse(extension.folder(self.data).exists())

    def test_mac_and_linux_only_the_browsers_on_this_computer(self) -> None:
        with tempfile.TemporaryDirectory() as home_dir:
            home = Path(home_dir)
            (home / ".config/google-chrome").mkdir(parents=True)
            (home / ".mozilla").mkdir()
            known = extension.register(self.data, ["/opt/agent"], "linux", home)
            self.assertEqual(known, ["Google Chrome", "Firefox"])
            self.assertTrue((home / ".mozilla/native-messaging-hosts"
                             / f"{extension.HOST}.json").exists())
            launcher = (extension.folder(self.data) / "host" / "native_host.sh").read_text()
            self.assertIn("exec /opt/agent --native-host \"$@\"", launcher)
            self.assertEqual(extension.connected(self.data, "linux", home), known)
            extension.remove(self.data, "linux", home)
            self.assertEqual(extension.connected(self.data, "linux", home), [])
            # NOT EDGE (the user, 2026-11-02): never made known to it; an
            # earlier version's there, taken out by remove.
            edge = home / "Library/Application Support/Microsoft Edge"
            edge.mkdir(parents=True)
            self.assertEqual(extension.register(self.data, ["/a"], "darwin", home), [])
            old = edge / "NativeMessagingHosts" / f"{extension.HOST}.json"
            old.parent.mkdir(parents=True)
            old.write_text("{}", encoding="utf-8")
            extension.remove(self.data, "darwin", home)
            self.assertFalse(old.exists())

    def test_installed_from_the_stores_not_edge(self) -> None:
        """The user, 2026-11-02: "install调用的是注册好的链接，而不是本地链接";
        Edge no longer."""
        self.assertEqual(extension.STORES["chrome"],
                         "https://chromewebstore.google.com/detail/ikdgdhidoljaomoicjlfdchckaklbcbh")
        self.assertIn(extension.FIREFOX_ID, extension.STORES["firefox"])
        names = [one[0] for one in extension.WINDOWS_KEYS + extension.MAC_FOLDERS
                 + extension.LINUX_FOLDERS]
        self.assertNotIn("Microsoft Edge", names)
        registry = _Registry()
        registry.keys[extension.LEGACY_EDGE["win32"] + "\\" + extension.HOST] = "old.json"
        extension.remove(self.data, platform="win32", registry=registry)
        self.assertEqual(registry.keys, {}, "an earlier version's Edge key taken out")

    def test_two_rows_installed_by_the_extensions_own_word(self) -> None:
        """The user, 2026-11-02: "直接就应该列出2个版本(chrome, firefox), 然后检查
        状态是否安装 ... refresh 按钮，安装了之后能再detect"."""
        import time
        rows = {one["key"]: one for one in extension.browsers(self.data)}
        self.assertEqual(list(rows), ["chrome", "firefox"])
        self.assertFalse(rows["chrome"]["installed"])
        self.assertEqual(rows["firefox"]["store"], extension.STORES["firefox"])
        extension.mark_seen(self.data, "Brave")            # Chrome's store serves Brave too
        rows = {one["key"]: one for one in extension.browsers(self.data)}
        self.assertTrue(rows["chrome"]["installed"])
        self.assertFalse(rows["firefox"]["installed"])
        # Not heard from for long: not taken to be there.
        seen = extension.folder(self.data) / "store_seen.json"
        seen.write_text(json.dumps({"Firefox": time.time() - (extension.SEEN_DAYS + 1) * 86400}),
                        encoding="utf-8")
        rows = {one["key"]: one for one in extension.browsers(self.data)}
        self.assertFalse(rows["firefox"]["installed"])
        self.assertIn("browsers", extension.status(self.data, platform="win32",
                                                   registry=_Registry()))

    def test_only_the_stores_extension_counts_as_installed(self) -> None:
        """The user, 2026-11-02: "应该只check browser public 版本的install, 你现在
        check应该是local也算了"."""
        store = f"chrome-extension://{extension.STORE_IDS[0]}/"
        unpacked = f"chrome-extension://{extension.CHROMIUM_ID}/"
        self.assertTrue(extension.from_store(store))
        self.assertFalse(extension.from_store(unpacked))
        # Its own word, where it gives it (0.0.2), comes first.
        self.assertFalse(extension.from_store(store, "development"))
        self.assertTrue(extension.from_store(extension.FIREFOX_ID, "normal"))
        self.assertFalse(extension.from_store(extension.FIREFOX_ID, "development"))
        # Said by a hand-loaded one: its icon is the extension's, but no row counts it.
        native_host.answer({"type": "here", "browser": "Google Chrome"}, self.data,
                           lambda *a: "", caller=unpacked)
        rows = {one["key"]: one for one in extension.browsers(self.data)}
        self.assertEqual(rows["chrome"]["state"], "absent")
        self.assertIn("chrome.exe", extension.seen_processes(self.data))
        native_host.answer({"type": "here", "browser": "Google Chrome"}, self.data,
                           lambda *a: "", caller=store)
        rows = {one["key"]: one for one in extension.browsers(self.data)}
        self.assertEqual(rows["chrome"]["state"], "installed")

    def test_heard_from_once_and_not_since_paused_or_removed(self) -> None:
        """The user, 2026-11-02: "extension 如果被暂停了，也要提醒用户"."""
        import time
        old = time.time() - (extension.SEEN_DAYS + 3) * 86400
        path = extension.folder(self.data) / "store_seen.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"Firefox": old}), encoding="utf-8")
        rows = {one["key"]: one for one in extension.browsers(self.data)}
        self.assertEqual(rows["firefox"]["state"], "quiet")
        self.assertFalse(rows["firefox"]["installed"])
        self.assertEqual(rows["chrome"]["state"], "absent")
        page = (WEB / "app.js").read_text(encoding="utf-8")
        self.assertIn("if you use this browser, it may be paused (after an update) or removed.", page)
        self.assertIn('data-extension-page="', page)
        self.assertEqual(extension.EXTENSIONS_PAGES["firefox_addons"], "about:addons")

    @unittest.skipUnless(HAS_SOURCE, "the extension's sources are in the private repository alone")
    def test_it_says_how_it_was_installed(self) -> None:
        code = (extension.source() / "background.js").read_text(encoding="utf-8")
        here = code[code.index("function sayHere()"):code.index("function menus()")]
        self.assertIn("management.getSelf", here)
        self.assertIn("install: (self && self.installType)", here)
        self.assertNotIn('"management"', json.dumps(extension.manifest("chromium")),
                         "no permission asked for it: an update asking more is paused")
        # Every six hours while the browser runs - "alarms", shown to no one.
        self.assertIn('ext.alarms.create("here", { periodInMinutes: HERE_EVERY_MINUTES })', code)
        self.assertIn("alarms", extension.manifest("chromium")["permissions"])
        self.assertGreaterEqual(tuple(int(one) for one in extension.EXTENSION_VERSION.split(".")),
                                (0, 0, 2))

    def test_the_host_is_told_who_calls(self) -> None:
        from personalized_agent import __main__ as entry
        with mock.patch.object(entry, "_root_of", return_value=None),                 mock.patch("personalized_agent.common.paths.AppPaths.create",
                           return_value=mock.Mock(data_dir=self.data)),                 mock.patch("personalized_agent.common.paths.resolve_root", return_value=self.data),                 mock.patch("personalized_agent.platform_layer.native_host.serve",
                           return_value=0) as served:
            entry._native_host([], [f"chrome-extension://{extension.STORE_IDS[0]}/",
                                    "--parent-window=0"])
        self.assertEqual(served.call_args.kwargs["caller"],
                         f"chrome-extension://{extension.STORE_IDS[0]}/")

    def test_install_for_one_browser_opens_its_store(self) -> None:
        agent = TemporaryAgent()
        try:
            from personalized_agent.api.routes import build_routes
            from test_handed_ways import _Ask
            routes = build_routes(agent.app)
            with mock.patch.object(extension, "Registry", return_value=_Registry()),                     mock.patch.object(extension.Path, "home", return_value=self.data),                     mock.patch.object(extension, "open_store", return_value=True) as opened:
                said = routes[("POST", "/api/extension/install")](_Ask(browser="firefox"))
            opened.assert_called_once_with("firefox")
            self.assertTrue(said["opened"])
            self.assertEqual([one["key"] for one in said["browsers"]], ["chrome", "firefox"])
        finally:
            agent.cleanup()

    def test_the_chrome_stores_id_is_answered(self) -> None:
        """Given by the store, 2026-11-02."""
        self.assertIn("ikdgdhidoljaomoicjlfdchckaklbcbh", extension.STORE_IDS)
        host = extension.host_manifest("chromium", Path("launch.cmd"))
        self.assertIn("chrome-extension://ikdgdhidoljaomoicjlfdchckaklbcbh/",
                      host["allowed_origins"])

    def test_kept_current_at_each_start_where_installed_only(self) -> None:
        """A host made known before the store's id did not name it: written
        again at the start, where it is - the registry itself untouched."""
        registry = _Registry()
        command = ["C:/A/agent.exe", "--root", "C:/A 1"]
        # Never installed: nothing at all.
        self.assertEqual(extension.keep_current(self.data, command, "0.2.20",
                                                platform="win32", registry=registry), [])
        self.assertFalse(extension.folder(self.data).exists())
        self.assertEqual(registry.keys, {})
        extension.install(self.data, command, "0.2.16", platform="win32", registry=registry)
        self.assertFalse((extension.folder(self.data) / "chromium").exists(),
                         "the store gives the extension: no folder made here")
        if HAS_SOURCE:
            extension.prepare(self.data, "0.2.16")          # a developer's, loaded by hand
        manifest_file = extension.folder(self.data) / "host" / f"{extension.HOST}.chromium.json"
        old = json.loads(manifest_file.read_text("utf-8"))
        old["allowed_origins"] = [f"chrome-extension://{extension.CHROMIUM_ID}/"]
        manifest_file.write_text(json.dumps(old), encoding="utf-8")       # as before the store
        keys_before = dict(registry.keys)

        class _Watched(_Registry):
            def set(self, key, value):
                raise AssertionError("the registry is not written again")
        watched = _Watched()
        watched.keys = registry.keys
        known = extension.keep_current(self.data, command, "0.2.20", platform="win32",
                                       registry=watched)
        self.assertIn("Google Chrome", known)
        self.assertEqual(registry.keys, keys_before)
        now = json.loads(manifest_file.read_text("utf-8"))
        self.assertIn("chrome-extension://ikdgdhidoljaomoicjlfdchckaklbcbh/", now["allowed_origins"])
        if not HAS_SOURCE:
            return
        chromium = json.loads((extension.folder(self.data) / "chromium" / "manifest.json")
                              .read_text("utf-8"))
        self.assertEqual(chromium["version"], extension.EXTENSION_VERSION,
                         "its folders made again - its own version, whatever the agent's")

    def test_kept_current_elsewhere_only_in_the_folders_it_is_in(self) -> None:
        home = self.data / "home"
        (home / ".config/google-chrome").mkdir(parents=True)
        (home / ".config/chromium").mkdir(parents=True)
        command = ["/opt/agent/agent"]
        extension.install(self.data, command, "0.2.16", platform="linux", home=home)
        chromium_host = home / ".config/chromium/NativeMessagingHosts" / f"{extension.HOST}.json"
        chromium_host.unlink()                       # taken out by the user
        extension.keep_current(self.data, command, "0.2.20", platform="linux", home=home)
        self.assertFalse(chromium_host.exists(), "not put back where it is not")
        chrome = json.loads((home / ".config/google-chrome/NativeMessagingHosts"
                             / f"{extension.HOST}.json").read_text("utf-8"))
        self.assertIn("chrome-extension://ikdgdhidoljaomoicjlfdchckaklbcbh/",
                      chrome["allowed_origins"])

    def test_settings_install_on_the_users_word(self) -> None:
        agent = TemporaryAgent()
        try:
            from personalized_agent.api.routes import build_routes
            routes = build_routes(agent.app)
            from test_handed_ways import _Ask
            registry = _Registry()
            if sys.platform == "darwin":
                # A Mac connects the browsers whose folder there is.
                (self.data / "Library/Application Support/Google/Chrome").mkdir(parents=True)
            with mock.patch.object(extension, "Registry", return_value=registry), \
                    mock.patch.object(extension.Path, "home", return_value=self.data):
                self.assertFalse(routes[("GET", "/api/extension")](_Ask())["prepared"])
                said = routes[("POST", "/api/extension/install")](_Ask())
                self.assertTrue(said["prepared"])
                self.assertTrue(said["connected"])
                self.assertFalse(said["folders"], "nothing made here: the stores give it")
                with mock.patch.object(extension, "_browser_program", return_value=None), \
                        mock.patch("webbrowser.open", return_value=True) as opened:
                    routes[("POST", "/api/extension/open")](_Ask(what="store", browser="firefox"))
                    routes[("POST", "/api/extension/open")](_Ask(what="store", browser="chrome"))
                self.assertEqual([one.args[0] for one in opened.call_args_list],
                                 [extension.STORES["firefox"], extension.STORES["chrome"]])
                routes[("POST", "/api/extension/remove")](_Ask())
            self.assertEqual(registry.keys, {})
        finally:
            agent.cleanup()


class FromAFileTests(unittest.TestCase):
    """JetBrains names only where the selection is: it is read off the file."""

    def test_the_selection_read_off_the_file_whatever_its_line_ends(self) -> None:
        with tempfile.TemporaryDirectory() as there:
            path = Path(there) / "notes.py"
            path.write_bytes(b"first line\r\nsecond line here\r\nthird\r\n")
            one = picked.from_file(str(path), "PyCharm", ["2", "2"], ["8", "17"])
            self.assertEqual(one["text"], "line here")
            self.assertEqual((one["kind"], one["line"], one["app"]), ("selection", 2, "PyCharm"))
            self.assertIn("first line", one["around"])
            across = picked.from_file(str(path), "", ["1", "2"], ["7", "7"])
            self.assertEqual(across["text"], "line\nsecond")
            caret = picked.from_file(str(path), "", ["3", "3"], ["2", "2"])
            self.assertEqual(caret["kind"], "file", "no selection where the two are the same")
            self.assertNotIn("text", caret)


class EditorTests(unittest.TestCase):

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.home = Path(self._home.name)
        self.app_data = self.home / "AppData"
        self.patch = mock.patch.dict("os.environ", {"APPDATA": str(self.app_data),
                                                    "XDG_CONFIG_HOME": str(self.app_data)})
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self._home.cleanup()

    def test_vscode_packed_for_its_own_installer(self) -> None:
        import zipfile
        from personalized_agent.platform_layer import editor_menus
        vsix = editor_menus.build_vsix(self.home, ["C:/A/agent.exe"], "0.2.16")
        with zipfile.ZipFile(vsix) as pack:
            names = set(pack.namelist())
            package = json.loads(pack.read("extension/package.json"))
            agent = json.loads(pack.read("extension/agent.json"))
        self.assertTrue({"[Content_Types].xml", "extension.vsixmanifest",
                         "extension/extension.js"} <= names)
        self.assertEqual(agent["command"], ["C:/A/agent.exe"])
        menus = package["contributes"]["menus"]
        self.assertEqual(set(menus), {"editor/context", "editor/title/context",
                                      "explorer/context", "commandPalette"})
        self.assertEqual(package["version"], "0.2.16")

    def test_vscode_installed_by_its_own_command(self) -> None:
        from personalized_agent.platform_layer import editor_menus
        ran = []

        def run(argv, **_how):
            ran.append(argv)
            return mock.Mock(returncode=0, stderr="")
        which = lambda name: "/bin/cursor" if name == "cursor" else None  # noqa: E731
        found = editor_menus.programs("linux", self.home, which)
        self.assertEqual([one["name"] for one in found], ["Cursor"])
        said = editor_menus.install("vscode:Cursor", self.home, ["/opt/agent"], "0.2.16",
                                    "linux", self.home, which, run)
        self.assertEqual(said["installed"], "Cursor")
        self.assertEqual(ran[0][:2], ["/bin/cursor", "--install-extension"])
        (self.home / ".cursor/extensions" / (editor_menus.VSCODE_ID + "-0.2.16")).mkdir(
            parents=True)
        self.assertTrue(editor_menus.programs("linux", self.home, which)[0]["installed"])

    def test_sublime_and_jetbrains_found_put_in_and_taken_out(self) -> None:
        from personalized_agent.platform_layer import editor_menus
        (self.app_data / "Sublime Text" / "Packages").mkdir(parents=True)
        (self.app_data / "JetBrains" / "PyCharm2025.2").mkdir(parents=True)
        (self.app_data / "JetBrains" / "consentOptions").mkdir(parents=True)
        nothing = lambda name: None  # noqa: E731
        found = {one["kind"]: one for one in editor_menus.programs("win32", self.home, nothing)}
        self.assertEqual(set(found), {"sublime", "jetbrains"})
        command = ["C:/Program Files/A/agent.exe", "--root", "C:/A"]
        from PIL import Image
        editor_menus.install(found["sublime"]["key"], self.home, command, "1", "win32",
                             self.home, nothing, icon=Image.new("RGBA", (128, 128)),
                             words={"delegate": "委托工作", "learn": "学习"})
        package = self.app_data / "Sublime Text" / "Packages" / editor_menus.SUBLIME_PACKAGE
        self.assertEqual(json.loads((package / "agent.json").read_text("utf-8"))["command"],
                         command)
        menu = json.loads((package / "Context.sublime-menu").read_text("utf-8"))
        self.assertEqual(menu[-1]["command"], "personal_agent_delegate")
        self.assertEqual(menu[-1]["caption"], "委托工作")
        self.assertTrue((package / "icon.png").exists(), "the icon beside selected text")
        editor_menus.install(found["jetbrains"]["key"], self.home, command, "1", "win32",
                             self.home, nothing)
        tools = (self.app_data / "JetBrains" / "PyCharm2025.2" / "tools"
                 / editor_menus.JETBRAINS_TOOLS)
        from xml.etree import ElementTree
        tool = ElementTree.fromstring(tools.read_text("utf-8")).find("tool")
        self.assertEqual(tool.get("showInEditor"), "true")
        options = {one.get("name"): one.get("value") for one in tool.iter("option")}
        self.assertEqual(options["COMMAND"], "C:/Program Files/A/agent.exe")
        self.assertIn('--delegate-file "$FilePath$"', options["PARAMETERS"])
        self.assertTrue(all(one["installed"] for one in
                            editor_menus.programs("win32", self.home, nothing)))
        for one in found.values():
            editor_menus.remove(one["key"], "win32", self.home, nothing)
        self.assertFalse(package.exists() or tools.exists())
        self.assertTrue((self.app_data / "JetBrains" / "PyCharm2025.2").exists(),
                        "the IDE's own settings left alone")


class SystemMenuTests(unittest.TestCase):

    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.home = Path(self._home.name)
        self.patch = mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": "", "XDG_DATA_HOME": ""})
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self._home.cleanup()

    def test_mac_services_for_text_in_any_program_and_files_in_finder(self) -> None:
        import plistlib
        from personalized_agent.platform_layer import system_menus
        made = system_menus.install_mac(self.home, ["/Applications/A.app/Contents/MacOS/a"],
                                        "zh", refresh=False)
        self.assertEqual(len(made), 2)
        text = Path(made[0]) / "Contents"
        info = plistlib.loads((text / "Info.plist").read_bytes())["NSServices"][0]
        self.assertEqual(info["NSMenuItem"]["default"], "委托工作")
        self.assertEqual(info["NSSendTypes"], ["public.utf8-plain-text"])
        flow = plistlib.loads((text / "document.wflow").read_bytes())
        script = flow["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
        self.assertIn("--delegate-text -", script)
        files = plistlib.loads((Path(made[1]) / "Contents" / "Info.plist").read_bytes())
        self.assertEqual(files["NSServices"][0]["NSRequiredContext"],
                         {"NSApplicationIdentifier": "com.apple.finder"})
        system_menus.remove_mac(self.home)
        self.assertFalse(any(Path(one).exists() for one in made))

    def test_linux_each_file_manager_here_and_theirs_kept(self) -> None:
        from personalized_agent.platform_layer import system_menus
        (self.home / ".config" / "Thunar").mkdir(parents=True)
        (self.home / ".config" / "Thunar" / "uca.xml").write_text(
            "<?xml version='1.0'?><actions><action><unique-id>theirs</unique-id>"
            "<name>Open a terminal here</name></action></actions>", encoding="utf-8")
        (self.home / ".local" / "share" / "nautilus").mkdir(parents=True)
        data = self.home / "data"
        which = lambda name: "/usr/bin/dolphin" if name == "dolphin" else None  # noqa: E731
        made = system_menus.install_linux(self.home, data, ["/opt/agent"], "en", which)
        self.assertEqual(set(made), {"nautilus", "dolphin", "thunar"})
        script = (self.home / ".local/share/nautilus/scripts/Delegate work").read_text()
        self.assertIn("system_menus/delegate.sh", script.replace("\\", "/"))
        self.assertIn("--delegate", (data / "system_menus" / "delegate.sh").read_text())
        uca = (self.home / ".config/Thunar/uca.xml").read_text("utf-8")
        self.assertIn("theirs", uca)
        self.assertIn(system_menus.THUNAR_ID, uca)
        system_menus.remove_linux(self.home, data, which)
        uca = (self.home / ".config/Thunar/uca.xml").read_text("utf-8")
        self.assertIn("theirs", uca)
        self.assertNotIn(system_menus.THUNAR_ID, uca)
        self.assertEqual(list((self.home / ".local/share/nautilus/scripts").iterdir()), [])
        self.assertFalse((self.home / ".local/share/kio/servicemenus"
                          / f"{system_menus.NAME}.desktop").exists())


class RightClickOnSelectedTextTests(unittest.TestCase):
    """The user, 2026-11-01: "我现在在记事本里选中右键没有delegate呀" - on
    Windows a program's own menu cannot be added to from outside, so the
    button comes beside it, to its left."""

    def test_a_right_click_on_selected_text_brings_the_button(self) -> None:
        from personalized_agent.ui import selection
        seen = []
        watch = selection.SelectionWatch(on_selection=seen.append)
        with mock.patch.object(selection, "_cursor", return_value=(500, 300)), \
                mock.patch.object(selection.SelectionWatch, "_selected",
                                  return_value={"text": "Plant the tomatoes", "title": "notes",
                                                "app": "Notepad.exe"}), \
                mock.patch.object(selection.time, "sleep"):
            watch._right_clicked(None, None)
        self.assertEqual(seen[0]["text"], "Plant the tomatoes")
        self.assertEqual((seen[0]["x"], seen[0]["y"], seen[0]["right"]), (500, 300, True))
        with mock.patch.object(selection, "_cursor", return_value=(1, 1)), \
                mock.patch.object(selection.SelectionWatch, "_selected", return_value=None), \
                mock.patch.object(selection.time, "sleep"):
            watch._right_clicked(None, None)
        self.assertEqual(len(seen), 1, "nothing selected: no button")

    def test_the_button_left_of_the_programs_menu(self) -> None:
        import gc
        import tkinter as tk
        from types import SimpleNamespace
        from personalized_agent.ui.desktop import DesktopAgent
        root = tk.Tk()
        root.withdraw()
        try:
            chosen = []
            probe = SimpleNamespace(root=root, _popup_font=lambda: ("Segoe UI", 10),
                                    _delegate_face=lambda *size: None, _words=lambda w: w,
                                    _delegate_selection=lambda said: chosen.append("delegate"),
                                    _learn_selection=lambda said: chosen.append("learn"),
                                    ICON_SIZE=24, ICON_SECONDS=6, MENU_SECONDS=12)
            for name in ("_hide_delegate_button", "_place_beside_selection",
                         "_open_selection_menu"):
                setattr(probe, name, getattr(DesktopAgent, name).__get__(probe))
            with mock.patch("personalized_agent.ui.selection.no_activate"):
                DesktopAgent._show_delegate_button(probe, {"text": "x", "x": 600, "y": 400,
                                                           "right": True})
                window = probe._delegate_button
                window.update_idletasks()
                self.assertLess(window.winfo_x() + window.winfo_reqwidth(), 600)
                self.assertEqual(len(window.winfo_children()), 1, "the icon alone, first")
                window.winfo_children()[0].invoke()
                menu = probe._delegate_button.winfo_children()
                self.assertEqual([one.cget("text") for one in menu], ["Delegate work", "Learn"])
                probe._delegate_button.update_idletasks()
                self.assertLess(probe._delegate_button.winfo_x()
                                + probe._delegate_button.winfo_reqwidth(), 600,
                                "the menu too, clear of the program's own")
                menu[0].invoke()
                self.assertEqual(chosen, ["delegate"])
                DesktopAgent._show_delegate_button(probe, {"text": "x", "x": 600, "y": 400})
                self.assertGreater(probe._delegate_button.winfo_x(), 600, "a drag: below right")
            DesktopAgent._hide_delegate_button(probe)
        finally:
            root.destroy()
            gc.collect()


class TextIsTheIconsTests(unittest.TestCase):
    """The user, 2026-11-01: "所有text，包括网页选择都是这个形式，只有直接在网址或
    者文件里才是右键delegate work"."""

    def _source(self, *parts) -> str:
        return (Path(__file__).resolve().parents[1] / "personalized_agent" / "resources"
                / Path(*parts)).read_text("utf-8")

    @unittest.skipUnless(HAS_SOURCE, "the extension's sources are in the private repository alone")
    def test_in_a_page_selected_text_is_the_right_clicks_too(self) -> None:
        # The user, 2026-11-01: "网页选字什么和网址一样，都是右键delegate work".
        background = self._source("browser_extension", "background.js")
        contexts = background[background.index("contexts:"):].split("]")[0]
        self.assertIn('"selection"', contexts)
        self.assertIn('"page"', contexts)
        self.assertFalse((Path(__file__).resolve().parents[1] / "personalized_agent"
                          / "resources" / "browser_extension" / "content.js").exists())

    def test_vscode_the_light_bulb_for_text_the_menus_for_the_file(self) -> None:
        from personalized_agent.platform_layer import editor_menus
        package = editor_menus.vscode_package(["agent"], "1.0.0")
        self.assertEqual(package["activationEvents"], ["onStartupFinished"])
        self.assertEqual(package["contributes"]["menus"]["commandPalette"][0]["when"], "false")
        script = self._source("editor_menus", "vscode", "extension.js")
        self.assertIn("registerCodeActionsProvider", script)
        self.assertIn('"--learn-picked"', script)

    def test_jetbrains_hands_over_the_file_itself(self) -> None:
        from personalized_agent.platform_layer import editor_menus
        self.assertNotIn("Selection", editor_menus.jetbrains_tools(["agent"]))

    def test_the_desktop_icon_leaves_a_browser_with_the_extension_to_it(self) -> None:
        from types import SimpleNamespace
        from personalized_agent.ui.desktop import DesktopAgent
        with tempfile.TemporaryDirectory() as data:
            extension.mark_seen(Path(data), "Google Chrome")
            probe = SimpleNamespace(app=SimpleNamespace(data_dir=Path(data)),
                                    _hide_delegate_button=lambda: None)
            with mock.patch("tkinter.Toplevel") as made:
                DesktopAgent._show_delegate_button(probe, {"text": "x", "app": "chrome.exe"})
            made.assert_not_called()
            extension.mark_seen(Path(data), "Not a browser")
            self.assertEqual(extension.seen_processes(Path(data)), {"chrome.exe"})

    def test_text_learned_by_the_icons_menu(self) -> None:
        agent = TemporaryAgent()
        try:
            from personalized_agent.api.routes import build_routes
            from test_handed_ways import _Ask
            routes = build_routes(agent.app)
            ident = handover.hand_over_picked(agent.app.data_dir, SELECTED)
            with mock.patch.object(agent.app.learning.planner, "start",
                                   return_value={"id": "P-1"}) as started:
                said = routes[("POST", "/api/learning/inbox")](_Ask(id=ident))
            self.assertEqual(said["draft"], {"id": "P-1"})
            handed = started.call_args[0][0]
            self.assertEqual((handed["kind"], handed["text"]),
                             ("text", "Plant the tomatoes by May 10."))
            self.assertEqual(handed["window"]["url"], "https://example.org/garden")
        finally:
            agent.cleanup()


class WorkAssignedTests(unittest.TestCase):
    """The user, 2026-11-02: on the Overview, left of To do, the work delegated
    not from a to-do - each with where it stands."""

    def test_delegated_work_listed_a_to_dos_and_a_kept_draft_not(self) -> None:
        from personalized_agent.agent.work import delegate
        agent = TemporaryAgent()
        try:
            drafts = agent.app.drafts
            made = {}
            for name, source, status, options in (
                    ("picked", "picked", delegate.STARTED, {}),
                    ("starting", "explorer", delegate.STARTING, {}),
                    ("todo", "todo", delegate.STARTED, {"todo": "t1"}),
                    ("kept", "delegate", delegate.READY, {}),
                    ("removed", "selection", delegate.STARTED, {"removed": True})):
                one = drafts.create(**picked.as_draft(picked.picked(SELECTED))) \
                    if source == "picked" else drafts.create(text=f"Do {name}", source=source,
                                                            collect=False)
                drafts._set(one["id"], status=status, options=options,
                            plan={"title": f"Plan {name}"} if name != "picked" else {})
                made[name] = one["id"]
            listed = drafts.assigned()
            self.assertEqual({one["id"] for one in listed}, {made["picked"], made["starting"]})
            mine = next(one for one in listed if one["id"] == made["picked"])
            self.assertEqual(mine["text"], "Plant the tomatoes by May 10.")
            self.assertEqual(mine["detail"], "Google Chrome - Garden club - spring plans")
            self.assertEqual(mine["work"]["versions"][0]["draft"], made["picked"])
            starting = next(one for one in listed if one["id"] == made["starting"])
            # Its start cut off (nothing starting it here): its plan, to start again.
            self.assertEqual((starting["text"], starting["work"]["versions"][0]["state"]),
                             ("Plan starting", "drafting"))
        finally:
            agent.cleanup()

    def test_a_start_that_failed_is_listed_and_told_never_silent(self) -> None:
        """The box closes at Start (the user, 2026-11-02): a start that fails
        after is listed - "did not start" - and notified."""
        from personalized_agent.agent.work import delegate
        agent = TemporaryAgent()
        try:
            drafts = agent.app.drafts
            one = drafts.create(text="Tidy the seed list", source="selection", collect=False)
            drafts._set(one["id"], status=delegate.READY, plan={"title": "Tidy the seeds"})
            self.assertEqual(drafts.assigned(), [], "a draft kept, before it is started")
            with mock.patch.object(agent.app.notifications, "notify") as told:
                drafts._did_not_start(one["id"], "the folder is in use")
            listed = drafts.assigned()
            version = listed[0]["work"]["versions"][0]
            self.assertEqual((listed[0]["id"], version["state"], version["failed"]),
                             (one["id"], "drafting", "the folder is in use"))
            self.assertEqual(told.call_args[0][0], "Delegated work did not start")
            self.assertIn("Tidy the seeds", told.call_args[0][1])
            drafts._started_after_all(one["id"])
            self.assertNotIn("start_failed", drafts.get(one["id"])["options"])
        finally:
            agent.cleanup()

    def test_left_of_to_do_on_the_overview(self) -> None:
        html = (Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
                / "index.html").read_text("utf-8")
        self.assertLess(html.index('id="ov-assigned"'), html.index('id="ov-todo"'))

    def test_a_file_handed_over_is_listed_once_as_a_file(self) -> None:
        """On the lab's Mac, 2026-11-02: a file from Finder's Quick Actions
        was listed in the box as a folder and as a file."""
        js = (WEB / "app.js").read_text("utf-8")
        compose = js[js.index("function delegateCompose"):]
        compose = compose[:compose.index("\n}\n")]
        self.assertIn("(draft.folders || []).filter((one) => !files.includes(one))", compose)

    def test_the_avatar_s_menu_on_a_mac_whatever_is_in_front(self) -> None:
        """On the lab's Mac, 2026-11-02: "右键logo没有东西弹出来" - Tk drops a
        right click while another program is in front. Heard through
        AppKit and told through the queue - a call into Tk from AppKit's
        callback ended the process - and opened up above the Dock."""
        desk = (Path(__file__).resolve().parents[1] / "personalized_agent" / "ui"
                / "desktop.py").read_text("utf-8")
        bound = desk[desk.index("from .macos_avatar import right_clicks_while_away"):]
        bound = bound[:bound.index("except Exception")]
        self.assertIn('self.queue.put(("call"', bound)
        self.assertNotIn("self.root.after", bound, "never into Tk from AppKit's callback")
        menu = desk[desk.index("def _on_right_click"):desk.index("# -- delegate work")]
        self.assertIn("y = max(0, y - high)", menu, "opened up where down it does not fit")
        mac = (Path(__file__).resolve().parents[1] / "personalized_agent" / "ui"
               / "macos_avatar.py").read_text("utf-8")
        self.assertIn("event.windowNumber() == number and not app.isActive()", mac)
        from personalized_agent.ui import chat_bubble
        source = Path(chat_bubble.__file__).read_text("utf-8")
        self.assertIn("visibleFrame()", source, "a Mac's Dock and menu bar left out")

    def test_an_answer_it_handed_over_opens_from_here(self) -> None:
        """The user, 2026-11-02: an answer could not be opened from Work
        assigned - it said "See it in Work"."""
        js = (WEB / "app.js").read_text("utf-8")
        made = js[js.index("function ovTodoMade"):js.index("function ovTodoTracked")]
        self.assertIn('item.kind === "answer" ? "Open the answer"', made)
        self.assertNotIn('shown: item.kind === "text" || item.kind === "answer"', made)

    def test_delegated_from_another_window_listed_here_too(self) -> None:
        """The user, 2026-11-02: work delegated from the browser's box (a
        window of its own) was not under Work assigned - the agent's window
        had read the list when it opened, and was never told."""
        js = (WEB / "app.js").read_text("utf-8")
        solo = js[js.index("if (DELEGATE_SOLO) {\n        // CLOSED AT ONCE"):]
        self.assertLess(solo.index("ovSayAssigned();"), solo.index("return;"),
                        "the box says it started a work")
        self.assertIn("new BroadcastChannel(OV_WORK_CHANNEL).onmessage", js)
        self.assertIn('if (!document.hidden) ovListsAgain();', js)
        self.assertNotIn('window.addEventListener("focus"', js, "nothing asked at each focus")
        # A work going that neither list knows: the lists asked again, once.
        watch = js[js.index("async function watchWorkGoing"):js.index("setTimeout(watchWorkGoing, 1000)")]
        self.assertIn("ovTodos.concat(ovAssigned)", watch)
        self.assertIn("!ovUnitsAskedFor.has(unit)", watch)
        self.assertIn("if (unknown.length", watch)


class TheWordsOnThePageTests(unittest.TestCase):

    def test_the_box_says_what_was_right_clicked(self) -> None:
        page = (Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
                / "app.js").read_text("utf-8")
        self.assertIn("You right-clicked", page)
        self.assertIn("if (where.picked)", page)
        html = (Path(__file__).resolve().parents[1] / "personalized_agent" / "api" / "web"
                / "index.html").read_text("utf-8")
        at = html.index('id="updates-card"')
        self.assertLess(at, html.index('id="extension-card"'), "under Updates")


if __name__ == "__main__":
    unittest.main()

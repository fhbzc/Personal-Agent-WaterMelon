"""Updates come only from published releases: a newer one is downloaded,
checked against the release's own checksums and installed - by itself by
default, never while work runs - or only told, as the user chose."""

from __future__ import annotations

import hashlib
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from personalized_agent.platform_layer import updater as up

ROOT = Path(__file__).resolve().parents[1]


class _Config(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _release(tag="v9.0.0", prerelease=False, files=None):
    files = files if files is not None else [up.ASSET["windows"], up.SUMS]
    return {"tag": tag, "page": f"https://example.org/releases/{tag}",
            "published": "2026-11-01T00:00:00Z", "notes": "",
            "prerelease": prerelease, "draft": False,
            "files": {name: {"url": f"https://example.org/{name}", "size": 1} for name in files}}


class WhichVersionIsNewerTests(unittest.TestCase):

    def test_by_number_not_by_text(self) -> None:
        self.assertTrue(up.is_newer("v1.0.10", "1.0.9"))
        self.assertFalse(up.is_newer("v1.0.0", "1.0.0"))
        self.assertFalse(up.is_newer("v0.9.9", "1.0.0"))
        self.assertFalse(up.is_newer("nightly", "1.0.0"))


class WhatIsDoneTests(unittest.TestCase):

    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.config = _Config(updates="install")
        # (The default is "ask": these first look at "install".)
        self.told = []
        self.updater = up.Updater(self.config, Path(self.work.name),
                                  notify=lambda *a, **k: self.told.append(a[0]),
                                  working=lambda: False)
        self.updater.quit = lambda: None
        self.installed = []
        patches = [mock.patch.object(up, "how_installed", return_value="windows"),
                   mock.patch.object(self.updater, "download",
                                     side_effect=lambda release, kind: Path("setup.exe")),
                   mock.patch.object(self.updater, "install",
                                     side_effect=lambda *a: self.installed.append(a[2]))]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)

    def test_installed_by_itself_by_default(self) -> None:
        with mock.patch.object(up, "published_releases", return_value=[_release()]):
            said = self.updater.once()
        self.assertEqual(self.installed, ["v9.0.0"])
        self.assertTrue(said["newer"])

    def test_not_while_work_runs(self) -> None:
        self.updater.working = lambda: True
        with mock.patch.object(up, "published_releases", return_value=[_release()]):
            said = self.updater.once()
        self.assertEqual(self.installed, [])
        self.assertIn("waiting", said["doing"])

    def test_asked_first_when_chosen(self) -> None:
        self.config["updates"] = "ask"
        with mock.patch.object(up, "published_releases", return_value=[_release()]):
            self.updater.once()
            self.updater.once()
        self.assertEqual(self.installed, [])
        self.assertEqual(len(self.told), 1, "told once, not every day")
        self.updater.install_now()
        with mock.patch.object(up, "published_releases", return_value=[_release()]):
            self.updater.once()
        self.assertEqual(self.installed, ["v9.0.0"])

    def test_off_asks_nobody(self) -> None:
        self.config["updates"] = "off"
        with mock.patch.object(up, "published_releases") as asked:
            self.updater.once()
        asked.assert_not_called()

    def test_an_older_one_is_nothing(self) -> None:
        for release in (_release(tag="v0.0.1"),):
            with mock.patch.object(up, "published_releases", return_value=[release]):
                said = self.updater.once()
            self.assertFalse(said["newer"])
        self.assertEqual(self.installed, [])

    def test_a_copy_that_cannot_replace_itself_is_only_told(self) -> None:
        with mock.patch.object(up, "how_installed", return_value=""), \
                mock.patch.object(up, "published_releases", return_value=[_release()]):
            said = self.updater.once()
        self.assertFalse(said["can_install"])
        self.assertEqual(self.installed, [])
        self.assertEqual(len(self.told), 1)

    def test_no_checksums_no_install(self) -> None:
        with mock.patch.object(up, "published_releases",
                               return_value=[_release(files=[up.ASSET["windows"]])]):
            self.updater.once()
        self.assertEqual(self.installed, [])


class ChooseTheVersionTests(unittest.TestCase):
    """Chosen to be asked (no longer the default): every newer published
    version is listed, each with what it says of itself, and the one chosen
    is installed."""

    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.updater = up.Updater(_Config(updates="ask"), Path(self.work.name),
                                  notify=lambda *a, **k: None)
        self.updater.quit = lambda: None
        self.installed = []
        listed = [_release("v9.1.0"), _release("v9.0.0"), _release(up.__version__),
                  _release("v0.0.1")]
        listed[0]["notes"] = "The newest."
        patches = [mock.patch.object(up, "how_installed", return_value="windows"),
                   mock.patch.object(up, "published_releases", return_value=listed),
                   mock.patch.object(self.updater, "download",
                                     side_effect=lambda release, kind, **k: Path("setup.exe")),
                   mock.patch.object(self.updater, "install",
                                     side_effect=lambda *a: self.installed.append(a[2])),
                   # The installer's way, here: no quiet one for this copy.
                   mock.patch("personalized_agent.platform_layer.quiet_update.can_make_ready",
                              return_value=False)]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)

    def test_newer_ones_listed_and_nothing_installed_until_chosen(self) -> None:
        said = self.updater.once()
        self.assertEqual([one["tag"] for one in said["versions"]], ["v9.1.0", "v9.0.0"])
        self.assertEqual(said["versions"][0]["notes"], "The newest.")
        self.assertEqual(self.installed, [])

    def test_the_one_chosen_is_installed(self) -> None:
        self.updater.once()
        self.updater.install_now("v9.0.0")
        self.updater.once()
        self.assertEqual(self.installed, ["v9.0.0"])
        self.updater.once()
        self.assertEqual(self.installed, ["v9.0.0"], "once, not on every look")

    def test_the_route_takes_only_a_listed_one(self) -> None:
        from support import TemporaryAgent
        from personalized_agent.api.errors import ApiError
        from personalized_agent.api.routes import build_routes
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        agent.app.updater = self.updater
        self.updater.once()
        routes = build_routes(agent.app)

        class _Req:
            def __init__(self, body):
                self.body = body

            def get(self, key, default=None):
                return self.body.get(key, default)
        with self.assertRaises(ApiError):
            routes[("POST", "/api/updates/install")](_Req({"version": "v0.0.1"}))
        routes[("POST", "/api/updates/install")](_Req({"version": "v9.1.0"}))
        self.assertEqual(self.updater.state["asked_to_install"], "v9.1.0")


class TheListOfReleasesTests(unittest.TestCase):

    def test_no_draft_no_pre_release_newest_first(self) -> None:
        listed = [{"tag_name": "v1.2.0", "prerelease": True, "draft": False, "assets": []},
                  {"tag_name": "v1.0.2", "prerelease": False, "draft": False, "assets": [],
                   "body": "Fixes", "published_at": "2026-11-01T00:00:00Z"},
                  {"tag_name": "v1.1.0", "prerelease": False, "draft": True, "assets": []},
                  {"tag_name": "v1.0.10", "prerelease": False, "draft": False, "assets": []}]
        with mock.patch.object(up, "_get", return_value=_Answer(
                __import__("json").dumps(listed).encode())):
            got = up.published_releases()
        self.assertEqual([one["tag"] for one in got], ["v1.0.10", "v1.0.2"])
        self.assertEqual(got[1]["notes"], "Fixes")
        with mock.patch.object(up, "_get", return_value=_Answer(
                __import__("json").dumps(listed).encode())):
            got = up.published_releases(pre=True)
        self.assertEqual([one["tag"] for one in got], ["v1.2.0", "v1.0.10", "v1.0.2"],
                         "with pre-releases when asked for; never a draft")


class PreReleasesTests(unittest.TestCase):
    """The user, 2026-11-02: "check now 是允许检测pre-release或者beta 版的，可以
    选择安装，但是pre-release/beta版不会被自动安装，也不会在被检测到的时候弹出提醒"."""

    def make(self, mode: str, listed) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.told, self.installed = [], []
        self.updater = up.Updater(_Config(updates=mode), Path(self.work.name),
                                  notify=lambda *a, **k: self.told.append(a[0]),
                                  working=lambda: False)
        self.updater.quit = lambda: None
        patches = [mock.patch.object(up, "how_installed", return_value="windows"),
                   mock.patch.object(up, "published_releases", return_value=listed),
                   mock.patch.object(self.updater, "download",
                                     side_effect=lambda release, kind, **k: Path("setup.exe")),
                   mock.patch.object(self.updater, "install",
                                     side_effect=lambda *a: self.installed.append(a[2])),
                   mock.patch("personalized_agent.platform_layer.quiet_update.can_make_ready",
                              return_value=False)]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)

    def test_found_listed_and_said_to_be_one_never_installed_by_itself(self) -> None:
        self.make("install", [_release("v9.1.0", prerelease=True)])
        said = self.updater.once()
        self.assertEqual([(one["tag"], one["prerelease"]) for one in said["versions"]],
                         [("v9.1.0", True)])
        self.assertTrue(said["versions"][0]["can_install"])
        self.assertFalse(said["newer"], "no newer release: a pre-release is not one")
        self.assertEqual(self.installed, [], "not installed by itself, even set to install")

    def test_never_told_in_a_notice(self) -> None:
        self.make("ask", [_release("v9.1.0", prerelease=True)])
        self.updater.once()
        self.updater.once()
        self.assertEqual(self.told, [])

    def test_installed_when_the_user_chooses_it(self) -> None:
        self.make("ask", [_release("v9.1.0", prerelease=True)])
        self.updater.check_now()
        # Its look is on a thread of its own: left going, it and the `once`
        # below both found the version asked for, on a slow machine, and both
        # installed it. The application's looks are one at a time (`_look`).
        for one in threading.enumerate():
            if one.name == "updates-now":
                one.join(10)
        self.updater.once()
        self.updater.install_now("v9.1.0")
        self.updater.once()
        self.assertEqual(self.installed, ["v9.1.0"])

    def test_a_release_beside_it_is_still_what_is_installed_and_told(self) -> None:
        self.make("install", [_release("v9.2.0", prerelease=True), _release("v9.1.0")])
        said = self.updater.once()
        self.assertEqual([one["tag"] for one in said["versions"]], ["v9.2.0", "v9.1.0"])
        self.assertEqual(said["latest"], "v9.1.0")
        self.assertEqual(self.installed, ["v9.1.0"], "the release, not the pre-release")
        self.make("ask", [_release("v9.2.0", prerelease=True), _release("v9.1.0")])
        self.updater.once()
        self.assertEqual(len(self.told), 1)
        self.assertIn("v9.1.0", self.told[0])

    def test_chosen_over_a_newer_release_set_to_install(self) -> None:
        self.make("install", [_release("v9.2.0"), _release("v9.1.0", prerelease=True)])
        self.updater.install_now("v9.1.0")
        self.updater.once()
        self.assertEqual(self.installed, ["v9.1.0"], "the one the user chose")

    def test_the_page_says_which_is_a_pre_release(self) -> None:
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("a pre-release", script)
        self.assertIn("A newer pre-release can be chosen below.", script)


class GoingBackTests(unittest.TestCase):
    """Older versions from v0.3.0 on, to go back to: chosen only by the user. (Here from 0.2.0,
    this being a version the list is older than.)"""

    def make(self, mode: str, listed) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.told, self.installed = [], []
        self.config = _Config(updates=mode)
        self.config.set = self.config.__setitem__
        self.updater = up.Updater(self.config, Path(self.work.name),
                                  notify=lambda *a, **k: self.told.append(a[0]),
                                  working=lambda: False)
        self.updater.quit = lambda: None
        patches = [mock.patch.object(up, "how_installed", return_value="windows"),
                   mock.patch.object(up, "published_releases", return_value=listed),
                   mock.patch.object(up, "BACK_AS_FAR_AS", (0, 2, 0)),
                   mock.patch.object(self.updater, "download",
                                     side_effect=lambda release, kind, **k: Path("setup.exe")),
                   mock.patch.object(self.updater, "install",
                                     side_effect=lambda *a: self.installed.append(a[2])),
                   mock.patch("personalized_agent.platform_layer.quiet_update.can_make_ready",
                              return_value=False)]
        for one in patches:
            one.start()
            self.addCleanup(one.stop)

    def test_listed_as_older_never_installed_by_itself(self) -> None:
        self.make("install", [_release("v0.2.5"), _release("v0.1.9")])
        said = self.updater.once()
        self.assertEqual([(one["tag"], one["older"]) for one in said["versions"]],
                         [("v0.2.5", True)], "nothing from before the oldest")
        self.assertEqual(self.installed, [])

    def test_this_version_is_not_among_them(self) -> None:
        self.make("ask", [_release("v" + up.__version__), _release("v0.2.5")])
        self.assertEqual([one["tag"] for one in self.updater.once()["versions"]], ["v0.2.5"])

    def test_chosen_it_is_installed_and_installing_by_itself_is_turned_off(self) -> None:
        self.make("install", [_release("v0.2.5")])
        said = self.updater.install_now("v0.2.5")
        self.assertEqual(said["mode"], "ask", "at once: the page shows it")
        self.assertEqual(self.config["updates"], "ask", "in the setting every version reads")
        self.updater.once()
        self.assertEqual(self.installed, ["v0.2.5"])

    def test_a_newer_one_chosen_leaves_the_setting_as_it_is(self) -> None:
        self.make("install", [_release("v9.1.0"), _release("v9.0.0")])
        self.updater.install_now("v9.0.0")
        self.assertEqual(self.config["updates"], "install")

    def test_not_looking_or_telling_stays_as_it_was(self) -> None:
        self.make("ask", [_release("v0.2.5")])
        self.updater.install_now("v0.2.5")
        self.assertEqual(self.config["updates"], "ask")

    def test_gone_back_a_newer_one_is_told_and_comes_only_when_asked(self) -> None:
        left = up.__version__
        self.make("install", [_release("v0.2.5")])
        self.updater.install_now("v0.2.5")
        # As it stands on the version gone back to, the one left now being newer.
        with mock.patch.object(up, "published_releases", return_value=[_release("v" + left)]), \
                mock.patch.object(up, "is_newer", lambda tag, current=None: (
                    up.version_of(tag) > up.version_of(current or "0.2.5"))):
            self.updater.state["asked_to_install"] = None
            self.updater.once()
            self.assertEqual(self.installed, [], "not by itself")
            self.assertEqual(len(self.told), 1, "told")
            self.updater.install_now()
            self.updater.once()
            self.assertEqual(self.installed, ["v" + left], "asked for, it comes")

    def test_one_made_ready_to_go_back_to_stays_ready(self) -> None:
        from personalized_agent.platform_layer import quiet_update as quiet
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            part = folder / "part"
            part.mkdir()
            said = {"tag": "v0.2.5", "kind": "windows", "dir": str(folder / "v0.2.5"),
                    "parts": [str(part)], "live_parts": [str(part)]}
            with mock.patch.object(up, "BACK_AS_FAR_AS", (0, 2, 0)):
                quiet._write(folder / quiet.STAGED, dict(said, back=True))
                self.assertEqual(quiet.staged(folder)["tag"], "v0.2.5")
                quiet._write(folder / quiet.STAGED, said)
                self.assertIsNone(quiet.staged(folder), "older, and not chosen to be")


class _Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class CheckedBeforeItRunsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.updater = up.Updater(_Config(), Path(self.work.name))
        self.payload = b"a made-up installer"

    def _serve(self, sums_hash):
        name = up.ASSET["windows"]

        def get(url, timeout=30.0, start=0):
            if url.endswith(up.SUMS):
                return _Answer(f"{sums_hash}  {name}\n".encode())
            return _Answer(self.payload)
        return mock.patch.object(up, "_get", side_effect=get)

    def test_kept_when_it_matches_and_older_downloads_go(self) -> None:
        old = self.updater.folder / "v8.0.0"
        old.mkdir(parents=True)
        with self._serve(hashlib.sha256(self.payload).hexdigest()):
            target = self.updater.download(_release(), "windows")
        self.assertEqual(target.read_bytes(), self.payload)
        self.assertFalse(old.exists())

    def test_deleted_and_refused_when_it_does_not(self) -> None:
        with self._serve("0" * 64):
            with self.assertRaises(RuntimeError):
                self.updater.download(_release(), "windows")
        self.assertEqual(list((self.updater.folder / "v9.0.0").iterdir()), [])


class InstalledOnceTheProgramHasGoneTests(unittest.TestCase):

    def test_the_windows_installer_runs_silently_after_this_one_quits(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        updater = up.Updater(_Config(display_language="zh"), Path(work.name))
        updater.folder.mkdir(parents=True)

        def ready(command, **kwargs):
            # The helper says it is there before the agent quits.
            (updater.folder / up.HELPER_READY).write_text("ready")
            return mock.Mock()
        with mock.patch.object(up.subprocess, "Popen", side_effect=ready) as started:
            updater._after_quit_windows(Path("C:/data/it's here/setup.exe"))
        command = started.call_args[0][0]
        script = command[-1]
        self.assertEqual(command[0], "powershell")
        self.assertIn("Wait-Process -Id", script)
        # Silent - no question - with the installer's own progress window.
        self.assertIn("'/SILENT'", script)
        self.assertIn("'/LANG=zh'", script)
        self.assertIn("it''s here", script, "a quote in the path is doubled")

    def test_the_installer_leaves_start_at_sign_in_and_starts_the_new_one(self) -> None:
        script = (ROOT / "build" / "windows" / "installer.iss").read_text(encoding="utf-8-sig")
        self.assertIn("Result := WizardSilent and (WizardForm.PrevAppDir <> '');", script)
        self.assertIn('Parameters: "--autostart on"', script)
        self.assertRegex(script, r'--autostart on".*Check: not AnUpdate')
        self.assertRegex(script, r'Flags: nowait runasoriginaluser; Check: AnUpdate')

    def test_what_is_new_is_held_until_it_is_seen_and_told_once(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            told = []
            updater = up.Updater(_Config(), Path(work), notify=lambda *a, **k: told.append(a[0]))
            updater.folder.mkdir(parents=True)
            updater.quit = lambda: None
            with mock.patch.object(updater, "_after_quit_windows"):
                updater.install(Path("setup.exe"), "windows", f"v{up.__version__}",
                                {"notes": "- Faster grouping\n- A window of what is new",
                                 "page": "https://example.org/r"})
            # The new version starts - twice, before the window is closed.
            for _ in range(2):
                again = up.Updater(_Config(), Path(work),
                                   notify=lambda *a, **k: told.append(a[0]))
                again._said_updated()
                news = again.status()["just_updated"]
                self.assertIn("Faster grouping", news["notes"])
                self.assertEqual(news["from"], up.__version__)
            self.assertEqual(told, [f"Updated to {up.__version__}"], "told once")
            again.seen()
            self.assertIsNone(again.status()["just_updated"])
            third = up.Updater(_Config(), Path(work))
            third._said_updated()
            self.assertIsNone(third.status()["just_updated"], "not shown after it was closed")

    def test_the_window_opens_with_the_release_notes(self) -> None:
        import json
        import re
        import subprocess
        from test_panels import _javascript_engine
        kind, engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        start = script.index("/* WHAT IS NEW, once, after an update")
        end = script.index("/* MACOS PERMISSIONS (Settings > Privacy)")
        page = """<!doctype html><html><head><meta charset='utf-8'></head><body>
<dialog id="update-notes"><h3 id="update-notes-title"></h3><p id="update-notes-from"></p>
<div id="update-notes-text"></div><a id="update-notes-page"></a>
<button id="update-notes-close"></button></dialog>
<script>window.__posts=[];const $=(s)=>document.querySelector(s);
const api={post:async(p)=>{window.__posts.push(p);return {};}};</script>
<script>%s</script>
<script>window.addEventListener('load',async()=>{let err='';const out={};
try{showWhatIsNew({to:'9.0.0',from:'1.0.1',notes:'- One\\n- Two <b>',page:'https://example.org/r'});
out.open=$('#update-notes').open;out.title=$('#update-notes-title').textContent;
out.notes=$('#update-notes-text').textContent;out.markup=$('#update-notes-text').children.length;
showWhatIsNew({to:'9.0.1'});out.again=$('#update-notes-title').textContent;
$('#update-notes-close').click();await new Promise(r=>setTimeout(r,100));
out.closed=!$('#update-notes').open;out.posts=window.__posts;
}catch(e){err=String(e&&e.stack?e.stack:e);}
document.title=JSON.stringify({err,out});});</script></body></html>""" % script[start:end]
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "news.html"
            path.write_text(page, encoding="utf-8")
            done = subprocess.run(
                [engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=5000",
                 "--dump-dom", path.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        said = json.loads(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
                          .replace("&quot;", '"').replace("&amp;", "&")
                          .replace("&lt;", "<").replace("&gt;", ">"))
        self.assertEqual(said["err"], "")
        out = said["out"]
        self.assertTrue(out["open"])
        self.assertEqual(out["title"], "Updated to version 9.0.0")
        self.assertIn("- Two <b>", out["notes"], "shown as written")
        self.assertEqual(out["markup"], 0, "never as markup")
        self.assertEqual(out["again"], "Updated to version 9.0.0", "once per page")
        self.assertTrue(out["closed"])
        self.assertEqual(out["posts"], ["/api/updates/seen"])


class CheckNowTests(unittest.TestCase):
    """Check now looks at once - also run from source, where no loop looks
    by itself - and the page shows the newer versions in a box to choose
    from, or says there is none."""

    def test_it_looks_with_no_loop_running(self) -> None:
        import time
        with tempfile.TemporaryDirectory() as work:
            updater = up.Updater(_Config(), Path(work), notify=lambda *a, **k: None)
            listed = [_release("v9.1.0"), _release(up.__version__)]
            with mock.patch.object(up, "published_releases", return_value=listed):
                updater.check_now()       # answers at once; the look may be done already
                for _ in range(100):
                    if updater.status()["doing"] != "checking":
                        break
                    time.sleep(0.05)
            said = updater.status()
            self.assertEqual(said["doing"], "")
            self.assertTrue(said["checked_at"])
            self.assertEqual([one["tag"] for one in said["versions"]], ["v9.1.0"])
            self.assertIsNone(updater._thread, "only the one look, no loop")

    def test_the_page_offers_a_box_of_versions(self) -> None:
        import json
        import re
        import subprocess
        from test_panels import _javascript_engine
        kind, engine = _javascript_engine()
        if kind != "chrome":
            self.skipTest("no browser available to render the page")
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        start = script.index("/* UPDATES (Settings > General)")
        end = script.index("/* MACOS PERMISSIONS (Settings > Privacy)")
        page = """<!doctype html><html><head><meta charset='utf-8'></head><body>
<p id="updates-status"></p><div id="updates-versions"></div>
<script>const $=(s)=>document.querySelector(s);const clock=(t)=>t.slice(0,10);
const toast=()=>{};const api={post:async()=>({}),get:async()=>({})};</script>
<script>%s</script>
<script>window.addEventListener('load',()=>{let err='';const out={};
const v=(tag,notes)=>({tag,notes,published:'2026-11-01T00:00:00Z',page:'https://example.org/'+tag,can_install:false});
const box=()=>$('#updates-version');
const options=()=>[...box().options].map(o=>o.textContent);
try{
renderUpdates({current:'0.2.0',mode:'ask',versions:[]});
out.before=options();out.beforeOff=box().disabled;
renderUpdates({current:'0.2.0',mode:'ask',checked_at:'x',versions:[]});
out.none=options();
renderUpdates({current:'0.2.0',mode:'ask',checked_at:'x',newer:true,latest:'v9.1.0',
  versions:[v('v9.1.0','The newest.'),v('v9.0.0','The one before.')]});
out.two=options();out.notes=$('.update-notes-text').textContent;
box().value='v9.0.0';box().dispatchEvent(new Event('change'));
out.chosen=$('.update-notes-text').textContent;
out.link=$('.update-version-action a').href;
renderUpdates({current:'0.2.0',mode:'ask',checked_at:'y',newer:true,latest:'v9.1.0',
  versions:[v('v9.1.0','The newest.'),v('v9.0.0','The one before.')]});
out.kept=box().value;
renderUpdateVersions.chosen='';
const many={current:'0.3.1',mode:'ask',checked_at:'z',versions:[
  Object.assign(v('v9.2.0','Trying.'),{prerelease:true}),v('v9.1.0','The newest.'),
  Object.assign(v('v0.3.0','Before.'),{older:true,can_install:true})]};
renderUpdates(many);
out.formal=options();out.warnedNot=$('.update-version p.muted').hidden;
const tick=$('.update-version-pre input');tick.checked=true;tick.dispatchEvent(new Event('change'));
out.withPre=options();
box().value='v0.3.0';box().dispatchEvent(new Event('change'));
out.warned=$('.update-version p.muted').hidden;
out.goBack=$('.update-version-action button').textContent;
out.offWhenAsked=$('.update-version-off').hidden;
renderUpdates(Object.assign({},many,{mode:'install'}));
out.offWhenInstalling=$('.update-version-off').hidden;
out.offWords=$('.update-version-off').textContent;
box().value='v9.1.0';box().dispatchEvent(new Event('change'));
out.offForANewerOne=$('.update-version-off').hidden;
renderUpdateVersions.pre=false;
renderUpdates({current:'0.2.0',mode:'ask',doing:'checking',versions:[]});
out.looking=$('#updates-status').textContent;
}catch(e){err=String(e&&e.stack?e.stack:e);}
document.title=JSON.stringify({err,out});});</script></body></html>""" % script[start:end]
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "versions.html"
            path.write_text(page, encoding="utf-8")
            done = subprocess.run(
                [engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=5000",
                 "--dump-dom", path.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        said = json.loads(re.search(r"<title>(.*?)</title>", done.stdout, re.S).group(1)
                          .replace("&quot;", '"').replace("&amp;", "&")
                          .replace("&lt;", "<").replace("&gt;", ">"))
        self.assertEqual(said["err"], "")
        out = said["out"]
        self.assertEqual(out["before"], ["Not looked for yet - press Check now"])
        self.assertTrue(out["beforeOff"])
        self.assertEqual(out["none"], ["No other version"])
        self.assertEqual(out["two"], ["Version v9.1.0 (2026-11-01)", "Version v9.0.0 (2026-11-01)"])
        self.assertEqual(out["notes"], "The newest.")
        self.assertEqual(out["chosen"], "The one before.")
        self.assertEqual(out["link"], "https://example.org/v9.0.0")
        self.assertEqual(out["kept"], "v9.0.0", "the one chosen stays chosen")
        self.assertEqual(out["formal"], ["Version v9.1.0 (2026-11-01)",
                                         "Version v0.3.0, older than this one (2026-11-01)"])
        self.assertTrue(out["warnedNot"], "a newer one needs no warning")
        self.assertEqual(out["withPre"], ["Version v9.2.0, a pre-release (2026-11-01)",
                                          "Version v9.1.0 (2026-11-01)",
                                          "Version v0.3.0, older than this one (2026-11-01)"])
        self.assertFalse(out["warned"], "an older one is said to be")
        self.assertEqual(out["goBack"], "Go back to this version")
        self.assertTrue(out["offWhenAsked"], "nothing is turned off: it is not installing by itself")
        self.assertFalse(out["offWhenInstalling"], "said before it is chosen")
        self.assertIn("turns off installing by itself", out["offWords"])
        self.assertTrue(out["offForANewerOne"])
        self.assertIn("Looking for newer versions", out["looking"])


class ChosenInSettingsTests(unittest.TestCase):

    def test_asked_by_default_on_general(self) -> None:
        # The user, 2026-10-27: looked for and told by default, not installed
        # by itself - every update a quiet one either way (quiet_update.py).
        from personalized_agent.common.config.schema import SCHEMA_BY_KEY
        field = SCHEMA_BY_KEY["updates"]
        self.assertEqual(field.default, "ask")
        self.assertEqual(up.Updater(_Config(), Path(tempfile.gettempdir())).mode(), "ask")
        self.assertEqual(list(field.choices), list(up.MODES))
        self.assertEqual(field.section, "General")
        page = (ROOT / "personalized_agent" / "api" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="settings-form-updates"', page)
        script = (ROOT / "personalized_agent" / "api" / "web" / "app.js").read_text(encoding="utf-8")
        for mode in up.MODES:
            self.assertRegex(script, rf"\n  {mode}: \"")

    def _new_here(self, kind, chosen=False):
        from types import SimpleNamespace
        from unittest import mock
        said = {}
        config = SimpleNamespace(user_chose=lambda _key: chosen, set=said.__setitem__)
        with mock.patch.object(up, "how_installed", return_value=kind):
            up.Updater.installed_just_now(SimpleNamespace(config=config))
        return said

    def test_a_new_install_with_no_installers_page_has_them_by_itself(self) -> None:
        self.assertEqual(self._new_here("macos"), {"updates": "install"})
        self.assertEqual(self._new_here("appimage"), {"updates": "install"})

    def test_not_what_the_user_chose_nor_what_cannot_replace_itself(self) -> None:
        self.assertEqual(self._new_here("macos", chosen=True), {})
        self.assertEqual(self._new_here(""), {}, "a .deb, an old .pkg, from source")
        self.assertEqual(self._new_here("windows"), {}, "its installer's page says")

    def test_nobody_already_installed_is_changed(self) -> None:
        from unittest import mock
        from support import TemporaryAgent
        from personalized_agent.app import AgentApplication
        made = TemporaryAgent()
        self.addCleanup(made.cleanup)
        made.app.db.close() if hasattr(made.app.db, "close") else None
        with mock.patch.object(up.Updater, "installed_just_now") as told:
            AgentApplication(root=made.dir / "root")
        told.assert_not_called()
        with mock.patch.object(up.Updater, "installed_just_now") as told:
            AgentApplication(root=made.dir / "another")
        told.assert_called_once()

    def test_the_installer_asks_ticked_and_hands_it_on(self) -> None:
        script = (ROOT / "build" / "windows" / "installer.iss").read_text(encoding="utf-8-sig")
        task = next(line for line in script.splitlines() if line.startswith('Name: "autoupdate"'))
        self.assertNotIn("unchecked", task, "ticked by default")
        self.assertIn('Parameters: "--updates install"', script)
        self.assertRegex(script, r'"--updates install".*Tasks: autoupdate; Check: not AnUpdate')
        self.assertRegex(script, r'"--updates ask".*Tasks: not autoupdate; Check: not AnUpdate')
        # A full install, and an uninstall, clear what a quiet update left.
        self.assertRegex(script, r'\[InstallDelete\][^\[]*\{app\}\\\.update')
        self.assertRegex(script, r'\[UninstallDelete\][^\[]*\{app\}\\_internal')
        lines = script.replace("\r\n", "\n")
        for code in ("en", "zh"):
            for key in ("AutoUpdate", "UpdatesGroup"):
                self.assertIn(f"\n{code}.{key}=", lines, (code, key))

    def test_the_installer_adds_the_pictures_languages_ticked(self) -> None:
        script = (ROOT / "build" / "windows" / "installer.iss").read_text(encoding="utf-8-sig")
        task = next(line for line in script.splitlines() if line.startswith('Name: "ocrlanguages"'))
        self.assertNotIn("unchecked", task, "ticked by default")
        self.assertRegex(script, r'"--add-ocr".*Tasks: ocrlanguages; Check: \(not AnUpdate\) and '
                                 r'\(not WizardSilent\)')
        for code in ("en", "zh"):
            self.assertIn(f"\n{code}.OcrLanguages=", script.replace("\r\n", "\n"))

    def test_only_the_missing_languages_are_asked_for(self) -> None:
        from personalized_agent import __main__ as entry
        from personalized_agent.platform_layer import ocr
        reader = mock.Mock()
        reader.available.return_value = True
        reader.can_add.return_value = True
        reader.installed.return_value = ["en"]
        with mock.patch.object(ocr, "current", return_value=reader):
            self.assertEqual(entry.main(["--add-ocr"]), 0)
            reader.add.assert_called_once_with(["zh"])
            reader.add.reset_mock()
            reader.installed.return_value = ["en", "zh", "ja"]
            self.assertEqual(entry.main(["--add-ocr"]), 0)
            reader.add.assert_not_called()

    def test_the_option_the_installer_runs_keeps_the_choice(self) -> None:
        from personalized_agent.__main__ import main
        from personalized_agent.common.config.store import ConfigStore
        from personalized_agent.common.paths import AppPaths
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / "root"
            self.assertEqual(main(["--updates", "ask", "--root", str(root)]), 0)
            paths = AppPaths.create(root)
            config = ConfigStore(paths.data_dir, path=paths.settings_file)
            self.assertEqual(config.get("updates"), "ask")

    def test_run_from_source_it_does_not_look(self) -> None:
        from support import TemporaryAgent
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        agent.app.start(with_api=False)
        self.assertIsNone(agent.app.updater._thread)
        self.assertEqual(up.how_installed(), "")


if __name__ == "__main__":
    unittest.main()

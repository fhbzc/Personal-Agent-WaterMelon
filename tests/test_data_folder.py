"""One data folder, one agent (section 110.1, as amended 2026-10-15).

The data folder - workspace/ and internal/ - is chosen when the agent is
installed (written beside the program as data_root.txt) or, run from source,
given with --root. A different folder is a different agent: its own
calendar, contacts and keys, and nothing taken from elsewhere. The folder is
never listened to, whatever the exclusion list says, and Settings shows it,
locked. A folder that already holds an agent's data is used by the installer
only if the user says so."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from support import TemporaryAgent
from test_panels import _javascript_engine

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "personalized_agent" / "api" / "web"


class WhereTheDataFolderIsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pa-folder-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _installed(self, chosen: str):
        program = self.dir / "Program"
        program.mkdir(exist_ok=True)
        (program / "data_root.txt").write_text(chosen, encoding="utf-8")
        return mock.patch.multiple("personalized_agent.common.paths",
                                   is_frozen=lambda: True), \
            mock.patch.object(sys, "executable", str(program / "PersonalizedAgent.exe"))

    def test_the_root_given_then_the_environment_then_the_installers_then_the_standard(self):
        from personalized_agent.common import paths
        chosen = self.dir / "Chosen"
        frozen, exe = self._installed(str(chosen))
        with frozen, exe, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(paths.ENV_ROOT, None)
            self.assertEqual(paths.resolve_root(), chosen.resolve())
            self.assertEqual(paths.resolve_root(self.dir / "Given"), (self.dir / "Given").resolve())
            with mock.patch.dict(os.environ, {paths.ENV_ROOT: str(self.dir / "Env")}):
                self.assertEqual(paths.resolve_root(), (self.dir / "Env").resolve())
        # Run from source, with nothing given: the standard folder.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(paths.ENV_ROOT, None)
            self.assertEqual(paths.resolve_root(), paths.default_root().resolve())
        self.assertEqual(paths.INSTALLED_ROOT_FILE, "data_root.txt")

    def test_the_installer_asks_and_writes_it(self) -> None:
        script = (ROOT / "build" / "windows" / "installer.iss").read_text(encoding="utf-8-sig")
        self.assertIn("CreateInputDirPage(wpSelectDir", script)
        self.assertIn("data_root.txt", script)
        # A folder with an agent's data in it: asked, and No keeps them there.
        self.assertIn("HasAgentData(Dir)", script)
        self.assertIn("mbConfirmation, MB_YESNO) = IDYES", script)
        # One it cannot write to without an administrator: refused.
        self.assertIn("NeedsAdmin(Dir)", script)
        for system_folder in ("{commonpf}", "{commonpf32}", "{win}", "{commonappdata}"):
            self.assertIn(f"Under(Folder, ExpandConstant('{system_folder}'))", script)
        for code in ("en", "zh", "es", "ja", "ko", "ru", "fr", "de", "it"):
            for key in ("DataTitle", "DataPrompt", "DataExisting", "DataProgramFiles"):
                self.assertIn(f"\n{code}.{key}=", script.replace("\r\n", "\n"), (code, key))


class AnotherFolderIsAnotherAgentTests(unittest.TestCase):

    def test_its_own_calendar(self) -> None:
        from personalized_agent.common.events.models import ScheduledTask, utcnow
        first, second = TemporaryAgent(), TemporaryAgent()
        try:
            first.app.scheduler.create_task(ScheduledTask(
                title="Water the tomatoes", scheduled_at=utcnow() + timedelta(hours=2),
                execution_type="notification", task_payload={"message": "Water them"},
                created_by="user"))
            self.assertEqual([t.title for t in first.app.scheduler.store.list(limit=10)],
                             ["Water the tomatoes"])
            self.assertEqual(second.app.scheduler.store.list(limit=10), [])
        finally:
            first.cleanup()
            second.cleanup()

    def test_its_own_keys(self) -> None:
        from personalized_agent.common.paths import INTERNAL_DIR, default_root
        from personalized_agent.models.credentials import SERVICE_NAME, service_name_for
        standard = default_root() / INTERNAL_DIR / "credentials"
        self.assertEqual(service_name_for(standard), SERVICE_NAME)
        with tempfile.TemporaryDirectory() as other:
            chosen = Path(other) / "internal" / "credentials"
            # Even when it is the folder this run uses - chosen when installing.
            with mock.patch("personalized_agent.common.paths.resolve_root",
                            return_value=Path(other)):
                self.assertNotEqual(service_name_for(chosen), SERVICE_NAME)

    def test_a_chosen_folder_takes_nothing_from_elsewhere(self) -> None:
        from personalized_agent.common import paths
        with tempfile.TemporaryDirectory() as work:
            legacy = Path(work) / "Legacy"
            (legacy / "settings").mkdir(parents=True)
            (legacy / "settings" / "settings.json").write_text("{}", encoding="utf-8")
            (legacy / "database").mkdir()
            (legacy / "database" / "agent.db").write_bytes(b"old")
            chosen = paths.AppPaths(Path(work) / "Chosen")
            chosen.ensure()
            with mock.patch.object(paths, "legacy_conventional_dir", return_value=legacy):
                self.assertIsNone(chosen.migrate_legacy_state())
            self.assertTrue((legacy / "database" / "agent.db").exists(), "left where it was")
            self.assertFalse(chosen.database_file.exists())


class NeverListenedToTests(unittest.TestCase):

    def test_the_data_folder_whatever_the_list_says(self) -> None:
        agent = TemporaryAgent()
        try:
            app = agent.app
            app.config.set("listener_directory_exclusions", "")
            from personalized_agent.common.file_noise import excluded_folders
            root = str(app.config.get("agent_root"))
            self.assertEqual(excluded_folders(app.config)[0], root)
            from personalized_agent.listener.core.privacy import PrivacyFilter
            privacy = PrivacyFilter(app.config)
            self.assertFalse(privacy.path_allowed(os.path.join(root, "internal", "logs", "a.log")))
            self.assertFalse(privacy.path_allowed(os.path.join(root, "workspace", "x.md")))
            elsewhere = os.path.join(os.path.splitdrive(root)[0] + os.sep, "Projects",
                                     "Garden", "plan.md")
            self.assertTrue(privacy.path_allowed(elsewhere))
        finally:
            agent.cleanup()

    def test_the_tray_says_which_agent_it_is(self) -> None:
        from personalized_agent.ui.tray import tooltip

        class App:
            root = Path(tempfile.gettempdir()) / "PA-release-test"

            def avatar_name(self):
                return "Watermelon"
        self.assertEqual(tooltip(App()), f"Watermelon ({App.root})")
        from personalized_agent.common.paths import default_root
        App.root = default_root()
        self.assertEqual(tooltip(App()), "Watermelon")


class SettingsShowItTests(unittest.TestCase):

    def setUp(self) -> None:
        self.kind, self.engine = _javascript_engine()
        if self.kind != "chrome":
            self.skipTest("no browser available to render the page")

    def test_locked_first_in_what_is_never_listened_to(self) -> None:
        agent = TemporaryAgent()
        try:
            from personalized_agent.api.routes import build_routes

            class Ask(dict):
                def int_param(self, key, default=0):
                    return int(self.get(key, default))
            settings = build_routes(agent.app)[("GET", "/api/settings")](Ask())
            settings = json.loads(json.dumps(settings, default=str))
        finally:
            agent.cleanup()
        root = settings["values"]["agent_root"]
        page = re.sub(r"<script[^>]*src=[^>]*></script>", "",
                      (WEB / "index.html").read_text(encoding="utf-8"))
        from test_settings_views import MODELS
        stub = {"/api/settings": settings, "/api/models": MODELS,
                "/api/settings/defaults": {"user_defaults": {}}, "/api/ocr": {"languages": []},
                "/api/modules": {"modules": []}}
        harness = (
            "<style>" + (WEB / "styles.css").read_text(encoding="utf-8") + "</style>"
            "<script>const STUB=" + json.dumps(stub) + ";"
            "window.fetch=(u)=>{const p=String(u).split('?')[0];"
            "return Promise.resolve({ok:true,status:200,statusText:'OK',"
            "headers:{get:()=>null},json:()=>Promise.resolve(STUB[p]||{})});};</script>"
            "<script>" + (WEB / "app.js").read_text(encoding="utf-8") + "</script>"
            "<script>window.addEventListener('load',async()=>{let err='';const out={};"
            "const wait=(ms)=>new Promise(r=>setTimeout(r,ms||150));"
            "try{await loadSettings();await wait(300);"
            "const box=document.querySelector('[data-key=\"listener_directory_exclusions\"]');"
            "const first=box.querySelector('.dir-entry');"
            "out.first=first.textContent.replace(/\\s+/g,' ').trim();"
            "out.locked=first.classList.contains('dir-entry-fixed');"
            "out.removable=!!first.querySelector('button');"
            "}catch(e){err=String(e&&e.stack?e.stack:e);}"
            "document.title=JSON.stringify({err,out});});</script>")
        with tempfile.TemporaryDirectory() as work:
            probe = Path(work) / "probe.html"
            probe.write_text(page + harness, encoding="utf-8")
            completed = subprocess.run(
                [self.engine, "--headless=new", "--disable-gpu", "--no-first-run",
                 f"--user-data-dir={work}/profile", "--virtual-time-budget=10000",
                 "--dump-dom", probe.as_uri()],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        match = re.search(r"<title>(.*?)</title>", completed.stdout or "", re.S)
        self.assertIsNotNone(match, "the page never finished rendering")
        result = json.loads(html.unescape(match.group(1)))
        self.assertEqual(result["err"], "")
        out = result["out"]
        self.assertTrue(out["locked"])
        self.assertFalse(out["removable"], "no Remove on the data folder")
        self.assertIn(root, out["first"])
        self.assertIn("never listened to", out["first"])


class OnlyAnAgentsOwnFolderTests(unittest.TestCase):
    """Empty, or only an earlier agent's data: everything in a data folder is
    the agent's and never listened to, so a folder with other things in it -
    Documents, a drive, a project - would hide the work kept there."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pa-only-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_what_may_be_a_data_folder(self) -> None:
        from personalized_agent.common.paths import data_folder_problem, holds_agent_data
        self.assertEqual(data_folder_problem(self.dir / "New"), "", "not there yet")
        self.assertEqual(data_folder_problem(self.dir), "", "empty")
        earlier = self.dir / "Earlier"
        (earlier / "internal" / "settings").mkdir(parents=True)
        (earlier / "internal" / "settings" / "settings.json").write_text("{}", encoding="utf-8")
        (earlier / "workspace").mkdir()
        (earlier / "desktop.ini").write_text("", encoding="utf-8")
        self.assertEqual(data_folder_problem(earlier), "", "an earlier agent's")
        self.assertTrue(holds_agent_data(earlier))
        self.assertFalse(holds_agent_data(self.dir / "New"))

    def test_and_what_may_not(self) -> None:
        from personalized_agent.common.paths import data_folder_problem
        docs = self.dir / "Documents"
        (docs / "Thesis").mkdir(parents=True)
        (docs / "notes.txt").write_text("mine", encoding="utf-8")
        (docs / "workspace").mkdir()
        said = data_folder_problem(docs)
        self.assertIn("Thesis", said)
        self.assertIn("notes.txt", said)
        self.assertIn("empty", said)
        a_file = self.dir / "plan.md"
        a_file.write_text("x", encoding="utf-8")
        self.assertIn("is a file", data_folder_problem(a_file))

    def test_the_program_does_not_start_in_one(self) -> None:
        from personalized_agent import __main__ as entry
        docs = self.dir / "Documents"
        docs.mkdir()
        (docs / "notes.txt").write_text("mine", encoding="utf-8")
        with mock.patch.object(sys, "argv", ["run.py", "--root", str(docs), "--headless"]), \
                mock.patch("personalized_agent.app.AgentApplication") as made:
            self.assertEqual(entry.main(), 4)
        made.assert_not_called()
        self.assertEqual(sorted(one.name for one in docs.iterdir()), ["notes.txt"],
                         "nothing made in it")

    def test_the_installer_says_the_same(self) -> None:
        script = (ROOT / "build" / "windows" / "installer.iss").read_text(encoding="utf-8-sig")
        self.assertIn("function OtherThings(Dir: String): String;", script)
        self.assertIn("OtherThings(Dir) <> ''", script)
        for code in ("en", "zh", "es", "ja", "ko", "ru", "fr", "de", "it"):
            self.assertIn(f"\n{code}.DataNotOnly=", script.replace("\r\n", "\n"), code)


if __name__ == "__main__":
    unittest.main()

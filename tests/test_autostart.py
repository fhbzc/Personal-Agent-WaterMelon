"""Starting when the user signs in (platform_layer/autostart.py): each
system's own way, in the user's own part of it; the setting the truth, on by
default; `--autostart on|off` for the installer's last page. Never the real
registry or the real home here - a fake registry and a folder of the test's."""

from __future__ import annotations

import plistlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from support import TemporaryAgent

from personalized_agent.platform_layer import autostart as startup
from personalized_agent.platform_layer.autostart import Autostart, launch_command


class _Registry:
    def __init__(self):
        self.value = None

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def delete(self):
        self.value = None


COMMAND = [r"C:\Program Files\PersonalizedAgent\PersonalizedAgent.exe"]


class EachSystemsOwnWayTests(unittest.TestCase):

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.home, ignore_errors=True)

    def test_windows_the_users_run_key(self) -> None:
        registry = _Registry()
        it = Autostart(COMMAND, system="win32", registry=registry)
        self.assertFalse(it.enabled())
        it.set(True)
        self.assertEqual(registry.value, '"C:\\Program Files\\PersonalizedAgent\\PersonalizedAgent.exe"')
        self.assertTrue(it.enabled() and it.current())
        it.set(False)
        self.assertIsNone(registry.value)
        self.assertIn("CurrentVersion\\Run", it.where())

    def test_macos_a_launch_agent(self) -> None:
        it = Autostart(["/Applications/Personalized Agent.app/Contents/MacOS/Personalized Agent"],
                       system="darwin", home=self.home)
        it.set(True)
        path = self.home / "Library" / "LaunchAgents" / f"{startup.MAC_LABEL}.plist"
        said = plistlib.loads(path.read_bytes())
        self.assertTrue(said["RunAtLoad"])
        self.assertEqual(said["ProgramArguments"], it.command)
        self.assertTrue(it.current())
        it.set(False)
        self.assertFalse(path.exists())

    def test_linux_an_autostart_entry(self) -> None:
        it = Autostart(["/opt/personalized-agent/personalized-agent", "--root", "/home/me/my agent"],
                       system="linux", home=self.home)
        it.set(True)
        path = self.home / ".config" / "autostart" / startup.LINUX_FILE
        text = path.read_text(encoding="utf-8")
        self.assertIn("Exec=/opt/personalized-agent/personalized-agent --root '/home/me/my agent'",
                      text)
        self.assertIn("X-GNOME-Autostart-enabled=true", text)
        it.set(False)
        self.assertFalse(path.exists())


class TheSettingIsTheTruthTests(unittest.TestCase):

    def test_followed_one_way_and_the_other(self) -> None:
        registry = _Registry()
        it = Autostart(COMMAND, system="win32", registry=registry)
        self.assertTrue(it.follow(True))
        self.assertFalse(it.follow(True), "already so: nothing written")
        self.assertTrue(it.follow(False))
        self.assertIsNone(registry.value)
        self.assertFalse(it.follow(False))

    def test_a_moved_installation_is_followed(self) -> None:
        registry = _Registry()
        registry.value = '"D:\\old\\PersonalizedAgent.exe"'
        Autostart(COMMAND, system="win32", registry=registry).follow(True)
        self.assertIn("Program Files", registry.value)

    def test_on_by_default(self) -> None:
        from personalized_agent.common.config.schema import SCHEMA_BY_KEY
        field = SCHEMA_BY_KEY["start_at_login"]
        self.assertTrue(field.default)
        self.assertEqual(field.section, "General")


class WhatIsStartedTests(unittest.TestCase):

    def test_from_the_source_this_python_and_run_py(self) -> None:
        command = launch_command()
        self.assertTrue(command[-1].endswith("run.py"), command)
        self.assertIn("python", Path(command[0]).name.lower())

    def test_the_installed_program(self) -> None:
        frozen = getattr(sys, "frozen", None)
        sys.frozen = True
        try:
            self.assertEqual(launch_command(), [sys.executable])
        finally:
            if frozen is None:
                del sys.frozen
            else:
                sys.frozen = frozen

    def test_with_its_root_and_headless(self) -> None:
        command = launch_command(Path("some root"), headless=True)
        self.assertEqual(command[-3:-1], ["--root", str(Path("some root").resolve())])
        self.assertEqual(command[-1], "--headless")


class _Fake:
    made = []

    def __init__(self, command, *args, **kwargs):
        self.command = command
        self.on = None
        _Fake.made.append(self)

    def set(self, on):
        self.on = on

    def follow(self, wanted):
        self.on = wanted
        return True

    def holder(self):
        return ""

    def where(self):
        return "a test's"


class TheInstallersLastPageTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.real = startup.Autostart
        startup.Autostart = _Fake
        _Fake.made = []

    def tearDown(self) -> None:
        startup.Autostart = self.real
        shutil.rmtree(self.root, ignore_errors=True)

    def _setting(self):
        from personalized_agent.common.config.store import ConfigStore
        from personalized_agent.common.paths import AppPaths
        paths = AppPaths.create(self.root)
        return ConfigStore(paths.data_dir, path=paths.settings_file).get("start_at_login")

    def test_left_unticked_it_is_kept_off(self) -> None:
        from personalized_agent.__main__ import main
        self.assertEqual(main(["--autostart", "off", "--root", str(self.root)]), 0)
        self.assertFalse(_Fake.made[-1].on)
        self.assertFalse(self._setting(), "so the program's own default does not turn it on")

    def test_ticked_it_is_on(self) -> None:
        from personalized_agent.__main__ import main
        self.assertEqual(main(["--autostart", "on", "--root", str(self.root)]), 0)
        self.assertTrue(_Fake.made[-1].on)
        self.assertTrue(self._setting())


class ChangedInSettingsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_made_so_at_once(self) -> None:
        from personalized_agent.api.routes import build_routes
        self.app.autostart = _Fake(COMMAND)

        class _Req:
            body = {"values": {"start_at_login": False}}

        build_routes(self.app)[("POST", "/api/settings")](_Req())
        self.assertFalse(self.app.autostart.on)


class AnotherCopysEntryTests(unittest.TestCase):
    """One entry for every copy of the program. One still on this machine
    keeps it: not overwritten, not removed, and turning it on here refused.
    One whose program is gone is nobody's."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.other = self.dir / "Other" / "PersonalizedAgent.exe"
        self.other.parent.mkdir()
        self.other.write_bytes(b"")
        self.registry = _Registry()
        self.registry.value = f'"{self.other}"'

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _here(self):
        return Autostart(COMMAND, system="win32", registry=self.registry)

    def test_it_is_neither_taken_nor_taken_away(self) -> None:
        it = self._here()
        self.assertEqual(Path(it.holder()), self.other)
        self.assertFalse(it.follow(True))
        self.assertFalse(it.follow(False))
        self.assertEqual(self.registry.value, f'"{self.other}"')

    def test_a_copy_run_from_the_source_holds_it_too(self) -> None:
        script = self.dir / "checkout" / "run.py"
        script.parent.mkdir()
        script.write_text("", encoding="utf-8")
        self.registry.value = f'"C:\\Python312\\pythonw.exe" "{script}" --headless'
        self.assertEqual(Path(self._here().holder()), script)

    def test_this_copys_own_entry_is_its_own(self) -> None:
        it = Autostart([str(self.other), "--headless"], system="win32", registry=self.registry)
        self.assertEqual(it.holder(), "")
        self.assertTrue(it.follow(True), "its own entry, written as it now runs")

    def test_an_entry_whose_program_is_gone_is_taken(self) -> None:
        self.other.unlink()
        it = self._here()
        self.assertEqual(it.holder(), "")
        self.assertTrue(it.follow(True))
        self.assertIn("Program Files", self.registry.value)

    def test_the_installers_tick_is_refused_and_untick_takes_nothing(self) -> None:
        import contextlib
        import io
        from personalized_agent.__main__ import main

        real = startup.Autostart
        registry = self.registry
        startup.Autostart = lambda command, *a, **k: real(COMMAND, system="win32",
                                                          registry=registry)
        root = self.dir / "root"
        said = io.StringIO()
        try:
            with contextlib.redirect_stderr(said):
                self.assertEqual(main(["--autostart", "on", "--root", str(root)]), 6)
            self.assertIn("already starts when you sign in", said.getvalue())
            self.assertEqual(main(["--autostart", "off", "--root", str(root)]), 0)
        finally:
            startup.Autostart = real
        self.assertEqual(self.registry.value, f'"{self.other}"', "the other copy's, untouched")


class RefusedInSettingsTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_it_cannot_be_saved_on_while_another_copy_holds_it(self) -> None:
        from personalized_agent.api.errors import ApiError
        from personalized_agent.api.routes import build_routes

        class _Held(_Fake):
            def holder(self):
                return r"D:\Elsewhere\PersonalizedAgent.exe"

        self.app.autostart = _Held(COMMAND)
        self.app.config.set("start_at_login", False)
        routes = build_routes(self.app)
        self.assertEqual(routes[("GET", "/api/settings")](None)["start_at_login_held_by"],
                         r"D:\Elsewhere\PersonalizedAgent.exe")

        class _Req:
            body = {"values": {"start_at_login": True}}

        with self.assertRaises(ApiError) as refused:
            routes[("POST", "/api/settings")](_Req())
        self.assertEqual(refused.exception.status, 409)
        self.assertIn("already starts when you sign in", str(refused.exception))
        self.assertFalse(self.app.config.get("start_at_login"), "nothing was written")


class TheInstallerAsksFirstTests(unittest.TestCase):
    """The installer's own side, read from its script: the last page refuses a
    tick another copy holds, in every language it speaks."""

    def test_its_last_page_and_its_words(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "build" / "windows"
                / "installer.iss").read_text(encoding="utf-8-sig")
        self.assertIn("function AutostartHolder", text)
        self.assertIn("CustomMessage('StartAtLoginTaken')", text)
        from personalized_agent.common.languages import DISPLAY_CODES
        for code in DISPLAY_CODES:
            self.assertIn(f"\n{code}.StartAtLoginTaken=", text)


if __name__ == "__main__":
    unittest.main()

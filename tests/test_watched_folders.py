"""WHAT THE FILE LISTENER WATCHES (the user, 2026-11-16: "默认看桌面、文档、下载，再自动加上你给工作任务指定过的项目文件夹
... 另外再加上 排除 AppData、临时文件、Windows、Program Files 这些系统目录"; "需要这个迁移").

A copy watching every drive was busy with the machine's own writing all day, took minutes to close, and its update
could not move its folder while it did. The user's own folders by default; a folder given to a piece of work is
watched while the work has it; a copy installed before - whose settings file holds every drive, written there as
the default - moves to the user's folders once; what is left out is dropped at the door; a stop takes seconds."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from support import TemporaryAgent

from personalized_agent.common.config import schema
from personalized_agent.common.config.store import ConfigStore
from personalized_agent.listener.providers.filesystem import FilesystemProvider


class _Config:
    def __init__(self, **values) -> None:
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def lines(self, key):
        return [line for line in str(self.values.get(key) or "").splitlines() if line.strip()]


class AnInstalledCopyMovesOnceTests(unittest.TestCase):
    def setUp(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.dir = Path(work.name)
        self.settings = self.dir / "settings.json"

    def _stored(self, **values) -> ConfigStore:
        self.settings.write_text(json.dumps(values), encoding="utf-8")
        return ConfigStore(self.dir, path=self.settings)

    def test_every_drive_kept_as_the_default_becomes_the_users_folders(self) -> None:
        store = self._stored(listener_watch_paths=schema.every_drive(),
                             listener_directory_exclusions="C:\\Windows")
        self.assertEqual(store.get("listener_watch_paths"), schema.default_watch_paths())
        if os.name == "nt":
            self.assertIn(str(Path.home() / "AppData"), store.get("listener_directory_exclusions").splitlines())
            self.assertIn("C:\\Windows", store.get("listener_directory_exclusions").splitlines(), "theirs kept")
        # Written back, and once: every drive chosen again later stays.
        again = ConfigStore(self.dir, path=self.settings)
        self.assertEqual(again.get("listener_watch_paths"), schema.default_watch_paths())
        again.set("listener_watch_paths", schema.every_drive())
        self.assertEqual(ConfigStore(self.dir, path=self.settings).get("listener_watch_paths"),
                         schema.every_drive())

    def test_a_list_of_the_users_own_is_kept(self) -> None:
        mine = str(Path.home() / "Research")
        store = self._stored(listener_watch_paths=f"{mine}\nC:\\")
        self.assertEqual(store.get("listener_watch_paths"), f"{mine}\nC:\\")

    def test_every_drive_among_the_users_own_defaults_is_kept(self) -> None:
        (self.dir / "user-defaults.json").write_text(
            json.dumps({"listener_watch_paths": schema.every_drive()}), encoding="utf-8")
        store = self._stored(listener_watch_paths=schema.every_drive())
        self.assertEqual(store.get("listener_watch_paths"), schema.every_drive(), "chosen by them")

    def test_appdata_is_not_written_twice(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows only")
        app_data = str(Path.home() / "AppData")
        store = self._stored(listener_directory_exclusions=app_data)
        self.assertEqual(store.get("listener_directory_exclusions").splitlines().count(app_data), 1)


class TheWorksFoldersAreWatchedTests(unittest.TestCase):
    def setUp(self) -> None:
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.mine = self.root / "Desktop"
        self.project = self.root / "Projects" / "orchard"
        self.inside = self.mine / "notes"
        for one in (self.mine, self.project, self.inside):
            one.mkdir(parents=True)
        (self.root / "one-file.txt").write_text("x", encoding="utf-8")

    def test_a_given_folder_beside_the_settings_own_and_not_twice(self) -> None:
        given = [str(self.project), str(self.inside), str(self.root / "one-file.txt"), str(self.root / "gone")]
        provider = FilesystemProvider(_Config(listener_watch_paths=str(self.mine)), more_roots=lambda: given)
        self.assertEqual(provider.watch_paths(), [self.mine, self.project],
                         "a folder held by a watched one, a file and a folder gone add nothing")

    def test_a_given_folder_holding_a_watched_one_replaces_it(self) -> None:
        provider = FilesystemProvider(_Config(listener_watch_paths=str(self.inside)),
                                      more_roots=lambda: [str(self.mine)])
        self.assertEqual(provider.watch_paths(), [self.mine])

    def test_the_application_hands_the_works_folders_and_says_when_they_change(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        told = []
        app.work.on_folders_changed = lambda: told.append(1)
        unit_id = "unit_orchard"
        app.work.grant(unit_id, str(self.project))
        app.work.let_read(unit_id, [str(self.mine), str(self.root / "one-file.txt")])
        self.assertEqual(set(app.work.project_folders()), {str(self.project.resolve()), str(self.mine)})
        app.work.revoke(unit_id, str(self.project.resolve()))
        self.assertEqual(app.work.project_folders(), [str(self.mine)])
        self.assertEqual(len(told), 3, "given, read, taken back")
        self.assertIs(app.listener.work_folders.__self__, app.work)
        self.assertEqual(app.listener._more_watch_roots(), [str(self.mine)])

    def test_never_a_whole_drive_or_the_home_folder_and_a_finished_work_keeps_its_own(self) -> None:
        """2026-11-16: a folder given to read may be a whole drive - never watched whole; a work that is done keeps
        its folders ("任务做完的东西监听了是不是也不会有event ... 要不就继续留着")."""
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        work = agent.app.work
        drive = Path(Path.home().anchor)
        work.db.execute("INSERT INTO kv (key, value) VALUES (?, ?)",
                        ("work.read_also.unit_done", json.dumps([str(drive), str(Path.home()), str(self.project)])))
        work.db.execute("INSERT INTO work_runs (id, unit_id, unit_name, folder, status, started_at, ended_at, "
                        "updated_at) VALUES ('r1', 'unit_done', 'Done', '', 'done', 'x', 'x', 'x')")
        self.assertEqual(work.project_folders(), [str(self.project)])

    def test_looked_at_again_on_its_own(self) -> None:
        given = []
        provider = FilesystemProvider(_Config(listener_watch_paths=str(self.mine)), more_roots=lambda: list(given))
        restarted = []
        provider.settings_changed = lambda: restarted.append(1)
        provider.LOOK_AGAIN_SECONDS = 0.05
        provider._roots = {str(self.mine): "watching"}
        provider._start_looking_again()
        time.sleep(0.2)
        self.assertEqual(restarted, [], "nothing new: left as it is")
        given.append(str(self.project))
        provider._start_looking_again()
        deadline = time.time() + 3
        while not restarted and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(restarted, [1], "a folder given elsewhere taken up")
        provider._stop.set()


class _Raw:
    def __init__(self, path: str, event_type: str = "created") -> None:
        self.event_type, self.src_path, self.is_directory = event_type, path, False


class LeftOutAtTheDoorAndQuickToStopTests(unittest.TestCase):
    def test_what_the_settings_leave_out_is_never_held(self) -> None:
        provider = FilesystemProvider(_Config())
        provider._settings_refuse = lambda path: "AppData" in str(path)
        provider._in_front = lambda: ("", "")
        provider._excerpt = lambda event_type, data: None
        provider.handle_raw(_Raw(str(Path.home() / "AppData" / "Local" / "x.log")))
        self.assertEqual(provider._holding, [], "dropped at once")
        provider.handle_raw(_Raw(str(Path.home() / "Desktop" / "report.docx")))
        self.assertEqual(len(provider._holding), 1)

    def test_a_stop_judges_for_two_seconds_and_lets_the_rest_go(self) -> None:
        provider = FilesystemProvider(_Config())
        sent = []

        def slow(event_type, data, happened_at=None):
            time.sleep(0.05)
            sent.append(data["path"])

        provider._send = slow
        now = time.time()
        # One at a time, far enough apart to be judged one by one, not as a sweep.
        provider._holding = [("filesystem.created", {"path": f"f{n}"}, now + n * 2.0) for n in range(400)]
        started = time.monotonic()
        provider.flush_held_events(budget=0.5)
        spent = time.monotonic() - started
        self.assertLess(spent, 1.5)
        self.assertTrue(sent)
        self.assertEqual(len(sent) + provider._let_go_at_stop, 400, "each judged or counted")
        self.assertGreater(provider._let_go_at_stop, 0)
        self.assertEqual(provider.STOP_FLUSH_SECONDS, 2.0)

    def test_the_stop_and_the_woken_flusher_use_the_budget(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "personalized_agent" / "listener" / "providers"
                  / "filesystem.py").read_text(encoding="utf-8")
        self.assertIn("self.flush_held_events(budget=self.STOP_FLUSH_SECONDS)", source)
        self.assertIn("budget=self.STOP_FLUSH_SECONDS if self._stop.is_set() else None", source)


if __name__ == "__main__":
    unittest.main()

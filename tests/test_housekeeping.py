"""Raw activity is kept for a while and then let go, by age."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from support import TemporaryAgent

from personalized_agent.common.config import schema
from personalized_agent.common.config.store import ConfigStore
from personalized_agent.common.events.models import ListenerEvent, RawEdit, utcnow
from personalized_agent.data_manage.storage.database import SCHEMA_SQL, Database


class RawActivityIsLetGoByAgeTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _event(self, days_ago: float) -> ListenerEvent:
        event = ListenerEvent(event_type="application.focus", source="test",
                              timestamp=utcnow() - timedelta(days=days_ago),
                              application="Editor")
        self.app.raw_store.add_event(event)
        return event

    def test_older_than_the_window_goes_and_the_rest_stays(self) -> None:
        self.app.config.set("raw_activity_keep_days", 14)
        self._event(30)
        self._event(15)
        kept = self._event(2)
        said = self.app.housekeeping.run_once()
        self.assertEqual(said["pruned"]["events"], 2)
        self.assertEqual(self.app.raw_store.count_events(), 1)
        self.assertEqual([e.id for e in self.app.raw_store.events_by_ids([kept.id])],
                         [kept.id])

    def test_in_batches_the_lock_let_go_and_stopped_when_closing(self) -> None:
        """2026-11-12 ("怎么一直卡住没有退出"): one DELETE of them all held every writer and quitting -
        now a batch at a time, and between batches it stops when the application closes."""
        store = self.app.raw_store
        store.PRUNE_BATCH = 2
        for _n in range(5):
            self._event(30)
        kept = self._event(1)
        batches = []

        def going() -> bool:
            batches.append(store.count_events())
            return len(batches) < 2                       # closing after the first batch
        said = store.prune_older_than(8, going=going)
        self.assertEqual(said["events"], 2, "one batch, then stopped")
        self.assertEqual(store.count_events(), 4)
        said = store.prune_older_than(8)
        self.assertEqual((said["events"], store.count_events()), (3, 1), "the rest the next time")
        self.assertEqual([one.id for one in store.events_by_ids([kept.id])], [kept.id])

    def test_what_points_at_a_raw_event_is_indexed(self) -> None:
        """A raw event deleted no longer has the WeChat tables read whole to unlink it."""
        names = {row["name"] for row in self.app.db.query(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
        for name in ("idx_wechat_capture_event", "idx_wechat_observation_event",
                     "idx_wechat_observation_capture", "idx_wechat_alias_capture"):
            self.assertIn(name, names)

    def test_nothing_waits_on_being_read_first(self) -> None:
        """There is no step that marks activity as read any more, so age
        alone decides - an old row is let go whether or not anything read it."""
        self.app.config.set("raw_activity_keep_days", 7)
        self._event(10)
        self.app.housekeeping.run_once()
        self.assertEqual(self.app.raw_store.count_events(), 0)
        # And there is no flag left to wait on.
        self.assertFalse(hasattr(ListenerEvent, "processed"))
        self.assertFalse(hasattr(self.app.raw_store, "unprocessed_edits"))

    def test_an_edit_is_still_written_and_read(self) -> None:
        edit = self.app.raw_store.add_edit(RawEdit(event_type="agent.action",
                                                   source="test", action="did it"))
        [back] = self.app.raw_store.edits_by_ids([edit.id])
        self.assertEqual(back.action, "did it")

    def test_a_window_of_nothing_keeps_everything(self) -> None:
        self.app.config.set("raw_activity_keep_days", 0)
        self._event(400)
        self.app.housekeeping.run_once()
        self.assertEqual(self.app.raw_store.count_events(), 1)


class TheDefaultWindowTests(unittest.TestCase):
    """Eight days: the Overview's week, and a day to spare for time zones."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pa-test-"))
        self.settings = self.dir / "settings.json"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def _open(self, stored, chosen=None):
        self.settings.write_text(json.dumps(stored), encoding="utf-8")
        if chosen is not None:
            (self.dir / "user-defaults.json").write_text(json.dumps(chosen),
                                                         encoding="utf-8")
        return ConfigStore(self.dir, path=self.settings)

    def test_it_is_eight(self) -> None:
        self.assertEqual(schema.defaults()["raw_activity_keep_days"], 8)

    def test_the_old_default_written_to_the_file_moves_to_the_new(self) -> None:
        # Every value is written to the file, the defaults included - so an
        # installation that never touched this has 14 written down.
        self.assertEqual(self._open({"raw_activity_keep_days": 14})
                         .get("raw_activity_keep_days"), 8)

    def test_moved_once_so_fourteen_chosen_later_stays(self) -> None:
        config = self._open({"raw_activity_keep_days": 14})
        self.assertEqual(config.get("raw_activity_keep_days"), 8)
        config.set("raw_activity_keep_days", 14)         # chosen in Settings
        again = ConfigStore(self.dir, path=self.settings)
        self.assertEqual(again.get("raw_activity_keep_days"), 14)

    def test_states_every_twelve_hours(self) -> None:
        config = self._open({"state_task_min_hours": 2.0,
                             "state_heartbeat_every_hours": 6.0})
        self.assertEqual(config.get("state_task_min_hours"), 2.0)
        self.assertEqual(config.get("state_heartbeat_every_hours"), 12.0)
        # Chosen in Settings, kept.
        chosen = self._open({"state_heartbeat_every_hours": 6.0},
                            chosen={"state_heartbeat_every_hours": 6.0})
        self.assertEqual(chosen.get("state_heartbeat_every_hours"), 6.0)

    def test_conversations_from_every_assistant_once(self) -> None:
        # Empty - the old default, even kept among the user's own - becomes
        # every assistant found, once.
        config = self._open({"listener_assistant_content_tools": ""},
                            chosen={"listener_assistant_content_tools": ""})
        self.assertEqual(config.get("listener_assistant_content_tools"), "*")
        self.assertEqual(config.user_defaults().get("listener_assistant_content_tools"), "*")
        # Emptied again in Settings, it stays empty.
        config.set("listener_assistant_content_tools", "")
        again = ConfigStore(self.dir, path=self.settings)
        self.assertEqual(again.get("listener_assistant_content_tools"), "")
        # And a list someone ticked is theirs.
        ticked = self._open({"listener_assistant_content_tools": "Claude Code",
                             "assistant_conversations_all": False})
        self.assertEqual(ticked.get("listener_assistant_content_tools"), "Claude Code")

    def test_fourteen_chosen_is_kept(self) -> None:
        config = self._open({"raw_activity_keep_days": 14},
                            chosen={"raw_activity_keep_days": 14})
        self.assertEqual(config.get("raw_activity_keep_days"), 14)

    def test_any_other_value_is_kept(self) -> None:
        self.assertEqual(self._open({"raw_activity_keep_days": 30})
                         .get("raw_activity_keep_days"), 30)
        self.assertEqual(self._open({"raw_activity_keep_days": 0})
                         .get("raw_activity_keep_days"), 0)


class TheProcessedIndexesAreGoneTests(unittest.TestCase):

    def test_an_existing_database_loses_them(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "old.db"
            connection = sqlite3.connect(path)
            connection.executescript(SCHEMA_SQL)
            connection.executescript(
                "CREATE INDEX idx_raw_events_processed ON raw_events(processed, timestamp);"
                "CREATE INDEX idx_raw_edits_processed ON raw_edits(processed, timestamp);")
            connection.commit()
            connection.close()

            Database(path).close()             # opening it is what migrates it

            check = sqlite3.connect(path)
            try:
                names = {row[0] for row in check.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'")}
            finally:
                check.close()
            self.assertNotIn("idx_raw_events_processed", names)
            self.assertNotIn("idx_raw_edits_processed", names)
            self.assertIn("idx_raw_events_recent", names)


if __name__ == "__main__":
    unittest.main()

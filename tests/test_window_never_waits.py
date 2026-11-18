"""The window never waits for the database, and when it stands still it is said (the user, 2026-11-16:
"我在chat里打字很慢，发送了点击半天发不出去").

The chat bubble kept its draft with a write on Tk's own thread; while another write held the database -
the old WeChat rows cleared in one write of 6.9 s - the avatar and the bubble stood still.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from support import TemporaryAgent

from personalized_agent.data_manage.storage.wechat_store import WechatStore
from personalized_agent.ui import stall_watch
from personalized_agent.ui.later_writes import LaterWrites


class LaterWritesTests(unittest.TestCase):
    def test_handed_over_at_once_written_in_turn_the_newest_for_a_key(self) -> None:
        inside, free, written = threading.Event(), threading.Event(), []

        def write(key, value):
            inside.set()
            free.wait(5)
            written.append((key, value))
        later = LaterWrites(write)
        began = time.monotonic()
        later.put("first", "1")
        self.assertTrue(inside.wait(5))                # the writer is held in a write
        self.assertEqual(later.waiting("first", None), "1", "being written: still read back")
        later.put("draft", "H")
        later.put("draft", "He")
        later.put("tick", "[1]")
        later.put("draft", "Hello")
        self.assertLess(time.monotonic() - began, 0.5, "nothing waited for the write")
        self.assertEqual(later.waiting("draft", None), "Hello")
        free.set()
        self.assertTrue(later.flush(5))
        self.assertEqual(written, [("first", "1"), ("tick", "[1]"), ("draft", "Hello")],
                         "in turn, and for a key only its newest")
        self.assertIsNone(later.waiting("draft", None))
        self.assertIsNone(later.waiting("first", None))

    def test_a_write_that_fails_does_not_stop_the_next(self) -> None:
        written = []

        def write(key, value):
            if key == "bad":
                raise RuntimeError("no")
            written.append(key)
        later = LaterWrites(write)
        with self.assertLogs("personalized_agent.ui.later_writes", "WARNING"):
            later.put("bad", 1)
            later.put("good", 2)
            self.assertTrue(later.flush(5))
        self.assertEqual(written, ["good"])


class TheBubblesDraftTests(unittest.TestCase):
    def test_the_draft_is_kept_while_the_database_is_held_and_written_after(self) -> None:
        from personalized_agent.ui.desktop import DesktopAgent
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        desk = SimpleNamespace(app=agent.app)
        held, let_go = threading.Event(), threading.Event()

        def hold():
            with agent.app.db.write():
                held.set()
                let_go.wait(10)
        holder = threading.Thread(target=hold, daemon=True)
        holder.start()
        self.assertTrue(held.wait(5))
        began = time.monotonic()
        DesktopAgent._keep_chat_draft(desk, "Half a thought")
        self.assertLess(time.monotonic() - began, 0.2, "the window did not wait for the database")
        self.assertEqual(DesktopAgent._chat_draft(desk), "Half a thought", "read back from what waits")
        let_go.set()
        holder.join(5)
        self.assertTrue(desk._later.flush(5))
        self.assertEqual(DesktopAgent._chat_draft(SimpleNamespace(app=agent.app)), "Half a thought")

    def test_no_write_is_left_on_the_windows_thread(self) -> None:
        import inspect

        from personalized_agent.ui import desktop
        source = inspect.getsource(desktop.DesktopAgent)
        # The one write left is the one the handed-over writes are written with.
        self.assertEqual(source.count("self.app.db.execute("), 1)
        self.assertIn("def _write_kv", source)


class TheStallIsSaidTests(unittest.TestCase):
    def make(self):
        now = [100.0]
        said = []
        frame = SimpleNamespace()
        watch = stall_watch.StallWatch(clock=lambda: now[0], frames=lambda: {7: frame}, say=said.append)
        watch.thread_id = 7
        watch.last_beat = now[0]
        return watch, now, said

    def test_where_the_thread_stood_and_how_long(self) -> None:
        watch, now, said = self.make()
        real = stall_watch.where
        stall_watch.where = lambda frame: "desktop.py:1 _keep_chat_draft"
        self.addCleanup(setattr, stall_watch, "where", real)
        now[0] += 0.3
        watch.look()
        self.assertIsNone(watch.stuck_at, "not yet")
        now[0] += 1.0
        watch.look()
        self.assertEqual(watch.stuck_at, "desktop.py:1 _keep_chat_draft")
        now[0] += 2.0
        watch.beat()
        self.assertEqual(len(said), 1)
        self.assertRegex(said[0], r"stood still for 3\.\d s")
        self.assertIn("_keep_chat_draft", said[0])

    def test_the_computer_asleep_is_not_said(self) -> None:
        watch, now, said = self.make()
        now[0] += 600
        watch.look(waited_too_long=True)
        watch.beat()
        self.assertEqual(said, [])

    def test_said_at_most_so_often_the_rest_counted(self) -> None:
        watch, now, said = self.make()
        stall_watch_where = stall_watch.where
        stall_watch.where = lambda frame: "here"
        self.addCleanup(setattr, stall_watch, "where", stall_watch_where)
        for _ in range(3):
            now[0] += 1.0
            watch.look()
            now[0] += 0.5
            watch.beat()
        self.assertEqual(len(said), 1)
        now[0] += stall_watch.SAID_EVERY_SECONDS
        watch.beat()                                # a beat on time: nothing to say
        now[0] += 1.0
        watch.look()
        now[0] += 0.5
        watch.beat()
        self.assertEqual(len(said), 2)
        self.assertIn("2 more since the last said", said[1])

    def test_where_its_time_goes_is_counted_and_said(self) -> None:
        import sys
        watch, now, said = self.make()
        times = []
        watch.say_time = times.append
        busy = sys._getframe()
        waiting = SimpleNamespace(f_code=SimpleNamespace(co_name="mainloop",
                                                         co_filename=r"C:\Python\Lib\tkinter\__init__.py"))
        self.assertTrue(stall_watch.waiting_on_tk(waiting))
        self.assertFalse(stall_watch.waiting_on_tk(busy))
        for n in range(10):
            watch.count(busy if n < 3 else waiting)
        self.assertEqual(watch.looks, 10)
        self.assertEqual(watch.running[""], 7)
        now[0] += stall_watch.TIME_SAID_EVERY_SECONDS
        watch.count(waiting)
        self.assertEqual(len(times), 1)
        self.assertIn("running 27% of the looks", times[0])
        self.assertIn("test_where_its_time_goes_is_counted_and_said", times[0])
        self.assertEqual(watch.looks, 0, "counted afresh")

    def test_the_threads_running_beside_it_are_named_the_waiting_ones_not(self) -> None:
        import sys
        stop = threading.Event()

        def spin():
            n = 0
            while not stop.is_set():
                n += 1

        busy = threading.Thread(target=spin, name="busy-one", daemon=True)
        idle = threading.Thread(target=lambda: stop.wait(10), name="idle-one", daemon=True)
        busy.start()
        idle.start()
        self.addCleanup(stop.set)
        time.sleep(0.05)
        said = stall_watch.others_running(sys._current_frames(), threading.get_ident())
        self.assertIn("busy-one", said)
        self.assertNotIn("idle-one", said)

    def test_where_names_the_innermost_frame_when_none_is_the_projects(self) -> None:
        import sys

        def inner():
            return stall_watch.where(sys._getframe())
        said = inner()
        self.assertIn("test_window_never_waits.py", said)
        self.assertIn("inner", said.split(" < ")[0])


class TheOldWechatRowsGoInBatchesTests(unittest.TestCase):
    def make(self, rows: int):
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        for table in WechatStore.PRUNED + ("listener_wechat_conversations",):
            more = ", conversation_id TEXT" if table == "listener_wechat_messages" else ""
            conn.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, last_seen_at TEXT{more})")
            conn.executemany(f"INSERT INTO {table} (id, last_seen_at) VALUES (?, ?)",
                             [(f"{table}-{n}", "2026-01-01") for n in range(rows)])
        conn.execute("INSERT INTO listener_wechat_conversations (id, last_seen_at) VALUES ('kept', '2026-01-01')")
        conn.execute("INSERT INTO listener_wechat_messages (id, last_seen_at, conversation_id) "
                     "VALUES ('new', '2026-12-01', 'kept')")
        writes = []

        @contextmanager
        def write():
            writes.append(1)
            yield conn
            conn.commit()
        return conn, SimpleNamespace(write=write), writes

    def test_each_write_takes_a_batch_and_lets_go(self) -> None:
        conn, db, writes = self.make(5)
        said = WechatStore.prune(SimpleNamespace(PRUNED=WechatStore.PRUNED, PRUNE_BATCH=2), db, "2026-06-01", batch=2)
        self.assertEqual(said, {"wechat_messages": 5, "wechat_captures": 5})
        self.assertGreaterEqual(len(writes), 6 * 3)
        for table in WechatStore.PRUNED:
            left = conn.execute(f"SELECT id FROM {table}").fetchall()
            self.assertEqual(left, [("new",)] if table == "listener_wechat_messages" else [])
        # A conversation one of whose messages is left stays.
        self.assertEqual(conn.execute("SELECT id FROM listener_wechat_conversations").fetchall(), [("kept",)])

    def test_closing_leaves_the_rest_for_the_next_time(self) -> None:
        conn, db, writes = self.make(5)
        asked = []

        def going():
            asked.append(1)
            return len(asked) <= 2
        WechatStore.prune(SimpleNamespace(PRUNED=WechatStore.PRUNED, PRUNE_BATCH=2), db, "2026-06-01", going=going, batch=2)
        self.assertEqual(len(writes), 2)
        self.assertEqual(conn.execute("SELECT count(*) FROM listener_wechat_observations").fetchone()[0], 1)


class ThePointersAreIndexedTests(unittest.TestCase):
    def test_every_column_pointing_at_a_wechat_row_has_an_index(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        conn = agent.app.db.connection
        tables = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'listener_wechat%'")]
        missing = []
        for table in tables:
            leading = set()
            for index in conn.execute(f"PRAGMA index_list('{table}')"):
                columns = [one[2] for one in conn.execute(f"PRAGMA index_info('{index[1]}')")]
                if columns:
                    leading.add(columns[0])
            for key in conn.execute(f"PRAGMA foreign_key_list('{table}')"):
                if key[2].startswith("listener_wechat") and key[3] not in leading:
                    missing.append(f"{table}.{key[3]}")
        self.assertEqual(missing, [])




class NoStateHidesAMethodTests(unittest.TestCase):
    """2026-11-16: `self._peek_on = False` (the side panel's tab) hid the avatar's own `_peek_on` method - every frame
    of the avatar failed ("TypeError: 'bool' object is not callable"). No value the desktop keeps on itself is named
    as one of its methods."""

    def test_no_attribute_set_on_the_desktop_is_named_as_its_method(self) -> None:
        import ast
        import inspect

        from personalized_agent.ui import desktop
        tree = ast.parse(inspect.getsource(desktop))
        agent = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DesktopAgent")
        methods = {node.name for node in agent.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        set_on_self = set()
        for node in ast.walk(agent):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
                node, (ast.AnnAssign, ast.AugAssign)) else []
            for target in targets:
                for one in (target.elts if isinstance(target, ast.Tuple) else [target]):
                    if isinstance(one, ast.Attribute) and isinstance(one.value, ast.Name) and one.value.id == "self":
                        set_on_self.add(one.attr)
        self.assertEqual(sorted(set_on_self & methods), [])


if __name__ == "__main__":
    unittest.main()

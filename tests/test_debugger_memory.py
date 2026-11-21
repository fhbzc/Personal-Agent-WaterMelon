"""THE DEBUGGER'S LOOK AT WHAT HOLDS THE PROGRAM'S MEMORY (the user, 2026-11-20: "主进程 900MB ... 继续分析"): the
process's figures, the live objects by type, the largest collections and whose they are - types, counts, sizes and
names only, never what any of it holds."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import unittest
import urllib.request

from support import TemporaryAgent, keyed


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MemoryLookTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.port = _free_port()
        self.app.config.set("api_port", self.port)
        from personalized_agent.api.server import ApiServer

        self.server = ApiServer(self.app)
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.agent.cleanup()

    def test_types_sizes_and_holders_never_contents(self) -> None:
        secret = {f"k{i}": "A PRIVATE WORD" for i in range(25000)}
        self.app._memory_test_holder = secret                       # a large collection, held by an attribute
        url = f"http://127.0.0.1:{self.port}/api/debugger/memory"
        with urllib.request.urlopen(keyed(self.app, url), timeout=120) as response:
            raw = response.read().decode("utf-8")
        said = json.loads(raw)
        self.assertIn("process_mb", said)
        self.assertTrue(any(one["type"] == "builtins.dict" for one in said["types"]))
        held = [one for one in said["largest_collections"] if one["length"] == 25000]
        self.assertTrue(held, "the large dict is found")
        self.assertTrue(any("_memory_test_holder" in owner for owner in held[0]["held_by"]), held[0]["held_by"])
        self.assertNotIn("A PRIVATE WORD", raw, "never what it holds")
        self.assertNotIn("k24999", raw)


if __name__ == "__main__":
    unittest.main()


class LessMemoryTests(unittest.TestCase):
    """2026-11-20: the memory looked at - "这3个要改", "其它改动都做"."""

    def test_numpy_loaded_with_one_thread_and_the_setting_taken_off_again(self) -> None:
        import os
        import subprocess
        import sys
        # (its private memory on Windows; a Mac's and Linux's psutil has none - its resident memory there, 2026-11-21)
        code = ("import os, sys, psutil; import personalized_agent.__main__ as m; m._blas_one_thread(); "
                "info = psutil.Process().memory_info(); "
                "print(round(getattr(info, 'private', info.rss) / 2**20), os.environ.get('OPENBLAS_NUM_THREADS'))")
        env = {k: v for k, v in os.environ.items() if k not in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}
        said = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120,
                              cwd=str(Path(__file__).resolve().parents[1])).stdout.split()
        self.assertLess(int(said[0]), 200, "one thread's buffers, not fourteen's (439 MB measured)")
        self.assertEqual(said[1], "None", "the children keep every core")

    def test_stacks_said_without_their_source_read(self) -> None:
        import linecache
        import sys
        from personalized_agent.ui import stall_watch
        linecache.clearcache()
        stall_watch.where(sys._getframe())
        self.assertFalse([one for one in linecache.cache.values() if len(one) > 2 and one[2]])

    def test_the_rest(self) -> None:
        root = Path(__file__).resolve().parents[1] / "personalized_agent"
        reading = (root / "learning" / "reading.py").read_text(encoding="utf-8")
        self.assertIn('marks["files"] = dict(kept["files"])', reading, "the files' table copied, not read again")
        self.assertIn("seen.pop(key, None)", reading, "what is never read is not marked")
        embedding = (root / "heartbeat" / "states" / "embedding.py").read_text(encoding="utf-8")
        self.assertIn('kept[state_id] = array.array("f", row["vector"])', embedding)
        text = (root / "platform_layer" / "window_text.py").read_text(encoding="utf-8")
        self.assertIn("fresh = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)", text)
        desktop = (root / "ui" / "desktop.py").read_text(encoding="utf-8")
        self.assertIn('if getattr(self, "character_hidden", False):\n            self._animate_job = self.root.after(1000, self._animate)', desktop)

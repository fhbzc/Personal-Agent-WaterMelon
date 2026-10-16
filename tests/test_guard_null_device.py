"""The null device where the system refuses it (runtime/guard.py).

A process in an AppContainer on Windows Server - GitHub's runners - is
refused "nul" itself, so subprocess.DEVNULL and pytest's start failed under
the guard with "Permission denied: 'nul'". The guard stands a file of the
run's own in for it. The refusal is reproduced here by a system open that
refuses the null device, installed before the guard, as the sandbox's is.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

GUARD_DIR = Path(__file__).resolve().parents[1] / "personalized_agent" / "runtime"

CHILD = r'''
import builtins, json, os, sys
sys.path.insert(0, GUARD_DIR)
NULLS = {"nul", "nul:", "\\\\.\\nul", "/dev/null", os.devnull.lower()}
system_os_open, system_open = os.open, builtins.open
def refusing_os_open(path, flags, *more, **kw):
    if isinstance(path, str) and path.lower() in NULLS:
        raise PermissionError(13, "Permission denied", path)
    return system_os_open(path, flags, *more, **kw)
def refusing_open(file, *more, **kw):
    if isinstance(file, str) and file.lower() in NULLS:
        raise PermissionError(13, "Permission denied", file)
    return system_open(file, *more, **kw)
os.open, builtins.open = refusing_os_open, refusing_open
import guard
guard.install()
said = {}
# What subprocess.DEVNULL opens (Popen._get_devnull).
handle = os.open(os.devnull, os.O_RDWR)
os.write(handle, b"thrown away")
os.close(handle)
said["devnull_rc"] = 0
with open(os.devnull, "w", encoding="utf-8") as sink:
    sink.write("thrown away")
with open(os.devnull, encoding="utf-8") as source:
    said["read"] = source.read()
try:
    open("C:/Windows/not-the-null-device.txt" if os.name == "nt" else "/etc/not-null", "w")
    said["outside"] = "written"
except Exception as exc:
    said["outside"] = type(exc).__name__
print(json.dumps(said))
'''


class TheNullDeviceWhereTheSystemRefusesItTests(unittest.TestCase):

    def test_devnull_still_works_and_nothing_else_is_opened_up(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            env = dict(os.environ, AGENT_WORKSPACE=work)
            code = f"GUARD_DIR = {str(GUARD_DIR)!r}\n" + CHILD
            done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                                  text=True, timeout=120, env=env)
            self.assertEqual(done.returncode, 0, done.stderr)
            said = json.loads(done.stdout.strip().splitlines()[-1])
            stand_in = Path(work) / ".agent_runtime" / "null-device"
            self.assertEqual(said["devnull_rc"], 0)
            # What was written went nowhere that is read back as the device.
            self.assertEqual(said["read"], "")
            self.assertTrue(stand_in.exists())
        # Only the null device is stood in for; any other refusal stands.
        self.assertNotEqual(said["outside"], "written")


if __name__ == "__main__":
    unittest.main()

"""A build made without the Debugger starts (section 14.33, as amended).

build.py --no-debugger - as every released download is built - leaves the
package personalized_agent.debugger out. The application imported it at
start-up all the same, and the released program stopped before it started.
Other tests switch the flag that says the Debugger is missing; this one takes
the package itself away, as the build does, and starts the application.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHILD = r'''
import importlib.abc, json, sys

class LeftOut(importlib.abc.MetaPathFinder):
    """As the packaged build: the Debugger's package is not there."""
    def find_spec(self, name, path=None, target=None):
        if name == "personalized_agent.debugger" or name.startswith("personalized_agent.debugger."):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None

sys.meta_path.insert(0, LeftOut())
from personalized_agent.app import AgentApplication
from personalized_agent.api.routes import build_routes
from personalized_agent.build_flags import debugger_available

app = AgentApplication(root=sys.argv[1])
routes = build_routes(app)
print(json.dumps({
    "available": debugger_available(),
    "status_says": app.status()["debugger_built_in"],
    "status_route": ("GET", "/api/status") in routes,
    "debugger_routes": sorted(p for (_m, p) in routes if p.startswith("/api/debugger")),
    "loaded": sorted(m for m in sys.modules if m.startswith("personalized_agent.debugger")),
}))
'''


class AReleasedBuildStartsTests(unittest.TestCase):

    def test_the_application_starts_with_no_debugger_package(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            done = subprocess.run([sys.executable, "-c", CHILD, str(Path(work) / "root")],
                                  capture_output=True, text=True, timeout=180,
                                  env=env, cwd=str(ROOT))
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        said = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertFalse(said["available"])
        self.assertFalse(said["status_says"], "the page would still show the Debugger's tabs")
        self.assertTrue(said["status_route"])
        self.assertEqual(said["debugger_routes"], [])
        self.assertEqual(said["loaded"], [])


if __name__ == "__main__":
    unittest.main()

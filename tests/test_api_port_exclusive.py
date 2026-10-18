"""One program on the agent's port.

On Windows, SO_REUSEADDR - which Python's HTTPServer sets - lets a second
program bind a port another is already listening on, with no error, and each
connection then goes to either of them. A second agent left running on the
same port answered the first one's sign-in links, which it had never issued:
"This link has already been used, or has expired". The port is now held
exclusively there, as macOS and Linux already hold a listening port.
"""

from __future__ import annotations

import socket
import unittest

from support import TemporaryAgent


def _free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class OneProgramOnThePortTests(unittest.TestCase):

    def setUp(self) -> None:
        from personalized_agent.api.server import ApiServer

        self.agent = TemporaryAgent()
        self.port = _free_port()
        self.agent.app.config.set("api_port", self.port)
        self.agent.app.config.set("api_host", "127.0.0.1")
        self.server = ApiServer(self.agent.app)
        self.server.start()

    def tearDown(self) -> None:
        self.server.stop()
        self.agent.cleanup()

    def test_a_second_agent_cannot_listen_on_the_same_port(self) -> None:
        from personalized_agent.api.server import ApiServer

        second = ApiServer(self.agent.app)
        with self.assertRaises(OSError) as raised:
            second.start()
        self.assertIn(str(self.port), str(raised.exception))

    def test_nor_can_another_program_that_asks_to_share_it(self) -> None:
        other = socket.socket()
        try:
            other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with self.assertRaises(OSError):
                other.bind(("127.0.0.1", self.port))
                other.listen()
        finally:
            other.close()

    def test_the_port_is_free_again_once_the_agent_stops(self) -> None:
        from personalized_agent.api.server import ApiServer

        self.server.stop()
        again = ApiServer(self.agent.app)
        again.start()
        self.server = again


class SaidPlainlyAtStartTests(unittest.TestCase):
    """A port another program holds is found before anything starts, and
    said as that: not "could not start" with no reason, after the listeners
    were already running."""

    def test_the_agent_says_the_port_is_in_use_and_starts_nothing(self) -> None:
        import contextlib
        import io
        import json
        import os
        import shutil
        import tempfile
        from pathlib import Path
        from unittest import mock

        from personalized_agent.__main__ import main
        from personalized_agent.common.instance import ALLOW_SECOND_INSTANCE_ENV
        from personalized_agent.common.paths import AppPaths, resolve_root

        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]
        root = Path(tempfile.mkdtemp()) / "root"
        try:
            paths = AppPaths.create(resolve_root(root))
            paths.settings_file.write_text(json.dumps({"api_port": port}), encoding="utf-8")
            said = io.StringIO()
            # Beside whatever agent this machine is running: only the
            # machine-wide guard is waived, never this root's own.
            with mock.patch.dict(os.environ, {ALLOW_SECOND_INSTANCE_ENV: "1"}), \
                    contextlib.redirect_stderr(said):
                code = main(["--root", str(root), "--headless"])
            self.assertEqual(code, 5)
            self.assertIn(f"Port {port} is in use by", said.getvalue())
            self.assertIn("api_port", said.getvalue())
            self.assertFalse(paths.database_file.exists(), "something was started")
        finally:
            holder.close()
            shutil.rmtree(root.parent, ignore_errors=True)

    def test_a_free_port_is_free(self) -> None:
        from personalized_agent.common.own_interface import port_holder

        self.assertEqual(port_holder("127.0.0.1", _free_port()), "")

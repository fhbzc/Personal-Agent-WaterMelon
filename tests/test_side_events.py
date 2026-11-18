"""The side panel told what changed, as it changes - not asking (the user, 2026-11-16: the sidebar quick, and not
slowing the computer)."""

from __future__ import annotations

import http.client
import socket
import threading
import time
import unittest
from unittest import mock

from support import TemporaryAgent

from personalized_agent.api import side_events as se
from personalized_agent.api.access import HEADER


def pieces(stream, many):
    out = []
    for piece in stream:
        out.append(piece.decode("utf-8"))
        if len(out) >= many:
            break
    return out


class TheBoardTests(unittest.TestCase):
    def test_what_is_told_reaches_a_listening_page_in_turn_with_its_number(self) -> None:
        board = se.SideEvents()
        stream = board.stream(0)
        self.assertEqual(pieces(stream, 2)[0], "retry: 2000\n\n")
        board.tell("notes")
        board.tell("turn", id="turn_1")
        got = pieces(stream, 2)
        self.assertEqual(got[0], "id: 1\nevent: notes\ndata: {}\n\n")
        self.assertEqual(got[1], 'id: 2\nevent: turn\ndata: {"id": "turn_1"}\n\n')
        stream.close()
        self.assertEqual(board.listening, 0, "gone, it is no longer listening")

    def test_back_after_a_moment_it_is_told_what_it_missed(self) -> None:
        board = se.SideEvents()
        for _ in range(3):
            board.tell("notes")
        stream = board.stream(1)
        got = pieces(stream, 4)
        self.assertIn("id: 2\n", got[2])
        self.assertIn("id: 3\n", got[3])
        stream.close()

    def test_a_new_page_is_not_told_what_was_before_it(self) -> None:
        board = se.SideEvents()
        board.tell("notes")
        stream = board.stream(0)
        got = pieces(stream, 2)
        self.assertIn('"last": 1', got[1])
        board.tell("turn", id="t")
        self.assertIn("id: 2\n", pieces(stream, 1)[0])
        stream.close()

    def test_a_quiet_line_is_kept_up(self) -> None:
        board = se.SideEvents()
        with mock.patch.object(se, "KEEP_ALIVE_SECONDS", 0.05):
            stream = board.stream(0)
            got = pieces(stream, 3)
        self.assertEqual(got[2], ": still here\n\n")
        stream.close()

    def test_stopping_lets_every_line_go(self) -> None:
        board = se.SideEvents()
        stream = board.stream(0)
        pieces(stream, 2)
        ended = threading.Event()

        def read():
            for _ in stream:
                pass
            ended.set()
        threading.Thread(target=read, daemon=True).start()
        board.stop()
        self.assertTrue(ended.wait(5))


class WhatIsFoundByItsLookTests(unittest.TestCase):
    def test_looked_at_only_while_a_page_listens_and_told_when_it_changes(self) -> None:
        board = se.SideEvents()
        looks, value = [], ["a"]

        def look():
            looks.append(1)
            return value[0]
        board.watch("notes", look)
        with mock.patch.object(se, "WATCH_SECONDS", 0.02):
            time.sleep(0.1)
            self.assertEqual(looks, [], "nobody listening: never looked at")
            stream = board.stream(0)
            pieces(stream, 2)
            time.sleep(0.1)
            value[0] = "b"
            got = pieces(stream, 1)
            self.assertIn("event: notes", got[0])
            stream.close()
            time.sleep(0.1)
            seen = len(looks)
            time.sleep(0.1)
            self.assertEqual(len(looks), seen, "nobody listening again: no longer looked at")


class OverTheApiTests(unittest.TestCase):
    def test_a_notification_made_is_told_down_the_open_line(self) -> None:
        agent = TemporaryAgent()
        self.addCleanup(agent.cleanup)
        app = agent.app
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        app.config.set("api_port", port)
        app.config.set("api_host", "127.0.0.1")
        from personalized_agent.api.server import ApiServer
        server = ApiServer(app)
        server.start()
        self.addCleanup(server.stop)
        self.addCleanup(app.side_events.stop)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        self.addCleanup(connection.close)
        connection.request("GET", "/api/side/stream", headers={HEADER: app.api_access.key,
                                                               "Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(response.getheader("Content-Type").startswith("text/event-stream"))
        began = time.monotonic()
        app.notifications.system("A fictional reminder", "Water the seedlings.", kind="info",
                                 template="reminder")
        line = b""
        while b"event: notes" not in line and time.monotonic() - began < 5:
            line += response.read1(256)
        self.assertIn(b"event: notes", line)
        self.assertLess(time.monotonic() - began, 1.0, "told at once")


if __name__ == "__main__":
    unittest.main()

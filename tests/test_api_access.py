"""The local API answers the application's own clients, and no one else.

It listens on 127.0.0.1, which is every program on this machine - and,
were it ever to reach this port, code an agent wrote. Before the key, any of
them could grant itself a writable folder (/api/work/folders), change a
setting, start a run, or read every conversation. These tests hold the
rules of api/access.py: no key, no answer; the key kept where an agent
cannot read it; the browser signed in by a single-use link the Listener
never records; and no request honoured that says it came from elsewhere.
"""

from __future__ import annotations

import http.client
import json
import socket
import sys
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from support import ROOT, TemporaryAgent, keyed  # noqa: E402

sys.path.insert(0, str(ROOT))

from personalized_agent.api import access as access_module  # noqa: E402
from personalized_agent.api.access import (COOKIE, HEADER, SIGN_IN_PATH,  # noqa: E402
                                           TICKET_PARAM)
from personalized_agent.runtime import filesystem as fs  # noqa: E402
from personalized_agent.runtime.permissions import SecurityViolation  # noqa: E402

#: A route of each family, read and written: what an agent's code, or any
#: program, would have used. Each must be refused without the key.
SAMPLED = [
    ("GET", "/api/status"),
    ("GET", "/api/settings"),
    ("POST", "/api/settings"),
    ("GET", "/api/conversations"),
    ("POST", "/api/chat"),
    ("GET", "/api/lessons"),
    ("GET", "/api/experience"),
    ("POST", "/api/experience/learn"),
    ("GET", "/api/tasks"),
    ("POST", "/api/tasks"),
    ("GET", "/api/work/folders"),
    ("POST", "/api/work/folders"),
    ("POST", "/api/work/start"),
    ("GET", "/api/work/presentation"),
    ("GET", "/api/models"),
    ("GET", "/api/notifications"),
    ("GET", "/api/protocol/applications"),
    # It signs whatever it is given with the agent's own key: not open.
    ("POST", "/api/protocol/sign"),
    ("GET", "/api/lessons/package"),
    ("POST", "/api/lessons/load"),
    ("POST", "/api/protocol/permissions"),
    ("POST", "/api/protocol/revoke"),
    ("GET", "/api/debugger/experience/projects"),
    ("POST", "/api/debugger/experience/projects"),
    ("GET", "/api/state_heartbeat"),
    ("GET", "/api/listener"),
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ApiAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.port = _free_port()
        self.app.config.set("api_port", self.port)
        self.app.config.set("api_host", "127.0.0.1")
        from personalized_agent.api.server import ApiServer

        self.server = ApiServer(self.app)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.port}"
        self.key = self.app.api_access.key

    def tearDown(self) -> None:
        self.server.stop()
        self.agent.cleanup()

    # -- a raw request, with exactly the headers given -----------------------
    def ask(self, method: str, path: str, headers=None, body=None,
            host: str = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            connection.putrequest(method, path, skip_host=True,
                                  skip_accept_encoding=True)
            connection.putheader("Host", host if host is not None
                                 else f"127.0.0.1:{self.port}")
            payload = b""
            if body is not None:
                payload = json.dumps(body).encode("utf-8")
                connection.putheader("Content-Type", "application/json")
            if method == "POST":
                connection.putheader("Content-Length", str(len(payload)))
            for name, value in (headers or {}).items():
                connection.putheader(name, value)
            connection.endheaders(payload if method == "POST" else None)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def keyed_headers(self, **more):
        return {HEADER: self.key, **more}

    # -- no key, no answer ---------------------------------------------------
    def test_every_family_refuses_a_request_without_the_key(self) -> None:
        for method, path in SAMPLED:
            with self.subTest(route=f"{method} {path}"):
                status, _headers, body = self.ask(method, path, body={} if method == "POST" else None)
                self.assertEqual(status, 401, f"{method} {path}: {body[:200]!r}")
                self.assertNotIn(b"conversations", body)

    def test_a_folder_is_not_granted_without_the_key(self) -> None:
        # The case that made it urgent: a program granting itself a folder
        # an agent may then write in.
        before = self.app.work.folders("task-x")
        status, _h, _b = self.ask("POST", "/api/work/folders",
                                  body={"unit": "task-x", "path": str(self.agent.dir)})
        self.assertEqual(status, 401)
        self.assertEqual(self.app.work.folders("task-x"), before)

    def test_a_setting_is_not_changed_without_the_key(self) -> None:
        held = self.app.config.get("font_size")
        status, _h, _b = self.ask("POST", "/api/settings",
                                  body={"values": {"font_size": 31}})
        self.assertEqual(status, 401)
        self.assertEqual(self.app.config.get("font_size"), held)

    def test_with_the_key_it_answers(self) -> None:
        for path in ("/api/status", "/api/settings", "/api/conversations",
                     "/api/lessons", "/api/experience", "/api/notifications"):
            with self.subTest(path=path):
                status, _h, body = self.ask("GET", path, self.keyed_headers())
                self.assertEqual(status, 200, body[:300])
        status, _h, _b = self.ask("POST", "/api/settings", self.keyed_headers(),
                                  body={"values": {"font_size": 19}})
        self.assertEqual(status, 200)
        self.assertEqual(self.app.config.get("font_size"), 19)

    def test_a_wrong_key_or_one_in_the_address_is_refused(self) -> None:
        self.assertEqual(self.ask("GET", "/api/status", {HEADER: "x" * 43})[0], 401)
        self.assertEqual(self.ask("GET", "/api/status", {HEADER: self.key[:-1]})[0], 401)
        # Only ever a header: an address is written down - in histories,
        # in logs - and a key in one would be a key given away.
        quoted = urllib.parse.quote(self.key)
        for path in (f"/api/status?{HEADER}={quoted}", f"/api/status?key={quoted}",
                     f"/api/status?session_token={quoted}"):
            self.assertEqual(self.ask("GET", path)[0], 401, path)
        # The cookie's value is not the key either way round.
        self.assertEqual(self.ask("GET", "/api/status",
                                  {HEADER: self.app.api_access.browser_token})[0], 401)

    def test_which_routes_exist_is_not_said_without_the_key(self) -> None:
        self.assertEqual(self.ask("GET", "/api/no-such-route")[0], 401)
        self.assertEqual(self.ask("GET", "/api/no-such-route", self.keyed_headers())[0], 404)

    def test_the_page_itself_is_served_and_holds_no_key(self) -> None:
        token = self.app.api_access.browser_token
        for path in ("/", "/app.js", "/chat.html", "/debugger.js", "/styles.css"):
            with self.subTest(path=path):
                status, _h, body = self.ask("GET", path)
                self.assertEqual(status, 200)
                self.assertNotIn(self.key.encode(), body)
                self.assertNotIn(token.encode(), body)

    # -- the host and the origin ----------------------------------------------
    def test_a_foreign_host_is_refused_even_with_the_key(self) -> None:
        for host in ("evil.example", f"evil.example:{self.port}", "127.0.0.1",
                     f"127.0.0.1:{self.port + 1}", f"rebind.127.0.0.1.nip.io:{self.port}", ""):
            with self.subTest(host=host):
                self.assertEqual(self.ask("GET", "/api/status", self.keyed_headers(),
                                          host=host)[0], 403)
        for host in (f"127.0.0.1:{self.port}", f"localhost:{self.port}"):
            self.assertEqual(self.ask("GET", "/api/status", self.keyed_headers(),
                                      host=host)[0], 200)

    def test_a_request_from_another_origin_is_refused(self) -> None:
        for origin in ("http://evil.example", f"http://127.0.0.1:{self.port + 1}",
                       f"http://localhost:{self.port + 1}", "null",
                       "chrome-extension://abcdef"):
            with self.subTest(origin=origin):
                self.assertEqual(self.ask("GET", "/api/status",
                                          self.keyed_headers(Origin=origin))[0], 403)
        self.assertEqual(self.ask("GET", "/api/status", self.keyed_headers(
            Origin=f"http://127.0.0.1:{self.port}"))[0], 200)

    # -- the browser's sign-in -----------------------------------------------
    def sign_in(self, next_path: str = "/#overview"):
        url = self.app.sign_in_url(next_path)
        parts = urllib.parse.urlsplit(url)
        return self.ask("GET", f"{parts.path}?{parts.query}", {"Sec-Fetch-Site": "none"})

    def cookie_of(self, headers) -> str:
        raw = headers.get("Set-Cookie", "")
        return raw.split(";", 1)[0].split("=", 1)[1]

    def test_a_sign_in_link_sets_the_cookie_once(self) -> None:
        url = self.app.sign_in_url("/#settings")
        self.assertTrue(url.startswith(f"{self.base}{SIGN_IN_PATH}?"))
        self.assertNotIn(self.key, url)
        parts = urllib.parse.urlsplit(url)
        status, headers, _b = self.ask("GET", f"{parts.path}?{parts.query}",
                                       {"Sec-Fetch-Site": "none"})
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/#settings")
        cookie = headers["Set-Cookie"]
        self.assertIn(f"{COOKIE}=", cookie)
        for attribute in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(attribute, cookie)
        # A session cookie: kept by the browser while it is open.
        self.assertNotIn("Expires", cookie)
        self.assertNotIn("Max-Age", cookie)
        value = self.cookie_of(headers)
        self.assertNotEqual(value, self.key)
        # Spent: the same link a second time signs nobody in.
        again = self.ask("GET", f"{parts.path}?{parts.query}", {"Sec-Fetch-Site": "none"})
        self.assertEqual(again[0], 401)
        self.assertNotIn("Set-Cookie", again[1])

    def test_the_cookie_answers_for_this_page_only(self) -> None:
        _s, headers, _b = self.sign_in()
        cookie = {"Cookie": f"{COOKIE}={self.cookie_of(headers)}"}
        own = f"http://127.0.0.1:{self.port}"
        self.assertEqual(self.ask("GET", "/api/status", {
            **cookie, "Sec-Fetch-Site": "same-origin"})[0], 200)
        self.assertEqual(self.ask("POST", "/api/settings", {
            **cookie, "Sec-Fetch-Site": "same-origin", "Origin": own},
            body={"values": {"font_size": 21}})[0], 200)
        self.assertEqual(self.app.config.get("font_size"), 21)
        # The run's deck, in a frame of this page: a frame sends no header.
        self.assertNotEqual(self.ask("GET", "/api/work/presentation?run=none", {
            **cookie, "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Dest": "iframe"})[0], 401)
        # A browser sends the cookie to this port from a page on any port of
        # 127.0.0.1 - an agent's own server, say - and says so.
        other = f"http://127.0.0.1:{self.port + 1}"
        self.assertEqual(self.ask("POST", "/api/work/folders", {
            **cookie, "Sec-Fetch-Site": "same-site", "Origin": other},
            body={"path": str(self.agent.dir)})[0], 403)
        self.assertEqual(self.ask("GET", "/api/conversations", {
            **cookie, "Sec-Fetch-Site": "same-site"})[0], 401)
        self.assertEqual(self.ask("GET", "/api/conversations", {
            **cookie, "Sec-Fetch-Site": "cross-site"})[0], 401)
        # A cookie not made from this key is nothing.
        self.assertEqual(self.ask("GET", "/api/status", {
            "Cookie": f"{COOKIE}={'A' * 43}"})[0], 401)

    def test_a_ticket_that_is_late_forged_or_followed_from_a_site_is_refused(self) -> None:
        access = self.app.api_access
        expired = access.ticket(seconds=-1)
        expires, nonce, mac = access.ticket().split(".")
        forged = f"{expires}.{nonce}.{mac[:-2]}AA"
        too_long = f"{int(time.time()) + 86400}.{nonce}.{access._mac(int(time.time()) + 86400, nonce)}"
        for ticket in (expired, forged, too_long, "", "a.b", "1.2.3"):
            with self.subTest(ticket=ticket):
                query = urllib.parse.urlencode({TICKET_PARAM: ticket, "next": "/"})
                self.assertEqual(self.ask("GET", f"{SIGN_IN_PATH}?{query}",
                                          {"Sec-Fetch-Site": "none"})[0], 401)
        # A good ticket in a link a site led the browser to: not this page's.
        query = urllib.parse.urlencode({TICKET_PARAM: access.ticket(), "next": "/"})
        self.assertEqual(self.ask("GET", f"{SIGN_IN_PATH}?{query}",
                                  {"Sec-Fetch-Site": "cross-site"})[0], 401)

    def test_a_sign_in_sends_the_browser_nowhere_but_this_page(self) -> None:
        for where in ("//evil.example/", "http://evil.example/", "/\\evil.example",
                      "javascript:alert(1)", "/ok\r\nSet-Cookie: x=y"):
            with self.subTest(where=where):
                status, headers, _b = self.sign_in(where)
                self.assertEqual(status, 303)
                self.assertEqual(headers["Location"], "/")

    def test_the_desktop_opens_the_page_signed_in(self) -> None:
        # Every way the desktop opens the page - the tray's "Open", the
        # menu's "#tab" entries, the chat window - is open_url.
        from personalized_agent.ui import desktop

        opened = []
        fake = mock.Mock()
        fake.app = self.app
        fake._conversation_id = "conv-1"
        fake.open_url = lambda fragment="/": desktop.DesktopAgent.open_url(fake, fragment)
        with mock.patch.object(desktop.webbrowser, "open", opened.append):
            desktop.DesktopAgent.open_url(fake, "/#overview")
            desktop.DesktopAgent.open_chat(fake, "hello there")
        self.assertEqual(len(opened), 2)
        for url, landing in zip(opened, ("/#overview",
                                         "/chat.html?conversation=conv-1")):
            with self.subTest(url=url):
                self.assertTrue(url.startswith(f"{self.base}{SIGN_IN_PATH}?"), url)
                parts = urllib.parse.urlsplit(url)
                status, headers, _b = self.ask("GET", f"{parts.path}?{parts.query}",
                                               {"Sec-Fetch-Site": "none"})
                self.assertEqual(status, 303)
                self.assertTrue(headers["Location"].startswith(landing), headers["Location"])
                cookie = {"Cookie": f"{COOKIE}={self.cookie_of(headers)}",
                          "Sec-Fetch-Site": "same-origin"}
                self.assertEqual(self.ask("GET", "/api/status", cookie)[0], 200)

    # -- the external protocol keeps working, and no more ---------------------
    def test_registering_without_the_key_is_for_pushing_events_only(self) -> None:
        # A program, not a page: no Origin, and no key.
        program = {}
        first = {"application_id": "editor-plugin-1", "public_key": "AAAA",
                 "permissions": ["ingest_events"]}
        self.assertEqual(self.ask("POST", "/api/protocol/register", program,
                                  body=first)[0], 200)
        # Saying again what it already is: a reconnect.
        self.assertEqual(self.ask("POST", "/api/protocol/register", program,
                                  body=first)[0], 200)
        # More than pushing events, or someone else's name: refused.
        self.assertEqual(self.ask("POST", "/api/protocol/register", program, body={
            **first, "application_id": "other", "permissions": ["write"]})[0], 403)
        self.assertEqual(self.ask("POST", "/api/protocol/register", program, body={
            **first, "public_key": "BBBB"})[0], 403)
        # Nor may it undo the user's revoking it.
        self.app.app_registry.revoke("editor-plugin-1")
        self.assertEqual(self.ask("POST", "/api/protocol/register", program,
                                  body=first)[0], 403)
        # Its events still need its own session.
        self.assertEqual(self.ask("POST", "/api/listener/ingest",
                                  body={"event_type": "integration.x", "data": {}})[0], 401)
        # A browser add-on's origin is another origin like any other.
        self.assertEqual(self.ask("POST", "/api/protocol/register",
                                  {"Origin": "moz-extension://abcdef"},
                                  body={**first, "application_id": "y"})[0], 403)
        # The page, with the key, may.
        self.assertEqual(self.ask("POST", "/api/protocol/register", self.keyed_headers(),
                                  body={**first, "permissions": ["write"]})[0], 200)
        # And a site may not register anything at all.
        self.assertEqual(self.ask("POST", "/api/protocol/register",
                                  {"Origin": "http://evil.example"},
                                  body={**first, "application_id": "x"})[0], 403)

    # -- an agent cannot reach it ----------------------------------------------
    def test_an_agents_web_reader_is_refused(self) -> None:
        from personalized_agent.agent.work.tools import fetch

        said = fetch(f"{self.base}/api/conversations")
        self.assertIn("error", said)
        self.assertIn("401", said["error"])


class KeyIsProtectedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.key = self.app.api_access.key
        self.file = self.app.api_access.key_file

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_the_key_lives_in_the_protected_state(self) -> None:
        self.assertTrue(self.file.is_file())
        self.assertGreaterEqual(len(self.key), 43)          # 32 random bytes
        self.assertEqual(self.file.read_text(encoding="ascii").strip(), self.key)
        protected = [Path(p) for p in self.app.policy.protected_paths]
        self.assertTrue(any(fs.is_inside(self.file.resolve(), p) for p in protected),
                        protected)
        self.assertTrue(fs.is_inside(self.file.resolve(),
                                     self.app.paths.data_dir.resolve()))

    def test_the_key_outlives_a_restart_and_is_not_shared(self) -> None:
        again = access_module.ApiAccess(self.file)
        self.assertEqual(again.key, self.key)
        other = TemporaryAgent()
        try:
            self.assertNotEqual(other.app.api_access.key, self.key)
        finally:
            other.cleanup()

    def test_an_agents_tools_cannot_read_it(self) -> None:
        with self.assertRaises(SecurityViolation):
            fs.validate_read_path(self.file, self.app.policy)
        try:
            said = self.app.agent.tools.call("read_file", {"path": str(self.file)})
        except SecurityViolation as refused:
            said = {"error": str(refused)}
        self.assertNotIn(self.key, json.dumps(said, default=str))
        self.assertIn("error", json.dumps(said, default=str).lower())

    def test_code_an_agent_runs_cannot_read_it(self) -> None:
        code = (f"print(open({self.file.as_posix()!r}).read())\n")
        result = self.app.runtime.run_python_code(code, timeout=60)
        self.assertFalse(result.ok)
        self.assertNotIn(self.key, result.stdout or "")
        self.assertNotIn(self.key, result.stderr or "")

    def test_the_key_and_a_ticket_never_reach_a_log(self) -> None:
        from personalized_agent.common.logging.setup import SECRETS

        url = self.app.sign_in_url("/")
        ticket = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)[TICKET_PARAM][0]
        scrubbed = SECRETS.scrub(f"key {self.key} link {url}")
        self.assertNotIn(self.key, scrubbed)
        self.assertNotIn(ticket, scrubbed)
        self.assertNotIn(self.app.api_access.browser_token,
                         SECRETS.scrub(f"cookie {self.app.api_access.browser_token}"))


class ListenerNeverKeepsATicketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app

    def tearDown(self) -> None:
        self.agent.cleanup()

    def test_a_sign_in_link_is_our_own_page_to_the_browser_reader(self) -> None:
        from personalized_agent.common.own_interface import is_our_own_interface

        port = int(self.app.config.get("api_port"))
        self.assertTrue(is_our_own_interface(self.app.sign_in_url("/#overview"), port))

    def test_no_event_keeps_a_ticket(self) -> None:
        from personalized_agent.common.events.models import ListenerEvent

        # Recording, as on the user's machine.
        self.app.config.set("listener_enabled", True)
        url = self.app.sign_in_url("/#overview")
        ticket = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)[TICKET_PARAM][0]
        encoded = urllib.parse.quote(url, safe="")
        event = ListenerEvent(
            event_type="window.focus", source="a-window-reader",
            application=f"Chrome - {url}",
            data={"title": f"{url} - Google Chrome", "url": url,
                  "history": [{"url": encoded}], "note": "nothing here"},
            metadata={"referrer": url})
        self.app.listener._on_event(event)  # noqa: SLF001 - the pipeline itself
        kept = [e for e in self.app.raw_store.recent_events(limit=50)
                if e.id == event.id]
        self.assertEqual(len(kept), 1)
        stored = json.dumps(kept[0].to_dict(), default=str)
        self.assertNotIn(ticket, stored)
        self.assertNotIn(urllib.parse.quote(ticket, safe=""), stored)
        self.assertIn("nothing here", stored)
        self.assertIn(f"{TICKET_PARAM}=[removed]", stored)


if __name__ == "__main__":
    unittest.main()

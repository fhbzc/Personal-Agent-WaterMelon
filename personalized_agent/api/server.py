"""Local HTTP API and static UI host.

The frontend (desktop agent, Overview, Calendar, Settings, Graph, Debugger)
talks only to this API - it never reads Listener storage, the Knowledge Graph
or the scheduler files directly (sections 9 and 60).

The server binds to the loopback interface, validates the Host header against
rebinding, and requires protocol authentication for anything an external
application can call.

AND THE KEY FOR EVERYTHING ELSE. Loopback is every program on this machine,
not the user: each request to /api/ must carry the installation's key, or
the browser's sign-in made from it, and must not say it comes from another
origin (api/access.py says why, and what stays open). The static files -
the page, its scripts and styles - are served to anyone, because they hold
nothing: no key is ever written into them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple

from ..common.paths import resource_path
from ..version import __version__
from .access import (EXTERNAL, SIGN_IN_PATH, TICKET_PARAM, ApiAccess, key_file_for,
                     scrub_sign_in)
from .errors import ApiError, Raw
from .routes import build_routes

log = logging.getLogger(__name__)

# Packaged static resources are read-only at runtime (section 112).
WEB_ROOT = resource_path("api", "web")
#: The client went away before the answer was written.
#:
#: The panel refreshes on a timer and reloads whenever the user navigates, so
#: a request in flight when the tab is closed or reloaded is ordinary, and
#: happens several times an hour. What made it look like a fault was what
#: happened next: the write failed, the handler treated it as a failed
#: request, and tried to send a 500 down the same closed socket - which
#: failed the same way, with nothing left to catch it. The stack that reached
#: the user was two exceptions deep and named neither the route nor the
#: reason.
CLIENT_GONE = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)


class ClientGone(Exception):
    """Raised in place of a write that had nobody to write to."""


#: The page shown when a sign-in link is not good: plain, because the user
#: arrived at it from a click and needs to know what to click instead.
SIGN_IN_REFUSED = """<!doctype html><html><head><meta charset="utf-8">
<title>Personalized Agent - open it again</title></head>
<body style="font-family:system-ui,sans-serif;max-width:34em;margin:4em auto;
line-height:1.5;padding:0 16px">
<h1 style="font-size:1.3em">This link has already been used, or has expired</h1>
<p>Each link that opens the agent's page works once, for two minutes.
Open the page again from the agent's icon beside the clock, or its window.</p>
</body></html>"""


class Request:
    def __init__(self, method: str, path: str, query: Dict[str, list], body: Any,
                 headers: Any, authorized: bool = True) -> None:
        self.method = method
        self.path = path
        self.query = query
        self.body = body if isinstance(body, dict) else {}
        self.raw_body = body
        self.headers = headers
        #: Whether the caller presented the key (or the browser's sign-in).
        #: False only for the external protocol's routes, which answer
        #: without it (api/access.EXTERNAL); a request built in-process -
        #: a test, the application itself - is the application's own.
        self.authorized = authorized

    def get(self, name: str, default: Any = None) -> Any:
        if name in self.body:
            return self.body[name]
        values = self.query.get(name)
        return values[0] if values else default

    def require(self, name: str) -> Any:
        value = self.get(name)
        if value in (None, ""):
            raise ApiError(f"missing parameter: {name}")
        return value

    def int_param(self, name: str, default: int) -> int:
        try:
            return int(self.get(name, default))
        except (TypeError, ValueError):
            return default

    @property
    def token(self) -> Optional[str]:
        header = self.headers.get("Authorization") or ""
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return self.get("session_token")


Handler = Callable[[Request], Any]


class ApiServer:
    def __init__(self, application: Any) -> None:
        self.application = application
        self.routes: Dict[Tuple[str, str], Handler] = build_routes(application)
        # The application's own, so the desktop signs the browser in with
        # the same key this server checks; made here for an application
        # built without one.
        self.access: ApiAccess = getattr(application, "api_access", None) or ApiAccess(
            key_file_for(application.paths.credentials_dir))
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        host = str(self.application.config.get("api_host", "127.0.0.1"))
        port = int(self.application.config.get("api_port", 8756))
        server = self
        # Made now, not at the first request: the user's own tools read it
        # from the protected state, and it should be there once the server
        # answers.
        self.access.key

        class RequestHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = f"PersonalizedAgent/{__version__}"

            def log_message(self, fmt: str, *args: Any) -> None:  # quieter logs
                # The request line of a sign-in carries its ticket.
                log.debug("api %s", scrub_sign_in(fmt % args))

            @property
            def _port(self) -> int:
                return int(self.server.server_address[1])

            def _host_allowed(self) -> bool:
                # This server, by its own name and port. Before, any port
                # and a missing Host passed; a missing Host is no browser's
                # and no client of ours, and naming the port is what makes
                # a name pointed at 127.0.0.1 (DNS rebinding) not this.
                return ApiAccess.host_allowed(self.headers.get("Host"), self._port)

            def _sign_in(self, query: Dict[str, list]) -> None:
                """A sign-in link: spend its ticket, set the browser's
                cookie, and send it on to the page it was opening."""
                ticket = (query.get(TICKET_PARAM) or [""])[0]
                site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
                # Opened from outside the browser (the desktop, a pasted
                # link) or from this page - never followed from a site.
                if site not in ("", "none", "same-origin") or not server.access.redeem(ticket):
                    self._send(401, SIGN_IN_REFUSED.encode("utf-8"),
                               "text/html; charset=utf-8")
                    return
                where = ApiAccess.safe_next((query.get("next") or ["/"])[0])
                try:
                    self.send_response(303)
                    self.send_header("Location", where)
                    self.send_header("Set-Cookie", server.access.set_cookie_header())
                    self.send_header("Cache-Control", "no-store")
                    # The link it came from holds the ticket: not to be told
                    # to anyone, though it is spent.
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                except CLIENT_GONE:
                    self.close_connection = True
                    raise ClientGone() from None

            def _send(self, status: int, payload: bytes, content_type: str) -> None:
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    tag = getattr(self, "_etag", None)
                    if tag:
                        self.send_header("ETag", tag)
                        self._etag = None
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(payload)
                except CLIENT_GONE:
                    # Nothing to report to: there is no longer anyone there.
                    # Raised rather than swallowed so the caller knows not to
                    # try an error response down the same socket, and caught
                    # by the one place that knows there is nothing to do.
                    self.close_connection = True
                    raise ClientGone() from None

            def _json(self, status: int, data: Any) -> None:
                body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
                if status == 200 and self.command == "GET":
                    # SAME ANSWER, NOT SENT AGAIN. A page following something
                    # that is megabytes - a round's report, four times a
                    # second - says which answer it already has, and is told
                    # when that is still the answer. Nothing is cached: the
                    # tag is the answer's own digest, so it cannot be stale.
                    tag = '"' + hashlib.blake2b(body, digest_size=16).hexdigest() + '"'
                    if self.headers.get("If-None-Match") == tag:
                        self._not_modified(tag)
                        return
                    self._etag = tag
                self._send(status, body, "application/json; charset=utf-8")

            def _not_modified(self, tag: str) -> None:
                try:
                    self.send_response(304)
                    self.send_header("ETag", tag)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                except CLIENT_GONE:
                    self.close_connection = True
                    raise ClientGone() from None

            def _read_body(self) -> Any:
                length = int(self.headers.get("Content-Length") or 0)
                if not length:
                    return {}
                try:
                    raw = self.rfile.read(length)
                except CLIENT_GONE:
                    self.close_connection = True
                    raise ClientGone() from None
                content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
                if content_type == "application/json" or raw[:1] in (b"{", b"["):
                    try:
                        return json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        raise ApiError("request body is not valid JSON")
                if content_type == "application/x-www-form-urlencoded":
                    parsed = urllib.parse.parse_qs(raw.decode("utf-8"))
                    return {k: v[0] for k, v in parsed.items()}
                return {}

            def _refuse(self, status: int, data: Any) -> None:
                # Answered before the body was read. A small body is read
                # and dropped, so the connection is left clean for the next
                # request - and so the refusal arrives: a socket closed with
                # unread bytes in it is reset on Windows, and the caller saw
                # "connection aborted" instead of being told why. A large
                # one is not read for someone without the key; the
                # connection is closed instead.
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = -1
                if 0 < length <= 1_000_000:
                    try:
                        self.rfile.read(length)
                    except CLIENT_GONE:
                        self.close_connection = True
                        raise ClientGone() from None
                elif length:
                    self.close_connection = True
                self._json(status, data)

            def _dispatch(self, method: str) -> None:
                try:
                    self._route(method)
                except ClientGone:
                    log.debug("api client closed the connection during %s %s",
                              method, self.path)

            def _route(self, method: str) -> None:
                if not self._host_allowed():
                    self._refuse(403, {"error": "host not allowed"})
                    return
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                query = urllib.parse.parse_qs(parsed.query)
                if not path.startswith("/api"):
                    self._serve_static(path)
                    return
                if method == "GET" and path == SIGN_IN_PATH:
                    self._sign_in(query)
                    return
                # Asked before the route is looked up, so that a caller
                # without the key learns nothing - not even which routes
                # there are.
                external = (method, path) in EXTERNAL
                refused = ApiAccess.origin_refusal(self.headers, self._port)
                if refused:
                    self._refuse(403, {"error": refused})
                    return
                authorized = server.access.authorized(self.headers)
                if not authorized and not external:
                    self._refuse(401, {"error": "this needs the agent's key: open the "
                                              "page from the agent's icon or window"})
                    return
                handler = server.routes.get((method, path))
                if handler is None:
                    self._json(404, {"error": f"no route for {method} {path}"})
                    return
                try:
                    body = self._read_body() if method in ("POST", "PUT", "PATCH", "DELETE") else {}
                    request = Request(method, path, query, body, self.headers,
                                      authorized=authorized)
                    result = handler(request)
                    if isinstance(result, Raw):
                        self._send(200, result.body, result.content_type)
                        return
                    self._json(200, result if result is not None else {"ok": True})
                except ApiError as exc:
                    self._json(exc.status, {"error": str(exc)})
                except PermissionError as exc:
                    self._json(403, {"error": str(exc)})
                except FileNotFoundError as exc:
                    self._json(404, {"error": str(exc)})
                except ValueError as exc:
                    self._json(400, {"error": str(exc)})
                except ClientGone:
                    raise
                except Exception as exc:  # last resort: never leak a traceback
                    log.exception("api handler failed for %s %s", method, path)
                    self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

            def _serve_static(self, path: str) -> None:
                relative = "index.html" if path == "/" else path.lstrip("/")
                candidate = (WEB_ROOT / relative).resolve()
                if WEB_ROOT.resolve() not in candidate.parents or not candidate.is_file():
                    self._json(404, {"error": "not found"})
                    return
                content_type, _ = mimetypes.guess_type(str(candidate))
                self._send(200, candidate.read_bytes(),
                           content_type or "application/octet-stream")

            def handle_one_request(self) -> None:
                # The request line and headers are read here, before any
                # handler of ours runs, and a tab closed mid-request fails at
                # that read too.
                try:
                    super().handle_one_request()
                except CLIENT_GONE:
                    self.close_connection = True
                    log.debug("api client closed the connection")

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                self._dispatch("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._dispatch("POST")

            def do_DELETE(self) -> None:  # noqa: N802
                self._dispatch("DELETE")

            def do_HEAD(self) -> None:  # noqa: N802
                self._dispatch("GET")

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            # AND NOT WAITED FOR ON THE WAY OUT. `block_on_close` defaults
            # to True, so `server_close` joins every handler thread - and
            # with HTTP/1.1 keep-alive a handler sits in `handle_one_request`
            # waiting for the browser's NEXT request, which may never come.
            # Measured on a real quit: the api took 3.4 seconds of an
            # eight-second shutdown, all of it waiting for an idle socket
            # belonging to a page that was not asking for anything.
            #
            # Safe because the threads are daemons and each request is
            # already finished or will be abandoned mid-write - the server
            # writes no state, so an abandoned response loses a response
            # and nothing else.
            block_on_close = False

            def handle_error(self, request: Any, client_address: Any) -> None:
                # socketserver prints a full traceback for anything that
                # escapes a handler, and a browser closing a tab is not a
                # thing to print a traceback about. Anything else still is.
                import sys

                if issubclass(sys.exc_info()[0] or Exception,
                              CLIENT_GONE + (ClientGone,)):
                    log.debug("api client %s went away", client_address)
                    return
                super().handle_error(request, client_address)

        self._httpd = Server((host, port), RequestHandler)
        # A tenth of a second rather than the default half: this is how
        # long `shutdown` waits for the accept loop to notice, and it is
        # paid on every quit.
        self._thread = threading.Thread(
            target=lambda: self._httpd.serve_forever(poll_interval=0.1),
            name="api", daemon=True)
        self._thread.start()
        log.info("api listening on http://%s:%s", host, port)

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        log.info("api stopped")

    @property
    def started_at(self) -> str:
        return datetime.now(timezone.utc).isoformat()

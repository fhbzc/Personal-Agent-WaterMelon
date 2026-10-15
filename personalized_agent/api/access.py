"""Who may use the local API: the key, the browser's sign-in, and the checks.

THE API ANSWERED ANYONE. It listens on the loopback interface only, and that
was taken to mean "only the user": it does not. Every program on this
machine reaches 127.0.0.1, and before this module any of them - a script the
user downloaded, a tool another application runs, and, the case that made
it urgent, code an agent wrote, once that code may reach this machine's own
ports - could ask the API to grant it a writable folder, change a setting,
start a piece of work, or read every conversation and lesson. The hard rule
that an agent writes only in its own folder would have been one request away
from not holding.

So every request to /api/ now carries a secret the agents cannot read:

* THE KEY. Thirty-two random bytes, made once per installation and kept in
  ``internal/credentials`` - the protected state, which an agent's tools
  refuse to read, which the guard refuses inside code an agent runs, and
  which the operating system hides from that code where it isolates it.
  The file itself is restricted to the user as well, like the credential
  manager's own key beside it. The application's own clients - the desktop,
  tests, scripts run by the user - send it as the ``X-PA-Key`` header.

* THE BROWSER'S SIGN-IN. The page cannot be handed the key: anything the
  server would put in a page, another program could fetch the same way. So
  the desktop opens the page through a sign-in link carrying a single-use
  ticket that lives two minutes, and the server answers that link by
  setting a cookie and sending the browser on to the page it asked for.
  The cookie is HttpOnly - no script, ours or anyone's, can read it -
  SameSite=Strict, and a session cookie, so it is not written to the
  browser's profile by most browsers at all, and where it is (Chromium on
  Windows keeps it encrypted for the user), not in a form a file read
  recovers. Its value is derived from the key, never the key itself.

  WHY A COOKIE AND NOT THE PAGE HOLDING A HEADER. A page that held the key
  in its own storage would have it written in plain text in the browser's
  profile, which is under the user's home - and the home is what an agent
  may read. And the panel shows a run's deck in a frame, with its figures,
  and a frame or an image cannot send a header: only a cookie goes with
  them.

  WHAT A COOKIE DOES NOT DO is tell ports apart: a browser sends it to every
  port of 127.0.0.1. So a page from anywhere else on this machine - a site
  an agent serves on its own port, say - could have the user's browser send
  a request here with the cookie on it. The browser says where a request
  came from, in headers no page can set (``Origin``, ``Sec-Fetch-Site``),
  and a cookie is honoured only for a request from this page itself.

* THE TICKET is signed with the key rather than remembered: whoever holds
  the key can make one - the desktop, and ``--sign-in`` from the command
  line for an agent run without a window - and the server, which holds it
  too, can check one. Single use is remembered: a ticket is refused the
  second time, for as long as it would otherwise have been good.

  It is in a URL, and URLs are recorded: the browser keeps them in its
  history, which the Listener reads. That reader already leaves out this
  application's own pages (common/own_interface.py), and every event the
  Listener records has a ticket cut out of it wherever it appears
  (``scrub_sign_in``) - so a ticket is never stored, and one copied from the
  history later has been spent within a second of being made anyway.

* THE HOST AND THE ORIGIN. A request must name this server as the host it
  wants - ``127.0.0.1:<port>`` or ``localhost:<port>`` - which is what stops
  DNS rebinding: a website whose name was pointed at 127.0.0.1 is still
  asked for under its own name. And a request that says it comes from some
  other origin is refused, key or no key.

WHAT STAYS OPEN. The protocol by which other applications - an editor
plugin, say - connect (sections 61-65) is theirs, not the page's: they cannot hold this key, and prove who they are with a key pair of
their own. Those few routes (``EXTERNAL``) answer without the key, as
before, and a web page still may not call them. Registering a new
application there is limited to what such an application needs - pushing
events - and may not take over one already registered; anything more is
granted from the page.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import stat
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..common.own_interface import TICKET_PARAM, scrub_sign_in

log = logging.getLogger(__name__)

#: The header the application's own clients send the key in.
HEADER = "X-PA-Key"
#: The browser's sign-in, as a cookie.
COOKIE = "pa_session"
#: The route a sign-in link opens. The name its ticket goes by,
#: TICKET_PARAM, and scrub_sign_in, which cuts tickets out of what is
#: recorded, live with what the Listener must never record about this
#: application's own pages (common/own_interface.py).
SIGN_IN_PATH = "/api/access/sign-in"
#: Where the key is kept, under the protected credentials folder.
KEY_FILENAME = "api_access.key"

#: How long a ticket from the desktop lives: the browser opens it at once.
TICKET_SECONDS = 120
#: And one printed for a run without a window, which the user copies by hand.
LONG_TICKET_SECONDS = 15 * 60

#: The routes other applications use to connect, which they reach without
#: the key: they hold a key pair of their own instead (sections 61-65).
EXTERNAL = frozenset({
    ("GET", "/api/protocol/identity"),
    # Not /api/protocol/sign: it signs whatever it is given with the agent's
    # own key, and nothing that connects uses it - open, it let any program
    # on this machine have things signed in the agent's name.
    ("POST", "/api/protocol/challenge"),
    ("POST", "/api/protocol/authenticate"),
    ("POST", "/api/protocol/register"),
    ("POST", "/api/listener/ingest"),
})
#: What an application registering itself without the key may ask for.
EXTERNAL_PERMISSIONS = frozenset({"ingest_events"})


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def restrict_to_user(path: Path) -> None:
    """Readable and writable by the user alone, as the credential manager
    keeps its own key: best effort, and quiet when it cannot."""
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME", "")
            if user:
                from ..common import children

                children.run_short(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=15)
        else:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:  # pragma: no cover - hardening, not a requirement
        log.debug("could not restrict permissions on the api key", exc_info=True)


class ApiAccess:
    """The key, the tickets made from it, and the checks a request passes."""

    def __init__(self, key_file: Path) -> None:
        self.key_file = Path(key_file)
        self._lock = threading.RLock()
        self._key: Optional[str] = None
        #: Tickets already spent, by nonce, with when each would have expired.
        self._spent: Dict[str, float] = {}

    # -- the key ------------------------------------------------------------
    @property
    def key(self) -> str:
        """The installation's key, made the first time it is asked for."""
        with self._lock:
            if self._key is None:
                self._key = self._load_or_make()
                # Never in a log, whatever logs it.
                from ..common.logging.setup import SECRETS

                SECRETS.register(self._key)
                SECRETS.register(self.browser_token_for(self._key))
            return self._key

    def _load_or_make(self) -> str:
        try:
            found = self.key_file.read_text(encoding="ascii").strip()
        except (OSError, UnicodeDecodeError):
            found = ""
        # Anything short is not a key this module made: made again rather
        # than trusted, since a guessable key is no key.
        if len(found) >= 43:
            return found
        made = secrets.token_urlsafe(32)
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        partial = self.key_file.with_name(self.key_file.name + ".new")
        # Created with the user's permissions alone from the first byte,
        # where the system honours a mode; tightened after, where it does not.
        handle = os.open(str(partial), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="ascii") as out:
            out.write(made)
        from ..common.atomic import replace_atomically

        replace_atomically(partial, self.key_file)
        restrict_to_user(self.key_file)
        log.info("made the local API key (kept in the protected state)")
        return made

    @staticmethod
    def browser_token_for(key: str) -> str:
        return _b64(hmac.new(key.encode("ascii"), b"browser-session",
                             hashlib.sha256).digest())

    @property
    def browser_token(self) -> str:
        """The cookie's value: from the key, and not the key."""
        return self.browser_token_for(self.key)

    def headers(self) -> Dict[str, str]:
        """What one of the application's own clients sends with a request."""
        return {HEADER: self.key}

    # -- tickets ------------------------------------------------------------
    def _mac(self, expires: int, nonce: str) -> str:
        message = f"sign-in|{expires}|{nonce}".encode("ascii")
        return _b64(hmac.new(self.key.encode("ascii"), message,
                             hashlib.sha256).digest())

    def ticket(self, seconds: int = TICKET_SECONDS) -> str:
        """A single-use ticket, good for ``seconds``."""
        expires = int(time.time()) + int(seconds)
        nonce = secrets.token_urlsafe(16)
        return f"{expires}.{nonce}.{self._mac(expires, nonce)}"

    def redeem(self, ticket: str) -> bool:
        """Whether ``ticket`` is good - spending it if it is."""
        try:
            expires_text, nonce, mac = str(ticket or "").split(".")
            expires = int(expires_text)
        except ValueError:
            return False
        now = time.time()
        if expires < now or expires > now + LONG_TICKET_SECONDS + 5:
            return False
        if not hmac.compare_digest(mac, self._mac(expires, nonce)):
            return False
        with self._lock:
            for old, until in list(self._spent.items()):
                if until < now:
                    del self._spent[old]
            if nonce in self._spent:
                return False
            self._spent[nonce] = float(expires)
        return True

    def sign_in_url(self, base_url: str, next_path: str = "/",
                    seconds: int = TICKET_SECONDS) -> str:
        """The link that signs a browser in and opens ``next_path``."""
        query = urllib.parse.urlencode({TICKET_PARAM: self.ticket(seconds),
                                        "next": next_path or "/"})
        return f"{base_url.rstrip('/')}{SIGN_IN_PATH}?{query}"

    # -- the checks ---------------------------------------------------------
    @staticmethod
    def own_hosts(port: int) -> Tuple[str, ...]:
        names = ("127.0.0.1", "localhost", "[::1]")
        hosts = tuple(f"{name}:{port}" for name in names)
        return hosts + (names if int(port) == 80 else ())

    @classmethod
    def host_allowed(cls, host_header: Optional[str], port: int) -> bool:
        """The request names this server - not a name pointed at it."""
        return str(host_header or "").strip().lower() in cls.own_hosts(port)

    @classmethod
    def own_origin(cls, origin: str, port: int) -> bool:
        return any(origin == f"http://{host}" for host in cls.own_hosts(port))

    @classmethod
    def origin_refusal(cls, headers: Any, port: int) -> Optional[str]:
        """Why a request's stated origin is refused, or None when it is not.

        No Origin is a program, not a page, and is judged by its key alone.
        A page may call only from this page, the external routes included:
        the applications that use those are programs, and send no Origin.
        """
        origin = str(headers.get("Origin") or "").strip().lower()
        if not origin or cls.own_origin(origin, port):
            return None
        return "requests from another origin are refused"

    def authorized(self, headers: Any) -> bool:
        """Whether a request carries the key, or the browser's sign-in."""
        presented = str(headers.get(HEADER) or "")
        if presented and hmac.compare_digest(presented.encode("utf-8", "replace"),
                                             self.key.encode("ascii")):
            return True
        cookie = self.cookie_from(headers)
        if not cookie or not hmac.compare_digest(
                cookie.encode("utf-8", "replace"), self.browser_token.encode("ascii")):
            return False
        # A cookie travels to this port from a page on any port; the
        # browser says which, in a header no page can set.
        site = str(headers.get("Sec-Fetch-Site") or "").strip().lower()
        return site in ("", "same-origin", "none")

    @staticmethod
    def cookie_from(headers: Any) -> str:
        raw = headers.get("Cookie") or ""
        for part in str(raw).split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return value.strip()
        return ""

    def set_cookie_header(self) -> str:
        return f"{COOKIE}={self.browser_token}; Path=/; HttpOnly; SameSite=Strict"

    @staticmethod
    def safe_next(value: Optional[str]) -> str:
        """Where a sign-in may send the browser on to: a page of this
        server, never another site."""
        text = str(value or "/").strip()
        if (not text.startswith("/") or text.startswith("//") or "\\" in text
                or any(ch in text for ch in "\r\n\t")):
            return "/"
        return text


def key_file_for(credentials_dir: Path) -> Path:
    return Path(credentials_dir) / KEY_FILENAME


__all__ = ["ApiAccess", "COOKIE", "EXTERNAL", "EXTERNAL_PERMISSIONS", "HEADER",
           "KEY_FILENAME", "SIGN_IN_PATH", "TICKET_PARAM", "key_file_for",
           "scrub_sign_in", "restrict_to_user"]

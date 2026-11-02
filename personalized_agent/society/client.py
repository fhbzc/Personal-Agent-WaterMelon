"""THE AGENT ON THE SOCIETY SERVER (agent_society_server_end/): every request
that changes something signed with the agent's key - over the method, the
path, the body's hash, the time and a once-only nonce - exactly as the server
checks it (its society/ids.py, `request_bytes`).

Only what the agent's user said of themselves, its keys' public halves and
sealed envelopes ever leave the computer.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

from .identity import Identity, b64

#: The server, unless the settings name another.
DEFAULT_SERVER = "https://society-20859845152.us-central1.run.app"
TIMEOUT = 30

Transport = Callable[[str, str, Dict[str, str], bytes], Tuple[int, Any]]


class SocietyError(RuntimeError):
    """What the server said, when it did not do it."""

    def __init__(self, status: int, said: str) -> None:
        super().__init__(said)
        self.status = status


def _urllib(method: str, url: str, headers: Dict[str, str], body: bytes) -> Tuple[int, Any]:
    request = urllib.request.Request(url, data=body or None, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            raw = answer.read()
            return answer.status, json.loads(raw.decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "null")
        except ValueError:
            return exc.code, {"error": f"the server answered {exc.code}"}


def request_bytes(method: str, path: str, body: bytes, at: str, nonce: str) -> bytes:
    return "\n".join(["pa-request-v1", method.upper(), path,
                      hashlib.sha256(body or b"").hexdigest(), at, nonce]).encode("utf-8")


class SocietyClient:
    def __init__(self, identity: Identity, server: str = DEFAULT_SERVER,
                 transport: Optional[Transport] = None) -> None:
        self.identity = identity
        self.server = server.rstrip("/")
        self.transport = transport or _urllib

    def _call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
              query: Optional[Dict[str, Any]] = None, signed: bool = True,
              registering: bool = False) -> Any:
        raw = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if signed:
            at, nonce = str(int(time.time())), secrets.token_urlsafe(18)
            headers.update({"X-Agent": self.identity.id, "X-Time": at, "X-Nonce": nonce,
                            "X-Signature": b64(self.identity.sign(
                                request_bytes(method, path, raw, at, nonce)))})
            if registering:
                headers["X-Public-Key"] = b64(self.identity.public_key)
        url = self.server + path + ("?" + urllib.parse.urlencode(query) if query else "")
        try:
            status, said = self.transport(method, url, headers, raw)
        except (OSError, ValueError) as exc:
            raise SocietyError(0, f"the server could not be reached: {exc}") from None
        if status >= 400:
            raise SocietyError(status, str((said or {}).get("error") or f"answered {status}"))
        return said

    # -- the agent itself -------------------------------------------------------------------
    def register(self, profile: Dict[str, Any], claims: list) -> Dict[str, Any]:
        return self._call("POST", "/v1/agents", {
            "enc_key": b64(self.identity.enc_key), "enc_key_sig": b64(self.identity.enc_key_sig()),
            "profile": profile, "claims": claims}, registering=True)

    def email_start(self, email: str) -> Dict[str, Any]:
        return self._call("POST", "/v1/email/start", {"email": email})

    def email_confirm(self, code: str) -> Dict[str, Any]:
        return self._call("POST", "/v1/email/confirm", {"code": code})

    def set_picture(self, data: bytes) -> Dict[str, Any]:
        return self._call("POST", "/v1/avatar", {"data": b64(data) if data else ""})

    # -- others ----------------------------------------------------------------------------
    def agent(self, agent_id: str) -> Dict[str, Any]:
        return self._call("GET", "/v1/agents/" + urllib.parse.quote(agent_id), signed=False)

    def people(self, words: str = "", page: int = 1) -> Dict[str, Any]:
        return self._call("GET", "/v1/people", query={"q": words, "page": page}, signed=False)

    def suggest(self, words: str) -> Dict[str, Any]:
        return self._call("GET", "/v1/suggest", query={"q": words}, signed=False)

    # -- the mailbox -------------------------------------------------------------------------
    def leave(self, to: str, envelope: Dict[str, Any]) -> Dict[str, Any]:
        """A sealed message left for another agent - called from one place
        alone: `SocietyService.send_draft`, on the user's click."""
        return self._call("POST", "/v1/messages", {"to": to, "envelope": envelope})

    def collect(self) -> Dict[str, Any]:
        return self._call("GET", "/v1/messages")

    def ack(self, ids: list) -> Dict[str, Any]:
        return self._call("POST", "/v1/messages/ack", {"ids": list(ids)})

    def page_url(self, agent_id: Optional[str] = None) -> str:
        return f"{self.server}/a/{agent_id or self.identity.id}"


__all__ = ["SocietyClient", "SocietyError", "DEFAULT_SERVER"]

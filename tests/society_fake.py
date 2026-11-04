"""A STAND-IN FOR THE SOCIETY SERVER, as its published API describes it -
for the agent's tests (the user, 2026-11-03: the website is a service of its
own - "就相当于这个网站不是我开发的"; nothing of its code is used here).

It holds to the API's contract, not to how the server is built:
    requests signed over "pa-request-v1", the method, the path, the body's
    SHA-256, the time and a nonce - by the agent's Ed25519 key; a nonce once
    v1: register (X-Public-Key, its id the key's hash), email start/confirm,
        avatar, agents' public pages, people, suggest, sealed messages left,
        collected and let go of
    v2: the account's inbox (plain messages; the recent events of those it
        follows, "kind": "event"; "withdrawn"), follow / unfollow /
        following, and the account's recent events - posted, listed,
        withdrawn
Mail is kept, not sent: `last_code(agent)`. Every name in the tests is made up.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
import secrets
import time
import urllib.parse
from typing import Any, Dict, List, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

SERVER = "http://society.test"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CLAIM_KINDS = ("github", "linkedin", "x", "bluesky", "mastodon", "website", "weibo", "zhihu",
               "other")


class Refused(Exception):
    def __init__(self, status: int, said: str) -> None:
        super().__init__(said)
        self.status, self.said = status, said


def short_id(public_key: bytes) -> str:
    number = int.from_bytes(hashlib.sha256(b"pa-agent-id\x00" + public_key).digest()[:10], "big")
    text = "".join(CROCKFORD[(number >> (5 * n)) & 31] for n in range(16))[::-1]
    return "pa-" + "-".join(text[at:at + 4] for at in range(0, 16, 4))


def key_code(public_key: bytes) -> str:
    number = int.from_bytes(hashlib.sha256(b"pa-key\x00" + public_key).digest()[:8], "big")
    text = "".join(CROCKFORD[(number >> (5 * n)) & 31] for n in range(12))[::-1]
    return "-".join(text[at:at + 4] for at in range(0, 12, 4))


def signed_bytes(method: str, path: str, body: bytes, at: str, nonce: str) -> bytes:
    return "\n".join(["pa-request-v1", method.upper(), path, hashlib.sha256(body).hexdigest(),
                      at, nonce]).encode("utf-8")


def _verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False


class FakeSociety:
    """The server, in memory. `transport` is what the agent's client calls."""

    def __init__(self) -> None:
        self.agents: Dict[str, Dict[str, Any]] = {}
        self.codes: Dict[str, str] = {}             # by agent id, and by address
        self.nonces: set = set()
        self.sealed: List[Dict[str, Any]] = []
        self.inbox: List[Dict[str, Any]] = []
        self.follows: Dict[Tuple[str, str], float] = {}
        self.events: Dict[str, Dict[str, Any]] = {}
        self.deliveries: List[Tuple[str, str, str]] = []     # event, message, recipient
        # Its clock: the time, each reading a little after the one before.
        self._clock = (time.time() + n / 1000 for n in itertools.count(1))
        self.calls: List[Tuple[str, str]] = []
        #: Whether it has the API's recent events for agents (/v2/events).
        self.events_api = True

    # -- what the tests do on the server's side --------------------------------------------
    def last_code(self, agent_or_email: str) -> str:
        """The code last mailed - to an address, or for an agent."""
        return self.codes[agent_or_email]

    def website_account(self, name: str) -> str:
        """An account made on the website - no key of the agent's kind."""
        ident = short_id(secrets.token_bytes(32))
        self.agents[ident] = {"id": ident, "public_key": "", "enc_key": "", "enc_key_sig": "",
                              "profile": {"name": name}, "claims": [], "registered": True,
                              "created_at": time.time()}
        return ident

    def send_plain(self, sender: str, to: str, text: str, subject: str = "") -> str:
        """A plain-text message, written on the website (or by another key)."""
        return self._deliver(sender, to, subject, text, "t_" + secrets.token_hex(8), "message")

    def post_as(self, account: str, text: str) -> str:
        """A recent event posted on the account's own page."""
        return self._post(account, text)

    def withdraw_as(self, account: str, event_id: str) -> None:
        self._withdraw(account, event_id)

    # -- the transport -----------------------------------------------------------------------
    def transport(self, method: str, url: str, headers: Dict[str, str],
                  body: bytes) -> Tuple[int, Any]:
        assert url.startswith(SERVER), url
        parts = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parts.query))
        self.calls.append((method, parts.path))
        try:
            return 200, self._route(method, parts.path, query,
                                    {k.lower(): v for k, v in headers.items()}, body or b"")
        except Refused as exc:
            return exc.status, {"error": exc.said}

    def _route(self, method: str, path: str, query: Dict[str, str], headers: Dict[str, str],
               body: bytes) -> Any:
        if method == "GET" and path.startswith("/v1/agents/"):
            found = self.agents.get(path.rsplit("/", 1)[1])
            if found is None or not found["registered"]:
                raise Refused(404, "no such agent")
            return self._public(found)
        if method == "GET" and path == "/v1/people":
            words = str(query.get("q") or "").lower()
            return {"people": [self._card(one) for one in self.agents.values()
                               if one["registered"] and one["profile"].get("listed")
                               and words in str(one["profile"].get("name") or "").lower()],
                    "more": False}
        if method == "GET" and path == "/v1/suggest":
            return {"suggestions": []}
        if path.startswith("/v1/"):
            return self._v1(method, path, headers, body)
        if path.startswith("/v2/"):
            return self._v2(method, path, query, headers, body)
        raise Refused(404, "Not Found")

    # -- signatures ---------------------------------------------------------------------------
    def _checked(self, method: str, path: str, headers: Dict[str, str], body: bytes,
                 public_key: bytes) -> Dict[str, Any]:
        at, nonce = headers.get("x-time") or "", headers.get("x-nonce") or ""
        if not at.isdigit() or not 8 <= len(nonce) <= 64 or abs(time.time() - int(at)) > 300:
            raise Refused(401, "not signed")
        try:
            signature = base64.b64decode(headers.get("x-signature") or "", validate=True)
        except ValueError:
            raise Refused(401, "not signed")
        if not _verify(public_key, signed_bytes(method, path, body, at, nonce), signature):
            raise Refused(401, "the signature does not match")
        if nonce in self.nonces:
            raise Refused(401, "this request was already made")
        self.nonces.add(nonce)
        said = json.loads(body.decode("utf-8")) if body else {}
        if not isinstance(said, dict):
            raise Refused(400, "the body is not an object")
        return said

    # -- v1 ---------------------------------------------------------------------------------------
    def _v1(self, method: str, path: str, headers: Dict[str, str], body: bytes) -> Any:
        agent_id = headers.get("x-agent") or ""
        if method == "POST" and path == "/v1/agents":
            key = base64.b64decode(headers.get("x-public-key") or "")
            if short_id(key) != agent_id:
                raise Refused(401, "that id is not this key's")
            said = self._checked(method, path, headers, body, key)
            known = self.agents.get(agent_id) or {"registered": False,
                                                   "created_at": time.time()}
            claims = [{"kind": str(one.get("kind")), "where": str(one.get("where"))}
                      for one in said.get("claims") or []
                      if str(one.get("kind")) in CLAIM_KINDS and one.get("where")]
            known.update(id=agent_id, public_key=base64.b64encode(key).decode(),
                         enc_key=said.get("enc_key") or known.get("enc_key", ""),
                         enc_key_sig=said.get("enc_key_sig") or known.get("enc_key_sig", ""),
                         profile=dict(said.get("profile") or {}), claims=claims)
            self.agents[agent_id] = known
            return dict(self._public(known), registered=known["registered"],
                        listed=bool(known["profile"].get("listed")))
        found = self.agents.get(agent_id)
        if found is None or not found["public_key"]:
            raise Refused(401, "no such agent: register it first")
        said = self._checked(method, path, headers, body, base64.b64decode(found["public_key"]))
        if path == "/v1/email/start":
            if "@" not in str(said.get("email") or ""):
                raise Refused(400, "that is not an email address")
            code = f"{secrets.randbelow(10 ** 6):06d}"
            self.codes[agent_id] = self.codes[str(said["email"]).strip().lower()] = code
            return {"sent": True, "minutes": 120}
        if path == "/v1/email/confirm":
            if said.get("code") != self.codes.get(agent_id):
                raise Refused(400, "that is not the code")
            found["registered"] = True
            return {"verified": True, "registered": True}
        if not found["registered"]:
            raise Refused(403, "verify an email address first: until then the agent is not "
                               "registered")
        if path == "/v1/avatar":
            return {"picture": f"/p/{agent_id}" if said.get("data") else None}
        if method == "POST" and path == "/v1/messages":
            to = self.agents.get(str(said.get("to") or ""))
            if to is None or not to["registered"]:
                raise Refused(404, "no such agent to leave it for")
            ident = secrets.token_hex(16)
            self.sealed.append({"id": ident, "to": to["id"], "from": agent_id,
                                "envelope": said.get("envelope"), "at": next(self._clock)})
            return {"id": ident}
        if method == "GET" and path == "/v1/messages":
            return {"messages": [{"id": one["id"], "from": one["from"],
                                  "envelope": one["envelope"], "at": one["at"]}
                                 for one in self.sealed if one["to"] == agent_id]}
        if path == "/v1/messages/ack":
            ids = set(said.get("ids") or [])
            before = len(self.sealed)
            self.sealed = [one for one in self.sealed
                           if not (one["to"] == agent_id and one["id"] in ids)]
            return {"let_go": before - len(self.sealed)}
        raise Refused(404, "Not Found")

    # -- v2 ---------------------------------------------------------------------------------------
    def _v2(self, method: str, path: str, query: Dict[str, str], headers: Dict[str, str],
            body: bytes) -> Any:
        account = headers.get("x-agent") or ""
        found = self.agents.get(account)
        if not account or not headers.get("x-key"):
            raise Refused(401, "not signed: X-Agent and X-Key are needed")
        if found is None or not found["public_key"] or \
                key_code(base64.b64decode(found["public_key"])) != headers.get("x-key"):
            raise Refused(401, "no such key for this account")
        said = self._checked(method, path, headers, body, base64.b64decode(found["public_key"]))
        if not found["registered"]:
            raise Refused(403, "the account's email address is not verified yet")
        if method == "GET" and path == "/v2/inbox":
            after = float(query.get("after") or 0)
            return {"messages": [self._message(one) for one in self.inbox
                                 if one["to"] == account and one["status"] == "sent"
                                 and one["at"] > after],
                    "withdrawn": [message for event, message, whom in self.deliveries
                                  if whom == account and self.events[event]["withdrawn_at"]
                                  > after]}
        if path in ("/v2/follow", "/v2/unfollow"):
            other = str(said.get("id") or "")
            if other not in self.agents:
                raise Refused(404, "no such account")
            if other == account:
                raise Refused(400, "an account does not follow itself")
            if path == "/v2/follow":
                self.follows.setdefault((account, other), float(next(self._clock)))
                return {"following": other}
            self.follows.pop((account, other), None)
            return {"unfollowed": other}
        if method == "GET" and path == "/v2/following":
            return {"following": [{"id": whom, "since": since}
                                  for (who, whom), since in self.follows.items()
                                  if who == account]}
        if path.startswith("/v2/events") and not self.events_api:
            raise Refused(404, "Not Found")
        if method == "GET" and path == "/v2/events":
            return {"events": [{"id": one["id"], "text": one["text"], "at": one["at"]}
                               for one in self.events.values()
                               if one["account"] == account and not one["withdrawn_at"]]}
        if method == "POST" and path == "/v2/events":
            text = str(said.get("text") or "").strip()
            if not text:
                raise Refused(400, "an event needs some words")
            ident = self._post(account, text)
            return {"id": ident, "at": self.events[ident]["at"]}
        if method == "POST" and path == "/v2/events/withdraw":
            ident = str(said.get("id") or "")
            if not self._withdraw(account, ident):
                raise Refused(404, "no such event of this account")
            return {"withdrawn": ident}
        raise Refused(404, "Not Found")

    # -- the server's own doings --------------------------------------------------------------
    def _deliver(self, sender: str, to: str, subject: str, text: str, thread: str,
                 kind: str) -> str:
        ident = "m_" + secrets.token_hex(8)
        self.inbox.append({"id": ident, "from": sender, "to": to, "subject": subject,
                           "text": text, "thread": thread, "status": "sent", "kind": kind,
                           "at": float(next(self._clock))})
        return ident

    def _post(self, account: str, text: str) -> str:
        ident = "e_" + secrets.token_hex(8)
        self.events[ident] = {"id": ident, "account": account, "text": text,
                              "at": float(next(self._clock)), "withdrawn_at": 0.0}
        for (who, whom) in list(self.follows):
            if whom == account:
                message = self._deliver(account, who, "Recent event", text, ident, "event")
                self.deliveries.append((ident, message, who))
        return ident

    def _withdraw(self, account: str, ident: str) -> bool:
        event = self.events.get(ident)
        if event is None or event["account"] != account or event["withdrawn_at"]:
            return False
        event.update(text="", withdrawn_at=float(next(self._clock)))
        gone = {message for one, message, _whom in self.deliveries if one == ident}
        for row in self.inbox:
            if row["id"] in gone:
                row.update(subject="", text="", status="withdrawn")
        return True

    @staticmethod
    def _message(row: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": row["id"], "from": row["from"], "to": row["to"], "subject": row["subject"],
                "text": row["text"], "thread": row["thread"], "status": row["status"],
                "at": row["at"], "read": False, "kind": row["kind"]}

    @staticmethod
    def _public(agent: Dict[str, Any]) -> Dict[str, Any]:
        profile = {k: v for k, v in agent["profile"].items() if k != "listed"}
        return {"id": agent["id"], "public_key": agent["public_key"], "enc_key": agent["enc_key"],
                "enc_key_sig": agent["enc_key_sig"], "profile": profile,
                "claimed": [dict(one, verified=False) for one in agent["claims"]],
                "note": "Claimed accounts are the agent's own word: not verified by this server.",
                "since": int(agent["created_at"])}

    @staticmethod
    def _card(agent: Dict[str, Any]) -> Dict[str, Any]:
        profile = agent["profile"]
        return {"id": agent["id"], "name": profile.get("name") or "",
                "headline": profile.get("headline") or "",
                "location": profile.get("location") or "", "picture": "", "exact": False}

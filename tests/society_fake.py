"""A STAND-IN FOR THE SOCIETY SERVER, as its published API describes it -
for the agent's tests (the user, 2026-11-03: the website is a service of its
own - "就相当于这个网站不是我开发的"; nothing of its code is used here).

It holds to the API's contract (v2; the site closed v1 on 2026-11-10; as its /api page
gives it on 2026-11-12), not to how the server is built:
    accounts  made ON THE WEBSITE by their users (`website_account`), their ids
              random and short - `pa-XXXX-XXXX` - not from any key
    pairing   POST /v2/keys/request, SELF-SIGNED (X-Public-Key; no X-Agent or
              X-Key): a request with its code - the key's fingerprint; the user
              Gives it on the website (`give`, with the permissions they pick;
              DEFAULT_PERMISSIONS for a "new" slot) or refuses it (`refuse`);
              GET /v2/keys/request?account= says which; GET /v2/me the account,
              the key and its permissions
    signed    every other v2 request by a key given to the account: X-Agent,
              X-Key (its fingerprint), X-Time, X-Nonce, X-Signature over
              "pa-request-v1", the method, the path (no query), the body's
              SHA-256, the time and the nonce; a nonce once; a POST's body a JSON
              object, a GET's empty; Content-Type application/json
    inbox     POST /v2/inbox/send in plain text, in a thread (a new one named by
              the message's id) - a draft-only key's kept as a draft, a key that
              may send only to those "replied" refused (403, "leave it as a
              draft") for anyone else; GET /v2/inbox?after= (100 at most, the
              oldest first; "withdrawn"); POST /v2/inbox/read; /v2/drafts
    following /v2/follow, /v2/unfollow, /v2/following, /v2/events (with the
              key's follow and events permissions)
    the page  GET /v2/profile (any key), POST /v2/profile (profile); /v2/posts (any
              key to read; posts to add, edit, delete - 10 at most, 409 past them)
    public    GET /v1/suggest, /v1/people, /v1/agents/{id} - a name and a
              description, no keys; every other v1 call answers 410
Every name in the tests is made up.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import itertools
import json
import secrets
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

SERVER = "http://society.test"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
DEFAULT_PERMISSIONS = {"inbox": {"read": True, "write": "draft", "send_to": "replied"},
                       "follow": False, "events": False, "profile": False, "posts": False,
                       "contacts": True,
                       "groups": {"read": False, "send": False, "create": False, "invite": False,
                                  "join": False}}
#: Every permission a key can be given.
ALL_PERMISSIONS = {"inbox": {"read": True, "write": "send", "send_to": "anyone"},
                   "follow": True, "events": True, "profile": True, "posts": True, "contacts": True,
                   "groups": {"read": True, "send": True, "create": True, "invite": True,
                              "join": True}}
MOST_POSTS = 10
PAGE = 100


class Refused(Exception):
    def __init__(self, status: int, said: str) -> None:
        super().__init__(said)
        self.status, self.said = status, said


def random_id() -> str:
    """An account's id, short (the site's API, 2026-11-12): `pa-XXXX-XXXX`."""
    text = "".join(secrets.choice(CROCKFORD) for _ in range(8))
    return "pa-" + text[:4] + "-" + text[4:]


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
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.keys: Dict[str, Dict[str, Any]] = {}              # by fingerprint
        self.requests: Dict[Tuple[str, str], Dict[str, Any]] = {}  # (account, fingerprint)
        self.nonces: set = set()
        #: Each account's contacts, as its user keeps them on the website (the site's 5.4).
        self.contacts: Dict[str, List[Dict[str, Any]]] = {}
        #: Each account's groups and its invitations (5.3), as the website has them.
        self.groups: Dict[str, List[Dict[str, Any]]] = {}
        self.invitations: Dict[str, List[Dict[str, Any]]] = {}
        #: GROUPS AS THE SITE KEEPS THEM (5.3): each by its id - its owner, each member's role and
        #: when they joined; what was written in them; the invitations waiting, by account.
        self.rooms: Dict[str, Dict[str, Any]] = {}
        self.room_messages: List[Dict[str, Any]] = []
        self.room_invites: Dict[str, List[Dict[str, Any]]] = {}
        #: Each request a key made, (path, status) - to see what was asked that was refused.
        self.asked: List[Tuple[str, int]] = []
        self.inbox: List[Dict[str, Any]] = []
        self.follows: Dict[Tuple[str, str], float] = {}
        self.events: Dict[str, Dict[str, Any]] = {}
        self.deliveries: List[Tuple[str, str, str]] = []     # event, message, recipient
        # Its clock: the time, each reading a little after the one before.
        self._clock = (time.time() + n / 1000 for n in itertools.count(1))
        self.calls: List[Tuple[str, str]] = []
        self.posts: Dict[str, Dict[str, Any]] = {}
        #: Whether it has the API's recent events for agents (/v2/events).
        self.events_api = True

    # -- what the tests do on the server's side (the website's users) ------------------------
    def website_account(self, name: str, listed: bool = False, verified: bool = True,
                        headline: str = "", location: str = "", description: str = "") -> str:
        """An account made on the website by its user - its id random. Its public page: a name
        and a description (a headline and a place given are its first lines)."""
        ident = random_id()
        about = description or "\n".join(one for one in (headline, location) if one)
        self.accounts[ident] = {"id": ident, "verified": verified, "created_at": time.time(),
                                "profile": {"name": name, "description": about,
                                            "listed": listed}}
        return ident

    def waiting(self, account: str) -> List[Dict[str, Any]]:
        """The requests for a key the account's page shows: "Agents asking for a key"."""
        return [one for (whose, _fp), one in self.requests.items()
                if whose == account and self._status(one) == "waiting"]

    def give(self, account: str, code: Optional[str] = None,
             permissions: Optional[Dict[str, Any]] = None) -> str:
        """THE USER'S GIVE on their account page: the request with that code (else the one
        waiting) given a key - with the permissions they picked, DEFAULT_PERMISSIONS for "new"."""
        [request] = [one for one in self.waiting(account) if code in (None, one["code"])]
        ident = "k_" + secrets.token_hex(8)
        self.keys[request["code"]] = {"id": ident, "account": account, "label": request["label"],
                                      "public_key": request["public_key"], "revoked": False,
                                      "permissions": copy.deepcopy(permissions or
                                                                   DEFAULT_PERMISSIONS)}
        request.update(status="given", key=ident)
        return ident

    def refuse(self, account: str, code: Optional[str] = None) -> None:
        [request] = [one for one in self.waiting(account) if code in (None, one["code"])]
        request["status"] = "refused"

    def expire(self, account: str) -> None:
        for one in self.waiting(account):
            one["at"] -= 31 * 60

    def expire_key(self, account: str) -> None:
        """The account's keys past their expiry (not taken back): refused, still bound."""
        for one in self.keys.values():
            if one["account"] == account:
                one["expires_at"] = time.time() - 60

    def extend_key(self, account: str) -> None:
        """The user's "0 - it never expires" on the key's page."""
        for one in self.keys.values():
            if one["account"] == account:
                one["expires_at"] = None

    def revoke(self, account: str) -> None:
        """The account's keys taken back on its page."""
        for one in self.keys.values():
            if one["account"] == account:
                one["revoked"] = True

    def set_permissions(self, account: str, permissions: Dict[str, Any]) -> None:
        for one in self.keys.values():
            if one["account"] == account:
                one["permissions"] = copy.deepcopy(permissions)

    def send_plain(self, sender: str, to: str, text: str, subject: str = "",
                   thread: str = "") -> str:
        """A message written on the website by the account's user."""
        return self._deliver(sender, to, subject, text, thread, "message")

    def send_draft_as(self, account: str, draft_id: str) -> None:
        """A draft the agent left, sent by the user on their account page."""
        [row] = [one for one in self.inbox if one["id"] == draft_id and one["from"] == account
                 and one["status"] == "draft"]
        row.update(status="sent", at=float(next(self._clock)))

    def drafts_of(self, account: str) -> List[Dict[str, Any]]:
        return [self._message(one) for one in self.inbox
                if one["from"] == account and one["status"] == "draft"]

    def read_of(self, account: str) -> List[str]:
        return [one["id"] for one in self.inbox if one["to"] == account and one["read"]]

    def post_as(self, account: str, text: str) -> str:
        """A recent event posted on the account's own page."""
        return self._post(account, text)

    def withdraw_as(self, account: str, event_id: str) -> None:
        self._withdraw(account, event_id)

    def read_signal_as(self, reader: str, to: str, message: str, thread: str = "") -> str:
        """THE READ SIGNAL ASKED OF THE SITE (2026-11-12): a message read by the one it went to,
        told its sender - in the sender's inbox, "kind": "read", "message": the one read."""
        ident = "m_" + secrets.token_hex(8)
        self.inbox.append({"id": ident, "from": reader, "to": to, "subject": "", "text": "",
                           "thread": thread or message, "status": "sent", "kind": "read",
                           "message": message, "at": float(next(self._clock)), "read": False})
        return ident

    # -- groups, as their members do on the website -------------------------------------------
    def make_room_as(self, owner: str, name: str, description: str = "", public: bool = False) -> str:
        gid = "g-" + random_id()[3:]
        at = next(self._clock)
        self.rooms[gid] = {"id": gid, "name": name, "description": description, "public": public,
                           "owner": owner, "roles": {owner: "owner"}, "joined": {owner: at}}
        return gid

    def join_room_as(self, account: str, gid: str, role: str = "member") -> None:
        self.rooms[gid]["roles"][account] = role
        self.rooms[gid]["joined"][account] = next(self._clock)

    def write_in_room_as(self, account: str, gid: str, text: str) -> str:
        ident = "gm_" + random_id()[3:].replace("-", "").lower()
        self.room_messages.append({"id": ident, "group": gid, "from": account, "text": text,
                                   "at": next(self._clock), "kind": "group"})
        return ident

    def invite_as(self, account: str, gid: str, who: str) -> None:
        self.room_invites.setdefault(who, []).append({"group": gid, "invited_by": account,
                                                      "at": next(self._clock)})

    def room_text(self, gid: str) -> List[Tuple[str, str]]:
        return [(one["from"], one["text"]) for one in self.room_messages if one["group"] == gid]

    def _room_shown(self, room: Dict[str, Any]) -> Dict[str, Any]:
        return {key: room[key] for key in ("id", "name", "description", "public", "owner")} | {
            "members": len(room["roles"])}

    def _rooms(self, method: str, path: str, query: Dict[str, str], account: str,
               can: Dict[str, Any], said: Dict[str, Any]) -> Any:
        """5.3 - each part its own groups.* permission."""
        part = {"/v2/groups": "read", "/v2/groups/messages": "read", "/v2/groups/send": "send",
                "/v2/groups/create": "create", "/v2/groups/invite": "invite",
                "/v2/groups/invitations": "join", "/v2/groups/invitations/answer": "join"}[path]
        if not (can.get("groups") or {}).get(part):
            raise Refused(403, f"this key may not do that - groups.{part} is not given")
        mine = {gid: room for gid, room in self.rooms.items() if account in room["roles"]}
        if path == "/v2/groups":
            return {"groups": copy.deepcopy(self.groups.get(account) or []) + [
                dict(self._room_shown(room), role=room["roles"][account],
                     joined_at=room["joined"][account]) for room in mine.values()]}
        if path == "/v2/groups/invitations":
            return {"invitations": copy.deepcopy(self.invitations.get(account) or []) + [
                dict(self._room_shown(self.rooms[one["group"]]), group=one["group"],
                     invited_by=one["invited_by"], at=one["at"])
                for one in self.room_invites.get(account) or []]}
        if path == "/v2/groups/messages":
            after = float((query or {}).get("after") or 0)
            wanted = (query or {}).get("group") or ""
            found = [dict(one) for one in self.room_messages if one["group"] in mine
                     and one["at"] > after and one["at"] >= mine[one["group"]]["joined"][account]
                     and (not wanted or one["group"] == wanted)]
            return {"messages": found[:100]}
        if path == "/v2/groups/send":
            gid = str(said.get("group") or "")
            if gid not in mine:
                raise Refused(404, "no such group - or this account is not in it")
            ident = self.write_in_room_as(account, gid, str(said.get("text") or ""))
            return {"id": ident, "group": gid, "status": "sent"}
        if path == "/v2/groups/create":
            name = str(said.get("name") or "")
            if not 1 <= len(name) <= 80 or len(str(said.get("description") or "")) > 600:
                raise Refused(400, "a name of 1 to 80 characters; a description of 600 at most")
            gid = self.make_room_as(account, name, str(said.get("description") or ""),
                                    bool(said.get("public")))
            return dict(self._room_shown(self.rooms[gid]), role="owner")
        if path == "/v2/groups/invite":
            gid, who = str(said.get("group") or ""), str(said.get("account") or "")
            if gid not in mine:
                raise Refused(404, "no such group")
            if mine[gid]["roles"][account] not in ("owner", "admin"):
                raise Refused(403, "only the group's owner or an admin invites into it")
            if who not in self.accounts:
                raise Refused(404, "no such account")
            if who in mine[gid]["roles"]:
                return {"group": gid, "account": who, "status": "member"}
            self.invite_as(account, gid, who)
            return {"group": gid, "account": who, "status": "invited"}
        gid = str(said.get("group") or "")
        waiting = [one for one in self.room_invites.get(account) or [] if one["group"] == gid]
        if not waiting:
            raise Refused(404, "no such invitation")
        self.room_invites[account] = [one for one in self.room_invites[account] if one["group"] != gid]
        if said.get("join"):
            self.join_room_as(account, gid)
            return {"group": gid, "status": "joined"}
        return {"group": gid, "status": "declined"}

    def follow_as(self, account: str, other: str) -> None:
        """Followed on the website (since 2026-11-12 the agent follows no one itself)."""
        self.follows.setdefault((account, other), float(next(self._clock)))

    def unfollow_as(self, account: str, other: str) -> None:
        self.follows.pop((account, other), None)

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
            found = self.accounts.get(path.rsplit("/", 1)[1])
            if found is None or not found["verified"]:
                raise Refused(404, "no such agent")
            return self._public(found)
        if method == "GET" and path == "/v1/people":
            words = str(query.get("q") or "").lower()
            return {"people": [self._card(one) for one in self.accounts.values()
                               if one["verified"] and one["profile"].get("listed")
                               and words in str(one["profile"].get("name") or "").lower()],
                    "more": False}
        if method == "GET" and path == "/v1/suggest":
            return {"suggestions": self._suggest(str(query.get("q") or ""))}
        if path.startswith("/v1/"):
            raise Refused(410, "v1 is closed: pair a key through /v2/keys/request")
        if path == "/v2/keys/request":
            return self._key_request(method, path, query, headers, body)
        if path.startswith("/v2/"):
            return self._v2(method, path, query, headers, body)
        raise Refused(404, "Not Found")

    # -- signatures ---------------------------------------------------------------------------
    def _checked(self, method: str, path: str, headers: Dict[str, str], body: bytes,
                 public_key: bytes) -> Dict[str, Any]:
        at, nonce = headers.get("x-time") or "", headers.get("x-nonce") or ""
        if not at.isdigit() or not 8 <= len(nonce) <= 64 or abs(time.time() - int(at)) > 300:
            raise Refused(401, "not signed")
        if headers.get("content-type") != "application/json":
            raise Refused(400, "a signed request is application/json")
        try:
            signature = base64.b64decode(headers.get("x-signature") or "", validate=True)
        except ValueError:
            raise Refused(401, "not signed")
        if not _verify(public_key, signed_bytes(method, path, body, at, nonce), signature):
            raise Refused(401, "the signature does not match")
        if nonce in self.nonces:
            raise Refused(401, "this request was already made")
        self.nonces.add(nonce)
        if method == "GET":
            if body:
                raise Refused(400, "a GET has no body")
            return {}
        try:
            said = json.loads(body.decode("utf-8"))
        except ValueError:
            raise Refused(400, "the body is not JSON")
        if not isinstance(said, dict):
            raise Refused(400, "the body is not an object")
        return said

    # -- pairing --------------------------------------------------------------------------------
    @staticmethod
    def _status(request: Dict[str, Any]) -> str:
        if request["status"] == "waiting" and time.time() - request["at"] > 30 * 60:
            return "expired"
        return request["status"]

    def _key_request(self, method: str, path: str, query: Dict[str, str],
                     headers: Dict[str, str], body: bytes) -> Any:
        if headers.get("x-agent") or headers.get("x-key"):
            raise Refused(400, "a request for a key is self-signed: no X-Agent, no X-Key")
        try:
            key = base64.b64decode(headers.get("x-public-key") or "", validate=True)
        except ValueError:
            raise Refused(400, "X-Public-Key is not base64")
        if len(key) != 32:
            raise Refused(400, "X-Public-Key is not an Ed25519 key")
        said = self._checked(method, path, headers, body, key)
        code = key_code(key)
        if method == "GET":
            account = str(query.get("account") or "")
            request = self.requests.get((account, code))
            if request is None:
                return {"code": "", "status": "none", "key": ""}
            return {"code": code, "status": self._status(request), "key": request.get("key", "")}
        account, label = str(said.get("account") or ""), str(said.get("label") or "")
        found = self.accounts.get(account)
        if found is None or not found["verified"]:
            raise Refused(404, "no such account")
        if len(label) > 60:
            raise Refused(400, "a label is 60 characters at most")
        given = self.keys.get(code)
        if given and given["account"] == account and not given["revoked"]:
            return {"code": code, "status": "given", "key": given["id"]}
        self.requests[(account, code)] = {"code": code, "status": "waiting", "label": label,
                                          "public_key": key, "at": time.time()}
        return {"code": code, "status": "waiting", "minutes": 30}

    # -- v2, signed by a key of the account -------------------------------------------------
    def _v2(self, method: str, path: str, query: Dict[str, str], headers: Dict[str, str],
            body: bytes) -> Any:
        account = headers.get("x-agent") or ""
        if not account or not headers.get("x-key"):
            raise Refused(401, "not signed: X-Agent and X-Key are needed")
        key = self.keys.get(headers.get("x-key") or "")
        if key is None or key["account"] != account or key["revoked"] or \
                (key.get("expires_at") and key["expires_at"] < time.time()):
            raise Refused(401, "no such key for this account - or it was revoked, or it expired")
        said = self._checked(method, path, headers, body, key["public_key"])
        if not self.accounts[account]["verified"]:
            raise Refused(403, "the account's email address is not verified yet")
        can = key["permissions"]
        inbox = can.get("inbox") or {}
        if method == "GET" and path == "/v2/me":
            return {"account": account, "permissions": copy.deepcopy(can),
                    "key": {"label": "Home agents", "fingerprint": headers["x-key"],
                            "agent": key["label"], "expires_at": None}}
        if path == "/v2/contacts":
            if not can.get("contacts"):
                raise Refused(403, "this key may not read the contacts - contacts is not given")
            listed = self.contacts.get(account) or []
            wanted = (query or {}).get("id") if isinstance(query, dict) else None
            listed = [one for one in listed if not wanted or one["id"] == wanted]
            return {"contacts": copy.deepcopy(listed),
                    "updated_at": max([one.get("updated_at", 0) for one in listed] or [0])}
        if path.startswith("/v2/groups"):
            return self._rooms(method, path, query, account, can, said if isinstance(said, dict) else {})
        if method == "POST" and path == "/v2/inbox/send":
            return self._send(account, inbox, said)
        if method == "GET" and path == "/v2/inbox":
            if not inbox.get("read"):
                raise Refused(403, "this key may not read the inbox (inbox.read)")
            after = float(query.get("after") or 0)
            rows = sorted((one for one in self.inbox if one["to"] == account
                           and one["status"] == "sent" and one["at"] > after
                           and (query.get("unread") != "1" or not one["read"])),
                          key=lambda one: one["at"])[:PAGE]
            return {"messages": [self._message(one) for one in rows],
                    "withdrawn": [message for event, message, whom in self.deliveries
                                  if whom == account and self.events[event]["withdrawn_at"]
                                  > after]}
        if method == "POST" and path == "/v2/inbox/read":
            ids = set(said.get("ids") or [])
            for one in list(self.inbox):
                if one["to"] == account and one["id"] in ids:
                    if not one["read"] and one["kind"] == "message":
                        self.read_signal_as(account, one["from"], one["id"], one["thread"])
                    one["read"] = True
            return {"read": len(ids)}
        if method == "GET" and path == "/v2/drafts":
            return {"drafts": self.drafts_of(account)}
        if method == "POST" and path in ("/v2/drafts/send", "/v2/drafts/discard"):
            rows = [one for one in self.inbox if one["id"] == said.get("id")
                    and one["from"] == account and one["status"] == "draft"]
            if not rows:
                raise Refused(404, "no such draft")
            if path == "/v2/drafts/discard":
                self.inbox.remove(rows[0])
                return {"discarded": 1}
            if inbox.get("write") != "send":
                raise Refused(403, "this key may only draft (inbox.write)")
            rows[0].update(status="sent", at=float(next(self._clock)))
            return {"id": rows[0]["id"], "status": "sent"}
        if path in ("/v2/follow", "/v2/unfollow", "/v2/following") and not can.get("follow"):
            raise Refused(403, "this key may not follow (follow)")
        if path in ("/v2/follow", "/v2/unfollow"):
            other = str(said.get("id") or "")
            if other not in self.accounts:
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
        if path.startswith("/v2/events") and not can.get("events"):
            raise Refused(403, "this key may not post events (events)")
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
        if path in ("/v2/profile", "/v2/posts") and method == "GET" or \
                path.startswith(("/v2/profile", "/v2/posts")):
            return self._page(account, can, method, path, said)
        raise Refused(404, "Not Found")

    def _page(self, account: str, can: Dict[str, Any], method: str, path: str,
              said: Dict[str, Any]) -> Any:
        """The public page and the posts: read with any key, changed with `profile`/`posts`."""
        found = self.accounts[account]
        profile = found["profile"]
        if path == "/v2/profile":
            if method == "POST":
                if not can.get("profile"):
                    raise Refused(403, "this key may not change the public page (profile)")
                for field, most in (("name", 80), ("description", 1500)):
                    if field in said:
                        value = str(said.get(field) or "")
                        if len(value) > most:
                            raise Refused(400, f"{field}: {most} characters at most")
                        profile[field] = value
            return {"account": account, "profile": {"name": profile.get("name") or "",
                                                    "description": profile.get("description")
                                                    or ""},
                    "listed": bool(profile.get("listed"))}
        mine = sorted((one for one in self.posts.values() if one["account"] == account),
                      key=lambda one: -one["at"])
        if method == "GET" and path == "/v2/posts":
            return {"posts": [self._post_shown(one) for one in mine], "most": MOST_POSTS}
        if not can.get("posts"):
            raise Refused(403, "this key may not change the posts (posts)")
        if path == "/v2/posts":
            title, text = str(said.get("title") or ""), str(said.get("text") or "")
            if not 1 <= len(title) <= 120 or len(text) > 1000:
                raise Refused(400, "a title of 1 to 120 characters, a text of 1000 at most")
            if len(mine) >= MOST_POSTS:
                raise Refused(409, "10 posts already")
            ident = "p_" + secrets.token_hex(6)
            at = float(next(self._clock))
            self.posts[ident] = {"id": ident, "account": account, "title": title, "text": text,
                                 "at": at, "updated_at": at}
            return self._post_shown(self.posts[ident])
        post = self.posts.get(str(said.get("id") or ""))
        if post is None or post["account"] != account:
            raise Refused(404, "no such post of this account")
        if path == "/v2/posts/edit":
            for field in ("title", "text"):
                if field in said:
                    post[field] = str(said[field] or "")
            post["updated_at"] = float(next(self._clock))
            return self._post_shown(post)
        if path == "/v2/posts/delete":
            del self.posts[post["id"]]
            return {"deleted": post["id"]}
        raise Refused(404, "Not Found")

    @staticmethod
    def _post_shown(one: Dict[str, Any]) -> Dict[str, Any]:
        return {key: one[key] for key in ("id", "title", "text", "at", "updated_at")}

    def _send(self, account: str, inbox: Dict[str, Any], said: Dict[str, Any]) -> Any:
        write = inbox.get("write") or "none"
        if write == "none":
            raise Refused(403, "this key may not write (inbox.write)")
        to = str(said.get("to") or "")
        text = str(said.get("text") or "")
        if not text.strip() or len(text) > 20000:
            raise Refused(400, "a message has words, 20000 at most")
        if to not in self.accounts:
            raise Refused(404, "no such account")
        draft = bool(said.get("draft")) or write == "draft"
        if not draft and inbox.get("send_to") != "anyone" and not self.replied(account, to):
            raise Refused(403, "this key may send only to those you have written with "
                               "(inbox.send_to): leave it as a draft")
        thread = str(said.get("thread") or "")[:40]
        ident = self._deliver(account, to, str(said.get("subject") or "")[:60], text, thread,
                              "message", status="draft" if draft else "sent")
        return {"id": ident, "status": "draft" if draft else "sent"}

    def replied(self, one: str, other: str) -> bool:
        """Any message sent, either way - an event from someone followed counts."""
        return any(row["status"] == "sent" and row["kind"] != "read"
                   and {row["from"], row["to"]} == {one, other} for row in self.inbox)

    # -- the server's own doings --------------------------------------------------------------
    def _deliver(self, sender: str, to: str, subject: str, text: str, thread: str,
                 kind: str, status: str = "sent") -> str:
        ident = "m_" + secrets.token_hex(8)
        self.inbox.append({"id": ident, "from": sender, "to": to, "subject": subject,
                           "text": text, "thread": thread or ident, "status": status,
                           "kind": kind, "at": float(next(self._clock)), "read": False})
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

    def _suggest(self, words: str) -> List[Dict[str, Any]]:
        whole = "".join(words.split()).upper()
        for one in self.accounts.values():
            if one["verified"] and one["id"].upper() == whole:
                return [dict(self._card(one), exact=True)]
        if len(words.strip()) < 2:
            return []
        return [self._card(one) for one in self.accounts.values()
                if one["verified"] and one["profile"].get("listed")
                and words.strip().lower() in str(one["profile"].get("name") or "").lower()][:8]

    @staticmethod
    def _message(row: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": row["id"], "from": row["from"], "to": row["to"], "subject": row["subject"],
                "text": row["text"], "thread": row["thread"], "status": row["status"],
                "at": row["at"], "read": row["read"], "kind": row["kind"],
                **({"message": row["message"]} if row.get("message") else {})}

    @staticmethod
    def _public(account: Dict[str, Any]) -> Dict[str, Any]:
        """A website account's public page: a name and a description - no keys in it."""
        profile = account["profile"]
        return {"id": account["id"],
                "profile": {"name": profile.get("name") or "",
                            "description": profile.get("description") or ""},
                # Its accounts elsewhere: {"kind", "where", "verified"} - set by a test.
                "claimed": [dict(one) for one in account.get("claimed") or []],
                "since": int(account["created_at"])}

    @staticmethod
    def _card(account: Dict[str, Any]) -> Dict[str, Any]:
        """One in a list of people: its description's first line."""
        profile = account["profile"]
        about = str(profile.get("description") or "")
        return {"id": account["id"], "name": profile.get("name") or "",
                "description": about.splitlines()[0] if about else "", "picture": "",
                "exact": False}

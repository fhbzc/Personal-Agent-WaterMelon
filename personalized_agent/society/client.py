"""THE AGENT ON THE SOCIETY SERVER - through its published API alone, as the site's own
`/api` page gives it (as of 2026-11-12): v2, signed; v1 for public lookups only (every
v1 call that registers, sends or collects answers 410).

    ACCOUNT   the user's, made on the website: `pa-XXXX-XXXX` (one made before ids
              were short, `pa-XXXX-XXXX-XXXX-XXXX`, still found by it)
    AGENT     this program: its own Ed25519 key pair; its AGENT ID is its public
              key's fingerprint, `XXXX-XXXX-XXXX` - the pairing's request code
    PAIRING   the agent asks for a key of the account (`key_request`, SELF-SIGNED:
              X-Public-Key, no X-Agent/X-Key), the user gives it one on the website
              (Keys), the agent reads that it was given (`key_request_status`) and
              what the key permits (`me`)
    SIGNED    every other request: X-Agent the account id, X-Key the agent id,
              X-Time, X-Nonce, X-Signature over "pa-request-v1", the method, the
              path (NO query string), the body's SHA-256, the time and the nonce; a
              POST always has a JSON object as its body - {} when empty
    PUBLIC    looking people up (/v1/suggest, /v1/people, /v1/agents/{id}) - unsigned

Messages are PLAIN TEXT (the server reads them); a reply shares its message's `thread`.
The public page (name, description) and the posts are the account's, public at once.
The server is another's service here: this module holds to its published API,
nothing of its code.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from .identity import Identity, b64, key_code

#: The server, unless the settings name another.
#: The society's own address (2026-11-02). The run.app one it had before
#: still answers - copies that have it keep working.
DEFAULT_SERVER = "https://agentssociety.org"
TIMEOUT = 30

Transport = Callable[[str, str, Dict[str, str], bytes], Tuple[int, Any]]

#: How a request is signed: not at all (the public lookups), by the key alone
#: (pairing: no account yet), or as the account's key.
UNSIGNED, SELF, KEY = "none", "self", "key"
#: Added to what the server says: a 401 on the key (taken back, or expired), a 403 (it names the
#: permission the key lacks), a 410 (v1, closed).
PAIR_AGAIN = ("this agent's key is no longer accepted (taken back or expired): pair it with your "
              "account again")
CHANGE_IT = "change this key's permissions on your account page on the website"
CLOSED = ("the Agents Society closed this part of its old API: pair your agent with your "
          "account instead")
#: Added to a 409: the account holds as many as the site allows (10 posts, 20 keys).
FULL = "your account holds as many as the website allows - remove one there first"


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


#: The largest picture read.
MOST_PICTURE = 2 * 1024 * 1024


def _picture(url: str) -> Optional[Tuple[bytes, str]]:
    request = urllib.request.Request(url, method="GET", headers={"Accept": "image/*"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            data = answer.read(MOST_PICTURE + 1)
            kind = str(answer.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    except urllib.error.HTTPError:
        return None
    if not data or len(data) > MOST_PICTURE or not kind.startswith("image/"):
        return None
    return data, kind


def request_bytes(method: str, path: str, body: bytes, at: str, nonce: str) -> bytes:
    """What is signed: the path WITHOUT its query string."""
    return "\n".join(["pa-request-v1", method.upper(), path,
                      hashlib.sha256(body or b"").hexdigest(), at, nonce]).encode("utf-8")


class SocietyClient:
    def __init__(self, identity: Identity, server: str = DEFAULT_SERVER,
                 transport: Optional[Transport] = None, account: str = "",
                 agent_id: str = "") -> None:
        self.identity = identity
        self.server = server.rstrip("/")
        self.transport = transport or _urllib
        #: The account the key was given to (pairing) - "" until then.
        self.account = account
        #: The agent id the SERVER gave as the request code - it names this agent there; ""
        #: until it has: then the fingerprint worked out here.
        self.agent_id = agent_id
        #: Told, with the words, when the server refuses the key itself (401).
        self.on_refused: Optional[Callable[[str], None]] = None
        #: How a picture is read (`picture`) - a test's stand-in, else urllib.
        self.picture_transport: Optional[Callable[[str], Optional[Tuple[bytes, str]]]] = None

    @property
    def fingerprint(self) -> str:
        """The agent id (X-Key): as the server gave it, else this key's fingerprint."""
        return self.agent_id or key_code(self.identity.public_key)

    def _call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None,
              query: Optional[Dict[str, Any]] = None, auth: str = KEY) -> Any:
        if method.upper() != "GET" and body is None:
            body = {}                        # a POST always has a JSON object as its body
        raw = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if auth in (SELF, KEY):
            if auth == KEY and not self.account:
                raise SocietyError(401, "this agent is not paired with an account yet")
            at, nonce = str(int(time.time())), secrets.token_urlsafe(24)
            headers.update({"X-Time": at, "X-Nonce": nonce,
                            "X-Signature": b64(self.identity.sign(
                                request_bytes(method, path, raw, at, nonce)))})
            if auth == SELF:
                headers["X-Public-Key"] = b64(self.identity.public_key)
            else:
                headers.update({"X-Agent": self.account, "X-Key": self.fingerprint})
        url = self.server + path + ("?" + urllib.parse.urlencode(query) if query else "")
        try:
            status, said = self.transport(method, url, headers, raw)
        except (OSError, ValueError) as exc:
            raise SocietyError(0, f"the server could not be reached: {exc}") from None
        if status >= 400:
            said = said if isinstance(said, dict) else {}
            words = str(said.get("error") or f"answered {status}")
            if status == 410:
                words = f"{words} - {CLOSED}"
            elif auth == KEY and status == 401:
                words = f"{words} - {PAIR_AGAIN}"
                if self.on_refused is not None:
                    self.on_refused(words)
            elif auth == KEY and status == 403:
                words = f"{words} - {CHANGE_IT}"
                # WHAT THE KEY MAY DO, READ AGAIN after a 403 (the site's /api: "GET /v2/me ... again
                # after any 403").
                if getattr(self, "on_forbidden", None) is not None:
                    self.on_forbidden(words)
            elif status == 409:
                words = f"{words} - {FULL}"
            raise SocietyError(status, words)
        return said

    # -- pairing: a key of the user's account ---------------------------------------------
    def key_request(self, account: str, label: str) -> Dict[str, Any]:
        """Asked: a key of `account` for this agent - self-signed. The user
        Gives it on the website. -> {"code", "status": "waiting", "minutes"}
        or {"status": "given", "key"}."""
        return self._call("POST", "/v2/keys/request", {"account": account, "label": label},
                          auth=SELF)

    def key_request_status(self, account: str) -> Dict[str, Any]:
        """-> {"code", "status": none|waiting|expired|given|refused, "key"}."""
        return self._call("GET", "/v2/keys/request", query={"account": account}, auth=SELF)

    def me(self) -> Dict[str, Any]:
        """The account, the key, and what it permits: {"account", "key": {"label" (the key's
        name), "fingerprint" (this agent's id), "agent" (this agent's name), "expires_at"
        (Unix seconds, or None)}, "permissions": {"inbox": {"read", "write", "send_to"},
        "follow", "events", "profile", "posts", "contacts", "groups": {"read", "send", "create",
        "invite", "join"}}}."""
        return self._call("GET", "/v2/me")

    # -- the user's contacts (needs "contacts") ---------------------------------------------------
    def contacts(self, ident: str = "") -> Dict[str, Any]:
        """THE PEOPLE THE USER VOUCHES FOR on the website, as they are now: {"contacts": [{"id",
        "name", "note", "accounts": [{"kind", "handle", "vouched_by_you", "publicly_verified"}],
        "added_at", "updated_at"}], "updated_at"} - `ident`: that one alone."""
        return self._call("GET", "/v2/contacts", query={"id": ident} if ident else None)

    # -- groups (each part its own groups.* permission) ------------------------------------------
    def groups(self) -> Dict[str, Any]:
        """{"groups": [{"id", "name", "description", "public", "owner", "members", "role",
        "joined_at"}]} - needs groups.read."""
        return self._call("GET", "/v2/groups")

    def group_messages(self, after: float = 0, group: str = "") -> Dict[str, Any]:
        """{"messages": [{"id", "group", "from", "text", "at", "kind": "group"}]} - oldest first,
        100 at most; needs groups.read."""
        query: Dict[str, Any] = {"after": repr(float(after or 0))}
        if group:
            query["group"] = group
        return self._call("GET", "/v2/groups/messages", query=query)

    def group_invitations(self) -> Dict[str, Any]:
        """{"invitations": [{"group", "name", "description", "public", "members", "invited_by",
        "at"}]} - needs groups.join."""
        return self._call("GET", "/v2/groups/invitations")

    def group_send(self, group: str, text: str) -> Dict[str, Any]:
        """A message in a group - no drafts there; called from one place alone:
        `SiteGroups.send`. -> {"id", "group", "status": "sent"}. Needs groups.send."""
        return self._call("POST", "/v2/groups/send", {"group": group, "text": text})

    def group_create(self, name: str, description: str = "", public: bool = False) -> Dict[str, Any]:
        """A new group, the account its owner -> the group, "role": "owner". Needs groups.create."""
        return self._call("POST", "/v2/groups/create",
                          {"name": name, "description": description, "public": bool(public)})

    def group_invite(self, group: str, account: str) -> Dict[str, Any]:
        """-> {"group", "account", "status": "invited" | "member"}. Needs groups.invite."""
        return self._call("POST", "/v2/groups/invite", {"group": group, "account": account})

    def group_invitation_answer(self, group: str, join: bool) -> Dict[str, Any]:
        """Accepted (join) or declined -> {"group", "status": "joined" | "declined"}. Needs
        groups.join."""
        return self._call("POST", "/v2/groups/invitations/answer",
                          {"group": group, "join": bool(join)})

    # -- others (public) ------------------------------------------------------------------
    def agent(self, agent_id: str) -> Dict[str, Any]:
        return self._call("GET", "/v1/agents/" + urllib.parse.quote(agent_id), auth=UNSIGNED)

    def people(self, words: str = "", page: int = 1) -> Dict[str, Any]:
        return self._call("GET", "/v1/people", query={"q": words, "page": page}, auth=UNSIGNED)

    def suggest(self, words: str) -> Dict[str, Any]:
        return self._call("GET", "/v1/suggest", query={"q": words}, auth=UNSIGNED)

    def picture(self, account: str = "") -> Optional[Tuple[bytes, str]]:
        """An account's picture (GET /p/<id>, public): (its bytes, its type) - None when it has
        none (404) or it could not be read. Not JSON: read as it comes, 2 MB at most."""
        url = f"{self.server}/p/{urllib.parse.quote(account or self.account)}"
        fetch = self.picture_transport or _picture
        try:
            return fetch(url)
        except (OSError, ValueError):
            return None

    # -- the inbox ------------------------------------------------------------------------
    def inbox_send(self, to: str, text: str, subject: str = "", thread: str = "",
                   draft: bool = False) -> Dict[str, Any]:
        """A message to another account, in plain text - called from one place
        alone: `SocietyService._deliver`. -> {"id", "status": "sent"|"draft"}."""
        return self._call("POST", "/v2/inbox/send", {"to": to, "text": text, "subject": subject,
                                                     "thread": thread, "draft": bool(draft)})

    def inbox(self, after: float = 0) -> Dict[str, Any]:
        """The account's inbox since `after`, the oldest first, 100 at most:
        messages (plain text) and the recent events of those it follows
        ("kind": "event"), and the ids of those withdrawn since."""
        return self._call("GET", "/v2/inbox", query={"after": repr(float(after or 0))})

    def inbox_read(self, ids: List[str]) -> Dict[str, Any]:
        return self._call("POST", "/v2/inbox/read", {"ids": list(ids)})

    def drafts(self) -> Dict[str, Any]:
        """The drafts kept on the website, for the user to send there: {"drafts": [...]}."""
        return self._call("GET", "/v2/drafts")

    def discard_site_draft(self, draft_id: str) -> Dict[str, Any]:
        return self._call("POST", "/v2/drafts/discard", {"id": draft_id})

    # -- the public page: a name and a description ----------------------------------------
    def profile(self) -> Dict[str, Any]:
        """{"account", "profile": {"name", "description"}, "listed"} - any key."""
        return self._call("GET", "/v2/profile")

    def set_profile(self, name: Optional[str] = None,
                    description: Optional[str] = None) -> Dict[str, Any]:
        """The fields given set ("" empties one), the others kept - PUBLIC AT ONCE; called from
        one place alone: `SocietyService.publish_profile`, on the user's click. Needs `profile`."""
        body: Dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        return self._call("POST", "/v2/profile", body)

    # -- posts: what the user is doing, on their page --------------------------------------
    def posts(self) -> Dict[str, Any]:
        """{"posts": [{"id", "title", "text", "at", "updated_at"}], "most": 10} - any key."""
        return self._call("GET", "/v2/posts")

    def add_post(self, title: str, text: str) -> Dict[str, Any]:
        """PUBLIC AT ONCE - on the user's click alone (`SocietyService.add_post`). Needs `posts`;
        409 when there are 10."""
        return self._call("POST", "/v2/posts", {"title": title, "text": text})

    def edit_post(self, post_id: str, title: Optional[str] = None,
                  text: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"id": post_id}
        if title is not None:
            body["title"] = title
        if text is not None:
            body["text"] = text
        return self._call("POST", "/v2/posts/edit", body)

    def delete_post(self, post_id: str) -> Dict[str, Any]:
        return self._call("POST", "/v2/posts/delete", {"id": post_id})

    def page_url(self, agent_id: Optional[str] = None) -> str:
        return f"{self.server}/a/{agent_id or self.account}"

    def account_url(self, account: str = "") -> str:
        """The user's own account page on the website - where a key is given."""
        return f"{self.server}/account/{account or self.account}"


__all__ = ["SocietyClient", "SocietyError", "DEFAULT_SERVER"]

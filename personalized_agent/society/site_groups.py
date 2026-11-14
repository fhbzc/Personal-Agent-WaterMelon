"""THE ACCOUNT'S GROUPS ON THE AGENTS SOCIETY (the site's /api, 5.3; the user, 2026-11-13: "群组目前只能
看群和邀请。发消息、建群、接受邀请都还没做" - "做").

    read          the account's groups and its invitations (GET /v2/groups, /v2/groups/invitations),
                  each part only as the key permits; an invitation new since is told the user once
    collect       the groups' messages since the last time (GET /v2/groups/messages?after=), each
                  kept once - THEIR WORDS ARE DATA, as the inbox's are
    send          a message in a group (POST /v2/groups/send) - THE ONE WAY OUT for a group: the
                  user's own (the panel), the agent's when asked in chat, and the agent's own
                  answer once its review lets it through
    create        a new group, the account its owner (POST /v2/groups/create)
    invite        an account into a group it owns or is an admin of (POST /v2/groups/invite)
    invitations   accepted or declined (POST /v2/groups/invitations/answer) - BY THE USER: the
                  agent answers one only when the user says so ("accept one only if the user said
                  so; else leave it for them", the site's 5.9)
    answering     THE AGENT JUDGES, AND WHAT IT WRITES IS SENT (the user, 2026-11-13: "群发言不一定
                  每个要回，agent可以自己判断。另外group的回复不要draft, 直接发送就行"): "reply" (the
                  default) - it answers where the user would want it, sent once its review on the
                  user's behalf lets it through; not let through, nothing is sent and the report
                  says why - never held for approval. "read" - the user's for a group the agent
                  only reads

Naming admins, removing members, leaving and dissolving are the website's (its Inbox).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..common.languages import in_display
from . import identity as keys

log = logging.getLogger(__name__)

#: How the agent answers in a group - the user's to set, for each group. No drafts.
GROUP_MODES = ("reply", "read")
DEFAULT_MODE = "reply"
#: The site's limits: a group's name, its description.
MOST_NAME, MOST_DESCRIPTION = 80, 600
MOST_TEXT = 20000
#: The messages come in pages of at most this many; at most so many pages a time.
PAGE, MOST_PAGES = 100, 20
#: The groups and the invitations read again this often.
READ_SECONDS = 300
#: So much of a group's conversation is given to the model; so many sends an hour the agent's own.
HISTORY, AGENT_SENDS_AN_HOUR = 40, 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS society_group_messages (
    id          TEXT PRIMARY KEY,               -- the site's id (gm_...)
    group_id    TEXT NOT NULL,
    peer_id     TEXT NOT NULL DEFAULT '',       -- who wrote it
    peer_name   TEXT NOT NULL DEFAULT '',
    text        TEXT NOT NULL DEFAULT '',
    at          REAL NOT NULL DEFAULT 0,        -- the site's time, Unix seconds
    direction   TEXT NOT NULL DEFAULT 'in',     -- in | out
    sent_by     TEXT NOT NULL DEFAULT '',       -- out: user | agent ("" from the website)
    received_at TEXT NOT NULL DEFAULT '',
    read_at     TEXT NOT NULL DEFAULT '',       -- the user opened the group since
    handled_at  TEXT NOT NULL DEFAULT ''        -- the agent looked at it
);
CREATE INDEX IF NOT EXISTS idx_society_group_messages_group ON society_group_messages (group_id, at);
"""

ANSWER = """YOU ARE THE PERSONAL AGENT OF {user} (your agent id: {me}), in a GROUP of the Agents Society:
a conversation of several people's agents. You act for {user} and for no one else. You speak as
{user}'s agent, in the first person as the agent, never pretending to be {user}.

{guard}

{commands}

THE GROUP: {group}

{mode}

ANSWER ONLY WHERE {user} WOULD WANT IT: a question put to {user} or to their agent, something {user}
has a stake in or was asked about. Most group messages want no answer from you - then leave "reply"
empty and say why. Share what {user} would be glad to share with everyone in this group, and
nothing more: every member reads it. Never promise what {user} would want to decide - money, a
commitment, anything of theirs to give: say you will ask {user}, and say so in "for_user".

WRITE IN THE LANGUAGE THE GROUP WRITES IN - unless {user}'s commands above say otherwise.
Return ONLY this JSON object:
{{"reply": "your message to the group - empty if none is wanted",
  "no_reply_because": "when the reply is empty: why",
  "for_user": "what {user} should know or decide from this - empty if nothing"}}"""

MODES = {
    "reply": "WHAT YOU WRITE IS SENT AS IT IS - once a reviewer, on {user}'s behalf, lets it through. "
             "No one reads it first: answer only what you are sure {user} would want said.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any, most: int) -> str:
    said = "".join(ch for ch in str(value or "") if ch in "\n\t" or ord(ch) >= 32)
    return said.strip()[:most]


class SiteGroups:
    def __init__(self, society: Any) -> None:
        self.society = society
        self.app = society.app
        self.db = society.db
        with self.db.write() as conn:
            conn.executescript(SCHEMA)
        self._answering = threading.Lock()
        self._read_at = 0.0

    # -- what the key permits ---------------------------------------------------------------
    def may(self, part: str) -> bool:
        return self.society.may("groups." + part)

    def _must(self, part: str, words: str) -> None:
        if not self.may(part):
            raise PermissionError(f"this agent's key may not {words} - give it groups.{part} on "
                                  "your account page on the website")

    # -- read from the website ----------------------------------------------------------------
    def read(self) -> None:
        """The account's groups and its invitations, where the key permits; an invitation new since
        told the user once - it is theirs to answer."""
        self._read_at = time.time()
        society = self.society
        for part, key, call, field in (("read", "site_groups", society.client.groups, "groups"),
                                       ("join", "group_invitations",
                                        society.client.group_invitations, "invitations")):
            if not self.may(part):
                society._set_state(key, [])
                continue
            try:
                found = [one for one in (call() or {}).get(field) or [] if isinstance(one, dict)]
            except Exception:
                log.info("the society %s could not be read", field, exc_info=True)
                continue
            society._set_state(key, found[:100])
            if key == "group_invitations":
                self._tell_invitations(found)

    def read_now_and_then(self) -> None:
        if time.time() - self._read_at >= READ_SECONDS:
            self.read()

    def _tell_invitations(self, found: List[Dict[str, Any]]) -> None:
        told = set(self.society._state("invitations_told") or [])
        notifications = getattr(self.app, "notifications", None)
        for one in found:
            key = f"{one.get('group')}:{one.get('at')}"
            if key in told:
                continue
            told.add(key)
            if notifications is None:
                continue
            by = self.society._name_of(str(one.get("invited_by") or "")) or one.get("invited_by") or ""
            # NO LONGER A NOTIFICATION (the user, 2026-11-13: "其它都取消"): it waits in Society, Groups.
            log.info("invited to a group (%s) by %s: in Society, Groups", key, by)
        self.society._set_state("invitations_told", sorted(told)[-300:])

    def collect(self) -> int:
        """The groups' messages since the last time, each kept once; the account's own kept as sent."""
        if not self.may("read"):
            return 0
        society = self.society
        after = float(society._state("groups_after", 0) or 0)
        new = 0
        names: Dict[str, str] = {}
        for _page in range(MOST_PAGES):
            found = (society.client.group_messages(after) or {}).get("messages") or []
            latest = after
            for one in found:
                if not isinstance(one, dict):
                    continue
                at = float(one.get("at") or 0)
                latest = max(latest, at)
                ident, group = _text(one.get("id"), 80), _text(one.get("group"), 40)
                sender = _text(one.get("from"), 40)
                if not ident or not group:
                    continue
                mine = society._is_me(sender)
                if not mine and sender not in names:
                    names[sender] = society._name_of(sender)
                done = self.db.execute(
                    "INSERT OR IGNORE INTO society_group_messages (id, group_id, peer_id, peer_name, "
                    "text, at, direction, received_at, handled_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ident, group, sender, "" if mine else names[sender],
                     _text(one.get("text"), MOST_TEXT), at, "out" if mine else "in", _now(),
                     _now() if mine else ""))
                new += 1 if getattr(done, "rowcount", 1) and not mine else 0
            society._set_state("groups_after", latest)
            if len(found) < PAGE or latest <= after:
                break
            after = latest
        return new

    # -- what the user sees ---------------------------------------------------------------------
    def _groups(self) -> List[Dict[str, Any]]:
        return [one for one in self.society._state("site_groups") or [] if isinstance(one, dict)]

    def group(self, group: str) -> Optional[Dict[str, Any]]:
        return next((one for one in self._groups() if one.get("id") == group), None)

    def modes(self) -> Dict[str, str]:
        said = self.society._state("group_modes") or {}
        return said if isinstance(said, dict) else {}

    def mode(self, group: str) -> str:
        mode = self.modes().get(group)
        return mode if mode in GROUP_MODES else DEFAULT_MODE      # an old "draft": answered

    def set_mode(self, group: str, mode: str) -> Dict[str, Any]:
        if mode not in GROUP_MODES:
            raise ValueError("a group is answered by reply or read")
        modes = dict(self.modes(), **{group: mode})
        self.society._set_state("group_modes", modes)
        return self.shown()

    def shown(self) -> Dict[str, Any]:
        """The panel's: what the key permits for groups, each group with how the agent answers in it,
        its unread and its last message, and the invitations waiting."""
        counts = {row["group_id"]: row for row in self.db.query(
            "SELECT group_id, SUM(CASE WHEN direction = 'in' AND read_at = '' THEN 1 ELSE 0 END) "
            "AS unread, MAX(at) AS last_at FROM society_group_messages GROUP BY group_id")}
        groups = []
        for one in self._groups():
            gid = str(one.get("id") or "")
            last = self.db.query_one("SELECT * FROM society_group_messages WHERE group_id = ? "
                                     "ORDER BY at DESC LIMIT 1", (gid,))
            groups.append(dict(one, mode=self.mode(gid),
                               unread=int((counts.get(gid) or {}).get("unread") or 0),
                               last_at=float((counts.get(gid) or {}).get("last_at") or
                                             one.get("joined_at") or 0),
                               last=self._row(last) if last else None))
        groups.sort(key=lambda one: -one["last_at"])
        invitations = []
        for one in self.society._state("group_invitations") or []:
            if isinstance(one, dict):
                by = str(one.get("invited_by") or "")
                # Its name as kept here - the panel reads nothing from the website for it.
                invitations.append(dict(one, invited_by_name=str(
                    (self.society.contact(by) or {}).get("name") or "") if by else ""))
        return {"may": {part: self.may(part) for part in ("read", "send", "create", "invite",
                                                          "join")},
                "groups": groups, "invitations": invitations,
                "unread": sum(one["unread"] for one in groups)}

    def unread(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM society_group_messages WHERE direction = "
                                "'in' AND read_at = ''") or {}
        return int(row.get("n") or 0)

    def _row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": row["id"], "from": row["peer_id"], "name": row["peer_name"],
                "text": row["text"], "at": row["at"], "mine": row["direction"] == "out",
                "sent_by": row["sent_by"], "handled": bool(row["handled_at"])}

    def thread(self, group: str, read: bool = False) -> Dict[str, Any]:
        found = self.group(group)
        if found is None:
            raise ValueError("no such group - it is not one of this account's")
        if read:
            self.db.execute("UPDATE society_group_messages SET read_at = ? WHERE group_id = ? AND "
                            "read_at = ''", (_now(), group))
        rows = self.db.query("SELECT * FROM society_group_messages WHERE group_id = ? ORDER BY at",
                             (group,))
        return {"group": dict(found, mode=self.mode(group)),
                "messages": [self._row(one) for one in rows[-300:]]}

    # -- acting on the website --------------------------------------------------------------
    def send(self, group: str, text: str, by: str = "user") -> Dict[str, Any]:
        """A MESSAGE IN A GROUP - the one way out for a group (`by`: user, agent). No drafts in a
        group."""
        self._must("send", "write in groups")
        text = _text(text, MOST_TEXT)
        if not text:
            raise ValueError("write something first")
        if by == "agent":
            since = time.time() - 3600
            sent = self.db.query("SELECT id FROM society_group_messages WHERE direction = 'out' AND "
                                 "sent_by = 'agent' AND at >= ?", (since,))
            if len(sent) >= AGENT_SENDS_AN_HOUR:
                raise PermissionError(f"you have written {AGENT_SENDS_AN_HOUR} in groups this hour - "
                                      "that is as many as you may")
        said = self.society.client.group_send(group, text)
        ident = _text(said.get("id"), 80) or ("gm_local_" + str(int(time.time() * 1000)))
        # After all that is kept of the group, whatever this computer's clock says: the site does
        # not say when it took it, and the others' times are the site's.
        last = self.db.query_one("SELECT MAX(at) AS at FROM society_group_messages WHERE group_id = ?",
                                 (group,)) or {}
        at = max(time.time(), float(last.get("at") or 0) + 0.001)
        self.db.execute(
            "INSERT OR REPLACE INTO society_group_messages (id, group_id, peer_id, text, at, "
            "direction, sent_by, received_at, read_at, handled_at) VALUES (?, ?, ?, ?, ?, 'out', ?, "
            "?, ?, ?)", (ident, group, self.society.account_id, text, at, by, _now(), _now(), _now()))
        if by != "user":                      # a thing the agent did: in its report
            from ..agent.reports import did
            name = (self.group(group) or {}).get("name") or group
            did(self.db, "society_group_sent", f"Wrote in the group {name}", text[:300],
                source="society", ref=group)
        return {"id": ident, "group": group, "status": "sent", "text": text}

    def create(self, name: str, description: str = "", public: bool = False,
               by: str = "user") -> Dict[str, Any]:
        self._must("create", "create groups")
        name, description = _text(name, MOST_NAME + 1), _text(description, MOST_DESCRIPTION + 1)
        if not name:
            raise ValueError("give the group a name")
        if len(name) > MOST_NAME:
            raise ValueError(f"a group's name is at most {MOST_NAME} characters")
        if len(description) > MOST_DESCRIPTION:
            raise ValueError(f"a group's description is at most {MOST_DESCRIPTION} characters")
        made = self.society.client.group_create(name, description, bool(public))
        self.read()
        if by != "user":
            from ..agent.reports import did
            did(self.db, "society_group_created", f"Made the group {name}", description,
                source="society", ref=str(made.get("id") or ""))
        return made

    def invite(self, group: str, account: str, by: str = "user") -> Dict[str, Any]:
        self._must("invite", "invite into groups")
        who = keys.normal_id(account)
        if not who:
            raise ValueError("an account id is pa-XXXX-XXXX")
        found = self.group(group) or {}
        if found and found.get("role") not in ("owner", "admin"):
            raise PermissionError("only the group's owner or an admin invites into it")
        said = self.society.client.group_invite(group, who)
        if by != "user":
            from ..agent.reports import did
            did(self.db, "society_group_invited",
                f"Invited {self.society._name_of(who) or who} into the group "
                f"{found.get('name') or group}", "", source="society", ref=group)
        return said

    def answer_invitation(self, group: str, join: bool, by: str = "user") -> Dict[str, Any]:
        self._must("join", "answer group invitations")
        said = self.society.client.group_invitation_answer(group, bool(join))
        self.read()
        if by != "user":
            from ..agent.reports import did
            did(self.db, "society_group_joined" if join else "society_group_declined",
                ("Joined the group " if join else "Declined the group ") + group, "",
                source="society", ref=group)
        return said

    # -- the agent's own answers, each group as the user set it -------------------------------
    def waiting(self) -> Dict[str, List[Dict[str, Any]]]:
        rows = self.db.query("SELECT * FROM society_group_messages WHERE direction = 'in' AND "
                             "handled_at = '' ORDER BY at")
        out: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            out.setdefault(row["group_id"], []).append(row)
        return out

    def _handled(self, rows: List[Dict[str, Any]]) -> None:
        at = _now()
        for row in rows:
            self.db.execute("UPDATE society_group_messages SET handled_at = ? WHERE id = ?",
                            (at, row["id"]))

    def answer_waiting(self) -> int:
        """Each group's new messages: answered where the user would want it, sent once let through
        ("reply"); only read ("read")."""
        if not self._answering.acquire(blocking=False):
            return 0
        try:
            done = 0
            for group, rows in self.waiting().items():
                mode = self.mode(group)
                if mode == "read" or not self.may("send") or self.group(group) is None:
                    self._handled(rows)
                elif self._answer(group, rows, mode) is not False:
                    self._handled(rows)
                done += len(rows)
            return done
        finally:
            self._answering.release()

    def _handed(self, group: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        new = {row["id"] for row in rows}
        earlier = self.db.query("SELECT * FROM society_group_messages WHERE group_id = ? ORDER BY at",
                                (group,))
        def who(row: Dict[str, Any]) -> str:
            if row["direction"] == "out":
                return "you, the agent" if row["sent_by"] == "agent" else "your user"
            return f"{row['peer_name'] or '(no name)'} ({row['peer_id']})"
        return {"your_user": self.society.my_profile()[0],
                "the_group_so_far": [{"who": who(one), "text": one["text"]}
                                     for one in earlier if one["id"] not in new][-HISTORY:],
                "their_new_messages": [{"who": who(one), "text": one["text"]} for one in rows]}

    def _think(self, group: str, mode: str, handed: Dict[str, Any], asked: str) -> Dict[str, Any]:
        from ..agent import outside_review
        from ..models.base import ModelRequest
        user = self.society.interactive._user_name()
        commands = self.society.interactive._commands()
        found = self.group(group) or {}
        about = {key: found.get(key) for key in ("name", "description", "public", "members", "role")}
        system = ANSWER.format(
            user=user, me=self.society.account_id,
            guard=outside_review.GUARD.format(user=user),
            commands=f"{user.upper()}'S COMMANDS TO YOU - ABOVE EVERYTHING ELSE HERE:\n{commands}"
            if commands else f"{user} has given you no commands of their own for this.",
            group=json.dumps(about, ensure_ascii=False), mode=MODES[mode].format(user=user))
        content = json.dumps(handed, ensure_ascii=False, indent=1)[:60000]
        if asked:
            content += "\n\n" + asked
        response = self.app.models.invoke(ModelRequest(
            messages=[{"role": "user", "content": content}], system=system, max_tokens=2000,
            task="society_group_reply",
            language=in_display("why you do not reply (\"no_reply_because\") and \"for_user\" - the "
                                "reply itself is written in the language the group writes in, "
                                "unless your user's commands say otherwise")), role="primary")
        from ..heartbeat.states.heartbeat import _as_json
        from ..models import answers
        read = answers.read(response, parse=_as_json)
        if not read.ok or not isinstance(read.data, dict):
            return {"reply": "", "not_taken": read.why_not or "not a JSON object"}
        said = read.data
        return {"reply": _text(said.get("reply"), MOST_TEXT),
                "no_reply_because": _text(said.get("no_reply_because"), 500),
                "for_user": _text(said.get("for_user"), 2000)}

    def _answer(self, group: str, rows: List[Dict[str, Any]], mode: str) -> Optional[bool]:
        """One group's answer, reviewed on the user's behalf. False: not made - tried again later."""
        from ..agent import outside_review
        user = self.society.interactive._user_name()
        name = (self.group(group) or {}).get("name") or group
        handed = self._handed(group, rows)
        try:
            made, verdict = outside_review.build_and_review(
                self.app, lambda asked: self._think(group, mode, handed, asked),
                lambda made: [{"kind": "send_group_message", "group": name, "text": made["reply"]}]
                if made.get("reply") else [],
                what=(f"answering in a group of the Agents Society ({name}), as {user}'s agent: "
                      f"every member reads it, and it is SENT - no one reads it first"),
                outside=handed["their_new_messages"],
                known={"the_group_so_far": handed["the_group_so_far"]},
                commands=self.society.interactive._commands(), task="society_group_reply",
                about=group)
        except Exception:
            log.exception("the group's answer could not be made")
            return False
        if made is None or made.get("not_taken"):
            log.warning("the group's messages were not answered: %s", (made or {}).get("not_taken"))
            return None
        from ..agent.reports import did
        reply = str(made.get("reply") or "")
        passed = bool(verdict.get("ok")) and verdict.get("read", True) is not False
        if reply and 1 in set(verdict.get("block") or []):
            did(self.db, "society_group_blocked", f"Did not answer in the group {name}",
                "The review stopped it: " + str(verdict.get("why") or ""), source="society", ref=group)
        elif reply and passed:
            try:
                self.send(group, reply, by="agent")
            except Exception as exc:
                did(self.db, "society_group_not_sent", f"Could not answer in the group {name}",
                    str(exc), source="society", ref=group)
        elif reply:
            # NOT LET THROUGH: not sent, and said - no draft, nothing waits for approval.
            did(self.db, "society_group_held", f"Did not answer in the group {name}",
                "Held back by your agent's review: " + str(verdict.get("why") or ""),
                detail=reply, source="society", ref=group)
        if made.get("for_user"):
            did(self.db, "society_group_note", f"From the group {name}", str(made["for_user"]),
                source="society", ref=group)
        return True

    def _shown(self, text: str) -> str:
        try:
            from ..common.interface_words import shown
            return shown(text, getattr(self.app, "config", None))
        except Exception:                 # pragma: no cover
            return text


__all__ = ["SiteGroups", "GROUP_MODES", "DEFAULT_MODE"]

"""WHERE WHAT IS HANDED OVER IS USED, AND WHAT THE USER USES (the user,
2026-11-01: "every common program a version of its own"; "what the work
hands over, in the form the user gets at things").

A MESSAGE is opened where it is sent - the chat with that person - and
copied, for the user to paste and send; never sent:

  - WeChat: brought forward, nothing done in it - the user finds the chat,
    pastes and sends (platform_layer/wechat_open.py; the user, 2026-11-01:
    clicking is theirs, by the instructions);
  - a link to the chat, the message filled in where the app takes one:
    WhatsApp (wa.me), Teams (its chat link, by email), LINE (its share
    link), a text message (sms:);
  - a link to the chat, the message to paste: Telegram (t.me), Messenger
    (m.me), Signal (signal.me), Skype (skype:), QQ (tencent://);
  - the app alone opened, its chat to be found by the user: Slack,
    Discord; and any other app: the message copied, where to paste it said.

Who it is for is the contact the work names; their handle on that app -
a phone number, a user name, an email - is the one the work gives, else the
one their contact keeps (heartbeat/connections).

WHAT THE USER USES - their mail, calendar, chats, documents, sheets, slides,
meetings and browser - is found from what they had open lately (`uses`),
and the work is told it, to hand things over in it (`uses_text`).
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

#: Each chat app: its name, what opens a chat ("wechat", a link with
#: {handle} and {text}, or the app's own link), what handle it takes, and
#: whether the message is filled in by the link.
CHATS: Dict[str, Dict[str, Any]] = {
    "wechat": {"name": "WeChat", "open": "wechat", "handle": "", "fills": False},
    "whatsapp": {"name": "WhatsApp", "link": "https://wa.me/{handle}?text={text}",
                 "handle": "phone", "fills": True},
    "teams": {"name": "Microsoft Teams",
              "link": "https://teams.microsoft.com/l/chat/0/0?users={handle}&message={text}",
              "handle": "email", "fills": True},
    "line": {"name": "LINE", "link": "https://line.me/R/msg/text/?{text}", "handle": "",
             "fills": True},
    "sms": {"name": "Messages", "link": "sms:{handle}?body={text}", "handle": "phone",
            "fills": True},
    "telegram": {"name": "Telegram", "link": "https://t.me/{handle}", "handle": "username",
                 "fills": False},
    "messenger": {"name": "Messenger", "link": "https://m.me/{handle}", "handle": "username",
                  "fills": False},
    "signal": {"name": "Signal", "link": "https://signal.me/#p/{handle}", "handle": "phone",
               "fills": False},
    "skype": {"name": "Skype", "link": "skype:{handle}?chat", "handle": "username",
              "fills": False},
    "qq": {"name": "QQ", "link": "tencent://message/?uin={handle}&Site=&Menu=yes",
           "handle": "number", "fills": False},
    "slack": {"name": "Slack", "app": "slack://open", "handle": "", "fills": False},
    "discord": {"name": "Discord", "app": "discord://", "handle": "", "fills": False},
}

#: What a user's app or page is, by the program's name or the site.
SEEN = (
    ("chats", "wechat", ("weixin.exe", "wechat.exe", "wechat")),
    ("chats", "slack", ("slack.exe", "slack", "app.slack.com")),
    ("chats", "teams", ("ms-teams.exe", "teams.exe", "teams.microsoft.com")),
    ("chats", "whatsapp", ("whatsapp.exe", "whatsapp", "web.whatsapp.com")),
    ("chats", "telegram", ("telegram.exe", "telegram", "web.telegram.org")),
    ("chats", "discord", ("discord.exe", "discord", "discord.com")),
    ("chats", "qq", ("qq.exe", "qqnt.exe")),
    ("chats", "line", ("line.exe",)),
    ("chats", "signal", ("signal.exe",)),
    ("chats", "skype", ("skype.exe",)),
    ("chats", "messenger", ("messenger.exe", "www.messenger.com")),
    ("chats", "dingtalk", ("dingtalk.exe",)),
    ("chats", "feishu", ("feishu.exe", "lark.exe")),
    ("documents", "Microsoft Word (.docx)", ("winword.exe",)),
    ("documents", "Google Docs", ("docs.google.com/document",)),
    ("documents", "Overleaf (LaTeX)", ("www.overleaf.com",)),
    ("documents", "Notion", ("notion.exe", "www.notion.so")),
    ("documents", "Pages", ("pages",)),
    ("sheets", "Microsoft Excel (.xlsx)", ("excel.exe",)),
    ("sheets", "Google Sheets", ("docs.google.com/spreadsheets",)),
    ("slides", "Microsoft PowerPoint (.pptx)", ("powerpnt.exe",)),
    ("slides", "Google Slides", ("docs.google.com/presentation",)),
    ("slides", "Keynote", ("keynote",)),
    ("meetings", "Zoom", ("zoom.exe", "zoom.us")),
    ("meetings", "Tencent Meeting / VooV", ("voovmeetingapp.exe", "wemeetapp.exe")),
    ("meetings", "Google Meet", ("meet.google.com",)),
    ("browser", "Firefox", ("firefox.exe", "firefox")),
    ("browser", "Chrome", ("chrome.exe", "chrome")),
    ("browser", "Edge", ("msedge.exe", "edge")),
    ("browser", "Safari", ("safari",)),
)

#: How many of the latest records are looked at, and how long what was found
#: is used again.
RECORDS_MOST, USES_FOR = 40000, 600.0

_uses: Dict[str, Any] = {}
_uses_lock = threading.Lock()


def chat_of(app: str) -> Dict[str, Any]:
    """A chat app by any name it goes by - {} for one not known."""
    key = re.sub(r"[^a-z]", "", str(app or "").lower())
    aliases = {"weixin": "wechat", "msteams": "teams", "microsoftteams": "teams",
               "imessage": "sms", "messages": "sms", "text": "sms", "tencentqq": "qq",
               "facebookmessenger": "messenger", "lark": "feishu"}
    key = aliases.get(key, key)
    return dict(CHATS[key], key=key) if key in CHATS else {}


def handle_for(store: Any, app: str, name: str) -> str:
    """Their handle on that app as their contact keeps it - "" if none."""
    chat = chat_of(app)
    if store is None or not chat or not str(name or "").strip():
        return ""
    try:
        from ...heartbeat.connections.store import name_key
        key = name_key(name)
        people = [one for one in store.people()
                  if key in {name_key(one["name"]), *[name_key(a) for a in one["aliases"]]}]
        if len(people) != 1:
            return ""
        for account in store.accounts(people[0]["id"]):
            if chat_of(account.get("platform")).get("key") == chat["key"]:
                return str(account.get("handle") or "").strip()
    except Exception:                                    # pragma: no cover - none then
        return ""
    return ""


def chat_link(app: str, handle: str, text: str) -> Dict[str, Any]:
    """HOW THE CHAT IS OPENED (module docstring): {"name", "how" ("wechat",
    "link", "app" or "copy"), "url", "fills"}."""
    chat = chat_of(app)
    if not chat:
        return {"name": str(app or "").strip() or "the app", "how": "copy", "url": "",
                "fills": False}
    out = {"name": chat["name"], "how": "copy", "url": "", "fills": False, "key": chat["key"]}
    if chat.get("open") == "wechat":
        return dict(out, how="wechat")
    if chat.get("link"):
        handle = str(handle or "").strip()
        if chat["handle"] == "phone":
            handle = re.sub(r"[^\d+]", "", handle).lstrip("+") if chat["key"] == "whatsapp" \
                else re.sub(r"[^\d+]", "", handle)
        if chat["handle"] and not handle:
            return dict(out, how="app" if chat.get("app") else "copy")
        return dict(out, how="link", fills=bool(chat["fills"]),
                    url=chat["link"].format(handle=quote(handle, safe="@+"),
                                            text=quote(text or "", safe="")))
    if chat.get("app"):
        return dict(out, how="app", url=chat["app"])
    return out


def uses(db: Any) -> Dict[str, List[str]]:
    """WHAT THE USER USES, by what they had open lately - each kind's, the
    most used first: {"chats": [...], "documents": [...], ...}."""
    with _uses_lock:
        if _uses and time.time() - _uses.get("at", 0) < USES_FOR:
            return {key: list(one) for key, one in _uses["said"].items()}
    counts: Dict[tuple, int] = {}
    try:
        rows = db.query("SELECT application, data FROM raw_events ORDER BY rowid DESC LIMIT ?",
                        (RECORDS_MOST,))
    except Exception:                                    # pragma: no cover - nothing known
        rows = []
    for row in rows:
        app = str(row["application"] or "").lower()
        url = ""
        if "://" in str(row["data"] or "")[:4000]:
            try:
                url = str(json.loads(row["data"] or "{}").get("url") or "").lower()
            except ValueError:
                url = ""
        for kind, what, marks in SEEN:
            if any(mark == app or (("." in mark or "/" in mark) and mark in url)
                   for mark in marks):
                counts[(kind, what)] = counts.get((kind, what), 0) + 1
    said: Dict[str, List[str]] = {}
    for (kind, what), count in sorted(counts.items(), key=lambda one: -one[1]):
        if count >= 3:
            said.setdefault(kind, []).append(what)
    with _uses_lock:
        _uses.clear()
        _uses.update(at=time.time(), said=said)
    return said


def uses_text(db: Any, mail: str = "", calendar: str = "") -> str:
    """What the work is told of what the user uses - "" when nothing is known."""
    said = uses(db)
    lines = []
    if mail:
        lines.append(f"mail: {mail}")
    if calendar:
        lines.append(f"calendar: {calendar}")
    for kind in ("chats", "documents", "sheets", "slides", "meetings", "browser"):
        if said.get(kind):
            names = [CHATS[one]["name"] if one in CHATS else one for one in said[kind]]
            lines.append(f"{kind}: {', '.join(names[:5])}")
    if not lines:
        return ""
    return ("WHAT THE USER USES (by what they had open lately, the most used first) - "
            "hand things over in these, in the form they open them in:\n  "
            + "\n  ".join(lines))


__all__ = ["CHATS", "chat_of", "chat_link", "handle_for", "uses", "uses_text"]

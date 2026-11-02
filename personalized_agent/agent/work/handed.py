"""WHAT THE USER GETS, AND HOW (the user, 2026-10-26).

A piece of work ends with something the user USES - an email they send, an
event in their calendar, a reply they paste, a document they open - and it
is handed to them in the form most convenient for that, not as whatever
file was easiest to write: an email to send is a draft opened in their own
mail, filled in, ready for them to read and send; never a .md to copy from.

The agent says what it hands over with `hand_over` (agent/work/tools.py),
one item of a KIND:

  email   to, cc, bcc, subject, body - opened as a new message in the
          user's mail, filled in (Gmail's or Outlook's compose page, or the
          computer's mail program by a mailto: link). A REPLY - reply_to, the
          email it answers (from, subject, date) - is opened IN THAT EMAIL'S
          THREAD, the reply copied, for the user to press Reply, paste and
          send (`reply_link`, below)
  event   title, start, end, where, details - opened as a new event in their
          calendar, filled in (Google Calendar's or Outlook's page), or as an
          .ics file their calendar program opens
  text    words to paste somewhere else - copied
  message to, app, text - a message to someone in a chat app: the chat with
          them opened (WeChat, WhatsApp, Teams, Telegram... -
          destinations.py) and the message copied, or filled in where the
          app takes it; the user sends it
  form    url, parts - what goes into a page's boxes (a review, an
          application): the page opened in the user's own browser, where
          they are signed in, each part to copy into its box, the first
          copied already
  file    a file the work made, opened in the program it opens with (one
          that would run as a program is shown in its folder instead)
  folder  a folder the work made, opened
  answer  the answer itself, shown

WITH EVERY ITEM, HOW AND WHAT TO CHECK (the user, 2026-11-01): the agent says
in a few lines how the user uses it ("how") and the few things to check
before they do ("check") - what they are most likely to miss, what the agent
is unsure of. Both are shown with it, and when it is opened they come up on
the desktop beside it, in a note of their own that stays on top until it is
closed (`note`; ui/desktop.py).

NOTHING IS SENT, SAVED OR SUBMITTED FOR THEM: every link opens a page filled
in, in the user's own browser where they are signed in; what is done with it
- send, save - is theirs. The agent never signs in anywhere and never writes
to the user's accounts.

WHERE IT IS OPENED - their mail, which account, their calendar - is the
user's setting ("mail_app", "mail_account", "calendar_app"), "auto" by
default: found from what they use, by the windows they had open (`found`).

A REPLY IN ITS THREAD (the user, 2026-11-01: a reply opened as a new message
was not in the thread it answered). No mail page opens a reply filled in, in
a thread, by a link; and the agent never reaches the user's account. But
the pages the user opened are in their browser's history, kept with their
titles - and a webmail page showing a message is titled by its subject
(Gmail: "<subject> - <account> - Gmail"). So the email a reply answers is
found among the mail pages the user opened (`thread_of`; mail_pages.py:
those the agent kept, half a year, and the browsers' own histories), the
newest of that subject - of their account first - and the reply's button
opens it there,
the reply copied: they press Reply, paste it and send it. Not found, in
Gmail a search for its subject is opened instead; elsewhere, it is opened
as a new message as before, its subject "Re: ...". Either way the new
message stays a button of its own.

CHECKED AGAINST WHO IT IS WITH (the user, 2026-11-01: two emails may share
a subject - "Meeting" - and Reply answers whoever is in the thread opened).
Each page of that subject is checked against who was on it while the user
had it open (the addresses and names read from its window, kept with the
page - mail_pages.py): whether the sender the reply answers, or someone it
is written to, is there. One that is - "matched" - is taken first; one read without any of
them - "differs" - never; one never read - "unchecked" - is taken, said so.
How many of the emails they opened share that subject is said too, and the
page asks the user to look before it opens anything (app.js).
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote, urlencode

KINDS = ("email", "event", "text", "file", "folder", "answer", "message", "form")
#: ONE THING, MORE THAN ONE WAY (the user, 2026-11-01: a message by Slack or
#: by email): at most this many ways of one thing - how many is the agent's.
WAYS_MOST = 3

#: A compose link longer than this may be refused by the page it opens:
#: then the body is left out of it, to be pasted - copied with a button.
LINK_MOST = 7500

#: What is kept of each part of an item - its words as long as the work made
#: them (the user, 2026-11-01: a review of a paper is long), within reason.
SHORT, LONG = 400, 200000

#: How long "how" may be, and how many things to check, each how long.
HOW_MOST, CHECKS_MOST, CHECK_MOST = 1200, 8, 300

#: What a reply or a forward puts before a subject, in the languages mail
#: programs write it in.
REPLY_MARK = re.compile(r"^\s*(?:(?:re|fw|fwd|aw|wg|sv|vs|antw|rif|r|回复|答复|回覆|转发|轉寄|轉發)"
                        r"\s*(?:\[\d+\])?\s*[:：]\s*)+", re.IGNORECASE)

#: For how long a thread found is used again.
THREAD_FOR = 120.0

#: What the agent is told of it - in every way's instructions that hands
#: work to the user.
HAND_OVER_RULES = """WHAT THE USER GETS, AND HOW. Make each result in the form most convenient
for what the user will do with it, and hand it over with hand_over - every
result, at the end of the work (again when it changes):
  - an email they will send: kind "email" (to, subject, body) - it opens in
    their own mail as a new message, filled in, for them to read and send.
    Not a .md or .txt to copy from. A REPLY to an email they got: also
    reply_to - the email it answers: from, its subject exactly as it is,
    date - and it is opened in that email's own thread for them to reply in.
  - a meeting or a date to keep: kind "event" - it opens in their calendar,
    filled in.
  - a message to someone in a chat app: kind "message" (to: who, app: which -
    WeChat, WhatsApp, Teams, Slack, Telegram... - handle: their phone, user
    name or email there if you know it, text) - the chat with them is opened
    and the message copied. WHICH APP - and whether an email or a message at
    all - IS YOURS TO CHOOSE: by what the user uses (below) and where they
    talk with that person (their contact's accounts: open_person; the
    records). Not an email when they talk in a chat app.
  - what goes into a web page's boxes - a review, an application, a form: kind
    "form" (url: the page, parts: [{field: the box's label, text}] in the
    page's order) - the page opens in their own browser, each part copied in
    turn. Never ask them to sign in anywhere: their browser is signed in.
  - words to paste somewhere else: kind "text".
  - a document, a sheet, slides, a page, code: the file - in the format they
    would open or share it in (a document to send: .docx or .pdf; figures
    in a sheet: .xlsx) - kind "file"; a set of files: kind "folder".
  - a question answered: kind "answer".
WITH EACH, HOW AND WHAT TO CHECK: "how" - a few lines, step by step, how the
user uses it - and "check" - the few things they must check before they do
(at most eight, each a line): what they are most likely to miss (who it goes
to, the account, a date, an attachment it mentions, what to fill in), and
what you are unsure of. Short and specific to this one - no general advice.
They come up on the user's desktop when they open it.
ONE THING, MORE THAN ONE WAY: where the user could reasonably do it more
than one way - a message to someone by Slack, or by email - hand over up to
3 ways; how many is yours to judge - one where one way is clearly theirs.
Each way is complete on its own - its own words, written for where it goes,
its own how and check - with the same choice (the one thing, in a few
words) and its own way ("By Slack", "By email"). MAKE THEM ONE AT A TIME:
hand each over as soon as it is done, then make the next - the user may
open one while you make the next, so a way handed over is not changed again.
Whether to also keep a copy as a file is yours to judge. NOTHING IS SENT,
SAVED OR SUBMITTED for the user: what you hand over is opened for them to
check, and what is done with it is theirs."""


def _short(value: Any, most: int = SHORT) -> str:
    return " ".join(str(value or "").split())[:most]


def _addresses(value: Any) -> List[str]:
    items = value if isinstance(value, list) else re.split(r"[,;]", str(value or ""))
    out = []
    for one in items:
        one = str(one or "").strip()
        if one and len(one) <= 320:
            out.append(one)
    return out[:30]


def _when(value: Any) -> str:
    """A moment as given - an ISO time, with its zone or the user's, or a
    date alone (all day); "" if it is neither."""
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return moment.isoformat()


def _checks(value: Any) -> List[str]:
    """The things to check, as given - a list, or lines."""
    items = value if isinstance(value, list) else str(value or "").splitlines()
    out = []
    for one in items:
        one = " ".join(str(one or "").split()).lstrip("-*• ").strip()[:CHECK_MOST]
        if one:
            out.append(one)
    return out[:CHECKS_MOST]


def _reply_to(value: Any) -> Dict[str, str]:
    """The email a reply answers, as given - {} for none."""
    if not isinstance(value, dict):
        return {}
    said = {key: _short(value.get(key)) for key in ("from", "subject", "date")}
    return {key: one for key, one in said.items() if one} if (
        said["subject"] or said["from"]) else {}


def checked(args: Dict[str, Any], resolve: Callable[[str], Optional[Path]]) -> Dict[str, Any]:
    """An item as kept, from what hand_over was called with - or {"error"}.
    `resolve`: a path the work may hand over (one of its own folders'),
    resolved; None for any other."""
    kind = str(args.get("kind") or "").strip().lower()
    if kind not in KINDS:
        return {"error": f"kind is one of {', '.join(KINDS)}"}
    item: Dict[str, Any] = {"kind": kind, "title": _short(args.get("title"), 200)}
    if kind == "email":
        item.update(to=_addresses(args.get("to")), cc=_addresses(args.get("cc")),
                    bcc=_addresses(args.get("bcc")), subject=_short(args.get("subject")),
                    body=str(args.get("body") or "")[:LONG])
        reply_to = _reply_to(args.get("reply_to"))
        if reply_to:
            item["reply_to"] = reply_to
        if not (item["subject"] or item["body"].strip()):
            return {"error": "an email needs a subject or a body"}
    elif kind == "event":
        item.update(start=_when(args.get("start")), end=_when(args.get("end")),
                    where=_short(args.get("where")), details=str(args.get("details") or "")[:8000])
        if not item["title"] or not item["start"]:
            return {"error": "an event needs a title and a start (an ISO time, or a date)"}
    elif kind in ("text", "answer"):
        item["text"] = str(args.get("text") or "")[:LONG]
        if not item["text"].strip():
            return {"error": f"a {kind} needs its text"}
    elif kind == "message":
        item.update(to=_short(args.get("to"), 200), app=_short(args.get("app"), 60),
                    handle=_short(args.get("handle"), 200),
                    text=str(args.get("text") or "")[:LONG])
        if not item["text"].strip() or not item["to"]:
            return {"error": "a message needs who it is to (to) and its text"}
        if not item["title"]:
            item["title"] = f"Message to {item['to']}"[:200]
    elif kind == "form":
        url = str(args.get("url") or "").strip()
        parts = [{"field": _short(one.get("field"), 200),
                  "text": str(one.get("text") or "")[:PART_MOST]}
                 for one in (args.get("parts") or []) if isinstance(one, dict)
                 and str(one.get("text") or "").strip()][:PARTS_MOST]
        if not url.startswith(("https://", "http://")) or not parts:
            return {"error": "a form needs the page it goes into (url, http or https) and "
                             "its parts ([{field, text}])"}
        item.update(url=url[:2000], parts=parts)
    else:
        found = resolve(str(args.get("path") or ""))
        if found is None or not found.exists():
            return {"error": "path: a file or folder this work made, that is there"}
        if (kind == "folder") != found.is_dir():
            return {"error": f"not a {kind}: {found}"}
        item["path"] = str(found)
    if not item["title"]:
        item["title"] = (item.get("subject") or Path(item.get("path") or "").name
                         or kind)[:200]
    choice = _short(args.get("choice"), 160)
    if choice:
        # One of the ways of one thing: its own words, how and check.
        item["choice"] = choice
        item["way"] = _short(args.get("way"), 80) or (item.get("app") or kind)
    how, check = str(args.get("how") or "").strip()[:HOW_MOST], _checks(args.get("check"))
    if how:
        item["how"] = how
    if check:
        item["check"] = check
    return item


def told_how(item: Dict[str, Any]) -> str:
    """"" when an item says how it is used and what to check; else what the
    agent is told to add (HAND_OVER_RULES)."""
    if item.get("how") and item.get("check"):
        return ""
    return ("say how the user uses it - how: a few lines, step by step - and what to "
            "check before they do - check: the few things they might miss or you are "
            "unsure of, a line each")


def _local(moment: str) -> str:
    """A time as the user reads it: their own, to the minute; a date as it is."""
    if len(moment or "") == 10:
        return moment
    try:
        return datetime.fromisoformat(moment).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return moment or ""


#: The part of an item that is its words - what the user may change and save
#: (an email's body, an event's details, the text, a form's parts); a file or a
#: folder has none.
WORDS_OF = {"email": "body", "event": "details", "text": "text", "answer": "text",
            "message": "text", "form": "parts"}

#: How many parts a form may have, and how long each.
PARTS_MOST, PART_MOST = 30, 20000

#: What the agent is told of what the user changed (the user, 2026-11-01:
#: "it may change it again, but from my version").
USERS_VERSION = """THE USER CHANGED WHAT YOU HANDED OVER - their version below is the one
now. Go on from it: whatever you change of it again, change THEIR version,
keeping what they changed unless the work truly needs otherwise (then say
in your report what and why); never hand over your earlier version again.
Changed, hand it over again with the same kind and title."""


def users_version(items: List[Dict[str, Any]]) -> str:
    """WHAT THE USER CHANGED of what was handed over, and saved (`edited_at`)
    - their words, for the agent to go on from - or "" when nothing is.
    Handed over again by the agent, an item is its again."""
    changed = [one for one in items or [] if one.get("edited_at")
               and WORDS_OF.get(str(one.get("kind") or ""))]
    if not changed:
        return ""

    def words(one: Dict[str, Any]) -> str:
        if one.get("kind") == "form":
            return "\n\n".join(f"[{part.get('field', '')}]\n{part.get('text', '')}"
                                for part in one.get("parts") or [])
        return str(one.get(WORDS_OF[str(one["kind"])]) or "")
    return USERS_VERSION + "\n\n" + "\n\n".join(
        f"{str(one.get('kind') or '').upper()} - {one.get('title', '')} (changed by the user "
        f"{str(one['edited_at'])[:16]}):\n<<<\n{words(one)[:12000]}\n>>>" for one in changed)


def what_it_is(item: Dict[str, Any], say: Callable[[str], str] = lambda text: text
               ) -> Dict[str, Any]:
    """THE ITEM ITSELF, AS THE NOTE SHOWS IT on its left (the user, 2026-11-01):
    {"kind", "meta" (its lines - who it is to, when, where), "text" (its
    words, or its path)}."""
    kind = item.get("kind") or ""
    meta: List[str] = []
    text = ""
    if kind == "email":
        # Labels of enough words to be found in the tables ("To: {0}" is too
        # short to be told from any other text).
        for label, key in (("Sent to", "to"), ("Copied to", "cc"), ("Hidden copy to", "bcc")):
            if item.get(key):
                meta.append(say(f"{label}: {', '.join(item[key])}"))
        if item.get("subject"):
            meta.append(say(f"Subject: {item['subject']}"))
        text = str(item.get("body") or "")
    elif kind == "event":
        when = _local(item.get("start") or "")
        if item.get("end"):
            when += " - " + _local(item["end"])
        meta.append(say(f"When: {when}"))
        if item.get("where"):
            meta.append(say(f"Where: {item['where']}"))
        text = str(item.get("details") or "")
    elif kind in ("text", "answer"):
        text = str(item.get("text") or "")
    elif kind == "message":
        from .destinations import chat_link
        meta.append(say(f"Sent to: {item.get('to', '')}"))
        meta.append(say(f"Sent in: {chat_link(item.get('app', ''), '', '')['name']}"))
        text = str(item.get("text") or "")
    elif kind == "form":
        meta.append(say(f"Goes into: {item.get('url', '')}"))
        return {"kind": kind, "meta": meta, "text": "", "editable": True,
                "parts": [dict(one) for one in item.get("parts") or []]}
    else:
        text = str(item.get("path") or "")
    return {"kind": kind, "meta": meta, "text": text[:LONG],
            "editable": kind in WORDS_OF}


def note(item: Dict[str, Any], acts: Optional[Dict[str, Any]] = None,
         say: Callable[[str], str] = lambda text: text, copied: bool = False) -> Dict[str, Any]:
    """WHAT COMES UP ON THE DESKTOP when an item is opened (module
    docstring): on the left the item itself - and whether it is already on
    the clipboard (`copied`) - on the right its title, how it is used, what
    to check: for a reply, the email it answers and who it is for first; in
    the user's language (`say`)."""
    check = list(item.get("check") or [])
    reply = (acts or {}).get("reply") or {}
    if item.get("kind") == "email" and reply:
        to = [*(item.get("to") or []), *(item.get("cc") or [])]
        first = [say(f"It answers: {answers(item)}")]
        if to:
            first.append(say(f"Reply sends to the sender alone - this is for {', '.join(to)}"))
        check = first + check
    check = check[:CHECKS_MOST + 2]
    title = item.get("title") or ""
    if item.get("way"):
        title = f"{title} - {item['way']}"
    if item.get("kind") in ("email", "message", "text", "form"):
        # IN THEIR OWN VOICE (the user, 2026-11-01): checked by them, last.
        check.append(say("Does it sound like you - your words, your tone?"))
    return {"title": title, "kind": item.get("kind") or "",
            "how": item.get("how") or "", "check": check,
            "item": what_it_is(item, say), "copied": bool(copied)}


def kept(items: List[Dict[str, Any]], item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The list with `item`: in place of the one of the same kind and title -
    a result handed over again, changed - else added. At most 20."""
    out = [one for one in items if (one.get("kind"), one.get("title"))
           != (item.get("kind"), item.get("title"))]
    out.append(item)
    return out[-20:]


def shown(item: Dict[str, Any]) -> str:
    """An item as the checks and the reviewer read it - with how it is used
    and what to check."""
    said = _shown(item)
    if item.get("choice"):
        said = f"ONE WAY ({item.get('way')}) OF: {item['choice']}\n" + said
    if item.get("how"):
        said += "\nHow: " + item["how"]
    if item.get("check"):
        said += "\nCheck:\n" + "\n".join(f"- {one}" for one in item["check"])
    return said


def _shown(item: Dict[str, Any]) -> str:
    kind = item.get("kind")
    if kind == "email":
        return "\n".join(filter(None, [
            f"EMAIL - {item.get('title', '')}", f"To: {', '.join(item.get('to') or [])}",
            f"Cc: {', '.join(item['cc'])}" if item.get("cc") else "",
            f"Bcc: {', '.join(item['bcc'])}" if item.get("bcc") else "",
            f"Subject: {item.get('subject', '')}",
            "In reply to: " + " - ".join(item["reply_to"].get(key, "") for key in
                                         ("from", "subject", "date") if item["reply_to"].get(key))
            if item.get("reply_to") else "",
            "", str(item.get("body") or "")]))
    if kind == "event":
        return "\n".join(filter(None, [
            f"EVENT - {item.get('title', '')}", f"From {item.get('start')} to "
            f"{item.get('end') or '(no end)'}", f"Where: {item['where']}" if item.get("where")
            else "", str(item.get("details") or "")]))
    if kind in ("text", "answer"):
        return f"{kind.upper()} - {item.get('title', '')}\n{item.get('text', '')}"
    if kind == "message":
        return (f"MESSAGE - {item.get('title', '')}\nTo: {item.get('to', '')} in "
                f"{item.get('app', '') or 'a chat app'}\n\n{item.get('text', '')}")
    if kind == "form":
        return (f"FORM - {item.get('title', '')}\nInto: {item.get('url', '')}\n\n"
                + "\n\n".join(f"[{one.get('field', '')}]\n{one.get('text', '')}"
                                for one in item.get("parts") or []))
    return f"{kind.upper()} - {item.get('title', '')}: {item.get('path', '')}"


# -- where it is opened ---------------------------------------------------------------------------
_found: Dict[str, Any] = {}
_found_lock = threading.Lock()
FOUND_FOR = 600.0


def found(db: Any) -> Dict[str, str]:
    """WHAT THE USER USES, by the windows they had open lately: Gmail or
    Outlook, the Gmail account they use most (a Gmail window's title names
    it), Google Calendar or Outlook's. Looked at again every ten minutes."""
    with _found_lock:
        if _found and time.time() - _found.get("at", 0) < FOUND_FOR:
            return dict(_found["said"])
    counts = {"gmail": 0, "outlook": 0, "google": 0, "outlook_calendar": 0}
    accounts: Dict[str, int] = {}
    try:
        rows = db.query("SELECT data FROM raw_events ORDER BY rowid DESC LIMIT 20000")
    except Exception:                      # pragma: no cover - nothing known then
        rows = []
    for row in rows:
        text = str(row["data"] or "")
        if "Gmail" in text or "mail.google.com" in text:
            counts["gmail"] += 1
            for address in re.findall(r"- ([\w.+-]+@[\w-]+(?:\.[\w-]+)+) - Gmail", text):
                accounts[address.lower()] = accounts.get(address.lower(), 0) + 1
        if "outlook.live.com/mail" in text or "outlook.office.com/mail" in text \
                or "- Outlook" in text:
            counts["outlook"] += 1
        if "calendar.google.com" in text or "Google Calendar" in text:
            counts["google"] += 1
        if "outlook.office.com/calendar" in text or "outlook.live.com/calendar" in text:
            counts["outlook_calendar"] += 1
    said = {"mail": ("gmail" if counts["gmail"] >= counts["outlook"] and counts["gmail"]
                     else "outlook" if counts["outlook"] else ""),
            "account": max(accounts, key=accounts.get) if accounts else "",
            "calendar": ("google" if counts["google"] >= counts["outlook_calendar"]
                         and counts["google"] else
                         "outlook" if counts["outlook_calendar"] else "")}
    with _found_lock:
        _found.clear()
        _found.update(at=time.time(), said=said)
    return dict(said)


def where(config: Any, db: Any) -> Dict[str, str]:
    """Where an item is opened: the user's settings, "auto" found from what
    they use - and, found nothing, the computer's own mail program and a
    calendar file."""
    def setting(key: str, default: str) -> str:
        try:
            return str(config.get(key, default) or default)
        except Exception:                  # pragma: no cover - the default then
            return default
    mail, account, calendar = (setting("mail_app", "auto"), setting("mail_account", ""),
                               setting("calendar_app", "auto"))
    if "auto" in (mail, calendar) or not account:
        seen = found(db)
        mail = (seen["mail"] or "default") if mail == "auto" else mail
        calendar = (seen["calendar"] or "file") if calendar == "auto" else calendar
        account = account or (seen["account"] if mail == "gmail" else "")
    return {"mail": mail, "account": account, "calendar": calendar}


def _q(params: Dict[str, Any]) -> str:
    return urlencode({k: v for k, v in params.items() if v not in ("", None, [])},
                     quote_via=quote, safe="@,")


def email_link(item: Dict[str, Any], place: Dict[str, str]) -> Dict[str, Any]:
    """The new message, filled in: {"url", "whole"} - "whole" false when the
    body was too long for the link and is left out, to be pasted."""
    to, cc, bcc = (",".join(item.get(k) or []) for k in ("to", "cc", "bcc"))
    subject, body = item.get("subject") or "", item.get("body") or ""

    def build(with_body: bool) -> str:
        text = body if with_body else ""
        if place["mail"] == "gmail":
            return "https://mail.google.com/mail/?" + _q({
                "authuser": place.get("account"), "view": "cm", "fs": "1", "to": to,
                "cc": cc, "bcc": bcc, "su": subject, "body": text})
        if place["mail"] == "outlook":
            return "https://outlook.office.com/mail/deeplink/compose?" + _q({
                "to": to, "cc": cc, "bcc": bcc, "subject": subject, "body": text})
        return f"mailto:{quote(to, safe='@,')}?" + _q({"cc": cc, "bcc": bcc,
                                                       "subject": subject, "body": text})
    url = build(True)
    if len(url) <= LINK_MOST:
        return {"url": url, "whole": True}
    return {"url": build(False), "whole": False}


def _stamp(moment: str, all_day: bool) -> str:
    if all_day:
        return moment.replace("-", "")
    return datetime.fromisoformat(moment).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _end_of(item: Dict[str, Any]) -> str:
    start = item["start"]
    if item.get("end"):
        return item["end"]
    if len(start) == 10:
        return (datetime.fromisoformat(start) + timedelta(days=1)).date().isoformat()
    return (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()


def event_link(item: Dict[str, Any], place: Dict[str, str]) -> str:
    """The new event, filled in, in Google Calendar or Outlook - "" when the
    user's calendar takes a file (`ics`)."""
    all_day = len(item["start"]) == 10
    end = _end_of(item)
    if place["calendar"] == "google":
        return "https://calendar.google.com/calendar/render?" + _q({
            "authuser": place.get("account"), "action": "TEMPLATE", "text": item["title"],
            "dates": f"{_stamp(item['start'], all_day)}/{_stamp(end, all_day)}",
            "details": item.get("details"), "location": item.get("where")})
    if place["calendar"] == "outlook":
        return "https://outlook.office.com/calendar/deeplink/compose?" + _q({
            "subject": item["title"], "startdt": item["start"], "enddt": end,
            "allday": "true" if all_day else "", "body": item.get("details"),
            "location": item.get("where")})
    return ""


def ics(item: Dict[str, Any]) -> str:
    """The event as a calendar file - what any calendar program opens."""
    all_day = len(item["start"]) == 10

    def line(text: str) -> str:
        return (str(text or "").replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))
    when = (lambda key, moment: f"{key};VALUE=DATE:{_stamp(moment, True)}") if all_day \
        else (lambda key, moment: f"{key}:{_stamp(moment, False)}")
    return "\r\n".join(filter(None, [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Personal Agent//Work//EN",
        "BEGIN:VEVENT", f"UID:{abs(hash((item['title'], item['start'])))}@personal-agent",
        f"DTSTAMP:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        when("DTSTART", item["start"]), when("DTEND", _end_of(item)),
        f"SUMMARY:{line(item['title'])}",
        f"LOCATION:{line(item['where'])}" if item.get("where") else "",
        f"DESCRIPTION:{line(item['details'])}" if item.get("details") else "",
        "END:VEVENT", "END:VCALENDAR", ""]))


def unmarked(subject: Any) -> str:
    """A subject without what a reply or a forward put before it."""
    return " ".join(REPLY_MARK.sub("", str(subject or "")).split())


def answers(item: Dict[str, Any]) -> str:
    """The subject of the email a reply answers - "" when it answers none:
    as reply_to says it, else its own subject's when that is a reply's."""
    said = unmarked((item.get("reply_to") or {}).get("subject"))
    if said:
        return said
    subject = str(item.get("subject") or "")
    return unmarked(subject) if REPLY_MARK.match(subject) else ""


_threads: Dict[tuple, tuple] = {}
_threads_lock = threading.Lock()


def who_of(item: Dict[str, Any]) -> List[str]:
    """Who a reply's thread is with: the sender it answers - address and
    name - and the addresses it is written to."""
    said = str((item.get("reply_to") or {}).get("from") or "")
    who = re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", said)
    name = " ".join(re.sub(r"<[^>]*>|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|[\"']", " ", said).split())
    if len(name) >= 3:
        who.append(name)
    for one in [*(item.get("to") or []), *(item.get("cc") or [])]:
        who += re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", str(one))
    out: List[str] = []
    for one in who:
        if one.casefold() not in {had.casefold() for had in out}:
            out.append(one)
    return out[:12]


def _on_it(people: List[str], who: List[str]) -> str:
    """The first of `who` among the people on a page - an address the
    same, a name in one of theirs or theirs in it - "" for none."""
    for person in who:
        mine = person.casefold()
        for one in people:
            theirs = one.casefold()
            if mine == theirs or ("@" not in mine and "@" not in theirs
                                  and (mine in theirs or theirs in mine)):
                return person
    return ""


def thread_of(db: Any, subject: str, account: str = "", who: Optional[List[str]] = None,
              history: Optional[Callable[[], List[tuple]]] = None) -> Optional[Dict[str, Any]]:
    """THE EMAIL OF THAT SUBJECT, AS THE USER OPENED IT in their webmail,
    CHECKED AGAINST WHO IT IS WITH (module docstring): {"url", "in",
    "title", "at" (when it was last opened), "check" ("matched",
    "unchecked" or "differs"), "matched" (who was found on it),
    "same_subject" (how many of the emails they opened have it)} - or None.
    The pages kept (mail_pages.py, brought up to date first), and those in
    the browsers' own histories (`history`), unchecked."""
    from . import mail_pages
    key = " ".join(unmarked(subject).split()).casefold()
    if not key or db is None:
        return None
    who = list(who or [])
    with _threads_lock:
        kept = _threads.get((key, account, tuple(who)))
        if kept and time.time() - kept[0] < THREAD_FOR:
            return kept[1]

    def same(title: str) -> bool:
        shown = " ".join(unmarked(title).split()).casefold()
        return shown == key or shown.startswith(key + " - ")
    try:
        mail_pages.keep(db)
    except Exception:                      # pragma: no cover - what is kept is used
        pass
    pages: Dict[str, Dict[str, Any]] = {}
    for one in mail_pages.kept(db):
        if same(one["title"]):
            pages[one["url"]] = dict(one)
    for url, site, title, at in (history() if history is not None else []):
        if url not in pages and same(title):
            pages[url] = {"url": url, "in": site, "title": title, "at": at,
                          "people": [], "read": False}
        elif url in pages and at > str(pages[url]["at"] or ""):
            pages[url]["at"] = at
    candidates = []
    for one in pages.values():
        one["check"], one["matched"] = "unchecked", ""
        if who and one["read"]:
            one["matched"] = _on_it(one["people"], who)
            one["check"] = "matched" if one["matched"] else "differs"
        one["mine"] = bool(account) and account.lower() in one["title"].lower()
        candidates.append(one)
    rank = {"matched": 0, "unchecked": 1, "differs": 2}
    candidates.sort(key=lambda one: str(one["at"] or ""), reverse=True)
    candidates.sort(key=lambda one: (rank[one["check"]], not one["mine"]))
    found: Optional[Dict[str, Any]] = None
    if candidates:
        best = candidates[0]
        found = {name: best[name] for name in ("url", "in", "title", "at", "check", "matched")}
        found["same_subject"] = len(pages)
    with _threads_lock:
        _threads[(key, account, tuple(who))] = (time.time(), found)
    return found


def reply_link(item: Dict[str, Any], place: Dict[str, str], db: Any,
               history: Optional[Callable[[], List[tuple]]] = None) -> Dict[str, str]:
    """A REPLY, OPENED IN ITS THREAD (module docstring): {"url", "found",
    "in"} - "found" "thread" for the email itself, "search" for Gmail's
    search for it - or {} when it is no reply, or nothing better than a new
    message is to be had."""
    subject = answers(item)
    if not subject:
        return {}
    account = place.get("account") or ""
    found = thread_of(db, subject, account, who_of(item), history)
    said = {"answers": subject, "account": account,
            "same_subject": (found or {}).get("same_subject", 0)}
    if found and found["check"] != "differs":
        return dict(said, url=found["url"], found="thread", **{
            key: found[key] for key in ("in", "title", "at", "check", "matched")})
    if place.get("mail") == "gmail":
        asked = "subject:(" + re.sub(r'[()"]', " ", subject).strip() + ")"
        return dict(said, url="https://mail.google.com/mail/" + (
            "?" + _q({"authuser": account}) if account else "") + "#search/" + quote(asked, safe=""),
                    found="search", **{"in": "Gmail"},
                    differs=bool(found and found["check"] == "differs"))
    return {}


def actions(item: Dict[str, Any], place: Dict[str, str], db: Any = None,
            history: Optional[Callable[[], List[tuple]]] = None) -> Dict[str, Any]:
    """What the page offers for an item: "open" (a link, opened in the
    user's own browser - or a path), "copy" (text to copy), "whole" (the
    link holds all of it), where it opens ("in") - and for a reply, "reply"
    (`reply_link`), with the user's records in `db` and their browsers'
    histories (`history`: mail_pages.from_history)."""
    kind = item.get("kind")
    if kind == "email":
        link = email_link(item, place)
        said = {"open": link["url"], "whole": link["whole"], "copy": item.get("body") or "",
                "in": {"gmail": "Gmail", "outlook": "Outlook"}.get(place["mail"], "your mail app")}
        reply = reply_link(item, place, db, history) if db is not None else {}
        if reply:
            said["reply"] = reply
        return said
    if kind == "event":
        url = event_link(item, place)
        return {"open": url, "ics": not url, "whole": True,
                "in": {"google": "Google Calendar", "outlook": "Outlook"}.get(
                    place["calendar"], "your calendar")}
    if kind in ("text", "answer"):
        return {"copy": item.get("text") or ""}
    if kind == "message":
        from .destinations import chat_link
        chat = chat_link(item.get("app", ""), item.get("handle", ""), item.get("text", ""))
        return {"copy": item.get("text") or "", "chat": chat, "in": chat["name"]}
    if kind == "form":
        parts = item.get("parts") or []
        return {"open": item.get("url", ""), "copy": (parts[0].get("text") if parts else ""),
                "parts": len(parts)}
    return {"path": item.get("path") or ""}

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
          computer's mail program by a mailto: link)
  event   title, start, end, where, details - opened as a new event in their
          calendar, filled in (Google Calendar's or Outlook's page), or as an
          .ics file their calendar program opens
  text    words to paste somewhere else - a chat reply, a form - copied
  file    a file the work made, opened in the program it opens with (one
          that would run as a program is shown in its folder instead)
  folder  a folder the work made, opened
  answer  the answer itself, shown

NOTHING IS SENT, SAVED OR SUBMITTED FOR THEM: every link opens a page filled
in, in the user's own browser where they are signed in; what is done with it
- send, save - is theirs. The agent never signs in anywhere and never writes
to the user's accounts.

WHERE IT IS OPENED - their mail, which account, their calendar - is the
user's setting ("mail_app", "mail_account", "calendar_app"), "auto" by
default: found from what they use, by the windows they had open (`found`).
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote, urlencode

KINDS = ("email", "event", "text", "file", "folder", "answer")

#: A compose link longer than this may be refused by the page it opens:
#: then the body is left out of it, to be pasted - copied with a button.
LINK_MOST = 7500

#: What is kept of each part of an item.
SHORT, LONG = 400, 60000

#: What the agent is told of it - in every way's instructions that hands
#: work to the user.
HAND_OVER_RULES = """WHAT THE USER GETS, AND HOW. Make each result in the form most convenient
for what the user will do with it, and hand it over with hand_over - every
result, at the end of the work (again when it changes):
  - an email they will send: kind "email" (to, subject, body) - it opens in
    their own mail as a new message, filled in, for them to read and send.
    Not a .md or .txt to copy from.
  - a meeting or a date to keep: kind "event" - it opens in their calendar,
    filled in.
  - words to paste somewhere - a chat reply, a form's answer: kind "text".
  - a document, a sheet, slides, a page, code: the file - in the format they
    would open or share it in (a document to send: .docx or .pdf; figures
    in a sheet: .xlsx) - kind "file"; a set of files: kind "folder".
  - a question answered: kind "answer".
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
    return item


def kept(items: List[Dict[str, Any]], item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The list with `item`: in place of the one of the same kind and title -
    a result handed over again, changed - else added. At most 20."""
    out = [one for one in items if (one.get("kind"), one.get("title"))
           != (item.get("kind"), item.get("title"))]
    out.append(item)
    return out[-20:]


def shown(item: Dict[str, Any]) -> str:
    """An item as the checks and the reviewer read it."""
    kind = item.get("kind")
    if kind == "email":
        return "\n".join(filter(None, [
            f"EMAIL - {item.get('title', '')}", f"To: {', '.join(item.get('to') or [])}",
            f"Cc: {', '.join(item['cc'])}" if item.get("cc") else "",
            f"Bcc: {', '.join(item['bcc'])}" if item.get("bcc") else "",
            f"Subject: {item.get('subject', '')}", "", str(item.get("body") or "")]))
    if kind == "event":
        return "\n".join(filter(None, [
            f"EVENT - {item.get('title', '')}", f"From {item.get('start')} to "
            f"{item.get('end') or '(no end)'}", f"Where: {item['where']}" if item.get("where")
            else "", str(item.get("details") or "")]))
    if kind in ("text", "answer"):
        return f"{kind.upper()} - {item.get('title', '')}\n{item.get('text', '')}"
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


def actions(item: Dict[str, Any], place: Dict[str, str]) -> Dict[str, Any]:
    """What the page offers for an item: "open" (a link, opened in the
    user's own browser - or a path), "copy" (text to copy), "whole" (the
    link holds all of it), and where it opens ("in")."""
    kind = item.get("kind")
    if kind == "email":
        link = email_link(item, place)
        return {"open": link["url"], "whole": link["whole"], "copy": item.get("body") or "",
                "in": {"gmail": "Gmail", "outlook": "Outlook"}.get(place["mail"], "your mail app")}
    if kind == "event":
        url = event_link(item, place)
        return {"open": url, "ics": not url, "whole": True,
                "in": {"google": "Google Calendar", "outlook": "Outlook"}.get(
                    place["calendar"], "your calendar")}
    if kind in ("text", "answer"):
        return {"copy": item.get("text") or ""}
    return {"path": item.get("path") or ""}

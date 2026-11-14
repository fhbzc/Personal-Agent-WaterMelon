"""WHAT IS IN FRONT OF THE USER, for a message from the chat bubble (the user,
2026-11-04: opened by a click, the chat sees the front screen - its address,
its file name, its title).

Read from what the listener has already recorded, so what the listener may not
see is not seen here, on every system alike. Clicking the avatar or writing in
the bubble makes this application the front one: the window meant is the last
that was not ours.

Told to the model with the turn, and kept out of the conversation: what is
written down is what the user said. The bubble says what goes with a message, and
it can be taken off; the model is told the message may have nothing to do with it
(the user, 2026-11-04).
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List

from ..runtime.documents import shows_credentials

log = logging.getLogger(__name__)

#: How much of what the screen last showed goes with a message.
MOST_TEXT = 4000

FRONT_NOTE = """

[What the user has in front of them now, read by this application - not said by them. Their message may have nothing to do with it. Use it only where it plainly bears on what they say ("this", "here", "the page"); otherwise answer as if it were not there, and do not mention it.
{lines}]"""


def _rows(db: Any, kind: str, limit: int) -> List[Dict[str, Any]]:
    rows = []
    for row in db.query("SELECT application, data, timestamp FROM raw_events WHERE event_type = ? "
                        "ORDER BY timestamp DESC LIMIT ?", (kind, limit)):
        try:
            rows.append({**dict(row), "data": json.loads(row["data"] or "{}")})
        except ValueError:
            continue
    return rows


def front_now(db: Any, config: Any) -> Dict[str, str]:
    """The last window that was not this application's: "application",
    "title", and where they can be told "address", "file" and "text"."""
    if db is None or not config.get("listener_enabled", True):
        return {}
    # NOTHING OF THE SCREEN in private mode, or while a file in a privacy folder is open (the
    # user, 2026-11-12: "整个chat不会输出屏幕内容").
    from ..common.privacy_folders import why_no_picture
    if why_no_picture(config):
        return {}
    window = next((one for one in _rows(db, "application.focused", 40)
                   if one["data"].get("pid") != os.getpid()), None)
    if window is None:
        return {}
    title = str(window["data"].get("window_title") or "")
    said = {"application": str(window["application"] or ""), "title": title}
    # A page and a file are known to be this window's by their name in its title.
    for one in _rows(db, "browser.navigation", 50):
        page = str(one["data"].get("title") or "")
        if page and page in title and one["data"].get("url"):
            said["address"] = str(one["data"]["url"])
            break
    for one in _rows(db, "filesystem.opened", 200):
        path = str(one["data"].get("path") or "")
        name = re.split(r"[\\/]", path)[-1]
        if name and name in title:
            said["file"] = path
            break
    for one in _rows(db, "screen.text", 20):
        if (one["timestamp"] >= window["timestamp"]
                and one["data"].get("window_title") == title):
            text = str(one["data"].get("text") or "").strip()
            if text and not shows_credentials(text):
                said["text"] = text[:MOST_TEXT]
            break
    return said


def shown_line(said: Dict[str, str]) -> str:
    """What the user is shown goes with their message: the window, and the page
    or the file in it."""
    name = said.get("title") or said.get("application") or ""
    place = said.get("address") or re.split(r"[\\/]", said.get("file") or "")[-1]
    if place and place not in name:
        name = f"{name} ({place})" if name else place
    return name


def front_note(db: Any, config: Any) -> str:
    """The same, as words to put after the message; "" when nothing is known."""
    try:
        said = front_now(db, config)
    except Exception:                  # a message goes, whatever this could not read
        log.info("what is in front of the user could not be read", exc_info=True)
        return ""
    return note_of(said)


def note_of(said: Dict[str, str]) -> str:
    """What `front_now` read, as words to put after the message."""
    if not said:
        return ""
    names = (("application", "Program"), ("title", "Window title"), ("address", "Web address"),
             ("file", "File"), ("text", "On the screen"),
             ("picture_words", "Words read in a picture of the screen"),
             ("pointer", "The mouse pointer in the picture of the screen"))
    return FRONT_NOTE.format(lines="\n".join(f"{name}: {said[key]}" for key, name in names
                                             if said.get(key)))


__all__ = ["front_now", "front_note", "note_of", "shown_line", "MOST_TEXT"]

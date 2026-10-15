"""This application's own panel, which is not a site the user visited.

The agent serves its interface over HTTP on this machine, and the user opens
it in their ordinary browser. So every look at the Overview, every trip to
Settings, went into the browser history like any other page - and came back
through the Listener as browsing. On this machine ``127.0.0.1:8756`` was the
sixth most visited host in the record, ahead of the search engine and the
sites the user actually reads.

It is the third road into the same loop that ``agent_writes`` and
``agent_sessions`` close: there the agent heard its own files and its own
voice, here it watches the user watching it. And it is the least useful of
the three, because the agent already knows every interaction with its own
interface - it served them.

**Not a site, but still the browser being used.** What is dropped is the
navigation: the URL, the site it counts towards, the page in a list of pages
visited. The window title is left alone, because the browser genuinely was
showing that page and what an application was doing is not something to blank
out (section 9, as amended).
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlsplit

#: Where "this machine" is spelled. A port is what distinguishes our own
#: interface from anything else the user runs locally, so a loopback address
#: alone is never enough - people develop on this machine too.
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"}

DEFAULT_PORT = 8756


def our_port(config: Any = None) -> int:
    """The port the interface is served on, as configured."""
    if config is None:
        return DEFAULT_PORT
    get = getattr(config, "get", None)
    if get is None:
        return DEFAULT_PORT
    try:
        return int(get("api_port", DEFAULT_PORT) or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT


def is_our_own_interface(url: object, port: Optional[int] = None) -> bool:
    """Whether this address is this application's own panel."""
    text = str(url or "").strip()
    if not text:
        return False
    try:
        parts = urlsplit(text if "//" in text else "//" + text)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if host not in LOOPBACK:
        return False
    try:
        found = parts.port
    except ValueError:                           # a port that is not a number
        return False
    return found == (DEFAULT_PORT if port is None else int(port))


#: The name a sign-in link's ticket goes by (api/access.py). A link that
#: opens this application's page signed in carries one, and it is no more
#: the user's browsing than the page is - and less something to keep, being
#: a credential for as long as it lives.
TICKET_PARAM = "pa_sign_in"

#: A ticket wherever it may have been written down: in a link, encoded or
#: not, in a page title, in a log line.
_TICKET_IN_TEXT = re.compile(
    r"(?i)(" + TICKET_PARAM + r"(?:=|%3D))[^&#\s\"'<>]+")


def scrub_sign_in(value: Any) -> Any:
    """``value`` with every sign-in ticket in it cut out.

    For what is about to be recorded - a Listener event, a log line. Walks
    dictionaries and lists; anything that holds no ticket comes back as the
    same object, so an event without one costs a search and nothing more.
    """
    if isinstance(value, str):
        if TICKET_PARAM not in value.lower():
            return value
        return _TICKET_IN_TEXT.sub(r"\1[removed]", value)
    if isinstance(value, dict):
        changed = {key: scrub_sign_in(item) for key, item in value.items()}
        return value if all(changed[k] is value[k] for k in value) else changed
    if isinstance(value, (list, tuple)):
        changed = [scrub_sign_in(item) for item in value]
        if all(a is b for a, b in zip(changed, value)):
            return value
        return type(value)(changed) if isinstance(value, tuple) else changed
    return value


__all__ = ["LOOPBACK", "DEFAULT_PORT", "TICKET_PARAM", "our_port",
           "is_our_own_interface", "scrub_sign_in"]

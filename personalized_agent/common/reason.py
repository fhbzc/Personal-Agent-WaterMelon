"""Why a thing exists, recorded on the thing itself (section 52.7, as amended).

Every task, state and calendar entry the agent creates says what judgement led
to it. Not as an optional extra field that thoughtful callers fill in - as
something the system puts there, because the moment it is optional it is
missing exactly where it is needed.

The reasoning is the same one behind evidence on a state (section 34.1) and
references on a record (section 39.1). An agent that acts on its own judgement
and leaves no account of that judgement gives the user two choices: accept
everything, or switch it off. A reminder that turns out to be unwelcome is a
different conversation when the user can read *why the agent thought it was
worth it* - and it is the only way the agent's own decisions can be reviewed
at all, by them or by anyone.

A user's own entry needs no justification. They put it there; that is the
reason, and asking them to type one would be the system demanding an
explanation from the only participant who does not owe one. So a user-created
thing with nothing written gets ``added by user``.

An **agent**-created thing with nothing written is different in kind. It is
not a thing with no reason; it is a thing whose reason was not recorded, and
those read identically afterwards unless they are written differently. So it
is marked as such and logged, because it is a fault in whatever created it.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

#: Who decided this should exist.
BY_USER = "user"
BY_AGENT = "agent"
BY_HEARTBEAT = "heartbeat"
BY_SCHEDULER = "scheduler"
CREATORS = (BY_USER, BY_AGENT, BY_HEARTBEAT, BY_SCHEDULER)

#: What a user's own entry says when they wrote nothing. Not a placeholder -
#: it is the true and complete answer to why it exists.
USER_ADDED = "added by user"

#: What an agent-created thing says when its creator recorded nothing. Stated
#: rather than left blank, so that "nobody wrote one" cannot be mistaken for
#: "there was no reason to write".
NOT_RECORDED = "no reason recorded - this is a fault in whatever created it"


def reason_for(given: Optional[str], created_by: str = BY_AGENT,
               what: str = "") -> str:
    """The reason to store, given what the caller supplied and who is asking.

    ``what`` is used only in the warning, to name the thing whose reason went
    missing - a log line saying a reason was not recorded, without saying what
    for, is barely better than no line.
    """
    text = str(given or "").strip()
    if text:
        return text
    if str(created_by or "").strip().lower() == BY_USER:
        return USER_ADDED
    log.warning("no reason recorded for %s created by %s",
                what or "something", created_by or "the agent")
    return NOT_RECORDED


def creator_of(value: Any, default: str = BY_AGENT) -> str:
    name = str(value or "").strip().lower()
    return name if name in CREATORS else default


__all__ = ["reason_for", "creator_of", "USER_ADDED", "NOT_RECORDED",
           "BY_USER", "BY_AGENT", "BY_HEARTBEAT", "BY_SCHEDULER", "CREATORS"]

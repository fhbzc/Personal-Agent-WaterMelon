"""Sessions the agent started itself, so the listener does not record them.

The Claude Code provider answers by running the client on this machine, and
the client writes a transcript of every run into its own session store. That
store is exactly what the assistant listener reads. So each model call the
agent made came back to it a moment later as activity: the relabeller's "Add
search keywords in Chinese and English to some memories", the data
heartbeat's "Raw activity to interpret", the folder's "You are folding away
old records", all recorded as things that happened at this desk, attributed
to whatever window happened to be in front at the time.

It is the same loop ``agent_writes`` closes, arriving by another road: there
the agent heard its own files, here it hears its own voice. And it is worse
in one way - interpreting that activity takes a model call, whose transcript
is more activity.

**The identity has to outlive the process.** A registry in memory would be
enough if the reader and the writer always ran together, but the transcripts
stay on disk for thirty days and the listener re-reads them from the start
after a restart, when nothing in memory remembers which were ours. So the
mark is carried in the one thing that passes from the writer to the reader:
the session id itself, which the client lets us choose. An id that begins
with the sentinel below is one of ours, and says so wherever it is read,
by any process, at any later time.

The sentinel replaces the first eight hex digits of a random UUID, leaving
the remaining ninety-six bits random and the id well-formed. A user session
could collide with it once in four billion, and the consequence of that
would be one session of theirs going unrecorded.
"""

from __future__ import annotations

import os
import uuid

#: The first eight hex digits of every session id this application creates.
SESSION_MARK = "a9e77f01"


def new_session_id() -> str:
    """A fresh session id that says, in itself, whose session it is."""
    return SESSION_MARK + str(uuid.uuid4())[8:]


def is_agent_session(session_id: object) -> bool:
    """Whether this session is the agent talking to a model, not the user."""
    return str(session_id or "").lower().startswith(SESSION_MARK)


def is_agent_project(project: object, root: object) -> bool:
    """Whether a session was run inside the agent's own storage.

    The second way of telling, and the one that reads sessions recorded
    before there was a mark to read. The provider runs the client with the
    agent's own root as its working directory, so the client files those
    transcripts under that project - and nobody runs a coding assistant
    inside the agent's storage on purpose. Measured on this machine: 888 of
    the 951 assistant events in recent history were filed there, every one of
    them a model call the agent had made itself.

    It is a weaker statement than the mark, and it is the one that can be
    made about the past, so both are asked.
    """
    if not project or not root:
        return False
    here = os.path.normcase(os.path.abspath(str(project)))
    base = os.path.normcase(os.path.abspath(str(root)))
    return here == base or here.startswith(base + os.sep)


def is_the_agent_itself(session_id: object, project: object = None,
                        root: object = None) -> bool:
    """Whether this session is the agent's own, by either way of telling."""
    return is_agent_session(session_id) or is_agent_project(project, root)


__all__ = ["SESSION_MARK", "new_session_id", "is_agent_session",
           "is_agent_project", "is_the_agent_itself"]

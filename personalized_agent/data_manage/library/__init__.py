"""The agent's memory, as folders of files (section 39.1, as amended).

    data/
      user_nature/     who the user is
      user_feedback/   what they thought of what the agent did
      states/          what was true over a span
      state_tasks/     what a run of states adds up to

Each folder carries an ``intro.txt`` saying where its contents come from.
Nothing searches the folders as a whole: a record is read by its id, or a
folder listed, by whoever wrote it.

This replaces the knowledge graph, which is kept unused in ``deprecated/``.
"""

from .record import (AGENT_LESSON, FOLDERS, INDEX, OTHER, STATE_TASKS,
                     STATES,
                     USER_FEEDBACK, USER_NATURE, Record)
from .store import DataLibrary

__all__ = ["DataLibrary", "Record", "FOLDERS", "INDEX", "USER_NATURE",
           "USER_FEEDBACK", "STATES", "STATE_TASKS", "OTHER", "AGENT_LESSON"]

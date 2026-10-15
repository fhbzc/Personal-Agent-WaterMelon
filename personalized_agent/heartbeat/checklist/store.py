"""What the heartbeat does, every time (section 35).

Four steps, fixed. They cannot be added to, removed, reordered or edited -
not by the user and not by the agent - because each one produces something a
later step or another part of the application depends on, and the order is the
reasoning:

1. **Work out what is going on** and write it to the state calendar. Nothing
   later can be decided without it.
2. **Work out who the user is** and write it to the graph. This is the only
   thing the agent accumulates that makes it this user's agent.
3. **Decide what to do for them**, which is what schedules work.
4. **Judge the states that are running or have run out** - finished, worth
   extending, or no longer worth carrying.

The checklist used to be editable, by both the user and the agent. That was
the wrong shape for it: a heartbeat whose steps can be rewritten by the thing
running them can quietly stop doing the one thing it exists for, and there is
no sign of it having happened. What the agent may change is the *content* of
what it knows - the states, the graph - never the procedure that produces it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

#: The one step whose output format matters to the rest of the application:
#: everything downstream reads the state calendar.
STATE_INSTRUCTIONS = """Write what is going on as states on the calendar.

A review reads a few minutes of activity, so the period a state can be
given is the window in front of it. Something longer-lived is either the
same situation seen again, written for this window as this window saw it,
or a habit rather than a situation - and a habit belongs in the step after
this one.

This is the user's state, not yours. What you intend to do is not their
situation, and a calendar of your own intentions described back to them as
their state is a calendar about the wrong person.

A state is shown, never executed: the scheduler is what runs things.

What is already on the calendar is not shown to you. This window is the
evidence and the calendar is what earlier runs made of earlier windows;
reading those first is how a review confirms last hour's conclusions
instead of looking at this hour's activity.

Every state says where it came from: what the user told you, or what you
inferred from their activity."""


@dataclass(frozen=True)
class ChecklistItem:
    """One step of the heartbeat. Fixed, and therefore not a stored row."""

    id: str
    position: int
    text: str
    detail: str = ""
    enabled: bool = True
    origin: str = "system"

    @property
    def founding(self) -> bool:
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "position": self.position, "text": self.text,
                "detail": self.detail, "enabled": True, "origin": self.origin,
                "founding": True, "editable": False}


STEPS: List[ChecklistItem] = [
    ChecklistItem("step_state", 0,
                  "Analyze the user's current state.",
                  STATE_INSTRUCTIONS),
    # Step 2 was who the user is - paused, and its place given to the people
    # around them (heartbeat/connections/people.py): the user's decision.
    ChecklistItem("step_people", 1,
                  "Find the people in this activity: who they are to the user, "
                  "and what they are doing.",
                  "From the same activity as step one and the states it has "
                  "just written: each person who appears, matched to the "
                  "contacts already known, with what they are doing - dated, "
                  "resting on the activity - and the user's own changes to "
                  "their contacts followed. Written to the Connections panel."),
    # Who the user is - kept, and paused (heartbeat/core/heartbeat.py,
    # PAUSED_STEPS): learned from their lessons now. Never run while paused.
    ChecklistItem("step_nature", 2,
                  "Determine what kind of person the user is: their habits, "
                  "their working rhythm, their character, and what they find "
                  "difficult.",
                  "Habits, character, how they work, what they find difficult. "
                  "Written to the knowledge graph, where it keeps. This is what "
                  "makes you this user's agent rather than a general one."),
    ChecklistItem("step_notify", 3,
                  "Determine which notifications need to be added and which "
                  "need to be disabled.",
                  "Read from the same activity as step one, the states it has "
                  "just written, and every active notification from now on. "
                  "One added goes on the calendar and pops up when its time "
                  "comes; one disabled stays on the calendar, closed with the "
                  "reason, and never interrupts. Nothing already there is "
                  "added again."),
    ChecklistItem("step_todo", 4,
                  "Determine which next steps of the user's projects and tasks "
                  "are now done.",
                  "Read from the same activity and the states just written: each "
                  "open next step of a project, or of a task in no project, that "
                  "the user can be seen to have finished is ticked - by the agent, "
                  "with why. The user can take a tick back; the next state "
                  "heartbeat writes the list again from the ticks and the user's "
                  "own changes."),
]

#: Kept for the code that reads step text.
DEFAULT_ITEMS = [step.text for step in STEPS]


class ChecklistStore:
    """The steps of one review. Read only - nothing here writes."""

    def __init__(self, db: Any = None) -> None:
        self.db = db

    def items(self, enabled_only: bool = False,
              include_retired: bool = False) -> List[ChecklistItem]:
        return list(STEPS)

    def snapshot(self) -> List[ChecklistItem]:
        """Section 35 - the view one execution works from."""
        return list(STEPS)

    def retired(self) -> List[ChecklistItem]:
        return []


__all__ = ["ChecklistItem", "ChecklistStore", "STEPS", "DEFAULT_ITEMS",
           "STATE_INSTRUCTIONS"]

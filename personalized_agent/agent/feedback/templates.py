"""The shapes a question to the user may take (section 52.4, as amended).

Three templates, and nothing else. Every interruption the agent raises is one
of them, and every option it offers is listed here.

**Why a fixed set.** A question written freshly each time is a question whose
buttons differ every time: one says "OK", the next "Got it", a third "Sure",
and none of them says what happens afterwards. The user has to read each one
before they can answer it, which is the cost that makes people stop answering.
Worse, the agent had no vocabulary for the *consequence* - "not now" and "never
mind" looked alike, and the task was left in whatever state the code happened
to choose.

So an option is not a label. It is a label **and** what becomes of the task,
declared together and in one place:

    acknowledged  ->  the reminder is dismissed and the task settled (nothing
                      is checked afterwards: only notifications run)
    delay         ->  the task comes back at a time the agent chose
    stop_asking   ->  (permission only) the task is over
    allow         ->  the task runs now
    deny          ->  the task is over
    ok            ->  the task is over; it was a notice, not a question

Note that ``deny`` and ``stop_asking`` both finish the task rather than
cancelling it. The work will not happen, and that is a settled outcome and not
an error: a calendar showing a row of cancellations reads as a list of things
that went wrong, when what happened is that the user decided.

Every answer, whichever template it came from, becomes a ``user_feedback`` node
in the graph. That is the point of asking - a judgement the user makes is only
worth the interruption if the agent can meet it again later.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

#: What an answer does to the task it was asked about.
#:
#: These are the three endings a run can have (section 59) plus "run it now",
#: because an answer is a release rather than an ending - the task goes back to
#: the scheduler to finish the way any run finishes.
TO_CHECK = "to_check"        # settled later, by a check the task schedules
DELAY = "delay"              # back on the calendar at the time the option names
COMPLETE = "complete"        # over; nothing further will happen
RUN = "run"                  # released to run now

#: A reminder: the agent is telling the user to do something.
REMINDER = "reminder"
#: A request for permission: the agent is asking whether it may do something.
PERMISSION = "permission"
#: Something said, with nothing asked: the agent is telling the user a thing
#: and wants no decision from them. One option, and it closes the task.
PASSIVE = "passive"

#: What these were called before the templates existed.
#:
#: Rows already in the database carry the old word, and a question whose kind
#: resolves to no template offers no buttons - it would sit in the list
#: forever, unanswerable, with the task behind it parked. Mapping the old names
#: costs two lines and is the difference between an upgrade and a stuck queue.
LEGACY_KINDS = {"approval": PERMISSION, "review": REMINDER}


def template_of(kind: str) -> str:
    """The template a question belongs to, old name or new."""
    return LEGACY_KINDS.get(str(kind or ""), str(kind or ""))


def kind_aliases(kind: str) -> Tuple[str, ...]:
    """Every name this template has gone by, current one first.

    Needed because a stored ``kind`` is matched in SQL, where no amount of
    resolving in Python helps: a row written as "approval" is not found by a
    query for "permission", and a caller still passing the old constant is not
    found by a query for the new one. Matching all the names covers both
    directions.
    """
    template = template_of(kind)
    legacy = tuple(old for old, new in LEGACY_KINDS.items() if new == template)
    return (template,) + legacy


class Option:
    """One button, and what it does.

    ``delay_seconds`` is filled in by the agent when it raises the question,
    from how urgent the task is - "remind me in ten minutes" and "remind me
    next week" are the same option with different numbers, and which one to
    offer is a judgement about this task, not a constant.
    """

    __slots__ = ("key", "label", "effect", "meaning", "delay_seconds")

    def __init__(self, key: str, label: str, effect: str, meaning: str,
                 delay_seconds: Optional[int] = None) -> None:
        self.key = key
        self.label = label
        self.effect = effect
        #: How this answer reads back to the agent months later, in the graph.
        self.meaning = meaning
        self.delay_seconds = delay_seconds

    def to_dict(self) -> Dict[str, object]:
        out: Dict[str, object] = {"key": self.key, "label": self.label,
                                  "effect": self.effect}
        if self.delay_seconds is not None:
            out["delay_seconds"] = self.delay_seconds
        return out


#: EACH BUTTON SAYS WHAT HAPPENS: how long until it comes back, and whether
#: it ever does. Every notification can be put on the to-do list - no more
#: notifications about it, and it is listed in To do on the Overview.
GOT_IT = "Got it (no more notifications)"
#: Dismissed, and a lesson: fewer like it from now on.
BAD_LABEL = "Bad notification (no more notifications + fewer similar ones in future)"
TO_TODO = "to_todo"
TO_TODO_LABEL = "Add to to-do (no more notifications; listed in To do on the Overview)"
TO_TODO_MEANING = ("the user put it on their to-do list: no more notifications about it, "
                   "and they will do it in their own time")
#: When the agent chose no interval: the scheduler's own default, said.
DEFAULT_DELAY_SECONDS = 900

#: The templates. The label is what the user reads; the effect is what happens.
TEMPLATES: Dict[str, Tuple[Option, ...]] = {
    REMINDER: (
        Option("acknowledged", GOT_IT, TO_CHECK,
               "the user said they would do it"),
        # The interval is the agent's to choose; the label is rewritten to
        # match, and says the default when it chose none.
        Option("delay", "Remind me later", DELAY,
               "the user wanted reminding later"),
        Option(TO_TODO, TO_TODO_LABEL, TO_CHECK, TO_TODO_MEANING),
        # No "Stop reminding me": "Got it" dismisses a reminder, and one the
        # user does not want again is disabled or deleted on the calendar.
    ),
    PERMISSION: (
        Option("allow", "Allow", RUN,
               "the user allowed this"),
        Option("deny", "Don't", COMPLETE,
               "the user did not want this done"),
        Option("delay", "Ask me later", DELAY,
               "the user wanted to be asked again later"),
        Option("stop_asking", "Stop asking about this", COMPLETE,
               "the user did not want to be asked about this again"),
        Option(TO_TODO, TO_TODO_LABEL, COMPLETE, TO_TODO_MEANING),
    ),
    # Nothing is being asked, so there is nothing to weigh - but there is
    # still something to dismiss. Without this template, a note the agent
    # simply wanted to say had to borrow a reminder's buttons and ask "shall
    # I remind you again?" about a sentence that needed no answer, or carry
    # no buttons at all and sit in the list for ever.
    PASSIVE: (
        Option("ok", "OK (no more notifications)", COMPLETE,
               "the user saw it"),
        Option(TO_TODO, TO_TODO_LABEL, COMPLETE, TO_TODO_MEANING),
    ),
}


def options_for(template: str, delay_seconds: Optional[int] = None) -> List[Option]:
    """The options of one template, with the delay filled in.

    The delay option is the only one that varies, and it varies per task: a
    build worth chasing in ten minutes and a chore worth raising next week are
    the same button with different numbers behind it.
    """
    out: List[Option] = []
    for option in TEMPLATES.get(template_of(template), ()):
        if option.effect == DELAY:
            # How long, always said: the agent's interval, or the default.
            seconds = int(delay_seconds or DEFAULT_DELAY_SECONDS)
            out.append(Option(option.key, _delay_label(seconds), DELAY,
                              option.meaning, seconds))
        else:
            out.append(option)
    return out


def effect_of(template: str, answer: str) -> Optional[str]:
    for option in TEMPLATES.get(template_of(template), ()):
        if option.key == answer:
            return option.effect
    return None


def meaning_of(template: str, answer: str) -> str:
    """How an answer reads back to the agent later, in words rather than a key."""
    for option in TEMPLATES.get(template_of(template), ()):
        if option.key == answer:
            return option.meaning
    return answer


def answers_for(template: str) -> Tuple[str, ...]:
    return tuple(option.key for option in TEMPLATES.get(template_of(template), ()))


def _delay_label(seconds: int) -> str:
    """"Remind me in 10 minutes" - said in whatever unit reads naturally."""
    if seconds < 90 * 60:
        minutes = max(1, round(seconds / 60))
        return f"Remind me in {minutes} minute{'s' if minutes != 1 else ''}"
    if seconds < 36 * 3600:
        hours = round(seconds / 3600)
        return f"Remind me in {hours} hour{'s' if hours != 1 else ''}"
    days = round(seconds / 86400)
    return f"Remind me in {days} day{'s' if days != 1 else ''}"


#: Every template there is. A notification declares one of these.
ALL_TEMPLATES = (REMINDER, PERMISSION, PASSIVE)

__all__ = ["REMINDER", "PERMISSION", "PASSIVE", "ALL_TEMPLATES", "TEMPLATES", "Option",
           "TO_CHECK", "DELAY", "COMPLETE", "RUN",
           "options_for", "effect_of", "meaning_of", "answers_for", "GOT_IT", "BAD_LABEL", "TO_TODO",
           "TO_TODO_LABEL", "DEFAULT_DELAY_SECONDS", "delay_label"]


def delay_label(seconds: int) -> str:
    """"Remind me in 30 minutes" - for a button made outside a template."""
    return _delay_label(seconds)

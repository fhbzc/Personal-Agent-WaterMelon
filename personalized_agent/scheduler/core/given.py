"""A time a MODEL gives: one way in, wherever a model sets one.

The chat's `schedule_task` and the heartbeat's notification step both put
things on the calendar, and both used to be different: the chat wrote a UTC
moment it worked out itself, the heartbeat a time as a clock shows it. A
model that works out an offset stamps today's on a date after the clocks
change, an hour wrong - so neither does now. Both give:

  - "at_local": the date and time as a clock shows it (YYYY-MM-DD HH:MM);
  - "zone": whose clock - "user" for the user's own, or a place's name
    held to zones.PLACE_RULE (Region/City, or UTC);
  - "as_written": the time as the source wrote it, if a source did;

and the moment is worked out here from the zone's rules for that date.

ONE RULE FOR A ZONE, ONE WAY ROUND A REFUSAL. A zone refused is sent back
ONCE, said why and what is accepted - by the heartbeat as a question to the
model, by the chat as the tool's error. Refused again, or not named, the
time is taken as the user's own, and the card says so ("zone_unread"):
nothing is dropped for its zone.

What is kept (`note`) is the same from both, and the calendar card shows it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from . import zones

#: The opening of a refusal of the zone - the chat's cue that it was asked once.
ZONE_REFUSED = "the zone was refused"

#: How a time is to be given - said in the chat's tool and the heartbeat's
#: step alike.
HOW_TO_GIVE = """GIVE A TIME AS A CLOCK SHOWS IT, AND WHOSE CLOCK - never work out an
offset or UTC yourself: "at_local" is the date and time on that clock
(YYYY-MM-DD HH:MM) and "zone" whose clock it is. The offset for that date,
daylight saving included, is worked out for you.
  - A time you choose, or one the user gives in their own time, is on the
    user's clock: "zone": "user".
  - A time given IN ANOTHER PLACE'S TIME is kept on that clock: "15:00 CET"
    or "3pm Paris time" is "zone": "Europe/Paris"; ten minutes before it is
    14:50 on the same clock. """ + zones.PLACE_RULE[0].upper() + zones.PLACE_RULE[1:] + """
    Beijing time is Asia/Shanghai; "3pm EST" in a New York email is
    America/New_York - EST alone is UTC-05:00 all year, not New York's
    summer time. A zone refused is sent back to you once, saying why.
  - When a source gives NO zone but the time is clearly the sender's - their
    signature, their organisation or the event is somewhere else - take
    their place's zone, and say so in your reason. When nothing says it is
    elsewhere, it is the user's.
  - "as_written": the time exactly as the source wrote it ("Thu 15:00 CET",
    "3pm their time"), so the user can see what it was taken from; empty for
    a time you chose."""


def now_line() -> str:
    """The time now, as the user's clock shows it, and whose clock."""
    zone = zones.here()
    now = datetime.now(timezone.utc)
    wall = zones.wall_of(now, zone)
    return (f"Now: {wall.strftime('%A %d %B %Y, %H:%M')} on the user's clock, "
            f"{zones.said(zone, now)}.")


def refusal(said: str, reason: str) -> str:
    """What a model is told of a zone refused: why, and what is accepted."""
    return f'{ZONE_REFUSED}: {reason}. What is accepted: {zones.PLACE_RULE}'


def time_given(entry: Dict[str, Any],
               again: Optional[Callable[[str, str], str]] = None
               ) -> Tuple[Optional[datetime], Dict[str, Any], str]:
    """The moment a time a model gave is for, what it was taken from, or
    why it could not be read: (moment, note, error).

    A zone refused under zones.PLACE_RULE: with no `again`, the error says
    why and what is accepted (`refusal`) - the caller sends it back. With
    `again(written, reason)`, that is the asking once more: what it names is
    held to the same rule, and if it is refused, empty or raises, the time
    is the user's own and the note says so. An "at" with its own offset - a
    time kept before - is read as it is; one with none is on the zone's
    clock, not UTC.
    """
    written = entry.get("zone")
    unread = named_again = ""
    try:
        zones.place_zone(written)
        zone = written
    except ValueError as exc:
        said = str(written).strip()[:60]
        if again is None:
            return None, {}, refusal(said, str(exc))
        try:
            zone = str(again(said, str(exc)) or "").strip()
            zone = zones.place_zone(zone) if zone and not zones.is_own(zone) else ""
        except Exception:                 # no answer, or still refused
            zone = ""
        if zone:
            named_again = said
        else:
            zone, unread = "user", said
    kept = zones.zone_name(zone)
    text = str(entry.get("at_local") or "").strip()
    how = zones.EXACT
    if text:
        try:
            wall = datetime.fromisoformat(text.replace("T", " ").strip()[:25])
        except ValueError:
            return None, {}, "at_local must be a date and time, YYYY-MM-DD HH:MM"
    else:
        from ...common.events.models import parse_iso
        wall = parse_iso(entry.get("at") or entry.get("scheduled_at"))
        if wall is None:
            return None, {}, "a time is needed: at_local, YYYY-MM-DD HH:MM"
    if wall.tzinfo is not None:
        when = wall.astimezone(timezone.utc)
        wall = zones.wall_of(when, kept)
    else:
        when, how = zones.moment_of(wall, kept)
    note: Dict[str, Any] = {"wall": wall.strftime("%Y-%m-%d %H:%M"), "zone": kept,
                            "zone_said": zones.said(kept, when),
                            # The user's own time: the zone this computer is
                            # in now, kept by name - the calendar shows it
                            # converted wherever they are later.
                            "own": zones.is_own(zone)}
    if how != zones.EXACT:
        note["clock"] = how
    if named_again:
        note["zone_written"] = named_again
    if unread:
        note["zone_unread"] = unread
    as_written = str(entry.get("as_written") or "").strip()[:120]
    if as_written:
        note["as_written"] = as_written
    return when, note, ""

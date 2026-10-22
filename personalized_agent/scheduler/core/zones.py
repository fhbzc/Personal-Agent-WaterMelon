"""A time as a clock on a wall shows it, made into one moment - and back.

A notification is about a moment, and the moment is what is stored (UTC);
the calendar shows it in the zone the computer is in NOW, whatever that is.
But what the user, an email or a meeting invitation says is a WALL TIME in
some place: "Thursday 15:00 Paris time", "every Monday at 10". Turning one
into the other is arithmetic with an offset, and the offset is not a
constant: it changes with daylight saving, on dates that differ between
places (Europe changes on the last Sunday of October, the United States a
week later). An offset taken today and stamped on a date after the change
is an hour wrong; so is a weekly meeting stepped by 7 x 24 hours across it.

So the zone is kept by NAME and the offset worked out for the date in
question, here, by code - never by the model:

  - THE USER'S OWN TIME is the zone this computer is in WHEN THE TIME IS
    MADE, kept by its name (`here`): a reminder made in Lisbon for 9:00 is
    9:00 Lisbon time, and the calendar - always in the zone the computer is
    in now - shows it at 4:00 when they are in New York. It does not follow
    them: the moment was meant where it was made.
  - any other is an IANA name ("Europe/Paris"), read from the tz database
    (`tzdata` where the system has none - Windows).
  - "local" - this computer's zone, whatever it is now - is only what a
    time kept before zones were named is read on, and the last resort when
    no name can be found for this computer's.

Two wall times are not one moment each, around a change:

  - one that DOES NOT EXIST (spring: 02:30 when the clock jumps from 02:00
    to 03:00) is moved on by the jump - 03:30, as long after 02:00 as was
    meant;
  - one that HAPPENS TWICE (autumn: 01:30 comes round again) is taken the
    first time - a reminder early is better than one late.

Each is said, so the reason under a notification can say it too.
"""

from __future__ import annotations

import os
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

#: This computer's zone as it is now - not a name: see the module's note.
LOCAL = "local"

#: What happened to a wall time on its way to a moment.
EXACT, SKIPPED, TWICE = "exact", "skipped", "twice"

#: The words a caller uses for "the user's own time".
OWN = ("", "user", "the user's", "mine", "own")

#: The zone the page last said it is in (the browser knows it by name), and
#: the name found for this computer's, kept while its offsets hold.
_seen: Dict[str, Any] = {}


def is_own(name: Any) -> bool:
    """Whether a zone said is the user's own time, not a place's."""
    return str(name or "").strip().lower() in OWN


def seen(name: Any) -> None:
    """The zone the page is in, by the name the browser gives it - the
    surest name for this computer's, used while its offsets are this
    computer's (a page left open across a flight is not believed)."""
    text = str(name or "").strip()
    if not text or text == _seen.get("page") or is_own(text) or text.lower() == LOCAL:
        return
    try:
        zone_of(text)
    except ValueError:
        return
    _seen["page"] = text


def here() -> str:
    """The name of the zone this computer is in now: the page's, when its
    offsets are this computer's; the system's own name where it gives one
    (TZ, /etc/localtime); else a zone with this computer's offsets across
    two years - so its daylight saving is this computer's even if its name
    is a neighbour's. "local" when none is found."""
    marks = _local_marks()
    page = _seen.get("page")
    if page and _marks_of(page) == marks:
        return page
    if _seen.get("found_for") == marks:
        return _seen["found"]
    found = next((name for name in _system_names() if _marks_of(name) == marks), "")
    if not found and set(marks) == {0}:
        found = "UTC"                           # no offset, ever: by its own name
    if not found:
        found = _matching(marks)
    _seen.update(found=found or LOCAL, found_for=marks)
    return _seen["found"]


def zone_of(name: Any) -> Optional[Any]:
    """The zone a name stands for: None for this computer's own as it is now
    ("local"), a ZoneInfo for an IANA name - the user's own time being the
    zone `here` names. Raises ValueError for a name that is not a zone: a
    time in a zone nobody can read is not guessed."""
    text = str(name or "").strip()
    if is_own(text):
        text = here()
    if text.lower() == LOCAL:
        return None
    if text.upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(text)
    except Exception:
        raise ValueError(f"{text!r} is not a time zone this computer knows "
                         "(an IANA name such as Europe/Paris)") from None


#: What a zone the model writes must be - said to it when one is sent back.
PLACE_RULE = ('a zone is accepted only as an IANA name of the form Region/City '
              '(Europe/Paris, America/New_York, America/Los_Angeles, '
              'Asia/Shanghai), as "UTC", or as "user" for the user\'s own time. '
              'Refused: a name with no slash (PST, EST, CET, PRC, Japan), a name '
              'starting with Etc/, an offset (UTC+2) and a city alone (Paris).')


def place_zone(name: Any) -> str:
    """A zone the MODEL wrote, held to `PLACE_RULE`: the name to keep, or
    ValueError saying why it was refused.

    Stricter than `zone_name`. The database also knows old short names,
    and some of them are not what a person writing them means: "EST" is
    UTC-05:00 all year, so "3pm EST" in July - meaning New York's time,
    UTC-04:00 then - would land an hour off and be told nobody; "Etc/GMT-8"
    is UTC+08:00, its sign the reverse of what it reads as. A place's name
    carries that place's clock changes. This computer's own zone (`here`)
    is not held to it: it is the system's, not a guess.
    """
    text = str(name or "").strip()
    if is_own(text):
        return zone_name(text)
    if text.upper() == "UTC":
        return "UTC"
    if text.lower() == LOCAL:
        raise ValueError(f'"{text}" is not a place - for the user\'s own time '
                         'write "user"')
    if text.startswith("Etc/"):
        raise ValueError(f'"{text}" is a fixed offset, not a place, and its sign '
                         'reads backwards (Etc/GMT-8 is UTC+08:00)')
    known = True
    try:
        zone_of(text)
    except ValueError:
        known = False
    if "/" not in text:
        if known:
            # Said of this name, not of another: one fixed all year may not
            # be the clock of the place meant in summer or in winter.
            year = datetime.now(timezone.utc).year
            offsets = {datetime(year, month, 1, 12, tzinfo=zone_of(text)).utcoffset()
                       for month in (1, 7)}
            fixed = ""
            if len(offsets) == 1:
                total = int(offsets.pop().total_seconds())
                hours, rest = divmod(abs(total), 3600)
                fixed = (f': it is UTC{"-" if total < 0 else "+"}{hours:02d}:'
                         f'{rest // 60:02d} all year, with no clock changes, '
                         'which may not be the clock of the place meant')
            raise ValueError(f'"{text}" is an old short name, not a place{fixed} '
                             '- give the Region/City name of the place meant')
        raise ValueError(f'"{text}" is not a zone name - an abbreviation, an '
                         'offset or a city alone is not read')
    if not known:
        raise ValueError(f'"{text}" is not a zone the time-zone database knows')
    return zone_name(text)


def zone_name(name: Any) -> str:
    """The name to keep: the IANA name as the database spells it - for the
    user's own time, the name of where this computer is now - or "local"
    when no name can be found for it."""
    zone = zone_of(name)
    if zone is None:
        return LOCAL
    return "UTC" if zone is timezone.utc else str(getattr(zone, "key", name))


def wall_of(moment: datetime, name: Any = LOCAL) -> datetime:
    """What a clock in that zone showed at this moment, without its zone."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    zone = zone_of(name)
    there = moment.astimezone(zone) if zone is not None else moment.astimezone()
    return there.replace(tzinfo=None)


def moment_of(wall: datetime, name: Any = LOCAL) -> Tuple[datetime, str]:
    """The moment a wall time in that zone stands for, in UTC, and whether it
    was exact, skipped by a clock going forward, or one of two."""
    wall = wall.replace(tzinfo=None, fold=0)
    zone = zone_of(name)
    # Every offset the zone has near that date: the one a day before and the
    # one a day after cover any change in between.
    offsets = []
    for side in (-1, 1):
        offset = _aware(wall + timedelta(days=side), zone).utcoffset() or timedelta(0)
        if offset not in offsets:
            offsets.append(offset)
    found = sorted({(wall - offset).replace(tzinfo=timezone.utc) for offset in offsets
                    if wall_of((wall - offset).replace(tzinfo=timezone.utc), name) == wall})
    if len(found) == 1:
        return found[0], EXACT
    if found:
        return found[0], TWICE                  # the first of the two
    # It does not exist: read with the offset from before the change, which
    # lands it as far past the jump as it was meant to be past its start.
    before = _aware(wall - timedelta(days=1), zone).utcoffset() or timedelta(0)
    return (wall - before).replace(tzinfo=timezone.utc), SKIPPED


def step_days(moment: datetime, days: int, name: Any = LOCAL) -> datetime:
    """The same time of day, `days` later (or earlier) on that zone's clock."""
    wall = wall_of(moment, name) + timedelta(days=days)
    return moment_of(wall, name)[0]


def said(name: Any, moment: Optional[datetime] = None) -> str:
    """A zone for a person: "Europe/Paris (CET, UTC+01:00)"."""
    moment = moment or datetime.now(timezone.utc)
    zone = zone_of(name)
    there = moment.astimezone(zone) if zone is not None else moment.astimezone()
    shift = there.utcoffset() or timedelta(0)
    total = int(shift.total_seconds())
    sign = "-" if total < 0 else "+"
    hours, rest = divmod(abs(total), 3600)
    offset = f"UTC{sign}{hours:02d}:{rest // 60:02d}"
    if zone is None:
        return f"this computer's time ({there.tzname() or 'local'}, {offset})"
    abbreviation = there.tzname() or ""
    return f"{zone_name(name)} ({abbreviation}, {offset})" if abbreviation \
        else f"{zone_name(name)} ({offset})"


def _aware(wall: datetime, zone: Optional[Any]) -> datetime:
    """A wall time made aware, roughly - only its offset is used."""
    if zone is not None:
        return wall.replace(tzinfo=zone)
    return wall.astimezone()                    # naive: this computer's rules


def _instants() -> List[datetime]:
    """Two years of moments, a fortnight apart, around now: zones whose
    offsets agree at all of them keep the same daylight saving."""
    start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return [start + timedelta(days=15 * step) for step in range(-24, 25)]


def _local_marks() -> Tuple[int, ...]:
    return tuple(int((moment.astimezone().utcoffset() or timedelta(0)).total_seconds())
                 for moment in _instants())


def _marks_of(name: str) -> Optional[Tuple[int, ...]]:
    try:
        from zoneinfo import ZoneInfo

        zone = timezone.utc if name.upper() in ("UTC", "GMT") else ZoneInfo(name)
    except Exception:
        return None
    return tuple(int((moment.astimezone(zone).utcoffset() or timedelta(0)).total_seconds())
                 for moment in _instants())


def _system_names() -> List[str]:
    """What the system itself calls its zone, where it says (macOS, Linux)."""
    names = [os.environ.get("TZ", "").lstrip(":")]
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            names.append(target.split("zoneinfo/", 1)[1])
    except OSError:                          # pragma: no cover - no such file
        pass
    return [name for name in names if name and "/" in name]


def _matching(marks: Tuple[int, ...]) -> str:
    """A zone with these offsets: of those that have them, the one whose
    abbreviations are this computer's (Windows says "Central Daylight Time"
    where the database says CDT), else the first by name."""
    try:
        from zoneinfo import ZoneInfo, available_timezones

        names = sorted(name for name in available_timezones()
                       if "/" in name and not name.startswith(("Etc/", "SystemV/")))
    except Exception:                        # pragma: no cover - no database
        return ""
    initials = set()
    for one in _time.tzname:
        words = str(one).split()
        initials.add("".join(word[0] for word in words).upper() if len(words) > 1
                     else str(one).upper())
    alike = [name for name in names if _marks_of(name) == marks]
    now = datetime.now(timezone.utc)
    for name in alike:
        called = {(now + timedelta(days=days)).astimezone(ZoneInfo(name)).tzname()
                  for days in (0, 182)}
        if called & initials:
            return name
    return alike[0] if alike else ""


__all__ = ["LOCAL", "EXACT", "SKIPPED", "TWICE", "OWN", "is_own", "seen", "here",
           "zone_of", "zone_name", "wall_of", "moment_of", "step_days", "said"]

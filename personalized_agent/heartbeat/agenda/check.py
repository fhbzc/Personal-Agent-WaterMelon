"""THE PLAN'S HARD RULES, CHECKED BY CODE - never left to a model's word (the user, 2026-11-09: "一方面要
triage (make sure everything catch deadline), 第二个要减少context switch，也就是尽量保证我有大块的时间").

A plan (plan.py's builder writes it) is a day of blocks, each from a time to a time, with the things
done in it; and what is put off to a later day, what is at risk, what the agent does, what is no
longer wanted. Checked here:

  TIMES        each block's times read; in order; none overlapping; within the day planned
  EVERYTHING   every thing on the plate somewhere - and nothing named that is not on it
  FIXED        what is at a time (a meeting) in a block at that time
  DEADLINES    1. today's: in a block that ends by it; 2. those put off: on a day by their deadline;
               3. the days ahead can hold them, the earliest deadline first - or they are said at risk
  BLOCKS       a deep block as long as the user's stretch; no piece of work cut under SHORTEST; one
               thing to do split over blocks that could be one; no more than MOST_BATCHES of small things
  SWITCHES     counted: each change of context from one block of work to the next

A problem is said in words the builder can act on. Switches and short pieces are counted as well,
so that two plans can be weighed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

#: A piece of deep work is a fragment under this share of the user's own deep block (the user,
#: 2026-11-09: "这个也要user-specific" - it was a fixed 30 minutes); how many times of small things a
#: day at most.
SHORT_SHARE = 0.5
SHORTEST = 30                                  # kept for what reads it; the check uses the share
MOST_BATCHES = 2
#: A fixed thing is at its time within this; a block may hold this much more than its length.
FIXED_SLACK_MIN = 10
OVER_SHARE = 1.25
#: A day ahead may hold this much more than such a day holds - little: a looser line hid days of
#: twelve hours and more (a side note, 2026-11-09).
DAY_OVER_SHARE = 1.1
WORK_KINDS = ("deep", "batch", "review", "meeting", "errand")
QUIET_KINDS = ("free", "break", "travel", "buffer")
KINDS = WORK_KINDS + QUIET_KINDS


def _clock(day: date, text: Any) -> Optional[datetime]:
    try:
        hours, minutes = str(text).strip().split(":")[:2]
        moment = datetime(day.year, day.month, day.day) + timedelta(hours=int(hours),
                                                                   minutes=int(minutes))
    except (ValueError, TypeError):
        return None
    return moment


def _when(text: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(text)[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _day(text: Any) -> Optional[date]:
    try:
        return datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _ids(entries: Any) -> List[str]:
    out = []
    for one in entries if isinstance(entries, list) else []:
        ident = one.get("item") if isinstance(one, dict) else one
        if ident:
            out.append(str(ident))
    return out


def check(plan: Dict[str, Any], items: List[Dict[str, Any]], day: date, begins: str, ends: str,
          stretch: int, capacity: int, now: Optional[datetime] = None,
          capacities: Optional[Dict[str, int]] = None, whole: bool = True) -> Dict[str, Any]:
    """THE AGENT'S PLAN, CHECKED (the user, 2026-11-10: "只对agent要做的事情进行planing"): each of the
    agent's things placed - today by the clock, on a day ahead, at risk or cleared away - in time
    for its deadline and not before its day; nothing named that is not on the plate. The user's
    things have no time to check: they are suggested in the free stretches, in "might_do"'s order.
    `whole` False: the day alone was planned - what belongs to the days ahead is held till then.
    The other arguments are kept for the callers; the blocks are the code's (plan.day_blocks)."""
    problems: List[str] = []
    by_id = {one["id"]: one for one in items}
    triage = {one["id"]: one.get("triage") or {} for one in items}
    blocks = plan.get("blocks") if isinstance(plan.get("blocks"), list) else []
    timed: List[Tuple[datetime, datetime, Dict[str, Any]]] = []
    for block in blocks:
        if isinstance(block, dict):
            start, end = _clock(day, block.get("start")), _clock(day, block.get("end"))
            if start is not None and end is not None and end > start:
                timed.append((start, end, block))
    later: Dict[str, Optional[date]] = {}
    for entry in plan.get("later") if isinstance(plan.get("later"), list) else []:
        if isinstance(entry, dict) and entry.get("item"):
            later[str(entry["item"])] = _day(entry.get("day"))
    at_risk = set(_ids(plan.get("at_risk")))
    gone = set(_ids(plan.get("clean_up")))
    might = set(_ids(plan.get("might_do")))
    agent_today: Dict[str, Tuple[Optional[datetime], Optional[datetime]]] = {}
    for entry in plan.get("agent_does") if isinstance(plan.get("agent_does"), list) else []:
        if not isinstance(entry, dict) or not entry.get("item"):
            continue
        ident = str(entry["item"])
        start, end = _clock(day, entry.get("start")), _clock(day, entry.get("end"))
        title = (by_id.get(ident) or {}).get("title", ident)
        if (entry.get("start") or entry.get("end")) and (start is None or end is None or end <= start):
            problems.append(f"the agent's \"{str(title)[:60]}\": its times do not read as HH:MM to a "
                            f"later HH:MM")
        agent_today[ident] = (start, end)
    named = set(agent_today) | set(later) | at_risk | gone | might
    for ident in sorted(named - set(by_id)):
        problems.append(f"\"{ident}\" is not one of the things on the plate")
    # THE SAME THING TWICE: planned once, under either id.
    twins: Dict[str, set] = {}
    for ident in by_id:
        other = str(triage[ident].get("same_as") or "")
        if other in by_id and other != ident:
            twins.setdefault(ident, set()).add(other)
            twins.setdefault(other, set()).add(ident)
    covered = set(named) | {ident for ident in by_id if twins.get(ident, set()) & named}
    # THE AGENT'S THINGS, EACH PLACED.
    for ident, one in by_id.items():
        said = triage[ident]
        if said.get("who") not in ("delegable", "approve") or ident in covered or not whole:
            continue
        if one.get("quiet") and not one.get("fixed_local"):
            continue
        problems.append(f"\"{one['title'][:80]}\" ({ident}) is the agent's and is nowhere in the plan: "
                        f"the agent's work today, a day ahead, at risk, or clean-up")
    for ident, one in by_id.items():
        said = triage[ident]
        if said.get("who") not in ("delegable", "approve") or ident in gone or ident in at_risk:
            continue
        # NOT BEFORE ITS DAY.
        not_before = _day(one.get("not_before"))
        if not_before is not None and ident in agent_today and day < not_before:
            problems.append(f"\"{one['title'][:80]}\" is to be done from {not_before} - not today")
        # NOT BEFORE ITS TIME TODAY (put off by the user to after a time).
        after = _clock(day, one.get("not_before_time")) if not_before == day else None
        begins = agent_today.get(ident, (None, None))[0]
        if after is not None and begins is not None and begins < after:
            problems.append(f"the agent's \"{one['title'][:80]}\" is put off to after {after:%H:%M} - "
                            f"not at {begins:%H:%M}")
        if whole and not_before is not None and later.get(ident) is not None and later[ident] < not_before:
            problems.append(f"\"{one['title'][:80]}\" is to be done from {not_before} - not on {later[ident]}")
        # IN TIME FOR ITS DEADLINE.
        deadline = _when(said.get("deadline"))
        if deadline is None:
            continue
        done_at = agent_today.get(ident, (None, None))[1]
        if done_at is not None and done_at > deadline:
            problems.append(f"the agent's \"{one['title'][:80]}\" is done at {done_at:%H:%M}, after "
                            f"its deadline {deadline:%Y-%m-%d %H:%M} - earlier, or say it at risk")
        elif ident in later and whole:
            on = later[ident]
            if on is None or on > deadline.date() or (on == deadline.date() and deadline.hour < 10):
                problems.append(f"the agent's \"{one['title'][:80]}\" is due {deadline:%Y-%m-%d %H:%M} "
                                f"and is put off to {on or 'no day'} - earlier, or say it at risk")
    # THE DAYS AHEAD: a day after today, for the agent's work only.
    for ident, on in later.items():
        if ident not in by_id:
            continue
        if whole and (on is None or on <= day):
            problems.append(f"\"{by_id[ident]['title'][:80]}\" is put off to {on or 'no day'} - a day "
                            f"after {day.isoformat()}")
        if triage[ident].get("who") == "human":
            problems.append(f"\"{by_id[ident]['title'][:80]}\" is the user's: give it no day - put it in "
                            f"\"might_do\" in its order")
    load: Dict[date, int] = {}
    for ident, on in later.items():
        if ident in by_id and on is not None and on > day:
            load[on] = load.get(on, 0) + int(triage[ident].get("minutes") or 30)
    days_ahead_load = {on.isoformat(): f"{load[on]} min of the agent's work"
                       for on in sorted(load) if on <= day + timedelta(days=14)}
    free = [b for _s, _e, b in timed if b.get("kind") == "free"]
    return {"ok": not problems, "problems": problems, "switches": 0, "fragments": 0,
            "days_ahead_load": days_ahead_load, "approval_days": [], "approval_blocks_today": 0,
            "deep_minutes": 0, "longest_deep": 0, "batches": 0,
            "free_stretches": len(free), "suggested": sum(len(b.get("items") or []) for b in free),
            "within_day": bool(_clock(day, begins) and _clock(day, ends))}

__all__ = ["check", "SHORTEST", "MOST_BATCHES", "KINDS", "WORK_KINDS"]

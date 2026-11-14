"""THE PLAN ADJUSTED, NOT MADE AGAIN (the user, 2026-11-09: "你每次重排的时候是不是可以复用之前拍的内容 ...
你想想算法怎么弄"; "注意builder会拿之前排好的，根据状态进行adjust") - code, no model.

Every thing has a day (plan.py) - and a plan of eighty things written out whole each time is long
(a side note: one call's answer cut off loses the plan) and moves what need not move. So:

  1. THE BASE: the latest plan - each day ahead with its things in order, and the day planned's
     blocks so far.
  2. WHAT IS TO BE PLACED (`to_place`), worked out here: new since the base; sorted again since it
     (a deadline, a length, a kind changed); slipped - on a day gone by, or in a block of an
     earlier day, and still open. What is done or no longer wanted is gone from the plan: room.
  3. THE BUILDER ADJUSTS: the day's blocks from now; and in "later" only the days whose list
     changes, each whole, in order - a day it leaves out stays as it was.
  4. MERGED HERE into the whole plan - which the check (check.py) then holds to every rule, as if
     it were made whole.

"later" is written by day - {"YYYY-MM-DD": ["agenda_..", ...]} - an id each, no words: a whole plan
fits in an answer too.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional


def _day(text: Any) -> Optional[date]:
    try:
        return datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def by_day(plan: Dict[str, Any]) -> Dict[str, List[str]]:
    """A plan's days ahead, each with its things in order - from either way it was written."""
    out: Dict[str, List[str]] = {}
    later = plan.get("later")
    if isinstance(later, dict):
        for day, ids in later.items():
            if _day(day) is not None and isinstance(ids, list):
                out[str(day)[:10]] = [str(one) for one in ids if one]
        return out
    entries = [one for one in later or [] if isinstance(one, dict) and one.get("item")]
    entries.sort(key=lambda one: (str(one.get("day") or ""), int(one.get("order") or 99)))
    for one in entries:
        if _day(one.get("day")) is not None:
            out.setdefault(str(one["day"])[:10], []).append(str(one["item"]))
    return out


def _ids(entries: Any) -> List[str]:
    return [str(one.get("item") if isinstance(one, dict) else one)
            for one in entries if (one.get("item") if isinstance(one, dict) else one)] \
        if isinstance(entries, list) else []


def _minutes(clock: Any) -> Optional[int]:
    try:
        hours, minutes = str(clock).split(":")[:2]
        return int(hours) * 60 + int(minutes)
    except (ValueError, TypeError):
        return None


def base_of(row: Optional[Dict[str, Any]], day: date, plan_from: str) -> Optional[Dict[str, Any]]:
    """The base an adjustment starts from: the latest plan's days ahead, and - when it was of the
    day planned - its blocks before `plan_from`, kept as they were."""
    if not row:
        return None
    plan = row.get("plan") or {}
    planned = _day(row.get("day"))
    start = _minutes(plan_from)
    past, ahead, earlier = [], [], []
    # THE AGENT'S WORK AND THE USER'S MIGHT-DO (the agent-only plan, 2026-11-10): known, not new -
    # today's agent work still to come kept; an earlier day's, not done, has had its day.
    agent_today: List[Dict[str, Any]] = []
    for one in plan.get("agent_does") or []:
        if not isinstance(one, dict) or not one.get("item"):
            continue
        end = _minutes(one.get("end"))
        if planned == day and (start is None or end is None or end > start):
            agent_today.append(one)
        elif planned is not None and planned <= day:
            earlier.append(str(one["item"]))
    might = _ids(plan.get("might_do"))
    for block in plan.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        end = _minutes(block.get("end"))
        if planned == day and start is not None and end is not None and end <= start:
            past.append(dict(block, past=True))
        elif planned == day:
            ahead.append(block)                    # this day's, still to come: planned again from now
        elif planned is not None and planned < day:
            earlier.extend(str(one) for one in block.get("items") or [])
    return {"made_at": str(row.get("made_at") or ""), "day": planned, "by_day": by_day(plan),
            "past_blocks": past, "ahead_blocks": ahead, "from_earlier_blocks": earlier,
            "agent_does": agent_today, "might_do": might}


def placed_ids(base: Dict[str, Any]) -> set:
    out = {one for ids in base["by_day"].values() for one in ids}
    out.update(base["from_earlier_blocks"])
    out.update(str(one["item"]) for one in base.get("agent_does") or [])
    out.update(base.get("might_do") or [])
    out.update(str(one) for block in base["past_blocks"] + base.get("ahead_blocks", [])
               for one in block.get("items") or [])
    return out


def to_place(base: Dict[str, Any], things: List[Dict[str, Any]], day: date) -> List[Dict[str, str]]:
    """What the builder must place: new, sorted again, or slipped - each with why."""
    open_ids = {one["id"] for one in things}
    known = placed_ids(base)
    on_day = {ident: _day(when) for when, ids in base["by_day"].items() for ident in ids}
    out = []
    for one in things:
        ident, said = one["id"], one.get("triage") or {}
        if said.get("who") == "gone":
            continue
        if ident not in known:
            out.append({"id": ident, "why": "new since the plan"})
        elif str(said.get("at") or "") > base["made_at"]:
            out.append({"id": ident, "why": "sorted again since the plan"})
        elif ident in base["from_earlier_blocks"] or (on_day.get(ident) and on_day[ident] <= day):
            out.append({"id": ident, "why": "its day has come or gone, and it is not done"})
    return [one for one in out if one["id"] in open_ids]


def shown(base: Dict[str, Any], things: List[Dict[str, Any]], day: date) -> Dict[str, Any]:
    """The plan so far, as the builder is given it: the days ahead, ids in order (their titles are
    in the things), and the blocks of the day planned already past."""
    open_ids = {one["id"] for one in things}
    days = {}
    for when, ids in sorted(base["by_day"].items()):
        on = _day(when)
        kept = [one for one in ids if one in open_ids]
        if on is not None and on > day and kept:
            days[on.strftime("%Y-%m-%d %a")] = kept
    keys = ("start", "end", "kind", "title", "items")
    return {"days_ahead": days,
            # The agent's work planned for the rest of this day: kept unless something moves.
            "agent_does_so_far": [one for one in base.get("agent_does") or []
                                  if str(one.get("item")) in open_ids],
            "might_do_so_far": [one for one in base.get("might_do") or [] if one in open_ids],
            "blocks_already_past": [{k: block.get(k) for k in keys} for block in base["past_blocks"]],
            # This day's blocks still to come: planned again from now - kept unless something moves.
            "blocks_still_ahead": [{k: block.get(k) for k in keys}
                                   for block in base.get("ahead_blocks", [])]}


def merge(base: Optional[Dict[str, Any]], answer: Dict[str, Any], things: List[Dict[str, Any]],
          day: date) -> Dict[str, Any]:
    """The builder's answer laid on the base: the whole plan, as the check and the page read it -
    "later" a list of {"item", "day", "order"}, and "later_by_day" the same by day."""
    open_ids = {one["id"] for one in things}
    gone = {one["id"] for one in things if (one.get("triage") or {}).get("who") == "gone"}
    given = by_day(answer)
    days: Dict[str, List[str]] = {}
    if base is not None:
        for when, ids in base["by_day"].items():
            on = _day(when)
            if on is not None and on > day:
                days[when] = list(ids)
    elsewhere = set(_ids(answer.get("at_risk"))) | set(_ids(answer.get("clean_up")))
    for block in answer.get("blocks") or []:
        if isinstance(block, dict):
            elsewhere.update(str(one) for one in block.get("items") or [])
    # The agent's work is not the user's: what it does today stays on the day the user approves it.
    moved = {one for ids in given.values() for one in ids}
    for when in list(days):
        days[when] = [one for one in days[when]
                      if one in open_ids and one not in gone and one not in moved
                      and one not in elsewhere]
    for when, ids in given.items():
        on = _day(when)
        if on is None or on <= day:
            continue                               # the check says so: given back to the builder
        # The builder's whole list for that day - what it also put today stays today.
        days[when] = [one for one in ids if one not in elsewhere]
    days = {when: ids for when, ids in sorted(days.items()) if ids}
    later = [{"item": ident, "day": when, "order": n + 1}
             for when, ids in days.items() for n, ident in enumerate(ids)]
    blocks = list(base["past_blocks"]) if base is not None else []
    blocks += [one for one in answer.get("blocks") or [] if isinstance(one, dict)]
    out = dict(answer)
    # THE AGENT'S WORK OF THE DAY KEPT unless the answer places it again (anywhere).
    if base is not None and base.get("agent_does"):
        placed = set(_ids(answer.get("agent_does"))) | elsewhere | moved
        carried = [one for one in base["agent_does"] if str(one.get("item")) in open_ids
                   and str(one.get("item")) not in gone and str(one.get("item")) not in placed]
        if carried:
            out["agent_does"] = carried + list(answer.get("agent_does") or [])
    out.update(blocks=blocks, later=later, later_by_day=days)
    return out


def kept_with(kept: Dict[str, Any], plan: Dict[str, Any], answer: Dict[str, Any],
              frozen_until: Optional[str] = None) -> Dict[str, Any]:
    """THE DAYS AHEAD LAID ON THE DAY JUST KEPT (plan.py, two parts): its blocks as they were -
    those before `frozen_until` (the next hour) whatever the answer says, the rest as the answer
    gives them again when it does (the user, 2026-11-09: "现在之后1小时内的时间不变，剩余的reviewer会正常
    审核"); the agent's work in it, its summary and reminders, unless given again; what is at risk
    or cleared away, both parts' together."""
    out = dict(plan)
    kept_blocks = [one for one in kept.get("blocks") or [] if isinstance(one, dict)]
    if "blocks" not in answer:
        out["blocks"] = kept_blocks
    else:
        freeze = _minutes(frozen_until)
        given = [one for one in answer.get("blocks") or [] if isinstance(one, dict)]
        if freeze is None:
            out["blocks"] = [one for one in kept_blocks if one.get("past")] + given
        else:
            held = [one for one in kept_blocks
                    if one.get("past") or (_minutes(one.get("start")) or 0) < freeze]
            out["blocks"] = held + [one for one in given if (_minutes(one.get("start")) or 0) >= freeze]
    for key in ("summary", "held_reminders"):
        if not answer.get(key) and kept.get(key):
            out[key] = kept[key]
    for key in ("agent_does", "at_risk", "clean_up"):
        seen = {str(one.get("item")) for one in answer.get(key) or [] if isinstance(one, dict)}
        out[key] = [one for one in kept.get(key) or [] if isinstance(one, dict)
                    and str(one.get("item")) not in seen] + list(answer.get(key) or [])
    return out


def for_check(plan: Dict[str, Any]) -> Dict[str, Any]:
    """The plan as the check holds it: the blocks already past are history, not to be checked."""
    return dict(plan, blocks=[one for one in plan.get("blocks") or []
                              if not (isinstance(one, dict) and one.get("past"))])


__all__ = ["by_day", "base_of", "to_place", "shown", "merge", "for_check", "placed_ids", "kept_with"]

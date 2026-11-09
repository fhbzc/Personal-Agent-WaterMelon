"""THE RECOMMENDATION DEBUGGER (the user, 2026-11-06: "里面显示当前的候选库, trigger的keyword").

Read-only: what is kept ready and the words that set each off, when it was last looked for and
why, and what matched. Opening it asks no model and changes nothing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..common.modules import enabled as module_on
from ..heartbeat.monitor import match, reserve as rs

log = logging.getLogger(__name__)

#: How many of each are shown.
MOST_ITEMS, MOST_HITS = 500, 100


def _state(item: Dict[str, Any], at: str) -> str:
    if item["status"] == rs.READY:
        return "past" if item["expires_at"] <= at else "live"
    return item["status"]


def _hits(db: Any) -> List[Dict[str, Any]]:
    out = []
    for one in match.hits(db, MOST_HITS):
        try:
            one["hit"] = json.loads(one["hit"] or "[]")
        except ValueError:
            one["hit"] = []
        out.append(one)
    return out


def _learned(app: Any) -> Dict[str, Any]:
    """What the learning made of the user's answers, and whether it tells what they will answer."""
    from ..learning.from_lessons import UNIT, how_it_went
    from ..learning.store import LearningStore
    from ..learning.vocab import OF_THE_ANSWERS
    store = LearningStore(app.db)
    reasons = [{"id": one["id"], "statement": one["statement"], "aspect": one.get("aspect"),
                "state": one["state"], "source": one["source"]}
               for one in store.reasons(("active", "candidate", "competing"))
               if one.get("aspect") in OF_THE_ANSWERS]
    changed = [{"at": str(step.get("at") or "")[:10], "was": (step.get("before") or {}).get("statement", ""),
                "now": one["statement"], "why": step.get("why", ""), "answers": step["on_answers"]}
               for one in store.reasons(("active", "candidate", "competing"))
               for step in one.get("history") or [] if step.get("on_answers")]
    taken = app.db.query_one("SELECT COUNT(*) AS n FROM learn_judgments j JOIN learn_cases c ON "
                             "c.id = j.case_id WHERE c.unit_id = ?", (UNIT,))
    return {"judgments": int(taken["n"]) if taken else 0, "reasons": reasons,
            "changed": sorted(changed, key=lambda one: one["at"], reverse=True)[:20],
            "predictions": how_it_went(app.db)}


def _learned_or_why_not(app: Any) -> Dict[str, Any]:
    """One part that cannot be read does not take the others with it."""
    try:
        return _learned(app)
    except Exception as error:
        log.warning("what was learned could not be read", exc_info=True)
        return {"error": str(error)}


def report(app: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    reserve, config = app.reserve, app.config
    at = now.isoformat()
    work = app.monitor_decider._tasks()
    matched = {row["reserve_id"]: row["n"] for row in app.db.query(
        "SELECT reserve_id, COUNT(*) AS n FROM reserve_hits GROUP BY reserve_id")}
    items = [dict(one, state=_state(one, at), matches=matched.get(one["id"], 0))
             for one in reserve.newest(MOST_ITEMS)]
    states: Dict[str, int] = {}
    for one in items:
        states[one["state"]] = states.get(one["state"], 0) + 1
    return {
        "at": at,
        "switches": {"module": module_on(config, "recommendation"),
                     "monitor": module_on(config, "monitor"),
                     "search": bool(config.get("monitor_recommend_search", True))},
        "looks": dict(reserve.looks(), due=reserve.due(now, work),
                      signals=reserve.signals(work, now)),
        "scores": {"proper": match.PROPER_WORTH, "concept": match.CONCEPT_WORTH,
                   "middle": match.line_of(app.db), "high": match.high_of(app.db),
                   "common": match.COMMON},
        "rarity": reserve.rarity.described(),
        "learned": _learned_or_why_not(app),
        "states": states, "items": items, "hits": _hits(app.db),
    }


__all__ = ["report"]

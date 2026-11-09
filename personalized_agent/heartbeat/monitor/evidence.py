"""THE BIGGER THE CHANGE, THE MORE IT MUST BE SHOWN (the user, 2026-11-04: "要改的越多，需要
agent搜越多的context来确认, 还是不要给用户审批").

One sentence of the user's is not a reason to rewrite how the agent speaks to them.
So what the model changes in chat is earned by what it has looked up: nobody is
asked, and what it cannot show it does not do - it does less. Evidence builds
up: the same thing said again, or answered again, is a case more, and the
change held back today goes through then.

    the first Activator changed    its record looked at first (`monitor_evidence`)
    the 2nd and 3rd, in one turn   as much, and CASES_FOR_MORE cases cited
    the 4th on, or any deleted     as much, on DAYS_FOR_MOST days at the least

Whether a case bears on the change is the model's to judge; that it looked, and
that the cases are real, is held here by code. A rule of quiet (`monitor_quiet_add`)
is one change however many Activators it stills, so it needs none of this.

THE USER'S OWN ORDER IS EVIDENCE (the user, 2026-11-04): all this is against a large
change inferred from a sentence, not one they named. Their words, quoted in
`user_said`, let it through - and code checks they are words they wrote just now,
so none can be made up.

THE HEARTBEAT'S OWN CHANGES ARE HELD TOO, and harder (`background_refusal`): no
user asked for them, so even the first takes CASES_FOR_MORE cases. It cites none;
what is on record is counted for it. Both count towards the same total.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

#: How long what was looked up stands, and how far back changes count as one turn's.
FRESH_SECONDS = 900
#: Cases cited for more than one change; the days they must span for the most.
CASES_FOR_MORE = 3
DAYS_FOR_MOST = 2
MOST_CASES = 20
#: The fewest characters quoted of the user's words.
LEAST_QUOTE = 4


class Evidence:
    """What was looked up for each Activator lately, and what it allows."""

    def __init__(self, monitor: Any, retriever: Callable[[], Any]) -> None:
        self.monitor, self.retriever = monitor, retriever
        self._looked: Dict[str, Dict[str, Any]] = {}

    def look(self, ident: str, about: str) -> Dict[str, Any]:
        """An Activator's record and the user's answers, and what is like it."""
        found = self.monitor.store.get(ident)
        if found is None:
            return {"error": f"there is no Activator {ident!r}"}
        cases: List[Dict[str, Any]] = []
        for row in self.monitor.db.query(
                "SELECT id, at, action, why, acted, outcome FROM monitor_firings "
                "WHERE activator_id = ? ORDER BY at DESC LIMIT ?", (ident, MOST_CASES)):
            cases.append({"id": row["id"], "at": str(row["at"])[:16], "kind": "fired",
                          "said": f"{row['action']}: {str(row['why'] or '')[:200]}"})
        rules: List[str] = []
        retriever = self.retriever()
        if retriever is not None:
            # WHAT THE CHANGE IS ABOUT (the user, 2026-11-07): what the model says it changes, and
            # what the Activator is for - not its name or when it runs; in English first.
            moment = " ".join(part for part in (str(about or ""), str(found.get("purpose") or ""))
                              if part.strip())
            found_lessons = retriever.retrieve(("notifications", "general"), moment,
                                               rules=6, examples=MOST_CASES // 2,
                                               rewrite_task="monitor_lessons_query",
                                               site="monitor evidence")
            rules = [str(one["statement"]) for one in found_lessons["rules"]]
            for one in found_lessons["examples"]:
                cases.append({"id": str(one.get("id")), "at": str(one.get("at") or "")[:16],
                              "kind": str(one.get("action") or "lesson"),
                              "said": str(one.get("reason") or one.get("english")
                                          or one.get("summary") or "")[:200]})
        self._looked[ident] = {"at": time.monotonic(),
                               "days": {one["id"]: one["at"][:10] for one in cases}}
        return {"activator": found["name"], "purpose": found.get("purpose"), "when": found["when"],
                "cases": cases, "lessons": rules,
                "said": "Cite in `evidence` the ids of the cases that truly bear on the change. "
                        "Too few: change less. The lesson stands, and the evidence builds up."}

    def _changed_lately(self) -> int:
        since = (datetime.now(timezone.utc) - timedelta(seconds=FRESH_SECONDS)).isoformat()
        return int(self.monitor.db.query_one(
            "SELECT COUNT(*) AS n FROM monitor_changes WHERE by IN ('chat', 'heartbeat') "
            "AND at >= ? AND op IN ('edit', 'delete')", (since,))["n"])

    def _they_said(self, quote: Any) -> bool:
        """Whether these are words the user has just written."""
        words = " ".join(str(quote or "").split())
        if len(words) < LEAST_QUOTE:
            return False
        since = (datetime.now(timezone.utc) - timedelta(seconds=FRESH_SECONDS)).isoformat()
        rows = self.monitor.db.query("SELECT content FROM messages WHERE role = 'user' "
                                     "AND created_at >= ?", (since,))
        return any(words in " ".join(str(row["content"] or "").split()) for row in rows)

    def refusal(self, op: str, ident: str, cited: Optional[Sequence[str]],
                user_said: Any = "") -> str:
        """Why this change may not be made yet - "" when it may."""
        found = self.monitor.store.get(ident)
        if found is None or found["protected"]:
            return ""                                    # refused as it always was
        if user_said:
            if self._they_said(user_said):
                return ""
            return ("Those are not words the user has just written: quote them exactly, or "
                    "leave user_said out and show cases instead.")
        looked = self._looked.get(ident)
        if looked is None or time.monotonic() - looked["at"] > FRESH_SECONDS:
            return f"Look first: call monitor_evidence for {ident}, then change it citing cases."
        days = {looked["days"][one] for one in cited or [] if one in looked["days"]}
        real = [one for one in cited or [] if one in looked["days"]]
        count = self._changed_lately() + 1
        most = op == "delete" or count > 3
        if (count > 1 or most) and len(real) < CASES_FOR_MORE:
            return (f"This would be change {count} in this turn, and {len(real)} real cases are "
                    f"cited: {CASES_FOR_MORE} are needed. Change less: keep the lesson, or use "
                    "monitor_quiet_add, which is one change however many Activators it stills. "
                    "The evidence builds up.")
        if most and len(days) < DAYS_FOR_MOST:
            return (f"A change this large needs cases from {DAYS_FOR_MOST} days at the least; "
                    f"they come from {len(days)}. Change less: keep the lesson for now.")
        return ""


    def background_refusal(self, op: str, ident: str, why: str) -> str:
        """Why the heartbeat may not make this change - "" when it may."""
        found = self.monitor.store.get(ident)
        if found is None or found["protected"]:
            return ""
        cases = self.look(ident, why).get("cases") or []
        if len(cases) < CASES_FOR_MORE:
            return f"nobody asked, and {len(cases)} cases are on record: {CASES_FOR_MORE} are needed"
        days = {one["at"][:10] for one in cases}
        if (op == "delete" or self._changed_lately() >= 3) and len(days) < DAYS_FOR_MOST:
            return f"a change this large needs cases from {DAYS_FOR_MOST} days"
        return ""


__all__ = ["Evidence", "CASES_FOR_MORE", "DAYS_FOR_MOST"]

"""Every model call, kept on disk (section 14.35, as amended).

What the agent spends was held in memory - a deque of four hundred calls and a
few running totals - so it began again at zero every time the application
started. That makes the one question worth asking unanswerable: *is this
getting more expensive?* A week of heartbeats against last week's, a change of
model against the one before it, the cost of a busy Tuesday - none of those
survive a restart, and the agent restarts whenever anything is fixed.

The gathering cost was already written down (section 14.36) and read back
across restarts. This is the same thing for the other half of the bill, in the
same shape and for the same reason: a measurement that only covers the current
process is a measurement of nothing in particular.

**Never raises.** This is instrumentation. An answer that fails because its own
accounting fell over is a far worse outcome than a missing row.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..events.models import iso, new_id, utcnow

log = logging.getLogger(__name__)

#: How long a call is kept.
#:
#: Long enough that a month-on-month comparison has something to compare, and
#: bounded because this is one row per model call for ever otherwise. A row is
#: about a hundred bytes; a year of heavy use is a few tens of megabytes.
KEEP_DAYS = 400

#: How many points a chart is drawn from.
#:
#: The question is the shape of the trend. Ten thousand points of one call each
#: is not a shape, and drawing it costs more than reading it.
MAX_POINTS = 600

#: WHAT A CALL READ, WHOLE: what was neither read from the provider's prompt
#: cache nor written to it, and both of those - as a run counts it
#: (heartbeat/experience/store.py, `input_of`). The column alone is a
#: handful on a cached provider - Claude Code's calls said "input 10" for
#: three hundred thousand read - so "input tokens" drew nothing at all, and
#: "total" was "output" over again: choosing between them changed nothing.
INPUT_SQL = ("(input_tokens + COALESCE(cached_input_tokens, 0) "
             "+ COALESCE(cache_written_tokens, 0))")
TOTAL_SQL = f"({INPUT_SQL} + output_tokens)"


def _read_whole(row: Any) -> int:
    """`INPUT_SQL`, of one row."""
    keys = row.keys() if hasattr(row, "keys") else ()
    return (int(row["input_tokens"] or 0)
            + int((row["cached_input_tokens"] if "cached_input_tokens" in keys else 0) or 0)
            + int((row["cache_written_tokens"] if "cache_written_tokens" in keys else 0) or 0))


class ModelCalls:
    """Where each model call is written, and how it is read back."""

    def __init__(self, db: Any) -> None:
        self.db = db

    # -- writing ------------------------------------------------------------
    def record(self, *, provider: str, model: str, task: str,
               input_tokens: int, output_tokens: int, latency_ms: float,
               ok: bool = True, error: str = "",
               estimated_cost: Optional[float] = None,
               cached_input_tokens: int = 0,
               cache_written_tokens: int = 0, language: str = "",
               by_user: bool = False) -> None:
        try:
            self.db.execute(
                "INSERT INTO model_calls (id, created_at, provider, model, task, "
                "input_tokens, output_tokens, total_tokens, latency_ms, ok, "
                "error, estimated_cost, cached_input_tokens, cache_written_tokens, "
                "language, by_user) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id("call"), iso(utcnow()), str(provider or ""),
                 str(model or ""), str(task or "general"),
                 int(input_tokens or 0), int(output_tokens or 0),
                 int(input_tokens or 0) + int(output_tokens or 0),
                 round(float(latency_ms or 0), 2), 1 if ok else 0,
                 str(error or ""), estimated_cost,
                 int(cached_input_tokens or 0), int(cache_written_tokens or 0),
                 str(language or ""), 1 if by_user else 0),
            )
        except Exception:            # pragma: no cover - measurement, not work
            log.debug("could not record a model call", exc_info=True)

    def prune(self, keep_days: int = KEEP_DAYS) -> int:
        if keep_days <= 0:
            return 0
        try:
            cutoff = iso(utcnow() - timedelta(days=int(keep_days)))
            row = self.db.query_one(
                "SELECT COUNT(*) AS c FROM model_calls WHERE created_at < ?",
                (cutoff,))
            count = int(row["c"]) if row else 0
            if count:
                self.db.execute("DELETE FROM model_calls WHERE created_at < ?",
                                (cutoff,))
            return count
        except Exception:            # pragma: no cover
            log.debug("could not prune model calls", exc_info=True)
            return 0

    def fill_costs(self) -> int:
        """THE COST OF CALLS MADE BEFORE IT WAS COUNTED: at the list price of
        the model each named (prices.py); a model not priced stays empty.
        Claude Code's names - "opus" - as it answers them today."""
        from ...models.prices import cache_write_times, estimate

        try:
            rows = self.db.query(
                "SELECT id, provider, model, input_tokens, output_tokens, "
                "cached_input_tokens, cache_written_tokens FROM model_calls "
                "WHERE estimated_cost IS NULL AND ok = 1")
            filled = []
            for r in rows:
                cost = estimate(r["model"], r["input_tokens"], r["output_tokens"],
                                r["cached_input_tokens"] or 0, r["cache_written_tokens"] or 0,
                                write_times=cache_write_times(r["provider"]))
                if cost is not None:
                    filled.append((cost, r["id"]))
            if filled:
                self.db.executemany(
                    "UPDATE model_calls SET estimated_cost = ? WHERE id = ?", filled)
            return len(filled)
        except Exception:            # pragma: no cover - measurement, not work
            log.debug("could not fill in the cost of earlier calls", exc_info=True)
            return 0

    # -- reading ------------------------------------------------------------
    def _rows(self, since: Optional[datetime] = None,
              limit: int = MAX_POINTS) -> List[Dict[str, Any]]:
        try:
            if since is not None:
                return list(self.db.query(
                    "SELECT * FROM model_calls WHERE created_at >= ? "
                    "ORDER BY created_at DESC LIMIT ?", (iso(since), int(limit))))
            return list(self.db.query(
                "SELECT * FROM model_calls ORDER BY created_at DESC LIMIT ?",
                (int(limit),)))
        except Exception:            # pragma: no cover
            log.debug("could not read model calls", exc_info=True)
            return []

    def over_time(self, since: Optional[datetime] = None,
                  limit: int = MAX_POINTS) -> List[Dict[str, Any]]:
        """The points a chart is drawn from, oldest first."""
        rows = self._rows(since, limit)
        out = []
        for r in reversed(rows):
            read = _read_whole(r)
            out.append({"timestamp": r["created_at"],
                        "total_tokens": read + int(r["output_tokens"] or 0),
                        "input_tokens": read, "output_tokens": r["output_tokens"],
                        "task": r["task"], "model": r["model"],
                        "estimated_cost": r["estimated_cost"]})
        return out

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [dict(r) for r in reversed(self._rows(None, limit))]

    def totals(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        """Everything spent, over the whole history rather than this process.

        Summed in the database rather than by reading the rows: the point of
        keeping them all is that there are eventually a great many.
        """
        where, args = "", ()
        if since is not None:
            where, args = "WHERE created_at >= ?", (iso(since),)
        try:
            row = self.db.query_one(
                f"SELECT COUNT(*) AS calls, "
                f"COALESCE(SUM({INPUT_SQL}), 0) AS input, "
                f"COALESCE(SUM(output_tokens), 0) AS output, "
                f"COALESCE(SUM({TOTAL_SQL}), 0) AS total, "
                f"COALESCE(SUM(estimated_cost), 0) AS cost, "
                f"COALESCE(AVG(latency_ms), 0) AS latency, "
                f"COALESCE(SUM(1 - ok), 0) AS errors, "
                f"MIN(created_at) AS first_at "
                f"FROM model_calls {where}", args)
        except Exception:            # pragma: no cover
            return {"calls": 0, "input": 0, "output": 0, "total": 0,
                    "errors": 0, "estimated_cost": 0.0,
                    "average_latency_ms": 0.0, "since": ""}
        if row is None:
            return {"calls": 0, "input": 0, "output": 0, "total": 0,
                    "errors": 0, "estimated_cost": 0.0,
                    "average_latency_ms": 0.0, "since": ""}
        return {"calls": int(row["calls"]), "input": int(row["input"]),
                "output": int(row["output"]), "total": int(row["total"]),
                "errors": int(row["errors"]),
                "estimated_cost": round(float(row["cost"] or 0), 6),
                "average_latency_ms": round(float(row["latency"] or 0), 2),
                "since": row["first_at"] or ""}

    def _grouped(self, column: str,
                 since: Optional[datetime] = None) -> Dict[str, Dict[str, int]]:
        where, args = "", ()
        if since is not None:
            where, args = "WHERE created_at >= ?", (iso(since),)
        try:
            rows = self.db.query(
                f"SELECT {column} AS name, COUNT(*) AS calls, "
                f"COALESCE(SUM({INPUT_SQL}), 0) AS input, "
                f"COALESCE(SUM(output_tokens), 0) AS output, "
                f"COALESCE(SUM({TOTAL_SQL}), 0) AS total "
                f"FROM model_calls {where} GROUP BY {column} "
                f"ORDER BY total DESC", args)
        except Exception:            # pragma: no cover
            return {}
        return {str(r["name"] or "general"): {
            "calls": int(r["calls"]), "input": int(r["input"]),
            "output": int(r["output"]), "total": int(r["total"])}
            for r in rows}

    def by_task(self, since: Optional[datetime] = None) -> Dict[str, Dict[str, int]]:
        return self._grouped("task", since)

    def by_model(self, since: Optional[datetime] = None) -> Dict[str, Dict[str, int]]:
        return self._grouped("model", since)

    def by_day(self, days: int = 30, minutes_east: int = 0) -> Dict[str, Any]:
        """What each kind of work spent on each of the last `days` days.

        The trend the last few hundred calls cannot show: a busy hour fills
        that window, and whether the spending is going up over weeks is the
        question. Days are the reader's own (`minutes_east` of UTC), cut at
        their midnight; summed in the database, one row per day and kind."""
        days = max(1, min(int(days or 30), KEEP_DAYS))
        shift = f"{int(minutes_east or 0):+d} minutes"
        today = (utcnow() + timedelta(minutes=int(minutes_east or 0))).date()
        first = today - timedelta(days=days - 1)
        # A day's first call is at most a day either side of UTC's.
        since = datetime.combine(first, datetime.min.time(),
                                 tzinfo=timezone.utc) - timedelta(days=1)
        try:
            rows = self.db.query(
                "SELECT date(created_at, ?) AS day, task, COALESCE(by_user, 0) AS by_user, "
                "COUNT(*) AS calls, "
                f"COALESCE(SUM({INPUT_SQL}), 0) AS input_tokens, "
                "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
                f"COALESCE(SUM({TOTAL_SQL}), 0) AS total_tokens, "
                # Of what was read, what came from the provider's cache - at a
                # fraction of the price; the chart shows it lighter.
                "COALESCE(SUM(COALESCE(cached_input_tokens, 0)), 0) AS cached_tokens, "
                "COALESCE(SUM(estimated_cost), 0) AS estimated_cost "
                "FROM model_calls WHERE created_at >= ? "
                "GROUP BY day, task, COALESCE(by_user, 0) ORDER BY day", (shift, iso(since)))
        except Exception:            # pragma: no cover
            log.debug("could not read model calls by day", exc_info=True)
            rows = []
        from ..modules import module_name, module_of
        out = [{"day": r["day"], "task": str(r["task"] or "general"),
                "module": module_of(r["task"], bool(r["by_user"])),
                "module_name": module_name(module_of(r["task"], bool(r["by_user"]))),
                "calls": int(r["calls"]), "input_tokens": int(r["input_tokens"]),
                "output_tokens": int(r["output_tokens"]),
                "total_tokens": int(r["total_tokens"]),
                "cached_tokens": int(r["cached_tokens"]),
                "estimated_cost": round(float(r["estimated_cost"] or 0), 6)}
               for r in rows if r["day"] and r["day"] >= first.isoformat()]
        return {"days": [(first + timedelta(days=i)).isoformat()
                         for i in range(days)], "rows": out}

    def snapshot(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        """What the Debugger draws, from disk rather than from this process."""
        totals = self.totals(since)
        return {
            "totals": {"calls": totals["calls"], "input": totals["input"],
                       "output": totals["output"], "total": totals["total"],
                       "errors": totals["errors"]},
            "estimated_cost": totals["estimated_cost"],
            "by_task": self.by_task(since),
            "by_model": self.by_model(since),
            "recent_calls": self.recent(50),
            "tokens_over_time": self.over_time(since),
            "average_latency_ms": totals["average_latency_ms"],
            # What the numbers actually cover, so that "since the agent
            # started" stops being the implied and wrong answer.
            "measured_since": totals["since"],
            "kept_days": KEEP_DAYS,
        }


__all__ = ["ModelCalls", "KEEP_DAYS", "MAX_POINTS"]

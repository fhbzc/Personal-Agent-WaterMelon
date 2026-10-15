"""Structured observability interface for the Debugger (section 14).

The Debugger never scrapes internals: every subsystem reports here, and the
frontend reads one normalized snapshot.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional

try:  # optional, degrades gracefully (section 28)
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


MAX_STEPS = 60


log = logging.getLogger(__name__)


class Observability:
    def __init__(self, history: int = 400) -> None:
        self._lock = threading.RLock()
        self._started = time.time()
        self._model_calls: Deque[Dict[str, Any]] = deque(maxlen=history)
        #: Where the same calls are written so they survive a restart.
        #:
        #: Attached after construction, because the database is built later
        #: than this. Without it everything still works and everything is
        #: still counted - for as long as the process lives, which is what it
        #: used to be.
        self.store: Any = None
        self._tokens_by_task: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"input": 0, "output": 0, "total": 0, "calls": 0}
        )
        self._tokens_by_model: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"input": 0, "output": 0, "total": 0, "calls": 0}
        )
        self._totals = {"input": 0, "output": 0, "total": 0, "calls": 0, "errors": 0}
        self._counters: Dict[str, int] = defaultdict(int)
        self._states: Dict[str, Any] = {}
        self._errors: Deque[Dict[str, Any]] = deque(maxlen=history)
        self._violations: Deque[Dict[str, Any]] = deque(maxlen=history)
        self._recent_tasks: Deque[Dict[str, Any]] = deque(maxlen=50)
        self._active_task: Optional[Dict[str, Any]] = None

    # -- model metrics ------------------------------------------------------
    def record_model_call(
        self,
        *,
        provider: str,
        model: str,
        task: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
        ok: bool = True,
        error: Optional[str] = None,
        estimated_cost: Optional[float] = None,
        cached_input_tokens: int = 0,
        cache_written_tokens: int = 0,
        language: str = "",
        by_user: bool = False,
    ) -> None:
        total = int(input_tokens) + int(output_tokens)
        if self.store is not None:
            # On disk first, because the in-memory copy is the one that can be
            # rebuilt. Guarded here as well as inside the store: this is
            # measurement, and a turn that fails because its own accounting
            # fell over is a far worse outcome than a missing row.
            try:
                self.store.record(
                    provider=provider, model=model, task=task,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                    latency_ms=latency_ms, ok=ok, error=error or "",
                    estimated_cost=estimated_cost,
                    cached_input_tokens=cached_input_tokens,
                    cache_written_tokens=cache_written_tokens, language=language,
                    by_user=by_user)
            except Exception:        # pragma: no cover - measurement, not work
                log.debug("could not write a model call", exc_info=True)
        from ..modules import module_name, module_of
        entry = {
            "timestamp": _now(),
            "provider": provider,
            "model": model,
            "task": task,
            # Its module, and whether the user started it: for the chart.
            "module": module_of(task, by_user),
            "module_name": module_name(module_of(task, by_user)),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cached_input_tokens": int(cached_input_tokens or 0),
            "cache_written_tokens": int(cache_written_tokens or 0),
            "total_tokens": total,
            "latency_ms": round(latency_ms, 2),
            "ok": ok,
            "error": error,
            "estimated_cost": estimated_cost,
        }
        with self._lock:
            self._model_calls.append(entry)
            for bucket, key in ((self._tokens_by_task, task), (self._tokens_by_model, f"{provider}/{model}")):
                bucket[key]["input"] += int(input_tokens)
                bucket[key]["output"] += int(output_tokens)
                bucket[key]["total"] += total
                bucket[key]["calls"] += 1
            self._totals["input"] += int(input_tokens)
            self._totals["output"] += int(output_tokens)
            self._totals["total"] += total
            self._totals["calls"] += 1
            if not ok:
                self._totals["errors"] += 1

    # -- counters and states ------------------------------------------------
    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def set_state(self, name: str, value: Any) -> None:
        with self._lock:
            self._states[name] = value

    def get_state(self, name: str, default: Any = None) -> Any:
        with self._lock:
            return self._states.get(name, default)

    def record_error(self, component: str, message: str, detail: Optional[str] = None) -> None:
        with self._lock:
            self._errors.append(
                {"timestamp": _now(), "component": component, "message": message, "detail": detail}
            )

    def record_violation(self, kind: str, detail: Dict[str, Any]) -> None:
        with self._lock:
            self._violations.append({"timestamp": _now(), "kind": kind, **detail})
            self._counters["sandbox.violations"] += 1

    # -- agent task tracking ------------------------------------------------
    def task_started(self, task_id: str, kind: str, summary: str) -> None:
        with self._lock:
            self._active_task = {
                "task_id": task_id, "kind": kind, "summary": summary,
                "started_at": _now(), "steps": [],
            }

    def task_step(self, task_id: str, stage: str, detail: str = "") -> None:
        """Record what the agent is doing now, for whoever is waiting on it.

        A request that takes twenty seconds and says only "thinking" is
        indistinguishable from one that has hung. The stages are recorded as
        they happen so that the wait can be shown as progress instead.
        """
        with self._lock:
            active = self._active_task
            if not active or active.get("task_id") != task_id:
                return
            steps = active.setdefault("steps", [])
            if steps:
                steps[-1]["finished_at"] = _now()
            steps.append({"stage": stage, "detail": detail, "started_at": _now()})
            # A long tool loop must not grow without bound in memory.
            if len(steps) > MAX_STEPS:
                del steps[: len(steps) - MAX_STEPS]

    def active_progress(self, kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """What the agent is working on now, if anything.

        Restricted by origin where asked: a person waiting on their own
        question should not be shown the progress of a background heartbeat
        that happened to start while they waited.
        """
        with self._lock:
            active = self._active_task
            if not active:
                return None
            if kind is not None and active.get("kind") != kind:
                return None
            snapshot = dict(active)
            snapshot["steps"] = [dict(step) for step in active.get("steps", [])]
            return snapshot

    def task_finished(self, task_id: str, status: str, detail: str = "") -> None:
        with self._lock:
            entry = dict(self._active_task or {"task_id": task_id})
            for step in entry.get("steps", []):
                step.setdefault("finished_at", _now())
            entry.update({"finished_at": _now(), "status": status, "detail": detail})
            self._recent_tasks.appendleft(entry)
            if self._active_task and self._active_task.get("task_id") == task_id:
                self._active_task = None

    # -- snapshot -----------------------------------------------------------
    def system_metrics(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "runtime_seconds": round(time.time() - self._started, 1),
            "thread_count": threading.active_count(),
        }
        if psutil is None:
            data.update({"cpu_percent": None, "memory_mb": None, "process_count": None,
                         "note": "psutil unavailable"})
            return data
        try:
            process = psutil.Process()
            with process.oneshot():
                data["cpu_percent"] = process.cpu_percent(interval=None)
                data["memory_mb"] = round(process.memory_info().rss / (1024 * 1024), 1)
                data["process_count"] = len(process.children(recursive=True)) + 1
            data["system_cpu_percent"] = psutil.cpu_percent(interval=None)
        except Exception as exc:  # pragma: no cover - platform dependent
            data["error"] = str(exc)
        return data

    def snapshot(self) -> Dict[str, Any]:
        if self.store is not None:
            # Read back from disk, so the numbers cover the agent's whole life
            # rather than the current process. The question worth asking is
            # whether this is getting more expensive, and that cannot be
            # answered from a window that restarts whenever anything is fixed.
            try:
                model_metrics = self.store.snapshot()
            except Exception:        # pragma: no cover - fall back to memory
                log.debug("could not read the model call history", exc_info=True)
                model_metrics = None
            if model_metrics is not None:
                with self._lock:
                    return {"model_metrics": model_metrics,
                            "system_metrics": self.system_metrics(),
                            **self._rest()}
        with self._lock:
            return {
                "model_metrics": {
                    "totals": dict(self._totals),
                    "by_task": {k: dict(v) for k, v in self._tokens_by_task.items()},
                    "by_model": {k: dict(v) for k, v in self._tokens_by_model.items()},
                    "recent_calls": list(self._model_calls)[-50:],
                    # Section 14.33, as amended: which kind of work spent it.
                    #
                    # This carried a timestamp and a number, which answers "how
                    # much" and not "on what" - and on what is the question
                    # worth asking, because the answer is actionable: a
                    # heartbeat costing more than every conversation put
                    # together is a setting to change, and indistinguishable
                    # from a busy week without the task on each point.
                    "tokens_over_time": [
                        {"timestamp": c["timestamp"], "total_tokens": c["total_tokens"],
                         "input_tokens": c["input_tokens"],
                         "output_tokens": c["output_tokens"],
                         "task": c["task"], "model": c["model"],
                         "estimated_cost": c["estimated_cost"]}
                        for c in list(self._model_calls)[-200:]
                    ],
                    "average_latency_ms": round(
                        sum(c["latency_ms"] for c in self._model_calls) / len(self._model_calls), 2
                    ) if self._model_calls else 0.0,
                },
                "system_metrics": self.system_metrics(),
                **self._rest(),
            }

    def _rest(self) -> Dict[str, Any]:
        """Everything that is about this run rather than about the history.

        What is running now, what it just did, what went wrong: none of that
        means anything from a previous process, so none of it is kept.
        """
        return {
            "agent_metrics": {
                "active_task": self._active_task,
                "recent_tasks": list(self._recent_tasks),
                "counters": dict(self._counters),
                "states": dict(self._states),
                "sandbox_violations": list(self._violations)[-25:],
                "errors": list(self._errors)[-25:],
            },
        }

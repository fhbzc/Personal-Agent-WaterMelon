"""The chat debugger: one chat turn, with everything it did written down.

Ported from PR #4 (by daniela-miaut) onto the chat as it is now.

A chat answer is the end of a chain nobody sees - the prompt the model was
actually sent, which model the manager resolved to, the exact payload handed
to the client, every tool call and what came back - and when an answer is
wrong, the question is which link. This runs a turn through the ordinary
`AgentCore.handle_message` with a `debug` callback at each of those seams, and
keeps the ordered events as the run's transcript.

Two kinds of run:

* **a scripted scenario** (chat_tests.py) - the real application composition
  with only the model scripted, on a throwaway application built for the run
  under this application's own data root, and deleted afterwards. Nothing of
  the user's is read or changed, and no credential is needed;
* **a prompt of the user's own** - sent to THIS application, through the
  configured model, with the normal tools under the normal policy. It is a
  real conversation: it can change things, and it is kept like any other.

Everything kept is redacted first, with the same registered-secret filter the
logs use, and stored in ``chat_debugger_runs`` so a run outlives a restart.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ..common.events.models import new_id
from ..common.logging.setup import REDACTED, SECRETS

#: Keys whose values are never kept, whatever they hold. The registered-secret
#: filter only knows the secrets it was told about; a key named like one of
#: these is a secret by its name.
SECRET_KEYS = {"api_key", "access_token", "refresh_token", "authorization",
               "password", "secret", "credential", "session_token", "x-api-key"}


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return SECRETS.scrub(value)
    if isinstance(value, dict):
        return {SECRETS.scrub(str(key)): REDACTED if str(key).lower() in SECRET_KEYS
                else _redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return SECRETS.scrub(str(value))


class ChatDebuggerBusy(Exception):
    """One run at a time: a second one waits for the first to finish."""


class ChatDebugger:
    """The runs, and the one runner. One per application; it never replaces
    anything the live chat uses."""

    def __init__(self, app: Any, *, capacity: int = 20,
                 event_bytes: int = 2_000_000) -> None:
        self.app = app
        #: How many whole transcripts are kept in memory. The database keeps
        #: every run; this only spares reading the newest ones back.
        self.capacity = max(1, capacity)
        #: How much of one run's events are kept, encoded. A turn that reads
        #: a large file into the model would otherwise make one run the size
        #: of the file several times over.
        self.event_bytes = max(1, event_bytes)
        self._runs: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._running = threading.Lock()
        self._db = getattr(app, "db", None)
        if self._db is not None:
            # A run the application closed under cannot be resumed. Kept, and
            # said so, rather than left saying "running" for ever.
            for row in self._db.query(
                    "SELECT transcript FROM chat_debugger_runs WHERE status = 'running'"):
                run = json.loads(row["transcript"])
                run["status"] = "error"
                run["error"] = "The application closed before this run finished."
                self._persist(run)

    @staticmethod
    def _summary(run: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in run.items() if key not in {"events", "result"}}

    def _persist(self, run: Dict[str, Any]) -> None:
        if self._db is None:
            return
        self._db.execute(
            "INSERT INTO chat_debugger_runs (id, started_at, status, summary, transcript) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "status = excluded.status, summary = excluded.summary, "
            "transcript = excluded.transcript",
            (run["id"], run["started_at"], run["status"],
             json.dumps(self._summary(run), ensure_ascii=False),
             json.dumps(run, ensure_ascii=False)))

    # -- reading -------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """The scenarios, and every run newest first - without their events."""
        from .chat_tests import TESTS

        with self._lock:
            if self._db is None:
                runs = [self._summary(run) for run in reversed(self._runs)]
            else:
                live = {run["id"]: run for run in self._runs}
                runs = [self._summary(live[row["id"]]) if row["id"] in live
                        else json.loads(row["summary"]) for row in self._db.query(
                            "SELECT id, summary FROM chat_debugger_runs "
                            "ORDER BY started_at DESC, rowid DESC")]
            return copy.deepcopy({"tests": TESTS, "runs": runs})

    def detail(self, run_id: str) -> Dict[str, Any]:
        """One run whole, events and all. KeyError when there is none."""
        with self._lock:
            for run in self._runs:
                if run["id"] == run_id:
                    return copy.deepcopy(run)
            if self._db is not None:
                row = self._db.query_one(
                    "SELECT transcript FROM chat_debugger_runs WHERE id = ?", (run_id,))
                if row:
                    return json.loads(row["transcript"])
        raise KeyError(run_id)

    # -- running -------------------------------------------------------------
    def run(self, *, test_id: Optional[str] = None, message: Optional[str] = None,
            conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """Run a scenario, or the user's own prompt, and return the run whole.

        KeyError for a scenario that does not exist; ChatDebuggerBusy while
        another run is going.
        """
        from .chat_tests import TESTS, run_test, scratch_folder

        test = next((one for one in TESTS if one["id"] == test_id), None)
        if message is None and test is None:
            raise KeyError(test_id)
        if not self._running.acquire(blocking=False):
            raise ChatDebuggerBusy("A chat debugger run is already going.")
        started = time.monotonic()
        custom = message is not None
        run: Dict[str, Any] = {
            "id": new_id("chat_debug"),
            "test_id": "custom" if custom else test_id,
            "name": "Your own prompt" if custom else test["name"],
            "prompt": _redact(message) if custom else "",
            "conversation_id": _redact(conversation_id) if custom else None,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "running", "duration_seconds": 0,
            "checks": [], "error": None, "events": [], "result": None,
            "capture_truncated": False,
        }
        try:
            with self._lock:
                self._persist(run)
                self._runs.append(run)
                if self._db is not None:
                    # The database keeps them all; memory keeps the newest.
                    del self._runs[:-self.capacity]
        except BaseException:
            self._running.release()
            raise
        used = 0

        def capture(kind: str, data: Any) -> None:
            nonlocal used
            # Redacted as it arrives: a secret never sits in memory in a
            # transcript, let alone reaches the database.
            event = {"sequence": len(run["events"]) + 1,
                     "time": datetime.now(timezone.utc).isoformat(),
                     "kind": kind, "data": _redact(data)}
            size = len(json.dumps(event, ensure_ascii=False).encode("utf-8"))
            with self._lock:
                if kind == "instruction" and not custom:
                    # A scenario's prompts are the ones it sends; kept even
                    # when the events themselves no longer fit.
                    said = str((event["data"] or {}).get("instruction", ""))
                    run["prompt"] += ("\n\n" if run["prompt"] else "") + said
                if run["capture_truncated"]:
                    return
                if used + size > self.event_bytes:
                    run["capture_truncated"] = True
                    run["events"].append({
                        "sequence": event["sequence"], "time": event["time"],
                        "kind": "capture_limit", "data": {
                            "message": "The transcript reached its limit; later "
                                       "events were not kept.",
                            "limit_bytes": self.event_bytes}})
                    return
                used += size
                run["events"].append(event)

        try:
            if custom:
                result = self.app.agent.handle_message(
                    message, conversation_id, origin="chat_debugger", debug=capture)
                checks = [{"name": "The chat answered without an error",
                           "passed": bool(result.get("answer")) and not result.get("error"),
                           "detail": result.get("error")
                           or "The configured model answered this prompt."}]
            else:
                # A folder of its own under this application's data root -
                # never the system's temporary folder - deleted afterwards.
                where = (scratch_folder(self.app)
                         if getattr(self.app, "paths", None) is not None else None)
                outcome = run_test(test_id, capture, where=where)
                result, checks = outcome["result"], outcome["checks"]
            # The result without what the events already hold in full.
            kept = {key: result.get(key) for key in (
                "task_id", "trace_id", "conversation_id", "message_id", "answer",
                "classification", "error", "duration_seconds")}
            with self._lock:
                run["result"] = _redact(kept)
                if custom:
                    run["conversation_id"] = (run["result"].get("conversation_id")
                                              or run["conversation_id"])
                run["checks"] = _redact(checks)
                passed = bool(checks) and all(check.get("passed") for check in checks)
                run["status"] = "passed" if passed else "failed"
                run["error"] = None if passed else _redact(result.get("error"))
        except Exception as exc:
            capture("error", {"error": f"{type(exc).__name__}: {exc}"})
            with self._lock:
                run["status"] = "error"
                run["error"] = SECRETS.scrub(f"{type(exc).__name__}: {exc}")
        finally:
            try:
                with self._lock:
                    run["duration_seconds"] = round(time.monotonic() - started, 3)
                    self._persist(run)
            finally:
                self._running.release()
        return self.detail(run["id"])

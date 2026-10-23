"""THE LEARNING, RUN: updates, their clock, and what the Learning panel shows.

AN UPDATE reads everything up to its CUTOFF:

  1. the units: the user's Work projects (each a unit of its own), and the
     places they listed - each folder mapped into units (`folders.py`), each
     folder the record folders bring a unit - and the rules every agent is
     given (`~/.claude/CLAUDE.md`, Codex's AGENTS.md);
  2. read in this order: the units with conversations or rules first, then
     the most recently changed. A unit read for the first time goes back
     `reading.RECENT_DAYS` first; what is older is read at the end;
  3. the reasons (`reasons.py`) - once all is read.

ONE UPDATE AT A TIME. Its cutoff stays what it was when it began, through
a pause, a day's limit or the application closing: it carries on where it
stopped. PAUSE stops it until Continue. THE DAY'S LIMIT (`learning_daily_tokens`,
counted over every learning call today, this computer's day) makes it wait
for the next day. Update now starts one with the cutoff now - not while one
is open.

THE CLOCK (`learning_auto_on`, `learning_times`): updates by themselves only
when the switch is on AND at least one time is set (both off and empty at
first), and the Work learning module is on. A time that passes while the
computer was off starts one update when it is back, its cutoff THAT time.
A time that passes while an update is still open is CANCELLED - said on the
update - and the next update waits for the next time.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .ask import Asker
from .places import Places
from .reading import UnitReader
from .reasons import Reasons
from .store import LearningStore, now
from .vocab import Vocabulary

log = logging.getLogger(__name__)

AUTO_ON = "learning_auto_on"
TIMES = "learning_times"
DAILY_TOKENS = "learning_daily_tokens"
DEFAULT_DAILY_TOKENS = 3_000_000
#: The latest scheduled time already started or cancelled.
LAST_SLOT = "learning_last_slot"
LOOKS_EVERY = 60


def _unit_id(kind: str, key: str) -> str:
    return f"{kind}:" + hashlib.sha1(key.lower().encode("utf-8")).hexdigest()[:12]


class Stop(Exception):
    pass


class Learning:
    def __init__(self, db: Any, config: Any, models: Any, data_dir: Any,
                 never: Sequence[Any] = ()) -> None:
        self.db = db
        self.config = config
        self.models = models
        self.data_dir = Path(data_dir)
        self.never = [Path(one) for one in never if one]
        self.store = LearningStore(db)
        self.vocab = Vocabulary(db)
        self.places = Places(config, self.never)
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._clock: Optional[threading.Thread] = None
        self._sessions_cache: Optional[List[Any]] = None

    # -- settings -------------------------------------------------------------
    def times(self) -> List[str]:
        from ..heartbeat.connections.schedule import parse_times
        return parse_times(self.config.get(TIMES, "") or "")

    def auto_on(self) -> bool:
        return bool(self.config.get(AUTO_ON, False))

    def daily_tokens(self) -> int:
        try:
            return max(0, int(self.config.get(DAILY_TOKENS, DEFAULT_DAILY_TOKENS)))
        except (TypeError, ValueError):
            return DEFAULT_DAILY_TOKENS

    def set_auto(self, on: Optional[bool] = None, times: Optional[str] = None,
                 daily_tokens: Optional[int] = None) -> None:
        if on is not None:
            self.config.set(AUTO_ON, bool(on), internal=True)
        if times is not None:
            from ..heartbeat.connections.schedule import parse_times
            self.config.set(TIMES, ", ".join(parse_times(times)), internal=True)
        if daily_tokens is not None:
            self.config.set(DAILY_TOKENS, max(0, int(daily_tokens)), internal=True)
        # Times already passed when the clock was set are not "missed".
        latest = self._latest()
        if latest is not None:
            self.config.set(LAST_SLOT, latest.isoformat(), internal=True)

    def _latest(self, moment: Optional[datetime] = None) -> Optional[datetime]:
        from ..heartbeat.connections.schedule import latest_time
        return latest_time(self.times(), moment or datetime.now().astimezone())

    # -- spending -------------------------------------------------------------
    def tokens_today(self) -> int:
        midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0,
                                                       microsecond=0)
        row = self.db.query_one(
            "SELECT COALESCE(SUM(total_tokens), 0) AS n FROM model_calls "
            "WHERE task LIKE 'learning%' AND created_at >= ?",
            (midnight.astimezone(timezone.utc).isoformat(),))
        return int(row["n"]) if row else 0

    def _over_limit(self) -> bool:
        cap = self.daily_tokens()
        return bool(cap) and self.tokens_today() >= cap

    def spending(self, days: int = 30) -> List[Dict[str, Any]]:
        """Each day's learning calls and tokens, by what they were for."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            rows = self.db.query(
                "SELECT substr(created_at, 1, 10) AS day, task, COUNT(*) AS calls, "
                "COALESCE(SUM(total_tokens), 0) AS tokens, "
                "COALESCE(SUM(estimated_cost), 0) AS cost FROM model_calls "
                "WHERE task LIKE 'learning%' AND created_at >= ? GROUP BY day, task "
                "ORDER BY day", (since,))
        except Exception:
            return []
        return [dict(row) for row in rows]

    # -- the update -----------------------------------------------------------
    def open_update(self) -> Optional[Dict[str, Any]]:
        return self.store.open_update()

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def update_now(self) -> Dict[str, Any]:
        """The user's button: an update up to now. Refused while one is open."""
        with self._lock:
            if self.store.open_update():
                raise ValueError("an update is already open - pause or wait for it")
            if self._over_limit():
                raise ValueError("today's learning limit is used up")
            update_id = self.store.save_update({"cutoff": now(), "by": "you", "state": "running",
                                                "plan": {}})
        self._start(update_id)
        return self.status()

    def pause(self) -> Dict[str, Any]:
        update = self.store.open_update()
        if update and update["state"] in ("running", "waiting"):
            update["state"] = "paused"
            self.store.save_update(update)
        return self.status()

    def resume(self) -> Dict[str, Any]:
        update = self.store.open_update()
        if update and update["state"] == "paused":
            update["state"] = "running"
            self.store.save_update(update)
            self._start(update["id"])
        return self.status()

    def _start(self, update_id: str) -> None:
        with self._lock:
            if self.running():
                return
            self._thread = threading.Thread(target=self._run, args=(update_id,),
                                            name="learning-update", daemon=True)
            self._thread.start()
        update = self.store.update(update_id)
        if update and update.get("by") == "you":
            from ..common.observability.started import while_running
            while_running(("learning",), self.running)

    def _stopped(self, update_id: str) -> bool:
        if self._stop.is_set():
            return True
        update = self.store.update(update_id)
        return not update or update["state"] != "running"

    def _run(self, update_id: str) -> None:
        try:
            self._go(update_id)
        except Stop:
            pass
        except Exception as exc:
            log.warning("a learning update failed", exc_info=True)
            update = self.store.update(update_id)
            if update and update["state"] == "running":
                update["state"] = "failed"
                update["note"] = str(exc)[:400]
                update["finished_at"] = now()
                self.store.save_update(update)

    def _asker(self, update_id: str) -> Asker:
        def stopped() -> bool:
            if self._stopped(update_id):
                return True
            if self._over_limit():
                update = self.store.update(update_id)
                if update and update["state"] == "running":
                    update["state"] = "waiting"
                    update["note"] = "today's learning limit is used up: carries on tomorrow"
                    self.store.save_update(update)
                return True
            return False
        return Asker(self.models, stopped=stopped)

    def _check(self, update_id: str) -> None:
        if self._stopped(update_id):
            raise Stop()

    def _go(self, update_id: str) -> None:
        self.vocab.ensure_fixed()
        update = self.store.update(update_id)
        if not update:
            return
        ask = self._asker(update_id)
        cutoff = update["cutoff"]
        reader = UnitReader(self.db, ask, self.data_dir / "learning" / "copies", self.places,
                            self.never)
        sessions = self.places.sessions()
        plan = update.get("plan") or {}
        if not plan.get("units"):
            plan = self._plan(update_id, ask, reader, sessions, cutoff)
            update = self.store.update(update_id)
            update["plan"] = plan
            update["total"] = sum(one["size"] for one in plan["units"])
            self.store.save_update(update)
        # Reading, unit by unit.
        for phase in ("recent", "older"):
            for one in plan["units"]:
                if one.get("done_" + phase) or (phase == "older" and not one.get("older")):
                    continue
                self._check(update_id)
                unit = self.store.unit(one["id"])
                if not unit:
                    one["done_" + phase] = True
                    continue
                update = self.store.update(update_id)
                update["now_reading"] = unit.get("title") or one["id"]
                self.store.save_update(update)
                pieces, marks = reader.pending(unit, cutoff, sessions, older=(phase == "older"))
                first = not unit.get("first_read")

                def progress(size: int) -> None:
                    current = self.store.update(update_id)
                    if current:
                        current["done"] = int(current.get("done") or 0) + size
                        self.store.save_update(current)

                said = reader.read(unit, pieces, marks, progress,
                                   stopped=lambda: ask.stopped and ask.stopped())
                self._check(update_id)
                if said.get("failed"):
                    one["failed"] = said["failed"]
                if first and phase == "recent":
                    unit = self.store.unit(one["id"]) or unit
                    unit["first_read"] = cutoff
                    unit["state"] = "read"
                    self.store.save_unit(unit)
                    one["older"] = bool((unit.get("marks") or {}).get("recent_from"))
                    one["first"] = True
                one["done_" + phase] = True
                update = self.store.update(update_id)
                update["plan"] = plan
                self.store.save_update(update)
        # The reasons, once everything is read.
        self._check(update_id)
        if not plan.get("reasoned"):
            update = self.store.update(update_id)
            update["now_reading"] = "reasons"
            self.store.save_update(update)
            first_learning = any(one.get("first") for one in plan["units"])
            report = Reasons(self.db, ask).run(first_learning,
                                               stopped=lambda: ask.stopped and ask.stopped())
            self._check(update_id)
            plan["reasoned"] = True
            plan["reasons"] = report
        update = self.store.update(update_id)
        update["plan"] = plan
        update["state"] = "done"
        update["now_reading"] = ""
        update["done"] = update.get("total") or update.get("done") or 0
        update["finished_at"] = now()
        if reader.removed:
            update["note"] = f"{reader.removed} secret-like strings were taken out before reading"
        self.store.save_update(update)

    def _plan(self, update_id: str, ask: Any, reader: UnitReader, sessions: List[Any],
              cutoff: str) -> Dict[str, Any]:
        """The units this update reads, in order, with how much each has."""
        from .folders import FolderMap
        units: List[Dict[str, Any]] = []
        fixed: Dict[str, str] = {}
        for project in self.places.work_projects(self.db):
            unit_id = _unit_id("work", project["id"])
            unit = self.store.unit(unit_id) or {"id": unit_id, "kind": "work",
                                                "key": project["id"], "marks": {}}
            unit.update({"title": project["title"], "summary": project["summary"],
                         "folders": project["folders"]})
            self.store.save_unit(unit)
            units.append(unit)
            for folder in project["folders"]:
                fixed[folder] = project["title"]
        mapper = FolderMap(self.store, ask, self.never, fixed,
                           sessions_in=lambda folder: self.places.sessions_in(folder, sessions))
        for place in self.places.listed():
            if place["kind"] != "folder" or self.places._left_out(place["path"]):
                continue
            self._check(update_id)
            current = self.store.update(update_id)
            current["now_reading"] = "mapping " + place["path"]
            self.store.save_update(current)
            for found in mapper.map(place["path"]):
                unit_id = _unit_id("place", found["key"])
                unit = self.store.unit(unit_id) or {"id": unit_id, "kind": "place",
                                                    "key": found["key"], "marks": {}}
                unit.update({key: found.get(key) for key in ("title", "summary", "folders")})
                unit["only_files"] = bool(found.get("only_files"))
                unit["leave_out"] = found.get("leave_out") or []
                self.store.save_unit(unit)
                units.append(unit)
        for found in self.places.found_folders():
            unit_id = _unit_id("place", found["path"])
            if any(one["id"] == unit_id for one in units):
                continue
            unit = self.store.unit(unit_id) or {"id": unit_id, "kind": "place",
                                                "key": found["path"], "marks": {}}
            unit.update({"title": Path(found["path"]).name,
                         "summary": "found through " + " and ".join(found["via"]),
                         "folders": [found["path"]]})
            self.store.save_unit(unit)
            units.append(unit)
        for found in self.places.work_tab():
            unit_id = _unit_id("place", found["path"])
            if any(one["id"] == unit_id for one in units):
                continue
            unit = self.store.unit(unit_id) or {"id": unit_id, "kind": "place",
                                                "key": found["path"], "marks": {}}
            unit.update({"title": found.get("project") or Path(found["path"]).name,
                         "summary": "a project's folder in Work", "folders": [found["path"]]})
            self.store.save_unit(unit)
            units.append(unit)
        if self.places.record_tools() or units:
            unit_id = "rules:global"
            unit = self.store.unit(unit_id) or {"id": unit_id, "kind": "rules", "key": "global",
                                                "marks": {}}
            unit.update({"title": "Rules for every agent", "summary": "", "folders": []})
            self.store.save_unit(unit)
            units.append(unit)
        # Units no longer listed are marked gone - kept, not read.
        wanted = {one["id"] for one in units}
        for old in self.store.units():
            if old["id"] not in wanted and old["state"] != "gone":
                old["state"] = "gone"
                self.store.save_unit(old)
        # How much each has, and the order - looked at without keeping a
        # thing (`dry`): what is read is kept when it is read.
        planned = []
        reader.dry = True
        for unit in units:
            self._check(update_id)
            pieces, _marks = reader.pending(unit, cutoff, sessions)
            has_talk = any(piece.mark[0] in ("sessions", "rules") for piece in pieces)
            latest = max((piece.at for piece in pieces), default="")
            planned.append({"id": unit["id"], "title": unit.get("title", ""),
                            "size": reader.size(pieces), "talk": has_talk, "latest": latest})
        reader.dry = False
        # Conversations and rules first; within each, the latest first.
        planned.sort(key=lambda one: one["latest"], reverse=True)
        planned.sort(key=lambda one: not one["talk"])
        return {"units": planned}

    # -- the clock ------------------------------------------------------------
    def look(self, moment: Optional[datetime] = None) -> None:
        from ..common.modules import enabled as module_on
        moment = moment or datetime.now().astimezone()
        update = self.store.open_update()
        # An open update carries on: after a restart, or the next day.
        if update and not self.running():
            if update["state"] == "running" or (update["state"] == "waiting"
                                                and not self._over_limit()):
                update["state"] = "running"
                self.store.save_update(update)
                self._start(update["id"])
        if not self.auto_on() or not self.times() or not module_on(self.config, "experience"):
            return
        latest = self._latest(moment)
        if latest is None:
            return
        last = str(self.config.get(LAST_SLOT, "") or "")
        if last and last >= latest.isoformat():
            return
        self.config.set(LAST_SLOT, latest.isoformat(), internal=True)
        if update:
            update["skipped"] = list(update.get("skipped") or []) + [latest.isoformat()]
            self.store.save_update(update)
            return
        if self._over_limit():
            return
        update_id = self.store.save_update({"cutoff": latest.astimezone(timezone.utc).isoformat(),
                                            "by": "schedule", "state": "running", "plan": {}})
        self._start(update_id)

    def begin(self) -> None:
        if self._clock is not None and self._clock.is_alive():
            return
        self._stop.clear()
        self._clock = threading.Thread(target=self._loop, name="learning-clock", daemon=True)
        self._clock.start()

    def halt(self) -> None:
        self._stop.set()
        for thread in (self._clock, self._thread):
            if thread is not None:
                thread.join(5)

    def _loop(self) -> None:
        while not self._stop.wait(LOOKS_EVERY):
            try:
                self.look()
            except Exception:                  # pragma: no cover - looked at again
                log.warning("the learning clock could not look", exc_info=True)

    # -- the panel ------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        from ..heartbeat.connections.schedule import next_time
        moment = datetime.now().astimezone()
        update = self.store.open_update() or self.store.last_update()
        upcoming = next_time(self.times(), moment) if self.auto_on() else None
        listed = self.places.listed()
        excluded = self.places.excluded()
        out = {
            "on": self.auto_on(), "times": self.times(),
            "time_zone": moment.strftime("%Z (UTC%z)"),
            "next": upcoming.isoformat() if upcoming else "",
            "daily_tokens": self.daily_tokens(), "tokens_today": self.tokens_today(),
            "over_limit": self._over_limit(),
            "update": None,
            "places": listed,
            "found": [dict(one, excluded=False) for one in self.places.found_folders(wait=False)]
            + [{"path": one, "excluded": True} for one in excluded],
            "records": {"claude": records_root_exists("claude"),
                        "codex": records_root_exists("codex")},
            "counts": self.store.counts(),
            "breakdown": self.store.breakdown(),
            "spending": self.spending(),
        }
        if update:
            total = int(update.get("total") or 0)
            done = int(update.get("done") or 0)
            out["update"] = {
                "id": update["id"], "cutoff": update["cutoff"], "by": update["by"],
                "state": update["state"], "now_reading": update.get("now_reading", ""),
                "share": (min(1.0, done / total) if total else (1.0 if update["state"] == "done" else 0.0)),
                "units": len((update.get("plan") or {}).get("units") or []),
                "units_done": sum(1 for one in (update.get("plan") or {}).get("units") or []
                                  if one.get("done_recent")),
                "skipped": update.get("skipped") or [], "note": update.get("note", ""),
                "started_at": update.get("started_at", ""),
                "finished_at": update.get("finished_at", "")}
        return out


def records_root_exists(tool: str) -> bool:
    from . import records
    root = records.claude_sessions_root() if tool == "claude" else records.codex_sessions_root()
    return root.is_dir()

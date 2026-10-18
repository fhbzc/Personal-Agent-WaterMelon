"""WORK LEARNING, UPDATED BY ITSELF: at the times of day the user chose
(`experience_auto_times`, "03:00" by default - several may be given, none at
all for never), in this computer's time zone, every project that has changed
since it was last learned is updated - as its Update learning button would.

A project is two things: its STATES (the tasks it is made of) and its
FOLDERS. It has changed when one of its tasks has states not yet learned,
or a file in one of its folders - or one of its conversations with a coding
agent - is newer than how far the folder was last read. Asked of the record
and the file system alone, no model.

EACH PROJECT SAYS WHETHER: on by default; one the user turns off is left to
its button (`experience_auto_off`). A project the user removed is never
learned again. A project made for the agents is never learned at all.

ONE RUN AT A TIME, AND IT IS FINISHED. While a run is going, the projects'
own buttons do not start learning - two learnings of one record at once
would each read what the other is writing. The run is kept in the settings
(`experience_auto_run`) as it goes; stopped by the application closing, it
carries on when the application starts again, each project from where its
learning stopped. Stopped on one project by the user, that project is done
for this run.

WHEN THE RUN IS OVER and its projects wrote natures of the user's work, the
patterns and principles are built again from them at once - the state
heartbeat's step for it, asked for now rather than when enough new ones
have come in.

Times missed while the computer was off make ONE run when it is back, as an
issue of the Newspaper does (connections/schedule.py, whose times these are
read like).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..connections.schedule import latest_time, next_time, parse_times

log = logging.getLogger(__name__)

TIMES = "experience_auto_times"
DEFAULT_TIMES = "03:00"
#: The projects the user turned automatic updating off for.
OFF = "experience_auto_off"
#: The run going now - kept so a run the application's closing cut short is
#: carried on when it starts again.
RUN = "experience_auto_run"
#: The time of day whose run was last started: not started twice.
LAST = "experience_auto_last"
#: How often the clock looks.
LOOKS_EVERY = 60


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _when(value: Any) -> Optional[datetime]:
    try:
        said = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return said if said.tzinfo else said.replace(tzinfo=timezone.utc)


class LearningClock:
    """Updates the changed projects' learning when it is due (module
    docstring)."""

    def __init__(self, config: Any, experience: Any) -> None:
        self.config = config
        self.experience = experience
        experience.auto_clock = self
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        #: The run's projects taken on by this process - one not yet taken
        #: on is one a closing cut short, and is carried on.
        self._taken: set = set()

    # -- what the user chose ----------------------------------------------------------------
    def times(self) -> List[str]:
        return parse_times(self.config.get(TIMES, DEFAULT_TIMES))

    def set_times(self, text: str) -> List[str]:
        times = parse_times(text)
        self.config.set(TIMES, ", ".join(times), internal=True)
        return times

    def off(self) -> List[str]:
        return [str(one) for one in (self.config.get(OFF) or []) if str(one)]

    def opted_in(self, unit_id: str) -> bool:
        return unit_id not in self.off()

    def set_opted(self, unit_id: str, on: bool) -> None:
        off = [one for one in self.off() if one != unit_id]
        if not on:
            off.append(unit_id)
        self.config.set(OFF, off, internal=True)

    # -- the run --------------------------------------------------------------------------------
    def run(self) -> Dict[str, Any]:
        said = self.config.get(RUN) or {}
        return dict(said) if isinstance(said, dict) else {}

    def running(self) -> bool:
        return bool(self.run().get("units"))

    def changed(self, unit: Dict[str, Any]) -> bool:
        """New states in its tasks, or something newer in its folders than
        how far they were read (module docstring)."""
        if int(unit.get("pending") or 0) > 0:
            return True
        paths = [one for one in (unit.get("paths") or [unit.get("workspace")]) if one]
        if not paths:
            return False
        since = str(unit.get("processed_until") or "")
        if not since:
            return True
        try:
            from .workspace import open_workspace
            place = open_workspace(paths)
            if place.files_changed(since, _iso_now()):
                return True
            after = _when(since)
            for talk in place.conversations():
                stamp = datetime.fromtimestamp(Path(talk).stat().st_mtime, timezone.utc)
                if after is None or stamp > after:
                    return True
        except Exception:                     # pragma: no cover - looked at next time
            log.debug("could not look at %s's folders", unit.get("id"), exc_info=True)
        return False

    def wanted(self) -> List[str]:
        """The projects a run would update now."""
        out = []
        for unit in self.experience.units():
            if unit.get("kind") == "new project" or not self.opted_in(unit["id"]):
                continue
            if self.changed(unit):
                out.append(unit["id"])
        return out

    def due(self, now: Optional[datetime] = None) -> bool:
        from ...common.modules import enabled as module_on
        if not module_on(self.config, "experience"):
            return False
        now = now or datetime.now().astimezone()
        latest = latest_time(self.times(), now)
        if latest is None:
            return False
        last = _when(self.config.get(LAST) or "")
        return last is None or last < latest

    def start_run(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """A run, now: the changed projects, one after another as there is
        room - each from where its learning stopped."""
        now = now or datetime.now().astimezone()
        with self._lock:
            if self.running():
                return self.run()
            latest = latest_time(self.times(), now) or now
            self.config.set(LAST, latest.isoformat(), internal=True)
            units = self.wanted()
            if not units:
                return {}
            said = {"for": latest.isoformat(), "started": _iso_now(),
                    "units": units, "finished": []}
            self.config.set(RUN, said, internal=True)
            self._taken = set()
        self.follow()
        return self.run()

    def follow(self) -> None:
        """The run, carried on: a project not taken on by this process is
        (the application closed under it); one that has ended is finished;
        all finished, the run is over. Not while the application closes -
        then what was cut short is carried on next time."""
        if self._stop.is_set() or getattr(self.experience, "_stopping", False):
            return
        with self._lock:
            run = self.run()
            if not run.get("units"):
                return
            finished = list(run.get("finished") or [])
            going = self.experience.learning_progress
            resume = []
            for unit_id in run["units"]:
                if unit_id in finished:
                    continue
                mine = going.get(unit_id) or {}
                if mine.get("running") or mine.get("queued"):
                    continue
                if unit_id in self._taken:
                    finished.append(unit_id)
                else:
                    resume.append(unit_id)
            if resume:
                taken = self.experience.learn(resume, auto=True)
                self._taken.update(resume)
                # One that cannot be learned (removed since, say) is done.
                finished += [one for one in resume if one not in taken]
            if all(one in finished for one in run["units"]):
                self.config.set(RUN, {}, internal=True)
                regrouped = self._regroup_after(run)
                self.config.set("experience_auto_done", {
                    "for": run.get("for", ""), "started": run.get("started", ""),
                    "ended": _iso_now(), "units": run["units"],
                    "regrouped": regrouped}, internal=True)
            else:
                run["finished"] = finished
                self.config.set(RUN, run, internal=True)

    def _regroup_after(self, run: Dict[str, Any]) -> int:
        """THE RUN OVER, ITS NATURES GROUPED: when the projects learned wrote
        natures of the user's work, their patterns and principles are built
        again at once - the state heartbeat's step, not waited for until
        enough new ones have come in. How many new ones there were."""
        started = str(run.get("started") or "")
        try:
            from .patterns import nature_records
            kinds = self.experience.store.nature_kinds()
            new = [one for one in nature_records(self.experience.library)
                   if one.created_at.isoformat() > started
                   and kinds.get(one.id) != "personal"]
        except Exception:                 # pragma: no cover - grouped next round
            log.warning("could not count the natures the run wrote", exc_info=True)
            return 0
        if new:
            self.experience._regroup_now = True
            self.experience.start_patterns()
        return len(new)

    def look(self, now: Optional[datetime] = None) -> None:
        if self.running():
            self.follow()
        elif self.due(now):
            self.start_run(now)

    def status(self) -> Dict[str, Any]:
        now = datetime.now().astimezone()
        upcoming = next_time(self.times(), now)
        run = self.run()
        return {"times": self.times(), "time_zone": now.strftime("%Z (UTC%z)"),
                "next": upcoming.isoformat() if upcoming else "",
                "running": bool(run.get("units")),
                "units": list(run.get("units") or []),
                "finished": list(run.get("finished") or []),
                "off": self.off(),
                "last": dict(self.config.get("experience_auto_done") or {})}

    # -- the loop ------------------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="learning-clock",
                                        daemon=True)
        self._thread.start()

    def halt(self) -> None:
        """Told to stop, without waiting: from now it follows nothing - a
        run the closing cuts short is carried on at the next start."""
        self._stop.set()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(5)

    def _loop(self) -> None:
        # A minute after start-up first - a run cut short by closing, too.
        while not self._stop.wait(LOOKS_EVERY):
            try:
                self.look()
            except Exception:                      # pragma: no cover - looked at again
                log.warning("the learning clock could not look", exc_info=True)


__all__ = ["LearningClock", "TIMES", "DEFAULT_TIMES", "OFF", "RUN"]

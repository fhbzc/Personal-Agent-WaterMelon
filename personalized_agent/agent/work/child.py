"""EACH RUN IN A PROCESS OF ITS OWN: the user's work done away from the agent.

A run of the user's work is long, calls models for hours, writes files and
starts code. Done inside the agent's own process it shared its memory and its
time with the listeners, the heartbeats, the page and the avatar on the
desktop - a run that went wrong could take the agent with it, and a heavy one
made the rest of it slow. So each run is a CHILD PROCESS of its own:

* the agent asks for it (`WorkRuns.start` / `continue_run`) and the child
  does the whole of it - the checks, a rollback, the run itself - with the
  same code, in an application of its own built on the same root (no
  listeners, heartbeats, page or desktop: only what a run needs);
* it writes what it always wrote - its events to the run's session log as
  they happen, its record to the database - and, every half second, a
  SNAPSHOT of where it stands; the page is shown the run from those, as it
  is shown a run from before the agent last started;
* what the user does to it - pause, resume, stop, an answer to a request -
  is written to its CONTROL file, which it reads twice a second;
* it starts at a lower priority than the agent's own, and the governor
  (governor.py) lowers it further, or pauses it, when the computer is busy;
* it ends when the agent ends (a job object on Windows, a watcher elsewhere,
  common/children.py): a run is never left going with nobody to show it.

THE EXPERIENCE DEBUGGER'S WAYS go in a process of their own too - slowed
no more by the agent, nor it by them. Each seals the application's own code
from the agents' code while it goes (`WorkRuns.hold_code`): the AGENT holds
the seal for it, from before its process starts (waiting until it is on
disk) until that process ends, and counts every way that goes - the child
is told the code is held for it (isolation.CodeSeal.held_elsewhere) and
never changes the seal itself. It finds its way of its project by the
way's own id (debug_projects.py).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: The first argument that makes the program a run's child (see __main__).
CHILD_FLAG = "--work-child"
#: How long the agent waits for a child to say it started, or why not -
#: while it lives (one that ends says why at once). Before it says, a child
#: does what its run does first: a rollback of a folder of thirteen thousand
#: files took two minutes, and at 90 seconds the agent gave up on a child
#: that went on - a run going that the page could neither follow nor stop.
STARTING_SECONDS = 30 * 60
#: How often the child writes where it stands, and reads what it is told.
BEAT_SECONDS = 0.5

SPEC, REPLY, SNAPSHOT, CONTROL, LOG = ("spec.json", "reply.json", "snapshot.json",
                                       "control.jsonl", "child.log")


def children_dir(paths: Any) -> Path:
    """Where each child's spec, reply, snapshot and control live."""
    return Path(paths.data_dir) / "work_children"


def command(spec_path: Path) -> List[str]:
    """How the program is started as a run's child: the packaged program
    itself, or this Python with the package - found where this one was, not
    in the folder the agent happened to start in (`-m` looked there: an
    agent started by a double click on run.py, or at sign-in, is in
    System32, and every run's child failed on "No module named")."""
    if getattr(sys, "frozen", False):
        return [sys.executable, CHILD_FLAG, str(spec_path)]
    home = str(Path(__file__).resolve().parents[3])
    boot = ("import sys; sys.path.insert(0, sys.argv.pop(1)); "
            "from personalized_agent.__main__ import main; sys.exit(main())")
    return [sys.executable, "-c", boot, home, CHILD_FLAG, str(spec_path)]


def _write_json(path: Path, value: Any) -> None:
    """Whole or not at all: written beside, then put in place."""
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, default=str),
                         encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:               # read at this moment (Windows)
            time.sleep(0.02 * (attempt + 1))
    temporary.unlink(missing_ok=True)


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    for _attempt in range(5):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (ValueError, PermissionError, OSError):
            time.sleep(0.02)
    return None


# -- the agent's side -------------------------------------------------------------------------
class ChildRun:
    """A run going in a child process, as the agent holds it: what `Run`
    offers the rest of the agent, answered from the child's snapshot and
    session log, and told through its control file."""

    def __init__(self, owner: Any, folder: Path, process: Any, reply: Dict[str, Any],
                 unit: Dict[str, Any]) -> None:
        self.owner = owner
        self.dir = folder
        self.process = process
        self.id = str(reply["run"])
        self.unit = unit
        self.debug = bool(unit.get("debug"))
        self.stopping = False
        self.stop_now = False
        self.started_at = time.time()

    # -- what the child says -------------------------------------------------------------
    def _snap(self) -> Dict[str, Any]:
        return read_json(self.dir / SNAPSHOT) or {}

    def alive(self) -> bool:
        if self.process is not None and self.process.poll() is not None:
            return False
        said = self._snap()
        return bool(said.get("alive", True))

    @property
    def pid(self) -> int:
        return int(getattr(self.process, "pid", 0) or 0)

    @property
    def status(self) -> str:
        return str(self._snap().get("status") or "running")

    @property
    def plan(self) -> Dict[str, Any]:
        said = self._snap()
        return {"phase": said.get("phase", ""), "round": said.get("round")}

    @property
    def used(self) -> Dict[str, Any]:
        return dict(self._snap().get("used") or {})

    @property
    def pause_asked(self) -> bool:
        return bool((self._snap().get("session") or {}).get("progress", {}).get("pause_asked"))

    @property
    def activity(self) -> str:
        return str(self._snap().get("activity") or "")

    @property
    def _model_ask(self) -> Optional[Dict[str, Any]]:
        return self._snap().get("model_ask")

    def _minutes(self) -> float:
        return float(self._snap().get("minutes") or 0.0)

    def live_state(self) -> str:
        return str(self._snap().get("state") or "going")

    def waiting_for(self) -> str:
        return str(self._snap().get("waiting_for") or "")

    def session(self, since: int = 0, calls: int = 0) -> Dict[str, Any]:
        """As `Run.session`: the events after `since` from the session log the
        child writes as it goes, the rest from its snapshot."""
        from .runner import read_session
        said = dict(self._snap().get("session") or {})
        log_dir = self.dir_of_logs()
        events = [one for one in (read_session(log_dir) if log_dir else [])
                  if int(one.get("seq") or 0) > since]
        said["events"] = events[-200:]
        said["last"] = max([since, *[int(one["seq"]) for one in events]])
        said["run"] = self.id
        from .runner import calls_after
        said["calls"] = calls_after({"calls": self._snap().get("calls") or []}, calls)
        said["state"] = self.live_state() if self.alive() else str(said.get("state") or "ended")
        return said

    def dir_of_logs(self) -> Optional[Path]:
        row = self.owner.run(self.id)
        return self.owner.log_dir_of(row) if row and row.get("folder") else None

    # -- what the user does --------------------------------------------------------------
    def _tell(self, what: str, **said: Any) -> bool:
        if not self.alive():
            return False
        entry = {"id": uuid.uuid4().hex[:10], "at": time.time(), "do": what, **said}
        with open(self.dir / CONTROL, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True

    def pause(self, why: str = "") -> bool:
        return self._tell("pause", why=why)

    def resume(self, why: str = "") -> bool:
        return self._tell("resume", why=why)

    def release(self, now: bool = False) -> None:
        self._tell("stop", now=bool(now or self.stop_now))

    def answer(self, request_id: str, allow: bool) -> Optional[Dict[str, Any]]:
        self._tell("answer", request=request_id, allow=bool(allow))
        return {"id": request_id, "status": "answered", "allow": bool(allow)}


def spawn_draft(owner: Any, draft_id: str) -> None:
    """A piece of work handed over: its context collected and its plan
    drafted in a process of its own (delegate.py) - not waited for: the page
    follows the draft as it is written."""
    from ...common.children import popen
    import subprocess
    folder = children_dir(owner.paths) / ("draft-" + draft_id)
    folder.mkdir(parents=True, exist_ok=True)
    _write_json(folder / SPEC, {"kind": "draft", "root": str(owner.paths.root),
                                "draft": draft_id, "agent_pid": os.getpid()})
    flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
             | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)) \
        if sys.platform == "win32" else 0
    with open(folder / LOG, "ab") as out:
        popen(command(folder / SPEC), stdout=out, stderr=out, stdin=subprocess.DEVNULL,
              **({"creationflags": flags} if flags else {}))


def launch(owner: Any, kind: str, unit: Dict[str, Any], **args: Any) -> Dict[str, Any]:
    """Start a child for a run and wait for its word: the run started (the
    agent then holds it as a `ChildRun`), or why not."""
    base = children_dir(owner.paths)
    folder = base / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True, exist_ok=True)
    held = _hold_code(owner, unit)
    spec = {"kind": kind, "root": str(owner.paths.root), "unit": unit.get("id"),
            "args": args, "agent_pid": os.getpid(), "code_held": held is not None}
    _write_json(folder / SPEC, spec)
    spawned: List[Any] = []
    try:
        return _start_child(owner, folder, unit, held, spawned)
    finally:
        # Given back as its process ends; or now, if none ever started.
        if not spawned:
            _let_code_go(held)


def _hold_code(owner: Any, unit: Dict[str, Any]) -> Optional[Path]:
    """A debugger way: the application's code sealed for it before its
    process starts - None when there is nothing to hold."""
    root = owner.code_to_hold(unit) if hasattr(owner, "code_to_hold") else None
    if root is None:
        return None
    from ...runtime.isolation import CodeSeal
    CodeSeal.hold(root)
    if not CodeSeal.wait(root, timeout=120.0):
        log.warning("the application's code is not sealed yet: the guard alone "
                    "keeps this way's code from reading it")
    return root


def _let_code_go(held: Optional[Path]) -> None:
    if held is not None:
        from ...runtime.isolation import CodeSeal
        CodeSeal.release(held)


def _release_when_ended(process: Any, held: Optional[Path]) -> None:
    """The seal given back as the way's process ends, however it ends."""
    if held is None:
        return
    import threading

    def wait() -> None:
        try:
            process.wait()
        finally:
            _let_code_go(held)
    threading.Thread(target=wait, name="code-held-for-way", daemon=True).start()


def _start_child(owner: Any, folder: Path, unit: Dict[str, Any],
                 held: Optional[Path], spawned: List[Any]) -> Dict[str, Any]:
    from ...common.children import popen
    flags = 0
    if sys.platform == "win32":
        import subprocess
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                 | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    with open(folder / LOG, "ab") as out:
        process = popen(command(folder / SPEC), stdout=out, stderr=out,
                        stdin=__import__("subprocess").DEVNULL,
                        **({"creationflags": flags} if flags else {}))
    spawned.append(process)
    _release_when_ended(process, held)
    if sys.platform != "win32":
        try:
            os.setpriority(os.PRIO_PROCESS, process.pid, 5)
        except Exception:                          # pragma: no cover - best effort
            pass
    deadline = time.monotonic() + STARTING_SECONDS
    while time.monotonic() < deadline:
        reply = read_json(folder / REPLY)
        if reply is not None:
            if reply.get("error") or not reply.get("run"):
                return {"error": str(reply.get("error") or "the run did not start")}
            child = ChildRun(owner, folder, process, reply, unit)
            return {"child": child, **{k: v for k, v in reply.items() if k != "kind"}}
        if process.poll() is not None:
            said = (folder / LOG).read_text(encoding="utf-8", errors="replace")[-600:]
            return {"error": f"the run's process ended before it started: {said.strip()}"}
        time.sleep(0.2)
    # Not left going unheld: ended, with what it started.
    from ...common.children import end_tree
    end_tree(process)
    return {"error": "the run's process did not start in time"}


# -- the child's side --------------------------------------------------------------------------
def child_main(argv: List[str]) -> int:
    """The program started as a run's child: build the application on the
    agent's root, do the run asked for, and say where it stands until it
    ends."""
    spec_path = Path(argv[0])
    folder = spec_path.parent
    spec = read_json(spec_path) or {}
    logging.basicConfig(level=logging.INFO, filename=str(folder / LOG),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    began = time.monotonic()
    try:
        from ...app import AgentApplication
        app = AgentApplication(root=Path(spec["root"]), configure_logs=False)
        # How long each part of starting takes, in its log: the full way took
        # two minutes to start (2026-10-23), and nothing said where they went.
        log.info("started in %.1fs: the application built", time.monotonic() - began)
        work = app.work
        work.in_subprocess = False            # here, the run is in this process
        work.code_held_elsewhere = bool(spec.get("code_held"))
        if spec.get("kind") == "draft":
            app.drafts.collect_and_plan(str(spec.get("draft") or ""))
            return 0
        unit = _unit_of(app, str(spec.get("unit") or ""))
        log.info("started in %.1fs: the work found", time.monotonic() - began)
        if unit is None:
            _write_json(folder / REPLY, {"error": "no such project or task"})
            return 2
        args = dict(spec.get("args") or {})
        if spec.get("kind") == "continue":
            said = work.continue_run(str(args.pop("run_id")), unit, **args)
        else:
            said = work.start(unit, **args)
        if said.get("error"):
            _write_json(folder / REPLY, {"error": said["error"]})
            return 2
        run = work._running.get(str(said["run"]))
        log.info("started in %.1fs: the run going", time.monotonic() - began)
        _write_json(folder / REPLY, {k: v for k, v in said.items()})
    except Exception as exc:                  # said, rather than lost
        log.exception("the run could not start")
        _write_json(folder / REPLY, {"error": f"the run could not start: {exc}"})
        return 1
    _follow(run, folder, int(spec.get("agent_pid") or 0))
    return 0


def _unit_of(app: Any, unit_id: str) -> Optional[Dict[str, Any]]:
    """The work a run is of: a way of a debugger project, or the user's."""
    from .debug_projects import DebugProjects, parse_unit
    way = parse_unit(unit_id)
    if way is not None:
        return DebugProjects(app.work).unit(*way)
    return app.state_heartbeat.experience.unit(unit_id)


def snapshot(run: Any) -> Dict[str, Any]:
    """Where a run stands, for the agent to show - without its events, which
    are in its session log."""
    session = run.session(since=10 ** 12, calls=10 ** 9)
    session.pop("events", None)
    plan = run.plan or {}
    return {"alive": run.alive(), "pid": os.getpid(), "at": time.time(),
            "status": run.status, "state": run.live_state(),
            "waiting_for": run.waiting_for(), "activity": getattr(run, "activity", ""),
            "minutes": round(run._minutes(), 2), "used": dict(run.used or {}),
            "phase": plan.get("phase", ""), "round": plan.get("round"),
            "model_ask": run._model_ask, "calls": plan.get("calls") or [],
            "session": session}


def _follow(run: Any, folder: Path, agent_pid: int) -> None:
    """Twice a second: write where the run stands, and do what it is told -
    until it ends, or the agent that asked for it does."""
    done: set = set()
    while True:
        try:
            _write_json(folder / SNAPSHOT, snapshot(run))
        except Exception:                      # pragma: no cover - next beat
            log.debug("could not write the snapshot", exc_info=True)
        for order in _orders(folder, done):
            _obey(run, order)
        if not run.alive():
            _write_json(folder / SNAPSHOT, dict(snapshot(run), alive=False))
            return
        if agent_pid and not _agent_alive(agent_pid):
            # The agent is gone: the run ends as it would have when the agent
            # closed - interrupted, to be continued from its last milestone.
            log.info("the agent ended; so does the run")
            os._exit(0)
        time.sleep(BEAT_SECONDS)


def _orders(folder: Path, done: set) -> List[Dict[str, Any]]:
    try:
        lines = (folder / CONTROL).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        try:
            order = json.loads(line)
        except ValueError:
            continue
        if order.get("id") in done:
            continue
        done.add(order.get("id"))
        out.append(order)
    return out


def _obey(run: Any, order: Dict[str, Any]) -> None:
    what = order.get("do")
    try:
        if what == "pause":
            run.pause(why=str(order.get("why") or ""))
        elif what == "resume":
            run.resume(why=str(order.get("why") or ""))
        elif what == "stop":
            run.stopping = True
            if order.get("now"):
                run.stop_now = True
            run.release(bool(order.get("now")))
        elif what == "answer":
            run.answer(str(order.get("request") or ""), bool(order.get("allow")))
    except Exception:                          # pragma: no cover - told again
        log.warning("could not do %s", what, exc_info=True)


def _agent_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:                          # pragma: no cover
        return True


__all__ = ["CHILD_FLAG", "ChildRun", "launch", "child_main", "children_dir", "snapshot"]

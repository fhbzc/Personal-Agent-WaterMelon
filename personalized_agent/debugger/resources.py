"""THE RESOURCE DEBUGGER: what this application costs the computer, as it
goes - for whoever develops it.

WHAT IS COUNTED is the application's own process and every process it
started, and what those started in turn: the embedding model's worker, the
coding agents a run of work calls (Claude Code, Codex), the code a run
executes, the browsers runs drive. Each is put under WHAT IT IS FOR - the
application itself, the embedding model, one of the user's runs of work, a
way of the Work debugger, a model call outside any run (a chat, a
heartbeat), or anything else - found from the process that started it, the
folder it runs in, or a run's folder named on its command line.

Every SAMPLE_SECONDS, while someone looks (the page asks at least every
IDLE_SECONDS; then it stops, and costs nothing):

* PROCESSORS: each process's share of the whole computer (all its cores);
* MEMORY: each process's resident memory, against what the computer has;
* GRAPHICS: on Windows, each process's share of the busiest engine of the
  graphics processor and its dedicated graphics memory, read from the
  system's own performance counters - as Task Manager shows them; on Linux
  with an NVIDIA card, from nvidia-smi; elsewhere said to be unmeasured.

The last KEEP_SECONDS are kept, added up by what each is for, to draw.

DISK: the application's own folder, part by part; the folders the user's
work runs in, and each Work debugger project's; and the models it fetched.
Measured behind the page - a walk of many files takes a while - when the
tab first asks, again every DISK_SECONDS while it is open, and when asked.

LEFT FROM BEFORE: processes an earlier run of the application left
(common/children.py `leftovers`), shown, with a way to end them.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

SAMPLE_SECONDS = 2.0
KEEP_SECONDS = 15 * 60
IDLE_SECONDS = 30.0
DISK_SECONDS = 10 * 60
LEFTOVER_SECONDS = 10.0
#: The parts of a folder shown, largest first; the rest added up.
PARTS_SHOWN = 8

#: What each process is for, in the order they are drawn - each always the
#: same colour on the page.
KINDS: List[Tuple[str, str]] = [
    ("app", "The application"),
    ("embedding", "Embedding model"),
    ("work", "Your work runs"),
    ("debugger", "Work debugger runs"),
    ("models", "Model calls outside a run"),
    ("other", "Everything else"),
]

_CODING_AGENTS = ("claude", "codex")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(text: str) -> str:
    # A path written inside Python's own quotes has its backslashes doubled.
    return os.path.normcase(str(text).replace("\\\\", "\\"))


# -- graphics ------------------------------------------------------------------------------------
class _WindowsGpu:
    """Windows' own GPU counters (as Task Manager reads them): each
    process's share of each engine, and its dedicated graphics memory."""

    ENGINE = r"\GPU Engine(*)\Utilization Percentage"
    MEMORY = r"\GPU Process Memory(*)\Dedicated Usage"

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.pdh = ctypes.WinDLL("pdh")
        self.query = ctypes.c_void_p()
        if self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)) != 0:
            raise OSError("could not open a performance query")
        self.engine = ctypes.c_void_p()
        self.memory = ctypes.c_void_p()
        for path, counter in ((self.ENGINE, self.engine), (self.MEMORY, self.memory)):
            if self.pdh.PdhAddEnglishCounterW(self.query, path, None,
                                              ctypes.byref(counter)) != 0:
                raise OSError(f"no counter {path}")

        class Value(ctypes.Structure):
            _fields_ = [("status", wintypes.DWORD), ("value", ctypes.c_double)]

        class Item(ctypes.Structure):
            _fields_ = [("name", ctypes.c_wchar_p), ("value", Value)]

        self.Item = Item
        self.pdh.PdhCollectQueryData(self.query)

    def _items(self, counter: Any) -> List[Tuple[str, float]]:
        ctypes = self.ctypes
        size, count = ctypes.c_ulong(0), ctypes.c_ulong(0)
        flags = 0x00000200 | 0x00008000           # a double, not capped at 100
        self.pdh.PdhGetFormattedCounterArrayW(counter, flags, ctypes.byref(size),
                                              ctypes.byref(count), None)
        if not size.value:
            return []
        buffer = (ctypes.c_byte * size.value)()
        if self.pdh.PdhGetFormattedCounterArrayW(counter, flags, ctypes.byref(size),
                                                 ctypes.byref(count), buffer) != 0:
            return []
        items = ctypes.cast(buffer, ctypes.POINTER(self.Item * count.value)).contents
        return [(one.name or "", float(one.value.value)) for one in items
                if one.value.status in (0, 1)]

    def read(self) -> Dict[str, Any]:
        self.pdh.PdhCollectQueryData(self.query)
        engines: Dict[int, Dict[str, float]] = {}
        whole: Dict[str, float] = {}
        for name, value in self._items(self.engine):
            pid = re.search(r"pid_(\d+)_", name)
            kind = re.search(r"engtype_(.*)$", name)
            if not pid:
                continue
            engine = kind.group(1) if kind else "?"
            mine = engines.setdefault(int(pid.group(1)), {})
            mine[engine] = mine.get(engine, 0.0) + value
            whole[engine] = whole.get(engine, 0.0) + value
        memory: Dict[int, float] = {}
        for name, value in self._items(self.memory):
            pid = re.search(r"pid_(\d+)_", name)
            if pid:
                memory[int(pid.group(1))] = memory.get(int(pid.group(1)), 0.0) + value
        return {"use": {pid: min(100.0, max(shares.values()))
                        for pid, shares in engines.items() if shares},
                "memory": memory,
                "whole": min(100.0, max(whole.values())) if whole else 0.0}


class _NvidiaGpu:
    """nvidia-smi, where it is: each process's graphics memory, and the
    card's own use - not each process's."""

    def __init__(self) -> None:
        import shutil
        self.tool = shutil.which("nvidia-smi")
        if not self.tool:
            raise OSError("no nvidia-smi")
        self.seen: Dict[str, Any] = {"use": {}, "memory": {}, "whole": 0.0}
        self.at = 0.0

    def read(self) -> Dict[str, Any]:
        if time.time() - self.at < 10:
            return self.seen
        self.at = time.time()
        from ..common import children
        try:
            apps = children.run([self.tool, "--query-compute-apps=pid,used_memory",
                                 "--format=csv,noheader,nounits"], capture_output=True,
                                text=True, timeout=5, short=True)
            card = children.run([self.tool, "--query-gpu=utilization.gpu",
                                 "--format=csv,noheader,nounits"], capture_output=True,
                                text=True, timeout=5, short=True)
            memory = {}
            for line in (apps.stdout or "").splitlines():
                pid, _sep, used = line.partition(",")
                if pid.strip().isdigit():
                    memory[int(pid)] = float(used or 0) * 2 ** 20
            whole = [float(one) for one in (card.stdout or "").split() if
                     one.replace(".", "", 1).isdigit()]
            self.seen = {"use": {}, "memory": memory, "whole": max(whole or [0.0])}
        except Exception:                      # pragma: no cover - read next time
            log.debug("nvidia-smi could not be read", exc_info=True)
        return self.seen


def _gpu_reader() -> Tuple[Optional[Any], str]:
    """How graphics are measured here - and, when they cannot be, why."""
    if sys.platform == "win32":
        try:
            return _WindowsGpu(), "Windows' GPU counters, as Task Manager reads them"
        except Exception as exc:
            return None, f"Windows' GPU counters could not be read: {exc}"
    try:
        return _NvidiaGpu(), "nvidia-smi: graphics memory by process, the card's use as a whole"
    except Exception:
        pass
    if sys.platform == "darwin":
        return None, "not measured on macOS: it tells no program another's graphics use"
    return None, "not measured: no NVIDIA card's nvidia-smi here"


# -- disk -----------------------------------------------------------------------------------------
def _walk(folder: Path, deadline: float) -> Tuple[int, int, Dict[str, Tuple[int, int]]]:
    """Bytes and files in a folder, and in each thing directly inside it.
    Links and junctions are not walked into: what they point to is not
    this folder's."""
    parts: Dict[str, Tuple[int, int]] = {}
    total = files = 0
    try:
        top = list(os.scandir(folder))
    except OSError:
        return 0, 0, {}
    for entry in top:
        size = count = 0
        try:
            if entry.is_symlink() or getattr(entry, "is_junction", lambda: False)():
                continue
            if entry.is_file(follow_symlinks=False):
                size, count = entry.stat(follow_symlinks=False).st_size, 1
            elif entry.is_dir(follow_symlinks=False):
                stack = [entry.path]
                while stack:
                    if time.time() > deadline:
                        break
                    here = stack.pop()
                    try:
                        inside = list(os.scandir(here))
                    except OSError:
                        continue
                    for one in inside:
                        try:
                            if one.is_symlink() or getattr(one, "is_junction",
                                                           lambda: False)():
                                continue
                            if one.is_dir(follow_symlinks=False):
                                stack.append(one.path)
                            elif one.is_file(follow_symlinks=False):
                                size += one.stat(follow_symlinks=False).st_size
                                count += 1
                        except OSError:
                            continue
        except OSError:
            continue
        parts[entry.name] = (size, count)
        total += size
        files += count
    return total, files, parts


def _place(label: str, kind: str, folder: Path, deadline: float) -> Dict[str, Any]:
    total, files, parts = _walk(folder, deadline)
    ranked = sorted(parts.items(), key=lambda one: -one[1][0])
    shown = [{"name": name, "bytes": size, "files": count}
             for name, (size, count) in ranked[:PARTS_SHOWN]]
    rest = ranked[PARTS_SHOWN:]
    if rest:
        shown.append({"name": f"{len(rest)} more", "bytes": sum(one[1][0] for one in rest),
                      "files": sum(one[1][1] for one in rest), "rest": True})
    return {"label": label, "kind": kind, "path": str(folder), "bytes": total,
            "files": files, "parts": shown, "exists": folder.is_dir(),
            "cut_short": time.time() > deadline}


def _model_folders() -> List[Tuple[str, Path]]:
    """The models this application fetched, in the shared model cache."""
    hub = os.environ.get("HF_HUB_CACHE") or (
        str(Path(os.environ["HF_HOME"]) / "hub") if os.environ.get("HF_HOME")
        else str(Path.home() / ".cache" / "huggingface" / "hub"))
    names = []
    try:
        from ..heartbeat.states.embedding import DEFAULT_MODEL
        names.append(DEFAULT_MODEL)
    except Exception:                          # pragma: no cover - none then
        pass
    try:
        from ..runtime.video import SPEECH_MODEL
        names.append(SPEECH_MODEL if "/" in SPEECH_MODEL
                     else f"Systran/faster-whisper-{SPEECH_MODEL}")
    except Exception:                          # pragma: no cover - none then
        pass
    out = []
    for name in names:
        folder = Path(hub) / ("models--" + name.replace("/", "--"))
        if folder.is_dir():
            out.append((name, folder))
    return out


# -- the watch --------------------------------------------------------------------------------
class ResourceWatch:
    """What the application costs, sampled while someone looks (module
    docstring)."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self._lock = threading.Lock()
        self._asked = 0.0
        self._thread: Optional[threading.Thread] = None
        self._history: deque = deque(maxlen=int(KEEP_SECONDS / SAMPLE_SECONDS))
        self._latest: Dict[str, Any] = {}
        self._processes: Dict[int, Any] = {}
        self._gpu, self._gpu_how = None, ""
        self._gpu_made = False
        self._disk: Dict[str, Any] = {"places": [], "measured_at": "", "measuring": False}
        self._disk_thread: Optional[threading.Thread] = None
        self._leftovers: List[Dict[str, Any]] = []
        self._leftovers_at = 0.0

    # -- what the page asks for ----------------------------------------------------------
    def now(self) -> Dict[str, Any]:
        """The latest sample, the minutes kept, and the disk as last
        measured - sampling started, if it was not going."""
        with self._lock:
            self._asked = time.time()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="resource-watch",
                                                daemon=True)
                self._thread.start()
        if not self._disk["measured_at"] and not self._disk["measuring"]:
            self.measure_disk()
        elif (self._disk["measured_at"] and not self._disk["measuring"]
              and time.time() - self._disk.get("at", 0) > DISK_SECONDS):
            self.measure_disk()
        return {"latest": self._latest, "history": list(self._history),
                "kinds": [{"id": key, "label": label} for key, label in KINDS],
                "sample_seconds": SAMPLE_SECONDS, "keep_seconds": KEEP_SECONDS,
                "gpu": {"measured": self._gpu is not None, "how": self._gpu_how},
                "disk": {key: value for key, value in self._disk.items() if key != "at"},
                "leftovers": self._leftovers}

    def measure_disk(self) -> Dict[str, Any]:
        """The disk measured again, behind the page."""
        with self._lock:
            if self._disk["measuring"]:
                return {"measuring": True}
            self._disk["measuring"] = True
            self._disk_thread = threading.Thread(target=self._measure, name="resource-disk",
                                                 daemon=True)
            self._disk_thread.start()
        return {"measuring": True}

    # -- sampling -------------------------------------------------------------------------
    def _loop(self) -> None:
        while time.time() - self._asked < IDLE_SECONDS:
            began = time.time()
            try:
                self.sample()
            except Exception:                  # pragma: no cover - sampled again
                log.warning("could not sample the application's processes", exc_info=True)
            time.sleep(max(0.2, SAMPLE_SECONDS - (time.time() - began)))
        self._processes.clear()

    def _owners(self) -> Tuple[Dict[int, Tuple[str, str]], List[Tuple[str, str, str]]]:
        """The runs going: by the process each runs in (a run in a process
        of its own), and by its folder (one in this process)."""
        by_pid: Dict[int, Tuple[str, str]] = {}
        by_folder: List[Tuple[str, str, str]] = []
        work = getattr(self.app, "work", None)
        for run in list(getattr(work, "_running", {}).values()) if work else []:
            try:
                if not run.alive():
                    continue
                unit = getattr(run, "unit", {}) or {}
                debug = unit.get("debug") or {}
                if debug:
                    label = (f"{unit.get('name') or 'a project'} - try "
                             f"{debug.get('try', 1)}, {debug.get('label', '')}")
                    kind = "debugger"
                else:
                    label = str(unit.get("name") or unit.get("id") or "a run")
                    kind = "work"
                pid = int(getattr(run, "pid", 0) or 0)
                if pid:
                    by_pid[pid] = (kind, label)
                folder = debug.get("folder") or getattr(run, "folder", None)
                if folder:
                    by_folder.append((_norm(str(folder)), kind, label))
            except Exception:                  # a run mid-change
                continue
        by_folder.sort(key=lambda one: -len(one[0]))
        return by_pid, by_folder

    def sample(self) -> Dict[str, Any]:
        """One look at every process of the application."""
        import psutil

        if not self._gpu_made:
            self._gpu, self._gpu_how = _gpu_reader()
            self._gpu_made = True
        me = psutil.Process(os.getpid())
        try:
            tree = [me, *me.children(recursive=True)]
        except psutil.Error:
            tree = [me]
        cores = psutil.cpu_count() or 1
        by_pid, by_folder = self._owners()
        gpu = {"use": {}, "memory": {}, "whole": None}
        if self._gpu is not None:
            try:
                gpu = self._gpu.read()
            except Exception:                  # pragma: no cover - read next time
                log.debug("could not read the GPU counters", exc_info=True)
        seen = {}
        parents: Dict[int, int] = {}
        found: Dict[int, Tuple[str, str]] = {}
        rows = []
        for process in tree:
            pid = process.pid
            held = self._processes.get(pid)
            if held is None or held.is_running() is False:
                held = process
                try:
                    held.cpu_percent(None)        # the first reading: from now
                except psutil.Error:
                    continue
            seen[pid] = held
            try:
                with held.oneshot():
                    name = held.name()
                    parents[pid] = held.ppid()
                    cpu = held.cpu_percent(None) / cores
                    memory = held.memory_info().rss
                    line = " ".join(held.cmdline() or [])
                    try:
                        where = held.cwd()
                    except psutil.Error:
                        where = ""
                    started = held.create_time()
            except psutil.Error:
                continue
            found[pid] = self._what_for(pid, name, line, where, me.pid, by_pid, by_folder)
            rows.append({"pid": pid, "parent": parents[pid], "name": name,
                         "cpu": round(cpu, 2), "memory": memory,
                         "gpu": round(float(gpu["use"].get(pid, 0.0)), 2)
                         if gpu["use"] else None,
                         "gpu_memory": gpu["memory"].get(pid) if gpu["memory"] else None,
                         "started": datetime.fromtimestamp(started, timezone.utc).isoformat(),
                         "command": line[:300]})
        self._processes = seen
        # What one was started for, its children are for too.
        for row in rows:
            kind, owner = found.get(row["pid"], ("other", row["name"]))
            if kind in ("other", "models"):
                up = row["parent"]
                while up in found and up != me.pid:
                    if found[up][0] in ("work", "debugger", "embedding"):
                        kind, owner = found[up]
                        break
                    up = parents.get(up, 0)
            row["kind"], row["owner"] = kind, owner
        memory = psutil.virtual_memory()
        totals: Dict[str, Dict[str, float]] = {key: {"cpu": 0.0, "memory": 0, "gpu": 0.0,
                                                     "gpu_memory": 0}
                                               for key, _label in KINDS}
        for row in rows:
            mine = totals[row["kind"]]
            mine["cpu"] += row["cpu"]
            mine["memory"] += row["memory"]
            mine["gpu"] += row["gpu"] or 0.0
            mine["gpu_memory"] += row["gpu_memory"] or 0
        latest = {"at": _now(), "t": time.time(), "processes": rows, "totals": totals,
                  "machine": {"cpu": psutil.cpu_percent(None), "cores": cores,
                              "memory_total": memory.total,
                              "memory_used": memory.total - memory.available,
                              "gpu": gpu.get("whole")}}
        self._latest = latest
        self._history.append({"t": latest["t"],
                              "cpu": {key: round(one["cpu"], 2) for key, one in totals.items()},
                              "memory": {key: one["memory"] for key, one in totals.items()},
                              "gpu": {key: round(one["gpu"], 2) for key, one in totals.items()},
                              "machine_cpu": latest["machine"]["cpu"]})
        if time.time() - self._leftovers_at > LEFTOVER_SECONDS:
            self._leftovers_at = time.time()
            self._leftovers = self._left_from_before()
        return latest

    @staticmethod
    def _what_for(pid: int, name: str, line: str, where: str, me: int,
                  by_pid: Dict[int, Tuple[str, str]],
                  by_folder: List[Tuple[str, str, str]]) -> Tuple[str, str]:
        if pid == me:
            return "app", "The application"
        if pid in by_pid:
            return by_pid[pid]
        said = _norm(line)
        if "embed_worker" in said:
            return "embedding", "Embedding model"
        here = _norm(where)
        for folder, kind, label in by_folder:
            if (here and (here == folder or here.startswith(folder + os.sep))) or folder in said:
                return kind, label
        if os.path.splitext(name.lower())[0] in _CODING_AGENTS:
            return "models", "Model calls outside a run"
        return "other", name

    def _left_from_before(self) -> List[Dict[str, Any]]:
        try:
            from ..common.children import leftovers
            marks = self.app.leftover_marks()
            out = []
            for process in leftovers(marks):
                try:
                    out.append({"pid": process.pid, "name": process.name(),
                                "memory": process.memory_info().rss,
                                "started": datetime.fromtimestamp(
                                    process.create_time(), timezone.utc).isoformat(),
                                "command": " ".join(process.cmdline() or [])[:300]})
                except Exception:
                    continue
            return out
        except Exception:                      # pragma: no cover - looked at next time
            log.debug("could not look for processes left from before", exc_info=True)
            return []

    # -- disk ----------------------------------------------------------------------------------
    def places(self) -> List[Tuple[str, str, Path]]:
        """Every folder the application keeps things in, with what it is."""
        paths = self.app.paths
        out: List[Tuple[str, str, Path]] = [
            ("The application's folder", "home", Path(paths.root))]
        root = _norm(str(paths.root))
        seen = {root}

        def add(label: str, kind: str, folder: Any) -> None:
            if not folder:
                return
            key = _norm(str(folder))
            if key in seen or key.startswith(root + os.sep):
                return
            seen.add(key)
            out.append((label, kind, Path(folder)))

        db = self.app.db
        try:
            for row in db.query("SELECT name, folder FROM debug_projects ORDER BY name"):
                add(f"Work debugger: {row['name']}", "debugger", row["folder"])
        except Exception:                      # no debugger projects table
            pass
        try:
            for row in db.query("SELECT DISTINCT folder FROM work_runs "
                                "WHERE substr(unit_id, 1, 4) != 'dbg_'"):
                add("A folder your work runs in", "work", row["folder"])
        except Exception:                      # pragma: no cover - no runs table
            pass
        for name, folder in _model_folders():
            add(f"Model: {name}", "model", folder)
        return out

    def _measure(self) -> None:
        try:
            measured = []
            for label, kind, folder in self.places():
                measured.append(_place(label, kind, folder, time.time() + 120))
            self._disk.update(places=measured, measured_at=_now(), at=time.time())
        except Exception:                      # pragma: no cover - measured again
            log.warning("could not measure the application's folders", exc_info=True)
        finally:
            self._disk["measuring"] = False


__all__ = ["ResourceWatch", "KINDS", "SAMPLE_SECONDS"]

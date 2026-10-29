"""A RESULT GIVEN AGAIN ONLY ON EXACTLY THE SAME INPUTS - run_python and
run_tests alike (the user, 2026-10-28).

Code that ran is kept with what it ran on - recorded as it ran, in its own
process and in any Python process it started, each guarded and recording
itself (runtime/guard.py, `RECORD_ENV`):

  * the code itself (or the tests' folder and arguments), the Python and the
    packages installed for the work;
  * EVERY FILE IT OPENED TO READ, wherever it is - its data, and every
    script it imported, however deep (an import opens the file) - by its
    size and the time it last changed, to the nanosecond: LIGHT (the user,
    2026-10-29). Hashed, every file a script that walked the work's folder
    had read was read again after each run - for results that, in a
    replication's exploring, were never asked for again;
  * every folder it listed, by what was in it;
  * every file it wrote, as it left it - an output since changed or gone
    is not there to be given again.

Asked again, it is given again only when every one of them is as it was -
and SAID TO BE SO: what was checked, and what could not be, for the agent
to decide whether to run it anew (`fresh`). Never given again: code that
started a program other than Python (its reading cannot be seen), that
loaded a library opening files in its own native code (`NATIVE_FILES`),
that read more than MOST_FILES (counted before any is looked at), or
that ended badly - nor run_python code quicker than `LEAST_SECONDS`.
run_command is never given again: what a program reads cannot be seen.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: run_python code quicker than this is run again: there is nothing to save.
LEAST_SECONDS = 5.0
#: Past this many, the inputs are not checked: not kept.
MOST_FILES = 3000
#: Libraries that open files in their own native code, out of the record's
#: sight - code that loaded one is run again. By the top-level module.
NATIVE_FILES = frozenset({
    "pyarrow", "polars", "duckdb", "h5py", "netCDF4", "tables", "osgeo", "rasterio",
    "fiona", "pyogrio", "cv2", "soundfile", "pyreadstat", "pysam", "SimpleITK", "vtk",
    "lmdb", "zarr", "tiledb", "lancedb", "sqlalchemy", "psycopg", "psycopg2", "pymysql",
    "pyodbc", "cx_Oracle", "oracledb", "pymongo", "redis", "openslide", "pyvips",
    "pydicom", "nibabel", "imageio", "imageio_ffmpeg", "av", "moviepy", "pyreadr",
    "rpy2", "jpype", "tensorflow", "jax", "pyspark", "dask",
})
#: What a result given again could not have been checked against.
CANNOT_SEE = ("what native code inside a library opens by itself, the time, randomness, "
              "a file rewritten with exactly the same size and time of change, "
              "and anything outside this computer (code here opens no network)")
KINDS = ("read", "listed", "wrote")


def state(path: str) -> str:
    """A file by its size and the time it changed, to the nanosecond - one
    look, nothing read; a folder by its listing - or that it is not there."""
    try:
        seen = os.stat(path)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"
    import stat as _stat
    if _stat.S_ISDIR(seen.st_mode):
        try:
            names = sorted(f"{one.name}{'/' if one.is_dir() else ''}"
                           for one in os.scandir(path))
        except OSError:
            return "unreadable"
        return "dir:" + hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()
    return f"file:{seen.st_size}:{seen.st_mtime_ns}"


def packages_key(packages: Optional[List[Path]]) -> Optional[str]:
    """The work's packages AS PIP LEAVES THEM: every name at the top of each
    folder - a package, and its NAME-VERSION.dist-info, so that an install,
    an upgrade or a removal changes them - and when each install's RECORD
    was written. Read from the top of each folder alone, a listing and a
    look an install: every file of them, by path, size and time, was 25,961
    files for a replication package's, and took half a minute before each
    run (measured 2026-10-29). None when they cannot be read."""
    digest = hashlib.sha256()
    for root in packages or []:
        digest.update(b"\0PACKAGES\0" + str(root).encode("utf-8"))
        try:
            entries = sorted(os.scandir(root), key=lambda one: one.name)
        except FileNotFoundError:
            continue                          # nothing installed yet
        except OSError:
            return None
        for one in entries:
            stamp = ""
            if one.name.endswith((".dist-info", ".egg-info")):
                try:
                    stamp = str(os.stat(os.path.join(one.path, "RECORD")).st_mtime_ns)
                except OSError:
                    stamp = "-"
            try:
                kind = "/" if one.is_dir() else ""
            except OSError:
                return None
            digest.update(f"{one.name}{kind}|{stamp}\n".encode("utf-8"))
    return digest.hexdigest()


def skip_roots(packages: Optional[List[Path]], *more: Path) -> List[str]:
    """What is never counted an input: Python's own, the packages (the key
    holds them), the system's, and the run's own scratch."""
    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix,
             *(str(one) for one in packages or []), *(str(one) for one in more)}
    if os.environ.get("SystemRoot"):
        roots.add(os.environ["SystemRoot"])
    return [os.path.normcase(os.path.abspath(one)) for one in roots if one]


def _counted(path: str, skip: Sequence[str]) -> bool:
    low = os.path.normcase(os.path.abspath(path))
    if "__pycache__" in low or low.endswith((".pyc", ".pyo")):
        return False
    return not any(low == root or low.startswith(root.rstrip(os.sep) + os.sep)
                   for root in skip)


def read_records(base: Path) -> Optional[Dict[str, Any]]:
    """What every process of the run recorded (`<base>-<pid>`), together -
    and the records removed. None when none was written."""
    base = Path(base)
    merged: Dict[str, Any] = {kind: set() for kind in KINDS}
    merged.update(spawned=False, modules=set())
    found = False
    try:
        records = list(base.parent.glob(base.name + "-*"))
    except OSError:
        records = []
    for one in records:
        try:
            said = json.loads(one.read_text(encoding="utf-8"))
            found = True
        except (OSError, ValueError):
            continue
        finally:
            try:
                one.unlink()
            except OSError:
                pass
        for kind in KINDS:
            merged[kind].update(said.get(kind) or [])
        merged["spawned"] = merged["spawned"] or bool(said.get("spawned"))
        merged["modules"].update(said.get("modules") or [])
    return merged if found else None


def inputs_of(record: Optional[Dict[str, Any]], skip: Sequence[str]
              ) -> Tuple[Optional[Dict[str, Dict[str, str]]], str]:
    """Each input as it is now: ({"read", "listed", "wrote"}, "") - or
    (None, why it cannot be given again)."""
    if record is None:
        return None, "what it read was not recorded"
    if record.get("spawned"):
        return None, "it started a program other than Python, whose reading cannot be seen"
    native = sorted({str(name).split(".", 1)[0] for name in record.get("modules") or []}
                    & NATIVE_FILES)
    if native:
        return None, f"it loaded {', '.join(native)}, which open files in their own native code"
    # Counted first: past MOST_FILES, nothing is looked at.
    wanted = {kind: [path for path in sorted(record.get(kind) or []) if _counted(path, skip)]
              for kind in KINDS}
    if sum(len(paths) for paths in wanted.values()) > MOST_FILES:
        return None, "it read too many files to check"
    inputs: Dict[str, Dict[str, str]] = {kind: {} for kind in KINDS}
    for kind in KINDS:
        for path in wanted[kind]:
            inputs[kind][path] = state(path)
    # What it read and then wrote over is its output, as it left it.
    for path in list(inputs["read"]):
        if path in inputs["wrote"]:
            inputs["read"].pop(path)
    return inputs, ""


def unchanged(inputs: Optional[Dict[str, Dict[str, str]]]) -> bool:
    if inputs is None:
        return False
    return all(state(path) == was for kind in KINDS
               for path, was in (inputs.get(kind) or {}).items())


def checked(inputs: Dict[str, Dict[str, str]], what: str) -> Dict[str, Any]:
    """What was found as it was - told with the result."""
    scripts = sorted(path for path in inputs["read"] if path.lower().endswith(".py"))
    data = [path for path in inputs["read"] if path not in scripts]
    return {"code": what, "python_and_packages": "the same Python, the work's packages unchanged",
            "scripts_imported": [Path(one).name for one in scripts][:30],
            "files_read": len(data), "files_read_named": [Path(one).name for one in data][:20],
            "folders_listed": len(inputs["listed"]),
            "outputs_as_written": len(inputs["wrote"])}


def told(tool: str, by: str, at: str, seconds: Any, inputs: Dict[str, Dict[str, str]],
         what: str) -> Tuple[str, Dict[str, Any]]:
    """The words a result given again comes with, and its record: why it
    was given again, what was checked and what could not be - and how to
    run it anew. The agent decides."""
    seen = checked(inputs, what)
    words = (f"NOT A NEW RUN: the result of {by}'s run at {at} ({seconds} s), given again "
             f"because everything it ran on is as it was - each file by its size and the "
             f"time it last changed - {what}, the Python and packages, "
             f"{len(seen['scripts_imported'])} script(s) it imported, {seen['files_read']} "
             f"file(s) it read, {seen['folders_listed']} folder(s) it listed, and the "
             f"{seen['outputs_as_written']} file(s) it wrote, still as it left them. Not "
             f"seen: {CANNOT_SEE}. If any of that could matter here, or you are not sure, "
             f"call {tool} again with fresh=true to run it anew.")
    return words, {"ran_at": at, "by": by, "seconds": seconds, "checked": seen,
                   "cannot_see": CANNOT_SEE, "run_anew_with": {"fresh": True}}


class PythonRuns:
    """run_python's results, by what they ran on - shared by the agents of a
    run, as tools.TestRuns is."""

    __test__ = False

    def __init__(self) -> None:
        self._kept: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(code: str, packages: Optional[List[Path]], cwd: Path) -> str:
        """The code, where it runs, the Python and the packages."""
        installed = packages_key(packages)
        if installed is None:
            return ""
        return hashlib.sha256(json.dumps(
            {"code": code, "cwd": str(cwd), "python": sys.version,
             "implementation": platform.python_implementation(),
             "packages": installed}).encode("utf-8")).hexdigest()

    def keep(self, key: str, record: Optional[Dict[str, Any]], result: Dict[str, Any],
             by: str, seconds: float, skip: Sequence[str]) -> str:
        """Kept, when it may be given again: "" - else why not."""
        if not key:
            return "its packages could not be read"
        if result.get("timed_out") or result.get("violation") or result.get("returncode") is None:
            return "it did not end by itself"
        if result.get("returncode") != 0:
            # A failure is run again: it may not be one the next time.
            return "it ended in an error"
        if seconds < LEAST_SECONDS:
            return "it was quick"
        inputs, why = inputs_of(record, skip)
        if inputs is None:
            return why
        with self._lock:
            self._kept[key] = {"inputs": inputs, "by": by, "seconds": round(seconds, 1),
                               "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                               "result": json.loads(json.dumps(result, default=str))}
        return ""

    def again(self, key: str) -> Optional[Dict[str, Any]]:
        """The kept result when every input is as it was - said to be so;
        else None."""
        if not key:
            return None
        with self._lock:
            kept = self._kept.get(key)
        if kept is None or not unchanged(kept["inputs"]):
            return None
        words, record = told("run_python", kept["by"], kept["at"], kept["seconds"],
                             kept["inputs"], "the same code, run in the same folder")
        said = dict(kept["result"], files_changed=[], not_a_new_run=words, reused=record)
        return said


__all__ = ["PythonRuns", "NATIVE_FILES", "LEAST_SECONDS", "MOST_FILES",
           "read_records", "inputs_of", "unchanged", "told", "skip_roots", "state"]

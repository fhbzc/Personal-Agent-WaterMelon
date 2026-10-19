"""THE INDEX IS BUILT HERE, in a process of its own, at the lowest priority
the operating system has - what the user is doing comes first, and a
build that takes an hour on a busy day costs them nothing:

  * Windows: background mode (`PROCESS_MODE_BACKGROUND_BEGIN`), which lowers
    the process's CPU, disk and memory priority together.
  * Linux: nice 19, and the disk at its idle class.
  * macOS: nice 19, and the disk throttled (`setiopolicy_np`).

And on a computer running on its battery, it waits until it is plugged in.

Started with the path of a job (a JSON file: the index file, the folders,
the places never gone into); one line of JSON on stdout for each step of
progress; ended by the end of its work, or by being ended.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

#: How long it waits on a battery before looking again.
BATTERY_WAIT_SECONDS = 60


def lowest_priority() -> str:
    """The process put below everything the user runs: what was done."""
    try:
        if sys.platform.startswith("win"):
            kernel = ctypes.windll.kernel32
            if kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00100000):
                return "windows background mode"
            kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00000040)
            return "windows idle priority"
        os.nice(19)
        if sys.platform == "darwin":
            libc = ctypes.CDLL("libc.dylib")
            # IOPOL_TYPE_DISK, IOPOL_SCOPE_PROCESS, IOPOL_THROTTLE
            libc.setiopolicy_np(0, 0, 3)
            return "nice 19, disk throttled"
        try:
            import psutil
            psutil.Process().ionice(psutil.IOPRIO_CLASS_IDLE)
            return "nice 19, disk idle"
        except Exception:
            return "nice 19"
    except Exception as exc:                 # pragma: no cover - the system's own
        return f"priority not lowered: {exc}"


def on_battery() -> bool:
    try:
        import psutil
        battery = psutil.sensors_battery()
    except Exception:
        return False
    return bool(battery is not None and not battery.power_plugged)


def serve(job_path: str) -> int:
    from .build import build
    from .store import IndexStore

    def say(message: Dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    say({"priority": lowest_priority()})
    while on_battery():
        say({"waiting": "on battery"})
        time.sleep(BATTERY_WAIT_SECONDS)
    store = IndexStore(Path(job["db"]))
    began = time.time()
    try:
        said = build(store, job.get("roots") or [], never=job.get("never") or [],
                     told=say)
    finally:
        store.close()
    say({"finished": True, "seconds": round(time.time() - began, 1), **said})
    return 0


if __name__ == "__main__":                    # pragma: no cover - started as a child
    sys.exit(serve(sys.argv[1]))

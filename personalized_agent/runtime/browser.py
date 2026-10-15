"""The browser work runs test with - installed once, from Settings.

Agents test what they build the way people do: start it and look at it. The
browser for that is Google's "Chrome for Testing" headless shell - not the
user's own Chrome, whose crash reporter cannot start inside a run's
container, and which holds the user's accounts. It is downloaded only when
the user asks (Settings, Browser testing), from Google's own storage, its
checksum compared with the one the storage gives, and kept in the
application's protected state:

    <internal>/browser/<version>/chrome-headless-shell-<platform>/...
    <internal>/browser/lib/agent_browser.py     the helper runs import
    <internal>/browser/current.json             which version is in use

No run can write there - every rule that keeps that state refuses it - and
it is one of the folders of it every child may read (runtime/isolation.py,
`note_reading_home`), as the guard's is. A run is given it only where the
operating system keeps the browser to the run's own loopback
(isolation.own_loopback): there the browser reaches the servers that run
starts and nothing else.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from . import isolation

log = logging.getLogger(__name__)

FOLDER = "browser"
LIB = "lib"
CURRENT = "current.json"
HELPER = "agent_browser.py"
#: Google's list of the current Chrome for Testing builds, with downloads.
VERSIONS_URL = ("https://googlechromelabs.github.io/chrome-for-testing/"
                "last-known-good-versions-with-downloads.json")
#: Only downloads from Google's own storage are taken from that list.
DOWNLOAD_PREFIX = "https://storage.googleapis.com/chrome-for-testing-public/"
_CHUNK = 1 << 20


def platform_key() -> Optional[str]:
    """Chrome for Testing's name for this platform, or None."""
    import platform
    machine = platform.machine().lower()
    if sys.platform.startswith("win"):
        return "win64" if sys.maxsize > 2 ** 32 else "win32"
    if sys.platform == "darwin":
        return "mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64"
    if sys.platform.startswith("linux") and machine in ("x86_64", "amd64"):
        return "linux64"
    return None


def executable_name() -> str:
    return "chrome-headless-shell.exe" if sys.platform.startswith("win") \
        else "chrome-headless-shell"


def home(data_dir: Path) -> Path:
    return Path(data_dir) / FOLDER


def _read_current(data_dir: Path) -> Dict[str, Any]:
    try:
        return json.loads((home(data_dir) / CURRENT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def installed(data_dir: Optional[Path]) -> Optional[Path]:
    """The installed browser's program - its folders made readable to the
    runs' code and the helper put beside it - or None."""
    if not data_dir:
        return None
    current = _read_current(data_dir)
    version = str(current.get("version") or "")
    if not version or "/" in version or "\\" in version or version.startswith("."):
        return None
    folder = home(data_dir) / version
    program = folder / str(current.get("program") or "")
    if not program.is_file() or not isolation._within(program, folder):
        return None
    lib = ensure_helper(data_dir)
    isolation.note_reading_home(folder)
    isolation.note_reading_home(lib)
    return program


def helper_folder(data_dir: Path) -> Path:
    return home(data_dir) / LIB


def ensure_helper(data_dir: Path) -> Path:
    """The helper runs import, copied beside the browser - outside the
    application's code, which a debugger run may not read - and put back
    whenever it differs from the code's."""
    folder = helper_folder(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent / HELPER
    copy = folder / HELPER
    wanted = source.read_bytes()
    try:
        same = copy.read_bytes() == wanted
    except OSError:
        same = False
    if not same:
        partial = folder / f".{HELPER}.partial"
        partial.write_bytes(wanted)
        os.replace(partial, copy)
    shutil.rmtree(folder / "__pycache__", ignore_errors=True)
    return folder


def for_run(data_dir: Optional[Path]) -> Optional[Path]:
    """The browser a run may start - only where the system keeps it to the
    run's own loopback - or None."""
    if not data_dir or not isolation.own_loopback():
        return None
    return installed(data_dir)


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------
def _fetch_json(url: str, timeout: float = 30.0) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as answer:  # noqa: S310 - fixed URL
        return json.loads(answer.read().decode("utf-8"))


def latest(platform: Optional[str] = None) -> Dict[str, str]:
    """{"version", "url"} of the current stable headless shell for this
    platform, from Google's list."""
    platform = platform or platform_key()
    if not platform:
        raise RuntimeError("Chrome for Testing has no build for this computer")
    stable = (_fetch_json(VERSIONS_URL).get("channels") or {}).get("Stable") or {}
    for one in (stable.get("downloads") or {}).get("chrome-headless-shell") or []:
        if one.get("platform") == platform:
            url = str(one.get("url") or "")
            if not url.startswith(DOWNLOAD_PREFIX):
                raise RuntimeError(f"unexpected download address {url!r}")
            return {"version": str(stable.get("version") or ""), "url": url}
    raise RuntimeError(f"no headless shell for {platform} in Google's list")


def _stored_md5(headers: Any) -> Optional[bytes]:
    """The MD5 Google's storage says the file has (x-goog-hash: md5=...)."""
    for value in headers.get_all("x-goog-hash") or []:
        for part in value.split(","):
            name, _, digest = part.strip().partition("=")
            if name == "md5" and digest:
                try:
                    return base64.b64decode(digest)
                except ValueError:
                    return None
    return None


class Installer:
    """One install at a time, on a thread of its own; its state for the
    Settings card."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self.state: Dict[str, Any] = {"state": "idle"}

    def status(self) -> Dict[str, Any]:
        program = installed(self.data_dir)
        current = _read_current(self.data_dir) if program else {}
        loopback = isolation.own_loopback()
        out = {
            "installed": bool(program),
            "version": current.get("version", ""),
            "installed_at": current.get("installed_at", ""),
            "bytes": current.get("bytes", 0),
            "program": str(program) if program else "",
            "platform": platform_key() or "",
            "usable": bool(program) and loopback,
            "why_not": "" if loopback else (
                "this computer cannot keep a browser to one run's own servers "
                f"({isolation.describe()}), so runs are not given one"),
            "job": dict(self.state),
        }
        return out

    def start(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.status()
            self._cancel.clear()
            self.state = {"state": "starting", "done": 0, "total": 0,
                          "started_at": time.time()}
            self._thread = threading.Thread(target=self._run, name="browser-install",
                                            daemon=True)
            self._thread.start()
        return self.status()

    def cancel(self) -> Dict[str, Any]:
        self._cancel.set()
        return self.status()

    def wait(self, timeout: Optional[float] = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _progress(self, done: int, total: int) -> None:
        self.state.update(state="downloading", done=done, total=total)
        if self._cancel.is_set():
            raise InterruptedError("cancelled")

    def _run(self) -> None:
        try:
            found = latest()
            self.state.update(version=found["version"])
            install(self.data_dir, found["version"], found["url"], self._progress)
            self.state.update(state="done", finished_at=time.time())
        except InterruptedError:
            self.state.update(state="cancelled", finished_at=time.time())
        except Exception as exc:
            log.warning("could not install the testing browser", exc_info=True)
            self.state.update(state="failed", error=str(exc), finished_at=time.time())

    def remove(self) -> Dict[str, Any]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("an install is going; cancel it first")
            remove(self.data_dir)
            self.state = {"state": "idle"}
        return self.status()


def install(data_dir: Path, version: str, url: str,
            progress: Callable[[int, int], None] = lambda done, total: None,
            opener: Callable[..., Any] = urllib.request.urlopen) -> Path:
    """Download `url`, check it against the checksum Google's storage gives,
    unpack it beside the others and make it the one in use. The program."""
    if not url.startswith(DOWNLOAD_PREFIX):
        raise ValueError(f"not a Chrome for Testing download: {url!r}")
    if not version or any(c in version for c in "/\\") or version.startswith("."):
        raise ValueError(f"not a version: {version!r}")
    where = home(data_dir)
    staging = where / ".download"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    archive = staging / "browser.zip"
    md5 = hashlib.md5()                                    # noqa: S324 - the store's own
    try:
        with opener(url, timeout=60) as answer, open(archive, "wb") as out:
            expected = _stored_md5(answer.headers)
            if expected is None:
                raise RuntimeError("the download came without a checksum to compare")
            total = int(answer.headers.get("Content-Length") or 0)
            done = 0
            progress(done, total)
            while True:
                chunk = answer.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                md5.update(chunk)
                done += len(chunk)
                progress(done, total)
        if md5.digest() != expected:
            raise RuntimeError("the download does not match Google's checksum; nothing "
                               "was installed")
        unpacked = staging / "unpacked"
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                target = (unpacked / member.filename).resolve()
                if not isolation._within(target, unpacked.resolve()):
                    raise RuntimeError(f"the archive names a path outside it: {member.filename}")
            bundle.extractall(unpacked)
        programs = list(unpacked.glob(f"*/{executable_name()}"))
        if len(programs) != 1:
            raise RuntimeError("the archive does not hold one headless shell")
        program = programs[0]
        if os.name != "nt":
            for one in program.parent.rglob("*"):
                if one.is_file() and (one == program or one.suffix in ("", ".so")
                                      or one.name.startswith("chrome_")):
                    one.chmod(one.stat().st_mode | 0o755)
        final = where / version
        shutil.rmtree(final, ignore_errors=True)
        os.replace(unpacked, final)
        relative = program.relative_to(unpacked)
        current = {"version": version, "program": relative.as_posix(), "url": url,
                   "bytes": archive.stat().st_size,
                   "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        partial = where / f".{CURRENT}.partial"
        partial.write_text(json.dumps(current, indent=2), encoding="utf-8")
        os.replace(partial, where / CURRENT)
        # Older versions go: one browser is kept.
        for other in where.iterdir():
            if other.is_dir() and other.name not in (version, LIB) \
                    and not other.name.startswith("."):
                shutil.rmtree(other, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    found = installed(data_dir)
    if found is None:
        raise RuntimeError("the browser was unpacked but cannot be found")
    log.info("installed the testing browser %s (%s)", version, found)
    return found


def remove(data_dir: Path) -> None:
    shutil.rmtree(home(data_dir), ignore_errors=True)

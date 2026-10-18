"""UPDATES, from this project's published releases.

Once a day - and when the user asks - the latest RELEASE is read from
GitHub. Only a published release counts: commits, merges, drafts and
pre-releases never reach anyone, so a release marked "pre-release" is how
a build is published without updating anyone.

What the user chose (Settings > General, ``updates``):

  - ``ask``, the default: the user is told, and shown every published
    version newer than theirs, each with what it says of itself; they
    install the one they choose. Off by default because a release that
    turns out broken would otherwise reach everyone within a day;
  - ``install``: the newest is downloaded, checked against the release's
    own SHA256SUMS.txt, and installed - never while a piece of work is
    running - and the agent starts again on it;
  - ``off``: nothing is asked of GitHub at all.

Only newer versions are offered: a version older than the one that last
wrote the data folder may not read it.

How it is installed is how the program was:

  - WINDOWS, from the installer: the new installer runs silently once this
    program has quit - the program folder, the data folder and the
    language are the ones chosen before - and starts the new version;
  - MACOS, an application the user dragged into Applications from the
    .dmg and so owns: the new one replaces it, and is opened;
  - LINUX, an AppImage: the file is replaced, and run.

Anything else - run from source, a portable folder, a .deb, an
application installed by the .pkg, which belongs to the system - is told
there is a newer version and where it is, and nothing more.

AFTER AN UPDATE, WHAT IS NEW is shown: the release's own description, as
written on GitHub, in a window the first time the page is opened on the
new version, until it is closed (`just_updated`, `seen`).

A download that does not match its checksum is deleted and never run.
Downloads are kept in the data folder (internal/updates), and only the
newest is kept.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..version import APP_NAME, __version__

log = logging.getLogger(__name__)

REPOSITORY = "fhbzc/Personal-Agent-WaterMelon"
RELEASES = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=30"
SUMS = "SHA256SUMS.txt"
CHECK_EVERY_SECONDS = 24 * 3600
#: The first look waits for the agent to have started properly.
FIRST_CHECK_AFTER = 120
#: Work running: looked at again this often.
WAIT_FOR_WORK = 300
#: What a release says of itself, kept to this many characters.
NOTES_KEPT = 20000
#: Written when an update is installed, read on the new version's start.
MARKER = "updated.json"

MODES = ("ask", "install", "off")
DEFAULT_MODE = "ask"

#: Each way of being installed, and the release file it updates from.
ASSET = {"windows": "PersonalAgentWaterMelon-Setup-windows-x64.exe",
         "macos": "PersonalAgentWaterMelon-macos.dmg",
         "appimage": "PersonalAgentWaterMelon-linux-x86_64.AppImage"}


def version_of(text: str) -> Optional[Tuple[int, ...]]:
    """"v1.2.3" or "1.2.3" as numbers; None for anything else."""
    text = str(text or "").strip().lstrip("vV")
    parts = text.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def is_newer(tag: str, current: str = __version__) -> bool:
    theirs, ours = version_of(tag), version_of(current)
    return bool(theirs and ours and theirs > ours)


def how_installed() -> str:
    """"windows", "macos" or "appimage" when this copy can replace itself,
    else "" - and then it is only told."""
    if not getattr(sys, "frozen", False):
        return ""
    executable = Path(sys.executable).resolve()
    if sys.platform.startswith("win"):
        # The installer leaves its uninstaller beside the program; a
        # portable folder has none.
        return "windows" if any(executable.parent.glob("unins*.exe")) else ""
    if sys.platform == "darwin":
        bundle = next((one for one in executable.parents if one.suffix == ".app"), None)
        if bundle is None:
            return ""
        # Dragged in from the .dmg, it is the user's; installed by the .pkg,
        # it is the system's and cannot be replaced without a password.
        mine = os.access(bundle, os.W_OK) and os.access(bundle.parent, os.W_OK)
        return "macos" if mine else ""
    image = os.environ.get("APPIMAGE", "")
    if image and os.access(image, os.W_OK) and os.access(os.path.dirname(image) or ".", os.W_OK):
        return "appimage"
    return ""


def _get(url: str, timeout: float = 30.0) -> Any:
    request = urllib.request.Request(url, headers={
        "User-Agent": f"{APP_NAME.replace(' ', '-')}/{__version__}",
        "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(request, timeout=timeout)


def _release_of(release: Dict[str, Any]) -> Dict[str, Any]:
    files = {one.get("name", ""): {"url": one.get("browser_download_url", ""),
                                   "size": int(one.get("size") or 0)}
             for one in release.get("assets") or []}
    return {"tag": str(release.get("tag_name") or ""),
            "page": str(release.get("html_url") or ""),
            "published": str(release.get("published_at") or ""),
            "notes": str(release.get("body") or "")[:NOTES_KEPT],
            "prerelease": bool(release.get("prerelease")),
            "draft": bool(release.get("draft")),
            "files": files}


def published_releases() -> List[Dict[str, Any]]:
    """Every published release - no draft, no pre-release - newest first."""
    with _get(RELEASES, timeout=20) as response:
        listed = json.loads(response.read().decode("utf-8"))
    out = [_release_of(one) for one in listed if isinstance(one, dict)]
    out = [one for one in out if not one["draft"] and not one["prerelease"]
           and version_of(one["tag"])]
    return sorted(out, key=lambda one: version_of(one["tag"]), reverse=True)


def _sums(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and len(parts[0]) == 64:
            out[parts[-1].lstrip("*")] = parts[0].lower()
    return out


class Updater:
    """One per running agent. `quit` is how this program is ended for an
    install - the desktop's own Quit, or the headless loop's stop - and
    with none, nothing is installed, only told."""

    def __init__(self, config: Any, data_dir: Path,
                 notify: Optional[Callable[..., Any]] = None,
                 working: Optional[Callable[[], bool]] = None) -> None:
        self.config = config
        self.folder = Path(data_dir) / "updates"
        self.notify = notify
        self.working = working or (lambda: False)
        self.quit: Optional[Callable[[], None]] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.state: Dict[str, Any] = {"current": __version__, "checked_at": "", "just_updated": None,
                                      "versions": [],
                                      "latest": "", "newer": False, "page": "",
                                      "doing": "", "error": "", "can_install": False}

    # -- the choice -----------------------------------------------------------
    def mode(self) -> str:
        chosen = str(self.config.get("updates", DEFAULT_MODE) or DEFAULT_MODE)
        return chosen if chosen in MODES else DEFAULT_MODE

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.state, mode=self.mode(), installed_as=how_installed())

    def _set(self, **values: Any) -> None:
        with self._lock:
            self.state.update(values)

    # -- the loop ---------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._said_updated()
        self._thread = threading.Thread(target=self._loop, name="updates", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def check_now(self) -> Dict[str, Any]:
        """The user's "Check now": looked at at once, on the loop's thread."""
        self._wake.set()
        return self.status()

    def install_now(self, version: str = "") -> Dict[str, Any]:
        """The user's "Install this version" - the newest when none is said."""
        self._set(asked_to_install=str(version or "") or True)
        self._wake.set()
        return self.status()

    def _loop(self) -> None:
        if self._stop.wait(FIRST_CHECK_AFTER):
            return
        while not self._stop.is_set():
            try:
                self.once()
            except Exception as exc:  # never the agent's
                log.info("could not look for an update: %s", exc)
                self._set(error=str(exc)[:300], doing="")
            self._wake.wait(WAIT_FOR_WORK if self.state.get("waiting_for_work")
                            else CHECK_EVERY_SECONDS)
            self._wake.clear()

    def once(self) -> Dict[str, Any]:
        """One look, and - where it is chosen and possible - one install."""
        if self.mode() == "off":
            self._set(doing="", error="")
            return self.status()
        self._set(doing="checking", error="")
        releases = published_releases()
        newer = [one for one in releases if is_newer(one["tag"])]
        kind = how_installed()

        def installable(release: Dict[str, Any]) -> bool:
            return bool(kind and ASSET.get(kind) in release["files"]
                        and SUMS in release["files"] and self.quit is not None)
        newest = newer[0] if newer else None
        self._set(checked_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  latest=(newest or (releases[0] if releases else {})).get("tag", ""),
                  newer=bool(newer), page=newest["page"] if newest else "", doing="",
                  can_install=bool(newest and installable(newest)),
                  # Every version newer than this one, each to be chosen.
                  versions=[{"tag": one["tag"], "published": one["published"],
                             "notes": one["notes"], "page": one["page"],
                             "can_install": installable(one)} for one in newer])
        if not newest:
            return self.status()
        asked = self.state.get("asked_to_install")
        if self.mode() == "install":
            chosen: Optional[Dict[str, Any]] = newest
        elif asked:
            chosen = newest if asked is True else next(
                (one for one in newer if one["tag"] == asked), None)
        else:
            self._tell_once(newest["tag"], newest["page"])
            return self.status()
        if chosen is None or not installable(chosen):
            self._set(asked_to_install=None)
            self._tell_once(newest["tag"], newest["page"])
            return self.status()
        if self.working():
            self._set(waiting_for_work=True, doing="waiting for work to finish")
            return self.status()
        self._set(waiting_for_work=False, asked_to_install=None)
        target = self.download(chosen, kind)
        self.install(target, kind, chosen["tag"], chosen)
        return self.status()

    def _tell_once(self, tag: str, page: str) -> None:
        if self.notify is None or self.state.get("told") == tag:
            return
        self._set(told=tag)
        try:
            self.notify(f"{APP_NAME} {tag} is available",
                        f"You have {__version__}. Settings > General updates it.",
                        kind="info", dedupe_key=f"update-{tag}",
                        action_hint=page, metadata={"update": tag})
        except Exception:  # pragma: no cover - a notice
            log.debug("could not tell of the update", exc_info=True)

    # -- download, checked ------------------------------------------------------
    def download(self, release: Dict[str, Any], kind: str) -> Path:
        name = ASSET[kind]
        tag = release["tag"]
        self._set(doing=f"downloading {tag}")
        with _get(release["files"][SUMS]["url"]) as response:
            expected = _sums(response.read().decode("utf-8", "replace")).get(name)
        if not expected:
            raise RuntimeError(f"{SUMS} of {tag} does not list {name}")
        here = self.folder / tag
        # Only the newest download is kept.
        if self.folder.exists():
            for old in self.folder.iterdir():
                if old.is_dir() and old.name != tag:
                    shutil.rmtree(old, ignore_errors=True)
        here.mkdir(parents=True, exist_ok=True)
        target = here / name
        partial = here / (name + ".part")
        digest = hashlib.sha256()
        with _get(release["files"][name]["url"], timeout=60) as response, open(partial, "wb") as out:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
                if self._stop.is_set():
                    raise RuntimeError("stopped while downloading")
        if digest.hexdigest() != expected:
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"{name} of {tag} does not match its checksum; deleted")
        os.replace(partial, target)
        return target

    # -- install ------------------------------------------------------------------
    def install(self, target: Path, kind: str, tag: str,
                release: Optional[Dict[str, Any]] = None) -> None:
        self._set(doing=f"installing {tag}")
        # What the new version says it is, for the window it opens with.
        (self.folder / MARKER).write_text(json.dumps({
            "from": __version__, "to": tag,
            "notes": (release or {}).get("notes", ""),
            "page": (release or {}).get("page", "")}), encoding="utf-8")
        if kind == "windows":
            self._after_quit_windows(target)
        elif kind == "macos":
            self._replace_macos(target)
        elif kind == "appimage":
            self._replace_appimage(target)
        log.info("updating to %s: quitting so it can be installed", tag)
        if self.quit is not None:
            self.quit()

    def _language(self) -> str:
        code = str(self.config.get("display_language", "en") or "en").split("-")[0].lower()
        return code if code in ("en", "zh", "es", "ja", "ko", "ru", "fr", "de", "it") else "en"

    def _after_quit_windows(self, installer: Path) -> None:
        """The installer, once this program has gone: started by a shell of
        its own, outside this program's job, so it outlives it."""
        # A quote in a path is doubled, as PowerShell reads it.
        quoted = str(installer).replace("'", "''")
        script = (f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; "
                  f"Start-Process -FilePath '{quoted}' -ArgumentList "
                  f"'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/LANG={self._language()}'")
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                 | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
        command = ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script]
        try:
            subprocess.Popen(command, creationflags=flags, close_fds=True)
        except OSError:
            # A job that may not be left: started inside it, it still ends
            # with this program - so not breaking away is not an option
            # worth having. Said, and left to the user.
            subprocess.Popen(command, creationflags=flags & ~getattr(
                subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), close_fds=True)

    def _replace_macos(self, image: Path) -> None:
        executable = Path(sys.executable).resolve()
        bundle = next(one for one in executable.parents if one.suffix == ".app")
        mount = self.folder / "mounted"
        if mount.exists():
            subprocess.run(["hdiutil", "detach", str(mount), "-force"], capture_output=True)
        mount.mkdir(parents=True, exist_ok=True)
        subprocess.run(["hdiutil", "attach", str(image), "-nobrowse", "-readonly",
                        "-mountpoint", str(mount)], check=True, capture_output=True, timeout=300)
        try:
            found = next((one for one in mount.iterdir() if one.suffix == ".app"), None)
            if found is None:
                raise RuntimeError("the disk image holds no application")
            # UNDER THE NAME IT COMES WITH: an application renamed since
            # (Personalized Agent, to 1.0.0) is replaced by the new one, not
            # kept under the old name beside it.
            target = bundle.with_name(found.name)
            fresh = target.with_name(target.name + ".new")
            stale = bundle.with_name(bundle.name + ".old")
            for one in (fresh, stale):
                if one.exists():
                    shutil.rmtree(one, ignore_errors=True)
            subprocess.run(["ditto", str(found), str(fresh)], check=True,
                           capture_output=True, timeout=600)
            os.replace(bundle, stale)
            if target != bundle and target.exists():
                shutil.rmtree(target, ignore_errors=True)
            os.replace(fresh, target)
            shutil.rmtree(stale, ignore_errors=True)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount), "-force"], capture_output=True)
        self._reopen(["open", str(target)])

    def _replace_appimage(self, image: Path) -> None:
        place = Path(os.environ["APPIMAGE"])
        fresh = place.with_name(place.name + ".new")
        shutil.copyfile(image, fresh)
        fresh.chmod(0o755)
        os.replace(fresh, place)
        self._reopen([str(place)])

    def _reopen(self, command: list) -> None:
        """Started again once this program has gone, by a shell that is not
        this program's child for long enough to be ended with it."""
        import shlex
        waited = " ".join(shlex.quote(str(part)) for part in command)
        script = (f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.5; done; "
                  f"{waited} >/dev/null 2>&1 &")
        subprocess.Popen(["/bin/sh", "-c", script], start_new_session=True, close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _said_updated(self) -> None:
        """Started on the version just installed: what is new is held for
        the page's window until it is closed, and a notice says so once."""
        marker = self.folder / MARKER
        try:
            said = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if version_of(said.get("to", "")) != version_of(__version__):
            # Not this version: an install that did not happen.
            marker.unlink(missing_ok=True)
            return
        self._set(just_updated={"from": str(said.get("from") or ""), "to": __version__,
                                "notes": str(said.get("notes") or ""),
                                "page": str(said.get("page") or "")})
        shutil.rmtree(self.folder / str(said.get("to")), ignore_errors=True)
        if said.get("told") or self.notify is None:
            return
        try:
            self.notify(f"Updated to {__version__}",
                        f"{APP_NAME} was updated from {said.get('from')}. "
                        f"What is new is shown when you open it.",
                        kind="info", dedupe_key=f"updated-{__version__}")
            said["told"] = True
            marker.write_text(json.dumps(said), encoding="utf-8")
        except Exception:  # pragma: no cover - a notice
            log.debug("could not say it was updated", exc_info=True)

    def seen(self) -> Dict[str, Any]:
        """The window of what is new was closed: not shown again."""
        (self.folder / MARKER).unlink(missing_ok=True)
        self._set(just_updated=None)
        return self.status()

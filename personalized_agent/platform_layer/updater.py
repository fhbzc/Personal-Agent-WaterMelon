"""UPDATES, from this project's published releases.

Once a day - and when the user asks - the latest RELEASE is read from
GitHub. Only a published release counts: commits, merges, drafts and
pre-releases never reach anyone, so a release marked "pre-release" is how
a build is published without updating anyone.

What the user chose (Settings > General, ``updates``):

  - ``ask``, the default (the user, 2026-10-27: looked for and told, not
    installed by itself): the user is told, and shown every published
    version newer than theirs, each with what it says of itself; they
    choose the one to have;
  - ``install``: the newest is had by itself;
  - ``off``: nothing is asked of GitHub at all.

EVERY UPDATE IS A QUIET ONE (the user, 2026-10-27: "like Claude CLI's" - the
one asked for too): made ready in the background, nothing stopped, and taken
up as the agent next starts, or when the user has been away a while and no
work runs - a restart of seconds - or at once at "Restart now". A version
that does not start is taken back by itself and never made ready again
(quiet_update.py).

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

WHILE IT IS INSTALLED, IT IS SEEN THROUGH (the user, 2026-10-27): the
download with its share done, then each step - checked, installing, the
agent closed and starting again (`step`, `progress`, `updating` in the
status) - and the page, which outlives the agent, follows it to the new
version and opens on it. On Windows the installer shows its own progress
window (/SILENT: no question, only the bar), for the page that is not open.

CHECK NOW says what it found on the page, where the user is looking - no
notice; that version counts as told, so the daily look does not tell it
again. Only a look the agent makes by itself tells of a new version.

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
#: The download's share done is said at most this often.
PROGRESS_EVERY = 0.5
#: The Windows installer's helper says it is there in this file, and within
#: this long, before the agent quits for it (`_after_quit_windows`).
HELPER_READY = "helper-ready.txt"
HELPER_READY_WITHIN = 15.0
#: The steps of an update as the page follows them, in order.
STEPS = ("downloading", "checking", "installing", "restarting")

MODES = ("ask", "install", "off")
DEFAULT_MODE = "ask"
#: Made ready, a version is taken up when the user has been away this long
#: and no work runs - looked at this often.
AWAY_SECONDS = 15 * 60
AWAY_LOOK_EVERY = 120

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
                 working: Optional[Callable[[], bool]] = None,
                 away: Optional[Callable[[], Optional[float]]] = None) -> None:
        self.config = config
        self.folder = Path(data_dir) / "updates"
        #: Seconds since the user last touched the computer - None where the
        #: system does not say (then a version is taken up at the next start).
        self.away = away or (lambda: None)
        self.notify = notify
        self.working = working or (lambda: False)
        self.quit: Optional[Callable[[], None]] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._looking = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.state: Dict[str, Any] = {"current": __version__, "checked_at": "", "just_updated": None,
                                      "versions": [],
                                      "latest": "", "newer": False, "page": "",
                                      "doing": "", "error": "", "can_install": False,
                                      "step": "", "progress": None, "updating_to": ""}
        #: A look the user asked for (Check now): what it finds is shown
        #: on the page, not told in a notice.
        self._for_you = False

    # -- the choice -----------------------------------------------------------
    def mode(self) -> str:
        chosen = str(self.config.get("updates", DEFAULT_MODE) or DEFAULT_MODE)
        return chosen if chosen in MODES else DEFAULT_MODE

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self.state, mode=self.mode(), installed_as=how_installed())

    def updating(self) -> Optional[Dict[str, Any]]:
        """An update under way - its version, its step and its download's
        share done - or None: sent with the page's regular status, so a
        page open anywhere follows an update it did not start."""
        with self._lock:
            if self.state.get("step") not in STEPS:
                return None
            return {"to": self.state.get("updating_to", ""), "step": self.state["step"],
                    "progress": self.state.get("progress"), "from": __version__}

    def _set(self, **values: Any) -> None:
        with self._lock:
            self.state.update(values)

    # -- the loop ---------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._said_updated()
        self._said_taken_back()
        self._thread = threading.Thread(target=self._loop, name="updates", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def check_now(self) -> Dict[str, Any]:
        """The user's "Check now": looked at at once - on the loop's thread,
        or, run from source where no loop looks by itself, on one of its own
        (it only looks: such a copy cannot replace itself)."""
        self._set(doing="checking", error="")
        self._for_you = True
        if self._thread is not None and self._thread.is_alive():
            self._wake.set()
        else:
            threading.Thread(target=self._look, name="updates-now", daemon=True).start()
        return self.status()

    def install_now(self, version: str = "") -> Dict[str, Any]:
        """The user's "Install this version" - the newest when none is said."""
        self._set(asked_to_install=str(version or "") or True)
        self._wake.set()
        return self.status()

    def _loop(self) -> None:
        # The first look waits a little after the start - unless the user
        # asks sooner (Check now, Install this version wake it).
        self._wake.wait(FIRST_CHECK_AFTER)
        self._wake.clear()
        looked_at = 0.0
        while not self._stop.is_set():
            if time.monotonic() - looked_at >= CHECK_EVERY_SECONDS or self._wake.is_set() \
                    or not looked_at:
                self._wake.clear()
                looked_at = time.monotonic()
                self._look()
            # MADE READY: taken up when the user is away and no work runs.
            if self._ready() is not None:
                self._take_up_when_away()
                self._wake.wait(AWAY_LOOK_EVERY)
            else:
                self._wake.wait(WAIT_FOR_WORK if self.state.get("waiting_for_work")
                                else CHECK_EVERY_SECONDS)

    def _look(self) -> None:
        """One look at a time: one asked for while another runs is that one."""
        if not self._looking.acquire(blocking=False):
            return
        try:
            self.once()
        except Exception as exc:  # never the agent's
            log.info("could not look for an update: %s", exc)
            self._set(error=str(exc)[:300], doing="")
        finally:
            self._looking.release()

    def once(self) -> Dict[str, Any]:
        """One look, and - where it is chosen and possible - one install."""
        # Asked for by the user, this look: what it finds is on the page.
        for_you, self._for_you = self._for_you, False
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
            self._tell_once(newest["tag"], newest["page"], quietly=for_you)
            return self.status()
        if chosen is None or not installable(chosen):
            self._set(asked_to_install=None)
            self._tell_once(newest["tag"], newest["page"], quietly=for_you)
            return self.status()
        from . import quiet_update as quiet
        if chosen["tag"] in quiet.skipped(self.folder):
            # It did not start here once: not again (a newer one will be).
            self._set(asked_to_install=None)
            return self.status()
        if quiet.QUIET_ASSET.get(kind) in chosen["files"] and quiet.can_make_ready(kind):
            # QUIETLY: made ready in the background - while work runs too,
            # it changes nothing yet - and taken up at the next start, when
            # the user is away, or now when they asked for it.
            ready = self._ready()
            if ready is None or ready.get("tag") != chosen["tag"]:
                self.make_ready(chosen, kind)
            self._set(asked_to_install=None)
            return self.status()
        if self.working():
            self._set(waiting_for_work=True, doing="waiting for work to finish")
            return self.status()
        self._set(waiting_for_work=False, asked_to_install=None)
        try:
            target = self.download(chosen, kind)
            self.install(target, kind, chosen["tag"], chosen)
        except Exception:
            # Not updating after all: the page stops following, and says why.
            self._set(step="", progress=None, updating_to="")
            raise
        return self.status()

    def _tell_once(self, tag: str, page: str, quietly: bool = False) -> None:
        """A newer version told once - in a notice, unless the user is
        looking at it already (`quietly`: Check now)."""
        if self.state.get("told") == tag:
            return
        self._set(told=tag)
        if quietly or self.notify is None:
            return
        try:
            self.notify(f"{APP_NAME} {tag} is available",
                        f"You have {__version__}. Settings > General updates it.",
                        kind="info", dedupe_key=f"update-{tag}",
                        action_hint=page, metadata={"update": tag})
        except Exception:  # pragma: no cover - a notice
            log.debug("could not tell of the update", exc_info=True)

    # -- download, checked ------------------------------------------------------
    def download(self, release: Dict[str, Any], kind: str, name: str = "",
                 shown: bool = True) -> Path:
        """One of the release's files, checked. `shown`: the page follows it
        (an update the user asked for); else it is fetched in the background,
        said only in Settings."""
        name = name or ASSET[kind]
        tag = release["tag"]
        size = int((release["files"].get(name) or {}).get("size") or 0)
        if shown:
            self._set(doing=f"downloading {tag}", step="downloading", updating_to=tag,
                      progress={"done": 0, "total": size})
        else:
            self._set(doing=f"getting {tag} ready in the background")
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
        done, said_at = 0, 0.0
        with _get(release["files"][name]["url"], timeout=60) as response, open(partial, "wb") as out:
            try:
                size = int(response.headers.get("Content-Length") or 0) or size
            except (AttributeError, TypeError, ValueError):
                pass
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
                done += len(chunk)
                if shown and time.monotonic() - said_at >= PROGRESS_EVERY:
                    said_at = time.monotonic()
                    self._set(progress={"done": done, "total": size})
                if self._stop.is_set():
                    raise RuntimeError("stopped while downloading")
        if shown:
            self._set(step="checking", progress={"done": done, "total": size or done})
        if digest.hexdigest() != expected:
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"{name} of {tag} does not match its checksum; deleted")
        os.replace(partial, target)
        return target

    # -- made ready, and taken up (quiet_update.py) --------------------------------
    def _ready(self) -> Optional[Dict[str, Any]]:
        from .quiet_update import staged
        return staged(self.folder)

    def make_ready(self, release: Dict[str, Any], kind: str, shown: bool = False) -> None:
        """The release made ready beside the program, nothing of the running
        one changed: downloaded, checked, unpacked (hard links for what did
        not change) - and what came down let go."""
        from . import quiet_update as quiet
        tag = release["tag"]
        archive = self.download(release, kind, name=quiet.QUIET_ASSET[kind], shown=shown)
        if shown:
            self._set(step="installing", doing=f"making {tag} ready")
        live = quiet.live_root(kind)
        where = quiet.place(kind)
        if live is None or where is None:
            raise RuntimeError("this copy cannot be updated quietly")
        quiet.forget_staged(self.folder)
        target = where / tag
        if kind == "windows":
            said = quiet.unpack_beside(archive, live, target, stop=self._stop.is_set)
            parts = sorted(target.iterdir())
            live_parts = [live / one.name for one in parts]
            log.info("%s made ready: %s files unchanged (linked), %s written",
                     tag, said["linked"], said["written"])
        elif kind == "macos":
            parts = [quiet.app_from_image(archive, target, self.folder)]
            live_parts = [live]
        else:
            target.mkdir(parents=True, exist_ok=True)
            fresh = target / live.name
            shutil.copyfile(archive, fresh)
            fresh.chmod(0o755)
            parts, live_parts = [fresh], [live]
        quiet._write(self.folder / quiet.STAGED, {
            "tag": tag, "kind": kind, "dir": str(target),
            "parts": [str(one) for one in parts], "live_parts": [str(one) for one in live_parts],
            "notes": release.get("notes", ""), "page": release.get("page", "")})
        shutil.rmtree(self.folder / tag, ignore_errors=True)
        self._set(doing="", ready=tag, ready_notes=release.get("notes", ""))

    def take_up_now(self) -> None:
        """The version made ready taken up now: its helper started, and the
        agent quits for it - started again on the new version, which the
        helper watches (quiet_update.run_helper)."""
        from . import quiet_update as quiet
        said = self._ready()
        if said is None:
            raise RuntimeError("no update is ready")
        tag = said["tag"]
        self._set(step="restarting", updating_to=tag, doing=f"starting {tag}")
        try:
            quiet.mark_taking_up(self.folder, said)
            port = 0 if self.config.get("api_enabled", True) is False else \
                int(self.config.get("api_port", 8756) or 8756)
            quiet.run_helper(said["kind"], self.folder, said, port, sys.argv[1:])
        except Exception:
            (self.folder / MARKER).unlink(missing_ok=True)
            self._set(step="", updating_to="", doing="")
            raise
        log.info("taking up %s: quitting so it starts in this one's place", tag)
        if self.quit is not None:
            self.quit()

    def restart_now(self) -> Dict[str, Any]:
        """The user's "Restart now": the version made ready, used at once -
        unless work runs, which it would end."""
        if self.working():
            raise RuntimeError("a piece of work is running: the new version is used "
                               "once it has finished, or at the next start")
        self.take_up_now()
        return self.status()

    def _take_up_when_away(self) -> None:
        """Taken up once the user has been away a while and no work runs -
        never under their hands; where the system does not say how long,
        at the next start. (A version is made ready only by itself, chosen
        so, or when the user asked for it.)"""
        if self.mode() == "off" or self.quit is None or self.working():
            return
        try:
            away = self.away()
        except Exception:
            away = None
        if away is None or away < AWAY_SECONDS:
            return
        try:
            self.take_up_now()
        except Exception as exc:
            log.info("the update made ready was not taken up: %s", exc)
            self._set(error=str(exc)[:300])

    def _said_taken_back(self) -> None:
        """A version that did not start, and was taken back: said once, and
        never made ready again (quiet_update.take_result)."""
        from .quiet_update import take_result
        said = take_result(self.folder)
        if not said or said.get("ok"):
            return
        why = str(said.get("why") or "it did not start")
        self._set(error=f"Version {said.get('tag')} was not kept: {why}. "
                        f"This version, {__version__}, goes on as before.")
        if self.notify is None:
            return
        try:
            self.notify(f"{APP_NAME} stayed on {__version__}",
                        f"Version {said.get('tag')} did not start, so it was taken back.",
                        kind="warning", dedupe_key=f"taken-back-{said.get('tag')}")
        except Exception:  # pragma: no cover - a notice
            log.debug("could not say an update was taken back", exc_info=True)

    # -- install ------------------------------------------------------------------
    def install(self, target: Path, kind: str, tag: str,
                release: Optional[Dict[str, Any]] = None) -> None:
        self._set(doing=f"installing {tag}", step="installing", updating_to=tag)
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
        self._set(step="restarting")
        if self.quit is not None:
            self.quit()

    def _language(self) -> str:
        code = str(self.config.get("display_language", "en") or "en").split("-")[0].lower()
        return code if code in ("en", "zh", "es", "ja", "ko", "ru", "fr", "de", "it") else "en"

    def _after_quit_windows(self, installer: Path) -> None:
        """The installer, once this program has gone: started by a shell of
        its own, outside this program's job, so it outlives it. /SILENT: no
        question asked, and its own progress window shown - the agent and its
        page are gone meanwhile, and something must say it is being done.

        WITH NO WINDOW, NOT DETACHED (found 2026-10-27, an update from 0.2.0
        that quit and never came back): PowerShell started detached - no
        console at all - ends at once without running its command, so the
        installer was never started and the agent simply went away. Every
        Windows update until 0.2.8 did so. And the shell says it is there
        before this program quits (`HELPER_READY`): one that is not, and
        nothing is quit - the update is said to have failed instead."""
        # A quote in a path is doubled, as PowerShell reads it.
        quoted = str(installer).replace("'", "''")
        ready = self.folder / HELPER_READY
        ready.unlink(missing_ok=True)
        marked = str(ready).replace("'", "''")
        script = (f"Set-Content -LiteralPath '{marked}' -Value 'ready'; "
                  f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; "
                  f"Start-Process -FilePath '{quoted}' -ArgumentList "
                  f"'/SILENT','/SUPPRESSMSGBOXES','/NORESTART','/LANG={self._language()}'")
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                 | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
        command = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-Command", script]
        try:
            subprocess.Popen(command, creationflags=flags, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError:
            # A job that may not be left: started inside it, it still ends
            # with this program - so not breaking away is not an option
            # worth having. Said, and left to the user.
            subprocess.Popen(command, creationflags=flags & ~getattr(
                subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
        until = time.monotonic() + HELPER_READY_WITHIN
        while not ready.exists():
            if time.monotonic() > until:
                raise RuntimeError("the installer could not be started (its helper did not "
                                   "answer); nothing was changed - try again, or install "
                                   f"{installer.name} yourself")
            time.sleep(0.1)
        ready.unlink(missing_ok=True)

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

    def start_again(self) -> bool:
        """Quit and open again - on a Mac, what makes Screen Recording,
        switched on while the agent ran, apply (macOS answers a running
        program as it was when it started). A packaged Mac build only."""
        if sys.platform != "darwin" or not getattr(sys, "frozen", False) or self.quit is None:
            return False
        executable = Path(sys.executable).resolve()
        bundle = next((one for one in executable.parents if one.suffix == ".app"), None)
        if bundle is None:
            return False
        log.info("starting again, as the user asked")
        self._reopen(["open", str(bundle)])
        self.quit()
        return True

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

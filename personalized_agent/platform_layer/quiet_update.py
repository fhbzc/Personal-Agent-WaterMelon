"""UPDATES THE USER DOES NOT FEEL (the user, 2026-10-27: "like Claude CLI's").

A newer version is made ready in the background while the agent runs - the
user never waits for it - and taken up when the agent next starts, or when
the user has been away a while and no work runs: a restart of seconds.

MADE READY BESIDE THE PROGRAM, on the same disk, so taking it up is a few
renames and nothing is copied then:

  - WINDOWS: the release's portable folder, unpacked into
    `<program>\\.update\\<tag>`; every file the same as the running one's
    (size and CRC) is a hard link to it - the 700 MB of libraries that do
    not change take no room and no time - and only what changed is written;
  - MACOS: the application from the .dmg, copied beside the user's own into
    a hidden folder (`.PersonalAgentWaterMelon-update`) - whole, as it was
    signed, so macOS keeps what it allowed it;
  - LINUX: the new AppImage, in the same hidden folder beside the old.

TAKEN UP by a helper of its own, once the agent has gone: the running parts
moved aside (`previous`), the new ones moved in, the program started - and
WATCHED. Unless it is up within two minutes (its page answering, or alive,
where it has no page), it is ended, the old parts moved back, the old
version started, and the version is never made ready again
(`SKIPPED`). A release that will not start costs the user seconds.

A copy that cannot do this - the program's folder not the user's to write
(installed for every user), no release file for it - is updated the old
way: the installer, once the agent has quit (updater.py).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import zipfile
import zlib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..version import APP_NAME, __version__

log = logging.getLogger(__name__)

#: What each way of being installed is made ready from.
QUIET_ASSET = {"windows": "PersonalAgentWaterMelon-portable-windows-x64.zip",
               "macos": "PersonalAgentWaterMelon-macos.dmg",
               "appimage": "PersonalAgentWaterMelon-linux-x86_64.AppImage"}
#: In the updates folder: the version made ready, what the helper said of
#: taking it up, and the versions that would not start.
STAGED = "staged.json"
RESULT = "applied.json"
SKIPPED = "skipped.json"
#: A new version is up within this long, or it is taken back.
HEALTHY_WITHIN = 120
#: With no page to ask, alive this long counts as up.
ALIVE_IS_UP = 30
#: The helper says it is there within this long, or nothing is quit.
READY_WITHIN = 15.0
#: A version whose parts would not move is tried this many times in all.
MOST_TRIES = 3
#: Beside the program on a Mac or Linux, hidden.
HIDDEN_PLACE = ".PersonalAgentWaterMelon-update"
#: The Windows installer's own entry, kept saying the version now there.
UNINSTALL_KEY = (r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall"
                 r"\{7C4E7B0E-2B4E-4E6B-9E52-5F1C9C7A1D21}_is1")


# -- where -------------------------------------------------------------------------
def live_root(kind: str) -> Optional[Path]:
    """What is replaced: the program's folder (Windows), the application
    (macOS), the AppImage (Linux)."""
    executable = Path(sys.executable).resolve()
    if kind == "windows":
        return executable.parent
    if kind == "macos":
        return next((one for one in executable.parents if one.suffix == ".app"), None)
    if kind == "appimage":
        image = os.environ.get("APPIMAGE", "")
        return Path(image) if image else None
    return None


def place(kind: str) -> Optional[Path]:
    """Where a version is made ready: beside what it replaces, on its disk."""
    live = live_root(kind)
    if live is None:
        return None
    return live / ".update" if kind == "windows" else live.parent / HIDDEN_PLACE


def can_make_ready(kind: str) -> bool:
    """Whether this copy can be updated quietly: the folder it is in is the
    user's to write (one installed for every user is not)."""
    where = place(kind)
    if where is None:
        return False
    try:
        where.mkdir(parents=True, exist_ok=True)
        probe = where / ".probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


# -- made ready ----------------------------------------------------------------------
def _crc_of(path: Path) -> int:
    crc = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


def unpack_beside(archive: Path, live: Path, target: Path,
                  stop: Optional[Callable[[], bool]] = None) -> Dict[str, int]:
    """The portable folder unpacked into `target`: a file the same as the
    running one's (its size, then its CRC) is a hard link to it - nothing
    written - and the rest is written. What was linked and written."""
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    linked = written = 0
    with zipfile.ZipFile(archive) as packed:
        for info in packed.infolist():
            if stop is not None and stop():
                raise RuntimeError("stopped while making the update ready")
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise RuntimeError(f"the update holds a path outside itself: {name}")
            out = target / name
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            same = live / name
            try:
                if same.is_file() and same.stat().st_size == info.file_size \
                        and _crc_of(same) == info.CRC:
                    os.link(same, out)
                    linked += 1
                    continue
            except OSError:
                pass                    # another disk, or a file in use: written
            with packed.open(info) as source, open(out, "wb") as sink:
                shutil.copyfileobj(source, sink, 1 << 20)
            written += 1
    return {"linked": linked, "written": written}


def app_from_image(image: Path, target: Path, work: Path) -> Path:
    """The application in a .dmg, copied whole (as it was signed) into
    `target`."""
    mount = work / "mounted"
    subprocess.run(["hdiutil", "detach", str(mount), "-force"], capture_output=True)
    mount.mkdir(parents=True, exist_ok=True)
    subprocess.run(["hdiutil", "attach", str(image), "-nobrowse", "-readonly",
                    "-mountpoint", str(mount)], check=True, capture_output=True, timeout=300)
    try:
        found = next((one for one in mount.iterdir() if one.suffix == ".app"), None)
        if found is None:
            raise RuntimeError("the disk image holds no application")
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        subprocess.run(["ditto", str(found), str(target / found.name)], check=True,
                       capture_output=True, timeout=900)
        return target / found.name
    finally:
        subprocess.run(["hdiutil", "detach", str(mount), "-force"], capture_output=True)


# -- what is kept of it ---------------------------------------------------------------
def _read(path: Path) -> Any:
    try:
        # With or without a byte-order mark: Windows PowerShell writes one.
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def skipped(folder: Path) -> List[str]:
    return list(_read(folder / SKIPPED) or [])


def skip(folder: Path, tag: str) -> None:
    _write(folder / SKIPPED, sorted(set(skipped(folder)) | {tag}))


def staged(folder: Path) -> Optional[Dict[str, Any]]:
    """The version made ready, when it is still there and newer than this
    one - else None (and what is left of it forgotten)."""
    said = _read(folder / STAGED)
    if not isinstance(said, dict):
        return None
    from .updater import is_newer
    parts = [Path(one) for one in said.get("parts") or []]
    if not parts or not all(one.exists() for one in parts) or not is_newer(str(said.get("tag"))) \
            or said.get("tag") in skipped(folder):
        forget_staged(folder, said)
        return None
    return said


def forget_staged(folder: Path, said: Optional[Dict[str, Any]] = None) -> None:
    said = said if said is not None else _read(folder / STAGED)
    (folder / STAGED).unlink(missing_ok=True)
    if isinstance(said, dict) and said.get("dir"):
        shutil.rmtree(said["dir"], ignore_errors=True)


def heed_result(folder: Path) -> Optional[Dict[str, Any]]:
    """What the helper said of the last taking up: a version that would not
    start is never made ready again, and what was left of it goes. Kept, to
    be told once (`take_result`)."""
    said = _read(folder / RESULT)
    if isinstance(said, dict) and not said.get("ok") and said.get("tag") \
            and not said.get("heeded"):
        if "did not start" in str(said.get("why") or ""):
            skip(folder, str(said["tag"]))
            forget_staged(folder)
        else:
            # A part that would not move (a file held open a moment too
            # long) - put back as it was: tried again, three times in all.
            ready = _read(folder / STAGED)
            if isinstance(ready, dict) and ready.get("tag") == said["tag"]:
                ready["tries"] = int(ready.get("tries") or 0) + 1
                _write(folder / STAGED, ready)
                if ready["tries"] >= MOST_TRIES:
                    skip(folder, str(said["tag"]))
                    forget_staged(folder)
        said["heeded"] = True
        _write(folder / RESULT, said)
    return said if isinstance(said, dict) else None


def take_result(folder: Path) -> Optional[Dict[str, Any]]:
    """`heed_result`, read once: what to tell the user."""
    said = heed_result(folder)
    (folder / RESULT).unlink(missing_ok=True)
    return said


# -- taken up -------------------------------------------------------------------------
def _ps(text: str) -> str:
    """A PowerShell single-quoted string."""
    return "'" + str(text).replace("'", "''") + "'"


def _sh(text: str) -> str:
    """A shell single-quoted string."""
    return "'" + str(text).replace("'", "'\\''") + "'"


def windows_helper(pid: int, pairs: List[List[str]], start: List[str], port: int,
                   result: Path, ready: Path, tag: str, program: Path) -> str:
    """The PowerShell that takes a version up once the agent `pid` has gone:
    each [live, new, aside] moved, the program started and watched, and all
    moved back - the old one started - unless it is up in time."""
    exe, args = start[0], start[1:]
    lines = [
        "$ErrorActionPreference = 'Continue'",
        f"Set-Content -LiteralPath {_ps(ready)} -Value 'ready'",
        f"$result = {_ps(result)}",
        # WHAT IT DID, STEP BY STEP, WITH THE TIME - beside what it says.
        f"$steps = {_ps(str(result) + '.log')}",
        "function Note($what) { Add-Content -LiteralPath $steps -Encoding UTF8 "
        "-Value ((Get-Date -Format 'HH:mm:ss.fff') + ' ' + $what) }",
        "Note 'started'",
        "function Say($ok, $why) { @{ok = $ok; tag = " + _ps(tag) + "; why = $why; "
        "at = (Get-Date -Format o)} | ConvertTo-Json | "
        "Set-Content -LiteralPath $result -Encoding UTF8 }",
        f"Wait-Process -Id {int(pid)} -Timeout 120 -ErrorAction SilentlyContinue",
        "Note 'the agent has gone'",
        # And whatever it started from its folder (a worker of its own) - a
        # file still open would keep its part from moving. Twenty seconds
        # at most, by the clock.
        f"$home_of = {_ps(str(program))}",
        "$until = (Get-Date).AddSeconds(20)",
        "while ((Get-Date) -lt $until) { $left = Get-Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Path -and $_.Path.StartsWith($home_of, "
        "[System.StringComparison]::OrdinalIgnoreCase) }; "
        "if (-not $left) { break }; Start-Sleep -Milliseconds 500 }",
        "Note ('its folder free' + $(if ($left) { ' - not: ' + @($left).Count + ' left' } "
        "else { '' }))",
        "function Move-Retry($from, $to) { for ($i = 0; $i -lt 60; $i++) { "
        "try { Move-Item -LiteralPath $from -Destination $to -ErrorAction Stop; return $true } "
        "catch { Start-Sleep -Milliseconds 500 } }; return $false }",
        # Added one by one: an array of one array is flattened by PowerShell.
        "$pairs = New-Object System.Collections.ArrayList",
        *["[void]$pairs.Add(@(" + ", ".join(_ps(one) for one in pair) + "))"
          for pair in pairs],
        "foreach ($p in $pairs) { if (Test-Path -LiteralPath $p[2]) { "
        "Remove-Item -LiteralPath $p[2] -Recurse -Force -ErrorAction SilentlyContinue }; "
        "New-Item -ItemType Directory -Force -Path (Split-Path -Parent $p[2]) | Out-Null }",
        "$done = New-Object System.Collections.ArrayList",
        "$failed = $null",
        "foreach ($p in $pairs) {",
        "  if (-not (Move-Retry $p[0] $p[2])) { $failed = \"could not move $($p[0]) aside\"; break }",
        "  [void]$done.Add(@($p[2], $p[0]))",
        "  if (-not (Move-Retry $p[1] $p[0])) { $failed = \"could not move the new $($p[0]) in\"; break }",
        "  [void]$done.Add(@($p[0], $p[1]))",
        "}",
        "function Undo { for ($i = $done.Count - 1; $i -ge 0; $i--) { "
        "[void](Move-Retry $done[$i][0] $done[$i][1]) } }",
        f"$exe = {_ps(exe)}",
        "$arguments = @(" + ", ".join(_ps(one) for one in args) + ")",
        "function Start-It { if ($arguments.Count) { Start-Process -FilePath $exe "
        "-ArgumentList $arguments -PassThru } else { Start-Process -FilePath $exe -PassThru } }",
        "Note $(if ($failed) { $failed } else { 'moved in' })",
        "if ($failed) { Undo; Note 'moved back'; [void](Start-It); Say $false $failed; exit }",
        "$started = Start-It",
        "Note 'started the new version'",
        "$up = $false",
        f"for ($i = 0; $i -lt {HEALTHY_WITHIN}; $i++) {{",
        "  Start-Sleep -Seconds 1",
        "  if ($started.HasExited) { break }",
        (f"  if (Get-NetTCPConnection -LocalPort {int(port)} -State Listen "
         "-ErrorAction SilentlyContinue) { $up = $true; break }") if port else
        f"  if ($i -ge {ALIVE_IS_UP}) {{ $up = $true; break }}",
        "}",
        "Note $(if ($up) { 'it is up' } elseif ($started.HasExited) { 'it ended' } "
        "else { 'not up in time' })",
        "if (-not $up) {",
        "  if (-not $started.HasExited) { & taskkill.exe /T /F /PID $started.Id | Out-Null; "
        "Start-Sleep -Seconds 2 }",
        "  Undo; Note 'moved back'; [void](Start-It); Note 'the old version started'",
        "  Say $false 'the new version did not start'; exit",
        "}",
        f"if (Test-Path {_ps(UNINSTALL_KEY)}) {{ "
        f"Set-ItemProperty -Path {_ps(UNINSTALL_KEY)} -Name DisplayVersion -Value {_ps(tag.lstrip('v'))} "
        "-ErrorAction SilentlyContinue; "
        f"Set-ItemProperty -Path {_ps(UNINSTALL_KEY)} -Name DisplayName "
        f"-Value {_ps(APP_NAME + ' ' + tag.lstrip('v'))} -ErrorAction SilentlyContinue }}",
        "Say $true ''",
        "Note 'done'",
    ]
    return "\r\n".join(lines) + "\r\n"


def posix_helper(pid: int, pairs: List[List[str]], start: List[str], port: int,
                 result: Path, ready: Path, tag: str, kill_pattern: str) -> str:
    """The shell that does the same on a Mac or Linux."""
    moves_in, moves_back = [], []
    for live, new, aside in pairs:
        moves_in.append(f"rm -rf {_sh(aside)}; mkdir -p \"$(dirname {_sh(aside)})\"; "
                        f"mv {_sh(live)} {_sh(aside)} || {{ failed='could not move it aside'; }}; "
                        f"[ -z \"$failed\" ] && {{ mv {_sh(new)} {_sh(live)} || "
                        f"{{ failed='could not move the new one in'; }}; }}")
        moves_back.insert(0, f"if [ -e {_sh(aside)} ]; then rm -rf {_sh(new)}; "
                             f"[ -e {_sh(live)} ] && mv {_sh(live)} {_sh(new)}; "
                             f"mv {_sh(aside)} {_sh(live)}; fi")
    start_line = " ".join(_sh(one) for one in start)
    if port:
        check = (f"(echo > /dev/tcp/127.0.0.1/{int(port)}) >/dev/null 2>&1 || "
                 f"(command -v lsof >/dev/null && lsof -nP -iTCP:{int(port)} -sTCP:LISTEN "
                 ">/dev/null 2>&1)")
    else:
        check = f"[ \"$i\" -ge {ALIVE_IS_UP} ]"
    return "\n".join([
        "#!/bin/bash",
        f"echo ready > {_sh(ready)}",
        f"say() {{ printf '{{\"ok\": %s, \"tag\": \"%s\", \"why\": \"%s\"}}' \"$1\" {_sh(tag)} \"$2\" "
        f"> {_sh(result)}; }}",
        f"while kill -0 {int(pid)} 2>/dev/null; do sleep 0.5; done",
        "failed=''",
        *moves_in,
        "undo() {",
        *["  " + one for one in moves_back],
        "}",
        f"start() {{ {start_line} >/dev/null 2>&1 & }}",
        "if [ -n \"$failed\" ]; then undo; start; say false \"$failed\"; exit 0; fi",
        "start",
        "up=''",
        f"for i in $(seq 1 {HEALTHY_WITHIN}); do sleep 1; if {check}; then up=1; break; fi; done",
        "if [ -z \"$up\" ]; then",
        f"  pkill -f {_sh(kill_pattern)} 2>/dev/null; sleep 2",
        "  undo; start; say false 'the new version did not start'; exit 0",
        "fi",
        "say true ''",
    ]) + "\n"


def run_helper(kind: str, folder: Path, said: Dict[str, Any], port: int,
               argv: List[str]) -> None:
    """The helper started, outside this program's job - and heard from -
    before the caller quits; else RuntimeError, and nothing is quit."""
    tag = str(said["tag"])
    live = live_root(kind)
    if live is None:
        raise RuntimeError("this copy is not one that can be updated quietly")
    aside = Path(said["dir"]).parent / "previous"
    pairs = [[str(live_part), str(new), str(aside / Path(live_part).name)]
             for live_part, new in zip(said["live_parts"], said["parts"])]
    ready = folder / "helper-ready.txt"
    ready.unlink(missing_ok=True)
    (folder / RESULT).unlink(missing_ok=True)
    if kind == "windows":
        start = [str(live / Path(sys.executable).name), *argv]
        script = folder / "take-up.ps1"
        script.write_text(windows_helper(os.getpid(), pairs, start, port, folder / RESULT,
                                         ready, tag, live), encoding="utf-8-sig")
        command = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(script)]
        # NO WINDOW, NEVER DETACHED: a detached PowerShell runs nothing (updater.py).
        flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                 | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
        try:
            subprocess.Popen(command, creationflags=flags, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError:
            subprocess.Popen(command, creationflags=flags & ~getattr(
                subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        if kind == "macos":
            start, kill = ["open", str(live)], f"{live}/Contents/MacOS/"
        else:
            start, kill = [str(live), *argv], str(live)
        script = folder / "take-up.sh"
        script.write_text(posix_helper(os.getpid(), pairs, start, port, folder / RESULT,
                                       ready, tag, kill), encoding="utf-8")
        script.chmod(0o755)
        subprocess.Popen(["/bin/bash", str(script)], start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    until = time.monotonic() + READY_WITHIN
    while not ready.exists():
        if time.monotonic() > until:
            raise RuntimeError("the update's helper did not answer; nothing was changed")
        time.sleep(0.1)
    ready.unlink(missing_ok=True)


def mark_taking_up(folder: Path, said: Dict[str, Any]) -> None:
    """What the new version opens with (updater.py `_said_updated`): from
    which version, and what the release says of itself. A version that does
    not start is not this one, and the mark is let go by the old."""
    from .updater import MARKER
    _write(folder / MARKER, {"from": __version__, "to": said["tag"],
                             "notes": said.get("notes", ""), "page": said.get("page", "")})


# -- at the start ---------------------------------------------------------------------
def port_of(root: Path) -> int:
    """The page's port, as the settings say - 0 when it cannot be read."""
    said = _read(Path(root) / "internal" / "settings" / "settings.json")
    try:
        return int((said or {}).get("api_port") or 8756)
    except (TypeError, ValueError):
        return 8756


def take_up_at_start(root: Path, argv: List[str], no_api: bool = False) -> bool:
    """A version made ready earlier, taken up as the agent starts - before
    it opens anything: the helper is started and this run ends (True), the
    new version starting in its place. False: nothing to take up."""
    from .updater import how_installed
    kind = how_installed()
    if not kind:
        return False
    folder = Path(root) / "internal" / "updates"
    heed_result(folder)                 # one that would not start: never again
    said = staged(folder)
    if said is None or said.get("kind") != kind:
        return False
    try:
        mark_taking_up(folder, said)
        run_helper(kind, folder, said, 0 if no_api else port_of(root), argv)
    except Exception as exc:
        log.warning("the update made ready could not be taken up: %s", exc)
        from .updater import MARKER
        (folder / MARKER).unlink(missing_ok=True)
        skip(folder, str(said["tag"]))
        forget_staged(folder, said)
        return False
    log.info("taking up %s: this start ends, the new version starts", said["tag"])
    return True

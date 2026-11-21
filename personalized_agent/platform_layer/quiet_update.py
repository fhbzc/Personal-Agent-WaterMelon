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
(`SKIPPED`). A release that will not start costs the user seconds. A part
that would not move - what runs from the program's folder is ended first, as
its own; a program outside it may still hold a file there - is said, with
what held it, and tried again later: an hour, then longer, never skipped.

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
#: The helper says it is there within this long, or nothing is quit - a minute: PowerShell starting cold, a virus
#: scan looking at it first (2026-11-20: a test copy's helper said so after the agent had stopped waiting).
READY_WITHIN = 60.0
#: WHEN THE AGENT STOPPED WAITING FOR ITS HELPER, SAID (2026-11-20: the agent gave up and stayed; its helper, late,
#: waited the two minutes for it to quit and then ended it - an update that changed nothing, and the agent gone): the
#: helper, seeing this beside its "ready", does nothing and leaves.
ABORTED = "helper-abort.txt"
#: A version whose parts would not move is tried again after a wait - an hour, then twice as long each time, a day
#: at most - never given up for it (2026-11-16: a file held open is not a version that will not start).
FIRST_WAIT_SECONDS = 3600
LONGEST_WAIT_SECONDS = 86400
#: How long the helper waits for the agent itself to quit, before it ends it.
AGENT_GONE_WITHIN = 120
#: How long the helper waits for what runs from the program's folder to end by itself, before it ends it.
LEFT_WAIT_SECONDS = 20
#: And how long for what it ended to be gone.
ENDED_WAIT_SECONDS = 10
#: Each part's move tried so many times, half a second apart.
MOVE_TRIES = 60
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


#: THE VERSION BEFORE, SET ASIDE, KEPT A DAY (the user, 2026-11-15: "新版本启动成功、正常运行一段时间（比如一天）之后，
#: 自动删掉 previous"): the new one not up, it is moved back within minutes; up and run a day, it goes - a
#: version gone back to after that is downloaded again.
PREVIOUS = "previous"
ASIDE_AT = "set-aside-at.txt"
PREVIOUS_KEPT_SECONDS = 24 * 3600


def let_previous_go(kind: str, now: Optional[float] = None) -> bool:
    """The version set aside at the last update, let go once it has been kept a day - by when it was set aside
    (its folder's own time where that was not said). Whether it went."""
    where = place(kind)
    if where is None:
        return False
    previous = where / PREVIOUS
    if not previous.is_dir():
        return False
    try:
        said = (previous / ASIDE_AT).read_text(encoding="utf-8").strip()
        at = float(said)
    except (OSError, ValueError):
        try:
            at = previous.stat().st_mtime
        except OSError:
            return False
    if (now if now is not None else time.time()) - at < PREVIOUS_KEPT_SECONDS:
        return False
    shutil.rmtree(previous, ignore_errors=True)
    gone = not previous.exists()
    if gone:
        log.info("the version before this one, set aside a day ago, let go")
    return gone


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
                  stop: Optional[Callable[[], bool]] = None,
                  told: Optional[Callable[[int, int], None]] = None) -> Dict[str, int]:
    """The portable folder unpacked into `target`: a file the same as the
    running one's (its size, then its CRC) is a hard link to it - nothing
    written - and the rest is written. What was linked and written.
    `told(done, total)`: how many of its files are done, as it goes."""
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    linked = written = 0
    with zipfile.ZipFile(archive) as packed:
        listed = packed.infolist()
        for at, info in enumerate(listed):
            if told is not None:
                told(at, len(listed))
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
        if told is not None:
            told(len(listed), len(listed))
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


def wait_after(tries: int) -> float:
    """How long after its `tries`-th failure to move a version is tried again: an hour, doubling, a day at most."""
    return float(min(LONGEST_WAIT_SECONDS, FIRST_WAIT_SECONDS * 2 ** max(0, int(tries) - 1)))


def not_yet(folder: Path, now: Optional[float] = None) -> float:
    """Seconds until the version made ready may be taken up again by itself - 0 when it may now."""
    said = _read(folder / STAGED)
    if not isinstance(said, dict):
        return 0.0
    return max(0.0, float(said.get("not_before") or 0) - (time.time() if now is None else now))


def skipped(folder: Path) -> List[str]:
    return list(_read(folder / SKIPPED) or [])


def skip(folder: Path, tag: str) -> None:
    _write(folder / SKIPPED, sorted(set(skipped(folder)) | {tag}))


def staged(folder: Path) -> Optional[Dict[str, Any]]:
    """The version made ready, when it is still there and newer than this
    one, or older and chosen to go back to - else None (and what is left of
    it forgotten)."""
    said = _read(folder / STAGED)
    if not isinstance(said, dict):
        return None
    from .updater import can_go_back_to, is_newer
    parts = [Path(one) for one in said.get("parts") or []]
    tag = str(said.get("tag"))
    wanted = is_newer(tag) or (said.get("back") and can_go_back_to(tag))
    if not parts or not all(one.exists() for one in parts) or not wanted \
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
            # A part that would not move (a file held open) - put back as it was, and tried again after a wait,
            # longer each time; never given up for it (2026-11-16): the version itself was never at fault.
            ready = _read(folder / STAGED)
            if isinstance(ready, dict) and ready.get("tag") == said["tag"]:
                ready["tries"] = int(ready.get("tries") or 0) + 1
                ready["not_before"] = time.time() + wait_after(ready["tries"])
                _write(folder / STAGED, ready)
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
        # ONE AT A TIME (2026-11-16: a version taken up twice while the old one was slow to close - two helpers, the
        # second undoing what the first had done): one already at work is left to it.
        f"$lock = {_ps(str(result) + '.helper')}",
        "$other = $null; if (Test-Path -LiteralPath $lock) { $other = Get-Content -LiteralPath $lock "
        "-ErrorAction SilentlyContinue | Select-Object -First 1 }",
        "if ($other -and ($other -as [int]) -and ($other -as [int]) -ne $PID -and "
        "(Get-Process -Id ($other -as [int]) -ErrorAction SilentlyContinue)) { "
        "Note ('another helper is at work (' + $other + ') - left to it'); exit }",
        "Set-Content -LiteralPath $lock -Value $PID",
        "function Say($ok, $why) { @{ok = $ok; tag = " + _ps(tag) + "; why = $why; "
        "at = (Get-Date -Format o)} | ConvertTo-Json | "
        "Set-Content -LiteralPath $result -Encoding UTF8 }",
        # WAITED FOR, UNLESS IT STAYED: the agent gone - or, had it stopped waiting for this helper and stayed
        # (ABORTED beside "ready"), nothing done at all.
        f"$abort = {_ps(Path(ready).parent / ABORTED)}",
        f"$until = (Get-Date).AddSeconds({int(AGENT_GONE_WITHIN)})",
        f"while ((Get-Date) -lt $until -and (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue)) {{ "
        "if (Test-Path -LiteralPath $abort) { break }; Start-Sleep -Milliseconds 500 }",
        "if (Test-Path -LiteralPath $abort) { Note 'the agent stopped waiting for this helper and stayed - nothing done'; "
        "Remove-Item -LiteralPath $abort -ErrorAction SilentlyContinue; "
        "Remove-Item -LiteralPath $lock -ErrorAction SilentlyContinue; exit }",
        # GONE, OR ENDED (2026-11-16: the old copy was still closing after two minutes - said to have gone - and its
        # folder would not move): it quits for this, so what is left of it is ended - IT ALONE, never its tree
        # (2026-11-20: "taskkill /T" ended this helper too, a child of the agent's, and the update stopped there).
        f"if (Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue) {{ "
        f"Note 'the agent is still closing - ended'; & taskkill.exe /F /PID {int(pid)} 2>&1 | Out-Null; "
        "Start-Sleep -Seconds 2 } else { Note 'the agent has gone' }",
        # And whatever runs from its folder (a worker of its own; the browser
        # extension's host, which the browser started and keeps while pages are
        # read) - a file still open would keep its part from moving. Waited for
        # a while; then ended: each is this program's own (2026-11-16: a
        # user's update would not move "_internal" aside).
        f"$home_of = {_ps(str(program))}",
        "function Left { Get-Process -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Path -and $_.Path.StartsWith($home_of, "
        "[System.StringComparison]::OrdinalIgnoreCase) } }",
        "function Named($list) { (@($list) | ForEach-Object { $_.ProcessName + ' (' + $_.Id + ')' }) -join ', ' }",
        f"$until = (Get-Date).AddSeconds({int(LEFT_WAIT_SECONDS)})",
        "while ((Get-Date) -lt $until) { $left = Left; if (-not $left) { break }; Start-Sleep -Milliseconds 500 }",
        "if ($left) {",
        "  Note ('still running from its folder: ' + (Named $left) + ' - ended')",
        "  foreach ($one in @($left)) { & taskkill.exe /T /F /PID $one.Id 2>&1 | Out-Null }",
        f"  $until = (Get-Date).AddSeconds({int(ENDED_WAIT_SECONDS)})",
        "  while ((Get-Date) -lt $until) { $left = Left; if (-not $left) { break }; Start-Sleep -Milliseconds 500 }",
        "}",
        "Note ('its folder free' + $(if ($left) { ' - not: ' + @($left).Count + ' left' } "
        "else { '' }))",
        f"function Move-Retry($from, $to) {{ for ($i = 0; $i -lt {int(MOVE_TRIES)}; $i++) {{ "
        "try { Move-Item -LiteralPath $from -Destination $to -ErrorAction Stop; return $true } "
        "catch { Start-Sleep -Milliseconds 500 } }; return $false }",
        # WHY IT WOULD NOT MOVE, SAID: what still runs from the folder, else something outside it.
        "function Held { $still = Left; if ($still) { 'still running from its folder: ' + (Named $still) } "
        "else { 'a program outside it has a file in it open - a File Explorer window or a terminal there, "
        "or a virus scan' } }",
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
        "  if (-not (Move-Retry $p[0] $p[2])) { $failed = \"could not move $($p[0]) aside ($(Held))\"; break }",
        "  [void]$done.Add(@($p[2], $p[0]))",
        "  if (-not (Move-Retry $p[1] $p[0])) { $failed = \"could not move the new $($p[0]) in ($(Held))\"; break }",
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
        f"while kill -0 {int(pid)} 2>/dev/null; do [ -e {_sh(Path(ready).parent / ABORTED)} ] && "
        f"{{ rm -f {_sh(Path(ready).parent / ABORTED)}; exit 0; }}; sleep 0.5; done",
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
    # WHEN IT WAS SET ASIDE, said beside it: let go a day after (`let_previous_go`).
    try:
        aside.mkdir(parents=True, exist_ok=True)
        (aside / ASIDE_AT).write_text(str(time.time()), encoding="utf-8")
    except OSError:
        log.debug("when the old version was set aside could not be said", exc_info=True)
    ready = folder / "helper-ready.txt"
    ready.unlink(missing_ok=True)
    (folder / ABORTED).unlink(missing_ok=True)          # a stay said for an earlier helper
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
            # STAYING, SAID TO THE HELPER: should it start after all, it does nothing (ABORTED).
            try:
                (folder / ABORTED).write_text("stayed", encoding="utf-8")
            except OSError:
                log.debug("the helper could not be told the agent stayed", exc_info=True)
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

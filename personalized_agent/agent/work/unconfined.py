"""WORK WITHOUT THE SANDBOX (the user, 2026-10-25): an option of the user's
own work - never of a Work debugger way - with a warning beside it.

WHAT IS OPENED. The client's own tools: Claude Code's Bash, PowerShell,
Write, Edit, its web tools and its agents - run on this computer, as the
user's account, not in the sandbox. Its permission system still decides:
it runs in its "auto" mode, and whatever would need a person's approval is
REFUSED at once (no one is there to answer); the model is told so, and
finds another way. A model with no tools of its own (Codex, whose own
reading no hook can hold back; a model reached by its API) is given one of
the agent's instead - run_outside - which runs a command on this computer
when a short judgment of it finds it needs no approval, and refuses it
otherwise, the same way.

WHAT IS NEVER DONE, sandbox or not, approved or not (`forbidden`):
  - the agent's own data - its database, settings, what it learned - read
    or written: what a model changed there would change the agent itself;
  - where sign-ins are kept - browser profiles and their cookies, .ssh,
    cloud and tool credentials, the system's credential store - read;
  - anything written OUT with the user's accounts: a git push, a package
    published, an issue, a post, a message, an upload.
Claude Code is held to it by its deny rules (its file tools) and by a hook
the agent answers before every command (`hook`); run_outside and every
approval by the same check. A command's words are what is checked - a
command can be written to hide what it does - so the client's own
judgment ("auto") stays the second line, and the sandbox the first.

ONLY WHEN EVERY OTHER WAY WAS TRIED (`request_approval`): a model refused
may ask the user to approve ONE exact command - saying why, and the ways it
tried - only after the run has seen it refused, and having tried two ways.
The run waits for the user; approved, that command is run once, by the
agent, and what it printed is the answer.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

#: The client's own tools that stay denied without the sandbox too: those
#: that reach the user or anyone else, arrange for later, or publish - none
#: of them work.
CLAUDE_STILL_DENIED = (
    "CronCreate", "CronDelete", "CronList", "ScheduleWakeup", "RemoteTrigger",
    "PushNotification", "SendMessage", "SendUserFile", "EndConversation",
    "Workflow", "Artifact", "ArtifactComments", "ArtifactData", "Skill",
)
#: The client's tools its hook is asked about before each use.
HOOKED = ("Bash", "PowerShell", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit",
          "Glob", "Grep")
#: How many ways a model must say it tried before asking the user.
TRIED_AT_LEAST = 2

#: Where sign-ins are kept, under the home: read by nothing.
_SIGN_INS = (
    ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", ".git-credentials", ".netrc",
    ".npmrc", ".pypirc", ".config/gh", ".config/gcloud", ".claude/.credentials.json",
    ".codex/auth.json",
    "AppData/Local/Google/Chrome/User Data", "AppData/Local/Microsoft/Edge/User Data",
    "AppData/Roaming/Mozilla/Firefox/Profiles", "AppData/Local/BraveSoftware",
    "AppData/Roaming/Microsoft/Credentials", "AppData/Local/Microsoft/Credentials",
    "AppData/Roaming/Microsoft/Protect", "AppData/Local/Microsoft/Vault",
    "AppData/Roaming/gcloud", "AppData/Roaming/GitHub CLI",
    "Library/Keychains", "Library/Cookies", "Library/Application Support/Google/Chrome",
    "Library/Application Support/Firefox", ".mozilla", ".config/google-chrome",
    ".config/chromium",
)

#: Commands that write out with the user's accounts - never run.
_OUT = [
    (r"\bgit\b[^\n;&|]*\bpush\b", "a git push"),
    (r"\bgit\b[^\n;&|]*\bsend-email\b", "a patch sent by e-mail"),
    (r"\bgh\b[^\n;&|]*\b(create|comment|edit|merge|close|reopen|delete|upload|review|"
     r"fork|sync|transfer|archive|rename)\b", "a GitHub change made as the user"),
    (r"\bgh\s+api\b[^\n;&|]*(-X|--method)\s*(POST|PUT|PATCH|DELETE)\b",
     "a GitHub change made as the user"),
    (r"\b(npm|pnpm|yarn|bun)\b[^\n;&|]*\bpublish\b", "a package published"),
    (r"\b(twine\s+upload|cargo\s+publish|gem\s+push|poetry\s+publish|flit\s+publish|"
     r"dotnet\s+nuget\s+push|docker\s+push|podman\s+push)\b", "a package or image published"),
    (r"\b(sendmail|mailx?|Send-MailMessage|mutt)\b", "an e-mail sent"),
    (r"\b(curl|wget|http|Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b[^\n;&|]*"
     r"(-X\s*(POST|PUT|PATCH|DELETE)|--data\b|--data-\w+|\s-d\s|\s-F\s|--form\b|"
     r"--upload-file|\s-T\s|--post-|-Method\s+(Post|Put|Patch|Delete)|-InFile|-Body\b)",
     "data sent out over the network"),
    (r"\b(scp|rsync|sftp)\b[^\n;&|]*\S+@?[\w.-]+:", "files copied to another computer"),
    (r"\b(cmdkey|vaultcmd)\b", "the system's credential store"),
]
_OUT = [(re.compile(pattern, re.IGNORECASE), why) for pattern, why in _OUT]


def sign_in_places(home: Optional[Path] = None) -> List[Path]:
    """Where sign-ins are kept, under the home."""
    home = Path(home or Path.home())
    return [home / one for one in _SIGN_INS]


def forbidden_places(agent_data: Iterable[Any], home: Optional[Path] = None) -> List[Path]:
    """Every place nothing reads or writes without the sandbox: the agent's
    own data, and where sign-ins are kept."""
    return [Path(one) for one in agent_data if one] + sign_in_places(home)


def _spellings(place: Path) -> List[str]:
    """A place as a command may write it: either slash, any case, the home
    as ~, %USERPROFILE% or $HOME."""
    text = str(place).replace("\\", "/").lower()
    out = {text, text.replace("/", "\\")}
    home = str(Path.home()).replace("\\", "/").lower()
    if text.startswith(home + "/"):
        rest = text[len(home) + 1:]
        for prefix in ("~/", "%userprofile%/", "$home/", "$env:userprofile/",
                       "${home}/"):
            out.add(prefix + rest)
            out.add((prefix + rest).replace("/", "\\"))
        if rest.lower().startswith("appdata/roaming/"):
            tail = rest[len("appdata/roaming/"):]
            for prefix in ("%appdata%/", "$env:appdata/"):
                out.update({prefix + tail, (prefix + tail).replace("/", "\\")})
        if rest.lower().startswith("appdata/local/"):
            tail = rest[len("appdata/local/"):]
            for prefix in ("%localappdata%/", "$env:localappdata/"):
                out.update({prefix + tail, (prefix + tail).replace("/", "\\")})
    return sorted(out, key=len, reverse=True)


def _within(path: Path, place: Path) -> bool:
    try:
        path = Path(os.path.normcase(os.path.abspath(path)))
        place = Path(os.path.normcase(os.path.abspath(place)))
    except (OSError, ValueError):
        return False
    return path == place or place in path.parents


def forbidden(text: str, places: Sequence[Path]) -> Optional[str]:
    """Why a command may never run - written out with the user's accounts,
    or touching a place nothing may - or None."""
    words = str(text or "")
    for pattern, why in _OUT:
        if pattern.search(words):
            return f"{why} - never done here, approved or not"
    low = words.replace("\\\\", "\\").lower()
    for place in places:
        for spelled in _spellings(place):
            if spelled and spelled in low:
                return (f"it names {place} - the agent's own data or where sign-ins are "
                        "kept: never read or written here")
    return None


def forbidden_path(path: Any, places: Sequence[Path]) -> Optional[str]:
    if not path:
        return None
    for place in places:
        if _within(Path(str(path)), place):
            return (f"{place} is the agent's own data or where sign-ins are kept: never "
                    "read or written here")
    return None


def hook(body: Dict[str, Any], places: Sequence[Path]) -> Optional[str]:
    """Why the client's tool may not run - its hook before it (PreToolUse) -
    or None, and its own permission mode then decides."""
    name = str(body.get("tool_name") or "")
    given = body.get("tool_input") or {}
    if not isinstance(given, dict):
        return None
    if name in ("Bash", "PowerShell"):
        return forbidden(str(given.get("command") or ""), places)
    for key in ("file_path", "path", "notebook_path"):
        why = forbidden_path(given.get(key), places)
        if why:
            return why
    if name in ("Glob", "Grep"):
        return forbidden(str(given.get("pattern") or ""), places)
    return None


def denied_answer(why: str) -> Dict[str, Any]:
    """The hook's answer refusing a tool, with why."""
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": why}}


def is_denial(text: str) -> bool:
    """Whether a tool's result says it was refused for want of permission
    (the client's own words, as it says them)."""
    low = str(text or "").lower()
    return any(one in low for one in (
        "permission to use", "has been denied", "requires approval", "was denied",
        "permission denied by", "not allowed in this mode", "denied by the hook",
        "blocked by"))


JUDGE_SYSTEM = """You decide whether a command an agent wants to run on the
user's computer - OUTSIDE any sandbox, as the user's account - may run now,
or needs the user's approval first. The agent works in the folder:
{folder}

It NEEDS APPROVAL ("ask") when it would do any of these:
  - change or delete anything outside that folder (files, folders, the
    registry, system or account settings, scheduled tasks, services);
  - install or remove software outside that folder, or change what is
    installed system-wide or for the user;
  - send anything out over the network beyond fetching public pages,
    packages or repositories (an upload, a POST, a message, a push);
  - start something that keeps running after the command, or runs at login;
  - run a program downloaded from the web outside that folder.
Anything else - reading, building and testing inside the folder, fetching
public packages into it, starting and stopping its own servers - may run
("allow"). When unsure, "ask".

Answer with ONE JSON object: {{"verdict": "allow" or "ask", "why": "one line"}}"""

"""THE BROWSER EXTENSION'S WAY IN: native messaging (platform_layer/
browser_extension.py).

The browser starts this program (`--native-host`, through the launcher the
extension's install wrote) and writes to its standard input; it reads the
answer from its standard output. Each message is its length - four bytes,
in this computer's order - then that much JSON.

    {"type": "ping"}                       -> {"ok": true, "version": ...}
    {"type": "here", "browser": "Brave"}   -> {"ok": true}
    {"type": "delegate", "picked": {...}}  -> {"ok": true}
    {"type": "learn", "picked": {...}}     -> {"ok": true}

HERE: the extension is in that browser - so the agent's own icon beside
selected text on Windows leaves that browser's pages to the extension's
(browser_extension.mark_seen).

DELEGATE: what was right-clicked (agent/work/picked.py) is kept in a note in
the agent's inbox (agent/work/handover.py), and its box opened - BY THE AGENT
RUNNING, asked over its own API (`opener`, __main__._native_host): the
browser holds this process and all it starts in a job of its own, and ends
them when the message is answered - a window started from here was ended
half drawn, black (the user, 2026-11-02: "弹出的委托面板是全黑的"). Only when
no agent is running is a process started apart, breaking away from the job.
The answer goes back at once.

Only what the extension sends is read; nothing else is listened to.
"""

from __future__ import annotations

import json
import logging
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: A message larger than this is not one of the extension's.
MOST_BYTES = 4 * 1024 * 1024
#: Windows: a process that outlives the browser's hold on its children.
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def read_message(stream: BinaryIO) -> Optional[Dict[str, Any]]:
    """One message, or None at the end of the input."""
    head = stream.read(4)
    if len(head) < 4:
        return None
    size = struct.unpack("=I", head)[0]
    if size > MOST_BYTES:
        raise ValueError("a message too large")
    body = stream.read(size)
    if len(body) < size:
        return None
    said = json.loads(body.decode("utf-8"))
    return said if isinstance(said, dict) else {}


def write_message(stream: BinaryIO, said: Dict[str, Any]) -> None:
    body = json.dumps(said, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("=I", len(body)) + body)
    stream.flush()


def answer(message: Dict[str, Any], data_dir: Path,
           open_note: Callable[..., Any]) -> Dict[str, Any]:
    """What one message is answered: a delegate - or a learn - kept, and
    its box opened (`open_note(ident, which)`) - or, when the extension opens
    the box's window itself ("opens", the user, 2026-11-02: no browser
    started cold), its address said back (`open_note(ident, which, True)`,
    the address, or "" when it opened it by itself after all)."""
    kind = str(message.get("type") or "")
    if kind == "ping":
        from ..version import __version__
        return {"ok": True, "version": __version__}
    if kind == "here":
        from .browser_extension import mark_seen
        mark_seen(data_dir, str(message.get("browser") or ""))
        return {"ok": True}
    if kind in ("delegate", "learn"):
        picked = message.get("picked")
        if not isinstance(picked, dict):
            return {"ok": False, "error": "nothing was picked"}
        from ..agent.work.handover import hand_over_picked
        from .browser_extension import mark_seen
        mark_seen(data_dir, str(picked.get("app") or ""))
        # Named by the extension when it opened the box already, on it.
        ident = hand_over_picked(data_dir, picked, str(message.get("id") or ""))
        if message.get("opens"):
            url = open_note(ident, kind, True)
            return {"ok": True, "url": url if isinstance(url, str) else ""}
        open_note(ident, kind)
        return {"ok": True}
    return {"ok": False, "error": f"not a message of the agent's: {kind or '(none)'}"}


def open_apart(command: List[str], ident: str, which: str = "delegate") -> None:
    """The Delegate work box - or the Learn box - opened by a process of its
    own, not one the browser ends with this one."""
    argv = list(command) + ["--learn-note" if which == "learn" else "--delegate-note", ident]
    if sys.platform == "win32":
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                 | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            subprocess.Popen(argv, close_fds=True, creationflags=flags
                             | CREATE_BREAKAWAY_FROM_JOB)
            return
        except OSError:               # a browser that does not let it break away
            subprocess.Popen(argv, close_fds=True, creationflags=flags)
            return
    subprocess.Popen(argv, close_fds=True, start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def streams() -> Tuple[BinaryIO, BinaryIO]:
    """The standard input and output, as bytes - also for a program with no
    console (pythonw, the installed app), whose sys.stdin is None though the
    browser gave it both."""
    stdin = getattr(sys.stdin, "buffer", None)
    stdout = getattr(sys.stdout, "buffer", None)
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        import os
        if stdin is None or stdout is None:
            kernel = ctypes.windll.kernel32
            kernel.GetStdHandle.restype = ctypes.c_void_p
            handles = (kernel.GetStdHandle(-10), kernel.GetStdHandle(-11))
            stdin = os.fdopen(msvcrt.open_osfhandle(handles[0], os.O_RDONLY | os.O_BINARY),
                              "rb", buffering=0)
            stdout = os.fdopen(msvcrt.open_osfhandle(handles[1], os.O_BINARY), "wb")
        else:
            msvcrt.setmode(stdin.fileno(), os.O_BINARY)
            msvcrt.setmode(stdout.fileno(), os.O_BINARY)
    return stdin, stdout  # type: ignore[return-value]


def serve(data_dir: Path, command: List[str],
          inbound: Optional[BinaryIO] = None, outbound: Optional[BinaryIO] = None,
          opener: Optional[Callable[..., Any]] = None) -> int:
    """Answer the browser's messages until it closes the input. `opener(ident,
    which)` opens a note's box - by default, a process apart."""
    opener = opener or (lambda ident, which, give=False: open_apart(command, ident, which))
    if inbound is None or outbound is None:
        inbound, outbound = streams()
    while True:
        try:
            message = read_message(inbound)
        except (ValueError, UnicodeDecodeError) as exc:
            write_message(outbound, {"ok": False, "error": str(exc)[:200]})
            return 1
        if message is None:
            return 0
        try:
            said = answer(message, data_dir, opener)
        except Exception as exc:                 # said back, not left hanging
            log.exception("the browser's message could not be answered")
            said = {"ok": False, "error": str(exc)[:200]}
        write_message(outbound, said)


__all__ = ["serve", "answer", "read_message", "write_message", "open_apart", "MOST_BYTES"]

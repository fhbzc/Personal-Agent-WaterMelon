"""THE HOOK A CLIENT RUNS AROUND ITS OWN WEB TOOLS (Claude Code's WebSearch
and WebFetch): what the client hands it on stdin is passed to the call's own
server (models/providers/mcp_bridge.py), whose web guard answers
(runtime/web_guard.py), and the answer is printed for the client.

FAILS CLOSED: when the guard cannot be asked - the server gone, an answer
that is not one - a search or a page is refused, never let through. Only
the standard library here: it is started for every search and every page.

    <the application> --web-hook <the server's hook address>
"""

from __future__ import annotations

import json
import sys
import urllib.request

FLAG = "--web-hook"
#: How long the guard is waited for.
TIMEOUT = 20


def hook_main(argv: list) -> int:
    url = argv[0] if argv else ""
    raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else b""
    try:
        event = str(json.loads(raw or b"{}").get("hook_event_name") or "")
    except ValueError:
        event = ""
    try:
        if not url.startswith("http://127.0.0.1:"):
            raise ValueError("not this computer's own server")
        request = urllib.request.Request(url, data=raw, method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            said = json.loads(answer.read() or b"{}")
        if not isinstance(said, dict):
            raise ValueError("not an answer")
    except Exception as exc:
        if event == "PreToolUse":
            sys.stderr.write(f"the web check could not be asked ({type(exc).__name__}): "
                             "nothing is searched or opened without it\n")
            return 2
        return 0
    if said:
        sys.stdout.write(json.dumps(said))
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(hook_main(sys.argv[1:]))

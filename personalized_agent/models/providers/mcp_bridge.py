"""The agent's tools, offered to Claude Code natively - over MCP.

Claude Code calls tools the way it was made to: as tool calls of its own.
Offered the agent's tools only as words ("reply with this JSON"), the model
reached for them natively all the same - and Claude Code, which has no such
tool, answered "No such tool available", a whole round wasted each time; a
file being written was written twice. So, for one call, the tools are served
to it as an MCP server of the application's own:

  * IN THIS PROCESS, on 127.0.0.1, a port of its own and an address with a
    secret in it - given to that one call (`--mcp-config`) and to nothing
    else; `--strict-mcp-config` keeps every other server out.
  * EACH CALL RUN BY THE AGENT - `runner(name, arguments)`, the same tool
    layer, sandbox and guard as every tool call of the agent's; Claude Code
    only asks. Only the tools of the request are served, by their names.
  * WATCHED: while a tool runs, the call is not silent - it is working (a
    run paused, an install waiting for the user's answer, a long script) -
    so the provider's watch on the stream counts it alive (`busy`).

Streamable HTTP, the part of it a client needs: initialize, the tools'
list, a tool's call; notifications acknowledged; no server-sent events.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

#: The server's name, and so the prefix of its tools in Claude Code:
#: `mcp__pa__run_python`.
SERVER_NAME = "pa"
#: The most of a tool's result handed back, as the agent's own loop hands it.
MOST_RESULT = 40000
PROTOCOL_VERSION = "2025-06-18"


def tool_name(name: str) -> str:
    """A tool's name as Claude Code knows it."""
    return f"mcp__{SERVER_NAME}__{name}"


class ToolBridge:
    """One call's tools, served while it lasts. Use as a context manager."""

    def __init__(self, tools: List[Any], runner: Callable[[str, Dict[str, Any]], Any]) -> None:
        self.tools = {str(one.name): one for one in tools or []}
        self.runner = runner
        self.secret = secrets.token_urlsafe(32)
        self.calls: List[Dict[str, Any]] = []
        self._busy = 0
        self._lock = threading.Lock()
        # One at a time, as the agent's own loop runs them: the tools were
        # never made to run side by side.
        self._serial = threading.Lock()
        self.last_activity = time.monotonic()
        self._server: Optional[ThreadingHTTPServer] = None

    # -- serving ------------------------------------------------------------
    def __enter__(self) -> "ToolBridge":
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def _empty(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:          # no server-sent events
                self._empty(405)

            def do_DELETE(self) -> None:       # the session ended: nothing kept
                self._empty(200 if bridge._ours(self.path) else 404)

            def do_POST(self) -> None:
                if not bridge._ours(self.path):
                    self._empty(404)
                    return
                try:
                    size = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(size) or b"null")
                except (ValueError, OSError):
                    self._empty(400)
                    return
                answers = [one for one in (bridge._answer(message) for message in
                                           (body if isinstance(body, list) else [body]))
                           if one is not None]
                if not answers:
                    self._empty(202)
                    return
                data = json.dumps(answers if isinstance(body, list) else answers[0],
                                  ensure_ascii=False, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="mcp-bridge",
                         daemon=True).start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def url(self) -> str:
        port = self._server.server_address[1] if self._server else 0
        return f"http://127.0.0.1:{port}/{self.secret}/mcp"

    def config(self) -> str:
        """The `--mcp-config` for the call: this server, and only it."""
        return json.dumps({"mcpServers": {SERVER_NAME: {"type": "http", "url": self.url}}})

    def allowed(self) -> List[str]:
        """The `--allowedTools` for the call: these tools by name."""
        return [tool_name(name) for name in self.tools]

    @property
    def busy(self) -> bool:
        """A tool is running now."""
        with self._lock:
            return self._busy > 0

    def _ours(self, path: str) -> bool:
        return secrets.compare_digest(path.split("?", 1)[0].rstrip("/"),
                                      f"/{self.secret}/mcp")

    # -- the protocol -------------------------------------------------------
    def _answer(self, message: Any) -> Optional[Dict[str, Any]]:
        self.last_activity = time.monotonic()
        if not isinstance(message, dict):
            return None
        method, ident = message.get("method"), message.get("id")
        if ident is None:                      # a notification: acknowledged
            return None
        params = message.get("params") or {}
        if method == "initialize":
            result: Dict[str, Any] = {
                "protocolVersion": str(params.get("protocolVersion") or PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": "1"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": [{"name": name, "description": str(one.description or ""),
                                 "inputSchema": one.input_schema or {"type": "object"}}
                                for name, one in self.tools.items()]}
        elif method == "tools/call":
            result = self._call(str(params.get("name") or ""), params.get("arguments") or {})
        else:
            return {"jsonrpc": "2.0", "id": ident,
                    "error": {"code": -32601, "message": f"no method {method}"}}
        return {"jsonrpc": "2.0", "id": ident, "result": result}

    def _call(self, name: str, arguments: Any) -> Dict[str, Any]:
        if name not in self.tools:
            said: Any = {"error": f"no tool {name}"}
        else:
            with self._lock:
                self._busy += 1
            try:
                with self._serial:
                    said = self.runner(name, arguments if isinstance(arguments, dict) else {})
            except Exception as exc:          # the tool's failure is the model's to read
                log.warning("a tool called through MCP failed", exc_info=True)
                said = {"error": f"{type(exc).__name__}: {exc}"}
            finally:
                with self._lock:
                    self._busy -= 1
                self.last_activity = time.monotonic()
        text = said if isinstance(said, str) else json.dumps(said, ensure_ascii=False,
                                                             default=str)
        failed = isinstance(said, dict) and bool(said.get("error"))
        self.calls.append({"name": name, "arguments": arguments, "result": text[:MOST_RESULT],
                           "error": failed})
        return {"content": [{"type": "text", "text": text[:MOST_RESULT]}], "isError": failed}

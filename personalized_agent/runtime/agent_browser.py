"""A browser for testing what a run builds - this run's own.

Imported by code a work run runs (`from agent_browser import Browser`), and
by nothing in the application itself: a copy of this file is kept beside the
installed browser (runtime/browser.py), outside the application's code, so a
run of the Experience debugger - which may not read that code - can import it
too. The standard library alone, as the run's code may have nothing else.

The browser is the "Chrome for Testing" headless shell the user installed
from Settings. It runs inside the run's own confinement, like everything the
run starts: it writes only in the run's folders, and reaches only the servers
this run starts on 127.0.0.1 - not another run's, not the application's, not
the internet (runtime/isolation.py: a container of the run's own). Its own
sandbox is off (--no-sandbox): it cannot start inside the container, which is
what confines it here. A fresh empty profile each time, in the run's
`.agent_runtime` (which no version keeps), deleted when it closes.

    from agent_browser import Browser, free_port, wait_for_server

    with Browser() as page:
        page.goto(f"http://127.0.0.1:{port}/")
        page.screenshot("screenshots/home.png")
        page.type("#name", "Ada")
        page.click("button[type=submit]")
        page.wait_for("#result")
        print(page.text("#result"), page.errors)

Driven over the DevTools protocol, on a port of the run's own loopback.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import time
import urllib.request
from typing import Any, Callable, Dict, List, Optional

#: Set for the run's code when a browser is installed and the run may use it
#: (runtime/guard.BROWSER_ENV): the one program besides Python it may start.
BROWSER_ENV = "AGENT_BROWSER"
WORKSPACE_ENV = "AGENT_WORKSPACE"

_KEYS = {
    "Enter": ("Enter", 13, "\r"), "Tab": ("Tab", 9, "\t"), "Escape": ("Escape", 27, ""),
    "Backspace": ("Backspace", 8, ""), "Delete": ("Delete", 46, ""),
    "ArrowUp": ("ArrowUp", 38, ""), "ArrowDown": ("ArrowDown", 40, ""),
    "ArrowLeft": ("ArrowLeft", 37, ""), "ArrowRight": ("ArrowRight", 39, ""),
    "Home": ("Home", 36, ""), "End": ("End", 35, ""), " ": ("Space", 32, " "),
}


class BrowserError(Exception):
    """Something the browser could not do, said plainly."""


def free_port() -> int:
    """A port no one is listening on now, on this run's loopback: start your
    server on it. Never a fixed port - other runs may be going at once."""
    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
    finally:
        probe.close()


def wait_for_server(url: str, timeout: float = 30.0) -> None:
    """Until `url` answers at all (any HTTP status), or BrowserError."""
    end = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(url, timeout=2).close()
            return
        except urllib.error.HTTPError:
            return                             # it answered
        except OSError as exc:
            last = exc
            time.sleep(0.2)
    raise BrowserError(f"{url} did not answer within {timeout:.0f}s: {last}")


# ---------------------------------------------------------------------------
# A WebSocket client, enough for the DevTools protocol
# ---------------------------------------------------------------------------
class _WebSocket:

    def __init__(self, url: str, timeout: float) -> None:
        if not url.startswith("ws://"):
            raise BrowserError(f"unexpected DevTools address {url!r}")
        rest = url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.rpartition(":")
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\n"
                           "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
                          .encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise BrowserError("the browser closed the DevTools connection")
            head += chunk
        head, _, self.buffer = head.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise BrowserError("the browser refused the DevTools connection: "
                               + head.split(b"\r\n", 1)[0].decode("latin-1"))

    def send(self, text: str) -> None:
        data = text.encode("utf-8")
        header = bytearray([0x81])
        if len(data) < 126:
            header.append(0x80 | len(data))
        elif len(data) < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack("!H", len(data))
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", len(data))
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data)) if len(data) < 4096 \
            else _mask_long(data, mask)
        self.sock.sendall(bytes(header) + mask + masked)

    def _exactly(self, count: int) -> bytes:
        while len(self.buffer) < count:
            chunk = self.sock.recv(max(65536, count - len(self.buffer)))
            if not chunk:
                raise BrowserError("the browser closed the DevTools connection")
            self.buffer += chunk
        out, self.buffer = self.buffer[:count], self.buffer[count:]
        return out

    def receive(self, timeout: float) -> Optional[str]:
        """One message, or None when nothing came within `timeout`."""
        message = b""
        while True:
            self.sock.settimeout(timeout)
            try:
                first, second = self._exactly(2)
            except socket.timeout:
                return None
            self.sock.settimeout(None)
            opcode, length = first & 0x0F, second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._exactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._exactly(8))[0]
            mask = self._exactly(4) if second & 0x80 else b""
            payload = self._exactly(length)
            if mask:
                payload = _mask_long(payload, mask)
            if opcode == 0x8:
                raise BrowserError("the browser closed the DevTools connection")
            if opcode == 0x9:                  # ping
                self._control(0xA, payload)
                continue
            if opcode in (0x1, 0x2, 0x0):
                message += payload
                if first & 0x80:
                    return message.decode("utf-8", "replace")

    def _control(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(payload)]) + mask
                          + _mask_long(payload, mask))

    def close(self) -> None:
        try:
            self._control(0x8, b"")
        except OSError:
            pass
        self.sock.close()


def _mask_long(data: bytes, mask: bytes) -> bytes:
    whole = (mask * (len(data) // 4 + 1))[:len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(whole, "big")).to_bytes(
        len(data), "big") if data else b""


# ---------------------------------------------------------------------------
# The browser
# ---------------------------------------------------------------------------
class Browser:
    """This run's own headless browser, with one page - the page's methods
    are the browser's. Use it as a context manager, or call close()."""

    def __init__(self, width: int = 1280, height: int = 800, timeout: float = 30.0) -> None:
        program = os.environ.get(BROWSER_ENV) or ""
        if not program or not os.path.isfile(program):
            raise BrowserError(
                "no browser is installed for testing - the user can install one in "
                "Settings (Browser testing); test with the framework's test client "
                "meanwhile")
        self.timeout = timeout
        self.console: List[Dict[str, str]] = []
        self.errors: List[str] = []
        self._loaded = False
        self._next = 0
        self._answers: Dict[int, Dict[str, Any]] = {}
        home = os.environ.get(WORKSPACE_ENV) or os.getcwd()
        # Short: the browser's own files lie deep inside it, and Windows
        # paths stop at 260 characters.
        self.profile = os.path.join(home, ".agent_runtime", "br", os.urandom(4).hex())
        os.makedirs(self.profile)
        self._log = open(os.path.join(self.profile, "browser.log"), "wb")
        self.process = subprocess.Popen(
            [program, "--no-sandbox", "--headless", "--disable-gpu", "--no-first-run",
             "--no-default-browser-check", "--disable-extensions", "--mute-audio",
             "--disable-background-networking", "--disable-component-update",
             "--user-data-dir=" + self.profile, "--remote-debugging-port=0",
             "--remote-allow-origins=*", f"--window-size={width},{height}",
             "about:blank"],
            stdin=subprocess.DEVNULL, stdout=self._log, stderr=self._log)
        try:
            self._ws = _WebSocket(self._page_address(), timeout)
            self._call("Page.enable")
            self._call("Runtime.enable")
            self._call("Log.enable")
            self.set_viewport(width, height)
        except BaseException:
            self.close()
            raise

    # -- starting -----------------------------------------------------------
    def _page_address(self) -> str:
        active = os.path.join(self.profile, "DevToolsActivePort")
        end = time.monotonic() + self.timeout
        port = ""
        while time.monotonic() < end:
            if self.process.poll() is not None:
                raise BrowserError(f"the browser ended at once (exit {self.process.returncode}); "
                                   f"its log: {self._tail()}")
            try:
                with open(active, encoding="utf-8") as handle:
                    port = handle.readline().strip()
            except OSError:
                port = ""
            if port.isdigit():
                break
            time.sleep(0.1)
        if not port.isdigit():
            raise BrowserError(f"the browser did not start within {self.timeout:.0f}s; "
                               f"its log: {self._tail()}")
        while time.monotonic() < end:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list",
                                            timeout=5) as answer:
                    targets = json.loads(answer.read().decode("utf-8"))
                pages = [one for one in targets if one.get("type") == "page"]
                if pages:
                    return pages[0]["webSocketDebuggerUrl"]
            except OSError:
                pass
            time.sleep(0.1)
        raise BrowserError("the browser opened no page")

    def _tail(self) -> str:
        try:
            self._log.flush()
            with open(os.path.join(self.profile, "browser.log"), "rb") as handle:
                return handle.read()[-600:].decode("utf-8", "replace").strip() or "(empty)"
        except OSError:
            return "(none)"

    # -- the protocol -------------------------------------------------------
    def _event(self, message: Dict[str, Any]) -> None:
        method, params = message.get("method"), message.get("params") or {}
        if method == "Page.loadEventFired":
            self._loaded = True
        elif method == "Runtime.consoleAPICalled":
            text = " ".join(str(arg.get("value", arg.get("description", "")))
                            for arg in params.get("args", []))
            self.console.append({"type": params.get("type", ""), "text": text})
            if params.get("type") in ("error", "assert"):
                self.errors.append("console: " + text)
        elif method == "Runtime.exceptionThrown":
            details = params.get("exceptionDetails") or {}
            text = ((details.get("exception") or {}).get("description")
                    or details.get("text") or "an exception")
            self.errors.append("exception: " + text.splitlines()[0])
        elif method == "Log.entryAdded":
            entry = params.get("entry") or {}
            if entry.get("level") == "error":
                self.errors.append(f"{entry.get('source', 'log')}: {entry.get('text', '')}"
                                   + (f" ({entry['url']})" if entry.get("url") else ""))

    def _pump(self, done: Callable[[], bool], timeout: float) -> bool:
        end = time.monotonic() + timeout
        while not done():
            left = end - time.monotonic()
            if left <= 0:
                return False
            raw = self._ws.receive(min(left, 0.5))
            if raw is None:
                continue
            message = json.loads(raw)
            if "id" in message:
                self._answers[message["id"]] = message
            else:
                self._event(message)
        return True

    def _call(self, method: str, params: Optional[Dict[str, Any]] = None,
              timeout: Optional[float] = None) -> Dict[str, Any]:
        if not hasattr(self, "_ws"):
            raise BrowserError("the browser is closed")
        self._next += 1
        ident = self._next
        self._ws.send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        if not self._pump(lambda: ident in self._answers, timeout or self.timeout):
            raise BrowserError(f"the browser did not answer {method} in time")
        answer = self._answers.pop(ident)
        if "error" in answer:
            raise BrowserError(f"{method}: {answer['error'].get('message', answer['error'])}")
        return answer.get("result") or {}

    # -- the page -----------------------------------------------------------
    def goto(self, url: str, timeout: Optional[float] = None) -> "Browser":
        """Open `url` and wait until it has loaded."""
        self._loaded = False
        result = self._call("Page.navigate", {"url": url})
        if result.get("errorText"):
            hint = ("" if url.startswith(("http://127.0.0.1", "http://localhost",
                                          "http://[::1]", "about:", "data:", "file:"))
                    else " - only the servers this run starts, on 127.0.0.1, can be opened")
            raise BrowserError(f"could not open {url}: {result['errorText']}{hint}")
        if not self._pump(lambda: self._loaded, timeout or self.timeout):
            raise BrowserError(f"{url} did not finish loading in time")
        return self

    def evaluate(self, expression: str) -> Any:
        """The value of a JavaScript expression in the page (a promise is
        awaited); BrowserError with the page's error if it throws."""
        result = self._call("Runtime.evaluate", {"expression": expression,
                                                 "returnByValue": True,
                                                 "awaitPromise": True})
        if result.get("exceptionDetails"):
            details = result["exceptionDetails"]
            raise BrowserError("the page's script failed: "
                               + ((details.get("exception") or {}).get("description")
                                  or details.get("text") or "error").splitlines()[0])
        return (result.get("result") or {}).get("value")

    @property
    def url(self) -> str:
        return self.evaluate("location.href")

    @property
    def title(self) -> str:
        return self.evaluate("document.title")

    def html(self) -> str:
        return self.evaluate("document.documentElement.outerHTML")

    def _on(self, selector: str, body: str) -> Any:
        """`body` run with `e`, the first element `selector` matches (or
        null); its return value."""
        return self.evaluate("(() => { const e = document.querySelector("
                             + json.dumps(selector) + "); " + body + " })()")

    def text(self, selector: str = "body") -> str:
        """The visible text of the first element `selector` matches."""
        found = self._on(selector, "return e ? e.innerText : null;")
        if found is None:
            raise BrowserError(f"nothing matches {selector!r}")
        return found

    def exists(self, selector: str) -> bool:
        return bool(self.evaluate(f"!!document.querySelector({json.dumps(selector)})"))

    def count(self, selector: str) -> int:
        return int(self.evaluate(f"document.querySelectorAll({json.dumps(selector)}).length"))

    def wait_for(self, selector: str, timeout: Optional[float] = None) -> "Browser":
        """Until an element matching `selector` is there."""
        end = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < end:
            if self.exists(selector):
                return self
            self._pump(lambda: False, 0.1)
        raise BrowserError(f"nothing matched {selector!r} within {timeout or self.timeout:.0f}s")

    def _center(self, selector: str) -> tuple:
        box = self._on(selector,
                       "if (!e) return null; e.scrollIntoView({block: 'center'});"
                       " const r = e.getBoundingClientRect();"
                       " return [r.left + r.width / 2, r.top + r.height / 2, r.width, r.height];")
        if box is None:
            raise BrowserError(f"nothing matches {selector!r}")
        if not box[2] or not box[3]:
            raise BrowserError(f"{selector!r} is not visible")
        return box[0], box[1]

    def click(self, selector: str) -> "Browser":
        """A real mouse click in the middle of the element."""
        x, y = self._center(selector)
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            self._call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y,
                                                    "button": "left", "clickCount": 1})
        self._pump(lambda: False, 0.05)
        return self

    def type(self, selector: str, text: str, clear: bool = True) -> "Browser":
        """Click into the field and type `text` (replacing what is there)."""
        self.click(selector)
        if clear:
            self._on(selector,
                     "if ('value' in e) { e.value = '';"
                     " e.dispatchEvent(new Event('input', {bubbles: true})); }"
                     " else if (e.isContentEditable) e.textContent = '';")
        self._call("Input.insertText", {"text": text})
        return self

    def press(self, key: str) -> "Browser":
        """A key: Enter, Tab, Escape, Backspace, Arrow..., or one character."""
        name, code, text = _KEYS.get(key, (key, ord(key.upper()[0]) if key else 0, key))
        down = {"type": "keyDown", "key": key, "code": name,
                "windowsVirtualKeyCode": code, "nativeVirtualKeyCode": code}
        if text:
            down["text"] = text
        self._call("Input.dispatchKeyEvent", down)
        self._call("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": name,
                                              "windowsVirtualKeyCode": code,
                                              "nativeVirtualKeyCode": code})
        self._pump(lambda: False, 0.05)
        return self

    def select(self, selector: str, value: str) -> "Browser":
        """Choose the option with this value in a <select>."""
        found = self._on(selector,
                         "if (!e) return false; e.value = " + json.dumps(value) + ";"
                         " e.dispatchEvent(new Event('input', {bubbles: true}));"
                         " e.dispatchEvent(new Event('change', {bubbles: true}));"
                         " return true;")
        if not found:
            raise BrowserError(f"nothing matches {selector!r}")
        return self

    def set_viewport(self, width: int, height: int, mobile: bool = False) -> "Browser":
        self._call("Emulation.setDeviceMetricsOverride",
                   {"width": int(width), "height": int(height), "deviceScaleFactor": 1,
                    "mobile": bool(mobile)})
        return self

    def screenshot(self, path: str, full_page: bool = False,
                   selector: Optional[str] = None) -> str:
        """A PNG of the page as it shows now - the whole page, or one
        element - written to `path` in your folder. Its path, absolute."""
        params: Dict[str, Any] = {"format": "png"}
        if selector:
            box = self._on(selector,
                           "if (!e) return null; e.scrollIntoView({block: 'center'});"
                           " const r = e.getBoundingClientRect();"
                           " return [r.left + scrollX, r.top + scrollY, r.width, r.height];")
            if box is None:
                raise BrowserError(f"nothing matches {selector!r}")
            params.update(clip={"x": box[0], "y": box[1], "width": max(1, box[2]),
                                "height": max(1, box[3]), "scale": 1},
                          captureBeyondViewport=True)
        elif full_page:
            size = self.evaluate("[document.documentElement.scrollWidth,"
                                 " document.documentElement.scrollHeight]")
            params.update(clip={"x": 0, "y": 0, "width": size[0], "height": size[1],
                                "scale": 1}, captureBeyondViewport=True)
        data = base64.b64decode(self._call("Page.captureScreenshot", params)["data"])
        target = os.path.abspath(path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
        return target

    # -- ending -------------------------------------------------------------
    def close(self) -> None:
        ws = self.__dict__.pop("_ws", None)
        if ws is not None:
            try:
                ws.send(json.dumps({"id": 0, "method": "Browser.close", "params": {}}))
            except OSError:
                pass
            ws.close()
        process = getattr(self, "process", None)
        if process is not None and process.poll() is None:
            try:
                process.wait(5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
        if getattr(self, "_log", None) is not None:
            self._log.close()
        for _ in range(20):                  # the browser lets go of its files late
            shutil.rmtree(self.profile, ignore_errors=True)
            if not os.path.exists(self.profile):
                break
            time.sleep(0.1)

    def __enter__(self) -> "Browser":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

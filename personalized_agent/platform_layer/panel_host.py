"""THE SIDE PANEL IN WEBVIEW2, IN A PROCESS OF ITS OWN - WINDOWS (the user, 2026-11-14: "那你试一下WebView2吧";
"接进"; and of starting it before it is wanted: "这个做").

Measured on 2026-11-14 with the panel's own page (notes.html): Chrome's app mode took 1.7-2.1 s to open the
side panel - a browser started each time - and WebView2, already running, 0.27-0.50 s; the first WebView2
window of a process 4.9 s, its runtime starting. So the runtime is started BEFORE IT IS WANTED: this process
starts a while after the application does (`PanelHost.start`), with one hidden window to keep it, and each
panel is a window opened in it on the desktop's word (`open`) - off every screen, as the browser's was,
for the desktop to slide in (app_window.place_side_window). Its own process: the application's work never
holds it up, and it never holds up the application.

    the host     `serve` - run as `python -m personalized_agent.platform_layer.panel_host <storage>`, or in
                 the packaged application as `<app> --panel-host <storage>`: reads one JSON line at a time
                 on its standard input - {"op": "open", "url", "title", "width", "height", "x", "y"},
                 {"op": "quit"} - and answers each with one line; its input closed (the application gone),
                 it ends
    the client   `PanelHost` - started once, asked to open; anything wrong (no pywebview, no WebView2
                 runtime, the process gone, no answer in time) and `open` says False: the desktop opens the
                 panel the way it did before, in the browser's app mode

Its window has no title bar (the page has its own heading; put away with Esc or the page's own answers),
its title the page's - how the desktop finds it - and its store of cookies in the application's own folder.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: How the packaged application is told to be the panel's host.
FLAG = "--panel-host"
#: Where the host is started from the source: the folder that holds the package, as "-m" finds it only
#: there - the application started at sign-in is in C:\Windows\System32, and its host ended at once.
SOURCE_FOLDER = Path(__file__).resolve().parents[2]
#: How long the application waits before starting the host - not in the way of its own start.
START_AFTER_SECONDS = 8.0
#: How long an open is waited for; how long the host is given to start.
OPEN_SECONDS, START_SECONDS = 5.0, 30.0
#: The WebView2 runtime's key, where the installer registers it (machine-wide, or for the user).
RUNTIME_KEY = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
#: Thin scrollbars that come and go, as Windows' own (measured: the classic one, with arrows, otherwise).
BROWSER_ARGUMENTS = "--enable-features=msOverlayScrollbarWinStyle,msOverlayScrollbarWinStyleAnimation"


# -- the host: run in its own process ----------------------------------------------------------------
def serve(storage: str) -> int:
    """The panel's host: WebView2 started at once, one window opened for each "open" read."""
    if not storage:
        sys.stderr.write("the panel host needs the folder its WebView2 store is kept in\n")
        return 2
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", BROWSER_ARGUMENTS)
    # The answers are the only thing on stdout: anything a library prints goes to stderr (as embed_worker's).
    channel = sys.stdout
    sys.stdout = sys.stderr
    import webview

    def answer(said: Dict[str, Any]) -> None:
        channel.write(json.dumps(said) + "\n")
        channel.flush()

    def read() -> None:
        answer({"ready": True})
        for line in sys.stdin:
            try:
                asked = json.loads(line)
            except ValueError:
                answer({"error": "not JSON"})
                continue
            if asked.get("op") == "quit":
                break
            if asked.get("op") != "open":
                answer({"error": "open or quit"})
                continue
            try:
                webview.create_window(
                    str(asked.get("title") or ""), str(asked.get("url") or "about:blank"),
                    width=int(asked.get("width") or 460), height=int(asked.get("height") or 900),
                    x=int(asked.get("x") if asked.get("x") is not None else -32000),
                    y=int(asked.get("y") or 0), frameless=True, easy_drag=False,
                    hidden=bool(asked.get("hidden")))
                answer({"opened": True})
            except Exception as exc:                  # said, and the desktop opens it as before
                answer({"error": f"{type(exc).__name__}: {exc}"[:300]})
        for window in list(webview.windows):         # the application gone, or told to quit
            try:
                window.destroy()
            except Exception:
                pass

    # KEPT ALIVE, OUT OF SIGHT: closing the last panel would otherwise end the process and its runtime.
    webview.create_window("WaterMelon panel host", "about:blank", width=200, height=200, x=-32000, y=-32000,
                          hidden=True)
    Path(storage).mkdir(parents=True, exist_ok=True)
    webview.start(read, private_mode=False, storage_path=storage)
    return 0


# -- the client: in the application ----------------------------------------------------------------
def runtime_present() -> bool:
    """Whether the WebView2 runtime is installed (every Windows 11; most Windows 10)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:                              # pragma: no cover
        return False
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key in (RUNTIME_KEY, RUNTIME_KEY.replace(r"\WOW6432Node", "")):
            try:
                with winreg.OpenKey(hive, key) as opened:
                    version, _kind = winreg.QueryValueEx(opened, "pv")
                    if version and version != "0.0.0.0":
                        return True
            except OSError:
                continue
    return False


def pywebview_present() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("webview") is not None
    except Exception:                                # pragma: no cover - a broken install
        return False


def host_folder() -> Optional[str]:
    """The folder the host starts in: the packaged application's own is fine wherever it is."""
    return None if getattr(sys, "frozen", False) else str(SOURCE_FOLDER)


def host_command(storage: Path) -> List[str]:
    """How the host is started: this module by its name, or - in the packaged application, where there is
    no module to run - the application itself with the flag that makes it the host."""
    if getattr(sys, "frozen", False):
        return [sys.executable, FLAG, str(storage)]
    return [sys.executable, "-m", "personalized_agent.platform_layer.panel_host", str(storage)]


class PanelHost:
    """The application's side of the host: started once, asked to open panels."""

    def __init__(self, storage: Path) -> None:
        self.storage = Path(storage)
        self._child: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._answers: List[Dict[str, Any]] = []
        self._said = threading.Condition()
        self.failed = ""                             # why it is not used - said once in the log

    def usable(self) -> bool:
        return (sys.platform == "win32" and not os.environ.get("PA_NO_DIALOGS")
                and not self.failed and pywebview_present() and runtime_present())

    def start_later(self, seconds: float = START_AFTER_SECONDS, then: Any = None) -> None:
        """Started a while after the application, out of its way - and `then()`, once it is ready: the panel
        made there already, hidden, so that the first time it is wanted it is only shown (measured: its page
        loaded hidden, and drew its list 0.1 s after it was shown)."""
        if not self.usable():
            return
        timer = threading.Timer(seconds, self._start_quietly, args=(then,))
        timer.daemon = True
        timer.start()

    def _start_quietly(self, then: Any = None) -> None:
        try:
            if self.start() and then is not None:
                then()
        except Exception:
            log.info("the side panel's host could not be started", exc_info=True)

    def running(self) -> bool:
        return self._child is not None and self._child.poll() is None

    def start(self) -> bool:
        with self._lock:
            if self.running():
                return True
            if not self.usable():
                return False
            try:
                self.storage.mkdir(parents=True, exist_ok=True)
                self._child = subprocess.Popen(
                    host_command(self.storage), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1,
                    cwd=host_folder(),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as exc:
                self.failed = f"not started: {exc}"[:300]
                log.info("the side panel's host: %s", self.failed)
                return False
            self._answers = []
            threading.Thread(target=self._listen, args=(self._child,), name="panel-host",
                             daemon=True).start()
        ready = self._next(START_SECONDS)
        if not ready.get("ready"):
            self.failed = "it did not start"
            log.info("the side panel's host did not start - the panel opens in the browser")
            self.stop()
            return False
        log.info("the side panel's host is ready (WebView2)")
        return True

    def _listen(self, child: subprocess.Popen) -> None:
        for line in child.stdout:                    # type: ignore[union-attr]
            try:
                said = json.loads(line)
            except ValueError:
                continue
            with self._said:
                self._answers.append(said)
                self._said.notify_all()
        with self._said:                             # its output closed: it ended
            self._answers.append({"ended": True})
            self._said.notify_all()

    def _next(self, seconds: float) -> Dict[str, Any]:
        end = time.monotonic() + seconds
        with self._said:
            while not self._answers:
                left = end - time.monotonic()
                if left <= 0:
                    return {}
                self._said.wait(left)
            return self._answers.pop(0)

    def open(self, url: str, title: str, width: int, height: int, x: int = -32000, y: int = 0,
             hidden: bool = False) -> bool:
        """A panel window opened off every screen - False when it cannot be, for the desktop to open it as
        before."""
        if not self.start():
            return False
        with self._lock:
            try:
                self._child.stdin.write(json.dumps({"op": "open", "url": url, "title": title,  # type: ignore
                                                    "width": int(width), "height": int(height),
                                                    "x": int(x), "y": int(y),
                                                    "hidden": bool(hidden)}) + "\n")
                self._child.stdin.flush()                                            # type: ignore
            except Exception:
                log.info("the side panel's host could not be asked", exc_info=True)
                return False
            said = self._next(OPEN_SECONDS)
        if said.get("opened"):
            return True
        log.info("the side panel's host did not open it: %s", said.get("error") or "no answer")
        return False

    def stop(self) -> None:
        child, self._child = self._child, None
        if child is None:
            return
        try:
            if child.poll() is None:
                child.stdin.write(json.dumps({"op": "quit"}) + "\n")             # type: ignore
                child.stdin.flush()                                              # type: ignore
                child.wait(timeout=3)
        except Exception:
            pass
        if child.poll() is None:
            try:
                child.kill()
            except Exception:
                pass


if __name__ == "__main__":                           # the host, run by its module's name
    if len(sys.argv) < 2:
        raise SystemExit("usage: panel_host <the folder its WebView2 store is kept in>")
    raise SystemExit(serve(sys.argv[1]))

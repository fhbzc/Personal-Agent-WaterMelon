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
#: How long the host is waited for to leave its panels out of the agent's picture - a picture waits for it.
UNSEEN_SECONDS = 0.4
#: The WebView2 runtime's key, where the installer registers it (machine-wide, or for the user).
RUNTIME_KEY = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
#: Thin scrollbars that come and go, as Windows' own (measured: the classic one, with arrows, otherwise).
# NO LIMIT TO THE PAGES' MEMORY (2026-11-21, v0.3.11's first start: "This page is having a problem - Out of Memory"):
# a limit of 512 MB to the scripts' memory, with one page process for every panel, took the side panel down twice in
# its first 20 s ("Ineffective mark-compacts near heap limit", 510.9 of 511.8 MB, in the renderer's dump). A page
# that needs more is to be found and mended, not ended.
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
            if asked.get("op") == "unseen":
                # OUT OF THE AGENT'S OWN PICTURE A MOMENT (the user, 2026-11-16: "只是自己截图看不到，其它是可以的"):
                # only the agent's - the user's own screenshots, a meeting's shared screen, a recording see it.
                answer({"unseen": leave_out_of_pictures(bool(asked.get("out")))})
                continue
            if asked.get("op") == "focus":
                answer({"focused": focus_panel(set(asked.get("titles") or []))})
                continue
            if asked.get("op") != "open":
                answer({"error": "open or quit"})
                continue
            try:
                title = str(asked.get("title") or "")
                window = webview.create_window(
                    title, str(asked.get("url") or "about:blank"),
                    width=int(asked.get("width") or 460), height=int(asked.get("height") or 900),
                    x=int(asked.get("x") if asked.get("x") is not None else -32000),
                    y=int(asked.get("y") or 0), frameless=True, easy_drag=False, text_select=True,
                    hidden=bool(asked.get("hidden")))
                threading.Thread(target=off_the_taskbar, args=(title,), name="panel-no-taskbar", daemon=True).start()
                # Set up once its page is there: a window made hidden and shown later by the application (put in
                # its place by Win32) is never "shown" for pywebview - it is loaded.
                window.events.loaded += lambda shown=window: tell_the_shell_when_active(shown)
                window.events.shown += lambda shown=window: tell_the_shell_when_active(shown)
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


#: SHAppBarMessage's "the bar was activated".
ABM_ACTIVATE = 6
#: The handlers the window calls, kept for as long as it may: let go of, it would call nothing.
_KEPT: list = []
#: The windows whose activation is told already - once each.
_TOLD: set = set()


def tell_the_shell_when_active(window: Any) -> bool:
    """THE SHELL TOLD WHEN THE PANEL IS ACTIVATED, AS A BAR OF THE SCREEN MUST TELL IT (the user, 2026-11-17: "sidebar有焦点
    的时候，你的任务栏会变暗" - only while pinned, its strip taken as a bar of the screen, SHAppBarMessage): each time its
    window is activated, ABM_ACTIVATE - which a bar is to say on WM_ACTIVATE, so that the shell keeps its own bars (the
    taskbar) as they are. Its window not a bar (not pinned): the shell takes it as nothing. Done on the window's own
    thread (its handle read there - never from here, 2026-11-17's lock). Whether it was set up."""
    try:
        from System import EventHandler, Func, Type                         # type: ignore
        from webview.platforms.winforms import BrowserView                   # type: ignore
    except Exception:
        return False
    form = BrowserView.instances.get(getattr(window, "uid", ""))
    if form is None:
        return False
    set_up = []

    def on_its_thread() -> None:
        handle = int(form.Handle.ToInt64())
        if handle in _TOLD:
            set_up.append(handle)
            return
        _TOLD.add(handle)

        def activated(_sender: Any, _args: Any) -> None:
            try:
                appbar_activated(handle)
            except Exception:                    # nothing may leave here into the window
                pass
        delegate = EventHandler(activated)
        _KEPT.append(delegate)
        form.Activated += delegate
        set_up.append(handle)
        sys.stderr.write(f"the shell is told each time the panel's window {handle:#x} is activated\n")
        sys.stderr.flush()
    try:
        form.Invoke(Func[Type](on_its_thread))
    except Exception:
        return False
    return bool(set_up)


def appbar_activated(handle: int) -> None:
    """ABM_ACTIVATE for the window `handle` - noted once, the first time, with what the shell said."""
    import ctypes
    from .dock import _bar
    data = _bar(handle)
    said = ctypes.windll.shell32.SHAppBarMessage(ABM_ACTIVATE, ctypes.byref(data))
    if handle not in _NOTED:
        _NOTED.add(handle)
        sys.stderr.write(f"the panel's window {handle:#x} activated; the shell told (it said {said})\n")
        sys.stderr.flush()


#: The windows whose first activation was noted.
_NOTED: set = set()

#: How long the host is waited for to give its page the keys.
FOCUS_SECONDS = 1.0
#: Left out of every picture taken of the screen (Windows 10 2004 and later) - for as long as it is said.
WDA_EXCLUDEFROMCAPTURE = 0x11


def focus_panel(titles: set) -> int:
    """THE KEYS TO THE PANEL'S PAGE (the user, 2026-11-17: "那个快捷键之后，焦点应该放到chat栏（这样我可以直接输入)"): its
    window made the active one, and the page's own control given the keys - the window in front alone left them
    with the window, none with the page. Done here, where the window is; the application let this process take the
    front first (AllowSetForegroundWindow). How many it was done to."""
    try:
        from System import Func, Type                                           # type: ignore
        from webview.platforms.winforms import BrowserView                    # type: ignore
    except Exception:
        return 0
    # EVERYTHING OF THE FORM READ ON ITS OWN THREAD (2026-11-17: the panel stood still for good as it was resized):
    # a property read from here - its title - asks the window's thread (SendMessage) while this thread holds
    # Python's lock, and that thread, at a Python callback of its own, waited for the lock. Invoke, a method,
    # lets the lock go while it waits.
    done = []
    for form in list(BrowserView.instances.values()):
        def keys(form=form) -> None:
            if titles and str(form.Text) not in titles:
                return
            form.Activate()
            browser = getattr(form, "browser", None)
            if browser is not None and getattr(browser, "webview", None) is not None:
                browser.webview.Focus()
            done.append(1)
        try:
            form.Invoke(Func[Type](keys))
        except Exception:
            continue
    return len(done)


def off_the_taskbar(title: str, seconds: float = 10.0) -> bool:
    """NOT ON THE TASKBAR (the user, 2026-11-16: "你现在一直在任务栏有一个python script ... 我不想要显示任务栏的图标"): the
    panel's window a tool window - no button on the taskbar, none in Alt+Tab - as soon as it is made. Whether it was."""
    import ctypes
    user32 = ctypes.windll.user32
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        found = own_windows(title)
        if found:
            for one in found:
                style = user32.GetWindowLongW(one, -20)                        # GWL_EXSTYLE
                user32.SetWindowLongW(one, -20, (style | 0x00000080) & ~0x00040000)   # TOOLWINDOW, not APPWINDOW
                if user32.IsWindowVisible(one):
                    # The taskbar reads it again only when it is shown again.
                    user32.ShowWindow(one, 0)                                  # SW_HIDE
                    user32.ShowWindow(one, 8)                                  # SW_SHOWNA
                user32.SetWindowPos(one, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)  # FRAMECHANGED
            return True
        time.sleep(0.05)
    return False


def own_windows(title: Optional[str] = None) -> List[int]:
    """This process's top-level windows - those called `title`, when it is given."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    me = os.getpid()
    found: List[int] = []
    call = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def look(handle: int, _extra: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == me:
            if title is None:
                found.append(handle)
            else:
                text = ctypes.create_unicode_buffer(128)
                user32.GetWindowTextW(handle, text, 128)
                if text.value == title:
                    found.append(handle)
        return True
    user32.EnumWindows(call(look), 0)
    return found


def leave_out_of_pictures(out: bool, title: Optional[str] = None) -> int:
    """THE PANEL SEEN THROUGH IN THE AGENT'S PICTURE OF THE SCREEN (the user, 2026-11-16: "要注意chat现在截图的时候，
    忽视/穿透 sidebar" - "只是自己截图看不到，其它是可以的"): this process's windows left out of what the screen is
    taken as while the agent takes it, `out`; put back after, for every other picture to have them. Only the
    process a window is in may say so, hence here. How many it was done to."""
    import ctypes
    user32 = ctypes.windll.user32
    done = 0
    for one in own_windows(title):
        if user32.SetWindowDisplayAffinity(one, WDA_EXCLUDEFROMCAPTURE if out else 0):
            done += 1
    return done


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
        # SAID READY - till then, no one asks it anything (2026-11-16: a panel opened while it was starting took
        # its "ready" for the answer; it was taken for not started, ended, and the panel opened in the browser).
        self._ready = threading.Event()
        self.failed = ""                             # why it is not used - said once in the log
        self._tried_again = False                    # once, after it was slow to start

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
                begun = False
            elif not self.usable():
                return False
            else:
                begun = True
                self._ready.clear()
        if not begun:                                # started already: ready, or waited for till it is
            return self._ready.wait(START_SECONDS) and self.running()
        with self._lock:
            if self.running():                       # started meanwhile by another
                return self._ready.wait(START_SECONDS) and self.running()
            try:
                self.storage.mkdir(parents=True, exist_ok=True)
                self._child = subprocess.Popen(
                    host_command(self.storage), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=self._said_wrong(), text=True, encoding="utf-8", errors="replace", bufsize=1,
                    cwd=host_folder(),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as exc:
                self.failed = f"not started: {exc}"[:300]
                log.info("the side panel's host: %s", self.failed)
                self._ready.set()                    # those waiting told: not running
                return False
            self._answers = []
            threading.Thread(target=self._listen, args=(self._child,), name="panel-host",
                             daemon=True).start()
            # ITS WINDOWS THE APPLICATION'S OWN (windows.py): never taken for the user's, and left out of the
            # agent's own picture of the screen while it is taken - by this process, the one that may.
            from . import windows
            windows.OTHER_OWN_PROCESSES[self._child.pid] = self.leave_out
        ready = self._next(START_SECONDS, "ready")
        if not ready.get("ready") and not self._tried_again:
            # SLOW, NOT GONE (2026-11-17: once it did not say it was ready in 30 s, and the panel was not there all
            # the session): started again, once - what it wrote kept in panel_host.log beside the other logs.
            self._tried_again = True
            log.info("the side panel's host did not start in %d s - started again", START_SECONDS)
            self.stop()
            return self.start()
        if not ready.get("ready"):
            self.failed = "it did not start"
            log.info("the side panel's host did not start - the panel opens in the browser")
            self.stop()
            self._ready.set()                        # those waiting told: not running
            return False
        log.info("the side panel's host is ready (WebView2)")
        self._ready.set()
        return True

    def _said_wrong(self) -> Any:
        """Where what the host writes of what went wrong goes: panel_host.log by the application's logs (begun
        again past 2 MB) - else nowhere."""
        try:
            logs = self.storage.parent / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            path = logs / "panel_host.log"
            if path.exists() and path.stat().st_size > 2_000_000:
                path.unlink()
            self._said_file = open(path, "a", encoding="utf-8")   # noqa: SIM115 - closed at stop
            return self._said_file
        except OSError:
            return subprocess.DEVNULL

    def _listen(self, child: subprocess.Popen) -> None:
        for line in child.stdout:                    # type: ignore[union-attr]
            try:
                said = json.loads(line)
            except ValueError:
                continue
            with self._said:
                if child is not self._child:         # one ended and started again: its words no longer count
                    return
                self._answers.append(said)
                self._said.notify_all()
        with self._said:                             # its output closed: it ended
            if child is self._child:
                self._answers.append({"ended": True})
                self._said.notify_all()

    def _next(self, seconds: float, wanted: str = "") -> Dict[str, Any]:
        """The next answer - of the kind `wanted` (its key), one of another kind come late passed over: an
        answer waited for too short a while never taken for the next question's."""
        end = time.monotonic() + seconds
        with self._said:
            while True:
                while self._answers:
                    said = self._answers.pop(0)
                    if not wanted or wanted in said or "error" in said or "ended" in said:
                        return said
                left = end - time.monotonic()
                if left <= 0:
                    return {}
                self._said.wait(left)

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
            said = self._next(OPEN_SECONDS, "opened")
        if said.get("opened"):
            return True
        log.info("the side panel's host did not open it: %s", said.get("error") or "no answer")
        return False

    def focus(self, titles: set) -> bool:
        """THE KEYS TO ITS PANEL'S PAGE: this application having been pressed for (a shortcut), the host let take the
        front, and asked to give its page the keys. Whether it was done."""
        if not self.running():
            return False
        try:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(int(self._child.pid))   # type: ignore
        except Exception:
            pass
        if not self._lock.acquire(timeout=FOCUS_SECONDS):
            return False
        try:
            self._child.stdin.write(json.dumps({"op": "focus", "titles": sorted(titles)}) + "\n")  # type: ignore
            self._child.stdin.flush()                                                           # type: ignore
            said = self._next(FOCUS_SECONDS, "focused")
        except Exception:
            log.debug("the side panel's host could not be asked", exc_info=True)
            return False
        finally:
            self._lock.release()
        return bool(said.get("focused"))

    def leave_out(self, out: bool) -> bool:
        """Its panels out of the agent's own picture of the screen (`out`), or back in it: asked, and waited
        for a moment only - never long, a picture is waiting. Whether it was done."""
        if not self.running():
            return False
        if not self._lock.acquire(timeout=UNSEEN_SECONDS):
            return False
        try:
            self._child.stdin.write(json.dumps({"op": "unseen", "out": bool(out)}) + "\n")  # type: ignore
            self._child.stdin.flush()                                                     # type: ignore
            said = self._next(UNSEEN_SECONDS, "unseen")
        except Exception:
            log.debug("the side panel's host could not be asked", exc_info=True)
            return False
        finally:
            self._lock.release()
        return bool(said.get("unseen"))

    def stop(self) -> None:
        child, self._child = self._child, None
        kept, self._said_file = getattr(self, "_said_file", None), None
        if kept is not None:
            try:
                kept.close()
            except OSError:
                pass
        if child is not None:
            from . import windows
            windows.OTHER_OWN_PROCESSES.pop(child.pid, None)
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

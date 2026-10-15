"""Entry point.

    python -m personalized_agent              # desktop agent + API + background loops
    python -m personalized_agent --headless   # backend and API only (no window)
    python -m personalized_agent --status     # print one status snapshot and exit
    python -m personalized_agent --version    # print the application version
    python -m personalized_agent --root DIR   # use DIR as the single writable root
    python -m personalized_agent --sign-in    # print a link that opens the page signed in

The packaged application uses the same executable to run agent generated
scripts (section 107): ``<app> --run-guarded-script <script.py>`` installs the
runtime guard and executes the script, because a bundled application has no
system Python to call.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from pathlib import Path

from .version import APP_NAME, __version__

log = logging.getLogger(__name__)

SCRIPT_RUNNER_FLAG = "--run-guarded-script"


def _run_guarded_script(argv: list[str]) -> int:
    """Script runner mode: install the guard, then execute the script."""
    from .runtime.guard import run_script

    if not argv:
        print(f"usage: {SCRIPT_RUNNER_FLAG} <script.py> [args...]", file=sys.stderr)
        return 2
    run_script(argv[0], argv[1:])
    return 0


def _report_already_running(message: str, headless: bool) -> None:
    """Tell the user, wherever they can see it (section 110.2)."""
    _report(f"{APP_NAME} is already running", message, headless)


def _report(title: str, message: str, headless: bool) -> None:
    """A reason it does not start, wherever the user can see it."""
    print(message, file=sys.stderr)
    if headless:
        return
    try:                                    # a window user may never see stderr
        import tkinter
        from tkinter import messagebox

        window = tkinter.Tk()
        window.withdraw()
        messagebox.showerror(title, message)
        window.destroy()
    except Exception:                       # pragma: no cover - no display
        log.debug("could not show the dialog", exc_info=True)


#: How long a link printed to the terminal is good for.
LONG_TICKET_SECONDS = 15 * 60


def _print_sign_in(root: Path | None) -> int:
    """A sign-in link for the agent running from ``root`` - made from the
    key in its protected state, which the user's own account may read and
    an agent may not, so nothing is asked of the running agent.

    The settings are read for where the page is, not loaded: this must not
    touch the state of an agent that is running."""
    from .api.access import ApiAccess, key_file_for
    from .common.paths import AppPaths, resolve_root

    paths = AppPaths(resolve_root(root))
    try:
        values = json.loads(paths.settings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        values = {}
    values = values if isinstance(values, dict) else {}
    host = values.get("api_host") or "127.0.0.1"
    port = values.get("api_port") or 8756
    access = ApiAccess(key_file_for(paths.credentials_dir))
    print(access.sign_in_url(f"http://{host}:{port}", "/", LONG_TICKET_SECONDS))
    return 0


def _follow_autostart(application, root: Path | None, headless: bool) -> None:
    from .platform_layer.autostart import Autostart, launch_command
    # And Explorer's "Delegate work", started the way the agent was.
    application.launch_command = launch_command(root)
    try:
        application.follow_explorer_menu()
    except Exception:                        # pragma: no cover - the system's own
        log.warning("Explorer's Delegate work could not be set", exc_info=True)
    try:
        application.autostart = Autostart(launch_command(root, headless))
        application.autostart.follow(bool(application.config.get("start_at_login", True)))
    except Exception:                        # pragma: no cover - the system's own
        log.warning("starting at sign-in could not be set", exc_info=True)


def _set_autostart(root: Path | None, on: bool) -> int:
    """`--autostart on|off`: the setting kept, and the system's entry made so -
    without starting the agent (the installer's last page)."""
    from .common.config.store import ConfigStore
    from .common.paths import AppPaths, resolve_root
    from .platform_layer.autostart import Autostart, launch_command

    paths = AppPaths.create(resolve_root(root))
    config = ConfigStore(paths.data_dir, path=paths.settings_file)
    config.set("start_at_login", on)
    startup = Autostart(launch_command(root))
    startup.set(on)
    print(f"{APP_NAME} {'starts' if on else 'does not start'} when you sign in "
          f"({startup.where()}).")
    return 0


def _delegate(root: Path | None, path: str) -> int:
    """`--delegate PATH`: what was right-clicked in Explorer, handed to the
    agent (agent/work/handover.py) - which is started first if it is not
    running - and the Delegate work box opened on it, in a window of its own
    (platform_layer/app_window.py)."""
    import time
    import urllib.error
    import urllib.request

    from .agent.work.handover import GATHER_SECONDS, gathered, hand_over
    from .api.access import ApiAccess, key_file_for
    from .common.instance import AlreadyRunning, SingleInstance
    from .common.paths import AppPaths, resolve_root
    from .platform_layer.autostart import launch_command

    paths = AppPaths.create(resolve_root(root))
    ident, mine = hand_over(paths.data_dir, path)
    if not mine:
        return 0                               # one of several chosen together
    time.sleep(GATHER_SECONDS)                 # the rest of them, if any
    gathered(paths.data_dir, ident)
    try:
        values = json.loads(paths.settings_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        values = {}
    values = values if isinstance(values, dict) else {}
    base = f"http://{values.get('api_host') or '127.0.0.1'}:{values.get('api_port') or 8756}"
    try:
        SingleInstance(resolve_root(root)).acquire().release()
        running = False
    except AlreadyRunning:
        running = True
    if not running:
        import subprocess
        subprocess.Popen(launch_command(root), close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                         | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(base + "/", timeout=2)
                break
            except urllib.error.HTTPError:
                break                          # it answers: it is up
            except Exception:
                time.sleep(1)
    access = ApiAccess(key_file_for(paths.credentials_dir))
    from .platform_layer import app_window
    app_window.open_page(access.sign_in_url(base, f"/?solo=delegate&delegate_inbox={ident}",
                                            LONG_TICKET_SECONDS),
                         paths.data_dir / app_window.PROFILE)
    return 0


def _set_explorer_menu(root: Path | None, on: bool) -> int:
    """`--explorer-menu on|off`: Explorer's "Delegate work" put in place or
    taken out (the uninstaller takes it out)."""
    from .platform_layer import explorer_menu
    from .platform_layer.autostart import launch_command
    if on:
        explorer_menu.install(launch_command(root), None)
    else:
        explorer_menu.remove()
    return 0


def _set_display_language(root: Path | None, code: str) -> int:
    """`--display-language CODE`: the language the installer was run in,
    the one the installed program starts in - unless the user chose one
    themselves. Their own choices are kept apart (user-defaults.json, in the
    agent root, which a reinstall leaves alone) and come first: a reinstall
    or an upgrade never overrides them. Set as the installer's, not as
    theirs (`internal`), so a later install in another language may still
    change it until they choose."""
    from .common.config.store import ConfigStore
    from .common.paths import AppPaths, resolve_root

    paths = AppPaths.create(resolve_root(root))
    config = ConfigStore(paths.data_dir, path=paths.settings_file)
    own = config.user_defaults().get("display_language")
    if own:
        print(f"{APP_NAME} stays in {own}, the language you chose.")
        return 0
    config.set("display_language", code, internal=True)
    print(f"{APP_NAME} is shown in {code}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)

    # Checked before anything else is imported or constructed.
    if args_in and args_in[0] == SCRIPT_RUNNER_FLAG:
        return _run_guarded_script(args_in[1:])
    # A run of the user's work, in a process of its own (agent/work/child.py).
    from .agent.work.child import CHILD_FLAG
    if args_in and args_in[0] == CHILD_FLAG:
        from .agent.work.child import child_main
        return child_main(args_in[1:])
    # Linux without bubblewrap: the packaged application confines itself
    # with Landlock, then becomes the guarded child (runtime/isolation.py).
    from .runtime.isolation import LANDLOCK_FLAG, landlock_exec_main
    if args_in and args_in[0] == LANDLOCK_FLAG:
        return landlock_exec_main(args_in[1:])
    # A watcher over one child of the packaged application: it ends the
    # child when the application ends (Linux and macOS; see children.py).
    from .common.children import WATCH_FLAG, watch_main
    if args_in and args_in[0] == WATCH_FLAG:
        return watch_main(args_in[1:])
    # The embedding model, in a process of its own (see embedding.py).
    if args_in and args_in[0] == "--embedding-worker":
        from .heartbeat.states.embed_worker import serve
        return serve(args_in[1] if len(args_in) > 1 else
                     "sentence-transformers/all-MiniLM-L6-v2")

    parser = argparse.ArgumentParser(prog="personalized-agent",
                                     description=f"{APP_NAME} {__version__}")
    parser.add_argument("--headless", action="store_true",
                        help="run without the desktop window")
    parser.add_argument("--status", action="store_true",
                        help="print a status snapshot and exit")
    parser.add_argument("--version", action="version",
                        version=f"{APP_NAME} {__version__}")
    parser.add_argument("--root", type=Path, default=None,
                        help="the data folder: workspace/ and internal/ (section 110.1). "
                             "A different folder is a different agent - its own calendar, "
                             "contacts and keys. Without it: the one chosen when it was "
                             "installed, or the standard one in your home folder")
    parser.add_argument("--data-dir", type=Path, default=None, dest="data_dir",
                        help=argparse.SUPPRESS)  # accepted as an alias for --root
    parser.add_argument("--no-api", action="store_true", help="do not start the local API")
    parser.add_argument("--sign-in", action="store_true", dest="sign_in",
                        help="print a single-use link that opens the page signed in, "
                             "good for fifteen minutes, and exit")
    parser.add_argument(SCRIPT_RUNNER_FLAG, dest="guarded_script", metavar="SCRIPT",
                        help=argparse.SUPPRESS)
    parser.add_argument("--autostart", choices=("on", "off"), default=None,
                        help="start the agent when you sign in, or not - kept as the "
                             "setting and made so; then exit (the installer's last page)")
    from .common.languages import DISPLAY_CODES
    parser.add_argument("--display-language", choices=tuple(DISPLAY_CODES), default=None,
                        dest="display_language",
                        help="the language the agent is shown in, kept as the setting; "
                             "then exit (the installer, in the language it was run in)")
    parser.add_argument("--delegate", metavar="PATH", default=None,
                        help="hand a file or folder to the agent as a piece of work "
                             "(Explorer's \"Delegate work\"): it opens on its plan")
    parser.add_argument("--explorer-menu", choices=("on", "off"), default=None,
                        dest="explorer_menu",
                        help="put \"Delegate work\" in Explorer's right-click menu, or "
                             "take it out; then exit")
    args = parser.parse_args(args_in)

    if args.guarded_script:
        return _run_guarded_script([args.guarded_script])
    if args.sign_in:
        return _print_sign_in(args.root or args.data_dir)
    if args.autostart:
        return _set_autostart(args.root or args.data_dir, args.autostart == "on")
    if args.display_language:
        return _set_display_language(args.root or args.data_dir, args.display_language)
    if args.delegate is not None:
        return _delegate(args.root or args.data_dir, args.delegate)
    if args.explorer_menu:
        return _set_explorer_menu(args.root or args.data_dir, args.explorer_menu == "on")

    from .app import AgentApplication
    from .common.instance import AlreadyRunning, SingleInstance
    from .common.paths import resolve_root

    # THE DATA FOLDER, before anything is opened in it (section 110.1, as
    # amended): empty, or only an earlier agent's data - never a folder with
    # other things in it, which would go unheard.
    from .common.paths import data_folder_problem
    problem = data_folder_problem(resolve_root(args.root or args.data_dir))
    if problem:
        _report(f"{APP_NAME}: choose another data folder", problem, headless=args.headless)
        return 4

    # Section 110.2: one agent at a time. Taken before any state is opened,
    # because two instances share one database, one scheduler and one Listener.
    # A read-only snapshot does not run the agent, so it is not guarded.
    guard = None
    if not args.status:
        try:
            guard = SingleInstance(resolve_root(args.root or args.data_dir)).acquire()
        except AlreadyRunning as exc:
            _report_already_running(str(exc), headless=args.headless)
            return 3

    try:
        application = AgentApplication(root=args.root or args.data_dir)
    except Exception:
        if guard is not None:
            guard.release()
        raise

    if args.status:
        print(json.dumps(application.status(), indent=2, default=str))
        return 0

    # STARTING AT SIGN-IN, as the setting says (on by default): the
    # system's entry put in line with it, for the way this run was started.
    _follow_autostart(application, args.root or args.data_dir, args.headless)

    # WITH A WINDOW, THE WINDOW GOES UP FIRST. Starting the agent means
    # waking the listeners, and a listener's first act is to ask the
    # operating system what is in front of it - a question that crosses
    # into other processes and takes as long as it takes. Doing that
    # before the window exists means the user asks for the application
    # and watches nothing happen; doing it after means they see it
    # arrive and the machinery catches up behind.
    #
    # The headless path keeps the old order because there is nothing to
    # show: the next line of its output IS its progress report.
    if args.headless:
        application.start(with_api=not args.no_api)
        print(f"{APP_NAME} {__version__} running. "
              f"UI: {application.api_base_url}")
        if not args.no_api:
            # Without a window there is no icon to open the page from, so
            # the way in is printed: a link that signs the browser in once.
            # The terminal is the user's own; the link is spent when used.
            print("Open the page, signed in (once, within fifteen minutes): "
                  f"{application.sign_in_url('/', seconds=LONG_TICKET_SECONDS)}")
            print("A new link: python -m personalized_agent --sign-in", flush=True)

    if args.headless:
        stop = threading.Event()

        def handle_signal(_signum, _frame):
            stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handle_signal)
            except (ValueError, AttributeError):  # pragma: no cover - platform dependent
                pass
        try:
            stop.wait()
        except KeyboardInterrupt:
            pass
        finally:
            application.stop()
            if guard is not None:
                guard.release()
        return 0

    try:
        from .ui.desktop import DesktopAgent
    except Exception as exc:  # pragma: no cover - depends on the display
        log.error("desktop UI unavailable (%s); falling back to headless mode", exc)
        application.stop()
        if guard is not None:
            guard.release()      # the headless run takes its own
        return main([*args_in, "--headless"])

    ui = DesktopAgent(application)
    # Started on a thread of its own, so the window can be drawn and
    # answer the mouse while the listeners wake. Tk only works on the
    # thread that made it, so the greeting is handed back to that thread
    # rather than posted from here.
    def behind_the_window() -> None:
        try:
            application.start(with_api=not args.no_api)
            log.info("%s %s running. UI: %s", APP_NAME, __version__,
                     application.api_base_url)
        except Exception:
            log.exception("the agent could not start")
            ui.on_main_thread(ui.say_it_could_not_start)
            return
        ui.on_main_thread(ui.say_hello)

    threading.Thread(target=behind_the_window, name="starting",
                     daemon=True).start()
    try:
        ui.run()
    except KeyboardInterrupt:
        pass
    finally:
        application.stop()
        if guard is not None:
            guard.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())

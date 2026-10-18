"""Shared test helpers: a fully wired application in a temporary directory."""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The OS keychain is a single global namespace shared with the user's real
# installation, so tests must never write into it.
os.environ["PERSONALIZED_AGENT_NO_KEYRING"] = "1"
# Nor play a sound on the machine running them - however they are run, with
# pytest's conftest.py or without it.
os.environ["PA_NO_SOUND"] = "1"
# Nor show a folder or file dialog (common/chooser.py). Set in conftest.py
# for pytest only, so under scripts/run_tests.py - unittest - a test that
# reached a "Choose..." route opened the real dialog, at whatever folder the
# person running them had last chosen.
os.environ["PA_NO_DIALOGS"] = "1"


# NOR OPEN A WINDOW ON THE SCREEN OF WHOEVER RUNS THEM. A file manager on a
# test's folder kept appearing during test runs; a test that reaches the
# real file manager or browser fails here, naming what it tried to open,
# instead of opening it. A test that means to check the opening replaces
# these itself.
class OpenedOnScreen(AssertionError):
    pass


def _not_on_screen(*args, **_kwargs):
    raise OpenedOnScreen(f"a test tried to open {args[:1]} on the screen")


if hasattr(os, "startfile"):
    os.startfile = _not_on_screen
import webbrowser  # noqa: E402

webbrowser.open = webbrowser.open_new = webbrowser.open_new_tab = _not_on_screen
import personalized_agent.agent.work.runner as _runner  # noqa: E402

_runner.open_in_file_manager = _not_on_screen

from personalized_agent.app import AgentApplication  # noqa: E402


def keyed(app, request):
    """``request`` - a URL or a urllib Request - to ``app``'s own API,
    carrying its key, as every client of the application's own sends it
    (api/access.py). A test that means to be refused builds its request
    without this."""
    import urllib.request

    if isinstance(request, str):
        request = urllib.request.Request(request)
    for name, value in app.api_access.headers().items():
        request.add_header(name, value)
    return request


class TemporaryAgent:
    """Builds an AgentApplication whose data and workspace live in a temp dir.

    Background loops are not started; tests drive the components directly.
    """

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="pa-test-")).resolve()
        # Section 110.1: one root holding workspace/ and internal/.
        self.app = AgentApplication(root=self.dir / "root")
        logging.getLogger().setLevel(logging.CRITICAL)
        from personalized_agent.runtime.permissions import WorkspacePolicy
        from personalized_agent.runtime.sandbox import SandboxRuntime

        from personalized_agent.agent.dialog.files import grant_chat
        self.app.policy = grant_chat(WorkspacePolicy.create(
            self.app.paths.workspace_dir,
            readable_paths=[self.dir],
            protected_paths=self.app.paths.protected_paths(),
        ))
        self.app.runtime = SandboxRuntime(
            self.app.policy,
            platform=self.app.platform,
            observability=self.app.observability,
            bus=self.app.bus,
            sandbox_enabled=False,  # exercise the application level guarantees
            tracer=self.app.observability_service.tracer_for("runtime"),
        )
        self.app.agent.runtime = self.app.runtime
        # Runs in this process: the tests give them their own models.
        self.app.work.in_subprocess = False
        # ROOM FOR EVERY RUN: whether one waits its turn is the tests' of
        # the governor and the pool (test_work_processes, test_delegate,
        # test_resources_and_room) - not how busy the computer running the
        # tests happens to be.
        self.app.work.governor.room = lambda going: (True, "")
        self.app.work.pool = None
        self.app.executor.runtime = self.app.runtime
        # The work runner reads its boundary from here too. Left as built, it
        # would be the real one - the user's whole home readable - and its
        # children, isolated by the operating system, would be granted read
        # on the real home rather than on this temporary folder.
        if getattr(self.app, "work", None) is not None:
            self.app.work.app_policy = self.app.policy
            # The application's code, as the Experience debugger's ways may
            # not read it: a stand-in in this folder. Left as built, it
            # would be the real checkout - and on Windows a debugger run
            # would seal the checkout itself while the test ran.
            self.code_root = self.dir / "app_code"
            (self.code_root / "pkg").mkdir(parents=True)
            (self.code_root / "pkg" / "judge.py").write_text(
                "HOW_IT_JUDGES = 'secret'\n", encoding="utf-8")
            self.app.work.code_root = self.code_root
        self.app.config.set("listener_enabled", False)
        self.app.config.set("notifications_enabled", True)
        # Five states, which is what the grouping tests were written at:
        # a state the tests write records no window, so it counts one of
        # fifteen minutes, and a task holds more than an hour - five.
        self.app.config.set("state_task_min_hours", 1.0)

    @property
    def workspace(self) -> Path:
        return self.app.policy.workspace_root

    @property
    def root(self) -> Path:
        return self.app.root

    def cleanup(self) -> None:
        try:
            self.app.db.close()
        except Exception:
            pass
        shutil.rmtree(self.dir, ignore_errors=True)


def asking(app: AgentApplication) -> AgentApplication:
    """Hand the question store back to what used to ask.

    The app asks nothing now - every event is on the calendar, and a task
    that wants approval is approved there - but the asking is kept, as code,
    and its tests run it with the store attached the way it once was.
    """
    app.notifications.questions = app.questions
    app.executor.questions = app.questions
    app.scheduler.questions = app.questions
    return app

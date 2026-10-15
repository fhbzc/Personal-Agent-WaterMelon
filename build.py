#!/usr/bin/env python3
"""Common build entry point (section 102).

    python build.py                      # build for the current operating system
    python build.py --platform windows   # explicit target (must run on that OS)
    python build.py --validate-only      # run the build-time checks and stop
    python build.py --skip-tests         # skip the automated tests before packaging

Section 103: a target can only be produced on its own operating system, so an
explicit cross-platform request fails with an explanation rather than emitting a
broken artifact.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from build.common import (  # noqa: E402
    APP_NAME,
    DIST_ENV,
    BuildError,
    __version__,
    current_platform,
    detect_cloud_sync,
    dist_root,
    running_agent_processes,
    terminate_agent_processes,
)
from build.validate_dependencies import validate  # noqa: E402

BUILDERS = {
    "windows": "build.windows.build_windows",
    "macos": "build.macos.build_macos",
    "linux": "build.linux.build_linux",
}


def run_tests() -> int:
    print("running the automated test suite before packaging ...", flush=True)
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=str(REPO_ROOT),
    )
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Build {APP_NAME} {__version__}")
    parser.add_argument("--platform", choices=sorted(BUILDERS), default=None,
                        help="target platform (defaults to the current one)")
    parser.add_argument("--validate-only", action="store_true",
                        help="run build-time validation and exit")
    parser.add_argument("--skip-tests", action="store_true",
                        help="do not run the test suite first")
    parser.add_argument("--no-installer", action="store_true",
                        help="Windows only: skip the Inno Setup installer")
    parser.add_argument("--dist", type=Path, default=None,
                        help=f"where to publish artifacts (default ./dist, env {DIST_ENV})")
    parser.add_argument("--fast", action="store_true",
                        help="build for trying it out: skip the tests, keep PyInstaller's "
                             "analysis cache, and skip the installer and portable archive")
    parser.add_argument("--no-debugger", action="store_true",
                        help="leave the Debugger and every debugger tab out of the "
                             "build entirely - as the released installers and "
                             "downloads are built. They show the agent's internals - "
                             "every state it has inferred, each call's prompt - which "
                             "a build for someone not developing the agent has no "
                             "business carrying. Without it, a build carries them")
    parser.add_argument("--kill-running", action="store_true",
                        help="terminate a running built application that holds its own files")
    args = parser.parse_args(argv)

    if args.dist:
        os.environ[DIST_ENV] = str(args.dist.expanduser().resolve())

    target = args.platform or current_platform()
    print(f"{APP_NAME} {__version__} - building for {target}")
    print(f"publishing artifacts to {dist_root()}\n")

    if args.kill_running:
        killed = terminate_agent_processes()
        if killed:
            print(f"terminated running application processes: {killed}")
    else:
        running = running_agent_processes()
        if running:
            print("warning: a built application is still running and will lock its own files:")
            for pid, name, executable in running:
                print(f"  pid {pid}  {name}  {executable}")
            print("  close it, or re-run with --kill-running\n")

    provider = detect_cloud_sync(dist_root())
    if provider:
        # Packaging writes thousands of small files; a sync client locking one
        # of them mid-write is the usual cause of a failed clean (section 113).
        print(f"note: the output directory is inside a {provider} folder.")
        print("      packaging runs in a temporary staging directory and only the finished")
        print(f"      artifact is copied in. If {provider} still locks the result, publish")
        print(f"      elsewhere:  python build.py --dist <path outside {provider}>\n")

    status = validate(target)
    if status != 0:
        return status
    if args.validate_only:
        return 0

    if not (args.skip_tests or args.fast):
        status = run_tests()
        if status != 0:
            print("build aborted: the test suite failed", file=sys.stderr)
            return status

    import os

    from build.common import NO_DEBUGGER_ENV

    if args.no_debugger:
        os.environ[NO_DEBUGGER_ENV] = "1"
        print("building without the Debugger or any debugger tab (as released)")
    else:
        os.environ.pop(NO_DEBUGGER_ENV, None)
        print("building WITH the Debugger and every debugger tab "
              "(--no-debugger leaves them out, as the released downloads do)")

    import importlib

    module = importlib.import_module(BUILDERS[target])
    try:
        if target == "windows":
            return module.build(skip_installer=args.no_installer, fast=args.fast)
        return module.build()
    except BuildError as exc:
        print(f"build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

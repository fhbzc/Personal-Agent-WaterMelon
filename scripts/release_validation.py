#!/usr/bin/env python3
"""Clean-machine release validation (sections 114, 115, 118).

Launches a built artifact - not a developer checkout - and walks the acceptance
list from section 114:

  1. install or launch the application       8. open the Overview
  2. complete initial configuration          9. open Settings
  3. select a writable workspace            10. start Listener components
  4. configure a model                      11. run an immediate agent task
  5. authenticate where supported           12. create a scheduled task
  6. advertised authentication works        13. restart and verify persistence
  7. launch the desktop agent               14. verify filesystem sandbox restrictions

Usage::

    python scripts/release_validation.py --executable dist/windows/PersonalizedAgent/PersonalizedAgent.exe
    python scripts/release_validation.py --dev          # validate the source checkout instead

The validated instance uses a throwaway data directory and workspace, so it
never touches the developer's own agent state.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]


class ValidationError(AssertionError):
    pass


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class AgentUnderTest:
    def __init__(self, command: List[str], root: Path, port: int) -> None:
        self.command = command
        self.root = root
        self.workspace = root / "workspace"
        self.data_dir = root / "internal"
        self.port = port
        self.process: Optional[subprocess.Popen] = None
        self.base = f"http://127.0.0.1:{port}"

    def launch(self, timeout: float = 90.0) -> None:
        # Section 110.2: the artifact under test runs against a throwaway root
        # while the user's own agent may be running. Only the machine-wide
        # guard is waived; the root lock still protects this root.
        # A throwaway instance must not reach the user's own credentials. Setting
        # PERSONALIZED_AGENT_HOME makes the keychain service name resolve to the
        # default installation's, so without this the validation run reads - and
        # could write - the credentials of the agent the user actually uses.
        env = dict(os.environ, PERSONALIZED_AGENT_HOME=str(self.root),
                   PERSONALIZED_AGENT_ALLOW_SECOND_INSTANCE="1",
                   PERSONALIZED_AGENT_NO_KEYRING="1")
        self.process = subprocess.Popen(
            [*self.command, "--headless", "--root", str(self.root)],
            cwd=str(REPO_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                output = (self.process.stdout.read() if self.process.stdout else "") or ""
                raise ValidationError(
                    f"the application exited with code {self.process.returncode}\n{output[-2000:]}"
                )
            try:
                self.get("/api/status", timeout=3)
                return
            except Exception:
                time.sleep(1.0)
        raise ValidationError(f"the application did not answer on {self.base} within {timeout}s")

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None

    # -- HTTP helpers -------------------------------------------------------
    def _keyed(self, request: Any) -> Any:
        """With the API's key, read from the root under test as the user's
        own tools may (api/access.py). Read each time: the agent makes it
        when its API starts, and until then there is nothing to send - and
        nothing that would answer."""
        if isinstance(request, str):
            request = urllib.request.Request(request)
        try:
            key = (self.data_dir / "credentials" / "api_access.key").read_text(
                encoding="ascii").strip()
        except OSError:
            key = ""
        if key:
            request.add_header("X-PA-Key", key)
        return request

    def get(self, path: str, timeout: float = 30) -> Any:
        with urllib.request.urlopen(self._keyed(f"{self.base}{path}"),
                                    timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def post(self, path: str, payload: Dict[str, Any], timeout: float = 120) -> Any:
        request = urllib.request.Request(
            f"{self.base}{path}", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(self._keyed(request), timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def raw(self, path: str, timeout: float = 30) -> str:
        with urllib.request.urlopen(self._keyed(f"{self.base}{path}"),
                                    timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")


class Validator:
    def __init__(self, agent: AgentUnderTest, expect_packaged: bool) -> None:
        self.agent = agent
        self.expect_packaged = expect_packaged
        self.results: List[Dict[str, Any]] = []

    def step(self, number: int, name: str, fn) -> bool:
        try:
            detail = fn() or ""
            self.results.append({"step": number, "name": name, "passed": True,
                                 "detail": str(detail)[:300]})
            print(f"  [{number:2d}] PASS  {name}  {str(detail)[:120]}")
            return True
        except Exception as exc:
            self.results.append({"step": number, "name": name, "passed": False,
                                 "detail": f"{type(exc).__name__}: {exc}"})
            print(f"  [{number:2d}] FAIL  {name}: {type(exc).__name__}: {exc}")
            return False

    # -- individual steps ---------------------------------------------------
    def launch(self) -> str:
        self.agent.launch()
        status = self.agent.get("/api/status")
        if self.expect_packaged and not status["paths"]["frozen"]:
            raise ValidationError("the artifact under test is not a packaged application")
        return f"{status['application']['name']} {status['application']['version']}"

    def initial_configuration(self) -> str:
        settings = self.agent.get("/api/settings")
        if not settings["schema"]:
            raise ValidationError("the settings schema is empty")
        applied = self.agent.post("/api/settings", {"values": {"agent_name": "Release Check"}})
        if applied["values"]["agent_name"] != "Release Check":
            raise ValidationError("a setting change did not persist")
        return f"{len(settings['schema'])} settings available"

    def select_workspace(self) -> str:
        """Section 110.1: one root, two subdirectories, nothing written outside."""
        status = self.agent.get("/api/status")
        workspace = Path(status["runtime"]["policy"]["workspace_root"])
        internal = Path(status["paths"]["internal_data"])
        root = Path(status["paths"]["root"])
        if root.resolve() != self.agent.root.resolve():
            raise ValidationError(f"root is {root}, expected {self.agent.root}")
        if workspace.resolve() != (root / "workspace").resolve():
            raise ValidationError(f"workspace is {workspace}, expected {root / 'workspace'}")
        if internal.resolve() != (root / "internal").resolve():
            raise ValidationError(f"internal state is {internal}, expected {root / 'internal'}")
        if not (workspace / "agent" / ".agent_runtime" / "scripts").exists():
            raise ValidationError("the agent runtime directory was not created in the workspace")
        if internal.resolve() in workspace.resolve().parents or                 workspace.resolve() in internal.resolve().parents:
            raise ValidationError("the two storage domains are nested, not siblings")
        return f"{root} (workspace/ + internal/)"

    def configure_model(self) -> str:
        models = self.agent.get("/api/models")
        providers = [p["provider"] for p in models["providers"]]
        if "offline" not in providers:
            raise ValidationError(f"expected an offline provider, found {providers}")
        self.agent.post("/api/settings",
                        {"values": {"primary_provider": "offline", "primary_model": "offline-1"}})
        availability = self.agent.post("/api/models/check", {"role": "primary"})
        if not availability["available"]:
            raise ValidationError(f"the configured model is unavailable: {availability}")
        return f"providers: {', '.join(providers)}"

    def authenticate(self) -> str:
        models = self.agent.get("/api/models")
        methods = {p["provider"]: p["authentication_methods"] for p in models["providers"]}
        if "anthropic" not in methods or "api_key" not in methods["anthropic"]:
            raise ValidationError("API key authentication is not offered by any provider")
        payload = json.dumps(models)
        for marker in ("sk-ant-", "PRIVATE KEY"):
            if marker in payload:
                raise ValidationError("credential material appeared in an API response")
        return f"methods: {methods}"

    def advertised_capabilities_work(self) -> str:
        """Section 113, as amended: an advertised capability must be startable.

        A packaged build that dropped a dependency's helper binaries still
        imports the dependency and still advertises the feature; it fails only
        when a user presses the button. This exercises the start of the flow so
        the build pipeline finds that, not the user.

        Note: a provider tool that opens the browser itself will open a tab
        here. The sign-in is cancelled immediately and nothing is minted.
        """
        tools = self.agent.get("/api/models/login/tools?provider=anthropic")["tools"]
        offered = "account_login" in {
            method
            for provider in self.agent.get("/api/models")["providers"]
            if provider["provider"] == "anthropic"
            for method in provider["authentication_methods"]
        }
        usable = [tool for tool in tools if tool["usable"]]
        if not offered:
            reasons = [tool["tool"] for tool in tools if tool["installed"]]
            if usable:
                raise ValidationError(
                    f"account login is usable ({usable[0]['tool']}) but not offered")
            return ("account login not offered: no sign-in tool on this machine"
                    + (f" (installed but unusable: {', '.join(reasons)})" if reasons else ""))
        if not usable:
            status = next((p["auth_status"] for p in self.agent.get("/api/models")["providers"]
                           if p["provider"] == "anthropic"), {})
            if status.get("state") == "authenticated":
                return ("account login is offered because a credential already exists "
                        f"({status.get('detail', '')[:60]})")
            raise ValidationError("account login is offered but no tool can perform it")
        started = self.agent.post("/api/models/login/start",
                                  {"provider": "anthropic", "open_browser": False})["login"]
        try:
            if started["state"] != "awaiting_authorization":
                raise ValidationError(
                    f"account login is offered but could not start: {started['detail']}")
            url = started.get("authorization_url") or ""
            if not url.startswith("https://"):
                raise ValidationError("the sign-in produced no authorization URL")
            if " " in url or len(url) > 2048:
                raise ValidationError(f"the authorization URL looks malformed: {url[:80]}")
        finally:
            self.agent.post("/api/models/login/cancel", {"provider": "anthropic"})
        return f"account login starts through {started['tool']}"

    def desktop_agent(self) -> str:
        characters = self.agent.get("/api/characters")
        selected = characters.get("selected") or {}
        frames = selected.get("frames") or {}
        if not frames.get("idle"):
            raise ValidationError("the built-in character package has no idle frames")
        if selected.get("read_only") is not True:
            raise ValidationError("built-in character assets are not read-only")
        index = self.agent.raw("/")
        if "Personalized" not in index:
            raise ValidationError("the frontend did not load")
        return f"character '{selected.get('name')}' with {len(frames['idle'])} idle frames"

    def open_overview(self) -> str:
        overview = self.agent.get("/api/overview?hours=24")
        for key in ("total_seconds", "applications", "recent_events"):
            if key not in overview:
                raise ValidationError(f"the overview payload is missing {key}")
        return f"{overview['total_human']} recorded"

    def open_settings(self) -> str:
        settings = self.agent.get("/api/settings")
        sections = {field["section"] for field in settings["schema"]}
        if "Models" not in sections or "Listener" not in sections:
            raise ValidationError(f"unexpected settings sections: {sections}")
        return f"sections: {', '.join(sorted(sections))}"

    def start_listener(self) -> str:
        self.agent.post("/api/settings", {"values": {"listener_enabled": True,
                                                     "listener_application_enabled": True}})
        health = self.agent.get("/api/listener")
        states = {p["provider_id"]: p["state"] for p in health["providers"]}
        if not states:
            raise ValidationError("no listener providers were discovered")
        if not any(state == "running" for state in states.values()):
            raise ValidationError(f"no listener provider is running: {states}")
        return f"providers: {states}"

    def immediate_task(self) -> str:
        result = self.agent.post("/api/chat", {"message": "Summarise what you can see so far."})
        if not result.get("answer"):
            raise ValidationError("the agent produced no answer")
        edits = self.agent.get("/api/edits?limit=20")["edits"]
        kinds = {edit["event_type"] for edit in edits}
        if "agent.response" not in kinds:
            raise ValidationError(f"the agent activity was not recorded: {kinds}")
        return f"{result['classification']} task, {len(result['tool_calls'])} tool calls"

    def scheduled_task(self) -> str:
        from datetime import datetime, timedelta, timezone

        when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        created = self.agent.post("/api/tasks", {
            "title": "release validation task",
            "scheduled_at": when,
            "execution_type": "notification",
            "payload": {"message": "created by release validation"},
        })
        self.task_id = created["id"]
        tasks = self.agent.get("/api/tasks")["tasks"]
        if self.task_id not in [task["id"] for task in tasks]:
            raise ValidationError("the scheduled task was not stored")
        return f"task {self.task_id}"

    def restart_persistence(self) -> str:
        graph_before = self.agent.get("/api/graph/stats")
        self.agent.stop()
        time.sleep(2)
        self.agent.launch()
        tasks = self.agent.get("/api/tasks")["tasks"]
        restored = [task for task in tasks if task["id"] == getattr(self, "task_id", "")]
        if not restored:
            raise ValidationError("the scheduled task did not survive a restart")
        if restored[0]["status"] not in ("pending", "running"):
            raise ValidationError(f"unexpected task status after restart: {restored[0]['status']}")
        settings = self.agent.get("/api/settings")["values"]
        if settings["agent_name"] != "Release Check":
            raise ValidationError("settings did not survive a restart")
        graph_after = self.agent.get("/api/graph/stats")
        if graph_after["nodes"] < graph_before["nodes"]:
            raise ValidationError("knowledge graph nodes were lost on restart")
        return f"task, settings and {graph_after['nodes']} graph nodes restored"

    def sandbox_restrictions(self) -> str:
        report = self.agent.get("/api/runtime/selftest", timeout=300)
        failed = [check["check"] for check in report["checks"] if not check["passed"]]
        if failed:
            raise ValidationError(f"sandbox self test failures: {failed}")
        return f"{len(report['checks'])} checks passed, isolation: {report['isolation']}"

    # -- driver -------------------------------------------------------------
    def run(self) -> bool:
        steps = [
            (1, "install or launch the application", self.launch),
            (2, "complete initial configuration", self.initial_configuration),
            (3, "select a writable workspace", self.select_workspace),
            (4, "configure a model", self.configure_model),
            (5, "authentication is available and credentials stay hidden", self.authenticate),
            (6, "advertised authentication actually works", self.advertised_capabilities_work),
            (7, "desktop agent resources are present", self.desktop_agent),
            (8, "open the Overview", self.open_overview),
            (9, "open Settings", self.open_settings),
            (10, "start Listener components", self.start_listener),
            (11, "run an immediate agent task", self.immediate_task),
            (12, "create a scheduled task", self.scheduled_task),
            (13, "restart and verify persistent state", self.restart_persistence),
            (14, "verify filesystem sandbox restrictions", self.sandbox_restrictions),
        ]
        ok = True
        for number, name, fn in steps:
            if not self.step(number, name, fn):
                ok = False
                if number == 1:
                    break
        return ok


def resolve_command(args: argparse.Namespace) -> List[str]:
    if args.dev:
        return [sys.executable, str(REPO_ROOT / "run.py")]
    if args.executable:
        executable = Path(args.executable)
    else:
        candidates = [
            REPO_ROOT / "dist/windows/PersonalizedAgent/PersonalizedAgent.exe",
            REPO_ROOT / "dist/macos/Personalized Agent.app/Contents/MacOS/Personalized Agent",
            REPO_ROOT / "dist/linux/PersonalizedAgent/PersonalizedAgent",
        ]
        found = [path for path in candidates if path.exists()]
        if not found:
            raise SystemExit(
                "no built artifact found - run `python build.py` first, or pass --executable/--dev"
            )
        executable = found[0]
    if not executable.exists():
        raise SystemExit(f"executable not found: {executable}")
    return [str(executable)]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Personalized Agent release artifact")
    parser.add_argument("--executable", help="path to the built application")
    parser.add_argument("--dev", action="store_true",
                        help="validate the source checkout instead of a built artifact")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway directories")
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    args = parser.parse_args(argv)

    command = resolve_command(args)
    sandbox_root = Path(tempfile.mkdtemp(prefix="pa-release-validation-"))
    # Section 110.1: one writable root, both domains inside it.  The validation
    # hands the artifact a throwaway root and then checks that nothing was
    # written anywhere else.
    root = sandbox_root / "AgentRoot"
    data_dir = root / "internal"
    workspace = root / "workspace"
    port = free_port()

    print(f"validating: {' '.join(command)}")
    print(f"  root:      {root}")
    print(f"  workspace: {workspace}")
    print(f"  internal:  {data_dir}")
    print(f"  api port:  {port}\n")

    agent = AgentUnderTest(command, root, port)
    # The launcher picks its own port through the settings file the app writes,
    # so seed the port before the first launch.  Only derived, non-readonly
    # values belong here: the workspace location follows from the root.
    (data_dir / "settings").mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    (data_dir / "settings" / "settings.json").write_text(
        json.dumps({"api_port": port, "listener_watch_paths": str(workspace)}, indent=2),
        encoding="utf-8",
    )

    validator = Validator(agent, expect_packaged=not args.dev)
    try:
        ok = validator.run()
    finally:
        agent.stop()

    report = {
        "command": command,
        "packaged": not args.dev,
        "passed": ok,
        "steps": validator.results,
    }
    if args.report:
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nreport written to {args.report}")
    if not args.keep:
        shutil.rmtree(sandbox_root, ignore_errors=True)

    print("\nrelease validation:", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

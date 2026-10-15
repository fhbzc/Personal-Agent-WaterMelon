"""Workspace policy and the security violation type (sections 2.3, 30, 31, 107-111).

Two invariants are enforced here:

* no process controlled by the Personalized Agent may write outside the
  user-authorized writable workspace;
* agent-controlled code may neither read nor write the application's trusted
  internal state (settings, databases, credentials, logs).

Both hold identically in a development checkout and in a packaged application
(section 107): installing the agent as an executable grants generated code no
additional filesystem authority.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common.paths import (
    AGENT_OUTPUT_DIR,
    AGENT_OUTPUT_SUBDIRS,
    AGENT_RUNTIME_DIR,
    WORKSPACE_READONLY_SUBDIRS,
    WORKSPACE_RUNTIME_SUBDIRS,
    WORKSPACE_USER_SUBDIRS,
)


class SecurityViolation(Exception):
    """Raised whenever a read or write crosses an enforced boundary."""

    def __init__(self, message: str, requested_path: Optional[str] = None,
                 workspace_root: Optional[str] = None, boundary: str = "workspace") -> None:
        super().__init__(message)
        self.requested_path = requested_path
        self.workspace_root = workspace_root
        self.boundary = boundary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": "security_violation",
            "message": str(self),
            "requested_path": self.requested_path,
            "workspace_root": self.workspace_root,
            "boundary": self.boundary,
        }


@dataclass
class WorkspacePolicy:
    """What agent controlled code may touch.

    Reading and writing are scoped differently, and deliberately so (section
    32, as amended):

    * ``workspace_root`` is what agent controlled code may **read**. It holds
      the user's own material as well as the agent's, and the agent is meant
      to be able to see the work it is helping with.
    * ``writable_root`` is the one directory it may **write**, a subdirectory
      of the workspace belonging to the agent. Everything it produces is
      therefore in one place the user can read, keep or delete wholesale, and
      nothing the user put in the workspace themselves can be overwritten.
    * ``readonly_paths`` are the layers inside the workspace that the agent
      may read and may never modify: the user's own material, and anything
      the application records there. The write root already excludes them;
      they are enforced separately so the protection is a stated rule rather
      than a consequence of where the write root happens to sit.
    * ``protected_paths`` is trusted application state that is neither
      readable nor writable by agent controlled code, even though the
      application itself writes there. The event log lives here, which is why
      the agent can neither read nor alter what was recorded about the user.

    Three layers, from most open to least: the agent's own directory, which it
    reads and writes; the rest of the workspace, which it reads; and the
    internal state, which it does not see at all.
    """

    workspace_root: Path
    #: Where agent controlled code may write. Defaults to the workspace root
    #: only when constructed directly, which is a convenience for tests; the
    #: application always supplies the agent's own directory.
    writable_root: Optional[Path] = None
    readable_paths: List[Path] = field(default_factory=list)
    #: Inside the workspace, readable, never writable.
    readonly_paths: List[Path] = field(default_factory=list)
    protected_paths: List[Path] = field(default_factory=list)
    #: Folders the USER granted a piece of work, beside its own: written as
    #: freely as its own - their files changed or deleted - which is why
    #: the user is warned when they grant one. A grant outranks the
    #: read-only layers (the user chose it), never the protected state.
    extra_writable: List[Path] = field(default_factory=list)
    #: Whether code the agent runs may open network connections. Off for
    #: work runs: the web is read through the fetch tool alone, which only
    #: reads and carries none of the user's sign-ins.
    network: bool = True
    #: With network on, the only places code may connect to, as
    #: "host:port" - empty is anywhere. The package installer's policy
    #: names the package index here and nothing else.
    network_allow: List[str] = field(default_factory=list)
    #: Folders, by name, that code it runs may read and never change,
    #: wherever they are beneath the folders it may write: a run's saved
    #: versions (the application rolls the work back from them) and the
    #: installed packages (pip's alone - its policy leaves this one out).
    #: Kept by the operating system where it isolates the child, and by the
    #: guard inside it; the application's own writes are not affected.
    child_readonly_names: List[str] = field(
        default_factory=lambda: [".versions", ".packages"])
    #: SEALED: folders this agent may neither read nor write, wherever they
    #: are - though what holds them may be readable. The Experience
    #: debugger's three ways each work in a subfolder of one folder; each is
    #: sealed from the other two, so no way can read - or copy - what another
    #: made. Kept by the path checks here, by the guard inside code it runs,
    #: and by the operating system where it isolates the child (on Windows by
    #: the sealed folder's own ACL: see runtime/isolation.seal_from_containers).
    #: Not the protected state: hiding a folder from an AppContainer that way
    #: would re-grant every readable root above it.
    sealed_paths: List[Path] = field(default_factory=list)
    #: THE APPLICATION'S OWN CODE (common/paths.app_code_root), when this
    #: agent may not READ it: a way of the Experience debugger, which is
    #: measured, and must not learn how the system that measures it works -
    #: the user's decision. Real work and the chat read it as before (None).
    #: Kept like a sealed folder - by the path checks here, by the guard
    #: inside code it runs, and by the operating system around that code -
    #: with a message of its own; on Windows not by a seal made once, but
    #: for as long as a way is running (runtime/isolation.CodeSeal).
    #: Reading alone: what may be written is decided by the rules above.
    code_root: Optional[Path] = None
    #: CLOSED: the record of what happened - a project's run logs (its
    #: transcripts, with every prompt, and its live sessions), and where a
    #: run from before kept its own. Folders inside what it may write that
    #: it may neither read, list nor write - every run, real or the
    #: debugger's: an agent that read its own transcript would read the
    #: prompts that shape it, and fill its context with them. Its file
    #: tools refuse reading them here (and writing, by their locked list);
    #: the guard refuses both inside code it runs; the operating system
    #: hides them from that code (on Windows by the folder's own ACL, sealed
    #: from every container: runtime/isolation.seal_from_containers). The
    #: application writes them itself, through this policy: its own writes
    #: are not refused.
    closed_paths: List[Path] = field(default_factory=list)

    @property
    def write_root(self) -> Path:
        """The agent's own directory: where relative paths land."""
        return self.writable_root or self.workspace_root

    @property
    def write_roots(self) -> List[Path]:
        """Every directory writes may go to: its own, then the granted."""
        return [self.write_root, *self.extra_writable]

    @property
    def child_code_root(self) -> Optional[Path]:
        """The code root as code this agent runs is kept out of it: none
        in a packaged application, whose interpreter reads its own bundle
        (common/paths.code_root_for_children)."""
        if not self.code_root:
            return None
        from ..common.paths import is_frozen
        if is_frozen():
            return None
        try:
            return Path(os.path.realpath(os.fspath(self.code_root)))
        except (OSError, ValueError):
            return Path(self.code_root)

    @staticmethod
    def create(
        workspace_root: os.PathLike | str,
        readable_paths: Optional[List[os.PathLike | str]] = None,
        protected_paths: Optional[List[os.PathLike | str]] = None,
    ) -> "WorkspacePolicy":
        root = Path(workspace_root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        for sub in WORKSPACE_USER_SUBDIRS:
            (root / sub).mkdir(exist_ok=True)
        # Section 32, as amended: the agent writes here and nowhere else.
        agent_root = root / AGENT_OUTPUT_DIR
        agent_root.mkdir(exist_ok=True)
        for sub in AGENT_OUTPUT_SUBDIRS:
            (agent_root / sub).mkdir(exist_ok=True)
        # Section 111, as amended: the runtime scratch is agent-controlled
        # output like everything else the agent makes, so it sits inside the
        # agent's own directory rather than beside the user's material. That
        # keeps the whole of what the agent produced in one place the user can
        # inspect or delete wholesale.
        runtime_root = agent_root / AGENT_RUNTIME_DIR
        runtime_root.mkdir(exist_ok=True)
        for sub in WORKSPACE_RUNTIME_SUBDIRS:
            (runtime_root / sub).mkdir(exist_ok=True)
        _hide(runtime_root)
        return WorkspacePolicy(
            workspace_root=root.resolve(),
            writable_root=agent_root.resolve(),
            readonly_paths=[(root / sub).resolve() for sub in WORKSPACE_READONLY_SUBDIRS],
            readable_paths=[Path(p).expanduser() for p in (readable_paths or [])],
            protected_paths=[Path(p).expanduser().resolve() for p in (protected_paths or [])],
        )

    # -- workspace layout ---------------------------------------------------
    def subdir(self, name: str) -> Path:
        target = self.workspace_root / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    def agent_subdir(self, name: str) -> Path:
        """A directory inside the one place the agent may write."""
        target = self.write_root / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    @property
    def runtime_dir(self) -> Path:
        """``<workspace>/agent/.agent_runtime`` (section 111, as amended)."""
        return self.agent_subdir(AGENT_RUNTIME_DIR)

    def runtime_subdir(self, name: str) -> Path:
        target = self.runtime_dir / name
        target.mkdir(parents=True, exist_ok=True)
        return target

    @property
    def scripts_dir(self) -> Path:
        return self.runtime_subdir("scripts")

    @property
    def tasks_dir(self) -> Path:
        return self.runtime_subdir("tasks")

    @property
    def temp_dir(self) -> Path:
        return self.runtime_subdir("temp")

    @property
    def agent_dir(self) -> Path:
        """``<workspace>/agent`` - everything the agent produced."""
        return self.write_root

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_root": str(self.workspace_root),
            "writable_root": str(self.write_root),
            "readable_paths": [str(p) for p in self.readable_paths],
            "granted_paths": [str(p) for p in self.extra_writable],
            "readonly_paths": [str(p) for p in self.readonly_paths],
            "protected_paths": [str(p) for p in self.protected_paths],
            "sealed_paths": [str(p) for p in self.sealed_paths],
            "code_root": str(self.code_root) if self.code_root else None,
            "closed_paths": [str(p) for p in self.closed_paths],
            "user_subdirectories": list(WORKSPACE_USER_SUBDIRS),
            "runtime_directory": f"{AGENT_OUTPUT_DIR}/{AGENT_RUNTIME_DIR}/"
                                 + "|".join(WORKSPACE_RUNTIME_SUBDIRS),
        }


def _hide(path: Path) -> None:
    """Best effort: keep the agent runtime directory out of normal user views."""
    if os.name != "nt":
        return  # a leading dot already hides it on POSIX
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x02
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:  # pragma: no cover - cosmetic only
        pass

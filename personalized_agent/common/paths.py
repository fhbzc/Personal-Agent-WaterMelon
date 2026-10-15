"""Storage layout (sections 30, 107-112, and the amended 110.1).

The user configures **one** writable root.  Everything the application writes
lives inside it, in two sibling subdirectories:

    <AGENT_ROOT>/
        workspace/     user-authorized agent workspace - agent-controlled writes
        internal/      trusted application state - settings, database, logs,
                       credentials, installed packages

Nothing outside the root is ever written, including operating-system managed
application state.  The trust boundary is unchanged: agent-controlled code may
write only inside ``workspace/`` and may neither read nor write ``internal/``,
even though both are inside the same root (section 110.1).

The root has to be known before any state can be read, so it comes from the
launch environment - a command line argument, an environment variable, or the
documented default - never from a configuration file that lives inside it.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..version import APP_DIR_NAME, APP_SLUG

log = logging.getLogger(__name__)

ENV_ROOT = "PERSONALIZED_AGENT_HOME"

WORKSPACE_DIR = "workspace"
INTERNAL_DIR = "internal"

# Locations used before the single-root layout; migrated once, then left alone.
LEGACY_DIRS = (
    Path.home() / ".personalized_agent",
)

MIGRATED_SUBDIRS = ("settings", "database", "logs", "credentials", "characters", "cache")


def is_frozen() -> bool:
    """True when running from a packaged (PyInstaller) application."""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Read-only root of the packaged application resources (section 112)."""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle)
    return Path(__file__).resolve().parents[1]


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)


def app_code_root() -> Path:
    """THE APPLICATION'S OWN CODE: the folder a way of the Experience
    debugger may never read.

    The user's decision: a way being measured may not read how the system
    that measures it works - how it judges, what the guard looks for, what
    the prompts say. Real work may (the user may have it work on this very
    checkout). So for the debugger's runs the code is out of reach of every
    read they make, the way the application's state is (see
    runtime/filesystem.check_not_app_code).

    Run from source it is the folder holding the package - the checkout,
    with its tests, its notes and its history beside the code, all of
    which say as much about the system as the code does. Installed with
    pip, that folder is site-packages, which the interpreter itself needs:
    then the package folder alone. In a packaged application, the bundle
    the executable runs from - which its own interpreter must read to run
    anything at all, so there the agents' file tools refuse it but code
    they run is not kept out of it (see `code_root_for_children`)."""
    bundle = getattr(sys, "_MEIPASS", None)
    if is_frozen() and bundle:
        return Path(bundle).resolve()
    package = Path(__file__).resolve().parents[1]
    holder = package.parent
    interpreter = {os.path.normcase(str(Path(p).resolve()))
                   for p in (sys.prefix, sys.base_prefix,
                             getattr(sys, "exec_prefix", ""))
                   if p}
    key = os.path.normcase(str(holder))
    if any(key == one or key.startswith(one.rstrip("\\/") + os.sep)
           for one in interpreter):
        return package
    return holder


def code_root_for_children() -> Optional[Path]:
    """The code root as code the agents run is kept out of it - by the
    guard inside the child and by the operating system around it. None in
    a packaged application: the child IS the bundled executable, and its
    interpreter reads its own archive from the bundle with the very `open`
    the guard watches. There the bundle is compiled bytecode, not source,
    and the agents' own file tools still refuse it."""
    if is_frozen():
        return None
    return app_code_root()


def default_root() -> Path:
    """The documented default when the launcher supplies nothing."""
    return Path.home() / APP_DIR_NAME


def legacy_conventional_dir() -> Path:
    """Where application state lived before section 110.1 consolidated it."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_SLUG


#: Beside the installed program: the data folder chosen when it was installed
#: (build/windows/installer.iss writes it).
INSTALLED_ROOT_FILE = "data_root.txt"


def installed_root() -> Optional[Path]:
    """The data folder the installer recorded beside the program - for an
    installed program only; one run from source is told with --root."""
    if not is_frozen():
        return None
    try:
        said = (Path(sys.executable).resolve().parent / INSTALLED_ROOT_FILE).read_text(
            encoding="utf-8-sig").strip()
    except OSError:
        return None
    return Path(said) if said else None


#: What a data folder may hold at its top level: the agent's own two folders,
#: and the files the system puts in any folder.
DATA_FOLDER_HOLDS = (WORKSPACE_DIR, INTERNAL_DIR)
SYSTEM_FILES = ("desktop.ini", "thumbs.db", ".ds_store")


def data_folder_problem(root: Path) -> str:
    """Why `root` cannot be a data folder - "" when it can: not there yet,
    empty, or holding only an earlier agent's data (workspace/, internal/).

    Everything in the data folder is the agent's, and is never listened to
    (common/file_noise.py): a folder with other things in it - Documents, a
    drive, a project - would hide the work kept there."""
    folder = Path(root)
    try:
        entries = list(folder.iterdir())
    except FileNotFoundError:
        return ""
    except NotADirectoryError:
        return f"{folder} is a file, not a folder."
    except OSError as exc:
        return f"{folder} cannot be read: {exc}"
    others = sorted(one.name for one in entries
                    if one.name.lower() not in DATA_FOLDER_HOLDS
                    and one.name.lower() not in SYSTEM_FILES)
    if not others:
        return ""
    shown = ", ".join(others[:3]) + (", ..." if len(others) > 3 else "")
    return (f"{folder} already holds other things ({shown}). The data folder must be "
            "empty, or hold only an earlier Personal Agent's data (workspace and "
            "internal): everything in it belongs to the agent and is never listened "
            "to, so work kept there would go unseen. Choose an empty folder.")


def holds_agent_data(root: Path) -> bool:
    """Whether `root` holds an earlier agent's data."""
    internal = Path(root) / INTERNAL_DIR
    return (internal / "database" / "agent.db").exists() or \
        (internal / "settings" / "settings.json").exists()


def resolve_root(root: Optional[Path] = None) -> Path:
    """Command line argument, then environment variable, then the data folder
    chosen when it was installed, then the default.

    Always fully resolved: the root is compared against stored paths when the
    directory is moved, and a short (8.3) or symlinked form would not match.
    """
    if root:
        chosen = Path(root)
    else:
        override = os.environ.get(ENV_ROOT)
        chosen = Path(override) if override else (installed_root() or default_root())
    expanded = chosen.expanduser()
    try:
        return expanded.resolve()
    except OSError:  # pragma: no cover - unresolvable path
        return expanded.absolute()


# Kept for callers that asked for the internal directory directly.
def default_data_dir() -> Path:
    return resolve_root() / INTERNAL_DIR


# ---------------------------------------------------------------------------
# Workspace layout (sections 110, 111)
# ---------------------------------------------------------------------------
AGENT_RUNTIME_DIR = ".agent_runtime"

# Section 32, as amended: the one directory agent controlled code may write to.
#
# The workspace also holds the user's own material - things they put there, and
# things they asked the agent to produce and then edited. An agent that may
# write anywhere in it may overwrite any of that, and "the agent changed a file
# I was working on" is not a failure the user can be expected to anticipate.
#
# One directory instead means everything the agent produced is in one place the
# user can read, keep, or delete wholesale, and nothing of theirs is reachable.
AGENT_OUTPUT_DIR = "agent"

# Visible, user facing task output.
WORKSPACE_USER_SUBDIRS = ("documents", "downloads", "exports", "generated")

# Section 32, as amended: the workspace is layered, not flat.
#
# The narrow write root already puts these out of reach, but leaving it at that
# makes the protection an accident of one constant rather than a rule. Named
# here, the rule is stated, enforced on its own, and survives any later change
# to where the agent writes. These hold the user's own material - what they put
# in the workspace, and what they asked the agent to produce and then edited -
# so the agent may read them and may never modify them.
WORKSPACE_READONLY_SUBDIRS = WORKSPACE_USER_SUBDIRS

# Inside the agent's own directory. The same shape as the workspace above, so
# that asking for "documents" means the same thing in both.
AGENT_OUTPUT_SUBDIRS = ("documents", "downloads", "exports", "generated")

# Hidden agent runtime area: generated scripts, task state, temporary files.
WORKSPACE_RUNTIME_SUBDIRS = ("scripts", "tasks", "temp")


@dataclass
class AppPaths:
    """Every filesystem location the application owns, under one root."""

    root: Path

    @staticmethod
    def create(root: Optional[Path] = None) -> "AppPaths":
        paths = AppPaths(resolve_root(root))
        paths.ensure()
        paths.migrate_legacy_state()
        return paths

    # -- the two domains ----------------------------------------------------
    @property
    def workspace_dir(self) -> Path:
        """User-authorized agent workspace: the only agent-writable domain."""
        return self.root / WORKSPACE_DIR

    @property
    def data_dir(self) -> Path:
        """Trusted application state: readable and writable by the app alone."""
        return self.root / INTERNAL_DIR

    # -- internal application state ----------------------------------------
    @property
    def settings_dir(self) -> Path:
        return self.data_dir / "settings"

    @property
    def database_dir(self) -> Path:
        return self.data_dir / "database"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def credentials_dir(self) -> Path:
        return self.data_dir / "credentials"

    @property
    def characters_dir(self) -> Path:
        """User installed character packages (trusted, application managed)."""
        return self.data_dir / "characters"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def state_sandbox_dir(self) -> Path:
        """The state heartbeat's test library: pseudo states in, tasks out.

        Laid out exactly like the real library, because the debugger runs
        the same code over it - the only difference between a debug round
        and a real one is which root it was handed.

        INTERNAL, not in the workspace. The workspace library is what
        retrieval searches and what the user reads, and pseudo states are
        neither memories nor readable history: a made-up state answering a
        question the user asked would be a fabrication, however clearly
        the folder it came from was named.
        """
        return self.data_dir / "state_sandbox"

    @property
    def settings_file(self) -> Path:
        return self.settings_dir / "settings.json"

    @property
    def database_file(self) -> Path:
        return self.database_dir / "agent.db"

    # -- packaged resources -------------------------------------------------
    @property
    def resources_dir(self) -> Path:
        return resource_root()

    @property
    def builtin_characters_dir(self) -> Path:
        return resource_path("resources", "characters")

    @property
    def web_dir(self) -> Path:
        return resource_path("api", "web")

    # -- protection ---------------------------------------------------------
    def protected_paths(self) -> List[Path]:
        """Off limits to agent-controlled code, though inside the root (110.1)."""
        return [self.data_dir]

    @property
    def guard_dir(self) -> Path:
        """Where code the agents run loads its guard from: a copy of
        runtime/guard.py, kept byte for byte (runtime/sandbox.ensure_guard).
        Not the copy in the code - the code is a folder a debugger run may
        not read, and every child must read the guard to start. INSIDE the
        protected state on purpose: no run may write there, by every rule
        that already keeps that state (a grant, a debugger folder, the
        guard and the operating system all refuse it), and it is the one
        folder of that state the children may read (runtime/isolation.py,
        `guard_homes`)."""
        return self.data_dir / "guard"

    def ensure(self) -> "AppPaths":
        for directory in (
            self.root, self.workspace_dir, self.data_dir, self.settings_dir,
            self.database_dir, self.logs_dir, self.credentials_dir,
            self.characters_dir, self.cache_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    # -- migration ----------------------------------------------------------
    def migrate_legacy_state(self) -> Optional[Path]:
        """Move pre-110.1 application state into the root, once.

        Only runs when the internal directory is still empty, so it can never
        overwrite state that already belongs to this root.
        """
        if any(self.settings_dir.iterdir()) or self.database_file.exists():
            return None
        # Only into the standard data folder: one the user chose is a new
        # agent of its own, and takes nothing from anywhere else.
        try:
            if self.root.resolve() != default_root().resolve():
                return None
        except OSError:
            return None
        for legacy in (legacy_conventional_dir(), *LEGACY_DIRS):
            try:
                if not legacy.is_dir() or legacy.resolve() == self.data_dir.resolve():
                    continue
                if not (legacy / "settings" / "settings.json").exists():
                    continue
            except OSError:
                continue
            moved = []
            for name in MIGRATED_SUBDIRS:
                source = legacy / name
                if not source.is_dir():
                    continue
                target = self.data_dir / name
                try:
                    for item in source.iterdir():
                        destination = target / item.name
                        if destination.exists():
                            continue
                        shutil.move(str(item), str(destination))
                    moved.append(name)
                except OSError:
                    log.warning("could not migrate %s from %s", name, legacy)
            if moved:
                log.info("migrated application state from %s into %s (%s)",
                         legacy, self.data_dir, ", ".join(moved))
                return legacy
        return None

    # -- relocation (section 110.1) -----------------------------------------
    def rewrite_path(self, value: str, previous_root: Path) -> str:
        """Repoint a stored absolute path from a previous root to this one.

        The root is meant to be moved by copying the directory, so nothing
        inside it may keep depending on the old absolute location.
        """
        previous = os.path.normcase(str(previous_root))
        candidate = os.path.normcase(str(value).strip())
        if not candidate:
            return value
        if candidate == previous:
            return str(self.root)
        if candidate.startswith(previous.rstrip("\\/") + os.sep) or \
                candidate.startswith(previous.rstrip("\\/") + "/"):
            relative = str(value).strip()[len(str(previous_root)):].lstrip("\\/")
            return str(self.root / relative)
        return value

    def describe(self) -> dict:
        return {
            "root": str(self.root),
            "workspace": str(self.workspace_dir),
            "internal_data": str(self.data_dir),
            "settings": str(self.settings_file),
            "database": str(self.database_file),
            "logs": str(self.logs_dir),
            "credentials": str(self.credentials_dir),
            "characters": str(self.characters_dir),
            "resources": str(self.resources_dir),
            "frozen": is_frozen(),
            "single_writable_root": True,
        }

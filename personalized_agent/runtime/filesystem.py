"""Application level path validation and safe filesystem operations.

Section 32 lists what validation has to survive: absolute paths, ``..``,
symlinks, junctions, relative path ambiguity, normalization, case
normalization, mount points and temporary directory redirection.

``resolve_securely`` therefore never trusts the textual form of a path: it
resolves the real path of the deepest existing ancestor (which collapses
symlinks and NTFS junctions), re-appends the remaining components, and then
compares normalized case forms.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Iterable, List, Optional, Union

from .permissions import SecurityViolation, WorkspacePolicy

PathLike = Union[str, os.PathLike]


def _normcase(path: Path) -> str:
    return os.path.normcase(str(path))


def real_of_existing_ancestor(path: Path) -> Path:
    """Resolve symlinks/junctions on the deepest part of *path* that exists."""
    candidate = path
    tail: list[str] = []
    while True:
        try:
            if candidate.exists() or candidate.is_symlink():
                break
        except OSError:
            pass
        parent = candidate.parent
        if parent == candidate:  # reached the filesystem root
            break
        tail.append(candidate.name)
        candidate = parent
    try:
        resolved_base = Path(os.path.realpath(str(candidate)))
    except OSError:
        resolved_base = candidate
    for name in reversed(tail):
        resolved_base = resolved_base / name
    return resolved_base


def resolve_securely(requested_path: PathLike, base: Path) -> Path:
    """Return an absolute, symlink free path for *requested_path*.

    Relative paths are anchored to *base* rather than the process working
    directory, removing the relative path ambiguity called out in section 32.
    """
    raw = Path(os.path.expandvars(str(requested_path))).expanduser()
    if not raw.is_absolute():
        raw = Path(base) / raw
    raw = Path(os.path.normpath(str(raw)))
    return real_of_existing_ancestor(raw)


def is_inside(path: Path, root: Path) -> bool:
    path_n = _normcase(path)
    root_n = _normcase(root)
    if path_n == root_n:
        return True
    return path_n.startswith(root_n.rstrip(os.sep) + os.sep)


def check_not_protected(resolved: Path, policy: WorkspacePolicy, operation: str) -> None:
    """Sections 108-110 - trusted application state is off limits either way.

    The internal data directory holds settings, the knowledge graph database,
    the scheduler, credentials and logs.  Agent-controlled code may neither read
    nor write it, so a workspace that happens to contain it does not open it up.
    """
    for protected in policy.protected_paths:
        if is_inside(resolved, real_of_existing_ancestor(protected)):
            raise SecurityViolation(
                f"{operation} inside the trusted application state directory is not permitted",
                requested_path=str(resolved),
                workspace_root=str(policy.workspace_root),
                boundary="application_state",
            )


#: Said of a sealed folder (see WorkspacePolicy.sealed_paths).
SEALED_MESSAGE = ("that is another benchmark variant's folder: this variant may "
                  "not read it")


def is_sealed(resolved: Path, policy: WorkspacePolicy) -> bool:
    """Inside a folder sealed from this agent - another way's, in the
    Experience debugger."""
    return any(is_inside(resolved, real_of_existing_ancestor(sealed))
               for sealed in getattr(policy, "sealed_paths", None) or [])


def check_not_sealed(resolved: Path, policy: WorkspacePolicy, operation: str) -> None:
    """A sealed folder is neither read nor written - nor anything in it."""
    if is_sealed(resolved, policy):
        raise SecurityViolation(
            SEALED_MESSAGE if operation == "reading"
            else f"{SEALED_MESSAGE} - nor write in it",
            requested_path=str(resolved),
            workspace_root=str(policy.workspace_root),
            boundary="sealed",
        )


#: Said of the application's own code (see WorkspacePolicy.code_root).
CODE_MESSAGE = ("that is this application's own code: the agents may not "
                "read it")


def is_app_code(resolved: Path, policy: WorkspacePolicy) -> bool:
    """Inside the application's own code, for an agent that may not read
    it - a way of the Experience debugger (WorkspacePolicy.code_root)."""
    root = getattr(policy, "code_root", None)
    if not root:
        return False
    return is_inside(resolved, real_of_existing_ancestor(Path(root)))


def check_not_app_code(resolved: Path, policy: WorkspacePolicy) -> None:
    """The user's decision: a way of the Experience debugger never reads
    how the system that measures it works. Reading alone - what may be
    written is left to the write rules (and a debugger project's folder is
    never in the code: debug_projects.folder_problem)."""
    if is_app_code(resolved, policy):
        raise SecurityViolation(
            CODE_MESSAGE,
            requested_path=str(resolved),
            workspace_root=str(policy.workspace_root),
            boundary="application_code",
        )


#: Said of the record of what happened (see WorkspacePolicy.closed_paths).
LOGS_MESSAGE = ("that is the project's run logs - the record of what happened: "
                "the agents may not read it")


def is_closed(resolved: Path, policy: WorkspacePolicy) -> bool:
    """Inside a folder closed to this agent: a project's run logs."""
    return any(is_inside(resolved, real_of_existing_ancestor(Path(closed)))
               for closed in getattr(policy, "closed_paths", None) or [])


#: Said of where the user's sign-ins are kept (see `is_credential_store`).
CREDENTIALS_MESSAGE = ("that is where the user's sign-ins are kept - browser profiles "
                       "and cookies, keys, credentials: the agents may not read it")

#: The credential stores, looked up again at most this often: find_files asks
#: once per file it looks at, and listing the home each time would be the
#: whole cost of the search.
_STORES_FOR_SECONDS = 30.0
_stores_cache: dict = {"at": 0.0, "home": "", "stores": []}


def _credential_stores() -> list:
    import time
    from .isolation import credential_stores
    home = str(Path.home())
    now = time.monotonic()
    if _stores_cache["home"] != home or now - _stores_cache["at"] > _STORES_FOR_SECONDS:
        _stores_cache.update(at=now, home=home, stores=[
            real_of_existing_ancestor(Path(one)) for one in credential_stores()])
    return list(_stores_cache["stores"])


def is_credential_store(resolved: Path, policy: WorkspacePolicy) -> bool:
    """INSIDE WHERE THE USER'S SIGN-INS ARE KEPT - and not in a folder named
    for this agent there. The readable root is the whole home, and the home
    holds AppData (browser profiles, their cookies - some in plain text -
    DPAPI keys) and its dot-folders (.ssh, .aws, .git-credentials ...): the
    operating system already keeps the agents' code out of them (the read
    grant excludes them: isolation.credential_stores), but the agents' own
    tools run in this process and read with the user's authority. What is
    named exactly stays open, as with the grant: the agent's own folder,
    the workspace, a granted folder, or a readable root that lies inside one
    of them (a work folder under the temp directory in AppData, say)."""
    stores = _credential_stores()
    if not any(is_inside(resolved, store) for store in stores):
        return False
    named = [policy.write_root, policy.workspace_root,
             *(getattr(policy, "extra_writable", None) or []),
             *[root for root in policy.readable_paths
               if any(is_inside(real_of_existing_ancestor(Path(root)), store)
                      for store in stores)]]
    return not any(is_inside(resolved, real_of_existing_ancestor(Path(one)))
                   for one in named if one)


def check_not_credential_store(resolved: Path, policy: WorkspacePolicy) -> None:
    if is_credential_store(resolved, policy):
        raise SecurityViolation(
            CREDENTIALS_MESSAGE,
            requested_path=str(resolved),
            workspace_root=str(policy.workspace_root),
            boundary="credential_store",
        )


def check_not_closed(resolved: Path, policy: WorkspacePolicy) -> None:
    """The run logs are not read by the agents - nor listed. Writing them
    is the application's: its tools are refused by their locked list."""
    if is_closed(resolved, policy):
        raise SecurityViolation(
            LOGS_MESSAGE,
            requested_path=str(resolved),
            workspace_root=str(policy.workspace_root),
            boundary="run_logs",
        )


def validate_write_path(requested_path: PathLike, policy: WorkspacePolicy) -> Path:
    """Section 32, as amended - a write stays inside the agent's own directory.

    Reads are scoped to the whole workspace and writes to one subdirectory of
    it. The workspace holds the user's own material too, and an agent that may
    write anywhere in it may overwrite any of that.

    A relative path is resolved against the writable directory rather than the
    workspace, so that "documents/notes.md" means the agent's documents and
    lands where it is allowed rather than being refused for being one level up.

    The read-only layers are checked first and refused by name. Both checks
    would refuse the same write, but only one of them can say why it matters:
    "that is the user's own material" is a different fact from "that is not
    your directory", and it is the one worth reporting.
    """
    write_root = real_of_existing_ancestor(policy.write_root)
    resolved = resolve_securely(requested_path, write_root)
    check_not_sealed(resolved, policy, "writing")
    # A folder the user granted this work: written as freely as its own -
    # the user chose it, which outranks the read-only layers - but never
    # the application's protected state.
    for granted in getattr(policy, "extra_writable", None) or []:
        if is_inside(resolved, real_of_existing_ancestor(granted)):
            check_not_protected(resolved, policy, "writing")
            return resolved
    for layer in policy.readonly_paths:
        if is_inside(resolved, real_of_existing_ancestor(layer)):
            raise SecurityViolation(
                f"{layer.name!r} holds the user's own material and is read only "
                "to the agent",
                requested_path=str(requested_path),
                workspace_root=str(policy.workspace_root),
                boundary="readonly",
            )
    if not is_inside(resolved, write_root):
        raise SecurityViolation(
            "write outside the agent's own workspace directory is not permitted",
            requested_path=str(requested_path),
            workspace_root=str(write_root),
        )
    check_not_protected(resolved, policy, "writing")
    return resolved


def validate_read_path(requested_path: PathLike, policy: WorkspacePolicy) -> Path:
    """Reads are broader than writes but still explicit (section 33).

    A relative path means the same file here as it does in a write. Writes
    resolve against the agent's own directory, so a read that resolved against
    the workspace instead would fail to find the file it had just written, and
    "documents/notes.md" would name two different files depending on which way
    it was travelling. The agent's own directory is therefore tried first, and
    the wider workspace after it - so the agent can still read the user's
    material by the same name when it has nothing of its own by that name.
    """
    resolved = resolve_securely(requested_path, policy.write_root)
    if not resolved.exists():
        in_workspace = resolve_securely(requested_path, policy.workspace_root)
        if in_workspace.exists():
            resolved = in_workspace
    check_not_protected(resolved, policy, "reading")
    check_not_sealed(resolved, policy, "reading")
    check_not_app_code(resolved, policy)
    check_not_closed(resolved, policy)
    check_not_credential_store(resolved, policy)
    # And what it may write, wherever that is: a project the user keeps
    # outside the workspace (a folder on the desktop) is its runs' own
    # folder, and the package installer - given nothing else to read - was
    # refused the very script it was about to run there.
    allowed_roots: Iterable[Path] = [policy.workspace_root, *policy.readable_paths,
                                     policy.write_root,
                                     *(getattr(policy, "extra_writable", None) or [])]
    for root in allowed_roots:
        if is_inside(resolved, real_of_existing_ancestor(root)):
            return resolved
    raise SecurityViolation(
        "read outside the authorized readable paths is not permitted",
        requested_path=str(requested_path),
        workspace_root=str(policy.workspace_root),
    )


# ---------------------------------------------------------------------------
# Guarded operations
# ---------------------------------------------------------------------------
def safe_write_text(requested_path: PathLike, content: str, policy: WorkspacePolicy,
                    append: bool = False) -> Path:
    target = validate_write_path(requested_path, policy)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with open(target, mode, encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    return target


def safe_read_text(requested_path: PathLike, policy: WorkspacePolicy,
                   max_chars: int = 200_000) -> str:
    target = validate_read_path(requested_path, policy)
    if not target.exists():
        raise FileNotFoundError(str(target))
    if target.is_dir():
        raise IsADirectoryError(str(target))
    data = target.read_text(encoding="utf-8", errors="replace")
    if len(data) > max_chars:
        return data[:max_chars] + f"\n... [truncated, {len(data) - max_chars} characters omitted]"
    return data


def safe_mkdir(requested_path: PathLike, policy: WorkspacePolicy) -> Path:
    target = validate_write_path(requested_path, policy)
    target.mkdir(parents=True, exist_ok=True)
    return target


def safe_delete(requested_path: PathLike, policy: WorkspacePolicy) -> Path:
    target = validate_write_path(requested_path, policy)
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    return target


def safe_move(source: PathLike, destination: PathLike, policy: WorkspacePolicy) -> Path:
    src = validate_write_path(source, policy)
    dst = validate_write_path(destination, policy)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return dst


def safe_extract_archive(archive_path: PathLike, destination: PathLike,
                         policy: WorkspacePolicy) -> Path:
    """Archive extraction with per member validation (section 31)."""
    import tarfile
    import zipfile

    archive = validate_read_path(archive_path, policy)
    target_dir = validate_write_path(destination, policy)
    target_dir.mkdir(parents=True, exist_ok=True)

    def check_member(name: str) -> None:
        candidate = target_dir / name
        validate_write_path(candidate, policy)

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                check_member(member)
            zf.extractall(target_dir)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                if member.issym() or member.islnk():
                    raise SecurityViolation(
                        "archive contains a link member", str(member.name), str(policy.workspace_root)
                    )
                check_member(member.name)
            tf.extractall(target_dir)
    else:
        raise ValueError("unsupported archive format")
    return target_dir


def workspace_tree(policy: WorkspacePolicy, max_entries: int = 500) -> list:
    """Listing used by the agent tools and the Overview panel."""
    entries = []
    root = policy.workspace_root
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        entries.append(
            {
                "path": str(rel).replace(os.sep, "/"),
                "type": "directory" if path.is_dir() else "file",
                "size": path.stat().st_size if path.is_file() else None,
            }
        )
        if len(entries) >= max_entries:
            break
    return entries


# -- a client's own reading tools, held to the same rules ------------------------------------
#: Past this many things in one folder beside a readable root - or this
#: many places in all - the places cannot be listed to deny them (a work
#: folder in the temp directory sits beside hundreds of thousands): the
#: client's own reading tools are then not offered at all.
DENY_PER_FOLDER = 400
DENY_IN_ALL = 1500


class _TooManyToDeny(Exception):
    pass


def unreadable_for(policy: WorkspacePolicy) -> Optional[List[str]]:
    """EVERYTHING THE AGENTS MAY NOT READ, as places to deny a client's own
    reading tools (Claude Code's Read, Glob and Grep), which deny what they
    are told and read everything else. So: every folder and file outside
    the readable roots, on every drive; and inside them, the protected
    state, the sealed folders, the application's code where it is shut, the
    closed run logs and the user's sign-in stores. A place that holds a
    readable root is opened down to that root and no further - the rest
    beside it denied. Absolute paths, folders and files. None when they
    are too many to list (`DENY_PER_FOLDER`, `DENY_IN_ALL`): then the
    client's own reading tools are not offered - never offered unbounded."""
    import string
    roots = [real_of_existing_ancestor(Path(one)) for one in
             [policy.workspace_root, *policy.readable_paths,
              *(getattr(policy, "extra_writable", None) or []), policy.write_root]
             if one]
    denied: List[Path] = []

    def holds_a_root(place: Path) -> bool:
        return any(is_inside(root, place) for root in roots)

    visited: set = set()

    def deny_beside(place: Path) -> None:
        """`place` holds a root: deny each thing in it that neither is in a
        root nor holds one; go down into those that hold one. A link that
        leads out of `place` (Windows keeps junctions that point back at
        their own parent) is denied by its own name, never followed."""
        if str(place) in visited:
            return
        visited.add(str(place))
        children = []
        try:
            with os.scandir(place) as entries:
                for entry in entries:
                    children.append(Path(entry.path))
                    if len(children) > DENY_PER_FOLDER:
                        raise _TooManyToDeny(str(place))
        except OSError:
            return
        for child in children:
            if len(denied) > DENY_IN_ALL:
                raise _TooManyToDeny("in all")
            real = real_of_existing_ancestor(child)
            if not is_inside(real, place) or real == place:
                if not any(is_inside(child, root) for root in roots):
                    denied.append(child)
                continue
            if any(is_inside(real, root) for root in roots):
                continue
            if holds_a_root(real):
                deny_beside(real)
            else:
                denied.append(real)

    if os.name == "nt":
        drives = [Path(f"{letter}:/") for letter in string.ascii_uppercase
                  if os.path.exists(f"{letter}:/")]
    else:
        drives = [Path("/")]
    try:
        _deny_all(drives, holds_a_root, deny_beside, denied, policy, roots)
    except _TooManyToDeny:
        return None
    out: List[str] = []
    for one in denied:
        text = str(one)
        if text not in out:
            out.append(text)
    return out


def _deny_all(drives: List[Path], holds_a_root: Any, deny_beside: Any, denied: List[Path],
              policy: WorkspacePolicy, roots: List[Path]) -> None:
    """Every drive, then the closed places inside the readable roots."""
    for drive in drives:
        if holds_a_root(drive):
            deny_beside(drive)
        else:
            denied.append(drive)
    inside = [*(policy.protected_paths or []),
              *(getattr(policy, "sealed_paths", None) or []),
              *([policy.code_root] if getattr(policy, "code_root", None) else []),
              *(getattr(policy, "closed_paths", None) or [])]
    for one in inside:
        real = real_of_existing_ancestor(Path(one))
        if holds_a_root(real) and not any(is_inside(root, real) and root == real
                                          for root in roots):
            deny_beside(real)
        else:
            denied.append(real)
    for store in _credential_stores():
        if holds_a_root(store):
            deny_beside(store)
        else:
            denied.append(store)

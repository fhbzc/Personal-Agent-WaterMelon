"""INSTALLED ENVIRONMENTS IN A WORK'S FOLDER, LEFT OUT OF EVERY WALK (the
user, 2026-10-29).

A Python environment the work made in its own folder - a virtual
environment to try a package's install from scratch, a conda one - is tens
of thousands of files that are not the work: a replication's held seven
virtual environments, 46,956 files and 1.85 GB beside some 600 files of the
work, and every walk of the folder - what code changed, each progress
point, each version, a full review's files since the last checkpoint, a
rollback - went through all of them.

So each of those walks leaves them out, told BY WHAT THEY ARE, never by a
name a work might use for its own (an "env" folder of settings, a "build"
of the deliverable): a folder with a virtual environment's `pyvenv.cfg` or
conda's `conda-meta` in it, or one named `site-packages` or
`dist-packages`. Left out is not forgotten: a version keeps the list of
the packages each held, a rollback leaves them as they are and says so,
and the reviewers are told each is there, and how big.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set

#: Folders installed packages live in, whatever holds them.
ENV_NAMES = frozenset({"site-packages", "dist-packages"})
#: What marks the top of an environment.
ENV_MARKERS = ("pyvenv.cfg", "conda-meta")


def is_environment(path: Path, name: str = "") -> bool:
    """Whether a folder is an installed environment, or the packages of one
    - by what it holds, or the one name installed packages are kept under."""
    name = (name or Path(path).name).lower()
    if name in ENV_NAMES:
        return True
    try:
        return any(os.path.exists(os.path.join(path, marker)) for marker in ENV_MARKERS)
    except OSError:
        return False


def roots_in(relatives: Iterable[str]) -> Set[str]:
    """The environments a list of a folder's files shows ("a/b/pyvenv.cfg",
    "x/conda-meta/history" -> "a/b", "x"), by their paths inside it."""
    roots: Set[str] = set()
    for relative in relatives:
        parts = str(relative).replace("\\", "/").split("/")
        if parts[-1] == "pyvenv.cfg" and len(parts) > 1:
            roots.add("/".join(parts[:-1]))
        if "conda-meta" in parts[:-1]:
            at = parts.index("conda-meta")
            if at:
                roots.add("/".join(parts[:at]))
    return roots


def inside(relative: str, roots: Iterable[str] = ()) -> bool:
    """Whether a file, by its path inside the work's folder, is in an
    environment: under a folder of installed packages, or one of `roots`."""
    relative = str(relative).replace("\\", "/")
    parts = relative.split("/")
    if any(part.lower() in ENV_NAMES for part in parts[:-1]):
        return True
    return any(relative == root or relative.startswith(root + "/") for root in roots)


class Seen:
    """Whether a path, anywhere under some folders, is in an environment -
    each folder looked at once (for paths that come one at a time: what a
    watch of the folder heard)."""

    def __init__(self) -> None:
        self._known: Dict[str, bool] = {}

    def __call__(self, path: str, roots: Sequence[Path]) -> bool:
        whole = os.path.normcase(os.path.abspath(path))
        tops = [os.path.normcase(os.path.abspath(one)) for one in roots]
        top = next((one for one in tops if whole.startswith(one.rstrip(os.sep) + os.sep)), None)
        if top is None:
            return False
        here = os.path.dirname(whole)
        while len(here) > len(top):
            known = self._known.get(here)
            if known is None:
                known = self._known[here] = is_environment(Path(here))
            if known:
                return True
            here = os.path.dirname(here)
        return False


def packages_of(env: Path) -> List[str]:
    """What an environment holds: NAME-VERSION of each package installed,
    from the folders pip leaves (`*.dist-info`, `*.egg-info`) - one listing
    of its packages' folder, not a walk."""
    places = [env] if env.name.lower() in ENV_NAMES else [
        *[env / lib / "site-packages" for lib in ("Lib", "lib")],
        *[one / "site-packages" for one in (env / "lib").glob("python*")]]
    found: Set[str] = set()
    for place in places:
        try:
            with os.scandir(place) as listing:
                for entry in listing:
                    if entry.name.endswith((".dist-info", ".egg-info")):
                        found.add(entry.name.rsplit(".", 1)[0])
        except OSError:
            continue
    return sorted(found)


def found_in(folder: Path, skip: Sequence[str] = (), most: int = 50) -> List[Dict[str, Any]]:
    """The environments in a work's folder - each by its path inside it and
    the packages it holds; never walked into. `skip`: folders by name not
    looked in at all."""
    folder = Path(folder)
    out: List[Dict[str, Any]] = []
    for here, dirnames, _files in os.walk(folder):
        keep = []
        for name in sorted(dirnames):
            if name in skip:
                continue
            path = Path(here) / name
            if is_environment(path, name):
                packages = packages_of(path)
                out.append({"path": path.relative_to(folder).as_posix(),
                            "packages": packages[:400], "count": len(packages)})
                if len(out) >= most:
                    return out
                continue
            keep.append(name)
        dirnames[:] = keep
    return out


def said(found: Sequence[Dict[str, Any]]) -> str:
    """For the reviewers: what was left out, and where - for them to judge
    whether it belongs in what the work delivers."""
    if not found:
        return ""
    lines = [f"  {one['path']} ({one.get('count', 0)} package"
             f"{'' if one.get('count') == 1 else 's'} installed)" for one in found[:20]]
    return ("INSTALLED ENVIRONMENTS IN THE WORK'S FOLDER - left out of every file list here, "
            "not read: judge whether each belongs in what the work delivers (an environment "
            "to try an install belongs in the run's scratch, never in the deliverable):\n"
            + "\n".join(lines))


__all__ = ["ENV_NAMES", "ENV_MARKERS", "is_environment", "roots_in", "inside", "Seen",
           "packages_of", "found_in", "said"]

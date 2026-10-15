#!/usr/bin/env python3
"""Make the environment release builds are packaged from.

    python build/make_build_env.py                # make it, or add what is missing
    python build/make_build_env.py --recreate     # start again from nothing
    python build/make_build_env.py --path DIR     # somewhere else
    python build/make_build_env.py --from PYTHON  # pin to another development Python

PyInstaller packages whatever the Python running it has installed. A
developer's own Python tends to have more than the application uses - a CUDA
build of torch, TensorFlow - and all of it ends up in the installer: a CUDA
torch alone adds about two gigabytes, though the embedding model runs on the
processor. So releases are built from an environment of their own, holding
the application's requirements, PyInstaller and a CPU-only torch, and
nothing else.

What it holds is the same as what the application is developed against:
every package is pinned to the version installed in the development Python
(read with ``pip freeze``, never changed), so the release runs the code that
was tested. torch is the one exception, and only in its build: the same
version, as the CPU-only wheel.

It lives outside the repository, so it is never committed, never synced and
never seen by the tests:

    Windows  %LOCALAPPDATA%\\PersonalizedAgent-build\\venv
    macOS    ~/Library/Caches/PersonalizedAgent-build/venv
    Linux    $XDG_CACHE_HOME/PersonalizedAgent-build/venv  (~/.cache by default)

Nothing is installed into the Python that runs this script, or into the
development Python. It prints the command that builds with the new
environment when it is done. Standard library only, so it runs before
anything else is installed.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = REPO_ROOT / "requirements.txt"

# The torch wheel the packaged application carries is the CPU-only one: on
# Windows and Linux the default wheel is a CUDA build. Its version is the
# development Python's; this one is used only when that has no torch at all.
# macOS wheels have no CUDA, so there it comes from the ordinary index.
FALLBACK_TORCH_VERSION = "2.5.1"
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"

# Lines of requirements.txt that are for developing, not for running.
SKIPPED_REQUIREMENTS = {"pytest"}

# Read by the application when they are present, though requirements.txt
# leaves them out: documents (PDF pages as pictures, workbooks) and building
# the release itself.
EXTRA_PACKAGES = ["openpyxl", "pypdfium2", "pyinstaller"]

# Never pinned from the development Python. torch is pinned separately, to
# its version without the CUDA tag; the rest are the GPU and the other
# machine-learning frameworks the application does not use, which the build
# environment must not have at any version.
UNPINNED = re.compile(
    r"^(torch|torchvision|torchaudio|nvidia-.*|tensorflow.*|keras|tf-keras|"
    r"tensorboard.*|jax.*|flax)$"
)


def normalise(name: str) -> str:
    """A distribution's name as pip compares it (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def default_location() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "PersonalizedAgent-build" / "venv"


def development_python() -> Path:
    """The Python running this script - or, when that is itself a virtual
    environment (the build environment, say), the Python it was made from."""
    if sys.prefix == sys.base_prefix:
        return Path(sys.executable)
    base = Path(sys.base_prefix)
    for candidate in (base / "python.exe", base / "bin" / "python3", base / "bin" / "python"):
        if candidate.exists():
            return candidate
    raise SystemExit(f"cannot find the Python this environment was made from in {base}; "
                     "name it with --from")


def venv_python(venv: Path) -> Path:
    if sys.platform.startswith("win"):
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def run(command: list) -> None:
    print("$ " + " ".join(str(part) for part in command), flush=True)
    completed = subprocess.run([str(part) for part in command])
    if completed.returncode != 0:
        raise SystemExit(f"failed with exit code {completed.returncode}")


def frozen_versions(python: Path) -> Dict[str, str]:
    """{normalised name: version} of what a Python has installed, read with
    ``pip freeze --all`` (so pip itself is included). Editable and local
    entries have no version to pin and are left out."""
    done = subprocess.run([str(python), "-m", "pip", "freeze", "--all",
                           "--disable-pip-version-check"],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"cannot read the packages of {python}:\n{done.stderr[-800:]}")
    versions: Dict[str, str] = {}
    for line in done.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-e", "--")) or " @ " in line or "==" not in line:
            continue
        name, version = line.split("==", 1)
        versions[normalise(name)] = version.strip()
    return versions


def requirement_name(line: str) -> str:
    name = line
    for stop in "<>=!~;[ ":
        name = name.split(stop, 1)[0]
    return normalise(name)


def runtime_requirements() -> List[str]:
    """requirements.txt, less what is skipped. Markers stay for pip."""
    kept = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and requirement_name(line) not in SKIPPED_REQUIREMENTS:
            kept.append(line)
    return kept


def torch_version(development: Dict[str, str]) -> str:
    """The development torch's version without its build tag (+cu121)."""
    found = development.get("torch")
    if not found:
        print(f"note: the development Python has no torch; using {FALLBACK_TORCH_VERSION}")
        return FALLBACK_TORCH_VERSION
    return found.split("+", 1)[0]


def constraints(development: Dict[str, str], torch: str) -> List[str]:
    pins = [f"{name}=={version}" for name, version in sorted(development.items())
            if not UNPINNED.match(name)]
    return pins + [f"torch=={torch}"]


def note_unpinned(development: Dict[str, str], wanted: List[str]) -> None:
    for name in wanted:
        if name not in development and not UNPINNED.match(name):
            print(f"note: {name} is not installed in the development Python, so the build "
                  f"environment takes its newest version")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--path", type=Path, default=None,
                        help=f"where to make it (default: {default_location()})")
    parser.add_argument("--from", dest="source", type=Path, default=None,
                        help="the development Python whose versions are pinned (default: "
                             "the one running this, or the one it was made from)")
    parser.add_argument("--recreate", action="store_true",
                        help="delete an existing environment first")
    args = parser.parse_args(argv)

    venv = (args.path or default_location()).expanduser().resolve()
    if REPO_ROOT in venv.parents or venv == REPO_ROOT:
        raise SystemExit(f"the build environment must be outside the repository: {venv}")
    source = (args.source or development_python()).expanduser()
    if source.resolve() == venv_python(venv).resolve():
        raise SystemExit("--from is the build environment itself; name the development Python")
    development = frozen_versions(source)
    print(f"pinning to the {len(development)} packages of {source}")
    torch = torch_version(development)
    requirements = runtime_requirements() + EXTRA_PACKAGES
    note_unpinned(development, [requirement_name(line) for line in requirements])

    if args.recreate and venv.exists():
        print(f"removing {venv}")
        shutil.rmtree(venv)
    python = venv_python(venv)
    if not python.exists():
        # Made from the development Python, so it is the same Python too.
        # Making an environment from it installs nothing into it.
        venv.parent.mkdir(parents=True, exist_ok=True)
        run([source, "-m", "venv", venv])

    with tempfile.TemporaryDirectory() as scratch:
        constraint_file = Path(scratch) / "constraints.txt"
        constraint_file.write_text("\n".join(constraints(development, torch)) + "\n",
                                   encoding="utf-8")
        requirement_file = Path(scratch) / "requirements.txt"
        requirement_file.write_text("\n".join(requirements) + "\n", encoding="utf-8")
        pip = [python, "-m", "pip", "install", "--disable-pip-version-check"]

        # pip first, at the development version: the application imports it.
        run([*pip, "pip", "-c", constraint_file])

        # torch alone, from the CPU index and without its dependencies, which
        # the CPU index does not carry at every version. They come with the
        # requirements below, from the ordinary index and at the pinned
        # versions; torch itself is already there and satisfies its pin
        # (2.5.1+cpu is a 2.5.1).
        torch_install = [*pip, f"torch=={torch}", "--no-deps"]
        if sys.platform != "darwin":
            torch_install += ["--index-url", TORCH_CPU_INDEX]
        run(torch_install)

        run([*pip, "-r", requirement_file, "-c", constraint_file])
    run([python, "-m", "pip", "check", "--disable-pip-version-check"])

    built = frozen_versions(python)
    differ = [(name, development.get(name), version) for name, version in sorted(built.items())
              if name in development and development[name] != version]
    if differ:
        print("\nversions that differ from the development Python:")
        for name, wanted, got in differ:
            print(f"  {name}: development {wanted}, build {got}")
    only_here = sorted(set(built) - set(development))
    if only_here:
        print("not in the development Python: " + ", ".join(only_here))

    run([python, "-c",
         "import torch, transformers, PyInstaller; "
         "print('torch', torch.__version__, '| CUDA', torch.version.cuda, "
         "'| transformers', transformers.__version__, '| PyInstaller', PyInstaller.__version__)"])
    builder = {"win32": "build/windows/build_windows.py",
               "darwin": "build/macos/build_macos.py"}.get(sys.platform,
                                                          "build/linux/build_linux.py")
    print(f"\nbuild environment ready: {venv}")
    print(f"build with:\n  {python} {builder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Shared build helpers (sections 100-106).

Every platform build goes through here so the bundled runtime, the packaged
resources, the excluded platform modules and the version metadata stay
identical across Windows, macOS and Linux.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from personalized_agent.version import (  # noqa: E402
    APP_DIR_NAME,
    APP_ID,
    APP_NAME,
    APP_SLUG,
    FILE_NAME,
    FORMER_PACKAGE_NAMES,
    PACKAGE_NAME,
    PUBLISHER,
    __version__,
)

ENTRY_SCRIPT = REPO_ROOT / "run.py"
DIST_ENV = "PERSONALIZED_AGENT_DIST"
BUILD_WORK_ROOT = REPO_ROOT / "build" / ".work"

# Cloud sync clients hold transient locks on freshly written files, which breaks
# a packaging step that writes thousands of small files.  We detect them so the
# build can stage its output elsewhere and explain itself.
CLOUD_SYNC_MARKERS = {
    "dropbox": "Dropbox",
    "onedrive": "OneDrive",
    "google drive": "Google Drive",
    "googledrive": "Google Drive",
    "icloud drive": "iCloud Drive",
    "com~apple~clouddocs": "iCloud Drive",
}

APP_PROCESS_NAMES = {"personalizedagent.exe", "personalizedagent", "personalized agent"}

# Section 106: ship only the platform implementations a build actually needs.
PLATFORM_MODULES = {
    "windows": "personalized_agent.platform_layer.windows",
    "macos": "personalized_agent.platform_layer.macos",
    "linux": "personalized_agent.platform_layer.linux",
}

# Packaged read-only resources (section 112).  Destinations are relative to the
# resource root, which is the package directory in development and the bundle
# directory when frozen - see personalized_agent.common.paths.resource_root.
DATA_FILES = [
    ("personalized_agent/api/web", "api/web"),
    ("personalized_agent/resources", "resources"),
    # The testing browser's helper, copied out for runs to import
    # (runtime/browser.py, ensure_helper): a file, not compiled code.
    ("personalized_agent/runtime/agent_browser.py", "runtime"),
]

# Imported through the platform factory and the provider registry, so they are
# not visible to static analysis.
BASE_HIDDEN_IMPORTS = [
    "personalized_agent.models.providers.anthropic_provider",
    "personalized_agent.models.providers.offline_provider",
    "personalized_agent.listener.integrations.ingest",
    "personalized_agent.listener.providers.application",
    "personalized_agent.listener.providers.browser",
    "personalized_agent.listener.providers.filesystem",
    "personalized_agent.runtime.guard",
    # The certificates a packaged build trusts on macOS and Linux
    # (__main__._certificates_carried).
    "certifi",
]


class BuildError(RuntimeError):
    """Build failures are explicit (section 113)."""


def current_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def require_native_platform(target: str) -> None:
    """Section 103 - release artifacts are produced on their own OS."""
    if target != current_platform():
        raise BuildError(
            f"cannot build the {target} artifact on {current_platform()}: platform binaries, "
            "signing and packaging require a native runner. Use the matching CI runner."
        )


def ensure_pyinstaller() -> None:
    import importlib.util

    if importlib.util.find_spec("PyInstaller") is None:
        raise BuildError(
            "PyInstaller is required to produce release artifacts: pip install pyinstaller"
        )


def dist_root() -> Path:
    """Where finished artifacts are published.

    Overridable with ``--dist`` or ``PERSONALIZED_AGENT_DIST`` so a repository
    that lives inside a synced folder can publish somewhere the sync client is
    not watching.
    """
    override = os.environ.get(DIST_ENV)
    return Path(override).expanduser() if override else REPO_ROOT / "dist"


# Kept for callers that imported the constant directly.
DIST_ROOT = dist_root()


def dist_dir(target: str) -> Path:
    path = dist_root() / target
    path.mkdir(parents=True, exist_ok=True)
    return path


def work_dir(target: str) -> Path:
    path = BUILD_WORK_ROOT / target
    path.mkdir(parents=True, exist_ok=True)
    return path


def staging_dir(target: str) -> Path:
    """Scratch output directory for the packaging step.

    PyInstaller writes thousands of files here before anything is published, so
    it always lives outside the repository - and therefore outside any cloud
    sync folder - regardless of where the finished artifact ends up.
    """
    path = Path(tempfile.gettempdir()) / f"personalized-agent-build-{target}"
    remove_tree(path, description="previous staging directory")
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Robust filesystem handling (Windows sync clients and antivirus hold locks)
# ---------------------------------------------------------------------------
def detect_cloud_sync(path: Path) -> Optional[str]:
    parts = [part.lower() for part in Path(path).resolve().parts]
    for marker, name in CLOUD_SYNC_MARKERS.items():
        if any(marker in part for part in parts):
            return name
    return None


def running_agent_processes() -> List[Tuple[int, str, str]]:
    """Any installed/built agent still running holds its own files open."""
    try:
        import psutil
    except ImportError:
        return []
    found: List[Tuple[int, str, str]] = []
    for process in psutil.process_iter(["pid", "name", "exe"]):
        try:
            name = (process.info.get("name") or "").lower()
            executable = process.info.get("exe") or ""
            if name in APP_PROCESS_NAMES:
                found.append((process.info["pid"], process.info["name"], executable))
        except Exception:
            continue
    return found


def terminate_agent_processes() -> List[int]:
    try:
        import psutil
    except ImportError:
        return []
    killed = []
    for pid, _name, _exe in running_agent_processes():
        try:
            process = psutil.Process(pid)
            process.terminate()
            process.wait(timeout=10)
            killed.append(pid)
        except Exception:
            try:
                psutil.Process(pid).kill()
                killed.append(pid)
            except Exception:
                continue
    return killed


def _force_writable(func, path, _exc_info):
    """rmtree error handler: clear the read-only bit, then retry once."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        func(path)
    except Exception:
        raise


def remove_tree(path: Path, attempts: int = 8, description: str = "directory") -> None:
    """Delete a tree, tolerating the transient locks a sync client takes.

    Raises :class:`BuildError` with an actionable message when the lock does not
    clear - build failures must be explicit (section 113).
    """
    path = Path(path)
    if not path.exists():
        return
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            shutil.rmtree(path, onexc=_force_writable)
            return
        except (PermissionError, OSError) as exc:
            last_error = exc
            if attempt == 0:
                print(f"note: {description} is locked, retrying: {path}")
            time.sleep(0.5 * (attempt + 1))
    raise BuildError(_lock_message(path, last_error, description))


def _lock_message(path: Path, error: Optional[Exception], description: str) -> str:
    lines = [f"cannot clear the {description}: {path}"]
    if error is not None:
        lines.append(f"  reason: {type(error).__name__}: {error}")
    processes = running_agent_processes()
    if processes:
        lines.append("  a built application is still running and holds its own files:")
        for pid, name, executable in processes:
            lines.append(f"    pid {pid}  {name}  {executable}")
        lines.append("  close it, or re-run the build with --kill-running.")
    provider = detect_cloud_sync(path)
    if provider:
        lines.append(
            f"  this path is inside a {provider} folder; the sync client locks files while it "
            "uploads them."
        )
        lines.append(
            f"  publish elsewhere with:  python build.py --dist <path outside {provider}>"
        )
        lines.append(f"  or set {DIST_ENV}, or pause syncing for this folder.")
    if not processes and not provider:
        lines.append("  another process (antivirus, indexer, an open shell) is holding a file.")
    return "\n".join(lines)


def publish_tree(source: Path, destination: Path) -> Path:
    """Move a finished artifact from staging to its published location."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    remove_tree(destination, description="previous build output")
    try:
        shutil.move(str(source), str(destination))
    except (PermissionError, OSError) as exc:
        raise BuildError(_lock_message(destination, exc, "publish target")) from exc
    return destination


def publish_file(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(6):
        try:
            if destination.exists():
                destination.unlink()
            shutil.move(str(source), str(destination))
            return destination
        except (PermissionError, OSError) as exc:
            if attempt == 5:
                raise BuildError(_lock_message(destination, exc, "publish target")) from exc
            time.sleep(0.5 * (attempt + 1))
    return destination


def data_arguments(separator: str) -> List[str]:
    args: List[str] = []
    for source, destination in DATA_FILES:
        absolute = REPO_ROOT / source
        if not absolute.exists():
            raise BuildError(f"required application asset is missing: {source}")
        args += ["--add-data", f"{absolute}{separator}{destination}"]
    return args


def hidden_import_arguments(target: str) -> List[str]:
    modules = list(BASE_HIDDEN_IMPORTS) + [PLATFORM_MODULES[target]]
    return [arg for module in modules for arg in ("--hidden-import", module)]


# Packages whose runtime helpers PyInstaller does not find on its own. pywinpty
# is the case that matters: its Python module and winpty.dll are collected
# automatically, but the pty is actually created by winpty-agent.exe (or the
# ConPTY pair), and without those the account sign-in of section 75.2 spawns a
# terminal that produces no output at all.
#
# comtypes is the second: it reads UIAutomationCore's type library and writes
# the generated Python for it into a ``comtypes.gen`` package. Inside a frozen
# application that package has to be present already - the module directory is
# not writable, and a build without it reports window reading as unavailable
# rather than failing, which is worse because it looks like a platform limit.
# pystray picks its backend by platform when it is imported: bundled whole.
# tzdata is the time zone database itself, as data files: Windows has none of
# its own for zoneinfo to read, so without it a named zone cannot be resolved
# in the packaged application (ported from PR #4).
# faster_whisper carries its voice-activity model as a data file, and
# ctranslate2 and av their libraries: bundled whole, or a video's speech
# cannot be read in the packaged application (runtime/video.py).
_VIDEO = ["av", "faster_whisper", "ctranslate2"]
COLLECT_ALL_PACKAGES = {"windows": ["winpty", "comtypes", "pystray", "tzdata", *_VIDEO],
                        "macos": ["pystray", *_VIDEO], "linux": ["pystray", *_VIDEO]}


def collect_arguments(target: str) -> List[str]:
    """Bundle whole packages whose data files and helper binaries are needed."""
    args: List[str] = []
    for package in COLLECT_ALL_PACKAGES.get(target, []):
        try:
            __import__(package)
        except Exception:
            print(f"note: optional package {package!r} is not installed; "
                  f"the features that need it will report themselves unavailable")
            continue
        if package == "comtypes":
            generate_uiautomation_bindings()
        args += ["--collect-all", package]
    return args


def generate_uiautomation_bindings() -> None:
    """Write comtypes' generated code before it is bundled, not after.

    comtypes builds a Python module from a COM type library the first time
    something asks for it and caches it inside its own package. A frozen
    application cannot do that - the package is inside the executable - so
    the module has to exist at packaging time. Generating it here means the
    build machine's first run pays for it and every installation gets it.
    """
    try:
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        print("generated the UI Automation bindings for packaging")
    except Exception as exc:
        print(f"note: could not generate the UI Automation bindings ({exc}); "
              f"reading the window in front will report itself unavailable")


#: Set by ``build.py --no-debugger``, as the released installers and
#: downloads are built (.github/workflows/release.yml): what a user installs
#: never carries the debuggers. A build without it - a developer's own -
#: carries them. An environment variable rather than a parameter because it
#: would otherwise have to be threaded through three platform builders that
#: are identical in every other respect.
NO_DEBUGGER_ENV = "PA_BUILD_NO_DEBUGGER"


# Machine-learning frameworks the application never imports. The embedding
# worker runs on torch through transformers and nothing else, but transformers
# looks for TensorFlow, Keras and JAX at import time, and PyInstaller follows
# those lookups into whatever the build machine happens to have installed. On
# a machine with TensorFlow that is well over a gigabyte of libraries nobody
# will load, so they are left out by name. Anything that tries them in the
# packaged application finds them missing, which transformers already treats
# as "not installed".
UNUSED_HEAVY_PACKAGES = [
    "tensorflow", "tensorflow_intel", "tf_keras", "keras", "tensorboard",
    "jax", "jaxlib", "flax",
]


def warn_about_bundle_weight() -> None:
    """Say so when the torch being bundled is a CUDA build.

    PyInstaller packages the torch it finds, and a CUDA build of torch brings
    its CUDA libraries along - about two gigabytes - though the embedding
    model runs on the processor. Read from torch's version file rather than
    by importing torch, which takes seconds and a good deal of memory.
    """
    import importlib.util
    import runpy

    try:
        spec = importlib.util.find_spec("torch")
        if spec is None or not spec.origin:
            return
        cuda = runpy.run_path(str(Path(spec.origin).parent / "version.py")).get("cuda")
    except Exception:
        return
    if not cuda:
        return
    print(
        f"\nnote: the Python running this build ({sys.executable}) has a CUDA build of "
        f"torch (CUDA {cuda}).\n"
        "  The packaged application will carry the CUDA libraries - about 2 GB more in the\n"
        "  folder and the installer - though the embedding model runs on the processor.\n"
        "  To build without them, make the CPU-only build environment once, outside the\n"
        "  repository, and run the build with the Python it prints:\n"
        "    python build/make_build_env.py\n"
        "  (on Windows: %LOCALAPPDATA%\\PersonalizedAgent-build\\venv\\Scripts\\python.exe "
        "build/windows/build_windows.py)\n",
        flush=True,
    )


def exclude_arguments(target: str) -> List[str]:
    excluded = [module for platform, module in PLATFORM_MODULES.items() if platform != target]
    excluded += UNUSED_HEAVY_PACKAGES
    if os.environ.get(NO_DEBUGGER_ENV) == "1":
        # Left out of the build rather than switched off in it. The Debugger
        # shows every state the agent has inferred about the user and what
        # each answer was assembled from; a build for someone who is not
        # developing the agent should not be carrying that at all, and a
        # setting can always be turned back on.
        excluded.append("personalized_agent.debugger")
    return [arg for module in excluded for arg in ("--exclude-module", module)]


def pyinstaller_command(
    target: str,
    *,
    name: str,
    windowed: bool = True,
    onefile: bool = False,
    icon: Optional[Path] = None,
    extra: Optional[List[str]] = None,
    distpath: Optional[Path] = None,
    clean: bool = True,
) -> List[str]:
    separator = ";" if target == "windows" else ":"
    warn_about_bundle_weight()
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        # --clean discards the analysis cache, so every build re-imports and
        # re-scans everything. A release wants that; iterating does not.
        *(["--clean"] if clean else []),
        "--name", name,
        "--distpath", str(distpath or dist_dir(target)),
        "--workpath", str(work_dir(target)),
        "--specpath", str(work_dir(target)),
        *(["--onefile"] if onefile else ["--onedir"]),
        *(["--windowed"] if windowed else ["--console"]),
        *data_arguments(separator),
        *hidden_import_arguments(target),
        *collect_arguments(target),
        *exclude_arguments(target),
        "--collect-submodules", "personalized_agent",
    ]
    if icon and icon.exists():
        command += ["--icon", str(icon)]
    if extra:
        command += extra
    command.append(str(ENTRY_SCRIPT))
    return command


def run(command: List[str], cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None,
        check: bool = True) -> subprocess.CompletedProcess:
    printable = " ".join(str(part) for part in command)
    print(f"$ {printable}", flush=True)
    completed = subprocess.run(
        [str(part) for part in command],
        cwd=str(cwd or REPO_ROOT),
        env={**os.environ, **(env or {})},
        text=True,
    )
    if check and completed.returncode != 0:
        raise BuildError(f"command failed with exit code {completed.returncode}: {printable}")
    return completed


def which(binary: str) -> Optional[str]:
    return shutil.which(binary)


def prepare_assets() -> None:
    """Generate the built-in character package if it is not committed."""
    from build.prepare_assets import ensure_assets

    ensure_assets()
    from build.prepare_assets import ensure_app_icons
    ensure_app_icons()


def write_build_manifest(target: str, artifacts: List[Path]) -> Path:
    """Record what a build produced, with the version metadata (section 105)."""
    import json
    import platform as platform_module
    from datetime import datetime, timezone

    manifest = {
        "application": APP_NAME,
        "application_id": APP_ID,
        "version": __version__,
        "target": target,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "builder": {
            "python": sys.version.split()[0],
            "platform": platform_module.platform(),
        },
        "artifacts": [
            {"path": str(path), "exists": path.exists(),
             "size_bytes": path.stat().st_size if path.is_file() else None}
            for path in artifacts
        ],
    }
    path = dist_dir(target) / "build-manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"build manifest: {path}")
    return path


__all__ = [
    "APP_DIR_NAME", "APP_ID", "APP_NAME", "APP_SLUG", "PUBLISHER", "__version__",
    "FILE_NAME", "PACKAGE_NAME", "FORMER_PACKAGE_NAMES",
    "BuildError", "DIST_ENV", "DIST_ROOT", "ENTRY_SCRIPT", "REPO_ROOT",
    "current_platform", "detect_cloud_sync", "dist_dir", "dist_root",
    "ensure_pyinstaller", "prepare_assets", "publish_file", "publish_tree",
    "pyinstaller_command", "remove_tree", "require_native_platform",
    "running_agent_processes", "run", "staging_dir", "terminate_agent_processes",
    "which", "write_build_manifest", "work_dir",
]

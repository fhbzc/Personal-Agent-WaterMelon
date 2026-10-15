"""Python packages for a piece of work: from PyPI, wheels only, into the
run's own folder.

A package is code from someone the user has never met, so where it can go
is settled by code, not by asking the model to be careful:

  WHAT CAN BE ASKED FOR is a plain PyPI requirement - a project's name and,
  if wanted, a version (``numpy>=1.26``). An address, a path, a VCS
  reference, an option such as ``--index-url`` or ``-e``, extras and
  environment markers are refused before anything runs: each is a way to
  fetch from somewhere other than PyPI or to change how pip behaves.

  PIP RUNS INSIDE THE GUARD - the same guarded interpreter `run_python`
  uses, as a child of its own - with writes allowed in the run's folder
  (the packages and pip's temporary files) and in the shared download
  cache under agent_work, and nowhere else. Its network is a list of two
  places: pypi.org and files.pythonhosted.org, port 443. The operating
  system keeps the same boundary from outside (runtime/isolation.py): the
  folders it may write, outbound HTTPS only, the run's versions read-only,
  and nothing of the user's to read - so pip's own native code, or a
  wheel's, cannot step past it the way it could step past the guard. Where
  the system cannot, Auto is not offered: each install is asked. It reads no
  configuration of the user's (no index, no credentials, no proxy), and it
  installs wheels only - building a package from source would start
  another process, which the guard does not allow.

  THE CODE THAT USES THEM runs through `run_python`, with the run's
  packages first on its path and every rule of the guard still in place:
  it can write only where the work may, and opens no connection at all.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...runtime.permissions import WorkspacePolicy
from ...runtime.sandbox import SandboxRuntime

#: Inside a run's folder: where its packages are installed.
PACKAGES_DIR = ".packages"

#: Inside agent_work: pip's download cache, shared by every run, so a
#: package is downloaded once. Code a run executes cannot write here - only
#: pip can - so one project's code cannot change what another installs.
PIP_CACHE_DIR = ".pip-cache"

#: Where packages come from, and the only places pip may connect to.
INDEX = "https://pypi.org/simple"
PYPI_PLACES = ("pypi.org:443", "files.pythonhosted.org:443")

#: How the user is asked: before each install, or not at all.
ASK = "ask"
AUTO = "auto"
MODES = (ASK, AUTO)

#: Requirements in one request, and how long one install may take.
MOST_AT_ONCE = 20
INSTALL_TIMEOUT = 900

#: A project name (PEP 508), and the version clauses after it.
_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
_CLAUSE = r"(?:===|==|!=|~=|<=|>=|<|>)\s*[A-Za-z0-9.*+!]+"
_PLAIN = re.compile(rf"^({_NAME})\s*(?:({_CLAUSE}(?:\s*,\s*{_CLAUSE})*))?$")


def mode_of(said: Any) -> str:
    """The mode asked for; anything else is to ask.

    Auto only where the operating system confines pip and the code that
    imports what it installed (runtime/isolation.py): a package's native
    code steps past the in-process guard, so without the OS boundary every
    install is the user's own decision, asked each time."""
    if str(said or "").strip().lower() != AUTO:
        return ASK
    return AUTO if auto_allowed() else ASK


def auto_allowed() -> bool:
    from ...runtime.isolation import isolation_status
    return bool(isolation_status().get("isolated"))


def _parsed(text: str) -> Any:
    """The requirement as packaging reads it - from the library if it is
    there, from pip's own copy if not; None when neither is."""
    try:
        from packaging.requirements import Requirement
    except Exception:                                      # pragma: no cover
        try:
            from pip._vendor.packaging.requirements import Requirement
        except Exception:
            return None
    return Requirement(text)


def check(requirement: Any) -> Tuple[Optional[str], str]:
    """One requirement: (the requirement as it will be passed to pip, "") or
    (None, why it is refused)."""
    text = str(requirement if requirement is not None else "").strip()
    if not text:
        return None, "an empty requirement"
    if text.startswith("-"):
        return None, f"{text!r}: options are not allowed - only a package name and a version"
    if "://" in text or re.match(r"(?i)^(git|hg|svn|bzr)\+", text):
        return None, f"{text!r}: addresses are not allowed - packages come from PyPI only"
    if "/" in text or "\\" in text or text.startswith(".") or re.match(r"^[A-Za-z]:", text):
        return None, f"{text!r}: paths are not allowed - packages come from PyPI only"
    if "@" in text:
        return None, f"{text!r}: a direct reference is not allowed - packages come from PyPI only"
    if ";" in text:
        return None, f"{text!r}: environment markers are not allowed"
    if "[" in text or "]" in text:
        return None, f"{text!r}: extras are not allowed - name the packages themselves"
    if len(text) > 200 or not _PLAIN.match(text):
        return None, (f"{text!r}: not a plain package name with an optional version "
                      "(like pandas, numpy>=1.26 or scikit-learn==1.5.*)")
    try:
        parsed = _parsed(text)
    except Exception as exc:
        return None, f"{text!r}: not a valid requirement ({exc})"[:300]
    if parsed is not None and (parsed.url or parsed.extras or parsed.marker):
        return None, f"{text!r}: only a package name and a version are allowed"
    return (str(parsed) if parsed is not None else text), ""


def validate(requirements: Any) -> Tuple[List[str], List[str]]:
    """Every requirement checked: (the ones that may be installed, why each
    of the others is refused)."""
    if isinstance(requirements, str):
        requirements = [requirements]
    if not isinstance(requirements, (list, tuple)):
        return [], ["give the packages as a list of names"]
    allowed: List[str] = []
    refused: List[str] = []
    for one in requirements:
        kept, why = check(one)
        if kept is None:
            refused.append(why)
        elif kept not in allowed:
            allowed.append(kept)
    if len(allowed) > MOST_AT_ONCE:
        refused.append(f"at most {MOST_AT_ONCE} packages in one request")
    if not allowed and not refused:
        refused.append("no packages were named")
    return allowed, refused


def _key(name: str) -> str:
    """A project name compared as PyPI compares them."""
    return re.sub(r"[-_.]+", "-", name).lower()


def installed_in(target: Path) -> Dict[str, Dict[str, str]]:
    """What is installed in a folder: each project by its key, with its own
    name and version - read from the metadata pip leaves, not from what pip
    printed."""
    found: Dict[str, Dict[str, str]] = {}
    if not target.is_dir():
        return found
    for info in target.glob("*.dist-info"):
        name, version = "", ""
        try:
            for line in (info / "METADATA").read_text(encoding="utf-8",
                                                      errors="replace").splitlines():
                if line.startswith("Name:") and not name:
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("Version:") and not version:
                    version = line.split(":", 1)[1].strip()
                elif not line.strip():
                    break
        except OSError:
            stem = info.name[:-len(".dist-info")]
            name, _, version = stem.partition("-")
        if name:
            found[_key(name)] = {"name": name, "version": version}
    return found


#: The pip run, as a script for the guarded interpreter. Written by the
#: application, never by the model; its only input is the checked list.
_PIP_SCRIPT = '''\
import json
import sys

ARGS = json.loads({args!r})

# No proxy - not the machine's, not the registry's: pip goes to the package
# index directly, and the guard lets it go nowhere else.
import urllib.request
urllib.request.getproxies = lambda: {{}}
urllib.request.proxy_bypass = lambda host: True

try:
    import os
    import pip
    from pip._internal.utils import appdirs
    from pip._internal.cli import cmdoptions
    from pip._internal.cli.main import main
except Exception as exc:
    print("pip is not available to this interpreter: %s" % exc, file=sys.stderr)
    sys.exit(3)

# On Windows pip's console output would be drawn through the console's
# native interface, which the guard does not load: plain text instead.
try:
    from pip._vendor.rich import console as _console
    _console.detect_legacy_windows = lambda: False
except Exception:
    pass

# pip's per-user and machine-wide folders are the run's own: no config file
# of the user's or the machine's is looked for, and nothing is kept there.
appdirs.site_config_dirs = lambda appname: []
appdirs.user_config_dir = lambda appname, roaming=True: os.path.join(
    os.environ["XDG_CONFIG_HOME"], appname)
appdirs.user_cache_dir = lambda appname: os.path.join(
    os.environ["XDG_CACHE_HOME"], appname)

options = list(ARGS)
# The machine's certificate store is read through a native library, which
# the guard does not load; pip's own bundle of certificates is used instead.
try:
    if "legacy-certs" in (cmdoptions.use_deprecated_feature().choices or []):
        options.append("--use-deprecated=legacy-certs")
except Exception:
    pass
# No keyring: pip asks the user's credential store for nothing.
try:
    cmdoptions.keyring_provider()
    options += ["--keyring-provider", "disabled"]
except Exception:
    pass
sys.exit(main(options))
'''


class Installer:
    """Installs checked requirements into a run's folder, with pip running
    under the guard. One per work run; `install` blocks until pip ends."""

    def __init__(self, policy: WorkspacePolicy, platform: Any = None,
                 sandbox_enabled: bool = True) -> None:
        #: The work's own policy: what pip may read is what the work may.
        self.policy = policy
        self.platform = platform
        self.sandbox_enabled = sandbox_enabled

    def cache_dir(self) -> Path:
        """agent_work/.pip-cache - beside the works' folders, in none."""
        return Path(self.policy.write_root).parent / PIP_CACHE_DIR

    def pip_policy(self, run_dir: Path) -> WorkspacePolicy:
        """Writes in the run's folder and the download cache alone - its
        .packages included, its saved versions not; the network to PyPI
        alone; never a folder the user granted, and nothing of the user's to
        read: the operating system opens it only the interpreter and its own
        folders (pip needs nothing else)."""
        cache = self.cache_dir()
        cache.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        return WorkspacePolicy(
            workspace_root=Path(self.policy.workspace_root),
            writable_root=run_dir,
            readable_paths=[],
            readonly_paths=list(self.policy.readonly_paths),
            protected_paths=list(self.policy.protected_paths),
            # What the work may not read, pip may not either.
            sealed_paths=list(getattr(self.policy, "sealed_paths", None) or []),
            code_root=getattr(self.policy, "code_root", None),
            closed_paths=list(getattr(self.policy, "closed_paths", None) or []),
            extra_writable=[cache],
            network=True,
            network_allow=list(PYPI_PLACES),
            child_readonly_names=[".versions"])

    @staticmethod
    def environment(run_dir: Path) -> Dict[str, str]:
        """Every place pip would look for the user's settings - its config
        files, the per-user folders - pointed into the run's folder or
        turned off, so no index, credential or proxy of theirs is read."""
        home = run_dir / ".agent_runtime" / "home"
        for one in ("roaming", "local", "config", "cache"):
            (home / one).mkdir(parents=True, exist_ok=True)
        return {
            "PIP_CONFIG_FILE": os.devnull,
            "PYTHONNOUSERSITE": "1",
            "HOME": str(home), "USERPROFILE": str(home),
            "APPDATA": str(home / "roaming"), "LOCALAPPDATA": str(home / "local"),
            "XDG_CONFIG_HOME": str(home / "config"), "XDG_CACHE_HOME": str(home / "cache"),
        }

    def run_guarded(self, run_dir: Path, code: str, name: str = "pip_child.py",
                    timeout: float = INSTALL_TIMEOUT) -> Any:
        """Code run as pip is: in the guarded interpreter, under the pip
        policy and environment. The installer's one way to run anything."""
        runtime = SandboxRuntime(self.pip_policy(run_dir), platform=self.platform,
                                 sandbox_enabled=self.sandbox_enabled)
        script = runtime.store_generated_script(code, name)
        return runtime.run_python_script(script, timeout=timeout,
                                         environment=self.environment(run_dir))

    def install(self, run_dir: Path, requirements: List[str],
                timeout: float = INSTALL_TIMEOUT) -> Dict[str, Any]:
        allowed, refused = validate(requirements)
        if refused:
            return {"error": "refused: " + "; ".join(refused), "installed": []}
        run_dir = Path(run_dir)
        target = run_dir / PACKAGES_DIR
        target.mkdir(parents=True, exist_ok=True)
        before = installed_in(target)
        args = ["install", "--isolated", "--no-input", "--disable-pip-version-check",
                "--no-color", "--progress-bar", "off", "--only-binary=:all:",
                "--upgrade", "--no-warn-script-location",
                "--index-url", INDEX, "--cache-dir", str(self.cache_dir()),
                "--target", str(target), *allowed]
        result = self.run_guarded(run_dir, _PIP_SCRIPT.format(args=json.dumps(args)),
                                  "install_packages.py", timeout)
        after = installed_in(target)
        changed = [dict(one) for key, one in sorted(after.items())
                   if before.get(key) != one]
        wanted = {_key(re.match(_NAME, one).group(0)) for one in allowed}
        said: Dict[str, Any] = {
            "requested": allowed, "installed": changed,
            "available": [dict(after[key]) for key in sorted(wanted) if key in after],
            "folder": str(target)}
        if result.ok:
            return said
        output = "\n".join(filter(None, [result.stdout or "", result.stderr or ""]))
        lines = [line for line in output.splitlines() if line.strip()]
        if result.timed_out:
            reason = f"pip did not finish within {int(timeout)} seconds"
        elif "SandboxViolation" in output:
            blocked = [line for line in lines if "SandboxViolation" in line]
            reason = ("pip needed something the guard does not allow, so the "
                      "install stopped: " + (blocked[-1] if blocked else "")).strip()
        elif "No matching distribution" in output or "Could not find a version" in output:
            reason = ("no wheel on PyPI fits this interpreter - only wheels can be "
                      "installed; nothing is built from source")
        else:
            reason = f"pip failed (exit {result.returncode})"
        said["error"] = reason[:600]
        said["output"] = "\n".join(lines[-25:])[-4000:]
        return said

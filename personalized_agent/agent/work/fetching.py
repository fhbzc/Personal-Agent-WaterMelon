"""WHAT A WORK BRINGS IN FROM THE NETWORK: FETCHED OUTSIDE, RUN INSIDE (the
user, 2026-10-25: npm, git and downloads, as Claude Code has them).

A work's commands reach no network: the operating system keeps them from it
(runtime/isolation.py), so whatever they read of the user's could not be
sent anywhere. Yet a work needs packages from npm, a repository, a file from
the web. So the agent fetches them itself, outside the sandbox, as the
package installer fetches Python packages - and fetching is all it does
there: nothing fetched is run outside. Each lands in the work's own folder,
and is used by the work's commands, inside.

  A FILE (`download`): one https address that already exists - the same
  check as a page read (runtime/web_guard.py), so nothing read can leave
  inside an address - saved, at most MOST_DOWNLOAD bytes, never opened.

  A REPOSITORY (`git_clone`): a public https one, cloned shallow unless
  asked. No configuration of the user's is read - not theirs, not the
  system's: no credential, no helper, no proxy of theirs, no template, no
  hook; nothing is asked for (no password prompt); no submodule, no
  symbolic link (each a plain file), no protocol but https. Never pushed:
  nothing here sends anything of the work's to a repository. Local git -
  commits, branches, diffs - is the work's own, in its commands.

  NPM PACKAGES (`npm_install`): from the public registry alone, as the
  user's package mode allows (asked first, or not). Plain names and version
  ranges only - an address, a git or file reference, a local path, an alias
  is refused, in what is asked for and in the package.json - and a lock
  file that resolves anywhere but the registry is refused too. npm runs
  with no configuration of the user's or the project's (in a stage of its
  own, with only package.json and its lock), and with every install script
  OFF: no package's code runs outside. A package that needs its scripts is
  built inside, by the work's own command (npm rebuild).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: The most a downloaded file may be, and how long fetching anything may take.
MOST_DOWNLOAD = 300 * 1024 * 1024
DOWNLOAD_SECONDS = 600
GIT_SECONDS = 900
NPM_SECONDS = 900
#: The most a cloned repository may be, on disk.
MOST_REPOSITORY = 1024 * 1024 * 1024
#: Where npm packages come from.
NPM_REGISTRY = "https://registry.npmjs.org/"
NPM_HOSTS = ("registry.npmjs.org",)
#: npm requests at once.
MOST_NPM = 30

AGENT = "Mozilla/5.0 (personal agent; fetching for its user's work)"

#: An npm package name - scoped or not - and a version range or tag after
#: "@": no address, path, colon or slash beyond the scope's.
_NPM_NAME = r"(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*"
_NPM_RANGE = r"[A-Za-z0-9.^~<>=|* +_-]+"
_NPM_SPEC = re.compile(rf"^({_NPM_NAME})(?:@({_NPM_RANGE}))?$")
_NPM_VERSION = re.compile(rf"^{_NPM_RANGE}$")
#: The places in a package.json that name what is installed.
NPM_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies",
                "peerDependencies")


# -- the environment of what runs outside -----------------------------------------
def outside_environment(home: Path) -> Dict[str, str]:
    """git's and npm's environment: the system's own (where programs are, a
    proxy the computer needs), the per-user folders pointed into the work's
    runtime folder, every configuration of the user's off, nothing asked."""
    for one in ("roaming", "local", "config", "cache", "temp"):
        (home / one).mkdir(parents=True, exist_ok=True)
    env = {key: os.environ[key] for key in (
        "PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "ProgramFiles",
        "ProgramFiles(x86)", "ProgramW6432", "ProgramData", "SystemDrive",
        "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "LANG",
        "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
        "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS") if os.environ.get(key)}
    env.update({
        "HOME": str(home), "USERPROFILE": str(home),
        "APPDATA": str(home / "roaming"), "LOCALAPPDATA": str(home / "local"),
        "XDG_CONFIG_HOME": str(home / "config"), "XDG_CACHE_HOME": str(home / "cache"),
        "TEMP": str(home / "temp"), "TMP": str(home / "temp"), "TMPDIR": str(home / "temp"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "GIT_ASKPASS": "",
        "SSH_ASKPASS": "", "GIT_LFS_SKIP_SMUDGE": "1",
        "NO_UPDATE_NOTIFIER": "1", "npm_config_update_notifier": "false", "CI": "1"})
    return env


def _run(command: Sequence[str], cwd: Path, env: Dict[str, str], timeout: float) -> Any:
    from ...common import children
    return children.run([str(one) for one in command], cwd=str(cwd), env=env,
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        timeout=timeout, whole_tree=True)


def _size(root: Path) -> int:
    total = 0
    for folder, _dirs, names in os.walk(root):
        for name in names:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                continue
    return total


def _tail(text: str, most: int = 3000) -> str:
    lines = [one for one in str(text or "").splitlines() if one.strip()]
    return "\n".join(lines[-30:])[-most:]


# -- a file -----------------------------------------------------------------------
def download(url: str, target: Path, timeout: float = DOWNLOAD_SECONDS,
             most: int = MOST_DOWNLOAD) -> Dict[str, Any]:
    """One https address saved to `target` (a path the caller checked is
    the work's): at most `most` bytes - more, and nothing is kept."""
    if not re.match(r"(?i)^https://", url or ""):
        return {"error": "only an https address can be downloaded"}
    part = target.with_name(target.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    began = time.time()
    got = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, open(part, "wb") as out:
            final = response.geturl()
            if not final.lower().startswith("https://"):
                raise ValueError(f"it was sent on to {final}, which is not https")
            kind = response.headers.get("Content-Type", "")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                got += len(chunk)
                if got > most:
                    raise ValueError(f"it is larger than {most // (1024 * 1024)} MB")
                if time.time() - began > timeout:
                    raise ValueError(f"it took longer than {int(timeout)} seconds")
                out.write(chunk)
        os.replace(part, target)
    except Exception as exc:
        try:
            part.unlink()
        except OSError:
            pass
        return {"error": f"could not download {url}: {exc}"[:500]}
    return {"downloaded": url, "path": str(target), "bytes": got, "type": kind,
            "seconds": round(time.time() - began, 1)}


# -- a repository -----------------------------------------------------------------
def git_program() -> str:
    return shutil.which("git") or ""


def git_clone(url: str, target: Path, home: Path, depth: int = 1, ref: str = "",
              timeout: float = GIT_SECONDS, most: int = MOST_REPOSITORY) -> Dict[str, Any]:
    """A public https repository cloned into `target` (a path the caller
    checked is the work's, not there yet)."""
    git = git_program()
    if not git:
        return {"error": "git is not installed on this computer"}
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme.lower() != "https" or not parts.netloc or "@" in parts.netloc:
        return {"error": "only a public https address of a repository can be cloned "
                         "(no user or password in it)"}
    if ref and not re.match(r"^[A-Za-z0-9._/-]{1,200}$", ref) or ref.startswith("-"):
        return {"error": "a branch or tag is letters, digits, '.', '_', '/' and '-'"}
    hooks = home / "no-hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    command = [git,
               "-c", "protocol.allow=never", "-c", "protocol.https.allow=always",
               "-c", f"core.hooksPath={hooks}", "-c", "core.symlinks=false",
               "-c", "credential.helper=", "-c", "core.askPass=",
               "-c", "submodule.recurse=false", "-c", "http.followRedirects=initial",
               "-c", "init.templateDir=",
               "clone", "--no-recurse-submodules", "--quiet"]
    if depth and depth > 0:
        command += ["--depth", str(int(depth))]
    if ref:
        command += ["--branch", ref]
    command += ["--", url, str(target)]
    began = time.time()
    try:
        done = _run(command, target.parent, outside_environment(home), timeout)
    except Exception as exc:
        shutil.rmtree(target, ignore_errors=True)
        return {"error": f"git could not clone it: {exc}"[:500]}
    if done.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        return {"error": "git could not clone it", "output": _tail(done.stderr or done.stdout)}
    size = _size(target)
    if size > most:
        shutil.rmtree(target, ignore_errors=True)
        return {"error": f"the repository is larger than {most // (1024 * 1024)} MB - "
                         "nothing was kept"}
    head = _run([git, "-C", str(target), "log", "-1", "--format=%H %s"], target,
                outside_environment(home), 60)
    return {"cloned": url, "path": str(target), "bytes": size,
            "head": (head.stdout or "").strip()[:300], "depth": depth or "whole",
            "seconds": round(time.time() - began, 1)}


# -- npm packages -----------------------------------------------------------------
def check_npm(specs: Sequence[Any]) -> Tuple[List[str], List[str]]:
    """(allowed, refused - each with why) of npm packages asked for."""
    allowed, refused = [], []
    for one in specs:
        text = str(one or "").strip()
        if not text:
            continue
        if _NPM_SPEC.match(text):
            allowed.append(text)
        else:
            refused.append(f"{text[:80]}: only a package's name, with @ and a version "
                           "or range if wanted, from the public registry - no address, "
                           "path, git or file reference, or alias")
    return allowed, refused


def check_package_json(data: Any) -> List[str]:
    """What in a package.json would fetch from anywhere but the registry."""
    if not isinstance(data, dict):
        return ["package.json is not an object"]
    wrong = []
    if data.get("workspaces"):
        wrong.append("workspaces: local packages are not installed here")
    for section in NPM_SECTIONS:
        for name, spec in (data.get(section) or {}).items():
            if not re.match(rf"^{_NPM_NAME}$", str(name)) or \
                    not _NPM_VERSION.match(str(spec or "").strip() or "x"):
                wrong.append(f"{section}: {name} {str(spec)[:60]} - not a version from the "
                             "public registry")
    for key in ("overrides", "resolutions"):
        if data.get(key):
            wrong.append(f"{key}: not used here - ask for the versions instead")
    return wrong


def check_lock(data: Any) -> List[str]:
    """What in a package-lock.json resolves anywhere but the registry."""
    wrong = []
    packages = data.get("packages") if isinstance(data, dict) else None
    for where, one in (packages or {}).items():
        resolved = str((one or {}).get("resolved") or "")
        if not where or not resolved:
            continue
        host = urllib.parse.urlsplit(resolved).hostname or ""
        if (one or {}).get("link") or host not in NPM_HOSTS:
            wrong.append(f"{where}: resolves to {resolved[:80]}")
    return wrong


def npm_program() -> Optional[List[str]]:
    """How npm is run: node with npm's own script - never npm.cmd, whose
    arguments cmd.exe would read again."""
    node = shutil.which("node")
    if not node:
        return None
    node_path = Path(node).resolve()
    for script in (node_path.parent / "node_modules" / "npm" / "bin" / "npm-cli.js",
                   node_path.parent.parent / "lib" / "node_modules" / "npm" / "bin" /
                   "npm-cli.js"):
        if script.is_file():
            return [str(node_path), str(script)]
    npm = shutil.which("npm")
    if npm and Path(npm).resolve().name == "npm-cli.js":
        return [str(node_path), str(Path(npm).resolve())]
    return None


def npm_install(folder: Path, packages: Sequence[str], stage: Path, cache: Path,
                home: Path, timeout: float = NPM_SECONDS) -> Dict[str, Any]:
    """npm packages installed for the project in `folder` (the work's):
    those asked for added to its package.json, or - none asked for - what its
    package.json lists. In `stage`, with its package.json and lock alone;
    node_modules, package.json and the lock then put in `folder`."""
    npm = npm_program()
    if npm is None:
        return {"error": "npm is not installed on this computer (node and npm are needed)"}
    allowed, refused = check_npm(packages)
    if refused:
        return {"error": "refused: " + "; ".join(refused)}
    manifest = folder / "package.json"
    lock = folder / "package-lock.json"
    data: Dict[str, Any] = {}
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"error": f"package.json cannot be read: {exc}"[:300]}
        wrong = check_package_json(data)
        if wrong:
            return {"error": "package.json asks for what is not installed here: "
                             + "; ".join(wrong[:10])}
    elif not allowed:
        return {"error": "no package.json in that folder, and no package asked for"}
    if lock.is_file():
        try:
            wrong = check_lock(json.loads(lock.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            return {"error": f"package-lock.json cannot be read: {exc}"[:300]}
        if wrong:
            return {"error": "package-lock.json resolves outside the public registry - "
                             "delete it and install again: " + "; ".join(wrong[:5])}
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    cache.mkdir(parents=True, exist_ok=True)
    if manifest.is_file():
        shutil.copyfile(manifest, stage / "package.json")
    else:
        (stage / "package.json").write_text('{"private": true}\n', encoding="utf-8")
    if lock.is_file():
        shutil.copyfile(lock, stage / "package-lock.json")
    empty = home / "empty.npmrc"
    home.mkdir(parents=True, exist_ok=True)
    empty.write_text("", encoding="utf-8")
    command = [*npm, "install", *allowed,
               "--ignore-scripts", "--no-audit", "--no-fund", "--no-progress",
               "--registry", NPM_REGISTRY, "--userconfig", str(empty),
               "--globalconfig", str(empty), "--cache", str(cache),
               "--prefix", str(stage), "--loglevel", "error"]
    began = time.time()
    try:
        done = _run(command, stage, outside_environment(home), timeout)
    except Exception as exc:
        return {"error": f"npm could not run: {exc}"[:500]}
    if done.returncode != 0:
        return {"error": f"npm failed (exit {done.returncode})",
                "output": _tail((done.stderr or "") + "\n" + (done.stdout or ""))}
    wrong = []
    if (stage / "package-lock.json").is_file():
        try:
            wrong = check_lock(json.loads((stage / "package-lock.json").read_text(
                encoding="utf-8")))
        except (OSError, ValueError):
            wrong = ["the lock npm wrote cannot be read"]
    if wrong:
        return {"error": "npm resolved packages outside the public registry - nothing "
                         "was installed: " + "; ".join(wrong[:5])}
    modules = folder / "node_modules"
    if modules.exists():
        shutil.rmtree(modules)
    if (stage / "node_modules").is_dir():
        os.replace(stage / "node_modules", modules)
    for name in ("package.json", "package-lock.json"):
        if (stage / name).is_file():
            shutil.copyfile(stage / name, folder / name)
    shutil.rmtree(stage, ignore_errors=True)
    installed = []
    try:
        listed = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        listed = {}
    for section in NPM_SECTIONS:
        for name in (listed.get(section) or {}):
            try:
                one = json.loads((modules / name / "package.json").read_text(encoding="utf-8"))
                installed.append({"name": name, "version": str(one.get("version") or "")})
            except (OSError, ValueError):
                continue
    return {"requested": allowed, "installed": installed, "folder": str(modules),
            "ecosystem": "npm", "seconds": round(time.time() - began, 1),
            "note": "Installed with every install script off: a package that must build "
                    "itself is built with run_command (npm rebuild <name>), inside."}

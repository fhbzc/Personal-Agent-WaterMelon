"""Build-time validation (section 113).

Runs before any artifact is produced and fails explicitly:

* required Python dependencies are importable;
* the platform providers for this target can load;
* required application assets exist;
* no development-only secret, private credential or test configuration is about
  to be shipped.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REQUIRED_IMPORTS = [
    ("sqlite3", "knowledge graph and scheduler storage"),
    ("tkinter", "desktop agent window"),
    ("cryptography", "agent identity and credential encryption"),
]

OPTIONAL_IMPORTS = [
    ("anthropic", "Anthropic model provider"),
    ("psutil", "process metrics"),
    ("watchdog", "realtime filesystem events"),
    ("PIL", "character rendering"),
    ("pystray", "the icon in the notification area"),
    ("keyring", "OS keychain credential storage"),
    ("comtypes", "reading the window in front (Windows only)"),
]

REQUIRED_ASSETS = [
    "personalized_agent/api/web/index.html",
    "personalized_agent/api/web/app.js",
    "personalized_agent/api/web/styles.css",
    "personalized_agent/resources/characters/watermelon/manifest.json",
    "requirements.txt",
    "README.md",
]

# Files that must never end up inside a release artifact.
FORBIDDEN_PATHS = [
    ".env",
    "secrets.json",
    "credentials.json",
    "personalized_agent/resources/dev_settings.json",
]

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)aws_secret_access_key\s*[:=]\s*\S{16,}"),
]

SCANNED_SUFFIXES = {".py", ".json", ".js", ".html", ".css", ".txt", ".md", ".toml", ".yml",
                    ".yaml", ".iss", ".plist", ".desktop"}

SKIP_DIRS = {".git", "dist", "build/.work", "__pycache__", ".venv", "venv", "tests"}


def check_imports() -> Tuple[List[str], List[str]]:
    errors, warnings = [], []
    for module, purpose in REQUIRED_IMPORTS:
        try:
            importlib.import_module(module)
        except Exception as exc:
            errors.append(f"required dependency '{module}' ({purpose}) cannot be imported: {exc}")
    for module, purpose in OPTIONAL_IMPORTS:
        try:
            importlib.import_module(module)
        except Exception:
            warnings.append(f"optional dependency '{module}' ({purpose}) is not installed; "
                            f"the packaged application will run with that capability disabled")
    return errors, warnings


def check_platform_providers(target: str) -> List[str]:
    module_name = {
        "windows": "personalized_agent.platform_layer.windows",
        "macos": "personalized_agent.platform_layer.macos",
        "linux": "personalized_agent.platform_layer.linux",
    }[target]
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        return [f"platform provider {module_name} failed to import: {exc}"]
    return []


def check_assets() -> List[str]:
    return [f"missing required asset: {asset}" for asset in REQUIRED_ASSETS
            if not (REPO_ROOT / asset).exists()]


def _iter_source_files():
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        if any(relative.startswith(skip) or f"/{skip}/" in f"/{relative}" for skip in SKIP_DIRS):
            continue
        yield path, relative


def check_no_secrets() -> List[str]:
    errors = []
    for forbidden in FORBIDDEN_PATHS:
        if (REPO_ROOT / forbidden).exists():
            errors.append(f"development-only file must not be shipped: {forbidden}")
    for path, relative in _iter_source_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match and "REDACT" not in match.group(0):
                errors.append(f"possible credential material in {relative}")
                break
    return errors


def check_production_configuration() -> List[str]:
    """Test configuration must not ship as production configuration."""
    errors = []
    settings = REPO_ROOT / "personalized_agent" / "resources" / "settings.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except ValueError:
            return [f"packaged default settings are not valid JSON: {settings}"]
        if data.get("debug") or data.get("test_mode"):
            errors.append("packaged default settings contain a debug/test flag")
    return errors


def validate(target: str) -> int:
    errors: List[str] = []
    warnings: List[str] = []

    import_errors, import_warnings = check_imports()
    errors += import_errors
    warnings += import_warnings
    errors += check_platform_providers(target)
    errors += check_assets()
    errors += check_no_secrets()
    errors += check_production_configuration()

    for warning in warnings:
        print(f"warning: {warning}")
    if errors:
        print("\nbuild validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"build validation passed for target '{target}'")
    return 0


def main() -> int:
    from build.common import current_platform

    target = sys.argv[1] if len(sys.argv) > 1 else current_platform()
    return validate(target)


if __name__ == "__main__":
    raise SystemExit(main())

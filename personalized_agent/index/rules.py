"""WHAT THE INDEX READS, WHAT IT KEEPS ONLY THE NAME OF, AND WHAT IT
LEAVES ALONE.

  * FOLDERS NEVER GONE INTO: what tools make and remake (version control,
    environments, caches, builds), the places keys and sign-ins are kept,
    and the application folders of the system.
  * FILES NEVER READ: anything named like a secret (.env, keys,
    credentials), and a file that is only in the cloud - a placeholder that
    opening would download (OneDrive and the like on Windows, iCloud on a
    Mac): its name is kept, nothing is fetched.
  * READ: a text file up to MOST_TEXT_BYTES, by its kind or by its content
    (no NUL in its first bytes). A data file - CSV, JSON lines, logs - only
    up to MOST_DATA_BYTES, and a JSON file up to MOST_JSON_BYTES; past it,
    its name.
  * A CONVERSATION RECORD (Claude Code's, Codex's): what was said and done
    in it, not what the tools gave back (talk.py).
  * EVERY OTHER FILE - a picture, a video, an archive, a binary: its name,
    which is what it is found by. No picture is read for its words.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Iterable, Optional

#: The most of a text file read.
MOST_TEXT_BYTES = 1_000_000
#: The most of a data file read: past it, only its name.
MOST_DATA_BYTES = 3_000_000
#: The most of a JSON file read: past it, only its name. One larger is data
#: - results, dumps - nearly all of it numbers and keys (on one machine,
#: JSON was half of all that was read, and found next to nothing).
MOST_JSON_BYTES = 200_000
#: The most of a conversation record's text kept (Claude Code's and Codex's
#: sessions, one JSON line a turn): of WHAT IS KEPT of it (talk.py), not of
#: the file - a record of 1.3 GB keeps under 8 MB, and the longest records
#: are the work the user did most. Past it, its name.
MOST_SESSION_BYTES = 30_000_000
#: What is read, and how. When it changes, the next build looks again at
#: every file it kept and keeps it the new way (build.py) - reading again
#: only what is now kept differently.
RULES_VERSION = 2
#: Lines to a piece of a file: what a search finds is a piece, with where.
LINES_PER_PIECE = 40

SKIPPED_FOLDERS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", ".idea",
    "site-packages", "dist-packages", ".cache", "build", "dist",
    ".next", ".nuxt", "target", "obj", ".terraform", ".ipynb_checkpoints",
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store",
    "$recycle.bin", "system volume information",
})
#: The application folders of the system, right under the user's home: by
#: those names anywhere else, a folder of the user's own.
HOME_ONLY_SKIPPED = frozenset({"appdata", "library"})
#: Folder names found anywhere in a path that mean "leave it": a virtual
#: environment's own marker files are looked for too (`skipped_folder`).
_ENV_MARKERS = ("pyvenv.cfg", "conda-meta")

_SECRET_NAME = re.compile(
    r"(^\.env(\..*)?$|^\.netrc$|^\.npmrc$|^\.pypirc$|^id_(rsa|dsa|ecdsa|ed25519)"
    r"|\.(pem|key|p12|pfx|keystore|jks|kdbx|ovpn)$|secret|credential|password|passwd"
    r"|token|apikey|api_key|private[_-]?key)", re.IGNORECASE)

TEXT_SUFFIXES = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".org", ".tex", ".bib", ".py", ".pyi", ".ipynb",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".html", ".htm", ".css", ".scss", ".less", ".xml", ".svg",
    ".java", ".kt", ".scala", ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs",
    ".rb", ".php", ".swift", ".m", ".r", ".jl", ".lua", ".pl", ".sh", ".bash", ".zsh",
    ".ps1", ".bat", ".cmd", ".sql", ".graphql", ".proto", ".vue", ".svelte", ".dart",
    ".tsv", ".csv", ".jsonl", ".ndjson", ".log", ".do", ".ado", ".sas", ".stan",
})
DATA_SUFFIXES = frozenset({".csv", ".tsv", ".jsonl", ".ndjson", ".log", ".json"})

# Windows file attributes of a file that is only in the cloud.
_RECALL_ON_OPEN = 0x00040000
_RECALL_ON_DATA_ACCESS = 0x00400000
_OFFLINE = 0x00001000


def skipped_folder(path: Path, never: Iterable[Path] = ()) -> bool:
    """Whether a folder is left alone: by its name, as a virtual
    environment, or as one of the places it may never go (the agent's own
    data, and what the agents may not read)."""
    name = path.name.lower()
    if name in SKIPPED_FOLDERS or (name.startswith(".") and name not in (".github",)):
        return True
    if name in HOME_ONLY_SKIPPED and _norm(path.parent) == _norm(Path.home()):
        return True
    if any((path / marker).exists() for marker in _ENV_MARKERS):
        return True
    resolved = _norm(path)
    return any(resolved == _norm(one) or resolved.startswith(_norm(one) + os.sep)
               for one in never)


def secret_name(name: str) -> bool:
    return bool(_SECRET_NAME.search(name))


def cloud_only(info: os.stat_result, name: str) -> bool:
    """A placeholder that reading would download: by its attributes on
    Windows, by its name on a Mac (iCloud's ".name.icloud")."""
    attributes = getattr(info, "st_file_attributes", 0) or 0
    if attributes & (_RECALL_ON_OPEN | _RECALL_ON_DATA_ACCESS | _OFFLINE):
        return True
    return name.startswith(".") and name.endswith(".icloud")


def how_kept(path: Path, info: os.stat_result, sessions: bool = False) -> str:
    """"text" - its words read; "talk" - a conversation record, kept as what
    was said and done in it (talk.py); "name" - only its name; "" - not at
    all. `sessions`: a folder of conversation records, each kept as "talk"
    whatever its size - what is kept of it is what MOST_SESSION_BYTES
    bounds (build.py)."""
    if not stat.S_ISREG(info.st_mode):
        return ""
    if secret_name(path.name):
        return "name" if not path.name.startswith(".") else ""
    if cloud_only(info, path.name):
        return "name"
    suffix = path.suffix.lower()
    size = int(info.st_size)
    if sessions and suffix in (".jsonl", ".json"):
        return "talk"
    if suffix == ".json" and size > MOST_JSON_BYTES:
        return "name"
    if suffix in DATA_SUFFIXES and size > MOST_DATA_BYTES:
        return "name"
    if size > MOST_TEXT_BYTES:
        return "name"
    if suffix in TEXT_SUFFIXES:
        return "text"
    if suffix == "" and size and _looks_like_text(path):
        return "text"
    return "name"


def _looks_like_text(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
    except OSError:
        return False
    return b"\0" not in head


def read_text(path: Path) -> Optional[str]:
    """A text file's words, or None when it is not one after all."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    for encoding in ("utf-8", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


__all__ = ["skipped_folder", "secret_name", "cloud_only", "how_kept", "read_text",
           "MOST_TEXT_BYTES", "MOST_DATA_BYTES", "MOST_JSON_BYTES", "MOST_SESSION_BYTES",
           "LINES_PER_PIECE", "RULES_VERSION"]

"""WHAT IS NEVER SENT OR KEPT: secrets in what learning reads.

The user's conversations and files may hold a key pasted in, a token, a
password, a private key. Before anything read is sent to a model - and
before anything is kept: a case, a judgment, the copy of a file as last
read - it goes through `scrub`, which puts "[removed]" where such a thing
was. It counts what it removed, for the update's log.
"""

from __future__ import annotations

import re
from typing import Any, Tuple

REMOVED = "[removed]"

#: A known key's prefix and what follows it.
_KNOWN_KEY = re.compile(
    r"\b(?:sk-|sk_live_|rk_live_|ghp_|gho_|ghs_|github_pat_|xox[abpr]-|AKIA|ASIA|AIza|"
    r"ya29\.|glpat-|hf_)[\w\-]{8,}")
#: A long run of key-like characters with letters AND digits in it - a key
#: or an encoded payload. Not a path (no slashes) and not plain hex: a
#: commit or a file's hash is a reference, kept.
_LONG_RUN = re.compile(r"(?<![\w/\\.\-])[A-Za-z0-9+_\-]{32,}={0,2}(?![\w/\\])")

#: A private key, whole.
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                          re.S)
#: "password = ...", "api_key: ...", "token=...", "secret ...", an
#: Authorization header: the value after it.
_ASSIGNED = re.compile(
    r"(?i)\b((?:pass(?:word|wd)?|pwd|secret|api[_\- ]?key|access[_\- ]?key|"
    r"auth(?:orization)?|token|client[_\- ]?secret|private[_\- ]?key)"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s\"',;]{6,})")
_BEARER = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._\-+/=]{12,})")
#: A line of a .env file: NAME=value, the name in capitals.
_ENV_LINE = re.compile(r"(?m)^([A-Z][A-Z0-9_]{2,}\s*=\s*)(\S{8,})$")


def scrub(text: Any) -> Tuple[str, int]:
    """The text with every secret-like thing taken out, and how many."""
    text = str(text or "")
    count = 0

    def gone(match: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        return REMOVED

    def keep_name(match: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        return match.group(1) + REMOVED

    text = _PRIVATE_KEY.sub(gone, text)
    text = _ASSIGNED.sub(keep_name, text)
    text = _BEARER.sub(keep_name, text)
    text = _ENV_LINE.sub(keep_name, text)
    text = _KNOWN_KEY.sub(gone, text)

    def random_run(match: "re.Match[str]") -> str:
        nonlocal count
        run = match.group(0)
        if re.fullmatch(r"[0-9a-fA-F]+", run):
            return run
        digits = sum(ch.isdigit() for ch in run)
        letters = sum(ch.isalpha() for ch in run)
        # Words joined by underscores or dashes are a name, not a key.
        if digits < 4 or letters < 8 or re.search(r"[a-z]{4,}[_\-][a-z]{4,}", run):
            return run
        count += 1
        return REMOVED

    text = _LONG_RUN.sub(random_run, text)
    return text, count


def clean(text: Any) -> str:
    return scrub(text)[0]

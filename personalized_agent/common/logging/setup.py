"""Logging with programmatic secret redaction.

Sections 62, 74, 84 and 97 forbid credentials from ever reaching logs.  We do
not rely on developers remembering that: every log record passes through a
redaction filter, and live secrets are registered with the filter by the
credential manager itself.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import threading
from pathlib import Path
from typing import Iterable, List

_SECRET_PATTERNS: List[re.Pattern] = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|authorization)"
               r"\"?\s*[:=]\s*\"?([A-Za-z0-9_\-\.]{8,})"),
    # The ticket in a link that signs the browser in to the page (api/access.py).
    re.compile(r"(?i)(pa_sign_in)(?:=|%3D)([A-Za-z0-9_\-\.]{8,})"),
]

REDACTED = "***REDACTED***"


class SecretRegistry:
    """Live secret values that must never appear in output."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._values: set[str] = set()

    def register(self, value: str | None) -> None:
        if value and len(value) >= 6:
            with self._lock:
                self._values.add(value)

    def forget(self, value: str | None) -> None:
        if not value:
            return
        with self._lock:
            self._values.discard(value)

    def scrub(self, text: str) -> str:
        with self._lock:
            values: Iterable[str] = tuple(self._values)
        for value in values:
            if value in text:
                text = text.replace(value, REDACTED)
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(lambda m: _mask(m), text)
        return text


def _mask(match: re.Match) -> str:
    if match.re.groups >= 2:
        return f"{match.group(1)}={REDACTED}"
    return REDACTED


SECRETS = SecretRegistry()


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        scrubbed = SECRETS.scrub(message)
        if scrubbed != message:
            record.msg = scrubbed
            record.args = ()
        return True


def configure_logging(log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pragma: no cover - handler already closed
            pass

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    redaction = RedactionFilter()

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    stream.addFilter(redaction)
    root.addHandler(stream)

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "agent.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(redaction)
    root.addHandler(file_handler)

    logging.getLogger("watchdog").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

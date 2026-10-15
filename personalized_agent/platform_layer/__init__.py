"""Platform abstraction layer (section 26).

``get_platform()`` is the single place in the system that inspects
``sys.platform``.  Everything else depends on the returned interfaces.
"""

from __future__ import annotations

import sys
import threading
from typing import Optional

from .base import (  # re-exported for consumers
    ActiveWindow,
    ExecutionResult,
    Platform,
    PlatformActivityProvider,
    PlatformCapabilities,
    PlatformNotificationProvider,
    PlatformProcessProvider,
    PlatformSandboxProvider,
)

_lock = threading.Lock()
_instance: Optional[Platform] = None


def _build() -> Platform:
    if sys.platform.startswith("win"):
        from .windows import WindowsPlatform

        return WindowsPlatform()
    if sys.platform == "darwin":
        from .macos import MacPlatform

        return MacPlatform()
    from .linux import LinuxPlatform

    return LinuxPlatform()


def get_platform() -> Platform:
    global _instance
    with _lock:
        if _instance is None:
            _instance = _build()
        return _instance


__all__ = [
    "ActiveWindow",
    "ExecutionResult",
    "Platform",
    "PlatformActivityProvider",
    "PlatformCapabilities",
    "PlatformNotificationProvider",
    "PlatformProcessProvider",
    "PlatformSandboxProvider",
    "get_platform",
]

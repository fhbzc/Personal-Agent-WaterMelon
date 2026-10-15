"""Application usage provider (section 20).

Emits focus transitions with activation time, deactivation time and foreground
duration.  Window titles, process ids and executables are only included when
the platform capability map says they are available; nothing is assumed.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...common.titles import same_title
from ...common.events.models import ListenerEvent
from ...platform_layer import get_platform
from ..core.provider import ListenerProvider, PluginManifest
from ..core.manager import STOPPING_GRACE_SECONDS

log = logging.getLogger(__name__)


class ApplicationProvider(ListenerProvider):
    provider_id = "application"
    category = "application"

    def __init__(self, config: Any, platform: Any = None) -> None:
        super().__init__()
        self.config = config
        self.platform = platform or get_platform()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._current: Optional[Dict[str, Any]] = None
        self._polls = 0
        self._platform_error: Optional[str] = None

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        capabilities = self.platform.capabilities()
        if not capabilities.active_application:
            raise RuntimeError(
                "this platform does not expose the active application to the agent"
            )
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="listener-application",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=STOPPING_GRACE_SECONDS)
        self._close_current()

    def _loop(self) -> None:
        while not self._stop.is_set():
            interval = float(self.config.get("listener_poll_interval_seconds", 2.0))
            try:
                self._poll()
            except Exception as exc:  # a permission failure must not kill the listener
                self.record_error(str(exc)[:200])
            self._stop.wait(max(0.5, interval))

    # -- polling ------------------------------------------------------------
    def _poll(self) -> None:
        self._polls += 1
        window = self.platform.activity.get_active_window()
        app = window.application
        if not app:
            error = getattr(self.platform.activity, "last_error", None)
            if error and error != self._platform_error:
                self._platform_error = str(error)
                self.record_error(self._platform_error)
            return
        self._platform_error = None
        now = time.time()
        current = self._current
        # Section 19.1: a title that is only being animated is the same title.
        # Comparing it literally ends the focus period several times a second
        # while a terminal spins a glyph, and one continuous session is then
        # recorded as dozens of two-second stretches.
        if (current and current["application"] == app
                and same_title(current.get("title"), window.title)):
            return
        if current:
            self._close_current(now)
        self._current = {
            "application": app,
            "title": window.title,
            "pid": window.pid,
            "executable": window.executable,
            "started": now,
        }
        self.emit(
            ListenerEvent(
                event_type="application.focused",
                source=self.provider_id,
                application=app,
                data={
                    "window_title": window.title,
                    "pid": window.pid,
                    "executable": window.executable,
                    "activation_time": datetime.now(timezone.utc).isoformat(),
                },
                metadata={"platform": self.platform.name},
            )
        )

    def _close_current(self, now: Optional[float] = None) -> None:
        current = self._current
        if not current:
            return
        now = now or time.time()
        duration = max(0.0, now - current["started"])
        self._current = None
        self.emit(
            ListenerEvent(
                event_type="application.focus_ended",
                source=self.provider_id,
                application=current["application"],
                data={
                    "window_title": current.get("title"),
                    "pid": current.get("pid"),
                    "executable": current.get("executable"),
                    "duration_seconds": round(duration, 2),
                    "deactivation_time": datetime.now(timezone.utc).isoformat(),
                },
                metadata={"platform": self.platform.name},
            )
        )

    # -- interface ----------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "polls": self._polls,
            "current_application": (self._current or {}).get("application"),
            "last_error": self.last_error,
        }

    def supported_event_types(self) -> List[str]:
        return ["application.focused", "application.focus_ended"]

    def get_capabilities(self) -> Dict[str, Any]:
        caps = self.platform.capabilities()
        return {
            "active_application": caps.active_application,
            "active_window_title": caps.active_window_title,
            "process_identifier": caps.process_info,
            "executable_identifier": caps.process_info,
        }

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.provider_id,
            version="1.0.0",
            supported_platforms=["windows", "macos", "linux"],
            supported_event_types=self.supported_event_types(),
            required_permissions=["read_active_window"],
            configuration_schema={"listener_poll_interval_seconds": "float"},
            description="Foreground application usage tracking.",
        )

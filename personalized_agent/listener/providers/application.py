"""Application usage provider (section 20).

Emits focus transitions with activation time, deactivation time and foreground
duration.  Window titles, process ids and executables are only included when
the platform capability map says they are available; nothing is assumed.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ...common.titles import same_title
from ...common.events.models import ListenerEvent
from ...platform_layer import get_platform
from ..core.provider import ListenerProvider, PluginManifest
from ..core.manager import STOPPING_GRACE_SECONDS

log = logging.getLogger(__name__)

#: TOLD BY THE SYSTEM (the user, 2026-11-06: it took two seconds to show). Another window in front
#: is looked at this long after, so that a window only passed through while switching is not one
#: more stretch. What the system says nothing of - a browser's tab, a title - and the user being
#: away are still looked at every `listener_poll_interval_seconds`.
SETTLE_SECONDS = 0.25


class ApplicationProvider(ListenerProvider):
    provider_id = "application"
    category = "application"

    def __init__(self, config: Any, platform: Any = None) -> None:
        super().__init__()
        self.config = config
        self.platform = platform or get_platform()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._told = threading.Event()
        self._stop_watching: Any = None
        self._current: Optional[Dict[str, Any]] = None
        self._polls = 0
        self._platform_error: Optional[str] = None
        #: Whether the last look found the user away (PR #6, taken for the
        #: Monitor's signals): None until the first look, which always says.
        self._away: Optional[bool] = None

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
        self._told.clear()
        self._thread = threading.Thread(target=self._loop, name="listener-application",
                                        daemon=True)
        self._thread.start()
        self._watch_front()

    def _watch_front(self) -> None:
        watch = getattr(self.platform.activity, "watch_front", None)
        if watch is None:
            return
        try:
            self._stop_watching = watch(self._told.set)
        except Exception:                    # looked at, as where the system cannot say
            log.info("cannot be told what comes to the front", exc_info=True)

    def stop(self) -> None:
        self._stop.set()
        self._told.set()
        if self._stop_watching is not None:
            try:
                self._stop_watching()
            except Exception:
                log.debug("could not stop being told what comes to the front", exc_info=True)
            self._stop_watching = None
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
            if self._told.wait(max(0.5, interval)) and not self._stop.wait(SETTLE_SECONDS):
                self._told.clear()

    # -- polling ------------------------------------------------------------
    def _poll(self) -> None:
        self._polls += 1
        self._watch_presence()
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

    def _watch_hands(self, idle: float) -> None:
        """When the hands leave the keyboard and mouse and come back, sooner
        than being away: beside an agent at work, it tells whose work it is
        (heartbeat/monitor/windows.py). The moment only, as with presence."""
        off = idle >= float(self.config.get("listener_hands_seconds", 120))
        if off == getattr(self, "_hands_off", None):
            return
        first = getattr(self, "_hands_off", None) is None
        self._hands_off = off
        moment = datetime.now(timezone.utc)
        if off and not first:
            moment -= timedelta(seconds=idle)
        self.emit(ListenerEvent(
            event_type="presence.hands_off" if off else "presence.hands_on",
            source=self.provider_id, application=None, data={},
            metadata={"platform": self.platform.name}, timestamp=moment))

    def _watch_presence(self) -> None:
        """Say when the user leaves and when they come back (PR #6; the user,
        2026-11-03: "可以的"). A window left in front overnight is one long
        focus period; without these a break and a day's end could only be
        guessed from silence. Only the moment is recorded - never a count
        of keys or the movement of the mouse."""
        ask = getattr(self.platform.activity, "seconds_since_input", None)
        if ask is None:
            return
        try:
            idle = ask()
        except Exception:
            return
        if idle is None:
            return
        self._watch_hands(float(idle))
        limit = float(self.config.get("listener_presence_seconds", 300))
        away = idle >= limit
        if away == self._away:
            return
        first = self._away is None
        self._away = away
        # The user left when they last touched the machine, not when it was noticed.
        moment = datetime.now(timezone.utc)
        if away and not first:
            moment -= timedelta(seconds=idle)
        self.emit(ListenerEvent(
            event_type="presence.away" if away else "presence.back",
            source=self.provider_id, application=None,
            data={"idle_seconds": round(float(idle), 1)},
            metadata={"platform": self.platform.name}, timestamp=moment))

    def supported_event_types(self) -> List[str]:
        return ["application.focused", "application.focus_ended",
                "presence.away", "presence.back", "presence.hands_off", "presence.hands_on"]

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

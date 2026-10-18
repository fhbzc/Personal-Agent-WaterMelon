"""Listener Manager (section 23).

Discovery, initialization, configuration, start/stop, health checks, error
isolation and event routing.  A failing provider is disabled, never fatal
(sections 19 and 24).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List

from ...common.events.models import ListenerEvent
from ...common.credentials_guard import hide_credentials
from ...common.own_interface import scrub_sign_in
from ...common.observability.tracing import NullTracer
from ..core.privacy import PrivacyFilter
from ..core.provider import ListenerProvider

log = logging.getLogger(__name__)


#: How long to wait for a listener's own thread on the way out.
#:
#: MEASURED, on a real quit: nine seconds, of which eight were two
#: providers' join timeouts spent in full - the assistant's five and the
#: application's three. Neither thread was asleep; both wait on an event
#: and wake at once. They were inside a POLL, and a poll here is a
#: question put to the operating system - which window is in front, what
#: has this assistant written - which can take seconds and cannot be
#: interrupted from outside.
#:
#: So the grace is short, because of what is actually lost by not
#: waiting: one sample. These threads are daemons, the process is going
#: away, and the work in flight is a reading of what was on screen a
#: moment ago, not a write somebody is depending on. Waiting five seconds
#: for a sample nobody will ever see is paying the user's time for
#: nothing. What matters at shutdown - closing the focus period that is
#: open, flushing what was already captured - happens in each provider's
#: `stop` AFTER the join, and still happens whether or not the thread
#: finished.
STOPPING_GRACE_SECONDS = 0.5


class ListenerManager:
    def __init__(
        self,
        *,
        config: Any,
        raw_store: Any,
        bus: Any = None,
        observability: Any = None,
        platform: Any = None,
        tracer: Any = None,
    ) -> None:
        self.config = config
        self.tracer = tracer or NullTracer()
        self.raw_store = raw_store
        self.bus = bus
        self.observability = observability
        self.platform = platform
        self.privacy = PrivacyFilter(config, platform=platform)
        self._providers: Dict[str, ListenerProvider] = {}
        self._states: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._running = False

    # -- discovery ----------------------------------------------------------
    def discover(self) -> None:
        """Section 23 - provider discovery and initialization."""
        from ..integrations import discover_integrations
        from ..providers.application import ApplicationProvider
        from ..providers.assistant import AssistantProvider
        from ..providers.browser import BrowserProvider
        from ..providers.filesystem import FilesystemProvider
        from ..providers.screen import ScreenProvider
        from ..providers.screenshot import ScreenshotProvider

        for factory in (
            lambda: ApplicationProvider(self.config, self.platform),
            lambda: FilesystemProvider(self.config, self.platform),
            lambda: BrowserProvider(self.config, self.platform),
            lambda: AssistantProvider(self.config, self.platform),
            lambda: ScreenProvider(self.config, self.platform),
            lambda: ScreenshotProvider(self.config, self.platform),
        ):
            try:
                self.register(factory())
            except Exception:
                log.exception("provider construction failed")

        for provider in discover_integrations(self.config):
            try:
                self.register(provider)
            except Exception:
                log.exception("integration construction failed")

    def register(self, provider: ListenerProvider) -> None:
        with self._lock:
            provider.bind(self._on_event)
            self._providers[provider.provider_id] = provider
            self._states[provider.provider_id] = {
                "state": "registered", "started_at": None, "events": 0, "errors": 0,
                "last_error": None,
            }
        log.info("listener provider registered: %s (%s)", provider.provider_id, provider.category)

    # -- routing ------------------------------------------------------------
    def _on_event(self, event: ListenerEvent) -> None:
        provider = self._providers.get(event.source)
        category = provider.category if provider else "generic"
        try:
            # Before the filter: a window the user excluded from being
            # recorded is still evidence about who touched a file while it
            # was in front (section 19, as amended).
            self.privacy.remember_what_was_in_front(event)
            refused = self.privacy.refusal(event, category)
            if refused is not None:
                # Kept out - and said so in the Record log: which rule, never
                # what was in it.
                from .filtered import note
                note(event.source, category, event.event_type, refused, event.application or "")
                if self.observability is not None:
                    self.observability.increment("listener.events_filtered")
                self.tracer.event(
                    "listener.event_filtered", event_type=event.event_type,
                    status="skipped", connection_id="listener_manager->event_pipeline",
                    outputs="blocked by the privacy filter",
                )
                return
            # A link that signs the browser in to the agent's own page carries
            # a ticket (api/access.py). The browser's history is read with
            # this application's pages left out, but a window title, an
            # integration's event, a path could still hold one: cut out
            # here, where every provider's event passes, before anything is
            # kept. Spent within a second of being made - and still never
            # written down.
            event.data = scrub_sign_in(event.data)
            event.metadata = scrub_sign_in(event.metadata)
            event.application = scrub_sign_in(event.application)
            # Passwords and codes, cut out by keywords and shapes - never
            # by a model (common/credentials_guard.py). The tickets above are
            # cut out whatever this switch says: they are the agent's own.
            if self.config is None or self.config.get("listener_hide_credentials", True):
                event.data = hide_credentials(event.data)
                event.metadata = hide_credentials(event.metadata)
                event.application = hide_credentials(event.application)
            self.tracer.event(
                "listener.event", event_type=event.event_type,
                connection_id="listener_manager->event_pipeline",
                inputs={"source": event.source, "application": event.application},
                metadata={"category": category, "kind": "listener"},
            )
            self.raw_store.add_event(event)
            self.tracer.event(
                "event_pipeline.persist", event_type=event.event_type,
                connection_id="event_pipeline->raw_event_storage",
                outputs=f"raw event {event.id}",
            )
            with self._lock:
                state = self._states.setdefault(event.source, {"events": 0})
                state["events"] = state.get("events", 0) + 1
            if self.observability is not None:
                self.observability.increment("listener.events_recorded")
            if self.bus is not None:
                self.bus.publish(f"listener.{event.event_type}", event.to_dict())
        except Exception:
            log.exception("failed to route listener event %s", event.event_type)
            if self.observability is not None:
                self.observability.record_error("listener", f"routing failed for {event.event_type}")

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if not self._providers:
            self.discover()
        self._running = True
        for provider_id, provider in list(self._providers.items()):
            self._start_provider(provider_id, provider)

    def _start_provider(self, provider_id: str, provider: ListenerProvider) -> None:
        if not self.privacy.category_enabled(provider.category):
            self._states[provider_id].update({"state": "disabled_by_settings"})
            return
        try:
            provider.start()
            self._states[provider_id].update(
                {"state": "running", "started_at": time.time(), "last_error": None}
            )
        except Exception as exc:  # error isolation
            log.exception("listener provider %s failed to start", provider_id)
            state = self._states.setdefault(provider_id, {})
            state.update({"state": "failed", "last_error": str(exc)[:300]})
            state["errors"] = state.get("errors", 0) + 1
            if self.observability is not None:
                self.observability.record_error("listener", f"{provider_id} failed to start",
                                                str(exc)[:300])

    def stop(self) -> None:
        # PER PROVIDER, TIMED. Every provider waits on an event, so a stop
        # wakes the ones that are asleep - which means any time spent here
        # is a provider in the middle of WORK: a window query that has to
        # cross into another process, a directory walk, a capture. Each
        # one has its own join timeout of three to five seconds, so one
        # that cannot be woken costs seconds of a quit, and until this was
        # measured the whole of it was invisible: the manager logged
        # nothing, and the seconds were read off the gap before the next
        # component's line and blamed on that.
        import time as _time

        self._running = False
        spent = {}
        for provider_id, provider in list(self._providers.items()):
            began = _time.perf_counter()
            try:
                provider.stop()
                self._states[provider_id].update({"state": "stopped"})
            except Exception:
                log.exception("listener provider %s failed to stop", provider_id)
            spent[provider_id] = round(_time.perf_counter() - began, 3)
        slow = {name: held for name, held in spent.items() if held >= 0.05}
        log.info("listeners stopped in %.3fs%s", sum(spent.values()),
                 (": " + ", ".join(f"{name} {held:.3f}s" for name, held
                                   in sorted(slow.items(),
                                             key=lambda one: -one[1])))
                 if slow else " (all of them at once)")
        self._stop_spent = spent

    def apply_settings(self) -> None:
        """Re-evaluate category toggles after a settings change."""
        if not self._running:
            return
        for provider_id, provider in list(self._providers.items()):
            enabled = self.privacy.category_enabled(provider.category)
            state = self._states.get(provider_id, {}).get("state")
            if enabled and state not in ("running",):
                self._start_provider(provider_id, provider)
            elif not enabled and state == "running":
                try:
                    provider.stop()
                    self._states[provider_id].update({"state": "disabled_by_settings"})
                except Exception:
                    log.exception("failed to stop provider %s", provider_id)
            elif enabled and state == "running":
                # Still on, and something it reads may have moved. A provider
                # that only reads its settings at startup otherwise answers a
                # change by ignoring it.
                try:
                    provider.settings_changed()
                except Exception:
                    log.exception("provider %s could not apply the new settings",
                                  provider_id)

    # -- health -------------------------------------------------------------
    def health(self) -> Dict[str, Any]:
        providers = []
        for provider_id, provider in self._providers.items():
            state = dict(self._states.get(provider_id, {}))
            try:
                status = provider.get_status()
            except Exception as exc:
                status = {"error": str(exc)[:200]}
            providers.append(
                {
                    "provider_id": provider_id,
                    "category": provider.category,
                    "manifest": provider.manifest().to_dict(),
                    "capabilities": _safe_call(provider.get_capabilities, {}),
                    "supported_event_types": _safe_call(provider.supported_event_types, []),
                    "status": status,
                    **state,
                }
            )
        return {
            "running": self._running,
            "privacy": self.privacy.describe(),
            "providers": providers,
        }

    def provider_ids(self) -> List[str]:
        return list(self._providers)


def _safe_call(fn, default):
    try:
        return fn()
    except Exception:
        return default

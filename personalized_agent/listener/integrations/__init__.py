"""Listener plugin discovery (section 24).

One ingest endpoint ships by default:

``app_integration``
    application specific plugins - the VS Code extension, a custom desktop
    application - pushing ``integration.*`` events.

Third party plugins are added by dropping a module into this package that
exposes ``create_providers(config) -> list[ListenerProvider]``.  A failure in
one plugin never affects the others.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any, List

from ..core.provider import ListenerProvider
from .ingest import IngestProvider

log = logging.getLogger(__name__)


def builtin_providers(config: Any) -> List[ListenerProvider]:
    return [
        IngestProvider(
            config,
            provider_id="app_integration",
            category="integration",
            allowed_prefixes=["integration."],
            description="Application integrations such as the VS Code extension.",
        ),
    ]


def discover_integrations(config: Any) -> List[ListenerProvider]:
    providers: List[ListenerProvider] = builtin_providers(config)
    for module_info in pkgutil.iter_modules(__path__):
        if module_info.name in ("ingest",):
            continue
        try:
            module = importlib.import_module(f"{__name__}.{module_info.name}")
        except Exception:
            log.exception("listener plugin %s failed to import", module_info.name)
            continue
        factory = getattr(module, "create_providers", None)
        if factory is None:
            continue
        try:
            providers.extend(factory(config) or [])
        except Exception:
            log.exception("listener plugin %s failed to create providers", module_info.name)
    return providers


__all__ = ["IngestProvider", "discover_integrations", "builtin_providers"]

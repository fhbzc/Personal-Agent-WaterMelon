"""Single source of the application version (section 105).

The same value must appear in executable metadata, the application UI, installer
metadata, logs, the Debugger and build artifacts, so every one of those reads it
from here.
"""

from __future__ import annotations

APP_NAME = "Personalized Agent"
APP_SLUG = "personalized-agent"
APP_ID = "com.personalizedagent.app"
APP_DIR_NAME = "PersonalizedAgent"
PUBLISHER = "Personalized Agent Project"

__version__ = "1.0.0"
VERSION = __version__
VERSION_TUPLE = tuple(int(part) for part in __version__.split("."))


def version_info() -> dict:
    return {
        "name": APP_NAME,
        "version": __version__,
        "application_id": APP_ID,
    }

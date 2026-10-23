"""Single source of the application version (section 105).

The same value must appear in executable metadata, the application UI, installer
metadata, logs, the Debugger and build artifacts, so every one of those reads it
from here.
"""

from __future__ import annotations

APP_NAME = "Personal Agent WaterMelon"
#: What it was called before (to 1.0.0): its own windows, recorded under
#: that name, are still its own.
FORMER_NAMES = ("Personalized Agent",)
APP_SLUG = "personalized-agent"
APP_ID = "com.personalizedagent.app"
#: The name as a file is named - the program, its downloads, the folders it
#: makes by default: the name without its spaces.
FILE_NAME = "PersonalAgentWaterMelon"
#: As the program's files were named before (to 1.0.0): still known as its own.
FORMER_FILE_NAMES = ("PersonalizedAgent",)
#: A Linux package's name, and its folder under /opt.
PACKAGE_NAME = "personal-agent-watermelon"
FORMER_PACKAGE_NAMES = ("personalized-agent",)
#: The data folder made by default in the home folder - and the one made
#: before (to 1.0.0), which is still used where it is there and this is not.
APP_DIR_NAME = FILE_NAME
FORMER_DIR_NAMES = ("PersonalizedAgent",)
PUBLISHER = "Personal Agent WaterMelon Project"

__version__ = "0.2.2"
VERSION = __version__
VERSION_TUPLE = tuple(int(part) for part in __version__.split("."))


def version_info() -> dict:
    return {
        "name": APP_NAME,
        "version": __version__,
        "application_id": APP_ID,
    }

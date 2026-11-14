"""Single source of the application version (section 105).

The same value must appear in executable metadata, the application UI, installer
metadata, logs, the Debugger and build artifacts, so every one of those reads it
from here.
"""

from __future__ import annotations

APP_NAME = "Personal Agent WaterMelon"
#: What it was called before (to 1.0.0): a program of that name is still its own.
FORMER_NAMES = ("Personalized Agent",)
#: THE PAGE'S OWN TITLE, alone or with its browser's name after it. Exactly: "watermelon" among the
#: names would have left out of the record a page the user reads about the fruit.
PAGE_TITLE = "WaterMelon"
#: ITS PAGES, EXACTLY, AND NOTHING THAT ONLY HOLDS ITS NAME (the user, 2026-11-06): a call named after
#: the project, "Meet - Personalized Agent", went unread for 42 minutes as the agent's own window.
OWN_TITLES = (PAGE_TITLE, f"Conversation - {APP_NAME}", f"Delegate work - {APP_NAME}",
              f"Learn - {APP_NAME}")
BROWSERS = ("Google Chrome", "Mozilla Firefox", "Microsoft Edge", "Microsoft\u200b Edge", "Chromium",
            "Safari", "Brave", "Opera")


def names_the_agent(application: str, title: str) -> bool:
    """Whether a window is the agent's own, by what it is called."""
    import re
    program = re.sub(r"\.(exe|app)$", "", str(application or "").strip(), flags=re.IGNORECASE)
    if program.lower() in {name.lower() for name in (APP_NAME, *FORMER_NAMES)}:
        return True
    # A browser may put the address of a page on this machine before its title.
    said = re.sub(r"^https?://(?:127\.0\.0\.1|localhost)(?::\d+)? [-\u2013\u2014] ", "",
                  str(title or "").strip())
    browser = r"(?: [-\u2013\u2014] (?:" + "|".join(re.escape(one) for one in BROWSERS) + "))?"
    return any(re.fullmatch(re.escape(own) + browser, said) for own in OWN_TITLES)
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

__version__ = "0.3.4"
VERSION = __version__
VERSION_TUPLE = tuple(int(part) for part in __version__.split("."))


def version_info() -> dict:
    return {
        "name": APP_NAME,
        "version": __version__,
        "application_id": APP_ID,
    }

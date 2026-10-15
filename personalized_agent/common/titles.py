"""Window titles, compared for what they mean rather than character by character.

A title is the only thing most applications tell the system about what the user
is doing, and it is written to be looked at rather than compared. Programs
animate them: a terminal running a task rewrites its own title several times a
second to spin a glyph at the front of it.

Compared literally, every frame of that animation is a different title. That
matters twice over:

* the Listener ends a focus period when the title changes, so one continuous
  session becomes dozens of two-second fragments, each recorded as a separate
  stretch of work;
* the Overview lists those fragments, so a morning at the terminal reads as a
  hundred entries of a few seconds each.

Both follow from the same mistake, so both are fixed by the same function.
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import Optional

__all__ = ["normalise_title", "same_title", "about_this_file"]


def normalise_title(title: str) -> str:
    """A window title with its animation stripped off the front.

    The leading run is judged by character category rather than by a list of
    known glyphs: a symbol at the front of a title is decoration, and the list
    of symbols people animate with is not one worth maintaining - the first
    attempt at this enumerated braille frames and half-circles, and the very
    next program used a star.
    """
    text = str(title or "")
    index = 0
    while index < len(text):
        character = text[index]
        if character.isspace() or unicodedata.category(character).startswith(("S", "C")):
            index += 1
            continue
        break
    return text[index:].strip()


def same_title(left: str, right: str) -> bool:
    """Whether two titles say the same thing about what the user is doing."""
    return normalise_title(left) == normalise_title(right)



# ---------------------------------------------------------------------------
# Whether a window was about a particular file (section 19, as amended)
# ---------------------------------------------------------------------------

#: How short a directory name may be and still count as naming something.
MEANINGFUL_NAME = 4

#: Directory names that name nothing in particular.
#:
#: The folder rule asks whether a folder above the file appears in the title,
#: and these appear in titles that have nothing to do with the file: a path
#: containing `agent` matched "Personalized Agent - Mozilla Firefox", which
#: is how a source file an assistant was reading came to be recorded as the
#: user's. They are too common to be evidence and too useful to forbid as
#: folder names, so they simply do not count towards this one test.
SAYS_NOTHING = frozenset({
    "agent", "app", "apps", "api", "bin", "build", "cache", "code", "core",
    "data", "dist", "docs", "file", "files", "home", "lib", "libs", "main",
    "misc", "new", "out", "output", "project", "projects", "public", "run",
    "share", "site", "source", "src", "temp", "test", "tests", "tmp", "user",
    "users", "util", "utils", "web", "work",
})


def about_this_file(path: Optional[str], title: Optional[str],
                    application: Optional[str] = None) -> bool:
    """Whether the window showing that title was about this file.

    Two pieces of evidence of different strength, weighed differently because
    they fail differently.

    **The file's own name** is the clear case: every editor puts it in the
    title bar, which is what makes this work without knowing which editor it
    is.

    **A folder above it** is what saves the rest - a build started from a
    terminal whose title names the project writes a hundred files the title
    never mentions. It is weak, and it has failed in four distinct ways, all
    of them found in a real record rather than by reading the code:

    - a folder named after the application in front. `firefox` matched
      "Mozilla Firefox" every time Firefox was open, so a browser checking
      its own extensions was filed as the user reading them
    - the **file** named after the application, which is the same clash
      through the stronger test: the directory `.../browser/firefox` has
      `firefox` as its own name
    - a title that is itself a path, which has already named its file. An
      editor showing one file then vouched for every file in the project
    - a folder with a name that names nothing: `agent`, `src`, `docs`

    So: a name carrying an extension is compared against the title as it is,
    because `firefox.html` open in Firefox is a real file and its name
    appearing in full is evidence of its own. Everything else is compared
    against the title with the application's own name taken out of it.
    """
    text = str(title or "").lower()
    if not text:
        return False
    name = os.path.basename(str(path or "")).lower()
    without_us = _without_the_applications_own_name(text, application)
    if name:
        _stem, extension = os.path.splitext(name)
        # A name with an extension is a filename. A name without one is a
        # directory - or an application.
        if name in (text if extension else without_us):
            return True
    if _is_itself_a_path(text):
        # It named a file, and not this one. Guessing from folders when the
        # title was precise is how one file came to vouch for a whole tree.
        return False
    for part in Path(str(path or "")).parts[:-1]:
        folder = part.strip("\\/:").lower()
        if len(folder) < MEANINGFUL_NAME or folder in SAYS_NOTHING:
            continue
        if folder in without_us:
            return True
    return False


def _without_the_applications_own_name(title: str,
                                       application: Optional[str]) -> str:
    """The title with the application's own name removed.

    Every window carries it, so it says nothing about what the window is
    showing - and anything that happens to share it then matches every time
    that application is in front. Names of one or two letters are left alone:
    removing them would cut ordinary words to pieces.
    """
    stem = os.path.splitext(os.path.basename(str(application or "")))[0]
    stem = stem.strip().lower()
    if len(stem) < 3:
        return title
    return title.replace(stem, " ")


def _is_itself_a_path(title: str) -> bool:
    """Whether the title names a file outright rather than a context.

    Some editors put the whole path in the title bar. That is the most
    precise thing a window can say about what it is showing, and the folder
    rule exists for the opposite case - a title naming only the project, or
    only the command, and leaving the file unsaid.
    """
    return ("\\" in title or "/" in title) and ":" in title

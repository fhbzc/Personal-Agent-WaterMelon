"""The languages the agent works in (section 12.2, as amended).

One setting, one meaning: the language the user works in. It decides what the
agent replies in, and - the part that is new - **what language the memory is
findable in**.

Why the second follows from the first
-------------------------------------
Records are written by a model, and a model writes them in whatever language
the material and the prompt suggest, which in practice is English. Keywords are
what search matches on. So a user who asks questions in Chinese was searching
an English index with Chinese words: measured on a real installation, 5.5% of
records carried any Chinese at all, and "最近在忙什么" matched nothing
whatsoever - not an error, not a warning, just an answer assembled without the
memory it should have had.

Translating the records is the wrong fix. What they say should stay exactly as
it was written, in the language it was written in; it is only the way *in* that
has to exist in the user's language. So records carry **keywords in more than
one language**, and the index holds all of them.

Eight, and why a limit at all
-----------------------------
Every additional language is more keywords on every record, a larger index, and
more of each round's reply spent on them. Eight is enough to cover the
languages a desktop user is realistically working in, and small enough that the
cost of carrying the choice stays bounded.

The default is the system's own language when it is one of these, because an
agent that has to be configured before it can find anything is one that looks
broken on first use.
"""

from __future__ import annotations

import locale
import logging
import os
import sys
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

#: What the agent works in. The code is what is stored; the name is what the
#: user sees; ``endonym`` is what a speaker of it calls it, which is what makes
#: a language list usable by the person who needs that entry.
LANGUAGES: Dict[str, Dict[str, str]] = {
    "en": {"name": "English", "endonym": "English"},
    "zh": {"name": "Chinese", "endonym": "中文"},
    "ja": {"name": "Japanese", "endonym": "日本語"},
    "ko": {"name": "Korean", "endonym": "한국어"},
    "es": {"name": "Spanish", "endonym": "Español"},
    "fr": {"name": "French", "endonym": "Français"},
    "de": {"name": "German", "endonym": "Deutsch"},
    "it": {"name": "Italian", "endonym": "Italiano"},
    "ru": {"name": "Russian", "endonym": "Русский"},
}

CODES = tuple(LANGUAGES)

#: THE LANGUAGES THE AGENT SPEAKS TO THE USER IN, for now: what the display
#: language may be set to. Another, set before, is read as English.
DISPLAY_CODES = ("zh", "en", "es", "ja", "ko", "ru", "fr", "de", "it")

#: What records are written in when nothing says otherwise. Keywords are always
#: carried in this as well as in the user's language, so that a record stays
#: findable by the words it is actually written in - and so that changing the
#: setting never makes anything unreachable.
BASE = "en"

DEFAULT = BASE


def normalise(code: Optional[str]) -> str:
    """A supported code, from whatever was given.

    Accepts the forms a locale actually arrives in - ``zh_CN``, ``zh-Hans``,
    ``en_GB.UTF-8`` - because rejecting them would mean a system language of
    ``zh_CN`` silently becoming English.
    """
    text = str(code or "").strip().lower().replace("_", "-")
    if not text:
        return DEFAULT
    primary = text.split(".")[0].split("-")[0]
    return primary if primary in LANGUAGES else DEFAULT


def _system_locale() -> str:
    """The machine's language as a locale name - ``zh_CN``, ``en_US``.

    On Windows, the language Windows itself is shown in (its UI language),
    which is the user's choice rather than the formats' region. Elsewhere,
    what the environment says, then the C library's locale. Not
    `locale.getdefaultlocale`, which is deprecated and leaves in Python 3.15.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            name = locale.windows_locale.get(lcid)
            if name:
                return name
        except Exception:        # pragma: no cover - platform dependent
            pass
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(variable, "").split(":")[0]
        if value and value not in ("C", "POSIX") and not value.startswith("C."):
            return value
    try:
        return locale.getlocale()[0] or ""
    except Exception:            # pragma: no cover - platform dependent
        return ""


def system_language() -> str:
    """What the machine is set to, if the agent speaks it."""
    return normalise(_system_locale())


def name_of(code: str) -> str:
    entry = LANGUAGES.get(normalise(code)) or LANGUAGES[DEFAULT]
    return entry["name"]


def label_of(code: str) -> str:
    """How the setting lists it: the English name and the endonym."""
    code = normalise(code)
    entry = LANGUAGES[code]
    if entry["name"] == entry["endonym"]:
        return entry["name"]
    return f"{entry['name']} ({entry['endonym']})"


def display_language(config: Any) -> str:
    """The language everything the user reads is written in.

    Separate from ``language`` because the two answer different questions.
    ``language`` is what the *memory* is findable in - which words are
    attached to a record so a search in the user's own phrasing reaches it.
    This is what the user is *shown*: the agent's replies, the notes it
    raises of its own accord, everything it composes for them to read.

    They are usually the same and do not have to be. Someone who keeps their
    machine in English and thinks in Chinese wants to be spoken to in Chinese;
    someone reading a shared installation wants the reverse. Defaults to the
    system's language, because an agent that has to be configured before it
    speaks to you comprehensibly looks broken on first use.
    """
    if config is None:
        return system_language()
    try:
        chosen = config.get("display_language", None)
    except Exception:            # pragma: no cover - a missing setting is a default
        chosen = None
    if not chosen:
        try:
            chosen = config.get("language", None)
        except Exception:        # pragma: no cover
            chosen = None
    code = normalise(chosen) if chosen else system_language()
    return code if code in DISPLAY_CODES else "en"


def default_display() -> str:
    """The display language a new installation starts with: the machine's,
    when the agent speaks it to the user - else English."""
    code = system_language()
    return code if code in DISPLAY_CODES else "en"


def display_instruction(config: Any) -> str:
    """The line appended to any prompt whose output the user will read.

    Every such prompt has to carry it. A model writes in whatever language the
    material suggests, so a note composed from English source material comes
    out in English however the application is configured - and the user is
    left reading half their agent in a language they did not choose.
    """
    return (f"Write everything the user will read in {name_of(display_language(config))}. "
            f"This applies to the whole of your reply, including headings, "
            f"labels and any list you write. Quoted material, file paths, "
            f"identifiers and code keep their original wording.")


#: The languages a record's keywords are written in, for now.
#:
#: Fixed rather than chosen, and fixed at two. The machinery underneath takes
#: any list - ``keyword_languages`` still returns one, the templates say their
#: own words in each of them, the relabeller works through whatever is missing
#: - so this is a decision about what is *carried*, not a limit in the design.
#:
#: Two because every additional language is more keywords on every record, a
#: larger index, and more of each background round spent writing them; and
#: because a translation nobody checked is worse than none. The template
#: vocabulary is written out by hand (section 48.1) and the words in it have
#: to be right in every language listed here, which is a promise that gets
#: harder to keep with each one added.
#:
#: English because that is the language a model writes records in whatever it
#: is asked in, so it is what the record's own text is findable by - and the
#: one language every keyword is written and searched in: one, because a
#: word is found only as it is written.
PINNED_LANGUAGES: List[str] = ["en"]

#: How many languages a record's keywords may be written in.
#:
#: Every additional one is more keywords on every record, a larger index, and
#: more of each round's reply spent producing them. Eight covers the languages
#: a desktop user is realistically working in and keeps the cost of the choice
#: bounded (section 12.2).
MAX_KEYWORD_LANGUAGES = 8


def chosen_languages(value: Any) -> List[str]:
    """The languages a setting names, in the order they were chosen.

    Accepts what the setting actually holds - a comma-separated string from
    the multi-select, a list, or a single code from before it could be more
    than one - because all three exist on disk somewhere.
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = [piece.strip() for piece in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        parts = [str(piece).strip() for piece in value]
    else:
        parts = [str(value).strip()]
    out: List[str] = []
    for piece in parts:
        if not piece:
            continue
        code = normalise(piece)
        if code not in out:
            out.append(code)
    return out[:MAX_KEYWORD_LANGUAGES]


def keyword_languages(value: Any) -> List[str]:
    """The languages a record's keywords should be written in.

    Two, fixed: the language records are written in, and the language the
    user asks in. The first keeps a record findable by quoting it; the second
    is how they will actually look for it.

    Fixed rather than chosen because the template vocabulary is written out by
    hand and has to be right in every language it claims, and a translation
    nobody checked is worse than none. ``PINNED_LANGUAGES`` is the whole of
    that decision.

    More than one because a person is not monolingual on a schedule. Someone
    who writes error reports in Chinese and reads papers in English searches
    in both, and a record indexed only under one of them is missing exactly
    half the time - which is not an error anyone sees, only an answer
    assembled without the memory it should have had.
    """
    # Fixed for now, whatever is stored. The argument is still taken and the
    # rest of the path still handles a list of any length, so lifting this is
    # a one-line change rather than a redesign.
    del value
    return list(PINNED_LANGUAGES)


# -- which language a model writes in: said in one way, at the call ---------------------------
#: What a model call's output - or part of it - is written in.
DISPLAY, INTERNAL = "display", "internal"
#: The whole of what the user will read.
EVERYTHING_READ = "everything the user will read"


class Language:
    """WHICH LANGUAGE A MODEL WRITES IN, said at the call that asks it - and
    nowhere else: `ModelRequest(..., language=in_display(...) + in_english(...))`.

    The models manager turns it into the instruction the model is given
    (`instruction`), and records it with the call (`rules`); the Language
    debugger reads it off the source. A prompt that says its own language
    in its own words is a prompt the debugger cannot see.
    """

    __slots__ = ("parts",)

    def __init__(self, parts: Any = ()) -> None:
        self.parts = tuple(parts)

    def __add__(self, other: "Language") -> "Language":
        return Language(self.parts + tuple(other.parts))

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Language) and self.parts == other.parts

    def __hash__(self) -> int:
        return hash(self.parts)

    def __repr__(self) -> str:
        return " + ".join(f"in_{'english' if rule == INTERNAL else 'display'}({what!r})"
                          for rule, what in self.parts) or "Language()"

    def rules(self) -> list:
        """What it says, as rows: [{"rule", "what"}]."""
        return [{"rule": rule, "what": what} for rule, what in self.parts]

    def compose(self, system: Any, config: Any) -> str:
        """THE SYSTEM PROMPT AS SENT: its language said FIRST, whole - the
        display language and every exception to it - and then the prompt.
        A rule after thousands of characters of English prose and English
        worked examples is one the examples outvote: a model shown twenty
        good English summaries writes English, whatever the last sentence
        says; and an exception said after a rule it qualifies is read after
        the rule has been taken."""
        return "\n\n".join(part for part in (self.instruction(config), str(system or ""))
                           if part)

    def instruction(self, config: Any) -> str:
        """The words the model is given: the display language named, what is
        written in it, and - straight after, as exceptions to it - what is
        written in English; English alone said as such."""
        shown = [what for rule, what in self.parts if rule == DISPLAY]
        kept = [what for rule, what in self.parts if rule == INTERNAL]
        lines = []
        if shown:
            line = (f"WRITE IT IN THIS LANGUAGE: {name_of(display_language(config))}. "
                    + display_instruction(config))
            named = [what for what in shown if what != EVERYTHING_READ]
            if named:
                line += f" That is: {'; '.join(named)}."
            lines.append(line)
        if kept:
            what = "; ".join(kept)
            if shown:
                lines.append(
                    f"EXCEPTION, for {what}: write them in English, whatever the line above "
                    f"says. They are kept for the agent's own use and compared with one "
                    f"another rather than only read, and that comparison is done in one "
                    f"language. Quoted material - a file path, a page title, a line the "
                    f"user wrote - keeps its own wording inside the English sentence "
                    f"around it.")
            else:
                lines.append(
                    f"Write {what} in English, whatever language the material is in: it is "
                    f"kept for the agent's own use, not shown to the user as it is. Quoted "
                    f"material - a file path, a page title, a line the user wrote - keeps "
                    f"its own wording.")
        return "\n\n".join(lines)


def needs_english_copy(config: Any) -> bool:
    """Whether what the user reads needs an English copy beside it: only
    when it is not written in English already. What is compared and
    measured is read in English (the embedding model reads English only);
    when the user reads English, the words they read are that copy."""
    return display_language(config) != "en"


def in_display(what: str = EVERYTHING_READ) -> Language:
    """These parts of the output in the user's display language."""
    return Language(((DISPLAY, str(what)),))


def in_english(what: str) -> Language:
    """These parts of the output in English: kept for the agent's own use."""
    return Language(((INTERNAL, str(what)),))


__all__ = ["LANGUAGES", "CODES", "BASE", "DEFAULT", "normalise",
           "system_language", "name_of", "label_of", "keyword_languages",
           "chosen_languages", "display_language", "display_instruction",
           "MAX_KEYWORD_LANGUAGES", "PINNED_LANGUAGES", "Language", "in_display",
           "in_english", "DISPLAY", "INTERNAL", "EVERYTHING_READ", "needs_english_copy"]

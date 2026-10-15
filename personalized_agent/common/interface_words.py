"""THE DESKTOP'S OWN WORDS IN THE DISPLAY LANGUAGE.

The avatar's menu, the tray's, the notification's buttons and the button
by a selection say what the page says, in the same words: they read the
page's tables (api/web/lang/<code>.json - English on the left, the
language's own words on the right; api/web/i18n.js reads them for the
page). A text that is exactly an entry is replaced; one made of an entry's
words around a number ({0}, {1}...) is matched to it, the number carried
over. Anything else - and everything, in English - is shown as written.
A text put together from pieces is put together in the language: its
entry, with numbered places, is filled with the pieces (``fill``).
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from .languages import display_language
from .paths import resource_path

log = logging.getLogger(__name__)

_SLOT = re.compile(r"\{(\d+)\}")


@lru_cache(maxsize=16)
def _raw(code: str) -> Dict[str, str]:
    path = resource_path("api", "web", "lang", f"{code}.json")
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        if code != "en":
            log.debug("no interface table for %s", code)
        return {}
    return table if isinstance(table, dict) else {}


Pattern = Tuple[List[str], List[str], str]          # its words, its places, the language's


@lru_cache(maxsize=16)
def _table(code: str) -> Tuple[Dict[str, str], List[Pattern]]:
    table = _raw(code)
    exact: Dict[str, str] = {}
    patterns: List[Pattern] = []
    for key, value in table.items():
        if not isinstance(value, str):
            continue
        if not _SLOT.search(key):
            exact[key] = value
            continue
        parts = _SLOT.split(key)[::2]
        if len(re.sub(r"[^\w]|\d|_", "", "".join(parts))) < 3:
            continue                     # too few words of its own
        patterns.append((parts, _SLOT.findall(key), value))
    patterns.sort(key=lambda p: -len("".join(p[0])))       # the most particular first
    return exact, patterns


def _matched(parts: List[str], text: str) -> Optional[List[str]]:
    """The words found in order, from the left, each place what lies between
    them - in one pass, as the page does it (api/web/i18n.js)."""
    first, last = parts[0], parts[-1]
    if not text.startswith(first) or not text.endswith(last):
        return None
    end, at, found = len(text) - len(last), len(first), []
    for part in parts[1:-1]:
        nxt = text.find(part, at)
        if nxt < 0 or nxt > end:
            return None
        found.append(text[at:nxt])
        at = nxt + len(part)
    if at > end:
        return None
    found.append(text[at:end])
    return found


def translated(text: str, code: str, _depth: int = 0) -> str:
    """``text``, one of the application's own, in the display language ``code`` - and what
    fills its places too, when that is one of them."""
    if not text or code == "en":
        return text
    exact, patterns = _table(code)
    key = " ".join(str(text).split())
    if key in exact:
        return exact[key]
    for parts, order, value in patterns:
        said = _matched(parts, key)
        if said is not None:
            def place(m: "re.Match[str]") -> str:
                if m.group(1) not in order:
                    return ""
                one = said[order.index(m.group(1))]
                bare = one.strip()
                if not bare or _depth >= 2:
                    return one
                return one.replace(bare, translated(bare, code, _depth + 1))
            return _SLOT.sub(place, value)
    return text


def fill(key: str, code: str, *values: Any) -> str:
    """The entry ``key`` - words with numbered places, "in {0}m" - in
    the display language ``code``, its places filled with ``values`` in order."""
    template = key if code == "en" else (_raw(code).get(key) or key)
    return _SLOT.sub(lambda m: (str(values[int(m.group(1))])
                                if int(m.group(1)) < len(values) else ""), template)


def shown(text: str, config: Any) -> str:
    """``text`` in the display language ``config`` names."""
    return translated(text, display_language(config))

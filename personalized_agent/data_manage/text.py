"""Turning text into the pieces worth searching for.

Lifted out of the graph store when the graph was deprecated. It was never
about graphs: it is about the fact that a question arrives as a sentence and
the thing being searched is also prose, and something has to decide what the
overlap between them is.

The hard part is that the answer differs by script. Splitting on whitespace is
right for languages that put spaces between words and useless for those that
do not - a Chinese question arrives as a single token, nothing stored contains
that whole sentence, and the search returns nothing while looking like it
worked. This user asks in Chinese most of the time, so that is not an edge
case here.
"""

from __future__ import annotations

import re
from typing import List

#: Scripts written without spaces between words.
CONTINUOUS_SCRIPTS = (
    ("一", "鿿"),      # Chinese
    ("぀", "ヿ"),      # Japanese kana
    ("가", "힯"),      # Korean
)

#: How many terms one query contributes by default. More is slower and not
#: more accurate: past a handful the extra terms match everything.
MAX_SEARCH_TERMS = 6

#: Words too common to search on. Without this a query built by counting
#: frequency is built out of "the" and "and", which matches everything and
#: therefore distinguishes nothing.
COMMON_WORDS = frozenset("""
a an the and or but if of in on at to for from with by as is are was were be
been being it its this that these those there here then than so not no yes
i you he she they we me him her them us my your his their our new update
""".split())

_WORD = re.compile(r"[\w']+", re.UNICODE)


def _continuous(character: str) -> bool:
    return any(low <= character <= high for low, high in CONTINUOUS_SCRIPTS)


def tokenize(text: str) -> List[str]:
    return _WORD.findall(str(text or "").lower())


def estimate_tokens(text: str) -> int:
    """Roughly how much of a context window something will take."""
    return max(1, len(str(text or "")) // 4)


def search_terms(query: str, limit: int = MAX_SEARCH_TERMS) -> List[str]:
    """The pieces of a query worth looking for in stored text.

    Words are used where there are words. Where there are not, overlapping
    pairs of characters are used instead: a pair is short enough to occur in
    stored text and long enough to mean something, which is the usual way to
    search these scripts without a dictionary to segment them.
    """
    text = str(query or "").lower()
    terms: List[str] = []
    run: List[str] = []

    def flush_run() -> None:
        if not run:
            return
        block = "".join(run)
        run.clear()
        if len(block) == 1:
            terms.append(block)
            return
        for index in range(len(block) - 1):
            terms.append(block[index:index + 2])

    for character in text:
        if _continuous(character):
            run.append(character)
            continue
        flush_run()
        terms.append(" " if character.isspace() else character)
    flush_run()

    # Re-join the runs of ordinary characters into words.
    words: List[str] = []
    current: List[str] = []
    for term in terms:
        if len(term) == 1 and not _continuous(term):
            if term == " ":
                if current:
                    words.append("".join(current))
                    current = []
            else:
                current.append(term)
        else:
            if current:
                words.append("".join(current))
                current = []
            words.append(term)
    if current:
        words.append("".join(current))

    seen: List[str] = []
    for word in words:
        word = word.strip()
        if len(word) < 2 or word in seen:
            continue
        seen.append(word)
        if len(seen) >= limit:
            break
    return seen


def salient_terms(texts, limit: int = MAX_SEARCH_TERMS) -> List[str]:
    """The terms a batch of text keeps coming back to.

    ``search_terms`` keeps the first terms it is handed, which is right for a
    question someone typed and wrong for a pile of activity - the first six
    words of the oldest item are not what the pile is about. Counting across
    the pile is.
    """
    counts = {}
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            continue
        seen = set()
        for term in search_terms(text):
            term = term.strip("{}[]()<>\"',.:;=|/\\-_")
            if len(term) < 2 or not any(c.isalnum() for c in term):
                continue
            if term in COMMON_WORDS or term in seen:
                continue
            seen.add(term)
            counts[term] = counts.get(term, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    return [term for term, _ in ranked[:limit]]


__all__ = ["search_terms", "salient_terms", "tokenize", "estimate_tokens",
           "MAX_SEARCH_TERMS", "COMMON_WORDS", "CONTINUOUS_SCRIPTS"]

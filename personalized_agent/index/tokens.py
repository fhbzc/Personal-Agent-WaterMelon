"""THE WORDS A TEXT IS FOUND BY: written the same way for what is kept and
for what is asked.

Latin letters and digits, lowercase, in words of two characters or more.
Chinese, Japanese and Korean, which put no space between words, in pairs
of characters side by side ("bigrams"): a word of two or more characters
is then found by the pairs it is made of - a pair alone in a longer run is
a word as often as not - without a dictionary of the language or a model.
A character standing alone is kept as itself.
"""

from __future__ import annotations

import re
from typing import List

#: The scripts written without spaces between words: CJK ideographs (and
#: their extensions), Hiragana, Katakana, Hangul.
_RANGES = ((0x3040, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF),
           (0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F))
_CJK = "".join(f"{chr(low)}-{chr(high)}" for low, high in _RANGES)
_PIECES = re.compile(f"[{_CJK}]+|[a-z0-9_]+", re.IGNORECASE)
_SPACELESS = re.compile(f"[{_CJK}]")
#: Words longer than this are not words: hashes, encoded data.
MOST_LETTERS = 40


def words(text: str) -> List[str]:
    """Every word of a text, in order, as it is kept and asked."""
    out: List[str] = []
    for piece in _PIECES.findall(str(text or "")):
        if _SPACELESS.match(piece):
            if len(piece) == 1:
                out.append(piece)
            else:
                out.extend(piece[at:at + 2] for at in range(len(piece) - 1))
            continue
        piece = piece.lower()
        if 2 <= len(piece) <= MOST_LETTERS:
            out.append(piece)
    return out


def body(text: str) -> str:
    """The words of a text as one line, for the index to keep."""
    return " ".join(words(text))


def query(text: str, any_word: bool = False) -> str:
    """A search for these words, as FTS5 reads one: each word quoted - so
    no word is read as an operator - all of them required, or any one of
    them with `any_word`. Empty when there is no word in it."""
    found: List[str] = []
    for one in words(text):
        if one not in found:
            found.append(one)
    joiner = " OR " if any_word else " "
    return joiner.join(f'"{one}"' for one in found[:32])


__all__ = ["words", "body", "query", "MOST_LETTERS"]


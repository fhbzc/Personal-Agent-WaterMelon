"""A page a test draws says its encoding. Without it Chrome guesses, and v0.3.0's build saw a
"\u2026" come back unreadable once in five runs of the very same commit."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent


class EveryTestPageSaysItsEncodingTests(unittest.TestCase):

    def test_none_is_left_to_a_guess(self) -> None:
        bare = []
        for path in sorted(TESTS.glob("*.py")):
            if path.name == Path(__file__).name:
                continue
            text = path.read_text(encoding="utf-8")
            for found in re.finditer(r"<!doctype html", text, re.I):
                if "charset" not in text[found.start():found.start() + 160]:
                    bare.append(f"{path.name}:{text.count(chr(10), 0, found.start()) + 1}")
        self.assertEqual(bare, [])


if __name__ == "__main__":
    unittest.main()

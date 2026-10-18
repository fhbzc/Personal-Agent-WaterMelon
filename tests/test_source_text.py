"""No control characters in the source.

A backslash escape written through a tool that read it as an escape of its
own leaves a control character where the escape was: the lock of the data
folder's row in Settings drew as "F512A0" (styles.css), an English browser's
own dates were never used ("^en\\b" in i18n.js), and a test of duplicate ids
found no ids to compare (test_panels.py). None of them failed anything.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = {".py", ".js", ".css", ".html", ".json", ".md", ".txt", ".yml", ".yaml",
        ".iss", ".sh", ".toml", ".cfg", ".spec", ".plist", ".desktop"}
# Tab, line feed and carriage return are text; every other C0 character is not.
CONTROL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class SourceTextTests(unittest.TestCase):

    def test_no_source_file_holds_a_control_character(self) -> None:
        found = []
        for folder in ("personalized_agent", "tests", "scripts", "build", "docs", ".github"):
            for path in (ROOT / folder).rglob("*"):
                if path.suffix.lower() not in TEXT or "__pycache__" in path.parts:
                    continue
                if not path.is_file():
                    continue
                data = path.read_bytes()
                for match in CONTROL.finditer(data):
                    line = data.count(b"\n", 0, match.start()) + 1
                    found.append(f"{path.relative_to(ROOT)}:{line} {match.group()!r}")
        for path in ROOT.glob("*"):
            if path.is_file() and path.suffix.lower() in TEXT:
                data = path.read_bytes()
                for match in CONTROL.finditer(data):
                    line = data.count(b"\n", 0, match.start()) + 1
                    found.append(f"{path.relative_to(ROOT)}:{line} {match.group()!r}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()

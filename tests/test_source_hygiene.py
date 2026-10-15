"""Things about the source that a reader cannot see by running it once.

What is here are mistakes that work on the machine they were written on
and fail on somebody else's - or on the same one, the first time the
input is not in English.
"""

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1] / "personalized_agent"

#: `subprocess.run(...)`, and everything up to the matching bracket.
A_CALL = re.compile(r"subprocess\.(run|Popen|check_output)\s*\(")


def _calls(source: str):
    """Each subprocess call in a file, whole, brackets balanced.

    Line by line is not enough: these calls are written over four or five
    lines, and the thing being looked for is often on a different one
    from the thing that makes it necessary.
    """
    for match in A_CALL.finditer(source):
        depth, index = 0, match.end() - 1
        while index < len(source):
            if source[index] == "(":
                depth += 1
            elif source[index] == ")":
                depth -= 1
                if depth == 0:
                    break
            index += 1
        yield match.start(), source[match.start():index + 1]


class EveryTextModeSubprocessSaysWhichTextTests(unittest.TestCase):
    """A pipe read as text is decoded by SOMETHING, and the default is
    the machine's rather than the program's.

    On Windows that default is cp1252, which cannot represent most of
    what this agent sees. One conversation in Chinese went through a
    provider's standard output and the reader thread died with
    `UnicodeDecodeError: 'charmap' codec can't decode byte 0x8f`. The
    THREAD, not the call - so it surfaced as a traceback from somewhere
    unrelated, and what was being read was lost rather than mangled,
    which is the harder failure to notice.

    Cheaper to say which encoding at every call than to work out which
    ones can carry a window title, a file name or a model's answer. They
    all can.
    """

    def test_and_names_the_encoding_it_wants(self) -> None:
        missing = []
        for path in sorted(ROOT.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for at, call in _calls(source):
                reads_text = ("text=True" in call
                              or "universal_newlines=True" in call)
                if reads_text and "encoding=" not in call:
                    missing.append(
                        f"{path.name}:{source[:at].count(chr(10)) + 1}")
        self.assertEqual(
            missing, [],
            "these read a pipe as text without saying in which encoding, "
            "so they decode as whatever the machine defaults to: "
            + ", ".join(missing))

    def test_and_asks_for_replacement_rather_than_an_exception(self) -> None:
        """Naming the encoding is most of it; this is the rest.

        A pipe is not a promise of valid UTF-8 either - a CLI can emit a
        truncated character at a buffer edge, and a strict decode turns
        that into the same lost output. `errors="replace"` keeps the
        other ten thousand characters.
        """
        strict = []
        for path in sorted(ROOT.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for at, call in _calls(source):
                if "encoding=" in call and "errors=" not in call:
                    strict.append(
                        f"{path.name}:{source[:at].count(chr(10)) + 1}")
        self.assertEqual(
            strict, [],
            "these decode strictly, so one bad byte loses the whole read: "
            + ", ".join(strict))


if __name__ == "__main__":        # pragma: no cover
    unittest.main()

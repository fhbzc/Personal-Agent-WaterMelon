"""WHERE A BLOCK FOUND BY find_in_files LIKELY ENDS (the user, 2026-10-29).

A line found is where something starts - a function, a class, a `{ ... }`
block, a Markdown section - and reading only that part needs its end too.
So each line found that opens one is given where it likely ends, and how
that was told:

- Python: by Python's own parser (a function's or class's last line,
  exactly), or, where the file does not parse, by its indentation;
- R, C, JavaScript, Stata and the like: by matching braces from that line,
  past strings and comments;
- Markdown: up to the next heading as high as its own.

It is an ESTIMATE, said as one: the agent reads up to it, and reads on, or
less, as it sees fit. Anything else - a line that opens no block, a
language not here - is given no end."""

from __future__ import annotations

import ast
import re
from typing import Dict, Iterable, List, Optional, Tuple

#: The most lines a block is followed for, from where it starts.
MOST_SCANNED = 5000
#: How many lines after the one found a brace may open the block (R's
#: `f <- function(x)` with `{` on the next line).
OPENS_WITHIN = 2

PYTHON = frozenset({".py", ".pyw", ".pyi"})
MARKDOWN = frozenset({".md", ".markdown", ".rmd", ".qmd"})
#: Brace languages whose comments start with "#".
HASH_BRACES = frozenset({".r", ".sh", ".bash", ".zsh", ".ps1", ".pl"})
#: Brace languages with // and /* */ comments.
SLASH_BRACES = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".c", ".h",
                          ".cc", ".cpp", ".hpp", ".cs", ".go", ".rs", ".php", ".swift",
                          ".kt", ".kts", ".scala", ".css", ".scss", ".less", ".dart",
                          ".groovy", ".gradle", ".do", ".ado"})
HEADING = re.compile(r"^(#{1,6})\s")

BY_PARSER = "Python's parser"
BY_INDENT = "indentation"
BY_BRACES = "matching braces"
BY_HEADINGS = "the next heading as high"


def ends_of(suffix: str, lines: List[str], found: Iterable[int]) -> Dict[int, Tuple[int, str]]:
    """For each line found (0-based) that opens a block: its likely last
    line, counting from 1, and how that was told."""
    suffix = suffix.lower()
    found = list(found)
    out: Dict[int, Tuple[int, str]] = {}
    parsed = _python_ends(lines) if suffix in PYTHON else {}
    for n in found:
        end: Optional[int] = None
        by = ""
        if suffix in PYTHON:
            end, by = parsed.get(n + 1), BY_PARSER
            if end is None:
                end, by = _indent_end(lines, n), BY_INDENT
        elif suffix in MARKDOWN:
            end, by = _heading_end(lines, n), BY_HEADINGS
            if end is None and suffix != ".md" and suffix != ".markdown":
                # An R Markdown or Quarto file's code: R's braces.
                end, by = _brace_end(lines, n, "#", False), BY_BRACES
        elif suffix in HASH_BRACES:
            end, by = _brace_end(lines, n, "#", False), BY_BRACES
        elif suffix in SLASH_BRACES:
            end, by = _brace_end(lines, n, "//", True), BY_BRACES
        if end is not None and end > n + 1:
            out[n] = (end, by)
    return out


def _python_ends(lines: List[str]) -> Dict[int, int]:
    """Each function's and class's first line - and each of its decorators'
    - to its last, counting from 1; nothing when the file does not parse."""
    try:
        tree = ast.parse("\n".join(lines))
    except (SyntaxError, ValueError):
        return {}
    ends: Dict[int, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", None)
            if end:
                for start in [node.lineno] + [one.lineno for one in node.decorator_list]:
                    ends[start] = end
    return ends


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _indent_end(lines: List[str], start: int) -> Optional[int]:
    """A line ending in ":" opens what is indented under it: up to the last
    line more indented than it, counting from 1."""
    if not lines[start].rstrip().endswith(":"):
        return None
    base = _indent(lines[start])
    last = start
    for i in range(start + 1, min(len(lines), start + MOST_SCANNED)):
        if not lines[i].strip():
            continue
        if _indent(lines[i]) <= base:
            break
        last = i
    return last + 1 if last > start else None


def _heading_end(lines: List[str], start: int) -> Optional[int]:
    """A heading's section: up to the next heading as high, outside fenced
    code, its blank lines at the end left off - counting from 1."""
    opened = HEADING.match(lines[start])
    if not opened:
        return None
    level = len(opened.group(1))
    fenced = False
    end = len(lines)
    for i in range(start + 1, len(lines)):
        text = lines[i]
        if text.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        heading = None if fenced else HEADING.match(text)
        if heading and len(heading.group(1)) <= level:
            end = i
            break
    while end - 1 > start and not lines[end - 1].strip():
        end -= 1
    return end


def _brace_end(lines: List[str], start: int, comment: str, block_comments: bool) -> Optional[int]:
    """From the line found: the line where the first brace opened on it - or
    on one of the next OPENS_WITHIN - is closed, past strings and comments;
    counting from 1. None when no brace opens there, or none closes it."""
    depth = 0
    opened = False
    quote = ""
    in_comment = False
    for i in range(start, min(len(lines), start + MOST_SCANNED)):
        line = lines[i]
        j = 0
        while j < len(line):
            ch = line[j]
            if in_comment:
                if line.startswith("*/", j):
                    in_comment = False
                    j += 2
                    continue
                j += 1
                continue
            if quote:
                if ch == "\\":
                    j += 2
                    continue
                if ch == quote:
                    quote = ""
                j += 1
                continue
            if line.startswith(comment, j):
                break
            if block_comments and line.startswith("/*", j):
                in_comment = True
                j += 2
                continue
            if ch in "\"'`":
                quote = ch
            elif ch == "{":
                depth += 1
                opened = True
            elif ch == "}" and opened:
                depth -= 1
                if depth == 0:
                    return i + 1
            j += 1
        if block_comments and quote in ("\"", "'"):
            # In C and its like a quote does not run past its line.
            quote = ""
        if not opened and i >= start + OPENS_WITHIN:
            return None
    return None


__all__ = ["ends_of", "BY_PARSER", "BY_INDENT", "BY_BRACES", "BY_HEADINGS"]

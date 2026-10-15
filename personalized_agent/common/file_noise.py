"""Files that change wherever they are, whoever is at the keyboard.

Section 19 established which ones: a registry hive log, a lock file, an
editor's backup, a browser's cache, the agent's own memory. They are excluded
when they arrive - but the same question is asked in two other places, and
asking it three different ways is how a filter and a panel come to disagree
about what the user did today.

- the **Listener** asks before recording, which is where it does the most good
- the **Overview** asks before showing, because a rule added on Tuesday does
  nothing about the thirty days already written down
- **pruning** asks before deleting, for the same thirty days

One set of rules, read from the same settings, applied by one function.
"""

from __future__ import annotations

import os
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence


def churns_regardless(path: Optional[str],
                      patterns: Sequence[str] = (),
                      names: Sequence[str] = (),
                      folders: Sequence[str] = ()) -> bool:
    """Whether this path is noise rather than something somebody did.

    Three kinds of rule, because the noise comes in three shapes:

    - **patterns** match the file's own name, wherever it sits. A directory
      exclusion cannot reach ``ntuser.dat.LOG2``: it lives in the home
      directory itself, beside everything worth watching. Measured on one
      machine, it was 10,653 of the 24,610 file events in a day
    - **names** match any directory along the way, because the directories
      that make a home unwatchable - ``node_modules``, ``.git``,
      ``__pycache__`` - are inside every project rather than at a fixed place
    - **folders** match a whole tree by its location, which is what an
      exclusion list of paths is for
    """
    if not path:
        return False
    text = str(path)
    name = os.path.basename(text).lower()
    if name:
        for pattern in patterns:
            wanted = pattern.strip().lower()
            if wanted and fnmatch(name, wanted):
                return True
    normalized = os.path.normcase(os.path.abspath(text))
    if names:
        wanted_names = {n.strip().lower() for n in names if n.strip()}
        if wanted_names:
            parts = [part.lower() for part in Path(normalized).parts]
            if any(part in wanted_names for part in parts):
                return True
    for folder in folders:
        if not folder.strip():
            continue
        candidate = os.path.normcase(os.path.abspath(folder.strip()))
        if normalized == candidate or normalized.startswith(
                candidate.rstrip(os.sep) + os.sep):
            return True
    return False


def rules_from(config) -> dict:
    """The three lists, as the settings hold them.

    Read through one function so that a panel and a filter cannot drift into
    consulting different settings for the same question.
    """
    return {
        "patterns": list(config.lines("listener_excluded_file_patterns")),
        "names": list(config.lines("listener_excluded_names")),
        "folders": excluded_folders(config),
    }


def excluded_folders(config: Any) -> List[str]:
    """The folders never listened to: THE AGENT'S DATA FOLDER, always - set
    when it was installed (or with --root), and not a line of the setting
    that could be taken out, or the agent would record its own writing as
    the user's activity - and then the setting's own lines."""
    out: List[str] = []
    root = str(config.get("agent_root") or "").strip() if config is not None else ""
    if root:
        out.append(root)
    for line in config.lines("listener_directory_exclusions") if config is not None else ():
        if line not in out:
            out.append(line)
    return out


def sql_prefilter(patterns: Iterable[str] = (), names: Iterable[str] = (),
                  folders: Iterable[str] = ()) -> tuple:
    """A loose SQL condition that cannot miss anything the rules would catch.

    Deciding properly means ``fnmatch`` on the basename, which SQLite will not
    do. Reading every stored path into Python to ask is affordable once and
    wasteful every time, so the database narrows first with LIKE - which is
    deliberately **wider** than the real rule, never narrower - and the exact
    question is asked of what comes back.

    Returns ``(sql, params)`` where sql is a parenthesised OR of LIKE tests,
    or ``("", [])`` when there is nothing to look for.
    """
    tests, params = [], []
    for pattern in patterns:
        wanted = pattern.strip()
        if not wanted:
            continue
        # fnmatch's wildcards to SQL's, and a pattern anchored to the end of
        # the path because a basename is what a path ends with.
        like = wanted.replace("*", "%").replace("?", "_")
        tests.append("json_extract(data, '$.path') LIKE ?")
        params.append(like if like.startswith("%") else "%" + like)
    for name in names:
        wanted = name.strip()
        if not wanted:
            continue
        tests.append("json_extract(data, '$.path') LIKE ?")
        params.append(f"%{wanted}%")
    for folder in folders:
        wanted = folder.strip()
        if not wanted:
            continue
        # A whole tree, matched by where it starts. Left out of this
        # prefilter once, and the sweep then examined 14,763 rows out of the
        # 50,784 that its own rules would have caught - every one of the
        # missing ones excluded by its folder rather than by its name.
        tests.append("json_extract(data, '$.path') LIKE ?")
        params.append(wanted.rstrip("\\/") + "%")
    if not tests:
        return "", []
    return "(" + " OR ".join(tests) + ")", params

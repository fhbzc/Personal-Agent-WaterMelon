"""ONCE: what the Build now test learned becomes the user's experience.

The test was the way stages were tried out while they were being designed:
the same code over the same tasks, kept apart in its own scope and its own
library. Learning is now a feature of its own - there is one experience -
and the test's is the better one. So, once, at start, before anything else
runs:

  1. BACKED UP: the database, and the user's nature folder, whole.
  2. THE OLD LESSONS OUT: the live stages, their cards, edges, threads,
     marks, prompts and index, and the nature records written from stages
     (source "experience"). The agent heartbeat's own records - who the
     user is - stay. The work patterns go too, built from those records;
     the personal ones stay, without members that are gone.
  3. THE TEST'S IN: its rows become the live scope's; its nature records
     move into the user's nature folder; each project it learned is
     recorded as learned, up to the test's cutoff.
  4. WHAT WAS RETIRED, PUT AWAY: records of the data heartbeat and of
     compaction, and the retired folders, moved out of the library to a
     folder of their own - nothing reads them.
  5. THE TEST'S FOLDER, PUT AWAY with them.

Done once: a setting says so, and a second start does nothing.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

log = logging.getLogger(__name__)

#: The setting that says it has been done.
DONE = "experience_test_promoted"

#: The test's scope, as it was kept.
TEST = "build"

#: Tables whose rows a scope owns.
SCOPED = ("experience_stages", "experience_cards", "experience_edges",
          "experience_threads", "experience_marks", "experience_prompts",
          "experience_patterns", "stage_terms")

#: Records nothing reads any more, by where they came from.
RETIRED_SOURCES = ("data_heartbeat", "compaction")

#: Library folders nothing writes or reads any more.
RETIRED_FOLDERS = ("others", "agent-lesson")


def _source_of(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    head = text.split("\n---", 1)[0]
    for line in head.splitlines():
        if line.startswith("source:"):
            return line.split(":", 1)[1].strip()
    return ""


def _id_of(path: Path) -> str:
    try:
        head = path.read_text(encoding="utf-8", errors="replace").split("\n---", 1)[0]
    except OSError:
        return ""
    for line in head.splitlines():
        if line.startswith("id:"):
            return line.split(":", 1)[1].strip()
    return ""


def promote_the_test(db: Any, config: Any, library_root: Path,
                     internal: Path) -> Dict[str, Any]:
    """Make the test's experience the user's - once. Returns what it did."""
    if config.get(DONE):
        return {}
    test_root = internal / "experience_build"
    has_test = bool(db.query("SELECT 1 FROM experience_stages WHERE scope = ? "
                             "LIMIT 1", (TEST,))) or test_root.exists()
    if not has_test:
        config.set(DONE, True, internal=True)
        return {}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    put_away = internal / "backups" / f"before-learning-{stamp}"
    put_away.mkdir(parents=True, exist_ok=True)
    said: Dict[str, Any] = {"backup": str(put_away)}

    # 1. Backed up, whole.
    database = Path(getattr(db, "path", "") or "")
    if database.is_file():
        target = sqlite3.connect(str(put_away / database.name))
        source = sqlite3.connect(str(database))
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
    natures = library_root / "user_nature"
    if natures.is_dir():
        shutil.copytree(natures, put_away / "user_nature")

    # 2. The old lessons out.
    live_stages = [row["id"] for row in db.query(
        "SELECT id FROM experience_stages WHERE scope = 'live'")]
    for start in range(0, len(live_stages), 400):
        chunk = live_stages[start:start + 400]
        holes = ", ".join("?" for _ in chunk)
        db.execute(f"DELETE FROM stage_natures WHERE stage_id IN ({holes})", tuple(chunk))
    gone_records: List[str] = []
    if natures.is_dir():
        for path in natures.glob("*.md"):
            if _source_of(path) == "experience":
                gone_records.append(_id_of(path))
                path.unlink()
    for start in range(0, len(gone_records), 400):
        chunk = [one for one in gone_records[start:start + 400] if one]
        if chunk:
            holes = ", ".join("?" for _ in chunk)
            db.execute(f"DELETE FROM nature_kinds WHERE record_id IN ({holes})",
                       tuple(chunk))
    for table in SCOPED:
        if table == "experience_patterns":
            continue
        db.execute(f"DELETE FROM {table} WHERE scope = 'live'")
    # The work patterns were built from those records; the personal ones
    # stay, without members that are gone.
    db.execute("DELETE FROM experience_patterns WHERE scope = 'live' AND "
               "nature_kind = 'work'")
    gone = set(gone_records)
    for row in db.query("SELECT id, members FROM experience_patterns WHERE "
                        "scope = 'live' AND layer = 'pattern'"):
        try:
            members = json.loads(row["members"] or "[]")
        except ValueError:
            continue
        kept = [one for one in members if one not in gone]
        if kept != members:
            db.execute("UPDATE experience_patterns SET members = ?, support = ? "
                       "WHERE id = ?", (json.dumps(kept), len(kept), row["id"]))
    said["old_stages"] = len(live_stages)
    said["old_natures"] = len(gone_records)

    # 3. The test's in.
    moved = {}
    for table in SCOPED:
        moved[table] = db.execute(f"UPDATE {table} SET scope = 'live' WHERE scope = ?",
                                  (TEST,)).rowcount
    # A project's folder as the test read it: the live record's place.
    for row in db.query("SELECT unit_id FROM experience_workspaces WHERE scope = ?",
                        (TEST,)):
        unit = str(row["unit_id"]).split("|", 1)[-1]
        db.execute("DELETE FROM experience_workspaces WHERE unit_id = ? AND "
                   "scope = 'live'", (unit,))
        db.execute("UPDATE experience_workspaces SET unit_id = ?, scope = 'live' "
                   "WHERE unit_id = ?", (unit, row["unit_id"]))
    test_natures = test_root / "user_nature"
    brought = 0
    if test_natures.is_dir():
        natures.mkdir(parents=True, exist_ok=True)
        for path in test_natures.glob("*.md"):
            if not (natures / path.name).exists():
                shutil.move(str(path), str(natures / path.name))
                brought += 1
    said["moved"] = moved
    said["natures_brought"] = brought
    # Each piece of work the test learned: learned, up to its cutoff.
    from .service import scoped_key
    run = config.get(scoped_key(TEST, "experience_run")) or {}
    if isinstance(run, str):
        try:
            run = json.loads(run)
        except ValueError:
            run = {}
    until = str((run or {}).get("until") or "")
    units = {row["task_id"] for row in db.query(
        "SELECT DISTINCT task_id FROM experience_stages WHERE scope = 'live'")}
    units |= {row["unit_id"] for row in db.query(
        "SELECT unit_id FROM experience_workspaces WHERE scope = 'live' AND "
        "origin = 'detected'")}
    if until:
        for unit in units:
            db.execute("INSERT OR IGNORE INTO experience_learning (unit_id, until, "
                       "done, learned_at) VALUES (?, ?, 1, ?)", (unit, until, until))
    # Its pass and grouping start over in the live experience: every record
    # is grouped again into the work patterns at the next grouping.
    for name in ("experience_stages_pass", "experience_grouped_until",
                 "experience_patterns_round"):
        config.set(name, {} if name != "experience_grouped_until" else "",
                   internal=True)
        config.set(scoped_key(TEST, name), {} if name != "experience_grouped_until"
                   else "", internal=True)
    config.set(scoped_key(TEST, "experience_run"), {}, internal=True)

    # 4. What was retired, put away.
    retired = put_away / "retired"
    count = 0
    if natures.is_dir():
        for path in natures.glob("*.md"):
            if _source_of(path) in RETIRED_SOURCES:
                (retired / "user_nature").mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(retired / "user_nature" / path.name))
                count += 1
    for name in RETIRED_FOLDERS:
        folder = library_root / name
        if folder.is_dir():
            retired.mkdir(parents=True, exist_ok=True)
            shutil.move(str(folder), str(retired / name))
    said["retired_records"] = count

    # 5. The test's folder, put away.
    if test_root.exists():
        shutil.move(str(test_root), str(put_away / "experience_build"))
    config.set(DONE, True, internal=True)
    log.info("the test's learned experience is the user's now: %s", said)
    return said


#: The setting that says only lesson natures are kept.
LESSONS_ONLY = "experience_natures_lessons_only"


def keep_only_lesson_natures(db: Any, config: Any, library_root: Path,
                             internal: Path) -> Dict[str, Any]:
    """ONCE: every nature record that did not come from a lesson - what the
    agent heartbeat wrote from minutes of activity, before its step 2 was
    paused - taken out of the library into a backup, with the database
    backed up whole first. What pointed at them goes too: their kinds,
    their links to states, their places in patterns; a pattern left empty
    is removed, and a principle with no patterns left. What remains is
    grouped again at the next grouping."""
    if config.get(LESSONS_ONLY):
        return {}
    natures = library_root / "user_nature"
    gone_files = [path for path in natures.glob("*.md")
                  if _source_of(path) != "experience"] if natures.is_dir() else []
    if not gone_files:
        config.set(LESSONS_ONLY, True, internal=True)
        return {}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    put_away = internal / "backups" / f"before-lessons-only-{stamp}"
    (put_away / "user_nature").mkdir(parents=True, exist_ok=True)
    database = Path(getattr(db, "path", "") or "")
    if database.is_file():
        target = sqlite3.connect(str(put_away / database.name))
        source = sqlite3.connect(str(database))
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
    gone: List[str] = []
    for path in gone_files:
        gone.append(_id_of(path))
        shutil.move(str(path), str(put_away / "user_nature" / path.name))
    gone = [one for one in gone if one]
    for start in range(0, len(gone), 400):
        chunk = gone[start:start + 400]
        holes = ", ".join("?" for _ in chunk)
        db.execute(f"DELETE FROM nature_kinds WHERE record_id IN ({holes})", tuple(chunk))
        db.execute(f"DELETE FROM nature_links WHERE record_id IN ({holes})", tuple(chunk))
        db.execute(f"DELETE FROM stage_natures WHERE record_id IN ({holes})", tuple(chunk))
    gone_set = set(gone)
    removed_patterns: List[str] = []
    for row in db.query("SELECT id, members FROM experience_patterns WHERE "
                        "layer = 'pattern'"):
        try:
            members = json.loads(row["members"] or "[]")
        except ValueError:
            continue
        kept = [one for one in members if one not in gone_set]
        if not kept:
            db.execute("DELETE FROM experience_patterns WHERE id = ?", (row["id"],))
            removed_patterns.append(row["id"])
        elif kept != members:
            db.execute("UPDATE experience_patterns SET members = ?, support = ? "
                       "WHERE id = ?", (json.dumps(kept), len(kept), row["id"]))
    left = {row["id"] for row in db.query(
        "SELECT id FROM experience_patterns WHERE layer = 'pattern'")}
    removed_principles = 0
    for row in db.query("SELECT id, members FROM experience_patterns WHERE "
                        "layer = 'principle'"):
        try:
            members = json.loads(row["members"] or "[]")
        except ValueError:
            continue
        kept = [one for one in members if one in left]
        if not kept:
            db.execute("DELETE FROM experience_patterns WHERE id = ?", (row["id"],))
            removed_principles += 1
        elif kept != members:
            db.execute("UPDATE experience_patterns SET members = ?, support = ? "
                       "WHERE id = ?", (json.dumps(kept), len(kept), row["id"]))
    config.set(LESSONS_ONLY, True, internal=True)
    said = {"backup": str(put_away), "natures_removed": len(gone),
            "patterns_removed": len(removed_patterns),
            "principles_removed": removed_principles}
    log.info("only the lessons' natures are kept now: %s", said)
    return said

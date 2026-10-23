"""ONCE: the learning starts again (the user's decision, 2026-10-21).

What the old learning learned - the stages each project was learned into,
the natures written from them, their patterns and principles, the lesson
cards - is BACKED UP, whole: the database and the natures' folder, copied
to `internal/backups/before-new-learning-<time>`. Nothing is deleted and
nothing is changed; it is simply no longer read for the user's work
(learning/ reads the user's work afresh). No case is made from the old
stages.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)

DONE = "learning_fresh_start"


def start_afresh(db: Any, config: Any, library_root: Path, internal: Path) -> Dict[str, Any]:
    if config.get(DONE):
        return {}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    put_away = Path(internal) / "backups" / f"before-new-learning-{stamp}"
    put_away.mkdir(parents=True, exist_ok=True)
    database = Path(getattr(db, "path", "") or "")
    if database.is_file():
        target = sqlite3.connect(str(put_away / database.name))
        source = sqlite3.connect(str(database))
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
    natures = Path(library_root) / "user_nature"
    if natures.is_dir():
        shutil.copytree(natures, put_away / "user_nature")
    config.set(DONE, True, internal=True)
    log.info("the old learning is backed up in %s; learning starts again", put_away)
    return {"backup": str(put_away)}


#: ONCE MORE (the user, 2026-10-22): the first learning read folders in the
#: system's folder for passing files and conversations a program started -
#: copies of this application, tests, its own model calls - as the user's
#: work. What it learned is BACKED UP, whole, and then emptied: the cases,
#: judgments, reasons, terms, folder maps and updates. The places the user
#: listed or left out stay, and the next update learns from nothing.
AGAIN = "learning_again_2026_10_22"

#: Everything learning wrote, the tables that point at others first.
LEARNED = ("learn_links", "learn_overrides", "learn_judgments", "learn_reasons",
           "learn_cases", "learn_folders", "learn_units", "learn_terms", "learn_updates")


def learn_again(db: Any, config: Any, internal: Path) -> Dict[str, Any]:
    if config.get(AGAIN):
        return {}
    try:
        learned = int((db.query_one("SELECT COUNT(*) AS n FROM learn_updates") or {}).get("n") or 0)
    except sqlite3.Error:                     # no learning tables yet: nothing to do
        learned = 0
    if not learned:
        config.set(AGAIN, True, internal=True)
        return {}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    put_away = Path(internal) / "backups" / f"before-learning-again-{stamp}"
    put_away.mkdir(parents=True, exist_ok=True)
    database = Path(getattr(db, "path", "") or "")
    if database.is_file():
        target = sqlite3.connect(str(put_away / database.name))
        source = sqlite3.connect(str(database))
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
    # The copies of documents it read go with the backup.
    copies = Path(internal) / "learning" / "copies"
    if copies.is_dir():
        shutil.move(str(copies), str(put_away / "copies"))
    gone: Dict[str, int] = {}
    with db.write() as conn:
        for table in LEARNED:
            gone[table] = conn.execute(f"DELETE FROM {table}").rowcount
    from .vocab import Vocabulary
    Vocabulary(db).ensure_fixed()
    config.set(AGAIN, True, internal=True)
    log.info("learning backed up in %s and emptied (%s); it learns again from nothing",
             put_away, gone)
    return {"backup": str(put_away), "gone": gone}

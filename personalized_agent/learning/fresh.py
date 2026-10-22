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

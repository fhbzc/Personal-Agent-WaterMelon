"""A task's and a project's English copy, filled in where it is missing.

What is compared and measured is read in English (the embedding model reads
English only), and the user reads their own language. So where the display
language is not English, a task's words carry an English copy beside them
(`described["in_english"]`), written in the same call that writes the words.

That call writes only what changed, so a task whose title has not moved since
before copies existed has none, and one whose summary was rewritten without
its copy has a stale one. Each copied field keeps a mark of the words it was
copied from (`in_english["_of"]`); a field with no copy, or a copy of other
words, is translated here - all of them together, in a few short calls - at
the end of a state round and before an issue is made.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ...common.languages import in_english, needs_english_copy

log = logging.getLogger(__name__)

#: A task's words that are copied; a project's.
TASK_FIELDS = ("title", "summary", "where", "goal", "doing", "status")
PROJECT_FIELDS = ("name", "status")
#: Fields in one call: short, so one bad answer loses little.
PER_CALL = 40
#: At most this many calls in one go - the rest the next time.
MOST_CALLS = 6

TRANSLATE = """TRANSLATE EACH FIELD BELOW INTO PLAIN ENGLISH.

These are the user's own tasks and projects, written in their language. The
agent compares and searches them in English, so it needs an English copy of
each - said the same way, nothing added or left out. Names of people, files,
products, papers and code stay as they are written.

Return ONLY this JSON object, with every id and field you were given:
{"items": [{"id": "...", "fields": {"<field>": "<the same words in English>"}}]}"""


def mark(text: Any) -> str:
    """What a copy was made from, in a few characters."""
    return hashlib.sha1(str(text or "").strip().encode("utf-8")).hexdigest()[:12]


def stamped(english: Optional[Dict[str, Any]], made_from: Dict[str, Any],
            parts: Any) -> Dict[str, Any]:
    """The English copy, with the parts just written marked as copies of
    these words - so they are not translated again."""
    out = dict(english or {})
    of = dict(out.get("_of") or {})
    for part in parts or []:
        if str(made_from.get(part) or "").strip() and str(out.get(part) or "").strip():
            of[part] = mark(made_from[part])
    out["_of"] = of
    return out


def wanting(described: Dict[str, Any], words: Dict[str, Any],
            fields: Tuple[str, ...]) -> Dict[str, str]:
    """The fields with no English copy, or a copy of other words."""
    english = described.get("in_english") if isinstance(described.get("in_english"),
                                                        dict) else {}
    of = english.get("_of") if isinstance(english.get("_of"), dict) else {}
    # Failed on these very words (2026-11-07): not asked again the same way.
    failed = english.get("_failed") if isinstance(english.get("_failed"), dict) else {}
    out = {}
    for field in fields:
        text = str(words.get(field) or "").strip()
        if not text or failed.get(field) == mark(text):
            continue
        if not str(english.get(field) or "").strip() or of.get(field) != mark(text):
            out[field] = text
    return out


def _loads(raw: Any) -> Dict[str, Any]:
    try:
        said = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return said if isinstance(said, dict) else {}


def _rows(db: Any, scope: str) -> List[Tuple[str, str, Dict[str, str]]]:
    """(table, id, the fields wanting a copy) of every live task and project."""
    out = []
    try:
        tasks = db.query("SELECT id, described FROM state_tasks WHERE scope = ? AND "
                         "COALESCE(retired_at, '') = '' AND COALESCE(archived_at, '') = ''",
                         (scope,))
        projects = db.query("SELECT id, name, described FROM state_projects WHERE scope = ?",
                            (scope,))
    except Exception:                        # no state tables yet
        return []
    for row in tasks:
        said = _loads(row["described"])
        need = wanting(said, said, TASK_FIELDS)
        if need:
            out.append(("state_tasks", str(row["id"]), need))
    for row in projects:
        said = _loads(row["described"])
        need = wanting(said, dict(said, name=row["name"]), PROJECT_FIELDS)
        if need:
            out.append(("state_projects", str(row["id"]), need))
    return out


def fill(db: Any, models: Any, config: Any, scope: str = "live",
         going: Optional[Callable[[], bool]] = None) -> Dict[str, Any]:
    """Every missing or stale English copy, translated and kept. Nothing
    at all when the user reads English: their words are the copy."""
    out: Dict[str, Any] = {"asked": 0, "filled": 0, "calls": 0}
    if models is None or config is None or not needs_english_copy(config):
        return out
    rows = _rows(db, scope)
    out["asked"] = sum(len(need) for _t, _i, need in rows)
    batch: List[Tuple[str, str, Dict[str, str]]] = []
    size = 0
    batches = []
    for one in rows:
        if batch and size + len(one[2]) > PER_CALL:
            batches.append(batch)
            batch, size = [], 0
        batch.append(one)
        size += len(one[2])
    if batch:
        batches.append(batch)
    for batch in batches[:MOST_CALLS]:
        if going is not None and not going():
            break
        filled, calls, why = _translate(db, models, batch)
        out["filled"] += filled
        out["calls"] += calls
        if why:
            out.setdefault("not_taken", []).append(why)
    return out


def asked_in_parts(items: Sequence[Any], ask: Callable[[Sequence[Any]], Any]) -> List[Any]:
    """A batch asked whole, in halves while it is cut off (`answers.in_parts`) - and a part
    answered unreadably though not cut off, once more in two halves (the user, 2026-11-07:
    never the same question again the same way; a shorter one may be). What is still not
    whole at the end is given back as it is, for the caller to record as failed."""
    from ...models import answers as _answers
    out: List[Any] = []
    for part, answer in _answers.in_parts(items, ask):
        if answer.ok or answer.cut or len(part) < 2:
            out.append((part, answer))
            continue
        half = len(part) // 2
        out += _answers.in_parts(part[:half], ask) + _answers.in_parts(part[half:], ask)
    return out


class _NotAsked(Exception):
    """The model could not be asked: the batch is left for next time."""


def _ask(models: Any, batch: Sequence[Tuple[str, str, Dict[str, str]]]) -> Any:
    """One call for these rows, its answer read (models/answers.py)."""
    from ...models import answers as _answers
    from ...models.base import ModelRequest
    from .heartbeat import _as_json
    asked = json.dumps({"items": [{"id": f"{table}:{ident}", "fields": need}
                                  for table, ident, need in batch]}, ensure_ascii=False)
    try:
        response = models.invoke(ModelRequest(
            messages=[{"role": "user", "content": asked}], system=TRANSLATE,
            max_tokens=4000, task="state_heartbeat_english",
            language=in_english("every field you return"),
            metadata={"kind": "english_copies"}), role="heartbeat")
    except Exception as exc:
        log.warning("the English copies could not be asked for", exc_info=True)
        raise _NotAsked() from exc
    return _answers.read(response, parse=_as_json, wants=("items",))


def _translate(db: Any, models: Any, batch: List[Tuple[str, str, Dict[str, str]]]
               ) -> Tuple[int, int, str]:
    """(fields filled, calls made, why some failed). A CUT BATCH IS ASKED AGAIN IN HALVES
    (2026-11-07): cut off, it filled nothing, and the same batch came first every time - so
    nothing after it was ever filled. A part not whole at its smallest is RECORDED AS FAILED
    on its rows (`_failed`) and moved past: not asked again until its words change."""
    calls = [0]

    def ask(part: Sequence[Tuple[str, str, Dict[str, str]]]) -> Any:
        calls[0] += 1
        return _ask(models, part)

    try:
        answered = asked_in_parts(batch, ask)
    except _NotAsked:
        return 0, calls[0], ""
    filled, why = 0, ""
    for part, answer in answered:
        if not answer.ok:
            why = why or answer.why_not
            log.warning("the English copies of %d row(s) failed: %s", len(part), answer.why_not)
            _failed(db, part, answer.why_not)
            continue
        filled += _keep(db, part, answer.data)
    return filled, calls[0], why


def _failed(db: Any, batch: Sequence[Tuple[str, str, Dict[str, str]]], why: str) -> None:
    """Each field of these rows marked as failed for the words it has now (`_failed` in the
    copy, with `_why_failed`): `wanting` passes over it until the words change."""
    from .todo import LOCK
    for table, ident, need in batch:
        with LOCK:
            row = db.query_one(f"SELECT described FROM {table} WHERE id = ?", (ident,))
            if row is None:
                continue
            said = _loads(row["described"])
            english = dict(said.get("in_english") or {}) \
                if isinstance(said.get("in_english"), dict) else {}
            failed = dict(english.get("_failed") or {}) \
                if isinstance(english.get("_failed"), dict) else {}
            failed.update({field: mark(text) for field, text in need.items()})
            english["_failed"] = failed
            english["_why_failed"] = str(why or "")[:300]
            said["in_english"] = english
            db.execute(f"UPDATE {table} SET described = ? WHERE id = ?",
                       (json.dumps(said, ensure_ascii=False), ident))


def _keep(db: Any, batch: Sequence[Tuple[str, str, Dict[str, str]]],
          answered: Dict[str, Any]) -> int:
    """What a whole answer gave, kept where the words are still the ones translated."""
    ids = {f"{table}:{ident}": (table, ident, need) for table, ident, need in batch}
    filled = 0
    from .todo import LOCK
    for item in answered.get("items") or []:
        if not isinstance(item, dict) or str(item.get("id")) not in ids:
            continue
        table, ident, need = ids[str(item["id"])]
        given = {field: str(text).strip()[:600]
                 for field, text in (item.get("fields") or {}).items()
                 if field in need and str(text or "").strip()}
        if not given:
            continue
        with LOCK:
            row = db.query_one(f"SELECT {'name, ' if table == 'state_projects' else ''}"
                               f"described FROM {table} WHERE id = ?", (ident,))
            if row is None:
                continue
            said = _loads(row["described"])
            words = dict(said, name=row["name"]) if table == "state_projects" else said
            english = dict(said.get("in_english") or {}) \
                if isinstance(said.get("in_english"), dict) else {}
            # Only where the words are still the ones translated: a round
            # that rewrote one meanwhile has made it stale again.
            still = [field for field in given
                     if str(words.get(field) or "").strip() == need[field]]
            if not still:
                continue
            english.update({field: given[field] for field in still})
            said["in_english"] = stamped(english, words, still)
            db.execute(f"UPDATE {table} SET described = ? WHERE id = ?",
                       (json.dumps(said, ensure_ascii=False), ident))
            filled += len(still)
    return filled


__all__ = ["fill", "mark", "stamped", "wanting", "TASK_FIELDS", "PROJECT_FIELDS"]

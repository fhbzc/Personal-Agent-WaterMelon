"""Move what the knowledge graph holds into the library's folders.

    python scripts/migrate_graph_to_library.py [--db PATH] [--data PATH] [--dry-run]

Reads the SQLite database directly rather than importing the deprecated graph
code, so that the graph modules can be changed or removed without breaking the
one thing that still needs to read what they wrote.

Where things go
---------------
    nodes.type = user_nature       ->  user_nature/
    nodes.type = user_feedback     ->  user_feedback/
    task_questions (answered)      ->  user_feedback/
    states table                   ->  states/
    everything else in nodes       ->  others/
    nodes.type = archive           ->  skipped

Archives are skipped on purpose. Each one holds the full original text of a
node that was summarised to fit a size limit that no longer exists - the file
can simply hold the text. Where an archive is reachable, its content replaces
the summarised node's, so nothing is lost and the pair does not become two
files saying the same thing.

Edges are not migrated. There is nowhere for them to go, and the reasoning for
letting them go is in deprecated/README.md.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personalized_agent.data_manage.library import (  # noqa: E402
    AGENT_LESSON, OTHER, STATES, USER_FEEDBACK, USER_NATURE, DataLibrary,
    Indexer, Record)

SKIP_TYPES = {"archive"}


def _time(value, fallback=None):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback or datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _json(value, default):
    try:
        loaded = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return loaded if isinstance(loaded, type(default)) else default


def folder_for(node_type: str) -> str:
    kind = str(node_type or "").strip().lower()
    if kind in (USER_NATURE, USER_FEEDBACK):
        return kind
    return OTHER


def archives_by_source(db) -> dict:
    """archive node id -> the id of the node it was cut out of.

    The edge runs archive -> node and is the only thing joining them; the
    node's metadata also names the archive, and either direction will do.
    """
    out = {}
    try:
        rows = db.execute(
            "SELECT source_node_id, target_node_id FROM edges "
            "WHERE relationship_type = 'archived_history_of'").fetchall()
    except sqlite3.Error:
        return out
    for source, target in rows:
        out[target] = source
    return out


def already_there(library) -> set:
    """Ids already in the folders, so a second run adds rather than duplicates.

    A migration that cannot be run twice has to be run at exactly the right
    moment - after the old process stops and before the new one starts. This
    one carries each node's id onto its record, so running it again picks up
    whatever the old process wrote in the meantime and leaves the rest alone.
    """
    ids = set()
    for record in library.records():
        if record.id:
            ids.add(record.id)
    return ids


def backfill_provenance(library, dry_run=False) -> int:
    """Give records migrated before references existed their references.

    A record that says nothing about where it came from is an assertion with
    no way to check it, and the first migration ran before the field existed.
    Everything needed is already on the record - the id it kept from the node,
    and the metadata saying what it was migrated from - so this is a rewrite
    rather than a second read of the database.
    """
    fixed = 0
    for record in library.records():
        if record.references:
            continue
        origin = record.metadata.get("migrated_from") or ""
        if origin == "knowledge_graph":
            record.references = [
                f"knowledge graph node: {record.id}",
                f"original type: {record.metadata.get('original_type') or 'unknown'}"]
        elif origin == "states_table":
            record.references = [r for r in (
                f"state: {record.id}",
                f"task: {record.metadata.get('task_id')}"
                if record.metadata.get("task_id") else "") if r]
        elif origin == "task_questions":
            record.references = [r for r in (
                f"question: {record.id}",
                f"task: {record.metadata.get('task_id')}"
                if record.metadata.get("task_id") else "") if r]
        else:
            continue
        if not dry_run:
            library.put(record)
        fixed += 1
    return fixed


def migrate_nodes(db, library, dry_run=False, seen=()):
    archive_of = archives_by_source(db)
    archive_text = {}
    for row in db.execute("SELECT id, content FROM nodes WHERE type = 'archive'"):
        archive_text[row[0]] = row[1] or ""

    written = {f: 0 for f in (USER_NATURE, USER_FEEDBACK, OTHER)}
    archives = already = 0
    rows = db.execute(
        "SELECT id, type, title, summary, content, created_at, source, "
        "metadata, keywords FROM nodes ORDER BY created_at").fetchall()
    for (node_id, node_type, title, summary, content, created_at, source,
         metadata, keywords) in rows:
        if str(node_type or "").lower() in SKIP_TYPES:
            archives += 1
            continue
        if node_id in seen:
            already += 1
            continue
        body = content or ""
        meta = _json(metadata, {})
        # The summarised node gives up its cut-down text for the original.
        original = archive_text.get(archive_of.get(node_id) or meta.get("archive_node"))
        if original:
            body = original
        folder = folder_for(node_type)
        record = Record(
            folder=folder, id=node_id, title=title or "(untitled)",
            summary=summary or "", body=body,
            keywords=_json(keywords, []),
            source=source or "data_heartbeat",
            references=[f"knowledge graph node: {node_id}",
                        f"original type: {node_type or 'unknown'}"],
            created_at=_time(created_at),
            metadata={"migrated_from": "knowledge_graph",
                      "original_type": node_type or ""},
        )
        if not dry_run:
            library.write(record)
        written[folder] += 1
    return written, archives, already


def migrate_states(db, library, dry_run=False, seen=()):
    count = 0
    try:
        rows = db.execute(
            "SELECT id, title, detail, source, about, starts_at, ends_at, "
            "status, task_id, created_at FROM states ORDER BY created_at"
        ).fetchall()
    except sqlite3.Error:
        return 0
    for (sid, title, detail, source, about, starts_at, ends_at, status,
         task_id, created_at) in rows:
        if sid in seen:
            continue
        record = Record(
            folder=STATES, id=sid, title=title or "(untitled state)",
            summary=(detail or "")[:300],
            body=(detail or ""),
            keywords=[w for w in (about, status) if w],
            source=source or "agent",
            references=[r for r in (f"state: {sid}",
                                    f"task: {task_id}" if task_id else "") if r],
            created_at=_time(created_at),
            observed_from=_time(starts_at, _time(created_at)) if starts_at else None,
            observed_to=_time(ends_at, None) if ends_at else None,
            metadata={"status": status or "", "task_id": task_id or "",
                      "about": about or "", "migrated_from": "states_table"},
        )
        if not dry_run:
            library.write(record)
        count += 1
    return count


def migrate_questions(db, library, dry_run=False, seen=()):
    """Answered questions are the user's verdict, which is what the folder is."""
    count = 0
    try:
        rows = db.execute(
            "SELECT id, kind, question, answer, task_id, created_at "
            "FROM task_questions WHERE answer IS NOT NULL AND answer != ''"
        ).fetchall()
    except sqlite3.Error:
        return 0
    for qid, kind, question, answer, task_id, created_at in rows:
        if qid in seen:
            continue
        record = Record(
            folder=USER_FEEDBACK, id=qid,
            title=f"Answered {kind or 'question'}: {(question or '')[:80]}",
            summary=f"the user chose: {answer}",
            body=f"Question ({kind}):\n{question}\n\nAnswer:\n{answer}",
            keywords=[k for k in (kind, answer) if k],
            references=[r for r in (f"question: {qid}",
                                    f"task: {task_id}" if task_id else "") if r],
            source="feedback", created_at=_time(created_at),
            metadata={"task_id": task_id or "", "migrated_from": "task_questions"},
        )
        if not dry_run:
            library.write(record)
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(Path.home() / "PersonalizedAgent"
                                            / "internal" / "database" / "agent.db"))
    parser.add_argument("--data", default=str(Path.home() / "PersonalizedAgent"
                                              / "workspace" / "data"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source = Path(args.db)
    if not source.exists():
        print(f"no database at {source}")
        return 1

    library = DataLibrary(args.data)
    if not args.dry_run:
        library.ensure()

    # Read-only, and through a copy of the URI so a running agent is not
    # disturbed by this at all.
    db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        backfilled = backfill_provenance(library, args.dry_run)
        if backfilled:
            print(f"{backfilled} records gained the references they were "
                  f"migrated without\n")
        # Read, not written - so a dry run asks the same question the real
        # run does. Discarding this here made --dry-run report every record
        # it would write as new, which on an already-migrated workspace is
        # every record there is, and the opposite of what happens.
        seen = already_there(library)
        if seen:
            print(f"{len(seen)} records already migrated; adding what is new\n")
        nodes, archives, already = migrate_nodes(db, library, args.dry_run, seen)
        states = migrate_states(db, library, args.dry_run, seen)
        questions = migrate_questions(db, library, args.dry_run, seen)
    finally:
        db.close()

    print(f"database : {source}")
    print(f"data     : {args.data}")
    for folder, count in nodes.items():
        print(f"  {folder:<14} {count}")
    print(f"  {STATES:<14} {states}")
    print(f"  {USER_FEEDBACK:<14} +{questions} (from answered questions)")
    print(f"  archives skipped : {archives}")
    if already:
        print(f"  already there    : {already}")
    if args.dry_run:
        print("\n(dry run - nothing written)")
        return 0

    report = Indexer(library).run(limit=10_000)
    print(f"\nindexed  : {report}")
    print(f"\nThe {AGENT_LESSON}/ folder starts empty. It is written by the "
          f"agent,\nnot migrated from anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

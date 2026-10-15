"""SQLite access layer shared by every persistent store.

One connection per thread (SQLite objects are not shareable across threads),
WAL journaling so the listener can write while the heartbeat and the
Overview read.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

log = logging.getLogger(__name__)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.RLock()
        self._init_schema()

    # -- connections --------------------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            conn = self.connection
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self.write() as conn:
            return conn.execute(sql, params)

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        with self.write() as conn:
            conn.executemany(sql, list(seq))

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        cursor = self.connection.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- schema -------------------------------------------------------------
    def _init_schema(self) -> None:
        with self.write() as conn:
            conn.executescript(SCHEMA_SQL)
            self._add_missing_columns(conn)
            # After the columns, never with them. An index on a column that
            # an existing database has not got yet fails the whole script -
            # and CREATE TABLE IF NOT EXISTS leaves that database exactly as
            # it was, so the column arrives one step later than the index
            # naming it. Every index on anything in ADDED_COLUMNS belongs
            # here.
            conn.executescript(LATE_INDEXES_SQL)
            conn.executescript(RETIRED_INDEXES_SQL)
            self._backfill_notification_choice(conn)
            self._name_old_calls(conn)

    #: Learning's calls found within this share of what the learning itself
    #: counted, or they are not named.
    _LEARNING_MATCH = 0.05

    @classmethod
    def _name_old_calls(cls, conn: Any) -> None:
        """WHAT OLD CALLS WERE FOR, named as calls are now (common/modules.py).

        Before 2026-10-15 a task learned into stages counted its calls as
        "experience" - with the natures grouped by themselves beside each
        round - and the lessons engine counted every kind it learned as
        "lessons". So a project's learning showed as the automatic grouping,
        and the work's lessons as lessons the user asked for.

        Named only where it can be told for certain, and once (each step
        looks only at what is not named yet):
        - a LEARNING's calls: the "experience" calls in the window it
          counted its calls over (experience_learning) - when they are as
          many as it counted and their tokens come within `_LEARNING_MATCH`
          of its count, and none in the window is named yet;
        - a LESSONS call: by the kind of what the engine processed in the
          same minute - when that was of one kind only.
        """
        try:
            learnings = list(conn.execute(
                "SELECT counted_since, learned_at, calls, tokens_in FROM experience_learning "
                "WHERE COALESCE(counted_since, '') != '' AND calls > 0"))
        except Exception:                       # the tables are new; nothing to name
            return
        for since, until, count, tokens_in in learnings:
            try:
                window = (str(since), str(until)[:19] + "~")
                if conn.execute(
                        "SELECT 1 FROM model_calls WHERE task = 'experience_stages' AND "
                        "created_at BETWEEN ? AND ? LIMIT 1", window).fetchone():
                    continue
                chosen = list(conn.execute(
                    "SELECT id, input_tokens + COALESCE(cached_input_tokens, 0) "
                    "+ COALESCE(cache_written_tokens, 0) AS read FROM model_calls "
                    "WHERE task = 'experience' AND created_at BETWEEN ? AND ? "
                    "AND input_tokens + output_tokens + COALESCE(cached_input_tokens, 0) > 0",
                    window))
                read = sum(int(one[1] or 0) for one in chosen)
                # Every call of the window the learning's, or none named: the
                # grouping beside a round writes "experience" too, and in the
                # same hours its calls cannot be told from the learning's.
                if len(chosen) != int(count or 0) or not tokens_in or \
                        abs(read - int(tokens_in)) > cls._LEARNING_MATCH * int(tokens_in):
                    continue
                conn.executemany("UPDATE model_calls SET task = 'experience_stages' WHERE id = ?",
                                 [(one[0],) for one in chosen])
            except Exception:                   # pragma: no cover - left as it was
                continue
        try:
            calls = list(conn.execute(
                "SELECT id, substr(created_at, 1, 16) FROM model_calls WHERE task = 'lessons'"))
            for ident, minute in calls:
                kinds = {row[0] for row in conn.execute(
                    "SELECT DISTINCT kind FROM lesson_evidence WHERE substr(processed_at, 1, 16) "
                    "= ?", (minute,))}
                if len(kinds) == 1:
                    conn.execute("UPDATE model_calls SET task = ? WHERE id = ?",
                                 (f"lessons_{kinds.pop()}", ident))
        except Exception:                       # pragma: no cover - left as it was
            return

    # Keys the agent invented to say "do not interrupt me": it had no parameter
    # for this, so it wrote its intention into the free-form payload, where
    # nothing read it. They are the only record of what it meant.
    _QUIET_HINTS = ("surface", "deliver_to", "no_toast_unless", "toast_condition",
                    "escalate_only_if", "output_surface", "on_null")

    def backfill_notification_choice(self) -> None:
        """Re-run the section 52.2 backfill against the open database."""
        with self.write() as conn:
            self._backfill_notification_choice(conn)

    @classmethod
    def _backfill_notification_choice(cls, conn: Any) -> None:
        """Section 52.2 - decide, once, for tasks made before there was a choice.

        Whether a task interrupts now comes from ``notify_user`` in its
        payload, and it defaults to silent. Tasks scheduled before that flag
        existed do not carry it, so without this they would all fall to the new
        default - including the ones that exist precisely in order to interrupt,
        like a recurring break reminder, which would go quiet without anyone
        asking it to.

        The rule: a task whose whole purpose is the notification keeps
        notifying; everything else becomes silent, which is what the agent was
        already trying to say with the payload keys above.
        """
        try:
            rows = list(conn.execute(
                "SELECT id, execution_type, task_payload FROM scheduled_tasks"))
        except Exception:                       # the table is new; nothing to do
            return
        changed = 0
        for task_id, execution_type, raw in rows:
            try:
                payload = json.loads(raw or "{}")
            except Exception:
                payload = {}
            if not isinstance(payload, dict) or "notify_user" in payload:
                continue
            hinted = any(key in payload for key in cls._QUIET_HINTS)
            payload["notify_user"] = execution_type == "notification" and not hinted
            conn.execute("UPDATE scheduled_tasks SET task_payload = ? WHERE id = ?",
                         (json.dumps(payload), task_id))
            changed += 1
        if changed:
            log.info("set the notification choice on %s task(s) made before "
                     "there was one", changed)

    @staticmethod
    def _add_missing_columns(conn: Any) -> None:
        """Bring an existing database up to the current schema.

        CREATE TABLE IF NOT EXISTS does nothing to a table that already
        exists, so a database made by an earlier version keeps its old
        columns and every query naming a new one fails. The user's data
        outlives any one version of this application, and they are not
        expected to delete it to install an update.
        """
        for table, column, definition in ADDED_COLUMNS:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue                      # the table itself is new
            if column not in existing:
                log.info("adding %s.%s to the existing database", table, column)
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


#: Columns added after the first release, as (table, column, definition).
#: SQLite can only add a column with a constant default, which is why each of
#: these is nullable rather than NOT NULL.
ADDED_COLUMNS = (
    # Which chat project a conversation belongs to, if any.
    ("conversations", "project_id", "TEXT REFERENCES chat_projects(id)"),
    # Where its files are kept (agent/dialog/files.py), once it wrote one.
    ("conversations", "folder", "TEXT"),
    # The lesson a notification action taught, beside why it was taken.
    ("calendar_actions", "lesson", "TEXT"),
    # What a model call read from the prompt cache and wrote to it.
    ("model_calls", "cached_input_tokens", "INTEGER DEFAULT 0"),
    ("model_calls", "cache_written_tokens", "INTEGER DEFAULT 0"),
    # Which language the call was told to write in (common/languages.py),
    # as JSON rows - "" when it was not told.
    ("model_calls", "language", "TEXT DEFAULT ''"),
    # Whether the user started the run it was made in, with a button.
    ("model_calls", "by_user", "INTEGER DEFAULT 0"),
    # A project the user made of several folders.
    ("experience_workspaces", "paths", "TEXT DEFAULT '[]'"),
    # The cutoff of a run of a project the user made that has not finished -
    # kept, so an interrupted run can be resumed under the same freeze.
    ("experience_workspaces", "processing_until", "TEXT DEFAULT ''"),
    # Whose prompt it was: the live experience's or the Build now test's.
    ("experience_prompts", "scope", "TEXT NOT NULL DEFAULT 'live'"),
    # A stage records the work itself: its goal, steps, criteria, outcome,
    # and where its references are.
    ("experience_stages", "work", "TEXT DEFAULT '{}'"),
    # A stage is an attempt inside a card of the work's tree.
    ("experience_stages", "card_id", "TEXT DEFAULT ''"),
    # Nature records in two kinds, and the patterns built from each apart.
    ("experience_patterns", "nature_kind", "TEXT DEFAULT ''"),
    # When a pattern or principle was last seen in the user's work - one
    # not seen for long may be a way they no longer work.
    ("experience_patterns", "last_active", "TEXT DEFAULT ''"),
    # Section 34.4, as amended: a task keeps a profile rather than every
    # state, and goes dormant rather than ending.
    ("state_tasks", "profile", "TEXT NOT NULL DEFAULT '{}'"),
    ("state_tasks", "archived_at", "TEXT"),
    ("state_projects", "described", "TEXT NOT NULL DEFAULT '{}'"),
    ("nodes", "keywords", "TEXT"),
    # Section 52.3: a database made before tasks could repeat.
    ("scheduled_tasks", "recurrence", "TEXT NOT NULL DEFAULT '{}'"),
    ("scheduled_tasks", "runs", "INTEGER NOT NULL DEFAULT 0"),
    ("context_uses", "context", "TEXT"),
    ("edges", "evidence", "REAL"),
    ("checklist_items", "retired_at", "TEXT"),
    ("checklist_items", "retired_reason", "TEXT"),
    ("checklist_items", "retired_by", "TEXT"),
    # Section 52.7: a database made before everything had to say why it
    # existed. These went into CREATE TABLE and were not listed here, so a
    # fresh install had them and an existing one did not - and since reading
    # tolerates a missing column and writing does not, the failure appeared
    # only when the user next asked for a task to be scheduled, long after
    # the update that caused it.
    ("scheduled_tasks", "created_by", "TEXT NOT NULL DEFAULT 'agent'"),
    ("scheduled_tasks", "reason", "TEXT NOT NULL DEFAULT ''"),
    ("states", "reason", "TEXT NOT NULL DEFAULT ''"),
    # Section 34.4: a database made before an appending round had to keep
    # the picture it did not rebuild.
    ("state_task_runs", "tree", "TEXT NOT NULL DEFAULT ''"),
    ("state_task_runs", "seen_from", "TEXT NOT NULL DEFAULT ''"),
    ("state_task_runs", "scope", "TEXT NOT NULL DEFAULT 'live'"),
    ("state_task_runs", "shape", "TEXT NOT NULL DEFAULT '{}'"),
    ("state_tasks", "scope", "TEXT NOT NULL DEFAULT 'live'"),
    # The structured description: where the work lives, what it is in aid
    # of, what it is, and where it stands. One JSON column and not four
    # of them, because which parts a task is described in is a property
    # of the instruction that writes them and will move again - a schema
    # change per revision of a prompt is a schema nobody will revise.
    ("state_tasks", "described", "TEXT NOT NULL DEFAULT '{}'"),
    # Where a task sits on the page. Assigned once and kept: see `seat` in
    # tasks.py. -1 means "never given one", which the first round after
    # this fills in from the order the tasks are already in.
    ("state_tasks", "seat", "INTEGER NOT NULL DEFAULT -1"),
    # Section 41.3: which retrievals have been folded into the co-use counts.
    # Separate from `processed`, which belongs to the pass that judges what an
    # answer was built from - two readers of the same rows, each keeping its
    # own place.
    ("context_uses", "linked", "INTEGER NOT NULL DEFAULT 0"),
)


#: Indexes on columns that only exist after a migration (see ``_init_schema``).
LATE_INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_context_uses_linked ON context_uses(linked, created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_project ON conversations(project_id);
"""


#: Indexes nothing uses any more, dropped from a database that has them.
#:
#: The two on ``processed`` served the data heartbeat's "what have I not
#: read yet". There is no such step now and nothing sets or asks for the
#: flag, so they were only kept up to date on every event written.
RETIRED_INDEXES_SQL = """
DROP INDEX IF EXISTS idx_raw_events_processed;
DROP INDEX IF EXISTS idx_raw_edits_processed;
"""


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS raw_events (
    id            TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    source        TEXT NOT NULL,
    timestamp     TEXT NOT NULL,
    application   TEXT,
    data          TEXT NOT NULL DEFAULT '{}',
    metadata      TEXT NOT NULL DEFAULT '{}',
    -- Left from the data heartbeat, which marked what it had read. Nothing
    -- writes or reads it now. It stays so that a database made before and
    -- one made after have the same shape: taking a column out rewrites the
    -- whole table, and this is the largest one there is.
    processed     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_raw_events_app ON raw_events(application, timestamp);
CREATE INDEX IF NOT EXISTS idx_raw_events_type ON raw_events(event_type, timestamp);
-- The Overview's own query: the newest N events, whatever they are.
--
-- Every other index here leads with a column that query does not
-- filter on, so it could use none of them: reading the most recent
-- 2,000 rows scanned all 49,522 and sorted them in a temporary
-- B-tree, 271 ms, on every click in the application list and every
-- stretch of use expanded.
CREATE INDEX IF NOT EXISTS idx_raw_events_recent ON raw_events(timestamp DESC);

CREATE TABLE IF NOT EXISTS raw_edits (
    id                   TEXT PRIMARY KEY,
    event_type           TEXT NOT NULL,
    source               TEXT NOT NULL,
    timestamp            TEXT NOT NULL,
    task_id              TEXT,
    instruction          TEXT,
    accessed_information TEXT NOT NULL DEFAULT '[]',
    action               TEXT,
    result               TEXT,
    related_nodes        TEXT NOT NULL DEFAULT '[]',
    metadata             TEXT NOT NULL DEFAULT '{}',
    -- Unused, as in raw_events, and kept for the same reason.
    processed            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_raw_edits_task ON raw_edits(task_id);

CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    node_key    TEXT UNIQUE,
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'agent',
    metadata    TEXT NOT NULL DEFAULT '{}',
    -- Section 51.1: the words this node should be found by, kept apart from
    -- its text so that they can be revised without rewriting what it says.
    keywords    TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_updated ON nodes(updated_at);

CREATE TABLE IF NOT EXISTS edges (
    id                TEXT PRIMARY KEY,
    source_node_id    TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_node_id    TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL,
    summary           TEXT NOT NULL DEFAULT '',
    weight            REAL NOT NULL DEFAULT 1.0,
    -- Section 44.1, as amended: how much this connection has actually proved
    -- itself, as a plain running count. The weight above is this passed
    -- through tanh - a bounded view of it, not the thing itself.
    evidence          REAL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    relationship_log  TEXT NOT NULL DEFAULT '[]',
    metadata          TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source_node_id, target_node_id, relationship_type)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_node_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_node_id);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    -- Section 52.7, as amended: who decided this should exist, and on what
    -- judgement. Never empty: a user's own entry reads "added by user", and
    -- an agent-created one with nothing recorded says that it has nothing
    -- recorded, because those two read identically if both are blank.
    created_by     TEXT NOT NULL DEFAULT 'agent',
    reason         TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    scheduled_at   TEXT NOT NULL,
    timezone_name  TEXT NOT NULL DEFAULT 'UTC',
    status         TEXT NOT NULL DEFAULT 'pending',
    execution_type TEXT NOT NULL DEFAULT 'notification',
    script_path    TEXT,
    model_config   TEXT NOT NULL DEFAULT '{}',
    task_payload   TEXT NOT NULL DEFAULT '{}',
    retry_policy   TEXT NOT NULL DEFAULT '{}',
    metadata       TEXT NOT NULL DEFAULT '{}',
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    last_run_at    TEXT,
    lease_token    TEXT,
    recurrence     TEXT NOT NULL DEFAULT '{}',
    runs           INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON scheduled_tasks(status, scheduled_at);

CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    message     TEXT NOT NULL,
    dedupe_key  TEXT,
    action_hint TEXT,
    metadata    TEXT NOT NULL DEFAULT '{}',
    read        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_dedupe ON notifications(dedupe_key, created_at);

-- A name the user gathers conversations under in the chat's sidebar. Only a
-- grouping: a conversation in a project reads nothing of the project's other
-- conversations.
CREATE TABLE IF NOT EXISTS chat_projects (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    name       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    project_id TEXT REFERENCES chat_projects(id),
    folder     TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    created_at      TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);

-- A follow-up asked about one reply in the chat - about a word or phrase in
-- it, or the whole of it. Asked on its own, with nothing of the conversation,
-- and never added to it; a follow-up has no follow-ups.
CREATE TABLE IF NOT EXISTS chat_followups (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL DEFAULT '',
    message_id      TEXT NOT NULL,
    about           TEXT NOT NULL DEFAULT '',   -- '' is the whole reply
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_followups_message ON chat_followups(message_id, created_at);

-- What the model is told of the earlier part of a long conversation, written
-- once and reused until the turns after it no longer fit beside it. One row
-- per conversation: `through_id` is the last message it covers, and
-- `covered` how many messages that is.
CREATE TABLE IF NOT EXISTS chat_summaries (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    through_id      TEXT NOT NULL,
    covered         INTEGER NOT NULL DEFAULT 0,
    summary         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

-- The chat debugger's runs, each with its whole transcript: what was sent to
-- the model, what came back, every tool call - redacted before it is kept.
-- `summary` is the row without its events, so the list never loads them.
CREATE TABLE IF NOT EXISTS chat_debugger_runs (
    id         TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    status     TEXT NOT NULL,
    summary    TEXT NOT NULL,
    transcript TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_debugger_runs_started ON chat_debugger_runs(started_at);

CREATE TABLE IF NOT EXISTS checklist_items (
    id        TEXT PRIMARY KEY,
    position  INTEGER NOT NULL,
    text      TEXT NOT NULL,
    enabled   INTEGER NOT NULL DEFAULT 1,
    origin    TEXT NOT NULL DEFAULT 'system',
    updated_at TEXT NOT NULL,
    -- Section 35, as amended: a step leaves the list by being retired, with
    -- the reason it was, rather than by being erased.
    retired_at     TEXT,
    retired_reason TEXT,
    retired_by     TEXT
);

-- Section 34.1, as amended: the state calendar. What is true over a stretch
-- of time, for the user or for the agent - shown, and used as context, but
-- never executed. The scheduler is what makes things happen.
CREATE TABLE IF NOT EXISTS states (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'agent',
    about      TEXT NOT NULL DEFAULT 'user',
    starts_at  TEXT,
    ends_at    TEXT,
    status     TEXT NOT NULL DEFAULT 'active',
    task_id    TEXT NOT NULL DEFAULT '',
    extensions INTEGER NOT NULL DEFAULT 0,
    -- Section 52.7, as amended: the judgement behind this state. `source`
    -- already says who: this says why.
    reason     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_states_span ON states(status, starts_at, ends_at);

-- Section 34.3, as amended: what a state means, as a vector.
--
-- A cache and not a record: every row here can be recomputed from the
-- state it belongs to, and is, whenever the state's words change or the
-- model does. It lives in the database rather than beside the state
-- because it is machine-readable only - a file of three hundred and
-- eighty-four floats in the folder the user reads would be noise in the
-- one place the design keeps readable.
--
-- The fingerprint is of the text that was read, so an edited state is
-- read again and a state merely seen again is not.
CREATE TABLE IF NOT EXISTS state_vectors (
    state_id    TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    dims        INTEGER NOT NULL,
    vector      BLOB NOT NULL,
    created_at  TEXT NOT NULL
);

-- Section 52.9: the tasks the state heartbeat found, kept between runs.
--
-- Without this table every run clusters from nothing and hands back a
-- different set of tasks with different numbers, and "task 3" means one
-- thing on Tuesday and another on Wednesday. Nothing can be said about a
-- task over time - not that it grew, not that it went quiet, not that
-- the user has been at it for three weeks - because there is no it.
--
-- So a task is a row that persists. New states are appended to the task
-- they are nearest, and the grouping is rebuilt only when appending
-- would start telling lies (see `heartbeat/states/tasks.py`, where each
-- limit carries the measurement that chose it).
--
-- `total` is the SUM of its members' vectors, not the mean, because a
-- sum is what lets a state be appended in constant time: add the vector,
-- add one to the count, and the centre follows. `anchor` is the centre
-- as it stood at the last rebuild, kept so that drift can be measured
-- against something that the drifting does not itself move.
--
-- Retired tasks are kept, not deleted. A task that stopped being a task
-- is a fact about the user's months, and `born_of` on its successors
-- points back at it.
CREATE TABLE IF NOT EXISTS state_tasks (
    id            TEXT PRIMARY KEY,
    number        INTEGER NOT NULL,
    members       TEXT NOT NULL,            -- JSON array of state ids
    total         BLOB,                     -- summed member vectors
    anchor        BLOB,                     -- the centre at the last rebuild
    dims          INTEGER NOT NULL DEFAULT 0,
    radius        REAL NOT NULL DEFAULT 0,
    spread        REAL NOT NULL DEFAULT 0,
    anchor_radius REAL NOT NULL DEFAULT 0,
    version       INTEGER NOT NULL DEFAULT 1,
    born_of       TEXT NOT NULL DEFAULT '{}',
    summary_id    TEXT NOT NULL DEFAULT '',   -- the node holding the summary
    summary_at    TEXT,
    summary_members INTEGER NOT NULL DEFAULT 0, -- how many it described
    described     TEXT NOT NULL DEFAULT '{}',  -- workspace/goal/doing/status
    seat          INTEGER NOT NULL DEFAULT -1, -- its place on the page
    created_at    TEXT NOT NULL,
    rebuilt_at    TEXT,
    updated_at    TEXT NOT NULL,
    retired_at    TEXT,
    -- What a task keeps instead of every state it ever held: its weighted
    -- centre's weight, its first and last activity, its places, the ids of
    -- its most recent states - JSON. And when it went dormant: an archived
    -- task is not drawn, and is still there to be picked up again.
    profile       TEXT NOT NULL DEFAULT '{}',
    archived_at   TEXT,
    -- Which run these belong to: the real one, or the debugger's sandbox.
    -- One column rather than a second pair of tables, because the whole
    -- point of the sandbox is that it is the same code over a different
    -- root - and two tables would be two schemas to keep in step, which
    -- is how a debugger stops testing what it claims to test.
    scope         TEXT NOT NULL DEFAULT 'live'
);
CREATE INDEX IF NOT EXISTS idx_state_tasks_live ON state_tasks(scope, retired_at, number);

-- Section 34.4: the states that were offered to every task and taken by
-- none.
--
-- Without this table they are rediscovered every round. A state in no
-- task looks exactly like a state nobody has looked at yet, so each hour
-- offered the whole pile again - which re-processed states that had
-- already been processed, and, worse, kept the count of "states that fit
-- nowhere" permanently above the limit at which that counts as a task
-- nobody has noticed. A first grouping that left thirty-eight states
-- loose therefore forced a rebuild of all thousand on the very next
-- round, every round, forever.
--
-- Being here means: offered, taken by nothing, and not to be offered
-- again. The pile is looked at as a whole when it grows big enough to be
-- a task of its own, and that is the only time these are read.
-- Section 34.4, as amended: tasks that are one larger piece of work - the
-- same project folder, the same paper - grouped under it. A project is
-- only made when the model has confirmed it, and a grouping it turned down
-- is remembered so it is not asked again.
CREATE TABLE IF NOT EXISTS state_projects (
    id          TEXT PRIMARY KEY,
    scope       TEXT NOT NULL DEFAULT 'live',
    name        TEXT NOT NULL DEFAULT '',
    why         TEXT NOT NULL DEFAULT '',
    home        TEXT NOT NULL DEFAULT '',      -- the folder or site it lives in
    tasks       TEXT NOT NULL DEFAULT '[]',    -- JSON array of task ids
    places      TEXT NOT NULL DEFAULT '[]',    -- the places its tasks share
    declined    TEXT NOT NULL DEFAULT '[]',    -- task ids the model said were not part of it
    described   TEXT NOT NULL DEFAULT '{}',    -- its status, and how big it was when named
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_state_projects_scope ON state_projects(scope);

-- EXPERIENCE: how the user's work went, stage by stage, and what it says
-- about how they work.
--
-- A stage is one step of the loop a piece of work goes through - a goal set,
-- a plan made, work done, a result evaluated, a plan adjusted, a deliverable
-- accepted. Stages belong to a task and are joined by dependency edges; a
-- project's graph is the union of its tasks' graphs plus the edges between
-- them. Nothing is rewritten: a stage keeps its id, and a graph grows by
-- new stages and new edges.
CREATE TABLE IF NOT EXISTS experience_stages (
    id          TEXT PRIMARY KEY,
    scope       TEXT NOT NULL DEFAULT 'live',
    task_id     TEXT NOT NULL,                 -- the task it belongs to
    thread_id   TEXT NOT NULL DEFAULT '',      -- the line of work inside it
    kind        TEXT NOT NULL,                 -- goal, plan, execution, ...
    medium      TEXT NOT NULL DEFAULT '',      -- email, code, document, ...
    summary     TEXT NOT NULL DEFAULT '',
    deliverable TEXT NOT NULL DEFAULT '{}',    -- {excerpt, ids}
    evaluation  TEXT NOT NULL DEFAULT '{}',    -- {verdict, criteria, words}
    states      TEXT NOT NULL DEFAULT '[]',    -- the state ids it is built from
    began       TEXT NOT NULL DEFAULT '',
    ended       TEXT NOT NULL DEFAULT '',
    written_by  TEXT NOT NULL DEFAULT '{}',    -- {model, provider, at}
    notes       TEXT NOT NULL DEFAULT '{}',    -- corrections and hindsight
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    work        TEXT DEFAULT '{}'              -- {goal, steps, criteria, outcome, references}
);
CREATE INDEX IF NOT EXISTS idx_experience_stages_task ON experience_stages(scope, task_id);

-- THE WORK AS A TREE OF CARDS. A card is a piece of the deliverable: what
-- must be true when it is done, how that is judged, and why the card above
-- it needs it. The root is the work's goal; the cards under the root are
-- the deliverables the user would recognise; the cards under those are
-- parts, each one output with one check. A stage is an ATTEMPT inside a
-- card - its input, one decision, its output, the verdict - and points at
-- its card by card_id. The level is where the card sits, not a field: a
-- task's root becomes a deliverable when the task joins a project.
CREATE TABLE IF NOT EXISTS experience_cards (
    id            TEXT PRIMARY KEY,
    scope         TEXT NOT NULL DEFAULT 'live',
    unit_id       TEXT NOT NULL DEFAULT '',      -- a root: the project or task it is the goal of
    task_id       TEXT NOT NULL DEFAULT '',      -- the task whose work it is
    parent_id     TEXT NOT NULL DEFAULT '',      -- '' for a root
    type          TEXT NOT NULL DEFAULT '',      -- code, document, slides, ...
    title         TEXT NOT NULL DEFAULT '',
    statement     TEXT NOT NULL DEFAULT '',      -- what is true when it is done
    specification TEXT NOT NULL DEFAULT '[]',    -- [{requirement, source}] - the user's only
    checked_by    TEXT NOT NULL DEFAULT '{}',    -- {by: test|user|agent, what}
    why           TEXT NOT NULL DEFAULT '{}',    -- {why, from}: what the card above needs it for
    refs          TEXT NOT NULL DEFAULT '[]',    -- where the evidence for it is
    status        TEXT NOT NULL DEFAULT 'open',  -- open, working, done, set_aside
    replaced_by   TEXT NOT NULL DEFAULT '',
    status_why    TEXT NOT NULL DEFAULT '',
    written_by    TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experience_cards_task ON experience_cards(scope, task_id);
CREATE INDEX IF NOT EXISTS idx_experience_cards_unit ON experience_cards(scope, unit_id);

-- The terms each stage is found by - worked out by code when the stage is
-- written (heartbeat/experience/index.py), never by a model: what it says,
-- and "env:..." terms for where it worked - kinds of file, folders, sites.
CREATE TABLE IF NOT EXISTS stage_terms (
    scope       TEXT NOT NULL DEFAULT 'live',
    stage_id    TEXT NOT NULL,
    term        TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1,
    PRIMARY KEY (scope, stage_id, term)
);
CREATE INDEX IF NOT EXISTS idx_stage_terms_term ON stage_terms(scope, term);

-- Each project's (or task's) LEARNING: the cutoff it was learned up to - or
-- is being learned up to, when not done - and everything it has cost, in
-- model calls and tokens, over every time it was learned. Never cleared:
-- what was learned is kept.
CREATE TABLE IF NOT EXISTS experience_learning (
    unit_id       TEXT PRIMARY KEY,
    until         TEXT NOT NULL DEFAULT '',
    done          INTEGER NOT NULL DEFAULT 0,
    learned_at    TEXT NOT NULL DEFAULT '',
    calls         INTEGER NOT NULL DEFAULT 0,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0,
    counted_since TEXT NOT NULL DEFAULT ''
);

-- Lines of work, from before the work was kept as cards: no longer written.
CREATE TABLE IF NOT EXISTS experience_threads (
    id          TEXT PRIMARY KEY,
    scope       TEXT NOT NULL DEFAULT 'live',
    task_id     TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open',  -- open, finished, abandoned
    why         TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experience_threads_task ON experience_threads(scope, task_id);

CREATE TABLE IF NOT EXISTS experience_edges (
    id          TEXT PRIMARY KEY,
    scope       TEXT NOT NULL DEFAULT 'live',
    source      TEXT NOT NULL,                 -- the later stage
    target      TEXT NOT NULL,                 -- the stage it depends on
    kind        TEXT NOT NULL,                 -- produces, evaluates, revises, continues
    reason      TEXT NOT NULL DEFAULT '',      -- e.g. "join:<project>" for one made by a join
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experience_edges_source ON experience_edges(scope, source);
CREATE INDEX IF NOT EXISTS idx_experience_edges_target ON experience_edges(scope, target);

-- Where each task's stages are written up to, and what is still open.
CREATE TABLE IF NOT EXISTS experience_marks (
    task_id     TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'live',
    states_seen TEXT NOT NULL DEFAULT '[]',    -- state ids already turned into stages or held open
    open_tail   TEXT NOT NULL DEFAULT '[]',    -- state ids not yet in a closed stage
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (task_id, scope)
);

-- Which states a nature record was written from: the link from what the
-- user did to what it says about them.
-- Each project's workspace: the folder its work lives in, read for its
-- stages. Detected for a project the listener saw, from where its states
-- point; chosen by the user for a folder they add - work from before the
-- agent was listening. processed_until is how far it has been read: a pass
-- reads only what changed after it, up to the stage pass's cutoff.
CREATE TABLE IF NOT EXISTS experience_workspaces (
    unit_id          TEXT PRIMARY KEY,
    scope            TEXT NOT NULL DEFAULT 'live',
    path             TEXT NOT NULL,
    origin           TEXT NOT NULL DEFAULT 'detected',  -- detected or chosen
    name             TEXT NOT NULL DEFAULT '',
    summary          TEXT NOT NULL DEFAULT '{}',        -- {title, goal, status}
    processed_until  TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    paths            TEXT DEFAULT '[]',                 -- a project of several folders
    processing_until TEXT DEFAULT ''                    -- an unfinished run's cutoff
);

-- What the stage writer was given for a piece of work, call by call: its
-- system prompt, every message in order - the work, its frontier, its
-- states, and for a workspace each round of reading with what came back -
-- and its answer. The last few per piece of work, to be read whole.
CREATE TABLE IF NOT EXISTS experience_prompts (
    id          TEXT PRIMARY KEY,
    unit_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,                 -- states or workspace
    system      TEXT NOT NULL DEFAULT '',
    messages    TEXT NOT NULL DEFAULT '[]',
    answer      TEXT NOT NULL DEFAULT '',
    calls       INTEGER NOT NULL DEFAULT 1,
    written_by  TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'live'   -- live, or the Build now test's
);
CREATE INDEX IF NOT EXISTS idx_experience_prompts_unit ON experience_prompts(unit_id, created_at);

-- The nature records a stage of the work showed: mostly work nature - the
-- user's taste and standards - and some personal, written from the stage
-- and linked to it. A stage finds what it says about the user through
-- these, with no state in between.
CREATE TABLE IF NOT EXISTS stage_natures (
    stage_id    TEXT NOT NULL,
    record_id   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (stage_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_stage_natures_record ON stage_natures(record_id);

-- What each state's work was done with or on - a picture of a slide, a
-- document or email read, a file opened or saved, a page, the user's own
-- words to an agent - linked when the state is written, with the copies
-- kept of it: its text, the raw file, the picture. A stage takes its
-- references from these and searches nothing again.
CREATE TABLE IF NOT EXISTS state_materials (
    state_id    TEXT NOT NULL,
    event_id    TEXT NOT NULL,                 -- the raw event it was seen in
    kind        TEXT NOT NULL,                 -- picture, file, screen, page, message
    title       TEXT NOT NULL DEFAULT '',
    location    TEXT NOT NULL DEFAULT '',      -- the original: a path, a URL, a window
    copies      TEXT NOT NULL DEFAULT '{}',    -- {text, raw, picture}: paths kept
    linked_by   TEXT NOT NULL DEFAULT '',      -- model, evidence, or session
    created_at  TEXT NOT NULL,
    PRIMARY KEY (state_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_state_materials_event ON state_materials(event_id);

CREATE TABLE IF NOT EXISTS nature_links (
    state_id    TEXT NOT NULL,
    record_id   TEXT NOT NULL,                 -- a user_nature record, rec_...
    run_id      TEXT NOT NULL DEFAULT '',      -- the heartbeat run that wrote both
    created_at  TEXT NOT NULL,
    PRIMARY KEY (state_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_nature_links_record ON nature_links(record_id);

-- How the user works, in two layers: patterns are groups of nature records
-- about one way of working; principles are groups of patterns.
CREATE TABLE IF NOT EXISTS experience_patterns (
    id          TEXT PRIMARY KEY,
    scope       TEXT NOT NULL DEFAULT 'live',
    layer       TEXT NOT NULL,                 -- pattern or principle
    parent      TEXT NOT NULL DEFAULT '',      -- a pattern's principle
    statement   TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    members     TEXT NOT NULL DEFAULT '[]',    -- record ids, or pattern ids
    support     INTEGER NOT NULL DEFAULT 0,    -- records under it, all the way down
    written_by  TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    nature_kind TEXT DEFAULT ''                -- work or personal; '' not yet known
);
CREATE INDEX IF NOT EXISTS idx_experience_patterns_layer ON experience_patterns(scope, layer);

-- Which kind of nature record each is: WORK (their taste and standards in
-- work, and how they like to work together - for the agent planning their
-- work) or PERSONAL (habits, rhythm, interests - for a companion). Kept
-- here rather than only in the record, so the records written before there
-- were two kinds can be told apart without rewriting their files.
CREATE TABLE IF NOT EXISTS nature_kinds (
    record_id   TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,                 -- work, personal, or both
    written_by  TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

-- The agent working on a piece of the user's work, when the user starts it:
-- a plan, its steps, the evaluations along the way, and what it delivered.
-- What it produced is in the work's own folder; this is the record of how.
CREATE TABLE IF NOT EXISTS work_runs (
    id          TEXT PRIMARY KEY,
    unit_id     TEXT NOT NULL,                 -- the project or task worked on
    unit_name   TEXT NOT NULL DEFAULT '',
    folder      TEXT NOT NULL DEFAULT '',      -- the only place it may write
    status      TEXT NOT NULL DEFAULT 'running',
    instruction TEXT NOT NULL DEFAULT '',
    plan        TEXT NOT NULL DEFAULT '{}',
    log         TEXT NOT NULL DEFAULT '[]',
    final       TEXT NOT NULL DEFAULT '{}',
    started_at  TEXT NOT NULL,
    ended_at    TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_runs_unit ON work_runs(unit_id, started_at);

-- Folders the user granted a project's work, beside its own folder: the
-- agent may create, change and delete anything in them. Granted with a
-- warning to keep a copy elsewhere; never inside the application's state.
CREATE TABLE IF NOT EXISTS work_folders (
    unit_id     TEXT NOT NULL,
    path        TEXT NOT NULL,
    added_at    TEXT NOT NULL,
    PRIMARY KEY (unit_id, path)
);

-- The Experience debugger's projects: the same work done three ways side by
-- side - one model alone, builder and reviewer with no experience, and the
-- whole system - each way in its own subfolder of the one folder the user
-- chose. Never one of the user's projects: nothing here reaches Experience.
-- Each way's runs are work_runs rows under the unit "<id>:<way>".
CREATE TABLE IF NOT EXISTS debug_projects (
    id          TEXT PRIMARY KEY,              -- dbg_<hex>
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    instruction TEXT NOT NULL DEFAULT '',      -- the one initial input of all three
    folder      TEXT NOT NULL,                 -- the folder the user chose
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_task_loose (
    state_id   TEXT NOT NULL,
    scope      TEXT NOT NULL DEFAULT 'live',
    since      TEXT NOT NULL,
    PRIMARY KEY (state_id, scope)
);
CREATE INDEX IF NOT EXISTS idx_state_task_loose_scope ON state_task_loose(scope);

-- Section 52.9: what each run of the state heartbeat did, so that the
-- panel can say why the grouping did or did not change. One row a run.
CREATE TABLE IF NOT EXISTS state_task_runs (
    id          TEXT PRIMARY KEY,
    ran_at      TEXT NOT NULL,
    states      INTEGER NOT NULL DEFAULT 0,
    arrivals    INTEGER NOT NULL DEFAULT 0,
    placed      INTEGER NOT NULL DEFAULT 0,
    loose       INTEGER NOT NULL DEFAULT 0,
    ambiguous   INTEGER NOT NULL DEFAULT 0,
    rebuilt     INTEGER NOT NULL DEFAULT 0,
    signals     TEXT NOT NULL DEFAULT '[]',   -- each one's value, limit, why
    lineage     TEXT NOT NULL DEFAULT '[]',   -- what became what, on a rebuild
    -- The tree as it stood at the last rebuild, kept because an
    -- appending round does not build one: joining every state to every
    -- other is the work that appending exists to avoid, and the picture
    -- would otherwise vanish from the page on every round but the rare
    -- one. Written only on a rebuild, and only the newest keeps it.
    tree        TEXT NOT NULL DEFAULT '',
    -- How far back this grouping has ever looked. The first round reads a
    -- bounded window - the last month, and at most a thousand states - so
    -- on a calendar of five thousand there are four thousand it never
    -- read. Those are not arrivals waiting to be noticed; they are before
    -- the beginning, and this is the line that says so. Without it every
    -- later round would find four thousand states in no task and rebuild
    -- on them forever.
    seen_from   TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT 'live',
    -- The settings that decide how the tree is READ, as they stood when
    -- this grouping was built. Without them a change to any of those
    -- settings does nothing until something else happens to force a
    -- rebuild, which may be days: the user moves the number, the page
    -- shows the same tasks, and the setting looks broken.
    shape       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_state_task_runs_when ON state_task_runs(scope, ran_at DESC);

-- Section 52.4: what the agent asked the user about a task, and what they said.
--
-- Two kinds of question live here. An APPROVAL is asked before a task runs,
-- when the agent is not sure the user wants it done at all. A REVIEW is asked
-- after one has run, and asks whether scheduling it was reasonable. Both end
-- as the same thing: a recorded judgement of the agent's own decision.
CREATE TABLE IF NOT EXISTS task_questions (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    question    TEXT NOT NULL,
    task_detail TEXT NOT NULL DEFAULT '',
    arrangement TEXT NOT NULL DEFAULT '',
    asked_at    TEXT NOT NULL,
    answered_at TEXT,
    answer      TEXT,
    comment     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    node_id     TEXT NOT NULL DEFAULT '',
    metadata    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_questions_open ON task_questions(status, asked_at);

-- Section 44.1, as amended: what each answer was built from, until the data
-- heartbeat can judge it against the answer it produced.
CREATE TABLE IF NOT EXISTS context_uses (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    task_id    TEXT NOT NULL DEFAULT '',
    question   TEXT NOT NULL DEFAULT '',
    answer     TEXT NOT NULL DEFAULT '',
    node_ids   TEXT NOT NULL DEFAULT '[]',
    edge_ids   TEXT NOT NULL DEFAULT '[]',
    -- Section 14.8: how the context was put together, kept so that the
    -- Debugger can show what an answer was actually built from.
    context    TEXT NOT NULL DEFAULT '{}',
    processed  INTEGER NOT NULL DEFAULT 0,
    -- Section 41.3: folded into the co-use counts. A second reader of these
    -- rows, keeping its own place independently of `processed`.
    linked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_context_uses_pending ON context_uses(processed, created_at);

-- Section 41.3: what is retrieved together, counted and not yet consulted.
--
-- A weighted graph over memories is only worth walking once its weights are
-- more than single observations, and that is a quantity rather than an
-- opinion. These two tables are the evidence; nothing in a retrieval reads
-- them, and `co_use.stats()` is what says whether that should change.
CREATE TABLE IF NOT EXISTS node_access (
    node_id  TEXT PRIMARY KEY,
    times    INTEGER NOT NULL DEFAULT 0,
    last_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS node_links (
    source    TEXT NOT NULL,
    target    TEXT NOT NULL,
    together  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, target)
);
CREATE INDEX IF NOT EXISTS idx_node_links_source ON node_links(source, together DESC);

-- Section 14.36, as amended: what every gathering cost, and how much was on
-- disk when it was measured. A search over files grows with what is stored,
-- so "is this still fast" is not a question that can be answered once - the
-- size is recorded per row because reading today's size against last month's
-- timings would compare two different systems.
-- Section 14.35, as amended: every model call, kept across restarts.
--
-- This lived in a deque of four hundred entries and began again at zero every
-- time the application started, which makes "is this getting more expensive"
-- unanswerable - and the agent restarts whenever anything is fixed. The
-- gathering cost below was already written down for exactly this reason.
CREATE TABLE IF NOT EXISTS model_calls (
    id             TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    provider       TEXT NOT NULL DEFAULT '',
    model          TEXT NOT NULL DEFAULT '',
    -- Which kind of work spent it: a chat turn, a heartbeat step, a fold.
    -- "How much" without "on what" is not actionable.
    task           TEXT NOT NULL DEFAULT 'general',
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    total_tokens   INTEGER NOT NULL DEFAULT 0,
    latency_ms     REAL NOT NULL DEFAULT 0,
    ok             INTEGER NOT NULL DEFAULT 1,
    error          TEXT NOT NULL DEFAULT '',
    estimated_cost REAL,
    -- Input read from the prompt cache, and input written to it. Kept apart
    -- from input_tokens, which is the uncached part only: 4,139 calls on one
    -- machine recorded 11,084 input tokens between them, because everything
    -- they read was read from the cache.
    cached_input_tokens  INTEGER DEFAULT 0,
    cache_written_tokens INTEGER DEFAULT 0,
    -- Which language it was told to write in: JSON rows, "" when not told.
    language       TEXT DEFAULT '',
    -- 1 when the user started the run it was made in, with a button.
    by_user        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_model_calls_when ON model_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_model_calls_task ON model_calls(task, created_at);

-- The calendar nature (heartbeat/core/calendar_nature.py): each add,
-- disable or delete the user makes on the calendar, and the running text of
-- what those say about the notifications they want. Not a work nature: it is
-- never grouped, and only the step that schedules notifications reads it.
CREATE TABLE IF NOT EXISTS calendar_actions (
    id          TEXT PRIMARY KEY,
    acted_at    TEXT NOT NULL,
    action      TEXT NOT NULL,              -- add | disable | delete
    task_id     TEXT NOT NULL DEFAULT '',
    event       TEXT NOT NULL DEFAULT '{}', -- the event as it was, JSON
    reasoned_at TEXT,                       -- NULL until a model has said why
    why         TEXT,
    lesson      TEXT                        -- what to do from now on
);
CREATE INDEX IF NOT EXISTS idx_calendar_actions_pending
    ON calendar_actions(reasoned_at, acted_at);
CREATE TABLE IF NOT EXISTS calendar_nature (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind       TEXT NOT NULL,               -- lesson (summary | entry from before)
    content    TEXT NOT NULL,
    action_id  TEXT                         -- the action that added it
);

-- Every change made to the notification lessons: by which action, what it
-- was before and after, and why - nothing changed without a trace.
CREATE TABLE IF NOT EXISTS calendar_lesson_changes (
    id         TEXT PRIMARY KEY,
    at         TEXT NOT NULL,
    action_id  TEXT NOT NULL DEFAULT '',    -- '' when the user changed it by hand
    op         TEXT NOT NULL,               -- add | edit | delete | carried
    lesson_id  TEXT NOT NULL,
    before     TEXT NOT NULL DEFAULT '',
    after      TEXT NOT NULL DEFAULT '',
    why        TEXT NOT NULL DEFAULT ''
);

-- THE LESSONS, LEARNED FROM EVIDENCE (heartbeat/lessons/engine.py): every
-- kind of lesson - notifications, connections - one system. Evidence is what
-- the user did (and what came of the agent acting on a lesson); a rule is a
-- hypothesis until enough evidence backs it, then a lesson the agent is
-- given; a link says how one piece of evidence bears on one rule.
CREATE TABLE IF NOT EXISTS lesson_evidence (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    source_id    TEXT NOT NULL DEFAULT '',
    at           TEXT NOT NULL,
    action       TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    weight       REAL NOT NULL DEFAULT 1.0,
    features     TEXT,
    processed_at TEXT,
    outcome_of   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS lesson_evidence_kind ON lesson_evidence (kind, processed_at);
CREATE TABLE IF NOT EXISTS lesson_rules (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    statement   TEXT NOT NULL,
    conditions  TEXT NOT NULL DEFAULT '{}',
    state       TEXT NOT NULL DEFAULT 'candidate',  -- candidate | active | retired | rejected
    pinned      INTEGER NOT NULL DEFAULT 0,
    support     REAL NOT NULL DEFAULT 0,
    contradict  REAL NOT NULL DEFAULT 0,
    confidence  REAL NOT NULL DEFAULT 0.5,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    history     TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS lesson_links (
    evidence_id TEXT NOT NULL,
    rule_id     TEXT NOT NULL,
    relation    TEXT NOT NULL,           -- supports | contradicts
    why         TEXT NOT NULL DEFAULT '',
    at          TEXT NOT NULL,
    PRIMARY KEY (evidence_id, rule_id)
);
CREATE TABLE IF NOT EXISTS lesson_uses (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    rule_ids    TEXT NOT NULL DEFAULT '[]',
    subject_id  TEXT NOT NULL DEFAULT '',
    decision    TEXT NOT NULL DEFAULT '',
    at          TEXT NOT NULL,
    resolved_at TEXT
);

-- WHAT THE USER READ (heartbeat/reading): a piece per thing read or
-- watched, in English, and the topics they fall into.
CREATE TABLE IF NOT EXISTS reading_pieces (
    id          TEXT PRIMARY KEY,
    first_at    TEXT NOT NULL,
    last_at     TEXT NOT NULL,
    url         TEXT NOT NULL DEFAULT '',
    url_key     TEXT NOT NULL DEFAULT '',
    title       TEXT NOT NULL DEFAULT '',
    title_key   TEXT NOT NULL DEFAULT '',
    app         TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'other',
    topics      TEXT NOT NULL DEFAULT '[]',
    entities    TEXT NOT NULL DEFAULT '[]',
    points      TEXT NOT NULL DEFAULT '[]',
    summary     TEXT NOT NULL DEFAULT '',
    seconds     REAL NOT NULL DEFAULT 0,
    visits      INTEGER NOT NULL DEFAULT 1,
    characters  INTEGER NOT NULL DEFAULT 0,
    fetched     INTEGER NOT NULL DEFAULT 0,
    engagement  REAL NOT NULL DEFAULT 0,
    topic_id    TEXT NOT NULL DEFAULT '',
    vector      TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reading_pieces_url ON reading_pieces(url_key);
CREATE INDEX IF NOT EXISTS idx_reading_pieces_title ON reading_pieces(title_key);
CREATE INDEX IF NOT EXISTS idx_reading_pieces_topic ON reading_pieces(topic_id);
-- The user's say on a story of Customized Recommendation, for its topic.
-- Agreed, its time not known yet (heartbeat/core/pending.py).
CREATE TABLE IF NOT EXISTS pending_confirmations (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    people      TEXT NOT NULL DEFAULT '[]',
    evidence    TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'open',
    by          TEXT NOT NULL DEFAULT 'agent',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    closed_why  TEXT NOT NULL DEFAULT '',
    task_id     TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS work_drafts (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'delegate',
    text        TEXT NOT NULL DEFAULT '',
    folders     TEXT NOT NULL DEFAULT '[]',
    files       TEXT NOT NULL DEFAULT '[]',
    window      TEXT NOT NULL DEFAULT '{}',
    unit_id     TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'collecting',
    context     TEXT NOT NULL DEFAULT '{}',
    plan        TEXT NOT NULL DEFAULT '{}',
    plan_by     TEXT NOT NULL DEFAULT '',
    options     TEXT NOT NULL DEFAULT '{}',
    run_id      TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS todo_items (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    text            TEXT NOT NULL DEFAULT '',
    detail          TEXT NOT NULL DEFAULT '',
    notification_id TEXT NOT NULL DEFAULT '',
    task_id         TEXT NOT NULL DEFAULT '',
    done_at         TEXT NOT NULL DEFAULT '',
    done_by         TEXT NOT NULL DEFAULT '',
    done_why        TEXT NOT NULL DEFAULT '',
    deleted_at      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS rec_feedback (
    id          TEXT PRIMARY KEY,
    at          TEXT NOT NULL,
    issue_id    TEXT NOT NULL DEFAULT '',
    story_id    TEXT NOT NULL DEFAULT '',
    topic_id    TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS reading_topics (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL DEFAULT '',
    centre      TEXT NOT NULL DEFAULT '[]',
    pieces      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_timings (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    -- Who asked: chat, heartbeat_step, scheduled_task, calendar_completion,
    -- data_heartbeat, debugger. They ask different questions at different
    -- rates, and one overall mean hides which is expensive.
    kind            TEXT NOT NULL DEFAULT 'unknown',
    query           TEXT NOT NULL DEFAULT '',
    duration_ms     REAL NOT NULL DEFAULT 0,
    hits            INTEGER NOT NULL DEFAULT 0,
    opened          INTEGER NOT NULL DEFAULT 0,
    library_records INTEGER NOT NULL DEFAULT 0,
    library_bytes   INTEGER NOT NULL DEFAULT 0,
    task_id         TEXT NOT NULL DEFAULT '',
    -- How far the gathering went. The graph counted walk steps; a library
    -- counts the two things that cost something: index searches, and files
    -- actually opened and read.
    searches        INTEGER NOT NULL DEFAULT 0,
    -- What this gathering contributed to the prompt, estimated from the text
    -- it assembled. Attributable to the gathering alone, unlike the call.
    context_tokens  INTEGER NOT NULL DEFAULT 0,
    -- The model call this gathering fed, filled in after it returns. One call
    -- can be fed by several gatherings - the agent heartbeat assembles four,
    -- one per step, and makes a single call - so call_id is what keeps the
    -- per-model averages from counting those tokens four times.
    model           TEXT NOT NULL DEFAULT '',
    provider        TEXT NOT NULL DEFAULT '',
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    call_id         TEXT NOT NULL DEFAULT '',
    call_gatherings INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_retrieval_timings_size
    ON retrieval_timings(library_records);
CREATE INDEX IF NOT EXISTS idx_retrieval_timings_when
    ON retrieval_timings(created_at);

-- Section 50.2, as amended: when each record was last actually read.
--
-- Compaction folds away what has gone cold, and half of "cold" is free - a
-- file says when it was written. The other half was recorded nowhere: a
-- record opened by every second answer looked identical to one nobody had
-- touched since it was made. Kept here rather than on the file because a read
-- that writes would rewrite the library continuously and make the index think
-- everything had changed.
CREATE TABLE IF NOT EXISTS record_access (
    record_id    TEXT PRIMARY KEY,
    last_read_at TEXT NOT NULL DEFAULT '',
    reads        INTEGER NOT NULL DEFAULT 0
);

-- Section 50.2, as amended: folds waiting for a model.
--
-- A fold needs a model, and without one the work waits rather than being done
-- badly. This is the same rule the raw activity follows (section 90): what
-- cannot be processed properly stays pending and is reported, because the
-- alternative here is worse than elsewhere - a summary written without a model
-- is permanent, and the records it covers drop out of the index and sit behind
-- it for ever.
--
-- Attempts are counted so that one group the model keeps failing on cannot
-- block the queue behind it for ever.
CREATE TABLE IF NOT EXISTS pending_folds (
    id              TEXT PRIMARY KEY,   -- folder|level|period
    folder          TEXT NOT NULL DEFAULT '',
    level           INTEGER NOT NULL DEFAULT 0,
    period          TEXT NOT NULL DEFAULT '',
    record_ids      TEXT NOT NULL DEFAULT '[]',
    records         INTEGER NOT NULL DEFAULT 0,
    first_seen_at   TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT NOT NULL DEFAULT '',
    last_error      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pending_folds_order
    ON pending_folds(attempts, first_seen_at);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

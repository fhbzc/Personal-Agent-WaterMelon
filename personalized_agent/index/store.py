"""THE INDEX ITSELF: one SQLite file of its own, apart from the
application's database - it is written by a process of its own, at length,
and read while it is.

    files       every file kept: its path, the folder it was found under,
                its size and time (what tells a change), and how it is kept
                ("text": its words; "talk": a conversation record's, as
                talk.py keeps it; "name": its name alone)
    pieces      each LINES_PER_PIECE lines of a text file: where they start
    piece_words the words of each piece (FTS5, rowid = the piece)
    name_words  the words of each file's name and its folders' (FTS5,
                rowid = the file)
    roots       each folder indexed: how many files, how far, when done
                ("within": walked with a folder it is inside)
    meta        what the index was built by (the rules' version)

The words are kept, not the text: the full-text tables keep no copy of what
they index where SQLite can delete from one that does not
(`contentless_delete`, from 3.43); what a search finds is read from the file
itself when it is shown. On an older SQLite they keep the words they index -
never the text of the file.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .tokens import body, query

#: SQLite from which a full-text table that keeps no copy can be deleted
#: from (`contentless_delete=1`).
CONTENTLESS_FROM = (3, 43, 0)


def fts5_available() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        con.close()
        return True
    except sqlite3.Error:
        return False


class IndexStore:
    """The index file: written by the builder, read by searches."""

    def __init__(self, path: Path, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            self.con = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True,
                                       timeout=10, check_same_thread=False)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.con = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
            self.con.execute("PRAGMA journal_mode=WAL")
            self.con.execute("PRAGMA synchronous=NORMAL")
            self._schema()
        self.con.row_factory = sqlite3.Row

    def _schema(self) -> None:
        keeps = "content='', contentless_delete=1" \
            if sqlite3.sqlite_version_info >= CONTENTLESS_FROM else ""
        tables = [
            "CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT "
            "NULL, root TEXT NOT NULL, size INTEGER, mtime REAL, kind TEXT, indexed_at REAL)",
            "CREATE INDEX IF NOT EXISTS files_root ON files(root)",
            "CREATE TABLE IF NOT EXISTS pieces (id INTEGER PRIMARY KEY, file_id INTEGER NOT "
            "NULL, line INTEGER NOT NULL)",
            "CREATE INDEX IF NOT EXISTS pieces_file ON pieces(file_id)",
            "CREATE VIRTUAL TABLE IF NOT EXISTS piece_words USING fts5(body"
            + (f", {keeps}" if keeps else "") + ")",
            "CREATE VIRTUAL TABLE IF NOT EXISTS name_words USING fts5(body"
            + (f", {keeps}" if keeps else "") + ")",
            "CREATE TABLE IF NOT EXISTS roots (path TEXT PRIMARY KEY, files INTEGER DEFAULT 0,"
            " texts INTEGER DEFAULT 0, started_at REAL, done_at REAL, state TEXT)",
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
        ]
        for one in tables:
            self.con.execute(one)
        self.con.commit()

    def close(self) -> None:
        try:
            self.con.close()
        except sqlite3.Error:                    # pragma: no cover
            pass

    # -- written by the builder --------------------------------------------------------
    def known(self, root: str) -> Dict[str, Tuple[int, int, float]]:
        """Every file kept under a folder: path -> (id, size, time)."""
        return {row["path"]: (row["id"], row["size"], row["mtime"]) for row in
                self.con.execute("SELECT id, path, size, mtime FROM files WHERE root = ?",
                                 (root,))}

    def known_under(self, root: str) -> Dict[str, Tuple[int, int, float, str, str]]:
        """Every file kept under a folder, whichever folder it was found
        under before - one inside it, walked on its own until then:
        path -> (id, size, time, how kept, the folder it is under)."""
        prefix = root.rstrip("\\/") + os.sep
        same = "lower(substr(path, 1, ?)) = lower(?)" if os.name == "nt" \
            else "substr(path, 1, ?) = ?"
        return {row["path"]: (row["id"], row["size"], row["mtime"], row["kind"], row["root"])
                for row in self.con.execute(
                    f"SELECT id, path, size, mtime, kind, root FROM files WHERE root = ? OR {same}",
                    (root, len(prefix), prefix))}

    def move(self, file_id: int, root: str) -> None:
        """A file kept as it was, now under the folder it is walked in."""
        self.con.execute("UPDATE files SET root = ? WHERE id = ?", (root, file_id))

    def meta(self, key: str) -> str:
        try:
            row = self.con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error:
            return ""
        return str(row[0]) if row else ""

    def set_meta(self, key: str, value: str) -> None:
        self.con.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def tidy(self, loose: float = 0.25) -> Dict[str, Any]:
        """THE FILE MADE SMALL AGAIN. What is forgotten leaves its room in
        the file, kept for what comes next; when more than `loose` of it is
        such room - a folder taken out, the rules changed - the file is
        written again without it (VACUUM). Done by the builder, at its end:
        nothing else writes the file then."""
        pages = int(self.con.execute("PRAGMA page_count").fetchone()[0] or 0)
        free = int(self.con.execute("PRAGMA freelist_count").fetchone()[0] or 0)
        said: Dict[str, Any] = {"pages": pages, "free": free, "vacuumed": False}
        if not pages or free / pages <= loose:
            return said
        self.con.commit()
        self.con.execute("VACUUM")
        self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        said["vacuumed"] = True
        said["pages_after"] = int(self.con.execute("PRAGMA page_count").fetchone()[0] or 0)
        return said

    def put(self, path: str, root: str, size: int, mtime: float, kind: str,
            pieces: Iterable[Tuple[int, str]], name: str) -> int:
        """A file kept anew: whatever was kept of it before goes first."""
        row = self.con.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
        if row is not None:
            self.drop(row[0])
        cursor = self.con.execute(
            "INSERT INTO files (path, root, size, mtime, kind, indexed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (path, root, int(size), float(mtime), kind,
                                           time.time()))
        file_id = int(cursor.lastrowid)
        self.con.execute("INSERT INTO name_words (rowid, body) VALUES (?, ?)",
                         (file_id, body(name)))
        for line, words in pieces:
            if not words:
                continue
            piece = self.con.execute("INSERT INTO pieces (file_id, line) VALUES (?, ?)",
                                     (file_id, int(line)))
            self.con.execute("INSERT INTO piece_words (rowid, body) VALUES (?, ?)",
                             (int(piece.lastrowid), words))
        return file_id

    def drop(self, file_id: int) -> None:
        for (piece,) in self.con.execute("SELECT id FROM pieces WHERE file_id = ?",
                                         (file_id,)).fetchall():
            self.con.execute("DELETE FROM piece_words WHERE rowid = ?", (piece,))
        self.con.execute("DELETE FROM pieces WHERE file_id = ?", (file_id,))
        self.con.execute("DELETE FROM name_words WHERE rowid = ?", (file_id,))
        self.con.execute("DELETE FROM files WHERE id = ?", (file_id,))

    def drop_root(self, root: str) -> int:
        """Everything kept of a folder no longer indexed."""
        ids = [row[0] for row in self.con.execute("SELECT id FROM files WHERE root = ?",
                                                   (root,)).fetchall()]
        for one in ids:
            self.drop(one)
        self.con.execute("DELETE FROM roots WHERE path = ?", (root,))
        self.con.commit()
        return len(ids)

    def mark(self, root: str, **said: Any) -> None:
        self.con.execute("INSERT OR IGNORE INTO roots (path) VALUES (?)", (root,))
        for key, value in said.items():
            if key in ("files", "texts", "started_at", "done_at", "state"):
                self.con.execute(f"UPDATE roots SET {key} = ? WHERE path = ?", (value, root))

    def commit(self) -> None:
        self.con.commit()

    # -- read by searches --------------------------------------------------------------
    def roots(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self.con.execute("SELECT * FROM roots ORDER BY path")]

    def search(self, text: str, roots: Optional[List[str]] = None,
               limit: int = 20) -> List[Dict[str, Any]]:
        """The pieces and the names that hold the words asked - every word,
        or, when nothing holds all of them, any - the best first (BM25),
        each with its path, where in the file, and how it was found. Only
        under `roots`, when given."""
        found: List[Dict[str, Any]] = []
        for any_word in (False, True):
            asked = query(text, any_word=any_word)
            if not asked:
                return []
            found = self._pieces(asked, roots, limit) + self._names(asked, roots, limit)
            if found:
                break
        found.sort(key=lambda one: one["score"])
        seen, out = set(), []
        for one in found:
            key = (one["path"], one.get("line"))
            if key not in seen:
                seen.add(key)
                out.append(one)
        return out[:limit]

    def _where(self, roots: Optional[List[str]]) -> Tuple[str, List[Any]]:
        if not roots:
            return "", []
        return (" AND f.root IN (" + ",".join("?" for _ in roots) + ")", list(roots))

    def _pieces(self, asked: str, roots: Optional[List[str]], limit: int) -> List[Dict[str, Any]]:
        where, extra = self._where(roots)
        try:
            rows = self.con.execute(
                "SELECT f.path, f.mtime, f.kind, p.line, bm25(piece_words) AS score FROM piece_words "
                "JOIN pieces p ON p.id = piece_words.rowid JOIN files f ON f.id = p.file_id "
                f"WHERE piece_words MATCH ?{where} ORDER BY score LIMIT ?",
                [asked, *extra, limit * 3]).fetchall()
        except sqlite3.Error:
            return []
        return [{"path": row["path"], "line": row["line"], "score": float(row["score"]),
                 "mtime": row["mtime"], "found_in": "text", "kind": row["kind"]} for row in rows]

    def _names(self, asked: str, roots: Optional[List[str]], limit: int) -> List[Dict[str, Any]]:
        where, extra = self._where(roots)
        try:
            rows = self.con.execute(
                "SELECT f.path, f.mtime, f.kind, bm25(name_words) AS score FROM name_words "
                "JOIN files f ON f.id = name_words.rowid "
                f"WHERE name_words MATCH ?{where} ORDER BY score LIMIT ?",
                [asked, *extra, limit]).fetchall()
        except sqlite3.Error:
            return []
        # A name found counts a little less than the words of a text: it is
        # what a file is called, not what it says.
        return [{"path": row["path"], "line": None, "score": float(row["score"]) * 0.8,
                 "mtime": row["mtime"], "found_in": "name", "kind": row["kind"]}
                for row in rows]

    def counts(self) -> Dict[str, int]:
        one = self.con.execute("SELECT COUNT(*) AS files, SUM(kind IN ('text', 'talk')) AS texts "
                               "FROM files").fetchone()
        return {"files": int(one["files"] or 0), "texts": int(one["texts"] or 0)}


__all__ = ["IndexStore", "fts5_available"]

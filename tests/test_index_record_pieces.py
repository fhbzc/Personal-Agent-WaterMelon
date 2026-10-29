"""A PIECE OF A CONVERSATION RECORD SHOWN FROM WHERE IT STARTS (the user,
2026-10-29): search_files showed six lines of a 1.4 GB record by reading
all of it - 46 seconds, every search. Each piece is now kept with where in
the file it starts, and shown by reading from there - the same lines as
before; a record kept by an index made before is read again once by the
next build; and a text file's piece is read only as far as it goes."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401

from personalized_agent.index import service, talk
from personalized_agent.index.build import build, pieces_of
from personalized_agent.index.rules import LINES_PER_PIECE
from personalized_agent.index.store import IndexStore, fts5_available
from personalized_agent.index.talk import lines_from, lines_of, talk_kept, where_lines_are


def _line(**entry) -> str:
    return json.dumps(entry) + "\n"


def _record(turns: int = 60) -> str:
    """A long fictional record: turns of several lines, a turn that keeps
    nothing, a line break splitlines() counts that \\n does not."""
    out = []
    for n in range(turns):
        out.append(_line(type="user", message={"role": "user", "content":
                         f"Question {n} about the seed planner.\nIts second line {n}."}))
        out.append(_line(type="user", isMeta=True, message={"role": "user", "content": "meta"}))
        out.append(_line(type="assistant", message={"role": "assistant", "content": [
            {"type": "text", "text": f"Answer {n}, first part.\nSecond part  of {n}.\n"
                                     f"Third part of {n}."},
            {"type": "tool_use", "name": "Grep", "input": {"pattern": f"sow_{n}",
                                                           "path": "/home/sam/seeds"}}]}))
    return "".join(out)


class _Folder(unittest.TestCase):

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.records = self.root / "records"
        self.records.mkdir()
        self.record = self.records / "session-one.jsonl"
        self.record.write_text(_record(), encoding="utf-8")

    def tearDown(self) -> None:
        self.directory.cleanup()


class TheSameLinesFromWhereItStartsTests(_Folder):

    def test_every_piece_the_same_as_read_whole(self) -> None:
        text, starts = talk_kept(self.record)
        self.assertEqual(text, talk.talk_text(self.record))
        pieces = pieces_of(text)
        self.assertGreater(len(pieces), 5)
        for line, _words in pieces:
            at, skip = where_lines_are(starts, line)
            self.assertEqual(lines_from(self.record, at, skip, LINES_PER_PIECE),
                             lines_of(text, line, LINES_PER_PIECE), f"the piece at line {line}")

    def test_a_record_added_to_keeps_where_its_pieces_start(self) -> None:
        text, starts = talk_kept(self.record)
        line = pieces_of(text)[3][0]
        at, skip = where_lines_are(starts, line)
        with open(self.record, "a", encoding="utf-8") as handle:
            handle.write(_record(5))
        self.assertEqual(lines_from(self.record, at, skip, LINES_PER_PIECE),
                         lines_of(text, line, LINES_PER_PIECE))


@unittest.skipUnless(fts5_available(), "this SQLite has no FTS5")
class KeptAndShownTests(_Folder):

    def setUp(self) -> None:
        super().setUp()
        self.store = IndexStore(self.root / "files.db")

    def tearDown(self) -> None:
        self.store.close()
        super().tearDown()

    def _build(self):
        return build(self.store, [{"path": str(self.records), "sessions": True}])

    def test_each_piece_kept_with_where_it_starts_and_shown_from_there(self) -> None:
        self._build()
        found = self.store.search("sow_41")
        self.assertTrue(found and found[0]["at"] is not None, found)
        one = found[0]
        text = talk.talk_text(self.record)
        # Shown without reading the record whole.
        with mock.patch.object(service, "talk_text", side_effect=AssertionError("read whole")):
            shown = service.LocalIndex._lines(one["path"], one["line"], "talk", {},
                                              at=one["at"], skip=one["skip"])
        self.assertEqual(shown, lines_of(text, one["line"], LINES_PER_PIECE)[:service.MOST_SHOWN])
        self.assertIn("sow_41", shown)

    def test_an_index_made_before_is_given_the_columns_and_its_records_read_once(self) -> None:
        self.store.close()
        old = self.root / "old.db"
        made = IndexStore(old)
        made.con.execute("DROP TABLE pieces")
        made.con.execute("CREATE TABLE pieces (id INTEGER PRIMARY KEY, file_id INTEGER NOT "
                         "NULL, line INTEGER NOT NULL)")
        made.con.commit()
        made.close()
        made = IndexStore(old)
        self.assertIn("at", made._columns("pieces"), "added to an index made before")
        build(made, [{"path": str(self.records), "sessions": True}])
        made.set_meta("talk_at", "")
        made.con.execute("UPDATE pieces SET at = NULL, skip = NULL")
        made.con.commit()
        made.close()
        # Read as an index made before: shown by name and line when too big.
        reading = IndexStore(old, read_only=True)
        hit = reading.search("sow_41")[0]
        reading.close()
        self.assertIsNone(hit["at"])
        with mock.patch.object(service, "MOST_RECORD_READ_WHOLE", 10):
            self.assertEqual(service.LocalIndex._lines(hit["path"], hit["line"], "talk", {}), "")
        self.assertIn("sow_41", service.LocalIndex._lines(hit["path"], hit["line"], "talk", {}))
        # The next build reads it again, once - with where each piece starts.
        self.store = IndexStore(old)
        said = build(self.store, [{"path": str(self.records), "sessions": True}])
        self.assertEqual(said["read"], 1)
        self.assertIsNotNone(self.store.search("sow_41")[0]["at"])
        self.assertEqual(build(self.store, [{"path": str(self.records), "sessions": True}])
                         ["unchanged"], 1)


class ATextFilesPieceTests(unittest.TestCase):

    def test_only_its_lines_read_and_the_same_as_before(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "rows.csv"
            path.write_bytes("".join(f"row {n},{n * 2}\r\n" for n in range(1, 301))
                             .encode("utf-8"))
            shown = service.LocalIndex._lines(str(path), 41, "text")
            self.assertEqual(shown.splitlines(), [f"row {n},{n * 2}" for n in range(41, 81)])
            self.assertNotIn("\r", shown)
            self.assertEqual(service.LocalIndex._lines(str(path), 290, "text").splitlines()[-1],
                             "row 300,600")


if __name__ == "__main__":
    unittest.main()

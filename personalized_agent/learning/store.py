"""Reading and writing the learning's tables (data_manage/storage/database.py:
learn_*): units, cases, judgments, reasons and how judgments bear on them,
the updates, and a Work run's questions."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

#: How strong each kind of evidence is - the order a reason's evidence is
#: weighed in, strongest first. "written": a rule the user wrote down;
#: "noted": one the agent noted from the user's words (its memory);
#: "changed": the user changed or turned down what was there; "approved":
#: they said yes to it; "accepted": they let it stand; "endorsed": they
#: put someone else's work in the learning list as a reference, saying what
#: of it they endorse (learning/items.py) - or the heartbeat found them
#: saying so (learning/interests.py).
STRENGTHS = ("written", "noted", "endorsed", "changed", "approved", "accepted")
WEIGHT = {"written": 3.0, "noted": 2.5, "endorsed": 2.0, "changed": 2.0, "approved": 1.5,
          "accepted": 1.0}
#: A unit the user turned off, or one whose first reading waits for them to
#: look: nothing learned from it is given (learning/find.py).
HELD = ("inactive", "pending")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def loads(text: Any, default: Any) -> Any:
    if isinstance(text, (dict, list)):
        return text
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


JSON_FIELDS = {
    "learn_units": {"folders": [], "sessions": [], "facets": {}, "marks": {}},
    "learn_cases": {"facets": {}, "locations": [], "versions": [], "items": []},
    "learn_judgments": {"refs": []},
    "learn_reasons": {"condition": {}, "objects": [], "predictions": [], "history": []},
    "learn_updates": {"plan": {}, "skipped": []},
    "work_run_questions": {"found": {}, "answer": {}},
}


def _row(table: str, row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out = dict(row)
    for name, default in JSON_FIELDS.get(table, {}).items():
        if name in out:
            out[name] = loads(out[name], type(default)())
    return out


class LearningStore:
    def __init__(self, db: Any) -> None:
        self.db = db

    # -- generic --------------------------------------------------------------
    def _get(self, table: str, key: str, value: Any) -> Optional[Dict[str, Any]]:
        return _row(table, self.db.query_one(f"SELECT * FROM {table} WHERE {key} = ?",
                                             (value,)))

    def _all(self, table: str, where: str = "", args: Sequence[Any] = ()
             ) -> List[Dict[str, Any]]:
        sql = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "")
        return [_row(table, row) for row in self.db.query(sql, tuple(args))]

    def _put(self, table: str, row: Dict[str, Any], key: str = "id") -> None:
        values = dict(row)
        for name in JSON_FIELDS.get(table, {}):
            if name in values and not isinstance(values[name], str):
                values[name] = dumps(values[name])
        names = list(values)
        holes = ", ".join("?" for _ in names)
        updates = ", ".join(f"{name} = excluded.{name}" for name in names if name != key)
        self.db.execute(
            f"INSERT INTO {table} ({', '.join(names)}) VALUES ({holes}) "
            f"ON CONFLICT({key}) DO UPDATE SET {updates}",
            tuple(values[name] for name in names))

    # -- units ----------------------------------------------------------------
    #: What a unit carries that is not a column: kept in its marks.
    UNIT_EXTRAS = ("only_files", "leave_out")
    UNIT_COLUMNS = ("id", "kind", "key", "title", "summary", "folders", "sessions", "facets",
                    "marks", "state", "first_read", "updated_at")

    @classmethod
    def _unit_out(cls, row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        marks = row.get("marks") or {}
        for key in cls.UNIT_EXTRAS:
            if "_" + key in marks:
                row[key] = marks["_" + key]
        return row

    def unit(self, unit_id: str) -> Optional[Dict[str, Any]]:
        return self._unit_out(self._get("learn_units", "id", unit_id))

    def units(self) -> List[Dict[str, Any]]:
        return [self._unit_out(row) for row in self._all("learn_units")]

    def set_unit_state(self, unit_id: str, state: str) -> None:
        self.db.execute("UPDATE learn_units SET state = ?, updated_at = ? WHERE id = ?",
                        (state, now(), unit_id))

    def held_units(self) -> List[str]:
        """The units nothing learned from is given: turned off, or waiting
        for the user to look at their first reading."""
        holes = ", ".join("?" for _ in HELD)
        return [row["id"] for row in self.db.query(
            f"SELECT id FROM learn_units WHERE state IN ({holes})", HELD)]

    def forget_unit(self, unit_id: str) -> List[str]:
        """EVERYTHING LEARNED FROM A UNIT, GONE: its cases, their judgments
        and how they bore on reasons, the unit itself. The reasons they bore
        on, for the caller to weigh again (or remove, with nothing left)."""
        cases = [row["id"] for row in self.db.query(
            "SELECT id FROM learn_cases WHERE unit_id = ?", (unit_id,))]
        reasons: List[str] = []
        for case_id in cases:
            for judgment in self.db.query("SELECT id FROM learn_judgments WHERE case_id = ?",
                                          (case_id,)):
                for link in self.links_of_judgment(judgment["id"]):
                    if link["reason_id"] not in reasons:
                        reasons.append(link["reason_id"])
                self.db.execute("DELETE FROM learn_links WHERE judgment_id = ?",
                                (judgment["id"],))
                self.db.execute("DELETE FROM learn_overrides WHERE judgment_id = ?",
                                (judgment["id"],))
            self.db.execute("DELETE FROM learn_judgments WHERE case_id = ?", (case_id,))
        if cases:
            holes = ", ".join("?" for _ in cases)
            self.db.execute(f"DELETE FROM learn_cases WHERE id IN ({holes}) OR alias_of IN "
                            f"({holes})", tuple(cases) * 2)
        self.db.execute("DELETE FROM learn_units WHERE id = ?", (unit_id,))
        return reasons

    def drop_reason(self, reason_id: str) -> None:
        self.db.execute("DELETE FROM learn_links WHERE reason_id = ?", (reason_id,))
        self.db.execute("DELETE FROM learn_overrides WHERE winner = ? OR loser = ?",
                        (reason_id, reason_id))
        self.db.execute("UPDATE learn_reasons SET rival = '' WHERE rival = ?", (reason_id,))
        self.db.execute("DELETE FROM learn_reasons WHERE id = ?", (reason_id,))

    def unit_counts(self) -> Dict[str, Dict[str, int]]:
        """For each unit: its cases and judgments, and the reasons they bear on."""
        out: Dict[str, Dict[str, int]] = {}
        for row in self.db.query("SELECT unit_id AS u, COUNT(*) AS n FROM learn_cases "
                                 "WHERE alias_of = '' GROUP BY unit_id"):
            out.setdefault(row["u"], {})["cases"] = int(row["n"])
        for row in self.db.query(
                "SELECT c.unit_id AS u, COUNT(DISTINCT j.id) AS n, COUNT(DISTINCT l.reason_id) AS r "
                "FROM learn_judgments j JOIN learn_cases c ON c.id = j.case_id "
                "LEFT JOIN learn_links l ON l.judgment_id = j.id AND l.role = 'supports' "
                "GROUP BY c.unit_id"):
            mine = out.setdefault(row["u"], {})
            mine["judgments"] = int(row["n"])
            mine["reasons"] = int(row["r"])
        return out

    def save_unit(self, unit: Dict[str, Any]) -> None:
        marks = dict(unit.get("marks") or {})
        for key in self.UNIT_EXTRAS:
            if key in unit:
                marks["_" + key] = unit[key]
        row = {key: unit[key] for key in self.UNIT_COLUMNS if key in unit}
        row["marks"] = marks
        row["updated_at"] = now()
        self._put("learn_units", row)

    # -- folders --------------------------------------------------------------
    def folder(self, path: str) -> Optional[Dict[str, Any]]:
        row = self.db.query_one("SELECT * FROM learn_folders WHERE path = ?", (path,))
        if row:
            row = dict(row)
            row["verdict"] = loads(row.get("verdict"), {})
        return row

    def save_folder(self, path: str, place: str, fingerprint: str, summary: str,
                    verdict: Dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO learn_folders (path, place, fingerprint, summary, verdict, at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(path) DO UPDATE SET place = excluded.place, "
            "fingerprint = excluded.fingerprint, summary = excluded.summary, "
            "verdict = excluded.verdict, at = excluded.at",
            (path, place, fingerprint, summary, dumps(verdict), now()))

    # -- cases ----------------------------------------------------------------
    def case(self, case_id: str) -> Optional[Dict[str, Any]]:
        row = self._get("learn_cases", "id", case_id)
        seen = set()
        while row and row.get("alias_of") and row["id"] not in seen:
            seen.add(row["id"])
            row = self._get("learn_cases", "id", row["alias_of"])
        return row

    def cases(self, unit_id: str = "") -> List[Dict[str, Any]]:
        if unit_id:
            return self._all("learn_cases", "unit_id = ? AND alias_of = ''", (unit_id,))
        return self._all("learn_cases", "alias_of = ''")

    def save_case(self, case: Dict[str, Any]) -> str:
        case = dict(case)
        case.setdefault("id", new_id("C"))
        case.setdefault("created_at", now())
        case["updated_at"] = now()
        self._put("learn_cases", case)
        return case["id"]

    # -- judgments ------------------------------------------------------------
    def judgment(self, judgment_id: str) -> Optional[Dict[str, Any]]:
        return self._get("learn_judgments", "id", judgment_id)

    def judgments(self, where: str = "", args: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        return self._all("learn_judgments", where, args)

    def judgments_of(self, case_id: str) -> List[Dict[str, Any]]:
        return self.judgments("case_id = ?", (case_id,))

    def save_judgment(self, judgment: Dict[str, Any]) -> str:
        judgment = dict(judgment)
        judgment.setdefault("id", new_id("J"))
        judgment.setdefault("created_at", now())
        self._put("learn_judgments", judgment)
        return judgment["id"]

    def set_judgment_state(self, judgment_id: str, state: str) -> None:
        self.db.execute("UPDATE learn_judgments SET state = ? WHERE id = ?",
                        (state, judgment_id))

    # -- reasons --------------------------------------------------------------
    def reason(self, reason_id: str) -> Optional[Dict[str, Any]]:
        return self._get("learn_reasons", "id", reason_id)

    def reasons(self, states: Iterable[str] = ("candidate", "active", "competing")
                ) -> List[Dict[str, Any]]:
        states = list(states)
        holes = ", ".join("?" for _ in states)
        return self._all("learn_reasons", f"state IN ({holes})", states)

    def save_reason(self, reason: Dict[str, Any]) -> str:
        reason = dict(reason)
        reason.setdefault("id", new_id("R"))
        reason.setdefault("created_at", now())
        reason["updated_at"] = now()
        self._put("learn_reasons", reason)
        return reason["id"]

    # -- links ----------------------------------------------------------------
    def link(self, reason_id: str, judgment_id: str, role: str, why: str = "",
             predicted: bool = False) -> None:
        self.db.execute(
            "INSERT INTO learn_links (reason_id, judgment_id, role, predicted, why, at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(reason_id, judgment_id) DO UPDATE SET "
            "role = excluded.role, predicted = excluded.predicted, why = excluded.why, "
            "at = excluded.at", (reason_id, judgment_id, role, int(predicted), why[:400], now()))

    def unlink(self, reason_id: str, judgment_id: str) -> None:
        self.db.execute("DELETE FROM learn_links WHERE reason_id = ? AND judgment_id = ?",
                        (reason_id, judgment_id))

    def links_of_reason(self, reason_id: str) -> List[Dict[str, Any]]:
        return self.db.query("SELECT * FROM learn_links WHERE reason_id = ?", (reason_id,))

    def links_of_judgment(self, judgment_id: str) -> List[Dict[str, Any]]:
        return self.db.query("SELECT * FROM learn_links WHERE judgment_id = ?",
                             (judgment_id,))

    def evidence(self, reason_id: str) -> List[Dict[str, Any]]:
        """A reason's judgments, each with how it bears on it."""
        out = []
        for link in self.links_of_reason(reason_id):
            judgment = self.judgment(link["judgment_id"])
            if judgment:
                judgment["role"] = link["role"]
                judgment["predicted"] = bool(link["predicted"])
                judgment["link_why"] = link["why"]
                out.append(judgment)
        return out

    def override(self, winner: str, loser: str, when_text: str, judgment_id: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO learn_overrides (winner, loser, when_text, judgment_id, at) "
            "VALUES (?, ?, ?, ?, ?)", (winner, loser, when_text[:400], judgment_id, now()))

    def overrides(self) -> List[Dict[str, Any]]:
        return self.db.query("SELECT * FROM learn_overrides")

    # -- updates --------------------------------------------------------------
    def update(self, update_id: str) -> Optional[Dict[str, Any]]:
        return self._get("learn_updates", "id", update_id)

    def open_update(self) -> Optional[Dict[str, Any]]:
        """The update not finished yet - running, paused or waiting."""
        rows = self._all("learn_updates", "state IN ('running', 'paused', 'waiting') "
                         "ORDER BY started_at DESC LIMIT 1")
        return rows[0] if rows else None

    def last_update(self) -> Optional[Dict[str, Any]]:
        rows = self._all("learn_updates", "1 = 1 ORDER BY started_at DESC LIMIT 1")
        return rows[0] if rows else None

    def last_finished(self) -> Optional[Dict[str, Any]]:
        rows = self._all("learn_updates", "state = 'done' ORDER BY cutoff DESC LIMIT 1")
        return rows[0] if rows else None

    def save_update(self, update: Dict[str, Any]) -> str:
        update = dict(update)
        update.setdefault("id", new_id("U"))
        update.setdefault("started_at", now())
        self._put("learn_updates", update)
        return update["id"]

    # -- a Work run's questions -----------------------------------------------
    def save_question(self, run_id: str, step: str, asked_by: str, question: str,
                      kind: str, found: Dict[str, Any], answer: Dict[str, Any]) -> int:
        row = self.db.query_one("SELECT COALESCE(MAX(n), 0) AS n FROM work_run_questions "
                                "WHERE run_id = ?", (run_id,))
        number = int(row["n"] if row else 0) + 1
        self.db.execute(
            "INSERT INTO work_run_questions (run_id, n, step, asked_by, question, kind, found, "
            "answer, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, number, step, asked_by, question[:2000], kind, dumps(found),
             dumps(answer), now()))
        return number

    def answer_question(self, run_id: str, number: int, answer: Dict[str, Any]) -> None:
        self.db.execute("UPDATE work_run_questions SET answer = ? WHERE run_id = ? AND n = ?",
                        (dumps(answer), run_id, number))

    def questions(self, run_id: str) -> List[Dict[str, Any]]:
        return self._all("work_run_questions", "run_id = ? ORDER BY n", (run_id,))

    # -- counts ---------------------------------------------------------------
    def counts(self) -> Dict[str, int]:
        out = {}
        for name, sql in (("units", "SELECT COUNT(*) AS n FROM learn_units WHERE state != 'gone'"),
                          ("cases", "SELECT COUNT(*) AS n FROM learn_cases WHERE alias_of = ''"),
                          ("judgments", "SELECT COUNT(*) AS n FROM learn_judgments"),
                          ("reasons", "SELECT COUNT(*) AS n FROM learn_reasons "
                                      "WHERE state IN ('active', 'competing')"),
                          ("forming", "SELECT COUNT(*) AS n FROM learn_reasons "
                                      "WHERE state = 'candidate'")):
            row = self.db.query_one(sql)
            out[name] = int(row["n"]) if row else 0
        return out

    def breakdown(self) -> Dict[str, Dict[str, int]]:
        """What the counts are made of, for the Learning panel: the places by
        kind, the judgments by how strong and what about and whether a reason
        explains them, the reasons by whose words and how far along, the
        terms fixed or on trial."""
        groups = {
            "places": "SELECT kind AS k, COUNT(*) AS n FROM learn_units "
                      "WHERE state != 'gone' GROUP BY kind",
            "places_with_cases": "SELECT 'all' AS k, COUNT(DISTINCT unit_id) AS n FROM learn_cases "
                                 "WHERE alias_of = ''",
            "judgments_strength": "SELECT strength AS k, COUNT(*) AS n FROM learn_judgments "
                                  "GROUP BY strength",
            "judgments_aspect": "SELECT aspect AS k, COUNT(*) AS n FROM learn_judgments "
                                "GROUP BY aspect",
            "judgments_state": "SELECT state AS k, COUNT(*) AS n FROM learn_judgments "
                               "GROUP BY state",
            "reasons_in_use": "SELECT source AS k, COUNT(*) AS n FROM learn_reasons "
                              "WHERE state IN ('active', 'competing') GROUP BY source",
            "reasons_forming": "SELECT source AS k, COUNT(*) AS n FROM learn_reasons "
                               "WHERE state = 'candidate' GROUP BY source",
            "reasons_aspect": "SELECT aspect AS k, COUNT(*) AS n FROM learn_reasons "
                              "WHERE state IN ('active', 'competing') GROUP BY aspect",
            "reasons_competing": "SELECT 'all' AS k, COUNT(*) AS n FROM learn_reasons "
                                 "WHERE state = 'competing'",
            "terms": "SELECT state AS k, COUNT(*) AS n FROM learn_terms "
                     "WHERE alias_of = '' GROUP BY state",
        }
        out: Dict[str, Dict[str, int]] = {}
        for name, sql in groups.items():
            out[name] = {str(row["k"] or ""): int(row["n"]) for row in self.db.query(sql)}
        return out

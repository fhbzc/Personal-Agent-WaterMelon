"""WHAT THE SCREEN SHOWED, LEARNED - AND WHAT THE EARLIER LEARNING FOUND (learning/activity.py; the
user, 2026-11-07: a new source of the learning, all history learned again, for every user by
itself). No model is called: each answer is made up, and so is everything read - fictional
work (a harbour survey, a choir's concert, a bicycle repair)."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401

from personalized_agent.data_manage.storage.database import Database
from personalized_agent.learning import activity as act
from personalized_agent.learning import records
from personalized_agent.learning.service import Learning
from personalized_agent.learning.store import LearningStore

NOW = datetime.now(timezone.utc)


def _at(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


class _Config(dict):
    def __init__(self, *args, **kwargs) -> None:
        # No place of this computer's (learning/places.py, `default_places`).
        super().__init__({"learning_places": []}, *args, **kwargs)

    def set(self, key, value, internal=False):
        self[key] = value
        return value


READ = {"cases": [{"ref": "new1", "title": "Survey slides",
                   "facets": {"deliverable": ["deliverable:slides"]}, "looks_like": "Short."}],
        "judgments": [{"case": "new1", "aspect": "output", "object": "object:amount of text",
                       "direction": "fewer words a page", "by": "user", "strength": "changed",
                       "at": "2026-09-02"}],
        "new_terms": []}


class _Models:
    """A model that answers the learning: a reading with `READ` (or what `read` gives), every
    other call with nothing to do. Keeps what each reading was shown."""

    def __init__(self, read=None) -> None:
        self.read = read or (lambda request: (json.dumps(READ), "end_turn"))
        self.reads = []
        self.calls = []

    def invoke(self, request, role=""):
        self.calls.append(request.task)
        if request.task == "learning_read":
            self.reads.append((request.system, request.messages[0]["content"]))
            text, stop = self.read(request)
        else:
            text, stop = "{}", "end_turn"
        return SimpleNamespace(text=text, stop_reason=stop, usage=None)


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="learning-activity-"))
        self.db = Database(self.dir / "agent.db")
        self.store = LearningStore(self.db)
        self.config = _Config()
        self.models = _Models()
        # Nothing of this computer's own: no conversation, no rules file of its agents.
        for patch in (mock.patch.object(records, "global_rule_files", lambda: []),):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self) -> None:
        self.db.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- made-up history ------------------------------------------------------------------
    def state(self, ident, title, days, about="user", kind="work", status="active", reason="",
              detail="worked on it"):
        at = _at(days)
        self.db.execute(
            "INSERT INTO states (id, title, detail, source, about, starts_at, ends_at, status, "
            "reason, created_at, updated_at, metadata) VALUES (?, ?, ?, 'agent', ?, ?, ?, ?, ?, ?, "
            "?, ?)", (ident, title, detail, about, at, _at(days - 0.01), status, reason, at, at,
                      json.dumps({"kind": kind})))
        return ident

    def project(self, ident, title, kind="work", ended=False, scope="live"):
        self.db.execute(
            "INSERT INTO state_tasks (id, number, members, described, created_at, updated_at, "
            "retired_at, profile, scope) VALUES (?, 1, '[]', ?, ?, ?, ?, ?, ?)",
            (ident, json.dumps({"title": title, "what_it_is": f"the {title}", "kind": kind}),
             _at(30), _at(1), _at(1) if ended else None, json.dumps({"scheme": "projects"}), scope))

    def before(self, ident, title, members):
        """A task of the older grouping, before the projects."""
        self.db.execute(
            "INSERT INTO state_tasks (id, number, members, described, created_at, updated_at, "
            "profile) VALUES (?, 1, ?, ?, ?, ?, '{}')",
            (ident, json.dumps(members), json.dumps({"title": title}), _at(60), _at(60)))

    def task(self, ident, project, states, days, kind="work", scope="live"):
        self.db.execute(
            "INSERT INTO state_run_tasks (id, scope, written_at, title, summary, kind, states, "
            "minutes, starts_at, ends_at, places, project_id) VALUES (?, ?, ?, ?, ?, ?, ?, 30, ?, "
            "?, '{}', ?)", (ident, scope, _at(days), f"task {ident}", "what was done", kind,
                            json.dumps(states), _at(days + 0.05), _at(days), project))

    def pattern(self, ident, statement, layer="pattern", parent="", support=7, scope="live"):
        self.db.execute(
            "INSERT INTO experience_patterns (id, scope, layer, parent, statement, detail, "
            "members, support, created_at, updated_at, last_active) VALUES (?, ?, ?, ?, ?, "
            "'seen in several pieces of work', '[]', ?, ?, ?, ?)",
            (ident, scope, layer, parent, statement, support, _at(40), _at(30), _at(30)))

    # -- the learning ---------------------------------------------------------------------
    def learning(self):
        learning = Learning(self.db, self.config, self.models, self.dir / "data")
        learning.places.sessions = lambda *a, **k: []
        return learning

    def update(self):
        learning = self.learning()
        update_id = learning.store.save_update({"cutoff": NOW.isoformat(), "by": "you",
                                                "state": "running", "plan": {}})
        before = len(self.models.reads)
        learning._go(update_id)
        return learning.store.update(update_id), self.models.reads[before:]

    def read_states(self):
        return {row["state_id"]: dict(row) for row in self.db.query(
            "SELECT * FROM learn_from_activity")}


class TheScreenIsASourceTests(_Base):

    def test_each_piece_of_work_a_unit_its_states_read_once(self) -> None:
        self.project("P-choir", "Choir concert", kind="leisure")
        self.state("s1", "Arranging the choir's second song", 3)
        self.state("s2", "Writing the concert programme", 2.9)
        self.task("T1", "P-choir", ["s1", "s2"], 2.8)
        update, reads = self.update()
        self.assertEqual(update["state"], "done")
        unit = self.store.unit(act.unit_id_of("P-choir"))
        self.assertEqual(unit["kind"], "activity")
        self.assertEqual(unit["title"], "Choir concert")
        self.assertEqual(len(reads), 1)
        system, shown = reads[0]
        self.assertIn("WHAT THE SCREEN SHOWED", system)
        self.assertIn("Arranging the choir's second song", shown)
        self.assertIn("A TASK", shown)
        self.assertEqual(set(self.read_states()), {"s1", "s2"})
        cases = self.store.cases(unit["id"])
        self.assertEqual(len(cases), 1)
        self.assertEqual(self.store.judgments_of(cases[0]["id"])[0]["direction"], "fewer words a page")
        # Read once: nothing again.
        _update, reads = self.update()
        self.assertEqual(reads, [])
        # What came after, and only that.
        self.state("s3", "Printing the concert tickets", 0.5)
        self.task("T2", "P-choir", ["s3"], 0.4)
        _update, reads = self.update()
        self.assertEqual(len(reads), 1)
        self.assertIn("Printing the concert tickets", reads[0][1])
        self.assertNotIn("Arranging the choir's second song", reads[0][1])
        self.assertEqual(self.store.unit(unit["id"])["marks"]["activity"][:10], _at(0.45)[:10])

    def test_a_work_project_is_read_into_its_own_unit(self) -> None:
        self.project("P-survey", "Harbour survey")
        self.state("s1", "Drawing the tide chart for the survey", 2)
        self.task("T1", "P-survey", ["s1"], 1.9)
        _update, reads = self.update()
        unit = self.store.unit(act.work_unit_id("P-survey"))
        self.assertEqual(unit["kind"], "work")
        self.assertIsNone(self.store.unit(act.unit_id_of("P-survey")), "never two units of one")
        self.assertIn("Drawing the tide chart", "\n".join(shown for _s, shown in reads))
        self.assertEqual(self.read_states()["s1"]["unit_id"], unit["id"])
        self.assertTrue(self.store.cases(unit["id"]))

    def test_before_the_projects_and_what_no_grouping_took(self) -> None:
        self.state("s1", "Patching the bicycle's rear tyre", 50)
        self.before("old-1", "Bicycle repair", ["s1"])
        self.state("s2", "Looking up a spoke wrench", 40)
        self.state("s3", "Reading about gear ratios", 0.2)        # the round has not come to it
        self.project("P-survey", "Harbour survey", ended=True)
        self.state("s4", "Survey wrap-up", 1)
        self.task("T1", "P-survey", ["s4"], 0.1)
        _update, reads = self.update()
        before = self.store.unit(act.unit_id_of("before:old-1"))
        loose = self.store.unit(act.unit_id_of("loose"))
        self.assertEqual(before["title"], "Bicycle repair")
        self.assertEqual(loose["title"], "Screen activity in no project")
        read = self.read_states()
        self.assertEqual(read["s1"]["unit_id"], before["id"])
        self.assertEqual(read["s2"]["unit_id"], loose["id"])
        self.assertEqual(read["s4"]["unit_id"], act.unit_id_of("P-survey"), "an ended project too")
        self.assertNotIn("s3", read, "waits for the project round")

    def test_never_the_agents_routine_retired_nor_put_away(self) -> None:
        self.project("P-choir", "Choir concert", kind="leisure")
        self.state("s1", "Rehearsing the choir", 3)
        self.state("s2", "The agent will book the hall", 3, about="agent")
        self.state("s3", "Lunch", 3, kind="routine")
        self.state("s4", "Thought to be a rehearsal", 3, status="retired")
        self.state("s5", "Put away by the user", 3)
        self.task("T1", "P-choir", ["s1", "s2", "s3", "s4"], 2.9)
        self.task("T2", "P-choir", ["s5"], 2.9)
        self.db.execute("UPDATE state_run_tasks SET put_away = ? WHERE id = 'T2'", (NOW.isoformat(),))
        _update, reads = self.update()
        shown = "\n".join(text for _s, text in reads)
        self.assertIn("Rehearsing the choir", shown)
        for never in ("book the hall", "Lunch", "Thought to be", "Put away by the user"):
            self.assertNotIn(never, shown)
        self.assertEqual(set(self.read_states()), {"s1"})


class WhatStaysOutTests(_Base):

    def test_left_out_places_private_windows_and_the_debuggers_stay_out(self) -> None:
        self.config.set("learning_excluded", [str(Path("/home/sam/secret-plans"))])
        self.project("P-choir", "Choir concert", kind="leisure")
        self.state("s1", "Rehearsing the choir", 3)
        self.state("s2", "Editing a plan", 3, reason="seen: /home/sam/secret-plans/plan.md open")
        self.state("s3", "A browser window", 3, reason="seen: an InPrivate window in front")
        self.task("T1", "P-choir", ["s1", "s2", "s3"], 2.9)
        # The Experience debugger's and the sandbox's are not the user's.
        self.project("P-sandbox", "Sandbox project", kind="leisure", scope="sandbox")
        self.state("s4", "A debugger's state", 3)
        self.task("T2", "P-sandbox", ["s4"], 2.9, scope="sandbox")
        self.pattern("dbg_1", "A debugger's pattern", scope="work:dbg_42:full")
        _update, reads = self.update()
        shown = "\n".join(text for _s, text in reads)
        self.assertIn("Rehearsing the choir", shown)
        for never in ("secret-plans", "InPrivate", "A debugger's"):
            self.assertNotIn(never, shown)
        self.assertEqual(set(self.read_states()), {"s1"}, "what is never shown is not marked read")
        self.assertIsNone(self.store.unit(act.EARLIER_UNIT))

    def test_a_unit_deleted_is_never_read_again(self) -> None:
        self.project("P-choir", "Choir concert", kind="leisure")
        self.state("s1", "Rehearsing the choir", 3)
        self.task("T1", "P-choir", ["s1"], 2.9)
        self.update()
        unit_id = act.unit_id_of("P-choir")
        self.learning().delete(unit_id)
        self.state("s2", "Ordering choir folders", 1)
        self.task("T2", "P-choir", ["s2"], 0.9)
        _update, reads = self.update()
        self.assertEqual(reads, [])
        self.assertIsNone(self.store.unit(unit_id))


class TheEarlierLearningTests(_Base):

    def test_its_patterns_become_judgments_once(self) -> None:
        self.pattern("pr-1", "Writes plainly to people senior to them", layer="principle")
        self.pattern("pa-1", "Emails to a harbour master: two sentences, one ask", parent="pr-1",
                     support=7)
        self.models.read = lambda request: (json.dumps({
            "cases": [{"ref": "new1", "title": "Emails to officials",
                       "facets": {"deliverable": ["deliverable:email"]}}],
            "judgments": [
                {"pattern": "pa-1", "case": "new1", "aspect": "output",
                 "object": "object:amount of text", "direction": "two sentences", "at": ""},
                {"pattern": "pa-9", "case": "new1", "aspect": "output",
                 "object": "object:amount of text", "direction": "never shown"}]}), "end_turn")
        _update, reads = self.update()
        self.assertEqual(len(reads), 1)
        system, shown = reads[0]
        self.assertIn("AN EARLIER LEARNING", system)
        self.assertIn("PATTERN pa-1 (seen in 7", shown)
        self.assertIn("under the principle: Writes plainly", shown)
        unit = self.store.unit(act.EARLIER_UNIT)
        self.assertEqual(unit["kind"], "earlier")
        judgments = self.store.judgments_of(self.store.cases(unit["id"])[0]["id"])
        self.assertEqual(len(judgments), 1, "only of a pattern shown")
        one = judgments[0]
        self.assertEqual(one["strength"], "earlier")
        self.assertEqual(one["said"], "", "never the user's words")
        self.assertEqual(one["refs"], ["earlier pattern pa-1"])
        self.assertIn("in 7 of their pieces of work", one["why"])
        self.assertEqual(one["at"], _at(30)[:10])
        _update, reads = self.update()
        self.assertEqual(reads, [], "read once")

    def test_two_found_by_it_make_a_reason_active_as_two_changed_would(self) -> None:
        from personalized_agent.learning.reasons import Reasons
        cases = [self.store.save_case({"unit_id": act.EARLIER_UNIT, "title": f"Kind {n}"})
                 for n in (1, 2)]
        ids = [self.store.save_judgment({"case_id": case, "aspect": "output",
                                         "object": "object:amount of text", "direction": "short",
                                         "strength": "earlier", "state": "linked"})
               for case in cases]
        rid = self.store.save_reason({"statement": "Kept short", "source": "inferred",
                                      "state": "candidate", "condition": {"text": "emails"}})
        for one in ids:
            self.store.link(rid, one, "supports")
        reason = Reasons(self.db, lambda *a, **k: {}).rescore(rid)
        self.assertEqual(reason["state"], "active")
        self.assertEqual(reason["support"], 3.0)


class EveryUserByItselfTests(_Base):

    def test_an_old_user_its_history_planned_once_at_its_pace(self) -> None:
        self.project("P-choir", "Choir concert", kind="leisure")
        self.project("P-bike", "Bicycle repair", kind="leisure")
        for n in range(4):
            self.state(f"c{n}", f"Choir work {n} " + "x" * 300, 20 - n)
            self.state(f"b{n}", f"Bike work {n} " + "y" * 300, 10 - n)
            self.task(f"TC{n}", "P-choir", [f"c{n}"], 19.9 - n)
            self.task(f"TB{n}", "P-bike", [f"b{n}"], 9.9 - n)
        self.pattern("pa-1", "Emails: two sentences")
        said = self.learning().catch_up()
        self.assertEqual(said["found"], {"states": 8, "patterns": 1})
        self.assertNotIn("done", said)
        # At most this much an update: the earlier patterns first, then the latest work.
        with mock.patch.object(act, "PER_UPDATE", 1200):
            update, reads = self.update()
            self.assertTrue(any("PATTERN pa-1" in shown for _s, shown in reads))
            read = self.read_states()
            self.assertTrue({"b3", "b2"} <= set(read), read)
            self.assertNotIn("c0", read, "the oldest waits")
            self.assertLess(len(read), 8)
            self.assertEqual(update["plan"]["history"], {"states": 8, "patterns": 1},
                             "what was left when it was planned")
            for _ in range(8):
                self.update()
        self.assertEqual(len(self.read_states()), 8)
        said = self.learning().catch_up()
        self.assertTrue(said["done"])
        self.assertEqual(said["found"], {"states": 8, "patterns": 1}, "said once, kept")
        self.assertEqual(said["left"], {"states": 0, "patterns": 0})

    def test_a_new_user_nothing_to_do(self) -> None:
        said = self.learning().catch_up()
        self.assertEqual(said["found"], {"states": 0, "patterns": 0})
        self.assertTrue(said["done"])
        update, reads = self.update()
        self.assertEqual(reads, [])
        self.assertEqual(update["plan"]["units"], [])
        self.assertFalse(any(one["kind"] in ("activity", "earlier") for one in self.store.units()))


class ACutAnswerTests(_Base):

    def test_asked_in_halves_then_recorded_failed_and_moved_past(self) -> None:
        self.project("P-choir", "Choir concert", kind="leisure")
        self.state("s1", "Rehearsing the choir", 3)
        self.state("s2", "Printing the programme", 2)
        self.task("T1", "P-choir", ["s1"], 2.9)
        self.task("T2", "P-choir", ["s2"], 1.9)
        self.models.read = lambda request: ('{"cases": [{"ref": "new1", "title": "Conc', "max_tokens")
        update, reads = self.update()
        self.assertEqual(len(reads), 3, "whole, then each half")
        read = self.read_states()
        self.assertEqual(set(read), {"s1", "s2"})
        self.assertTrue(all("cut off" in one["failed"] for one in read.values()))
        unit = self.store.unit(act.unit_id_of("P-choir"))
        self.assertEqual(len(unit["marks"]["failed"]), 2)
        self.assertEqual(self.store.cases(unit["id"]), [], "nothing kept of it")
        self.assertEqual(update["state"], "done")
        _update, reads = self.update()
        self.assertEqual(reads, [], "never asked again the same way")

    def test_a_call_not_answered_at_all_is_read_again(self) -> None:
        self.project("P-choir", "Choir concert", kind="leisure")
        self.state("s1", "Rehearsing the choir", 3)
        self.task("T1", "P-choir", ["s1"], 2.9)

        def down(request):
            raise ConnectionError("the service is down")
        self.models.read = down
        self.update()
        self.assertEqual(self.read_states(), {})
        self.models.read = lambda request: (json.dumps(READ), "end_turn")
        _update, reads = self.update()
        self.assertEqual(len(reads), 1)
        self.assertEqual(set(self.read_states()), {"s1"})


class TheModelNotThePartsTests(_Base):
    """The user, 2026-11-07: only what the model really answered is a part that failed."""

    def _history(self, n):
        self.project("P-harbour", "Harbour survey")
        for k in range(n):
            self.state(f"s{k}", f"Counting boats at pier {k}", 3 - k * 0.1)
            self.task(f"T{k}", "P-harbour", [f"s{k}"], 2.9 - k * 0.1)

    def test_five_unreadable_in_a_row_stop_the_update_and_mark_nothing(self) -> None:
        self._history(6)
        self.models.read = lambda request: ('{"cases": [{"ref": "new1", "title": "Boa', "max_tokens")
        update, _reads = self.update()
        self.assertEqual(update["state"], "failed")
        self.assertIn("model looks wrong", update["note"])
        self.assertEqual(self.read_states(), {}, "nothing recorded failed")
        self.models.read = lambda request: (json.dumps(READ), "end_turn")
        update, reads = self.update()
        self.assertEqual(update["state"], "done")
        self.assertTrue(reads, "read again once the model answers")
        self.assertEqual(len(self.read_states()), 6)
        self.assertTrue(all(not one["failed"] for one in self.read_states().values()))

    def test_the_offline_stand_in_learns_nothing(self) -> None:
        self._history(1)
        real = self.models.invoke

        def offline(request, role=""):
            said = real(request, role)
            return SimpleNamespace(text="Offline: a reply.", stop_reason="end_turn",
                                   usage=None, provider="offline")
        self.models.invoke = offline
        update, _reads = self.update()
        self.assertEqual(update["state"], "failed")
        self.assertIn("offline", update["note"])
        self.assertEqual(self.read_states(), {})

    def test_fewer_unreadable_are_the_parts_own_failure(self) -> None:
        self._history(2)
        self.models.read = lambda request: ("I could not say.", "end_turn")
        update, _reads = self.update()
        self.assertEqual(update["state"], "done")
        self.assertEqual(len(self.read_states()), 2)
        self.assertTrue(all(one["failed"] for one in self.read_states().values()))


class SoMuchAnUpdateTests(_Base):
    """The user, 2026-11-08: on by default each morning - at most so many pieces an update, the
    rest read next time."""

    def test_at_most_so_many_pieces_and_the_rest_next_time(self) -> None:
        for n, name in enumerate(("harbour", "choir", "bicycle")):
            self.project(f"P-{name}", f"The {name}")
            self.state(f"s{n}", f"Working on the {name}", 3 - n * 0.1)
            self.task(f"T{n}", f"P-{name}", [f"s{n}"], 2.9 - n * 0.1)
        self.config["learning_reads_per_update"] = 2
        _update, reads = self.update()
        self.assertEqual(len(reads), 2, "two pieces, as many as an update may read")
        self.assertEqual(len(self.read_states()), 2)
        _update, reads = self.update()
        self.assertEqual(len(reads), 1, "the one left, the next time")
        self.assertEqual(len(self.read_states()), 3)


class TheSameReasonFoundByMeaningTests(_Base):

    def _reason(self, statement, at):
        from personalized_agent.learning.store import now
        rid = self.store.save_reason({
            "statement": statement, "source": "inferred", "aspect": "output", "state": "active",
            "condition": {"dims": {}, "text": ""}, "objects": [], "predictions": [],
            "history": []})
        self.db.execute("UPDATE learn_reasons SET created_at = ? WHERE id = ?", (at, rid))
        return rid

    def test_a_new_reason_said_otherwise_is_made_one_with_the_kept_one(self) -> None:
        from personalized_agent.learning.reasons import Reasons
        old = self._reason("Boat counts are checked against the harbour log.", "2026-01-01")
        new = self._reason("The user checks each boat count with the harbour's own log.",
                           "2026-11-07")
        other = self._reason("Slides carry few words.", "2026-02-01")
        embedder = SimpleNamespace(
            available=lambda: True,
            embed=lambda texts: [[1.0, 0.0] if "boat" in t.lower() else [0.0, 1.0] for t in texts])
        asked = []

        def ask(kind, system, content, *a, **k):
            asked.append(content)
            return {"same": [{"new": new, "as": old, "statement": "Boat counts match the log."}]}
        reasons = Reasons(self.db, ask, embedder=embedder)
        reasons.merge_near({new})
        self.assertEqual(self.store.reason(new)["state"], "retired")
        self.assertEqual(self.store.reason(old)["statement"], "Boat counts match the log.")
        self.assertEqual(self.store.reason(other)["state"], "active")
        self.assertEqual(len(asked), 1)
        self.assertLess(asked[0].index(old), asked[0].index(other), "nearest first")

    def test_every_reason_is_looked_at_once_then_only_the_new(self) -> None:
        from personalized_agent.learning import reasons as rs
        self._reason("Boat counts are checked against the harbour log.", "2026-01-01")
        looked = []
        reasons = rs.Reasons(self.db, lambda *a, **k: {}, embedder=None)
        reasons.merge_near = lambda new: looked.append(set(new))
        reasons.run(False)
        self.assertEqual(len(looked[0]), 1, "every reason, the first time")
        self.assertTrue(reasons._kv(rs.MERGED_ALL))
        looked.clear()
        reasons.run(False)
        self.assertEqual(looked, [], "then only the new and the said anew")


if __name__ == "__main__":
    unittest.main()

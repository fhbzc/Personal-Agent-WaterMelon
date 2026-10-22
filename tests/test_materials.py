"""A state's materials: linked and kept when the state is written, the
pictures no state wanted dropped at the end of the run, a stage's references
taken from them - and the copies cleared once nothing can cite them."""

from __future__ import annotations

import json
import os
import time
import unittest
import unittest.mock
import zipfile
from datetime import timedelta
from pathlib import Path

from support import TemporaryAgent

from personalized_agent.agent.states.store import State
from personalized_agent.common.events.models import ListenerEvent, utcnow
from personalized_agent.heartbeat.experience import materials as materials_module
from personalized_agent.heartbeat.experience.materials import Materials
from personalized_agent.heartbeat.experience.references import collect_garbage
from personalized_agent.heartbeat.experience.store import ExperienceStore


def _docx(path: Path, words: str) -> Path:
    with zipfile.ZipFile(path, "w") as box:
        box.writestr("word/document.xml",
                     f'<w:document><w:body><w:p><w:r><w:t>{words}</w:t></w:r>'
                     f'</w:p></w:body></w:document>')
    return path


class _Base(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.root = Path(self.app.library.root) / "references"
        self.materials = Materials(self.app.db, self.root)
        self.now = utcnow()
        self.window = {"from": (self.now - timedelta(minutes=15)).isoformat(),
                       "to": self.now.isoformat()}
        self.home = Path(self.app.library.root).parent

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _event(self, kind, minutes_ago, **data):
        event = ListenerEvent(event_type=kind, source="test",
                              timestamp=self.now - timedelta(minutes=minutes_ago),
                              data=data)
        self.app.raw_store.add_event(event)
        return event

    def _state(self, title="They revise the FieldNotes slides.", detail="",
               evidence=()):
        return self.app.states.add(State(
            title=title, detail=detail or title,
            starts_at=self.now - timedelta(minutes=14), ends_at=self.now,
            metadata={"evidence": list(evidence), "looked_at": self.window}))


class WhatIsLinkedTests(_Base):

    def test_the_three_sources_of_links(self) -> None:
        chosen = self._event("browser.navigation", 10, url="https://arxiv.org/abs/1",
                             title="A paper")
        cited = self._event("screen.text", 9, window_title="Notes — Editor",
                            text="the plan for section 3")
        mine = self._event("assistant.message", 8, role="user",
                           session_id="7d1e5b20-aaaa", project=r"C:\work\fieldnotes",
                           text="make the slides denser")
        harness = self._event("assistant.message", 7, role="user",
                              session_id="7d1e5b20-aaaa",
                              text="Another Claude session sent a message: x")
        elsewhere = self._event("assistant.message", 6, role="user",
                                session_id="ffffffff-bbbb", project=r"C:\other",
                                text="unrelated")
        state = self._state(detail="In Claude Code session 7d1e5b20.",
                            evidence=[f"the notes ({cited.id})"])
        made = {m["event_id"]: m for m in
                self.materials.link(state, [chosen.id], self.window)}
        self.assertEqual(set(made), {chosen.id, cited.id, mine.id})
        how = {row["event_id"]: row["linked_by"] for row in self.app.db.query(
            "SELECT event_id, linked_by FROM state_materials")}
        self.assertEqual(how, {chosen.id: "model", cited.id: "evidence",
                               mine.id: "session"})
        self.assertNotIn(harness.id, made)
        self.assertNotIn(elsewhere.id, made)
        words = Path(made[mine.id]["copies"]["text"]).read_text(encoding="utf-8")
        self.assertIn("make the slides denser", words)


class WhatIsKeptTests(_Base):

    def test_a_document_keeps_its_text_and_the_file(self) -> None:
        original = _docx(self.home / "FieldNotes draft.docx", "Notes on caching")
        opened = self._event("filesystem.opened", 5, path=str(original))
        [made] = self.materials.link(self._state(), [opened.id], self.window)
        copies = made["copies"]
        self.assertEqual(Path(copies["raw"]).read_bytes(), original.read_bytes())
        self.assertIn("Notes on caching",
                      Path(copies["text"]).read_text(encoding="utf-8"))
        self.assertTrue(str(copies["raw"]).startswith(str(self.root)))
        # The original is named, never moved.
        self.assertEqual(made["location"], str(original))
        self.assertTrue(original.exists())

    def test_a_pdf_keeps_both(self) -> None:
        pdf = self.home / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 not really")
        opened = self._event("filesystem.opened", 5, path=str(pdf))
        with unittest.mock.patch.object(materials_module, "extract_text",
                                        return_value="the abstract"):
            [made] = self.materials.link(self._state(), [opened.id], self.window)
        self.assertEqual(sorted(made["copies"]), ["raw", "text"])

    def test_a_text_file_is_its_own_text_and_a_big_file_no_copy(self) -> None:
        notes = self.home / "notes.md"
        notes.write_text("# plan", encoding="utf-8")
        big = self.home / "big.pdf"
        big.write_bytes(b"x" * 2048)
        one = self._event("filesystem.modified", 5, path=str(notes))
        two = self._event("filesystem.opened", 4, path=str(big))
        with unittest.mock.patch.object(materials_module, "COPY_AT_MOST", 1024):
            made = {m["event_id"]: m for m in
                    self.materials.link(self._state(), [one.id, two.id], self.window)}
        # A text file: its raw copy is its text - one file, both names.
        copies = made[one.id]["copies"]
        self.assertEqual(sorted(copies), ["raw", "text"])
        self.assertEqual(copies["raw"], copies["text"])
        self.assertNotIn("raw", made[two.id]["copies"])
        self.assertEqual(made[two.id]["location"], str(big))

    def test_a_window_read_in_pieces_is_kept_whole(self) -> None:
        title = "Invitation to work on FieldNotes - Gmail — Mozilla Firefox"
        self._event("screen.text", 9, window_title=title, text="Dear Robin,\nJoin us?")
        last = self._event("screen.text", 8, window_title=title, text="Join us?\nBest, Sam")
        [made] = self.materials.link(self._state(), [last.id], self.window)
        body = Path(made["copies"]["text"]).read_text(encoding="utf-8").split("---", 1)[1]
        self.assertEqual([l for l in body.splitlines() if l],
                         ["Dear Robin,", "Join us?", "Best, Sam"])


class ACopyOutlivesTheRawRowTests(_Base):

    def test_linked_again_after_the_raw_row_is_let_go(self) -> None:
        """The copy is looked for before the raw row, because the copy is
        what is kept: an event a later state cites again, after the raw
        activity has been let go by age, still links to its copy."""
        cited = self._event("browser.navigation", 10, url="https://arxiv.org/abs/1",
                            title="A paper")
        [first] = self.materials.link(self._state(), [cited.id], self.window)
        self.app.db.execute("DELETE FROM raw_events WHERE id = ?", (cited.id,))
        later = self._state(title="They cite the paper again.")
        [again] = self.materials.link(later, [cited.id], self.window)
        self.assertEqual(again["location"], first["location"])
        self.assertIn(later.id, self.materials.of_states([later.id]))

    def test_never_kept_and_gone_is_not_linked(self) -> None:
        self.assertEqual(self.materials.link(self._state(), ["event_0123456789ab"],
                                             self.window), [])


class ThePicturesNoStateWantedTests(_Base):

    def _picture(self, minutes_ago, name):
        path = self.root / "screens" / "today" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"jpg")
        return self._event("screen.shot", minutes_ago, path=str(path),
                           window_title=name), path

    def test_dropped_at_the_end_of_the_run_that_read_them(self) -> None:
        wanted, kept = self._picture(10, "slide.jpg")
        _unwanted, dropped = self._picture(9, "video.jpg")
        _later, untouched = self._picture(-5, "after-the-window.jpg")
        self.materials.link(self._state(), [wanted.id], self.window)
        said = self.materials.drop_unlinked_pictures(self.window)
        self.assertEqual((said["kept"], said["deleted"]), (1, 1))
        self.assertTrue(kept.exists())
        self.assertFalse(dropped.exists())
        self.assertTrue(untouched.exists(), "taken after the stretch: the next run's")


class PastStatesTests(_Base):

    def test_the_fortnights_states_are_given_materials(self) -> None:
        cited = self._event("browser.navigation", 10, url="https://example.org",
                            title="Example")
        state = self._state(evidence=[f"read it ({cited.id})"])
        said = self.materials.backfill(self.app.states)
        self.assertEqual(said, {"states": 1, "materials": 1})
        self.assertIn(state.id, self.materials.of_states([state.id]))
        self.assertEqual(self.materials.backfill(self.app.states)["states"], 0)


class AStageTakesItsReferencesFromMaterialsTests(_Base):

    def test_shown_and_attached(self) -> None:
        from personalized_agent.heartbeat.experience.stages import STAGE_PROMPT
        from personalized_agent.heartbeat.states.tasks import Kept, Task

        said = self._event("assistant.message", 8, role="user",
                           session_id="7d1e5b20-aaaa", text="每页只放一个要点")
        answer = self._event("assistant.message", 7, role="assistant",
                            session_id="7d1e5b20-aaaa", text="Done: one idea a slide.")
        state = self._state(detail="Revising slides in session 7d1e5b20.")
        self.materials.link(state, [answer.id], self.window)
        Kept(self.app.db).save([Task(id="t1", members=[state.id],
                                     described={"title": "The slides"})])

        class Model:
            asked = []

            def invoke(self, request, role=""):
                Model.asked.append(request)
                reply = type("R", (), {})()
                reply.text = json.dumps({"new_stages": [{
                    "ref": "n1", "kind": "evaluation", "card": "root",
                    "summary": "s", "states": [state.id],
                    "references": [{"material": said.id,
                                    "what": "how dense the slides should be"}],
                    "deliverable": {"excerpt": "the reply",
                                    "materials": [answer.id]}}],
                    "still_open": []})
                reply.model, reply.provider, reply.usage = "m", "p", None
                return reply

        writer = self.app.state_heartbeat.experience.stages_writer()
        writer.models = Model()
        task = Kept(self.app.db).get("t1") if hasattr(Kept(self.app.db), "get") else \
            Task(id="t1", members=[state.id], described={"title": "The slides"})
        writer.extend(writer.units([task], [])[0])
        sent = Model.asked[0].messages[0]["content"]
        self.assertIn(f"material [{said.id}] message", sent)
        self.assertIn("每页只放一个要点", sent)
        self.assertNotIn("WHAT ACTUALLY HAPPENED", sent)
        [stage] = ExperienceStore(self.app.db).stages(["t1"])
        [ref] = stage["work"]["references"]
        self.assertEqual(ref["kind"], "message")
        self.assertIn("text", ref["copies"])
        self.assertIn("material", STAGE_PROMPT.lower())
        # What it produced carries its copies too: the agent's answer, a
        # reply - never quoted as the user's words.
        [made] = stage["deliverable"]["references"]
        self.assertEqual(made["kind"], "reply")
        self.assertIn("one idea a slide",
                      Path(made["copies"]["text"]).read_text(encoding="utf-8"))
        self.assertIn(f"material [{answer.id}] reply", sent)


class TheWorkIsModelledOnTheUsersOwnTests(unittest.TestCase):

    def test_the_prompts_say_so(self) -> None:
        from personalized_agent.agent.work import runner
        # The reviewer judges by what was learned of the user's own work -
        # its reasons and cases; the work agent may open every reference to
        # work from.
        self.assertIn("what was learned of THEIR OWN work", runner.REVIEWER_SYSTEM)
        self.assertNotIn("past stages of THEIR OWN work", runner.REVIEWER_SYSTEM)
        self.assertIn("EVERYTHING YOU SAY CITES WHAT IT RESTS ON", runner.REVIEWER_SYSTEM)
        self.assertIn("YOU MAY OPEN THE REFERENCES", runner.AGENT_SYSTEM)


class TheBackstopTests(_Base):

    def _age(self, path: Path, days: float) -> None:
        when = time.time() - days * 86400
        os.utime(path, (when, when))

    def test_from_the_cutoff_and_never_the_users_files(self) -> None:
        old = self._event("screen.text", 5, window_title="Old notes", text="old")
        new = self._event("screen.text", 4, window_title="New notes", text="new")
        cited_e = self._event("screen.text", 3, window_title="Cited notes", text="c")
        old_state, new_state, cited_state = self._state(), self._state(), self._state()
        [old_m] = self.materials.link(old_state, [old.id], self.window)
        [new_m] = self.materials.link(new_state, [new.id], self.window)
        [cited_m] = self.materials.link(cited_state, [cited_e.id], self.window)
        cutoff = self.now
        long_ago = (cutoff - timedelta(days=60)).isoformat()
        for state in (old_state, cited_state):
            self.app.db.execute("UPDATE states SET created_at = ? WHERE id = ?",
                                (long_ago, state.id))
        for made in (old_m, new_m, cited_m):
            self._age(Path(made["copies"]["text"]), 60)
        ExperienceStore(self.app.db).add_stage({
            "task_id": "t", "kind": "execution", "summary": "s",
            "work": {"references": [{"material": cited_e.id,
                                     "copies": cited_m["copies"]}]}})
        users = self.home / "mine.txt"
        users.write_text("mine", encoding="utf-8")
        self._age(users, 900)
        said = collect_garbage(self.app.db, self.root, 30, cutoff.isoformat())
        self.assertFalse(Path(old_m["copies"]["text"]).exists())
        self.assertTrue(Path(new_m["copies"]["text"]).exists(),
                        "a state within 30 days of the cutoff may still be cited")
        self.assertTrue(Path(cited_m["copies"]["text"]).exists())
        self.assertTrue(users.exists())
        self.assertEqual(said["links_dropped"], 1)
        self.assertEqual(self.materials.of_states([old_state.id]), {})

    def test_only_once_every_stage_is_written(self) -> None:
        xp = self.app.state_heartbeat.experience
        with unittest.mock.patch.object(xp, "_close_stage_pass_if_done",
                                        return_value=False), \
                unittest.mock.patch.object(xp, "collect_references") as collect:
            report = xp.after_round(budget=0)
        collect.assert_not_called()
        self.assertNotIn("references_collected", report)


class ADeliverableIsTypedAndPinnedDownTests(_Base):

    def test_a_commit_named_in_a_reply_is_looked_up_and_kept(self) -> None:
        import subprocess
        from personalized_agent.heartbeat.states.tasks import Kept, Task

        repo = self.home / "repo"
        repo.mkdir()
        run = lambda *args: subprocess.run(["git", "-C", str(repo), *args],
                                           capture_output=True, text=True, check=True)
        run("init", "-q")
        run("config", "user.email", "t@example.com")
        run("config", "user.name", "T")
        (repo / "app.js").write_text("draw();\n", encoding="utf-8")
        run("add", "app.js")
        run("commit", "-q", "-m", "Draw the tree per kind")
        sha = run("rev-parse", "HEAD").stdout.strip()
        # A short hash of digits alone is read as a number, not a commit
        # (stages._HASH) - about one commit in 25 starts so; make another.
        from personalized_agent.heartbeat.experience.stages import _HASH
        while not _HASH.fullmatch(sha[:7]):
            run("commit", "-q", "--amend", "--no-edit", "--date", "now")
            sha = run("rev-parse", "HEAD").stdout.strip()
        notes = self.home / "notes.md"
        notes.write_text("# what to keep", encoding="utf-8")

        answer = self._event("assistant.message", 7, role="assistant",
                             session_id="7d1e5b20-aaaa", project=str(repo),
                             text=f"Committed as {sha[:7]}: the tree per kind.")
        state = self.app.states.add(State(
            title="They had the agent draw the tree per kind.", detail="d",
            starts_at=self.now - timedelta(minutes=14), ends_at=self.now,
            metadata={"sources": [str(repo / "app.js")], "looked_at": self.window}))
        self.materials.link(state, [answer.id], self.window)
        Kept(self.app.db).save([Task(id="t2", members=[state.id],
                                     described={"title": "The tree"})])

        class Model:
            asked = []

            def invoke(self, request, role=""):
                Model.asked.append(request)
                out = type("R", (), {})()
                out.text = json.dumps({"new_stages": [{
                    "ref": "n1", "kind": "delivery", "medium": "code",
                    "card": "root", "summary": "s", "states": [state.id],
                    "references": [{"location": str(notes), "what": "the notes"}],
                    "deliverable": {"type": "code", "excerpt": "the tree",
                                    "materials": [answer.id]}}],
                    "still_open": []})
                out.model, out.provider, out.usage = "m", "p", None
                return out

        writer = self.app.state_heartbeat.experience.stages_writer()
        writer.models = Model()
        writer.extend(writer.units([Task(id="t2", members=[state.id],
                                         described={"title": "The tree"})], [])[0])
        sent = Model.asked[0].messages[0]["content"]
        self.assertIn(f"sources: {repo / 'app.js'}", sent)
        [stage] = ExperienceStore(self.app.db).stages(["t2"])
        made = stage["deliverable"]
        self.assertEqual(made["type"], "code")
        [commit] = [r for r in made["references"] if r.get("kind") == "commit"]
        self.assertEqual(commit["commit"], sha)
        self.assertEqual(commit["files"], ["app.js"])
        self.assertIn("draw();", Path(commit["copies"]["text"]).read_text(encoding="utf-8"))
        # A local file a reference names, with no copy from its state: kept
        # now, and said to be today's version.
        [notes_ref] = stage["work"]["references"]
        self.assertEqual(notes_ref["copied"], "now")
        self.assertIn("what to keep",
                      Path(notes_ref["copies"]["text"]).read_text(encoding="utf-8"))


class WhatAStageNamesIsPinnedDownTests(unittest.TestCase):

    def test_a_path_cut_short_is_found_whole(self) -> None:
        import tempfile
        root = Path(tempfile.mkdtemp())
        deep = root / "Research" / "survey" / "a" / "b" / "replication" / "scripts"
        deep.mkdir(parents=True)
        (root / "Research" / "survey" / "other").mkdir()
        cut = str(root / "Research" / "survey") + r"\...\replication\scripts"
        self.assertEqual(Materials.whole_path(cut), str(deep))
        self.assertIsNone(Materials.whole_path(str(root) + r"\...\nothing"))
        self.assertIsNone(Materials.whole_path(str(deep)), "not cut short")

    def test_an_agent_session_is_its_whole_conversation(self) -> None:
        import tempfile
        home = Path(tempfile.mkdtemp())
        session = "710a66b8-2de7-48b3-ad46-b81bbbb630ad"
        talk = home / ".claude" / "projects" / "C--work" / f"{session}.jsonl"
        talk.parent.mkdir(parents=True)
        talk.write_text("{}\n", encoding="utf-8")
        with unittest.mock.patch.object(Path, "home", return_value=home):
            self.assertEqual(Materials.transcript(
                f"Claude Code session {session} in C:/work"), str(talk))
            self.assertIsNone(Materials.transcript("no session here"))


class PathsAsPeopleWriteThemTests(_Base):

    def test_relative_and_two_at_once(self) -> None:
        from personalized_agent.heartbeat.experience.stages import _split_paths
        repo = self.home / "release"
        (repo / "pkg").mkdir(parents=True)
        (repo / "pkg" / "service.py").write_text("x", encoding="utf-8")
        (repo / "pkg" / "patterns.py").write_text("y", encoding="utf-8")
        out = _split_paths([{"location": r"pkg\service.py and patterns.py",
                             "what": "the build"},
                            {"location": "a session somewhere"}], [repo])
        self.assertEqual([one["location"] for one in out],
                         [str(repo / "pkg" / "service.py"),
                          str(repo / "pkg" / "patterns.py"),
                          "a session somewhere"])
        self.assertEqual(out[0]["kind"], "file")
        self.assertEqual(out[0]["what"], "the build")

    def test_past_stages_are_pinned_down_once(self) -> None:
        notes = self.home / "notes.md"
        notes.write_text("# kept", encoding="utf-8")
        store = ExperienceStore(self.app.db)
        store.add_stage({"task_id": "t", "kind": "execution", "summary": "s",
                         "work": {"references": [{"location": str(notes)}]},
                         "deliverable": {"type": "document",
                                         "references": [{"location": str(notes)}]}})
        xp = self.app.state_heartbeat.experience
        said = xp.repin_references()
        self.assertEqual(said["stages"], 1)
        [stage] = store.stages(["t"])
        [ref] = stage["work"]["references"]
        self.assertEqual(ref["copied"], "now")
        self.assertIn("raw", ref["copies"])
        self.assertIn("raw", stage["deliverable"]["references"][0]["copies"])
        self.assertEqual(xp.repin_references(), {}, "once")

"""WHAT A PIECE OF WORK HANDED OVER IS GIVEN, BESIDE THE RECORDS FOUND (the user, 2026-11-08):
a person's exchanges on its subject ("人名+工作"); what was said of it in the chat, learned of it,
other work on it, mail on it, the calendar and reminders on it ("收集时加上这几个来源"); the user's
own lessons that bear on it; and work handed over from the chat ("chat里要能add work"). Everything
here is made up."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

import support  # noqa: F401
from support import TemporaryAgent

from personalized_agent.agent.work import delegate as dl

WORDS = ["Tidewatch", "tide gauge dashboard", "harbour sensor migration", "calibration report"]


class TheSubjectTests(unittest.TestCase):

    def test_its_own_words_or_two_of_the_rest(self) -> None:
        strong, rest = dl.topic_words(WORDS)
        self.assertIn("tidewatch", strong)
        self.assertNotIn("report", strong | rest, "too common to tell a subject by")
        self.assertTrue(dl.on_topic("The Tidewatch launch notes", strong, rest))
        self.assertTrue(dl.on_topic("harbour sensor swap planned", strong, rest), "two of the rest")
        self.assertFalse(dl.on_topic("Reviewed conference submissions for Ada", strong, rest))
        self.assertFalse(dl.on_topic("a harbour picnic", strong, rest), "one of the rest is not enough")


class MoreContextTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app
        self.db = self.app.db

    def _draft(self, **options):
        return {"id": "draft_x", "source": "delegate", "text": "", "options": options}

    def test_a_persons_exchanges_on_the_work_first_and_a_few_latest_beside(self) -> None:
        for n, (title, at) in enumerate([
                ("Ada Quill sent the Tidewatch calibration plan", "2026-11-01T10:00:00+00:00"),
                ("Ada Quill asked about the harbour sensor migration", "2026-11-02T10:00:00+00:00"),
                ("Ada Quill reviewed conference submissions", "2026-11-03T10:00:00+00:00"),
                ("Ada Quill booked the department picnic", "2026-11-04T10:00:00+00:00")]):
            self.db.execute("INSERT INTO states (id, title, detail, starts_at, ends_at, source, about, "
                            "created_at, updated_at) VALUES (?, ?, '', ?, ?, 'screen', 'user', ?, ?)",
                            (f"s{n}", title, at, at, at, at))
        drafts = self.app.drafts
        said = drafts._person("Ada Quill", topic=WORDS)
        titles = [one["title"] for one in said["exchanges"]]
        self.assertIn("Ada Quill sent the Tidewatch calibration plan", titles)
        self.assertIn("Ada Quill asked about the harbour sensor migration", titles)
        self.assertEqual(len(titles), dl.LEAST_EXCHANGES, "two on it, the latest one besides")
        self.assertIn("Ada Quill booked the department picnic", titles)
        self.assertNotIn("Ada Quill reviewed conference submissions", titles)
        self.assertEqual(len(drafts._person("Ada Quill")["exchanges"]), 4, "with no subject: all, as before")

    def test_said_learned_other_work_mail_calendar_and_reminders_on_it(self) -> None:
        db = self.db
        db.execute("INSERT INTO conversations (id, created_at, title) VALUES ('c1', '2026-11-01', 't')")
        db.execute("INSERT INTO messages (id, conversation_id, created_at, role, content) VALUES "
                   "('m1', 'c1', '2026-11-01T09:00', 'user', 'For Tidewatch we need a public demo before December')")
        db.execute("INSERT INTO messages (id, conversation_id, created_at, role, content) VALUES "
                   "('m2', 'c1', '2026-11-01T09:01', 'assistant', 'I wrote a plan for the Tidewatch demo')")
        db.execute("INSERT INTO messages (id, conversation_id, created_at, role, content) VALUES "
                   "('m3', 'c1', '2026-11-01T09:02', 'user', 'what is the weather like')")
        db.execute("INSERT INTO learn_cases (id, unit_id, title, looks_like, created_at, updated_at) "
                   "VALUES ('C-1', 'u', 'Tidewatch dashboard web app', 'A Next.js site', '2026', '2026')")
        db.execute("INSERT INTO learn_cases (id, unit_id, title, looks_like, created_at, updated_at) "
                   "VALUES ('C-2', 'u', 'A cookbook', 'recipes', '2026', '2026')")
        db.execute("INSERT INTO work_runs (id, unit_id, unit_name, folder, status, instruction, plan, "
                   "log, final, started_at, updated_at) VALUES ('r1', 'u1', 'Invite Ada to the Tidewatch "
                   "review', 'C:/agent_work/invite', 'done', '', '{}', '[]', ?, '2026-11-01', '2026-11-01')",
                   (json.dumps({"answer": "The invite is drafted."}),))
        db.execute("CREATE TABLE IF NOT EXISTS work_mail_pages (url TEXT, site TEXT, title TEXT, "
                   "first_seen TEXT, last_seen TEXT, visits INTEGER, people TEXT, read TEXT)")
        db.execute("INSERT INTO work_mail_pages (url, title, last_seen) VALUES "
                   "('https://mail.example/1', 'tidewatch - inbox', '2026-11-02')")
        db.execute("INSERT INTO scheduled_tasks (id, title, description, created_at, scheduled_at, "
                   "status, execution_type) VALUES ('t1', 'Harbour sensor migration call', 'with Ada', "
                   "'2026-11-01', '2026-11-10T15:00', 'pending', 'reminder')")
        db.execute("INSERT INTO notifications (id, created_at, kind, title, message) VALUES "
                   "('n1', '2026-11-03', 'proactive', 'Check the Tidewatch nightly run', 'it ran at 8')")
        more = self.app.drafts._more_found(WORDS, self._draft())
        self.assertEqual([one["who"] for one in more["said_in_chat"]], ["user", "agent"])
        self.assertEqual([one["title"] for one in more["learned"]], ["Tidewatch dashboard web app"])
        self.assertEqual(more["other_works"][0]["folder"], "C:/agent_work/invite")
        self.assertIn("drafted", more["other_works"][0]["report"])
        self.assertEqual(more["mail"][0]["title"], "tidewatch - inbox")
        self.assertEqual(more["calendar"][0]["title"], "Harbour sensor migration call")
        self.assertEqual(more["reminders"][0]["title"], "Check the Tidewatch nightly run")
        told = "\n".join(dl.more_found_lines(dict(more, lessons="- When reporting a deploy, link the live page.")))
        for heading in ("WHAT WAS SAID OF IT IN THE CHAT", "WHAT WAS LEARNED OF THE USER'S WORK ON IT",
                        "OTHER WORK DONE ON THE SAME SUBJECT", "THE USER'S MAIL THREADS ON IT",
                        "ON THE USER'S CALENDAR", "THE USER'S OWN RULES AND WAYS"):
            self.assertIn(heading, told)
        self.assertIn("C:/agent_work/invite", told)
        self.assertIn("link the live page", told)

    def test_the_users_lessons_found_by_what_it_is_and_each_step(self) -> None:
        asked = []

        def in_parts(kinds, parts, **kw):
            asked.append((tuple(kinds), list(parts)))
            return "Rules:\n- When the work deploys a site, check it live before saying it is done."
        with mock.patch.object(self.app, "lesson_retriever", SimpleNamespace(in_parts=in_parts)):
            said = self.app.drafts._lessons_for(
                {"id": "d", "text": "Tidewatch: finish it\nThe steps left:\n1. Deploy the dashboard\n"
                                    "2. Write the calibration report", "options": {}},
                {"sentence": "Finishing the Tidewatch dashboard"})
        self.assertIn("check it live", said)
        self.assertEqual(asked[0][0], dl.LESSON_KINDS)
        self.assertEqual(asked[0][1], ["Finishing the Tidewatch dashboard", "Deploy the dashboard",
                                       "Write the calibration report"])
        with mock.patch.object(self.app, "lesson_retriever",
                               SimpleNamespace(in_parts=lambda *a, **k: "None learned yet.")):
            self.assertEqual(self.app.drafts._lessons_for({"id": "d", "text": "x", "options": {}},
                                                          {"sentence": "x"}), "")

    def test_rules_naming_its_subject_given_first(self) -> None:
        """Measured on the user's own (2026-11-08): by meaning alone, rules naming the project were
        left out for rules about another project's releases."""
        cols = {r["name"] for r in self.db.query("pragma table_info(lesson_rules)")}
        row = {"id": "rec_tide1", "kind": "work", "statement": "The user wants Tidewatch charts to "
               "show local time.", "state": "candidate", "confidence": 0.63,
               "created_at": "2026-11-01", "updated_at": "2026-11-01"}
        row = {k: v for k, v in row.items() if k in cols}
        self.db.execute(f"INSERT INTO lesson_rules ({', '.join(row)}) VALUES "
                        f"({', '.join('?' for _ in row)})", tuple(row.values()))
        with mock.patch.object(self.app, "lesson_retriever", SimpleNamespace(
                in_parts=lambda *a, **k: "- [rec_other] When releasing, test the download first.")):
            said = self.app.drafts._lessons_for({"id": "d", "text": "Tidewatch", "options": {}},
                                                {"sentence": "Tidewatch", "keywords": WORDS})
        self.assertLess(said.index("Tidewatch charts"), said.index("test the download"),
                        "its own subject's rule first")


class FromTheChatTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.addCleanup(self.agent.cleanup)
        self.app = self.agent.app

    def test_work_handed_over_from_the_chat_starts_by_itself(self) -> None:
        spec = next(one for one in self.app.agent.tools.specs if one.name == "assign_work")
        self.assertIn("Work assigned", spec.description)
        calls = []
        drafts = self.app.drafts
        with mock.patch.object(drafts, "compose", lambda i, w, t="": calls.append(("compose", w, t))
                               or {"draft": {}}), \
                mock.patch.object(drafts, "start_apart", lambda i, plan=None, folders=None, options=None:
                                  calls.append(("start", plan, options)) or {"starting": True}):
            said = self.app.agent.tools.call("assign_work", {"instruction": "Write the Tidewatch "
                                                                            "calibration report",
                                                             "title": "Calibration report"})
        draft = drafts.get(said["assigned"])
        self.assertEqual(draft["source"], "chat")
        self.assertEqual(calls[0], ("compose", "Write the Tidewatch calibration report",
                                    "Calibration report"))
        self.assertEqual(calls[1][0], "start", "started, nothing more to approve")
        self.assertIn("Work assigned", said["said"])
        self.assertIn("error", self.app.agent.tools.call("assign_work", {"instruction": " "}))

    def test_the_chat_keeps_lessons_without_being_asked(self) -> None:
        spec = next(one for one in self.app.agent.tools.specs if one.name == "lesson_add")
        self.assertIn("WITHOUT BEING ASKED", spec.description)
        self.assertIn("their own words", spec.description)


if __name__ == "__main__":
    unittest.main()

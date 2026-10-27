"""WHAT THE USER GETS, AND HOW (the user, 2026-10-26): a result handed over
in the form most convenient for what they do with it - an email as a new
message opened in their own mail, filled in, for them to send; an event in
their calendar, for them to save; text to copy; a file opened - never sent,
saved or submitted for them. Where it is opened is their setting, found by
default from the windows they had open."""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from support import TemporaryAgent
from test_final_checks import _Checked
from test_work_quick import _Quick

from personalized_agent.agent.work import handed

EMAIL = {"kind": "email", "title": "Reply to Ada", "to": ["ada@example.com"],
         "cc": "bo@example.com; cy@example.com", "subject": "Re: the talk & the date",
         "body": "Dear Ada,\n\n下周三可以吗？ A & B.\n\nBest,\nSam"}
GMAIL = {"mail": "gmail", "account": "sam@example.com", "calendar": "google"}


def _query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


class TheKindsTests(unittest.TestCase):

    def test_an_email_opens_as_a_new_message_filled_in(self) -> None:
        item = handed.checked(EMAIL, lambda path: None)
        self.assertEqual(item["cc"], ["bo@example.com", "cy@example.com"])
        acts = handed.actions(item, GMAIL)
        self.assertTrue(acts["whole"])
        self.assertEqual(acts["in"], "Gmail")
        url = acts["open"]
        self.assertTrue(url.startswith("https://mail.google.com/mail/?"))
        said = _query(url)
        self.assertEqual((said["view"], said["authuser"], said["to"], said["su"]),
                         ("cm", "sam@example.com", "ada@example.com", "Re: the talk & the date"))
        self.assertEqual(said["body"], EMAIL["body"], "line breaks, & and Chinese kept")
        self.assertEqual(said["cc"], "bo@example.com,cy@example.com")
        self.assertEqual(acts["copy"], EMAIL["body"])
        outlook = handed.actions(item, dict(GMAIL, mail="outlook"))["open"]
        self.assertTrue(outlook.startswith("https://outlook.office.com/mail/deeplink/compose?"))
        self.assertEqual(_query(outlook)["subject"], "Re: the talk & the date")
        mailto = handed.actions(item, dict(GMAIL, mail="default"))["open"]
        self.assertTrue(mailto.startswith("mailto:ada@example.com?"))
        self.assertEqual(_query(mailto)["body"], EMAIL["body"])

    def test_too_long_for_a_link_the_body_is_pasted(self) -> None:
        item = handed.checked(dict(EMAIL, body="word " * 3000), lambda path: None)
        acts = handed.actions(item, GMAIL)
        self.assertFalse(acts["whole"])
        self.assertNotIn("body", _query(acts["open"]))
        self.assertLessEqual(len(acts["open"]), handed.LINK_MOST)
        self.assertEqual(acts["copy"], item["body"])

    def test_an_event_opens_in_the_calendar_or_as_a_file(self) -> None:
        item = handed.checked({"kind": "event", "title": "Talk with Ada",
                               "start": "2026-11-04T15:00:00-05:00", "where": "Room 2"},
                              lambda path: None)
        url = handed.actions(item, GMAIL)["open"]
        said = _query(url)
        self.assertEqual((said["action"], said["text"], said["dates"], said["location"]),
                         ("TEMPLATE", "Talk with Ada", "20261104T200000Z/20261104T210000Z",
                          "Room 2"))
        day = handed.checked({"kind": "event", "title": "Deadline", "start": "2026-11-04"},
                             lambda path: None)
        self.assertEqual(_query(handed.actions(day, GMAIL)["open"])["dates"],
                         "20261104/20261105")
        file = handed.actions(item, dict(GMAIL, calendar="file"))
        self.assertTrue(file["ics"])
        self.assertFalse(file["open"])
        text = handed.ics(item)
        self.assertIn("DTSTART:20261104T200000Z", text)
        self.assertIn("SUMMARY:Talk with Ada", text)
        self.assertIn("LOCATION:Room 2", text)
        self.assertIn("error", handed.checked({"kind": "event", "title": "x", "start": "soon"},
                                              lambda path: None))

    def test_text_files_and_what_is_refused(self) -> None:
        self.assertEqual(handed.actions(handed.checked(
            {"kind": "text", "title": "Reply", "text": "Sure, Wednesday."},
            lambda path: None), GMAIL), {"copy": "Sure, Wednesday."})
        self.assertIn("error", handed.checked({"kind": "fax", "title": "x"}, lambda p: None))
        self.assertIn("error", handed.checked({"kind": "email", "title": "x"}, lambda p: None))
        self.assertIn("error", handed.checked({"kind": "file", "title": "x", "path": "a.md"},
                                              lambda path: None))
        # The same kind and title again: the newer in its place.
        items = handed.kept([], dict(EMAIL, body="one"))
        items = handed.kept(items, dict(EMAIL, body="two"))
        self.assertEqual([one["body"] for one in items], ["two"])


class WhereItOpensTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        handed._found.clear()
        self.addCleanup(handed._found.clear)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _window(self, n, title, url=""):
        self.app.db.execute(
            "INSERT INTO raw_events (id, event_type, source, timestamp, application, data) "
            "VALUES (?, 'window', 'screen', '2026-10-26T10:00:00', 'chrome', ?)",
            (f"ev_{n}", json.dumps({"title": title, "url": url})))

    def test_found_from_the_windows_they_had_open_unless_set(self) -> None:
        for n in range(3):
            self._window(n, "Inbox (2) - sam@example.com - Gmail", "https://mail.google.com/")
        self._window(9, "Inbox - other@example.com - Gmail")
        self._window(10, "Google Calendar - Week of Nov 2")
        self.assertEqual(handed.where(self.app.config, self.app.db),
                         {"mail": "gmail", "account": "sam@example.com", "calendar": "google"})
        self.app.config.set("mail_app", "outlook")
        self.app.config.set("calendar_app", "file")
        self.assertEqual(handed.where(self.app.config, self.app.db),
                         {"mail": "outlook", "account": "", "calendar": "file"})

    def test_found_nothing_the_computers_own(self) -> None:
        self.assertEqual(handed.where(self.app.config, self.app.db),
                         {"mail": "default", "account": "", "calendar": "file"})


class HandedOverByTheWorkTests(unittest.TestCase):

    def setUp(self) -> None:
        self.agent = TemporaryAgent()
        self.app = self.agent.app
        self.unit = {"id": "task-hand1", "name": "Email to Ada", "kind": "task", "why": ""}
        handed._found.clear()
        self.addCleanup(handed._found.clear)

    def tearDown(self) -> None:
        self.agent.cleanup()

    def _wait(self):
        for _ in range(900):
            runs = self.app.work.runs(self.unit["id"])
            if runs and not runs[0]["alive"] and runs[0]["status"] != "running":
                return runs[0]
            time.sleep(0.1)
        self.fail("the run did not end")

    def test_an_email_handed_over_is_checked_and_opened_in_their_mail(self) -> None:
        from personalized_agent.api.routes import build_routes
        from personalized_agent.models.base import ToolCall
        self.app.work.final_checks = True

        class _Hands(_Quick):
            def invoke(inner, request, role=""):
                last = request.messages[-1]["content"]
                if "on your own" in (request.system or "") and isinstance(last, str) \
                        and not last.startswith("GO ON") and not inner.quick \
                        and "BEFORE YOU START" not in last:
                    inner.quick.append(request)
                    return inner._reply("", [ToolCall(id="h1", name="hand_over",
                                                      arguments=EMAIL)])
                return super().invoke(request, role)
        models = _Checked(_Hands())
        self.app.work.models = models
        self.app.work.start(self.unit, "write Ada an email", way="quick")
        run = self._wait()
        self.assertEqual(run["status"], "done", run["log"][-6:])
        # Every way's agent is told how results are handed over.
        self.assertIn("WHAT THE USER GETS, AND HOW", models.inner.quick[0].system)
        self.assertIn("hand_over", {one.name for one in models.inner.quick[0].tools})
        self.assertEqual([one["kind"] for one in run["plan"]["handed"]], ["email"])
        # The privacy check reads the email as it will be opened.
        told = json.dumps(models.checks["privacy"][0].messages, ensure_ascii=False)
        self.assertIn("WHAT IS HANDED TO THE USER", told)
        self.assertIn("下周三可以吗", told)
        # The page: the email with how it opens - and opened in their own browser.
        routes = build_routes(self.app)

        class _Ask(dict):
            def get(self, key, default=None):
                return super().get(key, default)

            def require(self, key):
                return self[key]

            def int_param(self, key, default=0):
                return int(self.get(key, default))
        self.app.config.set("mail_app", "gmail")
        self.app.config.set("mail_account", "sam@example.com")
        shown = routes[("GET", "/api/work/runs")](_Ask(unit=self.unit["id"]))["runs"][0]
        acts = shown["handed"][0]["actions"]
        self.assertTrue(acts["open"].startswith("https://mail.google.com/mail/?"))
        with mock.patch("webbrowser.open", return_value=True) as opened:
            said = routes[("POST", "/api/work/handed/open")](_Ask(run=run["id"], n=0))
        self.assertEqual(said["opened"], "email")
        self.assertEqual(opened.call_args[0][0], acts["open"])
        # A to-do handed over: its result is what was handed over first.
        came = self.app.drafts._came_of(self.unit["id"])
        self.assertEqual((came["state"], came["handed"]), ("finished", "Reply to Ada"))
        self.assertEqual(came["items"], [{"n": 0, "kind": "email", "title": "Reply to Ada",
                                          "name": ""}])

    def test_only_the_work_agent_hands_over(self) -> None:
        from personalized_agent.agent.work.tools import WorkTools
        tools = WorkTools(self.app.work._runtime(self.unit, self.app.work.folder_of(self.unit)))
        self.assertNotIn("hand_over", {one.name for one in tools.specs()})
        tools.runs_code = ("run_python", "run_tests")
        tools.on_hand_over = lambda item: None
        self.assertNotIn("hand_over", {one.name for one in tools.specs(writing=False)},
                         "not the reviewer's")
        self.assertIn("hand_over", {one.name for one in tools.specs()})


if __name__ == "__main__":
    unittest.main()
